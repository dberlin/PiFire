from dataclasses import replace
from types import MappingProxyType
from uuid import UUID

import pytest

from common.control_trace import (
    CalibrationEventType,
    CalibrationTracePayload,
    ControllerBranch,
    ControllerType,
    InhibitReason,
    ModelEventPayload,
    ModelEventType,
    MpcFailureState,
    MpcUpdatePayload,
    PidSpUpdatePayload,
    PidUpdatePayload,
    RecorderGapPayload,
    ResultStaleState,
    SafetyEventType,
    TraceEventKind,
)
from controller.applied_output import AppliedOutput, FrameFeedbackDisposition, OutputSource, seed_output
from controller.base import MpcTraceDiagnostics, PidSpTraceDiagnostics, PidTraceDiagnostics
from controller.mpc_allocator import allocate
from controller.runtime.control_trace_session import (
    ControlTraceSession,
    TraceAppliedIntervalContext,
    TraceFrameContext,
    TraceModelAuthority,
    TraceModelContext,
    TraceOutputContext,
    TraceSafetyContext,
    TraceSessionContext,
    TraceUpdateContext,
)
from controller.runtime.framed_pulse import FramedPulseCompletion
from controller.runtime.logic.pulse import PulseFrameResult
from controller.runtime.runner import ControllerUpdateResult


class _Recorder:
    def __init__(self) -> None:
        self.records = []
        self.record_calls = 0
        self.fail_record_calls: set[int] = set()
        self.fail_flush = False
        self.flushes: list[int] = []
        self.close_calls = 0
        self.fail_close = False

    def record(self, record) -> None:
        self.record_calls += 1
        if self.record_calls in self.fail_record_calls:
            raise RuntimeError(f"record-{self.record_calls}")
        self.records.append(record)

    def flush_due(self, now_ms: int) -> None:
        self.flushes.append(now_ms)
        if self.fail_flush:
            raise RuntimeError("flush")

    def close(self) -> None:
        self.close_calls += 1
        if self.fail_close:
            raise RuntimeError("close")


def _context(
    *,
    cook_id: str = "cook-1",
    controller: ControllerType = ControllerType.MPC,
    fallback_model: TraceModelAuthority | None = None,
    fallback_safe: bool = True,
    generation: int = 4,
) -> TraceSessionContext:
    return TraceSessionContext(
        controller=controller,
        controller_config=MappingProxyType(
            {
                "zeta": 3,
                "nested": MappingProxyType({"beta": True, "alpha": 1.5}),
                "ignored": None,
                "name": "configured",
            }
        ),
        temperature_unit="C",
        control_period_seconds=2.0,
        fallback_model=fallback_model,
        runner_snapshot_fallback_safe=fallback_safe,
        pulse_slot_seconds=10.0,
        pulse_frame_seconds=20.0,
        fan_authority=True,
        fan_pwm_capable=True,
        fan_min_duty=30.0,
        fan_max_duty=100.0,
        setpoint=225.0,
        ambient_temperature=20.0,
        software_version="1.2.3",
        build_version="build-4",
        cook_id=cook_id,
        runner_generation=generation,
    )


def _open(
    recorder: _Recorder,
    warnings: list[str] | None = None,
    *,
    controller: ControllerType = ControllerType.MPC,
) -> ControlTraceSession:
    session = ControlTraceSession(recorder, warning=(warnings if warnings is not None else []).append)
    assert session.ensure_open(_context(controller=controller), timestamp_ms=1_000) is not None
    return session


