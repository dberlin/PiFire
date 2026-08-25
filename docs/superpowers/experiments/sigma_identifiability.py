#!/usr/bin/env python3
"""
Why `controller/update_mpc.py` does not fit the radiative coefficient.

RESULT: it cannot, and no cook can make it possible. Someone will propose
fitting `sigma` again -- this file is the evidence that stops the work being
repeated.

Ported from the two-lump grey box to the single chamber lump when
controller/mpc_model.py collapsed to schema 2. It is cited by a LIVE comment
-- `_FREE` in controller/update_mpc.py, the reasoning that holds h_amb and
sigma and so keeps every fit inside PROMOTION_BOUNDS -- so pinning it would
have left that decision resting on evidence about a model this repo no longer
has. Every number below is from the single-lump structure. What the port
changed: the firepot lump and its conductance leave the plant, the batched
twin and the free set; the scaling family loses C_f and h_fc with them; and
the transport chain is advanced in closed form on both sides, matching the
shipped simulator.

Three findings, in the order they matter:

1. THE QUESTION IS MOOT. The grey-box model is invariant under scaling
   (C_c, h_amb, K_Q, sigma) by one common factor: the state equation is
   homogeneous in them, so the trajectory of the one measured state is
   bit-identical. Four parameters carry three identifiable degrees of
   freedom. A log determines the RATIOS, never the values, so one parameter
   must be held to fix the scale -- and `sigma` is as good a choice as any.
   Freeing it while `K_Q` is also free does not learn anything; it lets the
   solver drift along an unobservable direction and report a confident,
   arbitrary number. Measured here, over the 330 fits in the grid: the
   recovered `sigma` spans a factor of 1.1e14 across noise seeds of one
   isothermal cell -- the solve simply parks it wherever it started drifting --
   and even in the cells whose temperature range DOES identify `sigma` (100%
   recovery once the degeneracy is pinned), the shipped free set's median lands
   at 0.70x to 1.18x truth depending on the cell rather than on the data. It
   costs nothing in fit quality either way: held RMSE is within 0.01 C of free
   RMSE on all 330 fits, both sitting at the 0.50 C noise floor.

   The corollary is the reassuring half: holding `sigma` at a wrong value costs
   nothing. Holding it at `s_h` against a truth `s_t` is exactly compensated by
   scaling the rest by `s_h/s_t`, and the effective time constant
   `C_c/(h_amb + 4*sigma*(T+273.15)**3)` -- the quantity the braking argument
   rests on -- is invariant along that direction. With one lump that invariance
   is EXACT rather than approximate: truth and its `sigma`-held equivalent give
   1203.91202 s and 324.16440 s at the two ends of the operating range, equal
   in every digit a float carries.

2. RAW TEMPERATURE SPAN IS THE WRONG VARIABLE, if anyone does gate on
   identifiability elsewhere. Two grid cells here have identical span (24.0 C)
   and identical min-to-max radiative swing (0.0934), yet recover `sigma` 20%
   versus 100% of the time -- the isothermal 220 C hold against the 220->225 C
   step. A record that starts off its setpoint, sags once and then sits there
   covers a wide range while HOLDING exactly one operating point.

3. WHAT DOES SEPARATE THEM is the dwell-weighted spread of radiative
   conductance `4*sigma*(T+273.15)**3` -- measured between the 10th and 90th
   percentiles, so it weights by how long the grill actually spent at a
   temperature. It separated identifiable cells from unidentifiable ones in 94%
   of the grid, against 91% for raw span.

Finding 1 is why `_FREE` in `controller/update_mpc.py` holds `sigma`, and why
findings 2 and 3 are recorded but unused.

--- how it measures that ---

`sigma` and `h_amb` are both chamber loss terms, distinguishable only by their
different temperature dependence: loss is
`h_amb*(T_c - T_amb) + sigma*((T_c+273.15)**4 - (T_amb+273.15)**4)`, so at a
single chamber temperature any `sigma` trades for an `h_amb` giving identical
loss.

It sweeps a grid of synthetic cooks generated from a KNOWN `sigma` that differs
from the fitter's starting value -- recovery therefore requires the solver to
actually move, and a gate that simply never frees `sigma` scores zero rather
than looking perfect. Each grid point is fitted three ways: with `sigma` free
(the shipped free set, which is degenerate), with `sigma` held, and with `K_Q`
held instead so the scaling direction is pinned and the temperature-range
question can be asked in isolation. The grid varies the two things that could
plausibly govern identifiability independently:

* the temperature SPAN the record covers (`T_hot - T_cold`), and
* the HOT END it reaches (`T_hot`), because radiative conductance grows with
  T**3 and a 60 C span down near ambient carries far less radiative signal than
  the same span at 250 C.

Sweeping both is what distinguishes "span is the right gate" from "span is a
proxy for reaching a hot enough temperature".

Cost, and what is done about it
-------------------------------
`controller.mpc_model.simulate_grey_box` integrates with a Python loop over
sub-steps at a 0.125 s sub-step, so one forward simulation of a 20-minute cook
costs ~12 ms and a fit costs tens of those. Three things make a sweep of this
size affordable:

1. JIT THE PER-FIT PATH. `ndelay_sweep_plants._sim` is a numba twin carrying
   both grey-box structures; this file drives it at `two_state=0`. That is
   where nearly all the work is -- one finite-difference Jacobian per solver
   iteration, tens of iterations, hundreds of grid points.
2. VECTORIZE THE WIDE WORK. `simulate_batch` evaluates B parameter sets at once
   with numpy, which is how the sigma/h_amb trade-off surface is computed.
   Batching only pays above B~16 (see `--bench`), and one fit's Jacobian needs
   just len(_FREE)+1 columns, so vectorization is applied where the work is
   actually wide rather than everywhere.
3. PARALLELIZE. Grid points are independent, so they run across a
   `ProcessPoolExecutor`. Workers are capped at `cpu_count() - 2` because a
   live `control.py` and gunicorn share this machine.

`verify_twins()` asserts BOTH twins reproduce `simulate_grey_box` before any
result is trusted -- the sweep is only evidence about the shipped fitter if it
is solving the shipped fitter's problem, at the shipped fitter's sub-step,
which `_MAX_DT` reads off the shipped function rather than restating.

Numba is an opt-in extra here, never a project dependency: it pins numpy below
the version this project runs, and downgrading the numeric library under the
control loop, the web app and the test suite to speed up a dev-only sweep is
not a trade this repo makes. `uv run --with numba` installs it for the sweep
process alone. The fit that runs at cook teardown on a Raspberry Pi is
single-process, pure numpy/scipy, and calls `simulate_grey_box` directly.

What made the fits affordable in the first place is not in this file: the
scaled-variable solve in `fit_params` conditions the problem well enough that
the solver makes real progress per evaluation instead of crawling. It does not
make every fit converge -- on a real cook it still runs out of evaluations --
but it reaches a materially better point inside the same budget.

Usage (numba is required for the jitted twin):
    uv run --with numba python -m docs.superpowers.experiments.sigma_identifiability
    uv run --with numba python -m docs.superpowers.experiments.sigma_identifiability --bench
    uv run --with numba python -m docs.superpowers.experiments.sigma_identifiability --quick
"""

