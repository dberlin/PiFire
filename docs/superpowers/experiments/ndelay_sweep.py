#!/usr/bin/env python3

"""
*****************************************
 PiFire MPC Deadtime-Chain Length Sweep
*****************************************

 Chooses controller/mpc.py's n_delay on DEAD TIME and COAST recovery rather
 than on RMSE. Those two are what decide overshoot; RMSE falls monotonically
 with n_delay on every record measured and so cannot pick a value on its own.

 n_delay is a structure constant, not a fitted parameter, so raising it costs
 no degrees of freedom. What it does cost is solver time -- it sizes the state
 vector and therefore the NLP -- so that is measured here and is half the
 decision. The answer is the knee: the largest chain whose marginal dead-time
 recovery still justifies its marginal solve cost.

 Everything is fitted with the SHIPPED free set (K_Q, C_c, theta) in log
 space, which is what a real refit solves, against both plants in
 controller/grill_sim.py and the real cook in tests/unit/mpc/fixtures.

 Needs numba for the jitted twin:
   uv run --with numba python -m docs.superpowers.experiments.ndelay_sweep
*****************************************
"""

import math
import os
import sys
import time

import numpy as np
from scipy.optimize import least_squares

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ndelay_sweep_plants import DEFAULTS, NORMAL, real_cook, run_plant, sim  # noqa: E402

KELVIN = 273.15
Q_FULL = 100.0
T_HAZARD_C = (550.0 - 32.0) * 5.0 / 9.0
T_FLOOR_C = (75.0 - 32.0) * 5.0 / 9.0
FREE = ("K_Q", "C_c", "theta")

#: The real cook's own coast, reduced to a step: the chamber peaked at
#: 271.278 C at t=1108.573 s and the equal-area equivalent step of the Q taper
#: falls at t=968.2 s. Same numbers tests/unit/mpc/test_model_promotion.py uses.
COOK_PEAK_C = 271.278
COOK_STEP_COAST_S = 1108.573 - 968.2


def fit(records, *, two_state, n_delay, starts=4, seed=0):
    rng = np.random.default_rng(seed)
    base = dict(DEFAULTS)
    best = None
    for s in range(starts):
        x0 = np.log([base[k] for k in FREE])
        if s:
            x0 = x0 + rng.normal(0, 0.5, size=len(FREE))

        def resid(z):
            p = dict(base)
            p.update({k: float(np.exp(v)) for k, v in zip(FREE, z)})
            out = []
            for r in records:
                y = sim(r["t"], r["Q"], dict(p, T0=float(r["true"][0])), two_state=two_state, n_delay=n_delay)
                if not np.all(np.isfinite(y)):
                    y = np.full_like(r["true"], 1e4)
                out.append((y - r["true"]) / np.sqrt(len(r["true"])))
            return np.concatenate(out)

        res = least_squares(resid, x0, method="trf", bounds=(math.log(1e-9), np.inf), max_nfev=2000)
        p = dict(base)
        p.update({k: float(np.exp(v)) for k, v in zip(FREE, res.x)})
        per = []
        for r in records:
            y = sim(r["t"], r["Q"], dict(p, T0=float(r["true"][0])), two_state=two_state, n_delay=n_delay)
            per.append(float(np.sqrt(np.mean((y - r["true"]) ** 2))))
        j = float(np.sqrt(np.mean(np.array(per) ** 2)))
        if best is None or j < best[0]:
            best = (j, p)
    return best


def dead_and_coast(p, *, two_state, n_delay):
    """What the FITTED MODEL predicts on the cq_probe profile."""
    duty = np.concatenate([np.full(900, 1.0), np.full(900, 0.0)])
    t = np.arange(len(duty), dtype=float)
    y = sim(t, duty * 100.0, dict(p, T0=20.0), two_state=two_state, n_delay=n_delay)
    rise = np.nonzero(y - y[0] >= 1.0)[0]
    dead = float(t[rise[0]]) if len(rise) else float("inf")
    return dead, float(np.max(y[900:]) - y[900])


def plant_dead_and_coast(plant):
    r = run_plant(plant, "cq_probe")
    y, t = r["true"], r["t"]
    rise = np.nonzero(y - y[0] >= 1.0)[0]
    return float(t[rise[0]]), float(np.max(y[900:]) - y[900])


# ---- braking distance, single-lump formula (what model_promotion now does) --
def _chain_survival(t, *, stages, mean):
    x = stages * t / mean
    if x > 700.0:
        return 0.0
    term, total = 1.0, 1.0
    for k in range(1, stages):
        term *= x / k
        total += term
    return math.exp(-x) * total


def brake(p, t_ref_c, n_delay):
    flux = p["K_Q"] * Q_FULL
    t_amb = p["T_amb"]
    loss = p["h_amb"] * (t_ref_c - t_amb) + p["sigma"] * ((t_ref_c + KELVIN) ** 4 - (t_amb + KELVIN) ** 4)
    if flux <= 0 or loss <= 0:
        return math.inf
    ratio = loss / flux
    if ratio >= 1.0:
        return 0.0
    if n_delay <= 0 or p["theta"] <= 0:
        return 0.0
    lo, hi = 0.0, p["theta"]
    while _chain_survival(hi, stages=n_delay, mean=p["theta"]) > ratio:
        hi *= 2.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if _chain_survival(mid, stages=n_delay, mean=p["theta"]) > ratio:
            lo = mid
        else:
            hi = mid
    return hi


