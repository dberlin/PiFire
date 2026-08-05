#!/usr/bin/env python3
"""Does dropping the temperature regressor rescue online identification?

The shipped FOPDT identifier recovers a negative gain AND a negative time
constant at nearly every delay candidate on a real MAK, with small relative
standard errors -- confidently impossible values. The suspicion this tests: a
FOPDT fit estimates a coefficient on T whose true value is near zero when the
chamber is slow relative to the observation window, and `tau = -1/c_T` inverts
that near-zero number. Any noise that pushes it across zero flips tau's sign
and drags the gain with it.

IPDT drops that regressor and models the chamber as an integrator with dead
time plus a local loss term:

    dT/dt = K_i * u(t - theta) + c0

`c0` absorbs the ambient loss at the operating point, so it is setpoint-local;
`K_i` and `theta` are not, which is the property a dead-time compensator needs.

Both forms are fitted the same way on the same data over the same delay grid,
in integral form -- summing over blocks rather than differentiating a noisy
signal -- so the comparison isolates the model structure and nothing else.

Run against all three data sources, because they disagree in practice:
the synthetic GrillSim, MAKGrillSim (fitted to a real cook), and the logged
cook itself.
"""

import argparse
import csv
import importlib
import math
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import controller_matrix as CM  # noqa: E402

#: Same grid the shipped identifier searches, so a theta here is comparable.
DELAYS = np.arange(0.0, 125.0, 5.0)

#: Seconds per regression block. Long enough that a block's temperature change
#: is well above probe noise, short enough to leave many blocks in a cook.
BLOCK_S = 120

REAL_COOK = "tests/unit/mpc/fixtures/mak_cook_2026-08-02.csv"

#: What each plant's dead time actually is, so a recovered theta can be scored
#: rather than merely reported. The logged cook has no ground truth beyond the
#: fit that produced MAKGrillSim.
TRUTH = {"GrillSim": 20.0, "MAKGrillSim": 100.0, "real_cook": 100.0}


def _c_to_f(c):
    return c * 9.0 / 5.0 + 32.0


def _delayed_duty(duty, theta, dt):
    """Duty shifted by `theta` seconds, held at its first value before the
    record starts (the auger was doing something before t=0; assuming zero
    would inject a step the plant never saw)."""
    shift = int(round(theta / dt))
    if shift == 0:
        return duty
    return np.concatenate([np.full(shift, duty[0]), duty[:-shift]])


def _blocks(temp, duty_d, dt, block_s):
    """Integral-form regressors over non-overlapping blocks.

    Returns (dT, U, T_int, span) where dT is the temperature change across each
    block, U the integrated delayed duty, T_int the integrated temperature and
    span the block duration. Integrating rather than differentiating keeps
    probe noise out of the regressand.
    """
    n = int(round(block_s / dt))
    count = (len(temp) - 1) // n
    if count < 4:
        return None
    dT, U, T_int = [], [], []
    for b in range(count):
        i, j = b * n, (b + 1) * n
        dT.append(temp[j] - temp[i])
        U.append(duty_d[i:j].sum() * dt)
        T_int.append(temp[i:j].sum() * dt)
    return np.array(dT), np.array(U), np.array(T_int), n * dt


def fit_ipdt(temp, duty, dt, theta, block_s=BLOCK_S):
    """dT = K_i * integral(u_delayed) + c0 * span."""
    got = _blocks(temp, _delayed_duty(duty, theta, dt), dt, block_s)
    if got is None:
        return None
    dT, U, _, span = got
    A = np.column_stack([U, np.full(len(U), span)])
    coef, *_ = np.linalg.lstsq(A, dT, rcond=None)
    resid = dT - A @ coef
    return {
        "K_i": float(coef[0]),  # F per second per unit duty
        "c0": float(coef[1]),  # F per second of ambient loss at this operating point
        "rmse": float(np.sqrt((resid**2).mean())),
        "cond": float(np.linalg.cond(A)),
    }


