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
    ChallengerProgressTracePayload,
    CompletedOriginPayload,
    ControllerBranch,
    ControllerType,
    ControlTraceDbRow,
    ControlTraceRecord,
    EstimatorSeedTracePayload,
    FramedPulseFramePayload,
    GreyActivationLifecyclePayload,
    GreyCandidateAssessmentPayload,
    GreyFitLifecyclePayload,
    GreyLearningFailurePayload,
    InhibitReason,
    LearningSnapshotPayload,
    ModelEvaluationPayload,
    ModelEventPayload,
    ModelEventType,
    ModelObservationPayload,
    MpcFailureState,
    MpcUpdatePayload,
    PidSpUpdatePayload,
    PidUpdatePayload,
    RecorderGapPayload,
    ResultStaleState,
    SafetyEventPayload,
    SafetyEventType,
    SessionPayload,
    TraceEventKind,
    TraceSetting,
    TrajectorySegmentTracePayload,
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
        TraceEventKind.FIT_LIFECYCLE,
        TraceEventKind.CANDIDATE_ASSESSMENT,
        TraceEventKind.ACTIVATION_LIFECYCLE,
        TraceEventKind.LEARNING_FAILURE,
        TraceEventKind.ESTIMATOR_SEED,
        TraceEventKind.TRAJECTORY_SEGMENT,
        TraceEventKind.CHALLENGER_PROGRESS,
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


def _estimator_seed_trace_payload() -> EstimatorSeedTracePayload:
    return EstimatorSeedTracePayload(
        delay_states=(0.25, 0.50),
        chamber_temperature_c=110.0,
        disturbance=0.0,
        segment_id="segment-1",
        pre_roll_digest="a" * 64,
        pre_roll_frame_count=2,
        required_frame_count=2,
        status="exact",
        role_generation=4,
        candidate_generation=5,
    )


def _trajectory_segment_trace_payload() -> TrajectorySegmentTracePayload:
    return TrajectorySegmentTracePayload(
        segment_id="segment-1",
        trajectory_session_id="trajectory-session-1",
        trace_session_ids=("trace-session-1", "trace-session-2"),
        cook_id="cook-1",
        segment_schema_version=1,
        observation_schema_version=3,
        state="finalized",
        source_trace_digest="b" * 64,
        content_digest="c" * 64,
        fit_partition_digest="d" * 64,
        source_row_digest="e" * 64,
        pre_roll_frame_count=2,
        scored_hold_frame_count=5,
        terminal_break_reason="stop",
    )


