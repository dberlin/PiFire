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
import do_mpc


def build_do_mpc_model(*, C_f, C_c, h_fc, h_amb, T_amb, theta=0.0, n_delay=0, K_Q=1.0):
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
	model.set_rhs('T_c', (h_fc * (T_f - T_c) - h_amb * (T_c - T_amb) + d) / C_c)
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
