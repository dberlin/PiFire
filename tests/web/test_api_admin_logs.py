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

from blueprints.api_admin import admin_api


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


def test_delete_clears_every_log_and_reports_them(env):
    """The live members stay on disk, emptied.

    This asserted the listing went to [], which pinned the unlink bug: the files
    have to survive so the handlers already holding them open keep writing where
    the viewer looks. What the user asked for is empty logs, and that is what the
    listing now shows -- present, and zero bytes.
    """
    _seed(env, "events.log", "control.log")
    with mock.patch("os.system") as m_system:
        resp = env["client"].post("/api/admin/logs/delete", json={})

    assert resp.status_code == 200
    assert sorted(resp.get_json()["data"]["removed"]) == ["control.log", "events.log"]
    assert env["client"].get("/api/admin/logs").get_json()["data"]["logs"] == [
        "control.log",
        "events.log",
    ]
    assert [p.read_text() for p in sorted(env["dir"].iterdir())] == ["", ""]
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


def test_delete_removes_rotated_members_too(env):
    """Before this, delete_logs filtered on endswith(".log") and left every
    rotated file behind -- so "Delete All" did not delete what the viewer shows,
    and the page kept displaying content after the user had cleared it."""
    _seed(env, "events.log", "events.log.1", "events.log.2", "control.log")

    with mock.patch("os.system") as m_system:
        resp = env["client"].post("/api/admin/logs/delete", json={})

    assert resp.status_code == 200
    assert sorted(resp.get_json()["data"]["removed"]) == [
        "control.log",
        "events.log",
        "events.log.1",
        "events.log.2",
    ]
    #  The rotated members go; the two live ones stay, emptied. See
    #  test_delete_keeps_open_handlers_writing_where_the_viewer_looks.
    assert sorted(p.name for p in env["dir"].iterdir()) == ["control.log", "events.log"]
    m_system.assert_not_called()


def test_download_archives_rotated_members_too(env):
    _seed(env, "events.log", "events.log.1")
    (env["dir"] / "notes.txt").write_text("not a log")

    resp = env["client"].get("/api/admin/logs/download")
    with zipfile.ZipFile(BytesIO(resp.data)) as archive:
        assert sorted(archive.namelist()) == ["events.log", "events.log.1"]
        assert "notes.txt" not in archive.namelist()


def test_delete_keeps_open_handlers_writing_where_the_viewer_looks(env):
    """Unlinking a log a handler holds open sends every later line nowhere.

    create_logger gives every logger a RotatingFileHandler, and on POSIX
    os.remove only drops the directory entry: the handler's descriptor stays
    valid and keeps appending to an orphaned inode, invisible to the viewer and
    holding its disk space until the process exits. control.py and
    display_process.py hold their own handlers on these same files and the web
    process cannot reopen them, so clearing has to work through the inode all
    three share -- truncate, do not unlink.
    """
    from logging.handlers import RotatingFileHandler

    path = env["dir"] / "events.log"
    path.write_text("before delete\n")
    handler = RotatingFileHandler(str(path))
    try:
        env["client"].post("/api/admin/logs/delete", json={})

        handler.stream.write("after delete\n")
        handler.stream.flush()

        assert path.exists()
        assert path.read_text() == "after delete\n"
    finally:
        handler.close()


def test_delete_still_unlinks_rotated_members(env):
    """Only the live member is held open, so the rotated ones are removed rather
    than emptied -- truncating them would leave the files behind forever."""
    _seed(env, "events.log", "events.log.1", "events.log.2")

    env["client"].post("/api/admin/logs/delete", json={})

    assert sorted(p.name for p in env["dir"].iterdir()) == ["events.log"]
    assert (env["dir"] / "events.log").read_text() == ""


def test_a_missing_log_folder_lists_empty(env, monkeypatch):
    monkeypatch.setattr(admin_api, "LOG_FOLDER", "/nonexistent-log-folder/")
    resp = env["client"].get("/api/admin/logs")
    assert resp.status_code == 200
    assert resp.get_json()["data"]["logs"] == []
