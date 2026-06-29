#!/usr/bin/env python3
"""
Can we speed the cold-climb rise without hurting the steady band? The MPC fires
max for only ~3 min then eases in over the deadtime to avoid overshoot, so the
slow tail is a tunable rise/overshoot trade, not a plant limit. Sweep the cost
knobs (R_dQ move penalty, Q_w tracking weight, horizon) and report rise +
overshoot on a cold->500F climb AND the steady-state RMS at 110/260 C, so we keep
only changes that speed the rise without widening the band.
"""

import warnings, sys

warnings.filterwarnings('ignore')
sys.path.insert(0, '.')
import numpy as np
from controller.mpc import Controller, _DEFAULTS
from controller.grill_sim import GrillSim

CYCLE = {'u_min': 0.1, 'u_max': 0.9, 'HoldCycleTime': 25}
TS = 25.0
F2C = lambda f: (f - 32) * 5 / 9


def climb(over, set_c=F2C(500), seed=0, minutes=45):
	cfg = dict(_DEFAULTS)
	cfg.update(over)
	c = Controller(cfg, 'C', dict(CYCLE))
	c.set_target(set_c)
	p = GrillSim(seed=seed)
	T = []
	for w in range(int(minutes * 60 / TS)):
		out = c.update(p.measured())
		r = float(np.clip(out['cycle_ratio'], 0.1, 0.9))
		on = int(round(r * TS))
		for s in range(int(TS)):
			p.step(auger_on=(s < on), fan_frac=1.0)
			T.append(p.true_Tc)
	T = np.array(T)
	reach = int(np.argmax(T >= set_c - 2.0))
	rise = reach / 60.0 if T[reach] >= set_c - 2.0 else float('nan')
	over_f = (T.max() - set_c) * 9 / 5
	return rise, over_f


def steady(over, set_c, seed=0, minutes=70):
	cfg = dict(_DEFAULTS)
	cfg.update(over)
	c = Controller(cfg, 'C', dict(CYCLE))
	c.set_target(set_c)
	p = GrillSim(seed=seed)
	p.T_c = p.T_meas = set_c
	p.T_f = set_c + 60.0
	T = []
	for w in range(int(minutes * 60 / TS)):
		out = c.update(p.measured())
		r = float(np.clip(out['cycle_ratio'], 0.1, 0.9))
		on = int(round(r * TS))
		for s in range(int(TS)):
			p.step(auger_on=(s < on), fan_frac=1.0)
			T.append(p.true_Tc)
	T = np.array(T)
	e = T[len(T) // 3 :] - set_c
	return np.sqrt(np.mean(e**2))


CONFIGS = [
	('baseline R_dQ1 Q_w1', {}),
	('R_dQ 0.3', {'R_dQ': 0.3}),
	('R_dQ 0.1', {'R_dQ': 0.1}),
	('R_dQ 0.03', {'R_dQ': 0.03}),
	('Q_w 4', {'Q_w': 4.0}),
	('Q_w 4, R_dQ 0.3', {'Q_w': 4.0, 'R_dQ': 0.3}),
	('n_horizon 12', {'n_horizon': 12}),
]

if __name__ == '__main__':
	print(f'{"config":>22} | {"rise(500F)":>10} {"overshoot":>10} | {"RMS@110C":>9} {"RMS@260C":>9}')
	for name, ov in CONFIGS:
		ri, o = climb(ov)
		r110 = np.mean([steady(ov, 110.0, s) for s in (0, 1)])
		r260 = np.mean([steady(ov, 260.0, s) for s in (0, 1)])
		print(f'{name:>22} | {ri:8.1f}m {o:9.1f}F | {r110:8.2f}C {r260:8.2f}C', flush=True)
