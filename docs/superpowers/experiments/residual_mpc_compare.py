#!/usr/bin/env python3
"""Compare learned residual MPC with the pre-change learned controller.

Each plant/seed runs three successive 325 F cooks. Cook one is deliberately
identical: the residual penalty and analytic equilibrium are admitted only
after a cook has identified a thermal model. The baseline arm disables both
private production seams in an isolated worker.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from multiprocessing import get_context
from typing import Any

import numpy as np

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
)

from docs.superpowers.experiments.controller_matrix import (  # noqa: E402
    SCENARIOS,
    run_scenario,
)

_run_scenario: Any = run_scenario

ARMS = ("baseline", "learned_residual")
PLANTS = ("GrillSim", "MAKGrillSim")
SEEDS = (0, 1, 2)
COOKS = 3
SCENARIO = "steady_325"
RESIDUAL_WEIGHT = 1_000.0
TRANSITION_LIMIT_PER_HOUR = 360.0
METRICS = (
    "pct_within_5f",
    "overshoot_f",
    "settle_s",
    "rmse_f",
    "steady_peak_to_peak_f",
    "auger_on_time_s",
    "requested_realized_load_error",
    "transitions_per_hour",
    "deadline_misses",
    "stale_result_episodes",
)


def _compact(row):
    compact = {
        key: row.get(key)
        for key in (
            "arm",
            "plant",
            "seed",
            "cook",
            "scenario",
            "effective_run",
            "reachability",
            "max_authority",
            *METRICS,
        )
    }
    durations = row.get("solver_duration_seconds") or ()
    compact["solver_p99_ms"] = None if not durations else float(np.percentile(durations, 99) * 1_000.0)
    refit = row.get("refit") or {}
    compact["refit"] = {
        key: refit.get(key) for key in ("accepted", "reason", "params", "rmse", "samples", "band_c", "nfev")
    }
    return compact


def _chain(job):
    arm, plant, seed = job
    import controller.mpc as mpc

    production_weight = getattr(mpc, "_LEARNED_RESIDUAL_WEIGHT")
    if arm == "baseline":
        setattr(mpc, "_LEARNED_RESIDUAL_WEIGHT", 0.0)
    elif production_weight != RESIDUAL_WEIGHT:
        raise RuntimeError(f"production residual weight is {production_weight}, expected {RESIDUAL_WEIGHT}")

    rows = []
    config = {}
    for cook in range(1, COOKS + 1):

        def setup(controller):
            if arm == "baseline":
                controller._equilibrium_load = lambda target, disturbance: 0.0

        raw = _run_scenario(
            "mpc",
            SCENARIOS[SCENARIO],
            seed,
            plant=plant,
            config=config,
            refit=True,
            core_setup=setup,
        )
        raw.update(arm=arm, cook=cook)
        rows.append(_compact(raw))
        refit = raw.get("refit") or {}
        if refit.get("accepted") and refit.get("params"):
            config = dict(refit["params"])
    return rows


def _median(rows, metric):
    values = [row[metric] for row in rows if row.get(metric) is not None]
    return None if not values else float(statistics.median(values))


def _summary(rows):
    aggregates = {}
    for arm in ARMS:
        aggregates[arm] = {}
        for plant in PLANTS:
            aggregates[arm][plant] = {}
            for cook in range(1, COOKS + 1):
                selected = [row for row in rows if row["arm"] == arm and row["plant"] == plant and row["cook"] == cook]
                aggregates[arm][plant][str(cook)] = {metric: _median(selected, metric) for metric in METRICS} | {
                    "solver_p99_ms": _median(selected, "solver_p99_ms")
                }

    paired = {}
    for plant in PLANTS:
        paired[plant] = {}
        for cook in range(1, COOKS + 1):
            deltas = {}
            for metric in METRICS:
                values = []
                for seed in SEEDS:
                    baseline = next(
                        row
                        for row in rows
                        if (row["arm"], row["plant"], row["seed"], row["cook"]) == ("baseline", plant, seed, cook)
                    )
                    candidate = next(
                        row
                        for row in rows
                        if (row["arm"], row["plant"], row["seed"], row["cook"])
                        == ("learned_residual", plant, seed, cook)
                    )
                    if baseline.get(metric) is not None and candidate.get(metric) is not None:
                        values.append(candidate[metric] - baseline[metric])
                deltas[metric] = None if not values else float(statistics.median(values))
            paired[plant][str(cook)] = deltas

    indexed = {(row["arm"], row["plant"], row["seed"], row["cook"]): row for row in rows}
    first_cook_unchanged = all(
        indexed[("learned_residual", plant, seed, 1)].get(metric) == indexed[("baseline", plant, seed, 1)].get(metric)
        for plant in PLANTS
        for seed in SEEDS
        for metric in METRICS
    )
    third_cook_quality_improved = all(
        indexed[("learned_residual", plant, seed, 3)]["overshoot_f"]
        < indexed[("baseline", plant, seed, 3)]["overshoot_f"]
        and indexed[("learned_residual", plant, seed, 3)]["steady_peak_to_peak_f"]
        < indexed[("baseline", plant, seed, 3)]["steady_peak_to_peak_f"]
        and indexed[("learned_residual", plant, seed, 3)]["rmse_f"] < indexed[("baseline", plant, seed, 3)]["rmse_f"]
        and indexed[("learned_residual", plant, seed, 3)]["pct_within_5f"]
        > indexed[("baseline", plant, seed, 3)]["pct_within_5f"]
        for plant in PLANTS
        for seed in SEEDS
    )
    safety_bounded = all(
        row["deadline_misses"] == 0
        and row["stale_result_episodes"] == 0
        and row["transitions_per_hour"] <= TRANSITION_LIMIT_PER_HOUR
        and row["requested_realized_load_error"] <= 2.0 / SCENARIOS[SCENARIO].duration_s
        for row in rows
        if row["arm"] == "learned_residual"
    )
    pellet_change_bounded = all(
        indexed[("learned_residual", plant, seed, 3)]["auger_on_time_s"]
        - indexed[("baseline", plant, seed, 3)]["auger_on_time_s"]
        <= 0.002 * indexed[("baseline", plant, seed, 3)]["auger_on_time_s"]
        for plant in PLANTS
        for seed in SEEDS
    )
    decision = {
        "first_cook_unchanged": first_cook_unchanged,
        "third_cook_quality_improved": third_cook_quality_improved,
        "safety_and_actuation_bounded": safety_bounded,
        "third_cook_pellet_increase_within_0_2_percent": pellet_change_bounded,
    }
    decision["ship_learned_residual"] = all(decision.values())
    return {"aggregates": aggregates, "paired_median_delta": paired, "decision": decision}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default="docs/superpowers/experiments/_residual_mpc_compare.json",
    )
    parser.add_argument("--workers", type=int, default=min(6, len(PLANTS) * len(SEEDS)))
    args = parser.parse_args(argv)

    jobs = [(arm, plant, seed) for arm in ARMS for plant in PLANTS for seed in SEEDS]
    started = time.perf_counter()
    with get_context("fork").Pool(args.workers, maxtasksperchild=1) as pool:
        rows = [row for chain in pool.map(_chain, jobs) for row in chain]
    artifact: dict[str, object] = {
        "schema_version": 1,
        "header": {
            "arms": list(ARMS),
            "plants": list(PLANTS),
            "seeds": list(SEEDS),
            "cooks": COOKS,
            "scenario": SCENARIO,
            "residual_weight": RESIDUAL_WEIGHT,
            "baseline_seams": [
                "controller.mpc._LEARNED_RESIDUAL_WEIGHT=0",
                "Controller._equilibrium_load=zero",
            ],
            "command": ".venv/bin/python docs/superpowers/experiments/residual_mpc_compare.py",
        },
        "runtime_seconds": round(time.perf_counter() - started, 3),
        "rows": rows,
    }
    summary = _summary(rows)
    artifact["summary"] = summary
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(artifact, handle, indent=1, sort_keys=True, allow_nan=False)
        handle.write("\n")
    print(json.dumps(summary["decision"], sort_keys=True))
    return 0 if summary["decision"]["ship_learned_residual"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
