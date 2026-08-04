#!/usr/bin/env python3

"""What the horizon cap costs, against the control period it has to fit in.

Provenance for the compute claim in `controller/model_promotion._HORIZON_CAP_S`.
That cap is set by the coast a pellet grill can physically have, not by solve
time; what this measures is the supporting claim -- that at the cap the solve
is nowhere near competing with the control cadence, so nothing is being traded
away to get a horizon that long.

Two points per chain length, and only two, because only two are load-bearing:

    n_horizon = 24   the shipped default horizon, the baseline it costs more than
    n_horizon = 96   the cap at the shipped t_step = 25 s

Two chain lengths, because the NLP's state dimension is n_delay + 2 and the
solve cost is a function of both that and the step count:

    n_delay =  8     the shipped default
    n_delay = 12     the largest controller/controllers.json lets an operator pick

Everything else is the shipped configuration at the shipped t_step, warm solves
only -- the cold start is discarded, since it happens once per cook while the
cap is about every step after it.

This is deliberately not a sweep. Solve cost grows superlinearly in n_horizon,
so points past the cap cost minutes to measure and decide nothing: the cap is
not the point where solving gets expensive, and reading it as though it were is
what would put it in the wrong place.

Run:  uv run python -m docs.superpowers.experiments.horizon_solve_cost
Committed output: _horizon_solve_cost.txt beside this file.
"""

import contextlib
import io
import time

import numpy as np

from controller.mpc import Controller, _DEFAULTS

#: The shipped control cadence, and so the period one solve has to fit inside.
T_STEP = 25.0

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
    period_ms = T_STEP * 1e3
    print(f"warm NLP solve cost, shipped config at t_step={T_STEP:.0f} s, {SOLVES} solves after a discarded cold start")
    print(
        f"control period {period_ms:.0f} ms; the 'Pi 5' columns scale by an ASSUMED "
        f"{PI5_SLOWDOWN:.0f}x slowdown to the nominal target"
    )
    print()

    worst_at_cap = 0.0
    for n_delay in CHAINS:
        print(f"=== n_delay = {n_delay} === state dimension {n_delay + 2}")
        print("  n_horizon | horizon s | build s | avg ms | max ms | max % period | Pi 5 max % period")
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

    print(
        f"  worst solve at the cap, over both chain lengths: {worst_at_cap:.0f} ms = "
        f"{100.0 * worst_at_cap / period_ms:.2f} % of the control period, and "
        f"{100.0 * worst_at_cap * PI5_SLOWDOWN / period_ms:.2f} % at the assumed Pi 5 slowdown."
    )
    print("  The cap is a bound on believable coasts. This is what it costs to honour one.")


if __name__ == "__main__":
    main()
