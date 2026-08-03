#!/usr/bin/env python3

"""
*****************************************
 PiFire MPC Braking-Distance Estimator Check
*****************************************

 controller/model_promotion.py sizes the prediction horizon from a closed-form
 braking distance: how long the chamber keeps rising after a full fuel cut.
 That form reads the charged transport chain exactly -- every stage is
 theta/n_delay, so the Erlang survival it inverts is the model's own decay --
 and its one approximation is that it ignores the chamber's own warming during
 the coast, which is argued to err long. This measures how far, against a
 direct integration of the same grey box over parameter sets drawn across the
 promotion bounds.

 The integration stops at dT_c/dt <= 0 with the loss read at the chamber's
 current temperature, which is what "stops rising" means. Reading it at the
 frozen reference temperature instead does not define a coast at all: with the
 chamber free to warm while its loss is held down, the crossing is an
 artifact of the mismatch rather than a property of the model.

 Usage: python -m docs.superpowers.experiments.braking_distance_check
*****************************************
"""

import numpy as np

from controller.model_promotion import T_FLOOR_C, T_HAZARD_C, braking_distance
from controller.mpc_model import _rad_loss

SPAN = 200000


def numeric_coast(p, t_ref_c, *, q_full=100.0, dt=0.5):
    """Seconds to the chamber's peak, by integrating the cut directly."""
    n = max(int(p["n_delay"]), 0)
    theta = float(p["theta"])
    lag_tau = (theta / n) if (n > 0 and theta > 0.0) else 0.0
    lags = np.full(n, q_full, dtype=float)
    C_c = float(p["C_c"])
    h_amb = float(p["h_amb"])
    K_Q, sigma, T_amb = float(p["K_Q"]), float(p["sigma"]), float(p["T_amb"])
    T_c = float(t_ref_c)
    heat_in = lags[-1] if n > 0 else 0.0
    for i in range(SPAN):
        loss = h_amb * (T_c - T_amb) + _rad_loss(T_c, T_amb, sigma)
        if K_Q * heat_in <= loss:
            return i * dt
        if lag_tau > 0.0:
            prev = 0.0
            for j in range(n):
                lags[j] += dt * (prev - lags[j]) / lag_tau
                prev = lags[j]
            heat_in = lags[-1]
        else:
            heat_in = 0.0
        dT_c = (K_Q * heat_in - h_amb * (T_c - T_amb) - _rad_loss(T_c, T_amb, sigma)) / C_c
        T_c += dt * dT_c
    return float("inf")


def sample(rng):
    return dict(
        C_c=float(np.exp(rng.uniform(np.log(50.0), np.log(50000.0)))),
        h_amb=float(np.exp(rng.uniform(np.log(0.05), np.log(20.0)))),
        K_Q=float(np.exp(rng.uniform(np.log(0.2), np.log(200.0)))),
        theta=float(rng.uniform(0.0, 400.0)),
        n_delay=int(rng.integers(0, 13)),
        sigma=float(rng.uniform(0.0, 1e-8)),
        T_amb=float(rng.uniform(-10.0, 40.0)),
    )


#: Integrated coasts below this are at the resolution of the integrator's own
#: step, so their ratio to the estimate measures rounding rather than method.
MIN_COAST_S = 5.0


def main():
    rng = np.random.default_rng(7)
    rows = []
    for _ in range(400):
        p = sample(rng)
        for t_ref in (T_FLOOR_C, 232.2, T_HAZARD_C):
            if t_ref <= p["T_amb"]:
                continue
            est = braking_distance(p, t_ref)
            num = numeric_coast(p, t_ref)
            if not (np.isfinite(num) and np.isfinite(est)) or num < MIN_COAST_S:
                continue
            rows.append(est / num)
    r = np.array(rows)
    print(f"{len(r)} cases, estimate / direct integration:")
    print(
        f"  min={r.min():.3f} p01={np.percentile(r, 1):.3f} p05={np.percentile(r, 5):.3f} "
        f"median={np.median(r):.2f} p95={np.percentile(r, 95):.1f} max={r.max():.0f}"
    )
    print(f"  below the integration: {(r < 1.0).sum()}/{len(r)}; by more than 10%: {(r < 0.9).sum()}")


if __name__ == "__main__":
    main()
