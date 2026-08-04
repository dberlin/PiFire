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
import math
import sys

import numpy as np
from scipy.optimize import least_squares

from controller.model_promotion import (
    T_FLOOR_C,
    T_HAZARD_C,
    built_n_horizon,
    effective_tau,
    longest_braking_distance,
    steady_state_at_full_fire,
)
from controller.mpc_model import simulate_grey_box

# Keys the controller reads back out of a fitted result.
CONFIG_KEYS = ("C_c", "h_amb", "T_amb", "theta", "n_delay", "K_Q", "sigma")

# Fitted free parameters. h_amb and sigma are held at their init values.
#
# WHY ONE OF THEM MUST BE HELD. The dynamics are invariant under scaling the
# chamber capacitance, both loss coefficients and the input gain --
# (C_c, h_amb, sigma, K_Q) -- by one common factor, because the state equation
# is homogeneous in them, so the trajectory of the one measured state is
# bit-identical. What a log determines is the ratios among them, which is what
# the controller plans against: the effective time constant
# C_c/(h_amb + 4*sigma*(T+273.15)**3) is one of them, and is unchanged by which
# parameter is held. See docs/superpowers/experiments/sigma_identifiability.py.
#
# WHY h_amb AND sigma ARE BOTH HELD, not just one. Holding one of the four
# fixes that scaling, and what a log leaves undetermined after that is the
# SPLIT of the chamber's loss between its linear and radiative parts: h_amb and
# sigma trade against each other with C_c following, at essentially no residual
# cost. Leaving either free lets the solve run away along that trade -- with
# h_amb free the real MAK cook lands at C_c 2.6e7 and h_amb 7.4e3, an order of
# magnitude past model_promotion.PROMOTION_BOUNDS, so evaluate() refuses the
# model however well it describes the log; with sigma free it goes the other
# way, to an all-radiative model at sigma 5e-3 and C_c 3e8. Holding both keeps
# every fit inside the bounds. The price is that the radiative share is fixed
# rather than fitted, so a grill whose share differs is described by a model
# carrying the right C_c/h_amb and the wrong split. What that costs the
# quantity the horizon is sized from is measured in
# tests/unit/mpc/test_model_promotion.py.
#
# WHAT THE THREE FREE ONES ARE. They are exactly the directions a cook
# determines. K_Q/C_c, the steady input gain, is the best-determined quantity
# in the model -- reproducible to 0.5% across nine cooks including ones where
# the raw parameters ran away by 800x. C_c against the held conductances is the
# effective time constant, recovered to within 2% of truth at every ambient-loss
# level from 0.25x to 4x nominal. theta is the only parameter sharply
# identifiable on every record measured including the real 1240 s cook, and the
# largest single lever on both dead time and coast.
_FREE = ("K_Q", "C_c", "theta")

_SIM_KEYS = ("C_c", "h_amb", "K_Q", "sigma", "theta", "n_delay")

# Parameters a caller supplies a starting value for. `_FREE` selects which of
# these the solve moves; the rest are held at the value they came in with.
_FIT_KEYS = ("C_c", "h_amb", "K_Q", "sigma", "theta")

# Strictly positive: theta divides the lag time constant, and every other free
# parameter is a capacitance or a conductance. The solve works in log space
# (see `fit_params`), so this is expressed as a floor on the logarithm and the
# positivity itself is structural rather than a constraint the solver enforces.
_LOWER_BOUND = 1e-9

# Evaluations the solve is allowed. Not enough to converge on every log -- see
# `fit_params`, which reports whether it did rather than presenting an
# exhausted solve as a finished one.
_MAX_NFEV = 2000

# The per-sample residual reported for a parameter set the model cannot be
# simulated at. 1e4 degrees C is far outside anything a cook contains, so the
# solve treats such a point as very bad, which is what it is; what matters is
# that it is a NUMBER, because a NaN residual is not comparable to anything and
# a trust region cannot step away from what it cannot compare.
_DIVERGED = 1e4

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


def _log_or_floor(value, floor):
    """log(value), or `floor` when there is no logarithm to take."""
    value = float(value)
    return math.log(value) if value > 0.0 and math.isfinite(value) else floor