import argparse
import inspect
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
from scipy.optimize import least_squares

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from controller.model_promotion import PROMOTION_BOUNDS, T_FLOOR_C, T_HAZARD_C, effective_tau  # noqa: E402
from controller.mpc_model import _erlang_coefficients, simulate_grey_box  # noqa: E402

OUT = "./docs/superpowers/experiments/_sigma_identifiability.json"

_KELVIN = 273.15

#: The grill the synthetic cooks come from. Deliberately NOT the shipped
#: defaults the fit starts from: every parameter differs, so a fit that
#: "recovers" sigma has had to move the whole model to get there, exactly as a
#: real grill's first calibration must. sigma sits 2.1x above the fitter's
#: starting 1.4e-9 and 3.3x below the PROMOTION_BOUNDS ceiling, so a sigma
#: parked at its upper bound is visibly a failure rather than something that
#: could be mistaken for success.
TRUTH = {"C_c": 800.0, "h_amb": 0.35, "K_Q": 5.0, "theta": 80.0, "n_delay": 8, "sigma": 3.0e-9}

#: Where the fit starts -- controller/mpc.py's _DEFAULTS. A gate that simply
#: never frees sigma therefore scores zero here rather than looking perfect.
INIT = {"C_c": 320.0, "h_amb": 0.50, "K_Q": 3.5, "theta": 50.0}
INIT_SIGMA = 1.4e-9

T_AMB = 20.0
DT = 5.0  # log cadence, matching the MPC controller's default control period

#: Thermocouple noise, in C. Sized from the high-frequency content of the real
#: MAK cook in tests/unit/mpc/fixtures (see `--bench`, which prints it): the
#: sweep must not conclude that sigma is identifiable from data cleaner than a
#: grill ever produces.
NOISE_C = 0.5

