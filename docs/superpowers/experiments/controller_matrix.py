#!/usr/bin/env python3
"""Scenario matrix for a controller against GrillSim.

Drives a controller core directly -- no Hold mode, no datastore -- so a run is
reproducible from (controller, scenario, seed) alone. The lid-open scenario
opens the lid on the plant as well as reproducing what Hold does to the auger
during a pause, and reports the applied duty through `set_output` when the
controller has that capability, so the same harness measures code from before
and after applied-output feedback exists.

A `lid_open` window drives two independent things, as production does:

* the physical lid, open for the whole window, leaking chamber heat to ambient
  (`GrillSim.step(lid_open=True)`); and
* Hold's actuator pause, which starts when the lid opens and runs
  `LidOpenPauseTime` seconds (`hold.py:265`, `hold.py:296`) -- the fan stops and
  the auger is pinned to `u_min` (`hold.py:171-173`). Hold releases the pause on
  the timer (`hold.py:269-271`) whether or not the lid is still open, so a window
  longer than the pause ends with the controller back at full authority while the
  chamber is still losing heat.

The pause begins at the instant the lid opens, which is the shape of Hold's
manual `lid_open_toggle` path and can happen at setpoint; the automatic detector
instead arms only once the chamber has already fallen `LidOpenThreshold` percent
below it, and the excursion this scenario produces is deep enough to cross that
trigger.
"""

import argparse
import importlib
import json
import os
import sys
import time
from dataclasses import dataclass, field
from multiprocessing import Pool

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from common.defaults import default_settings  # noqa: E402
from controller.grill_sim import GrillSim, MAKGrillSim  # noqa: E402

OUT = "./docs/superpowers/experiments/_matrix_baseline.json"

# Plants a run may be driven against, by name. Resolved out of this module's
# globals at call time rather than captured in a mapping here, so a test that
# substitutes `controller_matrix.GrillSim` is still substituting the plant the
# loop builds.
PLANTS = ("GrillSim", "MAKGrillSim")

CYCLE_DATA = {"HoldCycleTime": 20, "u_min": 0.15, "u_max": 0.9, "PMode": 2}

# How long Hold holds the actuators after a lid event, from the same setting
# production reads, so the harness tracks a user's configured pause.
LID_PAUSE_S = default_settings()["cycle_data"]["LidOpenPauseTime"]

CONTROLLER_CONFIGS = {
    "pid_sp": {"PB": 60.0, "Ti": 180.0, "Td": 45.0, "stable_window": 12, "center_factor": 0.0010},
    "pid_ac": {"PB": 60.0, "Ti": 180.0, "Td": 45.0, "stable_window": 12, "center_factor": 0.0010},
    "mpc": {},
}


def _c_to_f(c):
    return c * 9.0 / 5.0 + 32.0


@dataclass
class Scenario:
    name: str
    duration_s: int
    # (start_second, setpoint_F); the first entry must start at 0
    setpoints: list = field(default_factory=list)
    # (start_second, duration_s) windows where the lid is physically open. The
    # actuator pause each one triggers is derived, and is LID_PAUSE_S long
    # regardless of how long the lid stays open.
    lid_open: list = field(default_factory=list)


SCENARIOS = {
    "steady_225": Scenario("steady_225", 3 * 3600 + 1800, [(0, 225.0)]),
    "steady_325": Scenario("steady_325", 3 * 3600, [(0, 325.0)]),
    "steady_350": Scenario("steady_350", 3 * 3600 + 1800, [(0, 350.0)]),
    "steady_450": Scenario("steady_450", 3 * 3600 + 1800, [(0, 450.0)]),
    "step_225_275": Scenario("step_225_275", 4 * 3600, [(0, 225.0), (2 * 3600, 275.0)]),
    "capability_600": Scenario("capability_600", 3 * 3600, [(0, 600.0)]),
    "lid_open_225": Scenario("lid_open_225", 3 * 3600, [(0, 225.0)], [(2 * 3600, 120)]),
}


def _setpoint_at(scenario, t):
    sp = scenario.setpoints[0][1]
    for start, value in scenario.setpoints:
        if t >= start:
            sp = value
    return sp


def _lid_open_at(scenario, t):
    """The lid is physically open, so the chamber is leaking heat to ambient.
    Hold has no notion of this; it only ever sees the temperature."""
    return any(start <= t < start + dur for start, dur in scenario.lid_open)


