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

import numpy as np
from scipy.optimize import least_squares

from controller.model_promotion import _KELVIN, _T_FLOOR_C, _T_HAZARD_C, _effective_tau, _slowest_tau, PROMOTION_BOUNDS
from controller.mpc_model import simulate_grey_box

# Keys the controller reads back out of a fitted result.
CONFIG_KEYS = ("C_f", "C_c", "h_fc", "h_amb", "T_amb", "theta", "n_delay", "K_Q", "sigma")

# Fitted free parameters. C_f is held at its init value: it is redundant with
# K_Q for the steady gain, so fitting both is ill-posed.
_FREE = ("K_Q", "C_c", "h_fc", "h_amb", "theta")

# The free set used when the record can identify sigma. K_Q leaves it as sigma
# joins, because the two cannot both be free.
#
# Scaling (K_Q, C_c, h_amb, sigma) by one common factor leaves the measured
# chamber temperature almost unchanged: the firepot is much faster than the
# chamber, so the chamber only ever sees the product K_Q*heat_in, after which
# C_c, h_amb and sigma divide out together. Freeing all four therefore leaves a
# whole family of equally good fits, and the solver picks from it by noise --
# in the sweep behind this gate (docs/superpowers/experiments/
# sigma_identifiability.py) the recovered sigma ranged over a factor of 200
# across seeds of the SAME cook, at every temperature span.
#
# The effective time constant is invariant along that family, which is why
# holding sigma has not been visibly harmful: it merely selects one member.
# But a sigma that is genuinely wrong can then only be absorbed by moving
# C_c and h_amb off the grill's real values, and a later fit that does open
# this gate would have to undo that. Holding K_Q instead pins the family down
# so sigma means something: with it held, the sweep recovers sigma to within
# 1% wherever this gate opens, and the effective tau it implies is closer to
# truth than the sigma-held fit at every operating point.
_FREE_WITH_SIGMA = ("C_c", "h_fc", "h_amb", "theta", "sigma")

_SIM_KEYS = ("C_f", "C_c", "h_fc", "h_amb", "K_Q", "sigma", "theta", "n_delay")

# Per-parameter bounds. A single scalar pair cannot serve here: the shared
# lower bound of 1e-9 that used to apply to every free parameter sits directly
# on top of sigma's own magnitude (~1.4e-9) and would pin it at the bound.
# theta divides the lag time constant and the rest are capacitances or
# conductances, so those stay strictly positive; sigma may legitimately be zero
# (a purely linear loss model) and takes its ceiling from the promotion policy,
# imported rather than restated so this fitter cannot produce a value its own
# promotion gate would refuse.
_BOUNDS = {
    "K_Q": (1e-9, np.inf),
    "C_c": (1e-9, np.inf),
    "h_fc": (1e-9, np.inf),
    "h_amb": (1e-9, np.inf),
    "theta": (1e-9, np.inf),
    "sigma": PROMOTION_BOUNDS["sigma"],
}

# Magnitude each free parameter is scaled by before the solve. The free set
# spans roughly 1e-9 to 1e4, and scipy's finite-difference step is
# eps**0.5 * max(1, |x|) -- the max(1, ...) means an unscaled sigma near 1.4e-9
# is probed with a step TEN TIMES its own value, so the solver sees noise where
# the derivative should be and stops immediately. Solving in x/nominal puts
# every free parameter near 1, which makes that step a true relative one. It
# also fixes a defect that predates sigma: the unscaled solve never converged
# at all, exhausting max_nfev on the reference cook, where the scaled solve
# reaches a lower cost in ~70 evaluations.
_NOMINAL = {"K_Q": 3.5, "C_c": 320.0, "h_fc": 1.3, "h_amb": 0.5, "theta": 50.0, "sigma": 1e-9}

# Reference coefficient for the identifiability test below. The test asks a
# question about the DATA -- does this record cover enough temperature to tell
# a T**3 loss term from a constant one -- so it must not be evaluated at the
# incoming sigma, which may be 0.0 and would then report every record as
# uninformative. 1.4e-9 is the value used consistently as ground truth across
# this codebase (controller/grill_sim.py's plant, and the shipped grey-box
# default), the same anchor model_promotion.py's bound is a multiple of.
_SIGMA_REF = 1.4e-9

