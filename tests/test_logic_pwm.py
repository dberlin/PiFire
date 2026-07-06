from controller.runtime.logic.pwm import hold_duty_cycle, ramp_params


def test_hold_duty_cycle_over_setpoint_returns_min():
	# ptemp > setpoint (strict >) short-circuits to min_duty_cycle regardless
	# of temp_range_list/profiles.
	pwm_settings = {
		'min_duty_cycle': 20,
		'max_duty_cycle': 100,
		'temp_range_list': [5, 15, 30],
		'profiles': [{'duty_cycle': 50}, {'duty_cycle': 70}, {'duty_cycle': 90}],
	}
	assert hold_duty_cycle(setpoint=225, ptemp=230, pwm_settings=pwm_settings) == 20


def test_hold_duty_cycle_at_setpoint_uses_profile_zero():
	# ptemp == setpoint means (setpoint - ptemp) == 0, which is <= the first
	# range entry, so profile 0 is used (not the over-setpoint branch, since
	# that requires strict >).
	pwm_settings = {
		'min_duty_cycle': 20,
		'max_duty_cycle': 100,
		'temp_range_list': [5, 15, 30],
		'profiles': [{'duty_cycle': 50}, {'duty_cycle': 70}, {'duty_cycle': 90}],
	}
	assert hold_duty_cycle(setpoint=225, ptemp=225, pwm_settings=pwm_settings) == 50


def test_hold_duty_cycle_matches_early_profile():
	# setpoint - ptemp = 10, which is > temp_range_list[0]=5 but <= [1]=15,
	# so profile index 1 (duty_cycle=70) is used.
	pwm_settings = {
		'min_duty_cycle': 20,
		'max_duty_cycle': 100,
		'temp_range_list': [5, 15, 30],
		'profiles': [{'duty_cycle': 50}, {'duty_cycle': 70}, {'duty_cycle': 90}],
	}
	assert hold_duty_cycle(setpoint=225, ptemp=215, pwm_settings=pwm_settings) == 70


def test_hold_duty_cycle_boundary_uses_le_match():
	# setpoint - ptemp == temp_range_list[i] exactly must match index i
	# (uses <=, not <).
	pwm_settings = {
		'min_duty_cycle': 20,
		'max_duty_cycle': 100,
		'temp_range_list': [5, 15, 30],
		'profiles': [{'duty_cycle': 50}, {'duty_cycle': 70}, {'duty_cycle': 90}],
	}
	assert hold_duty_cycle(setpoint=225, ptemp=210, pwm_settings=pwm_settings) == 70


def test_hold_duty_cycle_clamps_below_min():
	# Matched profile's duty_cycle (10) is below min_duty_cycle (20), so the
	# clamp raises it to min_duty_cycle. Clamp order is max-then-min, so the
	# min clamp must win here.
	pwm_settings = {
		'min_duty_cycle': 20,
		'max_duty_cycle': 100,
		'temp_range_list': [5, 15, 30],
		'profiles': [{'duty_cycle': 10}, {'duty_cycle': 70}, {'duty_cycle': 90}],
	}
	assert hold_duty_cycle(setpoint=225, ptemp=225, pwm_settings=pwm_settings) == 20


def test_hold_duty_cycle_clamps_above_max():
	# Matched profile's duty_cycle (150) is above max_duty_cycle (100), so
	# the clamp lowers it to max_duty_cycle.
	pwm_settings = {
		'min_duty_cycle': 20,
		'max_duty_cycle': 100,
		'temp_range_list': [5, 15, 30],
		'profiles': [{'duty_cycle': 150}, {'duty_cycle': 70}, {'duty_cycle': 90}],
	}
	assert hold_duty_cycle(setpoint=225, ptemp=225, pwm_settings=pwm_settings) == 100


def test_hold_duty_cycle_fallthrough_beyond_all_ranges_returns_max():
	# setpoint - ptemp = 50, larger than every entry in temp_range_list, so
	# the loop falls through all comparisons and the last-index fallthrough
	# branch returns max_duty_cycle directly (bypassing profiles/clamps).
	pwm_settings = {
		'min_duty_cycle': 20,
		'max_duty_cycle': 100,
		'temp_range_list': [5, 15, 30],
		'profiles': [{'duty_cycle': 50}, {'duty_cycle': 70}, {'duty_cycle': 90}],
	}
	assert hold_duty_cycle(setpoint=225, ptemp=175, pwm_settings=pwm_settings) == 100


def test_hold_duty_cycle_last_boundary_uses_profile_not_fallthrough():
	# setpoint - ptemp == temp_range_list[-1] exactly still matches via <=
	# on the last iteration, using that profile's clamped duty_cycle rather
	# than the fallthrough max_duty_cycle.
	pwm_settings = {
		'min_duty_cycle': 20,
		'max_duty_cycle': 100,
		'temp_range_list': [5, 15, 30],
		'profiles': [{'duty_cycle': 50}, {'duty_cycle': 70}, {'duty_cycle': 90}],
	}
	assert hold_duty_cycle(setpoint=225, ptemp=195, pwm_settings=pwm_settings) == 90


def test_hold_duty_cycle_empty_temp_range_list_returns_none():
	# Documented behavior: range(len([])) is empty, so the for-loop body
	# never executes and there is no explicit return in the else branch.
	# The function falls off the end and implicitly returns None. This
	# mirrors control.py, which would leave control['duty_cycle'] untouched
	# in this case (no assignment happens in the loop).
	pwm_settings = {'min_duty_cycle': 20, 'max_duty_cycle': 100, 'temp_range_list': [], 'profiles': []}
	assert hold_duty_cycle(setpoint=225, ptemp=225, pwm_settings=pwm_settings) is None


def test_ramp_params_known_values():
	smoke_plus = {'on_time': 30, 'off_time': 60, 'duty_cycle': 50}
	pwm_settings = {'min_duty_cycle': 20, 'max_duty_cycle': 100}

	result = ramp_params(smoke_plus, pwm_settings)

	# on_time = 30
	# min_duty_cycle = 20
	# max_ramp = 100 * (50 / 100) = 50.0
	assert result == (30, 20, 50.0)


def test_ramp_params_returns_tuple_of_three():
	smoke_plus = {'on_time': 45, 'off_time': 90, 'duty_cycle': 80}
	pwm_settings = {'min_duty_cycle': 10, 'max_duty_cycle': 90}

	result = ramp_params(smoke_plus, pwm_settings)

	assert isinstance(result, tuple)
	assert len(result) == 3
	assert result == (45, 10, 72.0)
