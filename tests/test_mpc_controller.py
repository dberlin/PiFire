import time
import numpy as np
from controller.mpc import Controller

CONFIG = dict(
	n_horizon=20,
	t_step=25.0,
	control_period=1.0,
	Q_w=1.0,
	R_dQ=0.02,
	Q_min=5.0,
	Q_max=100.0,
	C_f=60.0,
	C_c=306.0,
	h_fc=2.0,
	h_amb=0.55,
	T_amb=20.0,
	fan_min_pct=40.0,
	fan_max_pct=100.0,
	enable_fan_input=True,
	est_q_temp=1e-2,
	est_q_dist=0.5,
	est_r_meas=0.04,
)
CYCLE = {'u_min': 0.1, 'u_max': 0.9, 'HoldCycleTime': 25}


def _make():
	c = Controller(dict(CONFIG), 'C', dict(CYCLE))
	c.set_target(110.0)
	return c


def test_update_returns_dict_contract():
	c = _make()
	out = c.update(100.0)
	assert isinstance(out, dict)
	assert 0.1 <= out['cycle_ratio'] <= 0.9
	assert 'fan' in out and 'duty' in out['fan']
	assert 40.0 <= out['fan']['duty'] <= 100.0


def test_below_setpoint_demands_more_than_at_setpoint():
	# settle the estimator at each measured temperature before comparing
	c = _make()
	for _ in range(5):
		cold = c.update(80.0)['cycle_ratio']
	c2 = _make()
	for _ in range(5):
		hot = c2.update(140.0)['cycle_ratio']
	assert cold > hot  # colder than target -> more auger


def test_control_period_advertised():
	assert _make().get_control_period() == 1.0


def test_fahrenheit_setpoint_converted():
	c = Controller(dict(CONFIG), 'F', dict(CYCLE))
	c.set_target(230.0)  # 230 F = 110 C
	assert abs(c._set_point_c - 110.0) < 0.6


def test_warm_solve_under_budget():
	c = _make()
	c.update(100.0)  # cold
	t0 = time.perf_counter()
	for _ in range(20):
		c.update(100.0)
	avg_ms = (time.perf_counter() - t0) / 20 * 1e3
	assert avg_ms < 200.0  # >=1 Hz with wide margin (x86 ~8 ms)
