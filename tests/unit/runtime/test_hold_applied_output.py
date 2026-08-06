"""Hold reports the duty that actually reached the auger, at every site where it
diverges from the controller's request."""

from common.control_trace import ActuationMode
from controller.applied_output import OutputSource
from tests.fakes.runner import FakeControllerRunner
from tests.unit.runtime.conftest import _off, _output


def test_setup_seeds_the_initial_ratio(hold_cycle):
    runner = FakeControllerRunner(period=0.01, actuation_mode=ActuationMode.FIXED_CYCLE)
    hold = hold_cycle(runner)
    hold.setup()
    assert [a.source for a in runner.applied] == [OutputSource.SEED]
    assert runner.applied[0].ratio == hold.settings["cycle_data"]["u_min"]


def test_per_tick_reports_the_clamped_ratio_and_the_raw_request(hold_cycle):
    runner = FakeControllerRunner(period=0.0, actuation_mode=ActuationMode.FIXED_CYCLE).script([_output(1.4)])
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


def test_per_tick_reports_controller_output_when_the_auger_is_pinned_at_u_min(hold_cycle):
    runner = FakeControllerRunner(period=0.0, actuation_mode=ActuationMode.FIXED_CYCLE).script([_output(0.01)])
    hold = hold_cycle(runner)
    hold.setup()
    runner.applied.clear()
    hold.on_tick(now=100.0, ptemp=200.0, current_output_status=_off())
    (applied,) = runner.applied
    assert applied.source is OutputSource.CONTROLLER
    assert applied.ratio == hold.settings["cycle_data"]["u_min"]
    assert applied.controller_commanded is True


def test_per_tick_is_suppressed_while_a_manual_override_is_live(hold_cycle):
    runner = FakeControllerRunner(period=0.0, actuation_mode=ActuationMode.FIXED_CYCLE).script([_output(0.5)])
    hold = hold_cycle(runner)
    hold.setup()
    runner.applied.clear()
    hold.state.manual_override["auger"] = 200.0
    hold.on_tick(now=100.0, ptemp=200.0, current_output_status=_off())
    assert runner.applied == []


def test_per_tick_report_fires_once_per_control_interval_not_once_per_tick(hold_cycle):
    runner = FakeControllerRunner(period=5.0, actuation_mode=ActuationMode.FIXED_CYCLE).script([_output(0.5)])
    hold = hold_cycle(runner)
    hold.setup()
    runner.applied.clear()

    # Several ticks inside the first interval: the gate has not opened yet,
    # so nothing is reported.
    for t in (1.0, 2.0, 3.0, 4.0):
        hold.on_tick(now=t, ptemp=200.0, current_output_status=_off())
    assert runner.applied == []

    # Crossing the interval boundary opens the gate once.
    hold.on_tick(now=6.0, ptemp=200.0, current_output_status=_off())
    assert len(runner.applied) == 1

    # Several more ticks inside the new interval must not add more reports --
    # a threaded runner replays its queue on every scheduling of the worker,
    # not once per control interval, so a report hoisted out of the gate
    # would flood the model with duplicates here.
    for t in (7.0, 8.0, 9.0, 10.0):
        hold.on_tick(now=t, ptemp=200.0, current_output_status=_off())
    assert len(runner.applied) == 1

    # Crossing the next boundary opens the gate a second time.
    hold.on_tick(now=12.0, ptemp=200.0, current_output_status=_off())
    assert len(runner.applied) == 2


def test_per_tick_report_boundary_matches_the_manual_override_expiry_convention(hold_cycle):
    runner = FakeControllerRunner(period=0.0, actuation_mode=ActuationMode.FIXED_CYCLE).script([_output(0.5)])
    hold = hold_cycle(runner)
    hold.setup()
    # base.py's own expiry check (`< now`) treats an override expiring at
    # exactly `now` as still live, not yet cleared; the per-tick report must
    # honor the same boundary and stay suppressed at equality.
    hold.state.manual_override["auger"] = 100.0
    runner.applied.clear()
    hold.on_tick(now=100.0, ptemp=200.0, current_output_status=_off())
    assert runner.applied == []


def test_lid_open_detection_reports_zero(hold_cycle):
    runner = FakeControllerRunner(period=999, actuation_mode=ActuationMode.FIXED_CYCLE).script([_output(0.5)])
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
    runner = FakeControllerRunner(period=999, actuation_mode=ActuationMode.FIXED_CYCLE)
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
    runner = FakeControllerRunner(period=999, actuation_mode=ActuationMode.FIXED_CYCLE)
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
    runner = FakeControllerRunner(period=999, actuation_mode=ActuationMode.FIXED_CYCLE).script([_output(0.5)])
    hold = hold_cycle(runner)
    hold.setup()
    hold.control["lid_open_toggle"] = True
    runner.applied.clear()
    hold.on_tick(now=100.0, ptemp=225.0, current_output_status=_off())
    lid_reports = [a for a in runner.applied if a.source is OutputSource.LID_OPEN]
    assert lid_reports and lid_reports[0].ratio == 0.0


def test_lid_open_toggle_reports_manual_when_an_override_is_live(hold_cycle):
    runner = FakeControllerRunner(period=999, actuation_mode=ActuationMode.FIXED_CYCLE)
    hold = hold_cycle(runner)
    hold.setup()
    hold.control["lid_open_toggle"] = True
    hold.state.manual_override["auger"] = 200.0  # still live at now=100.0
    runner.applied.clear()
    hold.on_tick(now=100.0, ptemp=225.0, current_output_status=_off())
    (report,) = [a for a in runner.applied if a.source in (OutputSource.LID_OPEN, OutputSource.MANUAL_OVERRIDE)]
    assert report.source is OutputSource.MANUAL_OVERRIDE


