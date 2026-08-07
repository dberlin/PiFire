from __future__ import annotations

from copy import deepcopy

import pytest

from common.datastore_accessors import ModelActivationState
from common.model_evidence import (
    ActivationEvidence,
    ConfidenceDecisionEvidence,
    EvidenceKind,
    ModelEvidenceRecord,
    RefreshDiagnosticsEvidence,
)
from controller.linear_mpc.activation import (
    ActivationManager,
    ActivationRequest,
    GREY_BOX_KIND,
    STATE_SPACE_KIND,
    canonical_snapshot_digest,
)
from controller.linear_mpc.state_space import InnovationStateSpace
from tests.unit.mpc.test_innovation_state_space import _config, _frames


@pytest.fixture(name="state_space_snapshot", scope="module")
def _state_space_snapshot():
    model = InnovationStateSpace(_config(orders=(1,), delays=(1,)))
    assert model.fit(_frames(order=1)).accepted
    return model.snapshot()


def _record(evidence_id, payload, *, digest, provenance, generation=7, timestamp=100, schema=2):
    return ModelEvidenceRecord(
        evidence_id=evidence_id,
        kind=EvidenceKind(payload.payload_type),
        session_id="activation-session",
        cook_id=None,
        timestamp_ms=timestamp,
        role_generation=generation,
        model_digest=digest,
        provenance_digest=provenance,
        schema_version=schema,
        payload=payload,
    )


def _fixture(snapshot, *, persistence=True, prospective=lambda _candidate: 0.37):
    candidate = deepcopy(snapshot)
    digest = canonical_snapshot_digest(candidate)
    rollback = {"schema": "grey-box-adapter/v1", "gain": 1.0}
    provenance = canonical_snapshot_digest(rollback)
    records = [
        _record(
            "refresh-7",
            RefreshDiagnosticsEvidence(
                accepted=True,
                full_rank=True,
                finite_diagnostics=True,
                snapshot_round_trip=True,
                production_prospective=True,
            ),
            digest=digest,
            provenance=provenance,
            timestamp=90,
        ),
        _record(
            "decision-7-record",
            ConfidenceDecisionEvidence(decision_id="decision-7", blocked=False),
            digest=digest,
            provenance=provenance,
            timestamp=100,
        ),
    ]
    writes = []
    invalidations = []
    config = {"controller": "mpc", "config": {"n_horizon": 24}}

    def persist(record):
        writes.append(record)
        if persistence:
            records.append(record)
        return persistence

    manager = ActivationManager(
        lambda: tuple(records),
        candidate_snapshot=lambda _digest, _generation: candidate,
        rollback_snapshot=rollback,
        controller_configuration=lambda: config,
        prospective_solve=prospective,
        persist_activation=persist,
        invalidate_pending_origins=lambda generation, value: invalidations.append((generation, value)),
        clock_ms=lambda: 1_000,
    )
    request = ActivationRequest(digest, "decision-7")
    return manager, request, records, writes, invalidations, config, rollback


def test_prepare_rejects_stale_decision_without_control_effect(state_space_snapshot):
    manager, request, records, *_ = _fixture(state_space_snapshot)
    records.append(
        _record(
            "decision-8-record",
            ConfidenceDecisionEvidence(decision_id="decision-8", blocked=False),
            digest=request.candidate_digest,
            provenance=records[0].provenance_digest,
            timestamp=101,
        )
    )

    decision = manager.prepare(request)

    assert decision.accepted is False
    assert decision.reason == "stale-confidence-decision"
    assert manager.active_kind == GREY_BOX_KIND


def test_prepare_rejects_changed_digest_without_control_effect(state_space_snapshot):
    manager, request, *_ = _fixture(state_space_snapshot)

    decision = manager.prepare(ActivationRequest("f" * 64, request.decision_id))

    assert decision.accepted is False
    assert decision.reason == "candidate-digest-changed"
    assert manager.active_kind == GREY_BOX_KIND


