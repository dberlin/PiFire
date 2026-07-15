#!/usr/bin/env python3
"""
High-temperature cooking on the realistic plant. Checks the production controller
(nonlinear + EKF) up to ~600F: can it REACH each high setpoint (or does firing
saturate near the ~646F plant ceiling?), how tight is the steady band, and how a
cold climb to a high target behaves (rise + overshoot).

Reports steady band (RMS/max/bias), the mean firing demand (cycle_ratio; u_max=0.9
is full fire -> saturation), and the fraction of time pinned at u_max.
"""

import warnings, sys

warnings.filterwarnings("ignore")
sys.path.insert(0, ".")
import numpy as np
from controller.mpc import Controller, _DEFAULTS
from controller.grill_sim import GrillSim

CYCLE = {"u_min": 0.1, "u_max": 0.9, "HoldCycleTime": 25}
TS = 25.0
C2F = lambda c: c * 9 / 5 + 32
F2C = lambda f: (f - 32) * 5 / 9


def steady(set_c, seed=0, minutes=70, warm=True):
    c = Controller(dict(_DEFAULTS), "C", dict(CYCLE))
    c.set_target(set_c)
    p = GrillSim(seed=seed)
    if warm:  # skip the long climb; start near target
        p.T_c = p.T_meas = set_c
        p.T_f = set_c + 60.0
    T, CR = [], []
    for w in range(int(minutes * 60 / TS)):
        out = c.update(p.measured())
        r = float(np.clip(out["cycle_ratio"], 0.1, 0.9))
        CR.append(r)
        on = int(round(r * TS))
        for s in range(int(TS)):
            p.step(auger_on=(s < on), fan_frac=1.0)
            T.append(p.true_Tc)
    T = np.array(T)
    CR = np.array(CR)
    tail = T[len(T) // 3 :]
    e = tail - set_c
    crt = CR[len(CR) // 3 :]
    return (np.sqrt(np.mean(e**2)), np.max(np.abs(e)), np.mean(e), np.mean(crt), np.mean(crt >= 0.895))


def climb(set_c, seed=0, minutes=120):
    c = Controller(dict(_DEFAULTS), "C", dict(CYCLE))
    c.set_target(set_c)
    p = GrillSim(seed=seed)  # cold start (ambient)
    T = []
    for w in range(int(minutes * 60 / TS)):
        out = c.update(p.measured())
        r = float(np.clip(out["cycle_ratio"], 0.1, 0.9))
        on = int(round(r * TS))
        for s in range(int(TS)):
            p.step(auger_on=(s < on), fan_frac=1.0)
            T.append(p.true_Tc)
    T = np.array(T)
    reach = np.argmax(T >= set_c - 2.0)
    rise = reach / 60.0 if T[reach] >= set_c - 2.0 else float("nan")
    over_f = (T.max() - set_c) * 9 / 5  # overshoot is a DELTA -> F-delta is *9/5
    settled = T[-30 * 60 :]
    return rise, over_f, np.sqrt(np.mean((settled - set_c) ** 2))


if __name__ == "__main__":
    print("STEADY hold at high temp (3-seed; warm start), realistic plant:")
    print(f"{'setpoint':>14} {'RMS':>6} {'max|e|':>7} {'bias':>6} {'mean_fire':>10} {'%@maxfire':>10}")
    for f in (425, 475, 525, 575, 600):
        sc = F2C(f)
        rs = [steady(sc, s) for s in (0, 1, 2)]
        rms = np.mean([r[0] for r in rs])
        mx = np.max([r[1] for r in rs])
        bias = np.mean([r[2] for r in rs])
        fire = np.mean([r[3] for r in rs])
        sat = np.mean([r[4] for r in rs])
        print(f"{f:>4}F ({sc:5.1f}C) {rms:6.2f} {mx:7.2f} {bias:+6.2f} {fire:10.2f} {sat * 100:9.0f}%", flush=True)

    print("\nCOLD climb to a high target (rise to within 2C, overshoot, settled RMS):")
    for f in (450, 550):
        sc = F2C(f)
        ri, ov, rms = climb(sc)
        print(
            f"  cold -> {f}F ({sc:.0f}C): rise {ri:5.1f}min  overshoot {ov:5.1f}F  settled RMS {rms:.2f}C", flush=True
        )
