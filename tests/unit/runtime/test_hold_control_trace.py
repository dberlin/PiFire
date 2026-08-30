"""End-to-end Hold control-trace contracts driven through the normal fake runtime."""

import queue
import threading
from collections.abc import Callable
from dataclasses import replace
from types import SimpleNamespace

import pytest

from common import datastore
from common.control_trace import (
    ActuationMode,
    AllocationClampReason,
    AllocationPayload,
    AmbientSource,
    AmbientUncertainty,
    AppliedOutputPayload,
    ControllerBranch,
    ControllerType,
    InhibitReason,
    ModelEvaluationPayload,
    ModelObservationPayload,
    PidSpUpdatePayload,
    RecorderGapPayload,
    ResultStaleState,
    SafetyEventType,
    TraceEventKind,
)
from common.controller_model_state import CheckpointSaveOutcome
from common.model_evidence import ForecastOriginEvidence, ModelEvidenceRecord, RecorderGapEvidence
from common.persistence.control_trace import read_control_trace_session
from controller.applied_output import AppliedOutput, FrameFeedbackDisposition, OutputSource
from controller.base import (
    ControllerLearningDiagnostics,
    MpcFailureState,
    MpcTraceDiagnostics,
    PidSpTraceDiagnostics,
    PidTraceDiagnostics,
)
from controller.control_trace_replay import ReplayIssueCode, validate_records
from controller.model_learning.contracts import CandidateOrigin, FrameObservation
from controller.mpc import Controller
from controller.mpc_allocator import allocate
from controller.runtime.control_trace_recorder import ControlTraceRecorder
from controller.runtime.control_trace_session import (
    TraceAppliedIntervalContext,
    TraceOutputContext,
    TraceUpdateContext,
)
from controller.runtime.framed_pulse import FramedPulseRuntime
from controller.runtime.model_persistence import EvidenceSubmission
from controller.runtime.modes.hold import HoldMode
from controller.runtime.modes.hold_learning import parse_model_lifecycle_payload
from controller.runtime.runner import (
    ControllerUpdateResult,
    ObservationOutcomeEnvelope,
    SyncControllerRunner,
    ThreadedControllerRunner,
    build_runner,
)
from controller.update_mpc import load_trace_samples
from tests.characterization.fixtures import base_control, base_settings
from tests.fakes.runner import FakeControllerRunner


def _runtime(mode) -> FramedPulseRuntime:
    runtime = mode._framed_pulse
    assert isinstance(runtime, FramedPulseRuntime)
    return runtime


def _open_trace_session(mode, now):
    trace = mode._control_trace
    assert trace is not None
    context = mode._trace_session_context()
    assert context is not None
    previous = trace.identity
    identity = trace.ensure_open(context, timestamp_ms=int(now * 1_000))
    mode._bind_trajectory_trace(identity)
    learning = mode._hold_learning
    if previous is None and identity is not None and learning is not None:
        learning.bind_generation(mode._runner_configuration_revision)
    return identity


def _trace(mode):
    trace = mode._control_trace
    assert trace is not None
    return trace


def _learning(mode):
    learning = mode._hold_learning
    assert learning is not None
    return learning


def _identity(mode):
    identity = _trace(mode).identity
    assert identity is not None
    return identity


def _record_trace_update(mode, result, *, now, controller_interval):
    trace = _trace(mode)
    applied = trace.applied_state
    lifecycle = (
        parse_model_lifecycle_payload(result.diagnostics.model_lifecycle)
        if isinstance(result.diagnostics, MpcTraceDiagnostics)
        else None
    )
    return trace.record_update(
        TraceUpdateContext(
            result=result,
            timestamp_ms=int(now * 1_000),
            controller_interval_seconds=float(controller_interval),
            setpoint=float(mode.control["primary_setpoint"]),
            prior_requested_auger_duty=applied.requested_auger_duty,
            prior_realized_auger_duty=applied.realized_auger_duty,
            prior_fan_duty=applied.fan_duty,
            controls_fan=mode.state.controller.controls_fan,
            lid_open=mode.state.lid.open_detected,
            manual_override_active=mode.state.manual_override["auger"] >= now,
            lifecycle_event=lifecycle,
        )
    )


def _advance_runtime(mode, now, actual_auger_on, *, ptemp=None, apply_transition=True):
    result = _runtime(mode).advance(
        now,
        actual_auger_on,
        sample=mode._framed_sample(ptemp),
        prior_output_source=mode._control_trace.applied_state.output_source,
    )
    transition = result.decision.transition
    if apply_transition and transition is not None:
        if transition.command_on:
            mode.grill.auger_on()
        else:
            mode.grill.auger_off()
    mode._dispatch_framed_result(result, record_terminal_trace=False)
    return result.decision


def _observe_runtime(mode, frame, *, ptemp, inhibit, role_generation=None):
    runtime = _runtime(mode)
    controller = mode.state.controller
    controller.pulse_result_revision = controller.pulse_frame_result_revision
    controller.pulse_combustion_load = controller.pulse_frame_combustion_load
    controller.pulse_baseline_combustion_load = getattr(
        controller,
        "pulse_frame_baseline_combustion_load",
        controller.pulse_frame_combustion_load or 0.0,
    )
    controller.pulse_requested_duty = controller.pulse_frame_requested_auger_duty
    controller.pulse_maximum_duty = controller.pulse_frame_maximum_duty
    runtime.latch(mode._model_role_generation(mode._runner_status()) if role_generation is None else role_generation)
    completion = runtime.complete_frame(
        frame,
        sample=mode._framed_sample(ptemp),
        inhibit=inhibit,
    )
    if completion.observation is not None:
        assert completion.frame_key is not None
        _learning(mode).submit_completed_observation(
            completion.frame_key,
            completion.observation,
        )
    elif completion.missing_observation_reason is not None:
        mode._trace_missing_frame_observation(completion)
    return completion


class _Recorder:
    def __init__(self, *, warning):
        self.records = []
        self.flushes = []
        self.closed = 0

    def record(self, record):
        self.records.append(record)

    def flush_due(self, now_ms):
        self.flushes.append(now_ms)

    def close(self):
        self.closed += 1


def _pid_result(revision=1):
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
        input_temperature=100.0,
        diagnostics=diagnostics,
        revision=revision,
        solve_start_monotonic=1.0,
        solve_end_monotonic=1.1,
        solve_duration_seconds=1.1 - 1.0,
        completed_wall_time=1.1,
    )


def _pid_sp_result(revision=1):
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
    return ControllerUpdateResult(
        cycle_ratio=0.3,
        fan=None,
        input_temperature=100.0,
        diagnostics=diagnostics,
        allocation=allocate(
            0.3,
            u_max=1.0,
            fan_min_pct=0.0,
            fan_max_pct=0.0,
            enable_fan=False,
        ),
        revision=revision,
        solve_start_monotonic=1.0,
        solve_end_monotonic=1.1,
        solve_duration_seconds=1.1 - 1.0,
        completed_wall_time=1.1,
    )


def _mpc_result(
    revision=1,
    *,
    consecutive_policy_failures=0,
    model_lifecycle=None,
    raw_policy_firing_load=0.4,
    requested_auger_duty=None,
    enable_fan=True,
    applied_combustion_load=0.4,
    stale_state=ResultStaleState.FRESH,
    recovered=False,
):
    diagnostics = MpcTraceDiagnostics(
        state_names=("temperature",),
        state_values=(220.0,),
        disturbance_estimate=0.0,
        model_revision=1,
        model_provenance="configured",
        raw_policy_firing_load=raw_policy_firing_load,
        equilibrium_feed_forward=0.35,
        residual_move=0.05,
        bounded_firing_load=0.4,
        applied_combustion_load=applied_combustion_load,
        policy_kind="net",
        failure_state=MpcFailureState.SUCCESS,
        consecutive_policy_failures=consecutive_policy_failures,
        solve_start_monotonic=1.0,
        solve_end_monotonic=1.1,
        solve_duration_seconds=1.1 - 1.0,
        model_lifecycle=model_lifecycle,
    )
    allocation = allocate(
        0.4,
        u_max=0.9,
        fan_min_pct=40.0,
        fan_max_pct=100.0,
        enable_fan=enable_fan,
    )
    if requested_auger_duty is not None:
        allocation = replace(allocation, auger_duty=requested_auger_duty)
    return ControllerUpdateResult(
        cycle_ratio=allocation.auger_duty,
        fan=None if allocation.fan_duty is None else {"duty": allocation.fan_duty},
        input_temperature=100.0,
        diagnostics=diagnostics,
        allocation=allocation,
        revision=revision,
        solve_start_monotonic=1.0,
        solve_end_monotonic=1.1,
        solve_duration_seconds=1.1 - 1.0,
        completed_wall_time=1.1,
        stale_state=stale_state,
        recovered=recovered,
        result_age_seconds=0.0,
    )


def _install_recorder(monkeypatch):
    import controller.runtime.modes.hold as hold_module

    recorder = _Recorder(warning=lambda _message: None)
    monkeypatch.setattr(hold_module, "ControlTraceRecorder", lambda *, warning: recorder)
    return recorder


def test_trace_identity_uses_durable_control_cook_id_not_metric_row_id(
    hold_cycle,
    monkeypatch,
) -> None:
    _install_recorder(monkeypatch)
    mode = hold_cycle(FakeControllerRunner(period=1.0), controller="pid_sp")
    mode.setup()
    mode.control["cook_id"] = "durable-cook-session"
    mode.state.metrics = {"id": "per-mode-row-id"}

    identity = _open_trace_session(mode, 0.0)

    assert identity is not None
    assert identity.cook_id == "durable-cook-session"


def test_active_history_clear_rotates_trace_and_evidence_identity_before_next_write(
    hold_cycle,
    monkeypatch,
) -> None:
    import controller.runtime.store as store_mod

    generated_ids = iter(("rotated-cook-session", "fresh-mode-row"))
    monkeypatch.setattr(store_mod, "generate_uuid", lambda: next(generated_ids))
    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0)
    evidence_bindings = []
    bind_evidence = runner.bind_evidence_context

    def record_binding(generation, session_id, cook_id):
        evidence_bindings.append((generation, session_id, cook_id))
        bind_evidence(generation, session_id, cook_id)

    monkeypatch.setattr(runner, "bind_evidence_context", record_binding)
    mode = hold_cycle(runner, controller="pid_sp")
    mode.setup()
    mode.control["cook_id"] = "old-cook-session"
    mode.ctx.store.write_control_snapshot(mode.control, origin="control")
    mode.state.metrics = {
        "id": "stale-mode-row",
        "starttime": 1.0,
        "augerontime": 99.0,
        "mode": "Hold",
    }
    old_identity = _open_trace_session(mode, 0.0)
    assert old_identity is not None

    result = mode._handle_history_clear(now=2.0)
    refreshed = mode.ctx.store.read_control()

    assert result["result"] == "OK"
    assert refreshed["cook_id"] == "rotated-cook-session"
    assert mode.state.metrics["id"] == "fresh-mode-row"
    assert mode.state.metrics["mode"] == "Hold"
    assert mode.state.metrics["augerontime"] == 0
    assert mode.state.metrics["starttime"] != 1.0
    assert mode.state.timers.auger_toggle == 2.0
    session_records = [record for record in recorder.records if record.event_kind is TraceEventKind.SESSION]
    assert [record.cook_id for record in session_records] == [
        "old-cook-session",
        "rotated-cook-session",
    ]
    assert [binding[2] for binding in evidence_bindings] == [
        "old-cook-session",
        "rotated-cook-session",
    ]


