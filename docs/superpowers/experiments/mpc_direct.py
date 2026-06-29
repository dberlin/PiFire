#!/usr/bin/env python3
"""
Direct auger+fan control (revisited). Two independent MPC inputs (u_a auger,
u_f fan) with a fan-AWARE nonlinear model so the optimizer understands what the
fan does: combustion efficiency vs air-fuel ratio, fan-boosted firepot->chamber
transfer, and fan-increased ambient loss. Estimated with MHE.

If this beats the cascade (esp. on the big step / high temps), direct control is
worth it. If not, we tune the cascade nonlinear+MHE instead.

Compares Direct vs Cascade (production linear+KF, K_Q-calibrated) on the
realistic plant (deadtime=20, fan lever) at holds and a 225->375F step.
"""

import warnings, sys

warnings.filterwarnings('ignore')
sys.path.insert(0, '.')
import numpy as np
from scipy.linalg import expm
import do_mpc
from casadi import exp as cexp
from collections import deque
from controller.grill_sim import GrillSim
from controller.mpc import Controller, _DEFAULTS

TSTEP, CP, ND, THETA = 25.0, 25.0, 4, 50.0
Cf, Cc, hfc0, hamb0, sigma, KQ, Tamb = 9.0, 320.0, 1.3, 0.42, 1.4e-9, 3.5, 17.0
UMIN, UMAX, FMIN, FMAX = 0.1, 0.9, 0.4, 1.0
PLANT_H = 420.0
KELVIN = 273.15
C2F = lambda c: c * 9 / 5 + 32
F2C = lambda f: (f - 32) * 5 / 9


class DirectMPC:
	"""Inputs u_a (auger) and u_f (fan), fan-aware nonlinear model + MHE."""

	def __init__(self):
		self.sp = 100.0
		self._ua = UMIN
		self._uf = 1.0
		self.n = ND + 3
		tau = THETA / ND

		def afr_eff(ua, uf):
			fuel = (ua - UMIN) / (UMAX - UMIN)
			air = (uf - FMIN) / (FMAX - FMIN)
			afr = (air + 1e-3) / (fuel + 1e-3)
			return cexp(-((afr - 1.0) ** 2) / (2 * 0.28**2))

		def chamber(uf, Tf, Tc, d):
			hfc = hfc0 * (0.6 + 0.7 * uf)
			hamb = hamb0 * (0.8 + 0.5 * uf)
			rad = sigma * ((Tc + KELVIN) ** 4 - (Tamb + KELVIN) ** 4)
			return (hfc * (Tf - Tc) - hamb * (Tc - Tamb) - rad + d) / Cc, hfc

		def firepot(qlast, ua, uf, Tf, Tc):
			hfc = hfc0 * (0.6 + 0.7 * uf)
			return (KQ * qlast * afr_eff(ua, uf) - hfc * (Tf - Tc)) / Cf

		# MPC model
		m = do_mpc.model.Model('continuous')
		q = [m.set_variable('_x', f'q{i}') for i in range(ND)]
		Tf = m.set_variable('_x', 'T_f')
		Tc = m.set_variable('_x', 'T_c')
		d = m.set_variable('_x', 'd')
		ua = m.set_variable('_u', 'u_a')
		uf = m.set_variable('_u', 'u_f')
		Ts = m.set_variable('_tvp', 'T_set')
		m.set_rhs('q0', (ua - q[0]) / tau)
		for i in range(1, ND):
			m.set_rhs(f'q{i}', (q[i - 1] - q[i]) / tau)
		m.set_rhs('T_f', firepot(q[ND - 1], ua, uf, Tf, Tc))
		m.set_rhs('T_c', chamber(uf, Tf, Tc, d)[0])
		m.set_rhs('d', d * 0)
		m.setup()
		mpc = do_mpc.controller.MPC(m)
		mpc.set_param(
			n_horizon=24,
			t_step=TSTEP,
			store_full_solution=False,
			nlpsol_opts={'ipopt.print_level': 0, 'print_time': 0, 'ipopt.sb': 'yes'},
		)
		mpc.set_objective(mterm=(Tc - Ts) ** 2, lterm=(Tc - Ts) ** 2)
		mpc.set_rterm(u_a=0.02, u_f=0.05)
		mpc.bounds['lower', '_u', 'u_a'] = UMIN
		mpc.bounds['upper', '_u', 'u_a'] = UMAX
		mpc.bounds['lower', '_u', 'u_f'] = FMIN
		mpc.bounds['upper', '_u', 'u_f'] = FMAX
		tv = mpc.get_tvp_template()

		def tvp(t):
			for k in range(25):
				tv['_tvp', k, 'T_set'] = self.sp
			return tv

		mpc.set_tvp_fun(tvp)
		mpc.setup()
		x0 = np.zeros((self.n, 1))
		x0[ND] = Tamb
		x0[ND + 1] = Tamb
		mpc.x0 = x0
		mpc.set_initial_guess()
		self.mpc = mpc

		# MHE (u_a, u_f known tvps)
		me = do_mpc.model.Model('continuous')
		q2 = [me.set_variable('_x', f'q{i}') for i in range(ND)]
		Tf2 = me.set_variable('_x', 'T_f')
		Tc2 = me.set_variable('_x', 'T_c')
		d2 = me.set_variable('_x', 'd')
		uaa = me.set_variable('_tvp', 'u_a')
		uff = me.set_variable('_tvp', 'u_f')
		me.set_rhs('q0', (uaa - q2[0]) / tau, process_noise=True)
		for i in range(1, ND):
			me.set_rhs(f'q{i}', (q2[i - 1] - q2[i]) / tau, process_noise=True)
		me.set_rhs('T_f', firepot(q2[ND - 1], uaa, uff, Tf2, Tc2), process_noise=True)
		me.set_rhs('T_c', chamber(uff, Tf2, Tc2, d2)[0], process_noise=True)
		me.set_rhs('d', d2 * 0, process_noise=True)
		me.set_meas('y', Tc2, meas_noise=True)
		me.setup()
		mhe = do_mpc.estimator.MHE(me, [])
		mhe.set_param(
			n_horizon=10,
			t_step=CP,
			store_full_solution=False,
			meas_from_data=True,
			nlpsol_opts={'ipopt.print_level': 0, 'print_time': 0, 'ipopt.sb': 'yes'},
		)
		mhe.set_default_objective(
			np.diag([1.0] * (ND + 2) + [0.01]), np.array([[1 / 0.04]]), P_w=np.diag([10.0] * (ND + 2) + [0.05])
		)
		self.uah = deque([UMIN] * 11, maxlen=11)
		self.ufh = deque([1.0] * 11, maxlen=11)
		tv2 = mhe.get_tvp_template()

		def tvp2(t):
			for k in range(11):
				tv2['_tvp', k, 'u_a'] = self.uah[k]
				tv2['_tvp', k, 'u_f'] = self.ufh[k]
			return tv2

		mhe.set_tvp_fun(tvp2)
		mhe.setup()
		x0m = np.zeros((self.n, 1))
		x0m[ND] = Tamb
		x0m[ND + 1] = Tamb
		mhe.x0 = x0m
		mhe.set_initial_guess()
		self.mhe = mhe

	def set_target(self, sp):
		self.sp = sp

	def update(self, y):
		self.uah.append(self._ua)
		self.ufh.append(self._uf)
		xh = np.asarray(self.mhe.make_step(np.array([[y]]))).flatten()
		try:
			u = np.asarray(self.mpc.make_step(xh.reshape(-1, 1))).flatten()
			ua, uf = float(u[0]), float(u[1])
		except Exception:
			ua, uf = self._ua, self._uf
		self._ua = float(np.clip(ua, UMIN, UMAX))
		self._uf = float(np.clip(uf, FMIN, FMAX))
		return self._ua, self._uf * 100.0


