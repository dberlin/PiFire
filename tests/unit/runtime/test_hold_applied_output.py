"""Hold reports observed framed-pulse output and explicit overrides."""

from dataclasses import replace

from controller.applied_output import OutputSource
from tests.fakes.runner import FakeControllerRunner
from tests.unit.runtime.conftest import _off, _output


def _completed_output(ratio):
    return replace(
        _output(ratio),
        revision=1,
        solve_start_monotonic=1.0,
        solve_end_monotonic=1.125,
        solve_duration_seconds=0.125,
        completed_wall_time=1.125,
    )


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


def test_manual_takeover_resets_the_active_frame_before_manual_feedback(hold_cycle, monkeypatch):
    runner = FakeControllerRunner(period=1.0).script([_completed_output(0.9)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    hold._last_now = 3.0
    hold._last_ptemp = 200.0
    runner.applied.clear()
    events = []
    set_output = runner.set_output
    observe_frame = runner.observe_frame

    def record_output(applied):
        if applied.source is OutputSource.MANUAL_OVERRIDE:
            events.append("manual-feedback")
        set_output(applied)

    def record_observation(observation):
        if observation.reset:
            events.append("frame-reset")
        return observe_frame(observation)

    monkeypatch.setattr(runner, "set_output", record_output)
    monkeypatch.setattr(runner, "observe_frame", record_observation)

    hold._on_manual_output("auger", False)

    assert events == ["frame-reset", "manual-feedback"]


def test_manual_release_reseeds_before_fresh_controller_authority(hold_cycle, monkeypatch):
    runner = FakeControllerRunner(period=1.0).script([_completed_output(0.9)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold._last_now = 1.0
    hold._last_ptemp = 200.0
    hold._on_manual_output("auger", True)
    hold.state.manual_override["auger"] = 1.0
    events = []
    set_output = runner.set_output
    latest = runner.latest
    auger_on = hold.grill.auger_on

    def record_output(applied):
        events.append(("feedback", applied.source))
        set_output(applied)

    def record_latest():
        events.append(("runner", "latest"))
        return latest()

    def record_auger_on():
        events.append(("hardware", "auger-on"))
        auger_on()

    monkeypatch.setattr(runner, "set_output", record_output)
    monkeypatch.setattr(runner, "latest", record_latest)
    monkeypatch.setattr(hold.grill, "auger_on", record_auger_on)

    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())

    seed = ("feedback", OutputSource.SEED)
    result = ("runner", "latest")
    authority = ("hardware", "auger-on")
    assert events.index(seed) < events.index(result) < events.index(authority)


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
