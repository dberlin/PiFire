from controller.runtime.clock import ManualClock, RealClock, Clock


def test_manual_clock_starts_at_zero_and_sleep_advances():
	c = ManualClock()
	assert c.now() == 0.0
	c.sleep(0.5)
	assert c.now() == 0.5


def test_manual_clock_advance():
	c = ManualClock(start=100.0)
	c.advance(3.0)
	assert c.now() == 103.0


def test_real_clock_is_a_clock():
	assert isinstance(RealClock(), Clock)