def test_inactive_reconfigure_records_controller_fallback(
    hold_cycle,
    monkeypatch,
) -> None:
    recorder = _install_recorder(monkeypatch)

    runner = FakeControllerRunner(period=999)
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    reconfigure = runner.reconfigure

    def inactive_reconfigure(settings, control, logger=None):
        reconfigure(
            settings,
            control,
            logger=logger,
        )
        return "Inactive"

    monkeypatch.setattr(runner, "reconfigure", inactive_reconfigure)
    mode.control["cook_id"] = "inactive-reconfigure"
    mode.control["controller_update"] = True

    mode.on_tick(2.0, 200.0, mode.grill.get_output_status())

    fallbacks = [
        record.payload
        for record in recorder.records
        if (
            record.event_kind is TraceEventKind.SAFETY_EVENT
            and record.payload.event is SafetyEventType.CONTROLLER_FALLBACK
        )
    ]
    assert len(fallbacks) == 1
    assert fallbacks[0].detail == "controller reconfigure fell back"


def test_mpc_hold_records_update_allocation_and_framed_feedback_once_per_revision(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    result = _mpc_result()
    runner = FakeControllerRunner(period=1.0, commands_fan=True, actuation_mode=ActuationMode.FRAMED_PULSE).script(
        [result, result]
    )
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.control["cook_id"] = "cook-mpc"
    mode.on_tick(2.0, 220.0, mode.grill.get_output_status())
    mode.on_tick(22.0, 220.0, mode.grill.get_output_status())

    event_kinds = [record.event_kind for record in recorder.records]
    assert event_kinds[:5] == [
        TraceEventKind.SESSION,
        TraceEventKind.ESTIMATOR_SEED,
        TraceEventKind.CONTROL_UPDATE,
        TraceEventKind.ALLOCATION,
        TraceEventKind.APPLIED_OUTPUT,
    ]
    seed_record = recorder.records[1]
    assert seed_record.session_id == _identity(mode).session_id
    assert seed_record.cook_id == "cook-mpc"
    assert seed_record.ts_ms == 2_000
    assert seed_record.payload.segment_id == "hold-test-segment"
    assert seed_record.payload.status == "exact"
    assert seed_record.payload.role_generation == 0
    assert seed_record.payload.candidate_generation == 0
    timestamps = [record.ts_ms for record in recorder.records]
    assert timestamps == sorted(timestamps)
    update_record = next(record for record in recorder.records if record.event_kind is TraceEventKind.CONTROL_UPDATE)
    assert update_record.ts_ms == 2_000
    assert update_record.payload.wall_ms == 1_100
    assert TraceEventKind.SESSION in event_kinds
    assert TraceEventKind.CONTROL_UPDATE in event_kinds
    assert TraceEventKind.ALLOCATION in event_kinds
    frame_index = next(
        index
        for index, record in enumerate(recorder.records)
        if record.event_kind is TraceEventKind.ACTUATION_FRAME and record.ts_ms == 22_000
    )
    applied_index = next(
        index
        for index, record in enumerate(recorder.records)
        if record.event_kind is TraceEventKind.APPLIED_OUTPUT and record.payload.interval_end_ms == 22_000
    )
    assert frame_index < applied_index
    frames = [record.payload for record in recorder.records if record.event_kind is TraceEventKind.ACTUATION_FRAME]
    assert frames and all(frame.result_revision > 0 for frame in frames)
    update = next(record.payload for record in recorder.records if record.event_kind is TraceEventKind.CONTROL_UPDATE)
    allocation = next(record.payload for record in recorder.records if record.event_kind is TraceEventKind.ALLOCATION)
    assert update.actuation_mode is ActuationMode.FRAMED_PULSE
    assert update.result_revision == allocation.result_revision == 1
    assert update.control_period_seconds == 1.0
    assert result.allocation is not None
    assert allocation.requested_auger_duty == result.allocation.auger_duty
    assert (
        allocation.u_max,
        allocation.fan_min_pct,
        allocation.fan_max_pct,
        allocation.fan_enabled,
    ) == (
        result.allocation.u_max,
        result.allocation.fan_min_pct,
        result.allocation.fan_max_pct,
        result.allocation.fan_enabled,
    )
    assert (replay := validate_records(recorder.records)).valid, [(issue.code, issue.detail) for issue in replay.issues]


@pytest.mark.parametrize(
    ("controller", "result_factory"),
    [("pid", _pid_result), ("pid_sp", _pid_sp_result)],
)
def test_pid_family_hold_records_completed_framed_pulse(hold_cycle, monkeypatch, controller, result_factory):
    recorder = _install_recorder(monkeypatch)
    result = result_factory()
    runner = FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE).script([result, result])
    mode = hold_cycle(runner, controller=controller)
    mode.setup()
    mode.control["cook_id"] = f"cook-{controller}"

    mode.on_tick(2.0, 220.0, mode.grill.get_output_status())
    mode.on_tick(22.0, 220.0, mode.grill.get_output_status())

    frames = [record for record in recorder.records if record.event_kind is TraceEventKind.ACTUATION_FRAME]
    assert frames and all(record.controller.value == controller for record in frames)
    assert (replay := validate_records(recorder.records)).valid, [(issue.code, issue.detail) for issue in replay.issues]


def test_fahrenheit_hold_keeps_model_observation_ambient_celsius_while_session_displays_fahrenheit(
    hold_cycle, monkeypatch
):
    """A physical model parameter must not be converted as a UI temperature."""

    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE)
    mode = hold_cycle(runner, controller="mpc")
    mode.settings["globals"]["units"] = "F"
    mode.settings["controller"]["config"]["mpc"]["T_amb"] = 20.0
    mode.setup()
    mode.control["cook_id"] = "fahrenheit-ambient"
    _open_trace_session(mode, 0.0)
    controller = mode.state.controller
    controller.pulse_result_revision = controller.pulse_frame_result_revision = 1
    controller.pulse_frame_combustion_load = 0.3
    controller.pulse_frame_requested_auger_duty = 0.3
    controller.pulse_frame_maximum_duty = 0.5

    from controller.runtime.logic.pulse import PulseFrameResult

    frame = PulseFrameResult(0.0, 20.0, 20.0, True, False, 0.3, 0.0, 0.0, 6, 6.0, 2, False, False, None)
    _observe_runtime(mode, frame, ptemp=212.0, inhibit=InhibitReason.NONE)
    runner.append_observation_outcome(
        ObservationOutcomeEnvelope(1, 0, runner.observations[0], _promotion_outcome(frame_end_ms=20_000))
    )
    _learning(mode).reconcile_outcomes(22.0)

    replayed = next(
        record.payload for record in recorder.records if isinstance(record.payload, ModelObservationPayload)
    )
    session = next(record.payload for record in recorder.records if record.event_kind is TraceEventKind.SESSION)
    assert (runner.observations[0].ambient_c, replayed.ambient_c) == (20.0, 20.0)
    assert (session.temperature_unit, session.ambient_temperature) == ("F", 68.0)


def test_first_framed_results_complete_the_initial_seed_once(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE).script(
        [ControllerUpdateResult(cycle_ratio=0.1, fan=None, input_temperature=220.0), _mpc_result(1), _mpc_result(2)]
    )
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.control["cook_id"] = "cook-mpc-seed"
    output = {"auger": False, "fan": False, "igniter": False, "power": True, "pwm": 100}

    mode.on_tick(2.0, 220.0, output)
    mode.on_tick(4.0, 220.0, mode.grill.get_output_status())
    mode.on_tick(6.0, 220.0, mode.grill.get_output_status())
    mode.on_tick(22.0, 220.0, mode.grill.get_output_status())

    seeds = [
        record.payload
        for record in recorder.records
        if record.event_kind is TraceEventKind.APPLIED_OUTPUT and record.payload.result_revision == 0
    ]
    seed_evidence = [
        (seed.interval_start_ms, seed.interval_end_ms, seed.sample_complete, seed.output_source) for seed in seeds
    ]
    assert seed_evidence == [(0, 22_000, True, OutputSource.SEED)]


def test_lid_reset_completes_deferred_initial_seed_before_first_frame_boundary(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE).script(
        [ControllerUpdateResult(cycle_ratio=0.1, fan=None, input_temperature=220.0), _mpc_result(1)]
    )
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.control["cook_id"] = "cook-mpc-seed-lid-reset"
    mode.settings["cycle_data"]["LidOpenDetectEnabled"] = True

    mode.on_tick(2.0, 220.0, mode.grill.get_output_status())
    mode.on_tick(4.0, 220.0, mode.grill.get_output_status())
    mode.state.target_temp_achieved = True
    mode.on_tick(6.0, 1.0, mode.grill.get_output_status())

    seeds = [
        record.payload
        for record in recorder.records
        if record.event_kind is TraceEventKind.APPLIED_OUTPUT and record.payload.result_revision == 0
    ]
    assert len(seeds) == 1
    assert seeds[0].sample_complete is True
    assert validate_records(recorder.records).valid


def test_misaligned_feedback_gate_keeps_framed_applied_coverage_contiguous(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE).script(
        [_mpc_result(revision) for revision in range(1, 24)]
    )
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.control["cook_id"] = "cook-mpc-misaligned"

    for now in range(1, 43):
        mode.on_tick(float(now), 220.0, mode.grill.get_output_status())

    assert validate_records(recorder.records).valid


