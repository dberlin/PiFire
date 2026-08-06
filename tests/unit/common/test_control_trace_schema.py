"""Contract tests for the versioned controller control-trace schema."""

import json
from dataclasses import FrozenInstanceError, replace
from math import inf, nan

import pytest
from pydantic import ValidationError

from common.control_trace import (
    TRACE_SCHEMA_VERSION,
    ActuationMode,
    AllocationClampReason,
    AllocationPayload,
    AppliedOutputPayload,
    ControlTraceDbRow,
    ControlTraceRecord,
    ControllerBranch,
    MpcFailureState,
    ControllerType,
    FramedPulseFramePayload,
    InhibitReason,
    ModelEventPayload,
    ModelEventType,
    MpcUpdatePayload,
    ResultStaleState,
    PidSpUpdatePayload,
    PidUpdatePayload,
    RecorderGapPayload,
    SafetyEventPayload,
    SafetyEventType,
    SessionPayload,
    TraceEventKind,
    TraceSetting,
)
from controller.applied_output import OutputSource


def test_trace_enums_have_exact_members():
    assert set(ControllerType) == {ControllerType.PID, ControllerType.PID_SP, ControllerType.MPC}
    assert set(TraceEventKind) == {
        TraceEventKind.SESSION,
        TraceEventKind.CONTROL_UPDATE,
        TraceEventKind.ALLOCATION,
        TraceEventKind.ACTUATION_FRAME,
        TraceEventKind.APPLIED_OUTPUT,
        TraceEventKind.SAFETY_EVENT,
        TraceEventKind.MODEL_EVENT,
        TraceEventKind.RECORDER_GAP,
    }
    assert set(ActuationMode) == {ActuationMode.FIXED_CYCLE, ActuationMode.FRAMED_PULSE}
    assert set(ResultStaleState) == {ResultStaleState.FRESH, ResultStaleState.STALE}
    assert set(InhibitReason) == {
        InhibitReason.NONE,
        InhibitReason.LID_OPEN,
        InhibitReason.MANUAL_OVERRIDE,
        InhibitReason.SAFETY,
        InhibitReason.STALE_COMMAND,
    }
    assert set(ModelEventType) == {
        ModelEventType.RESTORE,
        ModelEventType.ADOPT,
        ModelEventType.REJECT,
        ModelEventType.REFIT,
        ModelEventType.SCHEMA_INVALIDATED,
    }
    assert set(ControllerBranch) == {
        ControllerBranch.NONE,
        ControllerBranch.INITIALIZATION,
        ControllerBranch.FULL_HEAT,
        ControllerBranch.TARGET_REACHED,
        ControllerBranch.RESET,
        ControllerBranch.OVERSHOOT,
    }


