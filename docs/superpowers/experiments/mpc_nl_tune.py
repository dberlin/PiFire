#!/usr/bin/env python3
"""
Tune the CASCADE nonlinear+MHE properly (single firing-rate input Q via the
allocator; radiative T^4 model + deadtime; MHE estimator with Q a known tvp).

The earlier nonlinear+MHE overshot badly. Sweep the likely culprits -- move
suppression R_dQ and the MHE disturbance-state weights (process P_w[d], arrival
P_x[d]) -- on a 375F hold and a 225->375F step. Compare to linear+KF.
"""

import warnings, sys

warnings.filterwarnings('ignore')
sys.path.insert(0, '.')
import numpy as np
import do_mpc
from casadi import exp as cexp  # noqa (kept for parity; unused)
from collections import deque
from controller.grill_sim import GrillSim
from controller.mpc import Controller, _DEFAULTS

TSTEP, CP, ND, THETA = 25.0, 25.0, 4, 50.0
Cf, Cc, hfc, hamb, sigma, KQ, Tamb = 9.0, 320.0, 1.3, 0.50, 1.4e-9, 3.5, 17.0
QMIN, QMAX, UMIN, UMAX = 5.0, 100.0, 0.1, 0.9
K = 273.15
PLANT_H = 420.0
C2F = lambda c: c * 9 / 5 + 32
F2C = lambda f: (f - 32) * 5 / 9


class NLCascade:
	def __init__(self, *, rterm=0.2, pwd=0.5, pxd=0.5, q_proc=10.0):
		self.sp = 100.0
		self._lastQ = QMIN
		self.n = ND + 3
		tau = THETA / ND

		def rad(Tc):
			return sigma * ((Tc + K) ** 4 - (Tamb + K) ** 4)

		m = do_mpc.model.Model('continuous')
		q = [m.set_variable('_x', f'q{i}') for i in range(ND)]
		Tf = m.set_variable('_x', 'T_f')
		Tc = m.set_variable('_x', 'T_c')
		d = m.set_variable('_x', 'd')
		Q = m.set_variable('_u', 'Q')
		Ts = m.set_variable('_tvp', 'T_set')
		m.set_rhs('q0', (Q - q[0]) / tau)
		for i in range(1, ND):
			m.set_rhs(f'q{i}', (q[i - 1] - q[i]) / tau)
		m.set_rhs('T_f', (KQ * q[ND - 1] - hfc * (Tf - Tc)) / Cf)
		m.set_rhs('T_c', (hfc * (Tf - Tc) - hamb * (Tc - Tamb) - rad(Tc) + d) / Cc)
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
		mpc.set_rterm(Q=rterm)
		mpc.bounds['lower', '_u', 'Q'] = QMIN
		mpc.bounds['upper', '_u', 'Q'] = QMAX
		tv = mpc.get_tvp_template()
		mpc.set_tvp_fun(lambda t: self._sp(tv))
		mpc.setup()
		x0 = np.zeros((self.n, 1))
		x0[ND] = Tamb
		x0[ND + 1] = Tamb
		mpc.x0 = x0
		mpc.set_initial_guess()
		self.mpc = mpc

		me = do_mpc.model.Model('continuous')
		q2 = [me.set_variable('_x', f'q{i}') for i in range(ND)]
		Tf2 = me.set_variable('_x', 'T_f')
		Tc2 = me.set_variable('_x', 'T_c')
		d2 = me.set_variable('_x', 'd')
		Qa = me.set_variable('_tvp', 'Q_app')
		me.set_rhs('q0', (Qa - q2[0]) / tau, process_noise=True)
		for i in range(1, ND):
			me.set_rhs(f'q{i}', (q2[i - 1] - q2[i]) / tau, process_noise=True)
		me.set_rhs('T_f', (KQ * q2[ND - 1] - hfc * (Tf2 - Tc2)) / Cf, process_noise=True)
		me.set_rhs('T_c', (hfc * (Tf2 - Tc2) - hamb * (Tc2 - Tamb) - rad(Tc2) + d2) / Cc, process_noise=True)
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
			np.diag([1.0] * (ND + 2) + [pxd]), np.array([[1 / 0.04]]), P_w=np.diag([q_proc] * (ND + 2) + [pwd])
		)
		self.qh = deque([QMIN] * 11, maxlen=11)
		tv2 = mhe.get_tvp_template()
		mhe.set_tvp_fun(lambda t: self._q(tv2))
		mhe.setup()
		x0m = np.zeros((self.n, 1))
		x0m[ND] = Tamb
		x0m[ND + 1] = Tamb
		mhe.x0 = x0m
		mhe.set_initial_guess()
		self.mhe = mhe

	def _sp(self, tv):
		for k in range(25):
			tv['_tvp', k, 'T_set'] = self.sp
		return tv

	def _q(self, tv):
		for k in range(11):
			tv['_tvp', k, 'Q_app'] = self.qh[k]
		return tv

	def set_target(self, sp):
		self.sp = sp

	def update(self, y):
		self.qh.append(self._lastQ)
		xh = np.asarray(self.mhe.make_step(np.array([[y]]))).flatten()
		try:
			Q = float(np.asarray(self.mpc.make_step(xh.reshape(-1, 1))).flatten()[0])
		except Exception:
			Q = self._lastQ
		Q = float(np.clip(Q, QMIN, QMAX))
		self._lastQ = Q
		f = (Q - QMIN) / (QMAX - QMIN)
		return UMIN + f * (UMAX - UMIN), 100.0