_FREE = ("K_Q", "C_c", "h_amb", "theta", "sigma")
_SIGMA_MAX = PROMOTION_BOUNDS["sigma"][1]

#: The integration sub-step the SHIPPED fitter runs at, read off
#: `simulate_grey_box`'s own signature rather than restated here. A harness
#: that is evidence about controller/update_mpc.py has to be discretized the
#: way controller/update_mpc.py is, and a restated constant is one edit away
#: from silently not being.
_MAX_DT = float(inspect.signature(simulate_grey_box).parameters["max_dt"].default)


# --------------------------------------------------------------------------
# Forward model: `simulate_grey_box` itself for the per-fit residual, and a
# numpy-batched twin of it, verified against it, for the wide work.
# --------------------------------------------------------------------------


def _sim(t, Q, C_c, h_amb, T_amb, T0, K_Q, sigma, theta, n_delay, max_dt=_MAX_DT):
    """`simulate_grey_box` behind a positional signature the harness can pass around."""
    return simulate_grey_box(
        t,
        Q,
        C_c=C_c,
        h_amb=h_amb,
        T_amb=T_amb,
        T0=T0,
        K_Q=K_Q,
        sigma=sigma,
        theta=theta,
        n_delay=n_delay,
        max_dt=max_dt,
    )


def _jit_sim(t, Q, C_c, h_amb, T_amb, T0, K_Q, sigma, theta, n_delay, max_dt=_MAX_DT):
    """The jitted twin, in the same positional signature, for the per-fit path.

    ndelay_sweep_plants._sim carries both grey-box structures; `two_state=0`
    selects the single chamber lump this file is now about. It is imported
    rather than written again here so there is one jitted twin in this
    directory and one place `verify_twins` has to check.
    """
    from docs.superpowers.experiments.ndelay_sweep_plants import _sim as _njit_sim

    return _njit_sim(
        np.ascontiguousarray(t, dtype=float),
        np.ascontiguousarray(Q, dtype=float),
        0.0,  # C_f: unused with two_state=0
        C_c,
        0.0,  # h_fc: unused with two_state=0
        h_amb,
        T_amb,
        T0,
        K_Q,
        sigma,
        theta,
        int(n_delay),
        0,
        max_dt,
    )


def simulate_batch(t, Q, *, T_amb, T0, C_c, h_amb, K_Q, sigma, theta, n_delay=0, max_dt=_MAX_DT):
    """`simulate_grey_box` for B parameter sets at once; returns shape (len(t), B).

    Every thermal parameter may be a length-B array. The time loop stays in
    Python because the integration is sequential, but each step is one numpy
    operation over all B sets, which is what makes a wide sweep affordable.

    The transport chain is advanced in closed form, per set, exactly as
    `simulate_grey_box` does: it is linear with a constant input across the
    sample interval, so `_erlang_coefficients` gives its state at every
    sub-step. That is not an optimisation here -- it is what makes this a twin
    of the shipped simulator rather than of the Euler scheme the shipped one
    stopped using, and `verify_twins` measures the difference at 1e-9 C.
    """
    t = np.asarray(t, dtype=float)
    Q = np.asarray(Q, dtype=float)
    C_c, h_amb, K_Q, sigma, theta = np.broadcast_arrays(
        *(np.atleast_1d(np.asarray(v, dtype=float)) for v in (C_c, h_amb, K_Q, sigma, theta))
    )
    B = C_c.shape[0]
    n = max(int(n_delay), 0)
    lag_tau = (theta / n) if n > 0 else np.zeros(B)
    active = lag_tau > 0.0
    safe_tau = np.where(active, lag_tau, 1.0)
    lags = np.zeros((max(n, 1), B))
    T_c = np.full(B, float(T0))
    out = np.empty((len(t), B))
    amb4 = (T_amb + _KELVIN) ** 4
    for i in range(len(t)):
        out[i] = T_c
        if i == len(t) - 1:
            break
        span = float(t[i + 1] - t[i])
        if span <= 0.0:
            continue
        steps = max(1, int(np.ceil(span / max_dt)))
        dt = span / steps
        u = float(Q[i])
        if n > 0:
            # (steps, B, n) Erlang coefficients: one closed-form propagator per
            # sub-step per parameter set, built by the same recurrence the
            # shipped simulator uses so an over-long sub-step underflows to
            # zero rather than overflowing on the way to an exp.
            a = np.arange(1, steps + 1)[:, None] * (dt / safe_tau)[None, :]
            coef = np.empty((steps, B, n))
            coef[:, :, 0] = np.exp(-a)
            for m in range(1, n):
                coef[:, :, m] = coef[:, :, m - 1] * a / m
            dev = lags[:n] - u  # (n, B) departure from this input's equilibrium
            heat = u + np.einsum("sbm,mb->sb", coef, dev[::-1])
            last = coef[-1]  # (B, n)
            advanced = np.empty((n, B))
            for j in range(n):
                advanced[j] = u + np.einsum("bm,mb->b", last[:, : j + 1], dev[j::-1])
            lags[:n] = np.where(active, advanced, lags[:n])
            heat = np.where(active[None, :], heat, u)
        else:
            heat = np.full((steps, B), u)
        for k in range(steps):
            rad = sigma * ((T_c + _KELVIN) ** 4 - amb4)
            T_c = T_c + dt * (K_Q * heat[k] - h_amb * (T_c - T_amb) - rad) / C_c
    return out


