#!/usr/bin/env python3

'''
*****************************************
 PiFire MPC Grey-box Thermal Model + Estimator
*****************************************

 Two lumped thermal masses (firepot T_f, chamber T_c) driven by a scalar
 firing-rate Q, plus an integrating disturbance state d for offset-free
 tracking. Provides the do-mpc model used by the controller and a Kalman
 filter over the same augmented linear model used as the state/disturbance
 estimator.

*****************************************
'''

import numpy as np
from scipy.linalg import expm
import do_mpc


def build_do_mpc_model(*, C_f, C_c, h_fc, h_amb, T_amb):
	model = do_mpc.model.Model('continuous')
	T_f = model.set_variable('_x', 'T_f')
	T_c = model.set_variable('_x', 'T_c')
	d = model.set_variable('_x', 'd')
	Q = model.set_variable('_u', 'Q')
	model.set_variable('_tvp', 'T_set')
	model.set_rhs('T_f', (Q - h_fc * (T_f - T_c)) / C_f)
	model.set_rhs('T_c', (h_fc * (T_f - T_c) - h_amb * (T_c - T_amb) + d) / C_c)
	model.set_rhs('d', d * 0)
	model.setup()
	return model


class GreyBoxKF:
	'''
	Kalman filter over the augmented linear model x = [T_f, T_c, d], input Q.
	The constant ambient term enters as an affine input (held at 1).
	'''

	def __init__(self, *, C_f, C_c, h_fc, h_amb, T_amb, t_step,
	             q_temp, q_dist, r_meas, x0=(20.0, 20.0, 0.0)):
		Ac = np.array([
			[-h_fc / C_f,  h_fc / C_f,          0.0],
			[ h_fc / C_c, -(h_fc + h_amb) / C_c, 1.0 / C_c],
			[ 0.0,         0.0,                  0.0],
		])
		# columns: [Q input, affine constant=1]
		Baug = np.array([
			[1.0 / C_f, 0.0],
			[0.0,       h_amb * T_amb / C_c],
			[0.0,       0.0],
		])
		M = np.zeros((5, 5))
		M[:3, :3] = Ac
		M[:3, 3:] = Baug
		Md = expm(M * t_step)
		self.Ad = Md[:3, :3]
		self.Bd = Md[:3, 3:4]      # for Q
		self.bd = Md[:3, 4:5]      # affine (constant input = 1)
		self.H = np.array([[0.0, 1.0, 0.0]])
		self.Qkf = np.diag([q_temp, q_temp, q_dist])
		self.Rkf = np.array([[r_meas]])
		self.x = np.array(x0, dtype=float)
		self.P = np.eye(3) * 5.0

	def update(self, Q_applied, y_measured):
		# predict
		self.x = self.Ad @ self.x + self.Bd.flatten() * Q_applied + self.bd.flatten()
		self.P = self.Ad @ self.P @ self.Ad.T + self.Qkf
		# update
		S = self.H @ self.P @ self.H.T + self.Rkf
		K = (self.P @ self.H.T) / S
		self.x = self.x + K.flatten() * (y_measured - (self.H @ self.x)[0])
		self.P = (np.eye(3) - K @ self.H) @ self.P
		return self.x
