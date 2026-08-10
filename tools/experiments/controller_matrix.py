#!/usr/bin/env python3
"""Scenario matrix for a controller against GrillSim.

Drives a controller core directly -- no Hold mode or datastore -- while
resolving the same defaults and controller manifest a fresh Hold run would use.
Each JSON row contains the immutable effective configuration used to construct
the core, its framed-pulse actuation configuration, and feasibility derived
from plant/actuator authority rather than from the row's score.

A `lid_open` window opens the physical lid for the whole window. Every
executable controller uses the production `PulseScheduler` with its actual
2-second pulse/20-second frame timing. A lid or manual inhibit accounts
observed delivery to that instant, resets/discards pulse credit, and keeps the
auger off until the inhibit clears. `cycle_config` can still alter controller
settings and duty authority, but never selects or retimes the scheduler. The
feedback is actually delivered interval duty, reported only at completed
producing control boundaries.
"""

import argparse
import importlib
import json
import time
from dataclasses import dataclass, field
from enum import StrEnum
from multiprocessing import Pool

import numpy as np

from controller.grill_sim import GrillSim, MAKGrillSim  # noqa: E402
from controller.runtime.logic.pulse import PulseResetReason, PulseScheduler  # noqa: E402
from controller.runtime.runner import ControllerType, SyncControllerRunner  # noqa: E402
from grillplat.actuator_capabilities import AUGER_TIMING  # noqa: E402

OUT = "./docs/superpowers/experiments/_matrix_baseline.json"
STEADY_TAIL_S = 30 * 60

# Plants a run may be driven against, by name. Resolved out of this module's
# globals at call time rather than captured in a mapping here, so a test that
# substitutes `controller_matrix.GrillSim` is still substituting the plant the
# loop builds.
PLANTS = ("GrillSim", "MAKGrillSim")


class ReachabilityState(StrEnum):
    """Whether this run's highest target is within its plant/actuator authority."""

    REACHABLE = "reachable"
    UNREACHABLE_HIGH = "unreachable_high"
    UNKNOWN_AUTHORITY = "unknown_authority"


def _snapshot(value):
    """Make a JSON-safe value that cannot alias live settings or controller state."""
    return json.loads(json.dumps(value, allow_nan=False, default=str, sort_keys=True))


def _effective_configuration(controller, config, cycle_config):
    """Resolve the shipped defaults and controller manifest for this one run.

    `default_settings()` rebuilds controller configuration from
    ``controller/controllers.json`` each call. Importing its module rather than
    its function lets a test (or a newly installed manifest) change that source
    after this experiment module was imported. `config` and `cycle_config` are
    the only explicit override seams; each wins over the fresh shipped value.
    """
    settings = importlib.import_module("common.defaults").default_settings()
    controller_config = dict(settings["controller"]["config"].get(controller, {}))
    effective_cycle = dict(settings["cycle_data"])
    controller_override = dict(config or {})
    cycle_override = dict(cycle_config or {})
    controller_config.update(controller_override)
    effective_cycle.update(cycle_override)
    return controller_config, effective_cycle, controller_override, cycle_override


def _c_to_f(c):
    return c * 9.0 / 5.0 + 32.0


@dataclass
class Scenario:
    name: str
    duration_s: int
    # (start_second, setpoint_F); the first entry must start at 0.
    setpoints: list[tuple[int, float]] = field(default_factory=list)
    # (start_second, duration_s) physical-lid windows. Framed-pulse inhibition
    # lasts for this run's current LidOpenPauseTime.
    lid_open: list[tuple[int, int]] = field(default_factory=list)
    # (start_second, duration_s) deterministic manual-inhibit windows. They
    # model the auger being unavailable to the controller and exist only for
    # this experiment's framed-pulse accounting contracts.
    manual_inhibit: list[tuple[int, int]] = field(default_factory=list)