class Cascade:
	def __init__(self):
		cfg = dict(_DEFAULTS)
		cfg.update(enable_fan_input=True, K_Q=KQ)
		self.c = Controller(cfg, 'C', {'u_min': 0.1, 'u_max': 0.9, 'HoldCycleTime': 25})

	def set_target(self, sp):
		self.c.set_target(sp)

	def update(self, y):
		o = self.c.update(y)
		return o['cycle_ratio'], o['fan']['duty']


def drive(ctrl, sp_c, minutes=90, step=None, seed=0):
	plant = GrillSim(seed=seed, fan_is_lever=True, fixed_fan=None, deadtime=20, H=PLANT_H)
	ctrl.set_target(sp_c)
	T, ts = [], []
	for w in range(int(minutes * 60 / TSTEP)):
		t = w * TSTEP
		if step is not None and t >= step[0]:
			ctrl.set_target(step[1])
		ratio, fan = ctrl.update(plant.measured())
		on = int(round(ratio * TSTEP))
		for s in range(int(TSTEP)):
			plant.step(auger_on=(s < on), fan_frac=fan / 100.0)
			T.append(plant.true_Tc)
			ts.append(t + s)
	return np.array(ts), np.array(T)


def rep_hold(name, ts, T, sp_c):
	sm = ts >= ts[-1] * 0.5
	e = T[sm] - sp_c
	print(f'  {name:14s} RMS={np.sqrt(np.mean(e**2)):4.1f}  mean={C2F(np.mean(T[sm])):3.0f}F')


def rep_step(name, ts, T, step_t, sp2_c):
	post = ts >= step_t
	tt = ts[post] - step_t
	Tp = T[post]
	e = np.abs(Tp - sp2_c)
	st = next((tt[i] / 60 for i in range(len(tt)) if e[i] <= 3 and np.all(e[i:] <= 5)), None)
	over = max(0.0, np.max(Tp) - sp2_c)
	print(
		f'  {name:14s} overshoot {over:4.1f}C  '
		+ (f'settle +{st:.0f}min' if st else f'NEVER (final {C2F(Tp[-1]):.0f}F)')
	)


if __name__ == '__main__':
	print('Hold across temps (Direct vs Cascade):')
	for spf in (220, 375, 425):
		rep_hold(f'Direct {spf}F', *drive(DirectMPC(), F2C(spf)), F2C(spf))
		rep_hold(f'Cascade {spf}F', *drive(Cascade(), F2C(spf)), F2C(spf))
	print('\nStep 225 -> 375F at +30min:')
	rep_step('Direct', *drive(DirectMPC(), F2C(225), minutes=120, step=(1800, F2C(375))), 1800, F2C(375))
	rep_step('Cascade', *drive(Cascade(), F2C(225), minutes=120, step=(1800, F2C(375))), 1800, F2C(375))
