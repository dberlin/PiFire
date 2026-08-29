import json
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace

import pytest

from common.model_evidence import EvidenceKind, ModelEvidenceRecord, RollbackEvidence
from common.persistence.model_evidence import ModelActivationState, ModelRollbackCommitOutcome
from common.web_contracts.learning import ModelRollbackRequest
from controller.model_learning.activation import GreyControlPairDescriptor, canonical_snapshot_digest
from controller.model_learning.rollback_service import (
    ModelRollbackService,
    RollbackAccepted,
    RollbackRejected,
    RollbackRejectionCategory,
)

_NOW_MS = 1_725_000_123_456
_DECISION_ID = "decision-service-grey"


def _descriptor(theta: float, *, candidate_generation: int, role_generation: int) -> GreyControlPairDescriptor:
    configuration = {"theta": theta}
    return GreyControlPairDescriptor(
        model_digest=canonical_snapshot_digest(configuration),
        configuration=configuration,
        estimator_kind="ekf",
        solver_kind="acados-grey",
        candidate_generation=candidate_generation,
        role_generation=role_generation,
    )


def _active_state(
    *, phase: str = "active", include_lineage: bool = True
) -> tuple[ModelActivationState, GreyControlPairDescriptor, GreyControlPairDescriptor]:
    incumbent = _descriptor(50.0, candidate_generation=3, role_generation=4)
    candidate = _descriptor(40.0, candidate_generation=4, role_generation=5)
    return (
        ModelActivationState(
            active_snapshot_json=json.dumps(dict(candidate.configuration)),
            rollback_snapshot_json=json.dumps(dict(incumbent.configuration)),
            evidence_decision_id=_DECISION_ID,
            controller_configuration_digest=candidate.ownership_digest,
            role_generation=candidate.role_generation,
            phase=phase,
            transaction_id="1" * 64,
            incumbent_pair_json=json.dumps(incumbent.to_dict()),
            candidate_pair_json=json.dumps(candidate.to_dict()) if include_lineage else None,
            rollback_pair_json=json.dumps(incumbent.to_dict()) if include_lineage else None,
            origin="operator-calibration",
            policy="causal-auto",
            candidate_generation=candidate.candidate_generation,
            candidate_digest=candidate.model_digest,
        ),
        incumbent,
        candidate,
    )


def test_rollback_outcomes_are_immutable_typed_values() -> None:
    accepted = RollbackAccepted(_DECISION_ID, "reason", 6, "a" * 64)
    rejected = RollbackRejected(RollbackRejectionCategory.CONFLICT, "conflict")

    assert accepted.accepted is True
    assert rejected.accepted is False
    with pytest.raises(FrozenInstanceError):
        accepted.reason = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("state", "reason"),
    (
        (None, "there is no active grey generation"),
        ("prepared", "there is no active grey generation"),
        ("missing-lineage", "activation-lineage-missing"),
    ),
)
def test_rollback_rejects_missing_active_authority_or_lineage(state, reason) -> None:
    activation = None
    if state is not None:
        activation, _incumbent, _candidate = _active_state(
            phase="active" if state == "missing-lineage" else state,
            include_lineage=state != "missing-lineage",
        )
    service = ModelRollbackService(
        activation_reader=lambda: activation,
        rollback_committer=lambda _record, *, expected_activation: pytest.fail("must not commit"),
    )

    outcome = service.rollback(ModelRollbackRequest(reason=" operator rollback "), now_ms=_NOW_MS)

    assert outcome == RollbackRejected(RollbackRejectionCategory.CONFLICT, reason)


