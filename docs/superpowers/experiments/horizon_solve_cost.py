#!/usr/bin/env python3

"""Measure configured MPC horizon solve costs.

The controller builds precisely the configured `n_horizon`. This experiment
records representative warm-solve costs for the shipped 24-step configuration
and a 96-step comparison at the shipped discretization.

Run: `uv run python -m docs.superpowers.experiments.horizon_solve_cost`
"""

import contextlib
import io
import time

import numpy as np

from controller.mpc import Controller, _DEFAULTS

#: The shipped PREDICTION discretization -- how far apart the steps of the
#: horizon are. It sets how many steps a given span of foresight costs, not how
#: often the NLP is solved.
T_STEP = 25.0

#: The shipped re-solve cadence, and so the period one solve has to fit inside.
#: `Controller.update()` runs a full solve on every call and the runtime worker
#: loop calls it once per `control_period` (controllers.json: "How often the MPC
#: re-solves and updates the actuators"), so this and not t_step is what a solve
#: time is a fraction of.
CONTROL_PERIOD_S = 5.0

#: The shortest cadence controllers.json admits, reported alongside the default
#: because it is settings-reachable and it is where the margin is thinnest.
CONTROL_PERIOD_MIN_S = 1.0

#: How much slower the same NLP is assumed to be on PiFire's nominal target, a
#: Raspberry Pi 5, than on the x86 machine this runs on. An assumption, not a
#: measurement -- no Pi 5 is available here -- so it is deliberately past the
#: ~3x single-core gap the published CPU benchmarks show, to cover the smaller
#: cache and slower memory an interior-point solve is sensitive to.
PI5_SLOWDOWN = 6.0

CHAINS = (8, 12)

#: The shipped horizon and the cap at the shipped t_step. Fixed, not swept.
HORIZONS = (24, 96)

#: Timed warm solves per point.
SOLVES = 15

SETPOINT_C = 110.0
MEASURED_C = 100.0
CYCLE = {"u_min": 0.1, "u_max": 0.9, "HoldCycleTime": 25}


def measure(n_horizon, n_delay):
    """Build cost and warm solve costs for one (horizon, chain length) point."""
    cfg = dict(_DEFAULTS, n_horizon=n_horizon, n_delay=n_delay, t_step=T_STEP)
    with contextlib.redirect_stdout(io.StringIO()):
        t0 = time.perf_counter()
        c = Controller(cfg, "C", dict(CYCLE))
        build_s = time.perf_counter() - t0
        assert c.mpc is not None, "the NLP is what is being measured, not the net policy"
        assert c.mpc.settings.n_horizon == n_horizon, "the horizon built is the horizon asked for"
        c.set_target(SETPOINT_C)
        c.update(MEASURED_C)  # cold solve, discarded
        ms = []
        for _ in range(SOLVES):
            t0 = time.perf_counter()
            c.update(MEASURED_C)
            ms.append((time.perf_counter() - t0) * 1e3)
    return build_s, np.array(ms)


def main():
    period_ms = CONTROL_PERIOD_S * 1e3
    min_period_ms = CONTROL_PERIOD_MIN_S * 1e3
    print(f"warm NLP solve cost, shipped config at t_step={T_STEP:.0f} s, {SOLVES} solves after a discarded cold start")
    print(
        f"the budget is control_period, NOT t_step: {period_ms:.0f} ms shipped, "
        f"{min_period_ms:.0f} ms at controllers.json's minimum"
    )
    print(f"the 'Pi 5' columns scale by an ASSUMED {PI5_SLOWDOWN:.0f}x slowdown to the nominal target")
    print()

    worst_at_cap = 0.0
    for n_delay in CHAINS:
        print(f"=== n_delay = {n_delay} === state dimension {n_delay + 2}")
        print("  n_horizon | horizon s | build s | avg ms | max ms | max % of 5 s | Pi 5 max % of 5 s")
        for n_horizon in HORIZONS:
            build_s, ms = measure(n_horizon, n_delay)
            if n_horizon == max(HORIZONS):
                worst_at_cap = max(worst_at_cap, ms.max())
            print(
                f"  {n_horizon:9d} | {n_horizon * T_STEP:9.0f} | {build_s:7.2f} | "
                f"{ms.mean():6.1f} | {ms.max():6.1f} | {100.0 * ms.max() / period_ms:12.2f} | "
                f"{100.0 * ms.max() * PI5_SLOWDOWN / period_ms:17.2f}"
            )
        print()

    pi5_ms = worst_at_cap * PI5_SLOWDOWN
    print(f"  worst solve at {max(HORIZONS)} steps, over both chain lengths: {worst_at_cap:.1f} ms measured,")
    print(f"  {pi5_ms:.0f} ms at the assumed Pi 5 slowdown.")
    print(
        f"    vs control_period {CONTROL_PERIOD_S:.0f} s (shipped): "
        f"{100.0 * worst_at_cap / period_ms:.2f} % measured, {100.0 * pi5_ms / period_ms:.1f} % assumed"
    )
    print(
        f"    vs control_period {CONTROL_PERIOD_MIN_S:.0f} s (minimum): "
        f"{100.0 * worst_at_cap / min_period_ms:.2f} % measured, {100.0 * pi5_ms / min_period_ms:.1f} % assumed"
        f"{'  <-- OVER the period' if pi5_ms > min_period_ms else ''}"
    )
    print()
    print("  Overrunning control_period is a degraded cadence, not a loss of control: the worker")
    print("  loop calls update() again when the solve returns, so the controller re-solves less")
    print("  often than configured against a plant whose own time constants are minutes. The")
    print("  shipped cadence having margin is the load-bearing reading; the 1 s cadence is a")
    print("  documented cost of an operator setting, not a bound on what may be built.")


if __name__ == "__main__":
    main()
