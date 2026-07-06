"""Pure fan-timing calculations extracted from control.py. No I/O."""

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