def test_rollback_builds_exact_evidence_and_commits_against_identical_cas_authority() -> None:
    activation, incumbent, candidate = _active_state()
    calls = []

    def commit(record, *, expected_activation):
        calls.append((record, expected_activation))
        return ModelRollbackCommitOutcome(record, True)

    outcome = ModelRollbackService(
        activation_reader=lambda: activation,
        rollback_committer=commit,
    ).rollback(ModelRollbackRequest(reason=" operator rollback "), now_ms=_NOW_MS)

    assert outcome == RollbackAccepted(
        decision_id=_DECISION_ID,
        reason="operator rollback",
        role_generation=candidate.role_generation + 1,
        rollback_digest=incumbent.model_digest,
    )
    assert calls == [
        (
            ModelEvidenceRecord(
                evidence_id=f"rollback:{_DECISION_ID}:{candidate.role_generation + 1}:{_NOW_MS}",
                kind=EvidenceKind.ROLLBACK,
                session_id="api-manual-rollback",
                cook_id=None,
                timestamp_ms=_NOW_MS,
                role_generation=candidate.role_generation + 1,
                model_digest=candidate.model_digest,
                provenance_digest=incumbent.model_digest,
                payload=RollbackEvidence(decision_id=_DECISION_ID, reason="operator rollback"),
            ),
            activation,
        )
    ]


@pytest.mark.parametrize(
    ("failure", "category", "reason"),
    (
        (ValueError("activation-state-changed"), RollbackRejectionCategory.CONFLICT, "activation-state-changed"),
        (
            RuntimeError("disk offline"),
            RollbackRejectionCategory.PERSISTENCE_UNAVAILABLE,
            "rollback-persistence-failed: disk offline",
        ),
    ),
)
def test_rollback_classifies_cas_and_persistence_failures(failure, category, reason) -> None:
    activation, _incumbent, _candidate = _active_state()

    def fail_commit(_record, *, expected_activation):
        assert expected_activation is activation
        raise failure

    outcome = ModelRollbackService(
        activation_reader=lambda: activation,
        rollback_committer=fail_commit,
    ).rollback(ModelRollbackRequest(reason="operator rollback"), now_ms=_NOW_MS)

    assert outcome == RollbackRejected(category, reason)


def test_rollback_returns_original_idempotent_lifecycle() -> None:
    activation, incumbent, candidate = _active_state()
    original = ModelEvidenceRecord(
        evidence_id="rollback-original",
        kind=EvidenceKind.ROLLBACK,
        session_id="api-manual-rollback",
        cook_id=None,
        timestamp_ms=_NOW_MS - 10,
        role_generation=candidate.role_generation + 1,
        model_digest=candidate.model_digest,
        provenance_digest=incumbent.model_digest,
        payload=RollbackEvidence(decision_id=_DECISION_ID, reason="first reason"),
    )
    outcome = ModelRollbackService(
        activation_reader=lambda: activation,
        rollback_committer=lambda _record, *, expected_activation: ModelRollbackCommitOutcome(original, False),
    ).rollback(ModelRollbackRequest(reason="retry reason"), now_ms=_NOW_MS)

    assert outcome == RollbackAccepted(
        decision_id=_DECISION_ID,
        reason="first reason",
        role_generation=candidate.role_generation + 1,
        rollback_digest=incumbent.model_digest,
    )


def test_rollback_classifies_read_corrupt_lineage_and_malformed_lifecycle() -> None:
    def fail_read():
        raise RuntimeError("activation store offline")

    read_failure = ModelRollbackService(activation_reader=fail_read).rollback(
        ModelRollbackRequest(reason="operator rollback"),
        now_ms=_NOW_MS,
    )
    activation, _incumbent, _candidate = _active_state()
    corrupt = replace(activation, candidate_pair_json="{not-json")
    lineage_failure = ModelRollbackService(activation_reader=lambda: corrupt).rollback(
        ModelRollbackRequest(reason="operator rollback"),
        now_ms=_NOW_MS,
    )
    lifecycle_failure = ModelRollbackService(
        activation_reader=lambda: activation,
        rollback_committer=lambda _record, *, expected_activation: SimpleNamespace(
            record=SimpleNamespace(payload=object())
        ),
    ).rollback(ModelRollbackRequest(reason="operator rollback"), now_ms=_NOW_MS)

    assert read_failure == RollbackRejected(
        RollbackRejectionCategory.PERSISTENCE_UNAVAILABLE,
        "rollback-persistence-failed: activation store offline",
    )
    assert lineage_failure == RollbackRejected(
        RollbackRejectionCategory.CONFLICT,
        "activation-lineage-missing",
    )
    assert lifecycle_failure == RollbackRejected(
        RollbackRejectionCategory.PERSISTENCE_UNAVAILABLE,
        "rollback-persistence-failed: invalid lifecycle",
    )
