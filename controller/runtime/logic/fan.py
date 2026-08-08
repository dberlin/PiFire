"""Pure fan-timing calculations and the shared start_fan() helper used by the
mode handlers (controller/runtime/modes/) for smoke-plus timing, fan
ownership, and turning the fan on (AC or duty-cycled DC). No I/O beyond the
grill_platform call in start_fan()."""


def clamp_duty(duty, pwm_settings):
    adjusted = max(duty, pwm_settings["min_duty_cycle"])
    adjusted = min(adjusted, pwm_settings["max_duty_cycle"])
    return adjusted


def controller_fan_authority(settings, control):
    """Whether a controller-issued fan duty can actually reach the hardware.

    A duty only ever lands on a DC-fan build with PWM control switched on. The
    setup-time ownership decision and the per-tick apply path both ask this one
    question so they cannot disagree: an ownership claim that the apply path
    then refuses leaves the fan driven by nobody, because the claim also
    suppresses the temperature-profile and fan-assist paths.
    """
    return bool(settings["platform"]["dc_fan"]) and bool(control["pwm_control"])


def smoke_plus_max_ratio(smoke_plus_settings, s_plus):
    if s_plus:
        total = smoke_plus_settings["on_time"] + smoke_plus_settings["off_time"]
        return smoke_plus_settings["on_time"] / total
    return 1


def start_fan(grill_platform, settings, duty_cycle=None):
    """
    Check for DC Fan and set duty cycle when turning ON otherwise turn AC fan ON normally.

    :param settings: Settings
    :param duty_cycle: Duty Cycle to set. If not provided will be set to max_duty_cycle (dc_fan only)
    """
    if settings["platform"]["dc_fan"]:
        if duty_cycle is not None:
            adjusted_dc = clamp_duty(duty_cycle, settings["pwm"])
        else:
            adjusted_dc = settings["pwm"]["max_duty_cycle"]
        grill_platform.fan_on(adjusted_dc)
    else:
        grill_platform.fan_on()
