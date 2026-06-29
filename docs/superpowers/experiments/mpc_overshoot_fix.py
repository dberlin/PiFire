#!/usr/bin/env python3
"""
The 225->275F overshoot is a LIMIT CYCLE in the MPC/plant/deadtime loop (it
survives near-frozen d, so the disturbance estimator only follows it). Slowing d
(est_q_dist 0.5->0.05) cuts the initial overshoot for free; this is the base here.

This POC sweeps STRUCTURAL levers that could damp the cycle itself:
  - control_period : how often the MPC re-solves (vs the ~20s plant deadtime)
  - n_horizon      : prediction lookahead
  - t_step         : prediction granularity
  - theta          : the model's assumed deadtime (plant transit is ~20s)
Measures overshoot, rise, and oscillation EARLY (first 20 min after reaching
target) vs LATE (last 20 min) -- late osc near the steady band => cycle damped.

The harness respects control_period for the re-solve/auger cadence (like
control.py), feeds Fahrenheit, and uses production set_target on the step.
"""

import warnings, sys

warnings.filterwarnings('ignore')
sys.path.insert(0, '.')
import numpy as np
from controller.mpc import Controller, _DEFAULTS
from controller.grill_sim import GrillSim

CYCLE = {'u_min': 0.1, 'u_max': 0.9, 'HoldCycleTime': 25}
C2F = lambda c: c * 9 / 5 + 32
SP0, SP1 = 225.0, 275.0
STEADY_275 = 3.7  # ~steady-band RMS at 275F (osc floor)


def run(cfg_over=None, seed=0, settle_min=60, hold_min=60):
	cfg = dict(_DEFAULTS)
	cfg['est_q_dist'] = 0.05  # banked win as the base
	if cfg_over:
		cfg.update(cfg_over)
	cp = int(round(float(cfg['control_period'])))
	c = Controller(cfg, 'F', CYCLE)
	c.set_target(SP0)
	p = GrillSim(seed=seed)
	step_sec = settle_min * 60
	total_sec = (settle_min + hold_min) * 60
	PT = []
	cr, fan, on_secs, period_start = 0.1, 100.0, 1, 0
	stepped = False
	for sec in range(total_sec):
		if sec >= step_sec and not stepped:
			c.set_target(SP1)
			stepped = True
		if (sec - period_start) >= cp or sec == 0:
			out = c.update(C2F(p.measured()))
			cr = float(np.clip(out['cycle_ratio'], 0.1, 0.9))
			fan = out['fan']['duty'] or 100.0
			on_secs = int(round(cr * cp))
			period_start = sec
		auger_on = (sec - period_start) < on_secs
		p.step(auger_on=auger_on, fan_frac=fan / 100.0)
		PT.append(C2F(p.true_Tc))
	return np.array(PT), step_sec


def metrics(PT, step_s):
	post = PT[step_s:]
	over = post.max() - SP1
	reach = int(np.argmax(post >= SP1 - 2.0))
	rise = reach / 60.0 if post[reach] >= SP1 - 2.0 else float('nan')
	early = post[reach : reach + 20 * 60]
	late = post[-20 * 60 :]
	return over, rise, early.std(), late.std(), post.max()


if __name__ == '__main__':
	configs = [
		('base (q_dist .05)', {}),
		('control_period 10', {'control_period': 10.0}),
		('control_period 5', {'control_period': 5.0}),
		('n_horizon 40', {'n_horizon': 40}),
		('t_step 10, nh 60', {'t_step': 10.0, 'n_horizon': 60}),
		('theta 30', {'theta': 30.0}),
		('theta 80', {'theta': 80.0}),
		('cp5 + nh40', {'control_period': 5.0, 'n_horizon': 40}),
	]
	print(f'  (steady-band osc floor at 275F ~ {STEADY_275}F)')
	print(f'{"config":>20} {"overshoot":>10} {"rise":>6} {"osc_early":>10} {"osc_late":>9} {"peakF":>7}')
	for name, ov in configs:
		PT, ss = run(ov)
		o, ri, oe, ol, pk = metrics(PT, ss)
		print(f'{name:>20} {o:9.1f}F {ri:5.1f}m {oe:9.2f}F {ol:8.2f}F {pk:6.0f}F')
