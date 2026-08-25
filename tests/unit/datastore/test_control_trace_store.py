"""Behavioral contract tests for typed SQLite control-trace persistence."""

import sqlite3

import pytest
from pydantic import ValidationError

from common import datastore
from common.control_trace import (
    TRACE_SCHEMA_VERSION,
    ActuationMode,
    ControllerType,
    ControlTraceRecord,
    InhibitReason,
    LearningSnapshotPayload,
    PidUpdatePayload,
    SessionPayload,
    TraceEventKind,
    TraceSetting,
)
from common.persistence.control_trace import (
    CONTROL_TRACE_MAX_LIMIT,
    append_control_trace,
    delete_control_trace_session,
    prune_control_trace,
    prune_incompatible_control_trace,
    read_control_trace_cook,
    read_control_trace_range,
    read_control_trace_session,
)
from controller.applied_output import OutputSource
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


def _learning_record(ts_ms: int, session_id: str, cook_id: str) -> ControlTraceRecord:
    return ControlTraceRecord(
        ts_ms=ts_ms,
        session_id=session_id,
        cook_id=cook_id,
        controller=ControllerType.PID,
        event_kind=TraceEventKind.CONTROL_UPDATE,
        payload=PidUpdatePayload(
            monotonic_ms=ts_ms,
            wall_ms=ts_ms,
            result_revision=3,
            result_age_ms=0,
            control_period_seconds=2.0,
            observed_dt_seconds=2.0,
            setpoint=225.0,
            measured_temperature=220.0,
            raw_output=0.4,
            requested_output=0.4,
            actuation_mode=ActuationMode.FRAMED_PULSE,
            prior_requested_auger_duty=0.3,
            prior_realized_auger_duty=0.3,
            requested_fan_duty=None,
            applied_fan_duty=None,
            output_source=OutputSource.CONTROLLER,
            inhibit_reason=InhibitReason.NONE,
            learning=LearningSnapshotPayload(
                schema_version=1,
                state={"status": "collecting", "accepted_samples": 12},
            ),
            error=5.0,
            proportional_term=0.2,
            integral_term=0.1,
            derivative_term=0.0,
            integral_accumulator=0.1,
            integral_clamped=False,
            derivative_input=0.0,
            derivative_state=0.0,
            proportional_band=100.0,
            kp=1.0,
            ki=0.1,
            kd=0.0,
            center=225.0,
            previous_temperature=219.0,
            previous_update_ms=ts_ms - 2_000,
        ),
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


def test_v4_database_upgrades_to_v7_without_altering_existing_rows(tmp_path):
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
        assert upgraded.execute("PRAGMA user_version").fetchone()[0] == 7
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


def test_schema_six_store_round_trip_retains_learning_state(ds):
    session = _record(1_000, "learning-session", "learning-cook")
    update = _learning_record(3_000, "learning-session", "learning-cook")

    append_control_trace([session, update])

    restored = read_control_trace_session("learning-session")
    assert [record.schema_version for record in restored] == [6, 6]
    assert isinstance(restored[1].payload, PidUpdatePayload)
    assert restored[1].payload.learning is not None
    assert restored[1].payload.learning.state == {
        "status": "collecting",
        "accepted_samples": 12,
    }


@pytest.mark.parametrize("schema_version", [2, 3, 4, 5])
def test_typed_reads_preserve_compatible_historical_records(ds, schema_version):
    historical = _record(1_000, f"session-v{schema_version}", "historical-cook").model_copy(
        update={"schema_version": schema_version}
    )
    append_control_trace([historical])

    assert read_control_trace_session(historical.session_id) == [historical]
    assert read_control_trace_cook("historical-cook") == [historical]
    assert read_control_trace_range(0, 2_000, limit=10) == [historical]


def test_identifiers_are_normalized_for_session_cook_reads_and_session_deletion(ds):
    record = _record(1_000, "session-a", "cook-a")
    append_control_trace([record])

    assert read_control_trace_session("  session-a  ") == [record]
    assert read_control_trace_cook("  cook-a  ") == [record]
    assert delete_control_trace_session("  session-a  ") == 1
    assert read_control_trace_session("session-a") == []


@pytest.mark.parametrize("identifier", ["", "   ", 1, None])
def test_trace_identifiers_must_be_non_blank_strings(ds, identifier):
    with pytest.raises(ValueError, match="session_id"):
        read_control_trace_session(identifier)
    with pytest.raises(ValueError, match="cook_id"):
        read_control_trace_cook(identifier)
    with pytest.raises(ValueError, match="session_id"):
        delete_control_trace_session(identifier)


def test_session_and_cook_reads_accept_an_alternate_database_path(ds, tmp_path):
    alternate_path = tmp_path / "alternate.db"
    record = _record(1_000, "alternate-session", "alternate-cook")
    row = record.to_db_row()
    connection = sqlite3.connect(alternate_path)
    try:
        connection.execute(
            """
            CREATE TABLE control_trace(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_ms INTEGER NOT NULL,
                session_id TEXT NOT NULL,
                cook_id TEXT,
                controller TEXT NOT NULL,
                event_kind TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                payload TEXT NOT NULL CHECK(json_valid(payload))
            )
            """
        )
        connection.execute(
            """
            INSERT INTO control_trace(
                ts_ms, session_id, cook_id, controller, event_kind, schema_version, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.ts_ms,
                row.session_id,
                row.cook_id,
                row.controller,
                row.event_kind,
                row.schema_version,
                row.payload,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    assert read_control_trace_session(record.session_id) == []
    assert read_control_trace_session(record.session_id, database_path=alternate_path) == [record]
    assert read_control_trace_cook(record.cook_id, database_path=alternate_path) == [record]


def test_alternate_database_path_must_name_an_existing_file(ds, tmp_path):
    missing_path = tmp_path / "missing.db"

    with pytest.raises(FileNotFoundError, match="control trace database does not exist"):
        read_control_trace_session("session-a", database_path=missing_path)


def test_append_rejects_malformed_batches(ds):
    with pytest.raises(TypeError, match="records must be a sequence"):
        append_control_trace(iter(()))
    with pytest.raises(TypeError, match="records must contain only ControlTraceRecord"):
        append_control_trace([object()])


def test_empty_batch_append_is_a_noop_without_a_transaction(ds, monkeypatch):
    existing = _record(1_000, "existing")
    append_control_trace([existing])

    def transaction_must_not_open():
        raise AssertionError("an empty append must not open a transaction")

    monkeypatch.setattr(datastore, "transaction", transaction_must_not_open)
    append_control_trace([])
    assert read_control_trace_session("existing") == [existing]


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


def test_batch_append_runtime_failure_rolls_back_every_trace_row(ds):
    accepted = _record(1_000, "accepted")
    rejected = _record(2_000, "trigger-runtime-failure")
    ds.connection().execute(
        """
        CREATE TEMP TRIGGER fail_second_control_trace_row
        BEFORE INSERT ON control_trace
        WHEN NEW.session_id = 'trigger-runtime-failure'
        BEGIN
            SELECT RAISE(ABORT, 'simulated trace append failure');
        END
        """
    )

    try:
        with pytest.raises(sqlite3.IntegrityError, match="simulated trace append failure"):
            append_control_trace([accepted, rejected])
    finally:
        ds.connection().execute("DROP TRIGGER fail_second_control_trace_row")

    assert read_control_trace_session("accepted") == []
    assert read_control_trace_session("trigger-runtime-failure") == []

    append_control_trace([accepted])
    assert read_control_trace_session("accepted") == [accepted]


def test_range_limit_is_bounded(ds):
    append_control_trace([_record(1_000, "session-a")])

    with pytest.raises(ValueError, match="limit"):
        read_control_trace_range(0, 2_000, limit=0)
    with pytest.raises(ValueError, match="limit"):
        read_control_trace_range(0, 2_000, limit=CONTROL_TRACE_MAX_LIMIT + 1)


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(lambda limit: read_control_trace_range(0, 0, limit=limit), id="range"),
        pytest.param(lambda limit: prune_control_trace(0, limit=limit), id="age-prune"),
        pytest.param(
            lambda limit: prune_incompatible_control_trace(TRACE_SCHEMA_VERSION, limit=limit),
            id="schema-prune",
        ),
    ],
)
@pytest.mark.parametrize("invalid_limit", [True, 0, -1, 1.0, CONTROL_TRACE_MAX_LIMIT + 1])
def test_all_trace_limits_are_strict_bounded_integers(ds, operation, invalid_limit):
    with pytest.raises(ValueError, match="limit"):
        operation(invalid_limit)


def test_trace_limit_maximum_is_accepted_by_reads_and_prunes(ds):
    assert read_control_trace_range(0, 0, limit=CONTROL_TRACE_MAX_LIMIT) == []
    assert prune_control_trace(0, limit=CONTROL_TRACE_MAX_LIMIT) == 0
    assert prune_incompatible_control_trace(TRACE_SCHEMA_VERSION, limit=CONTROL_TRACE_MAX_LIMIT) == 0


@pytest.mark.parametrize(
    ("start_ms", "end_ms", "error_name"),
    [
        (-1, 0, "start_ms"),
        (0, -1, "end_ms"),
        (True, 0, "start_ms"),
        (0, False, "end_ms"),
        (0.0, 0, "start_ms"),
        (0, 0.0, "end_ms"),
    ],
)
def test_range_timestamps_are_strict_non_negative_integers(ds, start_ms, end_ms, error_name):
    with pytest.raises(ValueError, match=error_name):
        read_control_trace_range(start_ms, end_ms, limit=1)


def test_range_start_must_not_exceed_end(ds):
    with pytest.raises(ValueError, match="start_ms must not exceed end_ms"):
        read_control_trace_range(2, 1, limit=1)


@pytest.mark.parametrize("before_ms", [-1, True, 1.0])
def test_prune_timestamp_is_a_strict_non_negative_integer(ds, before_ms):
    with pytest.raises(ValueError, match="before_ms"):
        prune_control_trace(before_ms, limit=1)


@pytest.mark.parametrize("before_schema_version", [True, 1.0, "5", None])
def test_incompatible_prune_schema_version_is_a_strict_integer(ds, before_schema_version):
    with pytest.raises(TypeError, match="before_schema_version"):
        prune_incompatible_control_trace(before_schema_version, limit=1)


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


def test_schema_filtered_reads_and_incompatible_pruning_preserve_compatible_rows(ds):
    compatible_v2 = _record(997, "shared", "shared-cook").model_copy(update={"schema_version": 2})
    compatible_v5 = _record(998, "old-only", "old-cook").model_copy(update={"schema_version": 5})
    v2_row = compatible_v2.to_db_row()
    v5_row = compatible_v5.to_db_row()
    current = _record(1_000, "shared", "shared-cook")
    current_second = _record(1_001, "current-second", "shared-cook")
    conn = ds.connection()
    conn.executemany(
        """
        INSERT INTO control_trace(ts_ms, session_id, cook_id, controller, event_kind, schema_version, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (996, "incompatible", None, "pid", "session", 1, '"old"'),
            (
                v2_row.ts_ms,
                v2_row.session_id,
                v2_row.cook_id,
                v2_row.controller,
                v2_row.event_kind,
                v2_row.schema_version,
                v2_row.payload,
            ),
            (
                v5_row.ts_ms,
                v5_row.session_id,
                v5_row.cook_id,
                v5_row.controller,
                v5_row.event_kind,
                v5_row.schema_version,
                v5_row.payload,
            ),
            (999, "future-only", "future-cook", "pid", "session", TRACE_SCHEMA_VERSION + 1, '"future"'),
        ],
    )
    append_control_trace([current, current_second])

    assert read_control_trace_session("shared") == [compatible_v2, current]
    assert read_control_trace_cook("shared-cook") == [compatible_v2, current, current_second]
    assert read_control_trace_range(0, 2_000, limit=2) == [compatible_v2, compatible_v5]

    assert prune_incompatible_control_trace(TRACE_SCHEMA_VERSION, limit=1) == 1
    assert prune_incompatible_control_trace(TRACE_SCHEMA_VERSION, limit=1) == 0
    assert conn.execute("SELECT schema_version FROM control_trace ORDER BY id").fetchall() == [
        (2,),
        (5,),
        (TRACE_SCHEMA_VERSION + 1,),
        (TRACE_SCHEMA_VERSION,),
        (TRACE_SCHEMA_VERSION,),
    ]


def test_age_pruning_never_deletes_a_future_schema_row(ds):
    conn = ds.connection()
    conn.execute(
        """
        INSERT INTO control_trace(ts_ms, session_id, cook_id, controller, event_kind, schema_version, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (0, "future", None, "pid", "session", TRACE_SCHEMA_VERSION + 1, '"future"'),
    )
    append_control_trace([_record(1, "current-old"), _record(100, "current-new")])

    assert prune_control_trace(50, limit=10) == 1
    assert conn.execute("SELECT schema_version FROM control_trace ORDER BY id").fetchall() == [
        (TRACE_SCHEMA_VERSION + 1,),
        (TRACE_SCHEMA_VERSION,),
    ]


def test_recorder_maintenance_prunes_only_incompatible_historical_schemas(ds):
    timestamp_ms = RETENTION_PERIOD_MS + 1
    compatible = _record(timestamp_ms, "legacy").model_copy(update={"schema_version": 2})
    compatible_row = compatible.to_db_row()
    conn = ds.connection()
    conn.executemany(
        """
        INSERT INTO control_trace(ts_ms, session_id, cook_id, controller, event_kind, schema_version, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                compatible_row.ts_ms,
                compatible_row.session_id,
                compatible_row.cook_id,
                compatible_row.controller,
                compatible_row.event_kind,
                compatible_row.schema_version,
                compatible_row.payload,
            ),
            (timestamp_ms, "incompatible", None, "pid", "session", 1, '"incompatible"'),
            (timestamp_ms, "future", None, "pid", "session", TRACE_SCHEMA_VERSION + 1, '"future"'),
        ],
    )
    append_control_trace([_record(timestamp_ms, "current")])

    recorder = ControlTraceRecorder(
        append=lambda _records: None,
        monotonic_clock=lambda: 0,
        wall_clock=lambda: timestamp_ms,
    )

    assert recorder is not None
    assert conn.execute("SELECT schema_version FROM control_trace ORDER BY id").fetchall() == [
        (2,),
        (TRACE_SCHEMA_VERSION + 1,),
        (TRACE_SCHEMA_VERSION,),
    ]
