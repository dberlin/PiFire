"""Behavioral contracts for off-path model persistence."""

import json
import threading

import pytest

import controller.runtime.model_persistence as model_persistence_module
from common.control_trace import AmbientSource
from common.controller_model_state import (
    MAX_SNAPSHOT_BYTES,
    CheckpointSaveOutcome,
    ControllerModelStore,
)
from common.learning_trajectory import (
    FrameDeliveryCertainty,
    HoldEntrySample,
    LearningTrajectoryFrame,
    LearningTrajectorySegment,
    TrajectoryBreakReason,
)
from common.model_evidence import (
    ActivationEvidence,
    CandidateAssessmentEvidence,
    ConfidenceDecisionEvidence,
    EvidenceKind,
    ForecastOriginEvidence,
    ModelEvidenceRecord,
    RefreshDiagnosticsEvidence,
)
from common.persistence.learning_trajectory import (
    LearningTrajectoryRepository,
    SegmentCursor,
)
from common.persistence.model_evidence import (
    append_model_evidence,
    commit_model_activation_phase,
    read_model_activation,
    read_model_evidence,
)
from controller.model_learning.activation import (
    ActivationPhase,
    PreparedActivationRecord,
    recover_startup_activation,
)
from controller.model_learning.contracts import ActivationPolicy, CandidateOrigin
from controller.runtime.model_persistence import (
    DurableActivationReceipt,
    DurableCheckpointReceipt,
    EvidenceSubmission,
    ModelPersistenceWorker,
    PersistenceReceipt,
    TrajectoryAppendBatch,
)
from tests.unit.runtime._persistence_helpers import _current_pair_descriptor

_DIGEST = "a" * 64
_OTHER_DIGEST = "b" * 64


class _Store:
    def __init__(self):
        self.saved = []
        self.first_save_started = threading.Event()
        self.release_first_save = threading.Event()

    def save_outcome(self, name, snapshot):
        self.saved.append((name, snapshot))
        if len(self.saved) == 1:
            self.first_save_started.set()
            self.release_first_save.wait(timeout=1.0)
        return CheckpointSaveOutcome.SAVED


class _Logger:
    def __init__(self):
        self.errors = []

    def error(self, message):
        self.errors.append(message)


def _evidence(evidence_id: str) -> ModelEvidenceRecord:
    return ModelEvidenceRecord(
        evidence_id=evidence_id,
        kind=EvidenceKind.FORECAST_ORIGIN,
        session_id="session-a",
        cook_id="cook-a",
        timestamp_ms=200,
        role_generation=1,
        model_digest=_OTHER_DIGEST,
        provenance_digest=_DIGEST,
        payload=ForecastOriginEvidence(
            origin_sequence=1,
            origin_time_ms=100,
            completion_time_ms=200,
            horizon_steps=3,
            incumbent_digest=_DIGEST,
            challenger_digest=_OTHER_DIGEST,
            incumbent_prediction_c=100.0,
            challenger_prediction_c=101.0,
            observed_temperature_c=102.0,
            incumbent_error_c=2.0,
            challenger_error_c=1.0,
            temperature_band="near-target",
            phase="coasting",
            ambient_source=AmbientSource.CONFIGURED,
            calibration_fit=False,
        ),
    )


def _refresh(evidence_id: str) -> ModelEvidenceRecord:
    return ModelEvidenceRecord(
        evidence_id=evidence_id,
        kind=EvidenceKind.REFRESH_DIAGNOSTICS,
        session_id="session-a",
        cook_id="cook-a",
        timestamp_ms=250,
        role_generation=1,
        model_digest=_OTHER_DIGEST,
        provenance_digest=_DIGEST,
        schema_version=2,
        payload=RefreshDiagnosticsEvidence(
            accepted=True,
            full_rank=True,
            finite_diagnostics=True,
            pole_magnitude=0.9,
            gain=1.0,
            delay_steps=3,
            covariance_finite=True,
            alignment_error_c=0.1,
            snapshot_round_trip=True,
            sequential_wins=2,
            generation_continuity=True,
            atomic_persistence=False,
            production_prospective=True,
            braking_error_c=1.0,
            incumbent_braking_error_c=2.0,
        ),
    )


def _activation(evidence_id: str) -> ModelEvidenceRecord:
    return ModelEvidenceRecord(
        evidence_id=evidence_id,
        kind=EvidenceKind.ACTIVATION,
        session_id="session-a",
        cook_id="cook-a",
        timestamp_ms=201,
        role_generation=1,
        model_digest=_DIGEST,
        provenance_digest=_OTHER_DIGEST,
        payload=ActivationEvidence(
            decision_id="decision-a",
            active_snapshot_json='{"revision": 2}',
            rollback_snapshot_json='{"revision": 1}',
            controller_configuration_digest=_OTHER_DIGEST,
        ),
    )


