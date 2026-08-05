#!/usr/bin/env python3
"""
Setpoint-SPANNING residual approxMPC. One net replaces the MPC's NLP solve across
the whole operating range (~110-290C / 230-554F), not just a single setpoint.

Design (extends the single-setpoint PoC):
  * The analytic offset-free feedforward Q_ss(d, T_set) already takes T_set, so it
    generalizes across setpoints for FREE.
  * The net takes T_set as an extra input and learns only the transient residual
    MPC_Q - Q_ss(d, T_set).
  * Trained on setpoint-spanning closed-loop (DAgger) samples (sample_mpc.py
    --mode span), so the input distribution matches deployment at every setpoint.

Inference: Q = Q_ss(d_hat, T_set) + net([state, u_prev, T_set]). control.py must
never be imported (module-level while True); we import only controller.mpc.
"""

import warnings, sys, os, time

warnings.filterwarnings("ignore")
sys.path.insert(0, ".")
import numpy as np
import torch
import torch.nn as nn
from controller.mpc import Controller, _DEFAULTS
from controller.mpc_allocator import ALLOCATOR_REVISION, allocate
from controller.mpc_model import MODEL_SCHEMA, _rad_loss
from controller.mpc_net import _CALIB_FLOATS, _CALIB_INTS
from controller.grill_sim import GrillSim
from sample_mpc import SPAN_DATASET_SCHEMA, SPAN_GENERATION_VERSION, span_generation_command

CYCLE = {"u_min": 0.1, "u_max": 0.9, "HoldCycleTime": 25}
# prefer the larger external dataset if present, else the local span samples
SPAN_NPZ = next(
    (
        p
        for p in (
            "./pifire_from_other_machine_samples.npz",
            "./docs/superpowers/experiments/_ampc_data/pifire_span.npz",
        )
        if os.path.exists(p)
    ),
    "./docs/superpowers/experiments/_ampc_data/pifire_span.npz",
)
DCFG = _DEFAULTS
ND = int(_DEFAULTS["n_delay"])
DIDX = ND + 1  # index of d in the state vector [q0..q_{ND-1}, T_c, d]
QMIN, QMAX = 0.0, 1.0

_SPAN_REQUIRED_FIELDS = (
    "X0",
    "u_prev",
    "t_set",
    "u0",
    "sp_lo",
    "sp_hi",
    "dataset_schema",
    "sample_mode",
    "model_schema",
    "allocator_revision",
    "episode_count",
    "sampled_state_count",
    "seed",
    "generation_version",
    "sample_minutes",
    "sample_dither",
    "generation_command",
    *_CALIB_FLOATS,
    *_CALIB_INTS,
)
_SPAN_PROVENANCE_FIELDS = _SPAN_REQUIRED_FIELDS[4:]


def _scalar(dataset, key):
    value = np.asarray(dataset[key])
    if value.size != 1:
        raise ValueError(f"dataset {key} must be a scalar")
    return value.item()


def _require_equal(dataset, key, expected):
    actual = _scalar(dataset, key)
    if actual != expected:
        raise ValueError(f"dataset {key}={actual!r} does not match expected {expected!r}")
    return actual