def _payload_cases():
    return [
        (
            ControllerType.PID,
            TraceEventKind.SESSION,
            SessionPayload(
                controller=ControllerType.PID,
                controller_config=(TraceSetting(key="kp", value=1.25),),
                temperature_unit="F",
                control_period_seconds=2.0,
                model_revision=None,
                model_provenance=None,
                pulse_slot_seconds=2.0,
                pulse_frame_seconds=20.0,
                fan_authority=False,
                fan_pwm_capable=True,
                fan_min_duty=0.0,
                fan_max_duty=1.0,
                setpoint=225.0,
                ambient_temperature=70.0,
                software_version="1.2.3",
                build_version="42",
            ),
        ),
        (
            ControllerType.PID,
            TraceEventKind.CONTROL_UPDATE,
            PidUpdatePayload(
                monotonic_ms=10,
                wall_ms=20,
                result_revision=0,
                result_age_ms=0,
                control_period_seconds=2.0,
                observed_dt_seconds=2.0,
                setpoint=225.0,
                measured_temperature=220.0,
                raw_output=0.45,
                requested_output=0.45,
                actuation_mode=ActuationMode.FRAMED_PULSE,
                prior_requested_auger_duty=0.4,
                prior_realized_auger_duty=0.35,
                requested_fan_duty=None,
                applied_fan_duty=None,
                output_source=OutputSource.CONTROLLER,
                inhibit_reason=InhibitReason.NONE,
                error=5.0,
                proportional_term=0.3,
                integral_term=0.1,
                derivative_term=0.05,
                integral_accumulator=2.0,
                integral_clamped=False,
                derivative_input=-0.5,
                derivative_state=-0.25,
                proportional_band=30.0,
                kp=1.0,
                ki=0.1,
                kd=0.01,
                center=225.0,
                previous_temperature=219.0,
                previous_update_ms=8,
            ),
        ),
        (
            ControllerType.PID_SP,
            TraceEventKind.CONTROL_UPDATE,
            PidSpUpdatePayload(
                monotonic_ms=10,
                wall_ms=20,
                result_revision=1,
                result_age_ms=2,
                control_period_seconds=2.0,
                observed_dt_seconds=2.0,
                setpoint=225.0,
                measured_temperature=220.0,
                raw_output=0.45,
                requested_output=0.45,
                actuation_mode=ActuationMode.FRAMED_PULSE,
                prior_requested_auger_duty=0.4,
                prior_realized_auger_duty=0.35,
                requested_fan_duty=None,
                applied_fan_duty=None,
                output_source=OutputSource.CONTROLLER,
                inhibit_reason=InhibitReason.NONE,
                error=5.0,
                proportional_term=0.3,
                integral_term=0.1,
                derivative_term=0.05,
                integral_accumulator=2.0,
                integral_clamped=False,
                derivative_input=-0.5,
                derivative_state=-0.25,
                proportional_band=30.0,
                kp=1.0,
                ki=0.1,
                kd=0.01,
                center=225.0,
                previous_temperature=219.0,
                previous_update_ms=8,
                measured_rate=0.4,
                predicted_temperature=221.0,
                predicted_error=4.0,
                tau_seconds=30.0,
                theta_seconds=5.0,
                stable_window_seconds=60.0,
                center_factor=0.8,
                new_target_before=True,
                new_target_after=True,
                target_change_temperature=218.0,
                target_change_ms=4,
                branch=ControllerBranch.FULL_HEAT,
            ),
        ),
        (
            ControllerType.MPC,
            TraceEventKind.CONTROL_UPDATE,
            MpcUpdatePayload(
                monotonic_ms=10,
                wall_ms=20,
                result_revision=2,
                result_age_ms=1,
                control_period_seconds=2.0,
                observed_dt_seconds=2.0,
                setpoint=225.0,
                measured_temperature=220.0,
                raw_output=0.6,
                requested_output=0.6,
                actuation_mode=ActuationMode.FRAMED_PULSE,
                prior_requested_auger_duty=0.4,
                prior_realized_auger_duty=0.35,
                requested_fan_duty=0.5,
                applied_fan_duty=0.45,
                output_source=OutputSource.CONTROLLER,
                inhibit_reason=InhibitReason.NONE,
                state_names=("temperature", "delay_1"),
                state_values=(220.0, 219.0),
                disturbance_estimate=0.1,
                model_revision=7,
                model_provenance="restored",
                raw_policy_firing_load=0.6,
                equilibrium_feed_forward=0.5,
                residual_move=0.1,
                bounded_firing_load=0.6,
                policy_kind="linear_mpc",
                failure_state=MpcFailureState.SUCCESS,
                solve_start_ms=8,
                solve_end_ms=10,
                deadline_miss_count=0,
                stale=False,
                recovered=False,
                predicted_feasible=True,
                predicted_steady_load=0.55,
                solve_duration_ms=2,
                consecutive_deadline_miss_count=0,
                stale_state=ResultStaleState.FRESH,
            ),
        ),
        (
            ControllerType.MPC,
            TraceEventKind.ALLOCATION,
            AllocationPayload(
                result_revision=2,
                normalized_combustion_load=0.6,
                requested_auger_duty=0.55,
                requested_fan_duty=0.5,
                u_max=0.9,
                fan_min_pct=0.2,
                fan_max_pct=0.8,
                fan_enabled=True,
                mpc_has_fan_authority=True,
                auger_clamp_reason=AllocationClampReason.NONE,
                fan_clamp_reason=AllocationClampReason.NONE,
                allocator_revision=1,
            ),
        ),
        (
            ControllerType.MPC,
            TraceEventKind.ACTUATION_FRAME,
            FramedPulseFramePayload(
                result_revision=4,
                pulse_slot_seconds=2.0,
                frame_seconds=20.0,
                frame_start_ms=0,
                frame_end_ms=20_000,
                requested_combustion_load=0.6,
                requested_auger_duty=0.55,
                credit_before_seconds=0.5,
                credit_after_seconds=0.25,
                scheduled_on_seconds=11.0,
                delivered_on_seconds=10.0,
                actual_start_active=False,
                transition_count=2,
                actual_end_active=False,
                requested_fan_duty=0.5,
                applied_fan_duty=0.45,
                skipped=False,
                stale_command=False,
                inhibit_reason=InhibitReason.NONE,
                reset_reason=None,
            ),
        ),
        (
            ControllerType.PID,
            TraceEventKind.APPLIED_OUTPUT,
            AppliedOutputPayload(
                result_revision=5,
                interval_start_ms=100,
                interval_end_ms=25_100,
                realized_auger_duty=0.4,
                realized_combustion_load=None,
                actual_fan_duty=0.3,
                sample_complete=True,
                output_source=OutputSource.CONTROLLER,
            ),
        ),
        (
            ControllerType.PID,
            TraceEventKind.SAFETY_EVENT,
            SafetyEventPayload(
                event=SafetyEventType.LID_DETECTED,
                inhibit_reason=InhibitReason.LID_OPEN,
                result_revision=5,
                detail="lid opened",
            ),
        ),
        (
            ControllerType.MPC,
            TraceEventKind.MODEL_EVENT,
            ModelEventPayload(
                event=ModelEventType.ADOPT,
                model_revision=8,
                provenance="fit-42",
                detail="adopted a validated model",
            ),
        ),
        (
            ControllerType.MPC,
            TraceEventKind.RECORDER_GAP,
            RecorderGapPayload(lost_record_count=3, gap_start_ms=101, gap_end_ms=102),
        ),
    ]