def _pid_result(*, sp: bool = False, revision: int = 1) -> ControllerUpdateResult:
    if sp:
        diagnostics = PidSpTraceDiagnostics(
            observed_dt_seconds=2.0,
            error=5.0,
            proportional_term=0.2,
            integral_term=0.1,
            derivative_term=0.0,
            integral_accumulator=0.1,
            integral_clamped=False,
            derivative_input=1.0,
            derivative_state=0.5,
            proportional_band=100.0,
            kp=1.0,
            ki=0.1,
            kd=0.0,
            center=225.0,
            previous_temperature=219.0,
            previous_update_time=1.0,
            raw_output=0.3,
            final_output=0.3,
            measured_rate=-0.4,
            predicted_temperature=221.5,
            predicted_error=3.5,
            tau_seconds=12.0,
            theta_seconds=4.0,
            stable_window_seconds=15.0,
            center_factor=0.75,
            new_target_before=True,
            new_target_after=False,
            target_change_temperature=218.0,
            target_change_time=0.5,
            branch=ControllerBranch.OVERSHOOT,
        )
    else:
        diagnostics = PidTraceDiagnostics(
            observed_dt_seconds=2.0,
            error=5.0,
            proportional_term=0.2,
            integral_term=0.1,
            derivative_term=0.0,
            integral_accumulator=0.1,
            integral_clamped=False,
            derivative_input=1.0,
            derivative_state=0.5,
            proportional_band=100.0,
            kp=1.0,
            ki=0.1,
            kd=0.0,
            center=225.0,
            previous_temperature=219.0,
            previous_update_time=1.0,
            raw_output=0.3,
            final_output=0.3,
        )
    return ControllerUpdateResult(
        cycle_ratio=0.3,
        fan=None,
        input_temperature=220.0,
        diagnostics=diagnostics,
        revision=revision,
        solve_start_monotonic=1.0,
        solve_end_monotonic=1.1,
        solve_duration_seconds=1.1 - 1.0,
        completed_wall_time=1.1,
    )


def _mpc_result(
    revision: int = 1,
    *,
    stale_state: ResultStaleState = ResultStaleState.FRESH,
    recovered: bool = False,
) -> ControllerUpdateResult:
    diagnostics = MpcTraceDiagnostics(
        state_names=("temperature",),
        state_values=(220.0,),
        disturbance_estimate=0.0,
        model_revision=7,
        model_provenance="configured",
        raw_policy_firing_load=0.4,
        equilibrium_feed_forward=0.35,
        residual_move=0.05,
        bounded_firing_load=0.4,
        applied_combustion_load=0.4,
        policy_kind="net",
        failure_state=MpcFailureState.SUCCESS,
        consecutive_policy_failures=0,
        solve_start_monotonic=1.0,
        solve_end_monotonic=1.1,
        solve_duration_seconds=1.1 - 1.0,
        model_lifecycle=None,
    )
    allocation = allocate(0.4, u_max=0.9, fan_min_pct=40.0, fan_max_pct=100.0, enable_fan=True)
    assert allocation.fan_duty is not None
    return ControllerUpdateResult(
        cycle_ratio=allocation.auger_duty,
        fan={"duty": allocation.fan_duty},
        input_temperature=220.0,
        diagnostics=diagnostics,
        allocation=allocation,
        revision=revision,
        solve_start_monotonic=1.0,
        solve_end_monotonic=1.1,
        solve_duration_seconds=1.1 - 1.0,
        completed_wall_time=1.1,
        result_age_seconds=3.0 if stale_state is ResultStaleState.STALE else 0.0,
        stale_state=stale_state,
        recovered=recovered,
    )


def _update_context(
    result: ControllerUpdateResult,
    *,
    timestamp_ms: int = 2_000,
    lifecycle: ModelEventPayload | None = None,
) -> TraceUpdateContext:
    return TraceUpdateContext(
        result=result,
        timestamp_ms=timestamp_ms,
        controller_interval_seconds=2.0,
        setpoint=225.0,
        prior_requested_auger_duty=0.2,
        prior_realized_auger_duty=0.15,
        prior_fan_duty=55.0,
        controls_fan=True,
        lid_open=False,
        manual_override_active=False,
        lifecycle_event=lifecycle,
    )


def _completion(*, source: OutputSource = OutputSource.CONTROLLER) -> FramedPulseCompletion:
    frame = PulseFrameResult(
        nominal_start_s=20.0,
        nominal_end_s=40.0,
        ended_at_s=40.0,
        complete=True,
        skipped=False,
        latched_request=0.5,
        credit_before_s=0.0,
        credit_after_s=0.0,
        scheduled_on_s=10,
        delivered_on_s=10.0,
        observed_transition_count=1,
        actual_start_on=True,
        actual_end_on=False,
        reset_reason=None,
    )
    applied = AppliedOutput(
        ratio=0.5,
        requested=0.5,
        source=source,
        timestamp=40.0,
        producing_result_revision=5,
        feedback_disposition=FrameFeedbackDisposition.COMPLETE,
        sample_complete=True,
    )
    return FramedPulseCompletion(
        frame=frame,
        inhibit=InhibitReason.NONE,
        result_revision=5,
        source=source,
        requested_combustion_load=0.4,
        requested_fan_duty=60.0,
        stale_command=False,
        applied_fan_duty=60.0,
        frame_key=(20_000, 40_000),
        observation=None,
        applied=applied,
        realized_combustion_load=0.4,
        missing_observation_reason=None,
        observation_sequence=1,
    )


