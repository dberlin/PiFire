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
    AmbientSource,
    AmbientUncertainty,
    AppliedOutputPayload,
    CalibrationEventType,
    CalibrationTracePayload,
    ControlTraceDbRow,
    CompletedOriginPayload,
    ControlTraceRecord,
    ControllerBranch,
    MpcFailureState,
    ControllerType,
    FramedPulseFramePayload,
    InhibitReason,
    ModelEventPayload,
    ModelEventType,
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
    TraceSetting,
    StateSpaceRefreshPayload,
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
        TraceEventKind.MODEL_OBSERVATION,
        TraceEventKind.MODEL_EVALUATION,
        TraceEventKind.CALIBRATION,
        TraceEventKind.RECORDER_GAP,
    }
    assert set(ActuationMode) == {ActuationMode.FRAMED_PULSE}
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
            TraceEventKind.MODEL_OBSERVATION,
            ModelObservationPayload(
                frame_start_ms=1_000,
                frame_end_ms=21_000,
                temp_c=110.0,
                setpoint_c=120.0,
                ambient_c=20.0,
                observation_sequence=1,
                probe_valid=True,
                probe_source="chamber-probe-1",
                ambient_source=AmbientSource.CONFIGURED,
                ambient_uncertainty=AmbientUncertainty.UNMEASURED,
                baseline_combustion_load=0.4,
                calibration_probe_load=0.0,
                requested_combustion_load=0.4,
                allocated_combustion_load=0.4,
                realized_combustion_load=0.35,
                requested_auger_duty=0.4,
                scheduled_on_seconds=8.0,
                delivered_on_seconds=7.0,
                realized_auger_duty=0.35,
                allocator_revision=1,
                allocation_clamp_reasons=(),
                calibration_stage=None,
                calibration_fit=False,
                result_revision=2,
                eligible=True,
                rejection_reasons=(),
                input_variance=0.01,
                input_levels=3,
                incumbent_innovation_c=1.0,
                challenger_innovation_c=0.5,
                effective_updates=21,
                role_generation=0,
                model_digest="a" * 64,
            ),
        ),
        (
            ControllerType.MPC,
            TraceEventKind.MODEL_EVALUATION,
            ModelEvaluationPayload(
                decision_id="generation-0-evaluation-1",
                evaluated_at_ms=360_000,
                role_generation=0,
                promoted=False,
                committed=False,
                consecutive_wins=0,
                rejection_reasons=("prediction",),
                incumbent_prediction_score=1.0,
                challenger_prediction_score=1.2,
                incumbent_braking_score=None,
                challenger_braking_score=None,
                sample_count=2,
                prospective_digest=None,
                window_start_ms=20_000,
                window_end_ms=340_000,
                incumbent_digest="b" * 64,
                challenger_digest="c" * 64,
                completed_origins=(
                    {
                        "origin_time_ms": 20_000,
                        "completion_time_ms": 80_000,
                        "horizon_steps": 3,
                        "generation": 0,
                        "observed_temperature_c": 110.0,
                        "incumbent_error_c": 2.0,
                        "challenger_error_c": 1.0,
                        "braking": True,
                        "observation_sequence": 1,
                        "incumbent_digest": "b" * 64,
                        "challenger_digest": "c" * 64,
                        "incumbent_prediction_c": 108.0,
                        "challenger_prediction_c": 109.0,
                        "temperature_band": "near-target",
                        "ambient_source": AmbientSource.CONFIGURED,
                    },
                    {
                        "origin_time_ms": 40_000,
                        "completion_time_ms": 340_000,
                        "horizon_steps": 15,
                        "generation": 0,
                        "observed_temperature_c": 115.0,
                        "incumbent_error_c": -3.0,
                        "challenger_error_c": -4.0,
                        "braking": False,
                        "observation_sequence": 2,
                        "incumbent_digest": "b" * 64,
                        "challenger_digest": "c" * 64,
                        "incumbent_prediction_c": 118.0,
                        "challenger_prediction_c": 119.0,
                        "temperature_band": "below-target",
                        "ambient_source": AmbientSource.MEASURED,
                    },
                ),
                horizon_scores=(
                    {
                        "horizon_steps": 3,
                        "incumbent_rmse_c": 2.0,
                        "challenger_rmse_c": 1.0,
                        "sample_count": 1,
                    },
                    {
                        "horizon_steps": 15,
                        "incumbent_rmse_c": 3.0,
                        "challenger_rmse_c": 4.0,
                        "sample_count": 1,
                    },
                ),
                evaluation_duration_ms=7.5,
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


def test_model_evaluation_json_round_trip_preserves_auditable_completed_origins() -> None:
    payload = next(item[2] for item in _payload_cases() if isinstance(item[2], ModelEvaluationPayload))
    record = ControlTraceRecord(
        ts_ms=360_000,
        session_id="session-1",
        cook_id="cook-1",
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.MODEL_EVALUATION,
        payload=payload,
    )

    encoded = json.loads(record.model_dump_json())
    restored = ControlTraceRecord.model_validate_json(json.dumps(encoded))

    assert restored == record
    assert encoded["payload"] == {
        "decision_id": "generation-0-evaluation-1",
        "evaluated_at_ms": 360_000,
        "role_generation": 0,
        "promoted": False,
        "committed": False,
        "consecutive_wins": 0,
        "rejection_reasons": ["prediction"],
        "incumbent_prediction_score": 1.0,
        "challenger_prediction_score": 1.2,
        "incumbent_braking_score": None,
        "challenger_braking_score": None,
        "sample_count": 2,
        "prospective_digest": None,
        "window_start_ms": 20_000,
        "window_end_ms": 340_000,
        "incumbent_digest": "b" * 64,
        "challenger_digest": "c" * 64,
        "completed_origins": [
            {
                "origin_time_ms": 20_000,
                "completion_time_ms": 80_000,
                "horizon_steps": 3,
                "generation": 0,
                "observed_temperature_c": 110.0,
                "incumbent_error_c": 2.0,
                "challenger_error_c": 1.0,
                "braking": True,
                "observation_sequence": 1,
                "incumbent_digest": "b" * 64,
                "challenger_digest": "c" * 64,
                "incumbent_prediction_c": 108.0,
                "challenger_prediction_c": 109.0,
                "temperature_band": "near-target",
                "ambient_source": "configured",
            },
            {
                "origin_time_ms": 40_000,
                "completion_time_ms": 340_000,
                "horizon_steps": 15,
                "generation": 0,
                "observed_temperature_c": 115.0,
                "incumbent_error_c": -3.0,
                "challenger_error_c": -4.0,
                "braking": False,
                "observation_sequence": 2,
                "incumbent_digest": "b" * 64,
                "challenger_digest": "c" * 64,
                "incumbent_prediction_c": 118.0,
                "challenger_prediction_c": 119.0,
                "temperature_band": "below-target",
                "ambient_source": "measured",
            },
        ],
        "horizon_scores": [
            {
                "horizon_steps": 3,
                "incumbent_rmse_c": 2.0,
                "challenger_rmse_c": 1.0,
                "sample_count": 1,
            },
            {
                "horizon_steps": 15,
                "incumbent_rmse_c": 3.0,
                "challenger_rmse_c": 4.0,
                "sample_count": 1,
            },
        ],
        "evaluation_duration_ms": 7.5,
        "payload_type": "model_evaluation",
        "challenger_model_kind": "scheduled-arx",
        "state_space_refresh": None,
    }
    assert restored.payload.window_start_ms == min(
        origin.origin_time_ms for origin in restored.payload.completed_origins
    )
    assert restored.payload.window_end_ms == max(
        origin.completion_time_ms for origin in restored.payload.completed_origins
    )
    assert restored.payload.evaluated_at_ms >= restored.payload.window_end_ms
    assert restored.payload.sample_count == len(restored.payload.completed_origins)
    assert sum(score.sample_count for score in restored.payload.horizon_scores) == restored.payload.sample_count
    assert restored.payload.incumbent_digest == "b" * 64
    assert restored.payload.challenger_digest == "c" * 64
    with pytest.raises(FrozenInstanceError):
        restored.payload.completed_origins = ()


def test_model_evaluation_preserves_a_prior_win_until_both_horizons_are_complete() -> None:
    payload = next(item[2] for item in _payload_cases() if isinstance(item[2], ModelEvaluationPayload))
    short_origin = (payload.completed_origins[0],)
    short_scores = tuple(
        score
        if score.horizon_steps == 3
        else replace(score, incumbent_rmse_c=None, challenger_rmse_c=None, sample_count=0)
        for score in payload.horizon_scores
    )

    preserved = replace(
        payload,
        consecutive_wins=1,
        sample_count=1,
        completed_origins=short_origin,
        horizon_scores=short_scores,
        window_end_ms=short_origin[0].completion_time_ms,
    )

    assert preserved.rejection_reasons == ("prediction",)
    assert preserved.consecutive_wins == 1


def test_model_evaluation_rejects_unbounded_or_inconsistent_audit_evidence() -> None:
    payload = next(item[2] for item in _payload_cases() if isinstance(item[2], ModelEvaluationPayload))

    with pytest.raises(ValidationError):
        replace(payload, completed_origins=payload.completed_origins * 1_000)
    with pytest.raises(ValidationError):
        replace(payload, horizon_scores=payload.horizon_scores + payload.horizon_scores[:1])
    with pytest.raises(ValidationError):
        replace(payload, sample_count=3)
    with pytest.raises(ValidationError):
        replace(payload, window_start_ms=20_001)
    with pytest.raises(ValidationError):
        replace(payload, evaluated_at_ms=339_999)
    with pytest.raises(ValidationError):
        replace(payload, incumbent_digest="B" * 64)
    with pytest.raises(ValidationError):
        replace(payload, evaluation_duration_ms=-0.01)


@pytest.mark.parametrize(
    ("score_index", "field", "inconsistent"),
    [
        (0, "incumbent_rmse_c", 2.000_001),
        (0, "challenger_rmse_c", 0.999_999),
        (1, "incumbent_rmse_c", 3.000_001),
        (1, "challenger_rmse_c", 3.999_999),
        (0, "incumbent_rmse_c", -2.0),
        (1, "challenger_rmse_c", -4.0),
    ],
)
def test_model_evaluation_rejects_horizon_rmse_that_disagrees_with_completed_origins(
    score_index, field, inconsistent
) -> None:
    payload = next(item[2] for item in _payload_cases() if isinstance(item[2], ModelEvaluationPayload))
    scores = list(payload.horizon_scores)
    with pytest.raises(ValidationError):
        scores[score_index] = replace(scores[score_index], **{field: inconsistent})
        replace(payload, horizon_scores=tuple(scores))


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


def test_schema_four_has_one_canonical_model_evidence_contract():
    assert TRACE_SCHEMA_VERSION == 4
    assert {"u_min", "u_max", "hold_cycle_seconds"}.isdisjoint(SessionPayload.__annotations__)
    assert {"pulse_slot_seconds", "pulse_frame_seconds"} <= SessionPayload.__annotations__.keys()
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


@pytest.mark.parametrize(
    "replacement",
    [
        {"frame_end_ms": 1_000},
        {"requested_combustion_load": 1.1},
        {"eligible": False, "rejection_reasons": ()},
        {"eligible": True, "rejection_reasons": ("stale",)},
        {"effective_updates": -1},
        {"model_digest": "A" * 64},
    ],
)
def test_model_observation_rejects_invalid_learning_evidence(replacement):
    payload = next(item[2] for item in _payload_cases() if isinstance(item[2], ModelObservationPayload))
    with pytest.raises(ValidationError):
        replace(payload, **replacement)


def test_enriched_model_lifecycle_metadata_is_bounded_and_round_trips():
    payload = ModelEventPayload(
        event=ModelEventType.ADOPT,
        model_revision=8,
        provenance="fit-42",
        detail="adopted a validated model",
        model_kind="scheduled-arx",
        model_schema="scheduled-arx/v2",
        role_generation=3,
        snapshot_digest="b" * 64,
        parameters=(TraceSetting(key="delay", value=2),),
    )
    record = ControlTraceRecord(
        ts_ms=21_000,
        session_id="session-1",
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.MODEL_EVENT,
        payload=payload,
    )
    assert ControlTraceRecord.from_db_row(record.to_db_row()) == record
    with pytest.raises(ValidationError):
        replace(payload, snapshot_digest="B" * 64)
    with pytest.raises(ValidationError):
        replace(payload, parameters=(TraceSetting(key="delay", value=2),) * 33)


def test_model_evaluation_requires_win_count_to_match_rejection_evidence():
    payload = next(item[2] for item in _payload_cases() if isinstance(item[2], ModelEvaluationPayload))
    clean_first_win = replace(payload, rejection_reasons=(), consecutive_wins=1)
    assert (clean_first_win.promoted, clean_first_win.committed, clean_first_win.prospective_digest) == (
        False,
        False,
        None,
    )
    with pytest.raises(ValidationError):
        replace(payload, committed=True)
    with pytest.raises(ValidationError):
        replace(payload, promoted=True, rejection_reasons=(), prospective_digest=None)
    with pytest.raises(ValidationError):
        replace(payload, promoted=True, prospective_digest="c" * 64)
    with pytest.raises(ValidationError):
        replace(payload, prospective_digest="c" * 64)
    with pytest.raises(ValidationError):
        replace(payload, rejection_reasons=(), consecutive_wins=0)
    with pytest.raises(ValidationError):
        replace(clean_first_win, rejection_reasons=("prediction",))


def test_v2_envelopes_accept_unchanged_payloads_but_reject_v3_learning_payloads():
    lifecycle = next(item[2] for item in _payload_cases() if isinstance(item[2], ModelEventPayload))
    legacy = ControlTraceRecord(
        ts_ms=1,
        session_id="legacy",
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.MODEL_EVENT,
        schema_version=2,
        payload=lifecycle,
    )
    assert ControlTraceRecord.from_db_row(legacy.to_db_row()) == legacy
    observation = next(item[2] for item in _payload_cases() if isinstance(item[2], ModelObservationPayload))
    with pytest.raises(ValidationError, match="schema version 2"):
        ControlTraceRecord(
            ts_ms=1,
            session_id="legacy",
            controller=ControllerType.MPC,
            event_kind=TraceEventKind.MODEL_OBSERVATION,
            schema_version=2,
            payload=observation,
        )


def _state_space_refresh(*, accepted: bool, terminal_reason: str | None = None, **overrides):
    selected = {
        "order": 2,
        "delay": 3,
        "singular_values": (8.0, 2.0),
        "effective_rank": 2,
        "alignment_error_c": 1.5,
        "max_pole_magnitude": 0.8,
        "process_covariance_trace": 0.2,
        "measurement_covariance": 0.1,
    }
    fields = {
        "accepted": accepted,
        "terminal_reason": terminal_reason,
        "attempts": (
            {
                "order": 2,
                "delay": 3,
                "sample_count": 48,
                "hankel_shape": (8, 33),
                "singular_values": (8.0, 2.0),
                "effective_rank": 2,
                "alignment_error_c": 1.5,
                "rejection_reasons": (),
                "elapsed_ms": 4.0,
            },
        ),
        "refresh_duration_ms": 4.5,
        "state_space_digest": "d" * 64,
    }
    if accepted:
        fields.update(selected)
    fields.update(overrides)
    return StateSpaceRefreshPayload(**fields)


@pytest.mark.parametrize(
    "refresh_factory",
    (
        lambda: _state_space_refresh(accepted=True),
        lambda: _state_space_refresh(
            accepted=True,
            alignment_error_c=None,
            attempts=(replace(_state_space_refresh(accepted=True).attempts[0], alignment_error_c=None),),
        ),
        lambda: _state_space_refresh(accepted=False, terminal_reason="insufficient-samples", attempts=()),
        lambda: _state_space_refresh(
            accepted=False,
            terminal_reason="rank-deficient",
            attempts=(
                replace(
                    _state_space_refresh(accepted=True).attempts[0],
                    rejection_reasons=("rank-deficient",),
                ),
            ),
        ),
        lambda: _state_space_refresh(
            accepted=False,
            terminal_reason="no-valid-candidate",
            attempts=(
                replace(
                    _state_space_refresh(accepted=True).attempts[0],
                    rejection_reasons=("rank-deficient",),
                ),
            ),
        ),
        lambda: _state_space_refresh(
            accepted=False,
            terminal_reason="alignment-failed",
            attempts=(
                replace(
                    _state_space_refresh(accepted=True).attempts[0],
                    rejection_reasons=("alignment-failed",),
                ),
            ),
        ),
    ),
)
def test_state_space_refresh_trace_round_trips_accepted_rejected_bootstrap_and_replacement(refresh_factory):
    payload = next(item[2] for item in _payload_cases() if isinstance(item[2], ModelEvaluationPayload))
    refresh = refresh_factory()
    evaluation = replace(
        payload,
        challenger_model_kind="innovation-state-space",
        state_space_refresh=refresh,
    )

    record = ControlTraceRecord(
        ts_ms=evaluation.evaluated_at_ms,
        session_id="state-space-trace",
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.MODEL_EVALUATION,
        payload=evaluation,
    )

    assert ControlTraceRecord.model_validate_json(record.model_dump_json()) == record


def test_model_evaluation_rejects_unpaired_state_space_model_kind_and_refresh_evidence():
    payload = next(item[2] for item in _payload_cases() if isinstance(item[2], ModelEvaluationPayload))
    refresh = _state_space_refresh(accepted=True)

    with pytest.raises(ValidationError):
        replace(payload, challenger_model_kind="innovation-state-space")
    with pytest.raises(ValidationError):
        replace(payload, state_space_refresh=refresh)


def test_accepted_initial_state_space_fit_honestly_records_unattempted_alignment() -> None:
    refresh = _state_space_refresh(
        accepted=True,
        alignment_error_c=None,
        attempts=(replace(_state_space_refresh(accepted=True).attempts[0], alignment_error_c=None),),
    )

    assert refresh.accepted is True
    assert refresh.alignment_error_c is None


@pytest.mark.parametrize(
    "attempt",
    (
        {
            "order": 2,
            "delay": 3,
            "sample_count": 48,
            "hankel_shape": (8, 33),
            "singular_values": (-1.0, 0.5),
            "effective_rank": 2,
            "alignment_error_c": None,
            "rejection_reasons": (),
            "elapsed_ms": 4.0,
        },
        {
            "order": 2,
            "delay": 3,
            "sample_count": 48,
            "hankel_shape": (8, 33),
            "singular_values": tuple(float(value) for value in range(10)),
            "effective_rank": 9,
            "alignment_error_c": None,
            "rejection_reasons": (),
            "elapsed_ms": 4.0,
        },
    ),
)
def test_state_space_attempt_rejects_negative_singular_values_and_rank_beyond_hankel_dimensions(attempt):
    with pytest.raises(ValidationError):
        StateSpaceRefreshPayload(
            accepted=False,
            terminal_reason="rank-deficient",
            attempts=(attempt,),
            refresh_duration_ms=1.0,
            state_space_digest="a" * 64,
        )


@pytest.mark.parametrize(
    "refresh",
    (
        lambda: _state_space_refresh(accepted=False, terminal_reason="no-valid-candidate", attempts=()),
        lambda: _state_space_refresh(
            accepted=True,
            singular_values=(8.0, float("nan")),
        ),
        lambda: _state_space_refresh(accepted=True, effective_rank=3),
        lambda: _state_space_refresh(accepted=True, refresh_duration_ms=-0.1),
        lambda: _state_space_refresh(accepted=True, alignment_error_c=2.0000001),
        lambda: _state_space_refresh(
            accepted=True,
            attempts=(replace(_state_space_refresh(accepted=True).attempts[0], order=4),),
        ),
        lambda: _state_space_refresh(accepted=False, terminal_reason="rank-deficient", order=2, delay=3),
        lambda: _state_space_refresh(
            accepted=False,
            terminal_reason="rank-deficient",
            attempts=(
                replace(
                    _state_space_refresh(accepted=True).attempts[0],
                    rejection_reasons=("ill-conditioned",),
                ),
            ),
        ),
        lambda: _state_space_refresh(
            accepted=True,
            attempts=(replace(_state_space_refresh(accepted=True).attempts[0], effective_rank=1),),
        ),
        lambda: _state_space_refresh(
            accepted=True,
            attempts=(
                replace(
                    _state_space_refresh(accepted=True).attempts[0],
                    rejection_reasons=("rank-deficient",),
                ),
            ),
        ),
    ),
)
def test_state_space_refresh_trace_rejects_invalid_or_contradictory_diagnostics(refresh):
    with pytest.raises(ValidationError):
        refresh()
    with pytest.raises(ValidationError):
        _state_space_refresh(accepted=True, refresh_duration_ms=3.99)


def _canonical_observation_payload(*, calibration: bool) -> ModelObservationPayload:
    return ModelObservationPayload(
        frame_start_ms=0,
        frame_end_ms=20_000,
        temp_c=110.0,
        setpoint_c=120.0,
        ambient_c=20.0,
        baseline_combustion_load=0.35 if calibration else 0.40,
        calibration_probe_load=0.05 if calibration else 0.0,
        requested_combustion_load=0.40,
        allocated_combustion_load=0.38 if calibration else 0.40,
        realized_combustion_load=0.30 if calibration else 0.40,
        requested_auger_duty=0.19 if calibration else 0.20,
        scheduled_on_seconds=4.0 if calibration else 8.0,
        delivered_on_seconds=3.0 if calibration else 8.0,
        realized_auger_duty=0.15 if calibration else 0.20,
        allocator_revision=9,
        allocation_clamp_reasons=(AllocationClampReason.AUGER_MAX,) if calibration else (),
        observation_sequence=1,
        probe_valid=True,
        probe_source="chamber-probe-1",
        ambient_source=AmbientSource.CONFIGURED,
        ambient_uncertainty=AmbientUncertainty.UNMEASURED,
        calibration_stage="low" if calibration else None,
        calibration_fit=calibration,
        eligible=True,
        rejection_reasons=(),
        input_variance=0.01,
        input_levels=3,
        incumbent_innovation_c=1.0,
        challenger_innovation_c=0.5,
        effective_updates=21,
        role_generation=0,
        model_digest="a" * 64,
        result_revision=7,
        output_source=OutputSource.CONTROLLER,
        lid_open=False,
        safety_inhibited=False,
        manual_override=False,
        stale=False,
        skipped=False,
        reset=False,
        continuous=True,
    )


def test_schema_four_round_trips_distinct_canonical_observation_evidence() -> None:
    ordinary = _canonical_observation_payload(calibration=False)
    calibration = _canonical_observation_payload(calibration=True)
    record = ControlTraceRecord(
        ts_ms=20_000,
        session_id="session-1",
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.MODEL_OBSERVATION,
        payload=calibration,
    )

    restored = ControlTraceRecord.model_validate_json(record.model_dump_json())

    assert ordinary.calibration_probe_load == 0.0
    assert restored.payload == calibration
    assert calibration.baseline_combustion_load == 0.35
    assert calibration.calibration_probe_load == 0.05
    assert calibration.requested_combustion_load == 0.40
    assert calibration.allocated_combustion_load == 0.38
    assert calibration.realized_combustion_load == 0.30
    assert calibration.requested_auger_duty == 0.19
    assert calibration.scheduled_on_seconds == 4.0
    assert calibration.delivered_on_seconds == 3.0
    assert calibration.realized_auger_duty == 0.15
    assert calibration.ambient_source is AmbientSource.CONFIGURED
    assert calibration.ambient_uncertainty is AmbientUncertainty.UNMEASURED


@pytest.mark.parametrize(
    "replacement",
    (
        {"baseline_combustion_load": inf},
        {"requested_combustion_load": 0.42},
        {"allocator_revision": None},
        {"ambient_source": AmbientSource.MEASURED, "probe_source": None},
        {"probe_valid": False},
        {"eligible": True, "rejection_reasons": ("stale",)},
    ),
)
def test_schema_four_rejects_incoherent_canonical_observation_evidence(replacement) -> None:
    with pytest.raises(ValidationError):
        replace(_canonical_observation_payload(calibration=True), **replacement)


def test_calibration_trace_payload_round_trips_and_rejects_incoherent_reasons() -> None:
    payload = CalibrationTracePayload(
        event=CalibrationEventType.STAGE_STARTED,
        command_revision=7,
        command_action="start",
        result_revision=19,
        stage="low",
        intended_probe_load=0.05,
        bounded_probe_load=0.05,
        cumulative_probe_load=0.05,
        eligible_observations=1,
        positive_observations=1,
        negative_observations=0,
        reasons=(),
    )
    record = ControlTraceRecord(
        ts_ms=20_000,
        session_id="session-1",
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.CALIBRATION,
        payload=payload,
    )

    assert ControlTraceRecord.model_validate_json(record.model_dump_json()).payload == payload
    with pytest.raises(ValidationError):
        replace(payload, event=CalibrationEventType.START_REJECTED)


def test_completed_origins_require_precommitted_prediction_provenance() -> None:
    with pytest.raises(ValidationError):
        CompletedOriginPayload(
            origin_time_ms=20_000,
            completion_time_ms=80_000,
            horizon_steps=3,
            generation=0,
            observed_temperature_c=110.0,
            incumbent_error_c=2.0,
            challenger_error_c=1.0,
            braking=False,
        )
