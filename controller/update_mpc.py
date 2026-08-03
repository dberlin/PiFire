#!/usr/bin/env python3

"""
*****************************************
 PiFire MPC Offline Calibration Utility
*****************************************

 Fits the grey-box thermal parameters to a logged history CSV so the MPC model
 describes a specific grill. Fitting runs through controller.mpc_model's shared
 forward simulator, so the parameters produced describe the same dynamics the
 controller plans against -- radiative loss and transport deadtime included.

 CSV columns: time_s, temp_c, Q  (Q is the firing-rate demand; if you logged
 auger duty instead, map it back through the allocator first). Capture the log
 with the fan under the controller's command: a log taken with the fan pinned
 at one duty only describes the grill at that duty.

 Usage: python -m controller.update_mpc history.csv [--t-amb 20] [--json]
*****************************************
"""

import argparse
import json
import sys

import numpy as np
from scipy.optimize import least_squares

from controller.model_promotion import (
    T_FLOOR_C,
    T_HAZARD_C,
    effective_tau,
    longest_braking_distance,
    steady_state_at_full_fire,
)
from controller.mpc_model import simulate_grey_box

# Keys the controller reads back out of a fitted result.
CONFIG_KEYS = ("C_f", "C_c", "h_fc", "h_amb", "T_amb", "theta", "n_delay", "K_Q", "sigma")

# Fitted free parameters. C_f, h_fc, h_amb and sigma are held at their init
# values.
#
# WHY ONE OF THEM MUST BE HELD. The dynamics are invariant under scaling every
# capacitance, every conductance and the input gain -- (C_f, C_c, h_fc, h_amb,
# K_Q, sigma) -- by one common factor, because both state equations are
# homogeneous in them, so the trajectory of the one measured state is
# bit-identical. What a log determines is the ratios among them, which is what
# the controller plans against: the effective time constant
# C_c/(h_amb + 4*sigma*(T+273.15)**3) is one of them, and is unchanged by which
# parameter is held. See docs/superpowers/experiments/sigma_identifiability.py.
#
# WHY h_amb AND sigma ARE BOTH HELD. C_f is a handful of seconds' worth of
# firepot against a chamber of minutes, so through most of a cook the firepot
# is quasi-static: h_fc*(T_f - T_c) tracks K_Q*heat_in, C_f and h_fc leave the
# chamber equation, and what is left depends only on C_c/h_amb, K_Q/h_amb and
# sigma/h_amb. Holding C_f pins the global scaling above and does nothing about
# this one, so one of the chamber's own three must be held too -- and whichever
# of h_amb and sigma is left free, the solve escapes by inflating the rest
# until the held one is negligible. Freeing h_amb, as the shipped set did,
# converges on the real MAK cook at C_c 2.6e7 and h_amb 7.4e3, an order of
# magnitude past model_promotion.PROMOTION_BOUNDS: the radiative term deleted
# by dilution, and a model evaluate() refuses however well it describes the
# log, which is what happened on 30 of the 35 logs measured. Freeing sigma
# instead escapes the other way, to an all-radiative model with sigma 5e-3 and
# C_c 3e8. Holding both is the only configuration measured that lands inside
# the bounds on every log, and it costs only the ratio between the two loss
# terms -- see this file's report for what that ratio being fixed does and does
# not cover.
#
# WHAT THE LOG CANNOT RESOLVE AT ALL. h_fc appears only as the firepot's own
# time constant C_f/h_fc -- seconds, against a chamber and a transport delay of
# minutes -- so a chamber-temperature log at a five-second cadence carries
# almost nothing about it, and a solver given it free moves it a long way for a
# negligible residual gain. Freeing it buys 3% of RMSE and costs the answer the
# controller brakes with: across both plants in controller/grill_sim.py and the
# real cook, the braking distance the fit implied then reached 229x the plant's
# own, against 1.8x with h_fc held, and moved by up to 783x between restarts of
# the same fit. See docs/superpowers/experiments/free_set_identifiability.py,
# which scores every subset of these on the recovered dead time and braking
# distance rather than on RMSE.
_FREE = ("K_Q", "C_c", "theta")

_SIM_KEYS = ("C_f", "C_c", "h_fc", "h_amb", "K_Q", "sigma", "theta", "n_delay")

# Parameters a caller supplies a starting value for. `_FREE` selects which of
# these the solve moves; the rest are held at the value they came in with.
_FIT_KEYS = ("C_f", "C_c", "h_fc", "h_amb", "K_Q", "sigma", "theta")

# Strictly positive: theta divides the lag time constant, and every other free
# parameter is a capacitance or a conductance.
_LOWER_BOUND = 1e-9

