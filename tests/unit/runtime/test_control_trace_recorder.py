"""Behavioral contract for the synchronous controller trace recorder."""

from collections.abc import Callable, Sequence
from typing import Protocol

import pytest
from pydantic import ValidationError

from common.control_trace import (
    ControlTraceRecord,
    ControllerType,
    InhibitReason,
    RecorderGapPayload,
    SafetyEventPayload,
    SafetyEventType,
    TraceEventKind,
)
from controller.runtime.control_trace_recorder import (
    DAILY_PRUNE_INTERVAL_MS,
    EMERGENCY_BUFFER_CAPACITY,
    FLUSH_INTERVAL_MS,
    PRUNE_BATCH_SIZE,
    RETENTION_PERIOD_MS,
    ControlTraceRecorder,
)


class _IncompatiblePrune(Protocol):
    def __call__(self, before_schema_version: int, *, limit: int) -> int: ...


def _no_incompatible_prune(before_schema_version: int, *, limit: int) -> int:
    _ = before_schema_version, limit
    return 0


class _Prune(Protocol):
    def __call__(self, before_ms: int, *, limit: int) -> int: ...


def _no_prune(before_ms: int, *, limit: int) -> int:
    _ = before_ms, limit
    return 0


def _no_warning(message: str) -> None:
    _ = message
    return None


def _append_nothing(batch: tuple[ControlTraceRecord, ...]) -> None:
    _ = batch
    return None


class _Clock:
    def __init__(self, now_ms: int) -> None:
        self.now_ms: int = now_ms

    def __call__(self) -> int:
        return self.now_ms


def _record(timestamp: int, *, session_id: str = "session-a", cook_id: str = "cook-a") -> ControlTraceRecord:
    return ControlTraceRecord(
        ts_ms=timestamp,
        session_id=session_id,
        cook_id=cook_id,
        controller=ControllerType.PID,
        event_kind=TraceEventKind.SAFETY_EVENT,
        payload=SafetyEventPayload(
            event=SafetyEventType.LID_DETECTED,
            inhibit_reason=InhibitReason.NONE,
            result_revision=None,
            detail=f"event-{timestamp}",
        ),
    )


def _recorder(
    *,
    append: Callable[[tuple[ControlTraceRecord, ...]], None],
    prune: _Prune | None = None,
    prune_incompatible: _IncompatiblePrune | None = None,
    warning: Callable[[str], None] | None = None,
    monotonic_clock: _Clock | None = None,
    wall_clock: _Clock | None = None,
    capacity: object = EMERGENCY_BUFFER_CAPACITY,
) -> ControlTraceRecorder:
    def append_batch(records: Sequence[ControlTraceRecord]) -> None:
        append(tuple(records))

    return ControlTraceRecorder(
        append=append_batch,
        prune=prune or _no_prune,
        prune_incompatible=prune_incompatible or _no_incompatible_prune,
        warning=warning or _no_warning,
        monotonic_clock=monotonic_clock or _Clock(0),
        wall_clock=wall_clock or _Clock(RETENTION_PERIOD_MS),
        capacity=capacity,
    )


def test_record_revalidates_and_buffers_without_persisting():
    batches: list[tuple[ControlTraceRecord, ...]] = []
    recorder = _recorder(append=batches.append)
    accepted = _record(10)
    invalid = ControlTraceRecord.model_construct(
        ts_ms=11,
        session_id="session-a",
        cook_id="cook-a",
        controller=ControllerType.PID,
        event_kind=TraceEventKind.SAFETY_EVENT,
        payload=SafetyEventPayload(
            event=SafetyEventType.LID_DETECTED,
            inhibit_reason=InhibitReason.NONE,
            result_revision=None,
            detail="invalid",
        ),
        schema_version=99,
    )

    recorder.record(accepted)

    assert batches == []
    with pytest.raises(ValidationError):
        recorder.record(invalid)
    assert batches == []