# This fixed target is above the full-duty authority calculated for both
# unchanged plants.  It is a feasibility probe, never a ranked quality row.
CAPABILITY_UNREACHABLE_HIGH_TARGET_F = 2_000.0
CAPABILITY_UNREACHABLE_HIGH_SCENARIO = "capability_unreachable_high_2000"

SCENARIOS = {
    "steady_225": Scenario("steady_225", 3 * 3600 + 1800, [(0, 225.0)]),
    "steady_325": Scenario("steady_325", 3 * 3600, [(0, 325.0)]),
    "steady_350": Scenario("steady_350", 3 * 3600 + 1800, [(0, 350.0)]),
    "steady_450": Scenario("steady_450", 3 * 3600 + 1800, [(0, 450.0)]),
    "step_225_275": Scenario("step_225_275", 4 * 3600, [(0, 225.0), (2 * 3600, 275.0)]),
    CAPABILITY_UNREACHABLE_HIGH_SCENARIO: Scenario(
        CAPABILITY_UNREACHABLE_HIGH_SCENARIO,
        3 * 3600,
        [(0, CAPABILITY_UNREACHABLE_HIGH_TARGET_F)],
    ),
    "lid_open_225": Scenario("lid_open_225", 3 * 3600, [(0, 225.0)], [(2 * 3600, 120)]),
}


def _setpoint_at(scenario, t):
    sp = scenario.setpoints[0][1]
    for start, value in scenario.setpoints:
        if t >= start:
            sp = value
    return sp


def _lid_open_at(scenario, t):
    """The lid is physically open, so the chamber leaks heat to ambient."""
    return any(start <= t < start + duration for start, duration in scenario.lid_open)


def _lid_paused_at(scenario, t, cycle_data):
    """Framed-pulse inhibition uses this run's current pause setting."""
    pause_s = cycle_data["LidOpenPauseTime"]
    return any(start <= t < start + pause_s for start, _ in scenario.lid_open)


def _manual_inhibited_at(scenario, t):
    return any(start <= t < start + duration for start, duration in scenario.manual_inhibit)


def _manual_inhibit_start_at(scenario, t):
    return any(start == t for start, _ in scenario.manual_inhibit)


def _lid_pause_start_at(scenario, t):
    """True on the tick a lid event resets framed scheduler credit."""
    return any(start == t for start, _ in scenario.lid_open)


def _recovery_s(err_from_lid):
    """Seconds from lid opening to the first in-band sample after the cold trough.

    Probe/transport lag can briefly re-enter the 5 F band before the lid
    excursion reaches its minimum. That transient is not recovery: the
    relevant return begins only after the coldest error sample.
    """
    if not np.any(np.abs(err_from_lid) > 5.0):
        return 0
    trough = int(np.argmin(err_from_lid))
    back = np.flatnonzero(np.abs(err_from_lid[trough:]) <= 5.0)
    return None if back.size == 0 else trough + int(back[0])


def _report(core, ratio, source_name, t, requested=None):
    """Report applied duty when the controller can hear it; no-op otherwise."""
    setter = getattr(core, "set_output", None)
    if setter is None:
        return
    from controller.applied_output import AppliedOutput, OutputSource

    setter(AppliedOutput(ratio=ratio, source=OutputSource(source_name), timestamp=float(t), requested=requested))


class _SimClock:
    """Callable replacement for `time.time`, advanced once per simulated
    second so a controller reading the wall clock for its own `dt` observes
    the step size this harness actually models, not the wall-clock time
    between tight-loop calls."""

    def __init__(self, t0):
        self.t = t0

    def __call__(self):
        return self.t


def _authority(core, cycle_data):
    cycle_max = float(cycle_data["u_max"])
    controller_max = float(getattr(core, "u_max", cycle_max))
    effective_max = min(cycle_max, controller_max)
    binding = "controller_max_duty" if controller_max < cycle_max else "cycle_max_duty"
    return cycle_max, controller_max, effective_max, binding