def test_mpc_failure_payload_records_only_the_held_bounded_command():
    payload = _mpc_update_payload()
    failure = replace(
        payload,
        raw_policy_firing_load=None,
        equilibrium_feed_forward=None,
        residual_move=None,
        bounded_firing_load=0.42,
        failure_state=MpcFailureState.POLICY_EXCEPTION,
    )
    assert failure.bounded_firing_load == pytest.approx(0.42)


@pytest.mark.parametrize(
    "replacement",
    [
        {"failure_state": MpcFailureState.SUCCESS, "raw_policy_firing_load": None},
        {"failure_state": MpcFailureState.POLICY_EXCEPTION, "raw_policy_firing_load": 0.5},
    ],
)
def test_mpc_failure_state_requires_truthful_raw_components(replacement):
    with pytest.raises(ValidationError):
        replace(_mpc_update_payload(), **replacement)


def _pid_session_payload() -> SessionPayload:
    for _, _, payload in _payload_cases():
        if isinstance(payload, SessionPayload) and payload.controller is ControllerType.PID:
            return payload
    raise AssertionError("representative PID session payload is missing")


def _mpc_session_payload() -> SessionPayload:
    return SessionPayload(
        controller=ControllerType.MPC,
        controller_config=(TraceSetting(key="horizon", value=10),),
        temperature_unit="F",
        control_period_seconds=2.0,
        model_revision=7,
        model_provenance="restored",
        pulse_slot_seconds=2.0,
        pulse_frame_seconds=20.0,
        fan_authority=True,
        fan_pwm_capable=True,
        fan_min_duty=0.2,
        fan_max_duty=0.8,
        setpoint=225.0,
        ambient_temperature=70.0,
        software_version="1.2.3",
        build_version="42",
    )


def _pid_update_payload() -> PidUpdatePayload:
    for _, _, payload in _payload_cases():
        if isinstance(payload, PidUpdatePayload):
            return payload
    raise AssertionError("representative PID update payload is missing")


def _mpc_update_payload() -> MpcUpdatePayload:
    for _, _, payload in _payload_cases():
        if isinstance(payload, MpcUpdatePayload):
            return payload
    raise AssertionError("representative MPC update payload is missing")




