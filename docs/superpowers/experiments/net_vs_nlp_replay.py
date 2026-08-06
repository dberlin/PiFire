#!/usr/bin/env python3
"""Measure the net policy against the NLP on the NLP's closed-loop states.

The replay runs the plant once under the NLP, records every
``(x_hat, u_prev, set_point_c)`` input, and evaluates the net on those exact
states. It reports raw and [0, 1]-clamped disagreement, including a physical
lid-open window that is outside the training distribution.

Actuation is production's framed scheduler: 2-second pulses in 20-second
frames. There is no minimum-duty floor. At lid detection the scheduler resets,
the auger and fan remain off for ``LID_PAUSE_S``, and resumption starts a fresh
frame with no carried credit. The physical lid remains open for
``lid_open_for`` independently of that actuator pause.

Every control solve receives the realized mean auger duty since the preceding
solve through ``set_output``. During the pause, cumulative delivered time does
not change, so the reported realized duty is zero. This keeps the estimator's
``_applied_combustion_load`` and ``_policy_u_prev`` tied to delivery rather
than request.

Rows contain whole-run, warm-region, physical-lid, paused-lid, and
released-lid figures. The raw figures expose out-of-range learned outputs;
their clamped counterparts describe the bounded load the plant could receive.
Archived JSON captures are historical artifacts and are intentionally not
rewritten by this script.
"""

import argparse
import hashlib
import json
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common.defaults import default_settings  # noqa: E402
from controller.applied_output import AppliedOutput, OutputSource  # noqa: E402
from controller.grill_sim import GrillSim  # noqa: E402
from controller.mpc import Controller  # noqa: E402
from controller.mpc_net import NetPolicy, net_path_for  # noqa: E402
from controller.runtime.logic.pulse import PulseResetReason, PulseScheduler  # noqa: E402
from controller_matrix import _recovery_s  # noqa: E402

OUT = os.path.join(_ROOT, "docs/superpowers/experiments/_net_vs_nlp_baseline.json")
ARTIFACT = os.path.join(_ROOT, "controller/mpc_policy_net.npz")

# hold.py caps the actuator pause at LidOpenPauseTime on both the automatic
# (hold.py:265) and the manual (hold.py:296) path, and hold.py:269-271 clears it
# and restarts the fan on expiry. Read from settings so the harness tracks the
# default rather than restating it.
LID_PAUSE_S = default_settings()["cycle_data"]["LidOpenPauseTime"]

# Seconds past the lid closing that `lid_min_temp_f` keeps watching. The chamber
# is still climbing back when the lid shuts, so the coldest reading of the event
# lands after the window rather than inside it. It has to outlast the recovery
# itself or the trough it reports is an artifact of where the watch stopped;
# the test suite binds it against the recovery bound for exactly that reason.
LID_RECOVERY_WINDOW_S = 300

CYCLE_DATA = {"HoldCycleTime": 20, "u_max": 0.9}

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
# How far realized mean duty may drift from requested duty before this is
# treated as a framed-scheduler realization bug.
DUTY_DIVERGENCE_TOL = 0.05


def _c_to_f(c):
    return c * 9.0 / 5.0 + 32.0


def _rms(a):
    return float(np.sqrt((a**2).mean()))


