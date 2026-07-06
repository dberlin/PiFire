#!/usr/bin/env python3
"""
Parallel, physically-structured sampler of the production MPC policy
(state, u_prev) -> optimal firing rate Q, for training the residual approxMPC net.

Improvements over do-mpc's default AMPCSampler (uniform independent box, single
process):
  * STRUCTURED states -- only physically-reachable configurations:
      - firepot T_f coupled to chamber T_c and firing level via the steady heat
        balance T_f ~= T_c + K_Q*Q/h_fc (the box sampled T_f<T_c, which never
        happens and wastes net capacity),
      - delay chain q0..q3 correlated around a firing level with a ramp gradient
        (both signs) -- real ramp-up/ramp-down transients, not random chains,
      - T_c drawn from a mixture concentrated near the setpoint (steady-state
        accuracy) plus the cold-start approach range.
  * SPACE-FILLING via Latin Hypercube over the global coordinates.
  * PARALLEL across CPU cores; each worker builds its own MPC (CasADi is not
    picklable) and solves a chunk. Saves to an .npz the residual net loads.

control.py must never be imported (module-level while True). We import only
controller.mpc, which is import-safe.
"""

import warnings, sys, os, time, argparse

warnings.filterwarnings('ignore')
sys.path.insert(0, '.')
import numpy as np
import multiprocessing as mp
from scipy.stats import qmc

from controller.mpc import Controller, _DEFAULTS
from controller.mpc_allocator import allocate
from controller.grill_sim import GrillSim

SP = 110.0
CYCLE = {'u_min': 0.1, 'u_max': 0.9, 'HoldCycleTime': 25}
ND = int(_DEFAULTS['n_delay'])
OUT = './docs/superpowers/experiments/_ampc_data/pifire_samples.npz'
OUT_SPAN = './docs/superpowers/experiments/_ampc_data/pifire_span.npz'


def generate_states(n, *, seed=0, op_frac=0.55):
	"""Physically-structured, space-filling (state, u_prev) draws."""
	Qmin, Qmax = _DEFAULTS['Q_min'], _DEFAULTS['Q_max']
	Kq, hfc = _DEFAULTS['K_Q'], _DEFAULTS['h_fc']
	rng = np.random.default_rng(seed)
	# LHS over global coords: [Q_lvl, T_c, slope, d, u_prev, mix]
	U = qmc.LatinHypercube(d=6, seed=seed).random(n)

	Q_lvl = Qmin + U[:, 0] * (Qmax - Qmin)  # quasi-steady firing
	# chamber: mixture of operating (near setpoint) and cold-start approach
	T_c_op = np.clip(SP + (U[:, 1] - 0.5) * 2 * 22, 20, 145)
	T_c_app = 20.0 + U[:, 1] * (135.0 - 20.0)
	T_c = np.where(U[:, 5] < op_frac, T_c_op, T_c_app)
	slope = (U[:, 2] - 0.5) * 2 * 22.0  # +-22/stage gradient
	d = (U[:, 3] - 0.5) * 2 * 80.0  # broad disturbance
	u_prev = np.clip(Q_lvl + (U[:, 4] - 0.5) * 2 * 15.0, Qmin, Qmax)

	# delay chain around the firing level with the ramp gradient + small noise
	mid = (ND - 1) / 2.0
	q = np.stack([Q_lvl + slope * (i - mid) for i in range(ND)], axis=1)
	q = np.clip(q + rng.normal(0, 3.0, size=q.shape), Qmin, Qmax)
	# firepot: physically-consistent superheat for the firing level + transient spread
	superheat = Kq * Q_lvl / hfc
	T_f = np.clip(T_c + superheat + rng.normal(0, 25.0, size=n), T_c - 15.0, 360.0)

	X0 = np.column_stack([q, T_f, T_c, d])  # [n, ND+3]
	return X0, u_prev


# ----- parallel solve: each worker builds its own MPC once -----------------
_MPC = None


def _init_worker():
	global _MPC
	warnings.filterwarnings('ignore')
	c = Controller(dict(_DEFAULTS), 'C', dict(CYCLE))
	c.set_target(SP)
	_MPC = c.mpc


