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
#: purpose -- this rejects nonsense, it does not express a preference.
PROMOTION_BOUNDS = {
    "C_f": (0.1, 1e4),
    "C_c": (1.0, 1e6),
    "h_fc": (1e-3, 1e3),
    "h_amb": (1e-4, 1e3),
    "theta": (0.0, 1200.0),
    "K_Q": (1e-3, 1e4),
}

#: A candidate must beat the incumbent's error by this fraction to be adopted
#: at all. Below it the two models describe the data equally well and churn
#: buys nothing.
_RMSE_MARGIN = 0.02

#: A candidate that SHORTENS the believed chamber time constant must beat the
#: incumbent by this much instead. Braking distance scales with tau, so a
#: wrongly-short tau brakes late -- the failure this whole design exists to
#: prevent -- while a wrongly-long tau brakes early and merely costs settling
#: time. The evidence bar is therefore deliberately asymmetric.
_RMSE_MARGIN_FASTER = 0.50

#: Ignore tau changes smaller than this; they are noise, not a direction.
_TAU_DEADBAND = 0.10


@dataclass
class Verdict:
    accepted: bool
    reason: str
    horizon_needed: int | None = None


def _tau(params):
    h_amb = float(params["h_amb"])
    return float(params["C_c"]) / h_amb if h_amb > 0 else math.inf


def evaluate(candidate, incumbent, *, candidate_rmse, incumbent_rmse, n_horizon, t_step):
    """Whether `candidate` may replace `incumbent`, and what horizon it needs."""
    for key, (lo, hi) in PROMOTION_BOUNDS.items():
        value = candidate.get(key)
        if value is None or not math.isfinite(float(value)):
            return Verdict(False, f"{key} is not a finite number")
        if not (lo <= float(value) <= hi):
            return Verdict(False, f"{key}={value:g} is outside [{lo:g}, {hi:g}]")

    if not math.isfinite(float(candidate_rmse)):
        return Verdict(False, "candidate RMSE is not finite")

    horizon_needed = None
    tau = _tau(candidate)
    if math.isfinite(tau) and n_horizon * t_step < tau:
        horizon_needed = int(math.ceil(tau / t_step))

    if incumbent is None or incumbent_rmse is None:
        return Verdict(True, "no incumbent", horizon_needed)

    # A shorter tau is the dangerous direction, so it carries the higher bar.
    ratio = _tau(candidate) / _tau(incumbent) if _tau(incumbent) > 0 else 1.0
    faster = ratio < (1.0 - _TAU_DEADBAND)
    margin = _RMSE_MARGIN_FASTER if faster else _RMSE_MARGIN
    if candidate_rmse > incumbent_rmse * (1.0 - margin):
        direction = "shorter" if faster else "comparable-or-longer"
        return Verdict(
            False,
            f"candidate RMSE {candidate_rmse:.3g} does not beat incumbent "
            f"{incumbent_rmse:.3g} by the {margin:.0%} required for a {direction} tau",
            horizon_needed,
        )
    return Verdict(True, "better fit on the same data", horizon_needed)
