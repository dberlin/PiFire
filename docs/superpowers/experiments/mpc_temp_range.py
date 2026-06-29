#!/usr/bin/env python3
"""
Does the controller hold across the REAL temperature range (220/375/425 F, grill
maxes ~600 F)? And is the MPC uniquely bad, or do the PIDs struggle too?

Powerful plant (H=420 -> ~600 F max). Fixed fan 100% (pellet-grill hold mode) for
all controllers, fed the same realistic plant. Compares:
  - MPC linear (deadtime, K_Q heat-gain) + Kalman
  - MPC nonlinear (deadtime, K_Q, radiative T^4) + MHE
  - PID (standard)         from controller/pid.py
  - PID-SP (FOPDT/Smith)   from controller/pid_sp.py

The MPC needs a firing-rate->heat gain K_Q (calibration to grill power); the
current production model hardcodes K_Q=1, so these experiment controllers add it.
"""

import warnings, sys

warnings.filterwarnings('ignore')
sys.path.insert(0, '.')
import numpy as np
from scipy.linalg import expm
import do_mpc
from collections import deque
from controller.grill_sim import GrillSim

TSTEP, NH, CP = 25.0, 24, 25.0
ND, THETA = 4, 50.0
Cf, Cc, hfc, hamb, sigma, KQ, Tamb = 9.0, 320.0, 1.3, 0.50, 1.4e-9, 3.5, 17.0
QMIN, QMAX, UMIN, UMAX = 5.0, 100.0, 0.1, 0.9
K = 273.15
PLANT_H = 420.0

F2C = lambda f: (f - 32) * 5 / 9
C2F = lambda c: c * 9 / 5 + 32


def alloc(Q):
	f = (Q - QMIN) / (QMAX - QMIN)
	return UMIN + f * (UMAX - UMIN), 100.0


# ---- SimClock so the PIDs' time.time()-based dt works in sim time ----
class _Clk:
	def __init__(self):
		self.t = 0.0

	def time(self):
		return self.t


_clk = _Clk()
import controller.pid as _pid

_pid.time = _clk
import controller.pid_sp as _pidsp

_pidsp.time = _clk


class MPCGrey:
	"""Deadtime + K_Q heat-gain, optional radiative loss. KF (linear) or MHE (nl)."""

	def __init__(self, radiative):
		self.sp = 100.0
		self._lastQ = QMIN
		self.n = ND + 3
		self.radiative = radiative
		tau = THETA / ND

		def rad(Tc):
			return sigma * ((Tc + K) ** 4 - (Tamb + K) ** 4) if radiative else 0.0

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
		mpc.set_tvp_fun(lambda t: self._sp(tv, NH))
		mpc.setup()
		x0 = np.zeros((self.n, 1))
		x0[ND] = Tamb
		x0[ND + 1] = Tamb
		mpc.x0 = x0
		mpc.set_initial_guess()
		self.mpc = mpc

		if radiative:
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
			me.set_rhs(
				'T_c',
				(hfc * (Tf2 - Tc2) - hamb * (Tc2 - Tamb) - sigma * ((Tc2 + K) ** 4 - (Tamb + K) ** 4) + d2) / Cc,
				process_noise=True,
			)
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
		else:
			self._build_kf(tau)

	def _build_kf(self, tau):
		n = self.n
		iTf, iTc, iD = ND, ND + 1, ND + 2
		A = np.zeros((n, n))
		for i in range(ND):
			A[i, i] = -1 / tau
			if i > 0:
				A[i, i - 1] = 1 / tau
		A[iTf, ND - 1] = KQ / Cf
		A[iTf, iTf] = -hfc / Cf
		A[iTf, iTc] = hfc / Cf
		A[iTc, iTf] = hfc / Cc
		A[iTc, iTc] = -(hfc + hamb) / Cc
		A[iTc, iD] = 1 / Cc
		B = np.zeros((n, 2))
		B[0, 0] = 1 / tau
		B[iTc, 1] = hamb * Tamb / Cc
		Mb = np.zeros((n + 2, n + 2))
		Mb[:n, :n] = A
		Mb[:n, n:] = B
		Md = expm(Mb * CP)
		self.Ad, self.Bd, self.bd = Md[:n, :n], Md[:n, n : n + 1], Md[:n, n + 1 : n + 2]
		self.Hk = np.zeros((1, n))
		self.Hk[0, iTc] = 1
		self.Qk = np.diag([1e-2] * (ND + 2) + [0.5])
		self.Rk = np.array([[0.04]])
		self.xk = np.zeros(n)
		self.xk[iTf] = Tamb
		self.xk[iTc] = Tamb
		self.Pk = np.eye(n) * 5

	def _sp(self, tv, nh):
		for k in range(nh + 1):
			tv['_tvp', k, 'T_set'] = self.sp
		return tv

	def _q(self, tv):
		for k in range(11):
			tv['_tvp', k, 'Q_app'] = self.qh[k]
		return tv

	def set_target(self, sp):
		self.sp = sp

	def update(self, y):
		if self.radiative:
			self.qh.append(self._lastQ)
			xh = np.asarray(self.mhe.make_step(np.array([[y]]))).flatten()
		else:
			self.xk = self.Ad @ self.xk + self.Bd.flatten() * self._lastQ + self.bd.flatten()
			self.Pk = self.Ad @ self.Pk @ self.Ad.T + self.Qk
			S = self.Hk @ self.Pk @ self.Hk.T + self.Rk
			Kk = (self.Pk @ self.Hk.T) / S
			self.xk = self.xk + Kk.flatten() * (y - (self.Hk @ self.xk)[0])
			self.Pk = (np.eye(self.n) - Kk @ self.Hk) @ self.Pk
			xh = self.xk
		try:
			Q = float(np.asarray(self.mpc.make_step(xh.reshape(-1, 1))).flatten()[0])
		except Exception:
			Q = self._lastQ
		Q = float(np.clip(Q, QMIN, QMAX))
		self._lastQ = Q
		return alloc(Q)


