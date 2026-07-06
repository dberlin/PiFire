"""Pure fan-timing calculations and the shared start_fan() helper used by the
mode handlers (controller/runtime/modes/) for fan-assist/smoke-plus timing and
turning the fan on (AC or duty-cycled DC). No I/O beyond the grill_platform
call in start_fan()."""

from dataclasses import dataclass


def clamp_duty(duty, pwm_settings):
	adjusted = max(duty, pwm_settings['min_duty_cycle'])
	adjusted = min(adjusted, pwm_settings['max_duty_cycle'])
	return adjusted


def smoke_plus_max_ratio(smoke_plus_settings, s_plus):
	if s_plus:
		total = smoke_plus_settings['on_time'] + smoke_plus_settings['off_time']
		return smoke_plus_settings['on_time'] / total
	return 1


@dataclass
class FanTimes:
	on_time: float
	off_time: float
	ratio: float = None


def fan_assist_times(controller_output, total_fan_cycle, max_fan_ratio, u_min):
	adjusted = max(0, controller_output / u_min)
	ratio = adjusted * max_fan_ratio
	on_time = total_fan_cycle * ratio
	off_time = total_fan_cycle * (1 - ratio)
	return FanTimes(on_time=on_time, off_time=off_time, ratio=ratio)


def start_fan(grill_platform, settings, duty_cycle=None):
	"""
	Check for DC Fan and set duty cycle when turning ON otherwise turn AC fan ON normally.

	:param settings: Settings
	:param duty_cycle: Duty Cycle to set. If not provided will be set to max_duty_cycle (dc_fan only)
	"""
	if settings['platform']['dc_fan']:
		if duty_cycle is not None:
			adjusted_dc = clamp_duty(duty_cycle, settings['pwm'])
		else:
			adjusted_dc = settings['pwm']['max_duty_cycle']
		grill_platform.fan_on(adjusted_dc)
	else:
		grill_platform.fan_on()
