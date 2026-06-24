#!/usr/bin/env python3

'''
*****************************************
 PiFire MPC Grey-box Thermal Model + Estimator
*****************************************

 Two lumped thermal masses (firepot T_f, chamber T_c) driven by a scalar
 firing-rate Q, plus an integrating disturbance state d for offset-free
 tracking. Optionally an input transport delay (the feed -> combustion ->
 sensor deadtime) is modeled as a chain of n_delay first-order lag states
 (an Erlang / distributed-delay approximation of mean duration theta), which
 lets the MPC predict across the deadtime instead of over-correcting.

 State order: [q0 .. q_{n_delay-1}, T_f, T_c, d].

 Provides the do-mpc model used by the controller and a Kalman filter over the
 same augmented linear model used as the state/disturbance estimator.

*****************************************
'''

import numpy as np
from scipy.linalg import expm
# NOTE: do_mpc (and its CasADi/IPOPT stack) is imported LAZILY inside the do-mpc
# model builder and the MHE. The Kalman/EKF estimators and the neural-net policy
# are pure numpy/scipy, so an IPOPT-free deployment (net policy + EKF) does not
# require do_mpc to be installed at all.


_KELVIN = 273.15


def _rad_loss(T_c, T_amb, sigma):
	# Radiative chamber loss (Stefan-Boltzmann-like). sigma=0 -> purely linear.
	return sigma * ((T_c + _KELVIN) ** 4 - (T_amb + _KELVIN) ** 4)


def build_do_mpc_model(*, C_f, C_c, h_fc, h_amb, T_amb, theta=0.0, n_delay=0,
                       K_Q=1.0, sigma=0.0):
	import do_mpc
	model = do_mpc.model.Model('continuous')
	q = [model.set_variable('_x', f'q{i}') for i in range(n_delay)]
	T_f = model.set_variable('_x', 'T_f')
	T_c = model.set_variable('_x', 'T_c')
	d = model.set_variable('_x', 'd')
	Q = model.set_variable('_u', 'Q')
	model.set_variable('_tvp', 'T_set')
	if n_delay > 0:
		tau_d = theta / n_delay
		model.set_rhs('q0', (Q - q[0]) / tau_d)
		for i in range(1, n_delay):
			model.set_rhs(f'q{i}', (q[i - 1] - q[i]) / tau_d)
		heat_in = q[n_delay - 1]
	else:
		heat_in = Q
	# K_Q maps the abstract firing rate to actual heat (calibrated to grill power)
	model.set_rhs('T_f', (K_Q * heat_in - h_fc * (T_f - T_c)) / C_f)
	model.set_rhs('T_c', (h_fc * (T_f - T_c) - h_amb * (T_c - T_amb)
	                      - _rad_loss(T_c, T_amb, sigma) + d) / C_c)
	model.set_rhs('d', d * 0)
	model.setup()
	return model


class GreyBoxKF:
	'''
	Kalman filter over the augmented linear model
	x = [q0..q_{n_delay-1}, T_f, T_c, d], input Q. The constant ambient term
	enters as an affine input (held at 1). `t_step` is the real interval between
	update() calls (i.e. the control period) - the discretization matches the
	cadence so faster control re-solves are estimated correctly.
	'''

	def __init__(self, *, C_f, C_c, h_fc, h_amb, T_amb, t_step,
	             q_temp, q_dist, r_meas, theta=0.0, n_delay=0, K_Q=1.0, x0=None):
		n = n_delay + 3
		iTf, iTc, iD = n_delay, n_delay + 1, n_delay + 2

		A = np.zeros((n, n))
		if n_delay > 0:
			tau_d = theta / n_delay
			for i in range(n_delay):
				A[i, i] = -1.0 / tau_d
				if i > 0:
					A[i, i - 1] = 1.0 / tau_d
			A[iTf, n_delay - 1] = K_Q / C_f      # last lag feeds the firepot (scaled by K_Q)
		A[iTf, iTf] = -h_fc / C_f
		A[iTf, iTc] = h_fc / C_f
		A[iTc, iTf] = h_fc / C_c
		A[iTc, iTc] = -(h_fc + h_amb) / C_c
		A[iTc, iD] = 1.0 / C_c

		# columns: [Q input, affine constant=1]
		Baug = np.zeros((n, 2))
		if n_delay > 0:
			Baug[0, 0] = 1.0 / (theta / n_delay)  # Q enters the first transport lag
		else:
			Baug[iTf, 0] = K_Q / C_f              # no deadtime: Q enters the firepot (scaled by K_Q)
		Baug[iTc, 1] = h_amb * T_amb / C_c

		Mblk = np.zeros((n + 2, n + 2))
		Mblk[:n, :n] = A
		Mblk[:n, n:] = Baug
		Md = expm(Mblk * t_step)
		self.Ad = Md[:n, :n]
		self.Bd = Md[:n, n:n + 1]      # for Q
		self.bd = Md[:n, n + 1:n + 2]  # affine (constant input = 1)
		self.H = np.zeros((1, n)); self.H[0, iTc] = 1.0
		self.Qkf = np.diag([q_temp] * (n_delay + 2) + [q_dist])
		self.Rkf = np.array([[r_meas]])
		if x0 is None:
			x0 = [0.0] * n_delay + [T_amb, T_amb, 0.0]
		self.x = np.array(x0, dtype=float)
		self.P = np.eye(n) * 5.0
		self.n = n

	def update(self, Q_applied, y_measured):
		# predict
		self.x = self.Ad @ self.x + self.Bd.flatten() * Q_applied + self.bd.flatten()
		self.P = self.Ad @ self.P @ self.Ad.T + self.Qkf
		# update
		S = self.H @ self.P @ self.H.T + self.Rkf
		K = (self.P @ self.H.T) / S
		self.x = self.x + K.flatten() * (y_measured - (self.H @ self.x)[0])
		self.P = (np.eye(self.n) - K @ self.H) @ self.P
		return self.x


