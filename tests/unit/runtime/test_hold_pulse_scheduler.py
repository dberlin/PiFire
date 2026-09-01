from dataclasses import replace

import pytest

from common.control_trace import (
    ActuationMode,
    ControllerType,
    InhibitReason,
    OutputSource,
    ResultStaleState,
    TraceEventKind,
)
from controller.applied_output import FrameFeedbackDisposition
from controller.runtime.framed_pulse import FramedPulseRuntime
from controller.runtime.logic.pulse import PulseFrameResult, PulseResetReason
from controller.runtime.runner import ControllerUpdateResult
from tests.fakes.runner import FakeControllerRunner


def _output(revision: int, duty: float, *, fan_duty: float | None = None) -> ControllerUpdateResult:
    return ControllerUpdateResult(
        cycle_ratio=duty,
        fan=None if fan_duty is None else {"duty": fan_duty},
        input_temperature=200.0,
        revision=revision,
        solve_start_monotonic=0.0,
        solve_end_monotonic=0.0,
        solve_duration_seconds=0.0,
        completed_wall_time=0.0,
    )


def _status(hold):
    return hold.grill.get_output_status()


def _runtime(mode) -> FramedPulseRuntime:
    runtime = mode._framed_pulse
    assert isinstance(runtime, FramedPulseRuntime)
    return runtime


def _trace(mode):
    trace = mode._control_trace
    assert trace is not None
    return trace


def _open_trace_session(mode, now):
    trace = _trace(mode)
    context = mode._trace_session_context()
    assert context is not None
    identity = trace.ensure_open(context, timestamp_ms=int(now * 1_000))
    assert identity is not None
    return identity


def _scheduler(mode):
    scheduler = _runtime(mode).scheduler
    assert scheduler is not None
    return scheduler


def _advance_runtime(mode, now, actual_auger_on, *, ptemp=None, apply_transition=True):
    result = _runtime(mode).advance(
        now,
        actual_auger_on,
        sample=mode._framed_sample(ptemp),
        prior_output_source=_trace(mode).applied_state.output_source,
    )
    transition = result.decision.transition
    if apply_transition and transition is not None:
        if transition.command_on:
            mode.grill.auger_on()
        else:
            mode.grill.auger_off()
    mode._dispatch_framed_result(result, record_terminal_trace=False)
    return result.decision


def _reset_runtime(mode, reason, now, inhibit, *, ptemp=None, terminal_feedback=False):
    result = _runtime(mode).reset(
        reason,
        now,
        inhibit,
        actual_auger_on=mode.grill.get_output_status()["auger"],
        sample=mode._framed_sample(ptemp),
        terminal_feedback=terminal_feedback,
        prior_output_source=_trace(mode).applied_state.output_source,
    )
    mode.grill.auger_off()
    mode._dispatch_framed_result(result, record_terminal_trace=True)
    return result


def _observe_runtime(mode, frame, *, ptemp, inhibit):
    runtime = _runtime(mode)
    runtime.latch(mode._model_role_generation(mode._runner_status()))
    completion = runtime.complete_frame(
        frame,
        sample=mode._framed_sample(ptemp),
        inhibit=inhibit,
    )
    if completion.observation is not None:
        assert completion.frame_key is not None
        learning = mode._hold_learning
        assert learning is not None
        learning.submit_completed_observation(
            completion.frame_key,
            completion.observation,
        )
    elif completion.missing_observation_reason is not None:
        mode._trace_missing_frame_observation(completion)
    return completion


class _OrderedTickRunner(FakeControllerRunner):
    def __init__(self, events, outputs):
        super().__init__(period=1.0)
        self.events = events
        self.script(outputs)

    def set_safety_ceiling_c(self, ceiling_c):
        self.events.append("safety-ceiling")
        super().set_safety_ceiling_c(ceiling_c)

    def submit(self, temp):
        self.events.append("temperature-submit")
        super().submit(temp)

    def latest(self):
        self.events.append("result")
        return super().latest()

    def set_output(self, applied):
        self.events.append(("feedback", applied.feedback_disposition))
        super().set_output(applied)


