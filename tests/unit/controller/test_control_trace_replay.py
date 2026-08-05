"""Contracts for pure, typed control-trace replay validation."""

from dataclasses import replace
import sqlite3
from typing import Any, TypedDict, cast

import pytest

from common.control_trace import (
    TRACE_SCHEMA_VERSION,
    ActuationMode,
    AllocationPayload,
    AppliedOutputPayload,
    ControlTraceRecord,
    ControllerBranch,
    ControllerType,
    FixedCycleFramePayload,
    FramedPulseFramePayload,
    InhibitReason,
    MpcFailureState,
    MpcUpdatePayload,
    ResultStaleState,
    ModelEventPayload,
    ModelEventType,
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
from controller.control_trace_replay import ReplayIssueCode, TraceSelectionError, replay_session, validate_records
from controller.mpc_allocator import ALLOCATOR_REVISION, allocate

_SESSION_ID = "session-1"


class _CommonUpdate(TypedDict):
    monotonic_ms: int
    wall_ms: int
    result_revision: int
    result_age_ms: int
    control_period_seconds: float
    observed_dt_seconds: float
    setpoint: float
    measured_temperature: float
    raw_output: float
    requested_output: float
    actuation_mode: ActuationMode
    prior_requested_auger_duty: float
    prior_realized_auger_duty: float
    requested_fan_duty: float | None
    applied_fan_duty: float | None
    output_source: OutputSource
    inhibit_reason: InhibitReason


def _record(ts_ms, controller, kind, payload, *, session_id=_SESSION_ID):
    return ControlTraceRecord(
        ts_ms=ts_ms,
        session_id=session_id,
        cook_id="cook-1",
        controller=controller,
        event_kind=kind,
        schema_version=TRACE_SCHEMA_VERSION,
        payload=payload,
    )


def _common(
    revision,
    *,
    mode=ActuationMode.FIXED_CYCLE,
    output=0.5,
    requested_fan_duty=None,
    applied_fan_duty=None,
) -> _CommonUpdate:
    return {
        "monotonic_ms": revision * 2_000,
        "wall_ms": revision * 2_000,
        "result_revision": revision,
        "result_age_ms": 0,
        "control_period_seconds": 2.0,
        "observed_dt_seconds": 2.0,
        "setpoint": 225.0,
        "measured_temperature": 220.0,
        "raw_output": output,
        "requested_output": output,
        "actuation_mode": mode,
        "prior_requested_auger_duty": 0.5,
        "prior_realized_auger_duty": 0.5,
        "requested_fan_duty": requested_fan_duty,
        "applied_fan_duty": applied_fan_duty,
        "output_source": OutputSource.CONTROLLER,
        "inhibit_reason": InhibitReason.NONE,
    }


def _pid_session(controller=ControllerType.PID):
    return SessionPayload(
        controller=controller,
        controller_config=(TraceSetting(key="kp", value=1.0),),
        temperature_unit="F",
        control_period_seconds=2.0,
        model_revision=None,
        model_provenance=None,
        u_min=0.1,
        u_max=0.9,
        hold_cycle_seconds=20.0,
        pulse_slot_seconds=None,
        pulse_frame_seconds=None,
        fan_authority=False,
        fan_pwm_capable=False,
        fan_min_duty=0.0,
        fan_max_duty=100.0,
        setpoint=225.0,
        ambient_temperature=70.0,
        software_version="test",
        build_version="1",
    )


def _mpc_session():
    return SessionPayload(
        controller=ControllerType.MPC,
        controller_config=(TraceSetting(key="unused", value=1),),
        temperature_unit="F",
        control_period_seconds=2.0,
        model_revision=1,
        model_provenance="configured",
        u_min=None,
        u_max=None,
        hold_cycle_seconds=None,
        pulse_slot_seconds=2.0,
        pulse_frame_seconds=20.0,
        fan_authority=True,
        fan_pwm_capable=True,
        fan_min_duty=40.0,
        fan_max_duty=100.0,
        setpoint=225.0,
        ambient_temperature=70.0,
        software_version="test",
        build_version="1",
    )


def _pid_update(revision=1):
    return PidUpdatePayload(
        **_common(revision),
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
        previous_update_ms=(revision - 1) * 2_000,
    )


def _pid_sp_update(revision=1):
    return PidSpUpdatePayload(
        **_common(revision),
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
        previous_update_ms=(revision - 1) * 2_000,
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
    )


def _mpc_update(revision=1, *, mode=ActuationMode.FIXED_CYCLE):
    return MpcUpdatePayload(
        **_common(revision, mode=mode, output=0.5, requested_fan_duty=70.0, applied_fan_duty=70.0),
        state_names=("temperature",),
        state_values=(220.0,),
        disturbance_estimate=0.0,
        model_revision=1,
        model_provenance="configured",
        raw_policy_firing_load=0.5,
        equilibrium_feed_forward=0.45,
        residual_move=0.05,
        bounded_firing_load=0.5,
        policy_kind="net",
        failure_state=MpcFailureState.SUCCESS,
        solve_start_ms=revision * 2_000 - 1,
        solve_end_ms=revision * 2_000,
        deadline_miss_count=0,
        stale=False,
        recovered=False,
        predicted_feasible=True,
        predicted_steady_load=0.5,
        solve_duration_ms=1,
        consecutive_deadline_miss_count=0,
        stale_state=ResultStaleState.FRESH,
    )


def _allocation(revision=1):
    result = allocate(0.5, u_max=0.9, fan_min_pct=40.0, fan_max_pct=100.0, enable_fan=True)
    return AllocationPayload(
        result_revision=revision,
        normalized_combustion_load=result.normalized_combustion_load,
        requested_auger_duty=result.auger_duty,
        requested_fan_duty=result.fan_duty,
        u_max=result.u_max,
        fan_min_pct=result.fan_min_pct,
        fan_max_pct=result.fan_max_pct,
        fan_enabled=result.fan_enabled,
        mpc_has_fan_authority=True,
        auger_clamp_reason=result.auger_clamp_reason,
        fan_clamp_reason=result.fan_clamp_reason,
        allocator_revision=ALLOCATOR_REVISION,
    )


def _fixed_frame(revision=1):
    return FixedCycleFramePayload(
        **cast(
            Any,
            {
                "result_revision": revision,
                "raw_requested_duty": 0.5,
                "bounded_duty": 0.5,
                "u_min": 0.1,
                "u_max": 0.9,
                "cycle_start_ms": 2_000,
                "cycle_end_ms": 4_000,
                "scheduled_on_seconds": 1.0,
                "scheduled_off_seconds": 1.0,
                "actual_on_seconds": 1.0,
                "actual_start_active": False,
                "transition_count": 2,
                "fan_assist_active": False,
                "inhibit_reason": InhibitReason.NONE,
                "output_active": False,
            },
        )
    )


def _applied(revision=1):
    return AppliedOutputPayload(
        result_revision=revision,
        interval_start_ms=2_000,
        interval_end_ms=4_000,
        realized_auger_duty=0.5,
        realized_combustion_load=0.5,
        actual_fan_duty=70.0,
        sample_complete=True,
        output_source=OutputSource.CONTROLLER,
    )


def _pid_records(controller=ControllerType.PID):
    update = _pid_sp_update() if controller is ControllerType.PID_SP else _pid_update()
    return [
        _record(0, controller, TraceEventKind.SESSION, _pid_session(controller)),
        _record(2_000, controller, TraceEventKind.CONTROL_UPDATE, update),
        _record(4_000, controller, TraceEventKind.ACTUATION_FRAME, _fixed_frame()),
        _record(4_000, controller, TraceEventKind.APPLIED_OUTPUT, replace(_applied(), realized_combustion_load=None)),
    ]


def _mpc_fixed_records():
    return [
        _record(0, ControllerType.MPC, TraceEventKind.SESSION, _mpc_session()),
        _record(2_000, ControllerType.MPC, TraceEventKind.CONTROL_UPDATE, _mpc_update()),
        _record(2_000, ControllerType.MPC, TraceEventKind.ALLOCATION, _allocation()),
        _record(4_000, ControllerType.MPC, TraceEventKind.ACTUATION_FRAME, _fixed_frame()),
        _record(4_000, ControllerType.MPC, TraceEventKind.APPLIED_OUTPUT, _applied()),
    ]


def _mpc_framed_records():
    allocation = _allocation()
    frame = FramedPulseFramePayload(
        **cast(
            Any,
            {
                "result_revision": 1,
                "pulse_slot_seconds": 2.0,
                "frame_seconds": 20.0,
                "frame_start_ms": 2_000,
                "frame_end_ms": 22_000,
                "requested_combustion_load": 0.5,
                "requested_auger_duty": allocation.requested_auger_duty,
                "credit_before_seconds": 0.0,
                "credit_after_seconds": 0.0,
                "scheduled_on_seconds": 10.0,
                "delivered_on_seconds": 10.0,
                "transition_count": 2,
                "actual_start_active": False,
                "actual_end_active": False,
                "requested_fan_duty": 70.0,
                "applied_fan_duty": 70.0,
                "skipped": False,
                "stale_command": False,
                "inhibit_reason": InhibitReason.NONE,
                "reset_reason": None,
            },
        )
    )
    return [
        _record(0, ControllerType.MPC, TraceEventKind.SESSION, _mpc_session()),
        _record(2_000, ControllerType.MPC, TraceEventKind.CONTROL_UPDATE, _mpc_update(mode=ActuationMode.FRAMED_PULSE)),
        _record(2_000, ControllerType.MPC, TraceEventKind.ALLOCATION, allocation),
        _record(22_000, ControllerType.MPC, TraceEventKind.ACTUATION_FRAME, frame),
        _record(
            22_000,
            ControllerType.MPC,
            TraceEventKind.APPLIED_OUTPUT,
            replace(_applied(), interval_end_ms=22_000),
        ),
    ]


@pytest.mark.parametrize(
    "records", [_pid_records(), _pid_records(ControllerType.PID_SP), _mpc_fixed_records(), _mpc_framed_records()]
)
def test_validate_records_accepts_pristine_typed_sessions(records):
    report = validate_records(records)
    assert report.valid
    assert report.session_id == _SESSION_ID
    assert report.controller is records[0].controller
    assert report.issues == ()


def test_replay_accepts_exactly_one_same_revision_mpc_stale_observation():
    records = _mpc_fixed_records()
    fresh = records[1].payload
    stale = replace(
        fresh,
        result_age_ms=10_000,
        stale=True,
        stale_state=ResultStaleState.STALE,
    )
    stale_record = _record(3_000, ControllerType.MPC, TraceEventKind.CONTROL_UPDATE, stale)
    observed = records[:3] + [stale_record] + records[3:]

    assert validate_records(observed).valid

    duplicate_fresh = _record(3_000, ControllerType.MPC, TraceEventKind.CONTROL_UPDATE, fresh)
    repeated_stale = _record(3_001, ControllerType.MPC, TraceEventKind.CONTROL_UPDATE, stale)
    changed_stale = _record(
        3_001,
        ControllerType.MPC,
        TraceEventKind.CONTROL_UPDATE,
        replace(stale, bounded_firing_load=0.6),
    )
    for invalid in (duplicate_fresh, repeated_stale, changed_stale):
        report = validate_records(observed + [invalid])
        assert ReplayIssueCode.NON_MONOTONE_REVISION in [issue.code for issue in report.issues]


def test_validate_records_reconciles_framed_delivery_across_feedback_intervals_and_defers_open_tail():
    records = _mpc_framed_records()
    first = replace(_applied(), interval_start_ms=2_000, interval_end_ms=12_000)
    second = replace(_applied(), interval_start_ms=12_000, interval_end_ms=22_000)
    open_tail = replace(_applied(), interval_start_ms=22_000, interval_end_ms=24_000, realized_auger_duty=0.0)
    records = (
        records[:3]
        + [_record(12_000, ControllerType.MPC, TraceEventKind.APPLIED_OUTPUT, first)]
        + [records[3]]
        + [
            _record(22_000, ControllerType.MPC, TraceEventKind.APPLIED_OUTPUT, second),
            _record(24_000, ControllerType.MPC, TraceEventKind.APPLIED_OUTPUT, open_tail),
        ]
    )

    assert validate_records(records).valid


def test_validate_records_accepts_queued_earlier_model_event_after_session():
    records = _pid_records()
    session = records[0].model_copy(update={"ts_ms": 2_000})
    restore = _record(
        1_000,
        ControllerType.PID,
        TraceEventKind.MODEL_EVENT,
        ModelEventPayload(ModelEventType.RESTORE, None, "persisted", "queued at setup"),
    )

    assert validate_records([session, restore] + records[1:]).valid


def test_validate_records_preserves_deterministic_issue_order_after_typed_round_trip():
    records = _pid_records()
    corrupted = records[:2] + [
        _record(
            4_000, ControllerType.PID, TraceEventKind.ACTUATION_FRAME, replace(_fixed_frame(), scheduled_on_seconds=0.5)
        ),
        _record(
            4_000,
            ControllerType.PID,
            TraceEventKind.APPLIED_OUTPUT,
            replace(records[-1].payload, realized_auger_duty=0.1),
        ),
    ]
    round_tripped = [ControlTraceRecord.model_validate_json(record.model_dump_json()) for record in corrupted]
    assert validate_records(corrupted).issues == validate_records(round_tripped).issues


@pytest.mark.parametrize(
    ("records", "code"),
    [
        (
            _mpc_fixed_records()
            + [_record(5_000, ControllerType.MPC, TraceEventKind.RECORDER_GAP, RecorderGapPayload(1, 4_000, 5_000))],
            ReplayIssueCode.RECORDER_GAP,
        ),
        (
            _mpc_fixed_records()[:2]
            + [
                _record(
                    2_000,
                    ControllerType.MPC,
                    TraceEventKind.ALLOCATION,
                    replace(_allocation(), requested_auger_duty=0.1),
                )
            ]
            + _mpc_fixed_records()[3:],
            ReplayIssueCode.ALLOCATION_MISMATCH,
        ),
        (
            _pid_records()[:2]
            + [
                _record(
                    4_000,
                    ControllerType.PID,
                    TraceEventKind.ACTUATION_FRAME,
                    replace(_fixed_frame(), scheduled_on_seconds=0.5),
                )
            ]
            + _pid_records()[3:],
            ReplayIssueCode.FRAME_SCHEDULE_MISMATCH,
        ),
        (
            _mpc_framed_records()[:3]
            + [
                _record(
                    22_000,
                    ControllerType.MPC,
                    TraceEventKind.ACTUATION_FRAME,
                    replace(_mpc_framed_records()[3].payload, delivered_on_seconds=11.0),
                )
            ],
            ReplayIssueCode.FRAME_DELIVERY_MISMATCH,
        ),
        (
            _pid_records()[:3]
            + [
                _record(
                    4_000,
                    ControllerType.PID,
                    TraceEventKind.APPLIED_OUTPUT,
                    replace(_pid_records()[3].payload, realized_auger_duty=0.1),
                )
            ],
            ReplayIssueCode.APPLIED_OUTPUT_MISMATCH,
        ),
        (
            _pid_records()[:2]
            + [
                _record(
                    4_000,
                    ControllerType.PID,
                    TraceEventKind.ACTUATION_FRAME,
                    replace(_fixed_frame(), inhibit_reason=InhibitReason.LID_OPEN),
                )
            ]
            + _pid_records()[3:],
            ReplayIssueCode.UNEXPLAINED_INHIBIT,
        ),
        (
            _pid_records()[:2] + [_record(3_000, ControllerType.PID, TraceEventKind.CONTROL_UPDATE, _pid_update())],
            ReplayIssueCode.NON_MONOTONE_REVISION,
        ),
        (
            _pid_records()[:-1]
            + [
                _record(
                    3_000,
                    ControllerType.PID,
                    TraceEventKind.APPLIED_OUTPUT,
                    replace(_pid_records()[3].payload, interval_end_ms=2_000),
                )
            ],
            ReplayIssueCode.NON_POSITIVE_INTERVAL,
        ),
        (
            [_pid_records()[0].model_copy(update={"schema_version": TRACE_SCHEMA_VERSION + 1})] + _pid_records()[1:],
            ReplayIssueCode.UNSUPPORTED_SCHEMA,
        ),
        (
            _mpc_fixed_records()[:2]
            + [
                _record(
                    2_000,
                    ControllerType.MPC,
                    TraceEventKind.ALLOCATION,
                    replace(_allocation(), allocator_revision=ALLOCATOR_REVISION + 1),
                )
            ]
            + _mpc_fixed_records()[3:],
            ReplayIssueCode.UNSUPPORTED_ALLOCATOR,
        ),
    ],
)
def test_validate_records_reports_each_independent_corruption(records, code):
    assert code in [issue.code for issue in validate_records(records).issues]


def test_validate_records_rejects_mixed_session_and_controller_in_deterministic_order():
    records = _pid_records()
    mixed = records[-1].model_copy(update={"session_id": "other", "controller": ControllerType.MPC})
    report = validate_records(records + [mixed])
    assert [issue.code for issue in report.issues][:2] == [
        ReplayIssueCode.SESSION_ID_MISMATCH,
        ReplayIssueCode.CONTROLLER_MISMATCH,
    ]


@pytest.mark.parametrize(
    "event",
    [
        SafetyEventType.STOP,
        SafetyEventType.ERROR,
        SafetyEventType.TEMPERATURE_GUARD,
        SafetyEventType.CONTROLLER_FALLBACK,
        SafetyEventType.CONTROLLER_RECONFIGURE,
    ],
)
def test_validate_records_accepts_recorded_terminal_safety_inhibition(event):
    records = _pid_records()
    safety = _record(
        3_000,
        ControllerType.PID,
        TraceEventKind.SAFETY_EVENT,
        SafetyEventPayload(event, InhibitReason.SAFETY, 1, event.value),
    )
    inhibited = replace(_fixed_frame(), actual_on_seconds=0.0, transition_count=0, inhibit_reason=InhibitReason.SAFETY)
    assert validate_records(
        records[:2] + [safety, _record(4_000, ControllerType.PID, TraceEventKind.ACTUATION_FRAME, inhibited)]
    ).valid


def test_validate_records_requires_scheduler_reset_event_for_framed_reset():
    records = _mpc_framed_records()
    reset_frame = replace(
        records[3].payload,
        frame_end_ms=3_000,
        delivered_on_seconds=0.0,
        transition_count=0,
        actual_start_active=False,
        actual_end_active=False,
        reset_reason="scheduler reset",
    )
    coverage = _record(
        3_000,
        ControllerType.MPC,
        TraceEventKind.APPLIED_OUTPUT,
        replace(_applied(), interval_end_ms=3_000, realized_auger_duty=0.0),
    )
    without_event = records[:3] + [
        _record(3_000, ControllerType.MPC, TraceEventKind.ACTUATION_FRAME, reset_frame),
        coverage,
    ]
    assert ReplayIssueCode.UNEXPLAINED_INHIBIT in [issue.code for issue in validate_records(without_event).issues]
    reset = _record(
        3_000,
        ControllerType.MPC,
        TraceEventKind.SAFETY_EVENT,
        SafetyEventPayload(SafetyEventType.SCHEDULER_RESET, InhibitReason.SAFETY, 1, "scheduler reset"),
    )
    assert validate_records(
        records[:3] + [reset, _record(3_000, ControllerType.MPC, TraceEventKind.ACTUATION_FRAME, reset_frame), coverage]
    ).valid


def test_validate_records_requires_safety_event_for_manual_and_allows_recorded_transition():
    records = _pid_records()
    manual = replace(records[-1].payload, output_source=OutputSource.MANUAL_OVERRIDE)
    without_event = records[:-1] + [_record(4_000, ControllerType.PID, TraceEventKind.APPLIED_OUTPUT, manual)]
    assert ReplayIssueCode.UNEXPLAINED_OUTPUT_SOURCE in [issue.code for issue in validate_records(without_event).issues]
    event = _record(
        2_000,
        ControllerType.PID,
        TraceEventKind.SAFETY_EVENT,
        SafetyEventPayload(SafetyEventType.MANUAL_TAKEOVER, InhibitReason.MANUAL_OVERRIDE, 1, "manual"),
    )
    assert validate_records(
        records[:2]
        + [event]
        + records[2:-1]
        + [_record(4_000, ControllerType.PID, TraceEventKind.APPLIED_OUTPUT, manual)]
    ).valid


def test_validate_records_requires_lid_event_for_lid_output():
    records = _pid_records()
    lid_output = replace(records[-1].payload, output_source=OutputSource.LID_OPEN)
    without_event = records[:-1] + [_record(4_000, ControllerType.PID, TraceEventKind.APPLIED_OUTPUT, lid_output)]
    assert ReplayIssueCode.UNEXPLAINED_OUTPUT_SOURCE in [issue.code for issue in validate_records(without_event).issues]
    detected = _record(
        2_000,
        ControllerType.PID,
        TraceEventKind.SAFETY_EVENT,
        SafetyEventPayload(SafetyEventType.LID_DETECTED, InhibitReason.LID_OPEN, 1, "lid"),
    )
    assert validate_records(
        records[:2]
        + [detected]
        + records[2:-1]
        + [_record(4_000, ControllerType.PID, TraceEventKind.APPLIED_OUTPUT, lid_output)]
    ).valid


@pytest.mark.parametrize(
    ("event", "inhibit"),
    [
        (SafetyEventType.MANUAL_TAKEOVER, InhibitReason.MANUAL_OVERRIDE),
        (SafetyEventType.LID_DETECTED, InhibitReason.LID_OPEN),
    ],
)
def test_validate_records_allows_only_same_boundary_safety_for_an_ending_frame(event, inhibit):
    records = _pid_records()
    frame = replace(records[2].payload, inhibit_reason=inhibit)
    boundary = _record(
        4_000,
        ControllerType.PID,
        TraceEventKind.SAFETY_EVENT,
        SafetyEventPayload(event, inhibit, 1, "frame ended for recorded safety"),
    )

    assert validate_records(
        records[:2]
        + [_record(4_000, ControllerType.PID, TraceEventKind.ACTUATION_FRAME, frame), boundary]
        + records[3:]
    ).valid


@pytest.mark.parametrize("timestamp", (3_999, 4_001))
def test_validate_records_rejects_different_timestamp_safety_for_an_ending_frame(timestamp):
    records = _pid_records()
    frame = replace(records[2].payload, inhibit_reason=InhibitReason.MANUAL_OVERRIDE)
    wrong_boundary = _record(
        timestamp,
        ControllerType.PID,
        TraceEventKind.SAFETY_EVENT,
        SafetyEventPayload(
            SafetyEventType.MANUAL_TAKEOVER,
            InhibitReason.MANUAL_OVERRIDE,
            1,
            "not at the frame boundary",
        ),
    )

    report = validate_records(
        records[:2]
        + [_record(4_000, ControllerType.PID, TraceEventKind.ACTUATION_FRAME, frame), wrong_boundary]
        + records[3:]
    )
    assert ReplayIssueCode.UNEXPLAINED_INHIBIT in [issue.code for issue in report.issues]


def test_validate_records_models_terminal_partial_output_and_rejects_interior_partial():
    records = _pid_records()
    terminal = replace(records[-1].payload, sample_complete=False, realized_combustion_load=None)
    assert validate_records(
        records[:-1] + [_record(4_000, ControllerType.PID, TraceEventKind.APPLIED_OUTPUT, terminal)]
    ).valid
    interior = records[:-1] + [_record(3_000, ControllerType.PID, TraceEventKind.APPLIED_OUTPUT, terminal), records[-1]]
    assert ReplayIssueCode.INVALID_PARTIAL_OUTPUT in [issue.code for issue in validate_records(interior).issues]


def test_replay_session_uses_typed_accessor_and_translates_selection_failure(monkeypatch):
    import controller.control_trace_replay as replay

    monkeypatch.setattr(replay, "read_control_trace_session", lambda *_args, **_kwargs: _pid_records())
    assert replay_session(_SESSION_ID, database_path="trace.db").valid
    monkeypatch.setattr(
        replay,
        "read_control_trace_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("missing")),
    )
    with pytest.raises(TraceSelectionError, match="session-1"):
        replay_session(_SESSION_ID)


def test_validate_records_accepts_controller_partial_followed_by_manual_takeover():
    records = _pid_records()
    partial = replace(records[-1].payload, sample_complete=False, realized_combustion_load=None)
    takeover = _record(
        4_000,
        ControllerType.PID,
        TraceEventKind.SAFETY_EVENT,
        SafetyEventPayload(SafetyEventType.MANUAL_TAKEOVER, InhibitReason.MANUAL_OVERRIDE, 1, "manual"),
    )
    replay = records[:-1] + [
        _record(4_000, ControllerType.PID, TraceEventKind.APPLIED_OUTPUT, partial),
        takeover,
        _record(6_000, ControllerType.PID, TraceEventKind.CONTROL_UPDATE, _pid_update(2)),
    ]
    assert validate_records(replay).valid


def test_validate_records_rejects_duplicate_allocation_and_mismatched_cook():
    records = _mpc_fixed_records()
    duplicate = _record(2_001, ControllerType.MPC, TraceEventKind.ALLOCATION, _allocation())
    report = validate_records(records[:3] + [duplicate] + records[3:])
    assert ReplayIssueCode.DUPLICATE_ALLOCATION in [issue.code for issue in report.issues]
    wrong_cook = records[-1].model_copy(update={"cook_id": "other-cook"})
    assert ReplayIssueCode.COOK_ID_MISMATCH in [
        issue.code for issue in validate_records(records[:-1] + [wrong_cook]).issues
    ]


def test_replay_session_translates_missing_table_and_corrupt_sqlite(tmp_path):
    missing_table = tmp_path / "empty.db"
    sqlite3.connect(missing_table).close()
    with pytest.raises(TraceSelectionError, match="session-1"):
        replay_session(_SESSION_ID, database_path=missing_table)

    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"not a sqlite database")
    with pytest.raises(TraceSelectionError, match="session-1"):
        replay_session(_SESSION_ID, database_path=corrupt)