class GreyBoxEKF:
	'''
	Extended Kalman filter over the augmented model with the nonlinear radiative
	chamber loss. The only nonlinearity is the Stefan-Boltzmann term on T_c, so
	each step we linearize it about the current T_c estimate (slope
	4*sigma*(T_c+273.15)^3) and fold the linearization offset into the affine
	input -- this reproduces the nonlinear loss exactly at the operating point
	and to first order nearby, while keeping the exact expm propagation for the
	stiff linear part. Reduces EXACTLY to GreyBoxKF when sigma=0. Nonlinear-capable
	like the MHE but ~us/step (one small expm) instead of an NLP solve. Same
	integrating-disturbance state d gives offset-free tracking, and the same
	update(Q_applied, y) interface as GreyBoxKF / GreyBoxMHE.
	'''

	def __init__(self, *, C_f, C_c, h_fc, h_amb, T_amb, t_step,
	             q_temp, q_dist, r_meas, theta=0.0, n_delay=0, K_Q=1.0,
	             sigma=0.0, x0=None):
		n = n_delay + 3
		iTf, iTc, iD = n_delay, n_delay + 1, n_delay + 2

		A = np.zeros((n, n))
		if n_delay > 0:
			tau_d = theta / n_delay
			for i in range(n_delay):
				A[i, i] = -1.0 / tau_d
				if i > 0:
					A[i, i - 1] = 1.0 / tau_d
			A[iTf, n_delay - 1] = K_Q / C_f
		A[iTf, iTf] = -h_fc / C_f
		A[iTf, iTc] = h_fc / C_f
		A[iTc, iTf] = h_fc / C_c
		A[iTc, iTc] = -(h_fc + h_amb) / C_c
		A[iTc, iD] = 1.0 / C_c

		Baug = np.zeros((n, 2))
		if n_delay > 0:
			Baug[0, 0] = 1.0 / (theta / n_delay)
		else:
			Baug[iTf, 0] = K_Q / C_f
		Baug[iTc, 1] = h_amb * T_amb / C_c

		self.A_lin, self.Baug = A, Baug
		self.n, self.iTc = n, iTc
		self.C_c, self.T_amb, self.sigma = C_c, T_amb, sigma
		self.t_step = t_step
		self.H = np.zeros((1, n)); self.H[0, iTc] = 1.0
		self.Qkf = np.diag([q_temp] * (n_delay + 2) + [q_dist])
		self.Rkf = np.array([[r_meas]])
		if x0 is None:
			x0 = [0.0] * n_delay + [T_amb, T_amb, 0.0]
		self.x = np.array(x0, dtype=float)
		self.P = np.eye(n) * 5.0

	def _discretize(self):
		# linearize the radiative term about the current chamber estimate
		n, iTc, C_c = self.n, self.iTc, self.C_c
		T_c0 = self.x[iTc]
		rp = 4.0 * self.sigma * (T_c0 + _KELVIN) ** 3            # d(rad)/dT_c
		r0 = _rad_loss(T_c0, self.T_amb, self.sigma)             # rad loss at T_c0
		A = self.A_lin.copy(); A[iTc, iTc] += -rp / C_c
		Baug = self.Baug.copy(); Baug[iTc, 1] += -(r0 - rp * T_c0) / C_c
		Mblk = np.zeros((n + 2, n + 2))
		Mblk[:n, :n] = A
		Mblk[:n, n:] = Baug
		Md = expm(Mblk * self.t_step)
		return Md[:n, :n], Md[:n, n:n + 1], Md[:n, n + 1:n + 2]

	def update(self, Q_applied, y_measured):
		Ad, Bd, bd = self._discretize()
		# predict
		self.x = Ad @ self.x + Bd.flatten() * Q_applied + bd.flatten()
		self.P = Ad @ self.P @ Ad.T + self.Qkf
		# update
		S = self.H @ self.P @ self.H.T + self.Rkf
		K = (self.P @ self.H.T) / S
		self.x = self.x + K.flatten() * (y_measured - (self.H @ self.x)[0])
		self.P = (np.eye(self.n) - K @ self.H) @ self.P
		return self.x


