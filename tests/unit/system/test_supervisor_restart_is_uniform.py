"""Every supervisor restart goes through supervisorctl, not a unit name.

Two things make a unit name the wrong handle:

  * it differs by distro -- `supervisor` on Debian / Raspberry Pi OS,
    `supervisord` on Fedora / RHEL -- and nothing in PiFire detects which. The
    old restart_scripts() tried each in turn, and each installer's sudoers grant
    names only its own, so the wrong guess fell outside NOPASSWD;
  * the `service` command is granted by NO installer at all, so
    `sudo service supervisor restart` -- which display/_base_flex.py ran for its
    restart menu item -- asked for a password no one was there to type. That
    restart could never have worked on a stock install.

`supervisorctl` is one name on every platform and every installer grants it.

Structural, in the spirit of test_display_control_writes_are_deltas: pinning the
property at the source catches a site added tomorrow whether or not anyone
writes a test for its menu.

LIMIT, measured rather than assumed: it only sees the unit name as a LITERAL.
Reintroducing the old restart_scripts -- which looped a variable over
["supervisor", "supervisord"] -- slips past this untouched. The behavioural
tests in test_system_lifecycle.py are what caught that; this is the sweep for
everywhere else, not a substitute for them.
"""

import io
import pathlib
import re
import tokenize

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]

#: Everything that runs on a grill. tests/ is excluded -- a test may legitimately
#: name the old command to assert it is gone.
SOURCE_DIRS = ["common", "display", "blueprints", "controller", "notify", "probes", "grillplat"]
SOURCE_FILES = ["updater.py", "control.py", "app.py", "wizard.py"]

#: The separator is deliberately loose. These commands are written BOTH as a
#: shell string ("sudo service supervisor restart") and as an argv list
#: (["sudo", "systemctl", "restart", "supervisord"]) -- and the list form is
#: exactly how restart_scripts() used to do it, so a pattern that only matched
#: whitespace would have missed the regression it exists to catch.
SEP = r"""[\s"',\[\]]+"""
BY_UNIT_NAME = re.compile(rf"service{SEP}supervisor\w*{SEP}restart|systemctl{SEP}restart{SEP}supervisor\w*")


#: Token types after which a STRING is a statement in its own right -- a
#: docstring, or one of the bare `"""..."""` section banners updater.py and
#: common/process_mon.py use as headers.
_STATEMENT_START = frozenset(
    {tokenize.NEWLINE, tokenize.NL, tokenize.INDENT, tokenize.DEDENT, tokenize.ENCODING}
)


def code_only(path):
    """Source with comments and prose strings stripped.

    The rule is about what RUNS. A comment explaining why the old command was
    wrong must not trip it, and neither must a docstring: `publish_finished`'s
    says "nothing in the update path called supervisorctl", which is the history
    these sweeps exist to keep, not a command.

    A string is prose only when it stands alone as a statement AND sits at paren
    depth zero. That second half matters -- the commands themselves are string
    literals, and one written across lines inside a call

        os.system(
            "sudo reboot"
        )

    follows a NL token exactly as a docstring does. Depth is what tells them
    apart, and dropping it would have made this sweep blind to the very form it
    is looking for.
    """
    try:
        tokens = [t for t in tokenize.generate_tokens(io.StringIO(path.read_text("utf-8")).readline)]
    except tokenize.TokenError, SyntaxError:
        return path.read_text("utf-8")

    kept = []
    depth = 0
    previous = None
    for token in tokens:
        if token.type == tokenize.COMMENT:
            continue
        if token.type == tokenize.OP:
            if token.string in "([{":
                depth += 1
            elif token.string in ")]}":
                depth = max(0, depth - 1)
        prose = token.type == tokenize.STRING and depth == 0 and (previous is None or previous in _STATEMENT_START)
        previous = token.type
        if not prose:
            kept.append(token.string)
    return "\n".join(kept)


def python_sources():
    for d in SOURCE_DIRS:
        for path in sorted((ROOT / d).rglob("*.py")):
            if "__pycache__" not in path.parts:
                yield path
    for f in SOURCE_FILES:
        if (ROOT / f).exists():
            yield ROOT / f


ALL_SOURCES = list(python_sources())


def test_the_scan_actually_sees_the_files():
    """Negative control for the sweep below: an empty file list would make every
    assertion vacuous."""
    names = {p.name for p in ALL_SOURCES}
    assert len(ALL_SOURCES) > 50, f"only found {len(ALL_SOURCES)} sources"
    assert {"system.py", "_base_flex.py", "updater.py"} <= names