def _solve_chunk(chunk):
	X0c, Upc = chunk
	n = X0c.shape[0]
	U0 = np.full(n, np.nan)
	ok = np.zeros(n, dtype=bool)
	for j in range(n):
		x0 = X0c[j].reshape(-1, 1)
		try:
			_MPC.reset_history()
			_MPC.x0 = x0
			_MPC.u0 = np.array([[float(Upc[j])]])
			_MPC.set_initial_guess()
			u0 = float(np.asarray(_MPC.make_step(x0)).flatten()[0])
			U0[j] = u0
			ok[j] = bool(_MPC.solver_stats.get('success', True))
		except Exception:
			ok[j] = False
	return U0, ok


# ----- closed-loop (DAgger) sampling: log the ESTIMATOR's states + MPC label --
# Open-loop box sampling trains on arbitrary independent states, but the EKF
# produces correlated estimates the net never sees -> covariate shift. Here we
# roll out the real controller (EKF + MPC) on the realistic plant, logging the
# EKF state estimate (exactly what the net consumes at inference) paired with the
# MPC's command. Warm-start randomization + exploration dither (DAgger) widen the
# visited region so the policy is learned off the on-policy trajectory too.
def _episode(arg):
	ep_seed, minutes, dither = arg
	rng = np.random.default_rng(ep_seed)
	c = Controller(dict(_DEFAULTS), 'C', dict(CYCLE))
	c.set_target(SP)
	cfg = c.cfg
	qmin, qmax = cfg['Q_min'], cfg['Q_max']
	plant = GrillSim(seed=ep_seed)
	# warm-start half the episodes across the reachable range (incl. above setpoint)
	if rng.random() < 0.5:
		t0 = float(rng.uniform(20.0, 130.0))
		plant.T_c = plant.T_meas = t0
		plant.T_f = t0 + float(rng.uniform(0.0, 130.0))
		lastQ = float(rng.uniform(qmin, 60.0))
	else:
		lastQ = qmin
	Xh, Up, Q = [], [], []
	nsteps = int(minutes * 60 / 25)
	for k in range(nsteps):
		y = plant.measured()
		x_hat = c.estimator.update(lastQ, y)
		try:
			q_exp = float(np.asarray(c.mpc.make_step(x_hat.reshape(-1, 1))).flatten()[0])
		except Exception:
			q_exp = lastQ
		q_exp = float(np.clip(q_exp, qmin, qmax))
		if k >= 4:  # let the EKF settle on warm starts
			Xh.append(np.asarray(x_hat).flatten().copy())
			Up.append(lastQ)
			Q.append(q_exp)
		# DAgger exploration: perturb the APPLIED input to visit off-policy states
		q_app = q_exp + (rng.normal(0, dither) if rng.random() < 0.5 else 0.0)
		q_app = float(np.clip(q_app, qmin, qmax))
		auger, fan_duty = allocate(
			q_app,
			Q_min=qmin,
			Q_max=qmax,
			u_min=c.u_min,
			u_max=c.u_max,
			fan_min_pct=cfg['fan_min_pct'],
			fan_max_pct=cfg['fan_max_pct'],
			enable_fan=bool(cfg['enable_fan_input']),
		)
		ratio = float(np.clip(auger, c.u_min, c.u_max))
		fan = fan_duty if fan_duty is not None else 100.0
		on = int(round(ratio * 25))
		for s in range(25):
			plant.step(auger_on=(s < on), fan_frac=fan / 100.0)
		lastQ = q_app
	return np.array(Xh), np.array(Up), np.array(Q)