def verify_twins(verbose=True):
    """Both twins must reproduce `simulate_grey_box` before anything is trusted."""
    rng = np.random.default_rng(0)
    t = np.arange(0.0, 1800.0, DT)
    Q = np.clip(50.0 + 50.0 * np.sin(t / 300.0), 5.0, 100.0)
    worst_batch = 0.0
    worst_jit = 0.0
    for _ in range(8):
        p = {
            "C_c": float(rng.uniform(150.0, 3000.0)),
            "h_amb": float(rng.uniform(0.05, 1.0)),
            "K_Q": float(rng.uniform(1.0, 9.0)),
            "sigma": float(rng.uniform(0.0, 8e-9)),
            "theta": float(rng.uniform(0.0, 200.0)),
            "n_delay": int(rng.integers(0, 12)),
        }
        ref = simulate_grey_box(t, Q, T_amb=T_AMB, T0=25.0, max_dt=_MAX_DT, **p)
        got_batch = simulate_batch(t, Q, T_amb=T_AMB, T0=25.0, **p)[:, 0]
        worst_batch = max(worst_batch, float(np.max(np.abs(got_batch - ref))))
        got_jit = _jit_sim(
            t, Q, p["C_c"], p["h_amb"], T_AMB, 25.0, p["K_Q"], p["sigma"], p["theta"], p["n_delay"], _MAX_DT
        )
        worst_jit = max(worst_jit, float(np.max(np.abs(got_jit - ref))))
    if verbose:
        print(f"twin check: batched max|diff| = {worst_batch:.3e} C, jitted max|diff| = {worst_jit:.3e} C")
    assert worst_batch < 1e-9, f"batched twin disagrees with simulate_grey_box by {worst_batch} C"
    assert worst_jit < 1e-9, f"jitted twin disagrees with simulate_grey_box by {worst_jit} C"

    # Negative control on the generator: the truth parameters must reproduce
    # each synthetic record to within the measurement noise. If they cannot,
    # the cook carries a systematic error the fit will spend `sigma` on, and
    # every identifiability number below is about that error instead.
    worst_truth = 0.0
    for cold, hot in ((200.0, 200.0), (60.0, 260.0), (120.0, 240.0)):
        tt, temp, Q = make_cook(cold, hot, 0)
        sim = simulate_grey_box(tt, Q, T_amb=T_AMB, T0=float(temp[0]), **TRUTH)
        worst_truth = max(worst_truth, float(np.sqrt(np.mean((sim - temp) ** 2))))
    if verbose:
        print(f"generator check: worst truth-parameter RMSE = {worst_truth:.3f} C (noise is {NOISE_C})")
    assert worst_truth < 1.5 * NOISE_C, f"synthetic cooks carry {worst_truth} C of error the truth model cannot explain"
    return worst_batch, worst_truth


# --------------------------------------------------------------------------
# Synthetic cooks with a controlled temperature range
# --------------------------------------------------------------------------


