#!/usr/bin/env python3
"""
Fuel-buffer POC. Hypothesis: the brisket-step overshoot is caused by rate-limited
fuel ACCUMULATION the controller's grey-box model doesn't represent (its deadtime
lag chain only delays heat, it doesn't model a fuel buffer that piles up during
hard firing and keeps burning after firing is cut).

STAGE 1 (this file): the decisive diagnostic. Run the real closed loop through the
225->275F step; at the step, take the controller's own state estimate and run its
model OPEN-LOOP forward with the firing the controller actually applied. If the
model predicts much LESS overshoot than the plant actually shows, the model is
blind to the residual heat -> a better combustion model can help. If the model
already predicts the overshoot, the problem is control/estimation, not the model.
"""

import warnings, sys

warnings.filterwarnings("ignore")
sys.path.insert(0, ".")
import numpy as np
from controller.mpc import Controller, _DEFAULTS
from controller.mpc_model import _rad_loss
from controller.grill_sim import GrillSim

CYCLE = {"u_min": 0.1, "u_max": 0.9, "HoldCycleTime": 25}
C2F = lambda c: c * 9 / 5 + 32
F2C = lambda f: (f - 32) * 5 / 9
CFG = _DEFAULTS
ND = int(CFG["n_delay"])


def recon_Q(cr):  # invert the (linear) allocator: cycle_ratio -> Q
    frac = (cr - CYCLE["u_min"]) / (CYCLE["u_max"] - CYCLE["u_min"])
    return CFG["Q_min"] + frac * (CFG["Q_max"] - CFG["Q_min"])


def model_deriv(x, Q):  # the controller's grey-box rhs, x=[q0..q3,T_f,T_c,d]
    tau_d = CFG["theta"] / ND
    dx = np.zeros_like(x)
    dx[0] = (Q - x[0]) / tau_d
    for i in range(1, ND):
        dx[i] = (x[i - 1] - x[i]) / tau_d
    heat_in = x[ND - 1]
    Tf, Tc, d = x[ND], x[ND + 1], x[ND + 2]
    dx[ND] = (CFG["K_Q"] * heat_in - CFG["h_fc"] * (Tf - Tc)) / CFG["C_f"]
    dx[ND + 1] = (
        CFG["h_fc"] * (Tf - Tc) - CFG["h_amb"] * (Tc - CFG["T_amb"]) - _rad_loss(Tc, CFG["T_amb"], CFG["sigma"]) + d
    ) / CFG["C_c"]
    return dx


def main():
    c = Controller(dict(CFG), "F", CYCLE)
    c.set_target(225.0)
    p = GrillSim(seed=0)
    settle_w = int(70 * 60 / 25)
    total_w = int(115 * 60 / 25)
    Qs, ests, est_secs = [], [], []
    PT = []  # plant true_Tc EVERY SECOND
    step_sec = settle_w * 25
    for w in range(total_w):
        if w == settle_w:
            c.set_target(275.0)  # production: set_target once on change
        out = c.update(C2F(p.measured()))
        cr = float(np.clip(out["cycle_ratio"], 0.1, 0.9))
        Qs.append(recon_Q(cr))
        ests.append(np.asarray(c.estimator.x).flatten().copy())
        est_secs.append(len(PT))
        fan = out["fan"]["duty"] or 100.0
        on = int(round(cr * 25))
        for s in range(25):
            p.step(auger_on=(s < on), fan_frac=fan / 100.0)
            PT.append(p.true_Tc)
    PT = np.array(PT)

    post = PT[step_sec:]
    pk = post.max()
    pk_t = (np.argmax(post)) / 60.0
    print("225->275F (production set_target, per-second sampling):")
    print(f"  TRUE plant overshoot  : {C2F(pk) - 275:+.1f} F (peak {C2F(pk):.1f} F) at {pk_t:.1f} min after step")

    # trajectory shape: per-minute plant temp + applied firing + d estimate
    print(f"\n  {'t(min)':>7} {'plantF':>7} {'Q':>6} {'d_est':>7}  (275F steady firing ~24-25)")
    iD = ND + 2
    for m in range(0, 36, 2):
        sec = step_sec + m * 60
        if sec >= len(PT):
            break
        w = settle_w + int(m * 60 / 25)
        w = min(w, len(ests) - 1)
        print(f"  {m:7d} {C2F(PT[sec]):7.1f} {Qs[w]:6.1f} {ests[w][iD]:7.2f}")

    # model prediction from the controller's state at the step, driven by the
    # firing actually applied, over the MPC horizon (~10 min) and a long window
    for horizon_min in (10, 40):
        H = int(horizon_min * 60 / 25)
        x = ests[settle_w].astype(float).copy()
        mTc = []
        for k in range(H):
            Q = Qs[settle_w + k]
            for _ in range(25):
                x = x + model_deriv(x, Q)
                mTc.append(x[ND + 1])
        mTc = np.array(mTc)
        plant_win = post[: len(mTc)]
        print(
            f"  over {horizon_min:2d} min: plant peak {C2F(plant_win.max()):6.1f}F  "
            f"model-predicted peak {C2F(mTc.max()):6.1f}F  "
            f"(model {'UNDER' if mTc.max() < plant_win.max() else 'OVER'}-predicts by "
            f"{abs(C2F(mTc.max()) - C2F(plant_win.max())):.1f}F)"
        )


if __name__ == "__main__":
    main()