def test_ensure_open_validates_identity_and_flattens_sorted_settings() -> None:
    recorder = _Recorder()
    warnings: list[str] = []
    session = ControlTraceSession(recorder, warning=warnings.append)

    assert session.ensure_open(_context(cook_id=""), timestamp_ms=1_000) is None
    identity = session.ensure_open(_context(), timestamp_ms=1_000)

    assert identity is not None
    assert UUID(identity.session_id)
    assert (identity.cook_id, identity.controller, identity.runner_generation) == (
        "cook-1",
        ControllerType.MPC,
        4,
    )
    assert session.identity == identity
    payload = recorder.records[0].payload
    assert [(item.key, item.value) for item in payload.controller_config] == [
        ("name", "configured"),
        ("nested.alpha", 1.5),
        ("nested.beta", True),
        ("zeta", 3),
    ]


def test_model_authority_validation_and_explicit_fallback_policy() -> None:
    recorder = _Recorder()
    warnings: list[str] = []
    session = ControlTraceSession(recorder, warning=warnings.append)
    fallback = TraceModelAuthority(MappingProxyType({"revision": 3}), "runner")

    session.set_model_authority(MappingProxyType({"revision": True}), "invalid")
    session.set_model_authority(MappingProxyType({"revision": -1}), "invalid")
    session.set_model_authority(MappingProxyType({"revision": 9}), "restored")
    session.ensure_open(_context(fallback_model=fallback), timestamp_ms=1_000)
    first = recorder.records[0].payload
    assert (first.model_revision, first.model_provenance) == (9, "restored")

    session.rotate(runner_snapshot_fallback_safe=False)
    session.ensure_open(_context(fallback_model=fallback, fallback_safe=False), timestamp_ms=2_000)
    second = recorder.records[-1].payload
    assert (second.model_revision, second.model_provenance) == (None, None)

    session.rotate(runner_snapshot_fallback_safe=True)
    session.ensure_open(_context(fallback_model=fallback), timestamp_ms=3_000)
    third = recorder.records[-1].payload
    assert (third.model_revision, third.model_provenance) == (3, "runner")


def test_record_failures_warn_once_until_successful_recovery() -> None:
    recorder = _Recorder()
    warnings: list[str] = []
    session = _open(recorder, warnings)
    recorder.fail_record_calls = {2, 3, 5}
    safety = TraceSafetyContext(SafetyEventType.LID_DETECTED, InhibitReason.LID_OPEN, 1, "lid", 2_000)

    assert not session.record_safety(safety)
    assert not session.record_safety(safety)
    assert len(warnings) == 1
    assert session.status.warning_active
    assert session.record_safety(safety)
    assert not session.status.warning_active
    assert not session.record_safety(safety)
    assert len(warnings) == 2


def test_pending_model_fifo_stops_without_loss_and_resumes_once() -> None:
    recorder = _Recorder()
    recorder.fail_record_calls = {2}
    warnings: list[str] = []
    session = ControlTraceSession(recorder, warning=warnings.append)
    first = ModelEventPayload(event=ModelEventType.RESTORE, model_revision=1, provenance="disk", detail="first")
    second = ModelEventPayload(event=ModelEventType.ADOPT, model_revision=2, provenance="fit", detail="second")
    session.queue_model_event(first, 100)
    session.queue_model_event(second, 200)

    session.ensure_open(_context(), timestamp_ms=50)
    assert session.status.pending_model_events == 2
    assert [record.event_kind for record in recorder.records] == [TraceEventKind.SESSION]

    session.flush_pending()
    session.flush_pending()
    assert [record.payload.detail for record in recorder.records[1:]] == ["first", "second"]
    assert session.status.pending_model_events == 0
    assert len(warnings) == 1


