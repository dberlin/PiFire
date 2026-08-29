"""Explicit durable rollback workflow for the active grey model."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, Protocol, TypeAlias

from common.model_evidence import EvidenceKind, FallbackEvidence, ModelEvidenceRecord, RollbackEvidence
from common.persistence.model_evidence import (
    ModelActivationState,
    ModelRollbackCommitOutcome,
    commit_model_rollback,
    read_model_activation,
)
from common.web_contracts.learning import ModelRollbackRequest


class RollbackRejectionCategory(StrEnum):
    """Finite rollback categories exhaustively mapped by the HTTP adapter."""

    CONFLICT = "conflict"
    PERSISTENCE_UNAVAILABLE = "persistence-unavailable"


@dataclass(frozen=True, slots=True)
class RollbackAccepted:
    decision_id: str
    reason: str
    role_generation: int
    rollback_digest: str
    accepted: Literal[True] = field(default=True, init=False)


@dataclass(frozen=True, slots=True)
class RollbackRejected:
    category: RollbackRejectionCategory
    reason: str
    accepted: Literal[False] = field(default=False, init=False)


RollbackOutcome: TypeAlias = RollbackAccepted | RollbackRejected


class _RollbackCommitter(Protocol):
    def __call__(
        self,
        decision: ModelEvidenceRecord,
        *,
        expected_activation: ModelActivationState,
    ) -> ModelRollbackCommitOutcome: ...


class ModelRollbackService:
    """Persist an explicit rollback against the exact active authority."""

    def __init__(
        self,
        *,
        activation_reader: Callable[[], ModelActivationState | None] = read_model_activation,
        rollback_committer: _RollbackCommitter = commit_model_rollback,
    ) -> None:
        self._activation_reader = activation_reader
        self._rollback_committer = rollback_committer

    def rollback(self, request: ModelRollbackRequest, *, now_ms: int) -> RollbackOutcome:
        if not isinstance(request, ModelRollbackRequest):
            raise TypeError("request must be a ModelRollbackRequest")
        if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
            raise ValueError("now_ms must be a non-negative integer")
        try:
            activation = self._activation_reader()
        except Exception as error:
            return RollbackRejected(
                RollbackRejectionCategory.PERSISTENCE_UNAVAILABLE,
                f"rollback-persistence-failed: {error}",
            )
        if activation is None or activation.phase != "active":
            return RollbackRejected(
                RollbackRejectionCategory.CONFLICT,
                "there is no active grey generation",
            )
        try:
            active_pair = activation.active_pair
            rollback_pair = activation.rollback_pair
        except TypeError, ValueError:
            return RollbackRejected(
                RollbackRejectionCategory.CONFLICT,
                "activation-lineage-missing",
            )
        if active_pair is None or rollback_pair is None:
            return RollbackRejected(
                RollbackRejectionCategory.CONFLICT,
                "activation-lineage-missing",
            )
        reason = request.reason.strip()
        decision = ModelEvidenceRecord(
            evidence_id=f"rollback:{activation.evidence_decision_id}:{activation.role_generation + 1}:{now_ms}",
            kind=EvidenceKind.ROLLBACK,
            session_id="api-manual-rollback",
            cook_id=None,
            timestamp_ms=now_ms,
            role_generation=activation.role_generation + 1,
            model_digest=active_pair.model_digest,
            provenance_digest=rollback_pair.model_digest,
            payload=RollbackEvidence(decision_id=activation.evidence_decision_id, reason=reason),
        )
        try:
            outcome = self._rollback_committer(decision, expected_activation=activation)
        except ValueError as error:
            return RollbackRejected(RollbackRejectionCategory.CONFLICT, str(error))
        except Exception as error:
            return RollbackRejected(
                RollbackRejectionCategory.PERSISTENCE_UNAVAILABLE,
                f"rollback-persistence-failed: {error}",
            )
        lifecycle = outcome.record.payload
        if not isinstance(lifecycle, (RollbackEvidence, FallbackEvidence)):
            return RollbackRejected(
                RollbackRejectionCategory.PERSISTENCE_UNAVAILABLE,
                "rollback-persistence-failed: invalid lifecycle",
            )
        return RollbackAccepted(
            decision_id=activation.evidence_decision_id,
            reason=lifecycle.reason,
            role_generation=outcome.record.role_generation,
            rollback_digest=rollback_pair.model_digest,
        )