def make_cook(t_cold, t_hot, seed, *, noise_c=NOISE_C, leg_s=1350.0):
    """A two-legged cook: hold at `t_cold`, step to `t_hot`, hold again.

    Returns (t, temp_measured, Q) for the WHOLE record, so the span it covers
    is exactly [t_cold, t_hot] and span and hot end are set independently --
    `t_cold == t_hot` gives the isothermal case the gate must refuse.

    The truth plant starts at `T_c = t_cold` with an empty transport-lag
    chain, which is precisely how `simulate_grey_box` initialises itself. That
    matters more than it looks: an earlier version of this harness generated a
    long cook and returned a mid-cook slice, where the plant's lag states are
    nothing like the simulator's assumed ones. The fit then spent
    its freedom explaining that initial-condition step instead of the physics,
    drove `sigma` to its upper bound, and scored the data as unidentifiable at
    every span. Truth parameters must reproduce the record to within the noise,
    or the sweep measures the harness rather than the grill.

    Q comes from a proportional drive against the truth model with the same
    5..100 authority the allocator has, so the input is a plausible grill drive
    rather than a designed excitation the answer could be an artefact of.
    """
    rng = np.random.default_rng(seed)
    n = int(2 * leg_s / DT)
    t = np.arange(n, dtype=float) * DT
    setpoint = np.where(t < leg_s, t_cold, t_hot)

    p = TRUTH
    n_lag = int(p["n_delay"])
    lag_tau = p["theta"] / n_lag if n_lag > 0 else 0.0
    lags = np.zeros(n_lag)
    T_c = float(t_cold)
    temp = np.empty(n)
    Q = np.empty(n)
    amb4 = (T_AMB + _KELVIN) ** 4
    # The plant integrates at the shipped sub-step, and its chain in closed
    # form, so what separates the fit from the truth is the parameters and the
    # noise rather than two different discretizations of the same equations.
    sub = max(1, int(np.ceil(DT / _MAX_DT)))
    dt = DT / sub
    for i in range(n):
        temp[i] = T_c
        Q[i] = float(np.clip(5.0 + 3.0 * (setpoint[i] - T_c), 5.0, 100.0))
        if i == n - 1:
            break
        u = Q[i]
        if lag_tau > 0.0:
            dev = lags - u
            coef = _erlang_coefficients(n_lag, np.arange(1, sub + 1) * (dt / lag_tau))
            heat = (u + coef @ dev[::-1]).tolist()
            lags = u + np.convolve(coef[-1], dev)[:n_lag]
        else:
            heat = [u] * sub
        for k in range(sub):
            T_c += (
                dt
                * (p["K_Q"] * heat[k] - p["h_amb"] * (T_c - T_AMB) - p["sigma"] * ((T_c + _KELVIN) ** 4 - amb4))
                / p["C_c"]
            )
    return t, temp + rng.normal(0.0, noise_c, n), Q


# --------------------------------------------------------------------------
# The fit under test -- the shipped one's numerics, with the jitted simulator
# --------------------------------------------------------------------------


def fit(t, temp, Q, *, free_sigma, sigma0=INIT_SIGMA, sim=None, free=None, init=None):
    """Fit the grey-box parameters, optionally freeing `sigma`.

    Mirrors what `controller.update_mpc.fit_params` does: per-parameter bounds
    and a solve in variables scaled by their own nominal magnitude. The scaling
    is not cosmetic -- scipy's finite-difference step is
    `eps**0.5 * max(1, |x|)`, so an unscaled `sigma` near 1.4e-9 is probed with
    a step ten times its own value and the solver sees noise.
    """
    sim = sim or _jit_sim
    init = dict(INIT if init is None else init)
    if free is None:
        free = _FREE if free_sigma else _FREE[:-1]
    nominal = {**init, "sigma": 1e-9}
    scale = np.array([abs(nominal[k]) for k in free])
    x0 = np.array([(init[k] if k != "sigma" else sigma0) for k in free])
    lo = np.array([(1e-9 if k != "sigma" else 0.0) for k in free])
    hi = np.array([(np.inf if k != "sigma" else _SIGMA_MAX) for k in free])
    n_delay = int(TRUTH["n_delay"])
    T0 = float(temp[0])

    def residual(z):
        p = {**init, **dict(zip(free, z * scale))}
        s = p.get("sigma", sigma0)
        return sim(t, Q, p["C_c"], p["h_amb"], T_AMB, T0, p["K_Q"], s, p["theta"], n_delay, _MAX_DT) - temp

    res = least_squares(residual, x0 / scale, method="trf", bounds=(lo / scale, hi / scale), max_nfev=2000)
    out = dict(zip(free, (float(v) for v in res.x * scale)))
    out.setdefault("sigma", float(sigma0))
    out["rmse"] = float(np.sqrt(2.0 * res.cost / len(temp)))
    out["nfev"] = int(res.nfev)
    return out


