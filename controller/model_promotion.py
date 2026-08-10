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
from enum import StrEnum

from controller.mpc_model import steady_combustion_load, steady_temperature

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

#: How much a cook must determine the model before its fit may drive a grill.
#:
#: The unit is degrees C RMS per e-fold of the least-constrained direction in
#: (log K_Q, log C_c, log theta) -- controller/update_mpc.identifiability, which
#: is what supplies the value judged against this. A record scoring below this
#: leaves some combination of the three free to move by a factor of e without
#: the prediction moving, so what comes out of the solve is the starting point
#: rather than the grill. Every other test in this file asks whether a model is
#: good; this one asks whether the cook said anything, and no error statistic
#: can answer that -- an in-sample RMSE reaches 0.00 on a record that determines
#: nothing at all.
#:
#: The interval this sits inside is bounded at both ends by real records, each
#: measured by tools/experiments/promotion_signal.py with its output committed
#: as docs/superpowers/experiments/_promotion_signal.txt (Section 9):
#:
#:     lower  0.261203   generic/steady_hold/3600s, n=721 -- the strongest
#:                       record that still determines nothing, so the floor must
#:                       sit above it
#:     upper  1.098188   the real MAK cook truncated to 600 s, n=120 -- the
#:                       weakest record that must still be KEPT, so the floor
#:                       must sit below it
#:
#: Both ends are scales, so the distance between them is a ratio and the point
#: between them is their geometric midpoint, sqrt(0.261203 * 1.098188) =
#: 0.535584, truncated DOWNWARD to one decimal. That leaves 1.91x of margin
#: above the lower bound and 2.20x below the upper, and the truncation goes down
#: because the two costs are not symmetric: too low admits a record near the
#: uninformative ceiling, while too high refuses real cooks and the learning
#: this gate exists to allow never happens at all.
#:
#: Not the bare lower bound, which the interval alone would permit. At 0.261203
#: the shipped model still adopts a candidate 200.5 C worse than the incumbent
#: on the truth probe, and a calibrated one 223.7 C worse; at 0.50 the worst
#: accepted candidate is no worse than the incumbent on the shipped arm and
#: 2.56 C worse on the calibrated arm. Six of 102 acceptances buy that.
#:
#: Both bounds are drawn only from records of at least controller/mpc.py's
#: `_REFIT_MIN_SAMPLES`, because its `refit_from_cook` refuses a shorter refit before
#: `evaluate` is reached -- a bound set by a record this gate cannot be shown is
#: not a bound on anything, and the shorter records are the extremes that would
#: otherwise set one.
_IDENTIFIABILITY_FLOOR = 0.50

#: Incumbent fields the shrink comparison needs. A partial incumbent missing
#: one of these cannot be judged, so it is refused rather than raising.
_INCUMBENT_KEYS = ("C_c", "h_amb", "theta", "n_delay", "sigma")

#: Kelvin offset for the Celsius T_c/T_amb this model works in, matching
#: controller/mpc_model.py's own constant.
_KELVIN = 273.15

#: A full combustion command is the normalized scalar 1.0. It appears here
#: because the braking distance depends on heat in flight when fuel is cut.
NORMALIZED_FULL_LOAD = 1.0


class ReachabilityState(StrEnum):
    REACHABLE = "reachable"
    UNREACHABLE_HIGH = "unreachable_high"
    UNKNOWN_MODEL = "unknown_model"


@dataclass(frozen=True, slots=True)
class FeasibilityReport:
    state: ReachabilityState
    target_temperature: float
    required_load: float | None
    maximum_authority: float
    predicted_steady_temperature: float | None
    predicted_steady_load: float | None
    model_revision: int | None
    model_provenance: str | None
    binding_reason: str | None

    def as_status(self) -> dict[str, float | int | str | None]:
        return {
            "state": self.state.value,
            "target_temperature": self.target_temperature,
            "required_load": self.required_load,
            "maximum_authority": self.maximum_authority,
            "predicted_steady_temperature": self.predicted_steady_temperature,
            "predicted_steady_load": self.predicted_steady_load,
            "model_revision": self.model_revision,
            "model_provenance": self.model_provenance,
            "binding_reason": self.binding_reason,
        }


_REACHABILITY_TOLERANCE = 1e-6


def feasibility_report(params, target_temperature, *, disturbance=0.0, model_revision=None, model_provenance=None):
    """Report only the model-supported upper authority limit for a target."""
    target = float(target_temperature)
    if not math.isfinite(target):
        raise ValueError("reachability target must be finite")
    identified = (
        params is not None
        and isinstance(model_revision, int)
        and model_revision >= 0
        and isinstance(model_provenance, str)
        and bool(model_provenance.strip())
    )
    if not identified:
        return FeasibilityReport(
            ReachabilityState.UNKNOWN_MODEL,
            target,
            None,
            NORMALIZED_FULL_LOAD,
            None,
            None,
            None,
            None,
            "unidentified_model",
        )
    required_load = steady_combustion_load(params, target, disturbance)
    maximum_temperature = steady_temperature(params, NORMALIZED_FULL_LOAD, disturbance)
    state = (
        ReachabilityState.UNREACHABLE_HIGH
        if required_load > NORMALIZED_FULL_LOAD + _REACHABILITY_TOLERANCE
        else ReachabilityState.REACHABLE
    )
    return FeasibilityReport(
        state,
        target,
        required_load,
        NORMALIZED_FULL_LOAD,
        maximum_temperature,
        NORMALIZED_FULL_LOAD,
        model_revision,
        model_provenance,
        "maximum_authority" if state is ReachabilityState.UNREACHABLE_HIGH else None,
    )