def fit_fopdt(temp, duty, dt, theta, block_s=BLOCK_S):
    """dT = a*integral(u_delayed) + b*integral(T) + c*span, the same structure
    the shipped identifier uses, so `tau = -1/b` and `K = -a/b`."""
    got = _blocks(temp, _delayed_duty(duty, theta, dt), dt, block_s)
    if got is None:
        return None
    dT, U, T_int, span = got
    A = np.column_stack([U, T_int, np.full(len(U), span)])
    coef, *_ = np.linalg.lstsq(A, dT, rcond=None)
    resid = dT - A @ coef
    a, b = float(coef[0]), float(coef[1])
    tau = float("inf") if b == 0 else -1.0 / b
    K = float("nan") if b == 0 else -a / b
    return {
        "K": K,
        "tau": tau,
        "b": b,
        "rmse": float(np.sqrt((resid**2).mean())),
        "cond": float(np.linalg.cond(A)),
    }


def sweep(temp, duty, dt, label, truth):
    """Fit both forms across the delay grid and report what each one picks."""
    ip = [(d, fit_ipdt(temp, duty, dt, d)) for d in DELAYS]
    fo = [(d, fit_fopdt(temp, duty, dt, d)) for d in DELAYS]
    ip = [(d, r) for d, r in ip if r is not None]
    fo = [(d, r) for d, r in fo if r is not None]
    if not ip:
        print(f"{label}: too short to fit")
        return None

    ip_best = min(ip, key=lambda x: x[1]["rmse"])
    fo_best = min(fo, key=lambda x: x[1]["rmse"])

    n_neg_tau = sum(1 for _, r in fo if r["tau"] < 0)
    n_neg_K = sum(1 for _, r in fo if r["K"] < 0)
    n_neg_Ki = sum(1 for _, r in ip if r["K_i"] < 0)

    print(f"\n=== {label}   (true theta = {truth:.0f} s) ===")
    print(
        f"  IPDT   theta={ip_best[0]:5.0f}s  K_i={ip_best[1]['K_i'] * 3600:8.1f} F/hr/duty"
        f"  c0={ip_best[1]['c0'] * 3600:7.1f} F/hr  rmse={ip_best[1]['rmse']:.3f}"
        f"  cond={ip_best[1]['cond']:.2e}  negative K_i: {n_neg_Ki}/{len(ip)}"
    )
    print(
        f"  FOPDT  theta={fo_best[0]:5.0f}s  K={fo_best[1]['K']:10.1f} F/duty"
        f"  tau={fo_best[1]['tau']:10.1f} s  rmse={fo_best[1]['rmse']:.3f}"
        f"  cond={fo_best[1]['cond']:.2e}  negative tau: {n_neg_tau}/{len(fo)}, negative K: {n_neg_K}/{len(fo)}"
    )
    print(f"  theta error:  IPDT {abs(ip_best[0] - truth):5.0f} s     FOPDT {abs(fo_best[0] - truth):5.0f} s")
    return {
        "label": label,
        "ipdt_theta": float(ip_best[0]),
        "ipdt_K_i": ip_best[1]["K_i"],
        "ipdt_neg": n_neg_Ki,
        "fopdt_theta": float(fo_best[0]),
        "fopdt_K": fo_best[1]["K"],
        "fopdt_tau": fo_best[1]["tau"],
        "fopdt_neg_tau": n_neg_tau,
        "truth": truth,
    }


def load_real_cook(path):
    """The logged MAK cook, resampled to a uniform 1 Hz grid.

    The log is irregularly sampled at roughly 5 s, and both fits assume a fixed
    step, so it is interpolated rather than assumed uniform.
    """
    ts, tc, q = [], [], []
    with open(path) as fh:
        for row in csv.DictReader(fh):
            ts.append(float(row["time_s"]))
            tc.append(float(row["temp_c"]))
            q.append(float(row["Q"]) / 100.0)
    ts = np.array(ts) - ts[0]
    grid = np.arange(0.0, ts[-1], 1.0)
    return _c_to_f(np.interp(grid, ts, tc)), np.interp(grid, ts, q), 1.0


