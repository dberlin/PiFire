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
#: measured by docs/superpowers/experiments/promotion_signal.py with its output
#: committed as _promotion_signal.txt beside it (Section 9):
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
#: `_REFIT_MIN_SAMPLES`, because mpc.py:634 refuses a shorter refit before
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

#: How far past the operator's configured horizon a fitted model may push it,
#: in SECONDS of coast. This bounds the RAISE, not the model and not the
#: setting: a configured horizon that already spans a long coast is left alone,
#: because a controller that can see the end of a brake can plan it whatever
#: the number. What this refuses is a model that would demand more foresight
#: than the operator asked for AND more than a pellet grill's brake can
#: plausibly need.
#:
#: Seconds rather than steps, because a step count says nothing on its own --
#: 96 steps is 2400 s at the shipped t_step and 96 s at controllers.json's
#: minimum of 1 s. The step count follows from it, rounded DOWN so the horizon
#: built never reaches past the bound it is named for.
#:
#: What bounds the number is the coast a pellet grill can physically have, not
#: the cost of the solve. Two coasts have been measured here: the shipped
#: default model's is 150 s, and controller/update_mpc.py's fit to the real MAK
#: cook (tests/unit/mpc/fixtures/mak_cook_2026-08-02.csv, recorded as
#: REAL_MAK_FIT in tests/unit/mpc/test_model_promotion.py) reads 367 s. 2400 s
#: is 16x the first and 6.5x the second. A demand to plan 40 minutes past what
#: the operator configured is a fit that has run away along a direction the
#: cook did not determine.
#:
#: Compute does not set it, because at this length compute is not scarce. At
#: the shipped t_step = 25 s the bound is 96 steps, and the worst of 15 warm
#: solves there is 91 ms at the shipped n_delay = 8 and 169 ms at
#: controllers.json's largest selectable n_delay = 12. The period those have to
#: fit inside is control_period -- how often the runtime loop calls update(),
#: which solves on every call -- and NOT t_step, which only spaces the horizon's
#: steps. Against the shipped control_period = 5 s that is 1.8 % and 3.4 %.
#: The n_delay = 12 row is the one that has to hold: a bound a shipped setting
#: can step outside is not a bound. Those are x86 Core Ultra readings and
#: PiFire's nominal target is a Raspberry Pi 5; at an assumed 6x slowdown they
#: are 11 % and 20 % of the shipped period. The measurement is
#: docs/superpowers/experiments/horizon_solve_cost.py and its committed output
#: is _horizon_solve_cost.txt beside it.
#:
#: Neither direction is free. Too high adopts a model whose brake the
#: controller only appears to plan around. Too low refuses a model the gate has
#: just judged the better description of this grill, and the incumbent left
#: running may size the same physical coast no better.
_HORIZON_CAP_S = 2400.0

#: How many prediction steps a fitted model's coast may add to the build, on
#: top of whatever the operator configured. Like _HORIZON_CAP_S it holds down
#: the RAISE and not the setting: an operator who configures 200 steps gets 200,
#: because a horizon somebody asked for is not this constant's business. What it
#: bounds is how much larger a learned model may make the NLP than the length
#: the grill was set up with.
#:
#: It exists because _HORIZON_CAP_S converts to steps through t_step, and t_step
#: is settings-reachable down to 1 s: at that setting even the real MAK cook's
#: ordinary 367 s coast would ask for 367 steps, and a capped model for 2400.
#:
#: 96 is the largest step count this project has a committed warm-solve
#: measurement for, and the largest measured at BOTH selectable chain lengths
#: (_horizon_solve_cost.txt: n_delay = 12, n_horizon = 96 -> 169 ms worst).
#: Past it, headroom stops being a measured quantity and becomes an
#: extrapolation up a curve that is already accelerating. It is derived against
#: the shipped control_period = 5 s, where that worst solve is 3.4 % measured
#: and 20 % at the assumed 6x Pi 5 slowdown. At controllers.json's minimum
#: control_period = 1 s the same solve exceeds the period under that assumption
#: -- which costs a cadence, not control: the runtime loop calls update() again
#: when the solve returns, so the controller simply re-solves less often than
#: configured, against a plant whose own time constants are minutes.
#:
#: It coincides with the step count _HORIZON_CAP_S already yields at the
#: shipped t_step, so nothing about the shipped configuration turns on it.
#:
#: This bound belongs to the BUILD alone and must never reach `evaluate`. 96
#: steps at t_step = 1 covers 96 s, short of even the shipped default model's
#: own 150 s coast, so refusing on it would reject every model at a fine t_step
#: and blame the model for an operator's setting. Where it truncates the
#: horizon, controller/mpc.py's `_warn_about_model` says so and names t_step.
_HORIZON_CAP_STEPS = 96


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


def _model_coast(params, t_ref_c, *, q_full=Q_FULL_FIRE):
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


