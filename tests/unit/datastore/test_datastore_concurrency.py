"""Multi-process contention stress tests (T3): prove the SQLite datastore
behaves correctly under PiFire's real multi-process topology — several
producer processes hammering a SqliteQueue, and cross-process visibility of a
committed write via a fresh connection in another process."""

import multiprocessing as mp
import os
import sqlite3
from pathlib import Path

import pytest

from common import datastore


def _producer(db, table, n):
    os.environ["PIFIRE_DB_PATH"] = db
    datastore._reset_for_tests(db)
    from common.sqlite_queue import SqliteQueue

    q = SqliteQueue(table)
    for i in range(n):
        q.push({"i": i})


def _reader(db, out):
    os.environ["PIFIRE_DB_PATH"] = db
    datastore._reset_for_tests(db)
    out.put(datastore.get_blob("control:status"))


def _open_configured_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=30)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.isolation_level = None
    return connection


def _schema_start_worker(db: str, barrier, reports) -> None:
    try:
        connection = sqlite3.connect(db, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.isolation_level = None
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        barrier.wait(timeout=30)
        datastore._ensure_schema(connection)
        reports.put(("ok", connection.execute("PRAGMA user_version").fetchone()[0]))
        connection.close()
    except BaseException as exc:
        reports.put(("error", type(exc).__name__, str(exc)))
        raise


def _run_simultaneous_schema_start(db: Path, workers: int = 6) -> list[tuple[object, ...]]:
    context = mp.get_context("spawn")
    barrier = context.Barrier(workers)
    reports = context.Queue()
    processes = [context.Process(target=_schema_start_worker, args=(str(db), barrier, reports)) for _ in range(workers)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=60)
        assert not process.is_alive(), "schema worker timed out"
    observed = [reports.get(timeout=10) for _ in processes]
    assert all(process.exitcode == 0 for process in processes), observed
    return observed


def _seed_wal_database(path: Path, *, version: str) -> None:
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
        if version == "fresh":
            return
        assert version == "v8"
        connection.executescript(
            datastore.SCHEMA
            + datastore._queue_ddl()
            + "\n"
            + datastore._logs_retention_ddl()
            + """;
CREATE TABLE legacy_v8_data (
    identity TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
INSERT INTO legacy_v8_data(identity, payload) VALUES('legacy-row', 'untouched');
PRAGMA user_version=8;
"""
        )
    finally:
        connection.close()


_V9_TABLES = {
    "learning_trajectory_corpus",
    "learning_trajectory_segment",
    "learning_trajectory_frame",
    "learning_trajectory_operation_receipt",
    "learning_fit_run",
}
_V9_INDEXES = {
    "ix_learning_segment_retention",
    "ix_learning_segment_partition",
    "ix_learning_frame_revision",
    "ix_learning_operation_source",
    "ix_learning_fit_terminal",
}
_V10_OBJECTS = {"model_challenger_state", "ix_model_challenger_identity"}


def _assert_legacy_v10_schema_complete(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone() == (10,)
        objects = {
            (row[0], row[1])
            for row in connection.execute("SELECT name, type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")
        }
        assert {(name, "table") for name in _V9_TABLES} <= objects
        assert {(name, "index") for name in _V9_INDEXES} <= objects
        assert ("model_challenger_state", "table") in objects
        assert ("ix_model_challenger_identity", "index") in objects
        assert connection.execute("SELECT COUNT(*) FROM learning_trajectory_corpus WHERE singleton=1").fetchone() == (
            1,
        )
        trigger_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='logs_prune'"
        ).fetchone()[0]
        assert " ".join(trigger_sql.split()) == " ".join(datastore._logs_retention_ddl().split())
        assert not {name for name, _type in objects if name == "history_new" or name.endswith("_new")}
    finally:
        connection.close()


def _v9_or_v10_objects(connection: sqlite3.Connection) -> set[str]:
    expected = _V9_TABLES | _V9_INDEXES | _V10_OBJECTS
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE name IN ({})".format(",".join("?" for _name in expected)),
            tuple(sorted(expected)),
        )
    }


@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / "t.db")
    os.environ["PIFIRE_DB_PATH"] = p
    datastore._reset_for_tests(p)
    datastore.init()
    yield p
    datastore._reset_for_tests(None)


def test_concurrent_producers_no_loss(db):
    from common.sqlite_queue import SqliteQueue

    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=_producer, args=(db, "queue_systemq", 200)) for _ in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)
    for p in procs:
        assert p.exitcode == 0, f"producer process failed with exitcode {p.exitcode}"
    assert SqliteQueue("queue_systemq").length() == 800  # no lost/dup under contention


def test_cross_process_visibility(db):
    datastore.set_blob("control:status", '{"mode":"Hold"}')
    ctx = mp.get_context("spawn")
    q = ctx.Queue()

    p = ctx.Process(target=_reader, args=(db, q))
    p.start()
    assert q.get(timeout=30) == '{"mode":"Hold"}'  # committed write visible in another process
    p.join(timeout=30)
    assert p.exitcode == 0


@pytest.mark.parametrize("fixture", ["fresh", "v8"])
def test_simultaneous_schema_start_is_serialized_and_complete(tmp_path: Path, fixture: str) -> None:
    for attempt in range(5):
        path = tmp_path / f"{fixture}-{attempt}.db"
        _seed_wal_database(path, version=fixture)
        reports = _run_simultaneous_schema_start(path)
        assert reports == [("ok", 10)] * 6
        _assert_legacy_v10_schema_complete(path)


def test_legacy_bootstrap_failure_rolls_back_the_entire_batch_and_retries(tmp_path: Path) -> None:
    path = tmp_path / "legacy-failure.db"
    _seed_wal_database(path, version="v8")
    connection = _open_configured_connection(path)

    def deny_v10(action, arg1, arg2, database_name, trigger_name):
        if action == sqlite3.SQLITE_PRAGMA and arg1.lower() == "user_version" and arg2 == "10":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    connection.set_authorizer(deny_v10)
    with pytest.raises(sqlite3.DatabaseError):
        datastore._ensure_schema(connection)
    connection.set_authorizer(None)

    assert connection.execute("PRAGMA user_version").fetchone() == (8,)
    assert not _v9_or_v10_objects(connection)
    assert connection.execute("SELECT payload FROM legacy_v8_data WHERE identity='legacy-row'").fetchone() == (
        "untouched",
    )

    datastore._ensure_schema(connection)
    assert connection.execute("PRAGMA user_version").fetchone() == (10,)
    connection.close()
    _assert_legacy_v10_schema_complete(path)
