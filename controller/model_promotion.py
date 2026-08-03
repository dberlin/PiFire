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
#: spans a hard winter night to a hot afternoon; n_delay sizes the state
#: vector and Jacobian one lag state at a time, so it is bounded well below
#: values that stall the solver, and must additionally be a whole number of
#: lag states. sigma must be non-negative (a negative radiative coefficient
#: inverts the loss term into a gain); its cap is NOT a real-watts physical
#: ceiling -- C_c/h_amb in this model are not SI joules/watts (the shipped
#: h_amb=0.50 would be an implausibly under-insulated chamber if they were),
#: so a Stefan-Boltzmann-in-real-units bound is off by whatever unscaled
#: factor the fitting process folds in, and cannot be compared to h_amb
#: directly. What IS independently verifiable is that sigma=1.4e-9 is the
#: one value used consistently as ground truth across this codebase (the
#: plant simulator's own fitted truth parameter in controller/grill_sim.py,
#: and the shipped grey-box default's calibration target) -- so the cap is a
#: multiple of THAT anchor, not an independently-derived physical number.
#: sigma folds in the chamber's radiating area, which is a whole multiple
#: larger on a big cabinet smoker than on the anchor grill, and a fit landing
#: outside this range is refused outright rather than clipped -- so the cap
#: sits an order of magnitude above the anchor. That still leaves sigma the
#: tightest ratio in this table by a wide margin (every other parameter is
#: given three or more decades around its shipped value), which is affordable
#: because sigma's effect on braking distance is priced into the guarded
#: quantity below rather than left to this bound to police.
PROMOTION_BOUNDS = {
    "C_f": (0.1, 1e4),
    "C_c": (1.0, 1e6),
    "h_fc": (1e-3, 1e3),
    "h_amb": (1e-4, 1e3),
    "T_amb": (-40.0, 60.0),
    "theta": (0.0, 1200.0),
    "n_delay": (0.0, 50.0),
    "K_Q": (1e-3, 1e4),
    "sigma": (0.0, 1e-8),
}

#: A candidate must beat the incumbent's error by this fraction to be adopted
#: at all. Below it the two models describe the data equally well and churn
#: buys nothing.
_RMSE_MARGIN = 0.02

#: A candidate that SHORTENS the believed chamber time constant or effective
#: dead time, by any amount, must beat the incumbent by this much instead.
#: Braking distance grows with both, so a wrongly-short tau or dead time
#: brakes late -- the failure this whole design exists to prevent -- while
#: overestimating either brakes early and merely costs settling time. The bar
#: applies to the smallest shortening as much as the largest: an incumbent
#: must not be dislodged by a chain of individually-small cuts, each waved
#: through on the narrow margin, that compounds into a large, unproven one.
_RMSE_MARGIN_FASTER = 0.50

#: How far tau or effective dead time may grow past the incumbent's before the
#: refusal reason calls it "longer" rather than "unchanged". Labels the growth
#: side of the comparison only -- it never changes which margin applies, since
#: any growth at all already takes the narrow one.
_TAU_DEADBAND = 0.10

#: Incumbent fields the shrink comparison needs. A partial incumbent missing
#: one of these cannot be judged, so it is refused rather than raising.
_INCUMBENT_KEYS = ("C_c", "h_amb", "theta", "n_delay", "sigma")

#: Kelvin offset for the Celsius T_c/T_amb this model works in, matching
#: controller/mpc_model.py's own constant.
_KELVIN = 273.15

#: The grill's hottest permitted operating point -- the hard safety shutoff
#: (`maxtemp` in common/settings_schema.py, 550 F) converted to the Celsius
#: this model's chamber temperature is expressed in. The radiative loss
#: term's linearized conductance grows with T**3, so this is where it is
#: largest and the chamber's response quickest.
_T_HAZARD_C = (550.0 - 32.0) * 5.0 / 9.0

#: The coolest point of that same range -- `minstartuptemp` in
#: common/settings_schema.py (75 F), the floor of the flameout threshold below
#: which the safety logic declares the fire out, in the same Celsius. It is
#: _T_HAZARD_C's counterpart in the same settings section: between the two the
#: controller is driving a live fire, and outside them it is not running at
#: all. Here the radiative conductance is smallest and the chamber slowest,
#: which is the far end of the curve a single hot reference cannot see.
_T_FLOOR_C = (75.0 - 32.0) * 5.0 / 9.0


@dataclass
class Verdict:
    """The promotion decision, and the horizon the candidate would need.

    `reason` reasons about the effective time constant -- C_c over the linear
    plus linearized-radiative conductance -- at the ends of the operating
    range, since that is the quantity a late brake is measured against and it
    varies with temperature. `horizon_needed` is sized instead from C_c/h_amb,
    the radiation-free supremum of that same effective time constant, so that
    one horizon covers every temperature the grill runs at rather than only
    the end it was evaluated at. The two numbers are deliberately different
    quantities: the larger, temperature-free bound for sizing, the
    temperature-aware pair for judging.
    """

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


def _slowest_tau(params):
    """The longest chamber time constant any operating point can produce.

    The linearized radiative conductance below is non-negative, so C_c/h_amb
    bounds C_c/(h_amb + it) from above at every temperature. Sizing the
    horizon from this bound rather than from an operating-range endpoint keeps
    one horizon adequate across the whole range instead of only at the end it
    was measured at.
    """
    h_amb = float(params["h_amb"])
    return float(params["C_c"]) / h_amb if h_amb > 0 else math.inf