def _trajectory_frame(
    sequence: int,
    *,
    epoch_ms: int = 0,
    effective_mode: str = "Hold",
) -> LearningTrajectoryFrame:
    start_ms = epoch_ms + sequence * 20_000
    return LearningTrajectoryFrame(
        sequence=sequence,
        monotonic_start_ms=start_ms,
        monotonic_end_ms=start_ms + 20_000,
        wall_start_ms=1_700_000_000_000 + start_ms,
        wall_end_ms=1_700_000_020_000 + start_ms,
        chamber_temperature_c=225.0 + sequence,
        temperature_sample_monotonic_ms=start_ms + 20_000,
        temperature_sample_wall_ms=1_700_000_020_000 + start_ms,
        temperature_sample_age_ms=0,
        temperature_sample_wall_age_ms=0,
        temperature_sample_clock_skew_ms=0,
        source_temperature_units="C",
        settings_revision=7,
        probe_valid=True,
        probe_source="primary",
        ambient_temperature_c=20.0,
        ambient_source="configured",
        ambient_uncertainty_c=1.0,
        delivered_auger_on_seconds=5.0,
        realized_auger_duty=0.25,
        normalized_combustion_load=0.25,
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


def _stored_frame(
    sequence: int,
    *,
    epoch_ms: int = 0,
    effective_mode: str = "Hold",
) -> LearningTrajectoryFrame:
    return _trajectory_frame(
        sequence,
        epoch_ms=epoch_ms,
        effective_mode=effective_mode,
    )


def _stored_hold_entry(frame: LearningTrajectoryFrame) -> HoldEntrySample:
    return HoldEntrySample(
        monotonic_ms=frame.monotonic_start_ms,
        wall_ms=frame.wall_start_ms,
        chamber_temperature_c=frame.chamber_temperature_c,
        probe_valid=True,
        probe_source="primary",
    )


def _stored_segment(
    segment_id: str,
    *,
    epoch_ms: int = 0,
) -> LearningTrajectorySegment:
    frame = _stored_frame(0, epoch_ms=epoch_ms, effective_mode="Smoke")
    return LearningTrajectorySegment(
        schema_version=1,
        observation_schema_version=3,
        segment_id=segment_id,
        cook_id=f"cook-{segment_id}",
        trajectory_session_id=f"trajectory-{segment_id}",
        trace_session_ids=(f"trace-{segment_id}",),
        collection_provenance={"origin": "passive-online", "role_generation": 4},
        configuration_provenance={"controller": "MPC", "revision": 7},
        cadence_digest=_DIGEST,
        model_structure_digest=_OTHER_DIGEST,
        held_physics_digest=_DIGEST,
        delay_input_mapping_digest=_OTHER_DIGEST,
        actuation_mapping_digest=_DIGEST,
        scored_fan_regime_digest=_OTHER_DIGEST,
        ambient_semantics_digest=_DIGEST,
        pre_roll_frames=(frame,),
        hold_entry=None,
        scored_hold_frames=(),
        generation_audit_ranges=(
            {
                "start_sequence": 0,
                "end_sequence": 0,
                "role_generation": 4,
            },
        ),
        start_monotonic_ms=frame.monotonic_start_ms,
        end_monotonic_ms=frame.monotonic_end_ms,
        start_wall_ms=frame.wall_start_ms,
        end_wall_ms=frame.wall_end_ms,
        start_sequence=0,
        end_sequence=0,
        pre_roll_end_reason=None,
        terminal_break_reason=None,
        state="open",
        source_trace_digest=_OTHER_DIGEST,
        source_schema_version=7,
        source_row_digest=_DIGEST,
        build_provenance={"builder": "trajectory-runtime", "revision": 1},
    )


def _trajectory_batch(
    segment_id: str,
    *,
    sequence: int = 0,
    kind: str = "compound",
) -> TrajectoryAppendBatch:
    frame = _trajectory_frame(sequence)
    cursor = SegmentCursor(
        segment_id=segment_id,
        next_ordinal=sequence,
        chain_digest="0" * 64,
        corpus_revision=sequence,
    )
    if kind == "finalize":
        return TrajectoryAppendBatch(
            cursor=cursor,
            finalize_reason=TrajectoryBreakReason.STOP,
        )
    if kind == "pre-roll":
        return TrajectoryAppendBatch(cursor=cursor, pre_roll=(frame,))
    return TrajectoryAppendBatch(
        cursor=cursor,
        hold_entry=HoldEntrySample(
            monotonic_ms=frame.monotonic_start_ms,
            wall_ms=frame.wall_start_ms,
            chamber_temperature_c=frame.chamber_temperature_c,
            probe_valid=True,
            probe_source="primary",
        ),
        scored=(frame,),
        evidence=(_refresh(f"{segment_id}-evidence"),),
    )


def test_barrier_drains_prior_work_and_leaves_worker_usable() -> None:
    written: list[str] = []
    worker = ModelPersistenceWorker(
        _Store(),
        _Logger(),
        append_evidence=lambda records: written.extend(record.evidence_id for record in records),
    )
    try:
        assert worker.submit_evidence(_refresh("before-barrier")).accepted
        assert worker.barrier(timeout=1.0)
        assert written == ["before-barrier"]

        assert worker.submit_evidence(_refresh("after-barrier")).accepted
        assert worker.barrier(timeout=1.0)
        assert written == ["before-barrier", "after-barrier"]
    finally:
        worker.close(timeout=1.0)


def test_durable_checkpoint_receipt_reports_actual_store_save() -> None:
    store = _Store()
    store.release_first_save.set()
    worker = ModelPersistenceWorker(store, _Logger())

    try:
        receipt: DurableCheckpointReceipt = worker.submit_durable_checkpoint(
            "pid_sp",
            {"revision": 1},
        )

        assert receipt.accepted
        assert receipt.wait(timeout=1.0)
        assert receipt.completed
        assert receipt.durable
        assert receipt.error is None
        assert store.saved == [("pid_sp", {"revision": 1})]
    finally:
        worker.close(timeout=1.0)


def test_durable_checkpoint_receipt_reports_store_failure() -> None:
    class _FailingStore:
        def save_outcome(self, name, snapshot):
            return CheckpointSaveOutcome.FAILED

    worker = ModelPersistenceWorker(_FailingStore(), _Logger())

    try:
        receipt = worker.submit_durable_checkpoint(
            "pid_sp",
            {"revision": 1},
        )

        assert receipt.accepted
        assert not receipt.wait(timeout=1.0)
        assert receipt.completed
        assert not receipt.durable
        assert receipt.error == "RuntimeError: checkpoint store failed"
    finally:
        worker.close(timeout=1.0)


def test_checkpoint_terminal_protocol_writes_prepare_evidence_commit_in_order() -> None:
    store = _Store()
    store.release_first_save.set()
    persisted = []
    worker = ModelPersistenceWorker(
        store,
        _Logger(),
        append_evidence=lambda records: persisted.extend(records),
        read_evidence=lambda **_filters: list(persisted),
    )
    success = _evidence("success")
    failure = _evidence("failure")

    try:
        receipt = worker.submit_checkpoint_with_terminal_evidence(
            "pid_sp",
            {"revision": 1, "phase": "prepared"},
            {"revision": 2, "phase": "committed"},
            success,
            failure,
        )

        assert receipt.wait(timeout=1.0)
        assert store.saved == [
            ("pid_sp", {"revision": 1, "phase": "prepared"}),
            ("pid_sp", {"revision": 2, "phase": "committed"}),
        ]
        assert persisted == [success]
        assert worker.contains_evidence(success)
        mismatched = success.model_copy(update={"cook_id": "different-cook"})
        assert not worker.contains_evidence(mismatched)
    finally:
        worker.close(timeout=1.0)


def test_checkpoint_terminal_evidence_failure_leaves_only_safe_prepare() -> None:
    store = _Store()
    store.release_first_save.set()
    persisted = []
    success = _evidence("success")
    failure = _evidence("failure")

    def append(records):
        if records == (success,):
            raise RuntimeError("terminal append failed")
        persisted.extend(records)

    worker = ModelPersistenceWorker(
        store,
        _Logger(),
        append_evidence=append,
        read_evidence=lambda **_filters: list(persisted),
    )

    try:
        receipt = worker.submit_checkpoint_with_terminal_evidence(
            "pid_sp",
            {"revision": 1, "phase": "prepared"},
            {"revision": 2, "phase": "committed"},
            success,
            failure,
        )

        assert not receipt.wait(timeout=1.0)
        assert receipt.completed
        assert store.saved == [
            ("pid_sp", {"revision": 1, "phase": "prepared"}),
        ]
        assert persisted == [failure]
        assert not worker.contains_evidence(success)
    finally:
        worker.close(timeout=1.0)


def test_close_is_idempotent_joins_once_and_rejects_post_close_work(monkeypatch) -> None:
    threads = []
    real_thread = threading.Thread

    class _CountingThread(real_thread):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.join_count = 0
            threads.append(self)

        def join(self, timeout=None):
            self.join_count += 1
            return super().join(timeout=timeout)

    monkeypatch.setattr(model_persistence_module, "Thread", _CountingThread)
    store = _Store()
    store.release_first_save.set()
    worker = ModelPersistenceWorker(store, _Logger())
    assert worker.submit_checkpoint("mpc", {"revision": 1})

    assert worker.close(timeout=1.0)
    assert worker.close(timeout=1.0)
    assert not worker.submit_checkpoint("mpc", {"revision": 2})
    assert not worker.submit_evidence(_refresh("after-close")).accepted
    assert not worker.submit_activation_phase(
        _prepared_activation(),
        expected_phase=None,
    ).accepted

    rejected: PersistenceReceipt = worker.submit_trajectory_batch(_trajectory_batch("after-close"))
    assert not rejected.accepted
    assert rejected.completed
    assert not rejected.durable
    assert rejected.gap is not None
    assert rejected.gap.reason == "persistence-closed"
    assert len(threads) == 1
    assert threads[0].join_count == 1


def test_priority_fifo_and_timed_out_barrier_fence_prevent_overtake() -> None:
    started = threading.Event()
    release = threading.Event()
    order: list[str] = []

    class _PriorityStore(_Store):
        def save_outcome(self, name, snapshot):
            order.append(f"checkpoint:{name}:{snapshot['revision']}")
            return CheckpointSaveOutcome.SAVED

    def append_evidence(records):
        record = records[0]
        if record.evidence_id == "running":
            started.set()
            assert release.wait(timeout=1.0)
        order.append(f"evidence:{record.evidence_id}")

    def resulting_cursor(batch):
        if batch.begin_segment is not None:
            next_ordinal = len(batch.begin_segment.pre_roll_frames) + len(batch.begin_segment.scored_hold_frames)
            return SegmentCursor(
                segment_id=batch.begin_segment.segment_id,
                next_ordinal=next_ordinal,
                chain_digest=f"{next_ordinal:064x}",
                corpus_revision=1,
            )
        assert batch.cursor is not None
        if batch.break_reason is not None:
            assert batch.next_segment is not None
            next_ordinal = len(batch.next_segment.pre_roll_frames) + len(batch.next_segment.scored_hold_frames)
            return SegmentCursor(
                segment_id=batch.next_segment.segment_id,
                next_ordinal=next_ordinal,
                chain_digest=f"{next_ordinal:064x}",
                corpus_revision=batch.cursor.corpus_revision + 1,
            )
        if batch.finalize_reason is not None:
            return SegmentCursor(
                segment_id=batch.cursor.segment_id,
                next_ordinal=batch.cursor.next_ordinal,
                chain_digest=batch.cursor.chain_digest,
                corpus_revision=batch.cursor.corpus_revision + 1,
            )
        next_ordinal = batch.cursor.next_ordinal + len(batch.pre_roll) + len(batch.scored)
        return SegmentCursor(
            segment_id=batch.cursor.segment_id,
            next_ordinal=next_ordinal,
            chain_digest=f"{next_ordinal:064x}",
            corpus_revision=batch.cursor.corpus_revision + 1,
        )

    durable_revision = 0

    def persist_batch(batch):
        nonlocal durable_revision
        assert batch.cursor is not None
        order.append(f"trajectory:{batch.cursor.segment_id}:{batch.cursor.next_ordinal}")
        durable_revision += 1
        result = resulting_cursor(batch)
        return SegmentCursor(
            segment_id=result.segment_id,
            next_ordinal=result.next_ordinal,
            chain_digest=result.chain_digest,
            corpus_revision=durable_revision,
        )

    def persist_phase(record, _expected_phase):
        order.append(f"activation:{record.phase.value}")

    worker = ModelPersistenceWorker(
        _PriorityStore(),
        _Logger(),
        append_evidence=append_evidence,
        persist_trajectory_batch=persist_batch,
        persist_activation_phase=persist_phase,
    )
    prepared = _prepared_activation()
    assert worker.submit_evidence(_refresh("running")).accepted
    assert started.wait(timeout=1.0)
    assert worker.submit_checkpoint("ordinary", {"revision": 1})
    assert worker.submit_evidence(_refresh("ordinary-evidence")).accepted
    pre_roll = worker.submit_trajectory_batch(_trajectory_batch("pre-roll", kind="pre-roll"))
    compound_a_batch = _trajectory_batch("compound")
    compound_a = worker.submit_trajectory_batch(compound_a_batch)
    compound_b_source = _trajectory_batch("compound", sequence=1)
    compound_b = worker.submit_trajectory_batch(
        TrajectoryAppendBatch(
            cursor=resulting_cursor(compound_a_batch),
            hold_entry=compound_b_source.hold_entry,
            scored=compound_b_source.scored,
            evidence=compound_b_source.evidence,
        )
    )
    boundary = worker.submit_trajectory_batch(_trajectory_batch("segment-finalize", kind="finalize"))
    activation_before = worker.submit_activation_phase(
        prepared,
        expected_phase=None,
    )
    assert not worker.barrier(timeout=0.0)
    activation_after = worker.submit_activation_phase(
        prepared,
        expected_phase=None,
    )

    release.set()
    try:
        assert worker.barrier(timeout=1.0)
        assert all(
            receipt.wait(timeout=1.0)
            for receipt in (
                pre_roll,
                compound_a,
                compound_b,
                boundary,
                activation_before,
                activation_after,
            )
        )
    finally:
        worker.close(timeout=1.0)

    assert order == [
        "evidence:running",
        "activation:prepared",
        "trajectory:segment-finalize:0",
        "trajectory:compound:0",
        "trajectory:compound:1",
        "trajectory:pre-roll:0",
        "checkpoint:ordinary:1",
        "evidence:ordinary-evidence",
        "activation:prepared",
    ]


@pytest.mark.parametrize("fail_evidence", (False, True))
def test_compound_scored_frame_and_evidence_commit_or_roll_back_atomically(
    fail_evidence: bool,
    ds,
    monkeypatch,
) -> None:
    repository = LearningTrajectoryRepository()
    worker = ModelPersistenceWorker(
        _Store(),
        _Logger(),
        trajectory_repository=repository,
    )
    begin = worker.submit_trajectory_batch(TrajectoryAppendBatch(begin_segment=_stored_segment("atomic")))
    assert begin.wait(timeout=1.0)
    assert begin.cursor is not None

    frame = _stored_frame(1)
    evidence = _refresh("atomic-evidence")
    if fail_evidence:
        real_append = model_persistence_module.append_model_evidence_in_transaction

        def append_then_fail(connection, records):
            real_append(connection, records)
            raise RuntimeError("injected evidence failure")

        monkeypatch.setattr(
            model_persistence_module,
            "append_model_evidence_in_transaction",
            append_then_fail,
        )

    batch = TrajectoryAppendBatch(
        cursor=begin.cursor,
        hold_entry=_stored_hold_entry(frame),
        scored=(frame,),
        evidence=(evidence,),
    )
    receipt = worker.submit_trajectory_batch(batch)
    try:
        assert receipt.accepted
        assert receipt.wait(timeout=1.0) is (not fail_evidence)
        assert receipt.completed
        assert receipt.durable is (not fail_evidence)
        assert (receipt.error is not None) is fail_evidence
        stored = repository.read_segment("atomic")
        assert stored is not None
        assert len(stored.scored_hold_frames) == (0 if fail_evidence else 1)
        assert [record.evidence_id for record in read_model_evidence()] == (
            [] if fail_evidence else ["atomic-evidence"]
        )
        assert isinstance(batch.scored, tuple)
        assert isinstance(batch.evidence, tuple)
    finally:
        worker.close(timeout=1.0)


def test_trace_gap_quarantine_runs_on_persistence_worker_and_blocks_evidence(ds) -> None:
    repository = LearningTrajectoryRepository()
    worker = ModelPersistenceWorker(
        _Store(),
        _Logger(),
        trajectory_repository=repository,
    )
    begin = worker.submit_trajectory_batch(TrajectoryAppendBatch(begin_segment=_stored_segment("trace-gap")))
    assert begin.wait(timeout=1.0)

    quarantine = worker.submit_trajectory_quarantine("trace-gap")
    try:
        assert quarantine.accepted
        assert worker.evidence_blocked
        assert quarantine.wait(timeout=1.0)
        assert repository.read_segment("trace-gap") is None
        assert repository.status().quarantined_segment_count == 1
    finally:
        worker.close(timeout=1.0)


def test_worker_failure_completes_pending_quarantine_receipt() -> None:
    started = threading.Event()
    release = threading.Event()

    class _FailingStore:
        def save_outcome(self, _name, _snapshot):
            started.set()
            assert release.wait(timeout=1.0)
            raise RuntimeError("checkpoint-write-failed")

    worker = ModelPersistenceWorker(_FailingStore(), _Logger())
    try:
        assert worker.submit_checkpoint("mpc", {"revision": 1})
        assert started.wait(timeout=1.0)
        quarantine = worker.submit_trajectory_quarantine("trace-gap")
        assert quarantine.accepted
        assert not quarantine.completed

        release.set()

        assert not quarantine.wait(timeout=1.0)
        assert quarantine.completed
        assert not quarantine.durable
        assert quarantine.error == "RuntimeError: checkpoint-write-failed"
    finally:
        release.set()
        worker.close(timeout=1.0)


def test_injected_trajectory_callback_must_return_resulting_cursor() -> None:
    def missing_cursor(_batch):
        return None

    worker = ModelPersistenceWorker(
        _Store(),
        _Logger(),
        persist_trajectory_batch=missing_cursor,
    )
    receipt = worker.submit_trajectory_batch(_trajectory_batch("invalid-cursor"))
    try:
        assert receipt.accepted
        assert not receipt.wait(timeout=1.0)
        assert receipt.completed
        assert receipt.error is not None
        assert "must return SegmentCursor" in receipt.error
        assert receipt.cursor is None
    finally:
        worker.close(timeout=1.0)


def test_real_repository_cursor_progresses_across_stale_queued_batches(ds) -> None:
    repository = LearningTrajectoryRepository()
    worker = ModelPersistenceWorker(
        _Store(),
        _Logger(),
        trajectory_repository=repository,
    )
    begin = worker.submit_trajectory_batch(TrajectoryAppendBatch(begin_segment=_stored_segment("cursor")))
    assert begin.wait(timeout=1.0)
    assert begin.cursor is not None
    stale = begin.cursor
    pre_roll = worker.submit_trajectory_batch(
        TrajectoryAppendBatch(
            cursor=stale,
            pre_roll=(_stored_frame(1, effective_mode="Smoke"),),
        )
    )
    scored_frame = _stored_frame(2)
    scored = worker.submit_trajectory_batch(
        TrajectoryAppendBatch(
            cursor=stale,
            hold_entry=_stored_hold_entry(scored_frame),
            scored=(scored_frame,),
            evidence=(_refresh("cursor-evidence"),),
        )
    )
    finalized = worker.submit_trajectory_batch(
        TrajectoryAppendBatch(
            cursor=stale,
            finalize_reason=TrajectoryBreakReason.STOP,
        )
    )
    try:
        assert pre_roll.wait(timeout=1.0)
        assert scored.wait(timeout=1.0)
        assert finalized.wait(timeout=1.0)
        assert pre_roll.cursor is not None
        assert scored.cursor is not None
        assert finalized.cursor is not None
        assert pre_roll.cursor.next_ordinal == 2
        assert scored.cursor.next_ordinal == 3
        assert finalized.cursor.next_ordinal == 3
        assert (
            stale.corpus_revision
            < pre_roll.cursor.corpus_revision
            < scored.cursor.corpus_revision
            < finalized.cursor.corpus_revision
        )
        stored = repository.read_segment("cursor")
        assert stored is not None
        assert stored.state == "finalized"
        assert stored.terminal_break_reason is TrajectoryBreakReason.STOP
    finally:
        worker.close(timeout=1.0)


def test_interleaved_segments_rebase_cached_global_corpus_revision(ds) -> None:
    repository = LearningTrajectoryRepository()
    worker = ModelPersistenceWorker(
        _Store(),
        _Logger(),
        trajectory_repository=repository,
    )
    begin_a = worker.submit_trajectory_batch(TrajectoryAppendBatch(begin_segment=_stored_segment("interleaved-a")))
    assert begin_a.wait(timeout=1.0)
    assert begin_a.cursor is not None
    stale_a = begin_a.cursor
    begin_b = worker.submit_trajectory_batch(
        TrajectoryAppendBatch(begin_segment=_stored_segment("interleaved-b", epoch_ms=100_000))
    )
    assert begin_b.wait(timeout=1.0)
    assert begin_b.cursor is not None
    append_a = worker.submit_trajectory_batch(
        TrajectoryAppendBatch(
            cursor=stale_a,
            pre_roll=(_stored_frame(1, effective_mode="Smoke"),),
        )
    )
    try:
        assert append_a.wait(timeout=1.0)
        assert append_a.cursor is not None
        assert append_a.cursor.corpus_revision > begin_b.cursor.corpus_revision
        stored_a = repository.read_segment("interleaved-a")
        assert stored_a is not None
        assert len(stored_a.pre_roll_frames) == 2
        assert not worker.failed
    finally:
        worker.close(timeout=1.0)


def test_trajectory_queue_rejection_returns_explicit_gap_and_failure() -> None:
    started = threading.Event()
    release = threading.Event()

    def persist_batch(_batch):
        started.set()
        assert release.wait(timeout=1.0)
        assert _batch.cursor is not None
        return _batch.cursor

    worker = ModelPersistenceWorker(
        _Store(),
        _Logger(),
        trajectory_capacity=1,
        persist_trajectory_batch=persist_batch,
    )
    first = worker.submit_trajectory_batch(_trajectory_batch("running"))
    assert started.wait(timeout=1.0)
    queued = worker.submit_trajectory_batch(_trajectory_batch("queued", sequence=1))
    rejected = worker.submit_trajectory_batch(_trajectory_batch("rejected", sequence=2))
    try:
        assert first.accepted
        assert queued.accepted
        assert not rejected.accepted
        assert rejected.completed
        assert not rejected.durable
        assert rejected.error == "trajectory-queue-overflow"
        assert rejected.gap is not None
        assert rejected.gap.reason == "trajectory-queue-overflow"
        assert rejected.gap.break_reason is TrajectoryBreakReason.RECORDER_GAP
    finally:
        release.set()
        worker.close(timeout=1.0)
    assert first.completed and first.durable
    assert queued.completed and queued.durable


def test_rejected_lineage_stays_blocked_until_durable_break_and_begin(ds) -> None:
    repository = LearningTrajectoryRepository()
    started = threading.Event()
    release = threading.Event()
    worker = ModelPersistenceWorker(
        _Store(),
        _Logger(),
        trajectory_capacity=1,
        trajectory_repository=repository,
    )
    begin = worker.submit_trajectory_batch(TrajectoryAppendBatch(begin_segment=_stored_segment("gap-lineage")))
    assert begin.wait(timeout=1.0)
    assert begin.cursor is not None
    stale = begin.cursor
    real_persist = worker._default_persist_trajectory_batch
    blocked_once = False

    def block_first_append(batch):
        nonlocal blocked_once
        if (
            not blocked_once
            and batch.cursor is not None
            and batch.cursor.segment_id == "gap-lineage"
            and batch.pre_roll
        ):
            blocked_once = True
            started.set()
            assert release.wait(timeout=1.0)
        return real_persist(batch)

    worker._persist_trajectory_batch_callback = block_first_append
    running = worker.submit_trajectory_batch(
        TrajectoryAppendBatch(
            cursor=stale,
            pre_roll=(_stored_frame(1, effective_mode="Smoke"),),
        )
    )
    assert started.wait(timeout=1.0)
    queued = worker.submit_trajectory_batch(
        TrajectoryAppendBatch(
            cursor=stale,
            pre_roll=(_stored_frame(2, effective_mode="Smoke"),),
        )
    )
    rejected = worker.submit_trajectory_batch(
        TrajectoryAppendBatch(
            cursor=stale,
            pre_roll=(_stored_frame(3, effective_mode="Smoke"),),
        )
    )
    blocked = worker.submit_trajectory_batch(
        TrajectoryAppendBatch(
            cursor=stale,
            pre_roll=(_stored_frame(4, effective_mode="Smoke"),),
        )
    )
    boundary = worker.submit_trajectory_batch(
        TrajectoryAppendBatch(
            cursor=stale,
            break_reason=TrajectoryBreakReason.RECORDER_GAP,
            next_segment=_stored_segment("after-gap", epoch_ms=100_000),
        )
    )
    assert running.accepted and queued.accepted and boundary.accepted
    assert not rejected.accepted
    assert rejected.gap is not None
    assert not blocked.accepted
    assert blocked.error == "trajectory-lineage-blocked"
    release.set()
    try:
        assert running.wait(timeout=1.0)
        assert queued.wait(timeout=1.0)
        assert boundary.wait(timeout=1.0)
        assert boundary.cursor is not None
        continued = worker.submit_trajectory_batch(
            TrajectoryAppendBatch(
                cursor=boundary.cursor,
                pre_roll=(
                    _stored_frame(
                        1,
                        epoch_ms=100_000,
                        effective_mode="Smoke",
                    ),
                ),
            )
        )
        assert continued.wait(timeout=1.0)
        old = repository.read_segment("gap-lineage")
        assert old is not None
        assert old.terminal_break_reason is TrajectoryBreakReason.RECORDER_GAP
    finally:
        worker.close(timeout=1.0)


def test_global_capacity_reserves_boundary_and_activation_slots() -> None:
    started = threading.Event()
    release = threading.Event()
    store = _Store()
    store.release_first_save.set()

    def block_evidence(_records):
        started.set()
        assert release.wait(timeout=1.0)

    def persist_cursor(batch):
        assert batch.cursor is not None
        return batch.cursor

    def persist_phase(_record, _expected):
        return None

    worker = ModelPersistenceWorker(
        store,
        _Logger(),
        work_capacity=4,
        activation_reserve=1,
        boundary_reserve=1,
        append_evidence=block_evidence,
        persist_trajectory_batch=persist_cursor,
        persist_activation_phase=persist_phase,
    )
    assert worker.submit_evidence(_refresh("capacity-running")).accepted
    assert started.wait(timeout=1.0)
    assert worker.submit_checkpoint("ordinary-a", {"revision": 1})
    assert not worker.submit_checkpoint("ordinary-b", {"revision": 1})
    boundary = worker.submit_trajectory_batch(_trajectory_batch("capacity-boundary", kind="finalize"))
    first_activation = worker.submit_activation_phase(
        _prepared_activation(),
        expected_phase=None,
    )
    second_activation = worker.submit_activation_phase(
        _prepared_activation(),
        expected_phase=None,
    )
    assert boundary.accepted
    assert first_activation.accepted
    assert not second_activation.accepted
    assert not worker.barrier(timeout=0.0)
    release.set()
    try:
        assert boundary.wait(timeout=1.0)
        assert first_activation.wait(timeout=1.0)
    finally:
        worker.close(timeout=1.0)


def test_close_retry_waits_for_same_single_stop_to_finish() -> None:
    started = threading.Event()
    release = threading.Event()
    retry_finished = threading.Event()
    retry_results = []

    def block_evidence(_records):
        started.set()
        assert release.wait(timeout=1.0)

    worker = ModelPersistenceWorker(
        _Store(),
        _Logger(),
        append_evidence=block_evidence,
    )
    assert worker.submit_evidence(_refresh("close-running")).accepted
    assert started.wait(timeout=1.0)
    assert not worker.close(timeout=0.0)

    def retry_close():
        retry_results.append(worker.close(timeout=1.0))
        retry_finished.set()

    retry = threading.Thread(target=retry_close)
    retry.start()
    assert not retry_finished.wait(timeout=0.01)
    release.set()
    assert retry_finished.wait(timeout=1.0)
    retry.join(timeout=1.0)
    assert retry_results == [True]


def test_activation_and_checkpoint_receipts_survive_trajectory_priority() -> None:
    started = threading.Event()
    release = threading.Event()
    store = _Store()
    store.release_first_save.set()
    persisted_evidence = []

    def persist_batch(_batch):
        started.set()
        assert release.wait(timeout=1.0)
        assert _batch.cursor is not None
        return _batch.cursor

    worker = ModelPersistenceWorker(
        store,
        _Logger(),
        append_evidence=lambda records: persisted_evidence.extend(records),
        persist_trajectory_batch=persist_batch,
    )
    trajectory_receipt = worker.submit_trajectory_batch(_trajectory_batch("receipt-preservation"))
    assert started.wait(timeout=1.0)
    confidence = _confidence_for(_prepared_activation())
    confidence_receipt = worker.submit_activation_confidence(confidence)
    assert confidence_receipt.accepted
    assert not confidence_receipt.completed
    assert worker.submit_checkpoint("mpc", {"revision": 9})

    release.set()
    try:
        assert trajectory_receipt.wait(timeout=1.0)
        assert confidence_receipt.wait(timeout=1.0)
        assert confidence_receipt.completed
        assert confidence_receipt.durable
        assert confidence_receipt.error is None
        assert worker.barrier(timeout=1.0)
        assert persisted_evidence == [confidence]
        assert store.saved == [("mpc", {"revision": 9})]
    finally:
        worker.close(timeout=1.0)


def test_checkpoint_submission_coalesces_latest_owned_snapshot_without_blocking():
    store = _Store()
    worker = ModelPersistenceWorker(store, _Logger())
    try:
        assert worker.submit_checkpoint("mpc", {"revision": 1, "parameters": {"gain": 1}})
        assert store.first_save_started.wait(timeout=1.0)
        assert worker.submit_checkpoint("mpc", {"revision": 2, "parameters": {"gain": 2}})
        store.release_first_save.set()
        assert worker.close(timeout=1.0)
    finally:
        store.release_first_save.set()
        worker.close(timeout=1.0)

    assert [snapshot["revision"] for _, snapshot in store.saved] == [1, 2]


def test_failed_checkpoint_restage_preserves_previous_queued_payload() -> None:
    started = threading.Event()
    release = threading.Event()

    class _StagingStore(_Store):
        def __init__(self):
            super().__init__()
            self.release_first_save.set()
            self.staged_revisions = []

        def stage_owned(self, _name, snapshot):
            self.staged_revisions.append(snapshot["revision"])
            return snapshot["revision"] != 2

    def block_evidence(_records):
        started.set()
        assert release.wait(timeout=1.0)

    store = _StagingStore()
    worker = ModelPersistenceWorker(
        store,
        _Logger(),
        append_evidence=block_evidence,
    )
    assert worker.submit_evidence(_refresh("stage-running")).accepted
    assert started.wait(timeout=1.0)
    assert worker.submit_checkpoint("mpc", {"revision": 1, "gain": 1.0})
    assert not worker.submit_checkpoint("mpc", {"revision": 2, "gain": 2.0})
    release.set()
    try:
        assert worker.barrier(timeout=1.0)
    finally:
        worker.close(timeout=1.0)

    assert store.staged_revisions == [1, 2]
    assert store.saved == [("mpc", {"revision": 1, "gain": 1.0})]


def test_resubmitting_a_revision_already_being_saved_does_not_persist_it_twice():
    """The de-dup window has to cover the save, not just the queue.

    _next_work_locked pops the pending checkpoint before _run releases the lock
    and performs the save. A submission arriving inside that window saw no
    pending entry at all, so it skipped the revision comparison entirely and
    enqueued a revision that was already on its way to the store -- persisting
    the same revision twice.
    """
    store = _Store()
    worker = ModelPersistenceWorker(store, _Logger())
    try:
        assert worker.submit_checkpoint("mpc", {"revision": 7, "parameters": {"gain": 1}})
        assert store.first_save_started.wait(timeout=1.0)
        # The worker has dequeued revision 7 and is blocked inside save_outcome,
        # so _pending_checkpoints no longer holds it.
        assert worker.submit_checkpoint("mpc", {"revision": 7, "parameters": {"gain": 1}})
        store.release_first_save.set()
        assert worker.close(timeout=1.0)
    finally:
        store.release_first_save.set()
        worker.close(timeout=1.0)

    assert [snapshot["revision"] for _, snapshot in store.saved] == [7]


def test_resubmitting_a_revision_already_saved_is_not_saved_again():
    """De-dup has to outlive the save, not just the queue and the in-flight window.

    Once the save finishes, _pending_checkpoints and _inflight_checkpoints are
    both empty again, so a resubmission of a revision already durably stored had
    nothing left to compare against: it was queued and written a second time.
    The real store answers NONADVANCING for that, so nothing is corrupted -- but
    the worker should not spend a write to discover it, and a store that simply
    records what it is asked to save sees the duplicate.
    """
    store = _Store()
    worker = ModelPersistenceWorker(store, _Logger())
    try:
        assert worker.submit_checkpoint("mpc", {"revision": 7, "parameters": {"gain": 1}})
        assert store.first_save_started.wait(timeout=1.0)
        store.release_first_save.set()
        # Wait for the worker to finish the save and drop its in-flight record,
        # which is the exact state the second submission has to survive.
        with worker._condition:
            assert worker._condition.wait_for(lambda: not worker._inflight_checkpoints, timeout=1.0)

        assert worker.submit_checkpoint("mpc", {"revision": 7, "parameters": {"gain": 1}})
        assert worker.close(timeout=1.0)
    finally:
        store.release_first_save.set()
        worker.close(timeout=1.0)

    assert [snapshot["revision"] for _, snapshot in store.saved] == [7]


def test_bounded_evidence_overflow_returns_a_typed_gap_and_never_drops_activation():
    store = _Store()
    written = []
    activations = []
    worker = ModelPersistenceWorker(
        store,
        _Logger(),
        evidence_capacity=1,
        append_evidence=written.extend,
        commit_activation=activations.append,
    )
    try:
        assert worker.submit_checkpoint("mpc", {"revision": 1})
        assert store.first_save_started.wait(timeout=1.0)
        first = worker.submit_evidence(_evidence("first"))
        second = worker.submit_evidence(_evidence("second"))
        assert isinstance(first, EvidenceSubmission) and first.accepted
        assert isinstance(second, EvidenceSubmission) and not second.accepted
        assert second.recorder_gap is not None
        assert second.recorder_gap.kind is EvidenceKind.RECORDER_GAP
        assert worker.evidence_blocked
        assert worker.commit_activation(_activation("activation"))
        store.release_first_save.set()
        assert worker.close(timeout=1.0)
    finally:
        store.release_first_save.set()
        worker.close(timeout=1.0)

    assert written == [_evidence("first"), second.recorder_gap]
    assert activations == [_activation("activation")]


def test_evidence_submission_rejects_after_async_write_failure():
    failed = threading.Event()

    def fail_append(_records):
        failed.set()
        raise RuntimeError("disk unavailable")

    worker = ModelPersistenceWorker(_Store(), _Logger(), append_evidence=fail_append)
    try:
        assert worker.submit_evidence(_evidence("first")).accepted
        assert failed.wait(timeout=1.0)
        rejected = worker.submit_evidence(_evidence("second"))
        assert not rejected.accepted
        assert rejected.recorder_gap is not None
        assert rejected.recorder_gap.payload.reason == "persistence-failed"
    finally:
        worker.close(timeout=1.0)


def test_checkpoint_uses_the_store_default_json_byte_boundary_before_enqueue():
    logger = _Logger()
    store = _Store()
    store.release_first_save.set()
    worker = ModelPersistenceWorker(store, logger)
    snapshot = {"revision": 1, "blob": ""}
    snapshot["blob"] = "x" * (MAX_SNAPSHOT_BYTES - len(json.dumps(snapshot, allow_nan=False).encode("utf-8")))
    oversized = dict(snapshot, revision=2, blob=snapshot["blob"] + "x")
    try:
        assert len(json.dumps(snapshot, allow_nan=False).encode("utf-8")) == MAX_SNAPSHOT_BYTES
        assert worker.submit_checkpoint("mpc", snapshot)
        assert not worker.submit_checkpoint("mpc", oversized)
        assert worker.close(timeout=1.0)
    finally:
        store.release_first_save.set()
        worker.close(timeout=1.0)


def test_checkpoint_rejects_malformed_nonfinite_and_oversized_snapshots_synchronously():
    logger = _Logger()
    worker = ModelPersistenceWorker(_Store(), logger)
    try:
        assert not worker.submit_checkpoint("mpc", {"revision": -1})
        assert not worker.submit_checkpoint("mpc", {"revision": 1, "gain": float("nan")})
        assert not worker.submit_checkpoint("mpc", {"revision": 1, "blob": "x" * 65_537})
        assert logger.errors
    finally:
        worker.close(timeout=1.0)


def test_equal_checkpoint_revision_is_an_explicit_healthy_nonadvancing_outcome():
    state = {"version": 1, "models": {"mpc": {"revision": 1}}}
    store = ControllerModelStore(
        reader=lambda _key: state,
        writer=lambda _key, _value: None,
        conditional_writer=lambda _name, _snapshot: False,
    )
    worker = ModelPersistenceWorker(store, _Logger())
    try:
        assert worker.submit_checkpoint("mpc", {"revision": 1})
        assert worker.close(timeout=1.0)
        assert store.save_outcome("mpc", {"revision": 1}) is CheckpointSaveOutcome.NONADVANCING
        assert not worker.evidence_blocked
    finally:
        worker.close(timeout=1.0)


def test_conditional_checkpoint_writer_failure_blocks_later_submissions():
    state = {"version": 1, "models": {}}
    store = ControllerModelStore(
        reader=lambda _key: state,
        writer=lambda _key, _value: None,
        conditional_writer=lambda _name, _snapshot: False,
    )
    worker = ModelPersistenceWorker(store, _Logger())
    try:
        assert worker.submit_checkpoint("mpc", {"revision": 1})
        assert worker.close(timeout=1.0)
        assert worker.evidence_blocked
        assert not worker.submit_checkpoint("mpc", {"revision": 2})
        assert not worker.submit_evidence(_evidence("later")).accepted
        assert not worker.commit_activation(_activation("later"))
    finally:
        worker.close(timeout=1.0)


def test_save_only_store_cannot_coalesce_a_failed_checkpoint_as_healthy():
    class SaveOnlyStore:
        def __init__(self):
            self.save_calls = 0

        def save(self, _name, _snapshot):
            self.save_calls += 1
            return False

    store = SaveOnlyStore()
    worker = ModelPersistenceWorker(store, _Logger())
    try:
        assert worker.submit_checkpoint("mpc", {"revision": 1})
        assert worker.close(timeout=1.0)
        assert store.save_calls == 0
        assert worker.evidence_blocked
    finally:
        worker.close(timeout=1.0)


def test_explicit_failed_checkpoint_outcome_blocks_later_submissions():
    class FailedStore:
        def save_outcome(self, _name, _snapshot):
            return CheckpointSaveOutcome.FAILED

    worker = ModelPersistenceWorker(FailedStore(), _Logger())
    try:
        assert worker.submit_checkpoint("mpc", {"revision": 1})
        assert worker.close(timeout=1.0)
        assert worker.evidence_blocked
        assert not worker.submit_checkpoint("mpc", {"revision": 2})
        assert not worker.submit_evidence(_evidence("later")).accepted
        assert not worker.commit_activation(_activation("later"))
    finally:
        worker.close(timeout=1.0)


def test_evidence_batch_overflow_preserves_queued_fifo_and_records_every_omitted_origin():
    store = _Store()
    written = []
    worker = ModelPersistenceWorker(
        store,
        _Logger(),
        evidence_capacity=1,
        append_evidence=written.extend,
    )
    try:
        assert worker.submit_checkpoint("mpc", {"revision": 1})
        assert store.first_save_started.wait(timeout=1.0)
        assert worker.submit_evidence(_evidence("first")).accepted

        rejected = worker.submit_evidence_batch((_evidence("second"), _evidence("third")))
        assert not rejected.accepted
        assert rejected.recorder_gap is not None
        assert rejected.recorder_gap.payload.lost_record_count == 2
        assert worker.evidence_blocked
        store.release_first_save.set()
        assert worker.close(timeout=1.0)
    finally:
        store.release_first_save.set()
        worker.close(timeout=1.0)

    assert written == [_evidence("first"), rejected.recorder_gap]


def test_worker_commits_each_accepted_evidence_batch_once_and_never_partially() -> None:
    started = threading.Event()
    release = threading.Event()
    written = []

    def append(records):
        started.set()
        release.wait(timeout=1.0)
        written.append(tuple(records))

    worker = ModelPersistenceWorker(_Store(), _Logger(), append_evidence=append)
    batch = (_evidence("first"), _refresh("refresh"), _evidence("second"))
    try:
        assert worker.submit_evidence_batch(batch).accepted
        assert started.wait(timeout=1.0)
        assert written == []
        release.set()
        assert worker.close(timeout=1.0)
    finally:
        release.set()
        worker.close(timeout=1.0)

    assert written[0][0] == batch[0]
    assert written[0][2] == batch[2]
    assert written[0][1].payload.atomic_persistence is True


def _prepared_activation() -> PreparedActivationRecord:
    incumbent = _current_pair_descriptor(
        50.0,
        candidate_generation=3,
        role_generation=4,
    )
    candidate = _current_pair_descriptor(
        40.0,
        candidate_generation=4,
        role_generation=5,
    )
    return PreparedActivationRecord.prepared(
        timestamp_ms=1_000,
        incumbent=incumbent,
        candidate=candidate,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        policy=ActivationPolicy.CAUSAL_AUTO,
        decision_id="decision-grey-4",
    )


def test_activation_submission_returns_an_explicit_receipt_not_a_durability_claim() -> None:
    started = threading.Event()
    release = threading.Event()
    writes: list[tuple[PreparedActivationRecord, ActivationPhase | None]] = []

    def persist(record, expected_phase):
        started.set()
        release.wait(timeout=1.0)
        writes.append((record, expected_phase))

    worker = ModelPersistenceWorker(_Store(), _Logger(), persist_activation_phase=persist)
    prepared = _prepared_activation()
    try:
        receipt = worker.submit_activation_phase(prepared, expected_phase=None)
        assert isinstance(receipt, DurableActivationReceipt)
        assert receipt.accepted
        assert not receipt.completed
        assert not receipt.durable
        assert started.wait(timeout=1.0)
        assert not receipt.wait(timeout=0.01)
        release.set()
        assert receipt.wait(timeout=1.0)
        assert receipt.completed
        assert receipt.durable
    finally:
        release.set()
        worker.close(timeout=1.0)

    assert writes == [(prepared, None)]


def test_prepared_active_and_aborted_receipts_preserve_exact_cas_expectations_fifo() -> None:
    writes: list[tuple[ActivationPhase, ActivationPhase | None, str | None]] = []

    def persist(record, expected_phase):
        writes.append((record.phase, expected_phase, record.reason))

    worker = ModelPersistenceWorker(_Store(), _Logger(), persist_activation_phase=persist)
    prepared = _prepared_activation()
    active = prepared.transition(ActivationPhase.ACTIVE)
    interrupted = prepared.transition(ActivationPhase.ABORTED, reason="interrupted-activation")
    try:
        receipts = (
            worker.submit_activation_phase(prepared, expected_phase=None),
            worker.submit_activation_phase(active, expected_phase=ActivationPhase.PREPARED),
            worker.submit_activation_phase(interrupted, expected_phase=ActivationPhase.PREPARED),
        )
        assert all(receipt.wait(timeout=1.0) for receipt in receipts)
        assert all(receipt.durable for receipt in receipts)
    finally:
        worker.close(timeout=1.0)

    assert writes == [
        (ActivationPhase.PREPARED, None, None),
        (ActivationPhase.ACTIVE, ActivationPhase.PREPARED, None),
        (ActivationPhase.ABORTED, ActivationPhase.PREPARED, "interrupted-activation"),
    ]


def test_activation_confidence_and_phase_share_one_fifo_with_separate_durable_receipts() -> None:
    started = threading.Event()
    release = threading.Event()
    calls = []
    confidence = ModelEvidenceRecord(
        evidence_id="confidence-before-prepared",
        kind=EvidenceKind.CONFIDENCE_DECISION,
        session_id="session-activation",
        cook_id=None,
        timestamp_ms=900,
        role_generation=4,
        model_digest="b" * 64,
        provenance_digest="a" * 64,
        payload=ConfidenceDecisionEvidence(
            decision_id="decision-activation",
            blocked=False,
            reason="accepted",
        ),
    )
    prepared = _prepared_activation()

    def append(records):
        calls.append(("confidence-start", records[0].evidence_id))
        started.set()
        release.wait(timeout=1.0)
        calls.append(("confidence-durable", records[0].evidence_id))

    def persist(record, expected_phase):
        calls.append(("phase-durable", record.transaction_id, expected_phase))

    worker = ModelPersistenceWorker(
        _Store(),
        _Logger(),
        append_evidence=append,
        persist_activation_phase=persist,
    )
    try:
        confidence_receipt = worker.submit_activation_confidence(confidence)
        phase_receipt = worker.submit_activation_phase(prepared, expected_phase=None)
        assert started.wait(timeout=1.0)
        assert not confidence_receipt.durable
        assert not phase_receipt.durable
        release.set()
        assert confidence_receipt.wait(timeout=1.0)
        assert phase_receipt.wait(timeout=1.0)
    finally:
        release.set()
        worker.close(timeout=1.0)

    assert calls == [
        ("confidence-start", "confidence-before-prepared"),
        ("confidence-durable", "confidence-before-prepared"),
        ("phase-durable", prepared.transaction_id, None),
    ]


def test_activation_confidence_appends_preceding_assessment_in_one_durable_fifo_work() -> None:
    started = threading.Event()
    release = threading.Event()
    batches = []
    assessment = ModelEvidenceRecord(
        evidence_id="assessment-before-confidence",
        kind=EvidenceKind.CANDIDATE_ASSESSMENT,
        session_id="session-activation",
        cook_id=None,
        timestamp_ms=900,
        role_generation=4,
        model_digest="b" * 64,
        provenance_digest="a" * 64,
        payload=CandidateAssessmentEvidence(
            decision_id="decision-activation",
            origin=CandidateOrigin.PASSIVE_ONLINE.value,
            policy=ActivationPolicy.CAUSAL_AUTO.value,
            fit_accepted=True,
            identifiability_accepted=True,
            native_build="passed",
            native_dry_solve="passed",
            target_timing="passed",
            confidence_accepted=True,
            rejection_reasons=(),
        ),
    )
    confidence = ModelEvidenceRecord(
        evidence_id="confidence-after-assessment",
        kind=EvidenceKind.CONFIDENCE_DECISION,
        session_id="session-activation",
        cook_id=None,
        timestamp_ms=900,
        role_generation=4,
        model_digest="b" * 64,
        provenance_digest="a" * 64,
        payload=ConfidenceDecisionEvidence(
            decision_id="decision-activation",
            blocked=False,
            reason=None,
        ),
    )

    def append(records):
        batches.append(records)
        started.set()
        release.wait(timeout=1.0)

    worker = ModelPersistenceWorker(
        _Store(),
        _Logger(),
        append_evidence=append,
    )
    with pytest.raises(TypeError, match="ModelEvidenceRecord"):
        worker.submit_activation_confidence(
            confidence,
            preceding_evidence=(object(),),
        )
    with pytest.raises(ValueError, match="candidate-assessment"):
        worker.submit_activation_confidence(
            confidence,
            preceding_evidence=(_evidence("wrong-preceding-kind"),),
        )
    try:
        receipt = worker.submit_activation_confidence(
            confidence,
            preceding_evidence=(assessment,),
        )
        assert started.wait(timeout=1.0)
        assert not receipt.completed
        assert not receipt.durable
        release.set()
        assert receipt.wait(timeout=1.0)
    finally:
        release.set()
        worker.close(timeout=1.0)

    assert batches == [(assessment, confidence)]
    assert batches[0][0] is not assessment
    assert batches[0][1] is not confidence


def test_failed_phase_write_completes_receipt_without_durability_and_blocks_later_work() -> None:
    logger = _Logger()

    def fail(_record, _expected_phase):
        raise RuntimeError("disk full")

    worker = ModelPersistenceWorker(_Store(), logger, persist_activation_phase=fail)
    prepared = _prepared_activation()
    try:
        receipt = worker.submit_activation_phase(prepared, expected_phase=None)
        assert not receipt.wait(timeout=1.0)
        assert receipt.completed
        assert not receipt.durable
        assert receipt.error == "RuntimeError: disk full"
        rejected = worker.submit_activation_phase(
            prepared.transition(ActivationPhase.ACTIVE),
            expected_phase=ActivationPhase.PREPARED,
        )
        assert not rejected.accepted
        assert rejected.completed
        assert not rejected.durable
    finally:
        worker.close(timeout=1.0)

    assert worker.evidence_blocked
    assert any("Could not persist model activation-phase" in error for error in logger.errors)


def _confidence_for(record: PreparedActivationRecord, *, blocked: bool = False, timestamp_ms: int = 900):
    return ModelEvidenceRecord(
        evidence_id=f"confidence:{record.decision_id}:{timestamp_ms}",
        kind=EvidenceKind.CONFIDENCE_DECISION,
        session_id="session-grey",
        cook_id="cook-grey",
        timestamp_ms=timestamp_ms,
        role_generation=record.incumbent.role_generation,
        model_digest=record.candidate.model_digest,
        provenance_digest=record.incumbent.model_digest,
        payload=ConfidenceDecisionEvidence(
            decision_id=record.decision_id,
            blocked=blocked,
            reason="confidence-regressed" if blocked else None,
        ),
    )


def test_durable_phase_store_rechecks_confidence_then_cas_prepared_to_active(tmp_path) -> None:
    database_path = tmp_path / "activation.sqlite"
    prepared = _prepared_activation()
    append_model_evidence((_confidence_for(prepared),), database_path=database_path)

    commit_model_activation_phase(prepared, expected_phase=None, database_path=database_path)
    stored_prepared = read_model_activation(database_path=database_path)
    assert stored_prepared is not None
    assert stored_prepared.phase == "prepared"
    assert stored_prepared.transaction_id == prepared.transaction_id
    assert stored_prepared.incumbent_pair == prepared.incumbent
    assert stored_prepared.candidate_pair == prepared.candidate
    assert stored_prepared.rollback_pair == prepared.rollback
    assert stored_prepared.origin == CandidateOrigin.PASSIVE_ONLINE.value
    assert stored_prepared.policy == ActivationPolicy.CAUSAL_AUTO.value
    assert stored_prepared.evidence_decision_id == prepared.decision_id
    assert stored_prepared.controller_configuration_digest == prepared.candidate.ownership_digest
    assert stored_prepared.role_generation == prepared.incumbent.role_generation
    assert stored_prepared.candidate_generation == prepared.candidate.candidate_generation
    assert stored_prepared.candidate_digest == prepared.candidate.model_digest

    active = prepared.transition(ActivationPhase.ACTIVE)
    commit_model_activation_phase(
        active,
        expected_phase=ActivationPhase.PREPARED,
        database_path=database_path,
    )
    stored_active = read_model_activation(database_path=database_path)
    assert stored_active is not None
    assert stored_active.phase == "active"
    assert stored_active.active_pair == prepared.candidate
    assert stored_active.rollback_pair == prepared.incumbent
    assert stored_active.origin == CandidateOrigin.PASSIVE_ONLINE.value
    assert stored_active.policy == ActivationPolicy.CAUSAL_AUTO.value
    assert stored_active.evidence_decision_id == prepared.decision_id
    assert stored_active.controller_configuration_digest == prepared.candidate.ownership_digest
    assert stored_active.role_generation == prepared.candidate.role_generation
    assert stored_active.candidate_generation == prepared.candidate.candidate_generation
    assert stored_active.candidate_digest == prepared.candidate.model_digest
    with pytest.raises(ValueError, match="activation-state-changed"):
        commit_model_activation_phase(
            active,
            expected_phase=ActivationPhase.PREPARED,
            database_path=database_path,
        )


def test_active_pair_can_become_the_exact_incumbent_of_one_new_prepared_transaction(
    tmp_path,
) -> None:
    database_path = tmp_path / "successive-activation.sqlite"
    first = _prepared_activation()
    append_model_evidence((_confidence_for(first),), database_path=database_path)
    commit_model_activation_phase(first, expected_phase=None, database_path=database_path)
    commit_model_activation_phase(
        first.transition(ActivationPhase.ACTIVE),
        expected_phase=ActivationPhase.PREPARED,
        database_path=database_path,
    )
    next_candidate = _current_pair_descriptor(
        30.0,
        candidate_generation=5,
        role_generation=6,
    )
    second = PreparedActivationRecord.prepared(
        timestamp_ms=2_000,
        incumbent=first.candidate,
        candidate=next_candidate,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        policy=ActivationPolicy.CAUSAL_AUTO,
        decision_id="decision-grey-5",
    )
    append_model_evidence(
        (_confidence_for(second, timestamp_ms=1_900),),
        database_path=database_path,
    )

    commit_model_activation_phase(second, expected_phase=None, database_path=database_path)

    state = read_model_activation(database_path=database_path)
    assert state is not None
    assert state.phase == ActivationPhase.PREPARED.value
    assert state.incumbent_pair == first.candidate
    assert state.candidate_pair == next_candidate


def test_aborted_authority_can_prepare_one_new_exact_incumbent_transaction(tmp_path) -> None:
    database_path = tmp_path / "aborted-retry.sqlite"
    first = _prepared_activation()
    append_model_evidence((_confidence_for(first),), database_path=database_path)
    commit_model_activation_phase(first, expected_phase=None, database_path=database_path)
    commit_model_activation_phase(
        first.transition(ActivationPhase.ABORTED, reason="interrupted"),
        expected_phase=ActivationPhase.PREPARED,
        database_path=database_path,
    )
    next_candidate = _current_pair_descriptor(
        31.0,
        candidate_generation=6,
        role_generation=7,
    )
    second = PreparedActivationRecord.prepared(
        timestamp_ms=2_000,
        incumbent=first.incumbent,
        candidate=next_candidate,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        policy=ActivationPolicy.CAUSAL_AUTO,
        decision_id="decision-after-abort",
    )
    append_model_evidence(
        (_confidence_for(second, timestamp_ms=1_900),),
        database_path=database_path,
    )

    commit_model_activation_phase(second, expected_phase=None, database_path=database_path)

    state = read_model_activation(database_path=database_path)
    assert state is not None
    assert state.phase == ActivationPhase.PREPARED.value
    assert state.incumbent_pair == first.incumbent
    assert state.candidate_pair == next_candidate


def test_aborted_authority_rejects_new_prepared_with_different_incumbent(tmp_path) -> None:
    database_path = tmp_path / "aborted-mismatch.sqlite"
    first = _prepared_activation()
    append_model_evidence((_confidence_for(first),), database_path=database_path)
    commit_model_activation_phase(first, expected_phase=None, database_path=database_path)
    commit_model_activation_phase(
        first.transition(ActivationPhase.ABORTED, reason="interrupted"),
        expected_phase=ActivationPhase.PREPARED,
        database_path=database_path,
    )
    next_candidate = _current_pair_descriptor(
        32.0,
        candidate_generation=6,
        role_generation=7,
    )
    wrong = PreparedActivationRecord.prepared(
        timestamp_ms=2_000,
        incumbent=first.candidate,
        candidate=next_candidate,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        policy=ActivationPolicy.CAUSAL_AUTO,
        decision_id="decision-after-abort-wrong-owner",
    )
    append_model_evidence(
        (_confidence_for(wrong, timestamp_ms=1_900),),
        database_path=database_path,
    )

    with pytest.raises(ValueError, match="activation-state-changed"):
        commit_model_activation_phase(wrong, expected_phase=None, database_path=database_path)

    state = read_model_activation(database_path=database_path)
    assert state is not None
    assert state.phase == ActivationPhase.ABORTED.value
    assert state.active_pair == first.incumbent


def test_prepared_transaction_rechecks_the_latest_unblocked_confidence_atomically(tmp_path) -> None:
    database_path = tmp_path / "activation.sqlite"
    prepared = _prepared_activation()
    append_model_evidence(
        (
            _confidence_for(prepared, timestamp_ms=900),
            _confidence_for(prepared, blocked=True, timestamp_ms=901),
        ),
        database_path=database_path,
    )

    with pytest.raises(ValueError, match="activation-authority-changed"):
        commit_model_activation_phase(prepared, expected_phase=None, database_path=database_path)

    assert read_model_activation(database_path=database_path) is None


def test_confidence_regression_after_prepare_rejects_active_and_allows_exact_abort(
    tmp_path,
) -> None:
    database_path = tmp_path / "activation.sqlite"
    prepared = _prepared_activation()
    append_model_evidence((_confidence_for(prepared),), database_path=database_path)
    commit_model_activation_phase(prepared, expected_phase=None, database_path=database_path)
    append_model_evidence(
        (_confidence_for(prepared, blocked=True, timestamp_ms=901),),
        database_path=database_path,
    )

    with pytest.raises(ValueError, match="activation-authority-changed"):
        commit_model_activation_phase(
            prepared.transition(ActivationPhase.ACTIVE),
            expected_phase=ActivationPhase.PREPARED,
            database_path=database_path,
        )

    aborted = prepared.transition(
        ActivationPhase.ABORTED,
        reason="confidence-regressed",
    )
    commit_model_activation_phase(
        aborted,
        expected_phase=ActivationPhase.PREPARED,
        database_path=database_path,
    )
    state = read_model_activation(database_path=database_path)
    assert state is not None
    assert state.phase == "aborted"
    assert state.active_pair == prepared.incumbent
    assert state.reason == "confidence-regressed"


def test_aborted_cas_retains_incumbent_and_exact_interruption_reason(tmp_path) -> None:
    database_path = tmp_path / "activation.sqlite"
    prepared = _prepared_activation()
    append_model_evidence((_confidence_for(prepared),), database_path=database_path)
    commit_model_activation_phase(prepared, expected_phase=None, database_path=database_path)

    aborted = prepared.transition(ActivationPhase.ABORTED, reason="interrupted-activation")
    commit_model_activation_phase(
        aborted,
        expected_phase=ActivationPhase.PREPARED,
        database_path=database_path,
    )

    state = read_model_activation(database_path=database_path)
    assert state is not None
    assert state.phase == "aborted"
    assert state.active_pair == prepared.incumbent
    assert state.rollback_pair == prepared.incumbent
    assert state.reason == "interrupted-activation"


@pytest.mark.parametrize(
    ("durable_phase", "expected_phase", "expected_restore"),
    (
        (ActivationPhase.PREPARED, ActivationPhase.ABORTED, "incumbent"),
        (ActivationPhase.ACTIVE, ActivationPhase.ACTIVE, "candidate"),
        (ActivationPhase.ABORTED, ActivationPhase.ABORTED, "incumbent"),
    ),
)
def test_real_sqlite_restart_converges_at_every_durable_phase_boundary(
    tmp_path,
    durable_phase,
    expected_phase,
    expected_restore,
) -> None:
    database_path = tmp_path / f"restart-{durable_phase.value}.sqlite"
    prepared = _prepared_activation()
    append_model_evidence((_confidence_for(prepared),), database_path=database_path)
    commit_model_activation_phase(prepared, expected_phase=None, database_path=database_path)
    durable_record = prepared
    if durable_phase is ActivationPhase.ACTIVE:
        durable_record = prepared.transition(ActivationPhase.ACTIVE)
        commit_model_activation_phase(
            durable_record,
            expected_phase=ActivationPhase.PREPARED,
            database_path=database_path,
        )
    elif durable_phase is ActivationPhase.ABORTED:
        durable_record = prepared.transition(
            ActivationPhase.ABORTED,
            reason="swap-compensated",
        )
        commit_model_activation_phase(
            durable_record,
            expected_phase=ActivationPhase.PREPARED,
            database_path=database_path,
        )

    restarted_state = read_model_activation(database_path=database_path)
    assert restarted_state is not None
    assert restarted_state.phase == durable_phase.value
    assert restarted_state.transaction_id == prepared.transaction_id
    assert restarted_state.incumbent_pair == prepared.incumbent
    assert restarted_state.candidate_pair == prepared.candidate
    assert restarted_state.rollback_pair == prepared.rollback
    assert restarted_state.active_pair == (
        prepared.candidate if durable_phase is ActivationPhase.ACTIVE else prepared.incumbent
    )
    assert (
        json.loads(restarted_state.active_snapshot_json)
        == (prepared.candidate if durable_phase is ActivationPhase.ACTIVE else prepared.incumbent).to_dict()[
            "configuration"
        ]
    )
    assert json.loads(restarted_state.rollback_snapshot_json) == prepared.incumbent.to_dict()["configuration"]
    assert restarted_state.origin == CandidateOrigin.PASSIVE_ONLINE.value
    assert restarted_state.policy == ActivationPolicy.CAUSAL_AUTO.value
    assert restarted_state.evidence_decision_id == prepared.decision_id
    assert restarted_state.controller_configuration_digest == prepared.candidate.ownership_digest
    assert restarted_state.role_generation == (
        prepared.candidate.role_generation
        if durable_phase is ActivationPhase.ACTIVE
        else prepared.incumbent.role_generation
    )
    assert restarted_state.candidate_generation == prepared.candidate.candidate_generation
    assert restarted_state.candidate_digest == prepared.candidate.model_digest
    assert restarted_state.reason == durable_record.reason

    class _DurableReceipt:
        accepted = True
        durable = True

        @staticmethod
        def wait(_timeout=None):
            return True

    def persist_abort(record):
        commit_model_activation_phase(
            record,
            expected_phase=ActivationPhase.PREPARED,
            database_path=database_path,
        )
        return _DurableReceipt()

    recovery = recover_startup_activation(
        restarted_state,
        persist_aborted=persist_abort,
        receipt_timeout=0.1,
    )

    assert recovery.phase is expected_phase
    assert recovery.restore == getattr(prepared, expected_restore)
    assert recovery.rollback == prepared.rollback
    assert recovery.record.origin is CandidateOrigin.PASSIVE_ONLINE
    assert recovery.record.policy is ActivationPolicy.CAUSAL_AUTO
    assert recovery.record.decision_id == prepared.decision_id
    assert recovery.source_candidate_digest == prepared.candidate.model_digest
    assert recovery.record.phase is expected_phase
    converged = read_model_activation(database_path=database_path)
    assert converged is not None
    assert converged.phase == expected_phase.value
    assert converged.transaction_id == prepared.transaction_id
    assert converged.incumbent_pair == prepared.incumbent
    assert converged.candidate_pair == prepared.candidate
    assert converged.rollback_pair == prepared.rollback
    assert converged.active_pair == getattr(prepared, expected_restore)
    assert json.loads(converged.active_snapshot_json) == getattr(prepared, expected_restore).to_dict()["configuration"]
    assert json.loads(converged.rollback_snapshot_json) == prepared.incumbent.to_dict()["configuration"]
    assert converged.origin == CandidateOrigin.PASSIVE_ONLINE.value
    assert converged.policy == ActivationPolicy.CAUSAL_AUTO.value
    assert converged.evidence_decision_id == prepared.decision_id
    assert converged.controller_configuration_digest == prepared.candidate.ownership_digest
    assert converged.role_generation == getattr(prepared, expected_restore).role_generation
    assert converged.candidate_generation == prepared.candidate.candidate_generation
    assert converged.candidate_digest == prepared.candidate.model_digest
    assert converged.reason == (
        "interrupted-activation" if durable_phase is ActivationPhase.PREPARED else durable_record.reason
    )
