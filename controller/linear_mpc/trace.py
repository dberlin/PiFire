"""Canonical, lossless conversion of MPC control traces into learning frames."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

from common.control_trace import (
    TRACE_SCHEMA_VERSION,
    ActuationMode,
    AllocationPayload,
    AppliedOutputPayload,
    ControlTraceRecord,
    ControllerType,
    FramedPulseFramePayload,
    InhibitReason,
    ModelObservationPayload,
    MpcFailureState,
    MpcUpdatePayload,
    RecorderGapPayload,
    SafetyEventPayload,
    SafetyEventType,
    SessionPayload,
)
from controller.applied_output import OutputSource
from controller.control_trace_replay import validate_records
from controller.mpc_allocator import normalized_load_from_auger_duty

from .contracts import FrameObservation


class TraceSelectionError(ValueError):
    """A trace cannot provide one unambiguous learning history."""


def _to_c(value: float, unit: str) -> float:
    """Convert a recorded session scalar to Celsius without guessing units."""
    normalized = unit.strip().upper()
    if normalized == "C":
        return float(value)
    if normalized == "F":
        return (float(value) - 32.0) * 5.0 / 9.0
    raise TraceSelectionError(f"unsupported recorded temperature unit {unit!r}")


def learning_observations(records: Iterable[ControlTraceRecord]) -> tuple[FrameObservation, ...]:
    """Return exact learning events, or a strictly reconstructable legacy history.

    The fallback deliberately accepts only complete framed-pulse frames paired with
    one controller-owned, same-revision update at the frame endpoint.  Anything
    less is evidence of an unknown input or temperature and must not be filled in.
    """
    trace = tuple(records)
    session = _validate_session(trace)
    exact = tuple(record.payload for record in trace if isinstance(record.payload, ModelObservationPayload))
    if exact:
        return _exact_observations(exact, _allocation_payloads(trace))
    return _fallback_observations(trace, session)



def _allocation_payloads(trace: tuple[ControlTraceRecord, ...]) -> dict[int, list[AllocationPayload]]:
    allocations: dict[int, list[AllocationPayload]] = {}
    for record in trace:
        if isinstance(payload := record.payload, AllocationPayload):
            allocations.setdefault(payload.result_revision, []).append(payload)
    return allocations

def _validate_session(records: tuple[ControlTraceRecord, ...]) -> SessionPayload:
    if not records:
        raise TraceSelectionError("selected control trace contains no records")
    if any(record.schema_version != TRACE_SCHEMA_VERSION for record in records):
        raise TraceSelectionError("selected control trace has an evidence-incompatible schema version")
    if any(record.controller is not ControllerType.MPC for record in records):
        raise TraceSelectionError("selected control trace mixes controller types")
    if any(isinstance(record.payload, RecorderGapPayload) for record in records):
        raise TraceSelectionError("selected control trace contains a recorder gap")
    if len({record.session_id for record in records}) != 1:
        raise TraceSelectionError("selected records contain more than one control session")
    if any(right.ts_ms < left.ts_ms for left, right in zip(records, records[1:])):
        raise TraceSelectionError("selected control trace timestamps are not ordered")
    sessions = tuple(record.payload for record in records if isinstance(record.payload, SessionPayload))
    if len(sessions) != 1 or sessions[0].controller is not ControllerType.MPC:
        raise TraceSelectionError("selected records do not describe exactly one MPC trace session")
    _to_c(sessions[0].ambient_temperature, sessions[0].temperature_unit)
    _to_c(sessions[0].setpoint, sessions[0].temperature_unit)
    return sessions[0]


def _exact_observations(
    payloads: tuple[ModelObservationPayload, ...],
    allocations: dict[int, list[AllocationPayload]],
) -> tuple[FrameObservation, ...]:
    frames: list[FrameObservation] = []
    previous_end_ms = -1
    previous_sequence = -1
    for payload in payloads:
        if previous_end_ms >= 0 and payload.frame_start_ms != previous_end_ms:
            raise TraceSelectionError("model observation intervals are not contiguous")
        if payload.observation_sequence <= previous_sequence:
            raise TraceSelectionError("model observation sequences are not ordered")
        matching_allocations = allocations.get(payload.result_revision, [])
        if not matching_allocations:
            raise TraceSelectionError("missing-allocation")
        if len(matching_allocations) != 1:
            raise TraceSelectionError("ambiguous-allocation")
        allocation = matching_allocations[0]
        expected_duty = payload.delivered_on_seconds / 20.0
        expected_load = normalized_load_from_auger_duty(payload.realized_auger_duty, u_max=allocation.u_max)
        if (
            allocation.allocator_revision != payload.allocator_revision
            or not math.isclose(payload.allocated_combustion_load, allocation.normalized_combustion_load, rel_tol=0, abs_tol=1e-9)
            or not math.isclose(payload.requested_auger_duty, allocation.requested_auger_duty, rel_tol=0, abs_tol=1e-9)
            or not math.isclose(payload.realized_auger_duty, expected_duty, rel_tol=0, abs_tol=1e-9)
            or not math.isclose(payload.realized_combustion_load, expected_load, rel_tol=0, abs_tol=1e-9)
        ):
            raise TraceSelectionError("model observation allocation evidence does not match completed delivery")
        required = (
            payload.result_revision,
            payload.requested_auger_duty,
            payload.output_source,
            payload.lid_open,
            payload.safety_inhibited,
            payload.manual_override,
            payload.stale,
            payload.skipped,
            payload.reset,
            payload.continuous,
        )
        if any(value is None for value in required):
            raise TraceSelectionError("model observation omits required gate or actuation evidence")
        frames.append(
            FrameObservation(
                frame_start_s=payload.frame_start_ms / 1000.0,
                frame_end_s=payload.frame_end_ms / 1000.0,
                temp_c=payload.temp_c,
                setpoint_c=payload.setpoint_c,
                ambient_c=payload.ambient_c,
                requested_q=payload.requested_combustion_load,
                realized_q=payload.realized_combustion_load,
                requested_auger_duty=payload.requested_auger_duty,
                delivered_on_s=payload.delivered_on_seconds,
                requested_fan_duty=payload.requested_fan_duty,
                actual_fan_duty=payload.actual_fan_duty,
                result_revision=payload.result_revision,
                output_source=payload.output_source.value,
                lid_open=payload.lid_open,
                safety_inhibited=payload.safety_inhibited,
                manual_override=payload.manual_override,
                stale=payload.stale,
                skipped=payload.skipped,
                reset=payload.reset,
                role_generation=payload.role_generation,
                continuous=payload.continuous,
                observation_sequence=payload.observation_sequence,
                probe_valid=payload.probe_valid,
                probe_source=payload.probe_source,
                ambient_source=payload.ambient_source,
                ambient_uncertainty=payload.ambient_uncertainty,
                baseline_q=payload.baseline_combustion_load,
                probe_q=payload.calibration_probe_load,
                allocated_q=payload.allocated_combustion_load,
                scheduled_on_s=payload.scheduled_on_seconds,
                realized_auger_duty=payload.realized_auger_duty,
                allocator_revision=payload.allocator_revision,
                allocation_clamp_reasons=payload.allocation_clamp_reasons,
                calibration_stage=payload.calibration_stage,
                calibration_fit=payload.calibration_fit,
            )
        )
        previous_end_ms = payload.frame_end_ms
        previous_sequence = payload.observation_sequence
    return tuple(frames)


def _terminal_safety_tail_output_index(records: tuple[ControlTraceRecord, ...]) -> int | None:
    """Return the sole applied interval cut short by a terminal safety reset."""
    if len(records) < 3:
        return None
    output_record, safety_record, frame_record = records[-3:]
    output = output_record.payload
    safety = safety_record.payload
    frame = frame_record.payload
    if not (
        isinstance(output, AppliedOutputPayload)
        and output.result_revision > 0
        and output.sample_complete
        and output.output_source is OutputSource.CONTROLLER
        and isinstance(safety, SafetyEventPayload)
        and safety.event is SafetyEventType.SCHEDULER_RESET
        and safety.inhibit_reason is InhibitReason.SAFETY
        and safety.result_revision == output.result_revision
        and isinstance(frame, FramedPulseFramePayload)
        and frame.result_revision == output.result_revision
        and frame.inhibit_reason is InhibitReason.SAFETY
        and not frame.skipped
        and frame.reset_reason is not None
        and frame.frame_start_ms == output.interval_start_ms
        and frame.frame_end_ms == output.interval_end_ms
        and frame.frame_end_ms - frame.frame_start_ms < frame.frame_seconds * 1_000
        and output_record.ts_ms == safety_record.ts_ms == frame_record.ts_ms == output.interval_end_ms
    ):
        return None
    if any(
        isinstance(record.payload, FramedPulseFramePayload)
        and record.payload.result_revision == output.result_revision
        and record.payload.inhibit_reason is InhibitReason.NONE
        and not record.payload.skipped
        and not record.payload.stale_command
        and record.payload.reset_reason is None
        and math.isclose(
            (record.payload.frame_end_ms - record.payload.frame_start_ms) / 1000.0,
            record.payload.frame_seconds,
            rel_tol=0,
            abs_tol=1e-6,
        )
        and record.payload.frame_start_ms <= output.interval_start_ms
        and output.interval_end_ms <= record.payload.frame_end_ms
        for record in records
    ):
        return None
    return len(records) - 3


def _validate_framed_timeline(frames: dict[int, list[tuple[int, FramedPulseFramePayload]]]) -> None:
    latest_end_ms: int | None = None
    for _, frame in sorted(
        (item for revision_frames in frames.values() for item in revision_frames),
        key=lambda item: (item[1].frame_start_ms, item[1].frame_end_ms, item[0]),
    ):
        if latest_end_ms is not None and frame.frame_start_ms < latest_end_ms:
            raise TraceSelectionError("selected control trace has overlapping framed intervals")
        latest_end_ms = frame.frame_end_ms if latest_end_ms is None else max(latest_end_ms, frame.frame_end_ms)


@dataclass(frozen=True, slots=True)
class CalibrationSample:
    """One trusted legacy calibration sample in the session's canonical unit."""

    time_s: float
    temp_c: float
    combustion_load: float
    ambient_c: float