def _sha256(path):
    """Return the content identity of the net artifact."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _split_is_live(cycle_data):
    """Whether applied combustion-load feedback changes controller state."""
    probe = Controller({"policy": "nlp"}, "F", dict(cycle_data))
    before = getattr(probe, "_applied_combustion_load", None)
    probe.set_output(AppliedOutput(probe.u_max, OutputSource.CONTROLLER, 0.0))
    return before is not None and getattr(probe, "_applied_combustion_load", None) != before


def _lid_windows(t, lid_open_at, lid_open_for):
    """The two windows a lid opening drives, as production drives them.

    The chamber is open -- and losing heat -- for `lid_open_for`, while the
    actuators resume after `LID_PAUSE_S`. Returned separately because they are
    different lengths: collapsing them into one flag either surrenders control
    for as long as the lid is open, which no production path does, or seals the
    chamber the moment the actuators come back.
    """
    return (
        lid_open_at <= t < lid_open_at + lid_open_for,
        lid_open_at <= t < lid_open_at + LID_PAUSE_S,
    )


def replay(
    seed=0,
    duration_s=3 * 3600,
    lid_open_at=2 * 3600,
    lid_open_for=120,
    setpoint_f=225.0,
):
    core = Controller({"policy": "nlp"}, "F", CYCLE_DATA)
    assert core._net is None, "configure policy=nlp; the point is to log the NLP's answers"
    assert _split_is_live(CYCLE_DATA), "set_output must update _applied_combustion_load from realized delivery."
    core.set_target(setpoint_f)
    plant = GrillSim(seed=seed)
    period = core.get_control_period()
    net_path = net_path_for(ARTIFACT, bool(core.cfg["enable_fan_input"]))
    net = NetPolicy.load(net_path)
    assert net.matches_config(core.cfg), (
        "controller configuration and net artifact provenance disagree; "
        "their normalized combustion-load predictions are not comparable."
    )
    assert not core.cfg["enable_fan_input"], "enable_fan_input=True requires replaying the allocator's fan output."

    triples, nlp_loads, nlp_raw_loads, solve_failed, in_lid, sample_t = [], [], [], [], [], []
    # The lid window contains two regimes of different length: a full actuator
    # pause, then a still-open chamber under resumed control.
    in_lid_paused = []
    temps = []
    realized_duty, commanded_duty = [], []
    fan_frac = 1.0
    ratio, next_solve = 0.0, 0.0
    scheduler = PulseScheduler()
    actual_auger_on = False
    feedback_start = 0.0
    feedback_delivered = 0.0
    feedback_requested = 0.0

    def report_applied(now, source):
        nonlocal feedback_start, feedback_delivered
        elapsed = now - feedback_start
        realized = 0.0 if elapsed == 0.0 else feedback_delivered / elapsed
        core.set_output(
            AppliedOutput(
                ratio=realized,
                source=source,
                timestamp=now,
                requested=feedback_requested,
            )
        )
        feedback_start = now
        feedback_delivered = 0.0

    for t in range(duration_s):
        # `actual_auger_on` is the state that drove the previous plant step.
        # Account that observed second before handing its interval to the
        # estimator at this solve.
        if t:
            feedback_delivered += float(actual_auger_on)
        lid, lid_paused = _lid_windows(t, lid_open_at, lid_open_for)
        temp_f = _c_to_f(plant.measured())
        solved = t >= next_solve
        if solved:
            next_solve = t + period
            report_applied(
                float(t),
                OutputSource.LID_OPEN if lid_paused and t != lid_open_at else OutputSource.CONTROLLER,
            )
            applied_before_update = float(core._applied_combustion_load)
            raw = core.update(temp_f)
            triples.append(
                (np.asarray(core._x_hat).reshape(-1).copy(), float(core._policy_u_prev), float(core._set_point_c))
            )
            nlp_loads.append(float(core._last_combustion_load))
            nlp_raw_loads.append(float(core._last_raw_combustion_load))
            solve_failed.append(bool(core._last_solve_failed))
            in_lid.append(lid)
            in_lid_paused.append(lid_paused)
            sample_t.append(t)
            assert core._last_combustion_load == float(np.clip(core._last_raw_combustion_load, 0.0, 1.0))
            assert core._policy_u_prev == float(np.clip(applied_before_update, 0.0, 1.0))
            ratio = min(max(float(raw["cycle_ratio"]), 0.0), core.u_max)

        if lid_paused and t == lid_open_at:
            scheduler.advance(ratio, float(t), actual_auger_on)
            scheduler.reset(PulseResetReason.LID)
            actual_auger_on = False
        elif lid_paused:
            actual_auger_on = False
        else:
            decision = scheduler.advance(ratio, float(t), actual_auger_on)
            actual_auger_on = decision.command_on

        auger_frac = float(actual_auger_on)
        if solved:
            feedback_requested = ratio
        plant.step(auger_on=auger_frac, fan_frac=0.0 if lid_paused else fan_frac, lid_open=lid)
        temps.append(temp_f)
        realized_duty.append(auger_frac)
        commanded_duty.append(ratio)

    n_failed = int(sum(solve_failed))
    if n_failed:
        raise RuntimeError(
            f"seed {seed}: {n_failed} NLP solve(s) held the previous normalized "
            "combustion load after an exception; the raw-load comparison needs "
            "a fresh NLP answer at every solve."
        )

    temps = np.asarray(temps)
    realized_duty = np.asarray(realized_duty)
    commanded_duty = np.asarray(commanded_duty)
    mean_temp_last_hour_f = float(temps[-3600:].mean())
    # Coldest reading over the lid event and the recovery that follows it. The
    # lid detector arms on depth (hold.py:241), so this is what says whether the
    # window is a lid event at all rather than a shallow dip wearing the label.
    lid_min_temp_f = float(temps[lid_open_at : lid_open_at + lid_open_for + LID_RECOVERY_WINDOW_S].min())
    # Width of the same excursion, on controller_matrix.py's definition (its
    # `_recovery_s`, imported rather than restated): seconds from the lid
    # opening until the chamber is first back inside the 5 F band that
    # definition uses. Depth alone cannot say how long control was surrendered,
    # because a longer pause only digs the trough deeper.
    lid_recovery_s = _recovery_s(temps[lid_open_at:] - setpoint_f)
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

    # Exclude startup from the warm-region comparison at its first entrance
    # into the set-point band. Framed pulses may subsequently cross the band
    # while maintaining the requested mean duty, so requiring the per-second
    # temperature to remain inside it would discard an otherwise controlled run.
    warm_start_s = next(
        (tt for tt in range(lid_open_at) if abs(temps[tt] - setpoint_f) <= SETTLE_TOL_F),
        None,
    )
    if warm_start_s is None:
        raise RuntimeError(
            f"seed {seed}: the plant never entered the {SETTLE_TOL_F:.0f}F set-point band before the lid-open window."
        )

    sample_t_arr = np.asarray(sample_t)
    warm_mask = sample_t_arr >= warm_start_s
    lid_mask = np.asarray(in_lid)
    # The lid window's two regimes. `lid_paused_mask` is the window the plan's
    # gate paragraph describes ("while the auger is paused"); `lid_released_mask`
    # is the rest of the physical opening, where the controller has full
    # authority over a chamber that is colder than any training episode reaches.
    # `lid_mask` remains their union, so the whole-window figures stay readable.
    lid_paused_mask = np.asarray(in_lid_paused)
    lid_released_mask = lid_mask & ~lid_paused_mask

    net_raw_loads = np.asarray([net.firing_rate_raw(x, u, sp) for x, u, sp in triples])
    diffs_clamped = np.asarray(
        [abs(np.clip(net_load, 0.0, 1.0) - nlp_load) for net_load, nlp_load in zip(net_raw_loads, nlp_loads)]
    )
    diffs_raw = np.asarray([abs(net_load - nlp_load) for net_load, nlp_load in zip(net_raw_loads, nlp_raw_loads)])

    def _excursions(mask):
        selected = net_raw_loads[mask]
        if selected.size == 0:
            return {
                "n": 0,
                "n_excursions": 0,
                "worst_below_zero": None,
                "worst_above_one": None,
                "margin_to_zero": None,
                "margin_to_one": None,
            }
        below_zero = selected < 0.0
        above_one = selected > 1.0
        return {
            "n": int(mask.sum()),
            "n_excursions": int(below_zero.sum() + above_one.sum()),
            "worst_below_zero": float((-selected[below_zero]).max()) if below_zero.any() else 0.0,
            "worst_above_one": float((selected[above_one] - 1.0).max()) if above_one.any() else 0.0,
            "margin_to_zero": float(selected.min()),
            "margin_to_one": float((1.0 - selected).min()),
        }

    excursions_whole = _excursions(np.ones_like(lid_mask))
    excursions_warm = _excursions(warm_mask)
    excursions_lid = _excursions(lid_mask)
    excursions_lid_paused = _excursions(lid_paused_mask)
    excursions_lid_released = _excursions(lid_released_mask)

    return {
        "seed": seed,
        "net_path": os.path.relpath(net_path, _ROOT),
        "net_sha256": _sha256(net_path),
        "n": int(diffs_clamped.size),
        "n_lid": int(lid_mask.sum()),
        "n_lid_paused": int(lid_paused_mask.sum()),
        "n_lid_released": int(lid_released_mask.sum()),
        "warm_start_s": int(warm_start_s),
        "mean_temp_last_hour_f": mean_temp_last_hour_f,
        "lid_min_temp_f": lid_min_temp_f,
        "lid_recovery_s": lid_recovery_s,
        "realized_duty_mean": realized_duty_mean,
        "commanded_duty_mean": commanded_duty_mean,
        "load_excursion_n_warm": excursions_warm["n_excursions"],
        "load_excursion_pct_warm": 100.0 * excursions_warm["n_excursions"] / excursions_warm["n"],
        "load_excursion_worst_below_zero_warm": excursions_warm["worst_below_zero"],
        "load_excursion_worst_above_one_warm": excursions_warm["worst_above_one"],
        "load_excursion_n_lid": excursions_lid["n_excursions"],
        "load_excursion_worst_below_zero_lid": excursions_lid["worst_below_zero"],
        "load_excursion_worst_above_one_lid": excursions_lid["worst_above_one"],
        "load_excursion_n_lid_paused": excursions_lid_paused["n_excursions"],
        "load_excursion_worst_below_zero_lid_paused": excursions_lid_paused["worst_below_zero"],
        "load_excursion_worst_above_one_lid_paused": excursions_lid_paused["worst_above_one"],
        "load_excursion_n_lid_released": excursions_lid_released["n_excursions"],
        "load_excursion_worst_below_zero_lid_released": excursions_lid_released["worst_below_zero"],
        "load_excursion_worst_above_one_lid_released": excursions_lid_released["worst_above_one"],
        "load_excursion_n_whole": excursions_whole["n_excursions"],
        "load_excursion_pct_whole": 100.0 * excursions_whole["n_excursions"] / excursions_whole["n"],
        "load_excursion_worst_below_zero_whole": excursions_whole["worst_below_zero"],
        "load_excursion_worst_above_one_whole": excursions_whole["worst_above_one"],
        "load_margin_to_zero_whole": excursions_whole["margin_to_zero"],
        "load_margin_to_one_whole": excursions_whole["margin_to_one"],
        "load_margin_to_zero_lid": excursions_lid["margin_to_zero"],
        "load_margin_to_one_lid": excursions_lid["margin_to_one"],
        "load_margin_to_zero_lid_paused": excursions_lid_paused["margin_to_zero"],
        "load_margin_to_one_lid_paused": excursions_lid_paused["margin_to_one"],
        "load_margin_to_zero_lid_released": excursions_lid_released["margin_to_zero"],
        "load_margin_to_one_lid_released": excursions_lid_released["margin_to_one"],
        "rms_all_raw_warm": _rms(diffs_raw[warm_mask]),
        "max_all_raw_warm": float(diffs_raw[warm_mask].max()),
        "rms_all_raw_whole": _rms(diffs_raw),
        "max_all_raw_whole": float(diffs_raw.max()),
        "rms_lid_raw": _rms(diffs_raw[lid_mask]) if lid_mask.any() else None,
        "max_lid_raw": float(diffs_raw[lid_mask].max()) if lid_mask.any() else None,
        "rms_all_clamped_warm": _rms(diffs_clamped[warm_mask]),
        "max_all_clamped_warm": float(diffs_clamped[warm_mask].max()),
        "rms_all_clamped_whole": _rms(diffs_clamped),
        "max_all_clamped_whole": float(diffs_clamped.max()),
        "rms_lid_clamped": _rms(diffs_clamped[lid_mask]) if lid_mask.any() else None,
        "max_lid_clamped": float(diffs_clamped[lid_mask].max()) if lid_mask.any() else None,
        "load_span": 1.0,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Measure net-vs-NLP policy disagreement.")
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args(argv)
    rows = [replay(seed=seed) for seed in args.seeds]
    with open(args.out, "w") as f:
        json.dump(rows, f, indent=1, sort_keys=True)
    for row in rows:
        print(
            f"seed {row['seed']}: mean_temp_last_hour_f={row['mean_temp_last_hour_f']:.1f} "
            f"realized_duty={row['realized_duty_mean']:.3f} "
            f"commanded_duty={row['commanded_duty_mean']:.3f} warm_start_s={row['warm_start_s']}"
        )
        print(
            f"  load excursions: warm={row['load_excursion_n_warm']}/{row['n']} "
            f"lid={row['load_excursion_n_lid']}/{row['n_lid']} "
            f"(paused={row['load_excursion_n_lid_paused']}/{row['n_lid_paused']} "
            f"released={row['load_excursion_n_lid_released']}/{row['n_lid_released']}) "
            f"whole={row['load_excursion_n_whole']} "
            f"({row['load_excursion_pct_whole']:.1f}%, context only)"
        )
        print(
            f"  lid excursion: lid_min_temp_f={row['lid_min_temp_f']:.2f} "
            f"lid_recovery_s={row['lid_recovery_s']} net={row['net_path']} "
            f"sha256={row['net_sha256'][:12]}"
        )
        print(
            f"  normalized-load margin (whole): to_zero={row['load_margin_to_zero_whole']:+.3f} "
            f"to_one={row['load_margin_to_one_whole']:+.3f}; "
            f"(lid): to_zero={row['load_margin_to_zero_lid']:+.3f} "
            f"to_one={row['load_margin_to_one_lid']:+.3f}"
        )
        print(
            f"  rms_all_raw_warm={row['rms_all_raw_warm']:.3f} "
            f"max_all_raw_warm={row['max_all_raw_warm']:.3f} "
            f"(whole: rms={row['rms_all_raw_whole']:.3f} max={row['max_all_raw_whole']:.3f}) "
            f"rms_lid_raw={row['rms_lid_raw']} max_lid_raw={row['max_lid_raw']} "
            f"(normalized span {row['load_span']:.0f})"
        )


if __name__ == "__main__":
    main()
