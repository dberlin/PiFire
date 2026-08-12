"""Behavioral contracts for off-path model persistence."""

import json
import threading

import pytest

from common.controller_model_state import (
    MAX_SNAPSHOT_BYTES,
    CheckpointSaveOutcome,
    ControllerModelStore,
)
from common.persistence.model_evidence import (
    append_model_evidence,
    commit_model_activation_phase,
    read_model_activation,
)
from common.control_trace import AmbientSource
from common.model_evidence import (
    ActivationEvidence,
    ConfidenceDecisionEvidence,
    EvidenceKind,
    ForecastOriginEvidence,
    ModelEvidenceRecord,
    RefreshDiagnosticsEvidence,
)
from controller.model_learning.activation import (
    ActivationPhase,
    GreyControlPairDescriptor,
    PreparedActivationRecord,
    canonical_snapshot_digest,
    recover_startup_activation,
)
from controller.model_learning.contracts import ActivationPolicy, CandidateOrigin
from controller.runtime.model_persistence import (
    DurableActivationReceipt,
    EvidenceSubmission,
    ModelPersistenceWorker,
)

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


def test_checkpoint_submission_coalesces_latest_owned_snapshot_without_blocking():
    store = _Store()
    worker = ModelPersistenceWorker(store, _Logger())
    try:
        assert worker.submit_checkpoint("mpc", {"revision": 1, "parameters": {"gain": 1}})
        assert store.first_save_started.wait(timeout=1.0)
        assert worker.submit_checkpoint("mpc", {"revision": 2, "parameters": {"gain": 2}})
        store.release_first_save.set()
        assert worker.flush_and_stop(timeout=1.0)
    finally:
        store.release_first_save.set()
        worker.flush_and_stop(timeout=1.0)

    assert [snapshot["revision"] for _, snapshot in store.saved] == [1, 2]


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
        assert worker.flush_and_stop(timeout=1.0)
    finally:
        store.release_first_save.set()
        worker.flush_and_stop(timeout=1.0)

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
        assert worker.flush_and_stop(timeout=1.0)
    finally:
        store.release_first_save.set()
        worker.flush_and_stop(timeout=1.0)

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
        assert worker.flush_and_stop(timeout=1.0)
    finally:
        store.release_first_save.set()
        worker.flush_and_stop(timeout=1.0)

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
        worker.flush_and_stop(timeout=1.0)


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
        assert worker.flush_and_stop(timeout=1.0)
    finally:
        store.release_first_save.set()
        worker.flush_and_stop(timeout=1.0)


def test_checkpoint_rejects_malformed_nonfinite_and_oversized_snapshots_synchronously():
    logger = _Logger()
    worker = ModelPersistenceWorker(_Store(), logger)
    try:
        assert not worker.submit_checkpoint("mpc", {"revision": -1})
        assert not worker.submit_checkpoint("mpc", {"revision": 1, "gain": float("nan")})
        assert not worker.submit_checkpoint("mpc", {"revision": 1, "blob": "x" * 65_537})
        assert logger.errors
    finally:
        worker.flush_and_stop(timeout=1.0)


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
        assert worker.flush_and_stop(timeout=1.0)
        assert store.save_outcome("mpc", {"revision": 1}) is CheckpointSaveOutcome.NONADVANCING
        assert not worker.evidence_blocked
    finally:
        worker.flush_and_stop(timeout=1.0)


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
        assert worker.flush_and_stop(timeout=1.0)
        assert worker.evidence_blocked
        assert not worker.submit_checkpoint("mpc", {"revision": 2})
        assert not worker.submit_evidence(_evidence("later")).accepted
        assert not worker.commit_activation(_activation("later"))
    finally:
        worker.flush_and_stop(timeout=1.0)


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
        assert worker.flush_and_stop(timeout=1.0)
        assert store.save_calls == 0
        assert worker.evidence_blocked
    finally:
        worker.flush_and_stop(timeout=1.0)


