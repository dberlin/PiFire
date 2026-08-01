"""Finding, and keeping, the bun the web UI is built with.

Two failures on one grill produced this file. A rebuild triggered from the web
UI runs in a process descended from the service manager, so:

  - it has the system PATH, not the one a login shell assembles, and
  - it need not have HOME set at all.

The first sent it to the download every time. The second broke the download
after it had succeeded -- bun's installer runs with `set -u` and dereferences
$HOME to pretty-print its success line and to find the shell rc files it wants
to append to:

    + Fetching a temporary bun to build the web UI (not installed system-wide)
    ######################################################################## 100.0%
    bash: line 164: HOME: unbound variable
    bun was installed successfully to
    !! bun did not land at /tmp/pifire-bun-pOYiaP/bin/bun.

Only explicit signals are honoured now -- a bun on PATH, or $BUN_INSTALL -- and
what gets downloaded is kept in web-react/.bun-toolchain instead of a temp dir,
so it is fetched once rather than per rebuild.

By default nothing here touches the network: these drive pifire_locate_bun, the
offline half, against fake executables in tmp_path. Calling pifire_get_bun
instead fetches ~90MB on every miss -- which it did, once, before this note
existed. The fetch path has its own tests at the bottom, behind
PIFIRE_TEST_BUN_DOWNLOAD=1.
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

#: A systemd service's default. Deliberately without any version manager's
#: directory or a user's ~/.bun -- that absence is the whole subject.
SERVICE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def run_lib(script, extra_env=None):
    """Source the install library under a service-like environment and run
    `script`. `env -i` is what makes this a test rather than a coincidence: an
    inherited PATH from the pytest process would have bun on it, and an
    inherited HOME would hide the crash this file is named after.

    HOME is UNSET unless a caller asks for it -- that is how a service runs."""
    env = {"PATH": SERVICE_PATH, "LOG": "/dev/null"}
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", "-c", f"source {LIB!r}; {script}"],
        capture_output=True,
        text=True,
        env=env,
    )


def fake_bun(path, version="1.2.3"):
    """An executable that answers --version, which is what pifire_bun_works
    asks of a candidate."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        handle.write(f"#!/bin/sh\necho {version}\n")
    os.chmod(path, 0o755)
    return path


@pytest.fixture
def repo(tmp_path):
    """A checkout, with the web-react directory the toolchain lives under."""
    (tmp_path / "web-react").mkdir()
    return tmp_path


def test_nothing_explicit_means_nothing_found(repo):
    """The negative control. Without it, a test that finds bun proves only that
    the machine running the suite has one."""
    result = run_lib(f"pifire_locate_bun {repo!s} >/dev/null 2>&1 || echo NOTFOUND")

    assert "NOTFOUND" in result.stdout


def test_a_bun_on_path_is_used(repo, tmp_path):
    on_path = tmp_path / "pathbin"
    fake_bun(str(on_path / "bun"))

    result = run_lib(
        f'pifire_locate_bun {repo!s} && echo "RESOLVED=$PIFIRE_BUN"',
        {"PATH": f"{on_path}:{SERVICE_PATH}"},
    )

    assert f"RESOLVED={on_path / 'bun'}" in result.stdout


def test_bun_install_is_honoured(repo, tmp_path):
    """bun's own documented variable, and the way to point at an install this
    would otherwise never see -- deliberately set, not inferred."""
    elsewhere = tmp_path / "opt" / "bun"
    fake_bun(str(elsewhere / "bin" / "bun"))

    result = run_lib(
        f'pifire_locate_bun {repo!s} && echo "RESOLVED=$PIFIRE_BUN"',
        {"BUN_INSTALL": str(elsewhere)},
    )

    assert f"RESOLVED={elsewhere / 'bin' / 'bun'}" in result.stdout


def test_home_is_never_searched(repo, tmp_path):
    """Whose home this even is, is a guess under a service manager -- and
    reaching into a version manager's private layout to run what is found there
    is not something an install script should do. An operator who wants that bun
    says so with PATH or BUN_INSTALL."""
    home = tmp_path / "home"
    fake_bun(str(home / ".bun" / "bin" / "bun"))
    fake_bun(str(home / ".local/share/mise/installs/bun/latest/bin/bun"))

    result = run_lib(
        f"pifire_locate_bun {repo!s} >/dev/null 2>&1 || echo NOTFOUND",
        {"HOME": str(home)},
    )

    assert "NOTFOUND" in result.stdout, result.stdout


