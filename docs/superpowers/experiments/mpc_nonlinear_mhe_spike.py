#!/usr/bin/env python3
"""
Spike (for giggles): NONLINEAR grey-box model + do-mpc MHE.

Two changes from the linear KF design:
  1. Nonlinear chamber loss: convective + radiative (T^4). The MPC is now a
     genuine nonlinear NLP (do-mpc/IPOPT handles it natively).
  2. The MHE models the control input Q as a KNOWN time-varying parameter
     (fed the applied-input history), NOT a free decision variable. That is the
     fix for the earlier MHE failure -- with Q pinned, the disturbance state d
     must absorb model mismatch, giving offset-free tracking.

Plant is deliberately mismatched (params + radiative coeff offset, AFR
efficiency, ambient drift, lid event, noise).
"""

import warnings, time
from collections import deque

warnings.filterwarnings('ignore')
import numpy as np
import do_mpc

np.random.seed(0)

Ts = 25.0
T_END = 7200.0
N = int(T_END / Ts)
Q_MIN, Q_MAX = 5.0, 130.0
U_MIN, U_MAX = 0.10, 0.90
F_MIN, F_MAX = 40.0, 100.0
AFR_OPT, AFR_SIGMA = 1.0, 0.28
FUEL_TO_HEAT = Q_MAX / U_MAX

# nominal model (MPC + MHE)
C_f, C_c = 60.0, 306.0
h_fc, h_amb = 2.0, 0.30
SIGMA = 2.0e-9  # radiative coefficient (the nonlinearity)
T_AMB_NOM = 20.0
KELVIN = 273.15

# truth (mismatched ~15-20%)
C_f_t, C_c_t = 70.0, 350.0
h_fc_t, h_amb_t = 1.70, 0.34
SIGMA_t = 2.4e-9


def _rad(Tc, Tamb):
	return (Tc + KELVIN) ** 4 - (Tamb + KELVIN) ** 4


def allocate(Q):
	frac = max(0.0, min(1.0, (Q - Q_MIN) / (Q_MAX - Q_MIN)))
	return U_MIN + frac * (U_MAX - U_MIN), F_MIN + frac * (F_MAX - F_MIN)


def combustion_heat(auger, fan):
	fuel = max(auger, 1e-6)
	fuel_frac = (fuel - U_MIN) / (U_MAX - U_MIN)
	air_frac = (fan - F_MIN) / (F_MAX - F_MIN)
	afr = (air_frac + 1e-6) / (fuel_frac + 1e-6)
	eff = np.exp(-((afr - AFR_OPT) ** 2) / (2 * AFR_SIGMA**2))
	return FUEL_TO_HEAT * fuel * eff, afr


# ---------------- nonlinear MPC (input Q) ----------------
def build_mpc():
	m = do_mpc.model.Model('continuous')
	T_f = m.set_variable('_x', 'T_f')
	T_c = m.set_variable('_x', 'T_c')
	d = m.set_variable('_x', 'd')
	Q = m.set_variable('_u', 'Q')
	T_set = m.set_variable('_tvp', 'T_set')
	m.set_rhs('T_f', (Q - h_fc * (T_f - T_c)) / C_f)
	m.set_rhs('T_c', (h_fc * (T_f - T_c) - h_amb * (T_c - T_AMB_NOM) - SIGMA * _rad(T_c, T_AMB_NOM) + d) / C_c)
	m.set_rhs('d', d * 0)
	m.setup()
	mpc = do_mpc.controller.MPC(m)
	mpc.set_param(
		n_horizon=20,
		t_step=Ts,
		store_full_solution=False,
		nlpsol_opts={'ipopt.print_level': 0, 'print_time': 0, 'ipopt.sb': 'yes'},
	)
	mpc.set_objective(mterm=(T_c - T_set) ** 2, lterm=(T_c - T_set) ** 2)
	mpc.set_rterm(Q=0.02)
	mpc.bounds['lower', '_u', 'Q'] = Q_MIN
	mpc.bounds['upper', '_u', 'Q'] = Q_MAX
	tvp = mpc.get_tvp_template()
	state = {'sp': 107.0}

	def tvp_fun(t_now):
		for k in range(21):
			tvp['_tvp', k, 'T_set'] = state['sp']
		return tvp

	mpc.set_tvp_fun(tvp_fun)
	mpc.setup()
	return mpc, state


# ---------------- nonlinear MHE (Q is a KNOWN tvp, not a free input) ----------------
def build_mhe():
	NH = 10
	m = do_mpc.model.Model('continuous')
	T_f = m.set_variable('_x', 'T_f')
	T_c = m.set_variable('_x', 'T_c')
	d = m.set_variable('_x', 'd')
	Q_app = m.set_variable('_tvp', 'Q_app')  # applied input, KNOWN
	m.set_rhs('T_f', (Q_app - h_fc * (T_f - T_c)) / C_f, process_noise=True)
	m.set_rhs(
		'T_c',
		(h_fc * (T_f - T_c) - h_amb * (T_c - T_AMB_NOM) - SIGMA * _rad(T_c, T_AMB_NOM) + d) / C_c,
		process_noise=True,
	)
	m.set_rhs('d', d * 0, process_noise=True)
	m.set_meas('T_c_meas', T_c, meas_noise=True)
	m.setup()

	mhe = do_mpc.estimator.MHE(m, [])
	mhe.set_param(
		n_horizon=NH,
		t_step=Ts,
		store_full_solution=False,
		meas_from_data=True,
		nlpsol_opts={'ipopt.print_level': 0, 'print_time': 0, 'ipopt.sb': 'yes'},
	)
	P_x = np.diag([1.0, 1.0, 0.01])
	P_v = np.array([[1.0 / 0.04]])
	P_w = np.diag([10.0, 10.0, 0.05])  # d cheap to move -> tracks disturbance
	mhe.set_default_objective(P_x, P_v, P_w=P_w)

	qhist = deque([Q_MIN] * (NH + 1), maxlen=NH + 1)
	tvp = mhe.get_tvp_template()

	def tvp_fun(t_now):
		for k in range(NH + 1):
			tvp['_tvp', k, 'Q_app'] = qhist[k]
		return tvp

	mhe.set_tvp_fun(tvp_fun)
	mhe.setup()
	return mhe, qhist