def validate_span_dataset(dataset, *, expected_enable_fan, expected_episodes=None, expected_seed=None):
    """Reject a span archive unless its provenance and arrays match this model."""
    available = set(dataset.files) if hasattr(dataset, "files") else set(dataset)
    missing = sorted(set(_SPAN_REQUIRED_FIELDS) - available)
    if missing:
        raise ValueError(f"dataset missing required provenance: {', '.join(missing)}")

    _require_equal(dataset, "dataset_schema", SPAN_DATASET_SCHEMA)
    _require_equal(dataset, "sample_mode", "span")
    _require_equal(dataset, "model_schema", MODEL_SCHEMA)
    _require_equal(dataset, "allocator_revision", ALLOCATOR_REVISION)
    _require_equal(dataset, "generation_version", SPAN_GENERATION_VERSION)
    _require_equal(dataset, "enable_fan_input", int(bool(expected_enable_fan)))
    if int(_scalar(dataset, "episode_count")) <= 0:
        raise ValueError("dataset episode_count must be positive")
    if int(_scalar(dataset, "seed")) < 0:
        raise ValueError("dataset seed must be non-negative")
    if expected_episodes is not None:
        _require_equal(dataset, "episode_count", int(expected_episodes))
    if expected_seed is not None:
        _require_equal(dataset, "seed", int(expected_seed))

    for key in _CALIB_FLOATS:
        actual = float(_scalar(dataset, key))
        expected = float(_DEFAULTS[key])
        if not np.isfinite(actual) or actual != expected:
            raise ValueError(f"dataset {key}={actual!r} does not match active calibration {expected!r}")
    for key in _CALIB_INTS:
        if key != "enable_fan_input":
            _require_equal(dataset, key, int(_DEFAULTS[key]))

    X0 = np.asarray(dataset["X0"])
    u_prev = np.asarray(dataset["u_prev"]).reshape(-1)
    t_set = np.asarray(dataset["t_set"]).reshape(-1)
    u0 = np.asarray(dataset["u0"]).reshape(-1)
    sample_count = len(u0)
    if X0.ndim != 2 or X0.shape != (sample_count, ND + 2):
        raise ValueError(f"dataset X0 shape {X0.shape!r} does not match {(sample_count, ND + 2)!r}")
    if len(u_prev) != sample_count or len(t_set) != sample_count:
        raise ValueError("dataset sampled-state arrays have inconsistent lengths")
    if sample_count == 0 or not all(np.all(np.isfinite(values)) for values in (X0, u_prev, t_set, u0)):
        raise ValueError("dataset sampled-state arrays must be non-empty and finite")
    _require_equal(dataset, "sampled_state_count", sample_count)

    sp_lo = float(_scalar(dataset, "sp_lo"))
    sp_hi = float(_scalar(dataset, "sp_hi"))
    minutes = float(_scalar(dataset, "sample_minutes"))
    dither = float(_scalar(dataset, "sample_dither"))
    if (
        not np.isfinite((sp_lo, sp_hi, minutes, dither)).all()
        or not sp_lo < sp_hi
        or minutes <= 0.0
        or dither < 0.0
    ):
        raise ValueError("dataset span sampling metadata is invalid")
    expected_command = span_generation_command(
        episodes=int(_scalar(dataset, "episode_count")),
        minutes=minutes,
        dither=dither,
        sp_lo=sp_lo,
        sp_hi=sp_hi,
        seed=int(_scalar(dataset, "seed")),
        enable_fan=bool(_scalar(dataset, "enable_fan_input")),
    )
    _require_equal(dataset, "generation_command", expected_command)
    return {key: np.asarray(dataset[key]).copy() for key in _SPAN_PROVENANCE_FIELDS}


def load_span_dataset(data_path, *, expected_enable_fan, expected_episodes=None, expected_seed=None):
    """Load arrays and only validated immutable provenance from a span archive."""
    with np.load(data_path, allow_pickle=False) as dataset:
        provenance = validate_span_dataset(
            dataset,
            expected_enable_fan=expected_enable_fan,
            expected_episodes=expected_episodes,
            expected_seed=expected_seed,
        )
        return {
            "X0": np.asarray(dataset["X0"]).copy(),
            "u_prev": np.asarray(dataset["u_prev"]).reshape(-1).copy(),
            "t_set": np.asarray(dataset["t_set"]).reshape(-1).copy(),
            "u0": np.asarray(dataset["u0"]).reshape(-1).copy(),
            "provenance": provenance,
        }


def Q_ss(d, set_c):
    # exact offset-free steady firing rate for ANY setpoint
    return (DCFG["h_amb"] * (set_c - DCFG["T_amb"]) + _rad_loss(set_c, DCFG["T_amb"], DCFG["sigma"]) - d) / DCFG["K_Q"]


class SpanNet(nn.Module):
    def __init__(self, n_in, h=96):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(n_in, h), nn.Tanh(), nn.Linear(h, h), nn.Tanh(), nn.Linear(h, h), nn.Tanh(), nn.Linear(h, 1)
        )

    def forward(self, x):
        return self.net(x)