#: The parameterisation with the scaling degeneracy removed. Scaling
#: (K_Q, C_c, h_amb, sigma) by a common factor leaves the measured chamber
#: temperature EXACTLY unchanged: with one lump the chamber's state equation is
#: the only one, and it is homogeneous of degree zero in those four -- multiply
#: all of them and the numerator and the C_c below it scale together. Under two
#: lumps this held only approximately, through the firepot being quasi-steady;
#: the collapse to one lump made the degeneracy exact rather than removing it.
#: Holding any one of the four pins the family. Holding K_Q isolates the
#: question this sweep is actually asking -- "does the record's temperature
#: range separate sigma from h_amb?" -- from the entirely separate question of
#: whether the record fixes the overall scale.
_FREE_NO_SCALE = ("C_c", "h_amb", "theta", "sigma")


def _rad_conductance_span(temp, sigma_ref=INIT_SIGMA, dwell=False):
    """Variation in linearized radiative conductance across the record.

    `4*sigma*(T+273.15)**3` is what radiation contributes to the chamber's loss
    conductance. Its SPREAD over the record is the signal that separates sigma
    from the flat h_amb: no spread, no separation. Evaluated at the fitter's
    starting sigma, because a gate has to be computable before the fit.

    `dwell` takes the 10th-to-90th percentile of the record instead of its
    min-to-max. Min-to-max counts a temperature the grill merely passed
    through: a record that starts off its setpoint, droops once and then sits
    there has a wide min-to-max but only ever holds ONE operating point, and it
    does not identify sigma. Percentiles weight by how long the grill actually
    spent at a temperature, which is what carries the information.
    """
    lo, hi = np.percentile(temp, [10, 90]) if dwell else (np.min(temp), np.max(temp))
    return 4.0 * sigma_ref * ((float(hi) + _KELVIN) ** 3 - (float(lo) + _KELVIN) ** 3)


def _point(args):
    t_cold, t_hot, seed = args
    t, temp, Q = make_cook(t_cold, t_hot, seed)
    free = fit(t, temp, Q, free_sigma=True)
    held = fit(t, temp, Q, free_sigma=False)
    # Same cook, degeneracy removed, so sigma recovery reflects the record's
    # temperature range alone.
    pinned = fit(t, temp, Q, free_sigma=True, free=_FREE_NO_SCALE, init={**INIT, "K_Q": TRUTH["K_Q"]})

    def tau_err(p):
        return max(
            abs(effective_tau(p, T) - effective_tau(TRUTH, T)) / effective_tau(TRUTH, T)
            for T in (T_FLOOR_C, T_HAZARD_C)
        )

    return {
        "t_cold": t_cold,
        "t_hot": t_hot,
        "seed": seed,
        "span": float(temp.max() - temp.min()),
        "dwell_span": float(np.diff(np.percentile(temp, [10, 90]))[0]),
        "hot": float(temp.max()),
        "rad_span": _rad_conductance_span(temp),
        "rad_dwell": _rad_conductance_span(temp, dwell=True),
        "sigma_true": TRUTH["sigma"],
        "sigma_ratio": free["sigma"] / TRUTH["sigma"],
        "sigma_ratio_pinned": pinned["sigma"] / TRUTH["sigma"],
        "rmse_free": free["rmse"],
        "rmse_held": held["rmse"],
        "rmse_pinned": pinned["rmse"],
        "tau_err_free": tau_err(free),
        "tau_err_held": tau_err(held),
        "tau_err_pinned": tau_err(pinned),
        "nfev_free": free["nfev"],
    }


def run_sweep(grid, seeds, workers):
    """Fit every grid point across processes.

    The integration is a Python loop, not a BLAS call, so each worker is a
    single busy core and the only thing that needs limiting is how many of them
    there are -- `workers` is capped by the caller at `cpu_count() - 2` to leave
    room for the live `control.py` and gunicorn on this machine.
    """
    points = [(c, h, s) for (c, h) in grid for s in seeds]
    t0 = time.perf_counter()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(_point, points, chunksize=1))
    return rows, time.perf_counter() - t0