#: Bisection steps used to invert the lag chain's survival. The bracket halves
#: each step, so this resolves the answer to about a part in 10**15 of it --
#: past the point where the estimate's own approximations matter, and cheap
#: enough that the exactness costs nothing.
_BISECT_STEPS = 60

#: What makes `braking_distance` a bound rather than a best estimate.
#:
#: The closed form reads the fitted model's own transport chain exactly, and a
#: fitted chain is SHORTER than the grill it describes: the Erlang
#: approximation recovers about 0.71x of the reference plant's real dead time
#: at the shipped n_delay, so an estimate faithful to the model under-states a
#: real coast. Measured against both plants in controller/grill_sim.py -- each
#: fitted as a calibration would fit it, then cut at full fire across the
#: operating range -- the worst recovery depends on what the fan does during
#: the coast, because a fan left running dumps heat and stops the chamber
#: sooner:
#:
#:     coast fan 1.00   worst recovery 1.033   bound needed 0.968
#:     coast fan 0.40   worst recovery 0.881   bound needed 1.135
#:     coast fan 0.00   worst recovery 0.691   bound needed 1.446
#:
#: 1.45 is set by the last row, not the middle one: 0.40 is the DEFAULT
#: fan_min_pct in controller/controllers.json, but that field's own minimum is
#: 0, so a coast with the fan at rest is a configuration an operator can
#: select, and a bound that a shipped setting can step outside is not a bound.
#: The 0.40 row is what a configuration-dependent factor could use instead, if
#: the fan floor is ever threaded through to here.
#:
#: The measurement is docs/superpowers/experiments/braking_bound.py, and its
#: committed output is _braking_bound.txt beside it. That script reads
#: `_model_coast` deliberately: run against the public function it would be
#: measuring this factor against itself.
#:
#: Inflating is the fail-safe direction: a longer reading makes the promotion
#: gate refuse MORE models and `_warn_about_model` speak EARLIER, and no path
#: exists by which it drives the fire harder.
#:
#: This is a bound over a modelling shortfall, not a constant of nature. It
#: exists to be REMOVED when the model's dead-time recovery improves -- raise
#: n_delay far enough, or give the chain a structure that transports rather
#: than smears, and the measurement above is what says how much of it is still
#: needed.
_COAST_BOUND = 1.45

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
    """The promotion decision and its fit-quality reason."""

    accepted: bool
    reason: str


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


def _model_coast(params, t_ref_c, *, q_full=NORMALIZED_FULL_LOAD):
    """Seconds the FITTED MODEL keeps rising after full fire is cut.

    The model's own reading, faithful to it and nothing more. `braking_distance`
    is what callers want: this under-states a real grill, for the reason stated
    there. Kept separate so the arithmetic below can be checked against the
    closed form the Erlang chain has, without the bound in the way.

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
    approximation errs long, preserving a conservative coast estimate;
    docs/superpowers/experiments/braking_distance_check.py measures its error
    against a direct integration of the same grey box.
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
        # never stops rising on its own.
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


def braking_distance(params, t_ref_c, *, q_full=NORMALIZED_FULL_LOAD):
    """Bound a real grill's post-cut coast at `t_ref_c`.

    `_model_coast` reads the fitted model exactly. The fitted model is shorter
    than the grill it describes, so `_COAST_BOUND` widens that reading. Zero
    and infinity already encode their own bounds and remain unchanged.
    """
    return _COAST_BOUND * _model_coast(params, t_ref_c, q_full=q_full)


#: How far above ambient the steady-state search will look before it gives up
#: and calls the asymptote unbounded. Far past any temperature a grill reaches,
#: so it bounds the search rather than the answer.
_STEADY_STATE_CEILING_C = 100000.0


def steady_state_at_full_fire(params, *, q_full=NORMALIZED_FULL_LOAD):
    """The chamber temperature this model settles at under sustained full fire."""
    try:
        return steady_temperature(params, q_full)
    except ValueError:
        t_amb = float(params["T_amb"])
        target = float(params["K_Q"]) * float(q_full)
        if target <= 0.0:
            return t_amb
        return math.inf


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


def evaluate(candidate, incumbent, *, candidate_rmse, incumbent_rmse, identifiability):
    """Whether fit quality permits `candidate` to replace `incumbent`."""
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

    # Ahead of everything below, and ahead of the no-incumbent shortcut, because
    # a cook that did not determine the model has produced no candidate worth
    # comparing to anything -- including nothing. The first fit on an
    # undetermined record is the exact case this closes, so it must not reach
    # the "no incumbent" acceptance. A value that could not be computed is
    # refused for the same reason a low one is: neither shows the record
    # determined anything.
    ident = _finite(identifiability)
    if ident is None or ident < _IDENTIFIABILITY_FLOOR:
        shown = "unmeasurable" if ident is None else f"{ident:.3g} C per e-fold"
        return Verdict(
            False,
            f"the cook does not determine the model (identifiability {shown}, floor {_IDENTIFIABILITY_FLOOR:.3g})",
        )

    if incumbent is None:
        return Verdict(True, "no incumbent")

    if incumbent_rmse is None:
        return Verdict(False, "incumbent RMSE is not recorded; cannot compare")

    if _finite(incumbent_rmse) is None or float(incumbent_rmse) <= 0:
        return Verdict(False, "incumbent RMSE must be a positive, finite number")

    for key in _INCUMBENT_KEYS:
        if _finite(incumbent.get(key)) is None:
            return Verdict(False, "incumbent model is missing required parameters")

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
        )
    return Verdict(True, "better fit on the same data")
