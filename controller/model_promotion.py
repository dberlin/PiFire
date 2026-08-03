#!/usr/bin/env python3

"""
*****************************************
 PiFire MPC Model Promotion Policy
*****************************************

 Decides whether a freshly identified thermal model may replace the one
 currently driving the grill. Pure functions: the caller owns the fitting, the
 storage and the timing, so this file is only the judgement, and can be
 exercised without a solver, a datastore or a cook.

*****************************************
"""

import math
from dataclasses import dataclass

#: Ranges a fitted parameter must fall inside to be considered at all. Wide on
#: purpose -- this rejects nonsense, it does not express a preference. T_amb
#: spans a hard winter night to a hot afternoon; sigma must be non-negative (a
#: negative radiative coefficient inverts the loss term into a gain) and is
#: capped several decades above the physical default so a bad fit cannot make
#: the model over-predict ambient loss; n_delay sizes the state vector and
#: Jacobian one lag state at a time, so it is bounded well below values that
#: stall the solver, and must additionally be a whole number of lag states.
PROMOTION_BOUNDS = {
    "C_f": (0.1, 1e4),
    "C_c": (1.0, 1e6),
    "h_fc": (1e-3, 1e3),
    "h_amb": (1e-4, 1e3),
    "T_amb": (-40.0, 60.0),
    "theta": (0.0, 1200.0),
    "n_delay": (0.0, 50.0),
    "K_Q": (1e-3, 1e4),
    "sigma": (0.0, 1e-6),
}

#: A candidate must beat the incumbent's error by this fraction to be adopted
#: at all. Below it the two models describe the data equally well and churn
#: buys nothing.
_RMSE_MARGIN = 0.02

#: A candidate that SHORTENS the believed chamber time constant or dead time,
#: by any amount, must beat the incumbent by this much instead. Braking
#: distance grows with both, so a wrongly-short tau or theta brakes late --
#: the failure this whole design exists to prevent -- while overestimating
#: either brakes early and merely costs settling time. The bar applies to the
#: smallest shortening as much as the largest: an incumbent must not be
#: dislodged by a chain of individually-small cuts, each waved through on the
#: narrow margin, that compounds into a large, unproven one.
_RMSE_MARGIN_FASTER = 0.50

#: How far tau or theta may grow past the incumbent's before the refusal
#: reason calls it "longer" rather than "unchanged". Labels the growth side of
#: the comparison only -- it never changes which margin applies, since any
#: growth at all already takes the narrow one.
_TAU_DEADBAND = 0.10

#: Incumbent fields the shrink comparison needs. A partial incumbent missing
#: one of these cannot be judged, so it is refused rather than raising.
_INCUMBENT_KEYS = ("C_c", "h_amb", "theta")


@dataclass
class Verdict:
    accepted: bool
    reason: str
    horizon_needed: int | None = None


def _finite(value):
    """`value` as a float, or None if it isn't one or isn't finite."""
    try:
        f = float(value)
    except TypeError, ValueError:
        return None
    return f if math.isfinite(f) else None


def _tau(params):
    h_amb = float(params["h_amb"])
    return float(params["C_c"]) / h_amb if h_amb > 0 else math.inf


def _shrink_margin(candidate_value, incumbent_value):
    """The RMSE margin `candidate_value` must clear against `incumbent_value`.

    An incumbent that is not a usable positive reference is treated as the
    risky case rather than divided by: that forces the wide margin instead of
    silently falling through to the narrow one.
    """
    if not (incumbent_value > 0 and math.isfinite(incumbent_value)):
        return _RMSE_MARGIN_FASTER
    return _RMSE_MARGIN_FASTER if candidate_value < incumbent_value else _RMSE_MARGIN


def _direction_label(candidate_value, incumbent_value):
    if not (incumbent_value > 0 and math.isfinite(incumbent_value)):
        return "shorter"
    if candidate_value < incumbent_value:
        return "shorter"
    if candidate_value > incumbent_value * (1.0 + _TAU_DEADBAND):
        return "longer"
    return "unchanged"


def evaluate(candidate, incumbent, *, candidate_rmse, incumbent_rmse, n_horizon, t_step):
    """Whether `candidate` may replace `incumbent`, and what horizon it needs."""
    for key, (lo, hi) in PROMOTION_BOUNDS.items():
        value = candidate.get(key)
        if value is None or not math.isfinite(float(value)):
            return Verdict(False, f"{key} is not a finite number")
        if not (lo <= float(value) <= hi):
            return Verdict(False, f"{key}={value:g} is outside [{lo:g}, {hi:g}]")

    if not float(candidate["n_delay"]).is_integer():
        return Verdict(False, f"n_delay={candidate['n_delay']:g} must be a whole number")

    if not math.isfinite(float(candidate_rmse)):
        return Verdict(False, "candidate RMSE is not finite")

    if _finite(t_step) is None or float(t_step) <= 0:
        return Verdict(False, "t_step must be a positive, finite number")
    if _finite(n_horizon) is None or float(n_horizon) <= 0:
        return Verdict(False, "n_horizon must be a positive, finite number")

    horizon_needed = None
    tau = _tau(candidate)
    if math.isfinite(tau) and float(n_horizon) * float(t_step) < tau:
        horizon_needed = int(math.ceil(tau / float(t_step)))

    if incumbent is None or incumbent_rmse is None:
        return Verdict(True, "no incumbent", horizon_needed)

    if _finite(incumbent_rmse) is None:
        return Verdict(False, "incumbent RMSE is not a finite number", horizon_needed)

    for key in _INCUMBENT_KEYS:
        if _finite(incumbent.get(key)) is None:
            return Verdict(False, "incumbent model is missing required parameters", horizon_needed)

    tau_incumbent = _tau(incumbent)
    theta_candidate = float(candidate["theta"])
    theta_incumbent = float(incumbent["theta"])

    margin = max(
        _shrink_margin(tau, tau_incumbent),
        _shrink_margin(theta_candidate, theta_incumbent),
    )
    if candidate_rmse > incumbent_rmse * (1.0 - margin):
        direction = (
            f"a {_direction_label(tau, tau_incumbent)} tau and "
            f"a {_direction_label(theta_candidate, theta_incumbent)} dead time"
        )
        return Verdict(
            False,
            f"candidate RMSE {candidate_rmse:.3g} does not beat incumbent "
            f"{incumbent_rmse:.3g} by the {margin:.0%} required for {direction}",
            horizon_needed,
        )
    return Verdict(True, "better fit on the same data", horizon_needed)
