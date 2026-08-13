"""A controller may only own the fan when its command can reach the hardware."""

import logging

from controller.mpc_calibration import CalibrationCommand

from common.control_trace import ActuationMode

from tests.fakes.runner import FakeControllerRunner


def _grant(hold, *, dc_fan, pwm_control):
    hold.settings["platform"]["dc_fan"] = dc_fan
    hold.control["pwm_control"] = pwm_control


def test_ownership_is_granted_when_the_command_can_reach_the_fan(hold_cycle):
    runner = FakeControllerRunner(period=0.01, commands_fan=True, actuation_mode=ActuationMode.FRAMED_PULSE)
    hold = hold_cycle(runner, controller="mpc")
    _grant(hold, dc_fan=True, pwm_control=True)
    hold.setup()
    assert hold.state.controller.controls_fan is True

    runner.request_calibration(CalibrationCommand("start", 1, 20.0, "configured", True, True))
    assert hold.state.controller.controls_fan is True


def test_ownership_is_refused_when_pwm_control_is_off(hold_cycle):
    runner = FakeControllerRunner(period=0.01, commands_fan=True, actuation_mode=ActuationMode.FRAMED_PULSE)
    hold = hold_cycle(runner, controller="mpc")
    _grant(hold, dc_fan=True, pwm_control=False)
    hold.setup()
    # False, not True: the claim would suppress the temp-profile and fan-assist
    # paths, leaving nothing at all able to move the fan.
    assert hold.state.controller.controls_fan is False


def test_refusing_ownership_logs_an_error_naming_the_controller(hold_cycle, caplog):
    runner = FakeControllerRunner(period=0.01, commands_fan=True, actuation_mode=ActuationMode.FRAMED_PULSE)
    hold = hold_cycle(runner, controller="mpc")
    _grant(hold, dc_fan=True, pwm_control=False)
    with caplog.at_level(logging.ERROR):
        hold.setup()
    errors = [r.message for r in caplog.records if r.levelno == logging.ERROR]
    assert any("mpc" in m and "PWM" in m for m in errors)


def test_a_controller_that_does_not_command_the_fan_logs_nothing(hold_cycle, caplog):
    runner = FakeControllerRunner(period=0.01, commands_fan=False)
    hold = hold_cycle(runner, controller="pid_sp")
    _grant(hold, dc_fan=True, pwm_control=False)
    with caplog.at_level(logging.ERROR):
        hold.setup()
    assert hold.state.controller.controls_fan is False
    assert [r for r in caplog.records if r.levelno == logging.ERROR] == []


def test_hold_without_a_controller_remains_safe_and_reports_no_pulse(
    hold_cycle,
) -> None:
    hold = hold_cycle(None, controller="pid_sp")

    hold.setup()
    hold._on_safety_event("temperature_guard", 1.0)
    status_after_event = hold.status_fragment()
    output_after_event = hold.grill.get_output_status()
    hold.teardown(200.0)

    assert "pulse" not in status_after_event
    assert output_after_event["auger"] is False
    assert output_after_event["power"] is True
    assert hold.state.controller.controls_fan is False
    assert hold.grill.get_output_status() == {
        "dc_fan": False,
        "auger": False,
        "fan": False,
        "igniter": False,
        "power": False,
        "pwm": 100,
        "frequency": 100,
    }


def test_temperature_profile_updates_unowned_dc_fan_duty(
    hold_cycle,
) -> None:
    runner = FakeControllerRunner(period=999, commands_fan=False)
    hold = hold_cycle(runner, controller="pid_sp")
    _grant(hold, dc_fan=True, pwm_control=True)
    hold.settings["pwm"]["temp_range_list"] = [100.0]
    hold.settings["pwm"]["profiles"] = [{"duty_cycle": 42}]
    hold.setup()
    hold.control["duty_cycle"] = 17

    hold.on_tick(100.0, 220.0, hold.grill.get_output_status())

    assert hold.control["duty_cycle"] == 42
    assert hold.state.fan.update_time == 100.0


def test_empty_temperature_profile_preserves_existing_dc_fan_duty(
    hold_cycle,
) -> None:
    runner = FakeControllerRunner(period=999, commands_fan=False)
    hold = hold_cycle(runner, controller="pid_sp")
    _grant(hold, dc_fan=True, pwm_control=True)
    hold.settings["pwm"]["temp_range_list"] = []
    hold.settings["pwm"]["profiles"] = []
    hold.setup()
    hold.control["duty_cycle"] = 17

    hold.on_tick(100.0, 220.0, hold.grill.get_output_status())

    assert hold.control["duty_cycle"] == 17
    assert hold.state.fan.update_time == 100.0