def test_normal_tick_decides_then_commands_hardware_before_feedback(hold_cycle, monkeypatch):
    events = []
    runner = _OrderedTickRunner(events, [_output(1, 0.9)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    events.clear()
    auger_on = hold.grill.auger_on

    def record_auger_on():
        events.append("hardware:auger-on")
        auger_on()

    monkeypatch.setattr(hold.grill, "auger_on", record_auger_on)

    hold.on_tick(2.0, 200.0, _status(hold))

    first_feedback = next(index for index, event in enumerate(events) if isinstance(event, tuple))
    assert events.index("safety-ceiling") < events.index("temperature-submit") < events.index("result")
    assert events.index("result") < events.index("hardware:auger-on") < first_feedback
    assert hold.grill.get_output_status()["auger"] is True


@pytest.mark.parametrize(
    ("controller", "controller_type"),
    [
        ("pid", ControllerType.PID),
        ("pid_sp", ControllerType.PID_SP),
        ("mpc", ControllerType.MPC),
    ],
)
def test_every_production_controller_builds_one_pulse_scheduler_and_starts_off(hold_cycle, controller, controller_type):
    hold = hold_cycle(FakeControllerRunner(controller_type=controller_type), controller=controller)

    hold.setup()

    assert _runtime(hold).scheduler is not None
    assert hold.grill.get_output_status()["auger"] is False
    assert hold.state.cycle.cycle_time == 0.0


def test_low_duty_accumulates_to_one_quantum(hold_cycle):
    runner = FakeControllerRunner(period=1.0).script([_output(1, 0.05)])
    hold = hold_cycle(runner, controller="mpc", cycle_data_extra={"u_min": 0.9})

    hold.setup()
    hold.on_tick(2.0, 200.0, _status(hold))

    assert hold.state.controller.pulse_requested_duty == 0.05
    assert hold.grill.get_output_status()["auger"] is False
    hold.on_tick(22.0, 200.0, _status(hold))
    assert hold.grill.get_output_status()["auger"] is True


def test_result_is_adopted_once_and_latched_at_next_frame(hold_cycle):
    first = _output(1, 0.1)
    replacement = _output(2, 0.9)
    runner = FakeControllerRunner(period=1.0).script([first, first, replacement])
    hold = hold_cycle(runner, controller="mpc")

    hold.setup()
    hold.on_tick(2.0, 200.0, _status(hold))
    hold.on_tick(4.0, 200.0, _status(hold))
    hold.on_tick(22.0, 200.0, _status(hold))

    assert hold.state.controller.pulse_result_revision == 2
    assert hold.state.controller.pulse_requested_duty == 0.9
    assert hold.grill.get_output_status()["auger"] is True


def test_lid_opening_turns_auger_off_before_dispatching_frame_progress(hold_cycle, monkeypatch):
    events = []
    runner = _OrderedTickRunner(events, [_output(1, 0.1), _output(1, 0.1), _output(1, 0.1)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.on_tick(2.0, 200.0, _status(hold))
    hold.on_tick(4.0, 200.0, _status(hold))
    hold.state.target_temp_achieved = True
    hold.settings["cycle_data"]["LidOpenDetectEnabled"] = True
    hold.control["primary_setpoint"] = 225.0
    events.clear()
    auger_off = hold.grill.auger_off

    def record_auger_off():
        events.append("auger-off")
        auger_off()

    monkeypatch.setattr(hold.grill, "auger_off", record_auger_off)

    hold.on_tick(22.0, 0.0, _status(hold))
    feedback_index = next(
        index for index, event in enumerate(events) if isinstance(event, tuple) and event[0] == "feedback"
    )
    assert events.index("auger-off") < feedback_index


def test_stale_result_continues_last_command_and_measured_feedback(hold_cycle):
    result = _output(1, 0.1)
    runner = FakeControllerRunner(period=1.0).script([result, result, result])
    hold = hold_cycle(runner, controller="mpc")

    hold.setup()
    runner.applied.clear()
    hold.on_tick(2.0, 200.0, _status(hold))
    hold.on_tick(22.0, 200.0, _status(hold))
    hold.on_tick(24.0, 200.0, _status(hold))
    hold.on_tick(26.0, 200.0, _status(hold))

    assert hold.state.controller.pulse_result_revision == 1
    assert hold.state.controller.pulse_requested_duty == 0.1
    assert runner.applied[-1].ratio == 1.0
    assert runner.applied[-1].requested == 0.1


def test_stale_command_inhibits_non_solve_ticks_until_a_fresh_result_arrives(hold_cycle):
    fresh = _output(1, 0.9)
    stale = replace(fresh, stale_state=ResultStaleState.STALE)
    recovered = _output(2, 0.9)
    runner = FakeControllerRunner(period=1.0).script([fresh, stale, recovered])
    hold = hold_cycle(runner, controller="mpc")

    hold.control["cook_id"] = "stale-recovery-no-catchup"
    hold.state.metrics = {"augerontime": 0.0}
    hold.setup()
    hold.on_tick(2.0, 200.0, _status(hold))
    hold.on_tick(4.0, 200.0, _status(hold))
    delivery_at_reset = hold.state.controller.pulse_feedback_delivered_on_s

    hold.on_tick(4.5, 200.0, _status(hold))

    assert hold.grill.get_output_status()["auger"] is False
    assert hold.state.controller.pulse_feedback_delivered_on_s == delivery_at_reset
    hold.on_tick(6.0, 200.0, _status(hold))
    assert hold.state.controller.pulse_stale_command is False
    assert hold.grill.get_output_status()["auger"] is True
    hold.on_tick(8.0, 200.0, _status(hold))
    hold.on_tick(26.0, 200.0, _status(hold))

    assert hold.state.metrics["augerontime"] == 18.0


def test_reconfiguration_replaces_scheduler_and_discards_prior_credit(hold_cycle):
    runner = FakeControllerRunner(period=1.0).script([_output(1, 0.1)])
    hold = hold_cycle(runner, controller="pid")
    hold.setup()
    hold.on_tick(2.0, 200.0, _status(hold))
    hold.on_tick(22.0, 200.0, _status(hold))
    original_scheduler = _runtime(hold).scheduler
    hold.control["controller_update"] = True

    hold.on_tick(24.0, 200.0, _status(hold))

    assert _runtime(hold).scheduler is not original_scheduler
    assert _scheduler(hold).advance(0.1, 42.0, False).credit_s < 2.0


def test_reconfiguration_uses_post_reset_auger_state_for_the_replacement_scheduler(hold_cycle):
    runner = FakeControllerRunner(period=1.0).script([_output(1, 0.9)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.control["cook_id"] = "reconfigure-observed-state"
    hold.state.metrics = {"augerontime": 0.0}
    hold.on_tick(2.0, 200.0, _status(hold))
    original_scheduler = _runtime(hold).scheduler
    captured_before_reset = _status(hold)
    hold.control["controller_update"] = True
    runner.applied.clear()

    hold.on_tick(4.0, 200.0, captured_before_reset)

    assert _runtime(hold).scheduler is not original_scheduler
    assert hold.grill.get_output_status()["auger"] is True
    assert hold.state.metrics["augerontime"] == 0.0
    assert runner.applied[0].ratio == 0.0
    assert runner.applied[0].source is OutputSource.SEED


def test_safety_manual_lid_and_teardown_reset_credit(hold_cycle):
    runner = FakeControllerRunner(period=1.0).script([_output(1, 0.9)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.on_tick(2.0, 200.0, _status(hold))
    hold.on_tick(22.0, 200.0, _status(hold))
    hold._last_now = 23.0

    hold._on_manual_output("auger", True)
    hold._on_safety_event("stop", 24.0)
    assert hold.grill.get_output_status()["auger"] is False

    hold.ctx.clock.advance(24.0)
    hold.teardown(200.0)
    assert runner.stops == 1


@pytest.mark.parametrize("event", ["stop", "error", "temperature_guard"])
def test_guard_events_reset_without_restoring_credit(hold_cycle, event):
    runner = FakeControllerRunner(period=1.0).script([_output(1, 0.9)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.on_tick(2.0, 200.0, _status(hold))
    hold.on_tick(22.0, 200.0, _status(hold))

    hold._on_safety_event(event, 23.0)
    decision = _scheduler(hold).advance(0.9, 24.0, False)

    assert decision.reset_reason is not None
    assert decision.credit_s < 2.0
    assert hold.grill.get_output_status()["auger"] is False


def test_lid_inhibit_discards_credit_and_preempts_auger(hold_cycle):
    runner = FakeControllerRunner(period=1.0).script([_output(1, 0.9)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.state.target_temp_achieved = True
    hold.settings["cycle_data"]["LidOpenDetectEnabled"] = True

    hold.on_tick(2.0, 100.0, _status(hold))

    assert hold.state.lid.open_detected is True
    assert hold.grill.get_output_status()["auger"] is False
    assert _scheduler(hold).advance(0.9, 3.0, False).reset_reason is not None


def test_deferred_mpc_to_pid_swap_accounts_old_delivery_and_seeds_post_reset_output(hold_cycle, monkeypatch):
    class DeferredRunner(FakeControllerRunner):
        def reconfigure(self, settings, control, logger=None):
            self.pending = True
            return "Active"

        def complete_swap(self):
            self._controller_type = ControllerType.PID
            self._commands_fan = False
            self._configuration_revision += 1

    runner = DeferredRunner(period=1.0, commands_fan=True, controller_type=ControllerType.MPC).script([_output(1, 0.5)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.ctx.store._settings["controller"]["selected"] = "pid"
    hold.control["cook_id"] = "deferred-generation-accounting"
    hold.state.metrics = {"augerontime": 0.0}
    hold.control["controller_update"] = True
    status = {"auger": False, "fan": False, "igniter": False, "power": True, "pwm": 100}
    hold.on_tick(2.0, 200.0, status)
    _advance_runtime(hold, 2.0, True)

    assert hold.grill.get_output_status()["auger"] is True
    runtime = _runtime(hold)
    configure_scheduler = runtime.configure

    def configure_with_live_ratio(*args, **kwargs):
        configure_scheduler(*args, **kwargs)
        hold.state.cycle.ratio = 0.5

    monkeypatch.setattr(runtime, "configure", configure_with_live_ratio)
    runner.applied.clear()

    runner.complete_swap()
    hold.on_tick(4.0, 200.0, _status(hold))

    assert _runtime(hold).scheduler is not None
    assert hold.state.cycle.cycle_time == 0.0
    assert hold._controller_name == "pid"
    assert hold.grill.get_output_status()["auger"] is True
    assert hold.state.metrics["augerontime"] == 2.0
    seed = runner.applied[0]
    assert seed.ratio == 0.0
    assert seed.source is OutputSource.SEED


def test_missed_frames_are_recorded_as_skipped_without_catchup(hold_cycle, monkeypatch):
    runner = FakeControllerRunner(period=1.0).script([_output(1, 0.1)])
    hold = hold_cycle(runner, controller="mpc")
    frames = []
    hold.setup()
    _trace(hold).record = lambda kind, payload, ts: frames.append((kind, payload)) or True
    hold.on_tick(2.0, 200.0, _status(hold))
    hold.on_tick(62.0, 200.0, _status(hold))

    skipped = [payload for kind, payload in frames if kind is TraceEventKind.ACTUATION_FRAME and payload.skipped]
    assert skipped and all(payload.scheduled_on_seconds == 0.0 for payload in skipped)
    terminal_feedback = [
        item for item in runner.applied if item.feedback_disposition is not FrameFeedbackDisposition.PROGRESS
    ]
    assert terminal_feedback[-1].feedback_disposition is FrameFeedbackDisposition.DISCARDED


def test_auger_and_fan_adopt_together_from_one_result_revision(hold_cycle):
    first = _output(1, 0.1, fan_duty=25.0)
    replacement = _output(2, 0.9, fan_duty=75.0)
    runner = FakeControllerRunner(period=1.0, commands_fan=True).script([first, replacement])
    hold = hold_cycle(runner, controller="mpc", dc_fan=True)
    hold.settings["platform"]["dc_fan"] = True
    hold.control["pwm_control"] = True
    hold.setup()

    hold.on_tick(2.0, 200.0, _status(hold))
    hold.on_tick(4.0, 200.0, _status(hold))

    assert hold.state.controller.pulse_requested_duty == 0.9
    assert hold.state.controller.pulse_requested_fan_duty == 75.0
    assert hold.state.controller.fan_duty == 75.0


@pytest.mark.parametrize("actual_on", [False, True])
def test_reset_accounts_observed_output_before_safety_or_manual_preemption(hold_cycle, monkeypatch, actual_on):
    runner = FakeControllerRunner(period=1.0).script([_output(1, 0.1)])
    hold = hold_cycle(runner, controller="mpc")
    records = []
    hold.setup()
    _trace(hold).record = lambda kind, payload, ts: records.append((kind, payload)) or True
    if actual_on:
        hold.grill.auger_on()

    hold.on_tick(2.0, 200.0, _status(hold))
    hold._on_safety_event("stop", 4.0)
    hold._last_now = 4.0
    hold._on_manual_output("auger", actual_on)

    frames = [payload for kind, payload in records if kind is TraceEventKind.ACTUATION_FRAME]
    assert frames
    assert all(
        payload.actual_end_active is payload.actual_start_active ^ bool(payload.transition_count % 2)
        for payload in frames
    )


def test_reset_keeps_cumulative_delivery_baselines_for_feedback_and_metrics(hold_cycle):
    runner = FakeControllerRunner(period=1.0)
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.control["cook_id"] = "cook-reset-accounting"
    hold.state.metrics = {"augerontime": 0.0}
    hold.state.controller.pulse_requested_duty = 0.1
    runner.applied.clear()

    _advance_runtime(hold, 20.0, False)
    _advance_runtime(hold, 22.0, True)
    before_reset = _advance_runtime(hold, 24.0, False)
    assert before_reset.delivered_on_s == 2.0
    _reset_runtime(hold, PulseResetReason.SAFETY, 24.0, InhibitReason.SAFETY)

    _advance_runtime(hold, 24.0, True)
    _advance_runtime(hold, 34.0, False)
    feedback = _runtime(hold).report_feedback(
        44.0,
        12.0,
        source=OutputSource.CONTROLLER,
        prior_output_source=_trace(hold).applied_state.output_source,
    )
    assert feedback is not None
    hold._dispatch_framed_feedback(feedback)

    assert hold.state.metrics["augerontime"] == 12.0
    assert runner.applied[-1].ratio == 0.5


def test_stale_result_preempts_hardware_and_discards_scheduler_credit(hold_cycle):
    fresh = _output(1, 0.9)
    stale = replace(fresh, stale_state=ResultStaleState.STALE)
    runner = FakeControllerRunner(period=1.0).script([fresh, stale])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()

    hold.on_tick(2.0, 200.0, _status(hold))
    assert hold.grill.get_output_status()["auger"] is True
    hold.on_tick(4.0, 200.0, _status(hold))

    assert hold.grill.get_output_status()["auger"] is False
    assert _scheduler(hold).advance(0.9, 6.0, False).reset_reason is not None


def test_completed_frame_feedback_uses_the_completed_frame_request_bound_and_revision(hold_cycle):
    runner = FakeControllerRunner(period=1.0).script([_output(1, 0.1)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    controller = hold.state.controller
    controller.pulse_result_revision = 1
    controller.pulse_requested_duty = 0.1
    controller.pulse_maximum_duty = 0.5
    hold.on_tick(2.0, 200.0, _status(hold))
    _advance_runtime(hold, 2.0, False)
    _advance_runtime(hold, 2.0, True)
    _advance_runtime(hold, 4.0, True)
    _advance_runtime(hold, 4.0, False)
    controller.pulse_result_revision = 2
    controller.pulse_requested_duty = 0.9
    controller.pulse_maximum_duty = 1.0
    runner.applied.clear()
    if _trace(hold).applied_state.result_revision is None:
        assert _trace(hold).promote_seed_interval(1, OutputSource.CONTROLLER)

    _advance_runtime(hold, 22.0, False)

    assert runner.applied[-1].requested == 0.1
    assert _trace(hold).applied_state.combustion_load == 0.2
    assert _trace(hold).applied_state.result_revision == 1


def test_teardown_reports_final_observed_pulse_delivery_before_reset(hold_cycle):
    runner = FakeControllerRunner()
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    controller = hold.state.controller
    controller.pulse_result_revision = 1
    controller.pulse_requested_duty = 0.1
    _advance_runtime(hold, 0.0, False)
    runner.applied.clear()
    hold.ctx.clock.advance(2.0)
    _advance_runtime(hold, 0.0, True)

    hold.teardown(200.0)

    assert any(applied.requested == 0.1 and applied.ratio == 1.0 for applied in runner.applied)


def test_teardown_turns_auger_off_before_dispatching_final_frame_progress(hold_cycle, monkeypatch):
    events = []
    runner = FakeControllerRunner()
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    controller = hold.state.controller
    controller.pulse_result_revision = 1
    controller.pulse_requested_duty = 0.1
    _advance_runtime(hold, 0.0, False)
    hold.grill.auger_on()
    runner.applied.clear()
    hold.ctx.clock.advance(2.0)
    auger_off = hold.grill.auger_off
    set_output = runner.set_output

    def record_auger_off():
        events.append("auger-off")
        auger_off()

    def record_output(applied):
        events.append(("feedback", applied.feedback_disposition))
        set_output(applied)

    monkeypatch.setattr(hold.grill, "auger_off", record_auger_off)
    monkeypatch.setattr(runner, "set_output", record_output)

    hold.teardown(200.0)

    feedback_index = next(
        index for index, event in enumerate(events) if isinstance(event, tuple) and event[0] == "feedback"
    )
    assert events.index("auger-off") < feedback_index


class _ObservationStatusRunner(FakeControllerRunner):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.observation_status = {}

    def controller_state(self):
        return {"fake": True, **self.observation_status}


def _completed_frame(
    *,
    start=0.0,
    end=20.0,
    delivered=6.0,
    skipped=False,
    reset_reason=None,
):
    return PulseFrameResult(
        nominal_start_s=start,
        nominal_end_s=start + 20.0,
        ended_at_s=end,
        complete=not skipped and reset_reason is None,
        skipped=skipped,
        latched_request=0.3,
        credit_before_s=0.0,
        credit_after_s=0.0,
        scheduled_on_s=6,
        delivered_on_s=delivered,
        observed_transition_count=2,
        actual_start_on=False,
        actual_end_on=False,
        reset_reason=reset_reason,
    )


def _configure_frame_observation(mode, *, revision=1, u_max=0.5, load=0.3):
    controller = mode.state.controller
    controller.pulse_result_revision = revision
    controller.pulse_frame_result_revision = revision
    controller.pulse_requested_duty = 0.0 if load is None else load
    controller.pulse_combustion_load = load
    controller.pulse_maximum_duty = u_max
    controller.pulse_requested_fan_duty = 50.0
    controller.pulse_frame_requested_auger_duty = 0.0 if load is None else load
    controller.pulse_frame_combustion_load = load
    controller.pulse_frame_baseline_combustion_load = 0.0 if load is None else load
    controller.pulse_frame_calibration_probe_load = 0.0
    controller.pulse_frame_calibration_stage = None
    controller.pulse_frame_maximum_duty = u_max
    controller.pulse_frame_applied_fan_duty = 60.0
    controller.pulse_frame_stale_command = False
    controller.controls_fan = True


def test_framed_completed_observations_are_exactly_aligned_and_deduplicated(hold_cycle):
    runner = _ObservationStatusRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE)
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.control["cook_id"] = "completed-frame-observations"
    _configure_frame_observation(mode)

    _advance_runtime(mode, 0.0, True, ptemp=212.0)
    _advance_runtime(mode, 6.0, False, ptemp=212.0)
    _advance_runtime(mode, 20.0, False, ptemp=212.0)
    _advance_runtime(mode, 20.0, True, ptemp=392.0)
    _advance_runtime(mode, 26.0, False, ptemp=392.0)
    _advance_runtime(mode, 40.0, False, ptemp=392.0)

    assert len(runner.observations) == 2
    first, second = runner.observations
    assert (first.frame_start_s, first.frame_end_s, first.delivered_on_s) == (0.0, 20.0, 6.0)
    assert first.realized_q == pytest.approx((6.0 / 20.0) / 0.5)
    assert first.temp_c == pytest.approx(100.0)
    assert (second.frame_start_s, second.frame_end_s, second.delivered_on_s) == (20.0, 40.0, 6.0)
    assert second.temp_c == pytest.approx(200.0)


def test_framed_observation_latches_role_generation_at_frame_start(hold_cycle):
    runner = _ObservationStatusRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE)
    runner.observation_status = {"adaptation": {"role_generation": 7}}
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.control["cook_id"] = "latched-role-generation"
    _configure_frame_observation(mode)

    _advance_runtime(mode, 0.0, True, ptemp=212.0)
    _advance_runtime(mode, 6.0, False, ptemp=212.0)
    runner.observation_status = {"adaptation": {"role_generation": 8}}
    _advance_runtime(mode, 20.0, False, ptemp=212.0)
    _advance_runtime(mode, 20.0, True, ptemp=392.0)
    _advance_runtime(mode, 26.0, False, ptemp=392.0)
    _advance_runtime(mode, 40.0, False, ptemp=392.0)

    assert [(item.result_revision, item.role_generation) for item in runner.observations] == [(1, 7), (1, 8)]


@pytest.mark.parametrize(
    ("runner_status", "expected_generation"),
    [
        (
            {
                "activation": {"role_generation": True},
                "adaptation": {"role_generation": 7},
            },
            7,
        ),
        ({"activation": {"role_generation": -1}}, 0),
        ({"adaptation": {"role_generation": False}}, 0),
        ({"role_generation": 9}, 9),
        (["not", "a", "mapping"], 0),
    ],
    ids=[
        "invalid-activation-falls-back-to-adaptation",
        "negative-activation-falls-back-to-zero",
        "boolean-adaptation-falls-back-to-zero",
        "legacy-top-level-generation",
        "non-mapping-runner-status",
    ],
)
def test_framed_observation_normalizes_runner_role_generation_status(
    hold_cycle,
    monkeypatch,
    runner_status,
    expected_generation,
) -> None:
    runner = _ObservationStatusRunner(
        period=1.0,
        actuation_mode=ActuationMode.FRAMED_PULSE,
    )
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.control["cook_id"] = "normalized-role-generation"
    _configure_frame_observation(mode)
    monkeypatch.setattr(
        runner,
        "controller_state",
        lambda: runner_status,
    )

    _advance_runtime(mode, 0.0, True, ptemp=212.0)
    _advance_runtime(mode, 6.0, False, ptemp=212.0)
    _advance_runtime(mode, 20.0, False, ptemp=212.0)

    assert [item.role_generation for item in runner.observations] == [expected_generation]


def test_framed_observation_survives_runner_status_failure(
    hold_cycle,
    monkeypatch,
) -> None:
    runner = _ObservationStatusRunner(
        period=1.0,
        actuation_mode=ActuationMode.FRAMED_PULSE,
    )
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.control["cook_id"] = "unavailable-runner-status"
    _configure_frame_observation(mode)

    def unavailable_status():
        raise RuntimeError("runner status unavailable")

    monkeypatch.setattr(runner, "controller_state", unavailable_status)

    _advance_runtime(mode, 0.0, True, ptemp=212.0)
    _advance_runtime(mode, 6.0, False, ptemp=212.0)
    _advance_runtime(mode, 20.0, False, ptemp=212.0)

    assert len(runner.observations) == 1
    assert runner.observations[0].role_generation == 0


def test_framed_observation_survives_non_mapping_runner_status(
    hold_cycle,
    monkeypatch,
) -> None:
    runner = _ObservationStatusRunner(
        period=1.0,
        actuation_mode=ActuationMode.FRAMED_PULSE,
    )
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.control["cook_id"] = "malformed-runner-status"
    _configure_frame_observation(mode)
    monkeypatch.setattr(runner, "controller_state", lambda: None)

    _advance_runtime(mode, 0.0, True, ptemp=212.0)
    _advance_runtime(mode, 6.0, False, ptemp=212.0)
    _advance_runtime(mode, 20.0, False, ptemp=212.0)

    assert len(runner.observations) == 1
    assert runner.observations[0].role_generation == 0


@pytest.mark.parametrize(
    ("case", "inhibit", "skipped", "reset_reason", "expected_source"),
    [
        ("lid", InhibitReason.LID_OPEN, False, PulseResetReason.LID, "lid_open"),
        ("manual", InhibitReason.MANUAL_OVERRIDE, False, PulseResetReason.MANUAL, "manual_override"),
        ("safety", InhibitReason.SAFETY, False, PulseResetReason.SAFETY, "unknown"),
        ("stale", InhibitReason.STALE_COMMAND, False, None, "controller"),
        ("skipped", InhibitReason.NONE, True, None, "controller"),
        ("reset", InhibitReason.NONE, False, PulseResetReason.MODE_CHANGE, "unknown"),
        ("unknown", InhibitReason.NONE, False, None, "unknown"),
    ],
)
def test_ineligible_completed_frames_are_delivered_with_explicit_provenance(
    hold_cycle, case, inhibit, skipped, reset_reason, expected_source
):
    runner = _ObservationStatusRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE)
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.control["cook_id"] = f"ineligible-{case}"
    identity = _open_trace_session(mode, 0.0)
    runner.bind_evidence_context(0, identity.session_id, identity.cook_id)
    _configure_frame_observation(mode, load=None if case == "unknown" else 0.3)  # type: ignore[arg-type]
    records = []
    _trace(mode).record = lambda kind, payload, timestamp: records.append((kind, payload, timestamp)) or True
    runner.observation_outcome = {  # type: ignore[assignment]
        "eligible": False,
        "rejection_reasons": ("ineligible_frame",),
        "input_variance": 0.0,
        "input_levels": 0,
        "effective_updates": 0,
        "model_digest": "a" * 64,
    }

    _observe_runtime(
        mode,
        _completed_frame(skipped=skipped, reset_reason=reset_reason),
        ptemp=212.0,
        inhibit=inhibit,
    )

    assert len(runner.observations) == 1
    learning = mode._hold_learning
    assert learning is not None
    learning.reconcile_outcomes(20.0)
    observation = runner.observations[0]
    assert observation.output_source == expected_source
    assert observation.continuous is False
    assert observation.lid_open is (case == "lid")
    assert observation.manual_override is (case == "manual")
    assert observation.safety_inhibited is (case == "safety")
    assert observation.stale is (case == "stale")
    assert observation.skipped is (case == "skipped")
    assert observation.reset is (case in {"lid", "manual", "safety", "reset"})
    assert records and records[-1][0] is TraceEventKind.MODEL_OBSERVATION
    assert records[-1][1].eligible is False


def test_seed_and_zero_duration_frames_do_not_reach_the_runner(hold_cycle):
    runner = _ObservationStatusRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE)
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.control["cook_id"] = "seed-zero-observations"
    _configure_frame_observation(mode, revision=0)

    _observe_runtime(mode, _completed_frame(), ptemp=212.0, inhibit=InhibitReason.NONE)
    _configure_frame_observation(mode)
    _observe_runtime(mode, _completed_frame(end=0.0, delivered=0.0), ptemp=212.0, inhibit=InhibitReason.NONE)

    assert runner.observations == []


def test_running_controller_receives_changed_setpoint_without_rebuild(
    hold_cycle,
) -> None:
    runner = FakeControllerRunner(period=999).script([_output(1, 0.3)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()

    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    hold.control["primary_setpoint"] = 250.0
    hold.on_tick(4.0, 200.0, hold.grill.get_output_status())

    assert runner.target == 250.0
    assert runner.configuration_revision() == 0
