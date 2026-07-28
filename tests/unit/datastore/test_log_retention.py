"""Retention for the `logs` table.

The file sink is bounded: RotatingFileHandler(maxBytes=1 MiB, backupCount=3)
caps each logger at roughly 4 MiB. The database sink had no bound at all --
there is no DELETE FROM logs anywhere in the tree except clear_log -- so on a
real Pi the table grows onto the SD card forever.

Retention lives in a trigger rather than in SqliteLogHandler. See
common.datastore._logs_retention_ddl for why: a Python-side counter is
per-handler-instance and per-process, and reset_loggers() zeroes it.
"""

from common import datastore
from common.datastore import LOG_RETENTION_ROWS, PRUNE_INTERVAL


def _write(name, count):
    for i in range(count):
        datastore.execute_write("INSERT INTO logs(name, ts, message) VALUES(?,?,?)", (name, i, f"{name}-{i}"))


def test_keeps_only_the_newest_rows(ds):
    _write("events", 10)
    datastore.prune_log("events", 4)
    #  read_log returns newest first.
    assert datastore.read_log("events") == [f"events-{i}" for i in (9, 8, 7, 6)]


def test_leaves_other_loggers_untouched(ds):
    """Interleaved on purpose.

    `id` is ONE global AUTOINCREMENT sequence shared by every logger, so any
    `MAX(id) - keep` arithmetic lands on the wrong row wherever two loggers
    interleave -- which on a running grill is always.
    """
    for _ in range(10):
        _write("events", 1)
        _write("mqtt", 1)

    datastore.prune_log("events", 2)

    assert len(datastore.read_log("events")) == 2
    assert len(datastore.read_log("mqtt")) == 10


def test_fewer_rows_than_keep_is_a_no_op(ds):
    _write("events", 3)
    datastore.prune_log("events", 100)
    assert len(datastore.read_log("events")) == 3


def test_an_empty_logger_is_a_no_op(ds):
    datastore.prune_log("never-written", 10)
    assert datastore.read_log("never-written") == []


def test_the_trigger_bounds_the_table_without_any_handler(ds, monkeypatch):
    """A bare INSERT is enough -- no SqliteLogHandler in sight.

    This is the property a counter in the handler could not give: anything that
    writes to the table is bounded, including a process whose handler was just
    rebuilt and whose count would have restarted from zero.
    """
    monkeypatch.setattr(datastore, "LOG_RETENTION_ROWS", 5)
    monkeypatch.setattr(datastore, "PRUNE_INTERVAL", 10)
    datastore._reset_for_tests(datastore.DB_PATH)  # reconnect so the new DDL is applied
    datastore.init()

    _write("events", 30)

    remaining = datastore.read_log("events")
    #  The trigger fires when the global id hits a multiple of 10, trimming to
    #  the newest 5. Between firings the table runs slightly above the bound.
    assert len(remaining) <= 10
    assert remaining[0] == "events-29"


def test_the_trigger_only_trims_the_logger_that_wrote_the_row(ds, monkeypatch):
    monkeypatch.setattr(datastore, "LOG_RETENTION_ROWS", 2)
    monkeypatch.setattr(datastore, "PRUNE_INTERVAL", 4)
    datastore._reset_for_tests(datastore.DB_PATH)
    datastore.init()

    _write("mqtt", 20)
    _write("events", 3)

    #  events never reached the bound, so nothing of it was trimmed even though
    #  mqtt's writes tripped the trigger repeatedly.
    assert len(datastore.read_log("events")) == 3


def test_the_defaults_are_the_agreed_ones():
    """Confirmed 2026-07-28. Pruning is destructive and irreversible, so these
    two numbers are pinned rather than left to drift."""
    assert LOG_RETENTION_ROWS == 20_000
    assert PRUNE_INTERVAL == 1_000
