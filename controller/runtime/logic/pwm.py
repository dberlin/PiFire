"""Pure PWM duty-cycle/ramp calculations used by HoldMode
(controller/runtime/modes/hold.py) to derive DC-fan duty cycle and ramp
parameters from probe temperature and settings. No I/O."""


def hold_duty_cycle(setpoint, ptemp, pwm_settings):
    if ptemp > setpoint:
        return pwm_settings["min_duty_cycle"]
    for temp_profile in range(len(pwm_settings["temp_range_list"])):
        if (setpoint - ptemp) <= pwm_settings["temp_range_list"][temp_profile]:
            duty = pwm_settings["profiles"][temp_profile]["duty_cycle"]
            duty = max(duty, pwm_settings["min_duty_cycle"])
            duty = min(duty, pwm_settings["max_duty_cycle"])
            return duty
        if temp_profile == len(pwm_settings["temp_range_list"]) - 1:
            return pwm_settings["max_duty_cycle"]
    # temp_range_list is empty: the loop body never runs and there is no
    # explicit return, so this implicitly returns None and the caller leaves
    # control['duty_cycle'] unchanged.


def ramp_params(smoke_plus, pwm_settings):
    on_time = smoke_plus["on_time"]
    min_duty_cycle = pwm_settings["min_duty_cycle"]
    max_ramp = pwm_settings["max_duty_cycle"] * (smoke_plus["duty_cycle"] / 100)
    return (on_time, min_duty_cycle, max_ramp)