# ----- setpoint-spanning closed-loop sampling -------------------------------
# Like the single-setpoint DAgger rollout, but each episode follows a random
# setpoint SCHEDULE across the operating range -- so the data covers steady holds
# at many temperatures AND the big-step transients (110->220 etc., the hard
# cases). T_set is logged per sample; the spanning net takes it as an input and
# the analytic Q_ss(d, T_set) feedforward generalizes across the range for free.
def _episode_span(arg):
	ep_seed, minutes, dither, sp_lo, sp_hi, enable_fan = arg
	rng = np.random.default_rng(ep_seed)
	c = Controller({**_DEFAULTS, 'enable_fan_input': bool(enable_fan)}, 'C', dict(CYCLE))
	cfg = c.cfg
	qmin, qmax = cfg['Q_min'], cfg['Q_max']
	plant = GrillSim(seed=ep_seed)
	nsteps = int(minutes * 60 / 25)
	# random setpoint schedule: 1-3 segments across the range
	nseg = int(rng.integers(1, 4))
	seg_sp = rng.uniform(sp_lo, sp_hi, size=nseg)
	seg_bounds = np.linspace(0, nsteps, nseg + 1).astype(int)
	# warm-start most episodes anywhere in the reachable range
	if rng.random() < 0.6:
		t0 = float(rng.uniform(20.0, min(sp_hi, 300.0)))
		plant.T_c = plant.T_meas = t0
		plant.T_f = t0 + float(rng.uniform(0.0, 150.0))
		lastQ = float(rng.uniform(qmin, 80.0))
	else:
		lastQ = qmin
	c.set_target(float(seg_sp[0]))
	seg = 0
	Xh, Up, Ts, Q = [], [], [], []
	for k in range(nsteps):
		if seg + 1 < nseg and k >= seg_bounds[seg + 1]:
			seg += 1
			c.set_target(float(seg_sp[seg]))
		y = plant.measured()
		x_hat = c.estimator.update(lastQ, y)
		try:
			q_exp = float(np.asarray(c.mpc.make_step(x_hat.reshape(-1, 1))).flatten()[0])
		except Exception:
			q_exp = lastQ
		q_exp = float(np.clip(q_exp, qmin, qmax))
		if k >= 4:
			Xh.append(np.asarray(x_hat).flatten().copy())
			Up.append(lastQ)
			Ts.append(float(c._set_point_c))
			Q.append(q_exp)
		q_app = q_exp + (rng.normal(0, dither) if rng.random() < 0.5 else 0.0)
		q_app = float(np.clip(q_app, qmin, qmax))
		auger, fan_duty = allocate(
			q_app,
			Q_min=qmin,
			Q_max=qmax,
			u_min=c.u_min,
			u_max=c.u_max,
			fan_min_pct=cfg['fan_min_pct'],
			fan_max_pct=cfg['fan_max_pct'],
			enable_fan=bool(cfg['enable_fan_input']),
		)
		ratio = float(np.clip(auger, c.u_min, c.u_max))
		fan = fan_duty if fan_duty is not None else 100.0
		on = int(round(ratio * 25))
		for s in range(25):
			plant.step(auger_on=(s < on), fan_frac=fan / 100.0)
		lastQ = q_app
	return np.array(Xh), np.array(Up), np.array(Ts), np.array(Q)


def sample_span(episodes=150, workers=None, seed=0, minutes=120, dither=8.0,
                sp_lo=100.0, sp_hi=290.0, out=OUT_SPAN, enable_fan=False):
	workers = workers or max(1, (os.cpu_count() or 2) - 2)
	args = [(seed * 100000 + e, minutes, dither, sp_lo, sp_hi, bool(enable_fan))
	        for e in range(episodes)]
	t0 = time.perf_counter()
	ctx = mp.get_context('fork')
	with ctx.Pool(processes=workers) as pool:
		results = pool.map(_episode_span, args)
	dt = time.perf_counter() - t0
	X0 = np.concatenate([r[0] for r in results])
	Up = np.concatenate([r[1] for r in results])
	Ts = np.concatenate([r[2] for r in results])
	U0 = np.concatenate([r[3] for r in results])
	os.makedirs(os.path.dirname(out), exist_ok=True)
	np.savez_compressed(out, X0=X0, u_prev=Up, t_set=Ts, u0=U0, sp_lo=sp_lo, sp_hi=sp_hi,
	                     enable_fan=np.int64(bool(enable_fan)))
	print(
		f'span: {episodes} episodes [{sp_lo:.0f},{sp_hi:.0f}]C on {workers} workers in '
		f'{dt:.0f}s -> {len(U0)} samples ({len(U0) / dt:.0f}/s) | '
		f'T_set [{Ts.min():.0f},{Ts.max():.0f}] u0 [{U0.min():.1f},{U0.max():.1f}] mean {U0.mean():.1f} | '
		f'fan={enable_fan}'
	)
	print(f'saved {out}')
	return out


