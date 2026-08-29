"""Atomic grey estimator/native-pair activation contracts."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import FrozenInstanceError, replace
from math import ceil
from types import SimpleNamespace

import pytest

import controller.mpc as mpc_module
import controller.mpc_core as mpc_core_module
import controller.runtime.model_persistence as model_persistence_module
from common.learning_trajectory import ModelFitLineage
from common.model_evidence import (
    ConfidenceDecisionEvidence,
    EvidenceKind,
    FallbackEvidence,
    ModelEvidenceRecord,
    RollbackEvidence,
)
from common.persistence.model_challenger import (
    ModelChallengerState,
    create_model_challenger,
    prepare_model_challenger_activation,
)
from common.persistence.model_evidence import ModelActivationState, read_model_activation
from common.web_contracts.learning import ModelActivationRequest
from controller.acados import GreyBoxMPCConfig
from controller.applied_output import AppliedOutput, OutputSource
from controller.model_learning.activation import (
    ActivationDecision,
    ActivationManager,
    ActivationPhase,
    GreyControlPairDescriptor,
    PreparedActivationRecord,
    canonical_snapshot_digest,
    recover_startup_activation,
)
from controller.model_learning.activation_runtime import ActivationRuntime
from controller.model_learning.contracts import ActivationPolicy, CandidateOrigin
from controller.model_learning.grey_runtime import GreyLearningRuntime
from controller.mpc import Controller as MpcController
from controller.mpc_config import DEFAULT_MPC_CONFIG, MpcConfig
from controller.mpc_core import MpcCore
from controller.mpc_factory import MpcPairFactory, OwnedMpcPair
from controller.mpc_model import EstimatorSeed
from controller.runtime.model_fitting import CandidatePair
from tests.unit.common.test_model_challenger_store import _corpus
from tests.unit.mpc._solver_fixtures import (
    CYCLE,
    _Estimator,
    _Solver,
    inactive_calibration,
)
from tests.unit.mpc._solver_fixtures import (
    _config as _mpc_config,
)

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
        self.resets: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def reset(self, *args: object, **kwargs: object):
        self.resets.append((args, kwargs))

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


def _seed_qualified_challenger(
    incumbent: GreyControlPairDescriptor,
    candidate: GreyControlPairDescriptor,
    *,
    decision_id: str,
) -> ModelChallengerState:
    corpus = _corpus(decision_id)
    request_id = f"fit-{decision_id}"
    state = ModelChallengerState(
        schema_version=1,
        challenger_id=f"challenger-{decision_id}",
        revision=0,
        phase="qualified",
        origin=CandidateOrigin.PASSIVE_ONLINE,
        policy=ActivationPolicy.CAUSAL_AUTO,
        fit_corpus=corpus,
        fit_lineage=ModelFitLineage(
            request_id=request_id,
            parent_incumbent_digest=incumbent.model_digest,
            parent_incumbent_generation=incumbent.role_generation,
            candidate_generation=candidate.candidate_generation,
            fit_corpus=corpus,
            fit_corpus_digest=corpus.corpus_digest,
            trigger_origin=CandidateOrigin.PASSIVE_ONLINE.value,
            result_status="succeeded",
            candidate_digest=candidate.model_digest,
        ),
        fit_preparation={
            "request_id": request_id,
            "accepted": True,
            "candidate_digest": candidate.model_digest,
            "native_build": "passed",
            "dry_solve": "passed",
            "target_timing": {
                "target": "test",
                "samples": 1,
                "p99_ms": 1.0,
                "limit_ms": 2.0,
            },
        },
        controller_configuration_digest="c" * 64,
        incumbent=incumbent,
        candidate=candidate,
        calibration_manifest=None,
        evaluation_epoch=0,
        evaluation_round=2,
        consecutive_wins=2,
        required_wins=2,
        last_decision_id=decision_id,
        last_evidence_id=f"evidence-{decision_id}",
        activation_transaction_id=None,
        retirement_reason=None,
        created_ms=900,
        updated_ms=900,
        retired_ms=None,
    )
    return create_model_challenger(state)


def _migrated_descriptor(
    descriptor: GreyControlPairDescriptor,
) -> GreyControlPairDescriptor:
    return MpcPairFactory.migrate_legacy_descriptor(descriptor)


def _owned_with_handles(
    descriptor: GreyControlPairDescriptor,
    estimator,
    solver,
) -> OwnedMpcPair:
    numerical = MpcCore(
        _mpc_config(),
        "C",
        dict(CYCLE),
        components=(estimator, solver),
    )
    return OwnedMpcPair(numerical, descriptor)


def _owned(descriptor: GreyControlPairDescriptor, name: str) -> OwnedMpcPair:
    return _owned_with_handles(
        descriptor,
        _Handle(f"{name}-estimator"),
        _Handle(f"{name}-solver"),
    )


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

    def build(descriptor: GreyControlPairDescriptor) -> OwnedMpcPair:
        calls.append("build")
        assert descriptor is candidate_descriptor
        if build_error is not None:
            raise build_error
        return candidate

    def validate(pair: OwnedMpcPair) -> bool:
        calls.append("validate")
        assert pair is candidate
        return validation

    def solve(pair: OwnedMpcPair) -> bool:
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
        policy=ActivationPolicy.CAUSAL_AUTO,
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
        policy=ActivationPolicy.CAUSAL_AUTO,
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
def test_every_candidate_validation_failure_closes_the_complete_candidate_pair(changes, reason, expected_calls) -> None:
    manager, descriptor, incumbent, candidate, calls, _records, _receipt = _manager(**changes)

    decision = manager.prepare(
        _request(descriptor),
        descriptor,
        origin=CandidateOrigin.OPERATOR_CALIBRATION,
        policy=ActivationPolicy.CAUSAL_AUTO,
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
        (CandidateOrigin.PASSIVE_ONLINE, ActivationPolicy.CAUSAL_AUTO),
        (CandidateOrigin.OPERATOR_CALIBRATION, ActivationPolicy.CAUSAL_AUTO),
        (CandidateOrigin.COOK_REFIT, ActivationPolicy.COOK_REFIT),
    ],
)
def test_origin_policy_is_exact(origin: CandidateOrigin, policy: ActivationPolicy) -> None:
    manager, descriptor, *_ = _manager()
    decision = manager.prepare(_request(descriptor), descriptor, origin=origin, policy=policy)
    assert decision.accepted


def test_prepare_requires_exact_digest_decision_and_causal_policy() -> None:
    manager, descriptor, _incumbent, candidate, calls, *_ = _manager()

    wrong_digest = manager.prepare(
        ModelActivationRequest(candidate_digest="f" * 64, decision_id="decision-9"),
        descriptor,
        origin=CandidateOrigin.OPERATOR_CALIBRATION,
        policy=ActivationPolicy.CAUSAL_AUTO,
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
        _request(descriptor),
        descriptor,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        policy=ActivationPolicy.CAUSAL_AUTO,
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


def _persisted_state(record: PreparedActivationRecord) -> ModelActivationState:
    active = record.candidate if record.phase is ActivationPhase.ACTIVE else record.incumbent
    return ModelActivationState(
        active_snapshot_json=json.dumps(active.to_dict()["configuration"]),
        rollback_snapshot_json=json.dumps(record.rollback.to_dict()["configuration"]),
        evidence_decision_id=record.decision_id,
        controller_configuration_digest=record.candidate.ownership_digest,
        phase=record.phase.value,
        transaction_id=record.transaction_id,
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
        _request(descriptor),
        descriptor,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        policy=ActivationPolicy.CAUSAL_AUTO,
    ).record
    assert prepared is not None
    writes: list[PreparedActivationRecord] = []
    receipt = _Receipt()

    recovery = recover_startup_activation(
        _persisted_state(prepared),
        persist_aborted=lambda record: (writes.append(record), receipt)[1],
        receipt_timeout=0.1,
    )

    migrated_incumbent = _migrated_descriptor(incumbent.descriptor)
    assert recovery.restore == migrated_incumbent
    assert recovery.rollback == migrated_incumbent
    assert recovery.phase is ActivationPhase.ABORTED
    assert recovery.record.transaction_id == prepared.transaction_id
    assert recovery.record.incumbent == migrated_incumbent
    assert recovery.source_candidate_digest == descriptor.model_digest
    assert recovery.record.candidate == _migrated_descriptor(descriptor)
    assert writes[0].reason == "interrupted-activation"
    assert receipt.waits == 1


def test_startup_restores_candidate_only_from_active_and_never_replays_a_swap() -> None:
    manager, descriptor, incumbent, candidate, *_ = _manager()
    prepared = manager.prepare(
        _request(descriptor),
        descriptor,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        policy=ActivationPolicy.CAUSAL_AUTO,
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

    migrated_incumbent = _migrated_descriptor(incumbent.descriptor)
    migrated_candidate = _migrated_descriptor(candidate.descriptor)
    assert active.restore == migrated_candidate
    assert active.rollback == migrated_incumbent
    assert active.phase is ActivationPhase.ACTIVE
    assert active.source_candidate_digest == candidate.descriptor.model_digest
    assert active.record.transaction_id == prepared.transaction_id
    assert aborted.restore == migrated_incumbent
    assert aborted.rollback == migrated_incumbent
    assert aborted.phase is ActivationPhase.ABORTED
    assert aborted.source_candidate_digest == candidate.descriptor.model_digest
    assert aborted.record.transaction_id == prepared.transaction_id
    assert writes == []


def test_startup_refuses_ambiguous_prepared_compensation_without_a_durable_abort() -> None:
    manager, descriptor, *_ = _manager()
    prepared = manager.prepare(
        _request(descriptor),
        descriptor,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        policy=ActivationPolicy.CAUSAL_AUTO,
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
    monkeypatch,
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
        kind=(EvidenceKind.FALLBACK if lifecycle_kind == "fallback" else EvidenceKind.ROLLBACK),
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

    monkeypatch.setattr(core._pair_factory, "restore", build)

    assert core.restore_activation(persisted, (lifecycle,))

    migrated_rollback = _migrated_descriptor(prepared.rollback)
    assert built == [migrated_rollback]
    assert core.active_control_pair.descriptor == migrated_rollback
    assert core.rollback_control_pair is None
    assert core.activation_output_authorized
    assert core.failed_role_generations == frozenset({prepared.candidate.role_generation})
    assert core._grey_learning_runtime.learning_role_generation == core._grey_learning_runtime.model_authority()[0]
    core.close()


def _bare_mpc_pair_owner(
    persistence: model_persistence_module.ModelPersistenceWorker | None = None,
):
    incumbent_descriptor = _descriptor(_INCUMBENT_CONFIG, candidate_generation=3, role_generation=4)
    candidate_descriptor = _descriptor(_CANDIDATE_CONFIG, candidate_generation=4, role_generation=5)
    incumbent = _owned(incumbent_descriptor, "incumbent")
    candidate = _owned(candidate_descriptor, "candidate")
    prepared = PreparedActivationRecord.prepared(
        timestamp_ms=1_000,
        incumbent=incumbent_descriptor,
        candidate=candidate_descriptor,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        policy=ActivationPolicy.CAUSAL_AUTO,
        decision_id="decision-runtime",
    )
    incumbent.authorize_output()
    core = MpcController.__new__(MpcController)
    incumbent.core._last_combustion_load = 0.35
    core._closed = False
    core.set_point = 0.0
    core._pair_factory = MpcPairFactory(
        DEFAULT_MPC_CONFIG,
        "C",
        dict(CYCLE),
        advance_calibration=inactive_calibration,
        model_authority=lambda: (4, None),
        on_policy_failure=lambda _error: None,
    )
    if persistence is None:
        persistence = model_persistence_module.ModelPersistenceWorker(
            SimpleNamespace(save_outcome=lambda _name, _snapshot: None),
            SimpleNamespace(error=lambda _message: None),
            append_evidence=lambda _records: None,
            persist_activation_phase=lambda _record, _expected: None,
        )
    core._activation_runtime = ActivationRuntime(
        core._pair_factory,
        incumbent,
        persistence,
    )
    core._activation_runtime.bind_estimator_seed_source(
        lambda theta, n_delay: EstimatorSeed(
            delay_states=(0.4,) * n_delay,
            chamber_temperature_c=110.0,
            disturbance=0.0,
            segment_id="activation-fixture",
            pre_roll_digest="c" * 64,
            pre_roll_frame_count=ceil(3 * theta / 20.0),
            required_frame_count=ceil(3 * theta / 20.0),
            status="exact",
        )
    )
    core._grey_learning_runtime = GreyLearningRuntime(
        pair_factory=core._pair_factory,
        activation_runtime=core._activation_runtime,
        learning_enabled=False,
        units="C",
        cycle_data=dict(CYCLE),
        active_pair=lambda: core._activation_runtime.active_pair,
        active_components=lambda: CandidatePair(
            core._activation_runtime.active_pair.estimator,
            core._activation_runtime.active_pair.solver,
        ),
        configuration=lambda: MpcConfig(core._activation_runtime.active_pair.core.config),
        snapshot_parameters=lambda: core._activation_runtime.active_pair.core.snapshot_parameters(),
        sync_configuration=lambda: None,
        append_trace=lambda _records: None,
    )
    core._grey_learning_runtime.sync_activation_generation(exact=True)
    return core, incumbent, candidate, prepared


def test_automatic_preparation_drains_confidence_receipt_before_prepared_phase(ds) -> None:
    calls = []

    class _Worker(model_persistence_module.ModelPersistenceWorker):
        def __init__(self):
            pass

        def submit_evidence(self, record):
            calls.append(("evidence", record))
            return SimpleNamespace(accepted=True)

        def submit_activation_confidence(self, record):
            calls.append(("confidence", record))
            receipt = model_persistence_module.DurableActivationReceipt(accepted=True)
            receipt._complete(durable=True)
            return receipt

        def submit_activation_phase(self, record, *, expected_phase):
            calls.append(("phase", record, expected_phase))
            receipt = model_persistence_module.DurableActivationReceipt(accepted=True)
            receipt._complete(durable=True)
            return receipt

        def flush_and_stop(self, *, timeout=0.1):
            return True

    core, _incumbent, _candidate, _prepared = _bare_mpc_pair_owner(_Worker())

    native_config = GreyBoxMPCConfig(theta=39.0, horizon_steps=12)
    configuration = {name: getattr(native_config, name) for name in native_config.__dataclass_fields__}
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
            estimator=_Estimator(),
            controller=_Solver(native_config),
        ),
        candidate=SimpleNamespace(request=request, config=native_config),
        candidate_digest=canonical_snapshot_digest(configuration),
        dry_solve_finite=True,
    )
    core._activation_runtime.active_pair.core.cfg = {"estimator": "ekf"}
    _seed_qualified_challenger(
        core.active_control_pair.descriptor,
        core._grey_learning_runtime._prepared_candidate_descriptor(preparation),
        decision_id=evaluation.decision_id,
    )
    core._grey_learning_runtime.prepare_automatic_activation(
        preparation,
        ActivationPolicy.CAUSAL_AUTO,
        evaluation,
    )

    assert [kind for kind, *_ in calls] == [
        "confidence",
        "evidence",
    ]
    assert read_model_activation().phase == ActivationPhase.PREPARED.value
    confidence = next(record for kind, record, *_ in calls if kind == "confidence")
    assert isinstance(confidence.payload, ConfidenceDecisionEvidence)
    assert confidence.payload.decision_id == evaluation.decision_id
    assert confidence.payload.blocked is False
    core._grey_learning_runtime.close()
    core.close()


def test_hold_and_learning_share_one_injected_activation_persistence_fifo(ds) -> None:
    calls = []

    class _Worker(model_persistence_module.ModelPersistenceWorker):
        def __init__(self):
            pass

        def submit_evidence(self, record):
            calls.append(("evidence", record.evidence_id))
            return SimpleNamespace(accepted=True)

        def submit_activation_confidence(self, record):
            calls.append(("confidence", record.evidence_id))
            receipt = model_persistence_module.DurableActivationReceipt(accepted=True)
            receipt._complete(durable=True)
            return receipt

        def submit_activation_phase(self, record, *, expected_phase):
            calls.append(("phase", record.transaction_id, expected_phase))
            receipt = model_persistence_module.DurableActivationReceipt(accepted=True)
            receipt._complete(durable=True)
            return receipt

        def flush_and_stop(self, *, timeout=0.1):
            return True

    worker = _Worker()
    core, _incumbent, _candidate, _prepared = _bare_mpc_pair_owner(worker)
    core.cfg = {"estimator": "ekf"}
    native_config = GreyBoxMPCConfig(theta=39.0, horizon_steps=12)
    configuration = {name: getattr(native_config, name) for name in native_config.__dataclass_fields__}
    evaluation = SimpleNamespace(
        decision_id="decision-raced-first-use",
        accepted=True,
        blockers=(),
        role_generation=4,
        candidate_generation=4,
        incumbent_digest=core.active_control_pair.descriptor.model_digest,
        challenger_digest=canonical_snapshot_digest(configuration),
    )

    preparation = SimpleNamespace(
        candidate_pair=SimpleNamespace(
            estimator=_Estimator(),
            controller=_Solver(native_config),
        ),
        candidate=SimpleNamespace(
            request=SimpleNamespace(
                origin=CandidateOrigin.PASSIVE_ONLINE,
                candidate_generation=4,
                window=SimpleNamespace(role_generation=4),
            ),
            config=native_config,
        ),
        candidate_digest=canonical_snapshot_digest(configuration),
        dry_solve_finite=True,
    )
    _seed_qualified_challenger(
        core.active_control_pair.descriptor,
        core._grey_learning_runtime._prepared_candidate_descriptor(preparation),
        decision_id=evaluation.decision_id,
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

    core.submit_activation_confidence(hold_confidence)
    core._grey_learning_runtime.prepare_automatic_activation(
        preparation,
        ActivationPolicy.CAUSAL_AUTO,
        evaluation,
    )

    evidence_index = next(index for index, call in enumerate(calls) if call[0] == "evidence")
    assert any(call[0] == "confidence" for call in calls[:evidence_index])
    assert read_model_activation().phase == ActivationPhase.PREPARED.value
    core._grey_learning_runtime.close()
    core.close()


def test_mpc_installs_complete_pair_inertly_then_authorizes_only_the_active_receipt() -> None:
    core, incumbent, candidate, prepared = _bare_mpc_pair_owner()

    assert core.install_candidate_pair_inert(candidate, prepared)
    assert core.estimator is candidate.estimator
    assert core.mpc is candidate.solver
    assert not core.activation_output_authorized
    assert core.rollback_control_pair is incumbent
    assert not incumbent.authorized
    assert not candidate.authorized
    assert core.authorize_candidate_pair(prepared.transition(ActivationPhase.ACTIVE))
    assert core.activation_output_authorized
    assert core.active_control_pair is candidate
    assert candidate.authorized
    assert not incumbent.authorized
    assert core._grey_learning_runtime.learning_role_generation == prepared.candidate.role_generation
    core.close()


def test_inert_candidate_receives_live_target_before_output_authorization() -> None:
    core, incumbent, candidate, prepared = _bare_mpc_pair_owner()
    core.set_point = 107.2
    incumbent.core.set_target(core.set_point)
    incumbent.core.set_output(AppliedOutput(0.45, OutputSource.CONTROLLER, 1.0))
    incumbent.core.history.append((1.0, 100.0, 0.5))

    assert core.install_candidate_pair_inert(candidate, prepared)

    assert not core.activation_output_authorized
    assert candidate.core.set_point_c == pytest.approx(107.2)
    assert candidate.core.applied_combustion_load == pytest.approx(0.5)
    assert candidate.core.last_combustion_load == pytest.approx(0.35)
    assert tuple(candidate.core.history) == ((1.0, 100.0, 0.5),)
    assert candidate.estimator.resets
    assert candidate.solver.resets
    core.close()


def test_successive_activation_closes_displaced_rollback_before_retaining_current_owner() -> None:
    core, original, first, first_prepared = _bare_mpc_pair_owner()
    assert core.install_candidate_pair_inert(first, first_prepared)
    assert core.authorize_candidate_pair(first_prepared.transition(ActivationPhase.ACTIVE))
    second_descriptor = _descriptor(
        {
            **_INCUMBENT_CONFIG,
            "parameters": {**_INCUMBENT_CONFIG["parameters"], "theta": 35.0},
        },
        candidate_generation=5,
        role_generation=6,
    )
    second = _owned(second_descriptor, "second")
    second_prepared = PreparedActivationRecord.prepared(
        timestamp_ms=2_000,
        incumbent=first.descriptor,
        candidate=second.descriptor,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        policy=ActivationPolicy.CAUSAL_AUTO,
        decision_id="decision-runtime-second",
    )

    assert core.install_candidate_pair_inert(second, second_prepared)

    assert original.estimator.closed == original.solver.closed == 1
    assert core.rollback_control_pair is first
    assert not first.authorized
    assert not second.authorized
    assert core.authorize_candidate_pair(second_prepared.transition(ActivationPhase.ACTIVE))
    assert second.authorized
    assert not first.authorized
    assert original.estimator.closed == original.solver.closed == 1
    core.close()


def test_inert_compensation_reauthorizes_only_incumbent_and_closes_candidate() -> None:
    core, incumbent, candidate, prepared = _bare_mpc_pair_owner()
    assert core.install_candidate_pair_inert(candidate, prepared)

    assert core.compensate_candidate_pair(candidate, prepared, "persistence-failed")

    assert core.active_control_pair is incumbent
    assert incumbent.authorized
    assert not candidate.authorized
    assert candidate.estimator.closed == candidate.solver.closed == 1
    assert incumbent.estimator.closed == incumbent.solver.closed == 0
    core.close()


def test_post_activation_confidence_failure_restores_exact_pair_fences_generation_and_records_reason() -> None:
    core, incumbent, candidate, prepared = _bare_mpc_pair_owner()
    core.install_candidate_pair_inert(candidate, prepared)
    core.authorize_candidate_pair(prepared.transition(ActivationPhase.ACTIVE))
    assert candidate.authorized
    assert not incumbent.authorized

    assert core.activation_runtime_failure("confidence-window-regressed")

    assert core.estimator is incumbent.estimator
    assert core.mpc is incumbent.solver
    assert core.active_control_pair is incumbent
    assert candidate.estimator.closed == candidate.solver.closed == 1
    assert incumbent.estimator.closed == incumbent.solver.closed == 0
    assert core.failed_role_generations == frozenset({prepared.candidate.role_generation})
    assert core._grey_learning_runtime.learning_role_generation == core._grey_learning_runtime.model_authority()[0] == 6
    events = core.drain_activation_events()
    assert incumbent.authorized
    assert not candidate.authorized
    assert len(events) == 1
    assert isinstance(events[0].payload, FallbackEvidence)
    assert events[0].payload.reason == "confidence-window-regressed"
    assert events[0].payload.failed_digest == prepared.candidate.model_digest
    assert events[0].payload.failed_generation == prepared.candidate.role_generation
    assert events[0].payload.decision_id == prepared.decision_id
    assert core.drain_activation_events() == ()
    core.close()


def test_first_native_solve_failure_after_activation_restores_exact_pair_and_records_reason(
    monkeypatch,
    ds,
) -> None:
    class _FailingSolver:
        def __init__(self, config: GreyBoxMPCConfig) -> None:
            self.config = config

        def solve(self, *_args, **_kwargs):
            raise RuntimeError("native exploded")

        def reset(self):
            pass

        def close(self):
            pass

    _Solver.created.clear()
    monkeypatch.setattr(mpc_core_module, "GreyBoxEKF", _Estimator)
    monkeypatch.setattr(mpc_core_module, "AcadosGreyBoxMPC", _Solver)
    core = MpcController(_mpc_config(), "C", dict(CYCLE))
    core._activation_runtime.bind_estimator_seed_source(
        lambda theta, n_delay: EstimatorSeed(
            delay_states=(0.4,) * n_delay,
            chamber_temperature_c=110.0,
            disturbance=0.0,
            segment_id="activation-fixture",
            pre_roll_digest="c" * 64,
            pre_roll_frame_count=ceil(3 * theta / 20.0),
            required_frame_count=ceil(3 * theta / 20.0),
            status="exact",
        )
    )
    incumbent = core.active_control_pair
    native_config = replace(core.mpc.config, theta=core.mpc.config.theta + 1.0)
    candidate = core._pair_factory.adopt(
        core._pair_factory.native(
            native_config,
            estimator_kind="ekf",
            candidate_generation=incumbent.descriptor.candidate_generation + 1,
            role_generation=incumbent.descriptor.role_generation + 1,
        ),
        _Estimator(),
        _FailingSolver(native_config),
        authorized=False,
    )
    candidate_descriptor = candidate.descriptor
    prepared = PreparedActivationRecord.prepared(
        timestamp_ms=1_000,
        incumbent=incumbent.descriptor,
        candidate=candidate_descriptor,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        policy=ActivationPolicy.CAUSAL_AUTO,
        decision_id="decision-native-failure",
    )
    qualified = _seed_qualified_challenger(
        incumbent.descriptor,
        candidate_descriptor,
        decision_id=prepared.decision_id,
    )
    prepare_model_challenger_activation(
        expected_revision=qualified.revision,
        activation=prepared,
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

    assert core.rollback_activation("operator exact rollback")

    assert core.active_control_pair is incumbent
    assert core.estimator is incumbent.estimator
    assert core.mpc is incumbent.solver
    assert incumbent.estimator.closed == incumbent.solver.closed == 0
    assert candidate.estimator.closed == candidate.solver.closed == 1
    assert core._grey_learning_runtime.learning_role_generation == core._grey_learning_runtime.model_authority()[0] == 6
    assert unrelated.estimator.closed == unrelated.solver.closed == 0