def longest_brake(p, n_delay):
    return max(brake(p, t, n_delay) for t in (T_FLOOR_C, T_HAZARD_C) if t > p["T_amb"])


# ---- NLP solve time --------------------------------------------------------
def solve_time(n_delay, reps=30):
    """Median seconds per make_step for the shipped NLP at this n_delay."""
    from controller.mpc import Controller, _DEFAULTS

    cfg = dict(_DEFAULTS, n_delay=n_delay, policy="nlp", theta=110.0)
    c = Controller(cfg, "C", {"u_min": 0.1, "u_max": 0.9, "HoldCycleTime": 25})
    c.set_target(190.0)
    c.update(150.0)  # cold start, discarded
    ts = []
    for _ in range(reps):
        t0 = time.perf_counter()
        c.update(150.0 + float(np.random.default_rng(0).normal(0, 0.5)))
        ts.append(time.perf_counter() - t0)
    return float(np.median(ts)) * 1000.0


def main():
    grid = (4, 6, 8, 12, 16, 20, 24, 32)
    mak = [run_plant("mak", s) for s in NORMAL if s != "lid"]
    gen = [run_plant("generic", s) for s in NORMAL if s != "lid"]
    rc = [real_cook()]

    mak_truth = plant_dead_and_coast("mak")
    gen_truth = plant_dead_and_coast("generic")
    print(
        f"plant truth   MAK dead={mak_truth[0]:.0f}s coast={mak_truth[1]:.1f}C   "
        f"generic dead={gen_truth[0]:.0f}s coast={gen_truth[1]:.1f}C"
    )
    print(f"real cook step-equivalent coast = {COOK_STEP_COAST_S:.1f} s at {COOK_PEAK_C:.1f} C\n")

    print("=== TWO-STATE reference (the model before the surgery) ===")
    for nd in (4,):
        jm, pm = fit(mak, two_state=True, n_delay=nd)
        dm, cm = dead_and_coast(pm, two_state=True, n_delay=nd)
        jr, pr = fit(rc, two_state=True, n_delay=nd, starts=6)
        print(
            f"  n_delay={nd:2d}  MAK rmse={jm:.3f} dead={dm:.0f}s coast={cm:.1f}C   "
            f"real rmse={jr:.3f} theta={pr['theta']:.1f}"
        )
    print()

    hdr = (
        f"{'nd':>3} | {'MAK rmse':>8} {'dead':>6} {'coast':>7} | {'gen rmse':>8} {'dead':>6} {'coast':>7} | "
        f"{'real rmse':>9} {'theta':>7} {'brakeHot':>8} {'margin':>7} | {'solve ms':>8}"
    )
    print("=== SINGLE LUMP across n_delay ===")
    print(hdr)
    print("-" * len(hdr))
    rows = []
    for nd in grid:
        jm, pm = fit(mak, two_state=False, n_delay=nd)
        dm, cm = dead_and_coast(pm, two_state=False, n_delay=nd)
        jg, pg = fit(gen, two_state=False, n_delay=nd)
        dg, cg = dead_and_coast(pg, two_state=False, n_delay=nd)
        jr, pr = fit(rc, two_state=False, n_delay=nd, starts=6)
        bh = brake(pr, COOK_PEAK_C, nd)
        st = solve_time(nd)
        rows.append(
            dict(
                nd=nd,
                mak_rmse=jm,
                mak_dead=dm,
                mak_coast=cm,
                gen_rmse=jg,
                gen_dead=dg,
                gen_coast=cg,
                real_rmse=jr,
                theta=pr["theta"],
                brake_hot=bh,
                margin=bh / COOK_STEP_COAST_S,
                solve_ms=st,
                longest=longest_brake(pr, nd),
            )
        )
        print(
            f"{nd:3d} | {jm:8.3f} {dm:5.0f}s {cm:6.1f}C | {jg:8.3f} {dg:5.0f}s {cg:6.1f}C | "
            f"{jr:9.3f} {pr['theta']:7.1f} {bh:7.0f}s {bh / COOK_STEP_COAST_S:6.2f}x | {st:8.1f}"
        )
    print()
    print(
        "dead-time recovery as a fraction of the plant's own (MAK, truth "
        f"{mak_truth[0]:.0f}s) and coast (truth {mak_truth[1]:.1f}C):"
    )
    for r in rows:
        print(
            f"  n_delay={r['nd']:2d}  dead {r['mak_dead'] / mak_truth[0]:.2f}x  "
            f"coast {r['mak_coast'] / mak_truth[1]:.2f}x  longest_brake {r['longest']:.0f}s"
        )


if __name__ == "__main__":
    main()