def _maximum_plant_temperature_f(plant, maximum_duty):
    """Derive a no-lid maximum from plant and actuator authority, not metrics."""
    maximum = getattr(plant, "maximum_reachable_temperature_f", None)
    if callable(maximum):
        return float(maximum(maximum_duty))
    required = ("feed_rate", "H", "h_amb0", "sigma", "T_amb")
    if not all(hasattr(plant, name) for name in required):
        return None
    ambient = float(plant.T_amb)
    heat = maximum_duty * float(plant.feed_rate) * float(plant.H)
    loss, sigma = float(plant.h_amb0) * 1.3, float(plant.sigma)
    low, high = ambient, ambient + 5_000.0
    for _ in range(80):
        chamber = (low + high) / 2.0
        rejected = loss * (chamber - ambient) + sigma * ((chamber + 273.15) ** 4 - (ambient + 273.15) ** 4)
        if rejected < heat:
            low = chamber
        else:
            high = chamber
    return _c_to_f((low + high) / 2.0)


def _feasibility(core, cycle_data, plant, scenario):
    cycle_max, controller_max, effective_max, binding = _authority(core, cycle_data)
    plant_max = _maximum_plant_temperature_f(plant, effective_max)
    target = max(value for _, value in scenario.setpoints)
    if plant_max is None:
        state = ReachabilityState.UNKNOWN_AUTHORITY
    elif target > plant_max:
        state, binding = ReachabilityState.UNREACHABLE_HIGH, "plant_max_temperature"
    else:
        state = ReachabilityState.REACHABLE
    return state, {
        "target_f": float(target),
        "cycle_max_duty": cycle_max,
        "controller_max_duty": controller_max,
        "effective_max_duty": effective_max,
        "plant_max_temp_f": plant_max,
        "binding": binding,
    }


def rank_reachable_rows(rows, *, key):
    """Return rows eligible for target-achievement comparisons, best first."""
    return sorted(
        (row for row in rows if row["reachability"] == ReachabilityState.REACHABLE.value),
        key=lambda row: row[key],
    )