def fit_params(t, temp, Q, *, T_amb, init, sigma=0.0, n_delay=0):
    """Fit the free grey-box parameters to a logged temperature series.

    `sigma` is a starting value like every other in `init`, and gets the same
    treatment: moved if `_FREE` names it, returned exactly as passed if not. It
    is a separate argument only because every caller has it to hand apart from
    the parameters it is fitting.

    THE SOLVE IS IN LOG SPACE. Every free parameter is a strictly positive
    scale -- a capacitance, a gain, a duration -- so what a step of the solve
    should mean is a RATIO, not a difference. scipy's finite-difference step is
    eps**0.5 * max(1, |x|), whose max(1, ...) makes that step absolute rather
    than relative for anything below 1, so parameters decades apart in size
    would otherwise be probed with wildly different effective precision.
    Optimising log(parameter) makes every step a true relative one at every
    point of the solve rather than only at the starting point, and makes the
    positivity structural instead of a bound the solver has to respect.

    It also decides the answer on the record this model most has to fit. The
    chamber equation is close to invariant under scaling C_c and K_Q together
    -- the loss terms shrink against them, and the limit is a pure integrator
    that describes a heat-up ramp nearly as well as the real model does. That
    is a straight line in the parameters and a curve in their logarithms, so a
    solve in the raw parameters slides down it: from the shipped starting point
    the real 1240 s MAK cook in tests/unit/mpc/fixtures ends at C_c 1.4e9 and
    an RMSE of 11.98 C, while the same solve in log space reaches the actual
    minimum, C_c 3558 and 2.55 C, in ten evaluations. On the eight synthetic
    scenarios across both plants in controller/grill_sim.py, where no such
    escape is open, the two agree to three decimals on every one.

    The result carries `converged` alongside the parameters. A least-squares
    solve that runs out of evaluations still returns its best point so far, and
    that point can look entirely reasonable -- it simply has not been shown to
    be a minimum. A caller deciding whether to put this model on a live grill
    needs to tell the two apart, so the answer travels with the parameters
    rather than being something the caller must think to ask for.
    """
    temp = np.asarray(temp, dtype=float)
    init = dict(init, sigma=sigma)
    # Everything the solve does not move stays where the caller put it. Which
    # parameters those are is `_FREE`'s business alone, so shrinking that set
    # holds the parameters it drops rather than dropping them from the model.
    held = {k: float(init[k]) for k in _FIT_KEYS if k not in _FREE}
    lo = math.log(_LOWER_BOUND)
    # A non-positive or non-finite starting value has no logarithm to start
    # from, so it starts at the floor rather than taking the solve down with it.
    x0 = np.array([_log_or_floor(init[k], lo) for k in _FREE], dtype=float)

    def simulate(z):
        """The trajectory at `z`, or None where the model cannot be simulated.

        A parameter set the solve is only trying out can drive the chamber
        integration away, and it does so in either of two shapes. The chamber
        state is a Python float, so its radiative term raises OverflowError
        past about 1e77 -- an exception out of the middle of a fit, not a
        number -- while an intermediate that goes through numpy instead
        produces inf and NaN. Both are the same event and both are caught
        here, because a caller that has to remember which one a given
        parameter set produces has not been given a guard.
        """
        params = dict(held)
        params.update(zip(_FREE, (math.exp(v) for v in z)))
        params.update(n_delay=n_delay)
        try:
            y = simulate_grey_box(t, Q, T_amb=T_amb, T0=float(temp[0]), **_sim_kwargs(params))
        except OverflowError:
            return None
        return y if np.all(np.isfinite(y)) else None

    def residual(z):
        y = simulate(z)
        # NaN reaching least_squares is not a large residual -- every
        # comparison against it is False, so the step that produced it is
        # neither accepted nor rejected on its merits and the solve wanders
        # from there. `_DIVERGED` is finite, so such a point is simply a very
        # bad one and the trust region shrinks away from it.
        if y is None:
            return np.full_like(temp, _DIVERGED)
        return y - temp

    res = least_squares(residual, x0, method="trf", bounds=(lo, np.inf), max_nfev=_MAX_NFEV)
    out = dict(held)
    out.update(zip(_FREE, (math.exp(float(v)) for v in res.x)))
    # status 0 is scipy's "the evaluation budget ran out"; every other
    # non-negative status is one of its convergence criteria being met. A point
    # the model cannot even be simulated at is not a fit whatever scipy makes
    # of the residuals around it, so the finite check is ANDed in rather than
    # left to the caller: this result is about to be offered to a live grill.
    out.update(converged=bool(res.status > 0) and simulate(res.x) is not None, nfev=int(res.nfev))
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


# One part in a thousand of a log, for the central differences below. The solve
# itself works in log space, so this probes the parameters in the space they are
# identified in; small enough that the model is linear across the step and large
# enough to stay well clear of the simulator's own resolution.
_IDENT_STEP = 1e-3