@pytest.mark.parametrize(("controller", "event_kind", "payload"), _payload_cases())
def test_every_payload_round_trips_through_pydantic_json(controller, event_kind, payload):
    record = ControlTraceRecord(
        ts_ms=1_000,
        session_id="session-1",
        cook_id="cook-1",
        controller=controller,
        event_kind=event_kind,
        payload=payload,
    )

    restored = ControlTraceRecord.model_validate_json(record.model_dump_json())

    assert restored == record
    assert type(restored.payload) is type(payload)
    assert restored.controller is controller
    assert restored.event_kind is event_kind
    assert restored.payload.__class__.__slots__
    with pytest.raises(FrozenInstanceError):
        restored.payload.payload_type = "not-allowed"


def test_db_row_round_trip_preserves_typed_payload():
    record = ControlTraceRecord(
        ts_ms=1_000,
        session_id="session-1",
        cook_id=None,
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.RECORDER_GAP,
        payload=RecorderGapPayload(lost_record_count=3, gap_start_ms=101, gap_end_ms=102),
    )

    restored = ControlTraceRecord.from_db_row(record.to_db_row())

    assert restored == record
    assert restored.payload.lost_record_count == 3


@pytest.mark.parametrize("invalid", [nan, inf, -inf])
def test_non_finite_numeric_payload_values_are_rejected(invalid):
    with pytest.raises(ValidationError):
        PidUpdatePayload(
            monotonic_ms=10,
            wall_ms=20,
            result_revision=0,
            result_age_ms=0,
            control_period_seconds=2.0,
            observed_dt_seconds=2.0,
            setpoint=225.0,
            measured_temperature=220.0,
            raw_output=invalid,
            requested_output=0.45,
            actuation_mode=ActuationMode.FRAMED_PULSE,
            prior_requested_auger_duty=0.4,
            prior_realized_auger_duty=0.35,
            requested_fan_duty=None,
            applied_fan_duty=None,
            output_source=OutputSource.CONTROLLER,
            inhibit_reason=InhibitReason.NONE,
            error=5.0,
            proportional_term=0.3,
            integral_term=0.1,
            derivative_term=0.05,
            integral_accumulator=2.0,
            integral_clamped=False,
            derivative_input=-0.5,
            derivative_state=-0.25,
            proportional_band=30.0,
            kp=1.0,
            ki=0.1,
            kd=0.01,
            center=225.0,
            previous_temperature=219.0,
            previous_update_ms=8,
        )


@pytest.mark.parametrize(
    ("record", "message"),
    [
        (
            lambda: ControlTraceRecord(
                ts_ms=1,
                session_id="session",
                controller=ControllerType.PID,
                event_kind=TraceEventKind.ALLOCATION,
                payload=AllocationPayload(
                    result_revision=0,
                    normalized_combustion_load=0.2,
                    requested_auger_duty=0.2,
                    requested_fan_duty=0.2,
                    u_max=1.0,
                    fan_min_pct=0.0,
                    fan_max_pct=1.0,
                    fan_enabled=True,
                    mpc_has_fan_authority=True,
                    auger_clamp_reason=AllocationClampReason.NONE,
                    fan_clamp_reason=AllocationClampReason.NONE,
                    allocator_revision=0,
                ),
            ),
            "MPC-only",
        ),
        (
            lambda: ControlTraceRecord(
                ts_ms=1,
                session_id="session",
                controller=ControllerType.PID_SP,
                event_kind=TraceEventKind.CONTROL_UPDATE,
                payload=_payload_cases()[1][2],
            ),
            "PID diagnostics",
        ),
        (
            lambda: ControlTraceRecord(
                ts_ms=1,
                session_id="session",
                controller=ControllerType.PID,
                event_kind=TraceEventKind.SESSION,
                payload=RecorderGapPayload(lost_record_count=1, gap_start_ms=1, gap_end_ms=1),
            ),
            "event_kind",
        ),
    ],
)
def test_envelope_rejects_mismatched_event_or_controller(record, message):
    with pytest.raises(ValidationError, match=message):
        record()


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda: RecorderGapPayload(lost_record_count=1, gap_start_ms=4, gap_end_ms=3),
            "gap",
        ),
        (
            lambda: FramedPulseFramePayload(
                result_revision=0,
                pulse_slot_seconds=3.0,
                frame_seconds=20.0,
                frame_start_ms=0,
                frame_end_ms=20_000,
                requested_combustion_load=0.4,
                requested_auger_duty=0.4,
                credit_before_seconds=0.0,
                credit_after_seconds=0.0,
                scheduled_on_seconds=0.0,
                delivered_on_seconds=0.0,
                actual_start_active=False,
                transition_count=0,
                actual_end_active=False,
                requested_fan_duty=None,
                applied_fan_duty=None,
                skipped=False,
                stale_command=False,
                inhibit_reason=InhibitReason.NONE,
                reset_reason=None,
            ),
            "divisible",
        ),
    ],
)
def test_semantic_interval_and_pulse_invariants_are_rejected(factory, message):
    with pytest.raises(ValidationError, match=message):
        factory()


