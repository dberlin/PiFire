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
#: `off` is the negative control: the identified model still drives the
#: predictor and seeds the integral, it just does not supply the bias.
ARMS = {
    "off": {"bias_from_model": False},
    "bias": {"bias_from_model": True},
}


def _chain(job):
    """One controller/scenario/seed: `cooks` cooks, threading the learned model.

    The core is captured rather than rebuilt so the snapshot comes off the same
    object the run drove; `run_scenario` constructs the core itself and does not
    hand it back.
    """
    controller, scenario_name, seed, cooks, arm, config = job
    rows, snapshot = [], None
    for cook in range(1, cooks + 1):
        holder = {}

        def setup(core, _holder=holder, _snapshot=snapshot):
            _holder["core"] = core
            if _snapshot is not None:
                _holder["restored"] = bool(core.restore_model(dict(_snapshot)))

        started = time.perf_counter()
        row = run_scenario(
            controller, SCENARIOS[scenario_name], seed, plant=PLANT, config=dict(config), core_setup=setup
        )
        row["cook"] = cook
        row["arm"] = arm
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


def _render(rows, arms, cooks):
    out = []

    def w(line=""):
        out.append(line)

    w("Successive cooks, each restoring the model the previous cook learned.")
    w(f"Plant {PLANT}. Median over seeds. Cook 1 starts with no model at all.")
    w()
    for arm in arms:
        w(f"== {arm} ==")
        w(f"{'scenario':<14}{'cook':>5}{'overshoot':>11}{'in5%':>8}{'settle_s':>10}{'identified':>12}{'restored':>10}")
        for scenario_name in SCENARIO_NAMES:
            for cook in range(1, cooks + 1):
                sel = [r for r in rows if r["arm"] == arm and r["scenario"] == scenario_name and r["cook"] == cook]
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
    parser.add_argument("--controller", default="pid_sp")
    parser.add_argument("--seeds", type=int, default=3)
    parser.add_argument("--cooks", type=int, default=COOKS)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--arms", default="off,bias", help="comma-separated arm names (see ARMS)")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    arms = {name: ARMS[name] for name in args.arms.split(",")}
    jobs = [
        (args.controller, scenario_name, seed, args.cooks, arm, config)
        for arm, config in arms.items()
        for scenario_name in SCENARIO_NAMES
        for seed in range(args.seeds)
    ]
    with Pool(args.workers) as pool:
        rows = [row for chain in pool.map(_chain, jobs) for row in chain]

    report = _render(rows, list(arms), args.cooks)
    print(report)
    if args.out:
        with open(args.out, "w") as handle:
            json.dump(rows, handle, indent=2, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