def test_validate_records_reconciles_only_same_revision_fixed_and_framed_delivery():
    pid = _pid_records()
    cross_revision = [
        pid[0],
        pid[1],
        _record(4_000, ControllerType.PID, TraceEventKind.CONTROL_UPDATE, _pid_update(2)),
        _record(4_000, ControllerType.PID, TraceEventKind.ACTUATION_FRAME, replace(_fixed_frame(), result_revision=2)),
        pid[-1],
    ]
    assert ReplayIssueCode.APPLIED_OUTPUT_MISMATCH in [issue.code for issue in validate_records(cross_revision).issues]

    framed = _mpc_framed_records()
    wrong_coverage = _record(
        22_000,
        ControllerType.MPC,
        TraceEventKind.APPLIED_OUTPUT,
        replace(_applied(), interval_end_ms=22_000, realized_auger_duty=0.1),
    )
    assert ReplayIssueCode.APPLIED_OUTPUT_MISMATCH in [
        issue.code for issue in validate_records(framed[:4] + [wrong_coverage]).issues
    ]

    open_tail = _record(
        24_000,
        ControllerType.MPC,
        TraceEventKind.APPLIED_OUTPUT,
        replace(_applied(), interval_start_ms=22_000, interval_end_ms=24_000, realized_auger_duty=0.0),
    )
    assert validate_records(framed + [open_tail]).valid