# ---------------- nonlinear truth plant ----------------
def build_truth():
	m = do_mpc.model.Model('continuous')
	T_f = m.set_variable('_x', 'T_f')
	T_c = m.set_variable('_x', 'T_c')
	Qh = m.set_variable('_u', 'Qh')
	T_amb = m.set_variable('_tvp', 'T_amb')
	lid = m.set_variable('_tvp', 'lid')
	m.set_rhs('T_f', (Qh - h_fc_t * (T_f - T_c)) / C_f_t)
	m.set_rhs('T_c', (h_fc_t * (T_f - T_c) - h_amb_t * lid * (T_c - T_amb) - SIGMA_t * lid * _rad(T_c, T_amb)) / C_c_t)
	m.setup()
	sim = do_mpc.simulator.Simulator(m)
	sim.set_param(t_step=Ts)
	tvp = sim.get_tvp_template()

	def tvp_fun(t_now):
		tvp['T_amb'] = 18.0 - 8.0 * (t_now / T_END)
		tvp['lid'] = 3.0 if 3000.0 <= t_now < 3090.0 else 1.0
		return tvp

	sim.set_tvp_fun(tvp_fun)
	sim.setup()
	sim.x0 = np.array([[20.0], [20.0]])
	sim.set_initial_guess()
	return sim


def run():
	mpc, mpc_state = build_mpc()
	mhe, qhist = build_mhe()
	sim = build_truth()
	x0 = np.array([[20.0], [20.0], [0.0]])
	mhe.x0 = x0
	mhe.set_initial_guess()
	mpc.x0 = x0
	mpc.set_initial_guess()

	log = {k: [] for k in ['t', 'Tc', 'sp', 'dhat', 'afr']}
	mhe_ms, mpc_ms = [], []
	Qprev = Q_MIN
	for k in range(N):
		t = k * Ts
		mpc_state['sp'] = 107.0 if t < 3600 else 121.0
		y = float(sim.x0['T_c']) + np.random.normal(0, 0.2)
		qhist.append(Qprev)  # newest applied input
		t0 = time.perf_counter()
		xhat = np.asarray(mhe.make_step(np.array([[y]]))).flatten()
		t1 = time.perf_counter()
		u = np.asarray(mpc.make_step(xhat.reshape(-1, 1))).flatten()
		t2 = time.perf_counter()
		Qprev = float(np.clip(u[0], Q_MIN, Q_MAX))
		au, fa = allocate(Qprev)
		Qh, afr = combustion_heat(au, fa)
		sim.make_step(np.array([[Qh]]))
		log['t'].append(t)
		log['Tc'].append(float(sim.x0['T_c']))
		log['sp'].append(mpc_state['sp'])
		log['dhat'].append(xhat[2])
		log['afr'].append(afr)
		mhe_ms.append((t1 - t0) * 1e3)
		mpc_ms.append((t2 - t1) * 1e3)
	return {k: np.array(v) for k, v in log.items()}, np.array(mhe_ms), np.array(mpc_ms)


if __name__ == '__main__':
	print('Running NONLINEAR model + MHE closed loop ...')
	L, mhe_ms, mpc_ms = run()
	t, Tc, sp = L['t'], L['Tc'], L['sp']
	err = Tc - sp
	sm = ((t >= 1500) & (t < 2900)) | ((t >= 4600) & (t < 7200))
	print(f'STEADY band |error| max : {np.max(np.abs(err[sm])):.2f} C')
	print(f'STEADY RMS / mean bias  : {np.sqrt(np.mean(err[sm] ** 2)):.2f} C / {np.mean(err[sm]):+.2f} C')
	print(f'within +-1.0C fraction  : {100 * np.mean(np.abs(err[sm]) <= 1.0):.1f} %')
	print(
		f'd-hat range (steady)    : {L["dhat"][sm].min():+.2f} .. {L["dhat"][sm].max():+.2f}  (engaged => offset-free)'
	)
	print(f'AFR range (steady)      : {L["afr"][sm].min():.2f} .. {L["afr"][sm].max():.2f}')
	warm = slice(11, None)
	print(
		f'MHE warm  : mean={mhe_ms[warm].mean():.1f} p95={np.percentile(mhe_ms[warm], 95):.1f} max={mhe_ms[warm].max():.1f} ms'
	)
	print(
		f'MPC warm  : mean={mpc_ms[warm].mean():.1f} ms   TOTAL/step={mhe_ms[warm].mean() + mpc_ms[warm].mean():.1f} ms'
	)