def calibration_samples(records: Iterable[ControlTraceRecord]) -> tuple[CalibrationSample, ...]:
    """Convert one trace session into the historical grey-box calibration samples.

    This is the former offline loader's complete update/output join, relocated so
    offline calibration and online replay share session validation and unit
    conversion. Exact learning events take precedence when available.
    """
    trace = tuple(records)
    session = _validate_session(trace)
    exact = tuple(record.payload for record in trace if isinstance(record.payload, ModelObservationPayload))
    if exact and any(
        not payload.eligible
        or payload.calibration_fit
        or payload.output_source is not OutputSource.CONTROLLER
        or payload.lid_open is not False
        or payload.safety_inhibited is not False
        or payload.manual_override is not False
        or payload.stale is not False
        or payload.skipped is not False
        or payload.reset is not False
        or payload.continuous is not True
        for payload in exact
    ):
        raise TraceSelectionError("exact learning evidence is not eligible controller-owned continuous input")
    if exact:
        frames = _exact_observations(exact, _allocation_payloads(trace))
        start_s = frames[0].frame_end_s if frames else 0.0
        return tuple(
            CalibrationSample(
                frame.frame_end_s - start_s,
                frame.temp_c,
                frame.realized_q,
                frame.ambient_c,
            )
            for frame in frames
        )

    terminal_safety_tail_output_index = _terminal_safety_tail_output_index(trace)
    updates: list[tuple[int, MpcUpdatePayload]] = []
    allocations: dict[int, tuple[int, AllocationPayload]] = {}
    frames: dict[int, list[tuple[int, FramedPulseFramePayload]]] = {}
    complete_outputs: dict[int, list[tuple[int, AppliedOutputPayload]]] = {}
    terminal_safety_tail_output: tuple[int, AppliedOutputPayload] | None = None
    seed_outputs = 0
    partial_outputs: list[tuple[int, AppliedOutputPayload]] = []
    actuated_revisions: set[int] = set()
    previous_revision = -1
    previous_wall_ms = -1
    seed_allowed = False
    for index, record in enumerate(trace):
        payload = record.payload
        if isinstance(payload, SessionPayload):
            seed_allowed = True
            continue
        if isinstance(payload, AppliedOutputPayload) and payload.result_revision == 0:
            seed_outputs += 1
            if (
                not seed_allowed
                or seed_outputs != 1
                or payload.output_source is not OutputSource.SEED
                or not payload.sample_complete
            ):
                raise TraceSelectionError("revision-zero applied output must be the one complete initial seed")
            seed_allowed = False
            continue
        if isinstance(payload, MpcUpdatePayload):
            revision = payload.result_revision
            if revision <= previous_revision:
                raise TraceSelectionError("MPC control-update revisions are not strictly ordered")
            if payload.wall_ms < previous_wall_ms:
                raise TraceSelectionError("MPC control-update timestamps are not ordered")
            if payload.failure_state is not MpcFailureState.SUCCESS:
                raise TraceSelectionError(f"MPC revision {revision} did not complete successfully")
            if payload.stale:
                raise TraceSelectionError(f"MPC revision {revision} is stale")
            if payload.inhibit_reason is not InhibitReason.NONE:
                raise TraceSelectionError(f"MPC revision {revision} is inhibited")
            if payload.output_source is not OutputSource.CONTROLLER:
                raise TraceSelectionError(f"MPC revision {revision} has a non-controller output source")
            updates.append((index, payload))
            previous_revision = revision
            previous_wall_ms = payload.wall_ms
        elif isinstance(payload, AllocationPayload):
            if payload.result_revision in allocations:
                raise TraceSelectionError(f"MPC revision {payload.result_revision} has duplicate allocations")
            allocations[payload.result_revision] = (index, payload)
        elif isinstance(payload, FramedPulseFramePayload):
            seed_allowed = False
            frames.setdefault(payload.result_revision, []).append((index, payload))
            if (
                payload.result_revision > 0
                and payload.inhibit_reason is InhibitReason.NONE
                and not payload.skipped
                and not payload.stale_command
            ):
                actuated_revisions.add(payload.result_revision)
        elif isinstance(payload, AppliedOutputPayload):
            seed_allowed = False
            revision = payload.result_revision
            if payload.output_source is not OutputSource.CONTROLLER:
                raise TraceSelectionError(f"MPC revision {revision} is inhibited by {payload.output_source.value}")
            if payload.sample_complete:
                if payload.realized_combustion_load is None:
                    raise TraceSelectionError(f"MPC revision {revision} has no realized combustion load")
                if payload.interval_end_ms <= payload.interval_start_ms:
                    raise TraceSelectionError(
                        f"MPC revision {revision} complete applied output must span a positive interval"
                    )
                if index == terminal_safety_tail_output_index:
                    terminal_safety_tail_output = (index, payload)
                else:
                    complete_outputs.setdefault(revision, []).append((index, payload))
            else:
                if payload.realized_combustion_load is not None:
                    raise TraceSelectionError(
                        f"MPC revision {revision} has a malformed incomplete applied-output interval"
                    )
                partial_outputs.append((index, payload))
    _validate_framed_timeline(frames)

    if len(updates) < 2:
        raise TraceSelectionError("selected control trace requires at least two completed MPC control updates")
    update_indices = {payload.result_revision: index for index, payload in updates}
    for revision, (_, allocation) in allocations.items():
        if revision not in update_indices:
            raise TraceSelectionError(f"MPC allocation revision {revision} does not match an accepted MPC update")
    for revision, revision_frames in frames.items():
        if revision not in update_indices:
            raise TraceSelectionError(f"MPC framed pulse revision {revision} does not match an accepted MPC update")
        allocation_entry = allocations.get(revision)
        if allocation_entry is None:
            raise TraceSelectionError(f"MPC revision {revision} framed pulse has no allocation")
        allocation = allocation_entry[1]
        for _, frame in revision_frames:
            if not (
                math.isclose(
                    frame.requested_combustion_load,
                    allocation.normalized_combustion_load,
                    rel_tol=0,
                    abs_tol=1e-6,
                )
                and math.isclose(
                    frame.requested_auger_duty,
                    allocation.requested_auger_duty,
                    rel_tol=0,
                    abs_tol=1e-6,
                )
                and frame.requested_fan_duty == allocation.requested_fan_duty
            ):
                raise TraceSelectionError(f"MPC revision {revision} framed pulse does not match its allocation")

    latest_revision = updates[-1][1].result_revision
    if partial_outputs:
        if len(partial_outputs) != 1:
            raise TraceSelectionError("selected control trace has multiple incomplete applied-output intervals")
        partial_index, partial = partial_outputs[0]
        if partial.result_revision != latest_revision:
            raise TraceSelectionError("terminal incomplete applied-output interval does not match its latest update")
        if partial_index <= updates[-1][0]:
            raise TraceSelectionError("terminal incomplete applied-output interval does not follow its latest update")
        if any(
            isinstance(record.payload, (MpcUpdatePayload, AppliedOutputPayload))
            for record in trace[partial_index + 1 :]
        ):
            raise TraceSelectionError("terminal incomplete applied-output interval has a later update or output")
        if complete_outputs:
            latest_complete = max(
                (item for revision_outputs in complete_outputs.values() for item in revision_outputs),
                key=lambda item: item[0],
            )[1]
            if partial.interval_start_ms < latest_complete.interval_end_ms:
                raise TraceSelectionError(
                    "terminal incomplete applied-output interval does not follow its complete interval"
                )

    for revision, revision_outputs in complete_outputs.items():
        if revision not in update_indices:
            raise TraceSelectionError("complete applied output does not match an accepted MPC update")
        revision_frames = frames.get(revision, ())
        previous_end_ms: int | None = None
        for output_index, output in revision_outputs:
            if output_index <= update_indices[revision]:
                raise TraceSelectionError("complete applied output must follow its accepted update")
            if not any(
                frame.inhibit_reason is InhibitReason.NONE
                and not frame.skipped
                and not frame.stale_command
                and frame.reset_reason is None
                and math.isclose(
                    (frame.frame_end_ms - frame.frame_start_ms) / 1000.0,
                    frame.frame_seconds,
                    rel_tol=0,
                    abs_tol=1e-6,
                )
                and frame.frame_start_ms <= output.interval_start_ms
                and output.interval_end_ms <= frame.frame_end_ms
                for _, frame in revision_frames
            ):
                raise TraceSelectionError(
                    f"MPC revision {revision} complete applied output does not belong to a complete framed interval"
                )
            allocation = allocations[revision][1]
            delivered_load = min(1.0, max(0.0, output.realized_auger_duty / allocation.u_max))
            if not math.isclose(float(output.realized_combustion_load), delivered_load, rel_tol=0, abs_tol=1e-6):
                raise TraceSelectionError(
                    f"MPC revision {revision} realized combustion load does not match applied auger duty"
                )
            if previous_end_ms is not None and output.interval_start_ms != previous_end_ms:
                raise TraceSelectionError("repeated complete applied outputs must cover contiguous intervals")
            previous_end_ms = output.interval_end_ms
    previous_end_ms: int | None = None
    for _, output in sorted(
        (item for revision_outputs in complete_outputs.values() for item in revision_outputs),
        key=lambda item: item[0],
    ):
        if previous_end_ms is not None and output.interval_start_ms != previous_end_ms:
            raise TraceSelectionError("complete framed applied-output intervals must be globally contiguous")
        previous_end_ms = output.interval_end_ms
    if terminal_safety_tail_output is not None:
        output_index, output = terminal_safety_tail_output
        revision = output.result_revision
        if revision not in update_indices:
            raise TraceSelectionError("complete applied output does not match an accepted MPC update")
        if output_index <= update_indices[revision]:
            raise TraceSelectionError("complete applied output must follow its accepted update")
        allocation_entry = allocations.get(revision)
        if allocation_entry is None:
            raise TraceSelectionError(f"MPC revision {revision} terminal safety-reset output has no allocation")
        delivered_load = min(1.0, max(0.0, output.realized_auger_duty / allocation_entry[1].u_max))
        if not math.isclose(float(output.realized_combustion_load), delivered_load, rel_tol=0, abs_tol=1e-6):
            raise TraceSelectionError(
                f"MPC revision {revision} realized combustion load does not match applied auger duty"
            )
        if previous_end_ms is None or output.interval_start_ms != previous_end_ms:
            raise TraceSelectionError("terminal safety-reset output does not follow its complete interval")

    terminal_partial_revision = partial_outputs[0][1].result_revision if partial_outputs else None
    missing_actuated_revisions = sorted(
        revision
        for revision in actuated_revisions
        if revision in update_indices and revision not in complete_outputs and revision != terminal_partial_revision
    )
    if missing_actuated_revisions:
        raise TraceSelectionError(
            f"MPC revision {missing_actuated_revisions[0]} was actuated without a complete framed interval"
        )

    def realized_load(revision_outputs: list[tuple[int, AppliedOutputPayload]]) -> float:
        if len(revision_outputs) == 1:
            return float(revision_outputs[0][1].realized_combustion_load)
        duration_ms = sum(output.interval_end_ms - output.interval_start_ms for _, output in revision_outputs)
        return (
            sum(
                float(output.realized_combustion_load) * (output.interval_end_ms - output.interval_start_ms)
                for _, output in revision_outputs
            )
            / duration_ms
        )

    replay = validate_records(trace)
    if not replay.valid:
        first_issue = next(issue for issue in replay.issues if issue.severity.value == "error")
        raise TraceSelectionError(f"selected control trace has invalid framed relationships: {first_issue.detail}")
    paired_updates = [
        (index, update)
        for index, update in updates
        if update.result_revision in complete_outputs and update.result_revision != terminal_partial_revision
    ]
    if len(paired_updates) < 2:
        raise TraceSelectionError("selected control trace requires at least two complete framed MPC control updates")
    start_ms = paired_updates[0][1].wall_ms
    ambient_c = _to_c(session.ambient_temperature, session.temperature_unit)
    return tuple(
        CalibrationSample(
            (update.wall_ms - start_ms) / 1000.0,
            _to_c(update.measured_temperature, session.temperature_unit),
            realized_load(complete_outputs[update.result_revision]),
            ambient_c,
        )
        for _, update in paired_updates
    )


