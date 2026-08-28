"""Direct durable activation state-machine contracts."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from math import ceil
from types import SimpleNamespace

import pytest

from common.model_evidence import (
    CandidateAssessmentEvidence,
    ConfidenceDecisionEvidence,
    EvidenceKind,
    FallbackEvidence,
    ModelEvidenceRecord,
)
from controller.acados import GreyBoxMPCConfig
from controller.applied_output import AppliedOutput, OutputSource
from controller.model_learning.activation import (
    ActivationPhase,
    GreyControlPairDescriptor,
    PreparedActivationRecord,
    canonical_snapshot_digest,
)
from controller.model_learning.activation_runtime import ActivationRuntime
from controller.model_learning.contracts import ActivationPolicy, CandidateOrigin
from controller.mpc_config import DEFAULT_MPC_CONFIG
from controller.mpc_core import MpcCore
from controller.mpc_factory import MpcPairFactory, OwnedMpcPair
from controller.mpc_model import EstimatorSeed
from controller.runtime.model_persistence import (
    DurableActivationReceipt,
    EvidenceSubmission,
    ModelPersistenceWorker,
)
from tests.unit.mpc._solver_fixtures import (
    CYCLE,
    _config,
    _Estimator,
    _Solver,
    inactive_calibration,
)
from tests.unit.runtime._persistence_helpers import _pair_phase_state


@dataclass(slots=True)
class _PhaseSubmission:
    record: PreparedActivationRecord
    expected: ActivationPhase | None
    receipt: DurableActivationReceipt


class _Persistence(ModelPersistenceWorker):
    def __init__(self) -> None:
        self.phase_submissions: list[_PhaseSubmission] = []
        self.confidence_records = []
        self.confidence_preceding = []
        self.evidence_records = []
        self.close_count = 0
        self.events: list[str] = []
        self.phase_error: BaseException | None = None
        self.next_phase_error: BaseException | None = None
        self.reject_next_phase = False
        self.reject_evidence = False
        self.raise_evidence: BaseException | None = None
        self.complete_phase_immediately = False
        self.complete_phase_durable = True

    def submit_activation_phase(
        self,
        record: PreparedActivationRecord,
        *,
        expected_phase: ActivationPhase | None,
    ) -> DurableActivationReceipt:
        if self.phase_error is not None:
            raise self.phase_error
        if self.next_phase_error is not None:
            error = self.next_phase_error
            self.next_phase_error = None
            raise error
        if self.reject_next_phase:
            self.reject_next_phase = False
            return DurableActivationReceipt(accepted=False)
        receipt = DurableActivationReceipt(accepted=True)
        self.phase_submissions.append(_PhaseSubmission(record, expected_phase, receipt))
        self.events.append(f"phase:{record.phase.value}")
        if self.complete_phase_immediately:
            receipt._complete(durable=self.complete_phase_durable)
        return receipt

    def submit_activation_confidence(self, record, *, preceding_evidence=()):
        self.confidence_records.append(record)
        self.confidence_preceding.append(preceding_evidence)
        return DurableActivationReceipt(accepted=True)

    def submit_evidence(self, record):
        if self.raise_evidence is not None:
            raise self.raise_evidence
        self.evidence_records.append(record)
        return EvidenceSubmission(accepted=not self.reject_evidence)

    def close(self, timeout: float = 2.0) -> bool:
        self.events.append("close")
        self.close_count += 1
        return True


def _descriptor(theta: float, *, candidate_generation: int, role_generation: int) -> GreyControlPairDescriptor:
    configuration = {
        "schema": "pifire-grey-box-model/v4",
        "n_delay": 8,
        "parameters": {
            "C_c": 320.0,
            "K_Q": 350.0,
            "theta": theta,
            "h_amb": 0.5,
            "T_amb": 20.0,
            "sigma": 1.4e-9,
        },
    }
    return GreyControlPairDescriptor(
        model_digest=canonical_snapshot_digest(configuration),
        configuration=configuration,
        estimator_kind="ekf",
        solver_kind="acados-grey",
        candidate_generation=candidate_generation,
        role_generation=role_generation,
    )


def _activation_seed(
    descriptor: GreyControlPairDescriptor,
    *,
    status: str = "exact",
    delay_states: tuple[float, ...] | None = None,
) -> EstimatorSeed:
    theta = float(descriptor.configuration["parameters"]["theta"])
    required = ceil(3 * theta / 20.0)
    if delay_states is None:
        base = theta / 1_000.0
        delay_states = tuple(base + index / 100.0 for index in range(8))
    frame_count = required if status == "exact" else max(0, required - 1)
    if status in {"absent", "uncertain"}:
        delay_states = ()
        frame_count = 0
    return EstimatorSeed(
        delay_states=delay_states,
        chamber_temperature_c=110.0,
        disturbance=0.0,
        segment_id="segment-activation",
        pre_roll_digest=sha256(
            f"segment-activation:{descriptor.model_digest}:{status}".encode()
        ).hexdigest(),
        pre_roll_frame_count=frame_count,
        required_frame_count=required,
        status=status,
    )


def _pair(descriptor: GreyControlPairDescriptor) -> OwnedMpcPair:
    native = GreyBoxMPCConfig()
    theta = float(descriptor.configuration["parameters"]["theta"])
    n_delay = int(descriptor.configuration["n_delay"])
    core = MpcCore(
        _config(theta=theta, n_delay=n_delay),
        "C",
        dict(CYCLE),
        components=(_Estimator(), _Solver(native)),
    )
    core.seed_from_trajectory(_activation_seed(descriptor))
    return OwnedMpcPair(core, descriptor)


def _factory() -> MpcPairFactory:
    return MpcPairFactory(
        DEFAULT_MPC_CONFIG,
        "C",
        dict(CYCLE),
        advance_calibration=inactive_calibration,
        model_authority=lambda: (4, None),
        on_policy_failure=lambda _error: None,
    )


def _runtime(
    *,
    receipt_timeout: float = 2.0,
    owns_persistence: bool = False,
) -> tuple[
    ActivationRuntime,
    OwnedMpcPair,
    OwnedMpcPair,
    PreparedActivationRecord,
    _Persistence,
]:
    incumbent = _pair(_descriptor(50.0, candidate_generation=3, role_generation=4))
    candidate = _pair(_descriptor(40.0, candidate_generation=4, role_generation=5))
    incumbent.authorize_output()
    prepared = PreparedActivationRecord.prepared(
        timestamp_ms=1_000,
        incumbent=incumbent.descriptor,
        candidate=candidate.descriptor,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        policy=ActivationPolicy.PASSIVE_AUTO,
        decision_id="decision-runtime",
    )
    factory = _factory()
    persistence = _Persistence()
    runtime = ActivationRuntime(
        factory,
        incumbent,
        persistence,
        owns_persistence=owns_persistence,
        clock_ms=lambda: 2_000,
        receipt_timeout=receipt_timeout,
    )
    return runtime, incumbent, candidate, prepared, persistence


@pytest.mark.parametrize(
    ("phase_durable", "expected_active"),
    (
        pytest.param(True, "candidate", id="active-phase-durable-authorizes-candidate"),
        pytest.param(False, "incumbent", id="active-phase-failure-compensates"),
    ),
)
def test_prepared_activation_state_machine_matrix(
    phase_durable: bool,
    expected_active: str,
) -> None:
    runtime, incumbent, candidate, prepared, persistence = _runtime()
    durable_prepared = DurableActivationReceipt(accepted=True)
    durable_prepared._complete(durable=True)

    assert runtime.queue_prepared_activation(prepared, candidate, durable_prepared)
    assert runtime.advance_activation() is False
    assert runtime.active_pair is candidate
    assert runtime.rollback_pair is incumbent
    assert not candidate.authorized
    assert not incumbent.authorized
    active_submission = persistence.phase_submissions[-1]
    assert active_submission.record.phase is ActivationPhase.ACTIVE
    assert active_submission.expected is ActivationPhase.PREPARED
    active_submission.receipt._complete(
        durable=phase_durable,
        error=None if phase_durable else RuntimeError("phase-write-failed"),
    )

    advanced = runtime.advance_activation()
    if not phase_durable:
        aborted_submission = persistence.phase_submissions[-1]
        assert aborted_submission.record.phase is ActivationPhase.ABORTED
        assert aborted_submission.expected is ActivationPhase.PREPARED
        aborted_submission.receipt._complete(durable=True)
        advanced = runtime.advance_activation()

    assert advanced is True
    assert runtime.active_pair is (candidate if expected_active == "candidate" else incumbent)
    assert runtime.active_pair.authorized
    runtime.close()


def test_prepared_receipt_and_duplicate_transaction_are_fenced_before_install() -> None:
    runtime, incumbent, candidate, prepared, persistence = _runtime()
    pending = DurableActivationReceipt(accepted=True)

    assert runtime.queue_prepared_activation(prepared, candidate, pending) is False
    assert runtime.active_pair is incumbent
    assert not persistence.phase_submissions

    pending._complete(durable=True)
    assert runtime.queue_prepared_activation(prepared, candidate, pending)
    assert runtime.queue_prepared_activation(prepared, candidate, pending)
    assert runtime.advance_activation() is False
    assert len(persistence.phase_submissions) == 1
    runtime.close()


def _durable(*, durable: bool = True, error: BaseException | None = None):
    receipt = DurableActivationReceipt(accepted=True)
    receipt._complete(durable=durable, error=error)
    return receipt


def _confidence(evidence_id: str = "confidence-runtime") -> ModelEvidenceRecord:
    return ModelEvidenceRecord(
        evidence_id=evidence_id,
        kind=EvidenceKind.CONFIDENCE_DECISION,
        session_id="runtime",
        cook_id=None,
        timestamp_ms=1_000,
        role_generation=5,
        model_digest="a" * 64,
        provenance_digest=None,
        payload=ConfidenceDecisionEvidence(
            decision_id="decision-runtime",
            blocked=False,
            reason=None,
        ),
    )


def _assessment(evidence_id: str = "assessment-runtime") -> ModelEvidenceRecord:
    return ModelEvidenceRecord(
        evidence_id=evidence_id,
        kind=EvidenceKind.CANDIDATE_ASSESSMENT,
        session_id="runtime",
        cook_id=None,
        timestamp_ms=1_000,
        role_generation=5,
        model_digest="a" * 64,
        provenance_digest=None,
        payload=CandidateAssessmentEvidence(
            decision_id="decision-runtime",
            origin="passive-online",
            policy="passive-auto",
            fit_accepted=True,
            identifiability_accepted=True,
            native_build="passed",
            native_dry_solve="passed",
            target_timing="passed",
            confidence_accepted=True,
            rejection_reasons=(),
        ),
    )


def _activate(runtime, candidate, prepared, persistence) -> None:
    assert runtime.queue_prepared_activation(prepared, candidate, _durable())
    assert runtime.advance_activation() is False
    persistence.phase_submissions[-1].receipt._complete(durable=True)
    assert runtime.advance_activation() is True


def test_confidence_fifo_copy_receipt_and_event_copy_ownership() -> None:
    runtime, _incumbent, _candidate, prepared, persistence = _runtime()
    confidence = _confidence()
    assessment = _assessment()
    first = runtime.submit_activation_confidence(
        confidence,
        preceding_evidence=(assessment,),
    )
    assert runtime.submit_activation_confidence(confidence) is first
    assert persistence.confidence_records == [confidence]
    assert persistence.confidence_records[0] is not confidence
    assert persistence.confidence_preceding == [(assessment,)]
    assert persistence.confidence_preceding[0][0] is not assessment
    assert runtime.submit_evidence(confidence)
    assert persistence.evidence_records[-1] == confidence
    assert persistence.evidence_records[-1] is not confidence
    assert runtime.submit_prepared_phase(prepared) is persistence.phase_submissions[-1].receipt
    runtime.mark_confidence_persisted(prepared.decision_id)
    assert runtime.confidence_persisted(prepared.decision_id)
    assert runtime.consume_confidence_persisted(prepared.decision_id)
    assert not runtime.consume_confidence_persisted(prepared.decision_id)
    with pytest.raises(TypeError):
        runtime.submit_activation_confidence(SimpleNamespace())
    wrong_kind = ModelEvidenceRecord(
        evidence_id="fallback-runtime",
        kind=EvidenceKind.FALLBACK,
        session_id="runtime",
        cook_id=None,
        timestamp_ms=1_000,
        role_generation=5,
        model_digest="a" * 64,
        provenance_digest=None,
        payload=FallbackEvidence(
            decision_id="decision-runtime",
            reason="wrong-kind",
            failed_digest="a" * 64,
            failed_generation=5,
            last_safe_command=0.0,
            fallback_kind="grey-box",
        ),
    )
    with pytest.raises(TypeError):
        runtime.submit_activation_confidence(wrong_kind)
    runtime.close()
    with pytest.raises(TypeError, match="ModelEvidenceRecord"):
        runtime.submit_activation_confidence(
            _confidence("confidence-invalid-preceding-type"),
            preceding_evidence=(SimpleNamespace(),),
        )
    with pytest.raises(ValueError, match="candidate-assessment"):
        runtime.submit_activation_confidence(
            _confidence("confidence-invalid-preceding-kind"),
            preceding_evidence=(wrong_kind,),
        )
    assert not runtime.submit_activation_confidence(_confidence("closed-confidence")).accepted
    assert not runtime.submit_evidence(confidence)


@pytest.mark.parametrize("failure", ("raise-active", "reject-active", "cas-changed"))
def test_active_persistence_failures_restore_incumbent_and_durably_abort(failure) -> None:
    runtime, incumbent, candidate, prepared, persistence = _runtime()
    assert runtime.queue_prepared_activation(prepared, candidate, _durable())
    if failure == "raise-active":
        persistence.next_phase_error = RuntimeError("phase failed")
    elif failure == "reject-active":
        persistence.reject_next_phase = True
    assert runtime.advance_activation() is False
    if failure == "cas-changed":
        persistence.phase_submissions[-1].receipt._complete(
            durable=False,
            error=RuntimeError("activation-authority-changed"),
        )
        assert runtime.advance_activation() is False
    aborted = persistence.phase_submissions[-1]
    assert aborted.record.phase is ActivationPhase.ABORTED
    assert aborted.record.reason == (
        "activation-confidence-changed" if failure == "cas-changed" else "active-persistence-failed"
    )
    aborted.receipt._complete(durable=True)
    assert runtime.advance_activation()
    assert runtime.active_pair is incumbent
    assert incumbent.authorized
    assert candidate.closed
    runtime.close()


def test_abort_receipt_failure_terminalizes_and_fences_output() -> None:
    runtime, _incumbent, candidate, prepared, persistence = _runtime()
    assert runtime.queue_prepared_activation(prepared, candidate, _durable())
    candidate.close()
    assert runtime.advance_activation() is False
    aborted = persistence.phase_submissions[-1]
    assert aborted.record.phase is ActivationPhase.ABORTED
    aborted.receipt._complete(durable=False, error=RuntimeError("disk failed"))
    assert runtime.advance_activation() is False
    assert runtime.activation_terminated
    assert runtime.terminated_reason == "activation-abort-persistence-failed"
    assert not runtime.output_authorized
    runtime.close()


def test_runtime_failure_and_explicit_rollback_restore_exact_incumbent() -> None:
    runtime, incumbent, candidate, prepared, persistence = _runtime()
    _activate(runtime, candidate, prepared, persistence)
    assert runtime.activation_runtime_failure("solve failed")
    assert runtime.active_pair is incumbent
    assert candidate.closed
    assert prepared.candidate.role_generation in runtime.failed_role_generations
    events = runtime.drain_activation_events()
    assert len(events) == 1 and events[0].kind is EvidenceKind.FALLBACK
    assert events[0].payload.reason == "solve failed"
    assert runtime.drain_activation_events() == ()
    runtime.close()

    runtime, incumbent, candidate, prepared, persistence = _runtime()
    _activate(runtime, candidate, prepared, persistence)
    assert runtime.rollback_activation("operator")
    assert runtime.active_pair is incumbent
    assert candidate.closed
    assert runtime.drain_activation_events() == ()
    runtime.close()


def test_stale_generation_digest_and_descriptor_mismatches_are_rejected() -> None:
    runtime, incumbent, candidate, prepared, _persistence = _runtime()
    stale_pair = _pair(_descriptor(45.0, candidate_generation=5, role_generation=4))
    stale = PreparedActivationRecord.prepared(
        timestamp_ms=1_001,
        incumbent=incumbent.descriptor,
        candidate=stale_pair.descriptor,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        policy=ActivationPolicy.PASSIVE_AUTO,
        decision_id="stale",
    )
    assert not runtime.queue_prepared_activation(stale, stale_pair, _durable())
    stale_pair.close()
    mismatch = _pair(_descriptor(44.0, candidate_generation=6, role_generation=6))
    assert not runtime.queue_prepared_activation(prepared, mismatch, _durable())
    mismatch.close()
    wrong_incumbent = PreparedActivationRecord.prepared(
        timestamp_ms=1_002,
        incumbent=mismatch.descriptor,
        candidate=candidate.descriptor,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        policy=ActivationPolicy.PASSIVE_AUTO,
        decision_id="wrong-incumbent",
    )
    assert not runtime.queue_prepared_activation(wrong_incumbent, candidate, _durable())
    runtime.close()


@pytest.mark.parametrize(
    "phase",
    (ActivationPhase.PREPARED, ActivationPhase.ACTIVE, ActivationPhase.ABORTED),
)
def test_startup_restore_matrix_is_idempotent_and_closes_retired_owner(phase) -> None:
    state, _record = _pair_phase_state(phase)
    runtime, incumbent, candidate, _prepared, persistence = _runtime()
    persistence.complete_phase_immediately = True
    candidate.close()
    assert runtime.restore_activation(state, ())
    restored = runtime.active_pair
    assert restored is not incumbent
    assert incumbent.closed
    assert runtime.restore_activation(state, ())
    assert runtime.active_pair is restored
    assert (runtime.rollback_pair is not None) is (phase is ActivationPhase.ACTIVE)
    assert (runtime.active_record is not None) is (phase is ActivationPhase.ACTIVE)
    runtime.close()


def test_partial_restore_failure_keeps_incumbent_usable_and_closes_partial_pair(
    monkeypatch,
) -> None:
    state, _record = _pair_phase_state(ActivationPhase.ACTIVE)
    runtime, incumbent, candidate, _prepared, _persistence = _runtime()
    candidate.close()
    original_restore = MpcPairFactory.restore
    built = []

    def fail_second(factory, descriptor):
        if built:
            raise RuntimeError("rollback unavailable")
        pair = original_restore(factory, descriptor)
        built.append(pair)
        return pair

    monkeypatch.setattr(MpcPairFactory, "restore", fail_second)
    assert not runtime.restore_activation(state, ())
    assert built[0].closed
    assert runtime.active_pair is incumbent
    assert runtime.output_authorized
    runtime.close()


def test_pending_candidate_and_every_retained_pair_close_exactly_once() -> None:
    runtime, incumbent, candidate, prepared, persistence = _runtime()
    assert runtime.queue_prepared_activation(prepared, candidate, _durable())
    runtime.close()
    runtime.close()
    assert incumbent.closed
    assert candidate.closed
    assert persistence.close_count == 0

    runtime, incumbent, candidate, prepared, persistence = _runtime()
    _activate(runtime, candidate, prepared, persistence)
    runtime.close()
    assert incumbent.closed
    assert candidate.closed
    assert persistence.close_count == 0


def test_owned_activation_runtime_closes_persistence_once_on_normal_close() -> None:
    runtime, _incumbent, _candidate, _prepared, persistence = _runtime(
        owns_persistence=True
    )

    runtime.close()
    runtime.close()

    assert persistence.close_count == 1
    assert persistence.events == ["close"]


def test_owned_activation_close_false_retries_persistence_without_reclosing_pairs(
    monkeypatch,
) -> None:
    runtime, incumbent, _candidate, _prepared, persistence = _runtime(
        owns_persistence=True
    )
    outcomes = iter((False, True))

    def close_with_retry(*, timeout=2.0):
        del timeout
        persistence.events.append("close")
        persistence.close_count += 1
        return next(outcomes)

    persistence.close = close_with_retry
    pair_closes = []
    original_close = OwnedMpcPair.close

    def count_close(pair):
        if pair is incumbent:
            pair_closes.append(pair)
        return original_close(pair)

    monkeypatch.setattr(OwnedMpcPair, "close", count_close)

    with pytest.raises(RuntimeError, match="complete activation runtime ownership"):
        runtime.close()
    assert incumbent.closed
    runtime.close()

    assert persistence.close_count == 2
    assert persistence.events == ["close", "close"]
    assert pair_closes == [incumbent]


def test_injected_activation_runtime_never_closes_persistence_worker() -> None:
    runtime, _incumbent, _candidate, _prepared, persistence = _runtime()

    runtime.close()
    runtime.close()

    assert persistence.close_count == 0
    assert persistence.events == []


@pytest.mark.parametrize("first_failure", ("exception", "rejected", "non-durable"))
def test_pending_abort_retries_automatically_on_later_lifecycle_advancement(
    first_failure,
) -> None:
    runtime, incumbent, candidate, prepared, persistence = _runtime()
    assert runtime.queue_prepared_activation(prepared, candidate, _durable())
    if first_failure == "exception":
        persistence.next_phase_error = RuntimeError("abort unavailable")
    elif first_failure == "rejected":
        persistence.reject_next_phase = True
    else:
        persistence.complete_phase_immediately = True
        persistence.complete_phase_durable = False

    assert not runtime.abort_prepared_activation(
        prepared,
        "learning-lifecycle-persistence-failed",
    )
    assert runtime.pending_abort_count == 1
    assert candidate.closed
    assert runtime.active_pair is incumbent
    assert incumbent.authorized

    persistence.complete_phase_immediately = True
    persistence.complete_phase_durable = True
    assert runtime.advance_activation()
    assert runtime.pending_abort_count == 0
    assert persistence.phase_submissions[-1].record.phase is ActivationPhase.ABORTED
    assert persistence.phase_submissions[-1].expected is ActivationPhase.PREPARED
    runtime.close()


def test_pending_abort_reuses_accepted_receipt_until_it_becomes_durable() -> None:
    runtime, _incumbent, candidate, prepared, persistence = _runtime(receipt_timeout=0.0)
    assert runtime.queue_prepared_activation(prepared, candidate, _durable())

    assert not runtime.abort_prepared_activation(prepared, "lifecycle-failed")
    assert runtime.pending_abort_count == 1
    assert len(persistence.phase_submissions) == 1
    assert not runtime.abort_prepared_activation(prepared, "lifecycle-failed")
    assert len(persistence.phase_submissions) == 1
    receipt = persistence.phase_submissions[0].receipt

    receipt._complete(durable=True)
    assert runtime.advance_activation()
    assert runtime.pending_abort_count == 0
    assert len(persistence.phase_submissions) == 1
    runtime.close()


def test_activation_advancement_never_waits_on_an_incomplete_abort_receipt(
    monkeypatch,
) -> None:
    waits = []
    original_wait = DurableActivationReceipt.wait

    def count_wait(receipt, timeout=None):
        waits.append(receipt)
        return original_wait(receipt, timeout)

    monkeypatch.setattr(DurableActivationReceipt, "wait", count_wait)
    runtime, _incumbent, _candidate, prepared, persistence = _runtime(receipt_timeout=0.0)
    assert runtime.queue_prepared_activation(prepared, _candidate, _durable())
    assert not runtime.abort_prepared_activation(prepared, "lifecycle-failed")
    assert len(waits) == 1

    assert not runtime.advance_activation()
    assert len(waits) == 1
    persistence.phase_submissions[0].receipt._complete(durable=True)
    assert runtime.advance_activation()
    runtime.close()


def test_abort_is_type_safe_transaction_exact_and_idempotent() -> None:
    runtime, _incumbent, candidate, prepared, persistence = _runtime(receipt_timeout=0.0)
    with pytest.raises(TypeError, match="PreparedActivationRecord"):
        runtime.abort_prepared_activation(SimpleNamespace(), "lifecycle-failed")
    assert not runtime.abort_prepared_activation(prepared, "lifecycle-failed")

    assert runtime.queue_prepared_activation(prepared, candidate, _durable())
    persistence.complete_phase_immediately = True
    assert runtime.abort_prepared_activation(prepared, "lifecycle-failed")
    assert runtime.abort_prepared_activation(prepared, "lifecycle-failed")
    assert not runtime.abort_prepared_activation(prepared, "different-reason")
    runtime.close()


def test_closed_runtime_rejects_every_activation_advancement_boundary() -> None:
    runtime, _incumbent, candidate, prepared, _persistence = _runtime()
    assert runtime.retry_pending_aborts()
    runtime.close()

    assert not runtime.submit_prepared_phase(prepared).accepted
    assert not runtime.queue_prepared_activation(prepared, candidate, _durable())
    assert not runtime.advance_activation()
    assert runtime.retry_pending_aborts()
    candidate.close()


def test_owned_close_retries_pending_abort_before_persistence_close() -> None:
    runtime, _incumbent, candidate, prepared, persistence = _runtime(
        owns_persistence=True
    )
    assert runtime.queue_prepared_activation(prepared, candidate, _durable())
    persistence.next_phase_error = RuntimeError("abort unavailable")
    assert not runtime.abort_prepared_activation(prepared, "lifecycle-failed")
    persistence.complete_phase_immediately = True
    persistence.complete_phase_durable = True

    runtime.close()

    assert candidate.closed
    assert runtime.pending_abort_count == 0
    assert persistence.events[-2:] == ["phase:aborted", "close"]


def test_close_reports_permanently_unresolved_abort_after_all_owner_cleanup(
    monkeypatch,
) -> None:
    runtime, incumbent, candidate, prepared, persistence = _runtime(
        owns_persistence=True
    )
    close_calls = []
    original_close = OwnedMpcPair.close

    def count_close(pair):
        if pair is candidate:
            close_calls.append(pair)
        return original_close(pair)

    monkeypatch.setattr(OwnedMpcPair, "close", count_close)
    assert runtime.queue_prepared_activation(prepared, candidate, _durable())
    persistence.reject_next_phase = True
    assert not runtime.abort_prepared_activation(prepared, "lifecycle-failed")
    persistence.phase_error = RuntimeError("abort permanently unavailable")

    with pytest.raises(RuntimeError, match="unresolved activation abort"):
        runtime.close()

    assert candidate.closed
    assert incumbent.closed
    assert close_calls == [candidate]
    assert runtime.pending_abort_count == 1
    assert persistence.close_count == 1
    assert persistence.events[-1] == "close"


def test_public_boundary_validation_rejects_invalid_ownership_inputs() -> None:
    factory = _factory()
    persistence = _Persistence()
    incumbent = _pair(_descriptor(50.0, candidate_generation=3, role_generation=4))
    candidate = _pair(_descriptor(40.0, candidate_generation=4, role_generation=5))
    with pytest.raises(TypeError):
        ActivationRuntime(SimpleNamespace(), incumbent, persistence)
    with pytest.raises(TypeError):
        ActivationRuntime(factory, SimpleNamespace(), persistence)
    with pytest.raises(ValueError):
        ActivationRuntime(factory, incumbent, persistence)
    incumbent.authorize_output()
    with pytest.raises(TypeError):
        ActivationRuntime(factory, incumbent, SimpleNamespace())
    with pytest.raises(ValueError):
        ActivationRuntime(factory, incumbent, persistence, receipt_timeout=-1)
    with pytest.raises(TypeError):
        ActivationRuntime(factory, incumbent, persistence, owns_persistence=1)
    runtime = ActivationRuntime(factory, incumbent, persistence)
    prepared = PreparedActivationRecord.prepared(
        timestamp_ms=1_000,
        incumbent=incumbent.descriptor,
        candidate=candidate.descriptor,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        policy=ActivationPolicy.PASSIVE_AUTO,
        decision_id="boundary",
    )
    with pytest.raises(TypeError):
        runtime.queue_prepared_activation(SimpleNamespace(), candidate, _durable())
    with pytest.raises(TypeError):
        runtime.queue_prepared_activation(prepared, SimpleNamespace(), _durable())
    with pytest.raises(TypeError):
        runtime.queue_prepared_activation(prepared, candidate, SimpleNamespace())
    with pytest.raises(TypeError):
        runtime.submit_prepared_phase(SimpleNamespace())
    with pytest.raises(ValueError):
        runtime.submit_prepared_phase(prepared.transition(ActivationPhase.ACTIVE))
    with pytest.raises(TypeError):
        runtime.restore_activation(SimpleNamespace(), ())
    with pytest.raises(TypeError):
        runtime.replace_active_pair(SimpleNamespace(), retain_current=False)
    with pytest.raises(ValueError):
        runtime.terminate(" ")
    runtime.close()
    candidate.close()


@pytest.mark.parametrize("abort_failure", ("raise", "reject"))
def test_abort_submission_failure_terminalizes_immediately(abort_failure) -> None:
    runtime, _incumbent, candidate, prepared, persistence = _runtime()
    assert runtime.queue_prepared_activation(prepared, candidate, _durable())
    candidate.close()
    if abort_failure == "raise":
        persistence.next_phase_error = RuntimeError("abort write failed")
    else:
        persistence.reject_next_phase = True
    assert runtime.advance_activation() is False
    assert runtime.terminated_reason == "activation-abort-persistence-failed"
    runtime.close()


def test_incomplete_receipts_and_lifecycle_failure_never_authorize_next_solve() -> None:
    runtime, _incumbent, candidate, prepared, persistence = _runtime()
    assert runtime.queue_prepared_activation(prepared, candidate, _durable())
    assert runtime.advance_activation() is False
    assert runtime.advance_activation() is False
    persistence.reject_evidence = True
    persistence.phase_submissions[-1].receipt._complete(durable=True)
    assert runtime.advance_activation() is False
    assert runtime.terminated_reason == "activation-lifecycle-persistence-failed"
    assert not runtime.output_authorized
    runtime.close()


def test_compensation_lifecycle_failure_terminalizes() -> None:
    runtime, _incumbent, candidate, prepared, persistence = _runtime()
    assert runtime.queue_prepared_activation(prepared, candidate, _durable())
    assert runtime.advance_activation() is False
    persistence.reject_evidence = True
    persistence.phase_submissions[-1].receipt._complete(durable=False)
    assert runtime.advance_activation() is False
    assert runtime.terminated_reason == "activation-lifecycle-persistence-failed"
    runtime.close()


def test_replace_active_pair_closes_displaced_owners_and_closed_runtime_rejects() -> None:
    runtime, incumbent, candidate, _prepared, _persistence = _runtime()
    runtime.replace_active_pair(candidate, retain_current=True)
    assert runtime.active_pair is candidate
    assert runtime.rollback_pair is incumbent
    second = _pair(_descriptor(42.0, candidate_generation=8, role_generation=8))
    runtime.replace_active_pair(second, retain_current=False)
    assert incumbent.closed
    assert candidate.closed
    assert runtime.active_pair is second
    runtime.close()
    third = _pair(_descriptor(43.0, candidate_generation=9, role_generation=9))
    with pytest.raises(RuntimeError):
        runtime.replace_active_pair(third, retain_current=False)
    assert third.closed


def test_owned_close_attempts_persistence_and_all_pairs_after_collaborator_failures() -> None:
    runtime, incumbent, candidate, prepared, persistence = _runtime(
        owns_persistence=True
    )
    assert runtime.queue_prepared_activation(prepared, candidate, _durable())
    persistence.close = lambda timeout=2.0: (_ for _ in ()).throw(
        RuntimeError("close failed")
    )
    candidate.estimator.close = lambda: (_ for _ in ()).throw(RuntimeError("close failed"))
    with pytest.raises(RuntimeError, match="complete activation runtime ownership"):
        runtime.close()
    assert incumbent.closed
    assert candidate.closed


def test_active_lifecycle_submission_exception_revokes_and_terminalizes() -> None:
    runtime, _incumbent, candidate, prepared, persistence = _runtime()
    assert runtime.queue_prepared_activation(prepared, candidate, _durable())
    assert runtime.advance_activation() is False
    persistence.raise_evidence = RuntimeError("evidence queue failed")
    persistence.phase_submissions[-1].receipt._complete(durable=True)
    try:
        assert runtime.advance_activation() is False
        assert runtime.terminated_reason == "activation-lifecycle-persistence-failed"
        assert not runtime.output_authorized
        assert not candidate.authorized
    finally:
        runtime.close()


def test_fallback_lifecycle_submission_exception_revokes_and_terminalizes() -> None:
    runtime, incumbent, candidate, prepared, persistence = _runtime()
    _activate(runtime, candidate, prepared, persistence)
    persistence.raise_evidence = RuntimeError("fallback evidence queue failed")
    try:
        assert runtime.activation_runtime_failure("solve failed") is False
        assert runtime.terminated_reason == "activation-lifecycle-persistence-failed"
        assert not runtime.output_authorized
        assert not incumbent.authorized
        assert candidate.closed
    finally:
        runtime.close()


def test_replacement_never_observes_two_authorized_pairs(monkeypatch) -> None:
    runtime, incumbent, candidate, _prepared, _persistence = _runtime()
    observed = []
    original_authorize = OwnedMpcPair.authorize_output

    def observe_authorize(pair):
        if pair is candidate:
            observed.append((incumbent.authorized, candidate.authorized))
        return original_authorize(pair)

    monkeypatch.setattr(OwnedMpcPair, "authorize_output", observe_authorize)
    runtime.replace_active_pair(candidate, retain_current=False)
    assert observed == [(False, False)]
    assert runtime.active_pair is candidate
    assert candidate.authorized
    assert incumbent.closed
    runtime.close()


def test_replacement_close_failure_leaves_current_active_owner_usable(
    monkeypatch,
) -> None:
    runtime, incumbent, candidate, prepared, persistence = _runtime()
    _activate(runtime, candidate, prepared, persistence)
    replacement = _pair(_descriptor(42.0, candidate_generation=8, role_generation=8))
    original_close = OwnedMpcPair.close

    def fail_displaced_close(pair):
        if pair is incumbent:
            raise RuntimeError("displaced rollback close failed")
        return original_close(pair)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(OwnedMpcPair, "close", fail_displaced_close)
            with pytest.raises(RuntimeError, match="could not retire displaced activation ownership"):
                runtime.replace_active_pair(replacement, retain_current=False)
        assert runtime.active_pair is candidate
        assert candidate.authorized
        assert not candidate.closed
        assert runtime.rollback_pair is incumbent
        assert not incumbent.closed
        assert not replacement.authorized
        assert not replacement.closed
    finally:
        replacement.close()
        runtime.close()


def test_replacement_authorization_failure_restores_incumbent_owner(
    monkeypatch,
) -> None:
    runtime, incumbent, candidate, _prepared, _persistence = _runtime()
    original_authorize = OwnedMpcPair.authorize_output

    def fail_candidate_authorization(pair):
        if pair is candidate:
            raise RuntimeError("candidate authorization failed")
        return original_authorize(pair)

    with monkeypatch.context() as patch:
        patch.setattr(OwnedMpcPair, "authorize_output", fail_candidate_authorization)
        with pytest.raises(RuntimeError, match="candidate authorization failed"):
            runtime.replace_active_pair(candidate, retain_current=True)
    assert runtime.active_pair is incumbent
    assert runtime.rollback_pair is None
    assert incumbent.authorized
    assert not candidate.authorized
    assert not candidate.closed
    candidate.close()
    runtime.close()


def test_committed_replacement_retries_real_partial_core_close_on_runtime_close() -> None:
    runtime, incumbent, candidate, _prepared, _persistence = _runtime()
    solver = incumbent.solver
    estimator = incumbent.estimator
    solver_close_calls = 0
    estimator_close_calls = 0

    def close_solver() -> None:
        nonlocal solver_close_calls
        solver_close_calls += 1
        if solver_close_calls == 1:
            raise RuntimeError("solver close failed once")

    def close_estimator() -> None:
        nonlocal estimator_close_calls
        estimator_close_calls += 1

    solver.close = close_solver
    estimator.close = close_estimator
    runtime.replace_active_pair(candidate, retain_current=False)
    assert runtime.active_pair is candidate
    assert candidate.authorized
    assert incumbent.closed
    assert solver_close_calls == 1
    assert estimator_close_calls == 1
    runtime.close()
    assert candidate.closed
    assert solver_close_calls == 2
    assert estimator_close_calls == 1


def test_replacement_authorization_failure_never_restores_closed_rollback(
    monkeypatch,
) -> None:
    runtime, incumbent, candidate, prepared, persistence = _runtime()
    _activate(runtime, candidate, prepared, persistence)
    replacement = _pair(_descriptor(42.0, candidate_generation=8, role_generation=8))
    original_authorize = OwnedMpcPair.authorize_output

    def fail_replacement_authorization(pair):
        if pair is replacement:
            raise RuntimeError("replacement authorization failed")
        return original_authorize(pair)

    with monkeypatch.context() as patch:
        patch.setattr(OwnedMpcPair, "authorize_output", fail_replacement_authorization)
        with pytest.raises(RuntimeError, match="replacement authorization failed"):
            runtime.replace_active_pair(replacement, retain_current=False)
    assert runtime.active_pair is candidate
    assert candidate.authorized
    assert runtime.rollback_pair is None
    assert incumbent.closed
    replacement.close()
    runtime.close()


def test_replacement_authorization_failure_never_restores_closed_pending(
    monkeypatch,
) -> None:
    runtime, incumbent, candidate, prepared, _persistence = _runtime()
    replacement = _pair(_descriptor(42.0, candidate_generation=8, role_generation=8))
    original_authorize = OwnedMpcPair.authorize_output
    assert runtime.queue_prepared_activation(prepared, candidate, _durable())

    def fail_replacement_authorization(pair):
        if pair is replacement:
            raise RuntimeError("replacement authorization failed")
        return original_authorize(pair)

    with monkeypatch.context() as patch:
        patch.setattr(OwnedMpcPair, "authorize_output", fail_replacement_authorization)
        with pytest.raises(RuntimeError, match="replacement authorization failed"):
            runtime.replace_active_pair(replacement, retain_current=False)
    assert runtime.active_pair is incumbent
    assert incumbent.authorized
    assert not runtime.activation_pending
    assert candidate.closed
    replacement.close()
    runtime.close()


def test_later_retiree_close_failure_never_republishes_earlier_closed_owner(
    monkeypatch,
) -> None:
    runtime, incumbent, candidate, prepared, persistence = _runtime()
    _activate(runtime, candidate, prepared, persistence)
    pending = _pair(_descriptor(42.0, candidate_generation=8, role_generation=8))
    pending_record = PreparedActivationRecord.prepared(
        incumbent=candidate.descriptor,
        candidate=pending.descriptor,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        policy=ActivationPolicy.PASSIVE_AUTO,
        decision_id="second-prepared",
        timestamp_ms=2_000,
    )
    replacement = _pair(_descriptor(43.0, candidate_generation=9, role_generation=9))
    assert runtime.queue_prepared_activation(pending_record, pending, _durable())

    pending_solver = pending.solver
    pending_solver.close = lambda: (_ for _ in ()).throw(RuntimeError("later retiree close failed"))

    with pytest.raises(RuntimeError, match="could not retire displaced activation ownership"):
        runtime.replace_active_pair(replacement, retain_current=False)
    assert runtime.active_pair is candidate
    assert candidate.authorized
    assert runtime.rollback_pair is None
    assert incumbent.closed
    assert not runtime.activation_pending
    assert pending.closed
    pending_solver.close = lambda: None
    replacement.close()
    runtime.close()


def test_replacement_closes_pending_candidate_exactly_once(monkeypatch) -> None:
    runtime, incumbent, candidate, prepared, _persistence = _runtime()
    replacement = _pair(_descriptor(42.0, candidate_generation=8, role_generation=8))
    close_calls = []
    original_close = OwnedMpcPair.close

    def count_close(pair):
        if pair is candidate:
            close_calls.append(pair)
        return original_close(pair)

    monkeypatch.setattr(OwnedMpcPair, "close", count_close)
    assert runtime.queue_prepared_activation(prepared, candidate, _durable())
    runtime.replace_active_pair(replacement, retain_current=True)
    assert candidate.closed
    assert close_calls == [candidate]
    assert runtime.active_pair is replacement
    assert runtime.rollback_pair is incumbent
    runtime.close()
    assert close_calls == [candidate]


def test_duplicate_transaction_disposes_distinct_same_descriptor_owner_once(
    monkeypatch,
) -> None:
    runtime, _incumbent, candidate, prepared, _persistence = _runtime()
    duplicate = _pair(candidate.descriptor)
    close_calls = []
    original_close = OwnedMpcPair.close

    def count_close(pair):
        if pair is duplicate:
            close_calls.append(pair)
        return original_close(pair)

    monkeypatch.setattr(OwnedMpcPair, "close", count_close)
    assert runtime.queue_prepared_activation(prepared, candidate, _durable())
    assert runtime.queue_prepared_activation(prepared, duplicate, _durable())
    assert duplicate.closed
    assert close_calls == [duplicate]
    runtime.close()
    assert close_calls == [duplicate]


def test_different_theta_candidate_keeps_its_candidate_replay_and_never_copies_incumbent_delay_state(
    monkeypatch,
) -> None:
    runtime, incumbent, candidate, prepared, _persistence = _runtime()
    incumbent_state = incumbent.core.capture_operating_state()
    candidate_state = candidate.core.capture_operating_state()
    assert candidate.descriptor.configuration["parameters"]["theta"] != (
        incumbent.descriptor.configuration["parameters"]["theta"]
    )
    assert candidate_state.delay_states != incumbent_state.delay_states

    def forbidden_copy(*_args, **_kwargs):
        raise AssertionError("activation must not copy an incumbent delay chain")

    monkeypatch.setattr(
        incumbent.core,
        "capture_operating_state",
        forbidden_copy,
    )
    monkeypatch.setattr(
        candidate.core,
        "adopt_operating_state",
        forbidden_copy,
    )

    assert runtime.install_candidate_pair_inert(candidate, prepared)
    installed = candidate.core.capture_operating_state()
    assert installed.delay_states == candidate_state.delay_states
    assert installed.measured_temperature_c == pytest.approx(110.0)
    assert runtime.active_pair is candidate
    assert runtime.rollback_pair is incumbent
    runtime.close()


@pytest.mark.parametrize("status", ("short", "absent", "uncertain"))
def test_nonexact_candidate_seed_stays_inert_and_cannot_displace_active_incumbent(
    status: str,
) -> None:
    runtime, incumbent, candidate, prepared, _persistence = _runtime()
    candidate.core.seed_from_trajectory(
        _activation_seed(candidate.descriptor, status=status)
    )

    assert not runtime.install_candidate_pair_inert(candidate, prepared)
    assert runtime.active_pair is incumbent
    assert incumbent.authorized
    assert not candidate.authorized
    runtime.close()


def test_rollback_absent_seed_is_explicitly_reported_as_conservative_cold_start() -> None:
    runtime, incumbent, _candidate, _prepared, _persistence = _runtime()
    runtime.bind_estimator_seed_source(
        lambda _theta, _n_delay: _activation_seed(
            incumbent.descriptor,
            status="absent",
        )
    )

    assert runtime._refresh_pair_seed(incumbent)
    assert runtime.last_seed_refresh_status == "absent"
    assert incumbent.core.estimator_seed_status == "absent"
    runtime.close()


def test_rollback_cold_seed_uses_failed_pairs_current_applied_load() -> None:
    runtime, incumbent, candidate, prepared, _persistence = _runtime()
    assert runtime.install_candidate_pair_inert(candidate, prepared)
    candidate.core.set_output(
        AppliedOutput(
            ratio=0.45,
            source=OutputSource.CONTROLLER,
            timestamp=2.0,
        )
    )
    runtime.bind_estimator_seed_source(
        lambda _theta, _n_delay: _activation_seed(
            incumbent.descriptor,
            status="absent",
        )
    )

    assert runtime.compensate_candidate_pair(candidate, prepared, "test-cold-start")
    state = incumbent.core.capture_operating_state()
    assert state.applied_combustion_load == pytest.approx(0.5)
    assert state.delay_states == pytest.approx((0.5,) * 8)
    assert runtime.last_seed_refresh_status == "absent"
    runtime.close()
