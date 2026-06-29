#!/usr/bin/env python3
"""
Does a NONLINEAR controller model help on the realistic plant?

Compares, on the realistic plant (deadtime=20, fan lever, wind, sensor lag 4.5):
  A) production LINEAR deadtime model + Kalman filter (current implementation);
  B) NONLINEAR deadtime model (adds radiative T^4 chamber loss) + do-mpc MHE
     (nonlinear estimator; input Q as a known tvp for offset-free behavior).

Also runs a large setpoint step (95 -> 130 C) to probe whether the nonlinearity
helps where linearization error is largest (wide temperature excursions).
"""

import warnings, sys, time

warnings.filterwarnings('ignore')
sys.path.insert(0, 'docs/superpowers/experiments')
sys.path.insert(0, '.')
import numpy as np
import do_mpc
from collections import deque
from controller.grill_sim import GrillSim
from controller.mpc import Controller, _DEFAULTS

THETA, ND, TSTEP, NH, CP = 50.0, 4, 25.0, 20, 25.0
Cf, Cc, hfc, Tamb = 60.0, 306.0, 2.0, 20.0
QMIN, QMAX, UMIN, UMAX = 5.0, 100.0, 0.1, 0.9
K = 273.15


def alloc(Q):
	f = (Q - QMIN) / (QMAX - QMIN)
	return UMIN + f * (UMAX - UMIN), 40.0 + f * 60.0


class NonlinearMHE:
	"""Deadtime + radiative-loss model, estimated with a do-mpc MHE."""

	def __init__(self, h_amb=0.30, sigma=1.4e-9):
		self.sp = 110.0
		self._last_Q = QMIN
		self.n = ND + 3
		tau = THETA / ND

		def rad(Tc):
			return sigma * ((Tc + K) ** 4 - (Tamb + K) ** 4)

		# MPC model (nonlinear: radiative loss)
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
		m.set_rhs('T_f', (q[ND - 1] - hfc * (Tf - Tc)) / Cf)
		m.set_rhs('T_c', (hfc * (Tf - Tc) - h_amb * (Tc - Tamb) - rad(Tc) + d) / Cc)
		m.set_rhs('d', d * 0)
		m.setup()
		mpc = do_mpc.controller.MPC(m)
		mpc.set_param(
			n_horizon=NH,
			t_step=TSTEP,
			store_full_solution=False,
			nlpsol_opts={'ipopt.print_level': 0, 'print_time': 0, 'ipopt.sb': 'yes'},
		)
		mpc.set_objective(mterm=(Tc - Ts) ** 2, lterm=(Tc - Ts) ** 2)
		mpc.set_rterm(Q=0.02)
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

		# MHE model (Q known tvp; same nonlinear dynamics; process+meas noise)
		me = do_mpc.model.Model('continuous')
		q2 = [me.set_variable('_x', f'q{i}') for i in range(ND)]
		Tf2 = me.set_variable('_x', 'T_f')
		Tc2 = me.set_variable('_x', 'T_c')
		d2 = me.set_variable('_x', 'd')
		Qa = me.set_variable('_tvp', 'Q_app')
		me.set_rhs('q0', (Qa - q2[0]) / tau, process_noise=True)
		for i in range(1, ND):
			me.set_rhs(f'q{i}', (q2[i - 1] - q2[i]) / tau, process_noise=True)
		me.set_rhs('T_f', (q2[ND - 1] - hfc * (Tf2 - Tc2)) / Cf, process_noise=True)
		me.set_rhs('T_c', (hfc * (Tf2 - Tc2) - h_amb * (Tc2 - Tamb) - rad(Tc2) + d2) / Cc, process_noise=True)
		me.set_rhs('d', d2 * 0, process_noise=True)
		me.set_meas('T_c_meas', Tc2, meas_noise=True)
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
			np.diag([1.0] * (ND + 2) + [0.01]), np.array([[1.0 / 0.04]]), P_w=np.diag([10.0] * (ND + 2) + [0.05])
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
		for k in range(NH + 1):
			tv['_tvp', k, 'T_set'] = self.sp
		return tv

	def _q(self, tv):
		for k in range(11):
			tv['_tvp', k, 'Q_app'] = self.qh[k]
		return tv

	def set_target(self, sp):
		self.sp = sp

	def update(self, y):
		self.qh.append(self._last_Q)
		xh = np.asarray(self.mhe.make_step(np.array([[y]]))).flatten()
		try:
			Q = float(np.asarray(self.mpc.make_step(xh.reshape(-1, 1))).flatten()[0])
		except Exception:
			Q = self._last_Q
		Q = float(np.clip(Q, QMIN, QMAX))
		self._last_Q = Q
		return alloc(Q)