def test_a_previously_downloaded_bun_is_reused(repo):
    """The point of keeping it: a grill that has downloaded bun once must not
    pay ~90MB again on the next rebuild."""
    cached = repo / "web-react" / ".bun-toolchain" / "bin" / "bun"
    fake_bun(str(cached))

    result = run_lib(f'pifire_locate_bun {repo!s} && echo "RESOLVED=$PIFIRE_BUN"')

    assert f"RESOLVED={cached}" in result.stdout, result.stdout


def test_a_truncated_download_is_not_reused(repo):
    """An interrupted fetch leaves a file that is executable and does not run.
    Taking it fails later, inside the build, as an unexplained non-zero exit."""
    cached = repo / "web-react" / ".bun-toolchain" / "bin" / "bun"
    os.makedirs(os.path.dirname(cached), exist_ok=True)
    cached.write_text("#!/bin/sh\nexit 1\n")
    os.chmod(cached, 0o755)

    result = run_lib(f"pifire_locate_bun {repo!s} >/dev/null 2>&1 || echo NOTFOUND")

    assert "NOTFOUND" in result.stdout


def test_a_non_executable_candidate_is_skipped(repo):
    cached = repo / "web-react" / ".bun-toolchain" / "bin" / "bun"
    os.makedirs(os.path.dirname(cached), exist_ok=True)
    cached.write_text("not executable")

    result = run_lib(f"pifire_locate_bun {repo!s} >/dev/null 2>&1 || echo NOTFOUND")

    assert "NOTFOUND" in result.stdout


def test_path_wins_over_the_downloaded_copy(repo, tmp_path):
    """An operator who put a bun on the service's PATH said which one to use."""
    on_path = tmp_path / "pathbin"
    fake_bun(str(on_path / "bun"))
    fake_bun(str(repo / "web-react" / ".bun-toolchain" / "bin" / "bun"))

    result = run_lib(
        f'pifire_locate_bun {repo!s} && echo "RESOLVED=$PIFIRE_BUN"',
        {"PATH": f"{on_path}:{SERVICE_PATH}"},
    )

    assert f"RESOLVED={on_path / 'bun'}" in result.stdout


def test_the_resolved_bun_goes_on_path(repo):
    """Not a convenience: package.json's "build" script is `bun run typecheck &&
    rsbuild build`, and that inner `bun` is resolved through PATH by the shell
    bun itself spawns."""
    cached = repo / "web-react" / ".bun-toolchain" / "bin" / "bun"
    fake_bun(str(cached))

    result = run_lib(f"pifire_locate_bun {repo!s} >/dev/null && command -v bun")

    assert str(cached) in result.stdout


def test_discovery_needs_no_home_at_all(repo):
    """Every path it considers comes from PATH, from BUN_INSTALL, or from the
    checkout. None of them can be affected by HOME being unset."""
    cached = repo / "web-react" / ".bun-toolchain" / "bin" / "bun"
    fake_bun(str(cached))

    result = run_lib(f'pifire_locate_bun {repo!s} && echo "RESOLVED=$PIFIRE_BUN"')

    assert "HOME" not in result.stderr
    assert f"RESOLVED={cached}" in result.stdout


def test_the_failure_report_names_both_ways_out(repo):
    """The operator can often run `bun` themselves, so "could not download"
    alone reads as a network problem when the fix is to say where theirs is."""
    toolchain = f"{repo}/web-react/.bun-toolchain"
    out = run_lib(f'pifire_report_no_bun "Could not download bun." "{toolchain}"').stdout

    assert "Could not download bun." in out
    assert f"{toolchain}/bin/bun" in out
    assert "BUN_INSTALL" in out
    assert "shim will not do" in out