@pytest.mark.parametrize("path", ALL_SOURCES, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_module_restarts_supervisor_by_unit_name(path):
    hits = BY_UNIT_NAME.findall(code_only(path))

    assert not hits, (
        f"{path.relative_to(ROOT)} restarts supervisor by unit name ({hits}). "
        "The unit is `supervisor` on Debian and `supervisord` on Fedora, and no installer "
        "grants the `service` command under NOPASSWD. Use `sudo supervisorctl restart ...`."
    )


def test_the_pattern_would_catch_a_regression():
    """Proves the assertion above has teeth rather than matching nothing."""
    for offender in (
        'os.system("sleep 3 && sudo service supervisor restart &")',
        'subprocess.run(["sudo", "systemctl", "restart", "supervisord"])',
        "sudo systemctl restart supervisor",
    ):
        assert BY_UNIT_NAME.search(offender), offender
    for allowed in (
        'os.system("sleep 3 && sudo supervisorctl restart webapp &")',
        '["sudo", "supervisorctl", "restart", "all"]',
        "sudo systemctl restart nginx",
    ):
        assert not BY_UNIT_NAME.search(allowed), allowed


def test_every_installer_grants_supervisorctl():
    """The other end of the same data path: the command above is only passwordless
    because the installers say so."""
    installers = sorted((ROOT / "auto-install").glob("install*.sh"))
    assert installers, "no installers found"

    for path in installers:
        text = path.read_text(encoding="utf-8")
        if "NOPASSWD" not in text:
            continue
        assert "supervisorctl" in text, f"{path.name} grants sudo rules but not supervisorctl"


#: Restarting, rebooting and powering off belong to common/system.py and nowhere
#: else. Nine sites had grown their own copy -- updater.py, both display bases,
#: controller/runtime/controller.py, common/process_mon.py and the mobile socket
#: handler -- each with slightly different behaviour: some gated on real
#: hardware, some did not (the controller's auto-power-off would halt a
#: developer's workstation); the process monitor's ran `supervisorctl` with no
#: `sudo`, which the installers' NOPASSWD grant does not cover, so recovery from
#: a hung control loop was one permission away from never working.
#: Non-capturing throughout: findall returns GROUPS when a pattern has any, so a
#: capturing alternative reports empty strings for every branch that did not use
#: it -- which reads as a hit with nothing in it.
RUNS_LIFECYCLE = re.compile(
    rf"supervisorctl|sudo{SEP}reboot|systemctl{SEP}(?:reboot|poweroff)|shutdown{SEP}-h{SEP}now"
)

#: The one module allowed to name these commands.
LIFECYCLE_OWNER = "common/system.py"


@pytest.mark.parametrize("path", ALL_SOURCES, ids=lambda p: str(p.relative_to(ROOT)))
def test_only_common_system_runs_a_lifecycle_command(path):
    if path.relative_to(ROOT).as_posix() == LIFECYCLE_OWNER:
        return

    hits = RUNS_LIFECYCLE.findall(code_only(path))

    assert not hits, (
        f"{path.relative_to(ROOT)} runs a restart/reboot/shutdown command itself ({hits}). "
        f"Call {LIFECYCLE_OWNER}'s restart_control/restart_webapp/restart_scripts/"
        "reboot_system/shutdown_system instead -- it owns the sudo the installers grant, "
        "the real-hardware gate and the grace period that lets an in-flight response out. "
        "A caller that is about to EXIT passes wait=True rather than growing its own shell."
    )


def test_the_lifecycle_pattern_would_catch_a_regression():
    """Teeth, not a pattern that matches nothing -- every one of these is a form
    that was really in the tree before this sweep existed."""
    for offender in (
        'os.system("sleep 3 && sudo supervisorctl restart all &")',
        'os.system("sleep 3 && sudo reboot &")',
        'os.system("sleep 3 && sudo shutdown -h now &")',
        '["supervisorctl", "restart", "control"]',
        'subprocess.run(["sudo", "systemctl", "poweroff"])',
    ):
        assert RUNS_LIFECYCLE.search(offender), offender
    for allowed in (
        "restart_scripts(wait=True)",
        "reboot_system()",
        "shutdown_system()",
        'Process_Monitor("control", restart_control, timeout=30)',
    ):
        assert not RUNS_LIFECYCLE.search(allowed), allowed


def test_the_owner_really_does_hold_the_commands():
    """Negative control: if system.py stopped naming them, the sweep above would
    pass by describing a tree where nothing restarts anything at all."""
    owner = code_only(ROOT / LIFECYCLE_OWNER)
    assert RUNS_LIFECYCLE.search(owner), f"{LIFECYCLE_OWNER} no longer runs any lifecycle command"