@pytest.mark.parametrize(
    ("actual_start_active", "delivered_on_seconds"),
    [(False, 0.0), (True, 3.0)],
)
def test_framed_reset_allows_partial_zero_transition_delivery(actual_start_active, delivered_on_seconds):
    payload = FramedPulseFramePayload(
        result_revision=1,
        pulse_slot_seconds=2.0,
        frame_seconds=20.0,
        frame_start_ms=0,
        frame_end_ms=3_000,
        requested_combustion_load=0.4,
        requested_auger_duty=0.4,
        credit_before_seconds=0.0,
        credit_after_seconds=0.0,
        scheduled_on_seconds=2.0,
        delivered_on_seconds=delivered_on_seconds,
        actual_start_active=actual_start_active,
        transition_count=0,
        actual_end_active=actual_start_active,
        requested_fan_duty=None,
        applied_fan_duty=None,
        skipped=False,
        stale_command=False,
        inhibit_reason=InhibitReason.SAFETY,
        reset_reason="safety",
    )

    assert payload.delivered_on_seconds == delivered_on_seconds


def test_framed_zero_transition_rejects_delivery_that_disagrees_with_actual_duration():
    with pytest.raises(ValidationError, match="zero-transition framed-pulse delivery"):
        FramedPulseFramePayload(
            result_revision=1,
            pulse_slot_seconds=2.0,
            frame_seconds=20.0,
            frame_start_ms=0,
            frame_end_ms=3_000,
            requested_combustion_load=0.4,
            requested_auger_duty=0.4,
            credit_before_seconds=0.0,
            credit_after_seconds=0.0,
            scheduled_on_seconds=2.0,
            delivered_on_seconds=2.0,
            actual_start_active=True,
            transition_count=0,
            actual_end_active=True,
            requested_fan_duty=None,
            applied_fan_duty=None,
            skipped=False,
            stale_command=False,
            inhibit_reason=InhibitReason.NONE,
            reset_reason=None,
        )


def test_envelope_rejects_empty_sessions_unknown_enums_and_unsupported_versions():
    valid = ControlTraceRecord(
        ts_ms=1,
        session_id="session",
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.RECORDER_GAP,
        payload=RecorderGapPayload(lost_record_count=1, gap_start_ms=1, gap_end_ms=1),
    )
    raw = valid.model_dump(mode="json")

    raw["session_id"] = ""
    with pytest.raises(ValidationError, match="session_id"):
        ControlTraceRecord.model_validate(raw)

    raw = valid.model_dump(mode="json")
    raw["controller"] = "unknown"
    with pytest.raises(ValidationError):
        ControlTraceRecord.model_validate(raw)

    raw = valid.model_dump(mode="json")
    raw["event_kind"] = "unknown"
    with pytest.raises(ValidationError):
        ControlTraceRecord.model_validate(raw)

    raw = valid.model_dump(mode="json")
    raw["schema_version"] = TRACE_SCHEMA_VERSION + 1
    with pytest.raises(ValidationError, match="schema_version"):
        ControlTraceRecord.model_validate(raw)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: RecorderGapPayload(lost_record_count=-1, gap_start_ms=1, gap_end_ms=1),
        lambda: AllocationPayload(
            result_revision=-1,
            normalized_combustion_load=0.2,
            requested_auger_duty=0.2,
            requested_fan_duty=0.2,
            u_max=1.0,
            fan_min_pct=0.0,
            fan_max_pct=1.0,
            fan_enabled=True,
            mpc_has_fan_authority=True,
            auger_clamp_reason=AllocationClampReason.NONE,
            fan_clamp_reason=AllocationClampReason.NONE,
            allocator_revision=0,
        ),
    ],
)
def test_negative_counts_revisions_and_excess_delivered_time_are_rejected(factory):
    with pytest.raises(ValidationError):
        factory()














