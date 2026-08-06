"""Behavioral contract tests for typed SQLite control-trace persistence."""

import sqlite3

import pytest
from pydantic import ValidationError

from common import datastore
from common.control_trace import (
    ControlTraceRecord,
    ControllerType,
    SessionPayload,
    TraceEventKind,
    TraceSetting,
)
from common.datastore_accessors import (
    CONTROL_TRACE_MAX_LIMIT,
    append_control_trace,
    delete_control_trace_session,
    prune_control_trace,
    prune_incompatible_control_trace,
    read_control_trace_cook,
    read_control_trace_range,
    read_control_trace_session,
)
from controller.runtime.control_trace_recorder import RETENTION_PERIOD_MS, ControlTraceRecorder


def _record(ts_ms: int, session_id: str, cook_id: str | None = None) -> ControlTraceRecord:
    payload = SessionPayload(
        controller=ControllerType.PID,
        controller_config=(TraceSetting(key="kp", value=1.0),),
        temperature_unit="F",
        control_period_seconds=2.0,
        model_revision=None,
        model_provenance=None,
        pulse_slot_seconds=2.0,
        pulse_frame_seconds=20.0,
        fan_authority=False,
        fan_pwm_capable=True,
        fan_min_duty=0.0,
        fan_max_duty=1.0,
        setpoint=225.0,
        ambient_temperature=70.0,
        software_version="1.2.3",
        build_version="test",
    )
    return ControlTraceRecord(
        ts_ms=ts_ms,
        session_id=session_id,
        cook_id=cook_id,
        controller=ControllerType.PID,
        event_kind=TraceEventKind.SESSION,
        payload=payload,
    )


def test_control_trace_schema_has_indexed_typed_envelope(ds):
    conn = ds.connection()

    columns = {row[1]: row for row in conn.execute("PRAGMA table_info(control_trace)")}
    assert set(columns) == {
        "id",
        "ts_ms",
        "session_id",
        "cook_id",
        "controller",
        "event_kind",
        "schema_version",
        "payload",
    }
    assert columns["id"][5] == 1
    assert all(
        columns[name][3] == 1
        for name in ("ts_ms", "session_id", "controller", "event_kind", "schema_version", "payload")
    )

    table_sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='control_trace'").fetchone()[0]
    assert "CHECK(json_valid(payload))" in table_sql

    index_names = {row[1] for row in conn.execute("PRAGMA index_list(control_trace)")}
    assert index_names == {"ix_control_trace_session_id", "ix_control_trace_cook_id", "ix_control_trace_ts_ms"}
    index_columns = {
        tuple(row[2] for row in conn.execute(f"PRAGMA index_info({index_name})")) for index_name in index_names
    }
    assert index_columns == {("session_id", "id"), ("cook_id", "id"), ("ts_ms",)}


def test_v4_database_upgrades_to_v5_without_altering_existing_rows(tmp_path):
    db_path = str(tmp_path / "v4.db")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL CHECK(json_valid(value)))")
        conn.execute("INSERT INTO kv(key, value) VALUES (?, ?)", ("preserved", '{"value": 1}'))
        conn.execute("PRAGMA user_version=4")
        conn.commit()
    finally:
        conn.close()

    datastore._reset_for_tests(db_path)
    try:
        upgraded = datastore.connection()
        assert upgraded.execute("PRAGMA user_version").fetchone()[0] == 5
        assert upgraded.execute("SELECT value FROM kv WHERE key='preserved'").fetchone()[0] == '{"value": 1}'
        assert upgraded.execute("SELECT COUNT(*) FROM control_trace").fetchone()[0] == 0
    finally:
        datastore._reset_for_tests(None)


def test_batch_append_and_typed_reads_preserve_insertion_order(ds):
    first = _record(2_000, "session-a", "cook-a")
    second = _record(1_000, "session-a", "cook-a")
    other = _record(1_500, "session-b", "cook-b")

    append_control_trace([first, second, other])

    assert read_control_trace_session("session-a") == [first, second]
    assert read_control_trace_cook("cook-a") == [first, second]
    range_records = read_control_trace_range(1_000, 2_000, limit=10)
    assert range_records == [first, second, other]
    assert all(isinstance(record, ControlTraceRecord) for record in range_records)
    assert delete_control_trace_session("session-a") == 2
    assert read_control_trace_session("session-a") == []


def test_identifiers_are_normalized_for_session_cook_reads_and_session_deletion(ds):
    record = _record(1_000, "session-a", "cook-a")
    append_control_trace([record])

    assert read_control_trace_session("  session-a  ") == [record]
    assert read_control_trace_cook("  cook-a  ") == [record]
    assert delete_control_trace_session("  session-a  ") == 1
    assert read_control_trace_session("session-a") == []


def test_invalid_record_is_rejected_before_transaction(ds, monkeypatch):
    valid = _record(1_000, "session-a")
    invalid = valid.model_copy(update={"schema_version": 1})

    def transaction_must_not_open():
        raise AssertionError("invalid records must be rejected before opening a transaction")

    monkeypatch.setattr(datastore, "transaction", transaction_must_not_open)
    with pytest.raises(ValidationError):
        append_control_trace([invalid])