def identifiability(t, Q, fitted, *, T_amb, T0):
    """How well this record pins down the free parameters, in C RMS per e-fold.

    The smallest singular value of the prediction's Jacobian with respect to
    `_FREE` in log space, normalised per sample. Its units are degrees C RMS per
    e-fold of the orthonormal parameter direction this record constrains least,
    so it answers: how far can this record's best-fitting parameters be moved,
    in the direction it pins down worst, before the prediction moves at all? A
    record that leaves some direction free scores near zero, and the model it
    produces is determined by the starting point rather than by the cook.

    NO MEASURED TEMPERATURE ENTERS THIS BEYOND `T0`, and the signature is the
    guarantee: the record's temperature series is not an argument, so there is
    no channel through which the fit residual could reach the arithmetic below.
    What is read is `t`, `Q`, the starting temperature and the fitted point --
    what the record ASKED the grill to do, not how well the fit turned out.

    That is what lets this stand beside a residual statistic and say something
    the residual cannot. An in-sample RMSE is minimised just as neatly on a
    record that determines nothing -- more neatly, in fact, since there is less
    for the model to disagree with -- so the two rank records in opposite
    orders, and only this one ranks them the way the risk runs.

    `None` where no such measurement exists: a free parameter that is not a
    positive finite scale has no logarithm to perturb, and a simulation that
    leaves the reals says nothing about the record. Both shapes of the latter
    are caught -- a raised OverflowError out of the chamber's float arithmetic
    and a quiet NaN out of numpy -- for the reason `fit_params.simulate` states.
    The raised shape is the one that matters to the caller: `Controller.
    refit_from_cook` runs this inside a `try` that catches ValueError and
    FloatingPointError only, so an OverflowError leaving here would leave
    `refit_from_cook` altogether, into a grill teardown that has a cool-down
    fan to start.
    """
    cols = []
    for key in _FREE:
        base = float(fitted[key])
        if not (base > 0.0 and math.isfinite(base)):
            return None
        try:
            up = _sim_at(t, Q, fitted, key, base * math.exp(_IDENT_STEP), T_amb=T_amb, T0=T0)
            dn = _sim_at(t, Q, fitted, key, base * math.exp(-_IDENT_STEP), T_amb=T_amb, T0=T0)
        except OverflowError:
            return None
        if not (np.all(np.isfinite(up)) and np.all(np.isfinite(dn))):
            return None
        cols.append((up - dn) / (2.0 * _IDENT_STEP))
    jacobian = np.column_stack(cols) / math.sqrt(len(t))
    svals = np.linalg.svd(jacobian, compute_uv=False)
    return float(svals[-1])


def _sim_at(t, Q, fitted, key, value, *, T_amb, T0):
    """The model's trajectory with one parameter moved off the fitted point."""
    params = dict(fitted)
    params[key] = value
    return simulate_grey_box(t, Q, T_amb=T_amb, T0=float(T0), **_sim_kwargs(params))


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
    init = {k: float(_DEFAULTS[k]) for k in ("C_c", "h_amb", "K_Q", "theta")}
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
        # Not a warning any more: the controller derives its own horizon from
        # this same braking distance and plans over the longer one. All that is
        # left to say is what it will do, and that no setting has to be changed
        # to get it -- configuring that length by hand would only stop a later,
        # quicker model from shortening the horizon again.
        steps = built_n_horizon(payload, n_horizon=_DEFAULTS["n_horizon"], t_step=_DEFAULTS["t_step"])
        covered = steps * float(_DEFAULTS["t_step"])
        # This reads the shipped t_step, where _HORIZON_CAP_S // t_step is
        # exactly _HORIZON_CAP_STEPS, so the step bound has nothing left to
        # truncate and cannot be why the horizon falls short here. The only
        # shortfall reachable on this path is a coast past the seconds bound,
        # which no setting lengthens -- so the advice this prints is about the
        # model, and t_step is deliberately not offered.
        outcome = (
            "n_horizon does not need changing"
            if covered >= brake
            else f"that spans {covered:.0f} s, the furthest ahead it plans at any setting, so this\n"
            f"      fit's coast is longer than the controller is built to brake"
        )
        print(
            f"NOTE: the chamber keeps rising for {brake:.0f} s after a full fuel cut, past the\n"
            f"      {horizon:.0f} s the default prediction horizon spans. The controller plans over\n"
            f"      {steps} steps for this model on its own; {outcome}."
        )
    print("\nPaste into Settings > Controller (controller.config.mpc):")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