class LinKF:
	def __init__(self):
		cfg = dict(_DEFAULTS)
		cfg.update(enable_fan_input=False, K_Q=KQ)
		self.c = Controller(cfg, 'C', {'u_min': 0.1, 'u_max': 0.9, 'HoldCycleTime': 25})

	def set_target(self, sp):
		self.c.set_target(sp)

	def update(self, y):
		o = self.c.update(y)
		return o['cycle_ratio'], 100.0


def drive(ctrl, sp_c, minutes=70, step=None, seed=0):
	plant = GrillSim(seed=seed, fan_is_lever=True, fixed_fan=1.0, deadtime=20, H=PLANT_H)
	ctrl.set_target(sp_c)
	T, ts = [], []
	for w in range(int(minutes * 60 / TSTEP)):
		t = w * TSTEP
		if step is not None and t >= step[0]:
			ctrl.set_target(step[1])
		ratio, fan = ctrl.update(plant.measured())
		on = int(round(ratio * TSTEP))
		for s in range(int(TSTEP)):
			plant.step(auger_on=(s < on), fan_frac=1.0)
			T.append(plant.true_Tc)
			ts.append(t + s)
	return np.array(ts), np.array(T)


def hold(name, ts, T, sp_c):
	sm = ts >= ts[-1] * 0.45
	e = T[sm] - sp_c
	print(
		f'  {name:30s} RMS={np.sqrt(np.mean(e**2)):4.1f}C max={np.max(np.abs(e)):4.1f}C mean={C2F(np.mean(T[sm])):3.0f}F'
	)


def step(name, ts, T, st, sp2):
	post = ts >= st
	tt = ts[post] - st
	Tp = T[post]
	e = np.abs(Tp - sp2)
	s = next((tt[i] / 60 for i in range(len(tt)) if e[i] <= 3 and np.all(e[i:] <= 5)), None)
	print(
		f'  {name:30s} overshoot {max(0, np.max(Tp) - sp2):4.1f}C '
		+ (f'settle +{s:.0f}min' if s else f'NEVER {C2F(Tp[-1]):.0f}F')
	)


if __name__ == '__main__':
	print('Hold at 375F:')
	hold('linear+KF (baseline)', *drive(LinKF(), F2C(375)), F2C(375))
	for rt in (0.2, 1.0):
		for pwd in (0.5, 0.05):
			hold(f'NL+MHE rterm={rt} pwd={pwd}', *drive(NLCascade(rterm=rt, pwd=pwd), F2C(375)), F2C(375))
	print('\nStep 225 -> 375F at +25min:')
	step('linear+KF', *drive(LinKF(), F2C(225), minutes=110, step=(1500, F2C(375))), 1500, F2C(375))
	for rt in (0.2, 1.0):
		step(
			f'NL+MHE rterm={rt} pwd=0.5',
			*drive(NLCascade(rterm=rt, pwd=0.5), F2C(225), minutes=110, step=(1500, F2C(375))),
			1500,
			F2C(375),
		)