class PIDWrap:
	"""Drives a controller/ PID. Works in F (its tuning units)."""

	def __init__(self, module):
		import importlib, json

		meta = json.load(open('controller/controllers.json'))['metadata'][module]
		cfg = {o['option_name']: o['option_default'] for o in meta['config']}
		mod = importlib.import_module(f'controller.{module}')
		self.c = mod.Controller(cfg, 'F', {'u_min': 0.1, 'u_max': 0.9, 'HoldCycleTime': 25})
		self.units_f = True

	def set_target(self, sp_c):
		self.c.set_target(C2F(sp_c))

	def update(self, y_c):
		return float(np.clip(self.c.update(C2F(y_c)), UMIN, UMAX)), 100.0


def hold(ctrl, sp_c, minutes=75, seed=0, is_pid=False):
	plant = GrillSim(seed=seed, fan_is_lever=True, fixed_fan=1.0, deadtime=20, H=PLANT_H)
	ctrl.set_target(sp_c)
	T = []
	for w in range(int(minutes * 60 / TSTEP)):
		if is_pid:
			_clk.t = (w + 1) * TSTEP
		ratio, _ = ctrl.update(plant.measured())
		on = int(round(ratio * TSTEP))
		for s in range(int(TSTEP)):
			plant.step(auger_on=(s < on), fan_frac=1.0)
			T.append(plant.true_Tc)
	T = np.array(T)
	sm = np.arange(len(T)) * 1.0 >= (minutes * 60 * 0.45)  # last ~55%
	e = T[sm] - sp_c
	return np.sqrt(np.mean(e**2)), np.max(np.abs(e)), np.mean(e), np.mean(T[sm])


if __name__ == '__main__':
	sps_f = [220, 375, 425]
	builders = [
		('MPC linear+KF', lambda: MPCGrey(radiative=False)),
		('MPC nonlin+MHE', lambda: MPCGrey(radiative=True)),
		('PID', lambda: PIDWrap('pid')),
		('PID-SP', lambda: PIDWrap('pid_sp')),
	]
	print(f'Holding (plant maxes ~{C2F(258):.0f}F). RMS/max/bias in C, mean temp in F:\n')
	print(f'  {"controller":16s} ' + '  '.join(f'{s}F' for s in sps_f))
	for name, mk in builders:
		cells = []
		for spf in sps_f:
			is_pid = name.startswith('PID')
			rms, mx, bias, meanT = hold(mk(), F2C(spf), is_pid=is_pid)
			cells.append(f'RMS{rms:4.1f} mean{C2F(meanT):3.0f}F')
		print(f'  {name:16s} ' + '  '.join(cells))
