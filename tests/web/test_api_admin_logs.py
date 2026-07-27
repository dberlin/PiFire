"""/api/admin/logs -- list, download, delete.

The delete endpoint is the interesting one: Flask runs
`os.system("rm logs/*.log")` inside a bare `except:`, so a failure is
indistinguishable from success and the shell is handed a glob. This surface
globs server-side, calls os.remove, and reports what actually went -- so these
tests assert on the reported list, not just on a 200.
"""

import os
import zipfile
from io import BytesIO
from unittest import mock

import pytest

import blueprints.api_admin.admin_api as admin_api


@pytest.fixture
def env(ds, tmp_path, monkeypatch):
    from app import app as flask_app

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    #  LOG_FOLDER is resolved at call time by every helper here, precisely so
    #  this monkeypatch is seen -- a `folder=LOG_FOLDER` default argument would
    #  bind once at import and silently ignore it.
    monkeypatch.setattr(admin_api, "LOG_FOLDER", str(log_dir) + os.sep)

    flask_app.config["TESTING"] = True
    with flask_app.test_client() as client:
        yield {"client": client, "dir": log_dir}


def _seed(env, *names):
    for name in names:
        (env["dir"] / name).write_text(f"contents of {name}")
    return list(names)


def test_lists_only_log_files(env):
    _seed(env, "events.log", "control.log")
    (env["dir"] / "notes.txt").write_text("not a log")

    data = env["client"].get("/api/admin/logs").get_json()["data"]
    assert data["logs"] == ["control.log", "events.log"]


def test_download_returns_a_zip_of_every_log(env):
    _seed(env, "events.log", "control.log")
    resp = env["client"].get("/api/admin/logs/download")
    assert resp.status_code == 200

    with zipfile.ZipFile(BytesIO(resp.data)) as archive:
        assert sorted(archive.namelist()) == ["control.log", "events.log"]
        assert archive.read("events.log") == b"contents of events.log"


def test_download_does_not_stage_in_a_predictable_path(env):
    """A guessable /tmp name is how an attacker plants or reads content a later
    step trusts, so the archive is built in a private mkdtemp."""
    _seed(env, "events.log")
    resp = env["client"].get("/api/admin/logs/download")
    assert resp.status_code == 200
    assert not os.path.exists("/tmp/pifire")


def test_delete_removes_every_log_and_reports_them(env):
    _seed(env, "events.log", "control.log")
    with mock.patch("os.system") as m_system:
        resp = env["client"].post("/api/admin/logs/delete", json={})

    assert resp.status_code == 200
    assert sorted(resp.get_json()["data"]["removed"]) == ["control.log", "events.log"]
    assert env["client"].get("/api/admin/logs").get_json()["data"]["logs"] == []
    m_system.assert_not_called()


def test_delete_leaves_non_log_files_alone(env):
    """`rm logs/*.log` and a server-side glob agree here; asserting it means a
    future change to a broader glob cannot pass quietly."""
    _seed(env, "events.log")
    (env["dir"] / "notes.txt").write_text("keep me")

    env["client"].post("/api/admin/logs/delete", json={})
    assert (env["dir"] / "notes.txt").exists()


def test_delete_with_no_logs_is_success_not_an_error(env):
    resp = env["client"].post("/api/admin/logs/delete", json={})
    assert resp.status_code == 200
    assert resp.get_json()["data"]["removed"] == []


def test_a_missing_log_folder_lists_empty(env, monkeypatch):
    monkeypatch.setattr(admin_api, "LOG_FOLDER", "/nonexistent-log-folder/")
    resp = env["client"].get("/api/admin/logs")
    assert resp.status_code == 200
    assert resp.get_json()["data"]["logs"] == []