def closed_loop(plant_name, setpoint_f, duration_s, seed=0, adaptive=True):
    """Drive pid_ac against `plant_name` and return (temp_F, duty, dt).

    Uses the derived cycle so the duty floor does not rail the plant: on
    MAKGrillSim a fixed 25 s cycle cannot hold 225 F at all, and a record where
    the auger never leaves its floor carries no information about the chamber.
    """
    clock = CM._SimClock(-25.0)
    real = time.time
    time.time = clock
    try:
        mod = importlib.import_module("controller.pid_ac")
        cycle_data = dict(CM.CYCLE_DATA)
        cycle_data["HoldCycleTime"], cycle_data["u_min"] = 25.0, 0.10
        core = mod.Controller(dict(CM.CONTROLLER_CONFIGS["pid_ac"]), "F", cycle_data)
        plant = getattr(CM, plant_name)(seed=seed)

        pulse = cycle_data["HoldCycleTime"] * cycle_data["u_min"]
        cycle, u_min, u_max = cycle_data["HoldCycleTime"], cycle_data["u_min"], cycle_data["u_max"]
        core.set_target(setpoint_f)
        CM._report(core, u_min, "seed", 0)
        period = core.get_control_period() or cycle
        ratio, fan_frac, next_solve = u_min, 1.0, 0.0
        auger_on, auger_toggle, duty_ema = False, 0.0, None
        temps, duties = [], []

        for t in range(duration_s):
            clock.t = float(t)
            temp_f = _c_to_f(plant.measured())
            if t >= next_solve:
                raw = core.update(temp_f)
                if isinstance(raw, dict):
                    requested = float(raw.get("cycle_ratio", 0.0))
                    fan = raw.get("fan") or {}
                    if fan.get("duty") is not None:
                        fan_frac = float(fan["duty"]) / 100.0
                else:
                    requested = float(raw)
                ratio = min(max(requested, u_min), u_max)
                CM._report(core, ratio, "controller", t, requested=requested)
                alpha = 1.0 - math.exp(-period / 600.0)
                duty_ema = ratio if duty_ema is None else duty_ema + alpha * (ratio - duty_ema)
                next_solve = t + period
            auger_on, auger_toggle, frac = CM._auger_toggle_tick(auger_on, auger_toggle, t, ratio, cycle)
            plant.step(auger_on=frac, fan_frac=fan_frac, lid_open=False)
            temps.append(temp_f)
            duties.append(ratio)
        return np.array(temps), np.array(duties), 1.0
    finally:
        time.time = real


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--setpoints", type=float, nargs="+", default=[225.0, 350.0, 450.0])
    ap.add_argument("--duration", type=int, default=3 * 3600)
    ap.add_argument("--cook", default=REAL_COOK)
    args = ap.parse_args(argv)

    rows = []
    temp, duty, dt = load_real_cook(args.cook)
    rows.append(sweep(temp, duty, dt, f"real cook ({len(temp)} s)", TRUTH["real_cook"]))

    for plant in ("GrillSim", "MAKGrillSim"):
        for sp in args.setpoints:
            temp, duty, dt = closed_loop(plant, sp, args.duration)
            rows.append(sweep(temp, duty, dt, f"{plant} @ {sp:.0f} F", TRUTH[plant]))

    rows = [r for r in rows if r]
    print("\n=== theta recovery summary ===")
    print(f"{'source':<24}{'truth':>7}{'IPDT':>8}{'err':>6}{'FOPDT':>8}{'err':>6}{'FOPDT neg tau':>15}")
    for r in rows:
        print(
            f"{r['label']:<24}{r['truth']:>7.0f}{r['ipdt_theta']:>8.0f}"
            f"{abs(r['ipdt_theta'] - r['truth']):>6.0f}{r['fopdt_theta']:>8.0f}"
            f"{abs(r['fopdt_theta'] - r['truth']):>6.0f}{r['fopdt_neg_tau']:>15}"
        )
    ip_err = np.mean([abs(r["ipdt_theta"] - r["truth"]) for r in rows])
    fo_err = np.mean([abs(r["fopdt_theta"] - r["truth"]) for r in rows])
    print(f"\nmean |theta error|:  IPDT {ip_err:.1f} s   FOPDT {fo_err:.1f} s")


if __name__ == "__main__":
    main()
