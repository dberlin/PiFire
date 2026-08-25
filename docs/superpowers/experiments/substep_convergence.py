#!/usr/bin/env python3

"""
*****************************************
 PiFire MPC Fit-Simulator Sub-step Convergence
*****************************************

 Derives controller/mpc_model.py's `max_dt` from a convergence measurement
 instead of asserting one, and prices the integrator change that made a
 derivable answer possible at all.

 WHAT IS BEING MEASURED. `simulate_grey_box` is the plant the offline
 calibration fits through, so any error it makes is charged to the grill: the
 solve moves real parameters to absorb it. The single-lump structure was
 adopted at a measured fidelity cost of 0.16 C on the MAK plant, so numerical
 error of the same size or larger means a fit reports the integrator rather
 than the grill. Everything below is RMS degrees C over the real 247-row MAK
 cook in tests/unit/mpc/fixtures, against that scheme's own converged
 reference (a sub-step small enough that halving it moves nothing).

 THE TWO SCHEMES.

   euler  The chain integrated with explicit Euler, as it was:
              lags[j] += dt * (prev - lags[j]) / lag_tau
          Stable only for dt < 2 * theta / n_delay. `theta` is fitted and
          bounded below only by 1e-9, so that limit is not one the caller can
          promise to respect.

   exact  The chain advanced in closed form, as it is now: it is linear and
          its input is constant across a sample interval, so
              lags(k*dt) = u + exp(A*k*dt) @ (lags(0) - u)
          is available for every sub-step at once. The chain contributes NO
          discretization error, and the scheme is unconditionally stable.

 Both integrate the chamber (nonlinear through the radiative term) with the
 same explicit Euler sub-step, which is the error that is left over and the
 quantity `max_dt` is derived from.

 Run:
   uv run python -m docs.superpowers.experiments.substep_convergence

 The committed output is _substep_convergence.txt beside this file.
*****************************************
"""

import csv
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from controller.mpc_model import _rad_loss, simulate_grey_box  # noqa: E402

FIXTURE = "tests/unit/mpc/fixtures/mak_cook_2026-08-02.csv"

#: The shipped utility's own fit of that cook at n_delay=8. Held fixed here so
#: the comparison is between integrators and not between two different fits.
FIT = {"C_c": 3589.447, "h_amb": 0.5, "T_amb": 20.0, "K_Q": 9.9159, "sigma": 1.4e-9}

#: theta from a third of the shortest fitted deadtime seen on any record to
#: well past the longest, crossed with every n_delay the settings surface
#: offers. theta=3 with n_delay=8 is below the old Euler stability cliff and is
#: in the grid deliberately.
CELLS = ((118.08, 8), (50.0, 8), (10.0, 8), (8.0, 8), (5.0, 20), (30.0, 12), (3.0, 8), (200.0, 4))

#: The modelling cost the single-lump structure was adopted at. Numerical error
#: is priced as a fraction of this, because that is the size of the thing the
#: whole change was justified against.
FIDELITY_COST_C = 0.16


def euler_chain(t, Q, *, C_c, h_amb, T_amb, T0, K_Q, sigma, theta, n_delay, max_dt):
    """The integrator as it stood before this measurement. Kept verbatim."""
    t = np.asarray(t, dtype=float)
    Q = np.asarray(Q, dtype=float)
    n = max(int(n_delay), 0)
    lag_tau = (float(theta) / n) if (n > 0 and theta > 0.0) else 0.0
    lags = np.zeros(n)
    T_c = float(T0)
    out = np.empty_like(t)
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
        for _ in range(steps):
            if lag_tau > 0.0:
                prev = u
                for j in range(n):
                    lags[j] += dt * (prev - lags[j]) / lag_tau
                    prev = lags[j]
                heat_in = lags[-1]
            else:
                heat_in = u
            dT_c = (K_Q * heat_in - h_amb * (T_c - T_amb) - _rad_loss(T_c, T_amb, sigma)) / C_c
            T_c += dt * dT_c
    return out


def load_cook():
    with open(FIXTURE) as fh:
        rows = list(csv.DictReader(fh))
    t = np.array([float(r["time_s"]) for r in rows])
    return t - t[0], np.array([float(r["Q"]) for r in rows]), np.array([float(r["temp_c"]) for r in rows])


def rms(a, b):
    return float(np.sqrt(np.mean((a - b) ** 2)))


def substeps(t, max_dt):
    return int(sum(max(1, int(np.ceil((t[i + 1] - t[i]) / max_dt))) for i in range(len(t) - 1)))