def _effective_tau(params, t_ref_c):
    """The chamber time constant at chamber temperature `t_ref_c` (Celsius).

    controller/mpc_model.py's radiative loss term,
    sigma*((T_c+273.15)**4 - (T_amb+273.15)**4), has linearized conductance
    4*sigma*(T_c+273.15)**3 (exactly what GreyBoxEKF._discretize computes as
    `rp` about its own operating point) -- an addition to h_amb that a plain
    C_c/h_amb tau cannot see. A candidate that raises sigma shortens this
    tau exactly as cutting C_c or raising h_amb would. The conductance grows
    with T**3 while h_amb is flat, so this is a genuine function of the
    operating point and not one number for the whole grill.
    """
    h_amb = float(params["h_amb"])
    sigma = float(params["sigma"])
    h_eff = h_amb + 4.0 * sigma * (t_ref_c + _KELVIN) ** 3
    return float(params["C_c"]) / h_eff if h_eff > 0 else math.inf


def _effective_theta(params):
    """The dead time the controller actually anticipates.

    n_delay == 0 removes the transport-lag chain outright -- heat is routed
    straight to the firepot -- so theta contributes a delay only when at
    least one lag state exists. A candidate that zeroes n_delay while leaving
    theta untouched has, in effect, cut the dead time to zero.
    """
    return float(params["theta"]) if float(params["n_delay"]) > 0 else 0.0


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


def _range_label(labels):
    """One label for a quantity read at several operating points.

    Any shortening anywhere in the range is what the wide margin is charged
    for, so it names the whole range; "longer" is claimed only when it holds
    throughout.
    """
    if "shorter" in labels:
        return "shorter"
    return "longer" if all(label == "longer" for label in labels) else "unchanged"


def evaluate(candidate, incumbent, *, candidate_rmse, incumbent_rmse, n_horizon, t_step):
    """Whether `candidate` may replace `incumbent`, and what horizon it needs."""
    for key, (lo, hi) in PROMOTION_BOUNDS.items():
        value = _finite(candidate.get(key))
        if value is None:
            return Verdict(False, f"{key} is not a finite number")
        if not (lo <= value <= hi):
            return Verdict(False, f"{key}={value:g} is outside [{lo:g}, {hi:g}]")

    n_delay = _finite(candidate["n_delay"])
    if not n_delay.is_integer():
        return Verdict(False, f"n_delay={n_delay:g} must be a whole number")

    theta = _finite(candidate["theta"])
    if n_delay > 0 and theta <= 0:
        return Verdict(False, "theta must be positive whenever n_delay enables the transport-delay chain")

    if _finite(candidate_rmse) is None or float(candidate_rmse) <= 0:
        return Verdict(False, "candidate RMSE must be a positive, finite number")

    if _finite(t_step) is None or float(t_step) <= 0:
        return Verdict(False, "t_step must be a positive, finite number")
    if _finite(n_horizon) is None or float(n_horizon) <= 0:
        return Verdict(False, "n_horizon must be a positive, finite number")

    horizon_needed = None
    tau = _slowest_tau(candidate)
    if math.isfinite(tau) and float(n_horizon) * float(t_step) < tau:
        horizon_needed = int(math.ceil(tau / float(t_step)))

    if incumbent is None:
        return Verdict(True, "no incumbent", horizon_needed)

    if incumbent_rmse is None:
        return Verdict(False, "incumbent RMSE is not recorded; cannot compare", horizon_needed)

    if _finite(incumbent_rmse) is None or float(incumbent_rmse) <= 0:
        return Verdict(False, "incumbent RMSE must be a positive, finite number", horizon_needed)

    for key in _INCUMBENT_KEYS:
        if _finite(incumbent.get(key)) is None:
            return Verdict(False, "incumbent model is missing required parameters", horizon_needed)

    theta_candidate = _effective_theta(candidate)
    theta_incumbent = _effective_theta(incumbent)
    margin = _shrink_margin(theta_candidate, theta_incumbent)

    # Both ends of the operating range, not just the hot one. The effective
    # tau is monotone in temperature, and two candidates' curves are equal
    # exactly where a linear equation in (T+_KELVIN)**3 holds, so they cross
    # at most once unless they coincide everywhere. A candidate no shorter at
    # both ends is therefore no shorter anywhere between them -- dipping below
    # in the middle and returning would take two crossings. Reading one point
    # instead lets a candidate trade sigma against h_amb so that its single
    # crossing lands exactly there, reading "unchanged" while being genuinely
    # quicker across the rest of the range.
    tau_labels = []
    for t_ref_c in (_T_FLOOR_C, _T_HAZARD_C):
        tau_eff_candidate = _effective_tau(candidate, t_ref_c)
        tau_eff_incumbent = _effective_tau(incumbent, t_ref_c)
        margin = max(margin, _shrink_margin(tau_eff_candidate, tau_eff_incumbent))
        tau_labels.append(_direction_label(tau_eff_candidate, tau_eff_incumbent))

    if candidate_rmse > incumbent_rmse * (1.0 - margin):
        direction = (
            f"a {_range_label(tau_labels)} tau across the operating range and "
            f"a {_direction_label(theta_candidate, theta_incumbent)} dead time"
        )
        return Verdict(
            False,
            f"candidate RMSE {candidate_rmse:.3g} does not beat incumbent "
            f"{incumbent_rmse:.3g} by the {margin:.0%} required for {direction}",
            horizon_needed,
        )
    return Verdict(True, "better fit on the same data", horizon_needed)
