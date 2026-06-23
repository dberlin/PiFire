#!/usr/bin/env python3

'''
*****************************************
 PiFire MPC Controller (cascade: firing-rate + combustion allocator)
*****************************************

 Outer MPC manipulates a scalar firing-rate demand Q against a grey-box
 thermal model with an integrating-disturbance state (offset-free tracking via
 a Kalman filter). The inner combustion allocator maps Q to auger/fan. Returns
 a dict: {'cycle_ratio': auger_duty, 'fan': {'duty': pct or None}}.

 Operates internally in Celsius.

*****************************************
'''

import numpy as np
import do_mpc

from controller.base import ControllerBase
from controller.mpc_model import build_do_mpc_model, GreyBoxKF
from controller.mpc_allocator import allocate

_DEFAULTS = dict(
	n_horizon=20, t_step=25.0, control_period=1.0, Q_w=1.0, R_dQ=0.02,
	Q_min=5.0, Q_max=100.0, C_f=60.0, C_c=306.0, h_fc=2.0, h_amb=0.55,
	T_amb=20.0, fan_min_pct=40.0, fan_max_pct=100.0, enable_fan_input=False,
	est_q_temp=1e-2, est_q_dist=0.5, est_r_meas=0.04,
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

		# grey-box do-mpc model
		self.model = build_do_mpc_model(
			C_f=cfg['C_f'], C_c=cfg['C_c'], h_fc=cfg['h_fc'],
			h_amb=cfg['h_amb'], T_amb=cfg['T_amb'])

		# MPC controller
		self.mpc = do_mpc.controller.MPC(self.model)
		self.mpc.set_param(
			n_horizon=int(cfg['n_horizon']), t_step=float(cfg['t_step']),
			store_full_solution=False,
			nlpsol_opts={'ipopt.print_level': 0, 'print_time': 0,
			             'ipopt.sb': 'yes'})
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

		# estimator
		self.kf = GreyBoxKF(
			C_f=cfg['C_f'], C_c=cfg['C_c'], h_fc=cfg['h_fc'], h_amb=cfg['h_amb'],
			T_amb=cfg['T_amb'], t_step=float(cfg['t_step']),
			q_temp=cfg['est_q_temp'], q_dist=cfg['est_q_dist'],
			r_meas=cfg['est_r_meas'], x0=(cfg['T_amb'], cfg['T_amb'], 0.0))

		self.mpc.x0 = np.array([[cfg['T_amb']], [cfg['T_amb']], [0.0]])
		self.mpc.set_initial_guess()

	def set_target(self, set_point):
		self.set_point = set_point
		self._set_point_c = _to_c(set_point, self.units)
		self._last_Q = self.cfg['Q_min']

	def get_control_period(self):
		return float(self.cfg['control_period'])

	def update(self, current):
		y = _to_c(current, self.units)
		# 1) estimate states from the measurement
		x_hat = self.kf.update(self._last_Q, y)
		# 2) optimize firing rate Q. The box constraints bound Q; on any solver
		#    error we hold the previous move so the control loop never breaks.
		try:
			Q = float(np.asarray(self.mpc.make_step(x_hat.reshape(-1, 1))).flatten()[0])
		except Exception:
			Q = self._last_Q
		Q = float(np.clip(Q, self.cfg['Q_min'], self.cfg['Q_max']))
		self._last_Q = Q
		# 3) allocate Q -> actuators
		auger, fan_duty = allocate(
			Q, Q_min=self.cfg['Q_min'], Q_max=self.cfg['Q_max'],
			u_min=self.u_min, u_max=self.u_max,
			fan_min_pct=self.cfg['fan_min_pct'], fan_max_pct=self.cfg['fan_max_pct'],
			enable_fan=bool(self.cfg['enable_fan_input']))
		return {'cycle_ratio': auger, 'fan': {'duty': fan_duty}}