def test_periodic_flush_exception_is_bounded_and_recovers() -> None:
    recorder = _Recorder()
    warnings: list[str] = []
    session = _open(recorder, warnings)
    recorder.fail_flush = True

    assert not session.flush_due(5_000)
    assert not session.flush_due(10_000)
    assert len(warnings) == 1
    recorder.fail_flush = False
    assert session.flush_due(15_000)
    assert recorder.flushes == [5_000, 10_000, 15_000]
    assert not session.status.warning_active


@pytest.mark.parametrize(
    ("result", "payload_type"),
    [
        (_pid_result(), PidUpdatePayload),
        (_pid_result(sp=True), PidSpUpdatePayload),
        (_mpc_result(), MpcUpdatePayload),
    ],
)
def test_record_update_builds_exact_controller_payloads(result, payload_type) -> None:
    recorder = _Recorder()
    controller = (
        ControllerType.PID_SP
        if payload_type is PidSpUpdatePayload
        else ControllerType.PID
        if payload_type is PidUpdatePayload
        else ControllerType.MPC
    )
    session = _open(recorder, controller=controller)

    assert session.record_update(_update_context(result))

    update = next(record for record in recorder.records if record.event_kind is TraceEventKind.CONTROL_UPDATE)
    assert isinstance(update.payload, payload_type)
    assert (
        update.payload.result_revision,
        update.payload.setpoint,
        update.payload.prior_requested_auger_duty,
        update.payload.prior_realized_auger_duty,
        update.payload.output_source,
    ) == (1, 225.0, 0.2, 0.15, OutputSource.CONTROLLER)
    if isinstance(update.payload, MpcUpdatePayload):
        allocation_record = next(
            record for record in recorder.records if record.event_kind is TraceEventKind.ALLOCATION
        )
        assert allocation_record.payload.result_revision == 1
        assert allocation_record.payload.mpc_has_fan_authority


def test_update_revision_rules_stale_replay_and_lifecycle_fifo() -> None:
    recorder = _Recorder()
    session = _open(recorder)
    lifecycle = ModelEventPayload(
        event=ModelEventType.ADOPT,
        model_revision=7,
        provenance="runtime",
        detail="activated",
    )
    fresh = _mpc_result(3)

    assert session.record_update(_update_context(fresh, lifecycle=lifecycle))
    assert session.record_update(
        _update_context(
            replace(fresh, stale_state=ResultStaleState.STALE, result_age_seconds=4.0),
            timestamp_ms=3_000,
        )
    )
    assert not session.record_update(
        _update_context(replace(fresh, stale_state=ResultStaleState.STALE), timestamp_ms=4_000)
    )
    assert not session.record_update(_update_context(_mpc_result(2), timestamp_ms=5_000))
    assert not session.record_update(_update_context(fresh, timestamp_ms=6_000))

    updates = [record.payload for record in recorder.records if record.event_kind is TraceEventKind.CONTROL_UPDATE]
    assert len(updates) == 2
    assert (updates[1].result_revision, updates[1].stale, updates[1].recovered, updates[1].result_age_ms) == (
        3,
        True,
        False,
        4_000,
    )
    assert session.update_state.result_revision == 3
    assert session.update_state.mpc_stale
    assert [record.event_kind for record in recorder.records[1:4]] == [
        TraceEventKind.CONTROL_UPDATE,
        TraceEventKind.MODEL_EVENT,
        TraceEventKind.ALLOCATION,
    ]


def test_safety_model_gap_and_calibration_records_use_typed_envelopes() -> None:
    recorder = _Recorder()
    session = _open(recorder)
    assert session.record_safety(
        TraceSafetyContext(SafetyEventType.MANUAL_RELEASE, InhibitReason.NONE, None, "released", 2_000)
    )
    assert session.record_model(
        TraceModelContext(
            ModelEventType.REFIT,
            "refit",
            MappingProxyType({"revision": 8}),
            "persisted",
            3_000,
        )
    )
    assert session.record(
        TraceEventKind.RECORDER_GAP,
        RecorderGapPayload(
            lost_record_count=1,
            gap_start_ms=3_000,
            gap_end_ms=3_001,
            observation_sequence=1,
            reason="missing",
        ),
        3_001,
    )
    assert session.record(
        TraceEventKind.CALIBRATION,
        CalibrationTracePayload(
            event=CalibrationEventType.START_REQUESTED,
            command_revision=1,
            command_action="start",
            result_revision=1,
            stage=None,
            intended_probe_load=0.1,
            bounded_probe_load=0.1,
            cumulative_probe_load=0.1,
            eligible_observations=0,
            positive_observations=0,
            negative_observations=0,
            reasons=(),
        ),
        4_000,
    )
    assert [record.event_kind for record in recorder.records[1:]] == [
        TraceEventKind.SAFETY_EVENT,
        TraceEventKind.MODEL_EVENT,
        TraceEventKind.RECORDER_GAP,
        TraceEventKind.CALIBRATION,
    ]
    identity = session.identity
    assert identity is not None
    assert all(record.session_id == identity.session_id for record in recorder.records)


