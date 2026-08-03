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
    "C_c": (1.0, 1e6),
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

#: Firing-rate demand that counts as full fire. Q is not a physical unit -- it
#: is the abstract scalar controller/mpc_allocator.py maps affinely onto auger
#: duty, and 100 is the top of that range (`Q_max` in controller/mpc.py's
#: defaults). It appears here because the braking distance depends on how much
#: heat was in flight when the fuel was cut, and it is a keyword rather than
#: only a constant so a grill configured to a different top of range can be
#: asked about its own.
Q_FULL_FIRE = 100.0

#: Bisection steps used to invert the lag chain's survival. The bracket halves
#: each step, so this resolves the answer to about a part in 10**15 of it --
#: past the point where the estimate's own approximations matter, and cheap
#: enough that the exactness costs nothing.
_BISECT_STEPS = 60

#: The grill's hottest permitted operating point -- the hard safety shutoff
#: (`maxtemp` in common/settings_schema.py, 550 F) converted to the Celsius
#: this model's chamber temperature is expressed in. The radiative loss
#: term's linearized conductance grows with T**3, so this is where it is
#: largest and the chamber's response quickest.
T_HAZARD_C = (550.0 - 32.0) * 5.0 / 9.0

#: The coolest point of that same range -- `minstartuptemp` in
#: common/settings_schema.py (75 F), the floor of the flameout threshold below
#: which the safety logic declares the fire out, in the same Celsius. It is
#: T_HAZARD_C's counterpart in the same settings section: between the two the
#: controller is driving a live fire, and outside them it is not running at
#: all. Here the radiative conductance is smallest and the chamber slowest,
#: which is the far end of the curve a single hot reference cannot see.
T_FLOOR_C = (75.0 - 32.0) * 5.0 / 9.0


