"""Atomic grey estimator/native-pair activation contracts."""

from __future__ import annotations

import collections
from dataclasses import FrozenInstanceError
import json
import threading
import time
from types import SimpleNamespace

import pytest
from common.model_evidence import (
    ConfidenceDecisionEvidence,
    EvidenceKind,
    FallbackEvidence,
    ModelEvidenceRecord,
    RollbackEvidence,
)
from common.web_contracts.learning import ModelActivationRequest
import controller.runtime.model_persistence as model_persistence_module

from controller.model_learning.activation import (
    ActivationDecision,
    ActivationManager,
    ActivationPhase,
    GreyControlPairDescriptor,
    OwnedGreyControlPair,
    PreparedActivationRecord,
    canonical_snapshot_digest,
    recover_startup_activation,
)
from controller.model_learning.contracts import ActivationPolicy, CandidateOrigin
from controller.runtime.model_fitting import TeardownGreyHistory
from controller.mpc import Controller as MpcController
import controller.mpc as mpc_module
from tests.unit.mpc._solver_fixtures import CYCLE, _config as _mpc_config, _Estimator, _Solver


_INCUMBENT_CONFIG = {
    "schema": "pifire-grey-box-model/v4",
    "n_delay": 8,
    "parameters": {"C_c": 320.0, "K_Q": 350.0, "theta": 50.0, "h_amb": 0.5, "T_amb": 20.0, "sigma": 1.4e-9},
}
_CANDIDATE_CONFIG = {
    **_INCUMBENT_CONFIG,
    "parameters": {**_INCUMBENT_CONFIG["parameters"], "theta": 40.0},
}


class _Handle:
    def __init__(self, name: str) -> None:
        self.name = name
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


class _Receipt:
    def __init__(self, *, accepted: bool = True, durable: bool = True) -> None:
        self.accepted = accepted
        self.durable = durable
        self.completed = durable
        self.waits = 0

    def wait(self, timeout: float | None = None) -> bool:
        self.waits += 1
        return self.durable