def braking_distance(params, t_ref_c, *, q_full=Q_FULL_FIRE):
    """Seconds a REAL GRILL at `t_ref_c` keeps rising after full fire is cut.

    This is what a prediction horizon has to cover: unless the horizon reaches
    past this, no plan the controller can make ends with the chamber having
    stopped, and the overshoot it is trying to avoid happens outside anything
    it can see. It is a necessary length, not a sufficient one.

    A bound rather than a best estimate, because the horizon requirement it
    feeds has to fail closed. `_model_coast` reads the fitted model exactly, and
    the fitted model is shorter than the grill it describes -- so the reading
    faithful to the model lands under a real coast, and `_COAST_BOUND` is what
    closes that gap.

    The zero and infinite branches of `_model_coast` are already bounds --
    nothing to brake, and no horizon suffices -- and scaling them changes
    neither, so this applies at one point and every caller inherits it.
    """
    return _COAST_BOUND * _model_coast(params, t_ref_c, q_full=q_full)


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


def effective_n_horizon(params, *, n_horizon, t_step):
    """How many prediction steps a controller planning with `params` asks for.

    The configured `n_horizon` is a floor, not the answer. A chamber that goes
    on rising past the end of the horizon leaves the end of its own brake out
    of view, so the horizon is raised to cover `longest_braking_distance`, and
    the raise stops at the `_HORIZON_CAP_S` seconds a demand may reach past the
    configured length. A model needing less than the operator configured lowers
    nothing: the setting is a floor in both senses.

    This is the demand, not the length built. `built_n_horizon` applies the
    separate bound on how large an NLP this controller will assemble; the two
    part company only where `t_step` is short. `evaluate` reads THIS one, so
    what it refuses a model for is always the model's own coast and never the
    step count an operator's `t_step` happens to turn that coast into.

    Derived on every build rather than written back into the configuration, so
    the horizon tracks the current model in BOTH directions -- a later, quicker
    model brings it down again -- and the stored `n_horizon` goes on meaning
    what the operator set. A stored value could only ratchet upwards.

    A model that never predicts the chamber stops rising asks for the cap: no
    horizon satisfies it, and the cap is the furthest a demand may reach.
    `evaluate` refuses such a model outright, so this is the reading for one
    that arrives in a configuration instead of through the gate.
    """
    steps = int(n_horizon)
    step_s = float(t_step)
    if not (step_s > 0.0 and math.isfinite(step_s)):
        return steps
    # Rounded down: a step count rounded up would plan up to one step past
    # _HORIZON_CAP_S, and the reason strings below name that bound exactly.
    cap = int(_HORIZON_CAP_S // step_s)
    brake = longest_braking_distance(params)
    if math.isfinite(brake):
        needed = int(math.ceil(brake / step_s))
    elif brake == math.inf:
        needed = cap
    else:
        needed = 0  # nothing was computed, so nothing is being asked for
    return max(steps, min(needed, cap))


def built_n_horizon(params, *, n_horizon, t_step):
    """How many prediction steps the NLP is actually assembled with.

    `effective_n_horizon` is what the model's coast asks for; this is what gets
    built, which is that demand additionally held to `_HORIZON_CAP_STEPS`. The
    two agree at every shipped setting and part company only where `t_step` is
    fine enough that covering a believable coast would take more steps than
    this project has measured a solve for.

    The shortfall that opens there is a property of the configuration rather
    than of the model, which is why it stops here instead of reaching
    `evaluate`, and why controller/mpc.py's `_warn_about_model` reports it. The
    operator's own `n_horizon` is never lowered by this: like the seconds
    bound, it holds down the raise and not the setting.
    """
    return max(int(n_horizon), min(effective_n_horizon(params, n_horizon=n_horizon, t_step=t_step), _HORIZON_CAP_STEPS))


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


def evaluate(candidate, incumbent, *, candidate_rmse, incumbent_rmse, identifiability, n_horizon, t_step):
    """Whether `candidate` may replace `incumbent`, and what horizon it needs.

    `identifiability` is how well the record this candidate was fitted to pins
    the model down, in the units `_IDENTIFIABILITY_FLOOR` documents --
    controller/update_mpc.identifiability computes it. It is required rather
    than optional: a caller that has not measured it has not shown its cook
    determined anything, and a default would let that caller through silently.
    """
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
    # determined anything. The verdict carries no horizon demand, deliberately
    # -- a model that is not adopted asks nothing of the grill.
    ident = _finite(identifiability)
    if ident is None or ident < _IDENTIFIABILITY_FLOOR:
        shown = "unmeasurable" if ident is None else f"{ident:.3g} C per e-fold"
        return Verdict(
            False,
            f"the cook does not determine the model (identifiability {shown}, floor {_IDENTIFIABILITY_FLOOR:.3g})",
        )

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
    # The seconds bound is the one thing here that refuses a model for its
    # horizon. A demand under it is met by building a longer horizon -- the
    # extra steps cost single-digit percent of the 5 s control_period the solve
    # actually has to fit inside -- so refusing there would keep a model the
    # comparison below has just called worse, over a coast the grill has
    # whatever the verdict says. Past it the demand is no longer a description
    # of a pellet grill's brake, so it must not be planned with.
    #
    # Read from `effective_n_horizon` and never `built_n_horizon`: the step
    # bound the build applies is a fact about t_step, and a model must not be
    # refused for what an operator's discretization turns its coast into.
    covered = effective_n_horizon(candidate, n_horizon=n_horizon, t_step=t_step) * float(t_step)
    if covered < brake:
        return Verdict(
            False,
            f"the chamber keeps rising for {brake:.0f} s after a fuel cut, past the {covered:.0f} s "
            f"this controller plans over under a {_HORIZON_CAP_S:.0f} s horizon cap",
        )
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
