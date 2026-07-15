#!/usr/bin/env python3
"""
Setpoint-step overshoot vs rise-time on the brisket scenario (225F hold, step to
275F). The current controller climbs fast but overshoots ~17F and sits hot for
tens of minutes. This harness isolates anti-overshoot levers cleanly.

Two subtleties learned the hard way:
  * the plant is in Celsius; a controller in 'F' units must be fed Fahrenheit.
  * Controller.set_target() resets _last_Q (the estimator's applied-input feed),
    so a setpoint RAMP must move the internal reference, not call set_target
    repeatedly. We ramp by setting c._set_point_c directly (what an internal
    reference-ramp feature would do).
"""

import warnings, sys

warnings.filterwarnings("ignore")
sys.path.insert(0, ".")
import numpy as np
from controller.mpc import Controller, _DEFAULTS
from controller.grill_sim import GrillSim

CYCLE = {"u_min": 0.1, "u_max": 0.9, "HoldCycleTime": 25}
C2F = lambda c: c * 9 / 5 + 32
F2C = lambda f: (f - 32) * 5 / 9
SP0, SP1 = 225.0, 275.0


def _set_ref(c, sp_f):
    c._set_point_c = F2C(sp_f)  # move reference WITHOUT resetting _last_Q


def run(ramp_min=0.0, seed=0, settle_min=70, hold_min=60, cfg_over=None):
    cfg = dict(_DEFAULTS)
    if cfg_over:
        cfg.update(cfg_over)
    c = Controller(cfg, "F", CYCLE)
    c.set_target(SP0)
    p = GrillSim(seed=seed)
    T, FUEL = [], []
    step_w = int(settle_min * 60 / 25)
    total_w = int((settle_min + hold_min) * 60 / 25)
    fuel_steady = 0.0
    for w in range(total_w):
        tmin = w * 25 / 60.0
        if w < step_w:
            _set_ref(c, SP0)
            if w == step_w - 1:
                fuel_steady = p.fuel
        else:
            e = tmin - settle_min
            _set_ref(c, SP1 if ramp_min <= 0 else min(SP1, SP0 + (SP1 - SP0) * e / ramp_min))
        out = c.update(C2F(p.measured()))
        r = float(np.clip(out["cycle_ratio"], 0.1, 0.9))
        fan = out["fan"]["duty"] or 100.0
        on = int(round(r * 25))
        for s in range(25):
            p.step(auger_on=(s < on), fan_frac=fan / 100.0)
            T.append(C2F(p.true_Tc))
            FUEL.append(p.fuel)
    return np.array(T), np.array(FUEL), step_w * 25, fuel_steady


def metrics(T, step_s):
    seg = T[step_s:]
    over = seg.max() - SP1
    reach = np.argmax(seg >= SP1 - 2.0)
    rise = reach / 60.0 if seg[reach] >= SP1 - 2.0 else float("nan")
    overtemp = np.mean(seg > SP1 + 8.0) * len(seg) / 60.0  # minutes above +8F
    return over, rise, overtemp, seg.max()


def report(label, T, FUEL, step_s, fuel_steady):
    over, rise, overtemp, pk = metrics(T, step_s)
    fuel_slug = FUEL[step_s:].max()
    print(
        f"  {label:>16}: overshoot {over:5.1f}F  rise {rise:4.1f}m  "
        f"hot(>283F) {overtemp:4.1f}m  peak {pk:5.0f}F  fuel slug {fuel_slug:4.1f} (steady {fuel_steady:.1f})"
    )


if __name__ == "__main__":
    print("Brisket 225->275F. Current baseline (step) and internal setpoint ramp:")
    T, F, ss, fs = run(ramp_min=0)
    report("step (current)", T, F, ss, fs)
    for r in (5, 10, 15, 20, 30):
        T, F, ss, fs = run(ramp_min=r)
        report(f"ramp {r}min", T, F, ss, fs)