def test_frame_and_terminal_applied_intervals_preserve_boundaries_and_identity() -> None:
    recorder = _Recorder()
    session = _open(recorder)
    completion = _completion()
    assert session.record_frame(TraceFrameContext(completion, pulse_slot_seconds=10.0, frame_seconds=20.0))

    seed = seed_output(0.0, 0.0, lid_open=False, manual_override_active=False, auger_output=False)
    prepared_seed = session.prepare_applied_output(
        seed,
        TraceOutputContext(timestamp_ms=0, pulse_frame_result_revision=0, fan_duty=None),
    )
    coalesced = session.prepare_applied_output(
        AppliedOutput(0.2, OutputSource.CONTROLLER, 1.0, requested=0.3),
        TraceOutputContext(timestamp_ms=1_000, pulse_frame_result_revision=0, fan_duty=40.0),
    )
    assert prepared_seed.producing_result_revision == 0
    assert coalesced.producing_result_revision == 0
    assert session.applied_state.interval_start_ms == 0
    assert session.applied_state.output_source is OutputSource.SEED

    assert session.record_applied_interval(
        TraceAppliedIntervalContext(
            timestamp_ms=20_000,
            sample_complete=False,
            realized_combustion_load=None,
            controls_fan=True,
        )
    )
    session.prepare_applied_output(
        AppliedOutput(0.5, OutputSource.CONTROLLER, 20.0, requested=0.5),
        TraceOutputContext(
            timestamp_ms=20_000,
            pulse_frame_result_revision=5,
            fan_duty=60.0,
            producing_revision=5,
        ),
    )
    assert session.record_terminal_framed_output(completion, controls_fan=True)

    intervals = [record.payload for record in recorder.records if record.event_kind is TraceEventKind.APPLIED_OUTPUT]
    assert [(item.interval_start_ms, item.interval_end_ms, item.result_revision) for item in intervals] == [
        (0, 20_000, 0),
        (20_000, 40_000, 5),
    ]
    assert intervals[0].sample_complete
    assert intervals[1].realized_auger_duty == 0.5
    assert intervals[1].realized_combustion_load == 0.4
    assert session.applied_state.result_revision == 5



def test_measured_feedback_closes_prior_interval_and_seeds_next_realized_load() -> None:
    recorder = _Recorder()
    session = _open(recorder)
    session.prepare_applied_output(
        seed_output(0.0, 0.0, lid_open=False, manual_override_active=False, auger_output=False),
        TraceOutputContext(timestamp_ms=0, pulse_frame_result_revision=0, fan_duty=None),
    )

    session.prepare_applied_output(
        AppliedOutput(0.4, OutputSource.CONTROLLER, 20.0, requested=0.5),
        TraceOutputContext(
            timestamp_ms=20_000,
            pulse_frame_result_revision=4,
            fan_duty=60.0,
            controls_fan=True,
            producing_revision=4,
            sample_complete=True,
            measured_combustion_load=0.3,
        ),
    )
    session.prepare_applied_output(
        AppliedOutput(0.2, OutputSource.CONTROLLER, 40.0, requested=0.2),
        TraceOutputContext(
            timestamp_ms=40_000,
            pulse_frame_result_revision=5,
            fan_duty=50.0,
            controls_fan=True,
            producing_revision=5,
            sample_complete=True,
        ),
    )

    intervals = [record.payload for record in recorder.records if record.event_kind is TraceEventKind.APPLIED_OUTPUT]
    assert intervals[-1].realized_combustion_load == 0.3
    assert intervals[-1].result_revision == 4


