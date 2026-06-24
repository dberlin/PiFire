#!/usr/bin/env python3

'''
*****************************************
 PiFire MPC Grill Simulator (test-only)
*****************************************

 A higher-fidelity, deliberately MISMATCHED nonlinear grill plant for
 closed-loop validation of the MPC. Unlike the controller's smooth grey-box
 model it includes the effects that actually limit pellet-grill control:

   - discrete pellet-PULSE feeding (the auger toggles on/off), not a smooth
     continuous heat source;
   - transport + ignition DEADTIME between feeding a pellet and its heat
     release (default ~20 s);
   - the FAN as a real lever (it accelerates burn, boosts firepot->chamber
     convection, and increases chamber->ambient loss);
   - combustion noise (pellet quality), sensor LAG (probe time constant ~4.5 s),
     and intermittent WIND GUSTS on the chamber heat loss.

 So a passing closed-loop result is honest about realistic performance (a few
 degrees C band), not the artificially tight band an idealized plant gives.

 Interface: step(auger_on, fan_frac) advances one DT=1 s; measured() returns the
 lagged + noisy probe reading; true_Tc is the (noise-free) chamber temperature.

*****************************************
'''

import numpy as np

DT = 1.0


class GrillSim:
	def __init__(self, *, seed=0, deadtime=20, fan_is_lever=True, fixed_fan=None,
	             probe_tau=4.5, H=140.0):
		self.rng = np.random.default_rng(seed)
		self.fan_is_lever = fan_is_lever
		self.fixed_fan = fixed_fan            # if set, fan held at this frac
		self.probe_tau = probe_tau
		# truth params (offset from the controller's nominal grey-box)
		self.C_f, self.C_c = 9.0, 300.0
		self.h_fc0, self.h_amb0 = 1.3, 0.42
		self.sigma = 1.4e-9
		self.feed_rate = 1.0                  # fuel units/s while auger ON
		self.H = H                            # heat per fuel unit (~140 -> 334F max; ~300 -> ~450F max)
		self.k_burn = 0.10
		self.T_amb = 17.0
		from collections import deque
		self.transit = deque([0.0] * int(deadtime))
		self.fuel = 0.0
		self.T_f = 20.0
		self.T_c = 20.0
		self.T_meas = 20.0
		self.t = 0.0
		self.afr = 1.0
		self._gust_until = -1.0
		self._gust = 1.0

	def _wind(self):
		if self.t > self._gust_until:
			if self.rng.random() < 0.004:                 # ~ every 250 s
				self._gust = self.rng.uniform(1.6, 2.6)
				self._gust_until = self.t + self.rng.uniform(20, 60)
			else:
				self._gust = 1.0
		return self._gust

	@property
	def true_Tc(self):
		return float(self.T_c)

	def measured(self):
		return self.T_meas + float(self.rng.normal(0, 0.15))

	def step(self, auger_on, fan_frac):
		if self.fixed_fan is not None:
			fan_frac = self.fixed_fan
		fan = float(np.clip(fan_frac, 0.0, 1.0))
		eff_fan = fan if self.fan_is_lever else 0.65

		# deadtime: pellets fed now release heat `deadtime` seconds later
		fed = self.feed_rate if auger_on else 0.0
		released = self.transit.popleft()
		self.transit.append(fed)
		self.fuel += released

		# combustion: fan accelerates burn; air sufficiency sets efficiency
		burn = self.k_burn * self.fuel * (0.5 + 0.6 * eff_fan)
		burn = min(burn, self.fuel)
		avail_air = 0.45 + 0.85 * eff_fan
		needed_air = burn * 0.9 + 1e-6
		eff = float(np.clip(avail_air / needed_air, 0.45, 1.0))
		self.afr = avail_air / needed_air
		noise = 1.0 + self.rng.normal(0, 0.05)
		heat = burn * self.H * eff * max(noise, 0.0)

		# fan-dependent transfer + loss; wind gusts on loss
		h_fc = self.h_fc0 * (0.6 + 0.7 * eff_fan)
		h_amb = self.h_amb0 * (0.8 + 0.5 * eff_fan) * self._wind()
		rad = self.sigma * ((self.T_c + 273.15) ** 4 - (self.T_amb + 273.15) ** 4)

		dT_f = (heat - h_fc * (self.T_f - self.T_c)) / self.C_f
		dT_c = (h_fc * (self.T_f - self.T_c) - h_amb * (self.T_c - self.T_amb) - rad) / self.C_c
		self.T_f += dT_f * DT
		self.T_c += dT_c * DT
		self.fuel = max(0.0, self.fuel - burn * DT)
		self.T_meas += (self.T_c - self.T_meas) * DT / self.probe_tau
		self.t += DT
		return self.true_Tc
