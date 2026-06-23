#!/usr/bin/env python3

'''
*****************************************
 PiFire MPC Grill Simulator (test-only)
*****************************************

 A deliberately MISMATCHED nonlinear grill plant for closed-loop validation of
 the MPC. Differs from the controller's internal model (parameter offsets,
 air-fuel-ratio dependent combustion efficiency, ambient drift, a lid-open
 event, and process/measurement noise) so a passing +-1.0 C result is not
 tautological. Built on do-mpc's simulator.

*****************************************
'''

import numpy as np
import do_mpc

Ts = 25.0
# truth params (offset ~15% from the controller's nominal)
C_f_t, C_c_t = 70.0, 350.0
h_fc_t, h_amb_t = 1.70, 0.62
# allocator endpoints (must match the controller config used in tests)
Q_MIN, Q_MAX = 5.0, 100.0
U_MIN, U_MAX = 0.1, 0.9
FAN_MIN, FAN_MAX = 40.0, 100.0
FUEL_TO_HEAT = Q_MAX / U_MAX
AFR_OPT, AFR_SIGMA = 1.0, 0.28


class GrillSim:
	def __init__(self, *, seed=0):
		self.rng = np.random.default_rng(seed)
		self.t = 0.0
		self.afr = AFR_OPT
		m = do_mpc.model.Model('continuous')
		T_f = m.set_variable('_x', 'T_f')
		T_c = m.set_variable('_x', 'T_c')
		Qh = m.set_variable('_u', 'Qh')
		T_amb = m.set_variable('_tvp', 'T_amb')
		lid = m.set_variable('_tvp', 'lid')
		m.set_rhs('T_f', (Qh - h_fc_t * (T_f - T_c)) / C_f_t)
		m.set_rhs('T_c', (h_fc_t * (T_f - T_c) - h_amb_t * lid * (T_c - T_amb)) / C_c_t)
		m.setup()
		self.sim = do_mpc.simulator.Simulator(m)
		self.sim.set_param(t_step=Ts)
		tvp_t = self.sim.get_tvp_template()
		def tvp_fun(t_now):
			tvp_t['T_amb'] = 18.0 - 8.0 * (t_now / 7200.0)            # drift
			tvp_t['lid'] = 4.0 if 3000.0 <= t_now < 3090.0 else 1.0   # lid open
			return tvp_t
		self.sim.set_tvp_fun(tvp_fun)
		self.sim.setup()
		self.sim.x0 = np.array([[20.0], [20.0]])
		self.sim.set_initial_guess()

	@property
	def true_Tc(self):
		return float(self.sim.x0['T_c'])

	def measured(self):
		return self.true_Tc + float(self.rng.normal(0, 0.2))

	def step_from_allocation(self, auger, fan_duty_pct):
		fuel = max(auger, 1e-6)
		air_frac = ((fan_duty_pct - FAN_MIN) / (FAN_MAX - FAN_MIN)
		            if fan_duty_pct is not None else (fuel - U_MIN) / (U_MAX - U_MIN))
		fuel_frac = (fuel - U_MIN) / (U_MAX - U_MIN)
		# normalized air/fuel ratio; matched allocation drives air_frac==fuel_frac
		# so afr ~ AFR_OPT (1.0) and combustion stays efficient.
		afr = (air_frac + 1e-6) / (fuel_frac + 1e-6)
		self.afr = afr
		eff = np.exp(-((afr - AFR_OPT) ** 2) / (2 * AFR_SIGMA ** 2))
		Qh = FUEL_TO_HEAT * fuel * eff
		self.sim.make_step(np.array([[Qh]]))
		self.t += Ts
		return self.true_Tc