def _lid_paused_at(scenario, t):
    """Hold is holding the actuators. The pause is a timer armed when the lid
    opens (`hold.py:296`) and cleared LID_PAUSE_S later (`hold.py:269-271`),
    independent of whether the lid is still open."""
    return any(start <= t < start + LID_PAUSE_S for start, _ in scenario.lid_open)


def _lid_pause_start_at(scenario, t):
    """True on the single tick a pause begins -- the instant hold.py:247-264
    forces the auger off and reports one AppliedOutput(0.0), as distinct from
    the rest of the pause, which keeps cycling at u_min."""
    return any(start == t for start, _ in scenario.lid_open)


def _recovery_s(err_from_lid):
    """Seconds from the lid opening until the chamber, having left the 5 F
    band around setpoint, is back inside it. 0 if it never left, None if the
    run ends before it returns."""
    outside = np.flatnonzero(np.abs(err_from_lid) > 5.0)
    if outside.size == 0:
        return 0
    left = int(outside[0])
    back = np.flatnonzero(np.abs(err_from_lid[left:]) <= 5.0)
    return None if back.size == 0 else left + int(back[0])


def _report(core, ratio, source_name, t, requested=None):
    """Report applied duty when the controller can hear it; no-op otherwise."""
    setter = getattr(core, "set_output", None)
    if setter is None:
        return
    from controller.applied_output import AppliedOutput, OutputSource

    setter(AppliedOutput(ratio=ratio, source=OutputSource(source_name), timestamp=float(t), requested=requested))


def _auger_toggle_tick(auger_on, auger_toggle, t, ratio, cycle_time):
    """Port of controller.runtime.modes.base.ControlMode._auger_cycle_tick,
    returning the auger's exact fractional on-time over the window [t, t+1)
    instead of a single boolean sample of it.

    Production evaluates this strict-`>` toggle at its work-loop resolution
    (~20 Hz) and drives a physical auger that integrates fuel delivery
    continuously between samples. GrillSim can only be stepped once per
    simulated second, so sampling the toggle as a boolean once per second
    would quantize fuel delivery to whichever side of a transition the sample
    landed on. Instead, the transition instant within the window is located
    exactly (continuous time, so `>` vs `>=` at that single instant does not
    affect the fraction) and returned as the portion of the window it covers.
    This is arithmetic rather than sub-stepping, and it depends on at most one
    transition ever falling inside a 1 s window -- the assertion below makes
    that requirement explicit instead of leaving it to fail silently.

    `auger_toggle` is carried as the exact (possibly fractional) transition
    time rather than snapped to a tick, so later windows see the true elapsed
    time since the last transition. A re-solve to a smaller ratio can put the
    threshold computed from the old `auger_toggle` in the past relative to the
    current window; `transition` is clamped to `t` so that case reads as "flip
    right now" instead of a negative or >1 fraction, and the return value is
    clamped to [0, 1] as a backstop.
    """
    assert cycle_time * ratio >= 1 and cycle_time * (1 - ratio) >= 1, (
        f"ratio={ratio} at cycle_time={cycle_time} gives an on- or off-phase "
        "shorter than one window -- more than one transition could fall "
        "inside a single tick, which this closed-form arithmetic can't represent"
    )
    was_on = auger_on
    if not was_on:
        transition = max(auger_toggle + cycle_time * (1 - ratio), t)
        if transition >= t + 1:
            return False, auger_toggle, 0.0
        return True, transition, min(max(t + 1 - transition, 0.0), 1.0)
    transition = max(auger_toggle + cycle_time * ratio, t)
    if transition >= t + 1:
        return True, auger_toggle, 1.0
    return False, transition, min(max(transition - t, 0.0), 1.0)


class _SimClock:
    """Callable replacement for `time.time`, advanced once per simulated
    second so a controller reading the wall clock for its own `dt` observes
    the step size this harness actually models, not the wall-clock time
    between tight-loop calls."""

    def __init__(self, t0):
        self.t = t0

    def __call__(self):
        return self.t