def test_explicit_failed_checkpoint_outcome_blocks_later_submissions():
    class FailedStore:
        def save_outcome(self, _name, _snapshot):
            return CheckpointSaveOutcome.FAILED

    worker = ModelPersistenceWorker(FailedStore(), _Logger())
    try:
        assert worker.submit_checkpoint("mpc", {"revision": 1})
        assert worker.flush_and_stop(timeout=1.0)
        assert worker.evidence_blocked
        assert not worker.submit_checkpoint("mpc", {"revision": 2})
        assert not worker.submit_evidence(_evidence("later")).accepted
        assert not worker.commit_activation(_activation("later"))
    finally:
        worker.flush_and_stop(timeout=1.0)


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
        assert worker.flush_and_stop(timeout=1.0)
    finally:
        store.release_first_save.set()
        worker.flush_and_stop(timeout=1.0)

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
        assert worker.flush_and_stop(timeout=1.0)
    finally:
        release.set()
        worker.flush_and_stop(timeout=1.0)

    assert written[0][0] == batch[0]
    assert written[0][2] == batch[2]
    assert written[0][1].payload.atomic_persistence is True


def _pair_descriptor(
    configuration: dict[str, object], *, candidate_generation: int, role_generation: int
) -> GreyControlPairDescriptor:
    return GreyControlPairDescriptor(
        model_digest=canonical_snapshot_digest(configuration),
        configuration=configuration,
        estimator_kind="ekf",
        solver_kind="acados-grey",
        candidate_generation=candidate_generation,
        role_generation=role_generation,
    )


def _prepared_activation() -> PreparedActivationRecord:
    incumbent = _pair_descriptor(
        {"schema": "pifire-grey-box-model/v4", "theta": 50.0},
        candidate_generation=3,
        role_generation=4,
    )
    candidate = _pair_descriptor(
        {"schema": "pifire-grey-box-model/v4", "theta": 40.0},
        candidate_generation=4,
        role_generation=5,
    )
    return PreparedActivationRecord.prepared(
        timestamp_ms=1_000,
        incumbent=incumbent,
        candidate=candidate,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        policy=ActivationPolicy.PASSIVE_AUTO,
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
        worker.flush_and_stop(timeout=1.0)

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
        worker.flush_and_stop(timeout=1.0)

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
        worker.flush_and_stop(timeout=1.0)

    assert calls == [
        ("confidence-start", "confidence-before-prepared"),
        ("confidence-durable", "confidence-before-prepared"),
        ("phase-durable", prepared.transaction_id, None),
    ]


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
        worker.flush_and_stop(timeout=1.0)

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
    next_candidate = _pair_descriptor(
        {"schema": "pifire-grey-box-model/v4", "theta": 30.0},
        candidate_generation=5,
        role_generation=6,
    )
    second = PreparedActivationRecord.prepared(
        timestamp_ms=2_000,
        incumbent=first.candidate,
        candidate=next_candidate,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        policy=ActivationPolicy.PASSIVE_AUTO,
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
    next_candidate = _pair_descriptor(
        {"schema": "pifire-grey-box-model/v4", "theta": 31.0},
        candidate_generation=6,
        role_generation=7,
    )
    second = PreparedActivationRecord.prepared(
        timestamp_ms=2_000,
        incumbent=first.incumbent,
        candidate=next_candidate,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        policy=ActivationPolicy.PASSIVE_AUTO,
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
    next_candidate = _pair_descriptor(
        {"schema": "pifire-grey-box-model/v4", "theta": 32.0},
        candidate_generation=6,
        role_generation=7,
    )
    wrong = PreparedActivationRecord.prepared(
        timestamp_ms=2_000,
        incumbent=first.candidate,
        candidate=next_candidate,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        policy=ActivationPolicy.PASSIVE_AUTO,
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
    if durable_phase is ActivationPhase.ACTIVE:
        commit_model_activation_phase(
            prepared.transition(ActivationPhase.ACTIVE),
            expected_phase=ActivationPhase.PREPARED,
            database_path=database_path,
        )
    elif durable_phase is ActivationPhase.ABORTED:
        commit_model_activation_phase(
            prepared.transition(
                ActivationPhase.ABORTED,
                reason="swap-compensated",
            ),
            expected_phase=ActivationPhase.PREPARED,
            database_path=database_path,
        )

    restarted_state = read_model_activation(database_path=database_path)
    assert restarted_state is not None

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
    converged = read_model_activation(database_path=database_path)
    assert converged is not None
    assert converged.phase == expected_phase.value
