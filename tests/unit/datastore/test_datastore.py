import json
import logging
import os
import sqlite3
import threading

import pytest

from common import datastore


def test_pragmas_applied(ds):
    conn = ds.connection()
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_schema_tables_exist(ds):
    names = {r[0] for r in ds.connection().execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in [
        "kv",
        "history",
        "metrics",
        "logs",
        "control_trace",
        "queue_control_write",
        "queue_systemq",
        "queue_systemo",
        "queue_displayq",
        "queue_autotune",
        "list_warnings",
        "list_users_connected",
    ]:
        assert t in names, t


def test_init_idempotent(ds):
    ds.init()  # second call must not raise
    assert ds.connection().execute("PRAGMA user_version").fetchone()[0] == ds.DB_SCHEMA_VERSION


def test_init_existing_refuses_to_create_missing_database(tmp_path):
    original_db_path = datastore.DB_PATH
    database_path = tmp_path / "missing.db"
    try:
        datastore._reset_for_tests(str(database_path))
        with pytest.raises(datastore.DatabaseNotFoundError):
            datastore.init_existing()
        assert not database_path.exists()
    finally:
        datastore._reset_for_tests(original_db_path)


def test_init_existing_surfaces_permission_error(monkeypatch, tmp_path):
    original_db_path = datastore.DB_PATH
    database_path = tmp_path / "pifire.db"

    def deny_connection(*_args, **_kwargs):
        raise sqlite3.OperationalError("unable to open database file")

    def deny_stat(_path):
        raise PermissionError("permission denied")

    try:
        datastore._reset_for_tests(str(database_path))
        monkeypatch.setattr(datastore.sqlite3, "connect", deny_connection)
        monkeypatch.setattr(datastore.os, "stat", deny_stat)
        with pytest.raises(PermissionError, match="permission denied"):
            datastore.init_existing()
    finally:
        datastore._reset_for_tests(original_db_path)


def test_init_existing_applies_to_later_thread_connections(tmp_path):
    original_db_path = datastore.DB_PATH
    database_path = tmp_path / "existing.db"
    errors = []
    try:
        datastore._reset_for_tests(str(database_path))
        datastore.init()
        datastore._reset_for_tests(str(database_path))
        datastore.init_existing()
        datastore.connection().close()
        datastore._local.conn = None
        database_path.unlink()

        thread = threading.Thread(target=lambda: _capture_connection_error(errors))
        thread.start()
        thread.join(timeout=2)

        assert not thread.is_alive()
        assert len(errors) == 1
        assert isinstance(errors[0], datastore.DatabaseNotFoundError)
        assert not database_path.exists()
    finally:
        datastore._reset_for_tests(original_db_path)


def _capture_connection_error(errors):
    try:
        datastore.connection()
    except Exception as exc:
        errors.append(exc)


def test_kv_check_rejects_non_json(ds):
    with pytest.raises(sqlite3.IntegrityError):
        ds.execute_write("INSERT INTO kv(key,value) VALUES('x','{not json')")


def test_transaction_rolls_back_on_error(ds):
    with pytest.raises(RuntimeError), ds.transaction() as conn:
        conn.execute("INSERT INTO kv(key,value) VALUES('a','1')")
        raise RuntimeError("boom")
    assert ds.connection().execute("SELECT COUNT(*) FROM kv WHERE key='a'").fetchone()[0] == 0


def test_schema_lazy_without_init(tmp_path):
    """Regression test: accessors must work on a fresh DB even without calling
    init() first, matching Valkey's always-available semantics."""
    datastore._reset_for_tests(str(tmp_path / "fresh.db"))
    try:
        datastore.set_blob("k", '{"a":1}')
        assert datastore.get_blob("k") == '{"a":1}'

        names = {r[0] for r in datastore.connection().execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in [
            "kv",
            "history",
            "metrics",
            "logs",
            "queue_control_write",
            "control_trace",
            "queue_systemq",
            "queue_systemo",
            "queue_displayq",
            "queue_autotune",
            "list_warnings",
            "list_users_connected",
        ]:
            assert t in names, t
    finally:
        datastore._reset_for_tests(None)


def test_reset_for_tests_restores_db_path_on_none(tmp_path):
    """Regression test: _reset_for_tests(None) restores DB_PATH to
    datastore._ORIGINAL_DB_PATH.

    Does not force DB_PATH to any particular value before or after this
    test runs beyond restoring exactly what it found here: DB_PATH is
    process-global, and code besides this test reads it too -- including
    background threads from other tests that can still be finishing up
    (e.g. a checkpoint persistence worker that outlives its own test by a
    moment). An earlier version of this test called `_reset_for_tests(None)`
    as a "known-good starting point" before doing anything else, and forced
    it back to `_ORIGINAL_DB_PATH` again in a bare `finally` regardless of
    what was actually ambient beforehand -- both are extra, unnecessary
    writes to state this test does not own. A controlled A/B sweep (this
    fix vs. the file reverted to its pre-task-18 state, ~20 full-suite runs
    each) found the same family of pre-existing, load-sensitive
    controller/runtime checkpoint-persistence flakes at a similar rate in
    BOTH cases, including with this file completely unmodified -- so that
    concern turned out unrelated to this test, but the smaller footprint is
    worth keeping on its own merits.

    Asserting the None-restores-original property against
    `datastore._ORIGINAL_DB_PATH` directly (the actual documented fallback --
    see `_reset_for_tests`'s docstring) rather than against a value this test
    force-reset moments earlier is both a more precise check and a strictly
    smaller footprint on shared global state: whatever this test finds
    ambient on entry, it puts back exactly that on exit, nothing more.
    """
    original_db_path = datastore.DB_PATH
    temp_db_path = str(tmp_path / "temp.db")
    try:
        datastore._reset_for_tests(temp_db_path)
        assert datastore.DB_PATH == temp_db_path

        datastore._reset_for_tests(None)
        assert datastore.DB_PATH == datastore._ORIGINAL_DB_PATH
        assert datastore.DB_PATH.endswith("pifire.db")
    finally:
        #  Restore exactly what was ambient on entry -- not
        #  _reset_for_tests(None), which forces DB_PATH to
        #  _ORIGINAL_DB_PATH regardless of what this test actually found,
        #  a bigger footprint on shared state than "put it back" requires.
        datastore._reset_for_tests(original_db_path)


def test_blob_roundtrip_and_missing(ds):
    assert ds.get_blob("k") is None  # missing -> None (matches Valkey)
    ds.set_blob("k", '{"a": 1}')
    assert ds.get_blob("k") == '{"a": 1}'
    assert ds.exists_blob("k") is True
    ds.set_blob("k", '{"a": 2}')  # overwrite
    assert ds.get_blob("k") == '{"a": 2}'
    ds.delete_blob("k")
    assert ds.get_blob("k") is None
    assert ds.exists_blob("k") is False


def test_log_handler_and_read(ds):
    from common.sqlite_log_handler import SqliteLogHandler

    logger = logging.getLogger("t_events")
    logger.setLevel(logging.INFO)
    logger.addHandler(SqliteLogHandler("events"))
    logger.info("first")
    logger.info("second")
    assert ds.read_log("events", num=1) == ["second"]  # newest-first, limited
    assert ds.read_log("events") == ["second", "first"]
    ds.clear_log("events")
    assert ds.read_log("events") == []


def test_retry_bounded_by_wall_clock_deadline(monkeypatch):
    """FIX 4: _retry must not burn through all `attempts` when each attempt
    itself is slow (e.g. blocked inside SQLite's busy_timeout) -- it must
    give up once the wall-clock deadline is exceeded, well short of the
    worst-case attempts*busy_timeout (50 * 5s ~= 4min)."""
    calls = []
    fake_time = [0.0]

    def fake_monotonic():
        return fake_time[0]

    def fake_sleep(secs):
        fake_time[0] += secs

    def always_busy():
        calls.append(1)
        fake_time[0] += 1.0  # simulate ~1s blocked inside this attempt
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(datastore.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(datastore.time, "sleep", fake_sleep)

    with pytest.raises(sqlite3.OperationalError, match="deadline"):
        datastore._retry(always_busy, attempts=50, deadline_s=10.0)

    # Each simulated attempt costs >=1s; hitting the 10s deadline must stop
    # retries well short of all 50 attempts.
    assert len(calls) < 50


def test_retry_uncontended_case_unchanged():
    """Common case: no contention, first attempt succeeds immediately --
    the deadline machinery must not add overhead or change behavior."""
    assert datastore._retry(lambda: 42) == 42


def test_metrics_v1_blob_db_migrates_to_columnar(tmp_path):
    """Regression: an existing pre-fast-follow DB (old blob metrics(id, data)
    table, user_version=1) must be upgraded in place to the columnar schema
    on next connect, with the database advanced through the current schema
    version (including durable model-activation and trajectory-corpus tables).
    In-progress metric loss is expected/acceptable on this one-time upgrade.
    """
    db_path = str(tmp_path / "v1.db")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE metrics (id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL CHECK(json_valid(data)))"
        )
        conn.execute("INSERT INTO metrics(data) VALUES(?)", (json.dumps({"mode": "Hold"}),))
        conn.execute("PRAGMA user_version=1")
        conn.commit()
    finally:
        conn.close()

    datastore._reset_for_tests(db_path)
    try:
        conn = datastore.connection()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == datastore.DB_SCHEMA_VERSION
        cols = {r[1] for r in conn.execute("PRAGMA table_info(metrics)")}
        assert "seq" in cols
        assert "mode" in cols
        assert "data" not in cols
        # Symmetry with the v2->current-schema migration test below: pellet_level_start
        # must have NUMERIC affinity (not REAL), so integer values round-trip
        # as ints instead of being coerced to floats.
        affinities = {r[1]: r[2] for r in conn.execute("PRAGMA table_info(metrics)")}
        assert affinities["pellet_level_start"] == "NUMERIC"
        # Table is empty post-migration (in-progress metric loss is expected).
        assert conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0] == 0
    finally:
        datastore._reset_for_tests(None)


def test_metrics_v2_real_affinity_db_migrates_to_numeric(tmp_path):
    """Regression: a DB already upgraded to the columnar schema at v2 (numeric
    metric columns declared REAL) must be rebuilt in place with NUMERIC
    affinity on next connect, advancing through the current schema. REAL affinity
    silently coerced integer values (e.g. pellet_level_start=87) to floats
    (87.0) on round-trip; NUMERIC affinity fixes that.
    """
    db_path = str(tmp_path / "v2.db")
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
CREATE TABLE metrics (
    seq                 INTEGER PRIMARY KEY AUTOINCREMENT,
    id                  TEXT,
    starttime           REAL,
    mode                TEXT,
    pellet_level_start  REAL,
    pellet_level_end    REAL
);
"""
        )
        conn.execute("INSERT INTO metrics(id, mode, pellet_level_start) VALUES ('abc', 'Hold', 87)")
        conn.execute("PRAGMA user_version=2")
        conn.commit()
    finally:
        conn.close()

    datastore._reset_for_tests(db_path)
    try:
        conn = datastore.connection()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == datastore.DB_SCHEMA_VERSION
        cols = {r[1]: r[2] for r in conn.execute("PRAGMA table_info(metrics)")}
        assert cols["pellet_level_start"] == "NUMERIC"
        assert cols["starttime"] == "NUMERIC"
        # Table is empty post-migration (in-progress metric loss is expected).
        assert conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0] == 0
    finally:
        datastore._reset_for_tests(None)


def test_metrics_current_schema_migration_idempotent(tmp_path):
    """A fresh database at the current schema must not be destructively
    migrated on reconnect."""
    db_path = str(tmp_path / "current.db")
    datastore._reset_for_tests(db_path)
    try:
        conn = datastore.connection()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == datastore.DB_SCHEMA_VERSION
        datastore.execute_write("INSERT INTO metrics(id, mode) VALUES ('abc', 'Hold')")
        datastore._reset_for_tests(db_path)  # drop cached connection, keep file
        conn = datastore.connection()  # reconnect -> _ensure_schema runs again
        assert conn.execute("PRAGMA user_version").fetchone()[0] == datastore.DB_SCHEMA_VERSION
        # Row must survive: idempotent migration must not re-drop the table.
        assert conn.execute("SELECT mode FROM metrics WHERE id=?", ("abc",)).fetchone()[0] == "Hold"
    finally:
        datastore._reset_for_tests(None)


def test_history_v3_real_affinity_db_migrates_to_numeric_preserving_rows(tmp_path):
    """Regression: a DB at schema version 3 (history.psp declared REAL) must
    be rebuilt in place with NUMERIC affinity on next connect, advancing
    through the current schema -- and, unlike the transient metrics migrations
    above, every existing durable history row must be PRESERVED.
    Re-storing an existing REAL value (225.0) through the rebuilt NUMERIC
    column must normalize it back to an int (225)."""
    db_path = str(tmp_path / "v3_history.db")
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
CREATE TABLE history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             INTEGER NOT NULL,
    psp            REAL,
    primary_temps  TEXT NOT NULL CHECK(json_valid(primary_temps)),
    food_temps     TEXT NOT NULL CHECK(json_valid(food_temps)),
    aux_temps      TEXT NOT NULL CHECK(json_valid(aux_temps)),
    notify_targets TEXT NOT NULL CHECK(json_valid(notify_targets)),
    ext_data       TEXT CHECK(ext_data IS NULL OR json_valid(ext_data))
);
"""
        )
        conn.execute(
            "INSERT INTO history(ts, psp, primary_temps, food_temps, aux_temps, notify_targets) "
            "VALUES (1000, 225.0, '{\"Grill\": 225}', '{}', '{}', '{}')"
        )
        conn.execute("PRAGMA user_version=3")
        conn.commit()
    finally:
        conn.close()

    datastore._reset_for_tests(db_path)
    try:
        conn = datastore.connection()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == datastore.DB_SCHEMA_VERSION
        cols = {r[1]: r[2] for r in conn.execute("PRAGMA table_info(history)")}
        assert cols["psp"] == "NUMERIC"

        # Row must survive the rebuild-and-swap (history is durable).
        rows = conn.execute("SELECT ts, psp, primary_temps FROM history").fetchall()
        assert len(rows) == 1
        ts, psp, primary_temps = rows[0]
        assert ts == 1000
        assert primary_temps == '{"Grill": 225}'
        # Re-storing the old REAL value through the NUMERIC column normalizes
        # it: 225.0 (float) -> 225 (int).
        assert psp == 225
        assert isinstance(psp, int)

        # read_history()/PSP end-to-end must also yield an int now.
        from common.persistence import history as history_persistence

        assert isinstance(history_persistence.read_history()[0]["PSP"], int)
    finally:
        datastore._reset_for_tests(None)


