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
