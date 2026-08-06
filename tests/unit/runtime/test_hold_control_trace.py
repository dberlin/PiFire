"""End-to-end Hold control-trace contracts using the normal fake runtime seam."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from common.control_trace import (
    ActuationMode,
    ControllerBranch,
    ControllerType,
    InhibitReason,
    ModelObservationPayload,
    RecorderGapPayload,
    PidSpUpdatePayload,
    ResultStaleState,
    SafetyEventType,
    TraceEventKind,
)
from common.datastore_accessors import read_control_trace_session
from common import datastore
from controller.applied_output import OutputSource
from controller.base import MpcFailureState, MpcTraceDiagnostics, PidSpTraceDiagnostics, PidTraceDiagnostics
from controller.control_trace_replay import ReplayIssueCode, validate_records
from controller.mpc_allocator import allocate
from controller.runtime.control_trace_recorder import ControlTraceRecorder
from controller.runtime.runner import ControllerUpdateResult, ObservationOutcomeEnvelope, SyncControllerRunner
from controller.linear_mpc.contracts import FrameObservation
from tests.fakes.runner import FakeControllerRunner
from controller.update_mpc import load_trace_samples


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


def test_mpc_hold_records_update_allocation_and_framed_feedback_once_per_revision(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    result = _mpc_result()
    runner = FakeControllerRunner(period=1.0, commands_fan=True, actuation_mode=ActuationMode.FRAMED_PULSE).script(
        [result, result]
    )
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.state.metrics = {"id": "cook-mpc"}
    output = {"auger": False, "fan": False, "igniter": False, "power": True, "pwm": 100}
    mode.on_tick(2.0, 220.0, output)
    mode.on_tick(22.0, 220.0, mode.grill.get_output_status())

    event_kinds = [record.event_kind for record in recorder.records]
    assert event_kinds[:4] == [
        TraceEventKind.SESSION,
        TraceEventKind.CONTROL_UPDATE,
        TraceEventKind.ALLOCATION,
        TraceEventKind.APPLIED_OUTPUT,
    ]
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
    assert validate_records(recorder.records).valid


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
    mode.state.metrics = {"id": f"cook-{controller}"}

    mode.on_tick(2.0, 220.0, {"auger": False, "fan": False, "igniter": False, "power": True, "pwm": 100})
    mode.on_tick(22.0, 220.0, mode.grill.get_output_status())

    frames = [record for record in recorder.records if record.event_kind is TraceEventKind.ACTUATION_FRAME]
    assert frames and all(record.controller.value == controller for record in frames)
    assert validate_records(recorder.records).valid


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
    mode.state.metrics = {"id": "fahrenheit-ambient"}
    mode._ensure_trace_session(0.0)
    controller = mode.state.controller
    controller.pulse_result_revision = controller.pulse_frame_result_revision = 1
    controller.pulse_frame_combustion_load = 0.3
    controller.pulse_frame_requested_auger_duty = 0.3
    controller.pulse_frame_maximum_duty = 0.5

    from controller.runtime.logic.pulse import PulseFrameResult

    frame = PulseFrameResult(0.0, 20.0, 20.0, True, False, 0.3, 0.0, 0.0, 6, 6.0, 2, False, False, None)
    mode._observe_completed_pulse_frame(frame, ptemp=212.0, inhibit=InhibitReason.NONE)
    runner._observation_outcomes.append(
        ObservationOutcomeEnvelope(1, 0, runner.observations[0], _promotion_outcome(frame_end_ms=20_000))
    )
    mode._reconcile_model_observation_outcomes(now=22.0)

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
    mode.state.metrics = {"id": "cook-mpc-seed"}
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
    mode.state.metrics = {"id": "cook-mpc-seed-lid-reset"}
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
    mode.state.metrics = {"id": "cook-mpc-misaligned"}

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
    mode.state.metrics = {"id": "cook-mpc-no-fan"}
    mode.on_tick(2.0, 220.0, {"auger": False, "fan": False, "igniter": False, "power": True, "pwm": 100})
    allocation = next(record.payload for record in recorder.records if record.event_kind is TraceEventKind.ALLOCATION)

    assert allocation.requested_fan_duty is None
    assert allocation.fan_enabled is False


def test_production_hold_seed_lifecycle_rereads_into_calibration(hold_cycle, tmp_path):
    datastore._reset_for_tests(str(tmp_path / "hold-trace.db"))
    runner = FakeControllerRunner(period=1.0, commands_fan=True, actuation_mode=ActuationMode.FRAMED_PULSE).script(
        [_mpc_result(revision) for revision in range(1, 33)]
    )
    mode = hold_cycle(runner, controller="mpc")
    mode.settings["platform"]["dc_fan"] = True
    mode.control["pwm_control"] = True
    mode.ctx.store._settings["platform"]["dc_fan"] = True
    mode.ctx.store._control["pwm_control"] = True
    mode.setup()
    assert isinstance(mode._trace_recorder, ControlTraceRecorder)
    mode.state.metrics = {"id": "calibration-seed", "augerontime": 0.0}
    output = {"auger": False, "fan": False, "igniter": False, "power": True, "pwm": 100}
    for now in range(2, 64, 2):
        mode.on_tick(float(now), 225.0, output)
        output = mode.grill.get_output_status()
    mode.ctx.clock.advance(64)
    mode.teardown(220.0)

    session_id = mode._trace_session_id
    assert session_id is not None
    records = read_control_trace_session(session_id)
    assert [record.event_kind for record in records[:4]] == [
        TraceEventKind.SESSION,
        TraceEventKind.CONTROL_UPDATE,
        TraceEventKind.ALLOCATION,
        TraceEventKind.APPLIED_OUTPUT,
    ]
    seed_index = 3
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
    assert temperatures_c.tolist() == [100.0, 100.0, 100.0]
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
    mode.state.metrics = {"id": "cook-stale-observation"}
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
    mode.state.metrics = {"id": "cook-recovery"}
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
    mode.state.metrics = {"id": "cook-raw-zero"}
    mode.on_tick(2.0, 220.0, {"auger": False, "fan": False, "igniter": False, "power": True, "pwm": 100})
    (update,) = [record.payload for record in recorder.records if record.event_kind is TraceEventKind.CONTROL_UPDATE]

    assert update.raw_output == 0.0


def test_pid_sp_completed_update_records_exact_typed_fields_and_branch(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    mode = hold_cycle(FakeControllerRunner(period=1.0).script([_pid_sp_result()]), controller="pid_sp")
    mode.setup()
    mode.state.metrics = {"id": "cook-pid-sp"}

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


def test_reconfigure_finishes_the_old_pid_session_before_opening_coherent_mpc_session(hold_cycle, monkeypatch):
    class _ReconfiguringRunner(FakeControllerRunner):
        def reconfigure(self, settings, control, logger=None):
            self._commands_fan = True
            self._actuation_mode = ActuationMode.FRAMED_PULSE
            return super().reconfigure(settings, control, logger=logger)

    class _ModelStore:
        def load(self, controller):
            return {"revision": 8} if controller == "mpc" else None

        def save(self, controller, snapshot):
            return True

    recorder = _install_recorder(monkeypatch)
    runner = _ReconfiguringRunner(period=1.0, wants_async=True).script([_pid_result(), _mpc_result(2)])
    runner.snapshot = {"revision": 1}
    mode = hold_cycle(runner, controller="pid", model_store=_ModelStore())
    mode.settings["platform"]["dc_fan"] = True
    mode.control["pwm_control"] = True
    mode.ctx.store._settings["platform"]["dc_fan"] = True
    mode.ctx.store._settings["controller"]["selected"] = "mpc"
    mode.ctx.store._settings["controller"]["config"]["mpc"]["trace_marker"] = "new-mpc-session"
    mode.ctx.store._control["pwm_control"] = True
    mode.setup()
    mode.state.metrics = {"id": "cook-reconfigure"}
    output = {"auger": False, "fan": False, "igniter": False, "power": True, "pwm": 100}
    mode.on_tick(2.0, 220.0, output)
    old_session_id = mode._trace_session_id

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
    assert dict((setting.key, setting.value) for setting in new_session.payload.controller_config)["trace_marker"] == (
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
    assert not any(
        record.event_kind is TraceEventKind.APPLIED_OUTPUT and record.payload.result_revision == 0
        for record in new_session_events
    )
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
    mode.state.metrics = {"id": "cook-mpc-zero"}

    mode.on_tick(2.0, 220.0, {"auger": False, "fan": False, "igniter": False, "power": True, "pwm": 100})

    update = next(record.payload for record in recorder.records if record.event_kind is TraceEventKind.CONTROL_UPDATE)
    allocation = next(record.payload for record in recorder.records if record.event_kind is TraceEventKind.ALLOCATION)
    applied = next(record.payload for record in recorder.records if record.event_kind is TraceEventKind.APPLIED_OUTPUT)
    assert (update.raw_output, allocation.requested_auger_duty, applied.realized_auger_duty) == (0.0, 0.0, 0.0)


@pytest.mark.parametrize(
    ("accepted", "expected_event"),
    [(False, "reject"), (True, "adopt")],
    ids=["rejected-refit", "accepted-refit"],
)
def test_refit_records_refit_then_its_verdict(hold_cycle, monkeypatch, accepted, expected_event):
    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0, commands_fan=True, actuation_mode=ActuationMode.FRAMED_PULSE)
    runner.snapshot = {"revision": 7}
    runner.refit_verdict = SimpleNamespace(accepted=accepted)
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.settings["controller"]["config"]["mpc"]["enable_identification"] = True
    mode.state.metrics = {"id": f"cook-refit-{accepted}"}
    mode._ensure_trace_session(1.0)

    mode.teardown(220.0)

    model_events = [
        record.payload.event.value for record in recorder.records if record.event_kind is TraceEventKind.MODEL_EVENT
    ]
    assert model_events == ["refit", expected_event]


def test_mpc_applied_load_is_measured_and_attributed_to_the_producing_frame(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    result = _mpc_result(1)
    runner = FakeControllerRunner(period=1.0, commands_fan=True, actuation_mode=ActuationMode.FRAMED_PULSE).script(
        [result, result]
    )
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.state.metrics = {"id": "cook-mpc-feedback"}

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
    mode.state.metrics = {"id": "cook-mpc-lid-feedback", "augerontime": 0}
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
    mode.state.metrics = {"id": "cook-mpc-manual-feedback", "augerontime": 0}

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
    assert first.allocation is not None
    first = replace(first, allocation=replace(first.allocation, normalized_combustion_load=0.2))
    second = _mpc_result(2, enable_fan=False, stale_state=ResultStaleState.STALE)
    assert second.allocation is not None
    second = replace(second, allocation=replace(second.allocation, normalized_combustion_load=0.8))
    mode = hold_cycle(
        FakeControllerRunner(period=1.0, commands_fan=True, actuation_mode=ActuationMode.FRAMED_PULSE).script(
            [first, second]
        ),
        controller="mpc",
    )
    mode.setup()
    mode.state.metrics = {"id": "cook-framed-latch"}

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
    assert frame.requested_fan_duty == first.allocation.fan_duty
    assert frame.stale_command is False


def test_initial_async_restore_session_uses_queued_snapshot_not_old_published_snapshot(hold_cycle, monkeypatch):
    class _ModelStore:
        def load(self, controller):
            return {"revision": 8} if controller == "mpc" else None

        def save(self, controller, snapshot):
            return True

    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(
        period=1.0, commands_fan=True, wants_async=True, actuation_mode=ActuationMode.FRAMED_PULSE
    )
    runner.snapshot = {"revision": 1}
    mode = hold_cycle(runner, controller="mpc", model_store=_ModelStore())
    mode.setup()
    mode.state.metrics = {"id": "cook-async-restore"}

    mode._ensure_trace_session(1.0)

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

        def restore_model(self, snapshot):
            self.restored.append(snapshot)
            return restore_accepted

    class _ModelStore:
        def load(self, controller):
            return stored_snapshot if controller == "mpc" else None

        def save(self, controller, snapshot):
            return True

    recorder = _install_recorder(monkeypatch)
    runner = _Runner(period=1.0, wants_async=True).script([_pid_result(), _mpc_result(2)])
    runner.snapshot = {"revision": 1}
    mode = hold_cycle(runner, controller="pid", model_store=_ModelStore())
    mode.ctx.store._settings["controller"]["selected"] = "mpc"
    mode.setup()
    mode.state.metrics = {"id": f"cook-no-leak-{restore_accepted}"}
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

        def save(self, controller, snapshot):
            return True

    recorder = _install_recorder(monkeypatch)
    mode = hold_cycle(SyncControllerRunner(_Core()), controller="mpc", model_store=_ModelStore())
    mode.setup()
    mode.state.metrics = {"id": "cook-sync-restore"}

    mode._ensure_trace_session(1.0)

    (session,) = [record for record in recorder.records if record.event_kind is TraceEventKind.SESSION]
    assert (session.payload.model_revision, session.payload.model_provenance) == (8, "restored")


def test_initial_session_uses_the_current_published_model_without_restore(hold_cycle, monkeypatch):
    class _NoModelStore:
        def load(self, controller):
            return None

        def save(self, controller, snapshot):
            return True

    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(
        period=1.0, commands_fan=True, wants_async=True, actuation_mode=ActuationMode.FRAMED_PULSE
    )
    runner.snapshot = {"revision": 5}
    mode = hold_cycle(runner, controller="mpc", model_store=_NoModelStore())
    mode.setup()
    mode.state.metrics = {"id": "cook-published-model"}

    mode._ensure_trace_session(1.0)

    (session,) = [record for record in recorder.records if record.event_kind is TraceEventKind.SESSION]
    assert (session.payload.model_revision, session.payload.model_provenance) == (5, "persisted")


def test_base_manual_auger_on_reasserts_manual_output_after_framed_reset(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE).script([_mpc_result(1)])
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.state.metrics = {"id": "cook-base-manual"}
    mode.settings["safety"]["manual_override_time"] = 30
    mode.settings["safety"]["allow_manual_changes"] = True
    mode._ensure_trace_session(1.0)
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
    assert len(seeds) == 1 and seeds[0].sample_complete is True
    assert validate_records(recorder.records).valid


def test_automatic_lid_preempts_same_tick_framed_on_transition_and_keeps_replay_valid(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE).script(
        [_mpc_result(1), _mpc_result(1)]
    )
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.state.metrics = {"id": "cook-same-tick-lid"}
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
        "incumbent_innovation_c": 1.0 if eligible else None,
        "challenger_innovation_c": 0.5 if eligible else None,
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
        }
        for index in range(12)
    )
    return {
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
            "provenance": "scheduled-arx",
            "detail": "promotion",
            "model_kind": "scheduled-arx",
            "model_schema": "scheduled-arx/v1",
            "role_generation": 1,
            "snapshot_digest": "c" * 64,
            "parameters": (),
        },
    }


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
    mode.state.metrics = {"id": "ordered-learning-trace"}
    mode._ensure_trace_session(0.0)
    first, second = _learning_observation(0.0), _learning_observation(20.0)
    mode._pending_model_observations = {
        1: (first, mode._trace_session_id, 0, None),
        2: (second, mode._trace_session_id, 0, None),
    }
    return recorder, runner, mode, first, second


def test_framed_learning_trace_waits_for_the_matching_actual_async_outcome(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)

    class _Runner(FakeControllerRunner):
        def __init__(self):
            super().__init__(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE)

    runner = _Runner()
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.state.metrics = {"id": "async-learning-trace"}
    mode._ensure_trace_session(0.0)
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
    mode._observe_completed_pulse_frame(frame, ptemp=212.0, inhibit=InhibitReason.NONE)

    assert not [record for record in recorder.records if isinstance(record.payload, ModelObservationPayload)]
    runner._observation_outcomes.append(
        ObservationOutcomeEnvelope(1, 0, runner.observations[0], _promotion_outcome(frame_end_ms=20_000))
    )
    mode._reconcile_model_observation_outcomes(now=22.0)
    mode._reconcile_model_observation_outcomes(now=23.0)

    payloads = [record.payload for record in recorder.records if isinstance(record.payload, ModelObservationPayload)]
    assert len(payloads) == 1
    payload = payloads[0]
    assert (payload.frame_start_ms, payload.frame_end_ms, payload.role_generation) == (0, 20_000, 0)
    assert payload.eligible is False
    assert payload.rejection_reasons == ("insufficient_excitation",)
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


def test_framed_learning_trace_retries_transient_recorder_failure(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE)
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.state.metrics = {"id": "retry-learning-trace"}
    mode._ensure_trace_session(0.0)
    controller = mode.state.controller
    controller.pulse_result_revision = controller.pulse_frame_result_revision = 1
    controller.pulse_frame_combustion_load = 0.3
    controller.pulse_frame_requested_auger_duty = 0.3
    controller.pulse_frame_maximum_duty = 0.5
    from controller.runtime.logic.pulse import PulseFrameResult

    frame = PulseFrameResult(0.0, 20.0, 20.0, True, False, 0.3, 0.0, 0.0, 6, 6.0, 2, False, False, None)
    mode._observe_completed_pulse_frame(frame, ptemp=212.0, inhibit=InhibitReason.NONE)
    runner._observation_outcomes.append(
        ObservationOutcomeEnvelope(1, 0, runner.observations[0], _promotion_outcome(frame_end_ms=20_000))
    )
    original = mode._trace_record
    attempts = 0

    def transient(kind, payload, timestamp):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            return False
        return original(kind, payload, timestamp)

    mode._trace_record = transient
    mode._reconcile_model_observation_outcomes(now=22.0)
    assert len(mode._pending_model_observations[1][3]) == 2
    mode._reconcile_model_observation_outcomes(now=23.0)
    assert 1 not in mode._pending_model_observations
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
    second_outcome["lifecycle"] = {**second_outcome["lifecycle"], "detail": "promotion-2"}
    runner._observation_outcomes.extend(
        [
            ObservationOutcomeEnvelope(1, 0, first, first_outcome),
            ObservationOutcomeEnvelope(2, 0, second, second_outcome),
        ]
    )
    original = mode._trace_record
    failed = False

    def transient(kind, payload, timestamp):
        nonlocal failed
        if kind is TraceEventKind.MODEL_EVENT and not failed:
            failed = True
            return False
        return original(kind, payload, timestamp)

    mode._trace_record = transient
    mode._reconcile_model_observation_outcomes(now=22.0)
    assert [
        record.payload.decision_id
        for record in recorder.records
        if record.event_kind is TraceEventKind.MODEL_EVALUATION
    ] == ["generation-0-evaluation-1"]
    assert len(mode._pending_model_observations[1][3]) == 1
    assert len(mode._pending_model_observations[2][3]) == 3

    mode._reconcile_model_observation_outcomes(now=23.0)

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
    runner._observation_outcomes.append(
        ObservationOutcomeEnvelope(2, 0, second, _promotion_outcome(frame_end_ms=40_000))
    )

    mode._reconcile_model_observation_outcomes(now=22.0)

    assert not [
        record
        for record in recorder.records
        if record.event_kind
        in {TraceEventKind.MODEL_EVALUATION, TraceEventKind.MODEL_EVENT, TraceEventKind.MODEL_OBSERVATION}
    ]
    runner._observation_outcomes.append(
        ObservationOutcomeEnvelope(1, 0, first, _promotion_outcome(frame_end_ms=20_000))
    )
    mode._reconcile_model_observation_outcomes(now=23.0)

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
    mode.state.metrics = {"id": "rollback-lifecycle"}
    mode._ensure_trace_session(0.0)
    original = mode._trace_record
    failed = False

    def transient(kind, payload, timestamp):
        nonlocal failed
        if kind is TraceEventKind.MODEL_EVENT and not failed:
            failed = True
            return False
        return original(kind, payload, timestamp)

    mode._trace_record = transient
    mode._trace_update(result, now=2.0, controller_interval=1.0)
    assert len(mode._trace_pending_model_events) == 1
    mode._ensure_trace_session(2.5)
    mode._trace_update(
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
    _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE)
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode._ensure_trace_session(0.0)
    mode._pending_model_observations = {1: (None, mode._trace_session_id, 0, object())}

    mode._adopt_runner_configuration(1.0, mode.grill.get_output_status())

    assert mode._pending_model_observations == {}


def test_persistent_trace_retry_retention_is_bounded_to_pending_capacity(hold_cycle):
    mode = hold_cycle(FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE), controller="mpc")
    mode.setup()
    mode._pending_model_observations = {sequence: (None, "old-session", 0, object()) for sequence in range(60)}
    mode._trace_record = lambda *_: False

    mode._reconcile_model_observation_outcomes(now=1.0)

    assert len(mode._pending_model_observations) == 60


def test_hold_retires_self_evicted_submission_immediately(hold_cycle):
    class SelfEvictingRunner(FakeControllerRunner):
        def observe_frame(self, observation):
            from controller.runtime.runner import ObservationSubmission

            return ObservationSubmission(1, 0, 1)

    runner = SelfEvictingRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE)
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    existing = {sequence: (None, "existing-session", 0, object()) for sequence in range(2, 62)}
    mode._pending_model_observations = dict(existing)
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
    mode._deliver_completed_pulse_observation((0, 20), observation)

    assert mode._pending_model_observations == existing


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
    mode.state.metrics = {"id": "trace-append-recovery"}
    output = {"auger": False, "fan": False, "igniter": False, "power": True, "pwm": 100}

    mode.on_tick(2.0, 212.0, output)
    recorder.flush_due(5_000)
    assert persisted == []

    mode.on_tick(4.0, 213.0, output)
    runner.observation_outcome = _model_observation_outcome(frame_end_ms=20_000)
    for index in range(3):
        mode._deliver_completed_pulse_observation((index * 20, (index + 1) * 20), _learning_observation(index * 20.0))
    mode._reconcile_model_observation_outcomes(now=22.0)

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