def run_scenario(
    controller,
    scenario,
    seed,
    *,
    plant="GrillSim",
    config=None,
    cycle_config=None,
    refit=False,
    core_setup=None,
    output_transform=None,
    trace_sink=None,
):
    """Drive one controller/plant scenario with the configuration shipping now.

    The only override seams are ``config`` (controller options) and
    ``cycle_config`` (Hold cycle settings); both override fresh
    ``default_settings()`` values for this call only. Rows retain independent
    JSON-safe snapshots so subsequent settings/manifest changes cannot alter
    recorded evidence.
    """
    core_config, cycle_data, controller_override, cycle_override = _effective_configuration(
        controller, config, cycle_config
    )
    clock = _SimClock(-float(AUGER_TIMING.frame_s))
    real_time_time = time.time
    time.time = clock
    try:
        mod = importlib.import_module(f"controller.{controller}")
        core = mod.Controller(dict(core_config), "F", dict(cycle_data))
        if core_setup is not None:
            core_setup(core)
        runner = (
            SyncControllerRunner(core, controller_type=ControllerType(controller)) if trace_sink is not None else None
        )
        plant_name = plant
        plant_instance = globals()[plant_name](seed=seed)
        scheduler = PulseScheduler()
        cycle_max, controller_max, effective_max, _ = _authority(core, cycle_data)
        del cycle_max, controller_max
        effective_run = _snapshot(
            {
                "controller_config": core_config,
                "cycle_config": cycle_data,
                "actuation_mode": "framed_pulse",
                "scheduler": {
                    "kind": "framed_pulse",
                    "frame_seconds": float(scheduler.timing.frame_s),
                    "pulse_seconds": float(scheduler.timing.pulse_s),
                },
                "pulse_timing": {
                    "frame_seconds": float(scheduler.timing.frame_s),
                    "pulse_seconds": float(scheduler.timing.pulse_s),
                },
                "plant": plant_name,
                "seed": seed,
                "scenario": scenario.name,
                "overrides": {"controller": controller_override, "cycle": cycle_override},
            }
        )

        setpoint = _setpoint_at(scenario, 0)
        if runner is None:
            core.set_target(setpoint)
        else:
            runner.set_target(setpoint)
        period = float(
            (core.get_control_period() if runner is None else runner.control_period()) or scheduler.timing.frame_s
        )
        if trace_sink is not None:
            trace_sink.start(
                core=core,
                effective_run=effective_run,
                control_period_s=period,
                setpoint=setpoint,
            )
        requested, fan_frac = 0.0, 1.0
        next_solve = 0.0
        actual_auger_on = False
        feedback_start, feedback_delivered, feedback_requested = 0.0, 0.0, 0.0
        latest_result = None
        temps, duties = [], []
        delivered_request_s = delivered_actual_s = delivered_window_s = 0.0
        solve_durations, deadline_misses, stale_episodes, settle_from = [], [], 0, None
        for t in range(scenario.duration_s):
            clock.t = float(t)
            new_sp = _setpoint_at(scenario, t)
            if new_sp != setpoint:
                setpoint = new_sp
                if runner is None:
                    core.set_target(setpoint)
                else:
                    runner.set_target(setpoint)
                next_solve = t + period
                settle_from = None

            lid_open = _lid_open_at(scenario, t)
            lid_paused = _lid_paused_at(scenario, t, cycle_data)
            lid_pause_start = _lid_pause_start_at(scenario, t)
            manual_inhibit = _manual_inhibited_at(scenario, t)
            manual_start = _manual_inhibit_start_at(scenario, t)
            temp_f = _c_to_f(plant_instance.measured())
            solved = t >= next_solve
            if solved:
                next_solve = t + period
                if runner is None:
                    raw = core.update(temp_f)
                    if isinstance(raw, dict):
                        requested = float(raw.get("cycle_ratio", 0.0))
                        fan = raw.get("fan") or {}
                        if fan.get("duty") is not None:
                            fan_frac = float(fan["duty"]) / 100.0
                    else:
                        requested = float(raw)
                    diagnostics = getattr(core, "trace_diagnostics", lambda: None)()
                    duration = getattr(diagnostics, "solve_duration_seconds", None)
                    if duration is not None:
                        solve_durations.append(float(duration))
                        deadline_misses.append(int(diagnostics.deadline_miss_count))
                        stale_episodes += int(diagnostics.stale_state.value == "stale")
                else:
                    latest_result = runner.latest_from(temp_f)
                    requested = float(latest_result.cycle_ratio)
                    fan = latest_result.fan or {}
                    if fan.get("duty") is not None:
                        fan_frac = float(fan["duty"]) / 100.0
                    diagnostics = latest_result.diagnostics
                    if diagnostics is not None:
                        solve_durations.append(float(latest_result.solve_duration_seconds))
                        deadline_misses.append(int(latest_result.deadline_miss_count))
                        stale_episodes += int(latest_result.stale_state.value == "stale")
                if output_transform is not None:
                    requested = float(output_transform(requested))
                requested = min(max(requested, 0.0), effective_max)
                if trace_sink is not None:
                    assert latest_result is not None
                    trace_sink.solved(t=float(t), result=latest_result, requested=requested)

            inhibited = lid_paused or manual_inhibit
            reset_reason = PulseResetReason.MANUAL if manual_start else PulseResetReason.LID
            if lid_pause_start or manual_start:
                # Account the observed interval first, then discard the
                # interrupted credit exactly as Hold does before preemption.
                decision = scheduler.advance(requested, float(t), actual_auger_on)
                if trace_sink is not None and latest_result is not None:
                    trace_sink.frames(
                        t=float(t),
                        revision=latest_result.revision,
                        frames=decision.completed_frames,
                    )
                for frame in decision.completed_frames:
                    if frame.complete:
                        window_s = frame.nominal_end_s - frame.nominal_start_s
                        delivered_request_s += frame.latched_request * window_s
                        delivered_actual_s += frame.delivered_on_s
                        delivered_window_s += window_s
                if trace_sink is not None and latest_result is not None and t > feedback_start:
                    delivered = decision.delivered_on_s - feedback_delivered
                    trace_sink.applied(
                        interval_start_s=feedback_start,
                        interval_end_s=float(t),
                        result=latest_result,
                        requested=feedback_requested,
                        realized=delivered / (t - feedback_start),
                        sample_complete=False,
                    )
                scheduler.reset(reset_reason)
                actual_auger_on = False
                # PulseScheduler retains its monotone total across reset;
                # baseline this new feedback interval at that total so an
                # interrupted frame cannot be charged again after release.
                feedback_start = float(t)
                feedback_delivered = decision.delivered_on_s
            elif inhibited:
                actual_auger_on = False
            else:
                decision = scheduler.advance(requested, float(t), actual_auger_on)
                if trace_sink is not None and latest_result is not None:
                    trace_sink.frames(
                        t=float(t),
                        revision=latest_result.revision,
                        frames=decision.completed_frames,
                    )
                for frame in decision.completed_frames:
                    if frame.complete:
                        window_s = frame.nominal_end_s - frame.nominal_start_s
                        delivered_request_s += frame.latched_request * window_s
                        delivered_actual_s += frame.delivered_on_s
                        delivered_window_s += window_s
                actual_auger_on = decision.command_on
                if solved and t > feedback_start:
                    delivered = decision.delivered_on_s - feedback_delivered
                    realized = delivered / (t - feedback_start)
                    _report(
                        core,
                        realized,
                        "controller",
                        t,
                        requested=feedback_requested,
                    )
                    if trace_sink is not None and latest_result is not None:
                        trace_sink.applied(
                            interval_start_s=feedback_start,
                            interval_end_s=float(t),
                            result=latest_result,
                            requested=feedback_requested,
                            realized=realized,
                        )
                    feedback_start = float(t)
                    feedback_delivered = decision.delivered_on_s
            auger_frac = float(actual_auger_on)
            if solved:
                feedback_requested = requested

            plant_instance.step(
                auger_on=auger_frac,
                fan_frac=0.0 if lid_paused else fan_frac,
                lid_open=lid_open,
            )
            temps.append(temp_f)
            duties.append(auger_frac)
            if abs(temp_f - setpoint) <= 5.0:
                if settle_from is None:
                    settle_from = t
            else:
                settle_from = None

        final_decision = scheduler.advance(requested, float(scenario.duration_s), actual_auger_on)
        for frame in final_decision.completed_frames:
            if frame.complete:
                window_s = frame.nominal_end_s - frame.nominal_start_s
                delivered_request_s += frame.latched_request * window_s
                delivered_actual_s += frame.delivered_on_s
                delivered_window_s += window_s

        temps = np.asarray(temps)
        duties = np.asarray(duties)
        sp_series = np.asarray([_setpoint_at(scenario, t) for t in range(scenario.duration_s)])
        err = temps - sp_series
        if trace_sink is not None and latest_result is not None and float(scenario.duration_s) > feedback_start:
            trace_sink.frames(
                t=float(scenario.duration_s),
                revision=latest_result.revision,
                frames=final_decision.completed_frames,
            )
            delivered = final_decision.delivered_on_s - feedback_delivered
            realized = delivered / (float(scenario.duration_s) - feedback_start)
            trace_sink.applied(
                interval_start_s=feedback_start,
                interval_end_s=float(scenario.duration_s),
                result=latest_result,
                requested=feedback_requested,
                realized=realized,
            )

        lid_start = min((start for start, _ in scenario.lid_open), default=None)
        reachability, max_authority = _feasibility(core, cycle_data, plant_instance, scenario)
        result = {
            "controller": controller,
            "scenario": scenario.name,
            "plant": plant_name,
            "seed": seed,
            "effective_run": effective_run,
            "reachability": reachability.value,
            "max_authority": _snapshot(max_authority),
            "iae": float(np.abs(err).sum()),
            "pct_within_5f": float((np.abs(err) <= 5.0).mean() * 100.0),
            "overshoot_f": float(err.max()),
            "undershoot_f": float(err.min()),
            "settle_s": None if settle_from is None else int(settle_from),
            "mean_duty": float(duties.mean()),
            "std_duty": float(duties.std()),
            "final_temp_f": float(temps[-1]),
            "lid_min_temp_f": None if lid_start is None else float(temps[lid_start:].min()),
            "rmse_f": float(np.sqrt(np.mean(err**2))),
            "steady_peak_to_peak_f": float(np.ptp(temps[-min(len(temps), STEADY_TAIL_S) :])),
            "auger_on_time_s": float(duties.sum()),
            "pellet_proxy": float(duties.sum()),
            "requested_realized_load_error": (
                abs(delivered_request_s - delivered_actual_s) / delivered_window_s if delivered_window_s else 0.0
            ),
            "solver_duration_seconds": tuple(solve_durations),
            "deadline_misses": max(deadline_misses, default=0),
            "stale_result_episodes": stale_episodes,
            "transitions_per_hour": float(np.count_nonzero(np.diff(duties)) * 3600.0 / len(duties)),
            "lid_recovery_s": None if lid_start is None else _recovery_s(err[lid_start:]),
        }
        status = getattr(core, "get_status", lambda: None)()
        if status is not None:
            result["status"] = _snapshot(status)
        cfg = getattr(core, "cfg", None)
        if cfg is not None and "n_horizon" in cfg:
            result["configured_n_horizon"] = int(cfg["n_horizon"])
        if refit:
            result["refit"] = _refit_after_cook(core)
        if trace_sink is not None:
            result["trace_session"] = trace_sink.close()
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
    regeneration_command = (
        "uv run --no-sync python docs/superpowers/experiments/controller_matrix.py "
        "--controllers pid_sp mpc --scenarios steady_225 steady_350 steady_450 "
        "step_225_275 capability_unreachable_high_2000 lid_open_225 --seeds 0 1 2 3 4 "
        "--plants GrillSim MAKGrillSim --out docs/superpowers/experiments/_matrix_baseline.json"
    )
    ap = argparse.ArgumentParser(
        description="Run deterministic shipped-controller scenarios against GrillSim.",
        epilog=f"Regenerate the committed evidence only after validation:\n  {regeneration_command}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--controllers", nargs="+", default=["pid_sp", "mpc"])
    ap.add_argument("--scenarios", nargs="+", default=sorted(SCENARIOS))
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2, 3, 4])
    ap.add_argument("--plants", nargs="+", default=["GrillSim"], choices=PLANTS)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("-w", "--workers", type=int, default=None)
    args = ap.parse_args(argv)
    jobs = [
        (controller, scenario, seed, plant)
        for controller in args.controllers
        for scenario in args.scenarios
        for seed in args.seeds
        for plant in args.plants
    ]
    with Pool(args.workers) as pool:
        rows = pool.map(_job, jobs)

    payload = {
        "header": {
            "format_version": 2,
            "regeneration_command": regeneration_command,
            "effective_runs": [row["effective_run"] for row in rows],
        },
        "rows": rows,
        "summary": {
            "run_count": len(rows),
            "reachable_count": sum(row["reachability"] == ReachabilityState.REACHABLE.value for row in rows),
            "unreachable_high_count": sum(
                row["reachability"] == ReachabilityState.UNREACHABLE_HIGH.value for row in rows
            ),
            "unknown_authority_count": sum(
                row["reachability"] == ReachabilityState.UNKNOWN_AUTHORITY.value for row in rows
            ),
        },
    }
    with open(args.out, "w") as f:
        json.dump(payload, f, indent=1, sort_keys=True)
    print(f"{len(rows)} runs -> {args.out}")


if __name__ == "__main__":
    main()
