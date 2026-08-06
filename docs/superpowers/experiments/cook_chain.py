"""Successive cooks on one grill, each starting from the model the last one learned.

A single cook cannot show what identification is worth. Cook 1 climbs on shipped
defaults no matter how good the model turns out to be -- pid_sp's identifier
promotes only partway up it, and MPC's refit cannot run until the cook it fits
has finished. The question this answers is what cook 2 looks like, the first
cook that starts with the chamber already identified.

The carried state is a model snapshot, not a configuration: `get_model_snapshot`
at the end of one cook, `restore_model` at the construction of the next, which is
the same path `controller/runtime/modes/hold.py` drives on a real grill.

Controllers reach that snapshot by different routes, and both run here. pid_sp
identifies continuously while it cooks, so its snapshot is ready the moment the
cook ends. MPC learns only in one batch afterwards, so this asks it for that
refit -- `refit_from_cook`, gated and judged exactly as Hold's teardown does --
before taking the snapshot. A controller with no refit is unaffected.
"""

import argparse
import json
import os
import statistics
import sys
import time
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from docs.superpowers.experiments.controller_matrix import SCENARIOS, _refit_after_cook, run_scenario  # noqa: E402

PLANT = "MAKGrillSim"
SCENARIO_NAMES = ("steady_225", "steady_350", "steady_450")
COOKS = 3
#: An arm is a controller configuration plus whether the chain is allowed to
#: carry anything between its cooks. `learn` off is the negative control every
#: chain needs: the same cooks in the same order with the model thrown away
#: each time, which is what separates a real improvement from a seed that
#: happened to run second.
#:
#: `off`/`bias` are pid_sp's arms, where the question is not whether the model
#: is carried but whether the carried model is allowed to set the loop's
#: zero-error output.
ARMS = {
    "off": {"config": {"bias_from_model": False}},
    "bias": {"config": {"bias_from_model": True}},
    "learn": {"config": {}},
    "no_learn": {"config": {}, "learn": False},
}


def _chain(job):
    """One controller/seed: one cook per entry in `scenarios`, threading the model.

    The scenarios are a sequence rather than one name repeated, because what a
    model learned at one setpoint is worth at another is a different question
    from what it is worth at its own -- and the answer decides whether a grill
    has to relearn every time its operator cooks something else.

    The core is captured rather than rebuilt so the snapshot comes off the same
    object the run drove; `run_scenario` constructs the core itself and does not
    hand it back.
    """
    controller, scenarios, seed, arm, config, learn, plant = job
    rows, snapshot = [], None
    for cook, scenario_name in enumerate(scenarios, start=1):
        holder = {}

        def setup(core, _holder=holder, _snapshot=snapshot):
            _holder["core"] = core
            if _snapshot is not None:
                _holder["restored"] = bool(core.restore_model(dict(_snapshot)))

        started = time.perf_counter()
        row = run_scenario(
            controller, SCENARIOS[scenario_name], seed, plant=plant, config=dict(config), core_setup=setup
        )
        row["cook"] = cook
        row["arm"] = arm
        row["sequence"] = tuple(scenarios)
        row["wall_s"] = round(time.perf_counter() - started, 1)
        row["model_in"] = None if snapshot is None else dict(snapshot)
        row["restored"] = holder.get("restored")

        core = holder.get("core")
        # Offered to the gate after the run has been scored, so a refit never
        # touches the cook it was fitted from -- the same order production
        # takes, where an adopted model reaches the grill through the next
        # cook's restore.
        row["refit"] = _refit_after_cook(core) if (learn and core is not None) else None
        snapshot = core.get_model_snapshot() if (learn and core is not None) else None
        row["model_out"] = None if snapshot is None else dict(snapshot)
        row["identified"] = snapshot is not None
        rows.append(row)
    return rows


def _agg(rows, key):
    values = [r[key] for r in rows if r.get(key) is not None]
    return None if not values else statistics.median(values)


def _render(rows, arms, sequences, plant):
    out = []

    def w(line=""):
        out.append(line)

    w("Successive cooks, each restoring the model the previous cook learned.")
    w(f"Plant {plant}. Median over seeds. Cook 1 starts with no model at all.")
    w()
    for arm in arms:
        for sequence in sequences:
            w(f"== {arm}: {' -> '.join(sequence)} ==")
            w(
                f"{'scenario':<14}{'cook':>5}{'overshoot':>11}{'in5%':>8}{'settle_s':>10}"
                f"{'identified':>12}{'restored':>10}{'promoted':>10}"
            )
            for cook, scenario_name in enumerate(sequence, start=1):
                sel = [
                    r
                    for r in rows
                    if r["arm"] == arm
                    and r["cook"] == cook
                    and r["sequence"] == sequence
                    and r["scenario"] == scenario_name
                ]
                if not sel:
                    continue
                identified = sum(1 for r in sel if r.get("identified"))
                restored = sum(1 for r in sel if r.get("restored"))
                # Only controllers that refit report a verdict; for the rest
                # this column is empty rather than zero, which would read as a
                # gate that refused every cook.
                verdicts = [r["refit"] for r in sel if r.get("refit") is not None]
                promoted = "--" if not verdicts else f"{sum(1 for v in verdicts if v['accepted'])}/{len(verdicts)}"
                settle = _agg(sel, "settle_s")
                w(
                    f"{scenario_name:<14}{cook:>5}"
                    f"{_agg(sel, 'overshoot_f'):>11.1f}"
                    f"{_agg(sel, 'pct_within_5f'):>8.1f}"
                    f"{'--' if settle is None else int(settle):>10}"
                    f"{f'{identified}/{len(sel)}':>12}"
                    f"{f'{restored}/{len(sel)}':>10}"
                    f"{promoted:>10}"
                )
            w()
    return "\n".join(out)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--controller", default="pid_sp")
    parser.add_argument("--seeds", type=int, default=3)
    parser.add_argument("--cooks", type=int, default=COOKS)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--plant", default=PLANT)
    parser.add_argument("--arms", default="off,bias", help="comma-separated arm names (see ARMS)")
    parser.add_argument(
        "--sequences",
        default=None,
        help=(
            "semicolon-separated chains, each a comma-separated scenario per cook "
            "(e.g. 'steady_225,steady_350;steady_450,steady_350'). Defaults to every "
            "scenario in SCENARIO_NAMES held for --cooks cooks."
        ),
    )
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    arms = {name: ARMS[name] for name in args.arms.split(",")}
    if args.sequences:
        sequences = [tuple(chain.split(",")) for chain in args.sequences.split(";")]
    else:
        sequences = [(name,) * args.cooks for name in SCENARIO_NAMES]
    jobs = [
        (args.controller, sequence, seed, arm, spec["config"], spec.get("learn", True), args.plant)
        for arm, spec in arms.items()
        for sequence in sequences
        for seed in range(args.seeds)
    ]
    with Pool(args.workers) as pool:
        rows = [row for chain in pool.map(_chain, jobs) for row in chain]

    report = _render(rows, list(arms), sequences, args.plant)
    print(report)
    if args.out:
        with open(args.out, "w") as handle:
            json.dump(rows, handle, indent=2, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