def test_seed_promotion_keeps_interval_boundary_and_adopts_first_frame_identity() -> None:
    recorder = _Recorder()
    session = _open(recorder)
    session.prepare_applied_output(
        seed_output(0.0, 0.0, lid_open=False, manual_override_active=False, auger_output=False),
        TraceOutputContext(timestamp_ms=0, pulse_frame_result_revision=0, fan_duty=None),
    )

    assert session.promote_seed_interval(6, OutputSource.CONTROLLER)
    assert session.record_applied_interval(
        TraceAppliedIntervalContext(
            timestamp_ms=20_000,
            sample_complete=True,
            realized_combustion_load=0.25,
            controls_fan=False,
        )
    )

    interval = recorder.records[-1].payload
    assert (interval.interval_start_ms, interval.result_revision, interval.output_source) == (
        0,
        6,
        OutputSource.CONTROLLER,
    )

def test_reset_preserves_pending_fifo_and_clears_session_local_state() -> None:
    recorder = _Recorder()
    session = _open(recorder)
    event = ModelEventPayload(event=ModelEventType.REJECT, model_revision=None, provenance=None, detail="later")
    session.rotate(runner_snapshot_fallback_safe=False)
    session.queue_model_event(event, 2_000)

    assert session.identity is None
    assert session.status.pending_model_events == 1
    session.ensure_open(_context(generation=5, fallback_safe=False), timestamp_ms=3_000)
    assert session.identity is not None and session.identity.runner_generation == 5
    assert recorder.records[-1].payload == event
    assert session.status.pending_model_events == 0
    assert session.update_state.result_revision == -1


def test_close_before_or_after_open_is_idempotent_and_best_effort() -> None:
    first = _Recorder()
    ignored_warnings: list[str] = []
    before_open = ControlTraceSession(first, warning=ignored_warnings.append)
    before_open.close()
    before_open.close()
    assert first.close_calls == 1
    assert before_open.status.closed

    second = _Recorder()
    warnings: list[str] = []
    after_open = _open(second, warnings)
    second.fail_close = True
    after_open.close()
    after_open.close()
    assert second.close_calls == 1
    assert len(warnings) == 1

    unavailable = ControlTraceSession(None, warning=warnings.append)
    assert unavailable.ensure_open(_context(), timestamp_ms=1_000) is None
    unavailable.flush_pending()
    assert unavailable.flush_due(5_000)
    unavailable.close()
    assert unavailable.status.closed


def test_open_and_record_failures_leave_no_partial_identity_and_warning_callbacks_are_best_effort() -> None:
    recorder = _Recorder()
    recorder.fail_record_calls.add(1)

    def fail_warning(message: str) -> None:
        raise RuntimeError(message)

    session = ControlTraceSession(recorder, warning=fail_warning)
    payload = ModelEventPayload(
        event=ModelEventType.REJECT,
        model_revision=None,
        provenance=None,
        detail="not-open",
    )

    assert not session.record(TraceEventKind.MODEL_EVENT, payload, 500)
    assert session.ensure_open(_context(), timestamp_ms=1_000) is None
    assert session.identity is None
    assert session.status.warning_active


def test_repeated_open_and_invalid_updates_frames_and_seed_promotions_are_rejected() -> None:
    recorder = _Recorder()
    session = _open(recorder)
    identity = session.identity
    assert identity is not None
    assert session.ensure_open(_context(), timestamp_ms=2_000) == identity
    assert not session.record_update(
        _update_context(ControllerUpdateResult(cycle_ratio=0.3, fan=None, input_temperature=200.0))
    )
    assert not session.record_frame(
        TraceFrameContext(
            replace(_completion(), result_revision=0),
            pulse_slot_seconds=10.0,
            frame_seconds=20.0,
        )
    )
    assert not session.promote_seed_interval(0, OutputSource.CONTROLLER)
    assert not session.record_terminal_framed_output(
        replace(_completion(), applied=None),
        controls_fan=True,
    )


def test_model_authority_property_and_model_event_without_snapshot_are_explicit() -> None:
    recorder = _Recorder()
    session = _open(recorder)
    assert session.model_authority is None
    session.set_model_authority({"revision": 9}, "runtime")
    authority = session.model_authority
    assert authority is not None
    assert authority.snapshot["revision"] == 9
    assert session.record_model(
        TraceModelContext(
            event=ModelEventType.REJECT,
            detail="no snapshot",
            snapshot=None,
            provenance="runtime",
            timestamp_ms=3_000,
        )
    )