@dataclass
class Verdict:
    """The promotion decision, and the horizon the candidate would need.

    `reason` reasons about the effective time constant -- C_c over the linear
    plus linearized-radiative conductance -- at the ends of the operating
    range, since a shortened one is the shape of a model that brakes late, and
    it varies with temperature.

    `horizon_needed` is a different quantity, not a scaled version of that
    one. It is the braking distance: the seconds the chamber goes on rising
    after full fire is cut, at whichever end of the range takes longest. A
    horizon shorter than that cannot contain the end of any brake the
    controller might plan. The time constant is the wrong number to size from
    -- a heat-up ramp that never approaches steady state does not determine
    it, so a horizon derived from it is a demand no measurement supports.
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


def n_delay_is_whole(value):
    """Whether `value` (already known finite) is a whole number of lag states.

    n_delay sizes the estimator's lag-state chain one whole state at a time,
    so a fractional count is nonsense even where it lies inside
    PROMOTION_BOUNDS's numeric range. Exposed so this stays the one place
    that decides what "whole" means here -- a caller re-validating a
    persisted snapshot (controller/mpc.py's restore_model) imports this
    rather than re-deriving it, so the two cannot drift apart.
    """
    return float(value).is_integer()


def _chain_survival(t, *, stages, mean):
    """The fraction of a charged lag chain's output still present at `t`.

    A chain of `stages` equal first-order lags totalling `mean` seconds, every
    stage starting full and its input cut to zero, has output
    exp(-x) * sum(x**k/k!) for x = stages*t/mean -- the Erlang survival
    function. Strictly decreasing from 1 at t=0 to 0, which is what lets
    `braking_distance` invert it by bisection.
    """
    x = stages * t / mean
    if x > 700.0:
        return 0.0
    term, total = 1.0, 1.0
    for k in range(1, stages):
        term *= x / k
        total += term
    return math.exp(-x) * total


def braking_distance(params, t_ref_c, *, q_full=Q_FULL_FIRE):
    """Seconds a chamber at `t_ref_c` keeps rising after full fire is cut.

    This is what a prediction horizon has to cover: unless the horizon reaches
    past this, no plan the controller can make ends with the chamber having
    stopped, and the overshoot it is trying to avoid happens outside anything
    it can see. It is a necessary length, not a sufficient one.

    At the instant of the cut the grill has been at `q_full` long enough for
    the transport chain to be charged, so the heat reaching the chamber is
    K_Q*q_full. The chamber stops rising when that heat has fallen to the
    chamber's own loss at `t_ref_c` -- controller/mpc_model.py's
    h_amb*(T-T_amb) plus its radiative term. So the braking distance is the
    time the flux takes to decay by the factor between them.

    The flux decays through the n_delay lag stages of theta/n_delay each, and
    that is the whole cascade: the chamber is the only thermal mass left, and
    the flux has already arrived once it reaches the chamber. Every stage is
    the same length, so the Erlang survival below is the model's own decay
    exactly rather than a bound on it. With n_delay 0 there is no chain and
    nothing in flight at the cut, so the chamber stops rising at once.

    The chamber warming during the coast is left out, and a warmer chamber
    loses more, so the real crossing arrives sooner than this says. That
    approximation errs long, which for a horizon requirement is the safe
    direction; docs/superpowers/experiments/braking_distance_check.py measures
    how far, against a direct integration of the same grey box.
    """
    flux = float(params["K_Q"]) * float(q_full)
    if flux <= 0.0:
        return math.inf
    t_amb = float(params["T_amb"])
    loss = float(params["h_amb"]) * (t_ref_c - t_amb) + float(params["sigma"]) * (
        (t_ref_c + _KELVIN) ** 4 - (t_amb + _KELVIN) ** 4
    )
    if loss <= 0.0:
        # Nothing at this temperature pulls heat out of the chamber, so it
        # never stops rising on its own. No horizon covers that.
        return math.inf
    ratio = loss / flux
    if ratio >= 1.0:
        # Full fire cannot even hold this temperature, so the chamber is not
        # rising and there is nothing to brake.
        return 0.0
    # n_delay == 0 removes the transport chain outright, leaving theta with
    # nothing to delay -- the same reading `_effective_theta` takes.
    stages = max(int(params["n_delay"]), 0)
    mean = float(params["theta"]) if stages > 0 else 0.0
    if mean <= 0.0:
        return 0.0
    lo, hi = 0.0, mean
    while _chain_survival(hi, stages=stages, mean=mean) > ratio:
        hi *= 2.0
        if hi > 1e9 * mean:
            return math.inf
    for _ in range(_BISECT_STEPS):
        mid = 0.5 * (lo + hi)
        if _chain_survival(mid, stages=stages, mean=mean) > ratio:
            lo = mid
        else:
            hi = mid
    return hi


#: How far above ambient the steady-state search will look before it gives up
#: and calls the asymptote unbounded. Far past any temperature a grill reaches,
#: so it bounds the search rather than the answer.
_STEADY_STATE_CEILING_C = 100000.0


def steady_state_at_full_fire(params, *, q_full=Q_FULL_FIRE):
    """The chamber temperature this model settles at under sustained full fire.

    The asymptote the fitted parameters imply: where the chamber's loss,
    h_amb*(T-T_amb) plus the radiative term, has risen to meet K_Q*q_full. Loss
    is strictly increasing in T above ambient, so there is one such point and
    bisection finds it.

    A cook that never approaches steady state does not determine this, which is
    exactly why it is worth looking at: it is where a fit that has traded the
    chamber's parameters against each other along a direction the log cannot
    see says something visibly absurd. A grill that peaks at 520 F can be fitted
    to imply anything from 1067 F to 5664 F depending on which parameters are
    free.

    It is reported and not enforced. Sound and absurd fits of the same cook sit
    only about 1.6x apart on this quantity and both are far above the hazard
    limit, so a refusal drawn here would be a guess; separating them needs the
    temperature band the cook actually visited, which `evaluate` is not given.
    """
    t_amb = float(params["T_amb"])
    h_amb, sigma = float(params["h_amb"]), float(params["sigma"])
    target = float(params["K_Q"]) * float(q_full)
    if target <= 0.0:
        return t_amb
    if not (h_amb > 0.0 or sigma > 0.0):
        return math.inf

    def loss(t_c):
        return h_amb * (t_c - t_amb) + sigma * ((t_c + _KELVIN) ** 4 - (t_amb + _KELVIN) ** 4)

    lo, hi = t_amb, t_amb + 1.0
    while loss(hi) < target:
        hi = t_amb + (hi - t_amb) * 2.0
        if hi - t_amb > _STEADY_STATE_CEILING_C:
            return math.inf
    for _ in range(_BISECT_STEPS):
        mid = 0.5 * (lo + hi)
        if loss(mid) < target:
            lo = mid
        else:
            hi = mid
    return hi


def longest_braking_distance(params, *, q_full=Q_FULL_FIRE):
    """The braking distance at whichever end of the operating range is worst.

    One horizon has to be adequate everywhere the grill runs, so it is sized
    from the end that takes longest to stop. That is the cool end: the loss
    the decaying flux has to fall below is smallest there, so the flux has
    furthest to fall. Reference points at or below ambient are skipped -- a
    chamber the surroundings are warming is not braking, and the hazard end is
    above any ambient PROMOTION_BOUNDS admits, so at least one point remains.
    """
    t_amb = float(params["T_amb"])
    refs = [t for t in (T_FLOOR_C, T_HAZARD_C) if t > t_amb]
    return max(braking_distance(params, t, q_full=q_full) for t in refs)


def effective_tau(params, t_ref_c):
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
    straight to the chamber -- so theta contributes a delay only when at
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
    if not n_delay_is_whole(n_delay):
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

    # What the horizon has to cover is the braking distance -- how long the
    # chamber goes on rising after the fuel is cut -- not a time constant. The
    # two are not the same size and need not even be the same order: a ramp
    # that never approaches steady state does not determine C_c/h_amb, so a
    # horizon sized from it asks for a length no measurement supports, while
    # the coast after a cut is directly observable in the same log.
    horizon_needed = None
    brake = longest_braking_distance(candidate)
    if not math.isfinite(brake):
        # A chamber this model never predicts will stop rising. No horizon
        # covers that, so it is refused rather than passed with no demand
        # attached -- silence here would read as "the horizon is fine".
        return Verdict(False, "the model does not predict the chamber ever stops rising after a fuel cut")
    if float(n_horizon) * float(t_step) < brake:
        horizon_needed = int(math.ceil(brake / float(t_step)))

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
    for t_ref_c in (T_FLOOR_C, T_HAZARD_C):
        tau_eff_candidate = effective_tau(candidate, t_ref_c)
        tau_eff_incumbent = effective_tau(incumbent, t_ref_c)
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