_LEGACY_U_MAX_REL_TOLERANCE = 1e-9


def _legacy_latched_u_max(frames: tuple[FramedPulseFramePayload, ...]) -> float | None:
    """Recover the one normalized-load-to-duty scale carried by legacy frames."""
    u_max: float | None = None
    for frame in frames:
        requested_q = frame.requested_combustion_load
        requested_duty = frame.requested_auger_duty
        delivered_on_s = frame.delivered_on_seconds
        if not (0.0 <= requested_q <= 1.0 and 0.0 <= requested_duty <= 1.0 and math.isfinite(delivered_on_s)):
            raise TraceSelectionError("legacy framed-pulse input evidence is out of bounds")
        if requested_q == 0.0:
            if requested_duty != 0.0:
                raise TraceSelectionError("legacy framed-pulse input scale is unidentifiable")
            continue
        if requested_duty == 0.0:
            raise TraceSelectionError("legacy framed-pulse input scale is unidentifiable")
        candidate = requested_duty / requested_q
        if not 0.0 < candidate <= 1.0 or not math.isfinite(candidate):
            raise TraceSelectionError("legacy framed-pulse input scale is invalid")
        if u_max is not None and not math.isclose(candidate, u_max, rel_tol=_LEGACY_U_MAX_REL_TOLERANCE, abs_tol=0.0):
            raise TraceSelectionError("legacy framed-pulse input scale is inconsistent")
        u_max = candidate
    if u_max is None and any(frame.delivered_on_seconds != 0.0 for frame in frames):
        raise TraceSelectionError("legacy framed-pulse input scale is unidentifiable")
    return u_max