def test_flush_due_does_not_persist_before_the_five_second_boundary():
    batches: list[tuple[ControlTraceRecord, ...]] = []
    recorder = _recorder(append=batches.append)
    recorder.record(_record(10))

    recorder.flush_due(FLUSH_INTERVAL_MS - 1)

    assert batches == []


def test_flush_due_at_five_seconds_persists_one_ordered_batch_and_clears_it():
    batches: list[tuple[ControlTraceRecord, ...]] = []
    first = _record(10)
    second = _record(20)
    recorder = _recorder(append=batches.append)
    recorder.record(first)
    recorder.record(second)

    recorder.flush_due(FLUSH_INTERVAL_MS)
    recorder.flush_due(FLUSH_INTERVAL_MS * 2)

    assert batches == [(first, second)]


def test_due_flush_persists_during_the_cook_without_close():
    batches: list[tuple[ControlTraceRecord, ...]] = []
    recorder = _recorder(append=batches.append)
    event = _record(10)
    recorder.record(event)

    recorder.flush_due(FLUSH_INTERVAL_MS)

    assert batches == [(event,)]


def test_failed_flush_retains_batch_and_warns_once_until_next_interval_recovery():
    attempts: list[tuple[ControlTraceRecord, ...]] = []
    warnings: list[str] = []
    event = _record(10)

    def append(batch: tuple[ControlTraceRecord, ...]) -> None:
        attempts.append(batch)
        if len(attempts) < 3:
            raise OSError("database unavailable")

    recorder = _recorder(append=append, warning=warnings.append)
    recorder.record(event)

    recorder.flush_due(FLUSH_INTERVAL_MS)
    recorder.flush_due(FLUSH_INTERVAL_MS + 1)
    recorder.flush_due(FLUSH_INTERVAL_MS * 2)
    recorder.flush_due(FLUSH_INTERVAL_MS * 3 - 1)
    recorder.flush_due(FLUSH_INTERVAL_MS * 3)

    assert attempts == [(event,), (event,), (event,)]
    assert len(warnings) == 2
    assert "failed" in warnings[0].lower()
    assert "recovered" in warnings[1].lower()


def test_emergency_capacity_drops_oldest_records_and_emits_one_typed_gap_after_recovery():
    attempts: list[tuple[ControlTraceRecord, ...]] = []
    warnings: list[str] = []
    first, second, third, fourth = (_record(timestamp) for timestamp in (10, 20, 30, 40))

    def append(batch: tuple[ControlTraceRecord, ...]) -> None:
        attempts.append(batch)
        if len(attempts) == 1:
            raise OSError("database unavailable")

    recorder = _recorder(append=append, warning=warnings.append, capacity=2)
    recorder.record(first)
    recorder.record(second)
    recorder.flush_due(FLUSH_INTERVAL_MS)
    recorder.record(third)
    recorder.record(fourth)

    recorder.flush_due(FLUSH_INTERVAL_MS * 2)

    recovered = attempts[1]
    assert len(recovered) == 3
    gap, retained_first, retained_second = recovered
    assert gap.event_kind is TraceEventKind.RECORDER_GAP
    assert gap.session_id == first.session_id
    assert gap.cook_id == first.cook_id
    assert gap.controller is first.controller
    assert gap.schema_version == first.schema_version
    assert isinstance(gap.payload, RecorderGapPayload)
    assert gap.payload.lost_record_count == 2
    assert gap.payload.gap_start_ms == 10
    assert gap.payload.gap_end_ms == 20
    assert (retained_first, retained_second) == (third, fourth)
    assert len(warnings) == 2


def test_startup_and_daily_retention_use_exact_30_day_cutoff_and_bounded_deletes():
    prunes: list[tuple[int, int]] = []
    wall_clock = _Clock(RETENTION_PERIOD_MS + 123)
    delete_counts = iter((PRUNE_BATCH_SIZE, 1, 0))

    def prune(before_ms: int, *, limit: int) -> int:
        prunes.append((before_ms, limit))
        return next(delete_counts)

    recorder = _recorder(append=_append_nothing, prune=prune, wall_clock=wall_clock)
    wall_clock.now_ms += DAILY_PRUNE_INTERVAL_MS

    recorder.flush_due(DAILY_PRUNE_INTERVAL_MS)

    first_cutoff = 123
    second_cutoff = DAILY_PRUNE_INTERVAL_MS + 123
    assert prunes == [
        (first_cutoff, PRUNE_BATCH_SIZE),
        (first_cutoff, PRUNE_BATCH_SIZE),
        (second_cutoff, PRUNE_BATCH_SIZE),
    ]


