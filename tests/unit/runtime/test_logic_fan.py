from controller.runtime.logic.fan import FanTimes, clamp_duty, fan_assist_times, smoke_plus_max_ratio


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


def test_fan_assist_times_negative_controller_output_floors_to_zero():
    # controller_output negative -> adjusted floored at 0 -> ratio 0 -> on_time 0
    result = fan_assist_times(controller_output=-5, total_fan_cycle=60, max_fan_ratio=1, u_min=10)
    assert result.on_time == 0
    assert result.off_time == 60


def test_fan_assist_times_known_positive_output():
    # adjusted = 5/10 = 0.5, ratio = 0.5 * 0.8 = 0.4
    # on_time = 60 * 0.4 = 24.0, off_time = 60 * 0.6 = 36.0
    result = fan_assist_times(controller_output=5, total_fan_cycle=60, max_fan_ratio=0.8, u_min=10)
    assert result.on_time == 24.0
    assert result.off_time == 36.0


def test_fan_assist_times_output_equals_u_min_with_full_ratio_is_100_percent_fan():
    # adjusted = 10/10 = 1, ratio = 1 * 1 = 1
    # on_time = total_fan_cycle, off_time = 0
    result = fan_assist_times(controller_output=10, total_fan_cycle=45, max_fan_ratio=1, u_min=10)
    assert result.on_time == 45
    assert result.off_time == 0


def test_fan_times_is_a_dataclass_instance():
    result = fan_assist_times(controller_output=10, total_fan_cycle=45, max_fan_ratio=1, u_min=10)
    assert isinstance(result, FanTimes)