def run_scenario(controller, scenario, seed, *, plant="GrillSim", config=None, refit=False):
    """Drive `controller` through `scenario` on `plant` and score the result.

    `config` is merged over CONTROLLER_CONFIGS[controller] before the core is
    built. That is how a model an earlier cook learned reaches this one: mpc.py
    adopts a fit into `cfg` and rebuilds nothing, because production carries it
    across through the next cook's build from settings -- so a run that starts
    from learned parameters starts from them at CONSTRUCTION, exactly as the
    next Hold would, and not by mutating a core that has already sized its
    horizon and assembled its NLP.

    `refit` runs the end-of-cook refit the same way HoldMode's teardown does,
    after the metrics above are taken, and reports the gate's verdict and the
    parameters it left behind. It changes nothing about the run just scored.
    """
    # Some controllers (pid_sp, pid_ac) read time.time() for their own dt;
    # replacing it with a clock this loop drives makes their dt match the
    # simulated second the loop models, instead of the wall-clock nanoseconds
    # between calls in a tight loop. Controllers that don't read the wall
    # clock (mpc) are unaffected. Started one HoldCycleTime before t=0 so the
    # very first solve -- which happens immediately, since next_solve starts
    # at 0.0 -- sees a full period of elapsed time rather than dt=0.
    clock = _SimClock(-float(CYCLE_DATA["HoldCycleTime"]))
    real_time_time = time.time
    time.time = clock
    try:
        mod = importlib.import_module(f"controller.{controller}")
        core_config = dict(CONTROLLER_CONFIGS[controller])
        core_config.update(config or {})
        core = mod.Controller(core_config, "F", dict(CYCLE_DATA))
        plant_name = plant
        plant = globals()[plant_name](seed=seed)
        u_min, u_max = CYCLE_DATA["u_min"], CYCLE_DATA["u_max"]

        setpoint = _setpoint_at(scenario, 0)
        core.set_target(setpoint)
        _report(core, u_min, "seed", 0)

        period = core.get_control_period() or CYCLE_DATA["HoldCycleTime"]
        ratio, fan_frac = u_min, 1.0
        next_solve = 0.0
        auger_on, auger_toggle = False, 0.0

        temps, duties, settle_from = [], [], None
        for t in range(scenario.duration_s):
            clock.t = float(t)
            new_sp = _setpoint_at(scenario, t)
            if new_sp != setpoint:
                setpoint = new_sp
                core.set_target(setpoint)
                # set_target() just reset the controller's own last-update
                # clock to t; if a scheduled solve also lands on t (e.g.
                # step_225_275's setpoint change falls on a solve boundary),
                # calling update() again this same tick would hand it dt=0.
                # Push the next solve a full period out from here instead.
                next_solve = t + period
                settle_from = None

            lid_open = _lid_open_at(scenario, t)
            lid_paused = _lid_paused_at(scenario, t)
            lid_pause_start = _lid_pause_start_at(scenario, t)
            temp_f = _c_to_f(plant.measured())

            if t >= next_solve:
                next_solve = t + period
                raw = core.update(temp_f)
                if isinstance(raw, dict):
                    requested = float(raw.get("cycle_ratio", 0.0))
                    fan = raw.get("fan") or {}
                    if fan.get("duty") is not None:
                        fan_frac = float(fan["duty"]) / 100.0
                else:
                    requested = float(raw)
                ratio = min(max(requested, u_min), u_max)
                # hold.py:171-173 replaces the controller's answer with u_min
                # while the pause is live, ahead of the floor/ceiling just
                # above. Once the timer clears the controller is back at full
                # authority even if the lid is still open.
                if lid_paused:
                    ratio = u_min
                _report(core, ratio, "lid_open" if lid_paused else "controller", t, requested=requested)

            if lid_pause_start:
                # hold.py's detection instant: auger off, cycle timer reset,
                # one AppliedOutput(0.0) report (hold.py:247-264). That block
                # clears target_temp_achieved (hold.py:266), and the pause's
                # own heat loss keeps it clear -- hold.py:234 only re-arms it
                # once the plant is back at setpoint -- so this fires exactly
                # once per pause, not every tick.
                auger_on, auger_toggle, auger_frac = False, t, 0.0
                _report(core, 0.0, "lid_open", t)
            else:
                # hold.py:228 calls _auger_cycle_tick unconditionally and
                # base.py:118-147 has no lid gate, so for the rest of the
                # pause the auger keeps cycling at the ratio pinned above.
                auger_on, auger_toggle, auger_frac = _auger_toggle_tick(
                    auger_on, auger_toggle, t, ratio, CYCLE_DATA["HoldCycleTime"]
                )

            # The fan is cut for the pause only; hold.py:271 restarts it on
            # expiry, so it is running again for any part of the lid window
            # that outlasts the timer.
            plant.step(auger_on=auger_frac, fan_frac=0.0 if lid_paused else fan_frac, lid_open=lid_open)

            temps.append(temp_f)
            # The reported ratio: 0.0 at the detection instant, u_min (pinned
            # above) for the rest of the pause, the controller's own answer
            # otherwise.
            duties.append(0.0 if lid_pause_start else ratio)
            if abs(temp_f - setpoint) <= 5.0:
                if settle_from is None:
                    settle_from = t
            else:
                settle_from = None

        temps = np.asarray(temps)
        duties = np.asarray(duties)
        sp_series = np.asarray([_setpoint_at(scenario, t) for t in range(scenario.duration_s)])
        err = temps - sp_series
        lid_start = min((start for start, _ in scenario.lid_open), default=None)
        result = {
            "controller": controller,
            "scenario": scenario.name,
            "plant": plant_name,
            "seed": seed,
            "iae": float(np.abs(err).sum()),
            "pct_within_5f": float((np.abs(err) <= 5.0).mean() * 100.0),
            "overshoot_f": float(err.max()),
            "undershoot_f": float(err.min()),
            "settle_s": (None if settle_from is None else int(settle_from)),
            "mean_duty": float(duties.mean()),
            "std_duty": float(duties.std()),
            "final_temp_f": float(temps[-1]),
            # Depth of the lid excursion: the coldest reading from the first
            # lid opening to the end of the run, so the trough is captured
            # wherever transport lag puts it relative to the lid closing.
            "lid_min_temp_f": (None if lid_start is None else float(temps[lid_start:].min())),
            # Width of the same excursion: seconds from the lid opening until
            # the chamber is first back within 5 F of setpoint. Depth alone
            # cannot distinguish the modelled pause length, since a longer
            # pause only digs the trough deeper.
            "lid_recovery_s": (None if lid_start is None else _recovery_s(err[lid_start:])),
        }
        status = getattr(core, "get_status", lambda: None)()
        if status is not None:
            result["status"] = json.loads(json.dumps(status, allow_nan=False, default=str))
        cfg = getattr(core, "cfg", None)
        if cfg is not None and "n_horizon" in cfg:
            result["configured_n_horizon"] = int(cfg["n_horizon"])
            # What the NLP was actually assembled with. Before the horizon was
            # derived from the model's own coast there is no such attribute and
            # the configured length is the built one.
            built = getattr(core, "_built_n_horizon", None)
            result["built_n_horizon"] = int(cfg["n_horizon"]) if built is None else int(built)
        if refit:
            result["refit"] = _refit_after_cook(core)
        return result
    finally:
        time.time = real_time_time