@pytest.mark.parametrize("invalid", ["1", True])
def test_strict_scalar_boundaries_reject_coercible_values_in_constructor_json_and_db(invalid):
    with pytest.raises(ValidationError):
        RecorderGapPayload(lost_record_count=invalid, gap_start_ms=1, gap_end_ms=1)
    with pytest.raises(ValidationError):
        replace(_pid_update_payload(), raw_output=invalid)

    record = ControlTraceRecord(
        ts_ms=1,
        session_id="session",
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.RECORDER_GAP,
        payload=RecorderGapPayload(lost_record_count=1, gap_start_ms=1, gap_end_ms=1),
    )
    raw = record.model_dump(mode="json")
    raw["ts_ms"] = invalid
    with pytest.raises(ValidationError):
        ControlTraceRecord.model_validate_json(json.dumps(raw))

    row = replace(record.to_db_row(), ts_ms=invalid)
    with pytest.raises(ValidationError):
        ControlTraceRecord.from_db_row(row)


def test_strict_db_json_path_still_decodes_valid_enum_values():
    record = ControlTraceRecord(
        ts_ms=1,
        session_id="session",
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.RECORDER_GAP,
        payload=RecorderGapPayload(lost_record_count=1, gap_start_ms=1, gap_end_ms=1),
    )
    row = ControlTraceDbRow(
        ts_ms=record.ts_ms,
        session_id=record.session_id,
        cook_id=record.cook_id,
        controller="mpc",
        event_kind="recorder_gap",
        schema_version=TRACE_SCHEMA_VERSION,
        payload=json.dumps(
            {
                "payload_type": "recorder_gap",
                "lost_record_count": 1,
                "gap_start_ms": 1,
                "gap_end_ms": 1,
            }
        ),
    )

    assert ControlTraceRecord.from_db_row(row) == record





def test_schema_three_has_one_framed_trace_contract():
    assert TRACE_SCHEMA_VERSION == 3
    assert {"u_min", "u_max", "hold_cycle_seconds"}.isdisjoint(SessionPayload.__annotations__)
    assert {"pulse_slot_seconds", "pulse_frame_seconds"} <= SessionPayload.__annotations__.keys()
    assert "fixed_cycle_frame" not in str(ControlTraceRecord.model_json_schema())
    assert "FAN_ASSIST" not in OutputSource.__members__
    session = _pid_session_payload()
    with pytest.raises(ValidationError):
        replace(session, pulse_slot_seconds=None)
    with pytest.raises(ValidationError):
        replace(session, pulse_frame_seconds=None)


@pytest.mark.parametrize("controller", [ControllerType.PID, ControllerType.PID_SP, ControllerType.MPC])
def test_framed_sessions_round_trip_for_every_controller(controller):
    payload = replace(_pid_session_payload(), controller=controller)
    record = ControlTraceRecord(
        ts_ms=1,
        session_id="session",
        controller=controller,
        event_kind=TraceEventKind.SESSION,
        payload=payload,
    )

    assert ControlTraceRecord.from_db_row(record.to_db_row()) == record


@pytest.mark.parametrize("controller", [ControllerType.PID, ControllerType.PID_SP, ControllerType.MPC])
def test_envelope_accepts_framed_payload_for_every_controller(controller):
    frame = next(payload for _, _, payload in _payload_cases() if isinstance(payload, FramedPulseFramePayload))
    record = ControlTraceRecord(
        ts_ms=1,
        session_id="session",
        controller=controller,
        event_kind=TraceEventKind.ACTUATION_FRAME,
        payload=frame,
    )

    assert record.payload is frame


@pytest.mark.parametrize("payload", [_pid_update_payload, lambda: _payload_cases()[2][2], _mpc_update_payload])
def test_current_updates_require_framed_pulse(payload):
    with pytest.raises(ValidationError, match="FRAMED_PULSE"):
        replace(payload(), actuation_mode=ActuationMode.FIXED_CYCLE)