def test_history_migration_crash_mid_rebuild_rolls_back(tmp_path, monkeypatch):
    """Regression/atomicity guard: a crash partway through the history rebuild
    (after history_new is created and populated, but before the swap finishes)
    must roll back cleanly -- the original `history` table (and its row) must
    survive untouched, `user_version` must NOT be bumped to 4, and no leftover
    `history_new` shadow table must remain. This is only true because
    `_migrate_history_to_numeric_psp` runs inside `transaction(conn)` using
    plain `execute()` calls; if a future edit switched it to `executescript()`
    (which implicitly commits before each statement) the rollback guarantee
    would break and this test would catch it -- either by the injected fault
    no longer firing (executescript doesn't go through Connection.execute) or
    by leftover state surviving the crash."""
    db_path = str(tmp_path / "crash_history.db")
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
CREATE TABLE history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             INTEGER NOT NULL,
    psp            REAL,
    primary_temps  TEXT NOT NULL CHECK(json_valid(primary_temps)),
    food_temps     TEXT NOT NULL CHECK(json_valid(food_temps)),
    aux_temps      TEXT NOT NULL CHECK(json_valid(aux_temps)),
    notify_targets TEXT NOT NULL CHECK(json_valid(notify_targets)),
    ext_data       TEXT CHECK(ext_data IS NULL OR json_valid(ext_data))
);
"""
        )
        conn.execute(
            "INSERT INTO history(ts, psp, primary_temps, food_temps, aux_temps, notify_targets) "
            "VALUES (1000, 225.0, '{\"Grill\": 225}', '{}', '{}', '{}')"
        )
        conn.execute("PRAGMA user_version=3")
        conn.commit()
    finally:
        conn.close()

    datastore._reset_for_tests(db_path)
    try:
        # sqlite3.Connection is a C-level type and can't be monkeypatched
        # directly -- subclass it (supported via the `factory=` kwarg to
        # sqlite3.connect) and override execute() to inject the fault.
        class _CrashingConnection(sqlite3.Connection):
            def execute(self, sql, *args, **kwargs):
                # Fires right after history_new is created+populated, before
                # the DROP/RENAME swap -- the worst point mid-rebuild to crash.
                if isinstance(sql, str) and sql.strip() == "DROP TABLE history":
                    raise RuntimeError("injected crash mid-rebuild")
                return super().execute(sql, *args, **kwargs)

        orig_connect = sqlite3.connect

        def crashing_connect(*args, **kwargs):
            kwargs["factory"] = _CrashingConnection
            return orig_connect(*args, **kwargs)

        with monkeypatch.context() as m:
            m.setattr(datastore.sqlite3, "connect", crashing_connect)
            with pytest.raises(RuntimeError, match="injected crash mid-rebuild"):
                datastore.connection()

        # Inspect the DB file directly (a fresh, unpatched connection) to
        # prove the rollback actually held on disk.
        check = sqlite3.connect(db_path)
        try:
            assert check.execute("PRAGMA user_version").fetchone()[0] == 3  # NOT bumped
            names = {r[0] for r in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert "history" in names  # original table still present
            assert "history_new" not in names  # no leftover shadow table

            row = check.execute("SELECT ts, psp, primary_temps FROM history").fetchone()
            assert row == (1000, 225.0, '{"Grill": 225}')  # original row untouched
        finally:
            check.close()

        # A clean reconnect (no injected failure) must complete the migration.
        datastore._reset_for_tests(db_path)  # drop any cached connection, keep the file
        conn2 = datastore.connection()
        assert conn2.execute("PRAGMA user_version").fetchone()[0] == datastore.DB_SCHEMA_VERSION
        row2 = conn2.execute("SELECT ts, psp, primary_temps FROM history").fetchone()
        assert row2 == (1000, 225, '{"Grill": 225}')
        assert isinstance(row2[1], int)
    finally:
        datastore._reset_for_tests(None)


def test_history_current_schema_migration_idempotent(tmp_path):
    """A database already at the current schema must keep history untouched
    across reconnects."""
    db_path = tmp_path / "pifire.db"
    datastore._reset_for_tests(db_path)
    try:
        conn = datastore.connection()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == datastore.DB_SCHEMA_VERSION
        conn.execute(
            "INSERT INTO history(ts, psp, primary_temps, food_temps, aux_temps, notify_targets) "
            "VALUES (2000, 165, '{}', '{}', '{}', '{}')"
        )
        datastore._reset_for_tests(db_path)  # drop cached connection, keep file
        conn = datastore.connection()  # reconnect -> _ensure_schema runs again
        assert conn.execute("PRAGMA user_version").fetchone()[0] == datastore.DB_SCHEMA_VERSION
        # Row must survive: idempotent migration must not re-drop the table.
        row = conn.execute("SELECT ts, psp FROM history").fetchone()
        assert row == (2000, 165)
    finally:
        datastore._reset_for_tests(None)


def test_no_valkey_references_in_source():
    # Pure in-process file scan (no `grep` subprocess needed): just a plain-text
    # search across the source tree, with no external process/state to exercise.
    patterns = ("import valkey", "cmdsts", "ValkeyQueue", "ValkeyHandler")
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    targets = [
        os.path.join(repo_root, "common"),
        os.path.join(repo_root, "controller"),
        os.path.join(repo_root, "blueprints"),
        os.path.join(repo_root, "control.py"),
    ]

    py_files = []
    for target in targets:
        if os.path.isfile(target):
            py_files.append(target)
        else:
            for dirpath, _dirnames, filenames in os.walk(target):
                py_files.extend(os.path.join(dirpath, f) for f in filenames if f.endswith(".py"))

    hits = []
    for path in py_files:
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
        if any(pattern in content for pattern in patterns):
            hits.append(os.path.relpath(path, repo_root))

    assert hits == [], f"stale Valkey references in: {', '.join(hits)}"


def test_v7_history_gains_duty_columns_without_losing_rows(tmp_path):
    """A pre-v8 DB gains the three duty columns in place, keeping every row.

    history is durable -- unlike metrics, it is not safe to drop and recreate --
    so this asserts the existing sample survives with its temperatures intact
    AND reads back with NULL duty rather than a fabricated zero. 0% duty is a
    real reading, so "unrecorded" has to stay distinguishable from it.
    """
    db_path = str(tmp_path / "v7.db")
    datastore._reset_for_tests(db_path)
    try:
        conn = datastore.connection()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == datastore.DB_SCHEMA_VERSION
        # Drop back to a v7-shaped history: no duty columns, one durable row.
        conn.execute("DROP TABLE history")
        conn.execute("""
            CREATE TABLE history (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                ts             INTEGER NOT NULL,
                psp            NUMERIC,
                primary_temps  TEXT NOT NULL CHECK(json_valid(primary_temps)),
                food_temps     TEXT NOT NULL CHECK(json_valid(food_temps)),
                aux_temps      TEXT NOT NULL CHECK(json_valid(aux_temps)),
                notify_targets TEXT NOT NULL CHECK(json_valid(notify_targets)),
                ext_data       TEXT CHECK(ext_data IS NULL OR json_valid(ext_data))
            )
        """)
        conn.execute(
            "INSERT INTO history(ts,psp,primary_temps,food_temps,aux_temps,notify_targets,ext_data) "
            "VALUES(?,?,?,?,?,?,NULL)",
            (1700000000000, 225, json.dumps({"Grill": 224}), json.dumps({"P1": 145}), "{}", "{}"),
        )
        conn.execute("PRAGMA user_version=7")
        conn.commit()
    finally:
        datastore._reset_for_tests(db_path)

    try:
        conn = datastore.connection()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == datastore.DB_SCHEMA_VERSION

        affinities = {r[1]: r[2] for r in conn.execute("PRAGMA table_info(history)")}
        assert affinities["cycle_ratio"] == "REAL"
        assert affinities["realized_cycle_ratio"] == "REAL"
        # NUMERIC, not REAL: fan_duty is a whole percent and must not round-trip
        # 65 as 65.0, for the same reason psp uses NUMERIC.
        assert affinities["fan_duty"] == "NUMERIC"

        from common.persistence.history import read_history

        rows = read_history()
        assert len(rows) == 1, "the pre-v8 history row did not survive the migration"
        assert rows[0]["P"] == {"Grill": 224}
        assert rows[0]["PSP"] == 225
        assert rows[0]["CR"] is None
        assert rows[0]["RCR"] is None
        assert rows[0]["FD"] is None
    finally:
        datastore._reset_for_tests(str(tmp_path / "unused.db"))


def test_current_schema_migration_is_idempotent_on_an_already_migrated_db(tmp_path):
    """Reconnecting must not reapply an already-completed schema step.

    A fresh database is created by SCHEMA with all three duty columns and
    user_version 0, so it walks the v8 duty-column and v9 trajectory-corpus
    branches; their guards keep an already-current reconnect idempotent.
    """
    db_path = str(tmp_path / "fresh.db")
    datastore._reset_for_tests(db_path)
    try:
        assert datastore.connection().execute("PRAGMA user_version").fetchone()[0] == datastore.DB_SCHEMA_VERSION
    finally:
        datastore._reset_for_tests(db_path)

    conn = datastore.connection()  # second connect over the same file
    assert conn.execute("PRAGMA user_version").fetchone()[0] == datastore.DB_SCHEMA_VERSION
    cols = {r[1] for r in conn.execute("PRAGMA table_info(history)")}
    assert {"cycle_ratio", "realized_cycle_ratio", "fan_duty"} <= cols
    datastore._reset_for_tests(str(tmp_path / "unused.db"))
