"""When during a cook does the grill first become identifiable?

MPC learns in one batch at teardown, so a grill it has never met is steered
for that whole first cook by the shipped defaults -- which are roughly ten
times too fast for a real MAK, and an MPC plans its braking distance against
exactly that error. Cook 1 overshoots by tens of degrees; cook 2, holding the
model cook 1 produced, lands inside two.

Online learning would close that gap by refitting DURING the cook. Whether it
can is not a design question but a measurement: the fit needs enough
excitation to pin the parameters, and a cook only supplies that as it runs. If
the gate cannot accept until the third hour, mid-cook adoption arrives after
the overshoot it was meant to prevent and buys nothing.

So this replays one real cook and asks, at each candidate moment, exactly what
production would ask at that moment: `refit_from_cook` over the history so far,
judged by the same gate, from a controller carrying the same shipped defaults.
The answer is the earliest accept -- the opening of the window that any online
scheme has to work inside.
"""

import argparse
import contextlib
import io
import json
import os
import statistics
import sys
import time
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from controller.grill_sim import MAKGrillSim  # noqa: E402
from tools.experiments.controller_matrix import (  # noqa: E402
    SCENARIOS,
    _effective_configuration,
    run_scenario,
)

PLANT = "MAKGrillSim"
#: Minutes into the cook at which to ask the question. Dense early, where the
#: answer decides whether online learning is worth building at all, and sparse
#: later, where a positive answer is already too late to matter.
PROBES_MIN = (5, 8, 10, 12, 15, 20, 25, 30, 45, 60, 90)
#: What the plant actually is, for scoring a fit against truth rather than
#: against its own residual. `h_amb` is the base plant's unfitted value, so
#: only C_c and the gain carry MAK's measurement -- see MAKGrillSim.
TRUTH = {"C_c": MAKGrillSim.C_C, "deadtime_s": MAKGrillSim.DEADTIME}


def _capture_cook(controller, scenario_name, seed, plant):
    """Run one cook from shipped defaults and hand back its (t, temp, Q) rows.

    The peak is reported alongside, because an accept is only worth anything
    if it lands before the overshoot it would have prevented. `first_cross` is
    when the chamber first reaches setpoint -- the last moment a changed model
    can still alter the braking, since after it the damage is already being
    done.
    """
    holder = {}
    row = run_scenario(
        controller,
        SCENARIOS[scenario_name],
        seed,
        plant=plant,
        core_setup=lambda core: holder.__setitem__("core", core),
    )
    core = holder["core"]
    rows = list(core.cook_history())
    t0 = rows[0][0]
    setpoint_c = (SCENARIOS[scenario_name].setpoints[0][1] - 32.0) * 5.0 / 9.0
    peak = max(rows, key=lambda r: r[1])
    crossed = next((r for r in rows if r[1] >= setpoint_c), None)
    row["peak_min"] = round((peak[0] - t0) / 60.0, 1)
    row["first_cross_min"] = None if crossed is None else round((crossed[0] - t0) / 60.0, 1)
    return rows, row


def _probe(job):
    """Refit from a prefix of one cook, through the production gate.

    A fresh controller per probe, because `refit_from_cook` adopts into `cfg`
    on acceptance: reusing one would make each probe judge the previous
    probe's winner instead of the shipped default every real first cook starts
    from, and the question here is what THAT controller would have decided.
    """
    controller, rows, minutes, seed, scenario_name = job
    import importlib

    core_config, cycle_data, _, _ = _effective_configuration(controller, None, None)
    mod = importlib.import_module(f"controller.{controller}")
    core = mod.Controller(dict(core_config), "F", dict(cycle_data))

    t0 = rows[0][0]
    prefix = [r for r in rows if r[0] - t0 <= minutes * 60.0]
    started = time.perf_counter()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        verdict = core.refit_from_cook(prefix)
    snapshot = core.get_model_snapshot()
    temps = [r[1] for r in prefix]
    return {
        "scenario": scenario_name,
        "seed": seed,
        "minutes": minutes,
        "samples": len(prefix),
        "accepted": bool(verdict.accepted),
        "reason": str(verdict.reason),
        "seconds": round(time.perf_counter() - started, 2),
        "params": None if snapshot is None else dict(snapshot["params"]),
        "rmse": None if snapshot is None else snapshot.get("rmse"),
        # What the chamber was doing over the window the fit saw. A prefix that
        # is all ramp and no regulation pins the gain but says little about the
        # loss term, and the span is what shows which one this was.
        "span_c": None if not temps else round(max(temps) - min(temps), 1),
        "end_c": None if not temps else round(temps[-1], 1),
        "log": buf.getvalue().strip().splitlines(),
    }