# How much the radiative contribution to the loss conductance must vary across
# a record, as a fraction of the linear coefficient h_amb, before sigma is
# fitted rather than held.
#
# Derived, not chosen. sigma and h_amb are both chamber loss terms and differ
# only in temperature dependence, so what separates them is how much
# 4*sigma*(T+273.15)**3 MOVES over the record. The sweep in
# docs/superpowers/experiments/sigma_identifiability.py fits synthetic cooks
# from a known sigma over a grid of temperature ranges (spans of 0-200 C off
# floors at 40, 100, 160 and 220 C, ten noise seeds each) and scores recovery.
# This statistic separated identifiable cooks from unidentifiable ones better
# than the raw temperature span did (94% of grid cells versus 88%), because
# span alone cannot tell a record that HOLDS two temperatures from one that
# merely fell through the range once. At 0.20 every accepted cell in that sweep
# recovered sigma to within 25% on every seed, with no unidentifiable cell
# accepted; the price is refusing some cooks nearer 0.05 that would in fact
# have worked, which is the right direction for a gate whose failure mode is a
# confidently wrong loss model.
_MIN_RADIATIVE_SPREAD = 0.20

# Percentiles the spread is measured between. Min-to-max would count a
# temperature the grill only passed through on its way somewhere else: a record
# that starts off its setpoint, sags once and then sits there has a wide
# min-to-max but holds exactly one operating point, and the sweep shows it does
# not identify sigma. Percentiles weight by how long the grill actually spent
# at a temperature, which is what carries the information.
_SPREAD_PCTL = (10.0, 90.0)


def _sim_kwargs(params):
    return {k: params[k] for k in _SIM_KEYS}


def radiative_spread(temp):
    """How much radiative loss conductance varies across a logged record.

    `4*sigma*(T+273.15)**3` is what radiation contributes to the chamber's loss
    conductance (the linearisation `GreyBoxEKF._discretize` already computes).
    Returned in the same units as h_amb, so the two are directly comparable.
    """
    lo, hi = np.percentile(np.asarray(temp, dtype=float), _SPREAD_PCTL)
    return float(4.0 * _SIGMA_REF * ((hi + _KELVIN) ** 3 - (lo + _KELVIN) ** 3))


def can_identify_sigma(temp, *, h_amb):
    """Whether a record separates radiative loss from linear loss.

    False for a short or isothermal record, which is the case where holding
    sigma is not a compromise but the correct answer: at one temperature any
    sigma can be traded for an h_amb that produces identical loss, and fitting
    both would return a confident, arbitrary split of one number into two.
    """
    h_amb = float(h_amb)
    if not (h_amb > 0.0 and np.isfinite(h_amb)):
        return False
    return radiative_spread(temp) >= _MIN_RADIATIVE_SPREAD * h_amb