def test_commit_rejects_changed_controller_configuration(state_space_snapshot):
    manager, request, _records, writes, _invalidations, config, _rollback = _fixture(state_space_snapshot)
    prepared = manager.prepare(request)
    config["config"]["n_horizon"] = 30

    decision = manager.commit(prepared)

    assert decision.accepted is False
    assert decision.reason == "controller-configuration-changed"
    assert writes == []
    assert manager.active_kind == GREY_BOX_KIND


@pytest.mark.parametrize("reason", ["incompatible-evidence-schema", "incompatible-provenance"])
def test_prepare_rejects_incompatible_schema_or_provenance(state_space_snapshot, reason):
    manager, request, records, *_ = _fixture(state_space_snapshot)
    if reason == "incompatible-evidence-schema":
        records[1] = records[1].model_copy(update={"schema_version": 1})
    else:
        records[0] = records[0].model_copy(update={"provenance_digest": "a" * 64})

    decision = manager.prepare(request)

    assert decision.accepted is False
    assert decision.reason == reason
    assert manager.active_kind == GREY_BOX_KIND


def test_prepare_rejects_candidate_reconstruction_failure(state_space_snapshot):
    malformed = deepcopy(state_space_snapshot)
    malformed["state"] = []
    manager, request, *_ = _fixture(malformed)

    decision = manager.prepare(request)

    assert decision.accepted is False
    assert manager.active_kind == GREY_BOX_KIND


def test_prepare_rejects_prospective_solve_failure(state_space_snapshot):
    def fail(_candidate):
        raise RuntimeError("prospective-solve-failed")

    manager, request, *_ = _fixture(state_space_snapshot, prospective=fail)

    decision = manager.prepare(request)

    assert decision.accepted is False
    assert decision.reason == "prospective-solve-failed"
    assert manager.active_kind == GREY_BOX_KIND


def test_persistence_failure_never_transfers_ownership(state_space_snapshot):
    manager, request, _records, writes, invalidations, *_ = _fixture(state_space_snapshot, persistence=False)

    decision = manager.commit(manager.prepare(request))

    assert decision.accepted is False
    assert decision.reason == "activation-persistence-failed"
    assert len(writes) == 1
    assert invalidations == []
    assert manager.active_kind == GREY_BOX_KIND


def test_success_persists_before_generation_rollover_and_invalidates_origins(state_space_snapshot):
    order = []
    manager, request, records, _writes, _invalidations, config, rollback = _fixture(state_space_snapshot)
    manager._persist_activation = lambda record: (order.append("persist"), records.append(record), True)[-1]
    manager._invalidate_pending_origins = lambda generation, digest: order.append(("invalidate", generation, digest))

    decision = manager.commit(manager.prepare(request))

    assert decision.accepted is True
    assert order[0] == "persist"
    assert order[1] == ("invalidate", 8, request.candidate_digest)
    assert manager.active_kind == STATE_SPACE_KIND
    assert manager.state.role_generation == 8
    assert manager.state.rollback_kind == GREY_BOX_KIND
    assert manager.state.rollback_digest == canonical_snapshot_digest(rollback)
    assert manager.state.controller_configuration_digest is not None
    assert config["controller"] == "mpc"


def test_fallback_retains_grey_box_and_failed_generation_never_auto_reenables(state_space_snapshot):
    manager, request, *_ = _fixture(state_space_snapshot)
    prepared = manager.prepare(request)
    assert manager.commit(prepared).accepted

    state = manager.fallback("non-finite-forecast", last_safe_command=0.41)

    assert state.active_kind == GREY_BOX_KIND
    assert state.failed_digest == request.candidate_digest
    assert state.failed_generation == 8
    assert state.last_safe_command == pytest.approx(0.41)
    assert state.fallback_kind == GREY_BOX_KIND
    rejected = manager.commit(prepared)
    assert rejected.accepted is False
    assert rejected.reason == "failed-generation-cannot-be-reenabled"


