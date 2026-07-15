import os
import numpy as np
import pytest
from controller.mpc import Controller, _DEFAULTS
from controller.grill_sim import GrillSim
from controller.mpc_net import net_path_for

ART = os.path.join(os.path.dirname(__file__), '..', '..', 'controller', 'mpc_policy_net.npz')
CYCLE = {'u_min': 0.1, 'u_max': 0.9, 'HoldCycleTime': 25}
TS = 25.0

needs_art = pytest.mark.skipif(not os.path.exists(ART), reason='net artifact not exported')

_FAN_ART = net_path_for(ART, True)


def _run(cfg, setpoint, seed=0, minutes=90):
	# This harness advances the plant TS seconds per update(), so the estimator
	# discretization (control_period) must equal TS regardless of the shipped
	# default (which is 5.0 for a faster production re-solve cadence).
	c = Controller({**cfg, 'control_period': TS}, 'C', dict(CYCLE))
	c.set_target(setpoint)
	plant = GrillSim(seed=seed)
	ts, temps = [], []
	for w in range(int(minutes * 60 / TS)):
		out = c.update(plant.measured())
		ratio = float(np.clip(out['cycle_ratio'], CYCLE['u_min'], CYCLE['u_max']))
		fan = out['fan']['duty'] if out['fan']['duty'] is not None else 100.0
		on = int(round(ratio * TS))
		for s in range(int(TS)):
			plant.step(auger_on=(s < on), fan_frac=fan / 100.0)
			ts.append(w * TS + s)
			temps.append(plant.true_Tc)
	return c, np.array(ts), np.array(temps)


@needs_art
def test_net_policy_active_and_no_nlp_built():
	cfg = dict(_DEFAULTS)
	cfg['policy'] = 'net'
	c = Controller(cfg, 'C', dict(CYCLE))
	assert c._net is not None  # net policy loaded
	assert c.mpc is None  # NLP (do_mpc/IPOPT) never built


@needs_art
def test_net_policy_holds_band_low_setpoint():
	cfg = dict(_DEFAULTS)
	cfg['policy'] = 'net'
	_, ts, temps = _run(cfg, 110.0)
	sm = ts >= 1800
	err = temps[sm] - 110.0
	assert np.sqrt(np.mean(err**2)) <= 2.5  # net matches NLP band (~1.1C)
	assert np.mean(np.abs(err) <= 2.5) >= 0.85
	assert np.max(np.abs(err)) <= 6.0
	assert abs(np.mean(err)) <= 1.5  # offset-free


@needs_art
def test_net_policy_holds_band_high_setpoint():
	# 220C (~428F): band is slightly wider at high fire, but still tight + offset-free
	cfg = dict(_DEFAULTS)
	cfg['policy'] = 'net'
	_, ts, temps = _run(cfg, 220.0)
	sm = ts >= 2400
	err = temps[sm] - 220.0
	assert np.sqrt(np.mean(err**2)) <= 3.5  # measured ~1.4C
	assert np.max(np.abs(err)) <= 8.0
	assert abs(np.mean(err)) <= 1.5


def test_net_missing_artifact_falls_back_to_nlp():
	cfg = dict(_DEFAULTS)
	cfg.update(policy='net', policy_net_path='./controller/_does_not_exist.npz')
	c = Controller(cfg, 'C', dict(CYCLE))
	c.set_target(110.0)
	assert c._net is None  # fell back
	assert c.mpc is not None  # NLP built
	out = c.update(110.0)  # still controls
	assert 'cycle_ratio' in out


@needs_art
def test_net_calibration_mismatch_falls_back_to_nlp():
	# a recalibration (different K_Q) must NOT silently use the stale net
	cfg = dict(_DEFAULTS)
	cfg['policy'] = 'net'
	cfg['K_Q'] = _DEFAULTS['K_Q'] * 1.4
	c = Controller(cfg, 'C', dict(CYCLE))
	assert c._net is None
	assert c.mpc is not None


@pytest.mark.skipif(not os.path.exists(_FAN_ART), reason='fan-on net artifact not exported')
def test_fan_on_net_is_offset_free():
	# net policy + enable_fan_input=True should hold offset-free, matching the
	# regime it was trained on (fan-on closed-loop states).
	cfg = {**_DEFAULTS, 'control_period': 25.0, 'policy': 'net', 'enable_fan_input': True}
	c = Controller(cfg, 'C', dict(CYCLE))
	assert c._net is not None and c._net.calib['enable_fan_input'] == 1
	c.set_target(190.0)
	plant = GrillSim(seed=0)
	ts, temps = [], []
	for w in range(int(75 * 60 / 25)):
		out = c.update(plant.measured())
		ratio = float(np.clip(out['cycle_ratio'], CYCLE['u_min'], CYCLE['u_max']))
		fan = out['fan']['duty'] if out['fan']['duty'] is not None else 100.0
		on = int(round(ratio * 25))
		for s in range(25):
			plant.step(auger_on=(s < on), fan_frac=fan / 100.0)
			ts.append(w * 25 + s)
			temps.append(plant.true_Tc)
	ts, temps = np.array(ts), np.array(temps)
	err = temps[ts >= 1800] - 190.0
	assert abs(np.mean(err)) <= 0.5  # offset-free (fan-on net; prior mismatch was ~0.4-0.9)
	assert np.sqrt(np.mean(err**2)) <= 2.0
