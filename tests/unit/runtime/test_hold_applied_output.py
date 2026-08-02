"""Hold reports the duty that actually reached the auger, at every site where it
diverges from the controller's request."""

from controller.applied_output import OutputSource
from tests.fakes.runner import FakeControllerRunner
from tests.unit.runtime.conftest import _off, _output


def test_setup_seeds_the_initial_ratio(hold_cycle):
    runner = FakeControllerRunner(period=0.01)
    hold = hold_cycle(runner)
    hold.setup()
    assert [a.source for a in runner.applied] == [OutputSource.SEED]
    assert runner.applied[0].ratio == hold.settings["cycle_data"]["u_min"]


def test_per_tick_reports_the_clamped_ratio_and_the_raw_request(hold_cycle):
    runner = FakeControllerRunner(period=0.0).script([_output(1.4)])
    hold = hold_cycle(runner)
    hold.setup()
    runner.applied.clear()
    hold.on_tick(now=100.0, ptemp=200.0, current_output_status=_off())
    (applied,) = runner.applied
    assert applied.source is OutputSource.CONTROLLER
    assert applied.ratio == hold.settings["cycle_data"]["u_max"]
    assert applied.requested == 1.4
    assert applied.timestamp == 100.0
    assert applied.controller_commanded is True


def test_per_tick_reports_fan_assist_when_the_auger_is_pinned_at_u_min(hold_cycle):
    runner = FakeControllerRunner(period=0.0).script([_output(0.01)])
    hold = hold_cycle(runner, cycle_data_extra={"FanPidEnabled": True})
    hold.setup()
    runner.applied.clear()
    hold.on_tick(now=100.0, ptemp=200.0, current_output_status=_off())
    (applied,) = runner.applied
    assert applied.source is OutputSource.FAN_ASSIST
    assert applied.ratio == hold.settings["cycle_data"]["u_min"]
    assert applied.controller_commanded is False


def test_per_tick_is_suppressed_while_a_manual_override_is_live(hold_cycle):
    runner = FakeControllerRunner(period=0.0).script([_output(0.5)])
    hold = hold_cycle(runner)
    hold.setup()
    runner.applied.clear()
    hold.state.manual_override["auger"] = 200.0
    hold.on_tick(now=100.0, ptemp=200.0, current_output_status=_off())
    assert runner.applied == []
