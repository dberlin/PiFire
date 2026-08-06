"""Pure validation of one typed controller control-trace session.

Replay consumes only persisted typed evidence. It never opens a controller,
mutates runtime state, or substitutes today's configuration for a recording.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from os import PathLike
import sqlite3
from typing import Protocol, cast

from common.control_trace import (
    TRACE_SCHEMA_VERSION,
    ActuationMode,
    AllocationPayload,
    AppliedOutputPayload,
    ControlTraceRecord,
    ControllerType,
    FramedPulseFramePayload,
    InhibitReason,
    ModelEvaluationPayload,
    ModelObservationPayload,
    MpcUpdatePayload,
    ResultStaleState,
    PidSpUpdatePayload,
    PidUpdatePayload,
    RecorderGapPayload,
    SafetyEventPayload,
    SafetyEventType,
    SessionPayload,
    TraceEventKind,
)
from common.datastore_accessors import read_control_trace_session
from controller.applied_output import OutputSource
from controller.mpc_allocator import ALLOCATOR_REVISION, allocate


class _TraceSessionReader(Protocol):
    def __call__(
        self, session_id: str, *, database_path: str | PathLike[str] | None = None
    ) -> list[ControlTraceRecord]: ...


_FLOAT_TOLERANCE = 1e-6
UpdatePayload = PidUpdatePayload | PidSpUpdatePayload | MpcUpdatePayload
IssueAdder = Callable[["ReplayIssueCode", str, int | None], None]


class ReplayIssueSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class ReplayIssueCode(StrEnum):
    EMPTY_SESSION = "empty_session"
    UNSUPPORTED_SCHEMA = "unsupported_schema"
    SESSION_COUNT = "session_count"
    SESSION_NOT_FIRST = "session_not_first"
    COOK_ID_MISMATCH = "cook_id_mismatch"
    SESSION_ID_MISMATCH = "session_id_mismatch"
    CONTROLLER_MISMATCH = "controller_mismatch"
    RECORDER_GAP = "recorder_gap"
    NON_MONOTONE_REVISION = "non_monotone_revision"
    MISSING_UPDATE = "missing_update"
    MISSING_ALLOCATION = "missing_allocation"
    DUPLICATE_ALLOCATION = "duplicate_allocation"
    ALLOCATION_MISMATCH = "allocation_mismatch"
    UNSUPPORTED_ALLOCATOR = "unsupported_allocator"
    FRAME_MODE_MISMATCH = "frame_mode_mismatch"
    FRAME_SCHEDULE_MISMATCH = "frame_schedule_mismatch"
    FRAME_DELIVERY_MISMATCH = "frame_delivery_mismatch"
    FRAME_TRANSITION_MISMATCH = "frame_transition_mismatch"
    NON_POSITIVE_INTERVAL = "non_positive_interval"
    OVERLAPPING_INTERVAL = "overlapping_interval"
    APPLIED_OUTPUT_MISMATCH = "applied_output_mismatch"
    INVALID_PARTIAL_OUTPUT = "invalid_partial_output"
    INVALID_SEED_OUTPUT = "invalid_seed_output"
    UNEXPLAINED_INHIBIT = "unexplained_inhibit"
    UNEXPLAINED_OUTPUT_SOURCE = "unexplained_output_source"
    INVALID_SAFETY_TRANSITION = "invalid_safety_transition"


@dataclass(frozen=True, slots=True)
class ReplayRecordContext:
    index: int
    ts_ms: int
    event_kind: TraceEventKind
    result_revision: int | None


@dataclass(frozen=True, slots=True)
class ReplayIssue:
    code: ReplayIssueCode
    severity: ReplayIssueSeverity
    detail: str
    record: ReplayRecordContext | None


@dataclass(frozen=True, slots=True)
class ReplayReport:
    session_id: str | None
    controller: ControllerType | None
    issues: tuple[ReplayIssue, ...]

    @property
    def valid(self) -> bool:
        return not any(issue.severity is ReplayIssueSeverity.ERROR for issue in self.issues)


class TraceSelectionError(ValueError):
    """The requested typed trace session could not be selected."""


def replay_session(session_id: str, *, database_path: str | PathLike[str] | None = None) -> ReplayReport:
    """Read one session through the typed accessor, then validate insertion order."""
    reader = cast(_TraceSessionReader, read_control_trace_session)
    try:
        records = reader(session_id, database_path=database_path)
    except (OSError, ValueError, sqlite3.Error) as error:
        raise TraceSelectionError(f"could not select control-trace session {session_id!r}: {error}") from error
    return validate_records(records)


def validate_records(records: Sequence[ControlTraceRecord]) -> ReplayReport:
    """Validate typed records without consulting live controller/runtime state."""
    ordered = tuple(records)
    issues: list[ReplayIssue] = []

    def add(code: ReplayIssueCode, detail: str, index: int | None = None) -> None:
        if index is None:
            context = None
        else:
            record = ordered[index]
            revision = getattr(record.payload, "result_revision", None)
            context = ReplayRecordContext(
                index, record.ts_ms, record.event_kind, revision if isinstance(revision, int) else None
            )
        issues.append(ReplayIssue(code, ReplayIssueSeverity.ERROR, detail, context))

    if not ordered:
        add(ReplayIssueCode.EMPTY_SESSION, "a replay requires at least one typed record")
        return ReplayReport(None, None, tuple(issues))

    sessions: list[tuple[int, ControlTraceRecord]] = []
    for index, record in enumerate(ordered):
        if record.schema_version != TRACE_SCHEMA_VERSION:
            add(ReplayIssueCode.UNSUPPORTED_SCHEMA, f"schema revision {record.schema_version} is unsupported", index)
        if record.event_kind is TraceEventKind.SESSION:
            sessions.append((index, record))
    if len(sessions) != 1:
        add(ReplayIssueCode.SESSION_COUNT, f"expected exactly one SESSION record, found {len(sessions)}")
        return ReplayReport(None, None, tuple(issues))

    session_index, session_record = sessions[0]
    if not isinstance(session_record.payload, SessionPayload):
        add(ReplayIssueCode.SESSION_COUNT, "SESSION record lacks SessionPayload", session_index)
        return ReplayReport(session_record.session_id, None, tuple(issues))
    session = session_record.payload
    if session_index != 0:
        add(ReplayIssueCode.SESSION_NOT_FIRST, "SESSION must be first in insertion order", session_index)
    for index, record in enumerate(ordered):
        if record.session_id != session_record.session_id:
            add(ReplayIssueCode.SESSION_ID_MISMATCH, "record belongs to another session", index)
        if record.cook_id != session_record.cook_id:
            add(ReplayIssueCode.COOK_ID_MISMATCH, "record cook differs from SESSION cook", index)
        if record.controller is not session.controller:
            add(ReplayIssueCode.CONTROLLER_MISMATCH, "record controller differs from SESSION", index)

    updates: dict[int, tuple[int, UpdatePayload]] = {}
    allocations: dict[int, tuple[int, AllocationPayload]] = {}
    framed_frames: list[tuple[int, FramedPulseFramePayload]] = []
    applied_outputs: list[tuple[int, AppliedOutputPayload]] = []
    safety_events: list[tuple[int, SafetyEventPayload]] = []
    scheduler_resets: dict[int, int] = {}
    last_revision = 0
    lid_active = manual_active = safety_active = False
    seed_seen = False
    seed_eligible = True

    for index, record in enumerate(ordered):
        payload = record.payload
        if isinstance(payload, RecorderGapPayload):
            add(ReplayIssueCode.RECORDER_GAP, "recorder explicitly dropped trace records", index)
        if isinstance(payload, SafetyEventPayload):
            safety_events.append((record.ts_ms, payload))
            if payload.event is SafetyEventType.SCHEDULER_RESET and payload.result_revision is not None:
                scheduler_resets[payload.result_revision] = scheduler_resets.get(payload.result_revision, 0) + 1
            lid_active, manual_active, safety_active = _advance_safety(
                payload, lid_active, manual_active, safety_active, add, index
            )
        elif isinstance(payload, (PidUpdatePayload, PidSpUpdatePayload, MpcUpdatePayload)):
            prior_update = updates.get(payload.result_revision)
            if payload.result_revision < last_revision or (
                payload.result_revision == last_revision
                and not _is_stale_observation(prior_update[1] if prior_update is not None else None, payload)
            ):
                add(ReplayIssueCode.NON_MONOTONE_REVISION, "accepted result revisions must strictly increase", index)
            else:
                last_revision = max(last_revision, payload.result_revision)
                updates[payload.result_revision] = (index, payload)
                _validate_inhibit(payload.inhibit_reason, lid_active, manual_active, safety_active, add, index)
                _validate_output_source(payload.output_source, lid_active, manual_active, add, index)
        elif isinstance(payload, AllocationPayload):
            if payload.result_revision in allocations:
                add(ReplayIssueCode.DUPLICATE_ALLOCATION, "duplicate allocation result revision", index)
            else:
                allocations[payload.result_revision] = (index, payload)
        elif isinstance(payload, FramedPulseFramePayload):
            if payload.result_revision > 0:
                seed_eligible = False
            framed_frames.append((index, payload))
            _validate_framed_frame(
                payload,
                session,
                updates,
                allocations,
                scheduler_resets,
                lid_active,
                manual_active,
                safety_active,
                add,
                index,
            )
        elif isinstance(payload, (ModelObservationPayload, ModelEvaluationPayload)):
            # Learning evidence is informational to plant-control replay. Its
            # discriminated payload has already been validated at the envelope
            # boundary and must not silently fall through this dispatcher.
            continue
        elif isinstance(payload, AppliedOutputPayload):
            applied_outputs.append((index, payload))
            if payload.result_revision == 0:
                if (
                    seed_seen
                    or not seed_eligible
                    or payload.output_source is not OutputSource.SEED
                    or not payload.sample_complete
                ):
                    add(ReplayIssueCode.INVALID_SEED_OUTPUT, "invalid revision-zero seed lifecycle", index)
                seed_seen = True
            seed_eligible = False

    _validate_allocations(allocations, updates, add)
    _validate_framed_allocations(framed_frames, allocations, add)
    _validate_framed_timeline(framed_frames, add)
    for revision, (index, payload) in updates.items():
        if isinstance(payload, MpcUpdatePayload) and revision not in allocations:
            add(ReplayIssueCode.MISSING_ALLOCATION, "accepted MPC update has no joined allocation", index)
    _validate_applied_outputs(applied_outputs, framed_frames, updates, ordered, safety_events, add)
    return ReplayReport(session_record.session_id, session.controller, tuple(issues))


def _is_stale_observation(previous: object, current: object) -> bool:
    """The sole legal equal-revision update observes a result becoming stale."""
    if not isinstance(previous, MpcUpdatePayload) or not isinstance(current, MpcUpdatePayload):
        return False
    if (
        previous.result_revision != current.result_revision
        or previous.result_age_ms >= current.result_age_ms
        or previous.stale
        or previous.stale_state is not ResultStaleState.FRESH
        or current.recovered
        or not current.stale
        or current.stale_state is not ResultStaleState.STALE
        or current.result_age_ms < 2_000 * current.control_period_seconds
    ):
        return False
    return (
        replace(
            current,
            result_age_ms=previous.result_age_ms,
            stale=False,
            stale_state=ResultStaleState.FRESH,
        )
        == previous
    )


def _advance_safety(
    payload: SafetyEventPayload, lid: bool, manual: bool, safety: bool, add: IssueAdder, index: int
) -> tuple[bool, bool, bool]:
    if payload.event is SafetyEventType.LID_DETECTED:
        if lid:
            add(ReplayIssueCode.INVALID_SAFETY_TRANSITION, "lid detected while already active", index)
        return True, manual, safety
    if payload.event is SafetyEventType.LID_CLEARED:
        if not lid:
            add(ReplayIssueCode.INVALID_SAFETY_TRANSITION, "lid cleared without detection", index)
        return False, manual, safety
    if payload.event is SafetyEventType.MANUAL_TAKEOVER:
        if manual:
            add(ReplayIssueCode.INVALID_SAFETY_TRANSITION, "manual takeover while already active", index)
        return lid, True, safety
    if payload.event is SafetyEventType.MANUAL_RELEASE:
        if not manual:
            add(ReplayIssueCode.INVALID_SAFETY_TRANSITION, "manual release without takeover", index)
        return lid, False, safety
    if payload.event in (
        SafetyEventType.STOP,
        SafetyEventType.ERROR,
        SafetyEventType.TEMPERATURE_GUARD,
        SafetyEventType.CONTROLLER_FALLBACK,
        SafetyEventType.CONTROLLER_RECONFIGURE,
        SafetyEventType.SCHEDULER_RESET,
    ):
        return lid, manual, True
    return lid, manual, safety


def _validate_framed_frame(
    payload: FramedPulseFramePayload,
    session: SessionPayload,
    updates: dict[int, tuple[int, UpdatePayload]],
    allocations: dict[int, tuple[int, AllocationPayload]],
    scheduler_resets: dict[int, int],
    lid: bool,
    manual: bool,
    safety: bool,
    add: IssueAdder,
    index: int,
) -> None:
    update = updates.get(payload.result_revision)
    allocation = allocations.get(payload.result_revision)
    if update is None:
        add(ReplayIssueCode.MISSING_UPDATE, "framed pulse cannot join its result revision", index)
    elif update[1].actuation_mode is not ActuationMode.FRAMED_PULSE:
        add(ReplayIssueCode.FRAME_MODE_MISMATCH, "framed pulse joined to a non-framed update", index)
    if not (
        _close(payload.pulse_slot_seconds, session.pulse_slot_seconds or 0)
        and _close(payload.frame_seconds, session.pulse_frame_seconds or 0)
        and _multiple_of(payload.scheduled_on_seconds, payload.pulse_slot_seconds)
    ):
        add(ReplayIssueCode.FRAME_SCHEDULE_MISMATCH, "framed pulse schedule differs from SESSION slot authority", index)
    if payload.delivered_on_seconds > payload.scheduled_on_seconds + _FLOAT_TOLERANCE:
        add(ReplayIssueCode.FRAME_DELIVERY_MISMATCH, "framed pulse delivered more on-time than scheduled", index)
    _validate_transition(
        payload.delivered_on_seconds,
        payload.actual_start_active,
        payload.transition_count,
        payload.actual_end_active,
        (payload.frame_end_ms - payload.frame_start_ms) / 1000,
        add,
        index,
    )
    _validate_inhibit(payload.inhibit_reason, lid, manual, safety, add, index)
    if payload.reset_reason is not None:
        count = scheduler_resets.get(payload.result_revision, 0)
        if count == 0:
            add(
                ReplayIssueCode.UNEXPLAINED_INHIBIT,
                "scheduler reset lacks a prior matching scheduler-reset event",
                index,
            )
        else:
            scheduler_resets[payload.result_revision] = count - 1


def _validate_transition(
    on_seconds: float,
    starts_active: bool,
    transitions: int,
    ends_active: bool,
    duration: float,
    add: IssueAdder,
    index: int,
) -> None:
    if on_seconds > duration + _FLOAT_TOLERANCE:
        add(ReplayIssueCode.FRAME_DELIVERY_MISMATCH, "actual on-time exceeds frame duration", index)
    if ends_active is not (starts_active ^ bool(transitions % 2)):
        add(ReplayIssueCode.FRAME_TRANSITION_MISMATCH, "transition parity disagrees with start and end state", index)
    if transitions == 0 and not _close(on_seconds, duration if starts_active else 0.0):
        add(ReplayIssueCode.FRAME_TRANSITION_MISMATCH, "zero-transition delivery disagrees with start state", index)


def _validate_inhibit(
    reason: InhibitReason, lid: bool, manual: bool, safety: bool, add: IssueAdder, index: int
) -> None:
    expected = {InhibitReason.LID_OPEN: lid, InhibitReason.MANUAL_OVERRIDE: manual, InhibitReason.SAFETY: safety}
    if reason in expected and not expected[reason]:
        add(ReplayIssueCode.UNEXPLAINED_INHIBIT, f"{reason.value} inhibition lacks a prior safety event", index)


def _validate_output_source(source: OutputSource, lid: bool, manual: bool, add: IssueAdder, index: int) -> None:
    if source is OutputSource.LID_OPEN and not lid:
        add(ReplayIssueCode.UNEXPLAINED_OUTPUT_SOURCE, "lid output lacks a lid-detected event", index)
    if source is OutputSource.MANUAL_OVERRIDE and not manual:
        add(ReplayIssueCode.UNEXPLAINED_OUTPUT_SOURCE, "manual output lacks a manual-takeover event", index)


def _validate_allocations(
    allocations: dict[int, tuple[int, AllocationPayload]],
    updates: dict[int, tuple[int, UpdatePayload]],
    add: IssueAdder,
) -> None:
    for revision, (index, payload) in allocations.items():
        update = updates.get(revision)
        if update is None:
            add(ReplayIssueCode.MISSING_UPDATE, "allocation cannot join its result revision", index)
            continue
        if not isinstance(update[1], MpcUpdatePayload):
            add(ReplayIssueCode.ALLOCATION_MISMATCH, "allocation can join only an MPC update", index)
            continue
        if payload.allocator_revision != ALLOCATOR_REVISION:
            add(
                ReplayIssueCode.UNSUPPORTED_ALLOCATOR,
                f"allocator revision {payload.allocator_revision} is unsupported",
                index,
            )
            continue
        expected = allocate(
            payload.normalized_combustion_load,
            u_max=payload.u_max,
            fan_min_pct=payload.fan_min_pct,
            fan_max_pct=payload.fan_max_pct,
            enable_fan=payload.fan_enabled,
        )
        if not (
            _close(payload.normalized_combustion_load, update[1].bounded_firing_load)
            and _close(payload.normalized_combustion_load, expected.normalized_combustion_load)
            and _close(payload.requested_auger_duty, expected.auger_duty)
            and _optional_close(payload.requested_fan_duty, expected.fan_duty)
            and payload.auger_clamp_reason is expected.auger_clamp_reason
            and payload.fan_clamp_reason is expected.fan_clamp_reason
        ):
            add(ReplayIssueCode.ALLOCATION_MISMATCH, "allocation outputs do not reproduce from recorded inputs", index)


def _validate_framed_allocations(
    framed_frames: list[tuple[int, FramedPulseFramePayload]],
    allocations: dict[int, tuple[int, AllocationPayload]],
    add: IssueAdder,
) -> None:
    for index, frame in framed_frames:
        allocation = allocations.get(frame.result_revision)
        if allocation is None:
            continue
        payload = allocation[1]
        if not (
            _close(frame.requested_combustion_load, payload.normalized_combustion_load)
            and _close(frame.requested_auger_duty, payload.requested_auger_duty)
            and _optional_close(frame.requested_fan_duty, payload.requested_fan_duty)
        ):
            add(ReplayIssueCode.FRAME_SCHEDULE_MISMATCH, "framed pulse command differs from joined allocation", index)


def _validate_framed_timeline(framed_frames: list[tuple[int, FramedPulseFramePayload]], add: IssueAdder) -> None:
    latest_end_ms: int | None = None
    for index, frame in sorted(framed_frames, key=lambda item: (item[1].frame_start_ms, item[1].frame_end_ms, item[0])):
        if latest_end_ms is not None and frame.frame_start_ms < latest_end_ms:
            add(ReplayIssueCode.OVERLAPPING_INTERVAL, "framed pulse intervals overlap", index)
        latest_end_ms = frame.frame_end_ms if latest_end_ms is None else max(latest_end_ms, frame.frame_end_ms)


def _validate_applied_outputs(
    applied: list[tuple[int, AppliedOutputPayload]],
    framed_frames: list[tuple[int, FramedPulseFramePayload]],
    updates: dict[int, tuple[int, UpdatePayload]],
    ordered: tuple[ControlTraceRecord, ...],
    safety_events: list[tuple[int, SafetyEventPayload]],
    add: IssueAdder,
) -> None:
    previous_end: int | None = None
    terminal_partials: list[int] = []
    for index, payload in applied:
        if payload.interval_end_ms <= payload.interval_start_ms:
            add(ReplayIssueCode.NON_POSITIVE_INTERVAL, "applied-output interval must be positive", index)
        if previous_end is not None and payload.interval_start_ms < previous_end:
            add(ReplayIssueCode.OVERLAPPING_INTERVAL, "applied-output intervals overlap", index)
        previous_end = payload.interval_end_ms
        if payload.result_revision not in updates and not (
            payload.result_revision == 0 and payload.output_source is OutputSource.SEED
        ):
            add(ReplayIssueCode.MISSING_UPDATE, "applied output cannot join its producing result revision", index)
        _validate_applied_source(payload, safety_events, add, index)
        if not payload.sample_complete:
            if payload.realized_combustion_load is not None:
                add(ReplayIssueCode.INVALID_PARTIAL_OUTPUT, "partial output must omit realized combustion load", index)
            if not _replacement_partial(index, payload, ordered):
                terminal_partials.append(index)
    _reconcile_framed_applied_outputs(applied, framed_frames, add)
    if len(terminal_partials) > 1:
        for index in terminal_partials[1:]:
            add(ReplayIssueCode.INVALID_PARTIAL_OUTPUT, "only one terminal remainder is allowed", index)
    if terminal_partials:
        partial_index = terminal_partials[0]
        if any(index > partial_index for index, _ in applied) or any(
            index > partial_index for index, _ in updates.values()
        ):
            add(
                ReplayIssueCode.INVALID_PARTIAL_OUTPUT,
                "terminal partial output is followed by another command/output",
                partial_index,
            )


def _replacement_partial(index: int, payload: AppliedOutputPayload, ordered: tuple[ControlTraceRecord, ...]) -> bool:
    if payload.interval_end_ms != ordered[index].ts_ms:
        return False
    manual_replacement = False
    if payload.output_source in (OutputSource.CONTROLLER, OutputSource.MANUAL_OVERRIDE) and index + 1 < len(ordered):
        event = ordered[index + 1]
        manual_replacement = (
            event.ts_ms == payload.interval_end_ms
            and isinstance(event.payload, SafetyEventPayload)
            and event.payload.event is SafetyEventType.MANUAL_TAKEOVER
        )
    manual_release = False
    if payload.output_source is OutputSource.MANUAL_OVERRIDE and index:
        event = ordered[index - 1]
        manual_release = (
            event.ts_ms == payload.interval_end_ms
            and isinstance(event.payload, SafetyEventPayload)
            and event.payload.event is SafetyEventType.MANUAL_RELEASE
        )
    lid_replacement = False
    if payload.output_source in (OutputSource.CONTROLLER, OutputSource.LID_OPEN) and index:
        event = ordered[index - 1]
        lid_replacement = (
            event.ts_ms == payload.interval_end_ms
            and isinstance(event.payload, SafetyEventPayload)
            and event.payload.event is SafetyEventType.LID_DETECTED
        )
    return manual_replacement or manual_release or lid_replacement


def _validate_applied_source(
    payload: AppliedOutputPayload,
    safety_events: list[tuple[int, SafetyEventPayload]],
    add: IssueAdder,
    index: int,
) -> None:
    lid = manual = False
    for ts_ms, event in sorted(safety_events, key=lambda item: item[0]):
        if ts_ms > payload.interval_start_ms:
            continue
        if event.event is SafetyEventType.LID_DETECTED:
            lid = True
        elif event.event is SafetyEventType.LID_CLEARED:
            lid = False
        elif event.event is SafetyEventType.MANUAL_TAKEOVER:
            manual = True
        elif event.event is SafetyEventType.MANUAL_RELEASE:
            manual = False
    if payload.output_source is OutputSource.LID_OPEN and not lid:
        add(ReplayIssueCode.UNEXPLAINED_OUTPUT_SOURCE, "lid interval lacks a lid-detected event at its start", index)
    if payload.output_source is OutputSource.MANUAL_OVERRIDE and not manual:
        add(
            ReplayIssueCode.UNEXPLAINED_OUTPUT_SOURCE,
            "manual interval lacks a manual-takeover event at its start",
            index,
        )


def _reconcile_framed_applied_outputs(
    applied: list[tuple[int, AppliedOutputPayload]],
    framed_frames: list[tuple[int, FramedPulseFramePayload]],
    add: IssueAdder,
) -> None:
    for frame_index, frame in framed_frames:
        cursor_ms = frame.frame_start_ms
        delivered_on_seconds = 0.0
        incomplete = False
        fan_mismatch = False
        for _, payload in applied:
            if (
                payload.result_revision != frame.result_revision
                or payload.output_source is not OutputSource.CONTROLLER
                or payload.interval_end_ms <= cursor_ms
                or payload.interval_start_ms >= frame.frame_end_ms
            ):
                continue
            if payload.interval_start_ms > cursor_ms:
                break
            overlap_end_ms = min(payload.interval_end_ms, frame.frame_end_ms)
            overlap_seconds = (overlap_end_ms - cursor_ms) / 1000
            delivered_on_seconds += payload.realized_auger_duty * overlap_seconds
            incomplete = incomplete or not payload.sample_complete
            fan_mismatch = fan_mismatch or not _optional_close(payload.actual_fan_duty, frame.applied_fan_duty)
            cursor_ms = overlap_end_ms
            if cursor_ms == frame.frame_end_ms:
                break
        if incomplete:
            continue
        if cursor_ms != frame.frame_end_ms:
            add(
                ReplayIssueCode.APPLIED_OUTPUT_MISMATCH,
                "framed pulse lacks contiguous same-revision applied-output coverage",
                frame_index,
            )
            continue
        if not _close(delivered_on_seconds, frame.delivered_on_seconds):
            add(
                ReplayIssueCode.APPLIED_OUTPUT_MISMATCH,
                "framed pulse applied duty disagrees with delivered on-time",
                frame_index,
            )
        if fan_mismatch:
            add(
                ReplayIssueCode.APPLIED_OUTPUT_MISMATCH,
                "framed pulse applied fan disagrees with same-revision applied output",
                frame_index,
            )


def _close(left: float, right: float) -> bool:
    return abs(left - right) <= _FLOAT_TOLERANCE


def _multiple_of(value: float, unit: float) -> bool:
    return unit > 0 and _close(value / unit, round(value / unit))


def _optional_close(left: float | None, right: float | None) -> bool:
    return (left is None and right is None) or (left is not None and right is not None and _close(left, right))
