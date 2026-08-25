#!/usr/bin/env python3

"""
*****************************************
 PiFire MPC Grey-box Sweep Support: plants, scenarios, jitted twin
*****************************************

 Shared by ndelay_sweep.py. Independent of any earlier study's harness: the
 plants and the reference simulator come from the repo, and the jitted twin is
 verified against controller.mpc_model.simulate_grey_box before it is used for
 anything (main() prints the agreement, which is ~1e-14 C).

 The twin carries BOTH structures -- two_state=True is the two-lump model this
 controller used to plan against, two_state=False is the single lump it plans
 against now -- so a before/after comparison is one call apart and cannot
 accidentally compare a model against itself.

 Needs numba. Run via ndelay_sweep.py, or directly to re-measure the
 single-lump fidelity cost:
   uv run --with numba python -m docs.superpowers.experiments.ndelay_sweep_plants
*****************************************
"""

import os
import sys
import time

import numpy as np
from numba import njit
from scipy.optimize import least_squares

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from controller.grill_sim import DT, GrillSim, MAKGrillSim  # noqa: E402
from controller.mpc_model import simulate_grey_box  # noqa: E402

KELVIN = 273.15
DEFAULTS = {
    "C_f": 9.0,
    "C_c": 320.0,
    "h_fc": 1.3,
    "h_amb": 0.50,
    "T_amb": 20.0,
    "theta": 50.0,
    "K_Q": 3.5,
    "sigma": 1.4e-9,
}


@njit(cache=True)
def _sim(t, Q, C_f, C_c, h_fc, h_amb, T_amb, T0, K_Q, sigma, theta, n_delay, two_state, max_dt):
    n = max(0, n_delay)
    lag_tau = (theta / n) if (n > 0 and theta > 0.0) else 0.0
    lags = np.zeros(n)
    nxt = np.zeros(n)
    coef = np.zeros(n)
    T_f = T0
    T_c = T0
    N = t.shape[0]
    out = np.empty(N)
    amb4 = (T_amb + KELVIN) ** 4
    for i in range(N):
        out[i] = T_c
        if i == N - 1:
            break
        span = t[i + 1] - t[i]
        if span <= 0.0:
            continue
        steps = max(1, int(np.ceil(span / max_dt)))
        dt = span / steps
        u = Q[i]
        if lag_tau > 0.0:
            # exp(A*dt) for the chain: lower-triangular Toeplitz in
            # exp(-a) a**m / m!, a = dt/lag_tau. Mirrors
            # controller.mpc_model._erlang_coefficients, including building the
            # coefficients by recurrence so a long sub-step underflows to zero
            # rather than overflowing a**m on the way.
            a = dt / lag_tau
            coef[0] = np.exp(-a)
            for m in range(1, n):
                coef[m] = coef[m - 1] * a / m
        for _s in range(steps):
            if lag_tau > 0.0:
                # lags <- u + exp(A*dt) @ (lags - u), which is exact for an
                # input held constant across the interval and so has no
                # stability limit in dt at all.
                for j in range(n):
                    acc = 0.0
                    for m in range(j + 1):
                        acc += coef[m] * (lags[j - m] - u)
                    nxt[j] = u + acc
                for j in range(n):
                    lags[j] = nxt[j]
                heat_in = lags[n - 1]
            else:
                heat_in = u
            rad = sigma * ((T_c + KELVIN) ** 4 - amb4)
            if two_state == 1:
                dT_f = (K_Q * heat_in - h_fc * (T_f - T_c)) / C_f
                dT_c = (h_fc * (T_f - T_c) - h_amb * (T_c - T_amb) - rad) / C_c
                T_f += dt * dT_f
            else:
                dT_c = (K_Q * heat_in - h_amb * (T_c - T_amb) - rad) / C_c
            T_c += dt * dT_c
    return out


def sim(t, Q, p, *, two_state, n_delay=4, max_dt=0.125):
    g = dict(DEFAULTS)
    g.update(p)
    return _sim(
        t,
        Q,
        g["C_f"],
        g["C_c"],
        g["h_fc"],
        g["h_amb"],
        g["T_amb"],
        g["T0"],
        g["K_Q"],
        g["sigma"],
        g["theta"],
        n_delay,
        1 if two_state else 0,
        max_dt,
    )