def trade_off_surface(t_cold, t_hot, seed=0, n=61):
    """How flat is the sigma/h_amb valley for this cook? (vectorized, width n*n)

    The directed probe behind the gate: for a grid of (sigma, h_amb) pairs with
    the other parameters at truth, the residual cost surface shows whether the
    data separates the two loss terms at all. An isothermal cook produces a
    valley that is flat along a whole sigma/h_amb line -- any point on it fits
    equally well -- while a wide-span cook produces a single minimum.
    """
    t, temp, Q = make_cook(t_cold, t_hot, seed)
    sig = np.linspace(0.0, 8e-9, n)
    ham = np.linspace(0.05, 0.80, n)
    S, H = np.meshgrid(sig, ham, indexing="ij")
    S, H = S.ravel(), H.ravel()
    sim = simulate_batch(
        t,
        Q,
        T_amb=T_AMB,
        T0=float(temp[0]),
        C_c=np.full(S.size, TRUTH["C_c"]),
        h_amb=H,
        K_Q=TRUTH["K_Q"],
        sigma=S,
        theta=TRUTH["theta"],
        n_delay=int(TRUTH["n_delay"]),
    )
    rmse = np.sqrt(np.mean((sim - temp[:, None]) ** 2, axis=0))
    best = int(np.argmin(rmse))
    # Everything within 2% RMSE of the best is data-indistinguishable from it.
    near = rmse <= rmse[best] * 1.02
    return {
        "t_cold": t_cold,
        "t_hot": t_hot,
        "span": float(temp.max() - temp.min()),
        "best_sigma": float(S[best]),
        "best_h_amb": float(H[best]),
        "best_rmse": float(rmse[best]),
        "sigma_width": float(S[near].max() - S[near].min()),
        "sigma_width_ratio": float((S[near].max() - S[near].min()) / TRUTH["sigma"]),
        "n_near": int(near.sum()),
    }


