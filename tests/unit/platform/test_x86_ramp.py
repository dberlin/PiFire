def test_pwm_fan_ramp_runs_to_completion(x86_platform):
	# Use a very short ramp so the test is fast; join the thread before asserting.
	x86_platform.pwm_fan_ramp(on_time=0.1, min_duty_cycle=20, max_duty_cycle=100)
	x86_platform._ramp_thread.join(timeout=5)
	assert x86_platform._ramp_thread.is_alive() is False
	# Fan power relay enabled and final speed is the max duty cycle.
	x86_platform.relay.relay_on.assert_any_call(3)
	assert x86_platform._fan_speed_percent == 100


def test_stop_ramp_halts_thread(x86_platform):
	x86_platform.pwm_fan_ramp(on_time=10, min_duty_cycle=20, max_duty_cycle=100)
	x86_platform._stop_ramp()
	assert x86_platform._ramp_thread is None
