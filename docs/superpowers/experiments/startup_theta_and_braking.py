#!/usr/bin/env python3
"""Dead time from the startup ramp, and what it buys on the approach.

`ipdt_vs_fopdt.py` shows dead time is not recoverable from passive closed-loop
cook data in either model form -- mean theta error 20.7 s for both, with the
IPDT residual often flat enough to pin theta at the edge of the grid. The
regressors carry gain information but barely discriminate delay, because a
settled hold never steps the auger hard enough to show one.

Every cook does contain exactly one genuine step, though, and it costs nothing
to use: startup. The auger goes from off to running against a cold chamber. For
an integrator with dead time the ramp is flat for theta seconds and then linear,
so extrapolating the rising segment back to the starting temperature puts its
x-intercept at theta -- the classical tangent construction, and it yields the
integrator gain from the same fit.

Part 2 spends that estimate. The overshoot on the approach is heat already
committed: fuel inside the dead time that will land no matter what the
controller does next. Knowing theta and K_i makes the committed rise a
computable quantity, so the auger can be cut early by exactly that much rather
than by a tuned guess.
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

TRUTH = {"GrillSim": 20.0, "MAKGrillSim": 100.0}

#: Rise above the starting temperature that counts as "the ramp has begun".
#: Above probe noise, below the curvature that ambient loss adds later.
RISE_LO_F = 15.0
#: Upper end of the segment the tangent is fitted to.
RISE_HI_F = 90.0


def _c_to_f(c):
    return c * 9.0 / 5.0 + 32.0


def theta_from_startup(temp, duty, dt, *, lo=RISE_LO_F, hi=RISE_HI_F):
    """Dead time and integrator gain from the opening ramp.

    Fits a line to the segment between `lo` and `hi` degrees above the starting
    temperature and returns where that line crosses the starting temperature.
    Returns None when the record never covers the segment -- a log that begins
    mid-ramp has no baseline to extrapolate back to.
    """
    T0 = float(np.median(temp[: max(1, int(30 / dt))]))
    above = temp - T0
    try:
        i = int(np.argmax(above >= lo))
        j = int(np.argmax(above >= hi))
    except ValueError:
        return None
    if j <= i or above[j] < hi:
        return None
    t = np.arange(len(temp)) * dt
    slope, intercept = np.polyfit(t[i:j], temp[i:j], 1)
    if slope <= 0:
        return None
    theta = (T0 - intercept) / slope
    u = float(np.mean(duty[i:j])) or 1.0
    return {"theta": float(theta), "K_i": float(slope / u), "slope_f_per_hr": float(slope * 3600), "T0": T0}


def load_real_cook(path):
    ts, tc, q = [], [], []
    with open(path) as fh:
        for row in csv.DictReader(fh):
            ts.append(float(row["time_s"]))
            tc.append(float(row["temp_c"]))
            q.append(float(row["Q"]) / 100.0)
    ts = np.array(ts) - ts[0]
    grid = np.arange(0.0, ts[-1], 1.0)
    return _c_to_f(np.interp(grid, ts, tc)), np.interp(grid, ts, q), 1.0


class Braker:
    """Cuts the auger when the heat already in flight will reach the setpoint.

    The rise still to come has two parts that pull against each other: the fuel
    fed inside the last `theta` seconds, whose heat has not arrived yet, and the
    chamber's loss to ambient over that same interval. Counting only the first
    is what makes a braker cut too early at high setpoints, where the loss is
    large -- the projection then reads as an overshoot that never happens, the
    auger is held down, and the chamber stalls below setpoint.

    The loss needs no separate identification. The model says the observed slope
    is `K_i * u(t - theta) + c0`, so `c0` is whatever the measured slope is not
    explained by the delayed duty, and it can be read continuously off the
    temperature trace. It is negative, and over `theta` seconds it is exactly
    the credit the projection was missing.

    Releases whenever the projection falls back under the setpoint, so a cut
    that turns out to be premature corrects itself instead of latching.
    """

    def __init__(self, theta, K_i, *, margin_f=0.0, slope_window_s=60.0, c0_tau_s=120.0):
        self.theta = max(theta, 0.0)
        self.K_i = max(K_i, 0.0)
        self.margin = margin_f
        self.slope_window = slope_window_s
        self.c0_tau = c0_tau_s
        self.history = []
        self.temps = []
        self.c0 = None
        self.done = False

    def observe(self, duty, temp_f, now):
        self.history.append((now, duty))
        self.temps.append((now, temp_f))
        while len(self.history) > 1 and self.history[0][0] < now - self.theta:
            self.history.pop(0)
        while len(self.temps) > 1 and self.temps[0][0] < now - self.slope_window:
            self.temps.pop(0)
        self._update_c0(now)

    def _slope(self):
        if len(self.temps) < 2:
            return None
        (t0, T0), (t1, T1) = self.temps[0], self.temps[-1]
        return None if t1 <= t0 else (T1 - T0) / (t1 - t0)

    def _update_c0(self, now):
        """Whatever the measured slope is not explained by the delayed duty."""
        slope = self._slope()
        if slope is None or not self.history:
            return
        # The duty whose heat is arriving now was commanded theta ago -- the
        # oldest sample still inside the window.
        u_arriving = self.history[0][1]
        observed = slope - self.K_i * u_arriving
        if self.c0 is None:
            self.c0 = observed
        else:
            dt = now - self.temps[-2][0] if len(self.temps) > 1 else 1.0
            alpha = 1.0 - math.exp(-max(dt, 0.0) / self.c0_tau)
            self.c0 += alpha * (observed - self.c0)

    def projected_rise(self):
        if len(self.history) < 2:
            return 0.0
        fed = 0.0
        for (t0, u0), (t1, _) in zip(self.history, self.history[1:]):
            fed += u0 * (t1 - t0)
        loss = (self.c0 or 0.0) * self.theta
        return self.K_i * fed + loss

    def apply(self, requested, temp_f, setpoint_f, u_min):
        if self.done:
            return requested
        if temp_f >= setpoint_f:
            self.done = True
            return requested
        if temp_f + self.projected_rise() >= setpoint_f - self.margin:
            return u_min
        return requested


def closed_loop(plant_name, setpoint_f, duration_s, seed=0, braker_factory=None):
    """Drive pid_ac on the derived cycle, optionally through a braker."""
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
        braker = braker_factory() if braker_factory else None
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
                if braker is not None:
                    requested = braker.apply(requested, temp_f, setpoint_f, u_min)
                ratio = min(max(requested, u_min), u_max)
                CM._report(core, ratio, "controller", t, requested=requested)
                alpha = 1.0 - math.exp(-period / 600.0)
                duty_ema = ratio if duty_ema is None else duty_ema + alpha * (ratio - duty_ema)
                next_solve = t + period
            if braker is not None:
                braker.observe(ratio, temp_f, float(t))
            auger_on, auger_toggle, frac = CM._auger_toggle_tick(auger_on, auger_toggle, t, ratio, cycle)
            plant.step(auger_on=frac, fan_frac=fan_frac, lid_open=False)
            temps.append(temp_f)
            duties.append(ratio)

        temps = np.array(temps)
        err = temps - setpoint_f
        settle = None
        for i, e in enumerate(err):
            settle = i if (abs(e) <= 5.0 and settle is None) else (settle if abs(e) <= 5.0 else None)
        return {
            "temps": temps,
            "duties": np.array(duties),
            "iae": float(np.abs(err).sum()),
            "in5": float((np.abs(err) <= 5.0).mean() * 100),
            "overshoot": float(err.max()),
            "settle": settle,
        }
    finally:
        time.time = real


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--setpoints", type=float, nargs="+", default=[225.0, 350.0, 450.0])
    ap.add_argument("--duration", type=int, default=3 * 3600)
    ap.add_argument("--cook", default="tests/unit/mpc/fixtures/mak_cook_2026-08-02.csv")
    args = ap.parse_args(argv)

    print("=== Part 1: theta from the startup ramp ===")
    print(f"{'source':<24}{'truth':>7}{'theta':>8}{'err':>6}{'K_i (F/hr/duty)':>18}")
    estimates = {}
    temp, duty, dt = load_real_cook(args.cook)
    got = theta_from_startup(temp, duty, dt)
    if got is None:
        print(f"{'real cook':<24}{'100':>7}{'--':>8}{'--':>6}{'log begins mid-ramp':>18}")
    else:
        print(
            f"{'real cook':<24}{100:>7.0f}{got['theta']:>8.1f}{abs(got['theta'] - 100):>6.1f}{got['K_i'] * 3600:>18.1f}"
        )

    for plant in ("GrillSim", "MAKGrillSim"):
        for sp in args.setpoints:
            r = closed_loop(plant, sp, args.duration)
            got = theta_from_startup(r["temps"], r["duties"], 1.0)
            label = f"{plant} @ {sp:.0f} F"
            if got is None:
                print(f"{label:<24}{TRUTH[plant]:>7.0f}{'--':>8}{'--':>6}{'no usable ramp':>18}")
                continue
            estimates[(plant, sp)] = got
            print(
                f"{label:<24}{TRUTH[plant]:>7.0f}{got['theta']:>8.1f}"
                f"{abs(got['theta'] - TRUTH[plant]):>6.1f}{got['K_i'] * 3600:>18.1f}"
            )

    if estimates:
        errs = [abs(g["theta"] - TRUTH[p]) for (p, _), g in estimates.items()]
        print(f"\nmean |theta error| from startup ramp: {np.mean(errs):.1f} s")

    print("\n=== Part 2: approach braking on the startup estimate ===")
    print(f"{'source':<24}{'arm':<10}{'iae':>10}{'in5%':>8}{'overshoot':>11}{'settle':>9}")
    for plant in ("GrillSim", "MAKGrillSim"):
        for sp in args.setpoints:
            got = estimates.get((plant, sp))
            if got is None:
                continue
            base = closed_loop(plant, sp, args.duration)
            braked = closed_loop(
                plant,
                sp,
                args.duration,
                braker_factory=lambda g=got: Braker(g["theta"], g["K_i"]),
            )
            label = f"{plant} @ {sp:.0f} F"
            for name, r in (("baseline", base), ("braked", braked)):
                print(
                    f"{label if name == 'baseline' else '':<24}{name:<10}{r['iae']:>10.0f}"
                    f"{r['in5']:>8.1f}{r['overshoot']:>11.1f}{str(r['settle']):>9}"
                )


if __name__ == "__main__":
    main()