def _fallback_observations(
    records: tuple[ControlTraceRecord, ...], session: SessionPayload
) -> tuple[FrameObservation, ...]:
    updates: dict[int, list[MpcUpdatePayload]] = {}
    if any(
        isinstance(record.payload, AppliedOutputPayload) and not record.payload.sample_complete for record in records
    ):
        raise TraceSelectionError("selected control trace contains a partial applied-output interval")
    frames = tuple(record.payload for record in records if isinstance(record.payload, FramedPulseFramePayload))
    if not frames:
        raise TraceSelectionError("selected control trace has no exact learning observations or framed-pulse frames")
    u_max = _legacy_latched_u_max(frames)
    for record in records:
        if isinstance(record.payload, MpcUpdatePayload):
            updates.setdefault(record.payload.result_revision, []).append(record.payload)

    observations: list[FrameObservation] = []
    previous_end_ms = -1
    used_revisions: set[int] = set()
    for frame in frames:
        duration_ms = frame.frame_end_ms - frame.frame_start_ms
        if not math.isclose(duration_ms / 1000.0, frame.frame_seconds, rel_tol=0.0, abs_tol=1e-9):
            raise TraceSelectionError("framed-pulse interval is partial")
        if previous_end_ms >= 0 and frame.frame_start_ms != previous_end_ms:
            raise TraceSelectionError("framed-pulse intervals are not contiguous")
        if frame.skipped or frame.stale_command or frame.inhibit_reason is not InhibitReason.NONE:
            raise TraceSelectionError(f"MPC revision {frame.result_revision} frame is not complete")
        candidates = updates.get(frame.result_revision, [])
        if len(candidates) != 1 or frame.result_revision in used_revisions:
            raise TraceSelectionError(f"MPC revision {frame.result_revision} is ambiguous")
        update = candidates[0]
        if update.wall_ms != frame.frame_end_ms:
            raise TraceSelectionError(f"MPC revision {frame.result_revision} lacks a frame-end temperature")
        if (
            update.failure_state is not MpcFailureState.SUCCESS
            or update.stale
            or update.inhibit_reason is not InhibitReason.NONE
            or update.output_source is not OutputSource.CONTROLLER
            or update.actuation_mode is not ActuationMode.FRAMED_PULSE
        ):
            raise TraceSelectionError(f"MPC revision {frame.result_revision} has an unknown output source")
        observations.append(
            FrameObservation(
                frame_start_s=frame.frame_start_ms / 1000.0,
                frame_end_s=frame.frame_end_ms / 1000.0,
                temp_c=_to_c(update.measured_temperature, session.temperature_unit),
                setpoint_c=_to_c(update.setpoint, session.temperature_unit),
                ambient_c=_to_c(session.ambient_temperature, session.temperature_unit),
                requested_q=frame.requested_combustion_load,
                realized_q=(
                    0.0
                    if u_max is None
                    else normalized_load_from_auger_duty(frame.delivered_on_seconds / frame.frame_seconds, u_max=u_max)
                ),
                requested_auger_duty=frame.requested_auger_duty,
                delivered_on_s=frame.delivered_on_seconds,
                realized_auger_duty=frame.delivered_on_seconds / frame.frame_seconds,
                requested_fan_duty=frame.requested_fan_duty,
                actual_fan_duty=frame.applied_fan_duty,
                result_revision=frame.result_revision,
                output_source=update.output_source.value,
                lid_open=False,
                safety_inhibited=False,
                manual_override=False,
                stale=False,
                skipped=False,
                reset=frame.reset_reason is not None,
                continuous=not frame.stale_command,
                role_generation=0,
                observation_sequence=len(observations) + 1,
            )
        )
        previous_end_ms = frame.frame_end_ms
        used_revisions.add(frame.result_revision)
    return tuple(observations)