def test_manual_auger_on_reports_full_duty(hold_cycle):
    runner = FakeControllerRunner(period=999, actuation_mode=ActuationMode.FIXED_CYCLE)
    hold = hold_cycle(runner)
    hold.setup()
    runner.applied.clear()
    hold._on_manual_output("auger", True)
    (applied,) = runner.applied
    assert applied.source is OutputSource.MANUAL_OVERRIDE
    assert applied.ratio == 1.0
    assert applied.controller_commanded is False


def test_manual_auger_off_reports_zero(hold_cycle):
    runner = FakeControllerRunner(period=999, actuation_mode=ActuationMode.FIXED_CYCLE)
    hold = hold_cycle(runner)
    hold.setup()
    runner.applied.clear()
    hold._on_manual_output("auger", False)
    assert runner.applied[0].ratio == 0.0


def test_manual_changes_to_other_actuators_report_nothing(hold_cycle):
    runner = FakeControllerRunner(period=999, actuation_mode=ActuationMode.FIXED_CYCLE)
    hold = hold_cycle(runner)
    hold.setup()
    runner.applied.clear()
    for name in ("fan", "igniter", "power", "pwm"):
        hold._on_manual_output(name, True)
    assert runner.applied == []


def test_manual_override_timestamp_uses_last_now_not_a_fresh_clock_read(hold_cycle):
    """`_last_now` is refreshed by `ControlMode._apply_manual_overrides` (tested
    at that level in test_control_mode_base.py); here we only pin that the hook
    itself reads `self._last_now` rather than taking its own clock reading --
    ctx.clock (a ManualClock) stays at its default 0.0 for the whole test, so a
    fresh read would report 0.0, not the tick this override actually belongs to."""
    runner = FakeControllerRunner(period=999, actuation_mode=ActuationMode.FIXED_CYCLE)
    hold = hold_cycle(runner)
    hold.setup()
    hold._last_now = 100.0  # what _apply_manual_overrides would have set this tick
    runner.applied.clear()
    hold._on_manual_output("auger", True)
    (applied,) = runner.applied
    assert applied.timestamp == 100.0


def test_per_tick_during_an_active_pause_is_lid_open(hold_cycle):
    runner = FakeControllerRunner(period=0.0, actuation_mode=ActuationMode.FIXED_CYCLE).script([_output(0.5), _output(0.5)])
    hold = hold_cycle(runner)
    hold.setup()
    hold.control["lid_open_toggle"] = True
    hold.on_tick(now=100.0, ptemp=225.0, current_output_status=_off())
    assert hold.state.lid.open_detected is True
    runner.applied.clear()
    hold.on_tick(now=150.0, ptemp=225.0, current_output_status=_off())  # past the control interval
    (applied,) = runner.applied
    assert applied.source is OutputSource.LID_OPEN
    assert applied.ratio == 0.0
    assert applied.requested == 0.5


def test_lid_open_toggle_reports_the_controllers_request(hold_cycle):
    runner = FakeControllerRunner(period=999, actuation_mode=ActuationMode.FIXED_CYCLE)
    hold = hold_cycle(runner)
    hold.setup()
    hold.control["lid_open_toggle"] = True
    hold.state.controller.output = 0.5
    runner.applied.clear()
    hold.on_tick(now=100.0, ptemp=225.0, current_output_status=_off())
    (lid_report,) = [a for a in runner.applied if a.source is OutputSource.LID_OPEN]
    assert lid_report.requested == 0.5


def test_lid_open_detection_treats_an_override_expiring_at_exactly_now_as_still_live(hold_cycle):
    runner = FakeControllerRunner(period=999, actuation_mode=ActuationMode.FIXED_CYCLE)
    hold = hold_cycle(runner)
    hold.setup()
    hold.state.target_temp_achieved = True
    hold.settings["cycle_data"]["LidOpenDetectEnabled"] = True
    hold.control["primary_setpoint"] = 225.0
    # Matches base.py's own `< now` expiry convention (an override expiring at
    # exactly `now` is still live) and the per-tick report's same boundary.
    hold.state.manual_override["auger"] = 100.0
    runner.applied.clear()
    hold.on_tick(now=100.0, ptemp=100.0, current_output_status=_off())
    (report,) = [a for a in runner.applied if a.source in (OutputSource.LID_OPEN, OutputSource.MANUAL_OVERRIDE)]
    assert report.source is OutputSource.MANUAL_OVERRIDE


def test_lid_open_toggle_treats_an_override_expiring_at_exactly_now_as_still_live(hold_cycle):
    runner = FakeControllerRunner(period=999, actuation_mode=ActuationMode.FIXED_CYCLE)
    hold = hold_cycle(runner)
    hold.setup()
    hold.control["lid_open_toggle"] = True
    hold.state.manual_override["auger"] = 100.0
    runner.applied.clear()
    hold.on_tick(now=100.0, ptemp=225.0, current_output_status=_off())
    (report,) = [a for a in runner.applied if a.source in (OutputSource.LID_OPEN, OutputSource.MANUAL_OVERRIDE)]
    assert report.source is OutputSource.MANUAL_OVERRIDE