def test_validate_records_requires_safety_evidence_for_update_fields_and_matching_scheduler_reset():
    records = _pid_records()
    manual_update = _record(
        2_000,
        ControllerType.PID,
        TraceEventKind.CONTROL_UPDATE,
        replace(_pid_update(), output_source=OutputSource.MANUAL_OVERRIDE),
    )
    assert ReplayIssueCode.UNEXPLAINED_OUTPUT_SOURCE in [
        issue.code for issue in validate_records([records[0], manual_update] + records[2:]).issues
    ]

    frame = _mpc_framed_records()[3].payload
    reset = replace(
        frame,
        frame_end_ms=3_000,
        delivered_on_seconds=0.0,
        transition_count=0,
        actual_start_active=False,
        actual_end_active=False,
        reset_reason="scheduler reset",
    )
    coverage = _record(
        3_000,
        ControllerType.MPC,
        TraceEventKind.APPLIED_OUTPUT,
        replace(_applied(), interval_end_ms=3_000, realized_auger_duty=0.0),
    )
    framed = _mpc_framed_records()
    wrong_reset = _record(
        3_000,
        ControllerType.MPC,
        TraceEventKind.SAFETY_EVENT,
        SafetyEventPayload(SafetyEventType.SCHEDULER_RESET, InhibitReason.SAFETY, 2, "wrong revision"),
    )
    assert ReplayIssueCode.UNEXPLAINED_INHIBIT in [
        issue.code
        for issue in validate_records(
            framed[:3]
            + [wrong_reset, _record(3_000, ControllerType.MPC, TraceEventKind.ACTUATION_FRAME, reset), coverage]
        ).issues
    ]


