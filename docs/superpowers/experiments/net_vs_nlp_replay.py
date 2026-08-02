#!/usr/bin/env python3
"""Pointwise disagreement between the MPC net policy and the NLP it approximates.

Two closed-loop runs diverge on their own and cannot settle whether the net is
still the same policy. This runs the loop ONCE under the NLP, logs every
(x_hat, u_prev, set_point_c) the policy was asked about, and replays those exact
triples through the net. The difference is the approximation error on the states
the controller actually visits -- including the lid-open interval, which no
training episode contains.

Three kinds of figures are reported, and they answer different questions:

  * Excursion counts -- how often the net's UN-clipped answer falls outside
    [Q_min, Q_max], and by how much. This is the PRIMARY acceptance quantity:
    it is exactly zero when the net stays in distribution, and the pre-fix
    regression showed it moving sharply (-63..-38) when it does not -- but
    that describes only the catastrophic direction. A marginal case lives in
    the fractional range right at the boundary, which a bare count cannot
    distinguish from comfortable margin (measured: the lid-window count is
    0/24 on every seed, but the closest approach to Q_min is +0.468 out of a
    95-wide box). "margin_min_to_q_min"/"margin_min_to_q_max" are reported
    UNCONDITIONALLY, whole-run and lid-window, for exactly this reason -- a
    count of zero can still be a hair's breadth from one.
  * "_raw" RMS/max -- the net's un-clipped answer against the NLP's un-clipped
    answer. Clamping both sides before comparing (see "_clamped" below) hides
    the very failure mode this script exists to catch, so raw is the
    SECONDARY quantity a later comparison should read, not the clamped one.
  * "_clamped" RMS/max -- both sides forced into [Q_min, Q_max] first, i.e.
    what the plant would actually feel if either policy's answer were
    applied. Reported for completeness, not for the acceptance question.

The "_all" raw/clamped figures -- and the excursion counts -- are further
split into "_whole" (every solve) and "_warm" (excluding the startup
transient). The plant starts at ambient and
takes real time to approach the set point; every solve before it gets there
sees a state no calibrated controller would ever revisit, and folding that
transient into a single RMS inflates it enough to swamp the number a later
task should be comparing against (measured: whole-run rms_all_raw ~1.58 versus
~1.44 once the transient is excluded). The warm cutoff is not a hard-coded
minute count: it is the first simulated second at which the plant lands within
SETTLE_TOL_F of the set point and never leaves that band again before the
lid-open window -- i.e. the same "has it settled" test controller_matrix.py
already uses for its own settle_s metric, applied here to find where the
transient ends instead of whether it ends. The lid-open window is always well
past this point (its own onset is a controller/plan-defined disturbance, not a
symptom of startup), so lid-window figures are not split into whole/warm.

Two knobs select which experiment arm a run measures, and both are recorded
into every output row so a later comparison can tell arms apart.

`lid_model` chooses how the lid-open window is modelled:

  * "faithful" (default) reproduces controller/runtime/modes/hold.py. At the
    detection instant hold.py:243-265 turns the auger off, reports a single
    AppliedOutput(ratio=0.0), and resets the cycle timer; that block cannot
    re-fire during the pause because it clears target_temp_achieved
    (hold.py:266), which only re-arms once the plant is back at set point
    (hold.py:234). For the remainder of the pause hold.py:171-173 pins the
    commanded ratio to u_min, hold.py:206-217 reports that u_min once per
    control period, and hold.py:228 calls _auger_cycle_tick unconditionally --
    base.py:118-147 has no lid gate, so the auger keeps cycling at u_min. The
    fan is off throughout (hold.py:263 at detection, hold.py:271 at clear).
  * "stress" holds ratio at 0.0 for the entire pause and keeps the auger fully
    off. No production path does this; it drives the sub-u_min inverse far
    harder and far longer than a real grill can, and is kept as a deliberate
    upper bound on the lid-window disagreement, not as the measurement.

`estimator_input` chooses what the estimator's transport-lag chain is fed:

  * "applied" (default) reports every applied duty through set_output, so
    _policy_u_prev derives from what reached the auger.
  * "command" withholds those reports entirely. update() overwrites _applied_Q
    with each freshly computed command, so with nothing else writing it,
    _policy_u_prev derives from the command -- reproducing the pre-Task-13
    behavior on a post-Task-13 checkout. This exists so both arms can be run
    under the SAME lid model; see the comparability note below.

COMPARABILITY: the stored baseline was captured under lid_model="stress", so a
lid_model="faithful" run is NOT directly comparable to it -- the lid model
changes the plant trajectory (auger off for 120s versus cycling at u_min), not
just the estimator's input, so the two would differ in the lid window on one
side only. Compare arms within a single lid model. The stored baseline's role
is to validate that estimator_input="command" + lid_model="stress" reproduces
it; once that holds, the "command" arm is a trustworthy reference under either
lid model.

Set applied_q_split_expected=True (or --applied-q-split-expected on the CLI)
for the after-Task-13 run. The flag is checked against actual behavior, not
attribute presence: hasattr(core, "_applied_Q") is true on any checkout at
or after the task that added the field to __init__, which predates the
behavioral split by one task and so cannot tell a live split from
ControllerBase's set_output no-op leaving _applied_Q frozen at Q_min. See
_split_is_live below. The flag's value is recorded into the output JSON so a
later comparison knows which mode produced each row. Do not re-capture a
before-run: the stored baseline (_net_vs_nlp_baseline.json) predates both
_applied_Q and set_output entirely, so it remains a valid "estimator reads
the command" reference point for every after-run to compare against. The
provenance pin on core._last_Q in replay() does not depend on this flag and
must hold in either mode.
"""

