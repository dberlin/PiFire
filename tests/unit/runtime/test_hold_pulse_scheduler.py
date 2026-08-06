import pytest

from common.control_trace import ActuationMode, ControllerType, InhibitReason, SafetyEventType, TraceEventKind
from controller.runtime.logic.pulse import PulseResetReason
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

    assert hold._pulse_scheduler is not None
    assert hold.grill.get_output_status()["auger"] is False
    assert hold.state.cycle.cycle_time == 0.0


def test_hold_rejects_runner_without_framed_pulse_actuation(hold_cycle):
    hold = hold_cycle(FakeControllerRunner(actuation_mode=ActuationMode.FIXED_CYCLE), controller="pid")

    with pytest.raises(ValueError, match="framed pulse"):
        hold.setup()


def test_low_duty_accumulates_to_one_quantum_without_fixed_cycle_floor(hold_cycle):
    runner = FakeControllerRunner(period=1.0).script([_output(1, 0.05)])
    hold = hold_cycle(runner, controller="mpc", cycle_data_extra={"u_min": 0.9, "HoldCycleTime": 99})

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


def test_reconfiguration_replaces_scheduler_and_discards_prior_credit(hold_cycle):
    runner = FakeControllerRunner(period=1.0).script([_output(1, 0.1)])
    hold = hold_cycle(runner, controller="pid")
    hold.setup()
    hold.on_tick(2.0, 200.0, _status(hold))
    hold.on_tick(22.0, 200.0, _status(hold))
    original_scheduler = hold._pulse_scheduler
    hold.control["controller_update"] = True

    hold.on_tick(24.0, 200.0, _status(hold))

    assert hold._pulse_scheduler is not original_scheduler
    assert hold._pulse_scheduler.advance(0.1, 42.0, False).credit_s < 2.0


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
    decision = hold._pulse_scheduler.advance(0.9, 24.0, False)

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
    assert hold._pulse_scheduler.advance(0.9, 3.0, False).reset_reason is not None


def test_deferred_mpc_to_pid_swap_keeps_one_framed_scheduler(hold_cycle):
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
    hold.control["controller_update"] = True
    status = {"auger": False, "fan": False, "igniter": False, "power": True, "pwm": 100}

    hold.on_tick(2.0, 200.0, status)
    assert hold._pulse_scheduler is not None
    assert hold._controller_name == "mpc"

    runner.complete_swap()
    hold.on_tick(4.0, 200.0, status)
    assert hold._pulse_scheduler is not None
    assert hold.state.cycle.cycle_time == 0.0
    assert hold._controller_name == "pid"


def test_missed_frames_are_recorded_as_skipped_without_catchup(hold_cycle, monkeypatch):
    runner = FakeControllerRunner(period=1.0).script([_output(1, 0.1)])
    hold = hold_cycle(runner, controller="mpc")
    frames = []
    monkeypatch.setattr(hold, "_trace_record", lambda kind, payload, ts: frames.append((kind, payload)) or True)
    hold.setup()
    hold.on_tick(2.0, 200.0, _status(hold))
    hold.on_tick(62.0, 200.0, _status(hold))

    skipped = [payload for kind, payload in frames if kind is TraceEventKind.ACTUATION_FRAME and payload.skipped]
    assert skipped and all(payload.scheduled_on_seconds == 0.0 for payload in skipped)


def test_auger_and_fan_adopt_together_from_one_result_revision(hold_cycle):
    first = _output(1, 0.1, fan_duty=25.0)
    replacement = _output(2, 0.9, fan_duty=75.0)
    runner = FakeControllerRunner(period=1.0, commands_fan=True).script([first, replacement])
    hold = hold_cycle(runner, controller="mpc")
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


def test_reset_keeps_cumulative_delivery_baselines_for_feedback_and_metrics(hold_cycle):
    runner = FakeControllerRunner(period=1.0)
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