@pytest.mark.parametrize(
    "event, timestamp",
    [
        (SafetyEventType.MANUAL_TAKEOVER, 4_001),
        (SafetyEventType.STOP, 4_000),
    ],
)
def test_validate_records_rejects_partial_without_exact_replacement_boundary(event, timestamp):
    records = _pid_records()
    partial = replace(records[-1].payload, sample_complete=False, realized_combustion_load=None)
    replacement = _record(
        timestamp,
        ControllerType.PID,
        TraceEventKind.SAFETY_EVENT,
        SafetyEventPayload(event, InhibitReason.MANUAL_OVERRIDE, 1, "not an exact manual replacement"),
    )
    later_update = _record(6_000, ControllerType.PID, TraceEventKind.CONTROL_UPDATE, _pid_update(2))
    report = validate_records(
        records[:-1]
        + [_record(4_000, ControllerType.PID, TraceEventKind.APPLIED_OUTPUT, partial), replacement, later_update]
    )
    assert ReplayIssueCode.INVALID_PARTIAL_OUTPUT in [issue.code for issue in report.issues]


@pytest.mark.parametrize(
    ("detected", "cleared", "source"),
    [
        (SafetyEventType.MANUAL_TAKEOVER, SafetyEventType.MANUAL_RELEASE, OutputSource.MANUAL_OVERRIDE),
        (SafetyEventType.LID_DETECTED, SafetyEventType.LID_CLEARED, OutputSource.LID_OPEN),
    ],
)
def test_validate_records_uses_source_authority_at_interval_start_after_later_release(detected, cleared, source):
    records = _pid_records()
    open_event = _record(
        2_000,
        ControllerType.PID,
        TraceEventKind.SAFETY_EVENT,
        SafetyEventPayload(detected, InhibitReason.NONE, 1, "start authority"),
    )
    release_event = _record(
        3_000,
        ControllerType.PID,
        TraceEventKind.SAFETY_EVENT,
        SafetyEventPayload(cleared, InhibitReason.NONE, 1, "later release"),
    )
    historical = replace(records[-1].payload, output_source=source)
    report = validate_records(
        records[:2]
        + [open_event, release_event]
        + records[2:-1]
        + [_record(4_000, ControllerType.PID, TraceEventKind.APPLIED_OUTPUT, historical)]
    )
    assert ReplayIssueCode.UNEXPLAINED_OUTPUT_SOURCE not in [issue.code for issue in report.issues]


