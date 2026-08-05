import pytest

from common.control_trace import ActuationMode, ControllerType, InhibitReason, SafetyEventType, TraceEventKind
from controller.runtime.runner import ControllerUpdateResult
from controller.runtime.logic.pulse import PulseResetReason

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


def test_fixed_cycle_runner_keeps_existing_cycle_initialization(hold_cycle):
    runner = FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FIXED_CYCLE)
    hold = hold_cycle(runner, controller="pid")

    hold.setup()

    assert hold.state.cycle.cycle_time == hold.settings["cycle_data"]["HoldCycleTime"]
    assert hold.state.cycle.ratio == hold.settings["cycle_data"]["u_min"]
    assert hold.grill.get_output_status()["auger"] is True
    assert hold._pulse_scheduler is None


def test_framed_runner_starts_off_and_ignores_fixed_cycle_floor(hold_cycle):
    runner = FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE).script([_output(1, 0.1)])
    hold = hold_cycle(runner, controller="mpc", cycle_data_extra={"u_min": 0.9, "HoldCycleTime": 99})

    hold.setup()
    hold.on_tick(2.0, 200.0, _status(hold))

    assert hold.grill.get_output_status()["auger"] is True
    assert hold.state.cycle.ratio == 0.1
    assert (hold.state.cycle.on_time, hold.state.cycle.off_time, hold.state.cycle.cycle_time) == (0.0, 0.0, 0.0)
    assert hold._pulse_scheduler is not None


def test_framed_result_is_latched_only_on_revision_advance_and_next_frame(hold_cycle):
    first = _output(1, 0.1)
    replacement = _output(2, 0.9)
    runner = FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE).script(
        [first, first, replacement]
    )
    hold = hold_cycle(runner, controller="mpc")

    hold.setup()
    hold.on_tick(2.0, 200.0, _status(hold))
    hold.on_tick(4.0, 200.0, _status(hold))
    hold.on_tick(22.0, 200.0, _status(hold))

    assert hold.state.controller.pulse_result_revision == 2
    assert hold.state.controller.pulse_requested_duty == 0.9
    assert hold.grill.get_output_status()["auger"] is True
    assert hold.state.controller.pulse_requested_duty == 0.9


def test_framed_stale_result_continues_last_bounded_command_and_measured_feedback(hold_cycle):
    result = _output(1, 0.1)
    runner = FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE).script(
        [result, result, result]
    )
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


def test_framed_safety_manual_lid_reconfigure_and_teardown_reset_credit(hold_cycle):
    runner = FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE).script([_output(1, 0.9)])
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


def test_framed_trace_is_typed_and_records_safety_before_reset_frame(hold_cycle, monkeypatch):
    runner = FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE).script([_output(1, 0.1)])
    hold = hold_cycle(runner, controller="mpc")
    records = []
    monkeypatch.setattr(hold, "_trace_record", lambda kind, payload, ts: records.append((kind, payload, ts)) or True)

    hold.setup()
    hold.state.metrics = {"id": "pulse-cook"}
    hold._ensure_trace_session(0.0)
    hold.on_tick(2.0, 200.0, _status(hold))
    hold.on_tick(22.0, 200.0, _status(hold))
    hold._on_safety_event("stop", 23.0)

    frame_index = next(
        index
        for index, (kind, payload, _ts) in enumerate(records)
        if kind is TraceEventKind.ACTUATION_FRAME and payload.inhibit_reason is InhibitReason.SAFETY
    )
    stop_index = next(
        index
        for index, (kind, payload, _ts) in enumerate(records)
        if kind is TraceEventKind.SAFETY_EVENT and payload.event is SafetyEventType.STOP
    )
    reset_index = next(
        index
        for index, (kind, payload, _ts) in enumerate(records)
        if kind is TraceEventKind.SAFETY_EVENT and payload.event is SafetyEventType.SCHEDULER_RESET
    )
    frames = [payload for kind, payload, _ts in records if kind is TraceEventKind.ACTUATION_FRAME]
    assert stop_index < reset_index < frame_index
    assert frames and all(payload.result_revision > 0 for payload in frames)
    assert frames[-1].result_revision == 1
    assert frames[-1].requested_auger_duty == 0.1
    assert frames[-1].inhibit_reason is InhibitReason.SAFETY


