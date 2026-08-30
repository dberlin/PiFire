"""One off-path worker for trajectory, checkpoint, evidence, and activation durability."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from threading import Condition, Thread
from typing import Protocol

from common.controller_model_state import CheckpointSaveOutcome, copy_valid_snapshot
from common.learning_trajectory import (
    HoldEntrySample,
    LearningTrajectoryFrame,
    LearningTrajectorySegment,
    TrajectoryBreakReason,
)
from common.model_evidence import (
    CandidateAssessmentEvidence,
    ConfidenceDecisionEvidence,
    EvidenceKind,
    ModelEvidenceRecord,
    RecorderGapEvidence,
    RefreshDiagnosticsEvidence,
)
from common.persistence.learning_trajectory import (
    LearningTrajectoryRepository,
    SegmentCursor,
)
from common.persistence.model_evidence import (
    append_model_evidence,
    append_model_evidence_in_transaction,
    commit_model_activation,
    commit_model_activation_phase,
)
from controller.model_learning.activation import ActivationPhase, PreparedActivationRecord


class _ModelStore(Protocol):
    def save_outcome(self, name: str, snapshot: dict[str, object]) -> CheckpointSaveOutcome: ...


class _ErrorLogger(Protocol):
    def error(self, message: str) -> None: ...


@dataclass(frozen=True, slots=True)
class EvidenceSubmission:
    """Immediate, explicit result of a nonblocking evidence submission."""

    accepted: bool
    recorder_gap: ModelEvidenceRecord | None = None


class DurableActivationReceipt:
    """Completion handle whose durability changes only after the worker transaction."""

    def __init__(self, *, accepted: bool) -> None:
        self.accepted = accepted
        self._condition = Condition()
        self._completed = not accepted
        self._durable = False
        self._error: str | None = "persistence-unavailable" if not accepted else None

    @property
    def completed(self) -> bool:
        with self._condition:
            return self._completed

    @property
    def durable(self) -> bool:
        with self._condition:
            return self._durable

    @property
    def error(self) -> str | None:
        with self._condition:
            return self._error

    def wait(self, timeout: float | None = None) -> bool:
        if timeout is not None and (not isinstance(timeout, (int, float)) or timeout < 0.0):
            raise ValueError("activation receipt timeout must be nonnegative")
        with self._condition:
            self._condition.wait_for(lambda: self._completed, timeout=timeout)
            return self._completed and self._durable

    def _complete(self, *, durable: bool, error: BaseException | None = None) -> None:
        with self._condition:
            if self._completed:
                return
            self._durable = durable
            self._error = None if error is None else f"{type(error).__name__}: {error}"
            self._completed = True
            self._condition.notify_all()


class DurableCheckpointReceipt:
    """Completion handle for one checkpoint save owned by the persistence worker."""

    def __init__(self, *, accepted: bool, error: str | None = None) -> None:
        self.accepted = accepted
        self._condition = Condition()
        self._completed = not accepted
        self._durable = False
        self._error = error if not accepted else None

    @property
    def completed(self) -> bool:
        with self._condition:
            return self._completed

    @property
    def durable(self) -> bool:
        with self._condition:
            return self._durable

    @property
    def error(self) -> str | None:
        with self._condition:
            return self._error

    def wait(self, timeout: float | None = None) -> bool:
        if timeout is not None and (
            not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout < 0.0
        ):
            raise ValueError("checkpoint receipt timeout must be nonnegative")
        with self._condition:
            self._condition.wait_for(lambda: self._completed, timeout=timeout)
            return self._completed and self._durable

    def _complete(
        self,
        *,
        durable: bool,
        error: BaseException | None = None,
    ) -> None:
        with self._condition:
            if self._completed:
                return
            self._durable = durable
            self._error = None if error is None else f"{type(error).__name__}: {error}"
            self._completed = True
            self._condition.notify_all()


@dataclass(frozen=True, slots=True)
class TrajectoryPersistenceGap:
    """Explicit continuity loss produced by a rejected trajectory work item."""

    reason: str
    break_reason: TrajectoryBreakReason = TrajectoryBreakReason.RECORDER_GAP


@dataclass(frozen=True, slots=True)
class TrajectoryAppendBatch:
    """One immutable trajectory mutation owned by the persistence queue."""

    cursor: SegmentCursor | None = None
    begin_segment: LearningTrajectorySegment | None = None
    pre_roll: tuple[LearningTrajectoryFrame, ...] = ()
    hold_entry: HoldEntrySample | None = None
    scored: tuple[LearningTrajectoryFrame, ...] = ()
    evidence: tuple[ModelEvidenceRecord, ...] = ()
    finalize_reason: TrajectoryBreakReason | None = None
    break_reason: TrajectoryBreakReason | None = None
    next_segment: LearningTrajectorySegment | None = None

    def __post_init__(self) -> None:
        if self.cursor is not None and not isinstance(self.cursor, SegmentCursor):
            raise TypeError("cursor must be a SegmentCursor")
        for label, values, expected_type in (
            ("pre_roll", self.pre_roll, LearningTrajectoryFrame),
            ("scored", self.scored, LearningTrajectoryFrame),
            ("evidence", self.evidence, ModelEvidenceRecord),
        ):
            if type(values) is not tuple:
                raise TypeError(f"{label} must be a tuple")
            if not all(isinstance(value, expected_type) for value in values):
                raise TypeError(f"{label} contains an invalid value")
        if self.hold_entry is not None and not isinstance(
            self.hold_entry,
            HoldEntrySample,
        ):
            raise TypeError("hold_entry must be a HoldEntrySample")
        for label, value in (
            ("finalize_reason", self.finalize_reason),
            ("break_reason", self.break_reason),
        ):
            if value is not None and not isinstance(value, TrajectoryBreakReason):
                raise TypeError(f"{label} must be a TrajectoryBreakReason")
        for label, value in (
            ("begin_segment", self.begin_segment),
            ("next_segment", self.next_segment),
        ):
            if value is not None and not isinstance(value, LearningTrajectorySegment):
                raise TypeError(f"{label} must be a LearningTrajectorySegment")
        append_payload = bool(self.pre_roll or self.hold_entry is not None or self.scored or self.evidence)
        if self.begin_segment is not None:
            if (
                self.cursor is not None
                or append_payload
                or self.finalize_reason is not None
                or self.break_reason is not None
                or self.next_segment is not None
            ):
                raise ValueError("segment begin must be its own work item")
        elif self.break_reason is not None or self.next_segment is not None:
            if (
                self.cursor is None
                or self.break_reason is None
                or self.next_segment is None
                or append_payload
                or self.finalize_reason is not None
            ):
                raise ValueError("break-and-begin requires cursor, reason, and next segment")
            if self.next_segment.segment_id == self.cursor.segment_id:
                raise ValueError("break-and-begin requires a new segment identity")
        elif self.finalize_reason is not None:
            if self.cursor is None or append_payload:
                raise ValueError("trajectory finalization must be its own work item")
        elif self.cursor is None or not append_payload:
            raise ValueError("trajectory append requires a cursor and durable work")
        if self.hold_entry is not None and not self.scored:
            raise ValueError("Hold-entry anchor requires scored frames")
        if self.cursor is not None:
            object.__setattr__(
                self,
                "cursor",
                SegmentCursor(
                    segment_id=self.cursor.segment_id,
                    next_ordinal=self.cursor.next_ordinal,
                    chain_digest=self.cursor.chain_digest,
                    corpus_revision=self.cursor.corpus_revision,
                ),
            )
        object.__setattr__(self, "begin_segment", deepcopy(self.begin_segment))
        object.__setattr__(self, "next_segment", deepcopy(self.next_segment))
        object.__setattr__(
            self,
            "pre_roll",
            tuple(deepcopy(frame) for frame in self.pre_roll),
        )
        object.__setattr__(self, "hold_entry", deepcopy(self.hold_entry))
        object.__setattr__(
            self,
            "scored",
            tuple(deepcopy(frame) for frame in self.scored),
        )
        object.__setattr__(
            self,
            "evidence",
            tuple(ModelEvidenceRecord.model_validate_json(record.model_dump_json()) for record in self.evidence),
        )


class PersistenceReceipt:
    """Completion, cursor, and continuity result for one trajectory work item."""

    def __init__(
        self,
        *,
        accepted: bool,
        error: str | None = None,
        gap: TrajectoryPersistenceGap | None = None,
        cursor: SegmentCursor | None = None,
    ) -> None:
        self.accepted = accepted
        self._condition = Condition()
        self._completed = not accepted
        self._durable = False
        self._error = error if not accepted else None
        self._gap = gap
        self._cursor = cursor
        self._segments: tuple[LearningTrajectorySegment, ...] = ()

    @property
    def completed(self) -> bool:
        with self._condition:
            return self._completed

    @property
    def durable(self) -> bool:
        with self._condition:
            return self._durable

    @property
    def error(self) -> str | None:
        with self._condition:
            return self._error

    @property
    def gap(self) -> TrajectoryPersistenceGap | None:
        with self._condition:
            return self._gap

    @property
    def cursor(self) -> SegmentCursor | None:
        with self._condition:
            return self._cursor

    @property
    def segments(self) -> tuple[LearningTrajectorySegment, ...]:
        with self._condition:
            return self._segments

    def wait(self, timeout: float | None = None) -> bool:
        if timeout is not None and (
            not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout < 0.0
        ):
            raise ValueError("persistence receipt timeout must be nonnegative")
        with self._condition:
            self._condition.wait_for(lambda: self._completed, timeout=timeout)
            return self._completed and self._durable

    def _complete(
        self,
        *,
        durable: bool,
        error: BaseException | None = None,
        gap: TrajectoryPersistenceGap | None = None,
        cursor: SegmentCursor | None = None,
        segments: tuple[LearningTrajectorySegment, ...] = (),
    ) -> None:
        with self._condition:
            if self._completed:
                return
            self._durable = durable
            self._error = None if error is None else f"{type(error).__name__}: {error}"
            self._gap = gap
            self._cursor = cursor
            self._segments = segments
            self._completed = True
            self._condition.notify_all()


class _BarrierReceipt:
    def __init__(self) -> None:
        self._condition = Condition()
        self._completed = False

    def wait(self, timeout: float) -> bool:
        with self._condition:
            self._condition.wait_for(lambda: self._completed, timeout=timeout)
            return self._completed

    def complete(self) -> None:
        with self._condition:
            self._completed = True
            self._condition.notify_all()


@dataclass(slots=True)
class _ActivationPhaseWork:
    record: PreparedActivationRecord
    expected_phase: ActivationPhase | None
    receipt: DurableActivationReceipt


@dataclass(slots=True)
class _ActivationConfidenceWork:
    records: tuple[ModelEvidenceRecord, ...]
    receipt: DurableActivationReceipt


@dataclass(slots=True)
class _CheckpointWork:
    name: str
    snapshot: dict[str, object]
    receipt: DurableCheckpointReceipt


@dataclass(slots=True)
class _CheckpointTerminalWork:
    name: str
    prepared: dict[str, object]
    committed: dict[str, object]
    success: ModelEvidenceRecord
    failure: ModelEvidenceRecord
    receipt: DurableCheckpointReceipt


def _default_persist_activation_phase(record: PreparedActivationRecord, expected_phase: ActivationPhase | None) -> None:
    commit_model_activation_phase(record, expected_phase=expected_phase)


@dataclass(slots=True)
class _ActivationWork:
    decision: ModelEvidenceRecord
    completed: bool = False
    succeeded: bool = False


class ModelPersistenceWorker:
    """One bounded priority worker for every durable learning mutation."""

    _ACTIVATION_PRIORITY = 0
    _SEGMENT_BOUNDARY_PRIORITY = 1
    _COMPOUND_TRAJECTORY_PRIORITY = 2
    _PRE_ROLL_PRIORITY = 3
    _ORDINARY_PRIORITY = 4

    @dataclass(slots=True)
    class _QueuedWork:
        sequence: int
        priority: int
        kind: str
        payload: object

    @dataclass(slots=True)
    class _TrajectoryWork:
        batch: TrajectoryAppendBatch
        receipt: PersistenceReceipt

    @dataclass(slots=True)
    class _TrajectoryQuarantineWork:
        segment_id: str
        receipt: PersistenceReceipt

    def __init__(
        self,
        store: _ModelStore,
        logger: _ErrorLogger,
        *,
        evidence_capacity: int = 128,
        trajectory_capacity: int = 128,
        work_capacity: int = 256,
        activation_reserve: int = 16,
        boundary_reserve: int = 16,
        trajectory_repository: LearningTrajectoryRepository | None = None,
        append_evidence: Callable[[Sequence[ModelEvidenceRecord]], None] = append_model_evidence,
        read_evidence: Callable[..., list[ModelEvidenceRecord]] | None = None,
        commit_activation: Callable[[ModelEvidenceRecord], None] = commit_model_activation,
        persist_activation_phase: Callable[
            [PreparedActivationRecord, ActivationPhase | None], None
        ] = _default_persist_activation_phase,
        persist_trajectory_batch: Callable[[TrajectoryAppendBatch], SegmentCursor] | None = None,
    ) -> None:
        self._validate_capacity(evidence_capacity, "evidence_capacity")
        self._validate_capacity(trajectory_capacity, "trajectory_capacity")
        self._validate_capacity(work_capacity, "work_capacity")
        if isinstance(activation_reserve, bool) or not isinstance(activation_reserve, int) or activation_reserve < 1:
            raise ValueError("activation_reserve must be a positive integer")
        if isinstance(boundary_reserve, bool) or not isinstance(boundary_reserve, int) or boundary_reserve < 1:
            raise ValueError("boundary_reserve must be a positive integer")
        if activation_reserve + boundary_reserve >= work_capacity:
            raise ValueError("work capacity must exceed its reserved capacity")
        self._store = store
        self._logger = logger
        self._append_evidence = append_evidence
        self._read_evidence = read_evidence
        self._commit_activation = commit_activation
        self._persist_activation_phase = persist_activation_phase
        self._trajectory_repository = trajectory_repository
        self._persist_trajectory_batch_callback = (
            self._default_persist_trajectory_batch if persist_trajectory_batch is None else persist_trajectory_batch
        )
        self._evidence_capacity = evidence_capacity
        self._trajectory_capacity = trajectory_capacity
        self._work_capacity = work_capacity
        self._activation_reserve = activation_reserve
        self._boundary_reserve = boundary_reserve
        self._condition = Condition()
        self._pending_work: deque[ModelPersistenceWorker._QueuedWork] = deque()
        self._next_sequence = 0
        self._inflight_checkpoints: dict[str, dict[str, object]] = {}
        self._last_saved_revisions: dict[str, int] = {}
        self._segment_cursors: dict[str, SegmentCursor] = {}
        self._blocked_segments: set[str] = set()
        self._stopping = False
        self._close_called = False
        self._close_result: bool | None = None
        self._thread: Thread | None = None
        self._evidence_blocked = False
        self._failed = False
        self._work_active = False

    @staticmethod
    def _validate_capacity(value: int, name: str) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a positive integer")

    @staticmethod
    def _validate_timeout(timeout: float, context: str) -> float:
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout < 0.0:
            raise ValueError(f"{context} timeout must be nonnegative")
        return float(timeout)

    def _total_work_locked(self) -> int:
        return len(self._pending_work) + int(self._work_active)

    def _admits_priority_locked(self, priority: int) -> bool:
        if priority == self._ACTIVATION_PRIORITY:
            limit = self._work_capacity
        elif priority == self._SEGMENT_BOUNDARY_PRIORITY:
            limit = self._work_capacity - self._activation_reserve
        else:
            limit = self._work_capacity - self._activation_reserve - self._boundary_reserve
        return self._total_work_locked() < limit

    @property
    def evidence_blocked(self) -> bool:
        """Whether an evidence loss/failure has made confidence fail closed."""
        with self._condition:
            return self._evidence_blocked or self._failed

    def contains_evidence(self, record: ModelEvidenceRecord) -> bool:
        """Whether the exact immutable terminal record is already durable."""
        if not isinstance(record, ModelEvidenceRecord):
            raise TypeError("record must be ModelEvidenceRecord")
        reader = self._read_evidence
        if reader is None:
            return False
        return any(
            candidate.evidence_id == record.evidence_id and candidate == record
            for candidate in reader(
                session_id=record.session_id,
                cook_id=record.cook_id,
                kind=record.kind,
            )
        )

    def bind_evidence_reader(
        self,
        reader: Callable[..., list[ModelEvidenceRecord]],
    ) -> None:
        """Inject the durable evidence reader used for prepared recovery."""
        if not callable(reader):
            raise TypeError("evidence reader must be callable")
        self._read_evidence = reader

    @property
    def failed(self) -> bool:
        with self._condition:
            return self._failed

    def _stage_checkpoint_owned(
        self,
        name: str,
        snapshot: dict[str, object],
    ) -> bool:
        stage_owned = getattr(self._store, "stage_owned", None)
        if not callable(stage_owned):
            return True
        try:
            if stage_owned(name, snapshot):
                return True
            self._logger.error(f"Could not stage {name} model checkpoint")
        except Exception as error:
            self._logger.error(f"Could not stage {name} model checkpoint: {error}")
        return False

    def submit_checkpoint(self, name: str, snapshot: dict[str, object]) -> bool:
        """Copy and coalesce a checkpoint without crossing a barrier fence."""
        if not isinstance(name, str) or not name.strip():
            raise ValueError("checkpoint name must be non-blank")
        owned_snapshot = copy_valid_snapshot(snapshot)
        if owned_snapshot is None:
            self._logger.error(f"Could not own {name} model checkpoint: invalid persistence snapshot")
            return False
        with self._condition:
            if self._failed:
                self._logger.error(f"Could not checkpoint {name} model after persistence failed")
                return False
            if self._stopping:
                self._logger.error(f"Could not checkpoint {name} model after teardown began")
                return False
            accepted_revisions = [
                revision
                for revision in (
                    self._revision(accepted)
                    for accepted in (
                        *self._pending_checkpoint_snapshots_locked(name),
                        *((self._inflight_checkpoints[name],) if name in self._inflight_checkpoints else ()),
                    )
                )
                if revision is not None
            ]
            last_saved = self._last_saved_revisions.get(name)
            if last_saved is not None:
                accepted_revisions.append(last_saved)
            if accepted_revisions:
                submitted_revision = self._revision(owned_snapshot)
                if submitted_revision is None or submitted_revision <= max(accepted_revisions):
                    return True
            for queued in reversed(self._pending_work):
                if queued.kind != "checkpoint":
                    continue
                queued_name, _queued_snapshot = self._checkpoint_payload(queued.payload)
                if queued_name != name:
                    continue
                barrier_after = any(
                    candidate.kind == "barrier" and candidate.sequence > queued.sequence
                    for candidate in self._pending_work
                )
                if not barrier_after:
                    if not self._stage_checkpoint_owned(name, owned_snapshot):
                        return False
                    queued.payload = (name, owned_snapshot)
                    self._condition.notify()
                    return True
                break
            if not self._admits_priority_locked(self._ORDINARY_PRIORITY):
                self._logger.error(f"Could not checkpoint {name} model: persistence-queue-overflow")
                return False
            if not self._stage_checkpoint_owned(name, owned_snapshot):
                return False
            self._enqueue_locked(
                "checkpoint",
                (name, owned_snapshot),
                self._ORDINARY_PRIORITY,
            )
            self._start_locked()
            self._condition.notify()
        return True

    def submit_durable_checkpoint(
        self,
        name: str,
        snapshot: dict[str, object],
    ) -> DurableCheckpointReceipt:
        """Queue one uncoalesced checkpoint and expose its actual save outcome."""
        if not isinstance(name, str) or not name.strip():
            raise ValueError("checkpoint name must be non-blank")
        owned_snapshot = copy_valid_snapshot(snapshot)
        if owned_snapshot is None:
            self._logger.error(f"Could not own {name} model checkpoint: invalid persistence snapshot")
            return DurableCheckpointReceipt(
                accepted=False,
                error="invalid-persistence-snapshot",
            )
        with self._condition:
            if self._failed:
                return DurableCheckpointReceipt(
                    accepted=False,
                    error="persistence-failed",
                )
            if self._stopping:
                return DurableCheckpointReceipt(
                    accepted=False,
                    error="persistence-stopped",
                )
            if not self._admits_priority_locked(self._ORDINARY_PRIORITY):
                return DurableCheckpointReceipt(
                    accepted=False,
                    error="persistence-queue-overflow",
                )
            if not self._stage_checkpoint_owned(name, owned_snapshot):
                return DurableCheckpointReceipt(
                    accepted=False,
                    error="checkpoint-staging-failed",
                )
            receipt = DurableCheckpointReceipt(accepted=True)
            self._enqueue_locked(
                "checkpoint-durable",
                _CheckpointWork(name, owned_snapshot, receipt),
                self._ORDINARY_PRIORITY,
            )
            self._start_locked()
            self._condition.notify()
            return receipt

    def submit_checkpoint_with_terminal_evidence(
        self,
        name: str,
        prepared: dict[str, object],
        committed: dict[str, object],
        success: ModelEvidenceRecord,
        failure: ModelEvidenceRecord,
    ) -> DurableCheckpointReceipt:
        """Prepare safely, commit terminal evidence, then publish activatable state."""
        if not isinstance(name, str) or not name.strip():
            raise ValueError("checkpoint name must be non-blank")
        owned_prepared = copy_valid_snapshot(prepared)
        owned_committed = copy_valid_snapshot(committed)
        if owned_prepared is None or owned_committed is None:
            return DurableCheckpointReceipt(
                accepted=False,
                error="invalid-persistence-snapshot",
            )
        owned_success = ModelEvidenceRecord.model_validate_json(success.model_dump_json())
        owned_failure = ModelEvidenceRecord.model_validate_json(failure.model_dump_json())
        with self._condition:
            if self._failed:
                return DurableCheckpointReceipt(
                    accepted=False,
                    error="persistence-failed",
                )
            if self._stopping:
                return DurableCheckpointReceipt(
                    accepted=False,
                    error="persistence-stopped",
                )
            if not self._admits_priority_locked(self._ORDINARY_PRIORITY):
                return DurableCheckpointReceipt(
                    accepted=False,
                    error="persistence-queue-overflow",
                )
            receipt = DurableCheckpointReceipt(accepted=True)
            self._enqueue_locked(
                "checkpoint-terminal",
                _CheckpointTerminalWork(
                    name,
                    owned_prepared,
                    owned_committed,
                    owned_success,
                    owned_failure,
                    receipt,
                ),
                self._ORDINARY_PRIORITY,
            )
            self._start_locked()
            self._condition.notify()
            return receipt

    def submit_evidence(self, record: ModelEvidenceRecord) -> EvidenceSubmission:
        """Validate and copy one append-only record without blocking control."""
        return self.submit_evidence_batch((record,))

    def submit_evidence_batch(
        self,
        records: Sequence[ModelEvidenceRecord],
    ) -> EvidenceSubmission:
        """Atomically accept a FIFO evidence batch or retain one complete gap."""
        owned_records = tuple(
            ModelEvidenceRecord.model_validate_json(record.model_dump_json())
            if isinstance(record, ModelEvidenceRecord)
            else self._raise_record_type_error()
            for record in records
        )
        if not owned_records:
            return EvidenceSubmission(accepted=True)
        with self._condition:
            if self._failed:
                return self._reject_evidence_locked(
                    owned_records,
                    "persistence-failed",
                )
            if self._stopping:
                return self._reject_evidence_locked(
                    owned_records,
                    "persistence-stopped",
                )
            queued_count = sum(
                len(queued.payload)
                for queued in self._pending_work
                if queued.kind == "evidence" and isinstance(queued.payload, tuple)
            )
            if queued_count + len(owned_records) > self._evidence_capacity:
                return self._reject_evidence_locked(
                    owned_records,
                    "evidence-queue-overflow",
                )
            if not self._admits_priority_locked(self._ORDINARY_PRIORITY):
                return self._reject_evidence_locked(
                    owned_records,
                    "persistence-queue-overflow",
                )
            self._enqueue_locked(
                "evidence",
                owned_records,
                self._ORDINARY_PRIORITY,
            )
            self._start_locked()
            self._condition.notify()
        return EvidenceSubmission(accepted=True)

    def submit_trajectory_batch(
        self,
        batch: TrajectoryAppendBatch,
    ) -> PersistenceReceipt:
        """Own and queue one trajectory mutation without touching SQLite."""
        if not isinstance(batch, TrajectoryAppendBatch):
            raise TypeError("batch must be a TrajectoryAppendBatch")
        owned = TrajectoryAppendBatch(
            cursor=batch.cursor,
            begin_segment=batch.begin_segment,
            pre_roll=batch.pre_roll,
            hold_entry=batch.hold_entry,
            scored=batch.scored,
            evidence=batch.evidence,
            finalize_reason=batch.finalize_reason,
            break_reason=batch.break_reason,
            next_segment=batch.next_segment,
        )
        with self._condition:
            if self._failed:
                return self._rejected_trajectory_receipt("persistence-failed")
            if self._stopping:
                return self._rejected_trajectory_receipt("persistence-closed")
            if owned.begin_segment is not None and owned.begin_segment.segment_id in self._blocked_segments:
                return self._rejected_trajectory_receipt("trajectory-lineage-blocked")
            priority = self._trajectory_priority(owned)
            segment_id = self._trajectory_segment_id(owned)
            if self._is_trajectory_append(owned) and segment_id in self._blocked_segments:
                return self._rejected_trajectory_receipt("trajectory-lineage-blocked")
            pending = sum(
                queued.kind == "trajectory"
                and isinstance(queued.payload, self._TrajectoryWork)
                and self._is_trajectory_append(queued.payload.batch)
                for queued in self._pending_work
            )
            if self._is_trajectory_append(owned) and pending >= self._trajectory_capacity:
                self._evidence_blocked = True
                if segment_id is not None:
                    self._blocked_segments.add(segment_id)
                self._logger.error("Learning trajectory was not queued: trajectory-queue-overflow")
                return self._rejected_trajectory_receipt("trajectory-queue-overflow")
            if not self._admits_priority_locked(priority):
                if self._is_trajectory_append(owned) and segment_id is not None:
                    self._evidence_blocked = True
                    self._blocked_segments.add(segment_id)
                return self._rejected_trajectory_receipt("persistence-queue-overflow")
            receipt = PersistenceReceipt(accepted=True)
            self._enqueue_locked(
                "trajectory",
                self._TrajectoryWork(owned, receipt),
                priority,
            )
            self._start_locked()
            self._condition.notify()
            return receipt

    def submit_trajectory_quarantine(
        self,
        segment_id: str,
    ) -> PersistenceReceipt:
        """Queue fail-closed removal of an untraceable durable segment."""
        if not isinstance(segment_id, str) or not segment_id or segment_id != segment_id.strip():
            raise ValueError("segment_id must be a non-blank string")
        with self._condition:
            self._evidence_blocked = True
            self._blocked_segments.add(segment_id)
            if self._failed:
                return self._rejected_trajectory_receipt("persistence-failed")
            if self._stopping:
                return self._rejected_trajectory_receipt("persistence-closed")
            if not self._admits_priority_locked(self._SEGMENT_BOUNDARY_PRIORITY):
                return self._rejected_trajectory_receipt("persistence-queue-overflow")
            receipt = PersistenceReceipt(accepted=True)
            self._enqueue_locked(
                "trajectory-quarantine",
                self._TrajectoryQuarantineWork(segment_id, receipt),
                self._SEGMENT_BOUNDARY_PRIORITY,
            )
            self._start_locked()
            self._condition.notify()
            return receipt

    def commit_activation(
        self,
        decision: ModelEvidenceRecord,
        *,
        wait: bool = False,
        timeout: float | None = None,
    ) -> bool:
        """Queue activation, optionally waiting for its durable transaction."""
        if not isinstance(decision, ModelEvidenceRecord):
            raise TypeError("decision must be ModelEvidenceRecord")
        if timeout is not None:
            self._validate_timeout(timeout, "activation persistence")
        owned_decision = ModelEvidenceRecord.model_validate_json(decision.model_dump_json())
        if owned_decision.kind is not EvidenceKind.ACTIVATION:
            raise ValueError("activation worker requires activation evidence")
        work = _ActivationWork(owned_decision)
        with self._condition:
            if self._failed:
                self._logger.error("Could not commit model activation after persistence failed")
                return False
            if self._stopping:
                self._logger.error("Could not commit model activation after teardown began")
                return False
            if not self._admits_priority_locked(self._ACTIVATION_PRIORITY):
                self._logger.error("Could not commit model activation: persistence-queue-overflow")
                return False
            self._enqueue_locked(
                "activation",
                work,
                self._ACTIVATION_PRIORITY,
            )
            self._start_locked()
            self._condition.notify()
            if not wait:
                return True
            completed = self._condition.wait_for(
                lambda: work.completed,
                timeout=timeout,
            )
            return completed and work.succeeded

    def submit_activation_confidence(
        self,
        decision: ModelEvidenceRecord,
        *,
        preceding_evidence: Sequence[ModelEvidenceRecord] = (),
    ) -> DurableActivationReceipt:
        """Queue assessment evidence and confidence in the activation FIFO."""
        if not isinstance(decision, ModelEvidenceRecord):
            raise TypeError("decision must be ModelEvidenceRecord")
        if not isinstance(preceding_evidence, Sequence) or isinstance(
            preceding_evidence,
            (str, bytes),
        ):
            raise TypeError("preceding_evidence must be a sequence of ModelEvidenceRecord")
        owned_preceding: list[ModelEvidenceRecord] = []
        for record in preceding_evidence:
            if not isinstance(record, ModelEvidenceRecord):
                raise TypeError("preceding_evidence records must be ModelEvidenceRecord")
            owned_record = ModelEvidenceRecord.model_validate_json(record.model_dump_json())
            if owned_record.kind is not EvidenceKind.CANDIDATE_ASSESSMENT or not isinstance(
                owned_record.payload,
                CandidateAssessmentEvidence,
            ):
                raise ValueError("preceding_evidence requires candidate-assessment evidence")
            owned_preceding.append(owned_record)
        owned = ModelEvidenceRecord.model_validate_json(decision.model_dump_json())
        if owned.kind is not EvidenceKind.CONFIDENCE_DECISION or not isinstance(
            owned.payload, ConfidenceDecisionEvidence
        ):
            raise ValueError("activation confidence requires confidence-decision evidence")
        if any(
            record.payload.decision_id != owned.payload.decision_id
            for record in owned_preceding
            if isinstance(record.payload, CandidateAssessmentEvidence)
        ):
            raise ValueError("preceding candidate-assessment decision_id must match confidence")
        receipt = DurableActivationReceipt(accepted=True)
        with self._condition:
            if self._failed or self._stopping:
                return DurableActivationReceipt(accepted=False)
            if not self._admits_priority_locked(self._ACTIVATION_PRIORITY):
                return DurableActivationReceipt(accepted=False)
            self._enqueue_locked(
                "activation-confidence",
                _ActivationConfidenceWork(
                    (*owned_preceding, owned),
                    receipt,
                ),
                self._ACTIVATION_PRIORITY,
            )
            self._start_locked()
            self._condition.notify()
        return receipt

    def submit_activation_phase(
        self,
        record: PreparedActivationRecord,
        *,
        expected_phase: ActivationPhase | None,
    ) -> DurableActivationReceipt:
        """Queue one exact phase CAS and return a receipt not yet durable."""
        if not isinstance(record, PreparedActivationRecord):
            raise TypeError("record must be a PreparedActivationRecord")
        if expected_phase is not None and not isinstance(
            expected_phase,
            ActivationPhase,
        ):
            expected_phase = ActivationPhase(expected_phase)
        receipt = DurableActivationReceipt(accepted=True)
        with self._condition:
            if self._failed or self._stopping:
                return DurableActivationReceipt(accepted=False)
            if not self._admits_priority_locked(self._ACTIVATION_PRIORITY):
                return DurableActivationReceipt(accepted=False)
            self._enqueue_locked(
                "activation-phase",
                _ActivationPhaseWork(record, expected_phase, receipt),
                self._ACTIVATION_PRIORITY,
            )
            self._start_locked()
            self._condition.notify()
        return receipt

    def barrier(self, timeout: float = 2.0) -> bool:
        """Fence all previously accepted work while leaving the worker usable."""
        timeout = self._validate_timeout(timeout, "persistence barrier")
        with self._condition:
            if self._close_called:
                thread = self._thread
                if self._close_result is False and thread is not None and not thread.is_alive():
                    self._close_result = True
                if self._close_result is not None:
                    return self._close_result
                return thread is None or not thread.is_alive()
            if not self._pending_work and not self._work_active:
                return True
            if not self._admits_priority_locked(self._ORDINARY_PRIORITY):
                return False
            receipt = _BarrierReceipt()
            self._enqueue_locked(
                "barrier",
                receipt,
                self._ORDINARY_PRIORITY,
            )
            self._start_locked()
            self._condition.notify_all()
        return receipt.wait(timeout)

    def close(self, timeout: float = 2.0) -> bool:
        """Barrier and stop once; every caller may wait for the same terminal close."""
        timeout = self._validate_timeout(timeout, "persistence close")
        with self._condition:
            if self._close_called:
                return self._condition.wait_for(
                    lambda: self._close_result is True,
                    timeout=timeout,
                )
            self._close_called = True
            self._stopping = True
            thread = self._thread
            if thread is None:
                self._close_result = True
                self._condition.notify_all()
                return True
            self._enqueue_locked(
                "barrier",
                _BarrierReceipt(),
                self._ORDINARY_PRIORITY,
            )
            self._condition.notify_all()
        thread.join(timeout=timeout)
        completed = not thread.is_alive()
        with self._condition:
            if completed or self._close_result is not True:
                self._close_result = completed
            self._condition.notify_all()
        if not completed:
            self._logger.error("Model persistence close is still pending after its timeout")
        return completed

    @staticmethod
    def _raise_record_type_error() -> ModelEvidenceRecord:
        raise TypeError("record must be ModelEvidenceRecord")

    @staticmethod
    def _revision(snapshot: dict[str, object]) -> int | None:
        revision = snapshot.get("revision")
        if isinstance(revision, int) and not isinstance(revision, bool) and revision >= 0:
            return revision
        return None

    @staticmethod
    def _checkpoint_payload(
        payload: object,
    ) -> tuple[str, dict[str, object]]:
        if isinstance(payload, _CheckpointWork):
            return payload.name, payload.snapshot
        if isinstance(payload, _CheckpointTerminalWork):
            return payload.name, payload.prepared
        if (
            not isinstance(payload, tuple)
            or len(payload) != 2
            or not isinstance(payload[0], str)
            or not isinstance(payload[1], dict)
        ):
            raise TypeError("checkpoint work is malformed")
        return payload

    def _pending_checkpoint_snapshots_locked(
        self,
        name: str,
    ) -> tuple[dict[str, object], ...]:
        snapshots: list[dict[str, object]] = []
        for queued in self._pending_work:
            if queued.kind != "checkpoint":
                continue
            queued_name, snapshot = self._checkpoint_payload(queued.payload)
            if queued_name == name:
                snapshots.append(snapshot)
        return tuple(snapshots)

    def _enqueue_locked(self, kind: str, payload: object, priority: int) -> None:
        self._pending_work.append(
            self._QueuedWork(
                sequence=self._next_sequence,
                priority=priority,
                kind=kind,
                payload=payload,
            )
        )
        self._next_sequence += 1

    def _start_locked(self) -> None:
        if self._thread is None:
            self._thread = Thread(
                target=self._run,
                name="controller-model-persistence",
                daemon=True,
            )
            self._thread.start()

    def _reject_evidence_locked(
        self,
        records: Sequence[ModelEvidenceRecord],
        reason: str,
    ) -> EvidenceSubmission:
        self._evidence_blocked = True
        first = records[0]
        gap = ModelEvidenceRecord(
            evidence_id=f"{first.evidence_id}:gap",
            kind=EvidenceKind.RECORDER_GAP,
            session_id=first.session_id,
            cook_id=first.cook_id,
            timestamp_ms=first.timestamp_ms,
            role_generation=first.role_generation,
            model_digest=first.model_digest,
            provenance_digest=first.provenance_digest,
            payload=RecorderGapEvidence(
                lost_record_count=len(records),
                reason=reason,
            ),
        )
        if not self._stopping and not self._failed and self._admits_priority_locked(self._ORDINARY_PRIORITY):
            self._enqueue_locked(
                "recorder-gap",
                (gap,),
                self._ORDINARY_PRIORITY,
            )
            self._start_locked()
            self._condition.notify()
        self._logger.error(f"Model evidence was not queued: {reason}")
        return EvidenceSubmission(accepted=False, recorder_gap=gap)

    @staticmethod
    def _is_trajectory_append(batch: TrajectoryAppendBatch) -> bool:
        return batch.begin_segment is None and batch.break_reason is None and batch.finalize_reason is None

    @staticmethod
    def _trajectory_segment_id(
        batch: TrajectoryAppendBatch,
    ) -> str | None:
        if batch.begin_segment is not None:
            return batch.begin_segment.segment_id
        return None if batch.cursor is None else batch.cursor.segment_id

    @staticmethod
    def _trajectory_priority(batch: TrajectoryAppendBatch) -> int:
        if batch.begin_segment is not None or batch.break_reason is not None or batch.finalize_reason is not None:
            return ModelPersistenceWorker._SEGMENT_BOUNDARY_PRIORITY
        if batch.scored:
            return ModelPersistenceWorker._COMPOUND_TRAJECTORY_PRIORITY
        if batch.pre_roll:
            return ModelPersistenceWorker._PRE_ROLL_PRIORITY
        return ModelPersistenceWorker._ORDINARY_PRIORITY

    @staticmethod
    def _trajectory_gap(reason: str) -> TrajectoryPersistenceGap:
        return TrajectoryPersistenceGap(reason=reason)

    def _rejected_trajectory_receipt(
        self,
        reason: str,
    ) -> PersistenceReceipt:
        return PersistenceReceipt(
            accepted=False,
            error=reason,
            gap=self._trajectory_gap(reason),
        )

    def _lineage_ready_locked(self, candidate: _QueuedWork) -> bool:
        if candidate.kind != "trajectory":
            return True
        payload = candidate.payload
        if not isinstance(payload, self._TrajectoryWork):
            return True
        segment_id = self._trajectory_segment_id(payload.batch)
        if segment_id is None:
            return True
        return not any(
            queued.kind == "trajectory"
            and queued.sequence < candidate.sequence
            and isinstance(queued.payload, self._TrajectoryWork)
            and self._trajectory_segment_id(queued.payload.batch) == segment_id
            for queued in self._pending_work
        )

    def _next_work_locked(self) -> _QueuedWork | None:
        if not self._pending_work:
            return None
        barrier_sequences = [queued.sequence for queued in self._pending_work if queued.kind == "barrier"]
        fence = min(barrier_sequences) if barrier_sequences else None
        eligible = [
            (index, queued)
            for index, queued in enumerate(self._pending_work)
            if queued.kind != "barrier"
            and (fence is None or queued.sequence < fence)
            and self._lineage_ready_locked(queued)
        ]
        if eligible:
            index, _selected = min(
                eligible,
                key=lambda candidate: (
                    candidate[1].priority,
                    candidate[1].sequence,
                ),
            )
        elif fence is not None:
            index = next(
                index
                for index, queued in enumerate(self._pending_work)
                if queued.kind == "barrier" and queued.sequence == fence
            )
        else:
            return None
        queued = self._pending_work[index]
        del self._pending_work[index]
        if queued.kind != "barrier":
            self._work_active = True
        if queued.kind in {
            "checkpoint",
            "checkpoint-durable",
            "checkpoint-terminal",
        }:
            name, snapshot = self._checkpoint_payload(queued.payload)
            self._inflight_checkpoints[name] = snapshot
        return queued

    def _fail_pending_locked(self, error: BaseException) -> None:
        failure_gap = self._trajectory_gap("trajectory-persistence-failed")
        for queued in self._pending_work:
            payload = queued.payload
            if queued.kind in ("activation-phase", "activation-confidence") and isinstance(
                payload,
                (_ActivationPhaseWork, _ActivationConfidenceWork),
            ):
                payload.receipt._complete(durable=False, error=error)
            elif queued.kind == "activation" and isinstance(
                payload,
                _ActivationWork,
            ):
                payload.completed = True
                payload.succeeded = False
            elif (
                queued.kind == "checkpoint-terminal"
                and isinstance(
                    payload,
                    _CheckpointTerminalWork,
                )
                or queued.kind == "checkpoint-durable"
                and isinstance(
                    payload,
                    _CheckpointWork,
                )
            ):
                payload.receipt._complete(durable=False, error=error)
            elif (queued.kind == "trajectory" and isinstance(payload, self._TrajectoryWork)) or (
                queued.kind == "trajectory-quarantine" and isinstance(payload, self._TrajectoryQuarantineWork)
            ):
                payload.receipt._complete(
                    durable=False,
                    error=error,
                    gap=failure_gap,
                )
            elif queued.kind == "barrier" and isinstance(
                payload,
                _BarrierReceipt,
            ):
                payload.complete()
        self._pending_work.clear()

    def _effective_trajectory_batch(
        self,
        batch: TrajectoryAppendBatch,
    ) -> TrajectoryAppendBatch:
        if batch.cursor is None:
            return batch
        cursor = self._segment_cursors.get(batch.cursor.segment_id, batch.cursor)
        if batch.cursor.next_ordinal > cursor.next_ordinal:
            raise ValueError("queued trajectory cursor is ahead of durable lineage")
        if batch.cursor.next_ordinal == cursor.next_ordinal and batch.cursor.chain_digest != cursor.chain_digest:
            raise ValueError("queued trajectory cursor conflicts with durable lineage")
        return TrajectoryAppendBatch(
            cursor=cursor,
            begin_segment=batch.begin_segment,
            pre_roll=batch.pre_roll,
            hold_entry=batch.hold_entry,
            scored=batch.scored,
            evidence=batch.evidence,
            finalize_reason=batch.finalize_reason,
            break_reason=batch.break_reason,
            next_segment=batch.next_segment,
        )

    def _default_persist_trajectory_batch(
        self,
        batch: TrajectoryAppendBatch,
    ) -> SegmentCursor:
        repository = self._trajectory_repository
        if repository is None:
            repository = LearningTrajectoryRepository()
            self._trajectory_repository = repository
        with repository.write_transaction() as connection:
            if batch.begin_segment is not None:
                cursor = repository.begin_segment(batch.begin_segment)
            elif batch.break_reason is not None and batch.next_segment is not None:
                if batch.cursor is None:
                    raise TypeError("break-and-begin work has no cursor")
                cursor = repository.break_and_begin(
                    batch.cursor,
                    batch.break_reason,
                    batch.next_segment,
                )
            elif batch.finalize_reason is not None:
                if batch.cursor is None:
                    raise TypeError("finalization work has no cursor")
                finalized = repository.finalize(
                    batch.cursor,
                    batch.finalize_reason,
                )
                cursor = SegmentCursor(
                    segment_id=batch.cursor.segment_id,
                    next_ordinal=batch.cursor.next_ordinal,
                    chain_digest=batch.cursor.chain_digest,
                    corpus_revision=finalized.corpus_revision,
                )
            else:
                if batch.cursor is None:
                    raise TypeError("append work has no cursor")
                appended = repository.append(
                    batch.cursor,
                    pre_roll=batch.pre_roll,
                    hold_entry=batch.hold_entry,
                    scored=batch.scored,
                )
                cursor = appended.cursor
            if batch.evidence:
                append_model_evidence_in_transaction(
                    connection,
                    self._durable_evidence_batch(batch.evidence),
                )
            return cursor

    def _quarantine_trajectory_segment(self, segment_id: str) -> None:
        repository = self._trajectory_repository
        if repository is None:
            repository = LearningTrajectoryRepository()
            self._trajectory_repository = repository
        repository.quarantine_segment(
            segment_id,
            TrajectoryBreakReason.RECORDER_GAP,
        )

    def _readback_trajectory_segments(
        self,
        batch: TrajectoryAppendBatch,
    ) -> tuple[LearningTrajectorySegment, ...]:
        repository = self._trajectory_repository
        if repository is None:
            return ()
        segment_ids: tuple[str, ...]
        if batch.begin_segment is not None:
            segment_ids = (batch.begin_segment.segment_id,)
        elif batch.break_reason is not None and batch.next_segment is not None:
            if batch.cursor is None:
                raise TypeError("break-and-begin work has no source segment")
            segment_ids = (
                batch.cursor.segment_id,
                batch.next_segment.segment_id,
            )
        elif batch.cursor is not None:
            segment_ids = (batch.cursor.segment_id,)
        else:
            raise TypeError("durable trajectory work has no segment identity")
        materialized: list[LearningTrajectorySegment] = []
        for segment_id in segment_ids:
            segment = repository.read_segment(segment_id)
            if segment is None:
                raise RuntimeError(f"durable trajectory segment readback failed: {segment_id}")
            materialized.append(segment)
        return tuple(materialized)

    def _record_trajectory_success_locked(
        self,
        batch: TrajectoryAppendBatch,
        cursor: SegmentCursor | None,
    ) -> None:
        if cursor is None:
            return
        revision = cursor.corpus_revision
        self._segment_cursors = {
            segment_id: SegmentCursor(
                segment_id=existing.segment_id,
                next_ordinal=existing.next_ordinal,
                chain_digest=existing.chain_digest,
                corpus_revision=revision,
            )
            for segment_id, existing in self._segment_cursors.items()
        }
        if batch.begin_segment is not None:
            self._segment_cursors[cursor.segment_id] = cursor
            self._blocked_segments.discard(cursor.segment_id)
            return
        if batch.cursor is None:
            return
        source_id = batch.cursor.segment_id
        if batch.break_reason is not None:
            self._segment_cursors.pop(source_id, None)
            self._blocked_segments.discard(source_id)
            self._segment_cursors[cursor.segment_id] = cursor
        elif batch.finalize_reason is not None:
            self._segment_cursors.pop(source_id, None)
        else:
            self._segment_cursors[source_id] = cursor

    def _save_checkpoint(
        self,
        name: str,
        snapshot: dict[str, object],
    ) -> CheckpointSaveOutcome:
        return self._store.save_outcome(name, snapshot)

    def _run(self) -> None:
        while True:
            with self._condition:
                while (queued := self._next_work_locked()) is None and not self._stopping:
                    self._condition.wait()
                if queued is None:
                    if self._close_called:
                        self._close_result = True
                    self._condition.notify_all()
                    return
            kind = queued.kind
            payload = queued.payload
            if kind == "barrier":
                if not isinstance(payload, _BarrierReceipt):
                    raise TypeError("barrier work is malformed")
                payload.complete()
                with self._condition:
                    self._condition.notify_all()
                continue
            inflight_checkpoint_name = (
                self._checkpoint_payload(payload)[0]
                if kind
                in {
                    "checkpoint",
                    "checkpoint-durable",
                    "checkpoint-terminal",
                }
                else None
            )
            checkpoint_work = payload if kind == "checkpoint-durable" and isinstance(payload, _CheckpointWork) else None
            terminal_work = (
                payload if kind == "checkpoint-terminal" and isinstance(payload, _CheckpointTerminalWork) else None
            )
            activation_work = payload if kind == "activation" and isinstance(payload, _ActivationWork) else None
            phase_work = payload if kind == "activation-phase" and isinstance(payload, _ActivationPhaseWork) else None
            confidence_work = (
                payload if kind == "activation-confidence" and isinstance(payload, _ActivationConfidenceWork) else None
            )
            trajectory_work = payload if kind == "trajectory" and isinstance(payload, self._TrajectoryWork) else None
            quarantine_work = (
                payload
                if kind == "trajectory-quarantine" and isinstance(payload, self._TrajectoryQuarantineWork)
                else None
            )
            succeeded = False
            checkpoint_revision = None
            effective_trajectory_batch: TrajectoryAppendBatch | None = None
            trajectory_cursor: SegmentCursor | None = None
            trajectory_segments: tuple[LearningTrajectorySegment, ...] = ()
            try:
                if kind in {"checkpoint", "checkpoint-durable"}:
                    name, snapshot = self._checkpoint_payload(payload)
                    checkpoint_revision = self._revision(snapshot)
                    outcome = self._save_checkpoint(name, snapshot)
                    if outcome is CheckpointSaveOutcome.FAILED:
                        raise RuntimeError("checkpoint store failed")
                    if outcome not in (
                        CheckpointSaveOutcome.SAVED,
                        CheckpointSaveOutcome.NONADVANCING,
                    ):
                        raise RuntimeError(f"unknown checkpoint store outcome: {outcome!r}")
                elif terminal_work is not None:
                    checkpoint_revision = self._revision(terminal_work.prepared)
                    outcome = self._save_checkpoint(
                        terminal_work.name,
                        terminal_work.prepared,
                    )
                    if outcome is CheckpointSaveOutcome.FAILED:
                        checkpoint_revision = None
                        error = RuntimeError("checkpoint prepare failed")
                        self._append_evidence((terminal_work.failure,))
                        terminal_work.receipt._complete(
                            durable=False,
                            error=error,
                        )
                    elif outcome in (
                        CheckpointSaveOutcome.SAVED,
                        CheckpointSaveOutcome.NONADVANCING,
                    ):
                        try:
                            self._append_evidence((terminal_work.success,))
                        except Exception as evidence_error:
                            self._append_evidence((terminal_work.failure,))
                            terminal_work.receipt._complete(
                                durable=False,
                                error=evidence_error,
                            )
                        else:
                            commit_outcome = self._save_checkpoint(
                                terminal_work.name,
                                terminal_work.committed,
                            )
                            if commit_outcome in (
                                CheckpointSaveOutcome.SAVED,
                                CheckpointSaveOutcome.NONADVANCING,
                            ):
                                checkpoint_revision = self._revision(terminal_work.committed)
                            else:
                                self._logger.error(
                                    "PID-SP checkpoint remains prepared; "
                                    "cold recovery will reconcile its durable terminal"
                                )
                            terminal_work.receipt._complete(durable=True)
                    else:
                        raise RuntimeError(f"unknown checkpoint store outcome: {outcome!r}")
                elif kind in ("evidence", "recorder-gap"):
                    self._append_evidence(self._durable_evidence_batch(payload))
                elif confidence_work is not None:
                    self._append_evidence(confidence_work.records)
                elif activation_work is not None:
                    result = self._commit_activation(activation_work.decision)
                    if result is False:
                        raise RuntimeError("activation transaction declined")
                elif phase_work is not None:
                    result = self._persist_activation_phase(
                        phase_work.record,
                        phase_work.expected_phase,
                    )
                    if result is False:
                        raise RuntimeError("activation phase transaction declined")
                elif trajectory_work is not None:
                    effective_trajectory_batch = self._effective_trajectory_batch(trajectory_work.batch)
                    result = self._persist_trajectory_batch_callback(effective_trajectory_batch)
                    if not isinstance(result, SegmentCursor):
                        raise TypeError("persist_trajectory_batch must return SegmentCursor")
                    trajectory_cursor = result
                    trajectory_segments = self._readback_trajectory_segments(effective_trajectory_batch)
                elif quarantine_work is not None:
                    self._quarantine_trajectory_segment(
                        quarantine_work.segment_id,
                    )
                else:
                    raise TypeError(f"{kind} work is malformed")
                succeeded = True
            except Exception as error:
                with self._condition:
                    self._failed = True
                    self._evidence_blocked = True
                    if trajectory_work is not None:
                        segment_id = self._trajectory_segment_id(trajectory_work.batch)
                        if segment_id is not None:
                            self._blocked_segments.add(segment_id)
                    if checkpoint_work is not None:
                        checkpoint_work.receipt._complete(
                            durable=False,
                            error=error,
                        )
                    if terminal_work is not None:
                        terminal_work.receipt._complete(
                            durable=False,
                            error=error,
                        )
                    if phase_work is not None:
                        phase_work.receipt._complete(
                            durable=False,
                            error=error,
                        )
                    if confidence_work is not None:
                        confidence_work.receipt._complete(
                            durable=False,
                            error=error,
                        )
                    if trajectory_work is not None:
                        trajectory_work.receipt._complete(
                            durable=False,
                            error=error,
                            gap=self._trajectory_gap("trajectory-persistence-failed"),
                        )
                    if quarantine_work is not None:
                        quarantine_work.receipt._complete(
                            durable=False,
                            error=error,
                            gap=self._trajectory_gap("trajectory-quarantine-failed"),
                        )
                    if activation_work is not None:
                        activation_work.succeeded = False
                        activation_work.completed = True
                    self._fail_pending_locked(error)
                    self._condition.notify_all()
                self._logger.error(f"Could not persist model {kind}: {error}")
            else:
                if checkpoint_work is not None:
                    checkpoint_work.receipt._complete(durable=True)
                if phase_work is not None:
                    phase_work.receipt._complete(durable=True)
                if confidence_work is not None:
                    confidence_work.receipt._complete(durable=True)
                if quarantine_work is not None:
                    quarantine_work.receipt._complete(durable=True)
                if trajectory_work is not None:
                    with self._condition:
                        self._record_trajectory_success_locked(
                            (
                                effective_trajectory_batch
                                if effective_trajectory_batch is not None
                                else trajectory_work.batch
                            ),
                            trajectory_cursor,
                        )
                    trajectory_work.receipt._complete(
                        durable=True,
                        cursor=trajectory_cursor,
                        segments=trajectory_segments,
                    )
            finally:
                with self._condition:
                    if inflight_checkpoint_name is not None:
                        self._inflight_checkpoints.pop(
                            inflight_checkpoint_name,
                            None,
                        )
                        if succeeded and checkpoint_revision is not None:
                            previous = self._last_saved_revisions.get(inflight_checkpoint_name)
                            if previous is None or checkpoint_revision > previous:
                                self._last_saved_revisions[inflight_checkpoint_name] = checkpoint_revision
                    self._work_active = False
                    if activation_work is not None:
                        activation_work.succeeded = succeeded
                        activation_work.completed = True
                    self._condition.notify_all()

    @staticmethod
    def _durable_evidence_batch(
        payload: object,
    ) -> tuple[ModelEvidenceRecord, ...]:
        """Stamp atomicity only on the immutable tuple handed to a transaction."""
        if not isinstance(payload, tuple) or not all(isinstance(record, ModelEvidenceRecord) for record in payload):
            raise TypeError("evidence work must contain one immutable record batch")
        return tuple(
            record.model_copy(
                update={
                    "payload": replace(
                        record.payload,
                        atomic_persistence=True,
                    )
                }
            )
            if isinstance(record.payload, RefreshDiagnosticsEvidence)
            else record
            for record in payload
        )
