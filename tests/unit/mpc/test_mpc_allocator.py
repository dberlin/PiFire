import pytest
from controller.mpc_allocator import allocate

CFG = dict(Q_min=5.0, Q_max=100.0, u_min=0.1, u_max=0.9, fan_min_pct=40.0, fan_max_pct=100.0, enable_fan=True)


def test_min_fire_maps_to_lower_bounds():
	a, f = allocate(5.0, **CFG)
	assert a == pytest.approx(0.1)
	assert f == pytest.approx(40.0)


def test_max_fire_maps_to_upper_bounds():
	a, f = allocate(100.0, **CFG)
	assert a == pytest.approx(0.9)
	assert f == pytest.approx(100.0)


def test_monotonic_and_clamped():
	a_lo, _ = allocate(-50, **CFG)
	a_hi, _ = allocate(999, **CFG)
	a_mid, _ = allocate(52.5, **CFG)
	assert a_lo == pytest.approx(0.1)  # below Q_min clamps
	assert a_hi == pytest.approx(0.9)  # above Q_max clamps
	assert 0.1 < a_mid < 0.9
	assert allocate(40, **CFG)[0] < allocate(60, **CFG)[0]  # monotonic


def test_air_tracks_fuel_constant_afr():
	# fan fraction over its range should equal auger fraction over its range
	a, f = allocate(52.5, **CFG)
	auger_frac = (a - 0.1) / (0.9 - 0.1)
	fan_frac = (f - 40.0) / (100.0 - 40.0)
	assert auger_frac == pytest.approx(fan_frac)


def test_fan_disabled_returns_none():
	cfg = dict(CFG)
	cfg['enable_fan'] = False
	a, f = allocate(60, **cfg)
	assert f is None
	assert 0.1 < a < 0.9
