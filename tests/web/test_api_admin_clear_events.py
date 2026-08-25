"""Clearing the event log must empty BOTH stores.

Every logger create_logger builds writes to two sinks: a RotatingFileHandler and
a SqliteLogHandler. Clearing only one leaves the other holding what the user
asked to be rid of.

The pre-existing split was the wrong way round in a way nobody could see:
common.common.read_events_records() reads the FILE, while
common.common.flush_events_records() calls datastore.clear_log("events") and so
clears the DATABASE. Clearing events therefore deleted rows nothing reads and
left the file everything reads -- observable on the development machine as one
`events` row against 1,062 lines in logs/events.log.
"""

import os

import pytest

import blueprints.api_admin.admin_api as admin_api
from common import datastore


@pytest.fixture
def logdir(ds, tmp_path, monkeypatch):
    #  A subdirectory, not tmp_path itself: the `ds` fixture drops t.db and its
    #  -wal/-shm siblings into tmp_path, and they would show up in any
    #  directory-contents assertion below.
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "events.log").write_text("a\n")
    (log_dir / "events.log.1").write_text("b\n")
    (log_dir / "events.log.2").write_text("c\n")
    (log_dir / "mqtt.log").write_text("keep me\n")
    monkeypatch.setattr(admin_api, "LOG_FOLDER", str(log_dir) + os.sep)
    return log_dir


def test_clears_the_whole_events_family(logdir):
    """The live member is emptied in place and the rotated ones are removed.

    This asserted that `events.log` itself was gone, which was the bug: the
    running process still holds that file open through a RotatingFileHandler,
    so unlinking it sent every later event into an orphaned inode instead of
    into the file the viewer reads. See
    test_clearing_keeps_open_handlers_writing_where_the_viewer_looks below.
    """
    admin_api.clear_events_log()
    assert sorted(p.name for p in logdir.iterdir()) == ["events.log", "mqtt.log"]
    assert (logdir / "events.log").read_text() == ""


def test_clearing_keeps_open_handlers_writing_where_the_viewer_looks(logdir):
    """Same defect as the admin Delete All, reached through Clear Events.

    control.py and display_process.py hold their own handlers on this file and
    the web process cannot reopen them, so the clear has to work through the
    inode all three share.
    """
    from logging.handlers import RotatingFileHandler

    path = logdir / "events.log"
    handler = RotatingFileHandler(str(path))
    try:
        admin_api.clear_events_log()

        handler.stream.write("after clear\n")
        handler.stream.flush()

        assert path.exists()
        assert path.read_text() == "after clear\n"
    finally:
        handler.close()


def test_leaves_other_families_alone(logdir):
    admin_api.clear_events_log()
    assert (logdir / "mqtt.log").read_text() == "keep me\n"


def test_also_clears_the_database_rows(logdir):
    for i in range(3):
        datastore.execute_write("INSERT INTO logs(name, ts, message) VALUES(?,?,?)", ("events", i, f"event {i}"))
    datastore.execute_write("INSERT INTO logs(name, ts, message) VALUES(?,?,?)", ("mqtt", 0, "mqtt line"))
    assert len(datastore.read_log("events")) == 3

    admin_api.clear_events_log()

    assert datastore.read_log("events") == []
    #  Only the events logger: a blanket DELETE would take the grill's own
    #  control history with it.
    assert datastore.read_log("mqtt") == ["mqtt line"]


def test_still_returns_true(logdir):
    """_MAINTENANCE_ACTIONS dispatches this and the shipped MaintenanceCard is
    built against the resulting response shape."""
    assert admin_api.clear_events_log() is True


def test_missing_files_are_success(ds, tmp_path, monkeypatch):
    empty = tmp_path / "logs"
    empty.mkdir()
    monkeypatch.setattr(admin_api, "LOG_FOLDER", str(empty) + os.sep)
    assert admin_api.clear_events_log() is True
