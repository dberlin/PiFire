#!/usr/bin/env python3
"""
ApproxMPC proof of concept (single setpoint, 110C). Approximate the production
nonlinear MPC's policy (state -> firing rate Q) with a small neural net so the
~15ms NLP solve becomes a ~us net evaluation. Estimation uses the production
default (EKF) via the controller's own estimator.

Pipeline: sample the real MPC -> compare three policies closed-loop on the
realistic plant: full MPC, a do-mpc FeedforwardNN approximator, and a residual
net (learns MPC_Q - Q_ss(d) with the analytic offset-free feedforward added
back). Reports band and per-step time vs the full MPC.
"""

import warnings, sys, time, os, shutil

warnings.filterwarnings('ignore')
sys.path.insert(0, '.')
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from controller.mpc import Controller, _DEFAULTS
from controller.mpc_model import _rad_loss
from controller.grill_sim import GrillSim
from do_mpc.approximateMPC import AMPCSampler, ApproxMPC, Trainer

SP = 110.0
CYCLE = {'u_min': 0.1, 'u_max': 0.9, 'HoldCycleTime': 25}
DATA = './docs/superpowers/experiments/_ampc_data'
ND = int(_DEFAULTS['n_delay'])
N = ND + 3  # state dim [q0..q3, T_f, T_c, d]

# state / input box for sampling + scaling
LBX = [0.0] * ND + [20.0, 20.0, -80.0]
UBX = [100.0] * ND + [220.0, 170.0, 80.0]
LBU, UBU = [5.0], [100.0]


def make_controller():
	c = Controller(dict(_DEFAULTS), 'C', dict(CYCLE))
	c.set_target(SP)
	return c


def build_approx():
	c = make_controller()
	mpc = c.mpc

	# 1) sample the real MPC (skip if data already present)
	have_data = os.path.isdir(DATA) and any(f.startswith('pifire') for f in os.listdir(DATA))
	if not have_data:
		sampler = AMPCSampler(mpc)
		sampler.settings.n_samples = 2000
		sampler.settings.dataset_name = 'pifire'
		sampler.settings.data_dir = DATA
		sampler.settings.closed_loop_flag = False
		sampler.settings.lbx, sampler.settings.ubx = LBX, UBX
		sampler.settings.lbu, sampler.settings.ubu = LBU, UBU
		sampler.setup()
		t0 = time.perf_counter()
		sampler.default_sampling()
		print(f'  sampled 2000 in {time.perf_counter() - t0:.0f}s', flush=True)
	else:
		print('  reusing existing sampled data', flush=True)

	# 2) configure + setup the approximator
	ampc = ApproxMPC(mpc)
	ampc.settings.n_hidden_layers = 2
	ampc.settings.n_neurons = 40
	tt = lambda v: torch.tensor(v, dtype=torch.float32).reshape(-1, 1)
	ampc.settings.lbx, ampc.settings.ubx = tt(LBX), tt(UBX)
	ampc.settings.lbu, ampc.settings.ubu = tt(LBU), tt(UBU)
	ampc.setup()

	# 3) train
	trainer = Trainer(ampc)
	trainer.settings.dataset_name = 'pifire'
	trainer.settings.data_dir = DATA
	trainer.settings.n_epochs = 200
	trainer.setup()
	t0 = time.perf_counter()
	trainer.default_training()
	print(f'  trained 200 epochs in {time.perf_counter() - t0:.0f}s', flush=True)
	return ampc


def run_full(seed=0, minutes=75):
	c = make_controller()
	plant = GrillSim(seed=seed)
	T, ms = [], []
	for w in range(int(minutes * 60 / 25)):
		a = time.perf_counter()
		out = c.update(plant.measured())
		ms.append((time.perf_counter() - a) * 1e3)
		r = float(np.clip(out['cycle_ratio'], 0.1, 0.9))
		fan = out['fan']['duty'] or 100.0
		on = int(round(r * 25))
		for s in range(25):
			plant.step(auger_on=(s < on), fan_frac=fan / 100.0)
			T.append(plant.true_Tc)
	return np.array(T), np.array(ms)


