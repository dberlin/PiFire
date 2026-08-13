"""Off-request durable operator activation and rollback workflows."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Literal, Protocol, TypeAlias

from common.controller_model_state import ControllerModelStore
from common.model_evidence import EvidenceKind, FallbackEvidence, ModelEvidenceRecord, RollbackEvidence
from common.persistence.model_evidence import (
    ModelActivationState,
    ModelRollbackCommitOutcome,
    commit_model_rollback,
    read_model_activation,
)
from common.persistence.runtime import read_settings
from common.web_contracts.learning import ModelActivationRequest, ModelRollbackRequest
from controller.mpc_calibration import TemperatureForecast
from controller.mpc_factory import MpcPairFactory, OwnedMpcPair
from controller.runtime.model_fitting import TargetTimingEvidence
from controller.runtime.model_persistence import DurableActivationReceipt, ModelPersistenceWorker

from .activation import ActivationDecision, ActivationManager, GreyControlPairDescriptor, PreparedActivationRecord
from .calibration import CalibrationDecision, CalibrationProgress
from .contracts import ActivationPolicy, CandidateOrigin
from .report import LearningReport, backend_learning_report


class ActivationRejectionCategory(StrEnum):
    """Finite domain categories exhaustively mapped by the HTTP adapter."""

    CONFLICT = "conflict"
    INVALID_DATA = "invalid-data"
    PERSISTENCE_UNAVAILABLE = "persistence-unavailable"
    CLEANUP_FAILED = "cleanup-failed"


class RollbackRejectionCategory(StrEnum):
    """Finite rollback categories exhaustively mapped by the HTTP adapter."""

    CONFLICT = "conflict"
    PERSISTENCE_UNAVAILABLE = "persistence-unavailable"


@dataclass(frozen=True, slots=True)
class ActivationAccepted:
    transaction_id: str
    decision_id: str
    candidate_digest: str
    role_generation: int
    accepted: Literal[True] = field(default=True, init=False)


@dataclass(frozen=True, slots=True)
class ActivationRejected:
    category: ActivationRejectionCategory
    reason: str
    cleanup_failed: bool = False
    accepted: Literal[False] = field(default=False, init=False)


ActivationOutcome: TypeAlias = ActivationAccepted | ActivationRejected


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


class _CheckpointStore(Protocol):
    def load(self, name: str) -> dict[str, object] | None: ...


class _PairFactory(Protocol):
    def migrate_legacy_descriptor(self, descriptor: GreyControlPairDescriptor) -> GreyControlPairDescriptor: ...
    def restore(self, descriptor: GreyControlPairDescriptor) -> OwnedMpcPair: ...
    def validate(self, pair: OwnedMpcPair) -> bool: ...
    def dry_solve(self, pair: OwnedMpcPair, *, temperature_c: float) -> TargetTimingEvidence: ...


class _PersistenceWorker(Protocol):
    def submit_activation_phase(
        self,
        record: PreparedActivationRecord,
        *,
        expected_phase: None,
    ) -> DurableActivationReceipt: ...
    def flush_and_stop(self, *, timeout: float) -> bool: ...


class _RollbackCommitter(Protocol):
    def __call__(
        self,
        decision: ModelEvidenceRecord,
        *,
        expected_activation: ModelActivationState,
    ) -> ModelRollbackCommitOutcome: ...


_INACTIVE_MANUAL_CALIBRATION = CalibrationDecision(False, 0.0, None, CalibrationProgress())


def _inactive_manual_calibration(
    _baseline_q: float,
    _temperature_c: float,
    _forecast: TemperatureForecast,
) -> CalibrationDecision:
    return _INACTIVE_MANUAL_CALIBRATION


def _default_report() -> LearningReport:
    report, _records = backend_learning_report()
    return report


def _default_pair_factory() -> MpcPairFactory:
    settings = read_settings()
    controller = settings.get("controller")
    selected = controller.get("selected") if isinstance(controller, Mapping) else None
    controller_config = controller.get("config") if isinstance(controller, Mapping) else None
    configured = (
        controller_config.get(selected)
        if isinstance(controller_config, Mapping) and isinstance(selected, str)
        else None
    )
    if selected != "mpc" or not isinstance(configured, Mapping):
        raise ValueError("MPC must be the selected controller")
    cycle_data = settings.get("cycle_data")
    globals_config = settings.get("globals")
    units = globals_config.get("units") if isinstance(globals_config, Mapping) else None
    if not isinstance(cycle_data, Mapping) or not isinstance(units, str):
        raise ValueError("controller configuration is incomplete")
    return MpcPairFactory(
        configured,
        units,
        cycle_data,
        advance_calibration=_inactive_manual_calibration,
        model_authority=lambda: (0, None),
        on_policy_failure=lambda _error: None,
    )


def _activation_rejection(category: ActivationRejectionCategory, reason: str) -> ActivationRejected:
    return ActivationRejected(category, reason)

def _with_cleanup_failure(rejection: ActivationRejected) -> ActivationRejected:
    return replace(rejection, cleanup_failed=True)


def _projection_rejection(error: Exception) -> ActivationRejected:
    category = (
        ActivationRejectionCategory.INVALID_DATA
        if isinstance(error, (KeyError, TypeError))
        else ActivationRejectionCategory.CONFLICT
    )
    return _activation_rejection(category, str(error))


def _decision_rejection(decision: ActivationDecision[OwnedMpcPair]) -> ActivationRejected:
    reason = decision.reason
    category = (
        ActivationRejectionCategory.PERSISTENCE_UNAVAILABLE
        if reason.startswith("activation-persistence")
        else ActivationRejectionCategory.CONFLICT
    )
    return _activation_rejection(category, reason)


def _close_pair(pair: OwnedMpcPair | None) -> bool:
    if pair is None:
        return True
    try:
        pair.close()
    except Exception:
        return False
    return pair.closed and pair.core.close_complete


def _stop_worker(worker: _PersistenceWorker | None) -> bool:
    if worker is None:
        return True
    try:
        return worker.flush_and_stop(timeout=2.0) is True
    except Exception:
        return False


class ModelActivationService:
    """Prepare reviewed models and persist rollback evidence off the Flask layer."""

    def __init__(
        self,
        *,
        report_provider: Callable[[], LearningReport] = _default_report,
        checkpoint_store: _CheckpointStore | None = None,
        pair_factory_provider: Callable[[], _PairFactory] = _default_pair_factory,
        persistence_worker_provider: Callable[[], _PersistenceWorker] | None = None,
        activation_reader: Callable[[], ModelActivationState | None] = read_model_activation,
        rollback_committer: _RollbackCommitter = commit_model_rollback,
    ) -> None:
        self._report_provider = report_provider
        self._checkpoint_store = checkpoint_store or ControllerModelStore()
        self._pair_factory_provider = pair_factory_provider
        self._persistence_worker_provider = (
            persistence_worker_provider
            if persistence_worker_provider is not None
            else lambda: ModelPersistenceWorker(self._checkpoint_store, logging.getLogger("control"))
        )
        self._activation_reader = activation_reader
        self._rollback_committer = rollback_committer

    def activate(self, request: ModelActivationRequest, *, now_ms: int) -> ActivationOutcome:
        try:
            projection = self._report_provider().to_dict()
            if projection["status"] != "ready-for-review":
                raise ValueError("confidence decision is not ready-for-review")
            candidate_projection = projection["candidate"]
            if not isinstance(candidate_projection, Mapping):
                raise TypeError("candidate report must be an object")
            if candidate_projection["digest"] != request.candidate_digest:
                raise ValueError("candidate-digest-changed")
            if candidate_projection.get("policy") != ActivationPolicy.OPERATOR_REVIEWED.value:
                raise ValueError("manual activation requires operator-reviewed policy")
            if projection["decision_id"] != request.decision_id:
                raise ValueError("stale-confidence-decision")
        except (KeyError, TypeError, ValueError) as error:
            return _projection_rejection(error)
        except Exception as error:
            return _activation_rejection(
                ActivationRejectionCategory.PERSISTENCE_UNAVAILABLE,
                f"activation-report-failed: {error}",
            )

        try:
            checkpoint = self._checkpoint_store.load("mpc")
        except (KeyError, TypeError, ValueError) as error:
            return _projection_rejection(error)
        except Exception as error:
            return _activation_rejection(
                ActivationRejectionCategory.PERSISTENCE_UNAVAILABLE,
                f"activation-checkpoint-failed: {error}",
            )
        if not isinstance(checkpoint, dict):
            return _activation_rejection(
                ActivationRejectionCategory.CONFLICT,
                "candidate-snapshot-not-found",
            )
        incumbent_value = checkpoint.get("active_pair")
        candidate_value = checkpoint.get("candidate_pair")
        if not isinstance(incumbent_value, Mapping) or not isinstance(candidate_value, Mapping):
            return _activation_rejection(
                ActivationRejectionCategory.CONFLICT,
                "candidate-pair-not-found",
            )

        try:
            pair_factory = self._pair_factory_provider()
            incumbent = pair_factory.migrate_legacy_descriptor(
                GreyControlPairDescriptor.from_dict(incumbent_value)
            )
            candidate = pair_factory.migrate_legacy_descriptor(
                GreyControlPairDescriptor.from_dict(candidate_value)
            )
            if candidate.model_digest != request.candidate_digest:
                raise ValueError("candidate-digest-changed")
        except (KeyError, TypeError) as error:
            return _projection_rejection(error)
        except Exception as error:
            return _activation_rejection(ActivationRejectionCategory.CONFLICT, str(error))

        try:
            worker = self._persistence_worker_provider()
        except Exception:
            return _activation_rejection(
                ActivationRejectionCategory.PERSISTENCE_UNAVAILABLE,
                "activation-persistence-failed",
            )

        incumbent_owner: OwnedMpcPair | None = None
        candidate_owner: OwnedMpcPair | None = None
        receipt: DurableActivationReceipt | None = None
        domain_outcome: ActivationOutcome
        try:
            try:
                incumbent_owner = pair_factory.restore(incumbent)
            except Exception as error:
                domain_outcome = _activation_rejection(
                    ActivationRejectionCategory.CONFLICT,
                    str(error),
                )
            else:
                def build_candidate(descriptor: GreyControlPairDescriptor) -> OwnedMpcPair:
                    nonlocal candidate_owner
                    candidate_owner = pair_factory.restore(descriptor)
                    return candidate_owner

                def persist_prepared(record: PreparedActivationRecord) -> DurableActivationReceipt:
                    nonlocal receipt
                    receipt = worker.submit_activation_phase(record, expected_phase=None)
                    return receipt

                manager = ActivationManager(
                    incumbent_pair=incumbent_owner,
                    build_candidate=build_candidate,
                    validate_candidate=pair_factory.validate,
                    native_dry_solve=lambda pair: pair_factory.dry_solve(
                        pair,
                        temperature_c=float(pair.solver.config.T_amb),
                    ).accepted
                    is True,
                    persist_prepared=persist_prepared,
                    clock_ms=lambda: now_ms,
                    receipt_timeout=2.0,
                )
                decision = manager.prepare(
                    request,
                    candidate,
                    origin=CandidateOrigin.OPERATOR_CALIBRATION,
                    policy=ActivationPolicy.OPERATOR_REVIEWED,
                )
                if not decision.accepted:
                    domain_outcome = _decision_rejection(decision)
                elif receipt is None or receipt.completed is not True:
                    domain_outcome = _activation_rejection(
                        ActivationRejectionCategory.PERSISTENCE_UNAVAILABLE,
                        "activation-persistence-not-durable",
                    )
                elif decision.record is None:
                    domain_outcome = _activation_rejection(
                        ActivationRejectionCategory.PERSISTENCE_UNAVAILABLE,
                        "activation-persistence-failed",
                    )
                else:
                    record = decision.record
                    domain_outcome = ActivationAccepted(
                        transaction_id=record.transaction_id,
                        decision_id=record.decision_id,
                        candidate_digest=record.candidate.model_digest,
                        role_generation=record.candidate.role_generation,
                    )
        finally:
            candidate_closed = _close_pair(candidate_owner)
            incumbent_closed = _close_pair(incumbent_owner)
            worker_stopped = _stop_worker(worker)

        cleanup_failed = not candidate_closed or not incumbent_closed or not worker_stopped
        if isinstance(domain_outcome, ActivationRejected):
            return _with_cleanup_failure(domain_outcome) if cleanup_failed else domain_outcome
        if cleanup_failed:
            return _with_cleanup_failure(
                _activation_rejection(
                    ActivationRejectionCategory.CLEANUP_FAILED,
                    "activation-cleanup-failed",
                )
            )
        return domain_outcome

    def rollback(self, request: ModelRollbackRequest, *, now_ms: int) -> RollbackOutcome:
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
        except (TypeError, ValueError):
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
