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


def code_only(path):
    """Source with comments stripped. The rule is about what RUNS: a comment
    explaining why the old command was wrong must not trip it."""
    try:
        tokens = [t for t in tokenize.generate_tokens(io.StringIO(path.read_text("utf-8")).readline)]
    except tokenize.TokenError, SyntaxError:
        return path.read_text("utf-8")
    return "\n".join(t.string for t in tokens if t.type != tokenize.COMMENT)


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