def run_approx(ampc, seed=0, minutes=75):
	# production estimator (EKF) for state, approx-net for the policy
	c = make_controller()
	est = c.estimator
	plant = GrillSim(seed=seed)
	qmin, qmax = _DEFAULTS['Q_min'], _DEFAULTS['Q_max']
	umin, umax = 0.1, 0.9
	T, ms_est, ms_net = [], [], []
	lastQ = qmin
	for w in range(int(minutes * 60 / 25)):
		y = plant.measured()
		a = time.perf_counter()
		xh = est.update(lastQ, y)
		ms_est.append((time.perf_counter() - a) * 1e3)
		a = time.perf_counter()
		Q = float(np.asarray(ampc.make_step(xh.reshape(-1, 1), u_prev=np.array([[lastQ]]))).flatten()[0])
		ms_net.append((time.perf_counter() - a) * 1e3)
		Q = float(np.clip(Q, qmin, qmax))
		lastQ = Q
		r = umin + (Q - qmin) / (qmax - qmin) * (umax - umin)
		on = int(round(r * 25))
		for s in range(25):
			plant.step(auger_on=(s < on), fan_frac=1.0)
			T.append(plant.true_Tc)
	return np.array(T), np.array(ms_est), np.array(ms_net)


def band(T, win=0.4):
	sm = np.arange(len(T)) >= len(T) * win
	e = T[sm] - SP
	return np.sqrt(np.mean(e**2)), np.max(np.abs(e)), np.mean(e)


# ---------------------------------------------------------------------------
# Residual approxMPC: net learns MPC_Q - Q_ss(d); inference adds analytic Q_ss
# back. The disturbance d enters T_c as an additive heat rate, so at steady
# state the offset-free firing rate is exactly
#   Q_ss = [h_amb*(T_set-T_amb) + rad_loss(T_set) - d] / K_Q
# Anchoring on Q_ss makes steady-state offset-free BY CONSTRUCTION (residual->0),
# regardless of net approximation error -- and it tracks the real operating d
# the estimator produces, which uniform-box sampling under-covers.
DCFG = _DEFAULTS
DIDX = int(_DEFAULTS['n_delay']) + 2  # index of d in the state vector


def Q_ss(d, set_c):
	return (DCFG['h_amb'] * (set_c - DCFG['T_amb']) + _rad_loss(set_c, DCFG['T_amb'], DCFG['sigma']) - d) / DCFG['K_Q']


class ResidualNet(nn.Module):
	def __init__(self, n_in, h=64):
		super().__init__()
		self.net = nn.Sequential(nn.Linear(n_in, h), nn.Tanh(), nn.Linear(h, h), nn.Tanh(), nn.Linear(h, 1))

	def forward(self, x):
		return self.net(x)


SAMPLES_NPZ = os.path.join(DATA, 'pifire_samples.npz')


def _load_samples():
	# Prefer the structured parallel sampler's npz (sample_mpc.py); fall back to
	# the do-mpc uniform-box pkl.
	if os.path.exists(SAMPLES_NPZ):
		z = np.load(SAMPLES_NPZ)
		print(f'  using structured samples: {len(z["u0"])} from {SAMPLES_NPZ}', flush=True)
		return z['X0'], z['u_prev'].flatten(), z['u0'].flatten()
	df = pd.read_pickle(os.path.join(DATA, 'pifire', 'data_pifire_opt.pkl'))
	print(f'  using do-mpc uniform-box samples: {len(df)}', flush=True)
	return (
		np.stack([np.asarray(r).flatten() for r in df['x0']]),
		np.array([np.asarray(r).flatten()[0] for r in df['u_prev']]),
		np.array([np.asarray(r).flatten()[0] for r in df['u0']]),
	)