def test_validate_records_allows_only_seed_applied_output_without_an_update():
    records = _pid_records()
    seed = replace(
        records[-1].payload,
        result_revision=0,
        interval_start_ms=0,
        interval_end_ms=1_000,
        realized_combustion_load=None,
        output_source=OutputSource.SEED,
    )
    assert validate_records(
        [records[0], _record(1_000, ControllerType.PID, TraceEventKind.APPLIED_OUTPUT, seed)] + records[1:]
    ).valid

    non_seed = replace(seed, output_source=OutputSource.CONTROLLER)
    assert ReplayIssueCode.MISSING_UPDATE in [
        issue.code
        for issue in validate_records(
            [records[0], _record(1_000, ControllerType.PID, TraceEventKind.APPLIED_OUTPUT, non_seed)] + records[1:]
        ).issues
    ]


def test_validate_records_requires_a_same_revision_frame_for_fixed_cycle_complete_output():
    records = _mpc_fixed_records()[:3] + _mpc_fixed_records()[4:]

    report = validate_records(records)

    assert ReplayIssueCode.APPLIED_OUTPUT_MISMATCH in [issue.code for issue in report.issues]


def test_validate_records_allows_framed_feedback_tail_before_frame_completion():
    records = _mpc_framed_records()[:3] + [
        _record(
            22_000,
            ControllerType.MPC,
            TraceEventKind.APPLIED_OUTPUT,
            replace(_applied(), interval_end_ms=22_000),
        )
    ]

    report = validate_records(records)

    assert report.valid
    assert ReplayIssueCode.APPLIED_OUTPUT_MISMATCH not in [issue.code for issue in report.issues]