def _render(cooks, probes):
    out = []

    def w(line=""):
        out.append(line)

    w("Refit from a prefix of cook 1, judged by the production gate.")
    w(f"Plant {PLANT}. Truth: C_c {TRUTH['C_c']}, deadtime {TRUTH['deadtime_s']} s.")
    w("Each row is a fresh controller on shipped defaults refitting from what it")
    w("had seen by that minute -- the decision online learning would be making.")
    w()
    for scenario_name, seed in sorted({(p["scenario"], p["seed"]) for p in probes}):
        cook = cooks[(scenario_name, seed)]
        w(f"== {scenario_name} seed {seed} ==")
        w(
            f"   cook 1 as it ran: overshoot {cook['overshoot_f']:.1f} F, in5% {cook['pct_within_5f']:.1f}%, "
            f"reached setpoint at {cook['first_cross_min']} min, peaked at {cook['peak_min']} min"
        )
        w(
            f"{'min':>5}{'samples':>9}{'span_C':>8}{'accept':>8}{'C_c':>10}{'C_c err':>9}"
            f"{'theta':>8}{'K_Q':>9}{'rmse_C':>8}{'fit_s':>7}  reason"
        )
        mine = (p for p in probes if p["scenario"] == scenario_name and p["seed"] == seed)
        for probe in sorted(mine, key=lambda p: p["minutes"]):
            params = probe["params"] or {}
            c_c = params.get("C_c")
            span = "--" if probe["span_c"] is None else f"{probe['span_c']:.1f}"
            cc_s = "--" if c_c is None else f"{c_c:.0f}"
            err = "" if c_c is None else f"{c_c / TRUTH['C_c'] - 1.0:+.0%}"
            theta = "--" if not params else f"{params['theta']:.0f}"
            k_q = "--" if not params else f"{params['K_Q']:.0f}"
            rmse = "--" if probe["rmse"] is None else f"{probe['rmse']:.2f}"
            w(
                f"{probe['minutes']:>5}{probe['samples']:>9}{span:>8}"
                f"{'yes' if probe['accepted'] else 'no':>8}"
                f"{cc_s:>10}{err:>9}{theta:>8}{k_q:>9}{rmse:>8}"
                f"{probe['seconds']:>7}  {probe['reason']}"
            )
        w()
    accepted = [p["minutes"] for p in probes if p["accepted"]]
    if accepted:
        w(f"Earliest accept: {min(accepted)} min. Median earliest across cooks below.")
    else:
        w("No prefix was accepted at any probe. The window never opens on this cook.")
    per_cook = {}
    for probe in probes:
        if probe["accepted"]:
            key = (probe["scenario"], probe["seed"])
            per_cook[key] = min(per_cook.get(key, 10**9), probe["minutes"])
    if per_cook:
        w(f"Median earliest accept over {len(per_cook)} cooks: {statistics.median(per_cook.values()):.0f} min")
        w(f"Cooks that never accepted: {len({(p['scenario'], p['seed']) for p in probes}) - len(per_cook)}")
    return "\n".join(out)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--controller", default="mpc")
    parser.add_argument("--scenarios", default="steady_225,steady_350,steady_450")
    parser.add_argument("--seeds", type=int, default=2)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--plant", default=PLANT)
    parser.add_argument("--probes", default=",".join(str(m) for m in PROBES_MIN))
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    scenario_names = args.scenarios.split(",")
    probe_minutes = [int(m) for m in args.probes.split(",")]

    cook_jobs = [(args.controller, name, seed, args.plant) for name in scenario_names for seed in range(args.seeds)]
    with Pool(args.workers) as pool:
        captured = pool.starmap(_capture_cook, cook_jobs)

    cooks, probe_jobs = {}, []
    for (_, name, seed, _), (rows, row) in zip(cook_jobs, captured):
        cooks[(name, seed)] = row
        probe_jobs.extend((args.controller, rows, minutes, seed, name) for minutes in probe_minutes)

    with Pool(args.workers) as pool:
        probes = pool.map(_probe, probe_jobs)

    report = _render(cooks, probes)
    print(report)
    if args.out:
        with open(args.out, "w") as handle:
            json.dump(
                {"cooks": {f"{k[0]}:{k[1]}": v for k, v in cooks.items()}, "probes": probes},
                handle,
                indent=2,
                default=str,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
