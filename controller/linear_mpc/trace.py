"""Canonical, lossless conversion of MPC control traces into learning frames."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

from common.control_trace import (
    TRACE_SCHEMA_VERSION,
    ActuationMode,
    AppliedOutputPayload,
    ControllerType,
    FramedPulseFramePayload,
    InhibitReason,
    ModelObservationPayload,
    MpcFailureState,
    MpcUpdatePayload,
    RecorderGapPayload,
    SessionPayload,
    ControlTraceRecord,
)
from controller.applied_output import OutputSource
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
        return _exact_observations(exact)
    return _fallback_observations(trace, session)


def _validate_session(records: tuple[ControlTraceRecord, ...]) -> SessionPayload:
    if not records:
        raise TraceSelectionError("selected control trace contains no records")
    if any(record.schema_version not in (2, TRACE_SCHEMA_VERSION) for record in records):
        raise TraceSelectionError("selected control trace has an incompatible schema version")
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


def _exact_observations(payloads: tuple[ModelObservationPayload, ...]) -> tuple[FrameObservation, ...]:
    frames: list[FrameObservation] = []
    previous_end_ms = -1
    for payload in payloads:
        if previous_end_ms >= 0 and payload.frame_start_ms != previous_end_ms:
            raise TraceSelectionError("model observation intervals are not contiguous")
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
                continuous=payload.continuous,
                role_generation=payload.role_generation,
            )
        )
        previous_end_ms = payload.frame_end_ms
    return tuple(frames)


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
        frames = _exact_observations(exact)
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

    updates: list[tuple[int, MpcUpdatePayload]] = []
    complete_outputs: dict[int, list[tuple[int, AppliedOutputPayload]]] = {}
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
                raise TraceSelectionError("revision zero applied output must be the one complete initial seed")
            seed_allowed = False
            continue
        if isinstance(payload, MpcUpdatePayload):
            revision = payload.result_revision
            if revision <= previous_revision:
                raise TraceSelectionError("MPC control-update revisions are not strictly ordered")
            if payload.wall_ms < previous_wall_ms:
                raise TraceSelectionError("MPC control-update timestamps are not ordered")
            if payload.failure_state is not MpcFailureState.SUCCESS or payload.stale:
                raise TraceSelectionError(f"MPC revision {revision} is incomplete")
            if payload.inhibit_reason is not InhibitReason.NONE:
                raise TraceSelectionError(f"MPC revision {revision} is inhibited")
            updates.append((index, payload))
            previous_revision = revision
            previous_wall_ms = payload.wall_ms
        elif isinstance(payload, AppliedOutputPayload):
            seed_allowed = False
            revision = payload.result_revision
            if payload.output_source is not OutputSource.CONTROLLER:
                raise TraceSelectionError(f"MPC revision {revision} is inhibited by {payload.output_source.value}")
            if payload.sample_complete:
                if payload.realized_combustion_load is None:
                    raise TraceSelectionError(f"MPC revision {revision} has no realized combustion load")
                complete_outputs.setdefault(revision, []).append((index, payload))
            else:
                if payload.realized_combustion_load is not None:
                    raise TraceSelectionError(
                        f"MPC revision {revision} has a malformed incomplete applied-output interval"
                    )
                partial_outputs.append((index, payload))
        elif isinstance(payload, FramedPulseFramePayload):
            if payload.result_revision > 0:
                seed_allowed = False
                if payload.inhibit_reason is InhibitReason.NONE and not payload.skipped:
                    actuated_revisions.add(payload.result_revision)

    if len(updates) < 2:
        raise TraceSelectionError("selected control trace requires at least two completed MPC control updates")
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

    update_indices = {payload.result_revision: index for index, payload in updates}
    for revision, revision_outputs in complete_outputs.items():
        if revision not in update_indices:
            raise TraceSelectionError("complete applied output does not match an accepted MPC update")
        previous_end_ms: int | None = None
        for output_index, output in revision_outputs:
            if output_index <= update_indices[revision]:
                raise TraceSelectionError("complete applied output must follow its accepted update")
            duration_ms = output.interval_end_ms - output.interval_start_ms
            if duration_ms < 0:
                raise TraceSelectionError("complete applied output interval must not run backwards")
            if len(revision_outputs) > 1 and duration_ms == 0:
                raise TraceSelectionError("repeated complete applied outputs must span positive intervals")
            if previous_end_ms is not None and output.interval_start_ms != previous_end_ms:
                raise TraceSelectionError("repeated complete applied outputs must cover contiguous intervals")
            previous_end_ms = output.interval_end_ms

    terminal_partial_revision = partial_outputs[0][1].result_revision if partial_outputs else None
    missing_actuated_revisions = sorted(
        revision
        for revision in actuated_revisions
        if revision in update_indices and revision not in complete_outputs and revision != terminal_partial_revision
    )
    if missing_actuated_revisions:
        raise TraceSelectionError(
            f"MPC revision {missing_actuated_revisions[0]} was actuated without a complete applied output"
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

    paired_updates = [(index, update) for index, update in updates if update.result_revision in complete_outputs]
    if len(paired_updates) < 2:
        raise TraceSelectionError("selected control trace requires at least two applied MPC control updates")
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
            )
        )
        previous_end_ms = frame.frame_end_ms
        used_revisions.add(frame.result_revision)
    return tuple(observations)
