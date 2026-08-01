"""Finding bun when the build runs without a login shell's PATH.

A rebuild triggered from the web UI runs in a process descended from the
service manager, whose PATH is the system default -- not the one an interactive
shell assembles from ~/.profile, ~/.bashrc or a version manager's activation
hook. bun installs itself under $HOME and is put on PATH by exactly those
files, so a machine with a working `bun` at the command line took the download
path anyway, and then failed on a network the shell also had:

    + Fetching a temporary bun to build the web UI (not installed system-wide)
    curl: (6) Could not resolve host: bun.sh
    !! bun did not land at /tmp/pifire-bun-pOYiaP/bin/bun.

By default nothing here touches the network: these drive pifire_locate_bun, the
offline half, against fake executables in tmp_path, with a PATH that has no bun
on it. Calling pifire_get_bun instead fetches ~90MB from bun.sh on every miss --
which it did, once, before this note existed. The fetch path has its own tests
at the bottom, behind PIFIRE_TEST_BUN_DOWNLOAD=1.
"""

import os
import re
import subprocess

import pytest

LIB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "auto-install",
    "pifire-install-common.sh",
)

#: A systemd service's default. Deliberately without /home/*/.bun or any
#: version manager's directory -- that absence is the whole subject.
SERVICE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def run_lib(script, home, extra_env=None):
    """Source the install library under a service-like environment and run
    `script`. `env -i` is what makes this a test rather than a coincidence: an
    inherited PATH from the pytest process would have bun on it."""
    env = {"HOME": str(home), "PATH": SERVICE_PATH, "LOG": "/dev/null"}
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", "-c", f"source {LIB!r}; {script}"],
        capture_output=True,
        text=True,
        env=env,
    )


