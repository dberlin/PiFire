#!/usr/bin/env python3
"""
Chase the big-step limit cycle. Best controller (calibrated linear deadtime+KF).
Step 225 -> 375 F. Test the candidate levers:
  - move suppression R_dQ (0.02 baseline, higher = gentler)
  - theta matched to the plant deadtime (50 over-models a 20s plant)
  - setpoint RAMP instead of an instantaneous step
Reports settling time to +-3C of the new target and peak overshoot.
"""

import warnings, sys

warnings.filterwarnings('ignore')
sys.path.insert(0, '.')
import numpy as np
from scipy.linalg import expm
import do_mpc
from controller.grill_sim import GrillSim

TSTEP, CP, ND = 25.0, 25.0, 4
Cf, Cc, hfc, hamb, KQ, Tamb = 9.0, 320.0, 1.3, 0.50, 3.5, 17.0
QMIN, QMAX, UMIN, UMAX = 5.0, 100.0, 0.1, 0.9
PLANT_H = 420.0
C2F = lambda c: c * 9 / 5 + 32
F2C = lambda f: (f - 32) * 5 / 9


class LinMPC:
	def __init__(self, *, rterm=0.02, theta=50.0, nh=24):
		self.sp = 100.0
		self._lastQ = QMIN
		self.n = ND + 3
		tau = theta / ND
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
		m.set_rhs('T_c', (hfc * (Tf - Tc) - hamb * (Tc - Tamb) + d) / Cc)
		m.set_rhs('d', d * 0)
		m.setup()
		mpc = do_mpc.controller.MPC(m)
		mpc.set_param(
			n_horizon=nh,
			t_step=TSTEP,
			store_full_solution=False,
			nlpsol_opts={'ipopt.print_level': 0, 'print_time': 0, 'ipopt.sb': 'yes'},
		)
		mpc.set_objective(mterm=(Tc - Ts) ** 2, lterm=(Tc - Ts) ** 2)
		mpc.set_rterm(Q=rterm)
		mpc.bounds['lower', '_u', 'Q'] = QMIN
		mpc.bounds['upper', '_u', 'Q'] = QMAX
		tv = mpc.get_tvp_template()

		def tvp(t):
			for k in range(nh + 1):
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
		# KF
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

	def set_target(self, sp):
		self.sp = sp

	def update(self, y):
		self.xk = self.Ad @ self.xk + self.Bd.flatten() * self._lastQ + self.bd.flatten()
		self.Pk = self.Ad @ self.Pk @ self.Ad.T + self.Qk
		S = self.Hk @ self.Pk @ self.Hk.T + self.Rk
		Kk = (self.Pk @ self.Hk.T) / S
		self.xk = self.xk + Kk.flatten() * (y - (self.Hk @ self.xk)[0])
		self.Pk = (np.eye(self.n) - Kk @ self.Hk) @ self.Pk
		try:
			Q = float(np.asarray(self.mpc.make_step(self.xk.reshape(-1, 1))).flatten()[0])
		except Exception:
			Q = self._lastQ
		Q = float(np.clip(Q, QMIN, QMAX))
		self._lastQ = Q
		f = (Q - QMIN) / (QMAX - QMIN)
		return UMIN + f * (UMAX - UMIN)


def run_step(ctrl, sp1_c, sp2_c, step_min=30, ramp_min=0, minutes=120, deadtime=20, seed=0):
	plant = GrillSim(seed=seed, fan_is_lever=True, fixed_fan=1.0, deadtime=deadtime, H=PLANT_H)
	ctrl.set_target(sp1_c)
	T, ts = [], []
	for w in range(int(minutes * 60 / TSTEP)):
		t = w * TSTEP
		if t >= step_min * 60:
			if ramp_min > 0:
				frac = min(1.0, (t - step_min * 60) / (ramp_min * 60))
				ctrl.set_target(sp1_c + frac * (sp2_c - sp1_c))
			else:
				ctrl.set_target(sp2_c)
		ratio = ctrl.update(plant.measured())
		on = int(round(ratio * TSTEP))
		for s in range(int(TSTEP)):
			plant.step(auger_on=(s < on), fan_frac=1.0)
			T.append(plant.true_Tc)
			ts.append(t + s)
	return np.array(ts), np.array(T)


def settle(name, ts, T, step_min=30, sp2_c=None, band=3.0):
	post = ts >= step_min * 60
	tt = ts[post] - step_min * 60
	Tp = T[post]
	e = np.abs(Tp - sp2_c)
	over = max(0.0, np.max(Tp) - sp2_c)
	st = next((tt[i] / 60 for i in range(len(tt)) if e[i] <= band and np.all(e[i:] <= band + 2)), None)
	tag = f'settled +-{band}C at +{st:.0f}min' if st is not None else f'NEVER (final {C2F(Tp[-1]):.0f}F)'
	print(f'  {name:34s} overshoot {over:4.1f}C  {tag}')


if __name__ == '__main__':
	sp1, sp2 = F2C(225), F2C(375)
	print(f'Step 225 -> 375 F at +30min:\n')
	settle('baseline (R_dQ=0.02, theta=50)', *run_step(LinMPC(), sp1, sp2), sp2_c=sp2)
	for r in (0.1, 0.5, 2.0):
		settle(f'R_dQ={r}', *run_step(LinMPC(rterm=r), sp1, sp2), sp2_c=sp2)
	settle('theta=20 (matched to plant)', *run_step(LinMPC(theta=20.0), sp1, sp2), sp2_c=sp2)
	settle('theta=20, R_dQ=0.5', *run_step(LinMPC(theta=20.0, rterm=0.5), sp1, sp2), sp2_c=sp2)
	print()
	for rm in (10, 20):
		settle(f'setpoint ramp over {rm}min (R_dQ=0.02)', *run_step(LinMPC(), sp1, sp2, ramp_min=rm), sp2_c=sp2)
	settle('ramp 15min + theta=20', *run_step(LinMPC(theta=20.0), sp1, sp2, ramp_min=15), sp2_c=sp2)
