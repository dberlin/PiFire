#!/usr/bin/env python3
"""
Higher-fidelity grill plant to honestly stress the production MPC.

Breaks the idealized assumptions of grill_sim.py:
  - discrete pellet-PULSE feeding (auger on/off within each cycle), not a smooth
    continuous heat source;
  - transport + ignition DEADTIME between feeding a pellet and its heat release;
  - the FAN as a real lever: it accelerates burn, boosts firepot->chamber
    convection, AND increases chamber->ambient loss (non-trivial trade-off);
  - combustion noise (pellet quality), sensor LAG (probe time constant), and
    WIND GUSTS (intermittent loss spikes).

The production controller (controller.mpc.Controller) drives it at its true
t_step=25 s cadence (consistent with how its Kalman filter is discretized). The
plant integrates at dt=1 s.
"""

import warnings, sys
from collections import deque

warnings.filterwarnings('ignore')
sys.path.insert(0, '.')
import numpy as np
from controller.mpc import Controller

CYCLE = {'u_min': 0.1, 'u_max': 0.9, 'HoldCycleTime': 25}
TS = 25.0  # MPC update interval (matches the controller's t_step / KF)
DT = 1.0  # plant integration step
SETPOINT = 110.0


class HiFiGrill:
	def __init__(self, seed=0, fan_is_lever=True, fixed_fan=None, deadtime=40):
		self.rng = np.random.default_rng(seed)
		self.fan_is_lever = fan_is_lever
		self.fixed_fan = fixed_fan  # if set, fan held at this frac
		self._deadtime_override = deadtime
		# truth params
		self.C_f, self.C_c = 9.0, 300.0
		self.h_fc0, self.h_amb0 = 1.3, 0.42
		self.sigma = 1.4e-9
		self.feed_rate = 1.0  # fuel units/s while auger ON
		self.H = 140.0  # heat per fuel unit burned (110C at ~0.45 fire)
		self.k_burn = 0.10
		self.T_amb = 17.0
		self.deadtime = self._deadtime_override  # s (transport + ignition)
		self.probe_tau = 4.5  # sensor lag (s)
		# state
		self.transit = deque([0.0] * self.deadtime)
		self.fuel = 0.0
		self.T_f = 20.0
		self.T_c = 20.0
		self.T_meas = 20.0
		self.t = 0.0
		self._gust_until = -1.0
		self._gust = 1.0

	def _wind(self):
		# occasional gusts that spike chamber heat loss
		if self.t > self._gust_until:
			if self.rng.random() < 0.004:  # ~ every 250 s
				self._gust = self.rng.uniform(1.6, 2.6)
				self._gust_until = self.t + self.rng.uniform(20, 60)
			else:
				self._gust = 1.0
		return self._gust

	def step(self, auger_on, fan_frac):
		if self.fixed_fan is not None:
			fan_frac = self.fixed_fan
		fan = float(np.clip(fan_frac, 0.0, 1.0))
		eff_fan = fan if self.fan_is_lever else 0.65

		# 1) deadtime: pellets fed now release heat `deadtime` seconds later
		fed = self.feed_rate if auger_on else 0.0
		released = self.transit.popleft()
		self.transit.append(fed)
		self.fuel += released

		# 2) combustion: fan accelerates burn; air sufficiency sets efficiency
		burn = self.k_burn * self.fuel * (0.5 + 0.6 * eff_fan)
		burn = min(burn, self.fuel)
		avail_air = 0.45 + 0.85 * eff_fan
		needed_air = burn * 0.9 + 1e-6
		eff = float(np.clip(avail_air / needed_air, 0.45, 1.0))
		noise = 1.0 + self.rng.normal(0, 0.05)  # pellet variability
		heat = burn * self.H * eff * max(noise, 0.0)

		# 3) fan-dependent transfer + loss; wind gusts on loss
		h_fc = self.h_fc0 * (0.6 + 0.7 * eff_fan)
		h_amb = self.h_amb0 * (0.8 + 0.5 * eff_fan) * self._wind()
		rad = self.sigma * ((self.T_c + 273.15) ** 4 - (self.T_amb + 273.15) ** 4)

		dT_f = (heat - h_fc * (self.T_f - self.T_c)) / self.C_f
		dT_c = (h_fc * (self.T_f - self.T_c) - h_amb * (self.T_c - self.T_amb) - rad) / self.C_c
		self.T_f += dT_f * DT
		self.T_c += dT_c * DT
		self.fuel = max(0.0, self.fuel - burn * DT)
		# sensor lag + noise
		self.T_meas += (self.T_c - self.T_meas) * DT / self.probe_tau
		self.t += DT

	def measured(self):
		return self.T_meas + float(self.rng.normal(0, 0.15))


def run(enable_fan_input, fan_is_lever=True, fixed_fan=None, n_minutes=120, seed=0, deadtime=40):
	cfg = dict(
		n_horizon=20,
		t_step=TS,
		control_period=TS,
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
		enable_fan_input=enable_fan_input,
		est_q_temp=1e-2,
		est_q_dist=0.5,
		est_r_meas=0.04,
	)
	c = Controller(dict(cfg), 'C', dict(CYCLE))
	c.set_target(SETPOINT)
	plant = HiFiGrill(seed=seed, fan_is_lever=fan_is_lever, fixed_fan=fixed_fan, deadtime=deadtime)

	n_windows = int(n_minutes * 60 / TS)
	ratio, fan_duty = CYCLE['u_min'], 70.0
	temps, times = [], []
	for w in range(n_windows):
		out = c.update(plant.measured())
		ratio = float(np.clip(out['cycle_ratio'], CYCLE['u_min'], CYCLE['u_max']))
		fan_duty = out['fan']['duty'] if out['fan']['duty'] is not None else 70.0
		on_secs = int(round(ratio * TS))
		for s in range(int(TS)):
			plant.step(auger_on=(s < on_secs), fan_frac=fan_duty / 100.0)
			temps.append(plant.T_c)
			times.append(w * TS + s)
	return np.array(times), np.array(temps)


def report(name, times, temps):
	sm = times >= 1800  # steady window: after 30 min warmup
	err = temps[sm] - SETPOINT
	print(f'\n== {name} ==')
	print(
		f'  steady-state error: max |e|={np.max(np.abs(err)):5.2f}C  '
		f'RMS={np.sqrt(np.mean(err**2)):4.2f}C  bias={np.mean(err):+5.2f}C'
	)
	print(
		f'  within +-1C: {100 * np.mean(np.abs(err) <= 1.0):4.1f}%   '
		f'within +-3C: {100 * np.mean(np.abs(err) <= 3.0):4.1f}%   '
		f'within +-5C: {100 * np.mean(np.abs(err) <= 5.0):4.1f}%'
	)


if __name__ == '__main__':
	print('Higher-fidelity plant (pellet pulses + deadtime + fan lever + wind + sensor lag)')
	t1, T1 = run(enable_fan_input=True, fan_is_lever=True)
	report('cascade MPC, fan tracks fuel (deadtime=40s)', t1, T1)
	t2, T2 = run(enable_fan_input=False, fan_is_lever=True, fixed_fan=0.65)
	report('auger-only MPC, fan fixed 65% (deadtime=40s)', t2, T2)

	print('\n--- deadtime sweep (cascade), isolating its effect ---')
	for dt in (5, 20, 40, 60):
		t, T = run(enable_fan_input=True, fan_is_lever=True, deadtime=dt)
		report(f'deadtime = {dt}s', t, T)