# ---------------------------------------------------------------- scenarios
def _schedule(name, rng):
    def const(v, n):
        return np.full(n, v)

    if name == "ramp_full_coast":
        duty = np.concatenate([const(1.0, 1500), const(0.0, 1200)])
    elif name == "steps_up":
        duty = np.concatenate([const(0.30, 900), const(0.60, 900), const(1.0, 900)])
    elif name == "steps_down":
        duty = np.concatenate([const(1.0, 1200), const(0.50, 900), const(0.20, 900)])
    elif name == "hold_low":
        duty = const(0.25, 3000)
    elif name == "hold_high":
        duty = const(0.70, 3000)
    elif name == "prbs":
        segs = []
        while sum(len(s) for s in segs) < 3600:
            segs.append(const(float(rng.uniform(0.0, 1.0)), int(rng.integers(60, 180))))
        duty = np.concatenate(segs)[:3600]
    elif name == "lid":
        duty = const(0.40, 1800)
    elif name == "pulse":
        duty = np.concatenate([const(1.0, 300), const(0.0, 600), const(1.0, 300), const(0.0, 600)])
    elif name == "cq_probe":
        duty = np.concatenate([const(1.0, 900), const(0.0, 900)])
    else:
        raise ValueError(name)
    lid = np.zeros(len(duty), dtype=bool)
    if name == "lid":
        lid[1000:1090] = True
    return duty, lid


NORMAL = ("ramp_full_coast", "steps_up", "steps_down", "hold_low", "hold_high", "prbs", "lid", "pulse")


def run_plant(plant_name, scen, seed=0, T0=20.0, fan=1.0):
    rng = np.random.default_rng(1000 + hash(scen) % 997)
    duty, lid = _schedule(scen, rng)
    cls = GrillSim if plant_name == "generic" else MAKGrillSim
    kw = {"seed": seed, "fixed_fan": fan}
    if plant_name == "mak":
        kw["T0"] = T0
    s = cls(**kw)
    if plant_name == "generic":
        s.T_f = s.T_c = s.T_meas = float(T0)
    n = len(duty)
    t = np.arange(n, dtype=float) * DT
    true = np.empty(n)
    meas = np.empty(n)
    for i in range(n):
        s.step(auger_on=float(duty[i]), fan_frac=fan, lid_open=bool(lid[i]))
        true[i] = s.true_Tc
        meas[i] = s.measured()
    return {"plant": plant_name, "scen": scen, "t": t, "Q": duty * 100.0, "true": true, "meas": meas}


def real_cook():
    import pandas as pd

    df = pd.read_csv(os.path.join(REPO, "tests/unit/mpc/fixtures/mak_cook_2026-08-02.csv"))
    t = df["time_s"].values.astype(float)
    return {
        "plant": "mak_real",
        "scen": "real_cook",
        "t": t - t[0],
        "Q": df["Q"].values.astype(float),
        "true": df["temp_c"].values.astype(float),
        "meas": df["temp_c"].values.astype(float),
    }


# ------------------------------------------------------------------- fitter
FULL_FREE = ("K_Q", "C_c", "h_fc", "h_amb", "theta")
LUMP_FREE = ("K_Q", "C_c", "h_amb", "theta")


def joint_rmse(records, p, *, two_state):
    per = []
    for r in records:
        q = dict(p)
        q["T0"] = float(r["true"][0])
        y = sim(r["t"], r["Q"], q, two_state=two_state)
        if not np.all(np.isfinite(y)):
            return 1e6, [1e6] * len(records)
        per.append(float(np.sqrt(np.mean((y - r["true"]) ** 2))))
    return float(np.sqrt(np.mean(np.array(per) ** 2))), per


def fit_joint(records, free, *, two_state, starts=6, seed=0):
    rng = np.random.default_rng(seed)
    base = dict(DEFAULTS)
    best = None
    for s in range(starts):
        x0 = np.array([np.log(base[k]) for k in free])
        if s:
            x0 = x0 + rng.normal(0, 0.7, size=len(free))

        def resid(z):
            p = dict(base)
            p.update({k: float(np.exp(v)) for k, v in zip(free, z)})
            out = []
            for r in records:
                q = dict(p)
                q["T0"] = float(r["true"][0])
                y = sim(r["t"], r["Q"], q, two_state=two_state)
                if not np.all(np.isfinite(y)):
                    y = np.full_like(r["true"], 1e4)
                # equal weight per scenario regardless of length
                out.append((y - r["true"]) / np.sqrt(len(r["true"])))
            return np.concatenate(out)

        try:
            res = least_squares(resid, x0, method="lm", max_nfev=4000)
        except Exception:
            continue
        p = dict(base)
        p.update({k: float(np.exp(v)) for k, v in zip(free, res.x)})
        j, per = joint_rmse(records, p, two_state=two_state)
        if best is None or j < best[0]:
            best = (j, p, per)
    return best