class GreyBoxMHE:
	'''
	Moving-horizon estimator over the (possibly nonlinear) augmented model,
	x = [q0..q_{n_delay-1}, T_f, T_c, d]. The control input Q is modeled as a
	KNOWN time-varying parameter (fed the applied-input history) so the
	disturbance state d -- not the input -- absorbs model mismatch, giving
	offset-free tracking. Required for the nonlinear (radiative) model, where a
	linear Kalman filter does not apply. Same update(Q_applied, y) interface as
	GreyBoxKF.
	'''

	def __init__(self, *, C_f, C_c, h_fc, h_amb, T_amb, t_step, theta=0.0,
	             n_delay=0, K_Q=1.0, sigma=0.0, r_meas=0.04, pw_state=10.0,
	             pw_dist=0.5, px_state=1.0, px_dist=0.5, mhe_horizon=10):
		import do_mpc
		from collections import deque
		n = n_delay + 3
		self.n = n
		self._N = int(mhe_horizon)

		model = do_mpc.model.Model('continuous')
		q = [model.set_variable('_x', f'q{i}') for i in range(n_delay)]
		T_f = model.set_variable('_x', 'T_f')
		T_c = model.set_variable('_x', 'T_c')
		d = model.set_variable('_x', 'd')
		Q_app = model.set_variable('_tvp', 'Q_app')        # applied input, KNOWN
		if n_delay > 0:
			tau_d = theta / n_delay
			model.set_rhs('q0', (Q_app - q[0]) / tau_d, process_noise=True)
			for i in range(1, n_delay):
				model.set_rhs(f'q{i}', (q[i - 1] - q[i]) / tau_d, process_noise=True)
			heat_in = q[n_delay - 1]
		else:
			heat_in = Q_app
		model.set_rhs('T_f', (K_Q * heat_in - h_fc * (T_f - T_c)) / C_f, process_noise=True)
		model.set_rhs('T_c', (h_fc * (T_f - T_c) - h_amb * (T_c - T_amb)
		                      - _rad_loss(T_c, T_amb, sigma) + d) / C_c, process_noise=True)
		model.set_rhs('d', d * 0, process_noise=True)
		model.set_meas('T_c_meas', T_c, meas_noise=True)
		model.setup()

		mhe = do_mpc.estimator.MHE(model, [])
		mhe.set_param(n_horizon=self._N, t_step=t_step, store_full_solution=False,
		              meas_from_data=True,
		              nlpsol_opts={'ipopt.print_level': 0, 'print_time': 0, 'ipopt.sb': 'yes'})
		P_x = np.diag([px_state] * (n_delay + 2) + [px_dist])
		P_v = np.array([[1.0 / r_meas]])
		P_w = np.diag([pw_state] * (n_delay + 2) + [pw_dist])
		mhe.set_default_objective(P_x, P_v, P_w=P_w)

		self._qhist = deque([0.0] * (self._N + 1), maxlen=self._N + 1)
		tvp_template = mhe.get_tvp_template()
		def tvp_fun(t_now):
			for k in range(self._N + 1):
				tvp_template['_tvp', k, 'Q_app'] = self._qhist[k]
			return tvp_template
		mhe.set_tvp_fun(tvp_fun)
		mhe.setup()

		x0 = np.zeros((n, 1))
		x0[n_delay, 0] = T_amb
		x0[n_delay + 1, 0] = T_amb
		mhe.x0 = x0
		mhe.set_initial_guess()
		self.mhe = mhe

	def update(self, Q_applied, y_measured):
		self._qhist.append(float(Q_applied))
		return np.asarray(self.mhe.make_step(np.array([[float(y_measured)]]))).flatten()