import argparse
import json
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from controller.applied_output import AppliedOutput, OutputSource  # noqa: E402
from controller.grill_sim import GrillSim  # noqa: E402
from controller.mpc import Controller  # noqa: E402
from controller.mpc_net import NetPolicy, net_path_for  # noqa: E402
from controller_matrix import _auger_toggle_tick  # noqa: E402

OUT = os.path.join(_ROOT, "docs/superpowers/experiments/_net_vs_nlp_baseline.json")
ARTIFACT = os.path.join(_ROOT, "controller/mpc_policy_net.npz")

CYCLE_DATA = {"HoldCycleTime": 20, "u_min": 0.15, "u_max": 0.9}

# A run that has drifted this far from set point over its last simulated hour
# is not a baseline of controlled behavior -- it is a runaway (a prior version
# of this script re-anchored the duty cycle every solve, pinned realized duty
# near 60% regardless of demand, and produced a run that settled at 511F
# against a 225F target while looking numerically unremarkable). 20F is loose
# enough to tolerate the lid-open dip/recovery inside that final hour.
REGIME_TOL_F = 20.0
# "Settled" band used both for the regime's realized-vs-commanded-duty check
# and for locating the end of the startup transient below. Matches the
# +/-5F band controller_matrix.py already uses for its settle_s metric.
SETTLE_TOL_F = 5.0
# How far realized mean duty (what the plant actually received, via
# _auger_toggle_tick) may drift from commanded mean duty (what the policy,
# clipped to [u_min, u_max], asked for) before this is treated as a duty-cycle
# realization bug rather than the plant sitting at a floor/ceiling. This is
# the direct negative control for the actual C1 defect: the broken re-anchor
# put realized duty at 0.596 against a commanded ~0.17 -- 200%+ off, not a
# rounding difference.
DUTY_DIVERGENCE_TOL = 0.05


def _c_to_f(c):
    return c * 9.0 / 5.0 + 32.0


def _rms(a):
    return float(np.sqrt((a**2).mean()))


def _split_is_live(cycle_data):
    """Task 13's _last_Q/_applied_Q split, detected by behavior, not presence.

    hasattr(core, "_applied_Q") is true on any checkout at or after Task 12,
    which only added the attribute to __init__ -- not the behavior. The thing
    this flag actually needs to discriminate is whether a report the
    controller did not command can move _applied_Q off the command; a probe
    that only checks presence would let a mis-targeted after-run silently
    reproduce the baseline and read as "no change" instead of failing loudly.
    """
    probe = Controller({"policy": "nlp"}, "F", dict(cycle_data))
    before = getattr(probe, "_applied_Q", None)
    probe.set_output(AppliedOutput(0.0, OutputSource.LID_OPEN, 0.0))
    return before is not None and getattr(probe, "_applied_Q", None) != before