def build_residual_net(epochs=600):
	X0, UP, U0 = _load_samples()  # [N,7],[N],[N]
	Xin = np.column_stack([X0, UP])  # [N,8]
	resid = U0 - Q_ss(X0[:, DIDX], SP)  # target

	# z-score standardize in/out (stored for inference)
	xm, xs = Xin.mean(0), Xin.std(0) + 1e-8
	rm, rs = resid.mean(), resid.std() + 1e-8
	Xs = torch.tensor((Xin - xm) / xs, dtype=torch.float32)
	ys = torch.tensor(((resid - rm) / rs).reshape(-1, 1), dtype=torch.float32)

	torch.manual_seed(0)
	n_val = int(0.15 * len(Xs))
	perm = torch.randperm(len(Xs))
	vi, ti = perm[:n_val], perm[n_val:]
	net = ResidualNet(Xin.shape[1])
	opt = torch.optim.Adam(net.parameters(), lr=2e-3, weight_decay=1e-5)
	lossf = nn.MSELoss()
	t0 = time.perf_counter()
	for ep in range(epochs):
		net.train()
		opt.zero_grad()
		loss = lossf(net(Xs[ti]), ys[ti])
		loss.backward()
		opt.step()
	net.eval()
	with torch.no_grad():
		vloss = lossf(net(Xs[vi]), ys[vi]).item()
	print(
		f'  residual net trained {epochs} epochs in {time.perf_counter() - t0:.0f}s (val_mse={vloss:.4f})', flush=True
	)
	stats = (torch.tensor(xm, dtype=torch.float32), torch.tensor(xs, dtype=torch.float32), float(rm), float(rs))
	return net, stats


def run_residual(net, stats, seed=0, minutes=75):
	xm, xs, rm, rs = stats
	c = make_controller()
	est = c.estimator
	plant = GrillSim(seed=seed)
	qmin, qmax = DCFG['Q_min'], DCFG['Q_max']
	umin, umax = 0.1, 0.9
	T, ms_est, ms_net = [], [], []
	lastQ = qmin
	for w in range(int(minutes * 60 / 25)):
		y = plant.measured()
		a = time.perf_counter()
		xh = est.update(lastQ, y).flatten()
		ms_est.append((time.perf_counter() - a) * 1e3)
		a = time.perf_counter()
		with torch.no_grad():
			inp = (torch.tensor(np.append(xh, lastQ), dtype=torch.float32) - xm) / xs
			resid = net(inp.reshape(1, -1)).item() * rs + rm
		Q = Q_ss(xh[DIDX], c._set_point_c) + resid  # analytic feedforward + learned transient
		ms_net.append((time.perf_counter() - a) * 1e3)
		Q = float(np.clip(Q, qmin, qmax))
		lastQ = Q
		r = umin + (Q - qmin) / (qmax - qmin) * (umax - umin)
		on = int(round(r * 25))
		for s in range(25):
			plant.step(auger_on=(s < on), fan_frac=1.0)
			T.append(plant.true_Tc)
	return np.array(T), np.array(ms_est), np.array(ms_net)


if __name__ == '__main__':
	print('Building approxMPC (sample + train) ...', flush=True)
	ampc = build_approx()
	print('\nClosed loop at 110C:', flush=True)
	Tf, msf = run_full()
	r, m, b = band(Tf)
	print(
		f'  FULL  MPC  : band RMS={r:.2f} max={m:.2f} bias={b:+.2f}  solve(EKF+MPC) median={np.median(msf[2:]):.0f}ms'
	)
	Ta, mse, msn = run_approx(ampc)
	r, m, b = band(Ta)
	print(
		f'  APPROX MPC : band RMS={r:.2f} max={m:.2f} bias={b:+.2f}  est median={np.median(mse[2:]):.2f}ms  net median={np.median(msn[2:]):.2f}ms'
	)

	print('\nResidual approxMPC (net learns MPC_Q - Q_ss(d), + analytic feedforward):', flush=True)
	rnet, stats = build_residual_net()
	Tr, rse, rsn = run_residual(rnet, stats)
	r, m, b = band(Tr)
	print(
		f'  RESID  MPC : band RMS={r:.2f} max={m:.2f} bias={b:+.2f}  est median={np.median(rse[2:]):.2f}ms  net median={np.median(rsn[2:]):.2f}ms'
	)