@pytest.mark.parametrize("event", ["stop", "error", "temperature_guard"])
def test_framed_guard_events_reset_without_restoring_credit(hold_cycle, event):
    runner = FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE).script([_output(1, 0.9)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.on_tick(2.0, 200.0, _status(hold))
    hold.on_tick(22.0, 200.0, _status(hold))

    hold._on_safety_event(event, 23.0)
    decision = hold._pulse_scheduler.advance(0.9, 24.0, False)

    assert decision.reset_reason is not None
    assert decision.credit_s < 2.0
    assert hold.grill.get_output_status()["auger"] is False


def test_framed_lid_inhibit_discards_credit_and_preempts_auger(hold_cycle):
    runner = FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE).script([_output(1, 0.9)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.state.target_temp_achieved = True
    hold.settings["cycle_data"]["LidOpenDetectEnabled"] = True

    hold.on_tick(2.0, 100.0, _status(hold))

    assert hold.state.lid.open_detected is True
    assert hold.grill.get_output_status()["auger"] is False
    assert hold._pulse_scheduler.advance(0.9, 3.0, False).reset_reason is not None


def test_fallback_uses_runner_mode_and_restores_fixed_cycle(hold_cycle):
    class FallbackRunner(FakeControllerRunner):
        def reconfigure(self, settings, control, logger=None):
            self._actuation_mode = ActuationMode.FIXED_CYCLE
            self._controller_type = ControllerType.PID
            return super().reconfigure(settings, control, logger=logger)

    runner = FallbackRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE).script([_output(1, 0.5)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.control["controller_update"] = True

    hold.on_tick(2.0, 200.0, _status(hold))

    assert hold._actuation_mode is ActuationMode.FIXED_CYCLE
    assert hold._pulse_scheduler is None
    assert hold.state.cycle.cycle_time == hold.settings["cycle_data"]["HoldCycleTime"]


def test_deferred_mpc_to_pid_swap_adopts_only_after_runner_generation_changes(hold_cycle):
    class DeferredRunner(FakeControllerRunner):
        def reconfigure(self, settings, control, logger=None):
            self.pending = True
            return "Active"

        def complete_swap(self):
            self._actuation_mode = ActuationMode.FIXED_CYCLE
            self._controller_type = ControllerType.PID
            self._commands_fan = False
            self._configuration_revision += 1

    runner = DeferredRunner(
        period=1.0,
        commands_fan=True,
        actuation_mode=ActuationMode.FRAMED_PULSE,
        controller_type=ControllerType.MPC,
    ).script([_output(1, 0.5)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.ctx.store._settings["controller"]["selected"] = "pid"
    hold.control["controller_update"] = True
    status = {"auger": False, "fan": False, "igniter": False, "power": True, "pwm": 100}

    hold.on_tick(2.0, 200.0, status)
    assert hold._actuation_mode is ActuationMode.FRAMED_PULSE
    assert hold._pulse_scheduler is not None
    assert hold._controller_name == "mpc"

    runner.complete_swap()
    hold.on_tick(4.0, 200.0, status)
    assert hold._actuation_mode is ActuationMode.FIXED_CYCLE
    assert hold._pulse_scheduler is None
    assert hold._controller_name == "pid"
    adopted_revision = hold._runner_configuration_revision
    hold.on_tick(6.0, 200.0, status)
    assert hold._runner_configuration_revision == adopted_revision


def test_framed_missed_frames_are_recorded_as_skipped_without_catchup(hold_cycle, monkeypatch):
    runner = FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE).script([_output(1, 0.1)])
    hold = hold_cycle(runner, controller="mpc")
    frames = []
    monkeypatch.setattr(hold, "_trace_record", lambda kind, payload, ts: frames.append((kind, payload)) or True)
    hold.setup()
    hold.on_tick(2.0, 200.0, _status(hold))
    hold.on_tick(62.0, 200.0, _status(hold))

    skipped = [payload for kind, payload in frames if kind is TraceEventKind.ACTUATION_FRAME and payload.skipped]
    assert skipped and all(payload.scheduled_on_seconds == 0.0 for payload in skipped)


def test_framed_accepts_auger_and_fan_only_as_one_latest_allocation(hold_cycle):
    first = _output(1, 0.1, fan_duty=25.0)
    replacement = _output(2, 0.9, fan_duty=75.0)
    runner = FakeControllerRunner(period=1.0, commands_fan=True, actuation_mode=ActuationMode.FRAMED_PULSE).script(
        [first, replacement]
    )
    hold = hold_cycle(runner, controller="mpc")
    hold.settings["platform"]["dc_fan"] = True
    hold.control["pwm_control"] = True
    hold.setup()

    hold.on_tick(2.0, 200.0, _status(hold))
    hold.on_tick(4.0, 200.0, _status(hold))

    assert hold.state.controller.pulse_requested_duty == 0.9
    assert hold.state.controller.pulse_requested_fan_duty == 75.0
    assert hold.state.controller.fan_duty == 75.0


def test_fixed_cycle_fan_assist_is_unchanged(hold_cycle):
    runner = FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FIXED_CYCLE).script([_output(1, 0.1)])
    hold = hold_cycle(
        runner,
        controller="pid",
        cycle_data_extra={"u_min": 0.3, "FanPidEnabled": True},
    )
    hold.control["pwm_control"] = False
    hold.setup()

    hold.on_tick(2.0, 200.0, _status(hold))
    assert hold.state.fan.assist is True


@pytest.mark.parametrize("actual_on", [False, True])
def test_framed_reset_accounts_observed_output_before_safety_or_manual_preemption(hold_cycle, monkeypatch, actual_on):
    runner = FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE).script([_output(1, 0.1)])
    hold = hold_cycle(runner, controller="mpc")
    records = []
    monkeypatch.setattr(hold, "_trace_record", lambda kind, payload, ts: records.append((kind, payload)) or True)
    hold.setup()
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


def test_framed_reset_keeps_cumulative_delivery_baselines_for_feedback_and_metrics(hold_cycle):
    runner = FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE)
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.state.metrics = {"id": "cook-reset-accounting", "augerontime": 0.0}
    hold.state.controller.pulse_requested_duty = 0.1
    runner.applied.clear()

    hold._advance_framed_pulse(20.0, False)
    hold._advance_framed_pulse(22.0, True)
    before_reset = hold._advance_framed_pulse(24.0, False)
    assert before_reset.delivered_on_s == 2.0
    hold._reset_framed_pulse(PulseResetReason.SAFETY, 24.0, InhibitReason.SAFETY)

    hold._record_pulse_delivery(12.0)
    hold._report_framed_feedback(44.0, 12.0)

    assert hold.state.metrics["augerontime"] == 12.0
    assert runner.applied[-1].ratio == 0.5
