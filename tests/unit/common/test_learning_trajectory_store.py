"""Behavioral specification for the durable bounded learning-trajectory corpus."""

from __future__ import annotations

import random
import sqlite3
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
from operator import setitem
from pathlib import Path
from typing import Any

import pytest

from common import datastore
from common.learning_trajectory import (
    FitCorpusIdentity,
    FitCorpusSlice,
    FrameDeliveryCertainty,
    HoldEntrySample,
    LearningTrajectoryFrame,
    LearningTrajectorySegment,
    ModelFitLineage,
    TrajectoryBreakReason,
    canonical_trajectory_digest,
)
from common.persistence.learning_trajectory import (
    AppendReceipt,
    CorpusStatus,
    FinalizeReceipt,
    FitCorpusEvictedError,
    FitCorpusSnapshot,
    FitRun,
    LearningTrajectoryConflictError,
    LearningTrajectoryRepository,
    RecoveryReport,
    SegmentCursor,
    StaleSegmentCursorError,
)

_FRAME_MS = 20_000
_WALL_EPOCH_MS = 1_700_000_000_000
_TABLES = {
    "learning_trajectory_corpus",
    "learning_trajectory_segment",
    "learning_trajectory_frame",
    "learning_fit_run",
}


def _digest(label: str) -> str:
    return sha256(label.encode()).hexdigest()


def _frame(
    sequence: int,
    *,
    epoch_ms: int = 0,
    effective_mode: str = "Hold",
    temperature_offset: float = 0.0,
) -> LearningTrajectoryFrame:
    start_ms = epoch_ms + sequence * _FRAME_MS
    end_ms = start_ms + _FRAME_MS
    return LearningTrajectoryFrame(
        sequence=sequence,
        monotonic_start_ms=start_ms,
        monotonic_end_ms=end_ms,
        wall_start_ms=_WALL_EPOCH_MS + start_ms,
        wall_end_ms=_WALL_EPOCH_MS + end_ms,
        chamber_temperature_c=110.0 + temperature_offset + sequence / 100.0,
        temperature_sample_monotonic_ms=end_ms,
        temperature_sample_wall_ms=_WALL_EPOCH_MS + end_ms,
        temperature_sample_age_ms=0,
        temperature_sample_wall_age_ms=0,
        temperature_sample_clock_skew_ms=0,
        source_temperature_units="C",
        settings_revision=7,
        probe_valid=True,
        probe_source="grill-probe-1",
        ambient_temperature_c=24.0,
        ambient_source="configured",
        ambient_uncertainty_c=1.5,
        delivered_auger_on_seconds=8.0,
        realized_auger_duty=0.4,
        normalized_combustion_load=0.4,
        delivered_fan_on_seconds=20.0,
        fan_duty_integral_seconds=10.0,
        mean_actual_fan_duty=0.5,
        auger_delivery_certainty=FrameDeliveryCertainty.EXACT,
        fan_delivery_certainty=FrameDeliveryCertainty.EXACT,
        effective_mode=effective_mode,
        recipe_step_id=None,
        complete=True,
        continuous=True,
        partial=False,
        boundary_reason=None,
    )


def _hold_entry(frame: LearningTrajectoryFrame) -> HoldEntrySample:
    return HoldEntrySample(
        monotonic_ms=frame.monotonic_start_ms,
        wall_ms=frame.wall_start_ms,
        chamber_temperature_c=frame.chamber_temperature_c,
        probe_valid=True,
        probe_source="grill-probe-1",
    )


def _segment(
    segment_id: str,
    *,
    epoch_ms: int = 0,
    start_sequence: int = 0,
    pre_roll_count: int = 1,
    scored_count: int = 0,
    state: str = "open",
) -> LearningTrajectorySegment:
    if pre_roll_count + scored_count == 0:
        raise AssertionError("test segments must contain at least one frame")
    pre_roll = tuple(
        _frame(sequence, epoch_ms=epoch_ms, effective_mode="Smoke")
        for sequence in range(start_sequence, start_sequence + pre_roll_count)
    )
    scored_start = start_sequence + pre_roll_count
    scored = tuple(
        _frame(sequence, epoch_ms=epoch_ms)
        for sequence in range(scored_start, scored_start + scored_count)
    )
    all_frames = (*pre_roll, *scored)
    hold_entry = _hold_entry(scored[0]) if scored else None
    return LearningTrajectorySegment(
        schema_version=1,
        observation_schema_version=2,
        segment_id=segment_id,
        cook_id=f"cook-{segment_id}",
        trajectory_session_id=f"trajectory-{segment_id}",
        trace_session_ids=(f"trace-{segment_id}",),
        collection_provenance={"origin": "passive-online", "role_generation": 4},
        configuration_provenance={"controller": "MPC", "revision": 7},
        cadence_digest=_digest("cadence-20-seconds-v1"),
        model_structure_digest=_digest("grey-one-zone-erlang-v1"),
        held_physics_digest=_digest("held-grey-physics-v1"),
        delay_input_mapping_digest=_digest("normalized-combustion-load-v1"),
        actuation_mapping_digest=_digest("framed-pulse-v1"),
        scored_fan_regime_digest=_digest("fan-regime-v1"),
        ambient_semantics_digest=_digest("ambient-configured-celsius-v1"),
        pre_roll_frames=pre_roll,
        hold_entry=hold_entry,
        scored_hold_frames=scored,
        generation_audit_ranges=(
            {
                "start_sequence": all_frames[0].sequence,
                "end_sequence": all_frames[-1].sequence,
                "role_generation": 4,
            },
        ),
        start_monotonic_ms=all_frames[0].monotonic_start_ms,
        end_monotonic_ms=all_frames[-1].monotonic_end_ms,
        start_wall_ms=all_frames[0].wall_start_ms,
        end_wall_ms=all_frames[-1].wall_end_ms,
        start_sequence=all_frames[0].sequence,
        end_sequence=all_frames[-1].sequence,
        pre_roll_end_reason=None,
        terminal_break_reason=(None if state == "open" else TrajectoryBreakReason.STOP),
        state=state,
        source_trace_digest=_digest(f"source-trace-{segment_id}"),
        source_schema_version=7,
        source_row_digest=_digest(f"source-rows-{segment_id}"),
        build_provenance={"builder": "trajectory-runtime", "revision": 1},
    )


def _lineage(
    snapshot: FitCorpusSnapshot,
    request_id: str,
    *,
    status: str = "queued",
    candidate_digest: str | None = None,
) -> ModelFitLineage:
    return ModelFitLineage(
        request_id=request_id,
        parent_incumbent_digest=_digest("incumbent"),
        parent_incumbent_generation=4,
        candidate_generation=5,
        fit_corpus=snapshot.identity,
        fit_corpus_digest=snapshot.identity.corpus_digest,
        trigger_origin="retained-observation-threshold",
        result_status=status,
        candidate_digest=candidate_digest,
    )


def _corpus_payload(identity: FitCorpusIdentity) -> dict[str, object]:
    return {
        "schema_version": identity.schema_version,
        "corpus_revision": identity.corpus_revision,
        "fit_partition_digest": identity.fit_partition_digest,
        "slices": [
            {
                "segment_id": item.segment_id,
                "through_ordinal": item.through_ordinal,
                "prefix_digest": item.prefix_digest,
                "pre_roll_count": item.pre_roll_count,
                "scored_count": item.scored_count,
            }
            for item in identity.slices
        ],
    }


