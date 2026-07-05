import math
import random
import statistics

from probes.kalman import TempKalman


def _feed_constant(kf, value, steps, dt=0.05, start=0.0):
	t = start
	out = None
	for _ in range(steps):
		t += dt
		out = kf.update(value, now=t)
	return out, t


def test_converges_to_constant():
	kf = TempKalman(units='F')
	out, _ = _feed_constant(kf, 250.0, steps=60)
	assert abs(out - 250.0) < 0.5


def test_first_reading_returns_immediately():
	kf = TempKalman(units='F')
	out = kf.update(137.0, now=0.05)
	assert out == 137.0


def test_reduces_noise_on_constant():
	rng = random.Random(0)
	kf = TempKalman(units='F')
	ins, outs = [], []
	t = 0.0
	for i in range(300):
		t += 0.05
		z = 250.0 + rng.gauss(0, 2.0)
		o = kf.update(z, now=t)
		if i >= 20:
			ins.append(z)
			outs.append(o)
	assert statistics.pstdev(outs) < statistics.pstdev(ins)


def test_tracks_ramp_with_low_lag():
	kf = TempKalman(units='F')
	rate, dt = 1.5, 0.05
	t, temp, out = 0.0, 100.0, None
	for _ in range(400):
		temp += rate * dt
		t += dt
		out = kf.update(temp, now=t)
	lag = (temp - out) / rate
	assert -0.2 < lag < 0.2


def test_irregular_dt_stays_stable():
	rng = random.Random(1)
	kf = TempKalman(units='F')
	t, out = 0.0, None
	for _ in range(200):
		t += 0.05 + rng.uniform(-0.02, 0.05)
		out = kf.update(250.0, now=t)
	assert math.isfinite(out)
	assert abs(out - 250.0) < 1.0


def test_celsius_returns_one_decimal_and_scaled_tuning():
	kf = TempKalman(units='C')
	assert kf.R == 1.25
	out = kf.update(100.0, now=0.05)
	assert isinstance(out, float)
	assert out == 100.0


def test_rejects_single_spike():
	kf = TempKalman(units='F')
	_, t = _feed_constant(kf, 250.0, steps=40)
	before = kf.update(250.0, now=t + 0.05)
	after = kf.update(900.0, now=t + 0.10)
	assert abs(after - before) < 1.0


def test_none_reading_returns_none():
	kf = TempKalman(units='F')
	kf.update(250.0, now=0.05)
	assert kf.update(None) is None


def test_resets_after_three_nones():
	kf = TempKalman(units='F')
	_, t = _feed_constant(kf, 250.0, steps=40)
	assert kf.update(None) is None
	assert kf.update(None) is None
	assert kf.update(None) is None
	# After reset the next valid reading re-initializes and is returned as-is.
	assert kf.update(100.0, now=t + 0.05) == 100.0


def test_single_none_keeps_state_warm():
	kf = TempKalman(units='F')
	out, t = _feed_constant(kf, 250.0, steps=40)
	assert kf.update(None) is None
	resumed = kf.update(250.0, now=t + 0.10)
	# One dropped read must not force a re-init; estimate stays near 250.
	assert abs(resumed - 250.0) < 1.0
