"""Pure smart-start profile selection/cycle calculations used by StartupMode
and SmokeMode (controller/runtime/modes/) to pick a temperature-range profile
and derive its auger on/off cycle timing. No I/O."""

from controller.runtime.logic.cycle import CycleTimes


def select_profile(startup_temp, temp_range_list):
	for i in range(len(temp_range_list)):
		if startup_temp < temp_range_list[i]:
			return i
	return len(temp_range_list)


def profile_cycle(profile, cycle_data):
	on_time = profile['augerontime']
	off_time = cycle_data['SmokeOffCycleTime'] + profile['p_mode'] * 10
	cycle_time = on_time + off_time
	cycle_ratio = on_time / cycle_time
	startup_timer = profile['startuptime']
	metrics_bits = {'p_mode': profile['p_mode'], 'auger_cycle_time': profile['augerontime']}
	return CycleTimes(on_time, off_time, cycle_time, cycle_ratio), startup_timer, metrics_bits
