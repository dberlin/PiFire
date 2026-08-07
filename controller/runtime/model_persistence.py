"""One off-path worker for checkpoints, durable evidence, and activation."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from threading import Condition, Thread
from typing import Protocol

from common.controller_model_state import CheckpointSaveOutcome, copy_valid_snapshot
from common.datastore_accessors import append_model_evidence, commit_model_activation
from common.model_evidence import EvidenceKind, ModelEvidenceRecord, RecorderGapEvidence


class _ModelStore(Protocol):
    def save(self, name: str, snapshot: dict[str, object]) -> bool: ...


class _ErrorLogger(Protocol):
    def error(self, message: str) -> None: ...


@dataclass(frozen=True, slots=True)
class EvidenceSubmission:
    """Immediate, explicit result of a nonblocking evidence submission."""

    accepted: bool
    recorder_gap: ModelEvidenceRecord | None = None


class ModelPersistenceWorker:
    """Serialize durable writes without putting SQLite or model I/O on Hold."""

    def __init__(
        self,
        store: _ModelStore,
        logger: _ErrorLogger,
        *,
        evidence_capacity: int = 128,
        append_evidence: Callable[[Sequence[ModelEvidenceRecord]], None] = append_model_evidence,
        commit_activation: Callable[[ModelEvidenceRecord], None] = commit_model_activation,
    ) -> None:
        if isinstance(evidence_capacity, bool) or not isinstance(evidence_capacity, int) or evidence_capacity < 1:
            raise ValueError("evidence_capacity must be a positive integer")
        self._store = store
        self._logger = logger
        self._append_evidence = append_evidence
        self._commit_activation = commit_activation
        self._evidence_capacity = evidence_capacity
        self._condition = Condition()
        self._pending_checkpoints: dict[str, dict[str, object]] = {}
        self._pending_evidence: deque[ModelEvidenceRecord] = deque()
        self._pending_recorder_gaps: deque[ModelEvidenceRecord] = deque()
        self._pending_activations: deque[ModelEvidenceRecord] = deque()
        self._stopping = False
        self._thread: Thread | None = None
        self._evidence_blocked = False
        self._failed = False

    @property
    def evidence_blocked(self) -> bool:
        """Whether an evidence loss/failure has made confidence fail closed."""
        with self._condition:
            return self._evidence_blocked or self._failed

    def submit_checkpoint(self, name: str, snapshot: dict[str, object]) -> bool:
        """Copy a checkpoint and replace only its not-yet-written predecessor."""
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
            pending_snapshot = self._pending_checkpoints.get(name)
            if pending_snapshot is not None:
                pending_revision = self._revision(pending_snapshot)
                submitted_revision = self._revision(owned_snapshot)
                if pending_revision is not None and (submitted_revision is None or submitted_revision <= pending_revision):
                    return True
            stage_owned = getattr(self._store, "stage_owned", None)
            if callable(stage_owned):
                try:
                    if not stage_owned(name, owned_snapshot):
                        self._logger.error(f"Could not stage {name} model checkpoint")
                        return False
                except Exception as error:
                    self._logger.error(f"Could not stage {name} model checkpoint: {error}")
                    return False
            self._pending_checkpoints[name] = owned_snapshot
            self._start_locked()
            self._condition.notify()
        return True

    def submit_evidence(self, record: ModelEvidenceRecord) -> EvidenceSubmission:
        """Validate and copy one append-only record without blocking control."""
        return self.submit_evidence_batch((record,))

    def submit_evidence_batch(self, records: Sequence[ModelEvidenceRecord]) -> EvidenceSubmission:
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
                return self._reject_evidence_locked(owned_records, "persistence-failed")
            if self._stopping:
                return self._reject_evidence_locked(owned_records, "persistence-stopped")
            if len(self._pending_evidence) + len(owned_records) > self._evidence_capacity:
                return self._reject_evidence_locked(owned_records, "evidence-queue-overflow")
            self._pending_evidence.extend(owned_records)
            self._start_locked()
            self._condition.notify()
        return EvidenceSubmission(accepted=True)

    def commit_activation(self, decision: ModelEvidenceRecord) -> bool:
        """Queue a validated activation on its unbounded serialized channel."""
        if not isinstance(decision, ModelEvidenceRecord):
            raise TypeError("decision must be ModelEvidenceRecord")
        owned_decision = ModelEvidenceRecord.model_validate_json(decision.model_dump_json())
        if owned_decision.kind is not EvidenceKind.ACTIVATION:
            raise ValueError("activation worker requires activation evidence")
        with self._condition:
            if self._failed:
                self._logger.error("Could not commit model activation after persistence failed")
                return False
            if self._stopping:
                self._logger.error("Could not commit model activation after teardown began")
                return False
            self._pending_activations.append(owned_decision)
            self._start_locked()
            self._condition.notify()
        return True

    def _save_checkpoint(self, name: str, snapshot: dict[str, object]) -> CheckpointSaveOutcome:
        save_outcome = getattr(self._store, "save_outcome", None)
        if callable(save_outcome):
            return save_outcome(name, snapshot)
        return CheckpointSaveOutcome.SAVED if self._store.save(name, snapshot) else CheckpointSaveOutcome.NONADVANCING

    def flush_and_stop(self, *, timeout: float = 0.1) -> bool:
        """Flush accepted work once while keeping Hold teardown bounded."""
        if timeout < 0.0:
            raise ValueError("persistence flush timeout must be nonnegative")
        with self._condition:
            self._stopping = True
            thread = self._thread
            self._condition.notify_all()
        if thread is None:
            return True
        thread.join(timeout=timeout)
        completed = not thread.is_alive()
        if not completed:
            self._logger.error("Model persistence flush is still pending after Hold teardown")
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

    def _start_locked(self) -> None:
        if self._thread is None:
            self._thread = Thread(target=self._run, name="controller-model-persistence", daemon=True)
            self._thread.start()

    def _reject_evidence_locked(
        self, records: Sequence[ModelEvidenceRecord], reason: str
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
            payload=RecorderGapEvidence(lost_record_count=len(records), reason=reason),
        )
        if not self._stopping:
            self._pending_recorder_gaps.append(gap)
            self._start_locked()
            self._condition.notify()
        self._logger.error(f"Model evidence was not queued: {reason}")
        return EvidenceSubmission(accepted=False, recorder_gap=gap)

    def _next_work_locked(self) -> tuple[str, object] | None:
        if self._pending_activations:
            return "activation", self._pending_activations.popleft()
        if self._pending_evidence:
            return "evidence", self._pending_evidence.popleft()
        if self._pending_recorder_gaps:
            return "evidence", self._pending_recorder_gaps.popleft()
        if self._pending_checkpoints:
            name = next(iter(self._pending_checkpoints))
            return "checkpoint", (name, self._pending_checkpoints.pop(name))
        return None

    def _run(self) -> None:
        while True:
            with self._condition:
                while (work := self._next_work_locked()) is None and not self._stopping:
                    self._condition.wait()
                if work is None:
                    return
            kind, payload = work
            try:
                if kind == "checkpoint":
                    name, snapshot = payload
                    outcome = self._save_checkpoint(name, snapshot)
                    if outcome is CheckpointSaveOutcome.FAILED:
                        raise RuntimeError("checkpoint store failed")
                    if outcome is not CheckpointSaveOutcome.SAVED and outcome is not CheckpointSaveOutcome.NONADVANCING:
                        raise RuntimeError(f"unknown checkpoint store outcome: {outcome!r}")
                elif kind == "evidence":
                    self._append_evidence((payload,))
                else:
                    self._commit_activation(payload)
            except Exception as error:
                with self._condition:
                    self._failed = True
                    self._evidence_blocked = True
                self._logger.error(f"Could not persist model {kind}: {error}")
            finally:
                with self._condition:
                    self._condition.notify_all()