def _descriptor(
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


def _owned(descriptor: GreyControlPairDescriptor, name: str) -> OwnedGreyControlPair:
    return OwnedGreyControlPair(descriptor, _Handle(f"{name}-estimator"), _Handle(f"{name}-solver"))


def _manager(
    *,
    validation: bool = True,
    dry_solve: bool = True,
    receipt: _Receipt | None = None,
    build_error: Exception | None = None,
):
    incumbent_descriptor = _descriptor(_INCUMBENT_CONFIG, candidate_generation=3, role_generation=4)
    candidate_descriptor = _descriptor(_CANDIDATE_CONFIG, candidate_generation=4, role_generation=5)
    incumbent = _owned(incumbent_descriptor, "incumbent")
    candidate = _owned(candidate_descriptor, "candidate")
    calls: list[str] = []
    records: list[PreparedActivationRecord] = []
    durable_receipt = _Receipt() if receipt is None else receipt

    def build(descriptor: GreyControlPairDescriptor) -> OwnedGreyControlPair:
        calls.append("build")
        assert descriptor is candidate_descriptor
        if build_error is not None:
            raise build_error
        return candidate

    def validate(pair: OwnedGreyControlPair) -> bool:
        calls.append("validate")
        assert pair is candidate
        return validation

    def solve(pair: OwnedGreyControlPair) -> bool:
        calls.append("dry-solve")
        assert pair is candidate
        return dry_solve

    def persist(record: PreparedActivationRecord):
        calls.append("persist-prepared")
        records.append(record)
        return durable_receipt

    manager = ActivationManager(
        incumbent_pair=incumbent,
        build_candidate=build,
        validate_candidate=validate,
        native_dry_solve=solve,
        persist_prepared=persist,
        clock_ms=lambda: 1_000,
        receipt_timeout=0.1,
    )
    return manager, candidate_descriptor, incumbent, candidate, calls, records, durable_receipt


def _request(descriptor: GreyControlPairDescriptor) -> ModelActivationRequest:
    return ModelActivationRequest(
        candidate_digest=descriptor.model_digest,
        decision_id="decision-9",
    )


def test_pair_descriptor_is_complete_immutable_and_digest_checked() -> None:
    descriptor = _descriptor(_CANDIDATE_CONFIG, candidate_generation=4, role_generation=5)

    assert descriptor.estimator_kind == "ekf"
    assert descriptor.solver_kind == "acados-grey"
    assert descriptor.candidate_generation == 4
    assert descriptor.role_generation == 5
    assert descriptor.configuration["n_delay"] == 8
    assert len(descriptor.ownership_digest) == 64
    with pytest.raises(TypeError):
        descriptor.configuration["n_delay"] = 7  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        descriptor.role_generation = 7  # type: ignore[misc]
    with pytest.raises(ValueError, match="model_digest"):
        GreyControlPairDescriptor(
            model_digest="f" * 64,
            configuration=_CANDIDATE_CONFIG,
            estimator_kind="ekf",
            solver_kind="acados-grey",
            candidate_generation=4,
            role_generation=5,
        )


def test_owned_pair_closes_both_handles_exactly_once() -> None:
    pair = _owned(_descriptor(_CANDIDATE_CONFIG, candidate_generation=4, role_generation=5), "candidate")

    pair.close()
    pair.close()

    assert pair.estimator.closed == 1
    assert pair.solver.closed == 1


def test_prepare_builds_validates_dry_solves_then_drains_durable_prepared_receipt() -> None:
    manager, descriptor, incumbent, candidate, calls, records, receipt = _manager()

    decision = manager.prepare(
        _request(descriptor),
        descriptor,
        origin=CandidateOrigin.OPERATOR_CALIBRATION,
        policy=ActivationPolicy.OPERATOR_REVIEWED,
    )

    assert isinstance(decision, ActivationDecision)
    assert decision.accepted
    assert decision.phase is ActivationPhase.PREPARED
    assert decision.candidate_pair is candidate
    assert decision.incumbent_pair is incumbent
    assert calls == ["build", "validate", "dry-solve", "persist-prepared"]
    assert receipt.waits == 1
    assert records == [decision.record]
    assert decision.record is not None
    assert decision.record.incumbent == incumbent.descriptor
    assert decision.record.candidate == candidate.descriptor
    assert decision.record.rollback == incumbent.descriptor
    assert decision.record.phase is ActivationPhase.PREPARED


def test_queue_acceptance_without_durable_receipt_never_prepares_or_transfers_ownership() -> None:
    manager, descriptor, incumbent, candidate, calls, _records, receipt = _manager(receipt=_Receipt(durable=False))

    decision = manager.prepare(
        _request(descriptor),
        descriptor,
        origin=CandidateOrigin.OPERATOR_CALIBRATION,
        policy=ActivationPolicy.OPERATOR_REVIEWED,
    )

    assert not decision.accepted
    assert decision.reason == "activation-persistence-not-durable"
    assert manager.prepared is None
    assert manager.active_pair is incumbent
    assert candidate.estimator.closed == 1
    assert candidate.solver.closed == 1
    assert incumbent.estimator.closed == incumbent.solver.closed == 0
    assert calls[-1] == "persist-prepared"
    assert receipt.waits == 1


@pytest.mark.parametrize(
    ("changes", "reason", "expected_calls"),
    [
        ({"build_error": RuntimeError("build")}, "candidate-build-failed", ["build"]),
        ({"validation": False}, "candidate-validation-failed", ["build", "validate"]),
        ({"dry_solve": False}, "native-dry-solve-failed", ["build", "validate", "dry-solve"]),
    ],
)
def test_every_candidate_validation_failure_closes_the_complete_candidate_pair(
    changes, reason, expected_calls
) -> None:
    manager, descriptor, incumbent, candidate, calls, _records, _receipt = _manager(**changes)

    decision = manager.prepare(
        _request(descriptor),
        descriptor,
        origin=CandidateOrigin.OPERATOR_CALIBRATION,
        policy=ActivationPolicy.OPERATOR_REVIEWED,
    )

    assert not decision.accepted
    assert decision.reason == reason
    assert calls == expected_calls
    assert manager.active_pair is incumbent
    assert incumbent.estimator.closed == incumbent.solver.closed == 0
    if "build_error" not in changes:
        assert candidate.estimator.closed == 1
        assert candidate.solver.closed == 1


@pytest.mark.parametrize(
    ("origin", "policy"),
    [
        (CandidateOrigin.PASSIVE_ONLINE, ActivationPolicy.PASSIVE_AUTO),
        (CandidateOrigin.OPERATOR_CALIBRATION, ActivationPolicy.OPERATOR_REVIEWED),
        (CandidateOrigin.COOK_REFIT, ActivationPolicy.COOK_REFIT),
    ],
)
def test_origin_policy_is_exact(origin: CandidateOrigin, policy: ActivationPolicy) -> None:
    manager, descriptor, *_ = _manager()
    decision = manager.prepare(_request(descriptor), descriptor, origin=origin, policy=policy)
    assert decision.accepted


def test_manual_request_requires_exact_digest_decision_and_operator_reviewed_policy() -> None:
    manager, descriptor, _incumbent, candidate, calls, *_ = _manager()

    wrong_digest = manager.prepare(
        ModelActivationRequest(candidate_digest="f" * 64, decision_id="decision-9"),
        descriptor,
        origin=CandidateOrigin.OPERATOR_CALIBRATION,
        policy=ActivationPolicy.OPERATOR_REVIEWED,
    )
    wrong_policy = manager.prepare(
        _request(descriptor),
        descriptor,
        origin=CandidateOrigin.OPERATOR_CALIBRATION,
        policy=ActivationPolicy.PASSIVE_AUTO,
    )

    assert wrong_digest.reason == "candidate-digest-changed"
    assert wrong_policy.reason == "origin-policy-mismatch"
    assert calls == []
    assert candidate.estimator.closed == 0


def test_phase_transitions_preserve_exact_pair_owners_and_abort_reason() -> None:
    manager, descriptor, incumbent, candidate, *_ = _manager()
    prepared = manager.prepare(
        _request(descriptor), descriptor,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        policy=ActivationPolicy.PASSIVE_AUTO,
    ).record
    assert prepared is not None

    active = prepared.transition(ActivationPhase.ACTIVE)
    aborted = prepared.transition(ActivationPhase.ABORTED, reason="interrupted-activation")

    assert active.incumbent is incumbent.descriptor
    assert active.candidate is candidate.descriptor
    assert active.rollback is incumbent.descriptor
    assert aborted.reason == "interrupted-activation"
    with pytest.raises(ValueError, match="reason"):
        prepared.transition(ActivationPhase.ABORTED)
    with pytest.raises(ValueError, match="prepared"):
        active.transition(ActivationPhase.ACTIVE)


def test_canonical_digest_is_mapping_order_independent_and_parameter_sensitive() -> None:
    reordered = dict(reversed(tuple(_CANDIDATE_CONFIG.items())))
    changed = {**_CANDIDATE_CONFIG, "parameters": {**_CANDIDATE_CONFIG["parameters"], "theta": 41.0}}

    assert canonical_snapshot_digest(_CANDIDATE_CONFIG) == canonical_snapshot_digest(reordered)
    assert canonical_snapshot_digest(_CANDIDATE_CONFIG) != canonical_snapshot_digest(changed)


def _persisted_state(record: PreparedActivationRecord):
    active = record.candidate if record.phase is ActivationPhase.ACTIVE else record.incumbent
    return SimpleNamespace(
        phase=record.phase.value,
        transaction_id=record.transaction_id,
        evidence_decision_id=record.decision_id,
        incumbent_pair_json=json.dumps(record.incumbent.to_dict()),
        candidate_pair_json=json.dumps(record.candidate.to_dict()),
        rollback_pair_json=json.dumps(record.rollback.to_dict()),
        origin=record.origin.value,
        policy=record.policy.value,
        reason=record.reason,
        role_generation=active.role_generation,
        candidate_generation=record.candidate.candidate_generation,
        candidate_digest=record.candidate.model_digest,
    )


def test_startup_aborts_prepared_before_restoring_only_the_incumbent() -> None:
    manager, descriptor, incumbent, _candidate, *_ = _manager()
    prepared = manager.prepare(
        _request(descriptor), descriptor,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        policy=ActivationPolicy.PASSIVE_AUTO,
    ).record
    assert prepared is not None
    writes: list[PreparedActivationRecord] = []
    receipt = _Receipt()

    recovery = recover_startup_activation(
        _persisted_state(prepared),
        persist_aborted=lambda record: (writes.append(record), receipt)[1],
        receipt_timeout=0.1,
    )

    assert recovery.restore == incumbent.descriptor
    assert recovery.rollback == incumbent.descriptor
    assert recovery.phase is ActivationPhase.ABORTED
    assert writes[0].reason == "interrupted-activation"
    assert receipt.waits == 1


def test_startup_restores_candidate_only_from_active_and_never_replays_a_swap() -> None:
    manager, descriptor, incumbent, candidate, *_ = _manager()
    prepared = manager.prepare(
        _request(descriptor), descriptor,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        policy=ActivationPolicy.PASSIVE_AUTO,
    ).record
    assert prepared is not None
    writes = []

    active = recover_startup_activation(
        _persisted_state(prepared.transition(ActivationPhase.ACTIVE)),
        persist_aborted=lambda record: writes.append(record),
    )
    aborted = recover_startup_activation(
        _persisted_state(prepared.transition(ActivationPhase.ABORTED, reason="swap-failed")),
        persist_aborted=lambda record: writes.append(record),
    )

    assert active.restore == candidate.descriptor
    assert active.rollback == incumbent.descriptor
    assert active.phase is ActivationPhase.ACTIVE
    assert aborted.restore == incumbent.descriptor
    assert aborted.phase is ActivationPhase.ABORTED
    assert writes == []


def test_startup_refuses_ambiguous_prepared_compensation_without_a_durable_abort() -> None:
    manager, descriptor, *_ = _manager()
    prepared = manager.prepare(
        _request(descriptor), descriptor,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        policy=ActivationPolicy.PASSIVE_AUTO,
    ).record
    assert prepared is not None

    with pytest.raises(RuntimeError, match="durably abort"):
        recover_startup_activation(
            _persisted_state(prepared),
            persist_aborted=lambda _record: _Receipt(durable=False),
            receipt_timeout=0.01,
        )


@pytest.mark.parametrize("lifecycle_kind", ("fallback", "rollback"))
def test_startup_applies_persisted_fallback_before_candidate_output_is_authorized(
    lifecycle_kind,
) -> None:
    core, _incumbent, _candidate, prepared = _bare_mpc_pair_owner()
    active = prepared.transition(ActivationPhase.ACTIVE)
    persisted = _persisted_state(active)
    payload = (
        FallbackEvidence(
            decision_id=active.decision_id,
            reason="native-solve-failure",
            failed_digest=active.candidate.model_digest,
            failed_generation=active.candidate.role_generation,
            last_safe_command=0.25,
            fallback_kind="grey-box",
        )
        if lifecycle_kind == "fallback"
        else RollbackEvidence(
            decision_id=active.decision_id,
            reason="operator rollback",
        )
    )
    lifecycle = ModelEvidenceRecord(
        evidence_id=f"{lifecycle_kind}-startup",
        kind=(
            EvidenceKind.FALLBACK
            if lifecycle_kind == "fallback"
            else EvidenceKind.ROLLBACK
        ),
        session_id="session-startup",
        cook_id=None,
        timestamp_ms=2_000,
        role_generation=active.candidate.role_generation + 1,
        model_digest=active.candidate.model_digest,
        provenance_digest=active.rollback.model_digest,
        payload=payload,
    )
    built = []

    def build(descriptor):
        built.append(descriptor)
        return _owned(descriptor, f"restored-{len(built)}")

    core._activation_persistence_worker = object()
    core._build_pair_from_descriptor = build

    assert core.restore_activation(persisted, (lifecycle,))

    assert built == [prepared.rollback]
    assert core.active_control_pair.descriptor == prepared.rollback
    assert core.rollback_control_pair is None
    assert core.activation_output_authorized
    assert core.failed_role_generations == frozenset({prepared.candidate.role_generation})
    assert core._teardown_history.role_generation == core._model_revision


def _bare_mpc_pair_owner():
    incumbent_descriptor = _descriptor(_INCUMBENT_CONFIG, candidate_generation=3, role_generation=4)
    candidate_descriptor = _descriptor(_CANDIDATE_CONFIG, candidate_generation=4, role_generation=5)
    incumbent = _owned(incumbent_descriptor, "incumbent")
    candidate = _owned(candidate_descriptor, "candidate")
    prepared = PreparedActivationRecord.prepared(
        timestamp_ms=1_000,
        incumbent=incumbent_descriptor,
        candidate=candidate_descriptor,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        policy=ActivationPolicy.PASSIVE_AUTO,
        decision_id="decision-runtime",
    )
    core = MpcController.__new__(MpcController)
    core.estimator = incumbent.estimator
    core.mpc = incumbent.solver
    core._active_control_pair = incumbent
    core._rollback_control_pair = None
    core._inert_activation = None
    core._activation_output_authorized = True
    core._activation_terminated_reason = None
    core._activation_persistence_lock = threading.Lock()
    core._failed_role_generations = set()
    core._activation_events = collections.deque()
    core._last_combustion_load = 0.35
    core._model_revision = 4
    core._learning_role_generation = 4
    core._teardown_history = TeardownGreyHistory(role_generation=4, max_observations=120)
    return core, incumbent, candidate, prepared


def test_automatic_preparation_drains_confidence_receipt_before_prepared_phase() -> None:
    core, _incumbent, _candidate, _prepared = _bare_mpc_pair_owner()
    calls = []

    class _Worker:
        def submit_evidence(self, record):
            calls.append(("evidence", record))
            return SimpleNamespace(accepted=True)
        def submit_activation_confidence(self, record):
            calls.append(("confidence", record))
            return _Receipt()

        def submit_activation_phase(self, record, *, expected_phase):
            calls.append(("phase", record, expected_phase))
            return _Receipt()

    native_config = SimpleNamespace(
        __dataclass_fields__={"theta": None, "horizon_steps": None},
        theta=39.0,
        horizon_steps=12,
    )
    configuration = {"theta": 39.0, "horizon_steps": 12}
    evaluation = SimpleNamespace(
        decision_id="decision-confidence-fifo",
        accepted=True,
        blockers=(),
        role_generation=4,
        candidate_generation=4,
        incumbent_digest=core.active_control_pair.descriptor.model_digest,
        challenger_digest=canonical_snapshot_digest(configuration),
    )
    request = SimpleNamespace(
        origin=CandidateOrigin.PASSIVE_ONLINE,
        candidate_generation=4,
        window=SimpleNamespace(role_generation=4),
    )
    preparation = SimpleNamespace(
        candidate_pair=SimpleNamespace(
            estimator=_Handle("candidate-estimator"),
            controller=_Handle("candidate-solver"),
        ),
        candidate=SimpleNamespace(request=request, config=native_config),
        candidate_digest=canonical_snapshot_digest(configuration),
        dry_solve_finite=True,
    )
    core.cfg = {"estimator": "ekf"}
    core._learning = SimpleNamespace(_last_evaluation=evaluation)
    core._activation_persistence_worker = _Worker()
    core._prepared_pair_transitions = collections.deque()

    core._prepare_automatic_pair_activation(preparation, ActivationPolicy.PASSIVE_AUTO)

    assert [kind for kind, *_ in calls] == [
        "confidence",
        "phase",
        "evidence",
    ]
    confidence = next(record for kind, record, *_ in calls if kind == "confidence")
    assert isinstance(confidence.payload, ConfidenceDecisionEvidence)
    assert confidence.payload.decision_id == evaluation.decision_id
    assert confidence.payload.blocked is False



def test_hold_and_learning_first_use_share_one_activation_persistence_fifo(
    monkeypatch,
) -> None:
    core, _incumbent, _candidate, _prepared = _bare_mpc_pair_owner()
    core.cfg = {"estimator": "ekf"}
    core._learning_lock = threading.Lock()
    core._activation_persistence_worker = None
    core._persisted_activation_confidence_ids = set()
    core._prepared_pair_transitions = collections.deque()
    configuration = {"theta": 39.0, "horizon_steps": 12}
    evaluation = SimpleNamespace(
        decision_id="decision-raced-first-use",
        accepted=True,
        blockers=(),
        role_generation=4,
        candidate_generation=4,
        incumbent_digest=core.active_control_pair.descriptor.model_digest,
        challenger_digest=canonical_snapshot_digest(configuration),
    )
    core._learning = SimpleNamespace(_last_evaluation=evaluation)
    preparation = SimpleNamespace(
        candidate_pair=SimpleNamespace(
            estimator=_Handle("raced-estimator"),
            controller=_Handle("raced-solver"),
        ),
        candidate=SimpleNamespace(
            request=SimpleNamespace(
                origin=CandidateOrigin.PASSIVE_ONLINE,
                candidate_generation=4,
                window=SimpleNamespace(role_generation=4),
            ),
            config=SimpleNamespace(
                __dataclass_fields__={"theta": None, "horizon_steps": None},
                theta=39.0,
                horizon_steps=12,
            ),
        ),
        candidate_digest=canonical_snapshot_digest(configuration),
        dry_solve_finite=True,
    )
    hold_confidence = ModelEvidenceRecord(
        evidence_id="hold-confidence-first",
        kind=EvidenceKind.CONFIDENCE_DECISION,
        session_id="session-raced-first-use",
        cook_id=None,
        timestamp_ms=900,
        role_generation=4,
        model_digest=canonical_snapshot_digest(configuration),
        provenance_digest=core.active_control_pair.descriptor.model_digest,
        payload=ConfidenceDecisionEvidence(
            decision_id=evaluation.decision_id,
            blocked=False,
            reason=None,
        ),
    )
    first_constructor_entered = threading.Event()
    release_first_constructor = threading.Event()
    constructors = []

    class _Worker:
        def __init__(self, *_args, **_kwargs):
            self.calls = []
            constructors.append(self)
            if len(constructors) == 1:
                first_constructor_entered.set()
                release_first_constructor.wait(timeout=1.0)

        def submit_evidence(self, record):
            self.calls.append(("evidence", record.evidence_id))
            return SimpleNamespace(accepted=True)
        def submit_activation_confidence(self, record):
            self.calls.append(("confidence", record.evidence_id))
            return _Receipt()

        def submit_activation_phase(self, record, *, expected_phase):
            self.calls.append(("phase", record.transaction_id, expected_phase))
            return _Receipt()

    monkeypatch.setattr(model_persistence_module, "ModelPersistenceWorker", _Worker)
    errors = []
    hold_thread = threading.Thread(
        target=lambda: (
            core.submit_activation_confidence(hold_confidence)
            if not errors
            else None
        )
    )

    def prepare_from_learning():
        try:
            core._prepare_automatic_pair_activation(
                preparation,
                ActivationPolicy.PASSIVE_AUTO,
            )
        except Exception as error:
            errors.append(error)

    learning_thread = threading.Thread(target=prepare_from_learning)
    hold_thread.start()
    assert first_constructor_entered.wait(timeout=1.0)
    learning_thread.start()
    time.sleep(0.02)
    release_first_constructor.set()
    hold_thread.join(timeout=1.0)
    learning_thread.join(timeout=1.0)

    assert not hold_thread.is_alive()
    assert not learning_thread.is_alive()
    assert errors == []
    assert len(constructors) == 1
    assert core._activation_persistence_worker is constructors[0]
    calls = constructors[0].calls
    phase_index = next(index for index, call in enumerate(calls) if call[0] == "phase")
    assert any(call[0] == "confidence" for call in calls[:phase_index])

def test_mpc_installs_complete_pair_inertly_then_authorizes_only_the_active_receipt() -> None:
    core, incumbent, candidate, prepared = _bare_mpc_pair_owner()

    assert core.install_candidate_pair_inert(candidate, prepared)
    assert core.estimator is candidate.estimator
    assert core.mpc is candidate.solver
    assert not core.activation_output_authorized
    assert core.rollback_control_pair is incumbent
    assert core.authorize_candidate_pair(prepared.transition(ActivationPhase.ACTIVE))
    assert core.activation_output_authorized
    assert core.active_control_pair is candidate
    assert core._teardown_history.role_generation == prepared.candidate.role_generation


def test_post_activation_confidence_failure_restores_exact_pair_fences_generation_and_records_reason() -> None:
    core, incumbent, candidate, prepared = _bare_mpc_pair_owner()
    core.install_candidate_pair_inert(candidate, prepared)
    core.authorize_candidate_pair(prepared.transition(ActivationPhase.ACTIVE))

    assert core.activation_runtime_failure("confidence-window-regressed")

    assert core.estimator is incumbent.estimator
    assert core.mpc is incumbent.solver
    assert core.active_control_pair is incumbent
    assert candidate.estimator.closed == candidate.solver.closed == 1
    assert incumbent.estimator.closed == incumbent.solver.closed == 0
    assert core.failed_role_generations == frozenset({prepared.candidate.role_generation})
    assert core._teardown_history.role_generation == core._model_revision == 6
    events = core.drain_activation_events()
    assert len(events) == 1
    assert isinstance(events[0].payload, FallbackEvidence)
    assert events[0].payload.reason == "confidence-window-regressed"
    assert events[0].payload.failed_digest == prepared.candidate.model_digest
    assert events[0].payload.failed_generation == prepared.candidate.role_generation
    assert events[0].payload.decision_id == prepared.decision_id
    assert core.drain_activation_events() == ()



def test_first_native_solve_failure_after_activation_restores_exact_pair_and_records_reason(
    monkeypatch,
) -> None:
    class _FailingSolver:
        def solve(self, *_args, **_kwargs):
            raise RuntimeError("native exploded")

        def close(self):
            pass

    _Solver.created.clear()
    monkeypatch.setattr(mpc_module, "GreyBoxEKF", _Estimator)
    monkeypatch.setattr(mpc_module, "AcadosGreyBoxMPC", _Solver)
    core = MpcController(_mpc_config(), "C", dict(CYCLE))
    incumbent = core.active_control_pair
    candidate_descriptor = _descriptor(
        _CANDIDATE_CONFIG,
        candidate_generation=incumbent.descriptor.candidate_generation + 1,
        role_generation=incumbent.descriptor.role_generation + 1,
    )
    candidate = OwnedGreyControlPair(
        candidate_descriptor,
        _Estimator(),
        _FailingSolver(),
    )
    prepared = PreparedActivationRecord.prepared(
        timestamp_ms=1_000,
        incumbent=incumbent.descriptor,
        candidate=candidate_descriptor,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        policy=ActivationPolicy.PASSIVE_AUTO,
        decision_id="decision-native-failure",
    )
    try:
        assert core.install_candidate_pair_inert(candidate, prepared)
        assert core.authorize_candidate_pair(prepared.transition(ActivationPhase.ACTIVE))

        core.update(200.0)

        assert core.active_control_pair is incumbent
        event = core.drain_activation_events()[0]
        assert event.payload.reason == "native-solve-failure"
        assert event.payload.decision_id == prepared.decision_id
    finally:
        core.close()

def test_operator_rollback_restores_only_the_recorded_in_memory_rollback_owner() -> None:
    core, incumbent, candidate, prepared = _bare_mpc_pair_owner()
    unrelated = _owned(
        _descriptor(
            {**_INCUMBENT_CONFIG, "parameters": {**_INCUMBENT_CONFIG["parameters"], "theta": 45.0}},
            candidate_generation=5,
            role_generation=6,
        ),
        "unrelated",
    )
    core.install_candidate_pair_inert(candidate, prepared)
    core.authorize_candidate_pair(prepared.transition(ActivationPhase.ACTIVE))
    core._learning_candidate_pair = unrelated

    assert core.rollback_activation("operator exact rollback")

    assert core.active_control_pair is incumbent
    assert core.estimator is incumbent.estimator
    assert core.mpc is incumbent.solver
    assert incumbent.estimator.closed == incumbent.solver.closed == 0
    assert candidate.estimator.closed == candidate.solver.closed == 1
    assert core._teardown_history.role_generation == core._model_revision == 6
    assert unrelated.estimator.closed == unrelated.solver.closed == 0
