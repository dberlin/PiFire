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


def test_lid_open_detection_reports_zero(hold_cycle):
    runner = FakeControllerRunner(period=999).script([_output(0.5)])
    hold = hold_cycle(runner)
    hold.setup()
    hold.state.target_temp_achieved = True
    hold.settings["cycle_data"]["LidOpenDetectEnabled"] = True
    hold.control["primary_setpoint"] = 225.0
    runner.applied.clear()
    # far enough below setpoint to trip LidOpenThreshold
    hold.on_tick(now=100.0, ptemp=100.0, current_output_status=_off())
    lid_reports = [a for a in runner.applied if a.source is OutputSource.LID_OPEN]
    assert lid_reports and lid_reports[0].ratio == 0.0
    assert lid_reports[0].timestamp == 100.0


def test_lid_open_detection_reports_the_controllers_request(hold_cycle):
    runner = FakeControllerRunner(period=999)
    hold = hold_cycle(runner)
    hold.setup()
    hold.state.target_temp_achieved = True
    hold.settings["cycle_data"]["LidOpenDetectEnabled"] = True
    hold.control["primary_setpoint"] = 225.0
    hold.state.controller.output = 0.5
    runner.applied.clear()
    hold.on_tick(now=100.0, ptemp=100.0, current_output_status=_off())
    (lid_report,) = [a for a in runner.applied if a.source is OutputSource.LID_OPEN]
    assert lid_report.ratio == 0.0
    assert lid_report.requested == 0.5


def test_lid_open_detection_reports_manual_when_an_override_is_live(hold_cycle):
    runner = FakeControllerRunner(period=999)
    hold = hold_cycle(runner)
    hold.setup()
    hold.state.target_temp_achieved = True
    hold.settings["cycle_data"]["LidOpenDetectEnabled"] = True
    hold.control["primary_setpoint"] = 225.0
    hold.state.manual_override["auger"] = 200.0  # still live at now=100.0
    runner.applied.clear()
    hold.on_tick(now=100.0, ptemp=100.0, current_output_status=_off())
    (report,) = [a for a in runner.applied if a.source in (OutputSource.LID_OPEN, OutputSource.MANUAL_OVERRIDE)]
    assert report.source is OutputSource.MANUAL_OVERRIDE


def test_lid_open_toggle_reports_zero(hold_cycle):
    runner = FakeControllerRunner(period=999).script([_output(0.5)])
    hold = hold_cycle(runner)
    hold.setup()
    hold.control["lid_open_toggle"] = True
    runner.applied.clear()
    hold.on_tick(now=100.0, ptemp=225.0, current_output_status=_off())
    lid_reports = [a for a in runner.applied if a.source is OutputSource.LID_OPEN]
    assert lid_reports and lid_reports[0].ratio == 0.0


def test_lid_open_toggle_reports_manual_when_an_override_is_live(hold_cycle):
    runner = FakeControllerRunner(period=999)
    hold = hold_cycle(runner)
    hold.setup()
    hold.control["lid_open_toggle"] = True
    hold.state.manual_override["auger"] = 200.0  # still live at now=100.0
    runner.applied.clear()
    hold.on_tick(now=100.0, ptemp=225.0, current_output_status=_off())
    (report,) = [a for a in runner.applied if a.source in (OutputSource.LID_OPEN, OutputSource.MANUAL_OVERRIDE)]
    assert report.source is OutputSource.MANUAL_OVERRIDE


def test_manual_auger_on_reports_full_duty(hold_cycle):
    runner = FakeControllerRunner(period=999)
    hold = hold_cycle(runner)
    hold.setup()
    runner.applied.clear()
    hold._on_manual_output("auger", True)
    (applied,) = runner.applied
    assert applied.source is OutputSource.MANUAL_OVERRIDE
    assert applied.ratio == 1.0
    assert applied.controller_commanded is False


def test_manual_auger_off_reports_zero(hold_cycle):
    runner = FakeControllerRunner(period=999)
    hold = hold_cycle(runner)
    hold.setup()
    runner.applied.clear()
    hold._on_manual_output("auger", False)
    assert runner.applied[0].ratio == 0.0


def test_manual_changes_to_other_actuators_report_nothing(hold_cycle):
    runner = FakeControllerRunner(period=999)
    hold = hold_cycle(runner)
    hold.setup()
    runner.applied.clear()
    for name in ("fan", "igniter", "power", "pwm"):
        hold._on_manual_output(name, True)
    assert runner.applied == []


def test_manual_override_timestamp_uses_the_ticks_now_not_a_fresh_clock_read(hold_cycle):
    runner = FakeControllerRunner(period=999).script([_output(0.5)])
    hold = hold_cycle(runner)
    hold.setup()
    hold.on_tick(now=100.0, ptemp=200.0, current_output_status=_off())
    runner.applied.clear()
    hold._on_manual_output("auger", True)
    (applied,) = runner.applied
    assert applied.timestamp == 100.0
