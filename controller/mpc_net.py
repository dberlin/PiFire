#!/usr/bin/env python3

"""
*****************************************
 PiFire MPC Neural-Net Policy (IPOPT-free)
*****************************************

 A small feed-forward network that approximates the cascade MPC's firing-rate
 policy across the operating range, so the per-step NLP solve becomes a handful
 of numpy matmuls. PURE NUMPY -- no CasADi/IPOPT, no torch -- so a controller
 using this policy together with the EKF estimator needs only numpy/scipy.

 The net learns only the TRANSIENT residual; the offset-free steady-state firing
 rate is added analytically:

     Q = Q_ss(d, T_set) + net([state, u_prev, T_set])
     Q_ss = [h_amb*(T_set - T_amb) + rad_loss(T_set) - d] / K_Q

 which keeps steady-state offset-free BY CONSTRUCTION regardless of net error.

 The artifact (.npz) embeds the calibration it was trained for; the controller
 verifies it matches the active config and otherwise falls back to the NLP, so a
 stale net (e.g. after recalibration) can never silently mislead.

*****************************************
"""

import os
import numpy as np

_KELVIN = 273.15
# Calibration the net policy depends on. The net approximates the MPC's policy,
# which is a function of the full grey-box model, the MPC tuning AND the bounds --
# so ALL of these must match the active config or the net is stale.
_CALIB_FLOATS = (
	'C_f',
	'C_c',
	'h_fc',
	'h_amb',
	'T_amb',
	'theta',
	'K_Q',
	'sigma',
	'Q_w',
	'R_dQ',
	't_step',
	'Q_min',
	'Q_max',
)
_CALIB_INTS = ('n_delay', 'n_horizon', 'enable_fan_input')


def net_path_for(base_path, enable_fan):
	"""Fan-off uses base_path as-is; fan-on uses the _fan-suffixed sibling."""
	if not enable_fan:
		return base_path
	root, ext = os.path.splitext(base_path)
	return f'{root}_fan{ext}'


class NetPolicy:
	def __init__(self, weights, x_mean, x_std, r_mean, r_std, calib, sp_lo, sp_hi):
		self.weights = weights  # list of (W[in,out], b[out])
		self.x_mean = x_mean
		self.x_std = x_std
		self.r_mean = float(r_mean)
		self.r_std = float(r_std)
		self.calib = {k: float(calib[k]) for k in _CALIB_FLOATS}
		self.calib.update({k: int(calib[k]) for k in _CALIB_INTS})
		self.n_delay = int(calib['n_delay'])
		self.sp_lo = float(sp_lo)
		self.sp_hi = float(sp_hi)

	@classmethod
	def load(cls, path):
		z = np.load(path, allow_pickle=False)
		L = int(z['n_layers'])
		weights = [(z[f'W{i}'].astype(float), z[f'b{i}'].astype(float)) for i in range(L)]
		calib = {k: float(z[k]) for k in _CALIB_FLOATS}
		# enable_fan_input was added later; legacy artifacts lack it -> fan-off (0)
		calib.update({k: (int(z[k]) if k in z.files else 0) for k in _CALIB_INTS})
		return cls(
			weights,
			z['x_mean'].astype(float),
			z['x_std'].astype(float),
			float(z['r_mean']),
			float(z['r_std']),
			calib,
			float(z['sp_lo']),
			float(z['sp_hi']),
		)

	def matches_config(self, cfg, rtol=1e-3, atol=1e-12):
		"""True iff the net was trained for (essentially) this calibration."""
		for k in _CALIB_INTS:
			if k in cfg and int(cfg[k]) != self.calib[k]:
				return False
		for k in _CALIB_FLOATS:
			if k in cfg and not np.isclose(float(cfg[k]), self.calib[k], rtol=rtol, atol=atol):
				return False
		return True

	def _q_ss(self, d, set_c):
		rad = self.calib['sigma'] * ((set_c + _KELVIN) ** 4 - (self.calib['T_amb'] + _KELVIN) ** 4)
		return (self.calib['h_amb'] * (set_c - self.calib['T_amb']) + rad - d) / self.calib['K_Q']

	def _net_residual(self, x_in):
		z = (x_in - self.x_mean) / self.x_std
		for W, b in self.weights[:-1]:
			z = np.tanh(z @ W + b)
		W, b = self.weights[-1]
		out = float(np.reshape(z @ W + b, -1)[0])
		return out * self.r_std + self.r_mean

	def firing_rate(self, x_hat, u_prev, set_point_c):
		"""Firing-rate demand Q for the estimated state and target (Celsius)."""
		x = np.asarray(x_hat, dtype=float).reshape(-1)
		d = x[self.n_delay + 2]
		# the net only saw T_set in [sp_lo, sp_hi]; clip its input to avoid
		# extrapolation, but anchor Q_ss on the ACTUAL target (analytic, exact)
		ts_net = float(np.clip(set_point_c, self.sp_lo, self.sp_hi))
		inp = np.concatenate([x, [float(u_prev), ts_net]])
		Q = self._q_ss(d, float(set_point_c)) + self._net_residual(inp)
		return float(np.clip(Q, self.calib['Q_min'], self.calib['Q_max']))
