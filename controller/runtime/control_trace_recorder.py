"""Synchronous, bounded batching for controller control-quality traces."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from common.control_trace import ControlTraceRecord, RecorderGapPayload, TraceEventKind
from common.datastore_accessors import append_control_trace, prune_control_trace

FLUSH_INTERVAL_MS = 5_000
RETENTION_PERIOD_MS = 30 * 24 * 60 * 60 * 1_000
DAILY_PRUNE_INTERVAL_MS = 24 * 60 * 60 * 1_000
PRUNE_BATCH_SIZE = 10_000
EMERGENCY_BUFFER_CAPACITY = 10_000

_PERSISTENCE_FAILURE_WARNING = "Control trace persistence failed; buffering records for retry"
_PERSISTENCE_RECOVERY_WARNING = "Control trace persistence recovered"
_RETENTION_FAILURE_WARNING = "Control trace retention pruning failed"
_RETENTION_RECOVERY_WARNING = "Control trace retention pruning recovered"

_logger = logging.getLogger(__name__)


def _monotonic_ms() -> int:
    return time.monotonic_ns() // 1_000_000


def _wall_ms() -> int:
    return time.time_ns() // 1_000_000


class AppendControlTrace(Protocol):
    def __call__(self, records: Sequence[ControlTraceRecord]) -> None: ...


class PruneControlTrace(Protocol):
    def __call__(self, before_ms: int, *, limit: int) -> int: ...


@dataclass(slots=True)
class _LostRecordSpan:
    """The contiguous dropped range and envelope context for its one gap event."""

    first: ControlTraceRecord
    count: int
    start_ms: int
    end_ms: int

    def include(self, record: ControlTraceRecord) -> None:
        self.count += 1
        self.start_ms = min(self.start_ms, record.ts_ms)
        self.end_ms = max(self.end_ms, record.ts_ms)

    def to_record(self) -> ControlTraceRecord:
        return ControlTraceRecord(
            ts_ms=self.end_ms,
            session_id=self.first.session_id,
            cook_id=self.first.cook_id,
            controller=self.first.controller,
            event_kind=TraceEventKind.RECORDER_GAP,
            schema_version=self.first.schema_version,
            payload=RecorderGapPayload(
                lost_record_count=self.count,
                gap_start_ms=self.start_ms,
                gap_end_ms=self.end_ms,
            ),
        )


class ControlTraceRecorder:
    """Batch validated records without delaying the controller's hot path."""

    def __init__(
        self,
        *,
        append: AppendControlTrace = append_control_trace,
        prune: PruneControlTrace = prune_control_trace,
        warning: Callable[[str], None] = _logger.warning,
        monotonic_clock: Callable[[], int] = _monotonic_ms,
        wall_clock: Callable[[], int] = _wall_ms,
        capacity: object = EMERGENCY_BUFFER_CAPACITY,
    ) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError("capacity must be a positive integer")

        self._append: AppendControlTrace = append
        self._prune: PruneControlTrace = prune
        self._warning: Callable[[str], None] = warning
        self._monotonic_clock: Callable[[], int] = monotonic_clock
        self._wall_clock: Callable[[], int] = wall_clock
        self._capacity: int = capacity
        self._records: list[ControlTraceRecord] = []
        self._lost_records: _LostRecordSpan | None = None
        self._last_flush_ms: int = monotonic_clock()
        self._last_prune_ms: int | None = None
        self._persistence_degraded: bool = False
        self._retention_degraded: bool = False
        self._closed: bool = False
        self._prune_now(wall_clock())

    def record(self, record: ControlTraceRecord) -> None:
        """Revalidate and buffer one trace record without performing I/O."""
        if self._closed:
            raise RuntimeError("control trace recorder is closed")

        validated = ControlTraceRecord.model_validate_json(record.model_dump_json())
        if len(self._records) == self._capacity:
            self._remember_drop(self._records.pop(0))
        self._records.append(validated)

    def flush_due(self, now_ms: int | None = None) -> None:
        """Flush the current batch after its five-second monotonic interval."""
        if self._closed:
            return
        now_ms = self._monotonic_clock() if now_ms is None else now_ms
        if now_ms - self._last_flush_ms >= FLUSH_INTERVAL_MS:
            self._last_flush_ms = now_ms
            if not self._flush():
                return
        self._prune_if_due()

    def close(self) -> None:
        """Make exactly one final best-effort persistence attempt."""
        if self._closed:
            return
        self._closed = True
        _ = self._flush()

    def _remember_drop(self, record: ControlTraceRecord) -> None:
        if self._lost_records is None:
            self._lost_records = _LostRecordSpan(
                first=record,
                count=1,
                start_ms=record.ts_ms,
                end_ms=record.ts_ms,
            )
        else:
            self._lost_records.include(record)

    def _flush(self) -> bool:
        if not self._records:
            return True

        records: Sequence[ControlTraceRecord] = tuple(self._records)
        if self._lost_records is not None:
            records = (self._lost_records.to_record(), *records)

        try:
            self._append(records)
        except Exception:
            if not self._persistence_degraded:
                self._warn(_PERSISTENCE_FAILURE_WARNING)
                self._persistence_degraded = True
            return False

        self._records.clear()
        self._lost_records = None
        if self._persistence_degraded:
            self._warn(_PERSISTENCE_RECOVERY_WARNING)
            self._persistence_degraded = False
        return True

    def _prune_if_due(self) -> None:
        now_ms = self._wall_clock()
        if self._last_prune_ms is None or now_ms - self._last_prune_ms >= DAILY_PRUNE_INTERVAL_MS:
            self._prune_now(now_ms)

    def _prune_now(self, now_ms: int) -> None:
        self._last_prune_ms = now_ms
        cutoff_ms = now_ms - RETENTION_PERIOD_MS
        try:
            while self._prune(cutoff_ms, limit=PRUNE_BATCH_SIZE) == PRUNE_BATCH_SIZE:
                pass
        except Exception:
            if not self._retention_degraded:
                self._warn(_RETENTION_FAILURE_WARNING)
                self._retention_degraded = True
            return

        if self._retention_degraded:
            self._warn(_RETENTION_RECOVERY_WARNING)
            self._retention_degraded = False

    def _warn(self, message: str) -> None:
        try:
            self._warning(message)
        except Exception:
            _logger.exception("Control trace warning callback failed")