def replay(
    seed=0,
    duration_s=3 * 3600,
    lid_open_at=2 * 3600,
    lid_open_for=120,
    setpoint_f=225.0,
    applied_q_split_expected=False,
    lid_model="faithful",
    estimator_input="applied",
):
    if lid_model not in ("faithful", "stress"):
        raise ValueError(f"lid_model must be 'faithful' or 'stress', got {lid_model!r}")
    if estimator_input not in ("applied", "command"):
        raise ValueError(f"estimator_input must be 'applied' or 'command', got {estimator_input!r}")
    core = Controller({"policy": "nlp"}, "F", CYCLE_DATA)
    assert core._net is None, "configure policy=nlp; the point is to log the NLP's answers"
    # which side of the Task 13 landing we're on is a caller-supplied flag
    # (recorded below into the output row), not a hard-coded assumption --
    # checked by behavior (_split_is_live), not by attribute presence, since
    # presence alone cannot tell a live split from ControllerBase's
    # set_output no-op (see _split_is_live's docstring). This does not check
    # that `_last_Q` still means what this harness thinks it means; that is
    # the provenance pin further down, in the per-solve loop.
    split_live = _split_is_live(CYCLE_DATA)
    assert split_live == applied_q_split_expected, (
        f"applied_q_split_expected={applied_q_split_expected} does not match "
        "this checkout's actual behavior. A bare hasattr(core, '_applied_Q') "
        "check would pass here even against a pre-Task-13 checkout -- Task 12 "
        "added the attribute a task before the behavior existed -- and a "
        "mis-targeted after-run would then silently reproduce the baseline "
        "instead of failing loudly. Fix the flag, not this assertion."
    )
    # The flag above describes the CHECKOUT; estimator_input describes the ARM.
    # An "applied" arm on a checkout where set_output cannot move _applied_Q
    # would silently be a "command" arm wearing the other label -- the same
    # failure the flag exists to prevent, one level up.
    assert estimator_input == "command" or split_live, (
        "estimator_input='applied' on a checkout where set_output cannot move "
        "_applied_Q -- this arm would silently reproduce the 'command' arm and "
        "the two would read as agreeing when they were never distinguished."
    )
    core.set_target(setpoint_f)
    plant = GrillSim(seed=seed)
    period = core.get_control_period()
    cycle_time = CYCLE_DATA["HoldCycleTime"]

    net = NetPolicy.load(net_path_for(ARTIFACT, bool(core.cfg["enable_fan_input"])))
    assert core.cfg["Q_min"] == net.calib["Q_min"] and core.cfg["Q_max"] == net.calib["Q_max"], (
        "controller cfg and the net artifact's calibration disagree on "
        "[Q_min, Q_max] -- the two policies would be clipped to different "
        "ranges and the disagreement figures below would not be comparable."
    )
    # Enforced, not just assumed in a comment: this cfg's fan_frac=1.0 (fixed
    # duty, below) and net_path_for()'s artifact selection (just above) both
    # depend on enable_fan_input staying False. If it flips, both need
    # revisiting, not just whichever one happens to be read next.
    assert not core.cfg["enable_fan_input"], (
        "enable_fan_input=True in this cfg -- the fixed fan_frac=1.0 below and "
        "the artifact net_path_for() just selected both assumed it was False."
    )

    triples, q_nlp_clamped, q_nlp_raw, solve_failed, in_lid, sample_t = [], [], [], [], [], []
    temps = []
    # `realized_duty` is the auger's actual on-fraction each second, via
    # _auger_toggle_tick -- NOT what controller_matrix.py calls `duties`
    # there, which records the *requested* ratio rather than what the plant
    # received. `commanded_duty` here is that requested-ratio quantity, kept
    # so the two can be compared directly below.
    realized_duty, commanded_duty = [], []
    # enable_fan_input is False (asserted above), so the allocator never
    # returns a fan duty; the fan runs wide open for the whole simulation.
    fan_frac = 1.0
    ratio, next_solve = core.u_min, 0.0
    auger_on, auger_toggle = False, 0.0
    for t in range(duration_s):
        lid = lid_open_at <= t < lid_open_at + lid_open_for
        temp_f = _c_to_f(plant.measured())
        if t >= next_solve:
            next_solve = t + period
            # Captured before update() runs: update() derives _policy_u_prev
            # from _applied_Q at entry and then overwrites _applied_Q with the
            # freshly computed command, so reading _applied_Q back out after
            # the call is one control step stale relative to what
            # _policy_u_prev was actually computed from.
            applied_before_update = float(core._applied_Q)
            raw = core.update(temp_f)
            # these are recorded ON the controller during the update() call
            # just made, not read back out of state it mutates afterward
            triples.append(
                (np.asarray(core._x_hat).reshape(-1).copy(), float(core._policy_u_prev), float(core._set_point_c))
            )
            q_nlp_clamped.append(float(core._last_Q))
            q_nlp_raw.append(float(core._last_Q_raw))
            solve_failed.append(bool(core._last_solve_failed))
            in_lid.append(lid)
            sample_t.append(t)
            # Semantic pin for `core._last_Q`: PROVENANCE, not consistency.
            # Comparing allocate(core._last_Q) against raw["cycle_ratio"]
            # (a prior version of this check) derives both sides from the
            # same in-memory `_last_Q` in the same call, so a redefinition
            # shaped like `_last_Q := applied` with `cycle_ratio :=
            # allocate(applied)` stays internally consistent and slides
            # through undetected -- verified: that shape survives 40 solves
            # against the old check. Tying `_last_Q` to `_last_Q_raw` (the
            # value recorded before update() clips it) catches it instead,
            # because it checks where `_last_Q` came from, not just what it
            # agrees with -- verified to fail at solve #0 under that
            # redefinition, and under `_last_Q := 0.0` / `_last_Q :=
            # 0.5*_last_Q`.
            assert core._last_Q == float(np.clip(core._last_Q_raw, core.cfg["Q_min"], core.cfg["Q_max"])), (
                "core._last_Q is no longer clip(core._last_Q_raw, Q_min, Q_max) "
                "-- its provenance has changed and every read of it in this "
                "harness needs re-auditing before this baseline can be trusted "
                "again."
            )
            # Sibling pin for `core._policy_u_prev`, recorded in `triples` above
            # as "the u_prev the policy was asked about": Task 13 redefined it
            # from `float(core._last_Q)` to `clip(core._applied_Q, Q_min,
            # Q_max)`. mpc.py still passes exactly this value to firing_rate(),
            # so the harness measures what it claims -- pinned here the same
            # way `_last_Q` is, so a future redefinition fails loudly instead
            # of sliding through unnoticed. Compared against the value
            # `_applied_Q` held BEFORE this update() call (see
            # applied_before_update above): update() derives _policy_u_prev
            # from _applied_Q at entry, then overwrites _applied_Q with the
            # new command before this line runs, so comparing against the
            # post-call value is one control step off by construction (fails
            # every solve, not just when the split actually breaks).
            assert core._policy_u_prev == float(np.clip(applied_before_update, core.cfg["Q_min"], core.cfg["Q_max"])), (
                "core._policy_u_prev is no longer clip(_applied_Q-at-entry, "
                "Q_min, Q_max) -- its provenance has changed and every read of "
                "it in this harness needs re-auditing before this baseline can "
                "be trusted again."
            )
            ratio = min(max(float(raw["cycle_ratio"]), core.u_min), core.u_max)
            # hold.py:171-173 replaces the controller's answer with u_min for
            # the whole pause, before the u_min floor and u_max ceiling below
            # it can apply. The stress model leaves the controller's answer in
            # place and reports 0.0 for it instead.
            if lid and lid_model == "faithful":
                ratio = core.u_min
            if estimator_input == "applied":
                core.set_output(
                    AppliedOutput(
                        ratio=0.0 if (lid and lid_model == "stress") else ratio,
                        source=OutputSource.LID_OPEN if lid else OutputSource.CONTROLLER,
                        timestamp=float(t),
                    )
                )
        if lid and lid_model == "stress":
            auger_on, auger_toggle, auger_frac = False, t, 0.0
        elif lid and t == lid_open_at:
            # The harness imposes the pause rather than detecting it, so its
            # first second stands in for hold.py's detection instant: auger
            # off, cycle timer reset, and one AppliedOutput(0.0) report
            # (hold.py:247-264). That block clears target_temp_achieved
            # (hold.py:266) and so cannot fire again until the plant is back at
            # set point (hold.py:234) -- hence exactly one 0.0, not a stream.
            if estimator_input == "applied":
                core.set_output(AppliedOutput(ratio=0.0, source=OutputSource.LID_OPEN, timestamp=float(t)))
            auger_on, auger_toggle, auger_frac = False, t, 0.0
        else:
            # hold.py:228 calls _auger_cycle_tick unconditionally and
            # base.py:118-147 has no lid gate, so through the rest of the pause
            # the auger keeps cycling at the pinned u_min.
            auger_on, auger_toggle, auger_frac = _auger_toggle_tick(auger_on, auger_toggle, t, ratio, cycle_time)
        # The fan is off for the whole pause under either model: hold.py:263
        # cuts it at detection and hold.py:271 only restarts it at clear.
        plant.step(auger_on=auger_frac, fan_frac=0.0 if lid else fan_frac)
        temps.append(temp_f)
        realized_duty.append(auger_frac)
        commanded_duty.append(0.0 if (lid and lid_model == "stress") else ratio)

    n_failed = int(sum(solve_failed))
    if n_failed:
        raise RuntimeError(
            f"seed {seed}: {n_failed} NLP solve(s) fell back to the held-over Q "
            "(controller/mpc.py's except-branch) -- q_nlp_raw for those steps is "
            "not a fresh NLP answer, and this baseline would be measuring the "
            "wrong thing."
        )

    temps = np.asarray(temps)
    realized_duty = np.asarray(realized_duty)
    commanded_duty = np.asarray(commanded_duty)
    mean_temp_last_hour_f = float(temps[-3600:].mean())
    if abs(mean_temp_last_hour_f - setpoint_f) > REGIME_TOL_F:
        raise RuntimeError(
            f"seed {seed}: mean temperature over the last simulated hour is "
            f"{mean_temp_last_hour_f:.1f}F, more than {REGIME_TOL_F:.0f}F from "
            f"the {setpoint_f:.0f}F set point -- refusing to write a baseline "
            "captured while the plant was not under control."
        )

    # Direct negative control for C1's actual mechanism: a mean over the last
    # hour is a mean, and passes any zero-mean oscillation around set point.
    # Realized duty diverging from commanded duty is what a broken duty-cycle
    # realization looks like BEFORE it shows up in temperature at all.
    realized_duty_mean = float(realized_duty.mean())
    commanded_duty_mean = float(commanded_duty.mean())
    duty_divergence = abs(realized_duty_mean - commanded_duty_mean)
    if duty_divergence > DUTY_DIVERGENCE_TOL:
        raise RuntimeError(
            f"seed {seed}: realized duty ({realized_duty_mean:.3f}) diverges from "
            f"commanded duty ({commanded_duty_mean:.3f}) by {duty_divergence:.3f}, "
            f"more than the {DUTY_DIVERGENCE_TOL:.2f} tolerance -- the auger's "
            "on-fraction does not track what the policy asked for."
        )

    # Locate the end of the startup transient: the first second the plant
    # lands within SETTLE_TOL_F of set point and never leaves that band again
    # before the lid opens. Same test controller_matrix.py uses for settle_s,
    # applied here to find the start of the region worth comparing policies
    # on, not to score how fast the controller got there.
    settle_from = None
    for tt in range(lid_open_at):
        if abs(temps[tt] - setpoint_f) <= SETTLE_TOL_F:
            if settle_from is None:
                settle_from = tt
        else:
            settle_from = None
    if settle_from is None:
        raise RuntimeError(
            f"seed {seed}: the plant never settled within {SETTLE_TOL_F:.0f}F of "
            f"{setpoint_f:.0f}F before the lid-open window -- there is no warm "
            "region left to measure policy agreement on."
        )
    warm_start_s = settle_from
    # warm_start_s is genuinely data-derived (a SETTLE_TOL_F sweep of
    # 2/3/4/5/6/8/10F gave 4522/607/514/489/451/187/184s -- it is not 30
    # minutes in disguise), but it is close to the edge of the band it is
    # derived from: the worst post-settle error against SETTLE_TOL_F=5.0 is
    # 4.983F, 0.017F of headroom. Nothing in this script gates on
    # warm_start_s directly -- see the report for why, and what a later
    # comparison should check before trusting the warm-window figures.

    sample_t_arr = np.asarray(sample_t)
    warm_mask = sample_t_arr >= warm_start_s
    lid_mask = np.asarray(in_lid)

    net_raw_vals = np.asarray([net.firing_rate_raw(x, u, sp) for x, u, sp in triples])
    diffs_clamped = np.asarray([abs(net.firing_rate(x, u, sp) - q) for (x, u, sp), q in zip(triples, q_nlp_clamped)])
    diffs_raw = np.asarray([abs(nq - q) for nq, q in zip(net_raw_vals, q_nlp_raw)])

    def _excursions(mask):
        sel = net_raw_vals[mask]
        below = sel < core.cfg["Q_min"]
        above = sel > core.cfg["Q_max"]
        return {
            "n": int(mask.sum()),
            "n_excursions": int(below.sum() + above.sum()),
            "worst_below_q_min": float((core.cfg["Q_min"] - sel[below]).max()) if below.any() else 0.0,
            "worst_above_q_max": float((sel[above] - core.cfg["Q_max"]).max()) if above.any() else 0.0,
            # Unconditional (R2): signed distance to each boundary at its
            # closest approach, whether or not that approach crossed it. A
            # count of 0 can still be a hair's breadth from 1; this is the
            # number that says how much breathing room there actually was.
            "margin_min_to_q_min": float((sel - core.cfg["Q_min"]).min()),
            "margin_min_to_q_max": float((core.cfg["Q_max"] - sel).min()),
        }

    excursions_whole = _excursions(np.ones_like(lid_mask))
    excursions_warm = _excursions(warm_mask)
    excursions_lid = _excursions(lid_mask)

    return {
        "seed": seed,
        "applied_q_split_expected": bool(applied_q_split_expected),
        # Which experiment arm produced this row. Rows from different
        # lid_models are not comparable to each other (see module docstring).
        "lid_model": lid_model,
        "estimator_input": estimator_input,
        "n": int(diffs_clamped.size),
        "n_lid": int(lid_mask.sum()),
        "warm_start_s": int(warm_start_s),
        "mean_temp_last_hour_f": mean_temp_last_hour_f,
        "realized_duty_mean": realized_duty_mean,
        "commanded_duty_mean": commanded_duty_mean,
        # PRIMARY acceptance quantity: how often, and how far, the net's
        # un-clipped answer leaves [Q_min, Q_max] -- warm window (excludes
        # the startup transient, same as the RMS split below) and lid window.
        "excursion_n_warm": excursions_warm["n_excursions"],
        "excursion_pct_warm": 100.0 * excursions_warm["n_excursions"] / excursions_warm["n"],
        "excursion_worst_below_q_min_warm": excursions_warm["worst_below_q_min"],
        "excursion_worst_above_q_max_warm": excursions_warm["worst_above_q_max"],
        "excursion_n_lid": excursions_lid["n_excursions"],
        "excursion_worst_below_q_min_lid": excursions_lid["worst_below_q_min"],
        "excursion_worst_above_q_max_lid": excursions_lid["worst_above_q_max"],
        # SECONDARY/context: whole-run excursion count (includes the startup
        # transient -- kept, labelled, not the acceptance number).
        "excursion_n_whole": excursions_whole["n_excursions"],
        "excursion_pct_whole": 100.0 * excursions_whole["n_excursions"] / excursions_whole["n"],
        "excursion_worst_below_q_min_whole": excursions_whole["worst_below_q_min"],
        "excursion_worst_above_q_max_whole": excursions_whole["worst_above_q_max"],
        # Signed minimum margin to each boundary, unconditional (R2): reported
        # even when the excursion count above is zero, because a zero count
        # can still be a knife-edge result. Positive = stayed that far
        # inside the box; negative = crossed by that much (matches the worst_*
        # figures above in that case).
        "margin_min_to_q_min_whole": excursions_whole["margin_min_to_q_min"],
        "margin_min_to_q_max_whole": excursions_whole["margin_min_to_q_max"],
        "margin_min_to_q_min_lid": excursions_lid["margin_min_to_q_min"],
        "margin_min_to_q_max_lid": excursions_lid["margin_min_to_q_max"],
        # SECONDARY: raw RMS/max, warm-window (what a later comparison should
        # read) and whole-run (kept, clearly labelled, not hidden).
        "rms_all_raw_warm": _rms(diffs_raw[warm_mask]),
        "max_all_raw_warm": float(diffs_raw[warm_mask].max()),
        "rms_all_raw_whole": _rms(diffs_raw),
        "max_all_raw_whole": float(diffs_raw.max()),
        "rms_lid_raw": _rms(diffs_raw[lid_mask]) if lid_mask.any() else None,
        "max_lid_raw": float(diffs_raw[lid_mask].max()) if lid_mask.any() else None,
        # Reported for completeness, not the acceptance question (see
        # module docstring for why clamping both sides hides the failure
        # mode this script exists to catch).
        "rms_all_clamped_warm": _rms(diffs_clamped[warm_mask]),
        "max_all_clamped_warm": float(diffs_clamped[warm_mask].max()),
        "rms_all_clamped_whole": _rms(diffs_clamped),
        "max_all_clamped_whole": float(diffs_clamped.max()),
        "rms_lid_clamped": _rms(diffs_clamped[lid_mask]) if lid_mask.any() else None,
        "max_lid_clamped": float(diffs_clamped[lid_mask].max()) if lid_mask.any() else None,
        "q_span": float(core.cfg["Q_max"] - core.cfg["Q_min"]),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Measure net-vs-NLP policy disagreement.")
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--out", default=OUT)
    ap.add_argument(
        "--applied-q-split-expected",
        action="store_true",
        help="Set for the after-Task-13 run, once Controller exposes _applied_Q.",
    )
    ap.add_argument(
        "--lid-model",
        choices=("faithful", "stress"),
        default="faithful",
        help="'faithful' mirrors hold.py (0.0 once, then u_min, auger cycling); "
        "'stress' holds 0.0 with the auger off for the whole pause.",
    )
    ap.add_argument(
        "--estimator-input",
        choices=("applied", "command"),
        default="applied",
        help="'applied' reports duty through set_output; 'command' withholds "
        "those reports, reproducing pre-Task-13 behavior on a post-Task-13 checkout.",
    )
    args = ap.parse_args(argv)
    rows = [
        replay(
            seed=s,
            applied_q_split_expected=args.applied_q_split_expected,
            lid_model=args.lid_model,
            estimator_input=args.estimator_input,
        )
        for s in args.seeds
    ]
    with open(args.out, "w") as f:
        json.dump(rows, f, indent=1, sort_keys=True)
    for r in rows:
        print(
            f"seed {r['seed']}: mean_temp_last_hour_f={r['mean_temp_last_hour_f']:.1f} "
            f"realized_duty={r['realized_duty_mean']:.3f} commanded_duty={r['commanded_duty_mean']:.3f} "
            f"warm_start_s={r['warm_start_s']} applied_q_split_expected={r['applied_q_split_expected']} "
            f"lid_model={r['lid_model']} estimator_input={r['estimator_input']}"
        )
        print(
            f"  excursions: warm={r['excursion_n_warm']}/{r['n']} lid={r['excursion_n_lid']}/{r['n_lid']} "
            f"whole={r['excursion_n_whole']} ({r['excursion_pct_whole']:.1f}%, context only)"
        )
        # signed: positive = that far inside the boundary, negative = crossed
        # it by that much -- do not add a literal sign prefix here, the
        # values already carry their own.
        print(
            f"  margin to boundary (whole run): to_Q_min={r['margin_min_to_q_min_whole']:+.3f} "
            f"to_Q_max={r['margin_min_to_q_max_whole']:+.3f}   (lid window): "
            f"to_Q_min={r['margin_min_to_q_min_lid']:+.3f} to_Q_max={r['margin_min_to_q_max_lid']:+.3f}"
        )
        print(
            f"  rms_all_raw_warm={r['rms_all_raw_warm']:.3f} max_all_raw_warm={r['max_all_raw_warm']:.3f} "
            f"(whole: rms={r['rms_all_raw_whole']:.3f} max={r['max_all_raw_whole']:.3f}) "
            f"rms_lid_raw={r['rms_lid_raw']} max_lid_raw={r['max_lid_raw']} "
            f"(Q span {r['q_span']:.0f})"
        )


if __name__ == "__main__":
    main()