def _seed_v8_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE kv (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL CHECK(json_valid(value))
            );
            CREATE TABLE legacy_v8_data (
                identity TEXT PRIMARY KEY,
                payload TEXT NOT NULL
            );
            INSERT INTO kv(key, value) VALUES('preserved-v8', '{"value":8}');
            INSERT INTO legacy_v8_data(identity, payload) VALUES('legacy-row', 'untouched');
            PRAGMA user_version=8;
            """
        )
        connection.commit()
    finally:
        connection.close()


def _table_names(path: Path) -> set[str]:
    connection = sqlite3.connect(path)
    try:
        return {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        connection.close()


def _columns(path: Path, table: str) -> set[str]:
    connection = sqlite3.connect(path)
    try:
        return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    finally:
        connection.close()


def _execute(
    path: Path,
    sql: str,
    parameters: tuple[object, ...] = (),
    *,
    ignore_checks: bool = False,
) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        if ignore_checks:
            connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(sql, parameters)
        connection.commit()
    finally:
        connection.close()


def _executescript(path: Path, sql: str) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(sql)
        connection.commit()
    finally:
        connection.close()


def _scalar(path: Path, sql: str, parameters: tuple[object, ...] = ()) -> object:
    connection = sqlite3.connect(path)
    try:
        row = connection.execute(sql, parameters).fetchone()
        assert row is not None
        return row[0]
    finally:
        connection.close()


def _rows(
    path: Path, sql: str, parameters: tuple[object, ...] = ()
) -> list[tuple[Any, ...]]:
    connection = sqlite3.connect(path)
    try:
        return connection.execute(sql, parameters).fetchall()
    finally:
        connection.close()


def _append_scored(
    repository: LearningTrajectoryRepository,
    cursor: SegmentCursor,
    frames: tuple[LearningTrajectoryFrame, ...],
) -> AppendReceipt:
    return repository.append(cursor, hold_entry=_hold_entry(frames[0]), scored=frames)


def _finalize_segment(
    repository: LearningTrajectoryRepository,
    segment: LearningTrajectorySegment,
) -> FinalizeReceipt:
    cursor = repository.begin_segment(segment)
    return repository.finalize(cursor, TrajectoryBreakReason.STOP)


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    return tmp_path / "learning-trajectory.sqlite"


@pytest.fixture
def repository(database_path: Path) -> LearningTrajectoryRepository:
    return LearningTrajectoryRepository(str(database_path))


def test_schema_v9_migration_is_additive_and_declares_corpus_tables(database_path: Path) -> None:
    _seed_v8_database(database_path)
    datastore._reset_for_tests(str(database_path))
    try:
        connection = datastore.connection()
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 9
        assert _TABLES <= _table_names(database_path)
        assert connection.execute(
            "SELECT value FROM kv WHERE key='preserved-v8'"
        ).fetchone()[0] == '{"value":8}'
        assert connection.execute(
            "SELECT payload FROM legacy_v8_data WHERE identity='legacy-row'"
        ).fetchone()[0] == "untouched"

        assert {
            "singleton",
            "schema_version",
            "corpus_revision",
            "segment_count",
            "pre_roll_count",
            "scored_count",
            "evicted_segment_count",
            "quarantined_segment_count",
        } <= _columns(database_path, "learning_trajectory_corpus")
        assert {
            "segment_id",
            "state",
            "fit_partition_digest",
            "start_monotonic_ms",
            "end_monotonic_ms",
            "start_wall_ms",
            "end_wall_ms",
            "hold_entry_json",
            "pre_roll_count",
            "scored_count",
            "next_ordinal",
            "rolling_digest",
            "final_digest",
            "created_corpus_revision",
            "updated_corpus_revision",
            "terminal_break_reason",
            "source_trace_digest",
        } <= _columns(database_path, "learning_trajectory_segment")
        assert {
            "segment_id",
            "ordinal",
            "kind",
            "payload_schema_version",
            "canonical_json",
            "frame_digest",
        } <= _columns(database_path, "learning_trajectory_frame")
        frame_ddl = str(
            connection.execute(
                "SELECT sql FROM sqlite_master WHERE name='learning_trajectory_frame'"
            ).fetchone()[0]
        )
        assert "payload_schema_version = 2" in frame_ddl
        assert {
            "request_id",
            "status",
            "fit_partition_digest",
            "corpus_revision",
            "corpus_digest",
            "manifest_json",
            "parent_incumbent_digest",
            "parent_incumbent_generation",
            "candidate_generation",
            "candidate_digest",
            "result_error",
            "created_ms",
            "started_ms",
            "completed_ms",
        } <= _columns(database_path, "learning_fit_run")

        foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(learning_trajectory_frame)"
        ).fetchall()
        assert any(
            row[2] == "learning_trajectory_segment"
            and row[3] == "segment_id"
            and row[4] == "segment_id"
            and row[6].upper() == "CASCADE"
            for row in foreign_keys
        )
    finally:
        datastore._reset_for_tests(None)


def test_schema_v9_migration_rolls_back_before_version_bump(
    database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_v8_database(database_path)
    datastore._reset_for_tests(str(database_path))
    original_connect = datastore.sqlite3.connect

    class _CrashingConnection(sqlite3.Connection):
        def execute(self, sql: str, *args: Any, **kwargs: Any):
            normalized = "".join(sql.lower().split())
            if normalized == "pragmauser_version=9":
                raise RuntimeError("injected v9 migration crash")
            return super().execute(sql, *args, **kwargs)

    def crashing_connect(*args: Any, **kwargs: Any):
        kwargs["factory"] = _CrashingConnection
        return original_connect(*args, **kwargs)

    try:
        with (
            monkeypatch.context() as patch,
            pytest.raises(RuntimeError, match="injected v9 migration crash"),
        ):
            patch.setattr(datastore.sqlite3, "connect", crashing_connect)
            datastore.connection()

        check = sqlite3.connect(database_path)
        try:
            assert check.execute("PRAGMA user_version").fetchone()[0] == 8
            assert not (_TABLES & _table_names(database_path))
            assert check.execute(
                "SELECT payload FROM legacy_v8_data WHERE identity='legacy-row'"
            ).fetchone()[0] == "untouched"
        finally:
            check.close()

        datastore._reset_for_tests(str(database_path))
        assert datastore.connection().execute("PRAGMA user_version").fetchone()[0] == 9
        assert _TABLES <= _table_names(database_path)
    finally:
        datastore._reset_for_tests(None)


def test_schema_v9_migration_is_idempotent_and_preserves_trajectory_rows(
    database_path: Path,
) -> None:
    repository = LearningTrajectoryRepository(str(database_path))
    segment = _segment("migration-idempotency")
    cursor = repository.begin_segment(segment)
    before = repository.status()

    reopened = LearningTrajectoryRepository(str(database_path))
    current = reopened.read_segment(segment.segment_id)
    assert current is not None
    assert reopened.begin_segment(current) == cursor
    assert reopened.status() == before
    assert _scalar(database_path, "PRAGMA user_version") == 9


def test_begin_append_finalize_updates_cursor_chain_and_corpus_atomically(
    repository: LearningTrajectoryRepository, database_path: Path
) -> None:
    segment = _segment("happy-path")
    cursor = repository.begin_segment(segment)
    assert isinstance(cursor, SegmentCursor)
    assert cursor.segment_id == segment.segment_id
    assert cursor.next_ordinal == 1
    status = repository.status()
    assert isinstance(status, CorpusStatus)
    assert status.corpus_revision == cursor.corpus_revision
    assert status.segment_count == 1
    assert status.pre_roll_count == 1
    assert status.scored_count == 0
    assert status.evicted_segment_count == 0
    assert status.quarantined_segment_count == 0

    scored = (_frame(1), _frame(2))
    receipt = _append_scored(repository, cursor, scored)
    assert isinstance(receipt, AppendReceipt)
    assert receipt.cursor.next_ordinal == 3
    assert receipt.cursor.corpus_revision > cursor.corpus_revision

    canonical_rows = _rows(
        database_path,
        (
            "SELECT canonical_json FROM learning_trajectory_frame "
            "WHERE segment_id=? AND ordinal>=? ORDER BY ordinal"
        ),
        (segment.segment_id, cursor.next_ordinal),
    )
    expected_digest = cursor.chain_digest
    for (canonical_json,) in canonical_rows:
        expected_digest = sha256(
            bytes.fromhex(expected_digest) + canonical_json.encode()
        ).hexdigest()
    assert receipt.cursor.chain_digest == expected_digest

    final = repository.finalize(receipt.cursor, TrajectoryBreakReason.STOP)
    assert isinstance(final, FinalizeReceipt)
    assert final.segment_id == segment.segment_id
    stored = repository.read_segment(segment.segment_id)
    assert stored is not None
    assert stored.state == "finalized"
    assert stored.terminal_break_reason is TrajectoryBreakReason.STOP
    assert stored.pre_roll_frames == segment.pre_roll_frames
    assert stored.scored_hold_frames == scored
    assert repository.status().scored_count == 2


def test_public_repository_results_are_frozen_and_own_snapshot_tuples(
    repository: LearningTrajectoryRepository,
) -> None:
    segment = _segment("frozen-results", scored_count=1)
    cursor = repository.begin_segment(segment)
    snapshot = repository.snapshot_fit_corpus(segment.fit_partition_digest)
    run = repository.record_fit_request(snapshot, _lineage(snapshot, "frozen-run"))

    for result, field, value in (
        (cursor, "next_ordinal", 999),
        (repository.status(), "segment_count", 999),
        (snapshot, "segments", ()),
        (run, "status", "failed"),
    ):
        with pytest.raises((FrozenInstanceError, AttributeError)):
            setattr(result, field, value)

    assert isinstance(snapshot, FitCorpusSnapshot)
    assert type(snapshot.segments) is tuple
    with pytest.raises(TypeError):
        setitem(snapshot.segments, 0, segment)


def test_exact_duplicate_begin_append_and_finalize_are_idempotent(
    repository: LearningTrajectoryRepository,
) -> None:
    segment = _segment("duplicates")
    cursor = repository.begin_segment(segment)
    after_begin = repository.status()
    assert repository.begin_segment(segment) == cursor
    assert repository.status() == after_begin

    scored = (_frame(1),)
    first_append = _append_scored(repository, cursor, scored)
    after_append = repository.status()
    duplicate_append = _append_scored(repository, cursor, scored)
    assert duplicate_append == first_append
    assert repository.status() == after_append

    first_finalize = repository.finalize(
        first_append.cursor, TrajectoryBreakReason.STOP
    )
    after_finalize = repository.status()
    duplicate_finalize = repository.finalize(
        first_append.cursor, TrajectoryBreakReason.STOP
    )
    assert duplicate_finalize == first_finalize
    assert repository.status() == after_finalize


def test_conflicting_duplicate_interval_quarantines_without_overwrite(
    repository: LearningTrajectoryRepository, database_path: Path
) -> None:
    segment = _segment("conflicting-frame")
    cursor = repository.begin_segment(segment)
    original = _frame(1)
    _append_scored(repository, cursor, (original,))
    stored_before = _scalar(
        database_path,
        (
            "SELECT canonical_json FROM learning_trajectory_frame "
            "WHERE segment_id=? AND ordinal=1"
        ),
        (segment.segment_id,),
    )

    conflicting = replace(
        original, chamber_temperature_c=original.chamber_temperature_c + 5.0
    )
    with pytest.raises(LearningTrajectoryConflictError, match="conflict"):
        _append_scored(repository, cursor, (conflicting,))

    assert _scalar(
        database_path,
        (
            "SELECT canonical_json FROM learning_trajectory_frame "
            "WHERE segment_id=? AND ordinal=1"
        ),
        (segment.segment_id,),
    ) == stored_before
    assert _scalar(
        database_path,
        "SELECT state FROM learning_trajectory_segment WHERE segment_id=?",
        (segment.segment_id,),
    ) == "quarantined"
    assert repository.status().quarantined_segment_count == 1


def test_conflicting_duplicate_segment_identity_quarantines_original(
    repository: LearningTrajectoryRepository, database_path: Path
) -> None:
    segment = _segment("conflicting-segment")
    repository.begin_segment(segment)
    conflicting = replace(
        segment,
        collection_provenance={"origin": "different-source", "role_generation": 4},
    )

    with pytest.raises(LearningTrajectoryConflictError, match="conflict"):
        repository.begin_segment(conflicting)

    assert _scalar(
        database_path,
        "SELECT state FROM learning_trajectory_segment WHERE segment_id=?",
        (segment.segment_id,),
    ) == "quarantined"
    assert repository.status().segment_count == 1
    assert repository.status().quarantined_segment_count == 1


@pytest.mark.parametrize("cursor_field", ["next_ordinal", "chain_digest", "corpus_revision"])
def test_append_rejects_each_stale_cursor_cas_component_without_mutation(
    repository: LearningTrajectoryRepository,
    cursor_field: str,
) -> None:
    segment = _segment(f"stale-{cursor_field}")
    cursor = repository.begin_segment(segment)
    replacement: object
    if cursor_field == "chain_digest":
        replacement = _digest("forged-chain")
    else:
        replacement = getattr(cursor, cursor_field) + 1
    stale = replace(cursor, **{cursor_field: replacement})
    status_before = repository.status()

    with pytest.raises(StaleSegmentCursorError, match="stale"):
        _append_scored(repository, stale, (_frame(1),))

    stored = repository.read_segment(segment.segment_id)
    assert stored is not None
    assert stored.state == "open"
    assert stored.scored_hold_frames == ()
    assert repository.status() == status_before


def test_append_rolls_back_frame_header_counters_and_revision_on_sql_failure(
    repository: LearningTrajectoryRepository, database_path: Path
) -> None:
    segment = _segment("append-rollback")
    cursor = repository.begin_segment(segment)
    status_before = repository.status()
    _executescript(
        database_path,
        """
        CREATE TRIGGER abort_learning_frame_insert
        BEFORE INSERT ON learning_trajectory_frame
        WHEN NEW.segment_id = 'append-rollback'
        BEGIN
            SELECT RAISE(ABORT, 'injected append failure');
        END;
        """,
    )

    with pytest.raises(sqlite3.IntegrityError, match="injected append failure"):
        _append_scored(repository, cursor, (_frame(1),))

    stored = repository.read_segment(segment.segment_id)
    assert stored is not None
    assert stored.scored_hold_frames == ()
    assert repository.status() == status_before
    assert _scalar(
        database_path,
        "SELECT COUNT(*) FROM learning_trajectory_frame WHERE segment_id=?",
        (segment.segment_id,),
    ) == 1


def test_break_and_begin_is_atomic_idempotent_and_preserves_new_epoch(
    repository: LearningTrajectoryRepository,
) -> None:
    current = _segment("break-current", epoch_ms=9_000_000)
    current_cursor = repository.begin_segment(current)
    next_segment = _segment("break-next", epoch_ms=0)

    next_cursor = repository.break_and_begin(
        current_cursor, TrajectoryBreakReason.PROCESS_RESTART, next_segment
    )
    assert next_cursor.segment_id == next_segment.segment_id
    closed = repository.read_segment(current.segment_id)
    opened = repository.read_segment(next_segment.segment_id)
    assert closed is not None and opened is not None
    assert closed.state == "finalized"
    assert closed.terminal_break_reason is TrajectoryBreakReason.PROCESS_RESTART
    assert opened.state == "open"
    assert opened.start_monotonic_ms < closed.start_monotonic_ms

    after = repository.status()
    assert repository.break_and_begin(
        current_cursor, TrajectoryBreakReason.PROCESS_RESTART, next_segment
    ) == next_cursor
    assert repository.status() == after


def test_break_and_begin_rolls_back_both_sides_when_new_begin_fails(
    repository: LearningTrajectoryRepository, database_path: Path
) -> None:
    current = _segment("atomic-break-current")
    cursor = repository.begin_segment(current)
    next_segment = _segment("atomic-break-next", epoch_ms=1_000_000)
    before = repository.status()
    _executescript(
        database_path,
        """
        CREATE TRIGGER abort_next_learning_segment
        BEFORE INSERT ON learning_trajectory_segment
        WHEN NEW.segment_id = 'atomic-break-next'
        BEGIN
            SELECT RAISE(ABORT, 'injected next segment failure');
        END;
        """,
    )

    with pytest.raises(sqlite3.IntegrityError, match="injected next segment failure"):
        repository.break_and_begin(
            cursor, TrajectoryBreakReason.PROCESS_RESTART, next_segment
        )

    stored = repository.read_segment(current.segment_id)
    assert stored is not None and stored.state == "open"
    assert repository.read_segment(next_segment.segment_id) is None
    assert repository.status() == before


def test_finalize_rolls_back_state_and_counters_on_sql_failure(
    repository: LearningTrajectoryRepository, database_path: Path
) -> None:
    segment = _segment("finalize-rollback")
    cursor = repository.begin_segment(segment)
    before = repository.status()
    _executescript(
        database_path,
        """
        CREATE TRIGGER abort_finalize_corpus_update
        BEFORE UPDATE OF corpus_revision ON learning_trajectory_corpus
        BEGIN
            SELECT RAISE(ABORT, 'injected finalize failure');
        END;
        """,
    )

    with pytest.raises(sqlite3.IntegrityError, match="injected finalize failure"):
        repository.finalize(cursor, TrajectoryBreakReason.STOP)

    stored = repository.read_segment(segment.segment_id)
    assert stored is not None
    assert stored.state == "open"
    assert stored.terminal_break_reason is None
    assert repository.status() == before


def test_reopen_restores_exact_open_cursor_and_continues_chain(
    repository: LearningTrajectoryRepository, database_path: Path
) -> None:
    segment = _segment("reopen")
    first_cursor = repository.begin_segment(segment)
    first_receipt = _append_scored(repository, first_cursor, (_frame(1),))

    reopened = LearningTrajectoryRepository(str(database_path))
    restored = reopened.read_segment(segment.segment_id)
    assert restored is not None and restored.state == "open"
    restored_cursor = reopened.begin_segment(restored)
    assert restored_cursor == first_receipt.cursor
    second_receipt = reopened.append(restored_cursor, scored=(_frame(2),))
    assert second_receipt.cursor.next_ordinal == 3
    assert second_receipt.cursor.chain_digest != first_receipt.cursor.chain_digest


def test_recovery_finalizes_open_segment_at_last_committed_frame_and_new_epoch_begins(
    repository: LearningTrajectoryRepository, database_path: Path
) -> None:
    segment = _segment("recover-open", epoch_ms=8_000_000)
    cursor = repository.begin_segment(segment)
    committed = (_frame(1, epoch_ms=8_000_000), _frame(2, epoch_ms=8_000_000))
    _append_scored(repository, cursor, committed)

    restarted = LearningTrajectoryRepository(str(database_path))
    report = restarted.recover_open_segments(now_ms=_WALL_EPOCH_MS + 20_000_000)
    assert isinstance(report, RecoveryReport)
    assert report.finalized_segment_ids == (segment.segment_id,)
    assert report.quarantined_segment_ids == ()

    recovered = restarted.read_segment(segment.segment_id)
    assert recovered is not None
    assert recovered.state == "finalized"
    assert recovered.terminal_break_reason is TrajectoryBreakReason.UNCLEAN_RESTART
    assert recovered.scored_hold_frames == committed
    assert recovered.end_sequence == committed[-1].sequence
    assert recovered.end_monotonic_ms == committed[-1].monotonic_end_ms

    after_recovery = restarted.status()
    duplicate_recovery = restarted.recover_open_segments(
        now_ms=_WALL_EPOCH_MS + 20_000_001
    )
    assert duplicate_recovery.finalized_segment_ids == ()
    assert duplicate_recovery.quarantined_segment_ids == ()
    assert duplicate_recovery.interrupted_fit_request_ids == ()
    assert restarted.status() == after_recovery

    new_epoch = _segment("recover-new-epoch", epoch_ms=0)
    new_cursor = restarted.begin_segment(new_epoch)
    assert new_cursor.segment_id == new_epoch.segment_id
    assert new_epoch.start_monotonic_ms < recovered.start_monotonic_ms


@pytest.mark.parametrize(
    ("corruption_name", "corruption_sql"),
    [
        (
            "header",
            (
                "UPDATE learning_trajectory_segment SET next_ordinal=next_ordinal+1 "
                "WHERE segment_id='corrupt-header'"
            ),
        ),
        (
            "count",
            (
                "UPDATE learning_trajectory_segment SET scored_count=scored_count+1 "
                "WHERE segment_id='corrupt-count'"
            ),
        ),
        (
            "digest",
            (
                f"UPDATE learning_trajectory_segment SET rolling_digest='{'0' * 64}' "
                "WHERE segment_id='corrupt-digest'"
            ),
        ),
        (
            "payload",
            (
                "UPDATE learning_trajectory_frame SET canonical_json='{}' "
                "WHERE segment_id='corrupt-payload' AND ordinal=1"
            ),
        ),
    ],
)
def test_recovery_quarantines_whole_corrupt_segment_and_preserves_authority(
    database_path: Path,
    corruption_name: str,
    corruption_sql: str,
) -> None:
    repository = LearningTrajectoryRepository(str(database_path))
    segment_id = f"corrupt-{corruption_name}"
    segment = _segment(segment_id)
    cursor = repository.begin_segment(segment)
    _append_scored(repository, cursor, (_frame(1),))
    _execute(
        database_path,
        "INSERT INTO kv(key, value) VALUES(?, ?)",
        ("mpc:active-model-authority", '{"generation":4,"digest":"authority"}'),
    )
    _execute(database_path, corruption_sql, ignore_checks=True)

    restarted = LearningTrajectoryRepository(str(database_path))
    report = restarted.recover_open_segments(now_ms=_WALL_EPOCH_MS + 30_000_000)
    assert report.quarantined_segment_ids == (segment_id,)
    assert report.finalized_segment_ids == ()
    assert _scalar(
        database_path,
        "SELECT state FROM learning_trajectory_segment WHERE segment_id=?",
        (segment_id,),
    ) == "quarantined"
    assert _scalar(
        database_path,
        "SELECT COUNT(*) FROM learning_trajectory_frame WHERE segment_id=?",
        (segment_id,),
    ) == 2
    assert restarted.status().quarantined_segment_count == 1
    assert _scalar(
        database_path,
        "SELECT value FROM kv WHERE key='mpc:active-model-authority'",
    ) == '{"generation":4,"digest":"authority"}'

    counts = _rows(
        database_path,
        (
            "SELECT pre_roll_count,scored_count,next_ordinal "
            "FROM learning_trajectory_segment WHERE segment_id=?"
        ),
        (segment_id,),
    )
    assert counts == [(0, 0, 0)]
    assert restarted.status().pre_roll_count == 0
    assert restarted.status().scored_count == 0
    after_recovery = restarted.status()
    duplicate_recovery = restarted.recover_open_segments(
        now_ms=_WALL_EPOCH_MS + 30_000_001
    )
    assert duplicate_recovery.finalized_segment_ids == ()
    assert duplicate_recovery.quarantined_segment_ids == ()
    assert duplicate_recovery.interrupted_fit_request_ids == ()
    assert restarted.status() == after_recovery


def test_recovery_revalidates_finalized_segments_and_excludes_corruption_from_snapshot(
    database_path: Path,
) -> None:
    repository = LearningTrajectoryRepository(str(database_path))
    corrupt = _segment("finalized-corrupt", scored_count=1)
    healthy = _segment("finalized-healthy", epoch_ms=1_000_000, scored_count=1)
    _finalize_segment(repository, corrupt)
    _finalize_segment(repository, healthy)
    _execute(
        database_path,
        "UPDATE learning_trajectory_segment SET rolling_digest=? WHERE segment_id=?",
        ("0" * 64, corrupt.segment_id),
    )

    reopened = LearningTrajectoryRepository(str(database_path))
    report = reopened.recover_open_segments(now_ms=_WALL_EPOCH_MS + 50_000_000)

    assert report.quarantined_segment_ids == (corrupt.segment_id,)
    assert _scalar(
        database_path,
        "SELECT state FROM learning_trajectory_segment WHERE segment_id=?",
        (corrupt.segment_id,),
    ) == "quarantined"
    snapshot = reopened.snapshot_fit_corpus(healthy.fit_partition_digest)
    assert tuple(item.segment_id for item in snapshot.identity.slices) == (
        healthy.segment_id,
    )


def test_reopen_rebuilds_retained_counts_from_physical_frame_kinds(
    database_path: Path,
) -> None:
    repository = LearningTrajectoryRepository(str(database_path))
    segment = _segment("physical-count-rebuild", pre_roll_count=3, scored_count=2)
    repository.begin_segment(segment)
    _execute(
        database_path,
        (
            "UPDATE learning_trajectory_corpus SET "
            "pre_roll_count=9999,scored_count=9999 WHERE singleton=1"
        ),
    )

    reopened = LearningTrajectoryRepository(str(database_path))
    status = reopened.status()

    assert status.pre_roll_count == 3
    assert status.scored_count == 2


def test_auto_roll_after_180_scored_rows_carries_exact_intervals_as_pre_roll(
    repository: LearningTrajectoryRepository,
) -> None:
    segment = _segment("auto-roll")
    cursor = repository.begin_segment(segment)
    scored = tuple(_frame(sequence) for sequence in range(1, 181))

    receipt = _append_scored(repository, cursor, scored)
    assert receipt.cursor.segment_id != segment.segment_id
    rolled_from = repository.read_segment(segment.segment_id)
    rolled_to = repository.read_segment(receipt.cursor.segment_id)
    assert rolled_from is not None and rolled_to is not None
    assert rolled_from.state == "finalized"
    assert rolled_from.terminal_break_reason is TrajectoryBreakReason.RETENTION_ROLLOVER
    assert rolled_from.scored_hold_frames == scored
    assert rolled_to.state == "open"
    assert rolled_to.scored_hold_frames == ()
    assert rolled_to.pre_roll_frames == scored
    assert len(rolled_to.pre_roll_frames) == 180
    assert receipt.cursor.next_ordinal == 180
    assert repository.status().scored_count <= 8_640
    assert repository.status().pre_roll_count <= 8_640


def test_exact_append_retry_across_multiple_auto_rolls_returns_current_open_cursor(
    repository: LearningTrajectoryRepository,
) -> None:
    segment = _segment("multi-roll-retry")
    original_cursor = repository.begin_segment(segment)
    scored = tuple(_frame(sequence) for sequence in range(1, 361))

    rolled = _append_scored(repository, original_cursor, scored)
    assert rolled.cursor.segment_id != segment.segment_id
    assert repository.read_segment(rolled.cursor.segment_id).state == "open"  # type: ignore[union-attr]

    advanced = _append_scored(repository, rolled.cursor, (_frame(361),))
    retried = _append_scored(repository, original_cursor, scored)

    assert retried.cursor == advanced.cursor
    assert retried.cursor.segment_id != segment.segment_id
    source = repository.read_segment(segment.segment_id)
    assert source is not None
    assert source.state == "finalized"
    assert source.terminal_break_reason is TrajectoryBreakReason.RETENTION_ROLLOVER
    assert repository.status().quarantined_segment_count == 0


def test_delayed_begin_retry_matches_original_prefix_and_returns_current_roll_cursor(
    repository: LearningTrajectoryRepository, database_path: Path
) -> None:
    original = _segment("delayed-begin")
    original_cursor = repository.begin_segment(original)
    rolled = _append_scored(
        repository,
        original_cursor,
        tuple(_frame(sequence) for sequence in range(1, 181)),
    )
    advanced = _append_scored(repository, rolled.cursor, (_frame(181),))

    reopened = LearningTrajectoryRepository(str(database_path))
    materialized_current = reopened.read_segment(advanced.cursor.segment_id)
    assert materialized_current is not None
    assert reopened.begin_segment(materialized_current) == advanced.cursor
    retried = reopened.begin_segment(original)

    assert retried == advanced.cursor
    assert retried.segment_id != original.segment_id
    assert reopened.status().quarantined_segment_count == 0


def test_delayed_break_retry_returns_advanced_next_segment_cursor(
    repository: LearningTrajectoryRepository,
) -> None:
    current = _segment("delayed-break-current")
    current_cursor = repository.begin_segment(current)
    next_segment = _segment("delayed-break-next", epoch_ms=1_000_000)
    next_cursor = repository.break_and_begin(
        current_cursor, TrajectoryBreakReason.PROCESS_RESTART, next_segment
    )
    advanced = _append_scored(
        repository, next_cursor, (_frame(1, epoch_ms=1_000_000),)
    )

    retried = repository.break_and_begin(
        current_cursor, TrajectoryBreakReason.PROCESS_RESTART, next_segment
    )

    assert retried == advanced.cursor
    assert repository.status().quarantined_segment_count == 0


def test_break_and_begin_auto_rolls_a_full_next_segment_and_returns_rolled_cursor(
    repository: LearningTrajectoryRepository,
) -> None:
    current = _segment("break-full-current")
    current_cursor = repository.begin_segment(current)
    full_next = _segment(
        "break-full-next",
        epoch_ms=1_000_000,
        pre_roll_count=1,
        scored_count=180,
    )

    rolled_cursor = repository.break_and_begin(
        current_cursor, TrajectoryBreakReason.PROCESS_RESTART, full_next
    )

    assert rolled_cursor.segment_id != full_next.segment_id
    stored_next = repository.read_segment(full_next.segment_id)
    stored_roll = repository.read_segment(rolled_cursor.segment_id)
    assert stored_next is not None and stored_roll is not None
    assert stored_next.state == "finalized"
    assert (
        stored_next.terminal_break_reason
        is TrajectoryBreakReason.RETENTION_ROLLOVER
    )
    assert stored_roll.state == "open"
    assert len(stored_roll.pre_roll_frames) == 180
    assert stored_roll.scored_hold_frames == ()


def test_append_operation_receipts_are_deterministically_bounded_per_retained_segment(
    repository: LearningTrajectoryRepository, database_path: Path
) -> None:
    segment = _segment("bounded-operation-receipts")
    cursor = repository.begin_segment(segment)
    for sequence in range(1, 71):
        cursor = repository.append(
            cursor,
            hold_entry=_hold_entry(_frame(sequence)) if sequence == 1 else None,
            scored=(_frame(sequence),),
        ).cursor

    assert _scalar(
        database_path,
        (
            "SELECT COUNT(*) FROM learning_trajectory_operation_receipt "
            "WHERE source_segment_id=?"
        ),
        (segment.segment_id,),
    ) <= repository.operation_receipt_limit_per_segment


def test_earliest_one_frame_append_receipt_survives_auto_roll_until_source_eviction(
    repository: LearningTrajectoryRepository,
) -> None:
    segment = _segment("receipt-survival")
    original_cursor = repository.begin_segment(segment)
    first_frame = _frame(1)
    cursor = repository.append(
        original_cursor,
        hold_entry=_hold_entry(first_frame),
        scored=(first_frame,),
    ).cursor
    for sequence in range(2, 181):
        cursor = repository.append(cursor, scored=(_frame(sequence),)).cursor

    assert repository.operation_receipt_limit_per_segment == 512
    assert cursor.segment_id != segment.segment_id
    retried = repository.append(
        original_cursor,
        hold_entry=_hold_entry(first_frame),
        scored=(first_frame,),
    )
    assert retried.cursor == cursor

    repository.finalize(cursor, TrajectoryBreakReason.STOP)
    for index in range(255):
        _finalize_segment(
            repository,
            _segment(
                f"receipt-eviction-{index:03d}",
                epoch_ms=(index + 1) * 10_000_000,
            ),
        )
    assert repository.read_segment(segment.segment_id) is None
    with pytest.raises(StaleSegmentCursorError, match="stale"):
        repository.append(
            original_cursor,
            hold_entry=_hold_entry(first_frame),
            scored=(first_frame,),
        )


def test_per_segment_pre_roll_cap_rejects_partial_trimming_of_open_segment(
    repository: LearningTrajectoryRepository,
) -> None:
    segment = _segment("pre-roll-per-segment", pre_roll_count=180)
    cursor = repository.begin_segment(segment)
    before = repository.status()
    overflow = _frame(180, effective_mode="Smoke")

    with pytest.raises(ValueError, match="pre-roll.*180|180.*pre-roll"):
        repository.append(cursor, pre_roll=(overflow,))

    stored = repository.read_segment(segment.segment_id)
    assert stored is not None
    assert stored.pre_roll_frames == segment.pre_roll_frames
    assert repository.status() == before


def test_scored_retention_evicts_oldest_finalized_whole_segment_and_never_open(
    repository: LearningTrajectoryRepository, database_path: Path
) -> None:
    first_id = "scored-000"
    open_id = "scored-048-open"
    for index in range(49):
        segment_id = open_id if index == 48 else f"scored-{index:03d}"
        segment = _segment(
            segment_id,
            epoch_ms=index * 10_000_000,
            pre_roll_count=1,
            scored_count=179,
        )
        cursor = repository.begin_segment(segment)
        assert repository.status().scored_count <= 8_640
        if index < 48:
            repository.finalize(cursor, TrajectoryBreakReason.STOP)

    status = repository.status()
    assert status.scored_count <= 8_640
    assert repository.read_segment(first_id) is None
    opened = repository.read_segment(open_id)
    assert opened is not None and opened.state == "open"
    assert _scalar(
        database_path,
        "SELECT COUNT(*) FROM learning_trajectory_frame WHERE segment_id=?",
        (first_id,),
    ) == 0

    retained = repository.read_segment("scored-001")
    assert retained is not None
    assert retained.state == "finalized"
    assert len(retained.scored_hold_frames) == 179
    ordinals = _rows(
        database_path,
        "SELECT ordinal FROM learning_trajectory_frame WHERE segment_id=? ORDER BY ordinal",
        (retained.segment_id,),
    )
    assert [row[0] for row in ordinals] == list(range(180))


def test_global_pre_roll_retention_evicts_whole_segments_and_enforces_both_caps(
    repository: LearningTrajectoryRepository,
) -> None:
    for index in range(49):
        segment = _segment(
            f"pre-roll-{index:03d}",
            epoch_ms=index * 10_000_000,
            pre_roll_count=180,
        )
        cursor = repository.begin_segment(segment)
        status = repository.status()
        assert status.pre_roll_count <= 8_640
        stored = repository.read_segment(segment.segment_id)
        assert stored is not None
        assert len(stored.pre_roll_frames) <= 180
        if index < 48:
            repository.finalize(cursor, TrajectoryBreakReason.STOP)

    assert repository.read_segment("pre-roll-000") is None
    assert repository.read_segment("pre-roll-001") is not None
    open_segment = repository.read_segment("pre-roll-048")
    assert open_segment is not None and open_segment.state == "open"
    assert repository.status().pre_roll_count == 8_640


def test_segment_cap_uses_end_wall_then_identity_and_never_evicts_open(
    repository: LearningTrajectoryRepository, database_path: Path
) -> None:
    for index in range(256):
        _finalize_segment(repository, _segment(f"segment-{index:03d}"))
    open_segment = _segment("segment-zzz-open")
    repository.begin_segment(open_segment)

    assert repository.status().segment_count == 256
    assert repository.read_segment("segment-000") is None
    assert repository.read_segment("segment-001") is not None
    stored_open = repository.read_segment(open_segment.segment_id)
    assert stored_open is not None and stored_open.state == "open"
    assert _scalar(
        database_path,
        "SELECT COUNT(*) FROM learning_trajectory_frame WHERE segment_id='segment-000'",
    ) == 0


def test_retention_eviction_rolls_back_triggering_begin_and_counters_on_failure(
    repository: LearningTrajectoryRepository, database_path: Path
) -> None:
    for index in range(256):
        _finalize_segment(repository, _segment(f"rollback-retention-{index:03d}"))
    before = repository.status()
    _executescript(
        database_path,
        """
        CREATE TRIGGER abort_learning_segment_eviction
        BEFORE DELETE ON learning_trajectory_segment
        BEGIN
            SELECT RAISE(ABORT, 'injected retention failure');
        END;
        """,
    )

    with pytest.raises(sqlite3.IntegrityError, match="injected retention failure"):
        repository.begin_segment(_segment("rollback-retention-overflow"))

    assert repository.status() == before
    assert repository.read_segment("rollback-retention-overflow") is None
    assert repository.read_segment("rollback-retention-000") is not None
    assert _scalar(
        database_path,
        "SELECT COUNT(*) FROM learning_trajectory_segment",
    ) == 256


def test_randomized_retention_holds_after_every_append_with_fixed_seed(
    repository: LearningTrajectoryRepository, database_path: Path
) -> None:
    rng = random.Random(20_260_827)
    for segment_index in range(300):
        pre_roll_count = rng.randint(1, 20)
        scored_count = rng.randint(1, 20)
        epoch_ms = segment_index * 10_000_000
        segment = _segment(
            f"random-{segment_index:03d}",
            epoch_ms=epoch_ms,
            pre_roll_count=1,
        )
        cursor = repository.begin_segment(segment)

        pre_roll = tuple(
            _frame(sequence, epoch_ms=epoch_ms, effective_mode="Smoke")
            for sequence in range(1, pre_roll_count)
        )
        offset = 0
        while offset < len(pre_roll):
            width = rng.randint(1, 23)
            chunk = pre_roll[offset : offset + width]
            receipt = repository.append(cursor, pre_roll=chunk)
            cursor = receipt.cursor
            offset += len(chunk)
            status = repository.status()
            assert status.scored_count <= 8_640
            assert status.pre_roll_count <= 8_640
            assert status.segment_count <= 256
            assert repository.read_segment(cursor.segment_id) is not None

        scored = tuple(
            _frame(sequence, epoch_ms=epoch_ms)
            for sequence in range(pre_roll_count, pre_roll_count + scored_count)
        )
        offset = 0
        while offset < len(scored):
            width = rng.randint(1, 23)
            chunk = scored[offset : offset + width]
            receipt = repository.append(
                cursor,
                hold_entry=_hold_entry(scored[0]) if offset == 0 else None,
                scored=chunk,
            )
            cursor = receipt.cursor
            offset += len(chunk)
            status = repository.status()
            assert status.scored_count <= 8_640
            assert status.pre_roll_count <= 8_640
            assert status.segment_count <= 256
            maximum_pre_roll = _scalar(
                database_path,
                "SELECT MAX(pre_roll_count) FROM learning_trajectory_segment",
            )
            assert isinstance(maximum_pre_roll, int)
            assert maximum_pre_roll <= 180
            current = repository.read_segment(cursor.segment_id)
            assert current is not None and current.state == "open"

        repository.finalize(cursor, TrajectoryBreakReason.STOP)

    reopened = LearningTrajectoryRepository(str(database_path))
    reopened_status = reopened.status()
    assert reopened_status.scored_count <= 8_640
    assert reopened_status.pre_roll_count <= 8_640
    assert reopened_status.segment_count <= 256
    assert reopened_status.segment_count == 256
    assert reopened_status.evicted_segment_count == 44
    assert reopened_status.evicted_pre_roll_count > 0
    assert reopened_status.evicted_scored_count > 0
    for segment_index in range(44):
        assert repository.read_segment(f"random-{segment_index:03d}") is None
    assert repository.read_segment("random-044") is not None
    assert repository.read_segment("random-299") is not None


def test_snapshot_open_prefix_is_immutable_while_later_frames_append_and_reopens_by_revision(
    repository: LearningTrajectoryRepository,
) -> None:
    first = _segment("snapshot-a", epoch_ms=0)
    first_cursor = repository.begin_segment(first)
    first_receipt = _append_scored(repository, first_cursor, (_frame(1), _frame(2)))
    repository.finalize(first_receipt.cursor, TrajectoryBreakReason.STOP)

    second = _segment("snapshot-b", epoch_ms=1_000_000)
    second_cursor = repository.begin_segment(second)
    second_frame = _frame(1, epoch_ms=1_000_000)
    second_receipt = _append_scored(repository, second_cursor, (second_frame,))
    snapshot = repository.snapshot_fit_corpus(second.fit_partition_digest)
    original_identity = snapshot.identity
    original_sequences = tuple(
        tuple(frame.sequence for frame in segment.scored_hold_frames)
        for segment in snapshot.segments
    )

    later = _frame(2, epoch_ms=1_000_000)
    repository.append(second_receipt.cursor, scored=(later,))
    current = repository.snapshot_fit_corpus(second.fit_partition_digest)
    historical = repository.snapshot_fit_corpus(
        second.fit_partition_digest,
        through_revision=original_identity.corpus_revision,
    )

    assert snapshot.identity == original_identity
    assert tuple(
        tuple(frame.sequence for frame in segment.scored_hold_frames)
        for segment in snapshot.segments
    ) == original_sequences
    assert historical.identity == original_identity
    assert tuple(item.segment_id for item in snapshot.identity.slices) == (
        "snapshot-a",
        "snapshot-b",
    )
    assert current.identity.slices[-1].through_ordinal == (
        snapshot.identity.slices[-1].through_ordinal + 1
    )
    assert snapshot.identity.corpus_digest == canonical_trajectory_digest(
        _corpus_payload(snapshot.identity)
    )


def test_fit_manifest_is_unique_ordered_bounded_and_has_exact_digest(
    repository: LearningTrajectoryRepository,
) -> None:
    partition: str | None = None
    for index in range(256):
        segment = _segment(
            f"manifest-{index:03d}",
            epoch_ms=index * 1_000_000,
            pre_roll_count=1,
            scored_count=1,
        )
        partition = segment.fit_partition_digest
        _finalize_segment(repository, segment)
    assert partition is not None

    snapshot = repository.snapshot_fit_corpus(partition)
    slices = snapshot.identity.slices
    segment_ids = tuple(item.segment_id for item in slices)
    assert len(slices) == 256
    assert len(set(segment_ids)) == len(segment_ids)
    assert segment_ids == tuple(f"manifest-{index:03d}" for index in range(256))
    assert all(isinstance(item, FitCorpusSlice) for item in slices)
    assert snapshot.identity.corpus_digest == canonical_trajectory_digest(
        _corpus_payload(snapshot.identity)
    )


def test_fit_run_queued_running_success_failure_stale_and_conflicting_completion(
    repository: LearningTrajectoryRepository,
) -> None:
    segment = _segment("fit-transitions", scored_count=1)
    repository.begin_segment(segment)
    snapshot = repository.snapshot_fit_corpus(segment.fit_partition_digest)

    queued_lineage = _lineage(snapshot, "fit-success")
    queued = repository.record_fit_request(snapshot, queued_lineage)
    assert isinstance(queued, FitRun)
    assert queued.status == "queued"
    assert repository.record_fit_request(snapshot, queued_lineage) == queued

    running = repository.record_fit_request(
        snapshot, replace(queued_lineage, result_status="running")
    )
    assert running.status == "running"
    candidate = _digest("candidate-success")
    succeeded = repository.complete_fit(
        queued.request_id, candidate_digest=candidate, error=None
    )
    assert succeeded.status == "succeeded"
    assert succeeded.candidate_digest == candidate
    assert succeeded.error is None
    assert repository.complete_fit(
        queued.request_id, candidate_digest=candidate, error=None
    ) == succeeded
    with pytest.raises(LearningTrajectoryConflictError, match="conflict"):
        repository.complete_fit(
            queued.request_id,
            candidate_digest=_digest("different-candidate"),
            error=None,
        )

    failed_lineage = _lineage(snapshot, "fit-failure", status="running")
    repository.record_fit_request(snapshot, failed_lineage)
    failed = repository.complete_fit(
        failed_lineage.request_id, candidate_digest=None, error="solver failed"
    )
    assert failed.status == "failed"
    assert failed.candidate_digest is None
    assert failed.error == "solver failed"

    stale_lineage = _lineage(snapshot, "fit-stale")
    repository.record_fit_request(snapshot, stale_lineage)
    repository.record_fit_request(
        snapshot, replace(stale_lineage, result_status="running")
    )
    stale = repository.record_fit_request(
        snapshot, replace(stale_lineage, result_status="stale")
    )
    assert stale.status == "stale"

    with pytest.raises(ValueError, match="candidate|error"):
        repository.complete_fit(
            "fit-failure", candidate_digest=None, error=None
        )
    with pytest.raises(ValueError, match="candidate|error"):
        repository.complete_fit(
            "fit-failure",
            candidate_digest=_digest("invalid-candidate"),
            error="cannot be both",
        )


def test_record_fit_request_rejects_new_terminal_statuses(
    repository: LearningTrajectoryRepository,
) -> None:
    segment = _segment("fit-terminal-request-rejection", scored_count=1)
    repository.begin_segment(segment)
    snapshot = repository.snapshot_fit_corpus(segment.fit_partition_digest)
    succeeded = _lineage(
        snapshot,
        "new-succeeded-fit",
        status="succeeded",
        candidate_digest=_digest("premature-candidate"),
    )
    failed = _lineage(snapshot, "new-failed-fit", status="failed")
    interrupted = _lineage(snapshot, "new-interrupted-fit")
    object.__setattr__(interrupted, "result_status", "interrupted")

    for lineage in (succeeded, failed, interrupted):
        with pytest.raises(ValueError, match="queued|running|terminal"):
            repository.record_fit_request(snapshot, lineage)


def test_recovery_interrupts_queued_and_running_fit_runs(
    repository: LearningTrajectoryRepository, database_path: Path
) -> None:
    segment = _segment("fit-recovery", scored_count=1)
    repository.begin_segment(segment)
    snapshot = repository.snapshot_fit_corpus(segment.fit_partition_digest)
    repository.record_fit_request(snapshot, _lineage(snapshot, "queued-fit"))
    repository.record_fit_request(
        snapshot, _lineage(snapshot, "running-fit", status="running")
    )

    restarted = LearningTrajectoryRepository(str(database_path))
    report = restarted.recover_open_segments(now_ms=_WALL_EPOCH_MS + 40_000_000)
    assert report.interrupted_fit_request_ids == ("queued-fit", "running-fit")
    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute(
            "SELECT request_id, status FROM learning_fit_run ORDER BY request_id"
        ).fetchall()
    finally:
        connection.close()
    assert rows == [("queued-fit", "interrupted"), ("running-fit", "interrupted")]


def test_fit_replay_is_exact_until_retention_evicts_source_without_pinning_it(
    repository: LearningTrajectoryRepository,
) -> None:
    source = _segment("fit-source-000", scored_count=1)
    _finalize_segment(repository, source)
    snapshot = repository.snapshot_fit_corpus(source.fit_partition_digest)
    original_identity = snapshot.identity
    original_sequences = tuple(
        tuple(frame.sequence for frame in segment.scored_hold_frames)
        for segment in snapshot.segments
    )
    repository.record_fit_request(
        snapshot, _lineage(snapshot, "evictable-fit", status="running")
    )

    replayed = repository.replay_fit("evictable-fit")
    assert replayed.identity == original_identity
    assert tuple(
        tuple(frame.sequence for frame in segment.scored_hold_frames)
        for segment in replayed.segments
    ) == original_sequences

    for index in range(255):
        _finalize_segment(
            repository,
            _segment(
                f"fit-source-{index + 1:03d}",
                epoch_ms=(index + 1) * 1_000_000,
                scored_count=1,
            ),
        )
    open_segment = _segment("fit-source-zzz-open", epoch_ms=300_000_000)
    repository.begin_segment(open_segment)

    assert repository.read_segment(source.segment_id) is None
    assert snapshot.identity == original_identity
    assert tuple(
        tuple(frame.sequence for frame in segment.scored_hold_frames)
        for segment in snapshot.segments
    ) == original_sequences
    with pytest.raises(FitCorpusEvictedError, match="corpus-evicted") as error:
        repository.replay_fit("evictable-fit")
    assert error.value.code == "corpus-evicted"


def test_terminal_fit_manifests_use_repository_visible_deterministic_bound(
    repository: LearningTrajectoryRepository, database_path: Path
) -> None:
    segment = _segment("bounded-fit-runs", scored_count=1)
    repository.begin_segment(segment)
    snapshot = repository.snapshot_fit_corpus(segment.fit_partition_digest)
    limit = repository.terminal_fit_run_limit
    assert type(limit) is int and 1 <= limit <= 256

    for index in range(limit + 3):
        request_id = f"terminal-fit-{index:04d}"
        repository.record_fit_request(
            snapshot, _lineage(snapshot, request_id, status="running")
        )
        repository.complete_fit(
            request_id, candidate_digest=None, error=f"failure-{index:04d}"
        )

    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute(
            "SELECT request_id FROM learning_fit_run "
            "WHERE status IN ('succeeded','failed','interrupted','stale') "
            "AND manifest_json IS NOT NULL ORDER BY completed_ms, request_id"
        ).fetchall()
    finally:
        connection.close()
    retained_request_ids = tuple(row[0] for row in rows)
    assert len(retained_request_ids) == limit
    assert retained_request_ids == tuple(
        f"terminal-fit-{index:04d}" for index in range(3, limit + 3)
    )
    assert repository.replay_fit(f"terminal-fit-{limit + 2:04d}").identity == snapshot.identity


def test_older_frame_payload_schema_is_explicitly_non_scoreable(
    database_path: Path,
) -> None:
    repository = LearningTrajectoryRepository(str(database_path))
    segment = _segment("old-frame-schema", scored_count=1)
    repository.begin_segment(segment)
    _executescript(
        database_path,
        """
        PRAGMA ignore_check_constraints=ON;
        UPDATE learning_trajectory_frame
        SET payload_schema_version=1
        WHERE segment_id='old-frame-schema';
        PRAGMA ignore_check_constraints=OFF;
        """,
    )

    reopened = LearningTrajectoryRepository(str(database_path))

    assert reopened.read_segment("old-frame-schema") is None
    assert reopened.status().quarantined_segment_count == 1
