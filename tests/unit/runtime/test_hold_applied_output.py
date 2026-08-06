"""Hold reports observed framed-pulse output and explicit overrides."""

from controller.applied_output import OutputSource
from tests.fakes.runner import FakeControllerRunner
from tests.unit.runtime.conftest import _off


def test_setup_seeds_zero_until_the_first_pulse_frame(hold_cycle):
    runner = FakeControllerRunner()
    hold = hold_cycle(runner)

    hold.setup()

    assert [applied.source for applied in runner.applied] == [OutputSource.SEED]
    assert runner.applied[0].ratio == 0.0


def test_manual_auger_on_reports_full_duty(hold_cycle):
    runner = FakeControllerRunner(period=999)
    hold = hold_cycle(runner)
    hold.setup()
    hold._last_now = 100.0
    runner.applied.clear()

    hold._on_manual_output("auger", True)

    (applied,) = runner.applied
    assert applied.source is OutputSource.MANUAL_OVERRIDE
    assert applied.ratio == 1.0
    assert applied.controller_commanded is False
    assert applied.timestamp == 100.0


def test_manual_auger_off_reports_zero(hold_cycle):
    runner = FakeControllerRunner(period=999)
    hold = hold_cycle(runner)
    hold.setup()
    hold._last_now = 100.0
    runner.applied.clear()

    hold._on_manual_output("auger", False)

    assert runner.applied[0].ratio == 0.0


def test_lid_open_reports_zero_with_the_pulse_request(hold_cycle):
    runner = FakeControllerRunner(period=999)
    hold = hold_cycle(runner)
    hold.setup()
    hold.state.target_temp_achieved = True
    hold.settings["cycle_data"]["LidOpenDetectEnabled"] = True
    hold.control["primary_setpoint"] = 225.0
    hold.state.controller.output = 0.5
    runner.applied.clear()

    hold.on_tick(now=100.0, ptemp=100.0, current_output_status=_off())

    (applied,) = [applied for applied in runner.applied if applied.source is OutputSource.LID_OPEN]
    assert applied.ratio == 0.0
    assert applied.requested == 0.5
    assert applied.timestamp == 100.0


def test_manual_output_does_not_report_other_actuators(hold_cycle):
    runner = FakeControllerRunner(period=999)
    hold = hold_cycle(runner)
    hold.setup()
    runner.applied.clear()

    for name in ("fan", "igniter", "power", "pwm"):
        hold._on_manual_output(name, True)

    assert runner.applied == []