def bench():
    """Timings that justify how the sweep is built, plus the noise estimate."""
    import pandas as pd

    verify_twins()
    t, temp, Q = make_cook(80.0, 250.0, 0)
    p = TRUTH

    reps = 20
    t0 = time.perf_counter()
    for _ in range(reps):
        simulate_grey_box(t, Q, T_amb=T_AMB, T0=25.0, max_dt=_MAX_DT, **p)
    scalar_ms = (time.perf_counter() - t0) / reps * 1e3
    print(f"simulate_grey_box  {scalar_ms:8.3f} ms/sim")

    jit_args = (p["C_c"], p["h_amb"], T_AMB, 25.0, p["K_Q"], p["sigma"], p["theta"], p["n_delay"], _MAX_DT)
    _jit_sim(t, Q, *jit_args)  # compile before timing
    t0 = time.perf_counter()
    for _ in range(reps):
        _jit_sim(t, Q, *jit_args)
    jit_ms = (time.perf_counter() - t0) / reps * 1e3
    print(f"jitted twin        {jit_ms:8.3f} ms/sim  ({scalar_ms / jit_ms:5.1f}x)")
    # Where batching starts paying. A single fit's finite-difference Jacobian
    # needs only len(_FREE)+1 columns, which lands below the crossover -- so
    # the per-fit path calls simulate_grey_box directly and batching is saved
    # for the trade-off surface, which is hundreds of sets wide.
    for B in (1, 4, 8, 16, 32, 128, 512):
        kw = {k: (np.full(B, v) if k != "n_delay" else int(v)) for k, v in p.items()}
        t0 = time.perf_counter()
        simulate_batch(t, Q, T_amb=T_AMB, T0=25.0, **kw)
        el = (time.perf_counter() - t0) * 1e3
        print(f"simulate_batch B={B:4d} {el:8.2f} ms total {el / B:8.3f} ms/set  ({scalar_ms / (el / B):5.1f}x)")

    fx = "./tests/unit/mpc/fixtures/mak_cook_2026-08-02.csv"
    if os.path.exists(fx):
        real = pd.read_csv(fx)["temp_c"].values
        hf = np.diff(real, n=2) / np.sqrt(6.0)
        print(f"MAK cook high-frequency noise estimate: {np.std(hf):.3f} C (harness uses {NOISE_C})")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bench", action="store_true", help="Print timings and the noise estimate, then stop")
    ap.add_argument("--quick", action="store_true", help="Small grid (smoke test)")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    if args.bench:
        bench()
        return

    verify_twins()
    workers = max(1, (os.cpu_count() or 4) - 2)

    if args.quick:
        grid = [(200.0, 200.0), (180.0, 250.0), (60.0, 260.0)]
        seeds = range(2)
    else:
        # Isothermal at several levels, then widening spans off a cold, a warm
        # and a hot floor -- so span and hot end vary independently.
        # Spans off four different floors, so "how wide" and "how hot" vary
        # independently: a 40 C span at 240 C and a 40 C span at 60 C carry
        # very different amounts of radiative signal.
        grid = []
        for cold in (40.0, 100.0, 160.0, 220.0):
            for span in (0.0, 5.0, 10.0, 20.0, 40.0, 60.0, 90.0, 120.0, 160.0, 200.0):
                hot = cold + span
                if hot <= 290.0:
                    grid.append((cold, hot))
        seeds = range(10)

    rows, elapsed = run_sweep(grid, list(seeds), workers)
    print(f"{len(rows)} fits on {workers} workers in {elapsed:.1f} s ({elapsed / len(rows) * 1e3:.0f} ms/fit)")

    surfaces = [trade_off_surface(c, h) for (c, h) in ((200.0, 200.0), (180.0, 220.0), (120.0, 240.0), (60.0, 260.0))]

    with open(args.out, "w") as f:
        json.dump(
            {
                "truth": {k: float(v) for k, v in TRUTH.items()},
                "init_sigma": INIT_SIGMA,
                "noise_c": NOISE_C,
                "workers": workers,
                "elapsed_s": elapsed,
                "rows": rows,
                "surfaces": surfaces,
            },
            f,
            indent=1,
        )
    print(f"wrote {args.out}")

    # Recovery is judged on the ratio to truth, so the criterion reads the same
    # whatever sigma the grill actually has.
    by_cell = {}
    for r in rows:
        by_cell.setdefault((r["t_cold"], r["t_hot"]), []).append(r)

    print("\nsigma recovery with the scaling degeneracy REMOVED (K_Q held) -- this is")
    print("the question the gate is about: does the record's temperature range")
    print("separate sigma from h_amb?")
    print("  cold   hot   span dwell rad_span rad_dwl | median  worst | ok25% | tau err pin/held/free")
    for (cold, hot), rs in sorted(by_cell.items()):
        pin = np.array([r["sigma_ratio_pinned"] for r in rs])
        med = lambda k, rs=rs: np.median([r[k] for r in rs])  # noqa: E731
        good = np.mean(np.abs(pin - 1.0) <= 0.25)
        worst = pin[np.argmax(np.abs(pin - 1.0))]
        print(
            f"  {cold:5.0f} {hot:5.0f} {med('span'):6.1f} {med('dwell_span'):5.1f}"
            f" {med('rad_span'):8.4f} {med('rad_dwell'):7.4f} | {pin.mean():6.3f} {worst:6.3f} |"
            f" {good:5.0%} | {med('tau_err_pinned'):5.1%} / {med('tau_err_held'):5.1%} / {med('tau_err_free'):5.1%}"
        )

    print("\nsigma recovery with the SHIPPED free set (K_Q free, so the scaling")
    print("degeneracy is present) -- absolute sigma wanders even where the")
    print("temperature range identifies it:")
    print("  cold   hot   span | median  min    max")
    for (cold, hot), rs in sorted(by_cell.items()):
        f = np.array([r["sigma_ratio"] for r in rs])
        span = np.median([r["span"] for r in rs])
        print(f"  {cold:5.0f} {hot:5.0f} {span:6.1f} | {np.median(f):6.3f} {f.min():6.3f} {f.max():6.3f}")

    # The gate's decision variable, scored against the identifiability outcome.
    # Which decision variable actually separates identifiable cells from
    # unidentifiable ones? A usable gate needs a threshold with every
    # unidentifiable cell below it and every identifiable cell above.
    print("\nthreshold search (K_Q held): best single-threshold split by each candidate")
    cells = list(by_cell.items())
    for name, key in (
        ("span", "span"),
        ("dwell_span", "dwell_span"),
        ("rad_span", "rad_span"),
        ("rad_dwell", "rad_dwell"),
    ):
        vals = np.array([np.median([r[key] for r in rs]) for _, rs in cells])
        ok = np.array(
            [np.mean(np.abs([r["sigma_ratio_pinned"] for r in rs] - np.float64(1.0)) <= 0.25) >= 0.9 for _, rs in cells]
        )
        best, best_thr = -1.0, None
        for thr in np.unique(vals):
            acc = np.mean((vals >= thr) == ok)
            if acc > best:
                best, best_thr = acc, thr
        bad = [
            f"{c:.0f}->{h:.0f}"
            for (c, h), v in zip([k for k, _ in cells], vals)
            if (v >= best_thr) != ok[list(vals).index(v)]
        ]
        print(f"  {name:11s} threshold {best_thr:8.4f}  separates {best:5.0%} of cells  misfits: {bad[:6]}")

    print("\nsigma/h_amb trade-off surface (width of the data-indistinguishable valley):")
    for s in surfaces:
        print(
            f"  span {s['span']:6.1f} C (hot {s['t_hot']:.0f}): best sigma {s['best_sigma']:.2e}, "
            f"valley width {s['sigma_width_ratio']:6.2f}x truth over {s['n_near']} cells"
        )


if __name__ == "__main__":
    main()