def fit_params(t, temp, Q, *, T_amb, init, sigma=0.0, n_delay=0):
    """Fit the free grey-box parameters to a logged temperature series.

    `sigma` is the starting point and the fallback. It is returned unchanged
    unless the record covers enough temperature to identify it, in which case
    it is fitted and K_Q is held instead -- see `_FREE_WITH_SIGMA` for why the
    two cannot both be free.
    """
    temp = np.asarray(temp, dtype=float)
    C_f = float(init["C_f"])
    sigma = float(sigma)
    # sigma == 0 is a caller who has opted out of the radiative term, not a
    # caller who does not know it yet: fitting one would answer a different
    # question than the one asked, by changing the model's structure rather
    # than its parameters. Refining a radiative model is this gate's job;
    # introducing one is not.
    fitting_sigma = sigma > 0.0 and can_identify_sigma(temp, h_amb=init["h_amb"])
    free = _FREE_WITH_SIGMA if fitting_sigma else _FREE

    held = {"C_f": C_f, "sigma": sigma, "n_delay": n_delay, "K_Q": float(init["K_Q"])}
    start = {**{k: float(init[k]) for k in _FREE}, "sigma": sigma}
    scale = np.array([_NOMINAL[k] for k in free], dtype=float)
    x0 = np.array([start[k] for k in free], dtype=float) / scale
    lo = np.array([_BOUNDS[k][0] for k in free], dtype=float) / scale
    hi = np.array([_BOUNDS[k][1] for k in free], dtype=float) / scale

    def residual(z):
        params = {**held, **dict(zip(free, z * scale))}
        return simulate_grey_box(t, Q, T_amb=T_amb, T0=float(temp[0]), **_sim_kwargs(params)) - temp

    res = least_squares(residual, np.clip(x0, lo, hi), method="trf", bounds=(lo, hi), max_nfev=2000)
    out = dict(zip(free, (float(v) for v in res.x * scale)))
    out.setdefault("K_Q", held["K_Q"])
    out.setdefault("sigma", sigma)
    out.update(C_f=C_f, n_delay=int(n_delay), T_amb=float(T_amb))
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
        print(json.dumps(payload, indent=2))
        return

    rmse, max_err = fit_quality(t, temp, Q, fitted, T_amb=T_amb)
    print(f"Fit quality: RMSE {rmse:.2f} C, max error {max_err:.2f} C")
    if rmse > 10.0:
        print(
            "WARNING: RMSE above 10 C. This fit does not describe the log. Check that the log\n"
            "         covers a full heat-up and at least one step down, and that the fan was\n"
            "         under the controller's command throughout."
        )
    if fitted["sigma"] != float(_DEFAULTS["sigma"]):
        # Reported with the caveat because it is a real one: sigma is fitted
        # against a HELD K_Q, and the two are not separately identifiable from
        # a log of (time, temperature, firing rate) alone -- scaling K_Q and
        # every loss term together describes the same grill. The fitted sigma
        # therefore carries whatever error the starting K_Q had. It is most
        # meaningful when refitting a model this utility already calibrated,
        # and least meaningful on a first run, where K_Q is still the shipped
        # default. The time constants below are unaffected: they are invariant
        # under that rescaling.
        print(
            f"Radiative coefficient fitted: sigma {float(_DEFAULTS['sigma']):.3g} -> {fitted['sigma']:.3g}\n"
            f"         (measured against K_Q held at {fitted['K_Q']:.3g}; a first fit from the shipped\n"
            "         defaults cannot separate the two, so re-run this on a later cook to refine it.)"
        )
    else:
        print(
            f"Radiative coefficient held at {fitted['sigma']:.3g}: this log's temperature range does\n"
            f"         not separate radiative from linear loss (spread {radiative_spread(temp):.3g} vs the\n"
            f"         {_MIN_RADIATIVE_SPREAD:.0%} of h_amb needed). A log covering a wider range would fit it."
        )

    # Both quantities come from controller/model_promotion.py rather than being
    # recomputed here, so the horizon this utility asks for and the horizon the
    # promotion policy reports cannot drift apart. The two are deliberately
    # different: C_c/h_amb bounds the effective time constant from above at
    # every temperature and so sizes one horizon for the whole operating range,
    # while the effective values below include radiative conductance and are
    # what the chamber's response actually is at each end of that range.
    horizon = float(_DEFAULTS["n_horizon"]) * float(_DEFAULTS["t_step"])
    tau = _slowest_tau(payload)
    print(
        f"Chamber time constant: {_effective_tau(payload, _T_HAZARD_C):.0f} s at "
        f"{_T_HAZARD_C:.0f} C rising to {_effective_tau(payload, _T_FLOOR_C):.0f} s at {_T_FLOOR_C:.0f} C"
    )
    if horizon < tau:
        print(
            f"WARNING: fitted chamber time constant reaches {tau:.0f} s but the default prediction\n"
            f"         horizon is only {horizon:.0f} s. Raise n_horizon or t_step, or the\n"
            "         controller cannot see far enough ahead to stop in time."
        )
    print("\nPaste into Settings > Controller (controller.config.mpc):")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
