from controller.runtime.logic.fan import (
    clamp_duty,
    controller_fan_authority,
    smoke_plus_max_ratio,
)


def test_clamp_duty_below_min_raises_to_min():
    pwm_settings = {"min_duty_cycle": 20, "max_duty_cycle": 100}
    assert clamp_duty(10, pwm_settings) == 20


def test_clamp_duty_above_max_lowers_to_max():
    pwm_settings = {"min_duty_cycle": 20, "max_duty_cycle": 100}
    assert clamp_duty(150, pwm_settings) == 100


def test_clamp_duty_within_range_unchanged():
    pwm_settings = {"min_duty_cycle": 20, "max_duty_cycle": 100}
    assert clamp_duty(50, pwm_settings) == 50


def test_clamp_duty_order_is_max_then_min():
    # If min_duty_cycle > max_duty_cycle (degenerate config), the max-then-min
    # order means the final min() clamp wins, matching control.py's order.
    pwm_settings = {"min_duty_cycle": 90, "max_duty_cycle": 10}
    # duty=5 -> max(5, 90) = 90 -> min(90, 10) = 10
    assert clamp_duty(5, pwm_settings) == 10


def test_smoke_plus_max_ratio_s_plus_true_returns_on_over_total():
    smoke_plus_settings = {"on_time": 30, "off_time": 90}
    # total = 120, ratio = 30/120 = 0.25
    assert smoke_plus_max_ratio(smoke_plus_settings, True) == 0.25


def test_smoke_plus_max_ratio_s_plus_false_returns_one():
    smoke_plus_settings = {"on_time": 30, "off_time": 90}
    assert smoke_plus_max_ratio(smoke_plus_settings, False) == 1


def _s(dc_fan):
    return {"platform": {"dc_fan": dc_fan}}


def test_authority_requires_both_a_dc_fan_and_pwm_control():
    assert controller_fan_authority(_s(True), {"pwm_control": True}) is True


def test_authority_is_denied_when_pwm_control_is_off():
    assert controller_fan_authority(_s(True), {"pwm_control": False}) is False


def test_authority_is_denied_on_an_ac_fan_build():
    assert controller_fan_authority(_s(False), {"pwm_control": True}) is False