def build_span_net(
    epochs=400,
    batch=4096,
    data_path=SPAN_NPZ,
    *,
    expected_enable_fan=False,
    expected_episodes=None,
    expected_seed=None,
    dataset=None,
):
    dataset = dataset or load_span_dataset(
        data_path,
        expected_enable_fan=expected_enable_fan,
        expected_episodes=expected_episodes,
        expected_seed=expected_seed,
    )
    X0, UP, TS, U0 = dataset["X0"], dataset["u_prev"], dataset["t_set"], dataset["u0"]
    resid = U0 - Q_ss(X0[:, DIDX], TS)  # target

    xm, xs = Xin.mean(0), Xin.std(0) + 1e-8
    rm, rs = resid.mean(), resid.std() + 1e-8
    Xs = torch.tensor((Xin - xm) / xs, dtype=torch.float32)
    ys = torch.tensor(((resid - rm) / rs).reshape(-1, 1), dtype=torch.float32)

    torch.manual_seed(0)
    n = len(Xs)
    nval = int(0.15 * n)
    perm = torch.randperm(n)
    vi, ti = perm[:nval], perm[nval:]
    Xt, yt, Xv, yv = Xs[ti], ys[ti], Xs[vi], ys[vi]
    net = SpanNet(Xin.shape[1])
    opt = torch.optim.Adam(net.parameters(), lr=2e-3, weight_decay=1e-5)
    sched = torch.optim.lr_scheduler.StepLR(opt, step_size=max(1, epochs // 3), gamma=0.4)
    lossf = nn.MSELoss()
    t0 = time.perf_counter()
    for ep in range(epochs):
        net.train()
        order = torch.randperm(len(Xt))
        for b in range(0, len(Xt), batch):
            idx = order[b : b + batch]
            opt.zero_grad()
            loss = lossf(net(Xt[idx]), yt[idx])
            loss.backward()
            opt.step()
        sched.step()
    net.eval()
    with torch.no_grad():
        vloss = lossf(net(Xv), yv).item()
    print(
        f"  span net ({Xin.shape[1]}-in, {len(U0)} samples) trained {epochs} epochs "
        f"in {time.perf_counter() - t0:.0f}s (val_mse={vloss:.4f})",
        flush=True,
    )
    stats = (torch.tensor(xm, dtype=torch.float32), torch.tensor(xs, dtype=torch.float32), float(rm), float(rs))
    return net, stats, dataset["provenance"]


def _drive(plant, Q, c):
    Q = float(np.clip(Q, QMIN, QMAX))
    allocation = allocate(
        Q,
        Q_min=QMIN,
        Q_max=QMAX,
        u_min=c.u_min,
        u_max=c.u_max,
        fan_min_pct=c.cfg["fan_min_pct"],
        fan_max_pct=c.cfg["fan_max_pct"],
        enable_fan=bool(c.cfg["enable_fan_input"]),
    )
    ratio = float(np.clip(allocation.auger_duty, c.u_min, c.u_max))
    fan = allocation.fan_duty if allocation.fan_duty is not None else 100.0
    on = int(round(ratio * 25))
    T = []
    for s in range(25):
        plant.step(auger_on=(s < on), fan_frac=fan / 100.0)
        T.append(plant.true_Tc)
    return T


def run_full(set_c, seed=0, minutes=120):
    c = Controller(dict(_DEFAULTS), "C", dict(CYCLE))
    c.set_target(set_c)
    plant = GrillSim(seed=seed)
    T = []
    for _ in range(int(minutes * 60 / 25)):
        out = c.update(plant.measured())
        # reuse the controller's own allocation for an apples-to-apples plant drive
        ratio = float(np.clip(out["cycle_ratio"], c.u_min, c.u_max))
        fan = out["fan"]["duty"] if out["fan"]["duty"] is not None else 100.0
        on = int(round(ratio * 25))
        for s in range(25):
            plant.step(auger_on=(s < on), fan_frac=fan / 100.0)
            T.append(plant.true_Tc)
    return np.array(T)


def run_span(net, stats, set_c, seed=0, minutes=120):
    xm, xs, rm, rs = stats
    c = Controller(dict(_DEFAULTS), "C", dict(CYCLE))
    c.set_target(set_c)
    est = c.estimator
    plant = GrillSim(seed=seed)
    T = []
    lastQ = QMIN
    for _ in range(int(minutes * 60 / 25)):
        y = plant.measured()
        xh = est.update(lastQ, y).flatten()
        with torch.no_grad():
            inp = (torch.tensor(np.append(xh, [lastQ, set_c]), dtype=torch.float32) - xm) / xs
            resid = net(inp.reshape(1, -1)).item() * rs + rm
        Q = Q_ss(xh[DIDX], set_c) + resid
        Q = float(np.clip(Q, QMIN, QMAX))
        lastQ = Q
        T += _drive(plant, Q, c)
    return np.array(T)


def band(T, set_c, win=0.4):
    sm = np.arange(len(T)) >= len(T) * win
    e = T[sm] - set_c
    return np.sqrt(np.mean(e**2)), np.max(np.abs(e)), np.mean(e)


if __name__ == "__main__":
    print("Building setpoint-spanning residual net ...", flush=True)
    net, stats, _ = build_span_net()
    SETPOINTS = [110.0, 150.0, 190.0, 220.0, 260.0]  # 230..500F
    SEEDS = [0, 1, 2]
    print("\n setpoint    FULL MPC (rms/max/bias)     SPAN net (rms/max/bias)", flush=True)
    for sc in SETPOINTS:
        fr = np.array([band(run_full(sc, s), sc) for s in SEEDS]).mean(0)
        sr = np.array([band(run_span(net, stats, sc, s), sc) for s in SEEDS]).mean(0)
        print(
            f" {sc:5.0f}C ({sc * 9 / 5 + 32:3.0f}F)  {fr[0]:5.2f} {fr[1]:6.2f} {fr[2]:+5.2f}      "
            f"{sr[0]:5.2f} {sr[1]:6.2f} {sr[2]:+5.2f}",
            flush=True,
        )