def test_explicit_rollback_requires_reason_and_selects_grey_box(state_space_snapshot):
    manager, request, *_ = _fixture(state_space_snapshot)
    assert manager.commit(manager.prepare(request)).accepted

    with pytest.raises(ValueError, match="non-blank"):
        manager.rollback(" ")
    state = manager.rollback("operator observed unstable combustion")

    assert state.active_kind == GREY_BOX_KIND
    assert state.fallback_reason == "operator observed unstable combustion"


def test_restart_restores_exact_active_and_rollback_generation(state_space_snapshot):
    manager, request, records, writes, _invalidations, config, _rollback = _fixture(state_space_snapshot)
    assert manager.commit(manager.prepare(request)).accepted
    activation = writes[-1]
    assert isinstance(activation.payload, ActivationEvidence)
    persisted = ModelActivationState(
        active_snapshot_json=activation.payload.active_snapshot_json,
        rollback_snapshot_json=activation.payload.rollback_snapshot_json,
        evidence_decision_id=activation.payload.decision_id,
        controller_configuration_digest=activation.payload.controller_configuration_digest,
        role_generation=activation.role_generation,
    )
    restored = ActivationManager(
        lambda: tuple(records),
        candidate_snapshot={},
        rollback_snapshot={},
        controller_configuration=config,
        prospective_solve=lambda _candidate: 0.37,
    )

    decision = restored.restore(persisted)

    assert decision.accepted is True
    assert restored.state.active_digest == manager.state.active_digest
    assert restored.state.role_generation == manager.state.role_generation
    assert restored.state.rollback_digest == manager.state.rollback_digest


def test_restart_preserves_prior_state_space_as_the_last_safe_generation(state_space_snapshot):
    manager, first_request, records, writes, _invalidations, config, _rollback = _fixture(state_space_snapshot)
    assert manager.commit(manager.prepare(first_request)).accepted
    second_model = InnovationStateSpace(_config(orders=(2,), delays=(1,)))
    assert second_model.fit(_frames(order=2)).accepted
    second_snapshot = second_model.snapshot()
    second_digest = canonical_snapshot_digest(second_snapshot)
    records.extend(
        (
            _record(
                "refresh-8",
                RefreshDiagnosticsEvidence(
                    accepted=True,
                    full_rank=True,
                    finite_diagnostics=True,
                    snapshot_round_trip=True,
                    production_prospective=True,
                ),
                digest=second_digest,
                provenance=first_request.candidate_digest,
                generation=8,
                timestamp=1_100,
            ),
            _record(
                "decision-8-record",
                ConfidenceDecisionEvidence(decision_id="decision-8", blocked=False),
                digest=second_digest,
                provenance=first_request.candidate_digest,
                generation=8,
                timestamp=1_101,
            ),
        )
    )
    manager._candidate_source = lambda digest, _generation: (
        second_snapshot if digest == second_digest else state_space_snapshot
    )
    assert manager.commit(manager.prepare(ActivationRequest(second_digest, "decision-8"))).accepted
    activation = writes[-1]
    persisted = ModelActivationState(
        active_snapshot_json=activation.payload.active_snapshot_json,
        rollback_snapshot_json=activation.payload.rollback_snapshot_json,
        evidence_decision_id=activation.payload.decision_id,
        controller_configuration_digest=activation.payload.controller_configuration_digest,
        role_generation=activation.role_generation,
    )
    restored = ActivationManager(
        lambda: tuple(records),
        candidate_snapshot={},
        rollback_snapshot={},
        controller_configuration=config,
        prospective_solve=lambda _candidate: 0.37,
    )

    assert restored.restore(persisted).accepted
    assert restored.state.rollback_kind == STATE_SPACE_KIND
    assert restored.state.rollback_digest == first_request.candidate_digest

    fallback = restored.fallback("active-solve-failed")
    assert fallback.active_kind == STATE_SPACE_KIND
    assert fallback.active_digest == first_request.candidate_digest