# --------------------------------------------------- control quantities
def model_dead_time_and_coast(p, *, two_state, n_delay=4):
    """Dead time and coast the FITTED MODEL predicts on the cq_probe profile."""
    duty = np.concatenate([np.full(900, 1.0), np.full(900, 0.0)])
    t = np.arange(len(duty), dtype=float)
    q = dict(p)
    q["T0"] = 20.0
    y = sim(t, duty * 100.0, q, two_state=two_state, n_delay=n_delay)
    # dead time: seconds from the start of full fire until T rises 1 C
    rise = np.nonzero(y - y[0] >= 1.0)[0]
    dead = float(t[rise[0]]) if len(rise) else float("inf")
    # coast: peak after the cut minus temperature at the cut
    cut = 900
    coast = float(np.max(y[cut:]) - y[cut])
    return dead, coast


def plant_dead_time_and_coast(plant_name, seed=0):
    r = run_plant(plant_name, "cq_probe", seed=seed)
    y = r["true"]
    t = r["t"]
    rise = np.nonzero(y - y[0] >= 1.0)[0]
    dead = float(t[rise[0]]) if len(rise) else float("inf")
    coast = float(np.max(y[900:]) - y[900])
    return dead, coast


def noise_floor(plant_name, scens=NORMAL, seeds=(0, 1, 2, 3)):
    vals = []
    for sc in scens:
        runs = [run_plant(plant_name, sc, seed=s)["true"] for s in seeds]
        A = np.array(runs)
        vals.append(float(np.mean(np.sqrt(np.mean((A - A.mean(0)) ** 2, axis=1)))))
    return float(np.mean(vals))


def main():
    t0 = time.time()
    # --- twin verification against the shipped reference -------------------
    r = run_plant("mak", "steps_up")
    p = dict(DEFAULTS)
    # The reference is the SHIPPED simulator, which is the single lump, so the
    # twin is checked in its single-lump mode. Checking the two-state mode
    # against it would only prove the two disagree, which is the point of the
    # comparison this file exists to make.
    ref = simulate_grey_box(
        r["t"],
        r["Q"],
        C_c=p["C_c"],
        h_amb=p["h_amb"],
        T_amb=p["T_amb"],
        T0=20.0,
        K_Q=p["K_Q"],
        sigma=p["sigma"],
        theta=p["theta"],
        n_delay=4,
    )
    q = dict(p, T0=20.0)
    twin = sim(r["t"], r["Q"], q, two_state=False)
    print(f"twin vs controller.mpc_model.simulate_grey_box: max abs diff = {np.max(np.abs(ref - twin)):.3e} C")
    # And the two-state arm must NOT match it, or the before/after comparison
    # below would be one model against itself.
    two = sim(r["t"], r["Q"], q, two_state=True)
    print(f"two-state arm differs from it by:                {np.max(np.abs(ref - two)):.3f} C (must be > 0)")

    results = {}
    for plant in ("mak", "generic"):
        recs = [run_plant(plant, sc) for sc in NORMAL]
        recs_nolid = [r for r in recs if r["scen"] != "lid"]
        floor = noise_floor(plant)
        print(f"\n=== {plant} plant (noise floor {floor:.3f} C) ===")
        row = {}
        for label, free, two in (("full", FULL_FREE, True), ("lump", LUMP_FREE, False)):
            j, pbest, per = fit_joint(recs, free, two_state=two)
            jn, pn, pern = fit_joint(recs_nolid, free, two_state=two)
            _, per_all = joint_rmse(recs, pn, two_state=two)
            dead, coast = model_dead_time_and_coast(pn, two_state=two)
            row[label] = {
                "joint": j,
                "joint_nolid": jn,
                "worst_nolid": max(pern),
                "dead": dead,
                "coast": coast,
                "params": {k: pn[k] for k in free},
            }
            print(
                f"  {label:5s} nfree={len(free)}  joint={j:.3f}  joint-nolid={jn:.3f}  "
                f"worst(nolid)={max(pern):.3f}  dead={dead:.0f}s  coast={coast:.1f}C"
            )
        cost = row["lump"]["joint_nolid"] - row["full"]["joint_nolid"]
        print(f"  --> single-lump cost (joint-nolid): {cost:+.3f} C")
        pd_, pc_ = plant_dead_time_and_coast(plant)
        print(f"  plant truth: dead={pd_:.0f}s  coast={pc_:.1f}C")
        results[plant] = row

    # --- the real cook ------------------------------------------------------
    rc = [real_cook()]
    print("\n=== real MAK cook (mak_cook_2026-08-02.csv) ===")
    for label, free, two in (("full", FULL_FREE, True), ("lump", LUMP_FREE, False)):
        j, pbest, per = fit_joint(rc, free, two_state=two, starts=8)
        print(f"  {label:5s} RMSE={j:.3f} C   params=" + ", ".join(f"{k}={pbest[k]:.4g}" for k in free))
    print(f"\nelapsed {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