def _refit_after_cook(core):
    """Run the end-of-cook refit and report what the gate decided.

    Stdout is captured rather than left to interleave across pool workers: the
    fitter and the gate both narrate, and those lines are the evidence for why
    a promotion was refused.
    """
    import contextlib
    import io

    if not hasattr(core, "refit_from_cook"):
        return None
    buf = io.StringIO()
    started = time.perf_counter()
    with contextlib.redirect_stdout(buf):
        verdict = core.refit_from_cook()
    snapshot = getattr(core, "get_model_snapshot", lambda: None)()
    return {
        "accepted": bool(verdict.accepted),
        "reason": str(verdict.reason),
        "horizon_needed": getattr(verdict, "horizon_needed", None),
        "samples": len(getattr(core, "cook_history", list)()),
        "seconds": round(time.perf_counter() - started, 2),
        "params": None if snapshot is None else dict(snapshot["params"]),
        "rmse": None if snapshot is None else snapshot.get("rmse"),
        "log": buf.getvalue().strip().splitlines(),
    }


def _job(arg):
    controller, scenario_name, seed, plant = arg
    return run_scenario(controller, SCENARIOS[scenario_name], seed, plant=plant)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run the GrillSim controller scenario matrix.")
    ap.add_argument("--controllers", nargs="+", default=["pid_sp", "mpc"])
    # Defaults to every scenario, which since `steady_325` was added is a
    # SUPERSET of what `_matrix_baseline.json` currently holds: a bare
    # regeneration now writes seven scenarios where that file has six. Pass
    # `--scenarios` explicitly to reproduce the committed baseline.
    ap.add_argument("--scenarios", nargs="+", default=sorted(SCENARIOS))
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2, 3, 4])
    ap.add_argument("--plants", nargs="+", default=["GrillSim"], choices=PLANTS)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("-w", "--workers", type=int, default=None)
    args = ap.parse_args(argv)

    jobs = [
        (c, s, seed, plant)
        for c in args.controllers
        for s in args.scenarios
        for seed in args.seeds
        for plant in args.plants
    ]
    with Pool(args.workers) as pool:
        rows = pool.map(_job, jobs)
    with open(args.out, "w") as f:
        json.dump(rows, f, indent=1, sort_keys=True)
    print(f"{len(rows)} runs -> {args.out}")


if __name__ == "__main__":
    main()