def main():
    t, Q, temp = load_cook()

    def exact(theta, n_delay, max_dt):
        return simulate_grey_box(t, Q, T0=float(temp[0]), theta=theta, n_delay=n_delay, max_dt=max_dt, **FIT)

    def euler(theta, n_delay, max_dt):
        return euler_chain(t, Q, T0=float(temp[0]), theta=theta, n_delay=n_delay, max_dt=max_dt, **FIT)

    REF = 0.002
    hdr = f"  {'max_dt':>8}" + "".join(f"{f'th{th:g}/n{nd}':>12}" for th, nd in CELLS)

    print("=== 1. the reference is converged: halving the sub-step moves nothing ===")
    for th, nd in CELLS:
        print(
            f"  theta={th:7.2f} n_delay={nd:2d}   RMS(max_dt={REF}, max_dt={REF / 2}) = {rms(exact(th, nd, REF), exact(th, nd, REF / 2)):.2e} C"
        )

    refs = {c: exact(c[0], c[1], REF) for c in CELLS}

    print()
    print("=== 2. euler chain (the scheme this replaces): RMS error vs reference, C ===")
    print(hdr)
    for md in (1.0, 0.5, 0.25, 0.125, 0.0625):
        cells = []
        for c in CELLS:
            # The overflow is the measurement, not an accident, so it is
            # allowed to happen quietly rather than printed over the table.
            with np.errstate(over="ignore", invalid="ignore"):
                y = euler(c[0], c[1], md)
            cells.append(rms(y, refs[c]) if np.all(np.isfinite(y)) else float("inf"))
        print(f"  {md:>8.4f}" + "".join(f"{v:12.5f}" for v in cells))
    print("  (inf = the residual overflowed: dt is past 2*theta/n_delay)")

    print()
    print("=== 3. exact chain (the scheme now shipped): RMS error vs reference, C ===")
    print(hdr)
    slopes = []
    for md in (1.0, 0.5, 0.25, 0.125, 0.0625):
        cells = [rms(exact(c[0], c[1], md), refs[c]) for c in CELLS]
        slopes.append((md, cells))
        print(f"  {md:>8.4f}" + "".join(f"{v:12.5f}" for v in cells))

    print()
    print("=== 4. the criterion: error per second of sub-step, and what it costs ===")
    print(f"  {'max_dt':>8}{'worst C':>10}{'per second':>12}{'spread':>9}{'/0.16 C':>9}{'substeps':>10}")
    for md, cells in slopes:
        worst = max(cells)
        spread = (max(cells) - min(cells)) / max(cells)
        print(
            f"  {md:>8.4f}{worst:10.5f}{worst / md:12.4f}{spread:9.1%}{worst / FIDELITY_COST_C:9.1%}{substeps(t, md):10d}"
        )
    print()
    print("  The error is first order in the sub-step and, to within the spread")
    print("  column, INDEPENDENT of theta and n_delay -- because the chain no")
    print("  longer contributes any. So the sub-step is derived from the")
    print("  chamber alone, and 0.125 s is where the numerical error falls below")
    print("  a ninth of the 0.16 C the single-lump structure was adopted at.")

    print()
    print("=== 5. cost: wall clock for one simulation of the 247-row cook ===")
    for label, fn, md in (
        ("euler chain, max_dt=1.0 (was)", euler, 1.0),
        ("exact chain, max_dt=1.0", exact, 1.0),
        ("exact chain, max_dt=0.25", exact, 0.25),
        ("exact chain, max_dt=0.125 (now)", exact, 0.125),
    ):
        fn(118.08, 8, md)
        t0 = time.perf_counter()
        for _ in range(10):
            fn(118.08, 8, md)
        print(f"  {label:<34}{(time.perf_counter() - t0) / 10 * 1000:8.2f} ms")

    print()
    print("=== 5b. cost: wall clock for a real-cook fit, which is the inner loop ===")
    import controller.mpc_model as mm  # noqa: PLC0415
    import controller.update_mpc as um  # noqa: PLC0415
    from controller.update_mpc import fit_params  # noqa: PLC0415

    init = {"C_c": 320.0, "h_amb": 0.5, "K_Q": 3.5, "theta": 50.0}
    real = mm.simulate_grey_box
    for label, fn in (
        ("euler chain, max_dt=1.0 (was)", lambda *a, **k: euler_chain(*a, **dict(k, max_dt=1.0))),
        ("exact chain, max_dt=0.125 (now)", real),
    ):
        try:
            um.simulate_grey_box = fn
            t0 = time.perf_counter()
            got = fit_params(t, temp, Q, T_amb=20.0, init=init, sigma=1.4e-9, n_delay=8)
            elapsed = time.perf_counter() - t0
        finally:
            um.simulate_grey_box = real
        print(f"  {label:<34}{elapsed * 1000:9.0f} ms  ({got['nfev']} evaluations, theta {got['theta']:.2f})")

    print()
    print("=== 6. what the euler chain's under-delay did to a fitted theta ===")
    print("  Synthetic records generated at a converged sub-step from a known theta,")
    print("  then fitted back with the shipped free set through each integrator.")
    print(f"  {'true theta':>11}{'n_delay':>9}{'euler fit':>11}{'exact fit':>11}{'euler bias':>12}")
    for true_theta in (2.0, 6.0, 30.0, 120.0):
        for nd in (8, 20):
            tt = np.arange(0.0, 1800.0, 5.0)
            QQ = np.where(tt < 600.0, 100.0, 40.0)
            truth = {"C_c": 320.0, "h_amb": 0.5, "K_Q": 3.5, "sigma": 1.4e-9, "theta": true_theta}
            y = simulate_grey_box(tt, QQ, T_amb=20.0, T0=25.0, n_delay=nd, max_dt=0.002, **truth)
            got_exact = fit_params(tt, y, QQ, T_amb=20.0, init=init, sigma=1.4e-9, n_delay=nd)
            try:
                um.simulate_grey_box = lambda *a, **k: euler_chain(*a, **dict(k, max_dt=1.0))
                with np.errstate(over="ignore", invalid="ignore"):
                    got_euler = fit_params(tt, y, QQ, T_amb=20.0, init=init, sigma=1.4e-9, n_delay=nd)
            finally:
                um.simulate_grey_box = real
            print(
                f"  {true_theta:11.1f}{nd:9d}{got_euler['theta']:11.2f}{got_exact['theta']:11.2f}"
                f"{got_euler['theta'] - true_theta:12.2f}"
            )
    print()
    print("  The euler bias column is n_delay * max_dt to two decimals -- the chain")
    print("  under-delayed by half a sub-step per stage and the solve bought that")
    print("  back with a longer theta. The estimator and the NLP discretize the")
    print("  same continuous model exactly, so they never saw that theta as the")
    print("  fit's own artifact; they planned against it as the grill's deadtime.")


if __name__ == "__main__":
    main()
