#!/usr/bin/env python3
"""Scenario matrix for a controller against GrillSim.

Drives a controller core directly -- no Hold mode, no datastore -- so a run is
reproducible from (controller, scenario, seed) alone. The lid-open scenario
reproduces what Hold does to the auger during a pause and reports it through
`set_output` when the controller has that capability, so the same harness
measures code from before and after applied-output feedback exists.
"""

import argparse
import importlib
import json
import os
import sys
from dataclasses import dataclass, field
from multiprocessing import Pool

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from controller.grill_sim import GrillSim  # noqa: E402

OUT = "./docs/superpowers/experiments/_matrix_baseline.json"

CYCLE_DATA = {"HoldCycleTime": 20, "u_min": 0.15, "u_max": 0.9, "PMode": 2}

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
    # (start_second, duration_s) windows where Hold would pin the auger off
    lid_open: list = field(default_factory=list)


SCENARIOS = {
    "steady_225": Scenario("steady_225", 3 * 3600 + 1800, [(0, 225.0)]),
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
    return any(start <= t < start + dur for start, dur in scenario.lid_open)


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
    This is arithmetic rather than sub-stepping because `cycle_time * u_min`
    and `cycle_time * (1 - u_max)` are both >= 1 s for every ratio this
    harness runs, so at most one transition ever falls inside a given window.

    `auger_toggle` is carried as the exact (possibly fractional) transition
    time rather than snapped to a tick, so later windows see the true elapsed
    time since the last transition.
    """
    was_on = auger_on
    if not was_on:
        transition = auger_toggle + cycle_time * (1 - ratio)
        if transition >= t + 1:
            return False, auger_toggle, 0.0
        return True, transition, (t + 1 - transition)
    transition = auger_toggle + cycle_time * ratio
    if transition >= t + 1:
        return True, auger_toggle, 1.0
    return False, transition, (transition - t)


def run_scenario(controller, scenario, seed):
    mod = importlib.import_module(f"controller.{controller}")
    core = mod.Controller(dict(CONTROLLER_CONFIGS[controller]), "F", dict(CYCLE_DATA))
    plant = GrillSim(seed=seed)
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
        new_sp = _setpoint_at(scenario, t)
        if new_sp != setpoint:
            setpoint = new_sp
            core.set_target(setpoint)
            settle_from = None

        lid_open = _lid_open_at(scenario, t)
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
            if not lid_open:
                _report(core, ratio, "controller", t, requested=requested)

        if lid_open:
            auger_on, auger_toggle, auger_frac = False, t, 0.0
            _report(core, 0.0, "lid_open", t)
        else:
            auger_on, auger_toggle, auger_frac = _auger_toggle_tick(
                auger_on, auger_toggle, t, ratio, CYCLE_DATA["HoldCycleTime"]
            )

        plant.step(auger_on=auger_frac, fan_frac=0.0 if lid_open else fan_frac)

        temps.append(temp_f)
        duties.append(0.0 if lid_open else ratio)
        if abs(temp_f - setpoint) <= 5.0:
            if settle_from is None:
                settle_from = t
        else:
            settle_from = None

    temps = np.asarray(temps)
    duties = np.asarray(duties)
    sp_series = np.asarray([_setpoint_at(scenario, t) for t in range(scenario.duration_s)])
    err = temps - sp_series
    result = {
        "controller": controller,
        "scenario": scenario.name,
        "seed": seed,
        "iae": float(np.abs(err).sum()),
        "pct_within_5f": float((np.abs(err) <= 5.0).mean() * 100.0),
        "overshoot_f": float(err.max()),
        "undershoot_f": float(err.min()),
        "settle_s": (None if settle_from is None else int(settle_from)),
        "mean_duty": float(duties.mean()),
        "std_duty": float(duties.std()),
        "final_temp_f": float(temps[-1]),
    }
    status = getattr(core, "get_status", lambda: None)()
    if status is not None:
        result["status"] = json.loads(json.dumps(status, allow_nan=False, default=str))
    return result


def _job(arg):
    controller, scenario_name, seed = arg
    return run_scenario(controller, SCENARIOS[scenario_name], seed)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run the GrillSim controller scenario matrix.")
    ap.add_argument("--controllers", nargs="+", default=["pid_sp", "mpc"])
    ap.add_argument("--scenarios", nargs="+", default=sorted(SCENARIOS))
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2, 3, 4])
    ap.add_argument("--out", default=OUT)
    ap.add_argument("-w", "--workers", type=int, default=None)
    args = ap.parse_args(argv)

    jobs = [(c, s, seed) for c in args.controllers for s in args.scenarios for seed in args.seeds]
    with Pool(args.workers) as pool:
        rows = pool.map(_job, jobs)
    with open(args.out, "w") as f:
        json.dump(rows, f, indent=1, sort_keys=True)
    print(f"{len(rows)} runs -> {args.out}")


if __name__ == "__main__":
    main()