# Evaluations the solve is allowed. Not enough to converge on every log -- see
# `fit_params`, which reports whether it did rather than presenting an
# exhausted solve as a finished one.
_MAX_NFEV = 2000

# Said in both output modes, so neither can be the one that stays quiet.
_NOT_CONVERGED = (
    "WARNING: the solver ran out of evaluations after {nfev} without meeting a\n"
    "         convergence criterion. These parameters are its best point so far, not a\n"
    "         finished fit -- a better one for this log may exist. Treat the RMSE as a\n"
    "         description of this point only, and do not read the parameters as this\n"
    "         grill's measured values."
)


def _sim_kwargs(params):
    return {k: params[k] for k in _SIM_KEYS}


def _solve_scale(init):
    """Magnitude each free parameter is divided by before the solve.

    The free parameters differ by orders of magnitude, and scipy's
    finite-difference step is eps**0.5 * max(1, |x|) -- the max(1, ...) makes
    that step absolute rather than relative for anything below 1, so parameters
    of different size are probed with wildly different effective precision.
    Dividing each by its own magnitude puts them all near 1, which makes the
    step a true relative one for every parameter and leaves the Jacobian
    columns comparably sized.

    Taken from `init` rather than a fixed table so it tracks whatever the
    caller actually starts from -- a calibration run seeded from a previous
    fit, a grill whose parameters are decades away from the shipped ones -- and
    so that the scaling is fully determined by the starting point the caller
    chose, with nothing else feeding into how the solve is conditioned. A
    non-positive or non-finite starting value carries no magnitude to scale by,
    so it falls back to 1.
    """
    scale = []
    for key in _FREE:
        magnitude = abs(float(init[key]))
        scale.append(magnitude if magnitude > 0.0 and np.isfinite(magnitude) else 1.0)
    return np.array(scale, dtype=float)


def fit_params(t, temp, Q, *, T_amb, init, sigma=0.0, n_delay=0):
    """Fit the free grey-box parameters to a logged temperature series.

    `sigma` is a starting value like every other, and is subject to the same
    rule: moved if `_FREE` names it, returned exactly as passed if not. It is a
    named argument because every caller has it to hand separately from the
    parameters it is fitting. `_FREE` does not name it today -- see there for
    what that costs.

    The result carries `converged` alongside the parameters. A least-squares
    solve that runs out of evaluations still returns its best point so far, and
    that point can look entirely reasonable -- it simply has not been shown to
    be a minimum. A caller deciding whether to put this model on a live grill
    needs to tell the two apart, so the answer travels with the parameters
    rather than being something the caller must think to ask for.
    """
    temp = np.asarray(temp, dtype=float)
    # `sigma` is a starting value like any other, so it joins `init` and is
    # then subject to the same rule as the rest: moved if `_FREE` names it,
    # held at what came in if not. It stays a named argument because every
    # caller has it to hand separately from the parameters it is fitting.
    init = dict(init, sigma=sigma)
    # Everything the solve does not move stays where the caller put it. Which
    # parameters those are is `_FREE`'s business alone, so shrinking that set
    # holds the parameters it drops rather than dropping them from the model.
    held = {k: float(init[k]) for k in _FIT_KEYS if k not in _FREE}
    scale = _solve_scale(init)
    x0 = np.array([float(init[k]) for k in _FREE], dtype=float) / scale
    lo = _LOWER_BOUND / scale

    def residual(z):
        params = dict(held)
        params.update(zip(_FREE, z * scale))
        params.update(n_delay=n_delay)
        return simulate_grey_box(t, Q, T_amb=T_amb, T0=float(temp[0]), **_sim_kwargs(params)) - temp

    res = least_squares(residual, np.maximum(x0, lo), method="trf", bounds=(lo, np.inf), max_nfev=_MAX_NFEV)
    out = dict(held)
    out.update(zip(_FREE, (float(v) for v in res.x * scale)))
    # status 0 is scipy's "the evaluation budget ran out"; every other
    # non-negative status is one of its convergence criteria being met.
    out.update(converged=bool(res.status > 0), nfev=int(res.nfev))
    out.update(n_delay=int(n_delay), T_amb=float(T_amb))
    return out


def fit_quality(t, temp, Q, fitted, *, T_amb):
    """(RMSE, max absolute error) in degrees C between the fit and the log."""
    temp = np.asarray(temp, dtype=float)
    params = dict(fitted)
    params["T_amb"] = T_amb
    sim = simulate_grey_box(t, Q, T_amb=T_amb, T0=float(temp[0]), **_sim_kwargs(params))
    err = sim - temp
    return float(np.sqrt(np.mean(err**2))), float(np.max(np.abs(err)))


