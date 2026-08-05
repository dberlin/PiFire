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

Only `_episode_span` (`--mode span`) models a lid-open pause; `_episode`
(`--mode closed`, the CLI default) has no lid regime at all.
"""

import warnings, sys, os, time, argparse, math

warnings.filterwarnings("ignore")
sys.path.insert(0, ".")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
import numpy as np
import multiprocessing as mp
from scipy.stats import qmc

from common.defaults import default_settings
from controller.mpc import Controller, _DEFAULTS
from controller.mpc_allocator import ALLOCATOR_REVISION, allocate
from controller.mpc_model import MODEL_SCHEMA
from controller.mpc_net import _CALIB_FLOATS, _CALIB_INTS
from controller.grill_sim import GrillSim

SP = 110.0
CYCLE = {"u_min": 0.1, "u_max": 0.9, "HoldCycleTime": 25}
ND = int(_DEFAULTS["n_delay"])
OUT = "./docs/superpowers/experiments/_ampc_data/pifire_samples.npz"
OUT_SPAN = "./docs/superpowers/experiments/_ampc_data/pifire_span.npz"

SPAN_DATASET_SCHEMA = 1
SPAN_GENERATION_VERSION = 1


def span_generation_command(*, episodes, minutes, dither, sp_lo, sp_hi, seed, enable_fan):
    """Return the canonical, reproducible invocation represented by a span archive."""
    command = (
        f"sample_mpc.py --mode span -e {int(episodes)} --minutes {float(minutes)} "
        f"--dither {float(dither)} --sp-lo {float(sp_lo)} --sp-hi {float(sp_hi)} --seed {int(seed)}"
    )
    return f"{command} --enable-fan" if enable_fan else command


def span_dataset_metadata(*, episodes, sampled_state_count, minutes, dither, sp_lo, sp_hi, seed, enable_fan):
    """Build complete model and sampling provenance for one span dataset."""
    metadata = {
        "dataset_schema": np.int64(SPAN_DATASET_SCHEMA),
        "sample_mode": np.array("span"),
        "model_schema": np.int64(MODEL_SCHEMA),
        "allocator_revision": np.int64(ALLOCATOR_REVISION),
        "episode_count": np.int64(episodes),
        "sampled_state_count": np.int64(sampled_state_count),
        "seed": np.int64(seed),
        "generation_version": np.int64(SPAN_GENERATION_VERSION),
        "sample_minutes": np.float64(minutes),
        "sample_dither": np.float64(dither),
        "generation_command": np.array(
            span_generation_command(
                episodes=episodes,
                minutes=minutes,
                dither=dither,
                sp_lo=sp_lo,
                sp_hi=sp_hi,
                seed=seed,
                enable_fan=enable_fan,
            )
        ),
    }
    for key in _CALIB_FLOATS:
        metadata[key] = np.float64(_DEFAULTS[key])
    for key in _CALIB_INTS:
        metadata[key] = np.int64(bool(enable_fan) if key == "enable_fan_input" else _DEFAULTS[key])
    return metadata


# Hold arms a LidOpenPauseTime timer when the lid opens (`hold.py:285`,
# `hold.py:316`) but only re-decides the auger ratio on a cycle boundary
# (`hold.py:187`, `hold.py:191-193`). A pause beginning on a boundary therefore
# holds the actuators for every boundary at which it is still latched, which on
# this grid is `ceil(pause / cycle)` steps; rounding down would hand control
# back a whole cycle earlier than production ever does.
LID_PAUSE_S = default_settings()["cycle_data"]["LidOpenPauseTime"]
LID_PAUSE_STEPS = math.ceil(LID_PAUSE_S / CYCLE["HoldCycleTime"])


def generate_states(n, *, seed=0, op_frac=0.55):
    """Physically-structured, space-filling (state, u_prev) draws."""
    rng = np.random.default_rng(seed)
    # LHS over normalized load, temperature, load slope, disturbance, prior load, mix.
    U = qmc.LatinHypercube(d=6, seed=seed).random(n)

    load_level = U[:, 0]
    # chamber: mixture of operating (near setpoint) and cold-start approach
    T_c_op = np.clip(SP + (U[:, 1] - 0.5) * 2 * 22, 20, 145)
    T_c_app = 20.0 + U[:, 1] * (135.0 - 20.0)
    T_c = np.where(U[:, 5] < op_frac, T_c_op, T_c_app)
    slope = (U[:, 2] - 0.5) * 0.44
    d = (U[:, 3] - 0.5) * 2 * 80.0
    u_prev = np.clip(load_level + (U[:, 4] - 0.5) * 0.30, 0.0, 1.0)

    # Delay chain around the load level with the ramp gradient + small noise.
    mid = (ND - 1) / 2.0
    q = np.stack([load_level + slope * (i - mid) for i in range(ND)], axis=1)
    q = np.clip(q + rng.normal(0, 0.03, size=q.shape), 0.0, 1.0)

    X0 = np.column_stack([q, T_c, d])  # [n, ND+2]
    return X0, u_prev


# ----- parallel solve: each worker builds its own MPC once -----------------
_MPC = None


def _init_worker():
    global _MPC
    warnings.filterwarnings("ignore")
    c = Controller(dict(_DEFAULTS), "C", dict(CYCLE))
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
            ok[j] = bool(_MPC.solver_stats.get("success", True))
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
    c = Controller(dict(_DEFAULTS), "C", dict(CYCLE))
    c.set_target(SP)
    cfg = c.cfg
    plant = GrillSim(seed=ep_seed)
    # warm-start half the episodes across the normalized command range.
    if rng.random() < 0.5:
        t0 = float(rng.uniform(20.0, 130.0))
        plant.T_c = plant.T_meas = t0
        plant.T_f = t0 + float(rng.uniform(0.0, 130.0))
        lastQ = float(rng.uniform(0.0, 0.6))
    else:
        lastQ = 0.0
    Xh, Up, Q = [], [], []
    nsteps = int(minutes * 60 / 25)
    for k in range(nsteps):
        y = plant.measured()
        x_hat = c.estimator.update(lastQ, y)
        try:
            q_exp = float(np.asarray(c.mpc.make_step(x_hat.reshape(-1, 1))).flatten()[0])
        except Exception:
            q_exp = lastQ
        q_exp = float(np.clip(q_exp, 0.0, 1.0))
        if k >= 4:  # let the EKF settle on warm starts
            Xh.append(np.asarray(x_hat).flatten().copy())
            Up.append(lastQ)
            Q.append(q_exp)
        # DAgger exploration: perturb the APPLIED input to visit off-policy states
        q_app = q_exp + (rng.normal(0, dither) if rng.random() < 0.5 else 0.0)
        q_app = float(np.clip(q_app, 0.0, 1.0))
        allocation = allocate(
            q_app,
            u_max=c.u_max,
            fan_min_pct=cfg["fan_min_pct"],
            fan_max_pct=cfg["fan_max_pct"],
            enable_fan=bool(cfg["enable_fan_input"]),
        )
        ratio = float(np.clip(allocation.auger_duty, 0.0, c.u_max))
        fan = allocation.fan_duty if allocation.fan_duty is not None else 100.0
        on = int(round(ratio * 25))
        for s in range(25):
            plant.step(auger_on=(s < on), fan_frac=fan / 100.0)
        lastQ = q_app
    return np.array(Xh), np.array(Up), np.array(Q)


def _draw_lid_events(rng, nsteps):
    """Physical lid openings for one episode, as `[start, end)` step indices.

    About a third of episodes stand the chamber open once or twice, for 2-6
    steps (50-150 s) each.
    """
    if rng.random() >= 0.35:
        return []
    events = []
    for _ in range(int(rng.integers(1, 3))):
        start = int(rng.integers(8, max(9, nsteps - 8)))
        events.append((start, start + int(rng.integers(2, 7))))
    return events


def _lid_windows(k, lid_events):
    """The two windows a lid opening drives, as production drives them.

    The chamber leaks heat to ambient for as long as it stands open; Hold
    surrenders the actuators for `LID_PAUSE_STEPS` only. They are different
    lengths, so collapsing them into one flag either pins the auger down for the
    whole opening -- which no production path does -- or seals the chamber the
    moment control resumes.
    """
    return (
        any(lo <= k < hi for lo, hi in lid_events),
        any(lo <= k < lo + LID_PAUSE_STEPS for lo, _hi in lid_events),
    )


# ----- setpoint-spanning closed-loop sampling -------------------------------
# Like the single-setpoint DAgger rollout, but each episode follows a random
# setpoint SCHEDULE across the operating range -- so the data covers steady holds
# at many temperatures AND the big-step transients (110->220 etc., the hard
# cases). T_set is logged per sample; the spanning net takes it as an input and
# the analytic Q_ss(d, T_set) feedforward generalizes across the range for free.
def _episode_span(arg):
    ep_seed, minutes, dither, sp_lo, sp_hi, enable_fan = arg
    rng = np.random.default_rng(ep_seed)
    c = Controller({**_DEFAULTS, "enable_fan_input": bool(enable_fan)}, "C", dict(CYCLE))
    cfg = c.cfg
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
        lastQ = float(rng.uniform(0.0, 0.8))
    else:
        lastQ = 0.0
    c.set_target(float(seg_sp[0]))
    seg = 0
    Xh, Up, Ts, Q = [], [], [], []
    # Lid-open events: hold.py fires one AppliedOutput(0.0) at detection, then
    # pins cycle.ratio to u_min (auger still cycling, fan off) for the rest of
    # the pause. The estimator's transport-lag states carry that one below-Q_min
    # tick on every real lid event; without it here the net never learns the
    # regime and extrapolates exactly where the NLP does not. The chamber keeps
    # leaking heat after the pause expires, so the net also sees the state
    # production actually hands it back: a cold chamber at full authority.
    lid_events = _draw_lid_events(rng, nsteps)
    lid_starts = {lo for lo, _hi in lid_events}
    for k in range(nsteps):
        if seg + 1 < nseg and k >= seg_bounds[seg + 1]:
            seg += 1
            c.set_target(float(seg_sp[seg]))
        y = plant.measured()
        x_hat = c.estimator.update(lastQ, y)
        try:
            residual = float(np.asarray(c.mpc.make_step(x_hat.reshape(-1, 1))).flatten()[0])
        except Exception:
            residual = 0.0
        equilibrium = c._equilibrium_load(c._set_point_c, float(x_hat[int(cfg["n_delay"]) + 1]))
        q_exp = float(np.clip(equilibrium + residual, 0.0, 1.0))
        if k >= 4:
            Xh.append(np.asarray(x_hat).flatten().copy())
            Up.append(lastQ)
            Ts.append(float(c._set_point_c))
            Q.append(q_exp)
        lid, lid_paused = _lid_windows(k, lid_events)
        q_app = q_exp + (rng.normal(0, dither) if rng.random() < 0.5 else 0.0)
        q_app = float(np.clip(q_app, 0.0, 1.0))
        if lid_paused:
            # detection tick: auger forced fully off (ratio 0.0, below u_min);
            # remaining ticks: ratio pinned at u_min while the auger keeps
            # cycling. Fan is off for the whole pause either way.
            # The controller no longer carries the floor as an attribute; it is
            # the sampler's own configured value, which is what the controller
            # was constructed with.
            ratio = 0.0 if k in lid_starts else CYCLE["u_min"]
            fan = 0.0
            q_app = 0.0
        else:
            allocation = allocate(
                q_app,
                u_max=c.u_max,
                fan_min_pct=cfg["fan_min_pct"],
                fan_max_pct=cfg["fan_max_pct"],
                enable_fan=bool(cfg["enable_fan_input"]),
            )
            ratio = float(np.clip(allocation.auger_duty, 0.0, c.u_max))
            fan = allocation.fan_duty if allocation.fan_duty is not None else 100.0
        on = int(round(ratio * 25))
        for s in range(25):
            plant.step(auger_on=(s < on), fan_frac=fan / 100.0, lid_open=lid)
        lastQ = q_app
    return np.array(Xh), np.array(Up), np.array(Ts), np.array(Q)


def sample_span(
    episodes=150,
    workers=None,
    seed=0,
    minutes=120,
    dither=0.08,
    sp_lo=100.0,
    sp_hi=290.0,
    out=OUT_SPAN,
    enable_fan=False,
):
    workers = workers or max(1, (os.cpu_count() or 2) - 2)
    args = [(seed * 100000 + e, minutes, dither, sp_lo, sp_hi, bool(enable_fan)) for e in range(episodes)]
    t0 = time.perf_counter()
    ctx = mp.get_context("fork")
    with ctx.Pool(processes=workers) as pool:
        results = pool.map(_episode_span, args)
    dt = time.perf_counter() - t0
    X0 = np.concatenate([r[0] for r in results])
    Up = np.concatenate([r[1] for r in results])
    Ts = np.concatenate([r[2] for r in results])
    U0 = np.concatenate([r[3] for r in results])
    os.makedirs(os.path.dirname(out), exist_ok=True)
    np.savez_compressed(
        out,
        X0=X0,
        u_prev=Up,
        t_set=Ts,
        u0=U0,
        sp_lo=sp_lo,
        sp_hi=sp_hi,
        **span_dataset_metadata(
            episodes=episodes,
            sampled_state_count=len(U0),
            minutes=minutes,
            dither=dither,
            sp_lo=sp_lo,
            sp_hi=sp_hi,
            seed=seed,
            enable_fan=enable_fan,
        ),
    )
    print(
        f"span: {episodes} episodes [{sp_lo:.0f},{sp_hi:.0f}]C on {workers} workers in "
        f"{dt:.0f}s -> {len(U0)} samples ({len(U0) / dt:.0f}/s) | "
        f"T_set [{Ts.min():.0f},{Ts.max():.0f}] u0 [{U0.min():.1f},{U0.max():.1f}] mean {U0.mean():.1f} | "
        f"fan={enable_fan}"
    )
    print(f"saved {out}")
    return out


def sample_closed_loop(episodes=120, workers=None, seed=0, minutes=60, dither=8.0, out=OUT):
    workers = workers or max(1, (os.cpu_count() or 2) - 2)
    args = [(seed * 100000 + e, minutes, dither) for e in range(episodes)]
    t0 = time.perf_counter()
    ctx = mp.get_context("fork")
    with ctx.Pool(processes=workers) as pool:  # _episode builds its own Controller
        results = pool.map(_episode, args)
    dt = time.perf_counter() - t0
    X0 = np.concatenate([r[0] for r in results])
    Up = np.concatenate([r[1] for r in results])
    U0 = np.concatenate([r[2] for r in results])
    os.makedirs(os.path.dirname(out), exist_ok=True)
    np.savez_compressed(out, X0=X0, u_prev=Up, u0=U0, setpoint=SP)
    print(
        f"closed-loop: {episodes} episodes on {workers} workers in {dt:.0f}s "
        f"-> {len(U0)} samples ({len(U0) / dt:.0f}/s) | "
        f"u0 [{U0.min():.1f},{U0.max():.1f}] mean {U0.mean():.1f}"
    )
    print(f"saved {out}")
    return out


def sample(n=16000, workers=None, seed=0, out=OUT):
    workers = workers or max(1, (os.cpu_count() or 2) - 2)
    X0, Up = generate_states(n, seed=seed)
    # split into workers*3 chunks for load balance
    n_chunks = workers * 3
    idx = np.array_split(np.arange(n), n_chunks)
    chunks = [(X0[i], Up[i]) for i in idx]

    t0 = time.perf_counter()
    ctx = mp.get_context("fork")
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
        f"sampled {n} on {workers} workers in {dt:.0f}s "
        f"({n / dt:.0f}/s) | success {keep.mean() * 100:.1f}% "
        f"-> {keep.sum()} kept | u0 [{U0k.min():.1f},{U0k.max():.1f}] mean {U0k.mean():.1f}"
    )
    print(f"saved {out}")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--mode",
        choices=["box", "closed", "span"],
        default="closed",
        help=(
            "box=structured open-loop; closed=single-setpoint DAgger (no lid regime); "
            "span=setpoint-spanning DAgger (models a lid pause)"
        ),
    )
    ap.add_argument("-n", type=int, default=16000, help="box mode: number of samples")
    ap.add_argument("-e", "--episodes", type=int, default=120, help="closed/span: episodes")
    ap.add_argument("-w", "--workers", type=int, default=None)
    ap.add_argument("--minutes", type=float, default=None, help="episode length (min)")
    ap.add_argument("--dither", type=float, default=0.08)
    ap.add_argument("--sp-lo", type=float, default=100.0)
    ap.add_argument("--sp-hi", type=float, default=290.0)
    ap.add_argument("--enable-fan", action="store_true", help="span: sample with the MPC driving the fan")
    ap.add_argument("--out", default=None, help="override output .npz path")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    if a.mode == "box":
        sample(n=a.n, workers=a.workers, seed=a.seed)
    elif a.mode == "closed":
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
            out=a.out or (OUT_SPAN.replace(".npz", "_fan.npz") if a.enable_fan else OUT_SPAN),
            enable_fan=a.enable_fan,
        )
