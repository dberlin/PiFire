"""Successive cooks on one grill, each starting from the model the last one learned.

A single cook cannot show what identification is worth. The identifier promotes
partway up the first cook, so cook 1 climbs on shipped defaults no matter how
good the model turns out to be. The question this answers is what cook 2 looks
like -- the first cook that starts with the chamber already identified, because
Hold restores the stored snapshot before the first control tick.

The carried state is a model snapshot, not a configuration: `get_model_snapshot`
at the end of one cook, `restore_model` at the construction of the next, which is
the same path `controller/runtime/modes/hold.py` drives on a real grill.
"""

import argparse
import json
import os
import statistics
import sys
import time
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from docs.superpowers.experiments.controller_matrix import SCENARIOS, run_scenario  # noqa: E402

PLANT = "MAKGrillSim"
SCENARIO_NAMES = ("steady_225", "steady_350", "steady_450")
COOKS = 3


def _chain(job):
    """One controller/scenario/seed: `cooks` cooks, threading the learned model.

    The core is captured rather than rebuilt so the snapshot comes off the same
    object the run drove; `run_scenario` constructs the core itself and does not
    hand it back.
    """
    controller, scenario_name, seed, cooks = job
    rows, snapshot = [], None
    for cook in range(1, cooks + 1):
        holder = {}

        def setup(core, _holder=holder, _snapshot=snapshot):
            _holder["core"] = core
            if _snapshot is not None:
                _holder["restored"] = bool(core.restore_model(dict(_snapshot)))

        started = time.perf_counter()
        row = run_scenario(controller, SCENARIOS[scenario_name], seed, plant=PLANT, core_setup=setup)
        row["cook"] = cook
        row["wall_s"] = round(time.perf_counter() - started, 1)
        row["model_in"] = None if snapshot is None else dict(snapshot)
        row["restored"] = holder.get("restored")

        core = holder.get("core")
        snapshot = core.get_model_snapshot() if core is not None else None
        row["model_out"] = None if snapshot is None else dict(snapshot)
        row["identified"] = snapshot is not None
        rows.append(row)
    return rows


def _agg(rows, key):
    values = [r[key] for r in rows if r.get(key) is not None]
    return None if not values else statistics.median(values)


def _render(rows, controllers, cooks):
    out = []

    def w(line=""):
        out.append(line)

    w("Successive cooks, each restoring the model the previous cook learned.")
    w(f"Plant {PLANT}. Median over seeds. Cook 1 starts with no model at all.")
    w()
    for controller in controllers:
        w(f"== {controller} ==")
        w(f"{'scenario':<14}{'cook':>5}{'overshoot':>11}{'in5%':>8}{'settle_s':>10}{'identified':>12}{'restored':>10}")
        for scenario_name in SCENARIO_NAMES:
            for cook in range(1, cooks + 1):
                sel = [
                    r
                    for r in rows
                    if r["controller"] == controller and r["scenario"] == scenario_name and r["cook"] == cook
                ]
                if not sel:
                    continue
                identified = sum(1 for r in sel if r.get("identified"))
                restored = sum(1 for r in sel if r.get("restored"))
                settle = _agg(sel, "settle_s")
                w(
                    f"{scenario_name:<14}{cook:>5}"
                    f"{_agg(sel, 'overshoot_f'):>11.1f}"
                    f"{_agg(sel, 'pct_within_5f'):>8.1f}"
                    f"{'--' if settle is None else int(settle):>10}"
                    f"{f'{identified}/{len(sel)}':>12}"
                    f"{f'{restored}/{len(sel)}':>10}"
                )
            w()
    return "\n".join(out)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--controllers", default="pid_sp")
    parser.add_argument("--seeds", type=int, default=3)
    parser.add_argument("--cooks", type=int, default=COOKS)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    controllers = tuple(args.controllers.split(","))
    jobs = [
        (controller, scenario_name, seed, args.cooks)
        for controller in controllers
        for scenario_name in SCENARIO_NAMES
        for seed in range(args.seeds)
    ]
    with Pool(args.workers) as pool:
        rows = [row for chain in pool.map(_chain, jobs) for row in chain]

    report = _render(rows, controllers, args.cooks)
    print(report)
    if args.out:
        with open(args.out, "w") as handle:
            json.dump(rows, handle, indent=2, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