def main():
    ap = argparse.ArgumentParser(description="Fit MPC grey-box parameters to a calibration log.")
    ap.add_argument("csv")
    ap.add_argument("--t-amb", type=float, default=None, help="Ambient temperature in C")
    ap.add_argument("--json", action="store_true", help="Print only the fitted config JSON")
    args = ap.parse_args()

    import pandas as pd

    from controller.mpc import _DEFAULTS

    df = pd.read_csv(args.csv)
    t = df["time_s"].values
    temp = df["temp_c"].values
    Q = df["Q"].values

    T_amb = args.t_amb if args.t_amb is not None else float(_DEFAULTS["T_amb"])
    init = {k: float(_DEFAULTS[k]) for k in ("C_f", "C_c", "h_fc", "h_amb", "K_Q", "theta")}
    fitted = fit_params(
        t,
        temp,
        Q,
        T_amb=T_amb,
        init=init,
        sigma=float(_DEFAULTS["sigma"]),
        n_delay=int(_DEFAULTS["n_delay"]),
    )
    payload = {k: fitted[k] for k in CONFIG_KEYS}

    if args.json:
        # The config keys stay in their own object so they can still be pasted
        # or ingested whole, but they no longer travel without the fit's own
        # verdict on itself: this is the mode something else consumes, and a
        # machine reading an exhausted solve as a finished one is the failure
        # the `converged` flag exists to prevent. The human-readable warning
        # goes to stderr so stdout remains parseable JSON.
        rmse, max_err = fit_quality(t, temp, Q, fitted, T_amb=T_amb)
        print(
            json.dumps(
                {
                    "config": payload,
                    "fit": {
                        "converged": fitted["converged"],
                        "nfev": fitted["nfev"],
                        "rmse_c": rmse,
                        "max_error_c": max_err,
                    },
                },
                indent=2,
            )
        )
        if not fitted["converged"]:
            print(_NOT_CONVERGED.format(nfev=fitted["nfev"]), file=sys.stderr)
        return

    rmse, max_err = fit_quality(t, temp, Q, fitted, T_amb=T_amb)
    print(f"Fit quality: RMSE {rmse:.2f} C, max error {max_err:.2f} C")
    if not fitted["converged"]:
        print(_NOT_CONVERGED.format(nfev=fitted["nfev"]))
    if rmse > 10.0:
        print(
            "WARNING: RMSE above 10 C. This fit does not describe the log. Check that the log\n"
            "         covers a full heat-up and at least one step down, and that the fan was\n"
            "         under the controller's command throughout."
        )
    # Both quantities come from controller/model_promotion.py rather than being
    # recomputed here, so the horizon this utility asks for and the horizon the
    # promotion policy reports cannot drift apart. The two are deliberately
    # different: the time constant describes how sluggishly the chamber
    # responds and is printed because it is what a reader recognises, while the
    # braking distance -- how long the chamber goes on rising once the fuel is
    # cut -- is what the horizon has to cover, and is the only one of the two a
    # log like this determines.
    horizon = float(_DEFAULTS["n_horizon"]) * float(_DEFAULTS["t_step"])
    brake = longest_braking_distance(payload)
    print(
        f"Chamber time constant: {effective_tau(payload, T_HAZARD_C):.0f} s at "
        f"{T_HAZARD_C:.0f} C rising to {effective_tau(payload, T_FLOOR_C):.0f} s at {T_FLOOR_C:.0f} C"
    )
    print(f"Braking distance after a fuel cut: up to {brake:.0f} s across the operating range")
    # A cook that never approaches steady state cannot determine this, so it is
    # where a fit that has traded the chamber's parameters against each other
    # along a direction the log could not see says something visibly absurd. It
    # is printed rather than gated on: a reader who knows what this grill peaks
    # at can judge it, and a threshold that separated sound from absurd here
    # would have to be drawn much finer than the evidence supports.
    t_ss = steady_state_at_full_fire(payload)
    print(f"Implied steady state at full fire: {t_ss:.0f} C ({t_ss * 9.0 / 5.0 + 32.0:.0f} F)")
    if horizon < brake:
        print(
            f"WARNING: the chamber keeps rising for {brake:.0f} s after a full fuel cut, but the\n"
            f"         default prediction horizon is only {horizon:.0f} s. Raise n_horizon or\n"
            "         t_step, or the controller cannot see far enough ahead to stop in time."
        )
    print("\nPaste into Settings > Controller (controller.config.mpc):")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
