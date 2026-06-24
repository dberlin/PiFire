#!/usr/bin/env python3

'''
*****************************************
 PiFire MPC Controller (cascade: firing-rate + combustion allocator)
*****************************************

 Outer loop manipulates a scalar firing-rate demand Q against a grey-box thermal
 model with an integrating-disturbance state (offset-free tracking). The inner
 combustion allocator maps Q to auger/fan. Returns a dict:
 {'cycle_ratio': auger_duty, 'fan': {'duty': pct or None}}.

 State is estimated by the EKF (default), MHE, or KF. The firing-rate policy is
 either the do-mpc NLP solve ('nlp') or a pure-numpy neural approximation
 ('net', no IPOPT/CasADi) that falls back to the NLP if its artifact is missing
 or mismatched. net policy + EKF needs only numpy/scipy at runtime.

 Operates internally in Celsius.

*****************************************
'''

import os
import time

import numpy as np
# do_mpc (CasADi/IPOPT) is imported lazily only when the NLP policy is built; the
# net policy + EKF path is pure numpy/scipy and never imports it.

from controller.base import ControllerBase
from controller.mpc_model import build_do_mpc_model, GreyBoxKF, GreyBoxEKF, GreyBoxMHE
from controller.mpc_allocator import allocate

_DEFAULTS = dict(
	n_horizon=24, t_step=25.0, control_period=25.0, Q_w=1.0, R_dQ=1.0,
	Q_min=5.0, Q_max=100.0,
	# Nominal grey-box thermal params -- CALIBRATE to your grill via update_mpc.py.
	C_f=9.0, C_c=320.0, h_fc=1.3, h_amb=0.50, T_amb=20.0,
	theta=50.0, n_delay=4, K_Q=3.5, sigma=1.4e-9,
	# 'ekf' linearizes the nonlinear radiative term each step (~us, default);
	# 'mhe' solves an NLP (nonlinear, slower); 'kf' is linear-only.
	estimator='ekf',
	# Firing-rate policy: 'nlp' solves the MPC NLP each step (do_mpc/IPOPT);
	# 'net' uses a pure-numpy neural approximation of the policy (no IPOPT/CasADi)
	# and falls back to 'nlp' if the artifact is missing or its calibration does
	# not match this config.
	policy='nlp', policy_net_path='./controller/mpc_policy_net.npz',
	fan_min_pct=40.0, fan_max_pct=100.0, enable_fan_input=False,
	# est_q_dist deliberately slow: a fast disturbance estimate chases unmeasured
	# transients and worsens setpoint-step overshoot; 0.05 cut step overshoot ~30%
	# with no change to the steady-state band. See experiments/mpc_overshoot_fix.py.
	est_q_temp=1e-2, est_q_dist=0.05, est_r_meas=0.04,
	# Optional logging of (time_s, temp_c, Q) for the offline calibration utility.
	log_data=False, log_path='./controller/mpc_calibration_log.csv',
)


def _to_c(value, units):
	return (value - 32.0) * 5.0 / 9.0 if units == 'F' else value


