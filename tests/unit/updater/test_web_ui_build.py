"""When the updater rebuilds the served React bundle.

web-react/dist is git-ignored, so an update merges new React sources and leaves
the built bundle untouched -- and blueprints/spa/routes.py goes on serving it.
Nothing rebuilt it on an ordinary update: the only caller was
updater/upgrade.sh, which updater.py runs as a version-migration step listed in
updater_manifest.json under 1.10.0, so no current install still reaches it.

No test here runs a real build; rebuild_web_ui takes an injected runner.
"""

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