def test_out_of_range_append_timestamp_is_rejected_before_transaction_without_partial_effects(ds, monkeypatch):
    sqlite_max_int = 2**63 - 1
    accepted = _record(1_000, "accepted")
    rejected = _record(sqlite_max_int + 1, "rejected")

    def transaction_must_not_open():
        raise AssertionError("out-of-range timestamps must be rejected before opening a transaction")

    monkeypatch.setattr(datastore, "transaction", transaction_must_not_open)
    with pytest.raises(ValueError, match="ts_ms"):
        append_control_trace([accepted, rejected])
    assert read_control_trace_session("accepted") == []


def test_range_limit_is_bounded(ds):
    append_control_trace([_record(1_000, "session-a")])

    with pytest.raises(ValueError, match="limit"):
        read_control_trace_range(0, 2_000, limit=0)
    with pytest.raises(ValueError, match="limit"):
        read_control_trace_range(0, 2_000, limit=CONTROL_TRACE_MAX_LIMIT + 1)


def test_query_timestamps_must_fit_sqlite_signed_integers(ds):
    out_of_range = 2**63

    with pytest.raises(ValueError, match="start_ms"):
        read_control_trace_range(out_of_range, out_of_range, limit=1)
    with pytest.raises(ValueError, match="end_ms"):
        read_control_trace_range(0, out_of_range, limit=1)
    with pytest.raises(ValueError, match="before_ms"):
        prune_control_trace(out_of_range, limit=1)


def test_prune_removes_only_rows_strictly_older_than_30_days_in_bounded_batches(ds):
    now_ms = 31 * 24 * 60 * 60 * 1_000
    cutoff_ms = now_ms - 30 * 24 * 60 * 60 * 1_000
    older_first = _record(cutoff_ms - 2, "older-first")
    older_second = _record(cutoff_ms - 1, "older-second")
    boundary = _record(cutoff_ms, "boundary")
    newer = _record(cutoff_ms + 1, "newer")
    append_control_trace([older_first, older_second, boundary, newer])

    assert prune_control_trace(cutoff_ms, limit=1) == 1
    assert prune_control_trace(cutoff_ms, limit=1) == 1
    assert prune_control_trace(cutoff_ms, limit=1) == 0
    assert read_control_trace_range(cutoff_ms, cutoff_ms + 1, limit=10) == [boundary, newer]


def test_schema_filtered_reads_and_incompatible_pruning_are_bounded(ds):
    current = _record(1_000, "shared", "shared-cook")
    conn = ds.connection()
    conn.executemany(
        """
        INSERT INTO control_trace(ts_ms, session_id, cook_id, controller, event_kind, schema_version, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (999, "shared", "shared-cook", "pid", "session", 2, '"old"'),
            (998, "old-only", "old-cook", "pid", "session", 2, '"old"'),
            (997, "future-only", "future-cook", "pid", "session", 4, '"future"'),
        ],
    )
    append_control_trace([current, _record(1_001, "current-second", "shared-cook")])

    assert read_control_trace_session("shared") == [current]
    assert read_control_trace_cook("shared-cook") == [current, _record(1_001, "current-second", "shared-cook")]
    assert read_control_trace_range(0, 2_000, limit=2) == [
        current,
        _record(1_001, "current-second", "shared-cook"),
    ]

    assert prune_incompatible_control_trace(3, limit=1) == 1
    assert prune_incompatible_control_trace(3, limit=1) == 1
    assert prune_incompatible_control_trace(3, limit=1) == 0
    assert conn.execute("SELECT schema_version FROM control_trace ORDER BY id").fetchall() == [(4,), (3,), (3,)]


def test_age_pruning_never_deletes_a_future_schema_row(ds):
    conn = ds.connection()
    conn.execute(
        """
        INSERT INTO control_trace(ts_ms, session_id, cook_id, controller, event_kind, schema_version, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (0, "future", None, "pid", "session", 4, '"future"'),
    )
    append_control_trace([_record(1, "current-old"), _record(100, "current-new")])

    assert prune_control_trace(50, limit=10) == 1
    assert conn.execute("SELECT schema_version FROM control_trace ORDER BY id").fetchall() == [(4,), (3,)]


def test_recorder_maintenance_preserves_old_future_rows(ds):
    conn = ds.connection()
    conn.executemany(
        """
        INSERT INTO control_trace(ts_ms, session_id, cook_id, controller, event_kind, schema_version, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (0, "legacy", None, "pid", "session", 2, '"legacy"'),
            (0, "future", None, "pid", "session", 4, '"future"'),
        ],
    )
    append_control_trace([_record(0, "current")])

    recorder = ControlTraceRecorder(
        append=lambda _records: None,
        monotonic_clock=lambda: 0,
        wall_clock=lambda: RETENTION_PERIOD_MS + 1,
    )

    assert recorder is not None
    assert conn.execute("SELECT schema_version FROM control_trace ORDER BY id").fetchall() == [(4,)]