def fake_bun(path):
    """An executable that answers --version, which is all discovery asks of it."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        handle.write("#!/bin/sh\necho 1.2.3\n")
    os.chmod(path, 0o755)
    return path


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


def test_no_bun_anywhere_finds_nothing(home):
    """The negative control. Without it, a test that finds bun proves only that
    the machine running the suite has one."""
    result = run_lib("pifire_locate_bun >/dev/null 2>&1 || echo NOTFOUND", home)

    assert "NOTFOUND" in result.stdout


@pytest.mark.parametrize(
    "relative",
    [
        ".bun/bin/bun",
        ".local/share/mise/installs/bun/latest/bin/bun",
        ".asdf/installs/bun/latest/bin/bun",
    ],
)
def test_bun_is_found_where_installers_actually_put_it(home, relative):
    fake_bun(str(home / relative))

    result = run_lib('pifire_locate_bun && echo "RESOLVED=$PIFIRE_BUN"', home)

    assert f"RESOLVED={home / relative}" in result.stdout, result.stdout + result.stderr


def test_bun_install_env_var_is_honoured(home, tmp_path):
    """bun's own installer sets it, and an operator who moved the install has
    no other way to say so."""
    elsewhere = tmp_path / "opt" / "bun"
    fake_bun(str(elsewhere / "bin" / "bun"))

    result = run_lib('pifire_locate_bun && echo "RESOLVED=$PIFIRE_BUN"', home, {"BUN_INSTALL": str(elsewhere)})

    assert f"RESOLVED={elsewhere / 'bin' / 'bun'}" in result.stdout


def test_a_found_bun_goes_on_path(home):
    """Not a convenience: package.json's "build" script is `bun run typecheck &&
    rsbuild build`, and that inner `bun` is resolved through PATH by the shell
    bun spawns."""
    fake_bun(str(home / ".bun" / "bin" / "bun"))

    result = run_lib("pifire_locate_bun && command -v bun", home)

    assert str(home / ".bun" / "bin" / "bun") in result.stdout


def test_path_still_wins_when_bun_is_on_it(home, tmp_path):
    """An operator who put a specific bun on PATH gets that one, not whichever
    of the searched locations happens to also hold one."""
    on_path = tmp_path / "pathbin"
    fake_bun(str(on_path / "bun"))
    fake_bun(str(home / ".bun" / "bin" / "bun"))

    result = run_lib(
        'pifire_locate_bun && echo "RESOLVED=$PIFIRE_BUN"',
        home,
        {"PATH": f"{on_path}:{SERVICE_PATH}"},
    )

    assert f"RESOLVED={on_path / 'bun'}" in result.stdout


def test_a_non_executable_candidate_is_skipped(home):
    """A partial download or an interrupted install leaves the file without the
    execute bit, and taking it would fail later and less legibly."""
    path = home / ".bun" / "bin" / "bun"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    path.write_text("not executable")

    result = run_lib("pifire_locate_bun >/dev/null 2>&1 || echo NOTFOUND", home)

    assert "NOTFOUND" in result.stdout


def test_the_failure_report_names_every_place_it_looked(home):
    """The operator can run `bun` themselves, so "could not download" alone
    reads as a network problem when the fix is to put bun where this can see
    it."""
    result = run_lib('pifire_report_no_bun "Could not download bun."', home)
    out = result.stdout

    assert "Could not download bun." in out
    assert str(home / ".bun" / "bin" / "bun") in out
    assert "/usr/local/bin/bun" in out
    assert "shim will not do" in out


def test_the_failure_report_does_not_repeat_the_default_location(home):
    """With BUN_INSTALL unset the default fills in ~/.bun, which listed the same
    path twice."""
    out = run_lib('pifire_report_no_bun "x"', home).stdout

    assert out.count(str(home / ".bun" / "bin" / "bun")) == 1


# ---------------------------------------------------------------------------
# The fallback: no bun on the machine, fetch a throwaway one.
#
# Opt-in, because it downloads ~90MB from bun.sh and takes tens of seconds. The
# rest of this file is deliberately offline, and a network fetch that ran on
# every `pytest tests/` would be a bad trade -- but the path it covers is the
# one every fresh install and every Pi takes, so it cannot go untested either.
#
#     PIFIRE_TEST_BUN_DOWNLOAD=1 uv run pytest tests/unit/updater/test_bun_discovery.py -k download

downloads = pytest.mark.skipif(
    os.environ.get("PIFIRE_TEST_BUN_DOWNLOAD") != "1",
    reason="set PIFIRE_TEST_BUN_DOWNLOAD=1 to fetch bun from the network",
)


@downloads
def test_download_lands_a_working_bun_and_puts_it_on_path(home):
    """What a machine with no bun gets. The version probe is the assertion that
    matters: an unpacked-but-broken binary would satisfy `-x` and fail later,
    inside the build, as an unexplained non-zero exit."""
    result = run_lib(
        'pifire_get_bun && echo "RESOLVED=$PIFIRE_BUN" && echo "VERSION=$("$PIFIRE_BUN" --version)"'
        ' && echo "ONPATH=$(command -v bun)"',
        home,
    )
    out = dict(
        line.split("=", 1)
        for line in result.stdout.splitlines()
        if line.startswith(("RESOLVED=", "VERSION=", "ONPATH="))
    )

    assert "Fetching a temporary bun" in result.stdout, result.stdout + result.stderr
    assert out.get("RESOLVED", "").startswith("/tmp/pifire-bun-"), result.stdout + result.stderr
    # An unpacked-but-broken binary satisfies `-x` and fails later, inside the
    # build, as an unexplained non-zero exit.
    assert re.match(r"^\d+\.\d+\.\d+", out.get("VERSION", "")), out
    # Its own directory must reach PATH: package.json's "build" re-invokes
    # `bun`, resolved through PATH by the shell bun itself spawns.
    assert out.get("ONPATH") == out.get("RESOLVED"), out


@downloads
def test_the_downloaded_bun_is_cleaned_up_when_the_shell_exits(home):
    """~90MB per rebuild otherwise. The EXIT trap is what removes it, so this
    checks the directory is gone AFTER the shell that fetched it has ended."""
    result = run_lib('pifire_get_bun >/dev/null && echo "TMPDIR=$PIFIRE_BUN_TMPDIR"', home)
    line = [ln for ln in result.stdout.splitlines() if ln.startswith("TMPDIR=")]

    assert line, result.stdout + result.stderr
    tmpdir = line[0].split("=", 1)[1]
    assert tmpdir, "the fetch path must record where it unpacked"
    assert not os.path.exists(tmpdir), f"{tmpdir} outlived the shell that made it"


@downloads
def test_a_real_download_is_not_used_when_a_local_bun_exists(home):
    """The whole point of the discovery half. Guards against a future edit that
    reorders these and quietly restores the ~90MB-per-rebuild behaviour."""
    fake_bun(str(home / ".bun" / "bin" / "bun"))

    result = run_lib('pifire_get_bun && echo "RESOLVED=$PIFIRE_BUN"', home)

    assert "Fetching a temporary bun" not in result.stdout
    assert f"RESOLVED={home / '.bun' / 'bin' / 'bun'}" in result.stdout