def sample_closed_loop(episodes=120, workers=None, seed=0, minutes=60, dither=8.0, out=OUT):
	workers = workers or max(1, (os.cpu_count() or 2) - 2)
	args = [(seed * 100000 + e, minutes, dither) for e in range(episodes)]
	t0 = time.perf_counter()
	ctx = mp.get_context('fork')
	with ctx.Pool(processes=workers) as pool:  # _episode builds its own Controller
		results = pool.map(_episode, args)
	dt = time.perf_counter() - t0
	X0 = np.concatenate([r[0] for r in results])
	Up = np.concatenate([r[1] for r in results])
	U0 = np.concatenate([r[2] for r in results])
	os.makedirs(os.path.dirname(out), exist_ok=True)
	np.savez_compressed(out, X0=X0, u_prev=Up, u0=U0, setpoint=SP)
	print(
		f'closed-loop: {episodes} episodes on {workers} workers in {dt:.0f}s '
		f'-> {len(U0)} samples ({len(U0) / dt:.0f}/s) | '
		f'u0 [{U0.min():.1f},{U0.max():.1f}] mean {U0.mean():.1f}'
	)
	print(f'saved {out}')
	return out


def sample(n=16000, workers=None, seed=0, out=OUT):
	workers = workers or max(1, (os.cpu_count() or 2) - 2)
	X0, Up = generate_states(n, seed=seed)
	# split into workers*3 chunks for load balance
	n_chunks = workers * 3
	idx = np.array_split(np.arange(n), n_chunks)
	chunks = [(X0[i], Up[i]) for i in idx]

	t0 = time.perf_counter()
	ctx = mp.get_context('fork')
	with ctx.Pool(processes=workers, initializer=_init_worker) as pool:
		results = pool.map(_solve_chunk, chunks)
	dt = time.perf_counter() - t0

	U0 = np.concatenate([r[0] for r in results])
	ok = np.concatenate([r[1] for r in results])
	keep = ok & np.isfinite(U0)
	Xk, Upk, U0k = X0[keep], Up[keep], U0[keep]
	os.makedirs(os.path.dirname(out), exist_ok=True)
	np.savez_compressed(out, X0=Xk, u_prev=Upk, u0=U0k, setpoint=SP)
	print(
		f'sampled {n} on {workers} workers in {dt:.0f}s '
		f'({n / dt:.0f}/s) | success {keep.mean() * 100:.1f}% '
		f'-> {keep.sum()} kept | u0 [{U0k.min():.1f},{U0k.max():.1f}] mean {U0k.mean():.1f}'
	)
	print(f'saved {out}')
	return out


if __name__ == '__main__':
	ap = argparse.ArgumentParser()
	ap.add_argument(
		'--mode',
		choices=['box', 'closed', 'span'],
		default='closed',
		help='box=structured open-loop; closed=single-setpoint DAgger; span=setpoint-spanning DAgger',
	)
	ap.add_argument('-n', type=int, default=16000, help='box mode: number of samples')
	ap.add_argument('-e', '--episodes', type=int, default=120, help='closed/span: episodes')
	ap.add_argument('-w', '--workers', type=int, default=None)
	ap.add_argument('--minutes', type=float, default=None, help='episode length (min)')
	ap.add_argument('--dither', type=float, default=8.0)
	ap.add_argument('--sp-lo', type=float, default=100.0)
	ap.add_argument('--sp-hi', type=float, default=290.0)
	ap.add_argument('--enable-fan', action='store_true', help='span: sample with the MPC driving the fan')
	ap.add_argument('--out', default=None, help='override output .npz path')
	ap.add_argument('--seed', type=int, default=0)
	a = ap.parse_args()
	if a.mode == 'box':
		sample(n=a.n, workers=a.workers, seed=a.seed)
	elif a.mode == 'closed':
		sample_closed_loop(
			episodes=a.episodes, workers=a.workers, seed=a.seed, dither=a.dither, minutes=a.minutes or 60
		)
	else:
		sample_span(
			episodes=a.episodes,
			workers=a.workers,
			seed=a.seed,
			dither=a.dither,
			minutes=a.minutes or 120,
			sp_lo=a.sp_lo,
			sp_hi=a.sp_hi,
			out=a.out or (OUT_SPAN.replace('.npz', '_fan.npz') if a.enable_fan else OUT_SPAN),
			enable_fan=a.enable_fan,
		)
