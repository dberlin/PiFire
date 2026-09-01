"""Durable activation state, persistence sequencing, and pair ownership."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from common.model_evidence import (
    ActivationLifecycleEvidence,
    CandidateAssessmentEvidence,
    ConfidenceDecisionEvidence,
    EvidenceKind,
    FallbackEvidence,
    LearningFailureEvidence,
    ModelEvidenceRecord,
    RollbackEvidence,
)
from common.persistence.model_evidence import ModelActivationState
from controller.model_learning.activation import (
    ActivationPhase,
    PreparedActivationRecord,
    recover_startup_activation,
)
from controller.mpc_factory import MpcPairFactory, OwnedMpcPair
from controller.runtime.model_persistence import (
    DurableActivationReceipt,
    ModelPersistenceWorker,
)


@dataclass(frozen=True, slots=True)
class _PendingActivation:
    record: PreparedActivationRecord
    candidate_pair: OwnedMpcPair
    prepared_receipt: DurableActivationReceipt


@dataclass(frozen=True, slots=True)
class _PendingAbort:
    record: PreparedActivationRecord
    receipt: DurableActivationReceipt | None


@dataclass(frozen=True, slots=True)
class _ActivationFlight:
    pending: _PendingActivation
    phase: ActivationPhase
    receipt: DurableActivationReceipt


class ActivationRuntime:
    """Own activation pairs and optionally the persistence lifetime."""

    def __init__(
        self,
        pair_factory: MpcPairFactory,
        active_pair: OwnedMpcPair,
        persistence: ModelPersistenceWorker,
        *,
        owns_persistence: bool = False,
        clock_ms: Callable[[], int] | None = None,
        receipt_timeout: float = 2.0,
    ) -> None:
        if not isinstance(pair_factory, MpcPairFactory):
            raise TypeError("pair_factory must be an MpcPairFactory")
        if not isinstance(active_pair, OwnedMpcPair):
            raise TypeError("active_pair must be an OwnedMpcPair")
        if not active_pair.authorized:
            raise ValueError("initial activation pair must be authorized")
        if not isinstance(persistence, ModelPersistenceWorker):
            raise TypeError("persistence must be a ModelPersistenceWorker")
        if not isinstance(receipt_timeout, (int, float)) or isinstance(receipt_timeout, bool) or receipt_timeout < 0:
            raise ValueError("receipt_timeout must be nonnegative")
        if not isinstance(owns_persistence, bool):
            raise TypeError("owns_persistence must be a bool")
        self._pair_factory = pair_factory
        self._persistence = persistence
        self._owns_persistence = owns_persistence
        self._persistence_close_pending = owns_persistence
        self._clock_ms = (lambda: time.time_ns() // 1_000_000) if clock_ms is None else clock_ms
        self._receipt_timeout = float(receipt_timeout)
        self._lock = threading.RLock()
        self._active_pair = active_pair
        self._rollback_pair: OwnedMpcPair | None = None
        self._inert_record: PreparedActivationRecord | None = None
        self._active_record: PreparedActivationRecord | None = None
        self._pending: _PendingActivation | None = None
        self._flight: _ActivationFlight | None = None
        self._retired_pairs: list[OwnedMpcPair] = []
        self._transaction_ids: dict[str, PreparedActivationRecord] = {}
        self._pending_aborts: dict[str, _PendingAbort] = {}
        self._confidence_receipts: dict[str, DurableActivationReceipt] = {}
        self._persisted_decision_ids: set[str] = set()
        self._failed_role_generations: set[int] = set()
        self._events: deque[ModelEvidenceRecord] = deque()
        self._terminated_reason: str | None = None
        self._role_generation = active_pair.descriptor.role_generation
        self._estimator_seed_source: Callable[[float, int], object] | None = None
        self._last_seed_refresh_status: str | None = None
        self._closed = False

    @property
    def active_pair(self) -> OwnedMpcPair:
        with self._lock:
            return self._active_pair

    @property
    def rollback_pair(self) -> OwnedMpcPair | None:
        with self._lock:
            return self._rollback_pair

    @property
    def active_record(self) -> PreparedActivationRecord | None:
        with self._lock:
            return self._active_record

    @property
    def inert_record(self) -> PreparedActivationRecord | None:
        with self._lock:
            return self._inert_record

    @property
    def activation_pending(self) -> bool:
        with self._lock:
            return self._pending is not None or self._flight is not None

    @property
    def output_authorized(self) -> bool:
        with self._lock:
            return self._active_pair.authorized

    @property
    def failed_role_generations(self) -> frozenset[int]:
        with self._lock:
            return frozenset(self._failed_role_generations)

    @property
    def role_generation(self) -> int:
        with self._lock:
            return self._role_generation

    @property
    def pending_abort_count(self) -> int:
        """Return the number of fenced PREPARED owners awaiting durable ABORTED."""
        with self._lock:
            return len(self._pending_aborts)

    @property
    def activation_terminated(self) -> bool:
        with self._lock:
            return self._terminated_reason is not None

    @property
    def terminated_reason(self) -> str | None:
        with self._lock:
            return self._terminated_reason

    def bind_estimator_seed_source(
        self,
        source: Callable[[float, int], object] | None,
    ) -> None:
        if source is not None and not callable(source):
            raise TypeError("estimator seed source must be callable")
        with self._lock:
            self._estimator_seed_source = source

    def _refresh_pair_seed(self, pair: OwnedMpcPair) -> bool:
        source = self._estimator_seed_source
        if source is None:
            status = getattr(pair.core, "estimator_seed_status", None)
            self._last_seed_refresh_status = status
            return status == "exact"
        try:
            theta, n_delay = pair.core.estimator_seed_requirements()
            pair.core.seed_from_trajectory(source(theta, n_delay))
        except Exception:
            self._last_seed_refresh_status = "uncertain"
            return False
        self._last_seed_refresh_status = getattr(
            pair.core,
            "estimator_seed_status",
            None,
        )
        return self._last_seed_refresh_status is not None
    @property
    def last_seed_refresh_status(self) -> str | None:
        with self._lock:
            return self._last_seed_refresh_status


    @staticmethod
    def _receipt_is_durable(receipt: DurableActivationReceipt) -> bool:
        return receipt.accepted and receipt.completed and receipt.durable

    @staticmethod
    def _copy_record(record: ModelEvidenceRecord) -> ModelEvidenceRecord:
        if not isinstance(record, ModelEvidenceRecord):
            raise TypeError("record must be a ModelEvidenceRecord")
        return ModelEvidenceRecord.model_validate_json(record.model_dump_json())

    def submit_activation_confidence(
        self,
        record: ModelEvidenceRecord,
        *,
        preceding_evidence: Sequence[ModelEvidenceRecord] = (),
    ) -> DurableActivationReceipt:
        owned = self._copy_record(record)
        if owned.kind is not EvidenceKind.CONFIDENCE_DECISION or not isinstance(
            owned.payload, ConfidenceDecisionEvidence
        ):
            raise TypeError("activation confidence must be confidence-decision evidence")
        if not isinstance(preceding_evidence, Sequence) or isinstance(
            preceding_evidence,
            (str, bytes),
        ):
            raise TypeError("preceding_evidence must be a sequence of ModelEvidenceRecord")
        owned_preceding = tuple(self._copy_record(preceding) for preceding in preceding_evidence)
        if any(
            preceding.kind is not EvidenceKind.CANDIDATE_ASSESSMENT
            or not isinstance(
                preceding.payload,
                CandidateAssessmentEvidence,
            )
            for preceding in owned_preceding
        ):
            raise ValueError("preceding_evidence requires candidate-assessment evidence")
        if any(
            preceding.payload.decision_id != owned.payload.decision_id
            for preceding in owned_preceding
            if isinstance(preceding.payload, CandidateAssessmentEvidence)
        ):
            raise ValueError("preceding candidate-assessment decision_id must match confidence")
        with self._lock:
            if self._closed:
                return DurableActivationReceipt(accepted=False)
            receipt = self._confidence_receipts.get(owned.evidence_id)
            if receipt is not None:
                return receipt
            receipt = (
                self._persistence.submit_activation_confidence(
                    owned,
                    preceding_evidence=owned_preceding,
                )
                if owned_preceding
                else self._persistence.submit_activation_confidence(owned)
            )
            if receipt.accepted:
                self._confidence_receipts[owned.evidence_id] = receipt
            return receipt

    def submit_evidence(self, record: ModelEvidenceRecord) -> bool:
        owned = self._copy_record(record)
        with self._lock:
            return not self._closed and self._persistence.submit_evidence(owned).accepted

    def submit_prepared_phase(
        self,
        record: PreparedActivationRecord,
    ) -> DurableActivationReceipt:
        if not isinstance(record, PreparedActivationRecord):
            raise TypeError("record must be a PreparedActivationRecord")
        if record.phase is not ActivationPhase.PREPARED:
            raise ValueError("prepared activation phase required")
        with self._lock:
            if self._closed:
                return DurableActivationReceipt(accepted=False)
            return self._persistence.submit_activation_phase(
                record,
                expected_phase=None,
            )

    def mark_confidence_persisted(self, decision_id: str) -> None:
        normalized = self._reason(decision_id, "activation confidence decision_id")
        with self._lock:
            self._persisted_decision_ids.add(normalized)

    def consume_confidence_persisted(self, decision_id: str) -> bool:
        normalized = self._reason(decision_id, "activation confidence decision_id")
        with self._lock:
            if normalized not in self._persisted_decision_ids:
                return False
            self._persisted_decision_ids.remove(normalized)
            return True

    def confidence_persisted(self, decision_id: str) -> bool:
        normalized = self._reason(decision_id, "activation confidence decision_id")
        with self._lock:
            return normalized in self._persisted_decision_ids

    def queue_prepared_activation(
        self,
        record: PreparedActivationRecord,
        candidate_pair: OwnedMpcPair,
        prepared_receipt: DurableActivationReceipt,
    ) -> bool:
        if not isinstance(record, PreparedActivationRecord):
            raise TypeError("record must be a PreparedActivationRecord")
        if not isinstance(candidate_pair, OwnedMpcPair):
            raise TypeError("candidate_pair must be an OwnedMpcPair")
        if not isinstance(prepared_receipt, DurableActivationReceipt):
            raise TypeError("prepared_receipt must be a DurableActivationReceipt")
        with self._lock:
            if self._closed or self._terminated_reason is not None:
                return False
            known = self._transaction_ids.get(record.transaction_id)
            if known is not None:
                if known != record:
                    candidate_pair.close()
                    return False
                owned_pair = (
                    (self._pending is not None and self._pending.candidate_pair is candidate_pair)
                    or (self._flight is not None and self._flight.pending.candidate_pair is candidate_pair)
                    or (
                        self._active_pair is candidate_pair
                        and (
                            self._inert_record is not None
                            and self._inert_record.transaction_id == record.transaction_id
                            or self._active_record is not None
                            and self._active_record.transaction_id == record.transaction_id
                        )
                    )
                )
                if not owned_pair:
                    candidate_pair.close()
                return True
            if (
                record.phase is not ActivationPhase.PREPARED
                or candidate_pair.closed
                or candidate_pair.descriptor != record.candidate
                or record.incumbent != self._active_pair.descriptor
                or record.candidate.role_generation <= self._active_pair.descriptor.role_generation
                or record.candidate.role_generation in self._failed_role_generations
                or self._pending is not None
                or self._flight is not None
                or self._inert_record is not None
                or not self._receipt_is_durable(prepared_receipt)
            ):
                return False
            candidate_pair.revoke_output()
            self._pending = _PendingActivation(record, candidate_pair, prepared_receipt)
            self._transaction_ids[record.transaction_id] = record
            return True

    def abort_prepared_activation(
        self,
        record: PreparedActivationRecord,
        reason: str,
    ) -> bool:
        """Fence, close, and durably abort one exact owned PREPARED transition."""
        if not isinstance(record, PreparedActivationRecord):
            raise TypeError("record must be a PreparedActivationRecord")
        normalized_reason = self._reason(reason, "activation abort reason")
        aborted = record.transition(
            ActivationPhase.ABORTED,
            reason=normalized_reason,
        )
        with self._lock:
            known = self._transaction_ids.get(record.transaction_id)
            if known is not None and known.phase is ActivationPhase.ABORTED:
                if known != aborted:
                    return False
                if record.transaction_id not in self._pending_aborts:
                    return True
            pending = self._pending
            if pending is not None and pending.record == record:
                self._pending = None
                try:
                    pending.candidate_pair.close()
                finally:
                    self._failed_role_generations.add(record.candidate.role_generation)
                    self._transaction_ids[record.transaction_id] = aborted
                    self._pending_aborts[record.transaction_id] = _PendingAbort(
                        aborted,
                        None,
                    )
            elif record.transaction_id not in self._pending_aborts:
                return False
            return self._retry_pending_aborts_locked(
                transaction_id=record.transaction_id,
                wait_for_completion=True,
            )

    def retry_pending_aborts(self) -> bool:
        """Retry every fenced durable abort without releasing its transaction."""
        with self._lock:
            if self._closed:
                return not self._pending_aborts
            return self._retry_pending_aborts_locked(wait_for_completion=True)

    def _retry_pending_aborts_locked(
        self,
        *,
        transaction_id: str | None = None,
        wait_for_completion: bool,
    ) -> bool:
        pending_aborts = tuple(self._pending_aborts.items())
        for pending_id, pending_abort in pending_aborts:
            if transaction_id is not None and pending_id != transaction_id:
                continue
            receipt = pending_abort.receipt
            if receipt is None:
                try:
                    receipt = self._persistence.submit_activation_phase(
                        pending_abort.record,
                        expected_phase=ActivationPhase.PREPARED,
                    )
                except Exception:
                    continue
                if not receipt.accepted:
                    continue
                self._pending_aborts[pending_id] = _PendingAbort(
                    pending_abort.record,
                    receipt,
                )
            completed = receipt.completed and receipt.durable
            if wait_for_completion and not receipt.completed:
                try:
                    completed = receipt.wait(self._receipt_timeout)
                except Exception:
                    if receipt.completed:
                        self._pending_aborts[pending_id] = _PendingAbort(
                            pending_abort.record,
                            None,
                        )
                    continue
            if completed is True and self._receipt_is_durable(receipt):
                self._pending_aborts.pop(pending_id, None)
            elif receipt.completed:
                self._pending_aborts[pending_id] = _PendingAbort(
                    pending_abort.record,
                    None,
                )
        if transaction_id is None:
            return not self._pending_aborts
        return transaction_id not in self._pending_aborts

    def _submit_aborted(
        self,
        pending: _PendingActivation,
        reason: str,
    ) -> bool:
        aborted = pending.record.transition(ActivationPhase.ABORTED, reason=reason)
        try:
            receipt = self._persistence.submit_activation_phase(
                aborted,
                expected_phase=ActivationPhase.PREPARED,
            )
        except Exception:
            self._terminate_locked("activation-abort-persistence-failed")
            return False
        if not receipt.accepted:
            self._terminate_locked("activation-abort-persistence-failed")
            return False
        self._flight = _ActivationFlight(pending, ActivationPhase.ABORTED, receipt)
        return False

    def _compensate_locked(
        self,
        pending: _PendingActivation,
        reason: str,
    ) -> bool:
        if self._active_pair is pending.candidate_pair:
            if not self.compensate_candidate_pair(
                pending.candidate_pair,
                pending.record,
                reason,
            ):
                if self._terminated_reason is None:
                    self._terminate_locked("activation-compensation-failed")
                return False
        else:
            try:
                pending.candidate_pair.close()
            except Exception:
                self._terminate_locked("activation-compensation-failed")
                return False
        return self._submit_aborted(pending, reason)

    def advance_activation(self) -> bool:
        """Advance activation without waiting; true alone permits the next solve."""
        with self._lock:
            if self._closed:
                return False
            if not self._retry_pending_aborts_locked(wait_for_completion=False):
                return False
            if self._terminated_reason is not None:
                return False
            flight = self._flight
            if flight is not None:
                if not flight.receipt.completed:
                    return False
                if flight.phase is ActivationPhase.ACTIVE:
                    if not self._receipt_is_durable(flight.receipt):
                        reason = (
                            "activation-confidence-changed"
                            if flight.receipt.error is not None
                            and "activation-authority-changed" in str(flight.receipt.error)
                            else "active-persistence-failed"
                        )
                        self._flight = None
                        return self._compensate_locked(flight.pending, reason)
                    active = flight.pending.record.transition(ActivationPhase.ACTIVE)
                    if not self.authorize_candidate_pair(active):
                        if self._terminated_reason is None:
                            self._terminate_locked("active-authorization-failed")
                        return False
                elif not self._receipt_is_durable(flight.receipt):
                    self._terminate_locked("activation-abort-persistence-failed")
                    return False
                self._flight = None
                return True

            pending = self._pending
            if pending is None:
                return True
            self._pending = None
            if not self.install_candidate_pair_inert(
                pending.candidate_pair,
                pending.record,
            ):
                return self._compensate_locked(pending, "candidate-install-failed")
            try:
                receipt = self._persistence.submit_activation_phase(
                    pending.record.transition(ActivationPhase.ACTIVE),
                    expected_phase=ActivationPhase.PREPARED,
                )
            except Exception:
                return self._compensate_locked(pending, "active-persistence-failed")
            if not receipt.accepted:
                return self._compensate_locked(pending, "active-persistence-failed")
            self._flight = _ActivationFlight(pending, ActivationPhase.ACTIVE, receipt)
            return False

    def install_candidate_pair_inert(
        self,
        pair: OwnedMpcPair,
        record: PreparedActivationRecord,
    ) -> bool:
        with self._lock:
            if (
                self._closed
                or self._terminated_reason is not None
                or not isinstance(pair, OwnedMpcPair)
                or not isinstance(record, PreparedActivationRecord)
                or record.phase is not ActivationPhase.PREPARED
                or pair.closed
                or pair.descriptor != record.candidate
                or self._active_pair.descriptor != record.incumbent
            ):
                return False
            if self._inert_record is not None:
                return self._inert_record.transaction_id == record.transaction_id
            if getattr(pair.core, "estimator_seed_status", None) != "exact":
                source = self._estimator_seed_source
                if source is None:
                    return False
                try:
                    theta, n_delay = pair.core.estimator_seed_requirements()
                    pair.core.seed_from_trajectory(source(theta, n_delay))
                except Exception:
                    return False
                if getattr(pair.core, "estimator_seed_status", None) != "exact":
                    return False
            displaced = self._rollback_pair
            if displaced is not None:
                try:
                    displaced.close()
                except Exception:
                    return False
            incumbent = self._active_pair
            try:
                pair.core.adopt_model_independent_state(
                    incumbent.core.capture_model_independent_state()
                )
            except Exception:
                return False
            pair.revoke_output()
            incumbent.revoke_output()
            self._rollback_pair = incumbent
            self._active_pair = pair
            self._inert_record = record
            return True

    def authorize_candidate_pair(self, record: PreparedActivationRecord) -> bool:
        with self._lock:
            prepared = self._inert_record
            if (
                self._closed
                or self._terminated_reason is not None
                or prepared is None
                or not isinstance(record, PreparedActivationRecord)
                or record.phase is not ActivationPhase.ACTIVE
                or record.transaction_id != prepared.transaction_id
                or record.candidate != self._active_pair.descriptor
            ):
                return False
            try:
                self._active_pair.authorize_output()
            except Exception:
                return False
            self._role_generation = max(
                self._role_generation,
                record.candidate.role_generation,
            )
            self._active_record = record
            self._inert_record = None
            if not self._submit_lifecycle(record, phase="active", reason=None):
                self._terminate_locked("activation-lifecycle-persistence-failed")
                return False
            return True

    def compensate_candidate_pair(
        self,
        pair: OwnedMpcPair,
        record: PreparedActivationRecord,
        reason: str,
    ) -> bool:
        with self._lock:
            rollback = self._rollback_pair
            if (
                rollback is None
                or pair is not self._active_pair
                or pair.descriptor != record.candidate
                or rollback.descriptor != record.incumbent
            ):
                return False
            try:
                rollback.core.adopt_model_independent_state(
                    pair.core.capture_model_independent_state()
                )
            except Exception:
                return False
            if not self._refresh_pair_seed(rollback):
                return False
            pair.revoke_output()
            try:
                rollback.authorize_output()
            except Exception:
                return False
            self._active_pair = rollback
            self._rollback_pair = None
            self._inert_record = None
            self._active_record = None
            self._role_generation = max(
                self._role_generation,
                record.candidate.role_generation + 1,
            )
            if not self._submit_lifecycle(record, phase="aborted", reason=reason):
                self._terminate_locked("activation-lifecycle-persistence-failed")
                return False
            try:
                pair.close()
            except Exception:
                return False
            return True

    def _submit_lifecycle(
        self,
        record: PreparedActivationRecord,
        *,
        phase: str,
        reason: str | None,
    ) -> bool:
        timestamp_ms = self._clock_ms()
        lifecycle = ModelEvidenceRecord(
            evidence_id=(
                f"mpc-runtime-activation:activation-lifecycle:{timestamp_ms}:{record.candidate.role_generation}:{phase}"
            ),
            kind=EvidenceKind.ACTIVATION_LIFECYCLE,
            session_id="mpc-runtime-activation",
            cook_id=None,
            timestamp_ms=timestamp_ms,
            role_generation=record.candidate.role_generation,
            model_digest=record.candidate.model_digest,
            provenance_digest=record.incumbent.model_digest,
            payload=ActivationLifecycleEvidence(
                decision_id=record.decision_id,
                phase=phase,
                origin=record.origin.value,
                policy=record.policy.value,
                reason=reason,
            ),
        )
        try:
            return self._persistence.submit_evidence(lifecycle).accepted
        except Exception:
            return False

    def terminate(self, reason: str) -> None:
        normalized = self._reason(reason, "activation termination reason")
        with self._lock:
            self._terminate_locked(normalized)

    def _terminate_locked(self, reason: str) -> None:
        self._active_pair.revoke_output()
        self._terminated_reason = reason

    @staticmethod
    def _reason(reason: str, label: str) -> str:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"{label} must be non-blank")
        return reason.strip()

    def _restore_rollback(self, reason: str, *, emit_fallback: bool) -> bool:
        with self._lock:
            rollback = self._rollback_pair
            if (
                self._closed
                or self._terminated_reason is not None
                or rollback is None
                or not self._active_pair.authorized
            ):
                return False
            failed = self._active_pair
            active_record = self._active_record
            try:
                rollback.core.adopt_model_independent_state(
                    failed.core.capture_model_independent_state()
                )
            except Exception:
                return False
            if not self._refresh_pair_seed(rollback):
                return False
            failed.revoke_output()
            try:
                rollback.authorize_output()
            except Exception:
                return False
            self._active_pair = rollback
            self._rollback_pair = None
            self._failed_role_generations.add(failed.descriptor.role_generation)
            self._role_generation = max(
                self._role_generation,
                failed.descriptor.role_generation + 1,
            )
            try:
                failed.close()
            except Exception:
                self._terminate_locked("rollback-close-failed")
                return False
            if emit_fallback:
                timestamp_ms = self._clock_ms()
                event = ModelEvidenceRecord(
                    evidence_id=(
                        f"fallback:{failed.descriptor.role_generation}:{timestamp_ms}:{failed.descriptor.model_digest}"
                    ),
                    kind=EvidenceKind.FALLBACK,
                    session_id="mpc-runtime-activation",
                    cook_id=None,
                    timestamp_ms=timestamp_ms,
                    role_generation=self._role_generation,
                    model_digest=failed.descriptor.model_digest,
                    provenance_digest=rollback.descriptor.model_digest,
                    payload=FallbackEvidence(
                        decision_id=(
                            active_record.decision_id if active_record is not None else "runtime-confidence-window"
                        ),
                        reason=reason,
                        failed_digest=failed.descriptor.model_digest,
                        failed_generation=failed.descriptor.role_generation,
                        last_safe_command=failed.core.last_combustion_load,
                        fallback_kind="grey-box",
                    ),
                )
                self._events.append(event)
                failure = ModelEvidenceRecord(
                    evidence_id=(
                        f"mpc-runtime-activation:learning-failure:{timestamp_ms}:{failed.descriptor.role_generation}"
                    ),
                    kind=EvidenceKind.LEARNING_FAILURE,
                    session_id="mpc-runtime-activation",
                    cook_id=None,
                    timestamp_ms=timestamp_ms,
                    role_generation=failed.descriptor.role_generation,
                    model_digest=failed.descriptor.model_digest,
                    provenance_digest=rollback.descriptor.model_digest,
                    payload=LearningFailureEvidence(
                        code="activation-terminal",
                        detail=reason,
                        terminal=True,
                    ),
                )
                try:
                    accepted = self._persistence.submit_evidence(failure).accepted
                except Exception:
                    accepted = False
                if not accepted:
                    self._terminate_locked("activation-lifecycle-persistence-failed")
                    return False
            self._active_record = None
            self._inert_record = None
            return True

    def activation_runtime_failure(self, reason: str) -> bool:
        return self._restore_rollback(
            self._reason(reason, "activation fallback reason"),
            emit_fallback=True,
        )

    def rollback_activation(self, reason: str) -> bool:
        return self._restore_rollback(
            self._reason(reason, "activation rollback reason"),
            emit_fallback=False,
        )

    def drain_activation_events(self) -> tuple[ModelEvidenceRecord, ...]:
        with self._lock:
            events = tuple(self._events)
            self._events.clear()
            return events

    def restore_activation(
        self,
        persisted: ModelActivationState,
        records: Sequence[ModelEvidenceRecord],
    ) -> bool:
        if not isinstance(persisted, ModelActivationState):
            raise TypeError("persisted must be a ModelActivationState")
        owned_records = tuple(self._copy_record(record) for record in records)
        with self._lock:
            if self._closed or self._terminated_reason is not None:
                return False
            known = self._transaction_ids.get(persisted.transaction_id)
            if known is not None:
                return True
            restored: OwnedMpcPair | None = None
            rollback: OwnedMpcPair | None = None
            try:
                recovery = recover_startup_activation(
                    persisted,
                    persist_aborted=lambda record: self._persistence.submit_activation_phase(
                        record,
                        expected_phase=ActivationPhase.PREPARED,
                    ),
                    receipt_timeout=self._receipt_timeout,
                )
                candidate_digests = {
                    recovery.record.candidate.model_digest,
                    recovery.source_candidate_digest,
                }
                lifecycle = max(
                    (
                        record
                        for record in owned_records
                        if (
                            isinstance(record.payload, RollbackEvidence)
                            and record.payload.decision_id == recovery.record.decision_id
                            and record.model_digest in candidate_digests
                        )
                        or (
                            isinstance(record.payload, FallbackEvidence)
                            and record.payload.failed_digest in candidate_digests
                            and record.payload.failed_generation == recovery.record.candidate.role_generation
                        )
                    ),
                    key=lambda record: (record.timestamp_ms, record.evidence_id),
                    default=None,
                )
                restore_descriptor = recovery.rollback if lifecycle is not None else recovery.restore
                restored = self._pair_factory.restore(restore_descriptor)
                rollback = (
                    self._pair_factory.restore(recovery.rollback)
                    if recovery.phase is ActivationPhase.ACTIVE and lifecycle is None
                    else None
                )
            except Exception:
                for pair in (rollback, restored):
                    if pair is not None:
                        pair.close()
                return False
            try:
                restored.core.adopt_model_independent_state(
                    self._active_pair.core.capture_model_independent_state()
                )
            except Exception:
                restored.close()
                if rollback is not None:
                    rollback.close()
                return False
            restored.authorize_output()
            retired_active = self._active_pair
            retired_rollback = self._rollback_pair
            self._active_pair = restored
            self._rollback_pair = rollback
            self._inert_record = None
            self._active_record = (
                recovery.record if recovery.phase is ActivationPhase.ACTIVE and lifecycle is None else None
            )
            restored_generation = recovery.restore.role_generation
            if lifecycle is not None:
                restored_generation = lifecycle.role_generation
                self._failed_role_generations.add(recovery.record.candidate.role_generation)
            self._role_generation = restored_generation
            self._transaction_ids[recovery.record.transaction_id] = recovery.record
            try:
                retired_active.close()
                if retired_rollback is not None:
                    retired_rollback.close()
            except Exception:
                self._terminate_locked("activation-restore-retirement-failed")
                return False
            return True

    def replace_active_pair(
        self,
        pair: OwnedMpcPair,
        *,
        retain_current: bool,
    ) -> None:
        if not isinstance(pair, OwnedMpcPair):
            raise TypeError("pair must be an OwnedMpcPair")
        with self._lock:
            if self._closed:
                pair.close()
                raise RuntimeError("activation runtime is closed")
            current = self._active_pair
            if pair is current:
                raise ValueError("replacement pair must be a distinct owner")
            pair.core.adopt_model_independent_state(
                current.core.capture_model_independent_state()
            )
            pair.revoke_output()
            displaced_rollback = self._rollback_pair
            pending = self._pending
            flight = self._flight
            retirees: list[tuple[str, OwnedMpcPair]] = []
            retired_ids: set[int] = set()
            for slot, retired in (
                ("rollback", displaced_rollback),
                ("pending", None if pending is None else pending.candidate_pair),
                ("flight", None if flight is None else flight.pending.candidate_pair),
            ):
                if retired is None or retired is current or retired is pair or id(retired) in retired_ids:
                    continue
                retired_ids.add(id(retired))
                retirees.append((slot, retired))

            def detach_retired(slot: str, retired: OwnedMpcPair) -> None:
                if slot == "rollback" and self._rollback_pair is retired:
                    self._rollback_pair = None
                if slot == "pending" and self._pending is not None and self._pending.candidate_pair is retired:
                    self._pending = None
                if slot == "flight" and self._flight is not None and self._flight.pending.candidate_pair is retired:
                    self._flight = None

            for slot, retired in retirees:
                try:
                    retired.close()
                except BaseException as error:
                    if retired.closed:
                        detach_retired(slot, retired)
                        self._retired_pairs.append(retired)
                    raise RuntimeError("could not retire displaced activation ownership") from error
                detach_retired(slot, retired)

            previous_rollback = self._rollback_pair
            previous_inert_record = self._inert_record
            previous_active_record = self._active_record
            previous_pending = self._pending
            previous_flight = self._flight
            previous_role_generation = self._role_generation
            current.revoke_output()
            self._active_pair = pair
            self._rollback_pair = current if retain_current else None
            self._inert_record = None
            self._active_record = None
            self._pending = None
            self._flight = None
            self._role_generation = pair.descriptor.role_generation
            try:
                pair.authorize_output()
            except BaseException:
                self._active_pair = current
                self._rollback_pair = previous_rollback
                self._inert_record = previous_inert_record
                self._active_record = previous_active_record
                self._pending = previous_pending
                self._flight = previous_flight
                self._role_generation = previous_role_generation
                current.authorize_output()
                raise
            if not retain_current:
                try:
                    current.close()
                except BaseException:
                    self._retired_pairs.append(current)

    def _close_owned_persistence_locked(self) -> BaseException | None:
        if not self._persistence_close_pending:
            return None
        try:
            if self._persistence.close(timeout=2.0) is not True:
                return RuntimeError("model persistence close timed out")
        except BaseException as error:
            return error
        self._persistence_close_pending = False
        return None


    def close(self) -> None:
        with self._lock:
            if self._closed:
                persistence_error = self._close_owned_persistence_locked()
                if persistence_error is not None:
                    raise RuntimeError(
                        "could not close complete activation runtime ownership"
                    ) from persistence_error
                return
            aborts_durable = self._retry_pending_aborts_locked(wait_for_completion=True)
            self._closed = True
            self._active_pair.revoke_output()
            pairs: list[OwnedMpcPair] = [self._active_pair]
            if self._rollback_pair is not None:
                pairs.append(self._rollback_pair)
            if self._pending is not None:
                pairs.append(self._pending.candidate_pair)
            if self._flight is not None:
                pairs.append(self._flight.pending.candidate_pair)
            self._rollback_pair = None
            self._pending = None
            pairs.extend(self._retired_pairs)
            self._flight = None
            errors: list[BaseException] = []
            if not aborts_durable:
                errors.append(RuntimeError("unresolved activation abort transactions remain"))
            persistence_error = self._close_owned_persistence_locked()
            if persistence_error is not None:
                errors.append(persistence_error)
            closed_ids: set[int] = set()
            self._retired_pairs.clear()
            for pair in pairs:
                if id(pair) in closed_ids:
                    continue
                closed_ids.add(id(pair))
                try:
                    pair.close()
                except BaseException as error:
                    errors.append(error)
            if errors:
                message = (
                    "unresolved activation abort transactions remain"
                    if not aborts_durable
                    else "could not close complete activation runtime ownership"
                )
                raise RuntimeError(message) from errors[0]