def test_mpc_allocation_trace_preserves_disabled_fan_evidence(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    result = _mpc_result(enable_fan=False)
    runner = FakeControllerRunner(period=1.0, commands_fan=False, actuation_mode=ActuationMode.FRAMED_PULSE).script(
        [result]
    )
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.control["cook_id"] = "cook-mpc-no-fan"
    mode.on_tick(2.0, 220.0, {"auger": False, "fan": False, "igniter": False, "power": True, "pwm": 100})
    allocation = next(record.payload for record in recorder.records if record.event_kind is TraceEventKind.ALLOCATION)

    assert allocation.requested_fan_duty is None
    assert allocation.fan_enabled is False


def test_production_hold_seed_lifecycle_rereads_into_calibration(hold_cycle, tmp_path):
    datastore._reset_for_tests(str(tmp_path / "hold-trace.db"))
    runner = FakeControllerRunner(period=1.0, commands_fan=True, actuation_mode=ActuationMode.FRAMED_PULSE).script(
        [_mpc_result(revision) for revision in range(1, 33)]
    )
    mode = hold_cycle(runner, controller="mpc", dc_fan=True)
    mode.settings["platform"]["dc_fan"] = True
    mode.control["pwm_control"] = True
    mode.ctx.store._settings["platform"]["dc_fan"] = True
    mode.ctx.store._control["pwm_control"] = True
    mode.setup()
    assert _trace(mode).status.recorder_available
    mode.control["cook_id"] = "calibration-seed"
    mode.state.metrics = {"augerontime": 0.0}
    output = {"auger": False, "fan": False, "igniter": False, "power": True, "pwm": 100}
    for now in range(2, 64, 2):
        mode.on_tick(float(now), 225.0, output)
        output = mode.grill.get_output_status()
    mode.ctx.clock.advance(64)
    mode.teardown(220.0)

    session_id = _identity(mode).session_id
    assert session_id is not None
    records = read_control_trace_session(session_id)
    assert [record.event_kind for record in records[:5]] == [
        TraceEventKind.SESSION,
        TraceEventKind.ESTIMATOR_SEED,
        TraceEventKind.CONTROL_UPDATE,
        TraceEventKind.ALLOCATION,
        TraceEventKind.APPLIED_OUTPUT,
    ]
    estimator_seed = records[1]
    assert estimator_seed.session_id == session_id
    assert estimator_seed.cook_id == "calibration-seed"
    assert estimator_seed.payload.segment_id == "hold-test-segment"
    assert estimator_seed.payload.status == "exact"
    assert estimator_seed.payload.role_generation == 0
    assert estimator_seed.payload.candidate_generation == 0
    seed_index = 4
    seed = records[seed_index].payload
    assert seed.result_revision == 0
    assert seed.output_source is OutputSource.SEED
    assert seed.sample_complete
    revision_one_output = next(
        record.payload
        for record in records[seed_index + 1 :]
        if record.event_kind is TraceEventKind.APPLIED_OUTPUT and record.payload.result_revision == 1
    )
    assert revision_one_output.output_source is OutputSource.CONTROLLER
    assert revision_one_output.sample_complete
    terminal_output, terminal_safety, terminal_frame = records[-3:]
    assert [record.event_kind for record in (terminal_output, terminal_safety, terminal_frame)] == [
        TraceEventKind.APPLIED_OUTPUT,
        TraceEventKind.SAFETY_EVENT,
        TraceEventKind.ACTUATION_FRAME,
    ]
    assert (
        terminal_output.payload.result_revision
        == terminal_safety.payload.result_revision
        == terminal_frame.payload.result_revision
        == 31
    )
    assert terminal_output.payload.sample_complete
    assert terminal_output.payload.output_source is OutputSource.CONTROLLER
    assert terminal_safety.payload.event is SafetyEventType.SCHEDULER_RESET
    assert terminal_safety.payload.inhibit_reason is InhibitReason.SAFETY
    assert terminal_frame.payload.inhibit_reason is InhibitReason.SAFETY
    assert terminal_frame.payload.reset_reason == "mode_change"
    assert (
        terminal_output.payload.interval_start_ms,
        terminal_output.payload.interval_end_ms,
    ) == (
        terminal_frame.payload.frame_start_ms,
        terminal_frame.payload.frame_end_ms,
    )
    assert (
        terminal_output.payload.interval_start_ms,
        terminal_output.payload.interval_end_ms,
    ) == (62_000, 64_000)
    assert terminal_frame.payload.frame_end_ms - terminal_frame.payload.frame_start_ms < (
        terminal_frame.payload.frame_seconds * 1_000
    )
    time_s, temperatures_c, loads = load_trace_samples(session_id=session_id)
    assert time_s.tolist() == [0.0, 0.0, 0.0]
    assert temperatures_c.tolist() == pytest.approx([(100.0 - 32.0) * 5.0 / 9.0] * 3)
    assert loads.tolist() == [0.3, 0.4, 0.3]


def test_hold_records_one_same_revision_mpc_stale_observation_without_duplicate_allocation(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    fresh = _mpc_result(1)
    stale = replace(fresh, result_age_seconds=2.0, stale_state=ResultStaleState.STALE)
    recovered = _mpc_result(2, recovered=True)
    runner = FakeControllerRunner(
        period=1.0,
        commands_fan=True,
        actuation_mode=ActuationMode.FRAMED_PULSE,
        controller_type=ControllerType.MPC,
    ).script([fresh, stale, stale, recovered])
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.control["cook_id"] = "cook-stale-observation"
    output = {"auger": False, "fan": False, "igniter": False, "power": True, "pwm": 100}

    for now in (2.0, 4.0, 6.0, 8.0):
        mode.on_tick(now, 220.0, output)

    updates = [record.payload for record in recorder.records if record.event_kind is TraceEventKind.CONTROL_UPDATE]
    allocations = [record.payload for record in recorder.records if record.event_kind is TraceEventKind.ALLOCATION]
    assert [(update.result_revision, update.stale, update.recovered) for update in updates] == [
        (1, False, False),
        (1, True, False),
        (2, False, True),
    ]
    assert (
        replace(
            updates[1],
            result_age_ms=updates[0].result_age_ms,
            stale=False,
            stale_state=ResultStaleState.FRESH,
        )
        == updates[0]
    )
    assert [allocation.result_revision for allocation in allocations] == [1, 2]
    assert validate_records(recorder.records).valid


def test_mpc_trace_marks_the_first_fresh_result_after_runner_staleness(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0, commands_fan=True, actuation_mode=ActuationMode.FRAMED_PULSE).script(
        [
            _mpc_result(1, stale_state=ResultStaleState.STALE),
            _mpc_result(2, recovered=True),
        ]
    )
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.control["cook_id"] = "cook-recovery"
    output = {"auger": False, "fan": False, "igniter": False, "power": True, "pwm": 100}

    mode.on_tick(2.0, 220.0, output)
    mode.on_tick(4.0, 220.0, output)
    updates = [record.payload for record in recorder.records if record.event_kind is TraceEventKind.CONTROL_UPDATE]

    assert updates[0].stale is True
    assert updates[0].stale_state is ResultStaleState.STALE
    assert updates[1].stale is False
    assert updates[1].stale_state is ResultStaleState.FRESH
    assert updates[1].recovered is True


def test_mpc_trace_preserves_a_zero_raw_policy_load(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0, commands_fan=True, actuation_mode=ActuationMode.FRAMED_PULSE).script(
        [_mpc_result(raw_policy_firing_load=0.0)]
    )
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.control["cook_id"] = "cook-raw-zero"
    mode.on_tick(2.0, 220.0, {"auger": False, "fan": False, "igniter": False, "power": True, "pwm": 100})
    (update,) = [record.payload for record in recorder.records if record.event_kind is TraceEventKind.CONTROL_UPDATE]

    assert update.raw_output == 0.0


def test_pid_sp_completed_update_records_exact_typed_fields_and_branch(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    mode = hold_cycle(FakeControllerRunner(period=1.0).script([_pid_sp_result()]), controller="pid_sp")
    mode.setup()
    mode.control["cook_id"] = "cook-pid-sp"

    mode.on_tick(2.0, 220.0, {"auger": False, "fan": False, "igniter": False, "power": True, "pwm": 100})

    (record,) = [record for record in recorder.records if record.event_kind is TraceEventKind.CONTROL_UPDATE]
    payload = record.payload
    assert isinstance(payload, PidSpUpdatePayload)
    assert (
        payload.measured_rate,
        payload.predicted_temperature,
        payload.predicted_error,
        payload.tau_seconds,
        payload.theta_seconds,
        payload.stable_window_seconds,
        payload.center_factor,
        payload.new_target_before,
        payload.new_target_after,
        payload.target_change_temperature,
        payload.target_change_ms,
        payload.branch,
    ) == (-0.4, 221.5, 3.5, 12.0, 4.0, 15.0, 0.75, True, False, 218.0, 500, ControllerBranch.OVERSHOOT)


def test_real_pid_sp_first_hold_update_records_unidentified_model_trace(hold_cycle, monkeypatch):
    settings = base_settings()
    settings["controller"]["selected"] = "pid_sp"
    control = base_control(mode="Hold")
    control["primary_setpoint"] = 225
    runner, status = build_runner(settings, control)
    assert status == "Active"
    assert isinstance(runner, SyncControllerRunner)

    recorder = _install_recorder(monkeypatch)
    mode = hold_cycle(runner, controller="pid_sp")
    mode.setup()
    mode.control["cook_id"] = "cook-real-pid-sp"

    mode.on_tick(22.0, 220.0, mode.grill.get_output_status())

    (payload,) = [record.payload for record in recorder.records if record.event_kind is TraceEventKind.CONTROL_UPDATE]
    (allocation,) = [record.payload for record in recorder.records if record.event_kind is TraceEventKind.ALLOCATION]
    assert isinstance(payload, PidSpUpdatePayload)
    assert isinstance(allocation, AllocationPayload)
    assert payload.tau_seconds == 0.0
    assert allocation.result_revision == payload.result_revision
    assert (
        allocation.normalized_combustion_load,
        allocation.requested_auger_duty,
    ) == pytest.approx((payload.requested_output, payload.requested_output))
    assert allocation.requested_fan_duty is None
    assert allocation.u_max == 1.0
    assert allocation.fan_min_pct == allocation.fan_max_pct == 0.0
    assert allocation.fan_enabled is False
    assert allocation.mpc_has_fan_authority is False
    assert allocation.auger_clamp_reason is AllocationClampReason.NONE
    assert allocation.fan_clamp_reason is AllocationClampReason.NONE
    assert allocation.allocator_revision == 2
    assert validate_records(recorder.records).valid


def test_reconfigure_finishes_the_old_pid_session_before_opening_coherent_mpc_session(hold_cycle, monkeypatch):
    class _ReconfiguringRunner(FakeControllerRunner):
        def reconfigure(self, settings, control, logger=None):
            self._commands_fan = True
            self._actuation_mode = ActuationMode.FRAMED_PULSE
            return super().reconfigure(settings, control, logger=logger)

    class _ModelStore:
        def load(self, controller):
            return {"revision": 8} if controller == "mpc" else None

        def save_outcome(self, controller, snapshot):
            return CheckpointSaveOutcome.SAVED

    recorder = _install_recorder(monkeypatch)
    runner = _ReconfiguringRunner(period=1.0, wants_async=True).script([_pid_result(), _mpc_result(2)])
    runner.snapshot = {"revision": 1}
    mode = hold_cycle(runner, controller="pid", model_store=_ModelStore(), dc_fan=True)
    mode.settings["platform"]["dc_fan"] = True
    mode.control["pwm_control"] = True
    mode.ctx.store._settings["platform"]["dc_fan"] = True
    mode.ctx.store._settings["controller"]["selected"] = "mpc"
    mode.ctx.store._settings["controller"]["config"]["mpc"]["trace_marker"] = "new-mpc-session"
    mode.ctx.store._control["pwm_control"] = True
    mode.setup()
    mode.control["cook_id"] = "cook-reconfigure"
    output = {"auger": False, "fan": False, "igniter": False, "power": True, "pwm": 100}
    mode.on_tick(2.0, 220.0, output)
    old_session_id = _identity(mode).session_id

    mode.control["controller_update"] = True
    mode.on_tick(4.0, 220.0, output)

    sessions = [record for record in recorder.records if record.event_kind is TraceEventKind.SESSION]
    reconfigure = next(
        record
        for record in recorder.records
        if record.event_kind is TraceEventKind.SAFETY_EVENT and record.payload.event.value == "controller_reconfigure"
    )
    new_session = sessions[-1]
    assert len(sessions) == 2
    assert old_session_id is not None
    assert reconfigure.session_id == old_session_id
    assert new_session.session_id != old_session_id
    assert new_session.controller is ControllerType.MPC
    assert new_session.payload.fan_authority is True
    assert {setting.key: setting.value for setting in new_session.payload.controller_config}["trace_marker"] == (
        "new-mpc-session"
    )
    old_incomplete = [
        record
        for record in recorder.records
        if record.session_id == old_session_id
        and record.event_kind is TraceEventKind.APPLIED_OUTPUT
        and not record.payload.sample_complete
    ]
    assert len(old_incomplete) == 1
    assert old_incomplete[0].payload.result_revision == 1
    new_session_events = [record for record in recorder.records if record.session_id == new_session.session_id]
    assert [record.event_kind for record in new_session_events[:2]] == [
        TraceEventKind.SESSION,
        TraceEventKind.MODEL_EVENT,
    ]
    assert new_session_events[0].payload.model_revision == 8
    assert new_session_events[0].payload.model_provenance == "restore_submitted"
    assert new_session_events[1].payload.event.value == "restore"
    seed_records = [
        record
        for record in new_session_events
        if record.event_kind is TraceEventKind.APPLIED_OUTPUT and record.payload.result_revision == 0
    ]
    assert len(seed_records) == 1
    seed_record = seed_records[0]
    assert seed_record.payload.output_source is OutputSource.SEED
    assert seed_record.payload.sample_complete is True
    result_two = next(
        record
        for record in new_session_events
        if record.event_kind is TraceEventKind.CONTROL_UPDATE and record.payload.result_revision == 2
    )
    assert new_session_events.index(result_two) < new_session_events.index(seed_record)
    old_session_events = [record for record in recorder.records if record.session_id == old_session_id]
    assert validate_records(old_session_events).valid
    assert validate_records(new_session_events).valid
    assert (
        next(
            record
            for record in recorder.records
            if record.session_id == new_session.session_id and record.event_kind is TraceEventKind.CONTROL_UPDATE
        ).controller
        is ControllerType.MPC
    )


def test_mpc_zero_raw_load_and_zero_requested_auger_duty_remain_zero(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    result = _mpc_result(raw_policy_firing_load=0.0, requested_auger_duty=0.0)
    mode = hold_cycle(
        FakeControllerRunner(period=1.0, commands_fan=True, actuation_mode=ActuationMode.FRAMED_PULSE).script([result]),
        controller="mpc",
    )
    mode.setup()
    mode.control["cook_id"] = "cook-mpc-zero"

    mode.on_tick(2.0, 220.0, {"auger": False, "fan": False, "igniter": False, "power": True, "pwm": 100})

    update = next(record.payload for record in recorder.records if record.event_kind is TraceEventKind.CONTROL_UPDATE)
    allocation = next(record.payload for record in recorder.records if record.event_kind is TraceEventKind.ALLOCATION)
    applied = next(record.payload for record in recorder.records if record.event_kind is TraceEventKind.APPLIED_OUTPUT)
    assert (update.raw_output, allocation.requested_auger_duty, applied.realized_auger_duty) == (0.0, 0.0, 0.0)


def test_mpc_applied_load_is_measured_and_attributed_to_the_producing_frame(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    result = _mpc_result(1)
    runner = FakeControllerRunner(period=1.0, commands_fan=True, actuation_mode=ActuationMode.FRAMED_PULSE).script(
        [result, result]
    )
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.control["cook_id"] = "cook-mpc-feedback"

    mode.on_tick(2.0, 220.0, mode.grill.get_output_status())
    mode.on_tick(22.0, 220.0, mode.grill.get_output_status())
    mode.on_tick(24.0, 220.0, mode.grill.get_output_status())
    mode.on_tick(26.0, 220.0, mode.grill.get_output_status())
    assert any(applied.timestamp == 26.0 for applied in runner.applied)
    mode.on_tick(28.0, 220.0, mode.grill.get_output_status())

    applied = next(
        record.payload
        for record in recorder.records
        if record.event_kind is TraceEventKind.APPLIED_OUTPUT and record.payload.interval_start_ms == 26_000
    )
    assert applied.result_revision == 1
    assert applied.realized_combustion_load == 1.0


def test_mpc_lid_interval_records_measured_feedback_under_the_producing_frame_revision(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0, commands_fan=True, actuation_mode=ActuationMode.FRAMED_PULSE).script(
        [_mpc_result(1), _mpc_result(1), _mpc_result(2)]
    )
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.control["cook_id"] = "cook-mpc-lid-feedback"
    mode.state.metrics = {"augerontime": 0}
    mode.state.target_temp_achieved = True
    mode.settings["cycle_data"]["LidOpenDetectEnabled"] = True

    mode.on_tick(2.0, 220.0, mode.grill.get_output_status())
    mode.on_tick(22.0, 220.0, mode.grill.get_output_status())
    mode.on_tick(24.0, 1.0, mode.grill.get_output_status())
    mode.on_tick(26.0, 1.0, mode.grill.get_output_status())
    mode.ctx.clock.advance(26.0)
    mode.teardown(1.0)

    lid_feedback = [
        record.payload
        for record in recorder.records
        if record.event_kind is TraceEventKind.APPLIED_OUTPUT and record.payload.output_source is OutputSource.LID_OPEN
    ]
    assert lid_feedback and all(payload.result_revision == 1 for payload in lid_feedback)
    assert all(
        payload.realized_combustion_load is None or 0.0 <= payload.realized_combustion_load <= 1.0
        for payload in lid_feedback
    )
    measured = next(
        record.payload
        for record in recorder.records
        if record.event_kind is TraceEventKind.APPLIED_OUTPUT
        and (record.payload.interval_start_ms, record.payload.interval_end_ms) == (22_000, 24_000)
    )
    assert (measured.realized_auger_duty, measured.realized_combustion_load) == (1.0, 1.0)
    assert validate_records(recorder.records).valid


def test_mpc_manual_interval_records_measured_feedback_under_the_producing_frame_revision(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0, commands_fan=True, actuation_mode=ActuationMode.FRAMED_PULSE).script(
        [_mpc_result(1), _mpc_result(1), _mpc_result(2)]
    )
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.control["cook_id"] = "cook-mpc-manual-feedback"
    mode.state.metrics = {"augerontime": 0}

    mode.on_tick(2.0, 220.0, mode.grill.get_output_status())
    mode.on_tick(22.0, 220.0, mode.grill.get_output_status())
    mode.state.manual_override["auger"] = 25.0
    mode._last_now = 23.0
    mode._on_manual_output("auger", True)
    mode.on_tick(24.0, 220.0, mode.grill.get_output_status())
    mode._on_manual_release("auger", 26.0)
    mode.on_tick(26.0, 220.0, mode.grill.get_output_status())
    mode.on_tick(28.0, 220.0, mode.grill.get_output_status())

    manual_feedback = [
        record.payload
        for record in recorder.records
        if record.event_kind is TraceEventKind.APPLIED_OUTPUT
        and record.payload.output_source is OutputSource.MANUAL_OVERRIDE
    ]
    manual_partial = next(payload for payload in manual_feedback if not payload.sample_complete)
    assert (manual_partial.result_revision, manual_partial.realized_combustion_load) == (1, None)
    assert manual_feedback and all(payload.result_revision == 1 for payload in manual_feedback)
    assert all(
        payload.realized_combustion_load is None or 0.0 <= payload.realized_combustion_load <= 1.0
        for payload in manual_feedback
    )
    assert all(payload.realized_combustion_load is None for payload in manual_feedback if not payload.sample_complete)
    assert validate_records(recorder.records).valid


def test_framed_reset_preserves_the_interrupted_frame_metadata(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    first = _mpc_result(1)
    first_allocation = first.allocation
    assert first_allocation is not None
    first = replace(first, allocation=replace(first_allocation, normalized_combustion_load=0.2))
    second = _mpc_result(2, enable_fan=False, stale_state=ResultStaleState.STALE)
    assert second.allocation is not None
    second = replace(second, allocation=replace(second.allocation, normalized_combustion_load=0.8))
    runner = FakeControllerRunner(
        period=1.0,
        commands_fan=True,
        actuation_mode=ActuationMode.FRAMED_PULSE,
    ).script([first, second])
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.control["cook_id"] = "cook-framed-latch"

    mode.on_tick(2.0, 220.0, mode.grill.get_output_status())
    mode.on_tick(4.0, 220.0, mode.grill.get_output_status())
    mode._on_safety_event("stop", 5.0)

    frame = next(
        record.payload
        for record in reversed(recorder.records)
        if record.event_kind is TraceEventKind.ACTUATION_FRAME and record.payload.reset_reason == "safety"
    )
    assert frame.result_revision == 1
    assert frame.requested_combustion_load == 0.2
    assert frame.requested_fan_duty == first_allocation.fan_duty
    assert frame.stale_command is False
    terminal = [
        applied
        for applied in runner.applied
        if applied.feedback_disposition is FrameFeedbackDisposition.DISCARDED
        and applied.producing_result_revision == frame.result_revision
    ]
    assert len(terminal) == 1
    assert terminal[0].producing_calibration_revision == 0


def test_first_safety_callback_opens_and_binds_the_trace_session(
    hold_cycle,
    monkeypatch,
) -> None:
    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(
        period=1.0,
        actuation_mode=ActuationMode.FRAMED_PULSE,
    )
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.control["cook_id"] = "first-safety-callback"
    trace = _trace(mode)
    assert trace.identity is None

    mode._on_safety_event("temperature_guard", 1.0)

    assert trace.identity is not None
    safety = [record.payload for record in recorder.records if record.event_kind is TraceEventKind.SAFETY_EVENT]
    assert [payload.event for payload in safety] == [
        SafetyEventType.TEMPERATURE_GUARD,
        SafetyEventType.SCHEDULER_RESET,
    ]


def test_initial_async_restore_session_uses_queued_snapshot_not_old_published_snapshot(hold_cycle, monkeypatch):
    class _ModelStore:
        def load(self, controller):
            return {"revision": 8} if controller == "mpc" else None

        def save_outcome(self, controller, snapshot):
            return CheckpointSaveOutcome.SAVED

    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(
        period=1.0, commands_fan=True, wants_async=True, actuation_mode=ActuationMode.FRAMED_PULSE
    )
    runner.snapshot = {"revision": 1}
    mode = hold_cycle(runner, controller="mpc", model_store=_ModelStore())
    mode.setup()
    mode.control["cook_id"] = "cook-async-restore"

    _open_trace_session(mode, 1.0)

    (session,) = [record for record in recorder.records if record.event_kind is TraceEventKind.SESSION]
    assert (session.payload.model_revision, session.payload.model_provenance) == (8, "restore_submitted")
    assert runner.snapshot == {"revision": 1}
    assert [
        record.payload.event.value for record in recorder.records if record.event_kind is TraceEventKind.MODEL_EVENT
    ] == ["restore"]


def _run_first_loop_safety_trace(hold_cycle, monkeypatch, *, control_mode=None, guard_temperature=None):
    import controller.runtime.modes.base as base_module

    class _Monitor:
        def start_monitor(self):
            pass

        def heartbeat(self):
            pass

        def stop_monitor(self):
            pass

    recorder = _install_recorder(monkeypatch)
    monkeypatch.setattr(base_module, "Process_Monitor", lambda *args, **kwargs: _Monitor())
    mode = hold_cycle(FakeControllerRunner(period=1.0), controller="pid")
    if control_mode is not None:
        mode.ctx.store._control["mode"] = control_mode
        mode.ctx.store._control["updated"] = True
    if guard_temperature is not None:
        mode.ctx.devices.probe_complex.script([225.0, guard_temperature])

    mode.run()

    return [
        record
        for record in recorder.records
        if record.event_kind in (TraceEventKind.SESSION, TraceEventKind.SAFETY_EVENT)
    ]


@pytest.mark.parametrize(
    ("stored_snapshot", "restore_accepted"),
    [
        (None, True),
        ({"revision": 8}, False),
        ({"revision": "invalid"}, True),
    ],
    ids=["no-stored-model", "rejected-restore", "invalid-stored-model"],
)
def test_async_reconfigure_does_not_leak_the_old_published_model_into_new_session(
    hold_cycle, monkeypatch, stored_snapshot, restore_accepted
):
    class _Runner(FakeControllerRunner):
        def reconfigure(self, settings, control, logger=None):
            self._actuation_mode = ActuationMode.FRAMED_PULSE
            self._controller_type = ControllerType.MPC
            return super().reconfigure(settings, control, logger=logger)

        def restore_model(self, snapshot, *, restore_token=None):
            self.restore_token = restore_token
            self.restored.append(snapshot)
            return restore_accepted

    class _ModelStore:
        def load(self, controller):
            return stored_snapshot if controller == "mpc" else None

        def save_outcome(self, controller, snapshot):
            return CheckpointSaveOutcome.SAVED

    recorder = _install_recorder(monkeypatch)
    runner = _Runner(period=1.0, wants_async=True).script([_pid_result(), _mpc_result(2)])
    runner.snapshot = {"revision": 1}
    mode = hold_cycle(runner, controller="pid", model_store=_ModelStore())
    mode.ctx.store._settings["controller"]["selected"] = "mpc"
    mode.setup()
    mode.control["cook_id"] = f"cook-no-leak-{restore_accepted}"
    output = {"auger": False, "fan": False, "igniter": False, "power": True, "pwm": 100}
    mode.on_tick(2.0, 220.0, output)

    mode.control["controller_update"] = True
    mode.on_tick(4.0, 220.0, output)

    (new_session,) = [
        record
        for record in recorder.records
        if record.event_kind is TraceEventKind.SESSION and record.controller is ControllerType.MPC
    ]
    assert (new_session.payload.model_revision, new_session.payload.model_provenance) == (None, None)


def test_sync_runner_with_async_preferring_core_records_completed_restore(hold_cycle, monkeypatch):
    class _Core:
        def __init__(self):
            self.snapshot = {"revision": 1}

        def set_target(self, setpoint):
            pass

        def get_control_period(self):
            return 1.0

        def commands_fan(self):
            return False

        def wants_async(self):
            return True

        def actuation_mode(self):
            return ActuationMode.FRAMED_PULSE

        def set_output(self, applied):
            pass

        def get_model_snapshot(self):
            return self.snapshot

        def restore_model(self, snapshot):
            self.snapshot = snapshot
            return True

        def get_status(self):
            return None

    class _ModelStore:
        def load(self, controller):
            return {"revision": 8}

        def save_outcome(self, controller, snapshot):
            return CheckpointSaveOutcome.SAVED

    recorder = _install_recorder(monkeypatch)
    mode = hold_cycle(SyncControllerRunner(_Core()), controller="mpc", model_store=_ModelStore())
    mode.setup()
    mode.control["cook_id"] = "cook-sync-restore"

    _open_trace_session(mode, 1.0)

    (session,) = [record for record in recorder.records if record.event_kind is TraceEventKind.SESSION]
    assert (session.payload.model_revision, session.payload.model_provenance) == (8, "restored")


def test_initial_session_uses_the_current_published_model_without_restore(hold_cycle, monkeypatch):
    class _NoModelStore:
        def load(self, controller):
            return None

        def save_outcome(self, controller, snapshot):
            return CheckpointSaveOutcome.SAVED

    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(
        period=1.0, commands_fan=True, wants_async=True, actuation_mode=ActuationMode.FRAMED_PULSE
    )
    runner.snapshot = {"revision": 5}
    mode = hold_cycle(runner, controller="mpc", model_store=_NoModelStore())
    mode.setup()
    mode.control["cook_id"] = "cook-published-model"

    _open_trace_session(mode, 1.0)

    (session,) = [record for record in recorder.records if record.event_kind is TraceEventKind.SESSION]
    assert (session.payload.model_revision, session.payload.model_provenance) == (None, None)


def test_base_manual_auger_on_reasserts_manual_output_after_framed_reset(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE).script([_mpc_result(1)])
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.control["cook_id"] = "cook-base-manual"
    mode.settings["safety"]["manual_override_time"] = 30
    mode.settings["safety"]["allow_manual_changes"] = True
    _open_trace_session(mode, 1.0)
    mode.control["manual"]["change"] = "auger"
    mode.control["manual"]["output"] = True
    call_start = len(mode.grill.calls)

    mode._apply_manual_overrides(
        mode.control,
        now=2.0,
        current_output_status=mode.grill.get_output_status(),
    )
    manual_calls = mode.grill.calls[call_start:]
    call_count = len(mode.grill.calls)
    mode.on_tick(3.0, 220.0, mode.grill.get_output_status())

    assert mode.grill.get_output_status()["auger"] is True
    assert mode.state.manual_override["auger"] > 3.0
    assert [name for name, _args in manual_calls][-2:] == ["auger_off", "auger_on"]
    assert mode.grill.calls[call_count:] == []
    seeds = [
        record.payload
        for record in recorder.records
        if record.event_kind is TraceEventKind.APPLIED_OUTPUT and record.payload.output_source is OutputSource.SEED
    ]
    assert seeds == []
    manual = [
        record.payload
        for record in recorder.records
        if record.event_kind is TraceEventKind.APPLIED_OUTPUT
        and record.payload.output_source is OutputSource.MANUAL_OVERRIDE
    ]
    assert len(manual) == 1 and manual[0].sample_complete is True
    assert manual[0].result_revision == 1
    assert (manual[0].interval_start_ms, manual[0].interval_end_ms) == (
        2_000,
        3_000,
    )
    assert _trace(mode).applied_state.output_source is OutputSource.MANUAL_OVERRIDE
    assert validate_records(recorder.records).valid


def test_automatic_lid_preempts_same_tick_framed_on_transition_and_keeps_replay_valid(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE).script(
        [_mpc_result(1), _mpc_result(1)]
    )
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.control["cook_id"] = "cook-same-tick-lid"
    mode.state.target_temp_achieved = True
    mode.settings["cycle_data"]["LidOpenDetectEnabled"] = True
    mode.on_tick(2.0, 220.0, mode.grill.get_output_status())
    call_start = len(mode.grill.calls)

    mode.on_tick(20.0, 1.0, mode.grill.get_output_status())

    trigger_calls = [name for name, _args in mode.grill.calls[call_start:]]
    assert "auger_on" not in trigger_calls
    assert mode.grill.get_output_status()["auger"] is False
    assert validate_records(recorder.records).valid


def _model_observation_outcome(*, frame_end_ms, role_generation=0, eligible=False):
    return {
        "frame_end_ms": frame_end_ms,
        "role_generation": role_generation,
        "eligible": eligible,
        "rejection_reasons": () if eligible else ("insufficient_excitation",),
        "input_variance": 0.2,
        "input_levels": 2,
        "effective_updates": 4,
        "model_digest": "a" * 64,
    }


def _promotion_outcome(*, frame_end_ms):
    evaluated_at_s = frame_end_ms / 1_000
    completed_origins = tuple(
        {
            "origin_time_s": evaluated_at_s - 12.0 + index,
            "completion_time_s": evaluated_at_s - 11.5 + index,
            "horizon_steps": 3 if index < 6 else 15,
            "generation": 0,
            "observed_temperature_c": 225.0,
            "incumbent_error_c": 2.0,
            "challenger_error_c": 1.0,
            "braking": index % 2 == 0,
            "observation_sequence": index + 1,
            "incumbent_digest": "a" * 64,
            "challenger_digest": "b" * 64,
            "incumbent_prediction_c": 223.0,
            "temperature_band": "near-target",
            "ambient_source": AmbientSource.CONFIGURED.value,
            "challenger_prediction_c": 224.0,
        }
        for index in range(12)
    )
    outcome = {
        **_model_observation_outcome(frame_end_ms=frame_end_ms),
        "evaluation": {
            "decision_id": "generation-0-evaluation-1",
            "evaluated_at_s": evaluated_at_s,
            "role_generation": 0,
            "promoted": True,
            "committed": True,
            "consecutive_wins": 2,
            "rejection_reasons": (),
            "incumbent_prediction_score": 2.0,
            "challenger_prediction_score": 1.0,
            "incumbent_braking_score": 1.0,
            "challenger_braking_score": 0.5,
            "sample_count": len(completed_origins),
            "prospective_digest": "b" * 64,
            "window_start_s": evaluated_at_s - 12.0,
            "window_end_s": evaluated_at_s - 0.5,
            "incumbent_digest": "a" * 64,
            "challenger_digest": "b" * 64,
            "completed_origins": completed_origins,
            "horizon_scores": (
                {
                    "horizon_steps": 3,
                    "incumbent_rmse_c": 2.0,
                    "challenger_rmse_c": 1.0,
                    "sample_count": 6,
                },
                {
                    "horizon_steps": 15,
                    "incumbent_rmse_c": 2.0,
                    "challenger_rmse_c": 1.0,
                    "sample_count": 6,
                },
            ),
            "evaluation_duration_ms": 0.0,
        },
        "lifecycle": {
            "event": "adopt",
            "model_revision": 8,
            "provenance": "grey-fit",
            "detail": "promotion",
            "model_kind": "grey-box",
            "model_schema": "pifire-grey-learning/v4",
            "role_generation": 1,
            "snapshot_digest": "c" * 64,
            "parameters": (),
        },
    }
    evaluation = outcome["evaluation"]
    outcome["evaluation_payload"] = ModelEvaluationPayload(
        decision_id=evaluation["decision_id"],
        evaluated_at_ms=int(evaluation["evaluated_at_s"] * 1_000),
        role_generation=evaluation["role_generation"],
        promoted=evaluation["promoted"],
        committed=evaluation["committed"],
        consecutive_wins=evaluation["consecutive_wins"],
        rejection_reasons=evaluation["rejection_reasons"],
        incumbent_prediction_score=evaluation["incumbent_prediction_score"],
        challenger_prediction_score=evaluation["challenger_prediction_score"],
        incumbent_braking_score=evaluation["incumbent_braking_score"],
        challenger_braking_score=evaluation["challenger_braking_score"],
        sample_count=evaluation["sample_count"],
        prospective_digest=evaluation["prospective_digest"],
        window_start_ms=int(evaluation["window_start_s"] * 1_000),
        window_end_ms=int(evaluation["window_end_s"] * 1_000),
        incumbent_digest=evaluation["incumbent_digest"],
        challenger_digest=evaluation["challenger_digest"],
        completed_origins=tuple(
            {
                **{key: value for key, value in origin.items() if key not in {"origin_time_s", "completion_time_s"}},
                "origin_time_ms": int(origin["origin_time_s"] * 1_000),
                "completion_time_ms": int(origin["completion_time_s"] * 1_000),
            }
            for origin in evaluation["completed_origins"]
        ),
        horizon_scores=(
            *evaluation["horizon_scores"],
            *(
                {"horizon_steps": horizon, "incumbent_rmse_c": None, "challenger_rmse_c": None, "sample_count": 0}
                for horizon in (45, 90, 180)
            ),
        ),
        evaluation_duration_ms=evaluation["evaluation_duration_ms"],
    )
    return outcome


def _learning_observation(frame_start_s):
    return FrameObservation(
        frame_start_s,
        frame_start_s + 20.0,
        212.0,
        225.0,
        20.0,
        0.3,
        0.3,
        0.3,
        6.0,
        None,
        None,
        1,
        "controller",
        False,
        False,
        False,
        False,
        False,
        False,
        True,
        0,
    )


def _two_pending_learning_outcomes(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE)
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.control["cook_id"] = "ordered-learning-trace"
    _open_trace_session(mode, 0.0)
    first, second = _learning_observation(0.0), _learning_observation(20.0)
    _learning(mode).submit_completed_observation((0, 20), first)
    _learning(mode).submit_completed_observation((20, 40), second)
    return recorder, runner, mode, first, second


def test_historical_evidence_rotation_preserves_live_applied_interval(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE)
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.control["cook_id"] = "historical-evidence-rotation"
    old_identity = _open_trace_session(mode, 0.0)
    assert old_identity is not None
    trajectory = mode.ctx.learning_trajectory
    assert trajectory is not None
    trajectory_session_id = trajectory.trace_session_id
    trace = _trace(mode)
    trace.prepare_applied_output(
        AppliedOutput(0.4, OutputSource.CONTROLLER, 2.0, requested=0.5),
        TraceOutputContext(
            timestamp_ms=2_000,
            pulse_frame_result_revision=3,
            fan_duty=None,
            producing_revision=3,
            measured_combustion_load=0.3,
        ),
    )
    runner.reconfigure({}, {})
    _learning(mode).submit_completed_observation(
        (0, 20),
        _learning_observation(0.0),
    )
    monkeypatch.setattr(
        runner,
        "controller_type",
        lambda: ControllerType.MPC,
    )

    mode._rotate_evidence_sessions_for_reserved_runner_generations(3.0)

    assert _identity(mode).session_id != old_identity.session_id
    assert _identity(mode).controller is ControllerType.MPC
    assert trajectory.trace_session_id == trajectory_session_id
    assert trace.record_applied_interval(
        TraceAppliedIntervalContext(
            timestamp_ms=4_000,
            sample_complete=True,
            realized_combustion_load=0.3,
            controls_fan=False,
        )
    )
    intervals = [
        record.payload
        for record in recorder.records
        if record.event_kind is TraceEventKind.APPLIED_OUTPUT and isinstance(record.payload, AppliedOutputPayload)
    ]
    assert (intervals[-1].interval_start_ms, intervals[-1].interval_end_ms) == (2_000, 4_000)
    assert intervals[-1].result_revision == 3


def test_unknown_selected_controller_keeps_control_live_without_trace_identity(
    hold_cycle,
) -> None:
    runner = FakeControllerRunner(
        period=1.0,
        actuation_mode=ActuationMode.FRAMED_PULSE,
    ).script([_mpc_result(1), _mpc_result(2)])
    mode = hold_cycle(runner, controller="future-controller")
    mode.setup()
    mode.control["cook_id"] = "unknown-controller-trace"
    mode.on_tick(2.0, 200.0, mode.grill.get_output_status())
    runner.reconfigure({}, {})
    mode.teardown(200.0)

    assert any(applied.source is OutputSource.CONTROLLER for applied in runner.applied)
    assert _trace(mode).identity is None
    assert mode.grill.get_output_status()["auger"] is False


def test_threaded_stop_timeout_rotates_reserved_generation_gaps_and_fences_late_outcomes(hold_cycle, monkeypatch):
    class _WorkerGate:
        def __init__(self):
            self.waiting = threading.Event()
            self.release = threading.Event()

        def __call__(self, _period_s):
            self.waiting.set()
            self.release.wait()

        def close(self):
            self.release.set()

    class _BlockingObservationCore:
        def __init__(self):
            self.observation_started = threading.Event()
            self.release_observation = threading.Event()
            self.seed_requirement_calls = 0
            self.learning_diagnostics_calls = 0
            self.seed_source_bindings = []
            self.learning_identity_bindings = []
            self.observations = []
            self.observation_failures = []
            self.fit_schedules = []
            self.fit_ticket_schedules = []
            self.fit_polls = []
            self.consumed_fit_tickets = []
            self.fit_failures = []

        def estimator_seed_requirements(self) -> tuple[float, int]:
            self.seed_requirement_calls += 1
            return 60.0, 8

        def bind_estimator_seed_source(
            self,
            source: Callable[[float, int], object] | None,
        ) -> None:
            self.seed_source_bindings.append(source)

        def bind_learning_identity(
            self,
            session_id: str,
            cook_id: str | None,
            role_generation: int,
        ) -> None:
            self.learning_identity_bindings.append(
                (session_id, cook_id, role_generation),
            )

        def get_control_period(self):
            return 1.0

        def commands_fan(self):
            return False

        def wants_async(self):
            return True

        def actuation_mode(self):
            return ActuationMode.FRAMED_PULSE

        def get_status(self):
            return None

        def get_model_snapshot(self):
            return None

        def restore_model(self, _snapshot):
            return True

        def set_output(self, _applied):
            return None

        def set_target(self, _target):
            return None

        def update(self, _temperature):
            return {"cycle_ratio": 0.0, "fan": None}

        def observe_frame(self, observation: FrameObservation) -> object:
            self.observations.append(observation)
            self.observation_started.set()
            self.release_observation.wait()
            return _model_observation_outcome(
                frame_end_ms=int(observation.frame_end_s * 1_000),
            )

        def observation_failure(
            self,
            observation: FrameObservation,
            error: BaseException,
        ) -> object:
            self.observation_failures.append((observation, error))
            return _model_observation_outcome(
                frame_end_ms=int(observation.frame_end_s * 1_000),
            )

        def poll_learning_off_path(
            self,
            *,
            live_origin: CandidateOrigin | None = None,
        ) -> object:
            self.fit_polls.append((threading.get_ident(), live_origin))
            return None

        def schedule_corpus_fit(self, origin: CandidateOrigin) -> bool:
            self.fit_schedules.append(origin)
            return False

        def _schedule_corpus_fit_ticket(
            self,
            origin: CandidateOrigin,
        ) -> str | None:
            self.fit_ticket_schedules.append(origin)
            return None

        def _consume_terminal_corpus_fit_ticket(
            self,
            ticket: str,
            origin: CandidateOrigin,
        ) -> bool:
            self.consumed_fit_tickets.append((ticket, origin))
            return False

        def fail_corpus_fit(
            self,
            ticket: str,
            error: BaseException | str,
        ) -> None:
            self.fit_failures.append((ticket, error))

        def get_learning_diagnostics(self) -> ControllerLearningDiagnostics:
            self.learning_diagnostics_calls += 1
            return ControllerLearningDiagnostics(
                schema_version=1,
                state={},
            )

    class _EvidenceWorker:
        evidence_blocked = False

        def __init__(self):
            self.batches = []
            self.stopped = False

        def submit_evidence_batch(self, records):
            self.batches.append(records)
            return EvidenceSubmission(accepted=True)

        def barrier(self, timeout=2.0):
            del timeout
            self.stopped = True
            return True

    from controller.runtime.logic.pulse import PulseFrameResult

    recorder = _install_recorder(monkeypatch)
    gate = _WorkerGate()
    core = _BlockingObservationCore()
    runner = ThreadedControllerRunner(core, wait_for_period=gate)
    worker = _EvidenceWorker()
    monkeypatch.setattr(
        "controller.runtime.modes.hold.ModelPersistenceWorker",
        lambda *_args, **_kwargs: worker,
    )
    mode = hold_cycle(runner, controller="mpc")
    try:
        assert gate.waiting.wait(1.0)
        mode.setup()
        mode.control["cook_id"] = "runner-stop-timeout"
        _open_trace_session(mode, 0.0)
        controller = mode.state.controller
        controller.pulse_result_revision = controller.pulse_frame_result_revision = 1
        controller.pulse_frame_combustion_load = 0.3
        controller.pulse_frame_requested_auger_duty = 0.3
        controller.pulse_frame_maximum_duty = 0.5
        _observe_runtime(
            mode,
            PulseFrameResult(
                nominal_start_s=0.0,
                nominal_end_s=20.0,
                ended_at_s=20.0,
                complete=True,
                skipped=False,
                latched_request=0.3,
                credit_before_s=0.0,
                credit_after_s=0.0,
                scheduled_on_s=6.0,
                delivered_on_s=6.0,
                observed_transition_count=2,
                actual_start_on=False,
                actual_end_on=False,
                reset_reason=None,
            ),
            ptemp=212.0,
            inhibit=InhibitReason.NONE,
            role_generation=0,
        )
        gate.release.set()
        assert core.observation_started.wait(1.0)
        old_session_id = _identity(mode).session_id
        import controller.runtime.runner as runner_module

        next_core = _BlockingObservationCore()
        monkeypatch.setattr(runner_module, "_build_core", lambda *_args, **_kwargs: (next_core, "Active"))
        assert runner.reconfigure({}, {}) == "Active"
        with runner._lock:
            runner._configuration_revision = 1
        _observe_runtime(
            mode,
            PulseFrameResult(
                nominal_start_s=20.0,
                nominal_end_s=40.0,
                ended_at_s=40.0,
                complete=True,
                skipped=False,
                latched_request=0.3,
                credit_before_s=0.0,
                credit_after_s=0.0,
                scheduled_on_s=6.0,
                delivered_on_s=6.0,
                observed_transition_count=2,
                actual_start_on=False,
                actual_end_on=False,
                reset_reason=None,
            ),
            ptemp=212.0,
            inhibit=InhibitReason.NONE,
            role_generation=1,
        )
        with runner._lock:
            runner._configuration_revision = 0

        mode.teardown(212.0)

        gaps = [
            (record.session_id, record.payload)
            for record in recorder.records
            if record.event_kind is TraceEventKind.RECORDER_GAP
            and isinstance(record.payload, RecorderGapPayload)
            and record.payload.reason == "runner-stop-timeout"
        ]
        assert [(gap.observation_sequence, gap.reason) for _session_id, gap in gaps] == [
            (1, "runner-stop-timeout"),
            (2, "runner-stop-timeout"),
        ]
        assert gaps[0][0] == old_session_id
        assert gaps[1][0] is not None and gaps[1][0] != old_session_id
        assert runner.drain_observation_outcomes().terminal_drops == ()
        assert len(worker.batches) == 2
        compact_gaps = [batch[0] for batch in worker.batches]
        assert all(
            isinstance(record, ModelEvidenceRecord) and isinstance(record.payload, RecorderGapEvidence)
            for record in compact_gaps
        )
        assert [(record.role_generation, record.payload.reason) for record in compact_gaps] == [
            (0, "runner-stop-timeout"),
            (1, "runner-stop-timeout"),
        ]
        core.release_observation.set()
        assert runner._thread.join(1.0) is None
        assert not runner._thread.is_alive()
        assert runner.drain_observation_outcomes().envelopes == ()
        assert runner.drain_observation_outcomes().terminal_drops == ()
        assert len(worker.batches) == 2
    finally:
        core.release_observation.set()
        runner.stop()


def test_framed_learning_trace_waits_for_the_matching_actual_async_outcome(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)

    class _Runner(FakeControllerRunner):
        def __init__(self):
            super().__init__(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE)

    runner = _Runner()
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.control["cook_id"] = "async-learning-trace"
    _open_trace_session(mode, 0.0)
    controller = mode.state.controller
    controller.pulse_result_revision = controller.pulse_frame_result_revision = 1
    controller.pulse_frame_combustion_load = 0.3
    controller.pulse_frame_requested_auger_duty = 0.3
    controller.pulse_frame_maximum_duty = 0.5

    from controller.runtime.logic.pulse import PulseFrameResult

    frame = PulseFrameResult(
        nominal_start_s=0.0,
        nominal_end_s=20.0,
        ended_at_s=20.0,
        complete=True,
        skipped=False,
        latched_request=0.3,
        credit_before_s=0.0,
        credit_after_s=0.0,
        scheduled_on_s=6,
        delivered_on_s=6.0,
        observed_transition_count=2,
        actual_start_on=False,
        actual_end_on=False,
        reset_reason=None,
    )
    _observe_runtime(mode, frame, ptemp=212.0, inhibit=InhibitReason.NONE)

    assert not [record for record in recorder.records if isinstance(record.payload, ModelObservationPayload)]
    runner.append_observation_outcome(
        ObservationOutcomeEnvelope(1, 0, runner.observations[0], _promotion_outcome(frame_end_ms=20_000))
    )
    _learning(mode).reconcile_outcomes(22.0)
    _learning(mode).reconcile_outcomes(23.0)

    payloads = [record.payload for record in recorder.records if isinstance(record.payload, ModelObservationPayload)]
    assert len(payloads) == 1
    payload = payloads[0]
    assert (payload.frame_start_ms, payload.frame_end_ms, payload.role_generation) == (0, 20_000, 0)
    assert payload.eligible is False
    assert payload.rejection_reasons == ("insufficient_excitation",)
    assert payload.calibration_probe_load == 0.0
    assert payload.ambient_source is AmbientSource.CONFIGURED
    assert payload.ambient_uncertainty is AmbientUncertainty.UNMEASURED
    learning_event_kinds = [
        record.event_kind
        for record in recorder.records
        if record.event_kind
        in {TraceEventKind.MODEL_EVALUATION, TraceEventKind.MODEL_EVENT, TraceEventKind.MODEL_OBSERVATION}
    ]
    assert learning_event_kinds == [
        TraceEventKind.MODEL_OBSERVATION,
        TraceEventKind.MODEL_EVALUATION,
        TraceEventKind.MODEL_EVENT,
    ]


def test_hold_status_fragment_publishes_live_learning_state(hold_cycle):
    class _Runner(FakeControllerRunner):
        def controller_state(self):
            return {
                "learning": {
                    "status": "fitting",
                    "fit_status": "running",
                    "role_generation": 7,
                }
            }

    mode = hold_cycle(_Runner(), controller="mpc")
    mode.setup()

    assert mode.status_fragment()["learning"] == {
        "status": "fitting",
        "fit_status": "running",
        "role_generation": 7,
    }


def test_framed_learning_trace_uses_generation_latched_with_pulse_frame(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)

    class _Runner(FakeControllerRunner):
        def __init__(self):
            super().__init__(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE)
            self.status = {"adaptation": {"role_generation": 7}}

        def controller_state(self):
            return self.status

    runner = _Runner()
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.control["cook_id"] = "latched-generation-learning-trace"
    _open_trace_session(mode, 0.0)
    controller = mode.state.controller
    controller.pulse_result_revision = 1
    controller.pulse_requested_duty = 0.3
    controller.pulse_combustion_load = 0.3
    controller.pulse_maximum_duty = 0.5

    _advance_runtime(mode, 0.0, True, ptemp=212.0)
    _advance_runtime(mode, 6.0, False, ptemp=212.0)
    runner.status = {"adaptation": {"role_generation": 8}}
    _advance_runtime(mode, 20.0, False, ptemp=212.0)

    runner.append_observation_outcome(
        ObservationOutcomeEnvelope(
            1, 0, runner.observations[0], _model_observation_outcome(frame_end_ms=20_000, role_generation=7)
        )
    )
    _learning(mode).reconcile_outcomes(22.0)

    payloads = [record.payload for record in recorder.records if isinstance(record.payload, ModelObservationPayload)]
    assert [payload.role_generation for payload in payloads] == [7]


def test_framed_learning_trace_retries_transient_recorder_failure(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE)
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.control["cook_id"] = "retry-learning-trace"
    _open_trace_session(mode, 0.0)
    controller = mode.state.controller
    controller.pulse_result_revision = controller.pulse_frame_result_revision = 1
    controller.pulse_frame_combustion_load = 0.3
    controller.pulse_frame_requested_auger_duty = 0.3
    controller.pulse_frame_maximum_duty = 0.5
    from controller.runtime.logic.pulse import PulseFrameResult

    frame = PulseFrameResult(0.0, 20.0, 20.0, True, False, 0.3, 0.0, 0.0, 6, 6.0, 2, False, False, None)
    _observe_runtime(mode, frame, ptemp=212.0, inhibit=InhibitReason.NONE)
    runner.append_observation_outcome(
        ObservationOutcomeEnvelope(1, 0, runner.observations[0], _promotion_outcome(frame_end_ms=20_000))
    )
    original = _trace(mode).record
    attempts = 0

    def transient(kind, payload, timestamp):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            return False
        return original(kind, payload, timestamp)

    _trace(mode).record = transient
    _learning(mode).reconcile_outcomes(22.0)
    _learning(mode).reconcile_outcomes(23.0)
    assert [
        record.event_kind
        for record in recorder.records
        if record.event_kind
        in {TraceEventKind.MODEL_EVALUATION, TraceEventKind.MODEL_EVENT, TraceEventKind.MODEL_OBSERVATION}
    ] == [TraceEventKind.MODEL_OBSERVATION, TraceEventKind.MODEL_EVALUATION, TraceEventKind.MODEL_EVENT]


def test_learning_outcomes_hold_global_fifo_through_a_lifecycle_retry(hold_cycle, monkeypatch):
    recorder, runner, mode, first, second = _two_pending_learning_outcomes(hold_cycle, monkeypatch)
    first_outcome = _promotion_outcome(frame_end_ms=20_000)
    second_outcome = _promotion_outcome(frame_end_ms=40_000)
    second_outcome["evaluation"] = {**second_outcome["evaluation"], "decision_id": "generation-0-evaluation-2"}
    second_outcome["evaluation_payload"] = replace(
        second_outcome["evaluation_payload"], decision_id="generation-0-evaluation-2"
    )
    second_outcome["lifecycle"] = {**second_outcome["lifecycle"], "detail": "promotion-2"}
    runner.append_observation_outcome(ObservationOutcomeEnvelope(1, 0, first, first_outcome))
    runner.append_observation_outcome(ObservationOutcomeEnvelope(2, 0, second, second_outcome))
    original = _trace(mode).record
    failed = False

    def transient(kind, payload, timestamp):
        nonlocal failed
        if kind is TraceEventKind.MODEL_EVENT and not failed:
            failed = True
            return False
        return original(kind, payload, timestamp)

    _trace(mode).record = transient
    _learning(mode).reconcile_outcomes(22.0)
    assert [
        record.payload.decision_id
        for record in recorder.records
        if record.event_kind is TraceEventKind.MODEL_EVALUATION
    ] == ["generation-0-evaluation-1"]

    _learning(mode).reconcile_outcomes(23.0)

    assert [
        (record.event_kind, getattr(record.payload, "decision_id", getattr(record.payload, "detail", None)))
        for record in recorder.records
        if record.event_kind
        in {TraceEventKind.MODEL_EVALUATION, TraceEventKind.MODEL_EVENT, TraceEventKind.MODEL_OBSERVATION}
    ] == [
        (TraceEventKind.MODEL_OBSERVATION, None),
        (TraceEventKind.MODEL_EVALUATION, "generation-0-evaluation-1"),
        (TraceEventKind.MODEL_EVENT, "promotion"),
        (TraceEventKind.MODEL_OBSERVATION, None),
        (TraceEventKind.MODEL_EVALUATION, "generation-0-evaluation-2"),
        (TraceEventKind.MODEL_EVENT, "promotion-2"),
    ]


def test_learning_outcomes_wait_for_an_earlier_unready_frame(hold_cycle, monkeypatch):
    recorder, runner, mode, first, second = _two_pending_learning_outcomes(hold_cycle, monkeypatch)
    runner.append_observation_outcome(ObservationOutcomeEnvelope(2, 0, second, _promotion_outcome(frame_end_ms=40_000)))

    _learning(mode).reconcile_outcomes(22.0)

    assert not [
        record
        for record in recorder.records
        if record.event_kind
        in {TraceEventKind.MODEL_EVALUATION, TraceEventKind.MODEL_EVENT, TraceEventKind.MODEL_OBSERVATION}
    ]
    runner.append_observation_outcome(ObservationOutcomeEnvelope(1, 0, first, _promotion_outcome(frame_end_ms=20_000)))
    _learning(mode).reconcile_outcomes(23.0)

    assert [
        record.event_kind
        for record in recorder.records
        if record.event_kind
        in {TraceEventKind.MODEL_EVALUATION, TraceEventKind.MODEL_EVENT, TraceEventKind.MODEL_OBSERVATION}
    ] == [
        TraceEventKind.MODEL_OBSERVATION,
        TraceEventKind.MODEL_EVALUATION,
        TraceEventKind.MODEL_EVENT,
        TraceEventKind.MODEL_OBSERVATION,
        TraceEventKind.MODEL_EVALUATION,
        TraceEventKind.MODEL_EVENT,
    ]


def test_mpc_lifecycle_records_one_rollback_event_without_stale_duplicates(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    lifecycle = {
        "event": "reject",
        "model_revision": 9,
        "provenance": "grey-box",
        "detail": "active-solve-failed",
        "model_kind": "grey-box",
        "model_schema": "grey-box/v1",
        "role_generation": 2,
        "snapshot_digest": "d" * 64,
        "parameters": (),
    }
    result = _mpc_result(model_lifecycle=lifecycle)
    mode = hold_cycle(
        FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE),
        controller="mpc",
    )
    mode.setup()
    mode.control["cook_id"] = "rollback-lifecycle"
    _open_trace_session(mode, 0.0)
    original = _trace(mode).record
    failed = False

    def transient(kind, payload, timestamp):
        nonlocal failed
        if kind is TraceEventKind.MODEL_EVENT and not failed:
            failed = True
            return False
        return original(kind, payload, timestamp)

    _trace(mode).record = transient
    _record_trace_update(mode, result, now=2.0, controller_interval=1.0)
    assert _trace(mode).status.pending_model_events == 1
    _open_trace_session(mode, 2.5)
    _record_trace_update(
        mode,
        replace(result, stale_state=ResultStaleState.STALE, result_age_seconds=1.0),
        now=3.0,
        controller_interval=1.0,
    )

    lifecycle_payloads = [
        record.payload for record in recorder.records if record.event_kind is TraceEventKind.MODEL_EVENT
    ]
    assert [(payload.event.value, payload.detail) for payload in lifecycle_payloads] == [
        ("reject", "active-solve-failed")
    ]


def test_runner_configuration_adoption_retires_pending_trace_retry_from_old_session(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE)
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.control["cook_id"] = "runner-adoption"
    _open_trace_session(mode, 0.0)
    observation = _learning_observation(0.0)
    _learning(mode).submit_completed_observation((0, 20), observation)
    assert runner.reconfigure({}, {}) == "Active"

    mode._adopt_runner_configuration(1.0, mode.grill.get_output_status())
    runner.append_observation_outcome(
        ObservationOutcomeEnvelope(
            1,
            0,
            observation,
            _model_observation_outcome(frame_end_ms=20_000),
        )
    )
    _learning(mode).reconcile_outcomes(2.0)

    assert not [record for record in recorder.records if isinstance(record.payload, ModelObservationPayload)]


def test_persistent_trace_retry_retention_is_bounded_to_pending_capacity(
    hold_cycle,
    monkeypatch,
):
    recorder = _install_recorder(monkeypatch)
    mode = hold_cycle(
        FakeControllerRunner(
            period=1.0,
            actuation_mode=ActuationMode.FRAMED_PULSE,
        ),
        controller="mpc",
    )
    mode.setup()
    mode.control["cook_id"] = "bounded-trace-retry"
    _open_trace_session(mode, 0.0)
    learning = _learning(mode)
    for sequence in range(60):
        learning.submit_completed_observation(
            (sequence * 20, (sequence + 1) * 20),
            replace(
                _learning_observation(sequence * 20.0),
                observation_sequence=sequence + 1,
                probe_valid=False,
            ),
        )
    trace = _trace(mode)
    original = trace.record
    trace.record = lambda *_: False

    learning.reconcile_outcomes(1.0)
    trace.record = original
    learning.reconcile_outcomes(2.0)

    assert len([record for record in recorder.records if isinstance(record.payload, ModelObservationPayload)]) == 60


def test_hold_retires_self_evicted_submission_immediately(
    hold_cycle,
    monkeypatch,
):
    class SelfEvictingRunner(FakeControllerRunner):
        def observe_frame(self, observation):
            from controller.runtime.runner import ObservationSubmission

            return ObservationSubmission(1, 0, 1)

    recorder = _install_recorder(monkeypatch)
    runner = SelfEvictingRunner(
        period=1.0,
        actuation_mode=ActuationMode.FRAMED_PULSE,
    )
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.control["cook_id"] = "self-evicted-submission"
    _open_trace_session(mode, 0.0)
    observation = FrameObservation(
        0.0,
        20.0,
        100.0,
        120.0,
        20.0,
        0.2,
        0.2,
        0.2,
        4.0,
        None,
        None,
        1,
        "controller",
        False,
        False,
        False,
        False,
        False,
        False,
        True,
        0,
    )
    _learning(mode).submit_completed_observation((0, 20), observation)

    gaps = [record.payload for record in recorder.records if isinstance(record.payload, RecorderGapPayload)]
    assert [(gap.reason, gap.observation_sequence) for gap in gaps] == [("runner-observation-evicted", 0)]


def test_trace_append_failure_keeps_hold_control_and_learning_live_then_records_recovery_gap(hold_cycle, monkeypatch):
    """A full recorder must never stop the next control or learner submission."""
    import controller.runtime.modes.hold as hold_module

    persisted = []
    append_attempts = 0

    def append(records):
        nonlocal append_attempts
        append_attempts += 1
        if append_attempts == 1:
            raise OSError("trace store unavailable")
        persisted.extend(records)

    recorder = ControlTraceRecorder(
        append=append,
        prune=lambda *_args, **_kwargs: 0,
        monotonic_clock=lambda: 0,
        wall_clock=lambda: 0,
        capacity=3,
    )
    monkeypatch.setattr(hold_module, "ControlTraceRecorder", lambda **_kwargs: recorder)
    monkeypatch.setattr(hold_module.time, "monotonic_ns", lambda: 1_000_000)
    runner = FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE).script(
        [_mpc_result(1), _mpc_result(2)]
    )
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.control["cook_id"] = "trace-append-recovery"
    output = {"auger": False, "fan": False, "igniter": False, "power": True, "pwm": 100}

    mode.on_tick(2.0, 212.0, output)
    recorder.flush_due(5_000)
    assert persisted == []

    mode.on_tick(4.0, 213.0, output)
    runner.observation_outcome = _model_observation_outcome(frame_end_ms=20_000)
    for index in range(3):
        _learning(mode).submit_completed_observation(
            (index * 20, (index + 1) * 20),
            _learning_observation(index * 20.0),
        )
    _learning(mode).reconcile_outcomes(22.0)

    assert runner.submitted_temps == [212.0, 213.0]
    assert [observation.frame_end_s for observation in runner.observations] == [20.0, 40.0, 60.0]

    recorder.flush_due(10_000)

    assert [record.event_kind for record in persisted] == [
        TraceEventKind.RECORDER_GAP,
        TraceEventKind.MODEL_OBSERVATION,
        TraceEventKind.MODEL_OBSERVATION,
        TraceEventKind.MODEL_OBSERVATION,
    ]
    assert isinstance(persisted[0].payload, RecorderGapPayload)
    assert persisted[0].payload.lost_record_count > 0
    assert persisted[0].payload.gap_start_ms <= persisted[0].payload.gap_end_ms
    payloads = [record.payload for record in persisted[1:]]
    assert all(isinstance(payload, ModelObservationPayload) for payload in payloads)
    assert [payload.frame_end_ms for payload in payloads] == [20_000, 40_000, 60_000]


@pytest.mark.parametrize(
    ("generation", "outcome", "reason"),
    (
        (1, _model_observation_outcome(frame_end_ms=20_000), "observation-configuration-mismatch"),
        (0, object(), "observation-outcome-malformed"),
        (0, {}, "observation-outcome-malformed"),
    ),
)
def test_failed_async_outcomes_remain_as_ordered_rejected_observations(
    hold_cycle, monkeypatch, generation, outcome, reason
):
    """Regression: outcome drops must not silently erase a completed frame."""
    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE)
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.control["cook_id"] = "failed-outcome-evidence"
    _open_trace_session(mode, 0.0)
    runner.bind_evidence_context(1, _identity(mode).session_id, _identity(mode).cook_id)
    first, second = _learning_observation(0.0), _learning_observation(20.0)
    _learning(mode).submit_completed_observation((0, 20), first)
    _learning(mode).submit_completed_observation((20, 40), second)
    runner.append_observation_outcome(ObservationOutcomeEnvelope(1, generation, first, outcome))
    runner.append_observation_outcome(
        ObservationOutcomeEnvelope(
            2,
            0,
            second,
            _model_observation_outcome(frame_end_ms=40_000),
        )
    )

    _learning(mode).reconcile_outcomes(41.0)

    payloads = [record.payload for record in recorder.records if isinstance(record.payload, ModelObservationPayload)]
    assert [(payload.frame_end_ms, payload.eligible, payload.rejection_reasons) for payload in payloads] == [
        (20_000, False, (reason,)),
        (40_000, False, ("insufficient_excitation",)),
    ]


def test_missing_completed_frame_temperature_emits_sequence_linked_trace_gap(hold_cycle, monkeypatch):
    """Regression: a completed frame without a temperature must remain auditable."""
    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE)
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.control["cook_id"] = "missing-temperature-evidence"
    _open_trace_session(mode, 0.0)
    controller = mode.state.controller
    controller.pulse_result_revision = controller.pulse_frame_result_revision = 1
    controller.pulse_frame_combustion_load = 0.3
    controller.pulse_frame_requested_auger_duty = 0.3
    controller.pulse_frame_maximum_duty = 0.5
    from controller.runtime.logic.pulse import PulseFrameResult

    frame = PulseFrameResult(0.0, 20.0, 20.0, True, False, 0.3, 0.0, 0.0, 6, 6.0, 2, False, False, None)
    _observe_runtime(mode, frame, ptemp=None, inhibit=InhibitReason.NONE)

    gap = next(record.payload for record in recorder.records if isinstance(record.payload, RecorderGapPayload))
    assert (gap.reason, gap.frame_start_ms, gap.frame_end_ms, gap.result_revision) == (
        "missing-temperature",
        0,
        20_000,
        1,
    )


@pytest.mark.parametrize("reason", ("missing-allocation", "allocation-revision-mismatch"))
def test_allocation_join_failure_persists_an_ineligible_completed_observation(hold_cycle, monkeypatch, reason):
    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE)
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.control["cook_id"] = "allocation-join-evidence"
    _open_trace_session(mode, 0.0)
    observation = replace(_learning_observation(0.0), allocation_join_reason=reason)
    _learning(mode).submit_completed_observation((0, 20), observation)
    runner.append_observation_outcome(
        ObservationOutcomeEnvelope(1, 0, observation, _model_observation_outcome(frame_end_ms=20_000))
    )

    _learning(mode).reconcile_outcomes(21.0)

    payload = next(record.payload for record in recorder.records if isinstance(record.payload, ModelObservationPayload))
    assert (payload.frame_start_ms, payload.frame_end_ms, payload.result_revision) == (0, 20_000, 1)
    assert payload.eligible is False
    assert payload.rejection_reasons == (reason,)


def test_invalid_probe_is_persisted_without_submitting_to_the_learner(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE)
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.control["cook_id"] = "invalid-probe-evidence"
    _open_trace_session(mode, 0.0)
    observation = replace(_learning_observation(0.0), probe_valid=False, probe_source=None)

    _learning(mode).submit_completed_observation((0, 20_000), observation)
    _learning(mode).reconcile_outcomes(21.0)

    assert runner.observations == []
    payload = next(record.payload for record in recorder.records if isinstance(record.payload, ModelObservationPayload))
    assert (payload.probe_valid, payload.eligible, payload.rejection_reasons) == (
        False,
        False,
        ("invalid-probe",),
    )


def test_invalid_probe_waits_for_an_earlier_learner_outcome_before_trace_publication(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE)
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.control["cook_id"] = "ordered-invalid-probe-evidence"
    _open_trace_session(mode, 0.0)
    first = replace(_learning_observation(0.0), observation_sequence=1)
    second = replace(_learning_observation(20.0), observation_sequence=2, probe_valid=False, probe_source=None)

    _learning(mode).submit_completed_observation((0, 20_000), first)
    _learning(mode).submit_completed_observation((20_000, 40_000), second)

    assert runner.observations == [first]
    assert not [record for record in recorder.records if isinstance(record.payload, ModelObservationPayload)]
    runner.append_observation_outcome(
        ObservationOutcomeEnvelope(1, 0, first, _model_observation_outcome(frame_end_ms=20_000))
    )
    _learning(mode).reconcile_outcomes(41.0)

    payloads = [record.payload for record in recorder.records if isinstance(record.payload, ModelObservationPayload)]
    assert [(payload.observation_sequence, payload.eligible, payload.rejection_reasons) for payload in payloads] == [
        (1, False, ("insufficient_excitation",)),
        (2, False, ("invalid-probe",)),
    ]


def test_invalid_probe_queue_overflow_records_the_evicted_observation_gap_and_retains_fifo(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE)
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.control["cook_id"] = "invalid-probe-overflow-evidence"
    _open_trace_session(mode, 0.0)
    first = replace(_learning_observation(0.0), observation_sequence=1)

    _learning(mode).submit_completed_observation((0, 20_000), first)
    for sequence in range(2, 62):
        invalid = replace(
            _learning_observation((sequence - 1) * 20.0),
            observation_sequence=sequence,
            probe_valid=False,
            probe_source=None,
        )
        _learning(mode).submit_completed_observation(
            ((sequence - 1) * 20_000, sequence * 20_000),
            invalid,
        )

    assert runner.observations == [first]
    _learning(mode).reconcile_outcomes(1_221.0)

    gaps = [record.payload for record in recorder.records if isinstance(record.payload, RecorderGapPayload)]
    payloads = [record.payload for record in recorder.records if isinstance(record.payload, ModelObservationPayload)]
    assert [(gap.reason, gap.observation_sequence) for gap in gaps] == [("pending-observation-overflow", 1)]
    assert [(payload.observation_sequence, payload.eligible, payload.rejection_reasons) for payload in payloads] == [
        (sequence, False, ("invalid-probe",)) for sequence in range(2, 62)
    ]


def test_partial_terminal_frame_with_malformed_outcome_becomes_a_gap(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE)
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.control["cook_id"] = "partial-terminal-frame"
    _open_trace_session(mode, 0.0)
    observation = replace(
        _learning_observation(0.0),
        frame_end_s=1.0,
        delivered_on_s=0.0,
        scheduled_on_s=0.0,
        continuous=False,
        observation_sequence=1,
    )
    _learning(mode).submit_completed_observation((0, 1_000), observation)
    runner.append_observation_outcome(ObservationOutcomeEnvelope(1, 0, observation, {}))

    _learning(mode).reconcile_outcomes(1.0)
    gap = next(record.payload for record in recorder.records if isinstance(record.payload, RecorderGapPayload))
    assert (gap.reason, gap.observation_sequence) == ("observation-outcome-malformed", 1)