def _challenger_progress_trace_payload() -> ChallengerProgressTracePayload:
    return ChallengerProgressTracePayload(
        challenger_id="challenger-1",
        challenger_revision=3,
        phase="evaluating",
        origin="passive-online",
        policy="causal-auto",
        incumbent_digest="1" * 64,
        incumbent_generation=4,
        candidate_digest="2" * 64,
        candidate_generation=5,
        corpus_digest="3" * 64,
        lineage_digest="4" * 64,
        result_digest="5" * 64,
        evaluation_epoch=2,
        evaluation_round=1,
        consecutive_wins=1,
        required_wins=2,
        completed_horizons=(3, 15),
        required_horizons=(3, 15, 45, 90, 180),
        resumed_from_previous_cook=True,
        reset_reason=None,
    )


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
                policy_kind="acados-grey",
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
            lambda: _canonical_observation_payload(calibration=False),
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
        (
            ControllerType.MPC,
            TraceEventKind.ESTIMATOR_SEED,
            _estimator_seed_trace_payload(),
        ),
        (
            ControllerType.MPC,
            TraceEventKind.TRAJECTORY_SEGMENT,
            _trajectory_segment_trace_payload(),
        ),
        (
            ControllerType.MPC,
            TraceEventKind.CHALLENGER_PROGRESS,
            _challenger_progress_trace_payload(),
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


def _pid_sp_update_payload() -> PidSpUpdatePayload:
    for _, _, payload in _payload_cases():
        if isinstance(payload, PidSpUpdatePayload):
            return payload
    raise AssertionError("representative PID-SP update payload is missing")


def _mpc_update_payload() -> MpcUpdatePayload:
    for _, _, payload in _payload_cases():
        if isinstance(payload, MpcUpdatePayload):
            return payload
    raise AssertionError("representative MPC update payload is missing")


@pytest.mark.parametrize(("controller", "event_kind", "payload"), _payload_cases())
def test_every_payload_round_trips_through_pydantic_json(controller, event_kind, payload):
    payload = payload() if callable(payload) else payload
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


def _segmented_learning_trace_payload_cases():
    return (
        (TraceEventKind.ESTIMATOR_SEED, _estimator_seed_trace_payload()),
        (TraceEventKind.TRAJECTORY_SEGMENT, _trajectory_segment_trace_payload()),
        (TraceEventKind.CHALLENGER_PROGRESS, _challenger_progress_trace_payload()),
    )


def _mpc_only_learning_trace_payload_cases():
    mpc_only_payload_types = (
        CalibrationTracePayload,
        ModelEvaluationPayload,
        GreyFitLifecyclePayload,
        GreyCandidateAssessmentPayload,
        GreyActivationLifecyclePayload,
        GreyLearningFailurePayload,
    )
    model_learning_cases = tuple(
        (event_kind, payload)
        for _, event_kind, payload in _payload_cases()
        if isinstance(payload, mpc_only_payload_types)
    )
    return model_learning_cases + (
        (TraceEventKind.ESTIMATOR_SEED, _estimator_seed_trace_payload()),
        (TraceEventKind.CHALLENGER_PROGRESS, _challenger_progress_trace_payload()),
    )


@pytest.mark.parametrize(("event_kind", "payload"), _segmented_learning_trace_payload_cases())
def test_schema_v8_segmented_learning_payloads_round_trip_through_db(event_kind, payload) -> None:
    record = ControlTraceRecord(
        ts_ms=25_000,
        session_id="trace-session-2",
        cook_id="cook-1",
        controller=ControllerType.MPC,
        event_kind=event_kind,
        payload=payload,
    )

    row = record.to_db_row()
    restored = ControlTraceRecord.from_db_row(row)

    assert row.schema_version == 8
    assert json.loads(row.payload)["payload_type"] == event_kind.value
    assert restored == record
    assert type(restored.payload) is type(payload)


@pytest.mark.parametrize(
    ("factory", "field"),
    (
        (_estimator_seed_trace_payload, "pre_roll_digest"),
        (_trajectory_segment_trace_payload, "source_trace_digest"),
        (_trajectory_segment_trace_payload, "content_digest"),
        (_trajectory_segment_trace_payload, "fit_partition_digest"),
        (_trajectory_segment_trace_payload, "source_row_digest"),
        (_challenger_progress_trace_payload, "incumbent_digest"),
        (_challenger_progress_trace_payload, "candidate_digest"),
        (_challenger_progress_trace_payload, "corpus_digest"),
        (_challenger_progress_trace_payload, "lineage_digest"),
        (_challenger_progress_trace_payload, "result_digest"),
    ),
)
def test_segmented_learning_payloads_require_lowercase_sha256_digests(factory, field) -> None:
    with pytest.raises(ValidationError):
        replace(factory(), **{field: "A" * 64})


@pytest.mark.parametrize(
    "replacement",
    (
        {"pre_roll_frame_count": 3},
        {"status": "exact", "pre_roll_frame_count": 1},
        {"status": "short", "pre_roll_frame_count": 0},
        {"status": "short", "pre_roll_frame_count": 2},
        {"status": "absent", "pre_roll_frame_count": 1},
        {"segment_id": ""},
        {"required_frame_count": -1},
        {"role_generation": -1},
        {"candidate_generation": -1},
        {"status": "unsupported"},
        {"status": "uncertain", "delay_states": (0.25,)},
    ),
)
def test_estimator_seed_trace_rejects_invalid_identity_status_and_counts(replacement) -> None:
    with pytest.raises(ValidationError):
        replace(_estimator_seed_trace_payload(), **replacement)


@pytest.mark.parametrize(
    ("status", "pre_roll_frame_count", "delay_states"),
    (
        ("exact", 2, (0.25, 0.50)),
        ("short", 1, (0.25, 0.50)),
        ("absent", 0, ()),
        ("uncertain", 0, ()),
    ),
)
def test_estimator_seed_trace_accepts_each_truthful_status(status, pre_roll_frame_count, delay_states) -> None:
    payload = replace(
        _estimator_seed_trace_payload(),
        status=status,
        pre_roll_frame_count=pre_roll_frame_count,
        delay_states=delay_states,
    )

    assert payload.status == status
    assert payload.pre_roll_frame_count == pre_roll_frame_count


@pytest.mark.parametrize(
    "replacement",
    (
        {"segment_id": ""},
        {"trajectory_session_id": ""},
        {"trace_session_ids": ("trace-session-1", "")},
        {"cook_id": ""},
        {"segment_schema_version": 0},
        {"observation_schema_version": 0},
        {"trace_session_ids": ()},
        {"trace_session_ids": ("trace-session-1", "trace-session-1")},
        {"pre_roll_frame_count": -1},
        {"scored_hold_frame_count": -1},
        {"pre_roll_frame_count": 0, "scored_hold_frame_count": 0},
        {"state": "open"},
        {"state": "finalized", "terminal_break_reason": None},
        {"state": "quarantined", "terminal_break_reason": None},
    ),
)
def test_trajectory_segment_trace_rejects_invalid_links_counts_and_state(replacement) -> None:
    with pytest.raises(ValidationError):
        replace(_trajectory_segment_trace_payload(), **replacement)


def test_trajectory_segment_trace_accepts_an_open_segment_without_a_terminal_reason() -> None:
    payload = replace(
        _trajectory_segment_trace_payload(),
        state="open",
        terminal_break_reason=None,
    )

    assert payload.state == "open"
    assert payload.terminal_break_reason is None


@pytest.mark.parametrize(
    "replacement",
    (
        {"challenger_id": ""},
        {"origin": "passive-online", "policy": "cook-refit"},
        {"origin": "cook-refit", "policy": "cook-refit"},
        {"challenger_revision": -1},
        {"incumbent_generation": -1},
        {"candidate_generation": -1},
        {"evaluation_epoch": -1},
        {"evaluation_round": -1},
        {"consecutive_wins": 3},
        {"required_wins": 0},
        {"completed_horizons": (15, 3)},
        {"completed_horizons": (3, 3)},
        {"completed_horizons": (3, 360)},
        {"required_horizons": (15, 3, 45, 90, 180)},
        {"required_horizons": (3, 15, 15, 45, 90, 180)},
        {"phase": "unsupported"},
        {"reset_reason": ""},
    ),
)
def test_challenger_progress_trace_rejects_inconsistent_authority_and_progress(
    replacement,
) -> None:
    with pytest.raises(ValidationError):
        replace(_challenger_progress_trace_payload(), **replacement)


@pytest.mark.parametrize(
    ("phase", "replacement"),
    (
        (
            "built",
            {
                "evaluation_epoch": 0,
                "evaluation_round": 0,
                "consecutive_wins": 0,
                "completed_horizons": (),
                "resumed_from_previous_cook": False,
            },
        ),
        ("evaluating", {}),
        (
            "qualified",
            {
                "consecutive_wins": 2,
                "completed_horizons": (3, 15, 45, 90, 180),
            },
        ),
        (
            "activating",
            {
                "consecutive_wins": 2,
                "completed_horizons": (3, 15, 45, 90, 180),
            },
        ),
        ("retired", {"reset_reason": "incumbent-changed"}),
    ),
)
def test_challenger_progress_trace_round_trips_every_durable_phase(phase, replacement) -> None:
    payload = replace(
        _challenger_progress_trace_payload(),
        phase=phase,
        **replacement,
    )
    record = ControlTraceRecord(
        ts_ms=25_000,
        session_id="trace-session-2",
        cook_id="cook-1",
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.CHALLENGER_PROGRESS,
        payload=payload,
    )

    restored = ControlTraceRecord.model_validate_json(record.model_dump_json())

    assert restored.payload == payload
    assert restored.payload.phase == phase


@pytest.mark.parametrize(
    ("factory", "replacement"),
    (
        (_estimator_seed_trace_payload, {"pre_roll_frame_count": True}),
        (_trajectory_segment_trace_payload, {"segment_schema_version": True}),
        (_challenger_progress_trace_payload, {"resumed_from_previous_cook": 1}),
    ),
)
def test_segmented_learning_payloads_reject_coercible_scalar_values(factory, replacement) -> None:
    with pytest.raises(ValidationError):
        replace(factory(), **replacement)


@pytest.mark.parametrize(("event_kind", "payload"), _mpc_only_learning_trace_payload_cases())
@pytest.mark.parametrize("controller", (ControllerType.PID, ControllerType.PID_SP))
def test_non_observation_learning_payloads_are_mpc_only(controller, event_kind, payload) -> None:
    with pytest.raises(ValidationError, match="MPC-only"):
        ControlTraceRecord(
            ts_ms=25_000,
            session_id="trace-session-2",
            cook_id="cook-1",
            controller=controller,
            event_kind=event_kind,
            payload=payload,
        )


def test_pid_sp_trajectory_segment_round_trips_through_db() -> None:
    payload = _trajectory_segment_trace_payload()
    record = ControlTraceRecord(
        ts_ms=25_000,
        session_id="trace-session-2",
        cook_id="cook-1",
        controller=ControllerType.PID_SP,
        event_kind=TraceEventKind.TRAJECTORY_SEGMENT,
        payload=payload,
    )

    restored = ControlTraceRecord.from_db_row(record.to_db_row())

    assert restored == record
    assert restored.controller is ControllerType.PID_SP
    assert restored.payload == payload


def test_pid_trajectory_segment_is_rejected() -> None:
    with pytest.raises(ValidationError, match="MPC or PID-SP"):
        ControlTraceRecord(
            ts_ms=25_000,
            session_id="trace-session-2",
            cook_id="cook-1",
            controller=ControllerType.PID,
            event_kind=TraceEventKind.TRAJECTORY_SEGMENT,
            payload=_trajectory_segment_trace_payload(),
        )


def test_pid_sp_model_observation_round_trips_through_db() -> None:
    payload = _canonical_observation_payload(calibration=False)
    record = ControlTraceRecord(
        ts_ms=25_000,
        session_id="trace-session-2",
        cook_id="cook-1",
        controller=ControllerType.PID_SP,
        event_kind=TraceEventKind.MODEL_OBSERVATION,
        payload=payload,
    )

    restored = ControlTraceRecord.from_db_row(record.to_db_row())

    assert restored == record
    assert restored.controller is ControllerType.PID_SP
    assert restored.payload == payload


def test_pid_model_observation_is_rejected() -> None:
    with pytest.raises(ValidationError, match="MPC or PID-SP"):
        ControlTraceRecord(
            ts_ms=25_000,
            session_id="trace-session-2",
            cook_id="cook-1",
            controller=ControllerType.PID,
            event_kind=TraceEventKind.MODEL_OBSERVATION,
            payload=_canonical_observation_payload(calibration=False),
        )


@pytest.mark.parametrize(("event_kind", "payload"), _segmented_learning_trace_payload_cases())
def test_segmented_learning_payloads_require_their_exact_event_kind(event_kind, payload) -> None:
    with pytest.raises(ValidationError, match="event_kind"):
        ControlTraceRecord(
            ts_ms=25_000,
            session_id="trace-session-2",
            cook_id="cook-1",
            controller=ControllerType.MPC,
            event_kind=TraceEventKind.RECORDER_GAP,
            payload=payload,
        )


@pytest.mark.parametrize(("event_kind", "payload"), _segmented_learning_trace_payload_cases())
def test_schema_v7_rejects_segmented_learning_payloads(event_kind, payload) -> None:
    with pytest.raises(ValidationError, match="schema version 7"):
        ControlTraceRecord(
            ts_ms=25_000,
            session_id="trace-session-2",
            cook_id="cook-1",
            controller=ControllerType.MPC,
            event_kind=event_kind,
            schema_version=7,
            payload=payload,
        )


def test_pid_sp_update_round_trips_owned_learning_snapshot() -> None:
    payload = replace(
        _pid_sp_update_payload(),
        learning=LearningSnapshotPayload(
            schema_version=1,
            state={
                "status": "collecting",
                "gates": [{"name": "accepted_samples", "passed": False}],
            },
        ),
    )
    record = ControlTraceRecord(
        ts_ms=1_000,
        session_id="session-learning",
        cook_id="cook-learning",
        controller=ControllerType.PID_SP,
        event_kind=TraceEventKind.CONTROL_UPDATE,
        payload=payload,
    )

    restored = ControlTraceRecord.model_validate_json(record.model_dump_json())

    assert restored.schema_version == TRACE_SCHEMA_VERSION
    assert isinstance(restored.payload, PidSpUpdatePayload)
    assert restored.payload.learning is not None
    assert restored.payload.learning.state == {
        "status": "collecting",
        "gates": [{"name": "accepted_samples", "passed": False}],
    }


@pytest.mark.parametrize(
    "state",
    [
        {"nested": [{"value": nan}]},
        {"nested": {"value": inf}},
        {"nested": {1: "non-string key"}},
        {"nested": [object()]},
    ],
    ids=("nan", "infinity", "non-string-key", "unsupported-object"),
)
def test_learning_snapshot_rejects_values_that_are_not_strict_json(state) -> None:
    with pytest.raises(ValidationError):
        LearningSnapshotPayload(schema_version=1, state=state)


def test_learning_snapshot_requires_a_positive_schema_version() -> None:
    with pytest.raises(ValidationError):
        LearningSnapshotPayload(schema_version=0, state={})


def test_pid_update_without_learning_round_trips_as_none() -> None:
    record = ControlTraceRecord(
        ts_ms=1_000,
        session_id="session-pid",
        cook_id=None,
        controller=ControllerType.PID,
        event_kind=TraceEventKind.CONTROL_UPDATE,
        payload=replace(_pid_update_payload(), learning=None),
    )

    restored = ControlTraceRecord.model_validate_json(record.model_dump_json())

    assert isinstance(restored.payload, PidUpdatePayload)
    assert restored.payload.learning is None


@pytest.mark.parametrize("schema_version", [2, 3, 4, 5])
def test_compatible_historical_control_updates_without_learning_remain_readable(schema_version) -> None:
    record = ControlTraceRecord(
        ts_ms=1_000,
        session_id="historical-session",
        cook_id=None,
        controller=ControllerType.PID,
        event_kind=TraceEventKind.CONTROL_UPDATE,
        payload=_pid_update_payload(),
    )
    raw = record.model_dump(mode="json")
    raw["schema_version"] = schema_version
    raw["payload"].pop("learning", None)

    restored = ControlTraceRecord.model_validate_json(json.dumps(raw))

    assert restored.schema_version == schema_version
    assert isinstance(restored.payload, PidUpdatePayload)
    assert restored.payload.learning is None


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
        "challenger_model_kind": "grey-box",
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
            "MPC or PID-SP",
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


def test_framed_reset_delivery_survives_the_millisecond_bounds_it_is_compared_against():
    # The frame's duration is recovered from int(x * 1000) bounds, so it reads up
    # to a millisecond short of the float delivery it is compared with. A reset
    # that stops the auger mid-frame produces exactly that pair, and refusing it
    # costs the trace record for every lid opening and safety trip.
    payload = FramedPulseFramePayload(
        result_revision=1,
        pulse_slot_seconds=2.0,
        frame_seconds=20.0,
        frame_start_ms=0,
        frame_end_ms=7_333,
        requested_combustion_load=0.4,
        requested_auger_duty=0.4,
        credit_before_seconds=0.0,
        credit_after_seconds=0.0,
        scheduled_on_seconds=2.0,
        delivered_on_seconds=7.3333331,
        actual_start_active=True,
        transition_count=0,
        actual_end_active=True,
        requested_fan_duty=None,
        applied_fan_duty=None,
        skipped=False,
        stale_command=False,
        inhibit_reason=InhibitReason.LID_OPEN,
        reset_reason="lid",
    )

    assert payload.delivered_on_seconds == 7.3333331


def test_framed_delivery_beyond_the_millisecond_tolerance_is_still_refused():
    with pytest.raises(ValidationError, match="delivered_on_seconds must not exceed"):
        FramedPulseFramePayload(
            result_revision=1,
            pulse_slot_seconds=2.0,
            frame_seconds=20.0,
            frame_start_ms=0,
            frame_end_ms=7_333,
            requested_combustion_load=0.4,
            requested_auger_duty=0.4,
            credit_before_seconds=0.0,
            credit_after_seconds=0.0,
            scheduled_on_seconds=2.0,
            delivered_on_seconds=7.4,
            actual_start_active=True,
            transition_count=0,
            actual_end_active=True,
            requested_fan_duty=None,
            applied_fan_duty=None,
            skipped=False,
            stale_command=False,
            inhibit_reason=InhibitReason.LID_OPEN,
            reset_reason="lid",
        )


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


def test_schema_eight_has_one_canonical_model_evidence_contract():
    assert TRACE_SCHEMA_VERSION == 8
    assert {"u_min", "u_max", "hold_cycle_seconds"}.isdisjoint(SessionPayload.__annotations__)
    assert {"pulse_slot_seconds", "pulse_frame_seconds"} <= SessionPayload.__annotations__.keys()
    assert "FAN_ASSIST" not in OutputSource.__members__
    session = _pid_session_payload()
    with pytest.raises(ValidationError):
        replace(session, pulse_slot_seconds=None)
    with pytest.raises(ValidationError):
        replace(session, pulse_frame_seconds=None)


def test_schema_eight_fit_lifecycle_requires_a_corpus_sha256():
    with pytest.raises(ValidationError, match="current fit corpus digest"):
        ControlTraceRecord(
            ts_ms=10,
            session_id="session-grey",
            cook_id="cook-grey",
            controller=ControllerType.MPC,
            event_kind=TraceEventKind.FIT_LIFECYCLE,
            payload=GreyFitLifecyclePayload(
                request_id="invalid-corpus",
                status="running",
                origin="passive-online",
                policy="causal-auto",
                fit_corpus_digest="legacy-window",
            ),
        )


@pytest.mark.parametrize(
    ("kind", "payload"),
    (
        (
            TraceEventKind.FIT_LIFECYCLE,
            GreyFitLifecyclePayload(
                request_id="request-grey",
                status="running",
                origin="passive-online",
                policy="causal-auto",
                fit_corpus_digest="6" * 64,
                error=None,
            ),
        ),
        (
            TraceEventKind.CANDIDATE_ASSESSMENT,
            GreyCandidateAssessmentPayload(
                decision_id="decision-grey",
                origin="operator-calibration",
                policy="causal-auto",
                fit_accepted=True,
                identifiability_accepted=True,
                native_build="passed",
                native_dry_solve="passed",
                target_timing="passed",
                confidence_accepted=True,
            ),
        ),
        (
            TraceEventKind.ACTIVATION_LIFECYCLE,
            GreyActivationLifecyclePayload(
                decision_id="decision-grey",
                phase="prepared",
                origin="operator-calibration",
                policy="causal-auto",
            ),
        ),
        (
            TraceEventKind.LEARNING_FAILURE,
            GreyLearningFailurePayload(
                code="fit-process-exit",
                detail="worker exited before delivering the requested fit",
                terminal=True,
            ),
        ),
    ),
)
def test_schema_eight_round_trips_current_grey_lifecycle_vocabulary(kind, payload):
    record = ControlTraceRecord(
        ts_ms=10,
        session_id="session-grey",
        cook_id="cook-grey",
        controller=ControllerType.MPC,
        event_kind=kind,
        payload=payload,
    )

    restored = ControlTraceRecord.from_db_row(record.to_db_row())
    assert restored == record
    encoded = restored.model_dump_json()
    assert "state_space_refresh" not in encoded


_RETIRED_GREY_LIFECYCLE_PAYLOADS = (
    (
        TraceEventKind.FIT_LIFECYCLE,
        GreyFitLifecyclePayload(
            request_id="legacy-request",
            status="running",
            origin="passive-online",
            policy="passive-auto",
            fit_corpus_digest="legacy-window",
        ),
    ),
    (
        TraceEventKind.CANDIDATE_ASSESSMENT,
        GreyCandidateAssessmentPayload(
            decision_id="legacy-assessment",
            origin="operator-calibration",
            policy="operator-reviewed",
            fit_accepted=True,
            identifiability_accepted=True,
            native_build="passed",
            native_dry_solve="passed",
            target_timing="passed",
            confidence_accepted=True,
        ),
    ),
    (
        TraceEventKind.ACTIVATION_LIFECYCLE,
        GreyActivationLifecyclePayload(
            decision_id="legacy-activation",
            phase="prepared",
            origin="cook-refit",
            policy="cook-refit",
        ),
    ),
    (
        TraceEventKind.MODEL_EVENT,
        ModelEventPayload(
            event=ModelEventType.SCHEMA_INVALIDATED,
            model_revision=None,
            provenance=None,
            detail="legacy schema invalidation",
        ),
    ),
)


@pytest.mark.parametrize(("kind", "payload"), _RETIRED_GREY_LIFECYCLE_PAYLOADS)
def test_schema_eight_rejects_retired_lifecycle_authority(kind, payload):
    with pytest.raises(ValidationError, match="retired"):
        ControlTraceRecord(
            ts_ms=10,
            session_id="session-grey",
            cook_id="cook-grey",
            controller=ControllerType.MPC,
            event_kind=kind,
            payload=payload,
        )


def test_schema_eight_writer_revalidates_copied_retired_lifecycle_payload():
    valid = ControlTraceRecord(
        ts_ms=10,
        session_id="session-grey",
        cook_id="cook-grey",
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.FIT_LIFECYCLE,
        payload=GreyFitLifecyclePayload(
            request_id="current-request",
            status="running",
            origin="passive-online",
            policy="causal-auto",
            fit_corpus_digest="6" * 64,
        ),
    )
    copied = valid.model_copy(update={"payload": _RETIRED_GREY_LIFECYCLE_PAYLOADS[0][1]})

    with pytest.raises(ValueError, match="retired"):
        copied.to_db_row()


@pytest.mark.parametrize("schema_version", (5, 6, 7))
@pytest.mark.parametrize(("kind", "payload"), _RETIRED_GREY_LIFECYCLE_PAYLOADS)
def test_old_trace_schemas_explicitly_round_trip_retired_lifecycle_vocabulary(
    schema_version,
    kind,
    payload,
):
    record = ControlTraceRecord(
        ts_ms=10,
        session_id="session-grey",
        cook_id="cook-grey",
        controller=ControllerType.MPC,
        event_kind=kind,
        schema_version=schema_version,
        payload=payload,
    )

    assert ControlTraceRecord.from_db_row(record.to_db_row()) == record


def test_old_fit_window_trace_row_migrates_to_corpus_lifecycle_identity():
    row = ControlTraceDbRow(
        ts_ms=10,
        session_id="session-grey",
        cook_id="cook-grey",
        controller=ControllerType.MPC.value,
        event_kind=TraceEventKind.FIT_LIFECYCLE.value,
        schema_version=7,
        payload=json.dumps(
            {
                "request_id": "legacy-request",
                "status": "running",
                "origin": "passive-online",
                "policy": "passive-auto",
                "window_id": "session-grey:1:99",
                "error": None,
                "payload_type": "fit_lifecycle",
            }
        ),
    )

    restored = ControlTraceRecord.from_db_row(row)

    assert isinstance(restored.payload, GreyFitLifecyclePayload)
    assert restored.payload.fit_corpus_digest == "session-grey:1:99"
    migrated_payload = json.loads(restored.to_db_row().payload)
    assert migrated_payload["fit_corpus_digest"] == "session-grey:1:99"
    assert "window_id" not in migrated_payload


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
    payload = _canonical_observation_payload(calibration=False)
    with pytest.raises(ValidationError):
        replace(payload, **replacement)


def test_enriched_model_lifecycle_metadata_is_bounded_and_round_trips():
    payload = ModelEventPayload(
        event=ModelEventType.ADOPT,
        model_revision=8,
        provenance="fit-42",
        detail="adopted a validated model",
        model_kind="grey-box",
        model_schema="pifire-grey-learning/v4",
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
    observation = _canonical_observation_payload(calibration=False)
    with pytest.raises(ValidationError, match="schema version 2"):
        ControlTraceRecord(
            ts_ms=1,
            session_id="legacy",
            controller=ControllerType.MPC,
            event_kind=TraceEventKind.MODEL_OBSERVATION,
            schema_version=2,
            payload=observation,
        )


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


def test_v7_model_observation_round_trip_omits_legacy_v6_score_keys() -> None:
    obsolete_keys = {"incumbent_innovation_c", "challenger_innovation_c"}
    payload = _canonical_observation_payload(calibration=False)
    record = ControlTraceRecord(
        ts_ms=20_000,
        session_id="session-v7",
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.MODEL_OBSERVATION,
        schema_version=7,
        payload=payload,
    )

    encoded = record.model_dump_json()
    restored = ControlTraceRecord.model_validate_json(encoded)

    assert restored.payload == payload
    assert obsolete_keys.isdisjoint(json.loads(encoded)["payload"])


def test_v6_model_observation_migration_drops_only_obsolete_score_keys() -> None:
    obsolete_scores = {
        "incumbent_innovation_c": 1.0,
        "challenger_innovation_c": 0.5,
    }
    payload = _canonical_observation_payload(calibration=False)
    current = ControlTraceRecord(
        ts_ms=20_000,
        session_id="session-v6",
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.MODEL_OBSERVATION,
        schema_version=7,
        payload=payload,
    )
    legacy_payload = json.loads(current.to_db_row().payload)
    legacy_payload.update(obsolete_scores)
    legacy_row = replace(
        current.to_db_row(),
        schema_version=6,
        payload=json.dumps(legacy_payload),
    )

    restored = ControlTraceRecord.from_db_row(legacy_row)

    assert restored.payload == payload
    assert obsolete_scores.keys().isdisjoint(json.loads(restored.to_db_row().payload))

    unrelated_extra = dict(legacy_payload, unexpected_legacy_key=True)
    with pytest.raises(ValidationError):
        ControlTraceRecord.from_db_row(
            replace(legacy_row, payload=json.dumps(unrelated_extra)),
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


def test_schema_four_accepts_a_reset_shortened_observation_frame() -> None:
    payload = replace(
        _canonical_observation_payload(calibration=False),
        frame_end_ms=7_333,
        scheduled_on_seconds=20.0,
        delivered_on_seconds=7.3333331,
        realized_auger_duty=1.0,
        realized_combustion_load=0.40,
        reset=True,
        continuous=False,
        eligible=False,
        rejection_reasons=("observation-gate-mismatch",),
    )

    assert payload.frame_end_ms - payload.frame_start_ms == 7_333
    assert payload.reset is True
    assert payload.calibration_status == "inactive"


@pytest.mark.parametrize(
    "replacement",
    (
        {"frame_end_ms": 7_333},
        {"frame_end_ms": 30_000, "reset": True, "continuous": False},
        {
            "frame_end_ms": 7_333,
            "reset": True,
            "continuous": False,
            "delivered_on_seconds": 7.4,
            "scheduled_on_seconds": 20.0,
        },
    ),
)
def test_schema_four_rejects_frame_durations_no_reset_explains(replacement) -> None:
    with pytest.raises(ValidationError):
        replace(_canonical_observation_payload(calibration=False), **replacement)


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
