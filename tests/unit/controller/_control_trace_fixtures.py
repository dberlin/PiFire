"""Canonical current-schema control-trace sequences for controller tests."""

from common.control_trace import (
    TRACE_SCHEMA_VERSION,
    ActuationMode,
    AllocationClampReason,
    AllocationPayload,
    AppliedOutputPayload,
    ControllerBranch,
    ControllerType,
    ControlTraceRecord,
    FramedPulseFramePayload,
    InhibitReason,
    PidSpUpdatePayload,
    SessionPayload,
    TraceEventKind,
    TraceSetting,
)
from controller.applied_output import OutputSource
from controller.mpc_allocator import allocate, normalized_load_from_auger_duty
from controller.runtime.logic.pulse import PulseScheduler
from grillplat.actuator_capabilities import AUGER_TIMING


def current_pid_sp_records(
    *,
    session_id: str = "session-1",
    cook_id: str = "cook-1",
    revision: int = 1,
    raw_demand: float = 0.5,
    include_frame: bool = False,
) -> tuple[ControlTraceRecord, ...]:
    """Build one valid current PID-SP result lifecycle from one allocation."""
    diagnostic_raw_demand = float(raw_demand)
    bounded_demand = min(1.0, max(0.0, diagnostic_raw_demand))
    allocation = allocate(
        bounded_demand,
        u_max=1.0,
        fan_min_pct=0.0,
        fan_max_pct=0.0,
        enable_fan=False,
    )
    auger_clamp_reason = allocation.auger_clamp_reason
    if diagnostic_raw_demand < 0.0:
        auger_clamp_reason = AllocationClampReason.AUGER_MIN
    elif diagnostic_raw_demand > 1.0:
        auger_clamp_reason = AllocationClampReason.AUGER_MAX

    control_ms = revision * 2_000
    pulse_slot_seconds = float(AUGER_TIMING.pulse_s)
    frame_seconds = float(AUGER_TIMING.frame_s)
    frame_end_ms = control_ms + int(frame_seconds * 1_000)
    applied_end_ms = frame_end_ms if include_frame else control_ms + 2_000
    realized_auger_duty = allocation.auger_duty
    realized_combustion_load = allocation.normalized_combustion_load
    if include_frame:
        pulse = PulseScheduler(AUGER_TIMING).advance(
            allocation.auger_duty,
            at_s=0.0,
            actual_auger_on=False,
        )
        credit_after_seconds = pulse.credit_s
        scheduled_on_seconds = float(pulse.scheduled_on_s)
        realized_auger_duty = scheduled_on_seconds / frame_seconds
        realized_combustion_load = normalized_load_from_auger_duty(
            realized_auger_duty,
            u_max=allocation.u_max,
        )

    session = ControlTraceRecord(
        ts_ms=0,
        session_id=session_id,
        cook_id=cook_id,
        controller=ControllerType.PID_SP,
        event_kind=TraceEventKind.SESSION,
        schema_version=TRACE_SCHEMA_VERSION,
        payload=SessionPayload(
            controller=ControllerType.PID_SP,
            controller_config=(TraceSetting(key="kp", value=1.0),),
            temperature_unit="F",
            control_period_seconds=2.0,
            model_revision=None,
            model_provenance=None,
            pulse_slot_seconds=pulse_slot_seconds,
            pulse_frame_seconds=frame_seconds,
            fan_authority=False,
            fan_pwm_capable=False,
            fan_min_duty=allocation.fan_min_pct,
            fan_max_duty=allocation.fan_max_pct,
            setpoint=225.0,
            ambient_temperature=70.0,
            software_version="test",
            build_version="1",
        ),
    )
    update = ControlTraceRecord(
        ts_ms=control_ms,
        session_id=session_id,
        cook_id=cook_id,
        controller=ControllerType.PID_SP,
        event_kind=TraceEventKind.CONTROL_UPDATE,
        schema_version=TRACE_SCHEMA_VERSION,
        payload=PidSpUpdatePayload(
            monotonic_ms=control_ms,
            wall_ms=control_ms,
            result_revision=revision,
            result_age_ms=0,
            control_period_seconds=2.0,
            observed_dt_seconds=2.0,
            setpoint=225.0,
            measured_temperature=220.0,
            raw_output=diagnostic_raw_demand,
            requested_output=allocation.normalized_combustion_load,
            actuation_mode=ActuationMode.FRAMED_PULSE,
            prior_requested_auger_duty=allocation.auger_duty,
            prior_realized_auger_duty=allocation.auger_duty,
            requested_fan_duty=allocation.fan_duty,
            applied_fan_duty=allocation.fan_duty,
            output_source=OutputSource.CONTROLLER,
            inhibit_reason=InhibitReason.NONE,
            error=5.0,
            proportional_term=0.2,
            integral_term=0.2,
            derivative_term=0.1,
            integral_accumulator=0.2,
            integral_clamped=False,
            derivative_input=0.0,
            derivative_state=0.0,
            proportional_band=100.0,
            kp=1.0,
            ki=0.1,
            kd=0.0,
            center=225.0,
            previous_temperature=219.0,
            previous_update_ms=max(0, control_ms - 2_000),
            measured_rate=0.0,
            predicted_temperature=220.0,
            predicted_error=5.0,
            tau_seconds=20.0,
            theta_seconds=2.0,
            stable_window_seconds=10.0,
            center_factor=1.0,
            new_target_before=False,
            new_target_after=False,
            target_change_temperature=220.0,
            target_change_ms=0,
            branch=ControllerBranch.NONE,
        ),
    )
    allocation_record = ControlTraceRecord(
        ts_ms=control_ms,
        session_id=session_id,
        cook_id=cook_id,
        controller=ControllerType.PID_SP,
        event_kind=TraceEventKind.ALLOCATION,
        schema_version=TRACE_SCHEMA_VERSION,
        payload=AllocationPayload(
            result_revision=revision,
            normalized_combustion_load=allocation.normalized_combustion_load,
            requested_auger_duty=allocation.auger_duty,
            requested_fan_duty=allocation.fan_duty,
            u_max=allocation.u_max,
            fan_min_pct=allocation.fan_min_pct,
            fan_max_pct=allocation.fan_max_pct,
            fan_enabled=allocation.fan_enabled,
            mpc_has_fan_authority=False,
            auger_clamp_reason=auger_clamp_reason,
            fan_clamp_reason=allocation.fan_clamp_reason,
            allocator_revision=allocation.allocator_revision,
        ),
    )

    records = [session, update, allocation_record]
    if include_frame:
        active_for_entire_frame = scheduled_on_seconds == frame_seconds
        frame = ControlTraceRecord(
            ts_ms=frame_end_ms,
            session_id=session_id,
            cook_id=cook_id,
            controller=ControllerType.PID_SP,
            event_kind=TraceEventKind.ACTUATION_FRAME,
            schema_version=TRACE_SCHEMA_VERSION,
            payload=FramedPulseFramePayload(
                result_revision=revision,
                pulse_slot_seconds=pulse_slot_seconds,
                frame_seconds=frame_seconds,
                frame_start_ms=control_ms,
                frame_end_ms=frame_end_ms,
                requested_combustion_load=allocation.normalized_combustion_load,
                requested_auger_duty=allocation.auger_duty,
                credit_before_seconds=0.0,
                credit_after_seconds=credit_after_seconds,
                scheduled_on_seconds=scheduled_on_seconds,
                delivered_on_seconds=scheduled_on_seconds,
                transition_count=0 if scheduled_on_seconds in (0.0, frame_seconds) else 2,
                actual_start_active=active_for_entire_frame,
                actual_end_active=active_for_entire_frame,
                requested_fan_duty=allocation.fan_duty,
                applied_fan_duty=allocation.fan_duty,
                skipped=False,
                stale_command=False,
                inhibit_reason=InhibitReason.NONE,
                reset_reason=None,
            ),
        )
        records.append(frame)

    records.append(
        ControlTraceRecord(
            ts_ms=applied_end_ms,
            session_id=session_id,
            cook_id=cook_id,
            controller=ControllerType.PID_SP,
            event_kind=TraceEventKind.APPLIED_OUTPUT,
            schema_version=TRACE_SCHEMA_VERSION,
            payload=AppliedOutputPayload(
                result_revision=revision,
                interval_start_ms=control_ms,
                interval_end_ms=applied_end_ms,
                realized_auger_duty=realized_auger_duty,
                realized_combustion_load=realized_combustion_load,
                actual_fan_duty=allocation.fan_duty,
                sample_complete=True,
                output_source=OutputSource.CONTROLLER,
            ),
        )
    )
    return tuple(records)