def drive(ctrl, deadtime=20, seed=0, minutes=120, step_at=None, sp2=None):
	plant = GrillSim(seed=seed, fan_is_lever=True, fixed_fan=None, deadtime=deadtime)
	ts, T = [], []
	for w in range(int(minutes * 60 / TSTEP)):
		t = w * TSTEP
		if step_at is not None and t >= step_at:
			(ctrl.set_target if hasattr(ctrl, 'set_target') else (lambda s: None))(sp2)
		ratio, fan = ctrl.update(plant.measured())
		on = int(round(ratio * TSTEP))
		for s in range(int(TSTEP)):
			plant.step(auger_on=(s < on), fan_frac=fan / 100.0)
			ts.append(t + s)
			T.append(plant.true_Tc)
	return np.array(ts), np.array(T)


def kf_ctrl(sp=110.0):
	cfg = dict(_DEFAULTS)
	cfg.update(enable_fan_input=True)
	c = Controller(cfg, 'C', {'u_min': 0.1, 'u_max': 0.9, 'HoldCycleTime': 25})
	c.set_target(sp)
	return (lambda o: (lambda out: (out['cycle_ratio'], out['fan']['duty']))(o)), c


class _KFWrap:
	def __init__(self, sp=110.0):
		cfg = dict(_DEFAULTS)
		cfg.update(enable_fan_input=True)
		self.c = Controller(cfg, 'C', {'u_min': 0.1, 'u_max': 0.9, 'HoldCycleTime': 25})
		self.c.set_target(sp)

	def set_target(self, sp):
		self.c.set_target(sp)

	def update(self, y):
		o = self.c.update(y)
		return o['cycle_ratio'], o['fan']['duty']


def report(name, ts, T, sp=110.0, win=1800):
	sm = ts >= win
	e = T[sm] - sp
	print(
		f'  {name:26s} RMS={np.sqrt(np.mean(e**2)):4.2f}  max={np.max(np.abs(e)):5.2f}  '
		f'bias={np.mean(e):+5.2f}  <3C={100 * np.mean(np.abs(e) <= 3):4.1f}%'
	)


if __name__ == '__main__':
	print('Realistic plant (deadtime=20). Hold at 110C:')
	report('A) linear deadtime + KF', *drive(_KFWrap()))
	report('B) nonlinear (rad) + MHE', *drive(NonlinearMHE()))

	print('\nLarge setpoint step 95 -> 130C at t=60min (settling of the step):')

	def settle(name, ts, T, step_t=3600.0, sp=130.0, band=2.0):
		post = ts >= step_t
		tt = ts[post] - step_t
		Tp = T[post]
		e = np.abs(Tp - sp)

		def at(mins):
			i = min(np.searchsorted(tt, mins * 60), len(Tp) - 1)
			return Tp[i]

		# first time it enters the band and stays within band+2 for the rest
		st = None
		for i in range(len(tt)):
			if e[i] <= band and np.all(e[i:] <= band + 2.0):
				st = tt[i] / 60.0
				break
		tag = (
			f'settled to +-{band}C at +{st:.0f} min'
			if st is not None
			else f'NEVER settled to +-{band}C (final {Tp[-1]:.1f}C)'
		)
		print(f'  {name:20s} T@+5/+15/+30/+60min = {at(5):.1f}/{at(15):.1f}/{at(30):.1f}/{at(60):.1f}C   {tag}')

	a = _KFWrap(95.0)
	ta, Ta = drive(a, minutes=180, step_at=3600, sp2=130.0)
	settle('A) linear + KF', ta, Ta)
	b = NonlinearMHE()
	b.set_target(95.0)
	tb, Tb = drive(b, minutes=180, step_at=3600, sp2=130.0)
	settle('B) nonlinear + MHE', tb, Tb)