@pytest.mark.parametrize("capacity", [0, -1, True, 2.5, float("nan")])
def test_capacity_must_be_a_real_positive_integer(capacity: object):
    with pytest.raises(ValueError, match="positive integer"):
        _ = _recorder(append=_append_nothing, capacity=capacity)


def test_failed_retention_pruning_waits_until_daily_cadence_and_flushes_first():
    events: list[str] = []
    warnings: list[str] = []
    wall_clock = _Clock(RETENTION_PERIOD_MS + 123)
    prune_attempts = 0

    def append(batch: tuple[ControlTraceRecord, ...]) -> None:
        _ = batch
        events.append("append")

    def prune(before_ms: int, *, limit: int) -> int:
        nonlocal prune_attempts
        _ = before_ms, limit
        events.append("prune")
        prune_attempts += 1
        if prune_attempts == 1:
            raise OSError("database unavailable")
        return 0

    recorder = _recorder(append=append, prune=prune, warning=warnings.append, wall_clock=wall_clock)
    recorder.record(_record(10))
    recorder.flush_due(FLUSH_INTERVAL_MS)
    wall_clock.now_ms += DAILY_PRUNE_INTERVAL_MS - 1
    recorder.record(_record(20))
    recorder.flush_due(FLUSH_INTERVAL_MS * 2)
    wall_clock.now_ms += 1
    recorder.record(_record(30))
    recorder.flush_due(FLUSH_INTERVAL_MS * 3)

    assert events == ["prune", "append", "append", "append", "prune"]
    assert len(warnings) == 2
    assert "failed" in warnings[0].lower()
    assert "recovered" in warnings[1].lower()


def test_close_makes_one_best_effort_flush_attempt_without_retrying():
    attempts: list[tuple[ControlTraceRecord, ...]] = []

    def append(batch: tuple[ControlTraceRecord, ...]) -> None:
        attempts.append(batch)
        raise OSError("database unavailable")

    event = _record(10)
    recorder = _recorder(append=append)
    recorder.record(event)

    recorder.close()
    recorder.close()

    assert attempts == [(event,)]


def test_incompatible_pruning_is_bounded_backlogged_and_does_not_block_flush():
    incompatible_calls: list[tuple[int, int]] = []
    batches: list[tuple[ControlTraceRecord, ...]] = []
    delete_counts = iter((PRUNE_BATCH_SIZE, 0))

    def prune_incompatible(before_schema_version: int, *, limit: int) -> int:
        incompatible_calls.append((before_schema_version, limit))
        return next(delete_counts)

    recorder = _recorder(append=batches.append, prune_incompatible=prune_incompatible)
    event = _record(10)
    recorder.record(event)
    recorder.flush_due(FLUSH_INTERVAL_MS)

    assert batches == [(event,)]
    assert incompatible_calls == [(3, PRUNE_BATCH_SIZE), (3, PRUNE_BATCH_SIZE)]


def test_incompatible_prune_failure_warns_once_retries_and_recovers():
    warnings: list[str] = []
    calls = 0

    def prune_incompatible(before_schema_version: int, *, limit: int) -> int:
        nonlocal calls
        _ = before_schema_version, limit
        calls += 1
        if calls == 1:
            raise OSError("database unavailable")
        return 0

    recorder = _recorder(
        append=_append_nothing,
        prune_incompatible=prune_incompatible,
        warning=warnings.append,
    )
    recorder.flush_due(FLUSH_INTERVAL_MS)

    assert calls == 2
    assert len(warnings) == 2
    assert "failed" in warnings[0].lower()
    assert "recovered" in warnings[1].lower()
