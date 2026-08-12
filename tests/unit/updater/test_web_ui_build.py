"""When the updater rebuilds the served React bundle.

web-react/dist is git-ignored, so an update merges new React sources and leaves
the built bundle untouched -- and blueprints/spa/routes.py goes on serving it.
Nothing rebuilt it on an ordinary update: the only caller was
updater/upgrade.sh, which updater.py runs as a version-migration step listed in
updater_manifest.json under 1.10.0, so no current install still reaches it.

No test here runs a real build; rebuild_web_ui takes an injected runner.
"""

import io
import os

import pytest

from common.web_ui_build import rebuild_web_ui, web_ui_needs_rebuild


def write(path, text="x", mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        handle.write(text)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


@pytest.fixture
def repo(tmp_path):
    """A checkout with sources at t=1000 and a bundle built afterwards at t=2000."""
    root = str(tmp_path)
    write(os.path.join(root, "web-react", "src", "main.tsx"), mtime=1000)
    write(os.path.join(root, "web-react", "package.json"), mtime=1000)
    write(os.path.join(root, "web-react", "dist", "index.html"), mtime=2000)
    return root


def test_a_bundle_newer_than_its_sources_is_not_rebuilt(repo):
    """The whole point of the check: a backend-only update must not pay for a
    build that takes minutes on a Pi."""
    assert web_ui_needs_rebuild(repo) is False


def test_a_source_touched_after_the_build_triggers_a_rebuild(repo):
    write(os.path.join(repo, "web-react", "src", "main.tsx"), text="changed", mtime=3000)

    assert web_ui_needs_rebuild(repo) is True


def test_a_new_source_file_triggers_a_rebuild(repo):
    write(os.path.join(repo, "web-react", "src", "NewThing.tsx"), mtime=3000)

    assert web_ui_needs_rebuild(repo) is True


def test_a_changed_lockfile_triggers_a_rebuild(repo):
    """Sources are not only .tsx: a dependency bump changes what gets bundled."""
    write(os.path.join(repo, "web-react", "bun.lock"), mtime=3000)

    assert web_ui_needs_rebuild(repo) is True


def test_a_missing_bundle_triggers_a_rebuild(repo):
    os.remove(os.path.join(repo, "web-react", "dist", "index.html"))

    assert web_ui_needs_rebuild(repo) is True


def test_the_bundle_does_not_make_itself_look_stale(repo):
    """dist is the OUTPUT. Walking it would find assets newer than index.html
    -- they are written in the same build -- and every check would then demand
    another rebuild, forever."""
    write(os.path.join(repo, "web-react", "dist", "static", "js", "index.abc123.js"), mtime=9000)

    assert web_ui_needs_rebuild(repo) is False


def test_the_downloaded_toolchain_does_not_make_the_bundle_look_stale(repo):
    """web-react/.bun-toolchain is the bun downloaded to build WITH, so it is
    newer than the bundle the moment it is fetched. Walked, it would leave the
    UI permanently "older than the code on disk" -- and every rebuild would
    refresh it and stay stale, forever."""
    write(os.path.join(repo, "web-react", ".bun-toolchain", "bin", "bun"), mtime=9000)

    assert web_ui_needs_rebuild(repo) is False


def test_node_modules_does_not_make_the_bundle_look_stale(repo):
    """`bun install` restamps thousands of files without changing any source."""
    write(os.path.join(repo, "web-react", "node_modules", "react", "index.js"), mtime=9000)

    assert web_ui_needs_rebuild(repo) is False


def test_test_artifacts_do_not_trigger_a_rebuild(repo):
    for artifact in ("coverage/index.html", "test-results/run.json", "playwright-report/i.html"):
        write(os.path.join(repo, "web-react", *artifact.split("/")), mtime=9000)

    assert web_ui_needs_rebuild(repo) is False


def test_rebuild_runs_the_shared_script_and_streams_its_output(repo):
    seen = []
    calls = []

    def runner(command, on_line):
        calls.append(command)
        on_line("+ Building the web UI")
        on_line("+ Web UI built")
        return 0

    code = rebuild_web_ui(repo, seen.append, runner=runner)

    assert code == 0
    assert seen == ["+ Building the web UI", "+ Web UI built"]
    # The one build implementation stays pifire_build_web_ui, reached through
    # the wrapper -- never reimplemented here.
    assert calls[0][0] == "bash"
    assert calls[0][1].endswith(os.path.join("updater", "rebuild-web-ui.sh"))


def test_rebuild_reports_a_failing_build(repo):
    code = rebuild_web_ui(repo, lambda line: None, runner=lambda command, on_line: 1)

    assert code == 1


# ---------------------------------------------------------------------------
# updater.py's decision, which is the part that was missing entirely: the build
# was reachable only as a version-migration command, so an ordinary update
# never rebuilt the bundle at all.


def test_updater_rebuilds_when_the_bundle_is_stale(repo, monkeypatch):
    import updater

    monkeypatch.setattr(updater, "web_ui_needs_rebuild", lambda root: True)
    monkeypatch.setattr(updater, "logger", __import__("logging").getLogger("t"), raising=False)
    published = []
    monkeypatch.setattr(updater, "set_updater_install_status", lambda p, s, o: published.append((p, s, o)))
    ran = []

    def runner(command, on_line):
        ran.append(command)
        on_line("+ Building the web UI")
        return 0

    assert updater.rebuild_web_ui_if_stale(repo, runner=runner) is True
    assert ran, "a stale bundle must be rebuilt"
    assert any("+ Building the web UI" in entry[2] for entry in published), (
        "build output must reach the updater's status so it is not a silent gap"
    )


def test_updater_skips_the_rebuild_when_the_bundle_is_current(repo, monkeypatch):
    """A backend-only update must not pay for a build that takes minutes on a
    Pi -- and an update whose migration step already built it must not build
    twice."""
    import updater

    monkeypatch.setattr(updater, "web_ui_needs_rebuild", lambda root: False)
    monkeypatch.setattr(updater, "logger", __import__("logging").getLogger("t"), raising=False)
    monkeypatch.setattr(updater, "set_updater_install_status", lambda *a: None)

    def runner(command, on_line):
        pytest.fail("a current bundle must not be rebuilt")

    assert updater.rebuild_web_ui_if_stale(repo, runner=runner) is True


def test_updater_reports_a_failed_rebuild_without_claiming_success(repo, monkeypatch):
    """The backend is already on the new code; what is being served is a bundle
    built from different sources. That cannot pass silently."""
    import updater

    monkeypatch.setattr(updater, "web_ui_needs_rebuild", lambda root: True)
    monkeypatch.setattr(updater, "logger", __import__("logging").getLogger("t"), raising=False)
    published = []
    monkeypatch.setattr(updater, "set_updater_install_status", lambda p, s, o: published.append((p, s, o)))

    assert updater.rebuild_web_ui_if_stale(repo, runner=lambda c, o: 1) is False
    assert any("FAILED" in entry[2] for entry in published)


# ---------------------------------------------------------------------------
# The build transcript. logs/update.log carries the whole update -- git, apt,
# pip -- so a failed build is offered scoped between the markers updater.py
# writes around it, not as the whole file.


import logging

from common.web_ui_build import (
    BUILD_FAIL_MARKER,
    BUILD_OK_MARKER,
    BUILD_RUN_MARKER,
    last_build_failed,
    read_build_log,
)


def build_log(tmp_path, *lines):
    path = tmp_path / "update.log"
    path.write_text("".join(f"2026-08-01 12:00:0{i} +0000 | INFO | {line}\n" for i, line in enumerate(lines)))
    return str(path)


def test_the_transcript_starts_at_the_build_not_at_the_top_of_the_update(tmp_path):
    path = build_log(
        tmp_path,
        "Attempting an update on branch main",
        "Fast-forwarded to abc1234",
        BUILD_RUN_MARKER,
        "+ Building the web UI",
        BUILD_OK_MARKER,
    )

    text, _, _ = read_build_log(path=path)

    assert "+ Building the web UI" in text
    assert "Fast-forwarded" not in text, "the update's git output is not this build's output"


def test_the_transcript_is_incremental_from_an_offset(tmp_path):
    """A panel open on a running build polls once a second. Re-sending the whole
    thing every tick is what the offset exists to avoid."""
    path = build_log(tmp_path, BUILD_RUN_MARKER, "first")

    whole, offset, reset = read_build_log(path=path)
    assert reset is True
    assert "first" in whole

    with open(path, "a") as handle:
        handle.write("2026-08-01 12:00:09 +0000 | INFO | second\n")

    delta, _, again = read_build_log(offset=offset, path=path)

    assert again is False
    assert "second" in delta
    assert "first" not in delta, "a delta that re-sends what the client has already drawn"


def test_a_failed_build_is_reported_as_failed(tmp_path):
    path = build_log(tmp_path, BUILD_RUN_MARKER, "+ Building the web UI", BUILD_FAIL_MARKER)

    assert last_build_failed(path) is True


def test_a_successful_build_is_not_reported_as_failed(tmp_path):
    path = build_log(tmp_path, BUILD_RUN_MARKER, "+ Web UI built", BUILD_OK_MARKER)

    assert last_build_failed(path) is False


def test_a_build_still_running_is_not_reported_as_failed(tmp_path):
    """It has an opening marker and no closing one. Reading that as a failure
    would put the log on offer while the build was still writing it."""
    path = build_log(tmp_path, BUILD_RUN_MARKER, "+ Building the web UI")

    assert last_build_failed(path) is False


def test_a_successful_rebuild_clears_a_previous_failure(tmp_path):
    """The marker scan takes the LAST run. A failure that stayed on offer after
    the operator fixed it and rebuilt would be worse than not reporting it."""
    path = build_log(
        tmp_path,
        BUILD_RUN_MARKER,
        "+ Building the web UI",
        BUILD_FAIL_MARKER,
        BUILD_RUN_MARKER,
        "+ Web UI built",
        BUILD_OK_MARKER,
    )

    assert last_build_failed(path) is False


def test_a_log_with_no_build_in_it_is_not_a_failure(tmp_path):
    path = build_log(tmp_path, "Attempting an update on branch main", "Fast-forwarded to abc1234")

    assert last_build_failed(path) is False


def test_a_missing_log_is_not_a_failure(tmp_path):
    assert last_build_failed(str(tmp_path / "absent.log")) is False


def test_the_rebuild_writes_the_markers_the_reader_looks_for(repo, tmp_path, monkeypatch):
    """The two halves of this are in different files, and the transcript is
    empty if either drifts: updater.py writes the boundaries, web_ui_build.py
    reads them."""
    import updater

    path = tmp_path / "update.log"
    logger = logging.getLogger("test_build_markers")
    logger.handlers.clear()
    handler = logging.FileHandler(str(path))
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%Y-%m-%d %H:%M:%S %z"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    monkeypatch.setattr(updater, "logger", logger, raising=False)
    monkeypatch.setattr(updater, "web_ui_needs_rebuild", lambda root: True)
    monkeypatch.setattr(updater, "set_updater_install_status", lambda *a: None)

    def runner(command, on_line):
        on_line("error: could not resolve dependencies")
        return 1

    assert updater.rebuild_web_ui_if_stale(repo, runner=runner) is False
    handler.flush()

    assert last_build_failed(str(path)) is True
    text, _, _ = read_build_log(path=str(path))
    assert "could not resolve dependencies" in text, "the reason must survive into what is offered"

    logger.handlers.clear()


# ---------------------------------------------------------------------------
# How a run ENDS. The browser polls until percent goes above 100, so whatever
# publishes last decides whether the page ever stops.


@pytest.fixture
def quiet_updater(monkeypatch):
    """updater.py's -u and -b flows with every side effect replaced, and the
    status blob captured. Returns the list of (percent, status, output)."""
    import updater

    published = []
    monkeypatch.setattr(updater, "logger", logging.getLogger("t"), raising=False)
    monkeypatch.setattr(updater, "set_updater_install_status", lambda p, s, o: published.append((p, s, o)))
    monkeypatch.setattr(updater, "read_settings", lambda: {"versions": {"server": "1.10.10", "build": 70}})
    monkeypatch.setattr(updater, "time", type("_", (), {"sleep": staticmethod(lambda _: None)}))
    monkeypatch.setattr(updater, "install_update", lambda: (True, "Update Completed Successfully", " - ok"))
    monkeypatch.setattr(updater, "change_branch", lambda b: (True, "Branch Changed Successfully", " - ok"))
    monkeypatch.setattr(updater, "install_dependencies", lambda v, b: (0, False))
    monkeypatch.setattr(updater, "rebuild_web_ui_if_stale", lambda *a, **k: True)
    return published


def test_an_update_ends_above_100_so_the_page_stops_polling(quiet_updater):
    """The rebuild runs AFTER install_dependencies and publishes progress of its
    own, so install_dependencies ending the run left every update parked at
    percent 95 -- "Checking Web UI..." -- against a process that had exited."""
    import updater

    updater.run_update("main")

    assert quiet_updater[-1][0] > 100, quiet_updater[-3:]


def test_a_branch_change_ends_above_100_too(quiet_updater):
    import updater

    updater.run_branch_change("dev")

    assert quiet_updater[-1][0] > 100, quiet_updater[-3:]


def test_an_up_to_date_web_ui_does_not_swallow_the_finish(quiet_updater, monkeypatch):
    """The reported case: the rebuild is skipped, publishes "Web UI is already
    up to date with its sources" at 95, and that was the last word."""
    import updater

    def skipped(*a, **k):
        updater.set_updater_install_status(95, "Checking Web UI...", " - Web UI is already up to date with its sources")
        return True

    monkeypatch.setattr(updater, "rebuild_web_ui_if_stale", skipped)

    updater.run_update("main")

    assert any("already up to date" in entry[2] for entry in quiet_updater), "the skip should still be reported"
    assert quiet_updater[-1][0] > 100, quiet_updater[-3:]


def test_a_reboot_required_run_ends_on_the_reboot_sentinel(quiet_updater, monkeypatch):
    """142 rather than a plain finish, or the page tells the operator to restart
    a service when the machine needs a reboot."""
    import updater

    monkeypatch.setattr(updater, "install_dependencies", lambda v, b: (0, True))

    updater.run_update("main")

    assert quiet_updater[-1][0] == updater.REBOOT_REQUIRED_PERCENT


def test_a_failed_pull_ends_negative_and_never_rebuilds(quiet_updater, monkeypatch):
    import updater

    monkeypatch.setattr(updater, "install_update", lambda: (False, "ERROR Performing Update.", " - detached"))
    monkeypatch.setattr(
        updater, "rebuild_web_ui_if_stale", lambda *a, **k: pytest.fail("a failed pull must not rebuild")
    )

    with pytest.raises(SystemExit):
        updater.run_update("main")

    assert quiet_updater[-1][0] < 0


# ---------------------------------------------------------------------------
# The version the updater records after installing. settings["versions"] is the
# cursor install_dependencies selects migrations by, and nothing wrote it back
# on a SQLite install: the only code that does is in settings_migration.py,
# reached through read_settings_file(), the JSON reader that now runs on first
# boot and a backup restore and nowhere else. So a grill kept the version it was
# first installed with -- the page reported it forever, and every update re-ran
# every migration above it.


@pytest.fixture
def version_store(monkeypatch):
    """A stored settings dict the recorder can read and write. Returns it, so a
    test can assert on what was written."""
    import updater

    stored = {"versions": {"server": "1.10.10", "cookfile": "1.5.0", "recipe": "1.0.0", "build": 70}}
    monkeypatch.setattr(updater, "logger", logging.getLogger("t"), raising=False)
    monkeypatch.setattr(updater, "read_settings", lambda: stored)
    monkeypatch.setattr(updater, "write_settings", lambda s: stored.update(s))
    return stored


def manifest(server, build):
    return {"metadata": {"versions": {"server": server, "cookfile": "1.5.0", "recipe": "1.0.0", "build": build}}}


@pytest.mark.parametrize(
    ("server", "build", "expected_recorded", "expected_server", "expected_build"),
    [
        ("1.11.0", 71, True, "1.11.0", 71),
        # Releases move the build without the version far more often than not.
        ("1.10.10", 71, True, None, 71),
        ("1.10.10", 70, False, None, 70),
    ],
    ids=["newer_version", "build_bump_alone", "same_version_and_build"],
)
def test_manifest_version_build_recorded_or_not(
    version_store, server, build, expected_recorded, expected_server, expected_build
):
    import updater

    assert updater.record_installed_version(manifest(server, build)) is expected_recorded
    if expected_server is not None:
        assert version_store["versions"]["server"] == expected_server
    assert version_store["versions"]["build"] == expected_build


def test_an_older_manifest_does_not_wind_the_version_back(version_store):
    """A branch change onto older code. install_dependencies' own walk already
    skips entries at or below the cursor, and unwinding one is
    settings_migration's downgrade path, not this."""
    import updater

    assert updater.record_installed_version(manifest("1.9.0", 23)) is False
    assert version_store["versions"]["server"] == "1.10.10"


def test_a_manifest_with_no_versions_is_refused_not_crashed(version_store):
    import updater

    assert updater.record_installed_version({"metadata": {}}) is False
    assert version_store["versions"]["server"] == "1.10.10"


def test_the_whole_versions_block_is_carried_over(version_store):
    """cookfile and recipe move with the release; settings_migration assigns the
    block wholesale and this matches it."""
    import updater

    updater.record_installed_version(
        {"metadata": {"versions": {"server": "1.11.0", "cookfile": "1.6.0", "recipe": "1.1.0", "build": 71}}}
    )

    assert version_store["versions"] == {
        "server": "1.11.0",
        "cookfile": "1.6.0",
        "recipe": "1.1.0",
        "build": 71,
    }


class _FakeProcess:
    """Enough of Popen for install_dependencies' command loop, and nothing that
    could reach a real shell."""

    def __init__(self, code):
        self._code = code
        self.stdout = io.StringIO("a line of output\n")

    def poll(self):
        return self._code


@pytest.fixture
def one_command_update(monkeypatch, version_store):
    """install_dependencies with a single manifest command, no real subprocess.

    Returns a setter for that command's exit code."""
    import updater

    codes = {"exit": 0}
    version_store["globals"] = {"python_exec": "python", "uv": False}
    monkeypatch.setattr(updater, "DEBUG", False, raising=False)
    monkeypatch.setattr(updater, "set_updater_install_status", lambda *a: None)
    monkeypatch.setattr(updater, "set_wizard_install_status", lambda *a: None)
    monkeypatch.setattr(updater, "time", type("_", (), {"sleep": staticmethod(lambda _: None)}))
    monkeypatch.setattr(
        updater,
        "read_updater_manifest",
        lambda: {
            "metadata": {"versions": {"server": "1.11.0", "cookfile": "1.5.0", "recipe": "1.0.0", "build": 71}},
            "versions": [
                {
                    "version": "1.11.0",
                    "build": 71,
                    "reboot_required": False,
                    "dependencies": {
                        "app": {"py_dependencies": [], "apt_dependencies": [], "command_list": [["true"]]}
                    },
                }
            ],
        },
    )
    monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **k: _FakeProcess(codes["exit"]))
    monkeypatch.setattr(
        updater.subprocess, "call", lambda *a, **k: pytest.fail("a real subprocess escaped the test harness")
    )
    return codes


def test_a_clean_install_records_the_manifest_version(one_command_update, version_store):
    import updater

    result, _ = updater.install_dependencies("1.10.10", 70)

    assert result == 0
    assert version_store["versions"]["server"] == "1.11.0"
    assert version_store["versions"]["build"] == 71


def test_a_failed_dependency_step_leaves_the_version_alone(one_command_update, version_store):
    """The cursor is what makes the next run retry those migrations. Recording it
    after a failure would skip them permanently."""
    import updater

    one_command_update["exit"] = 1

    result, _ = updater.install_dependencies("1.10.10", 70)

    assert result != 0
    assert version_store["versions"]["server"] == "1.10.10", "a failed run must not claim the new version"
    assert version_store["versions"]["build"] == 70