@pytest.mark.parametrize(
    "replacement",
    [
        {"inhibit_reason": InhibitReason.SAFETY},
        {"result_revision": 2},
        {"event": SafetyEventType.LID_DETECTED, "inhibit_reason": InhibitReason.LID_OPEN},
    ],
)
def test_validate_records_rejects_wrong_boundary_safety_identity(replacement):
    records = _pid_records()
    frame = replace(records[2].payload, inhibit_reason=InhibitReason.MANUAL_OVERRIDE)
    event = SafetyEventPayload(
        SafetyEventType.MANUAL_TAKEOVER,
        InhibitReason.MANUAL_OVERRIDE,
        1,
        "wrong boundary identity",
    )
    event = replace(event, **replacement)
    report = validate_records(
        records[:2]
        + [_record(4_000, ControllerType.PID, TraceEventKind.ACTUATION_FRAME, frame)]
        + [_record(4_000, ControllerType.PID, TraceEventKind.SAFETY_EVENT, event)]
        + records[3:]
    )
    assert ReplayIssueCode.UNEXPLAINED_INHIBIT in [issue.code for issue in report.issues]


def test_validate_records_enforces_optional_seed_lifecycle():
    records = _pid_records()
    seed = replace(
        records[-1].payload,
        result_revision=0,
        interval_start_ms=0,
        interval_end_ms=1_000,
        realized_combustion_load=None,
        output_source=OutputSource.SEED,
    )
    seed_record = _record(1_000, ControllerType.PID, TraceEventKind.APPLIED_OUTPUT, seed)
    model_event = _record(
        500,
        ControllerType.PID,
        TraceEventKind.MODEL_EVENT,
        ModelEventPayload(ModelEventType.RESTORE, None, "persisted", "queued"),
    )
    assert validate_records([records[0], model_event, seed_record] + records[1:]).valid

    incomplete = seed_record.model_copy(update={"payload": replace(seed, sample_complete=False)})
    duplicate = seed_record.model_copy(update={"ts_ms": 1_001})
    late = seed_record.model_copy(update={"ts_ms": 5_000})
    for corrupted in (
        [records[0], incomplete] + records[1:],
        [records[0], seed_record, duplicate] + records[1:],
        records + [late],
    ):
        report = validate_records(corrupted)
        assert ReplayIssueCode.INVALID_SEED_OUTPUT in [issue.code for issue in report.issues]