# ---------------------------------------------------------------------------
# The fallback: no bun anywhere, fetch one and keep it.
#
# Opt-in, because it downloads ~90MB from bun.sh and takes tens of seconds. The
# rest of this file is deliberately offline, and a network fetch on every
# `pytest tests/` would be a bad trade -- but this is the path every fresh
# install and every Pi takes, so it cannot go untested either.
#
#     PIFIRE_TEST_BUN_DOWNLOAD=1 uv run pytest tests/unit/updater/test_bun_discovery.py -k download

downloads = pytest.mark.skipif(
    os.environ.get("PIFIRE_TEST_BUN_DOWNLOAD") != "1",
    reason="set PIFIRE_TEST_BUN_DOWNLOAD=1 to fetch bun from the network",
)


@downloads
def test_download_survives_an_unset_home_and_lands_in_the_checkout(repo):
    """The reported failure, end to end. HOME unset is how a service runs, and
    bun's installer dereferences $HOME under `set -u` AFTER the 90MB has already
    come down -- so the download succeeded and the run reported that it had
    not."""
    result = run_lib(
        f'pifire_get_bun {repo!s} && echo "RESOLVED=$PIFIRE_BUN"'
        ' && echo "VERSION=$("$PIFIRE_BUN" --version)" && echo "ONPATH=$(command -v bun)"'
    )
    out = dict(
        line.split("=", 1)
        for line in result.stdout.splitlines()
        if line.startswith(("RESOLVED=", "VERSION=", "ONPATH="))
    )

    assert "HOME: unbound variable" not in result.stdout + result.stderr
    assert out.get("RESOLVED") == str(repo / "web-react" / ".bun-toolchain" / "bin" / "bun"), (
        result.stdout + result.stderr
    )
    assert re.match(r"^\d+\.\d+\.\d+", out.get("VERSION", "")), out
    assert out.get("ONPATH") == out.get("RESOLVED"), out


@downloads
def test_the_download_does_not_touch_the_operators_home(repo, tmp_path):
    """bun's installer appends its PATH line to ~/.bashrc, ~/.zshrc and fish's
    config. For a toolchain nobody invokes directly that is pure litter, and it
    happened on every machine that had HOME set."""
    home = tmp_path / "home"
    (home / ".config" / "fish").mkdir(parents=True)
    for name in (".bashrc", ".zshrc", ".bash_profile"):
        (home / name).write_text("# untouched\n")
    (home / ".config" / "fish" / "config.fish").write_text("# untouched\n")

    run_lib(f"pifire_get_bun {repo!s} >/dev/null", {"HOME": str(home)})

    for path in home.rglob("*"):
        if path.is_file():
            assert path.read_text() == "# untouched\n", f"{path} was edited"


@downloads
def test_the_downloaded_bun_survives_for_the_next_rebuild(repo):
    """It used to be deleted on exit, so every rebuild paid the download again
    -- including the one an operator runs to recover from a failed rebuild."""
    first = run_lib(f"pifire_get_bun {repo!s} >/dev/null 2>&1 && echo OK")
    assert "OK" in first.stdout, first.stdout + first.stderr
    assert (repo / "web-react" / ".bun-toolchain" / "bin" / "bun").exists()

    second = run_lib(f'pifire_get_bun {repo!s} && echo "RESOLVED=$PIFIRE_BUN"')

    assert "Downloading bun" not in second.stdout, "it downloaded again"
    assert "Using the downloaded bun" in second.stdout


@downloads
def test_a_broken_cache_is_replaced_rather_than_unpacked_over(repo):
    """A download interrupted half way leaves a directory that is neither empty
    nor usable."""
    toolchain = repo / "web-react" / ".bun-toolchain"
    cached = toolchain / "bin" / "bun"
    os.makedirs(os.path.dirname(cached), exist_ok=True)
    cached.write_text("#!/bin/sh\nexit 1\n")
    os.chmod(cached, 0o755)
    (toolchain / "junk").write_text("left over")

    result = run_lib(f'pifire_get_bun {repo!s} && echo "VERSION=$("$PIFIRE_BUN" --version)"')

    assert re.search(r"VERSION=\d+\.\d+\.\d+", result.stdout), result.stdout + result.stderr
    assert not (toolchain / "junk").exists()