class Controller(ControllerBase):
	def __init__(self, config, units, cycle_data):
		super().__init__(config, units, cycle_data)
		self.function_list.append('get_control_period')

		cfg = dict(_DEFAULTS)
		cfg.update(config or {})
		self.cfg = cfg
		self.u_min = cycle_data.get('u_min', 0.1)
		self.u_max = cycle_data.get('u_max', 0.9)

		self._set_point_c = 0.0
		self._last_Q = cfg['Q_min']

		n_delay = int(cfg['n_delay'])

		# State/disturbance estimator (independent of the policy). EKF linearizes
		# the nonlinear radiative term each step (default); MHE solves an NLP; KF
		# is linear-only. All expose update(Q_applied, y) -> state estimate and are
		# discretized at the control period so faster re-solves track elapsed time.
		est_kind = str(cfg.get('estimator', 'ekf')).lower()
		if est_kind == 'kf':
			self.estimator = GreyBoxKF(
				C_f=cfg['C_f'], C_c=cfg['C_c'], h_fc=cfg['h_fc'], h_amb=cfg['h_amb'],
				T_amb=cfg['T_amb'], t_step=float(cfg['control_period']),
				q_temp=cfg['est_q_temp'], q_dist=cfg['est_q_dist'],
				r_meas=cfg['est_r_meas'], theta=float(cfg['theta']), n_delay=n_delay,
				K_Q=float(cfg['K_Q']))
		elif est_kind == 'ekf':
			self.estimator = GreyBoxEKF(
				C_f=cfg['C_f'], C_c=cfg['C_c'], h_fc=cfg['h_fc'], h_amb=cfg['h_amb'],
				T_amb=cfg['T_amb'], t_step=float(cfg['control_period']),
				q_temp=cfg['est_q_temp'], q_dist=cfg['est_q_dist'],
				r_meas=cfg['est_r_meas'], theta=float(cfg['theta']), n_delay=n_delay,
				K_Q=float(cfg['K_Q']), sigma=float(cfg['sigma']))
		else:
			self.estimator = GreyBoxMHE(
				C_f=cfg['C_f'], C_c=cfg['C_c'], h_fc=cfg['h_fc'], h_amb=cfg['h_amb'],
				T_amb=cfg['T_amb'], t_step=float(cfg['control_period']),
				theta=float(cfg['theta']), n_delay=n_delay, K_Q=float(cfg['K_Q']),
				sigma=float(cfg['sigma']), r_meas=cfg['est_r_meas'])

		# Firing-rate policy. 'net' uses the pure-numpy neural approximation (no
		# IPOPT/CasADi); it falls back to the NLP if the artifact is missing or its
		# calibration does not match this config.
		self.model = None
		self.mpc = None
		self._net = None
		if str(cfg.get('policy', 'nlp')).lower() == 'net':
			self._net = self._load_net_policy(cfg)
		if self._net is None:
			self._build_nlp(cfg, n_delay)

		# Optional data logging for offline calibration (update_mpc.py): one
		# (time_s, temp_c, Q) row per control step. Logs internal Celsius.
		self._log_path = cfg['log_path'] if cfg.get('log_data') else None
		if self._log_path and (not os.path.exists(self._log_path)
		                        or os.path.getsize(self._log_path) == 0):
			try:
				with open(self._log_path, 'a') as f:
					f.write('time_s,temp_c,Q\n')
			except OSError:
				self._log_path = None      # disable logging if the path is unwritable

	def _load_net_policy(self, cfg):
		'''Load the numpy net policy, or return None to fall back to the NLP.'''
		from controller.mpc_net import NetPolicy
		path = cfg.get('policy_net_path')
		if not path or not os.path.exists(path):
			print(f'[mpc] policy=net but artifact not found ({path}); using NLP')
			return None
		try:
			net = NetPolicy.load(path)
		except Exception as e:
			print(f'[mpc] could not load net policy ({e}); using NLP')
			return None
		if not net.matches_config(cfg):
			print('[mpc] net policy calibration does not match config; using NLP')
			return None
		return net

	def _build_nlp(self, cfg, n_delay):
		'''Build the do-mpc NLP policy (lazily imports do_mpc/CasADi/IPOPT).'''
		import do_mpc
		self.model = build_do_mpc_model(
			C_f=cfg['C_f'], C_c=cfg['C_c'], h_fc=cfg['h_fc'],
			h_amb=cfg['h_amb'], T_amb=cfg['T_amb'],
			theta=float(cfg['theta']), n_delay=n_delay, K_Q=float(cfg['K_Q']),
			sigma=float(cfg['sigma']))
		self.mpc = do_mpc.controller.MPC(self.model)
		self.mpc.set_param(
			n_horizon=int(cfg['n_horizon']), t_step=float(cfg['t_step']),
			store_full_solution=False,
			nlpsol_opts={'ipopt.print_level': 0, 'print_time': 0, 'ipopt.sb': 'yes'})
		T_c = self.model.x['T_c']
		T_set = self.model.tvp['T_set']
		self.mpc.set_objective(mterm=cfg['Q_w'] * (T_c - T_set) ** 2,
		                       lterm=cfg['Q_w'] * (T_c - T_set) ** 2)
		self.mpc.set_rterm(Q=cfg['R_dQ'])
		self.mpc.bounds['lower', '_u', 'Q'] = cfg['Q_min']
		self.mpc.bounds['upper', '_u', 'Q'] = cfg['Q_max']

		tvp_template = self.mpc.get_tvp_template()
		def tvp_fun(t_now):
			for k in range(int(cfg['n_horizon']) + 1):
				tvp_template['_tvp', k, 'T_set'] = self._set_point_c
			return tvp_template
		self.mpc.set_tvp_fun(tvp_fun)
		self.mpc.setup()

		x0 = np.zeros((n_delay + 3, 1))
		x0[n_delay, 0] = cfg['T_amb']        # T_f
		x0[n_delay + 1, 0] = cfg['T_amb']    # T_c
		self.mpc.x0 = x0
		self.mpc.set_initial_guess()

	def _log_row(self, temp_c, Q):
		try:
			with open(self._log_path, 'a') as f:
				f.write(f'{time.time():.3f},{temp_c:.3f},{Q:.4f}\n')
		except OSError:
			self._log_path = None      # stop trying after a write failure

	def set_target(self, set_point):
		self.set_point = set_point
		self._set_point_c = _to_c(set_point, self.units)
		self._last_Q = self.cfg['Q_min']

	def get_control_period(self):
		return float(self.cfg['control_period'])

	def update(self, current):
		y = _to_c(current, self.units)
		# 1) estimate states from the measurement
		x_hat = self.estimator.update(self._last_Q, y)
		# 2) compute firing rate Q from the active policy (net or NLP). On any
		#    error we hold the previous move so the control loop never breaks.
		try:
			if self._net is not None:
				Q = self._net.firing_rate(x_hat, self._last_Q, self._set_point_c)
			else:
				Q = float(np.asarray(self.mpc.make_step(x_hat.reshape(-1, 1))).flatten()[0])
		except Exception:
			Q = self._last_Q
		Q = float(np.clip(Q, self.cfg['Q_min'], self.cfg['Q_max']))
		self._last_Q = Q
		if self._log_path:
			self._log_row(y, Q)
		# 3) allocate Q -> actuators
		auger, fan_duty = allocate(
			Q, Q_min=self.cfg['Q_min'], Q_max=self.cfg['Q_max'],
			u_min=self.u_min, u_max=self.u_max,
			fan_min_pct=self.cfg['fan_min_pct'], fan_max_pct=self.cfg['fan_max_pct'],
			enable_fan=bool(self.cfg['enable_fan_input']))
		return {'cycle_ratio': auger, 'fan': {'duty': fan_duty}}
