"""What did reducing the grey box from five free parameters to three buy,
closed-loop, on a three-hour 325 F hold?

Everything measured in the online-identification slice so far is OPEN-loop --
fit error, truth error, identifiability. None of it says how well the MPC holds
a setpoint. This closes that gap by running the whole learning pipeline end to
end -- cook, refit, promotion gate, adopt, cook again -- against two plants, on
both sides of the structural change, and scoring the temperature the plant
actually reached.

THE TWO STRUCTURES, AND WHERE THEY PART. Two commits separate them, not one:

  * 4f02d647dc10 shrank `update_mpc._FREE` from
    ("K_Q", "C_c", "h_fc", "h_amb", "theta") to ("K_Q", "C_c", "theta").
  * bc9b6821f3fe collapsed the model itself to a single chamber lump, removing
    the firepot state T_f and its coupling conductance h_fc.

So "five free" is the era BEFORE the first of those, and the arm is the whole
checkout at 282ee65c9309 -- the commit immediately preceding 4f02d647dc10 --
not HEAD with a longer `_FREE` tuple. It carries everything else that shipped
then, including n_delay=4 (HEAD ships 8) and the two-lump model. That is the
comparison the question asks for: two shipped controllers, not one controller
with one constant changed.

THE ARMS. Three, and the first two are run in BOTH checkouts because an
uncalibrated floor is a property of the code that has to beat it:

  U   uncalibrated  shipped `_DEFAULTS`, no refit, one cook. The incident
                    condition and the floor. Reported as U5 (pre-A10 defaults)
                    and U3 (HEAD defaults); they are not the same numbers.
  F5  five free     the pre-A10 checkout, learning on: three successive cooks,
                    each refitting from its own history and handing an accepted
                    model to the next.
  F3  three free    the same, at HEAD.

An adopted model reaches the next cook the way production carries it: mpc.py's
`_adopt_model` writes into `cfg`, and the next Hold builds a new controller
from those settings. Mutating a core that has already assembled its estimator
and policy would silently keep stale internal state and confound the next cook.

HOW IDENTIFICATION IS SWITCHED ON. Not by `enable_identification`. That key is
a settings-surface flag read in the Hold mode's teardown path; `controller/mpc.py`
has never heard of it. What actually runs a refit is calling
`Controller.refit_from_cook()`, which fits the cook's own history, asks
`model_promotion.evaluate` for a verdict, and adopts on acceptance. This
experiment calls that method directly, once per cook, after the run is already
scored -- so the refit never touches the run it is measured on.

WHAT IS DELIBERATELY NOT MEASURED. There is no real-data arm, because there
cannot be one: the real MAK cook is a fixed log, and a controller that acts
differently gets no different temperature back from a CSV. `MAKGrillSim` is the
faithful proxy -- its C_c=3115.9, H=958.8 and 100 s deadtime were identified
FROM that very log (RMSE 2.3 C, peak 519 F against 520 F measured) -- so the MAK
columns below speak about the grill that log came from. They are NOT a replay of
that cook and must not be read as one.

TWO OF THE MAK METRICS CARRY ALMOST NO SIGNAL, and the output says so beside the
numbers rather than in a footnote. That chamber's time constant is about two
hours, so no arm reaches the 5 F band inside a 3 h run and `settle_s` reads
`never` on every MAK row in every arm. `pct_within_5f` there is measuring how far
the tail of a single overshoot got, not how well a setpoint was held. Overshoot,
peak and IAE are the MAK columns worth reading.

WHAT THE F5 ARM DOES AND DOES NOT PROVE. Its zero is proven by REFUSAL: no model
is ever adopted in that checkout, so the adopt-and-carry path -- a cook built
from the previous cook's parameters -- is never exercised inside it. The same
harness bytes do carry an adopted model in the F3 arm, where `model_in` is
non-empty on exactly the accepted rows, so the path works; it is simply never
reached on the F5 side.

Nothing under `controller/` is modified by this experiment, in either checkout.
No decision path changes; the promotion gate is called as it ships and its
verdicts are recorded, not overridden.

THE CONFOUND CONTROL. Only `controller/` may differ between F5 and F3. The
harness, the scenario, the plant, the seeds and the metric code are identical
because HEAD's `controller_matrix.py` and this file are COPIED INTO the pre-A10
workspace before it runs. `controller_matrix.py` and `controller/grill_sim.py`
happen to be byte-identical between 282ee65c9309 and HEAD, so the copy changes
only what this task added to the harness (the 325 F scenario, plant selection,
and the end-of-cook refit hook) -- but the copy is done unconditionally, since
a result mixing a harness change with a model-structure change measures neither.

Usage:
  # F3 arm, at HEAD
  .venv/bin/python docs/superpowers/experiments/structure_compare.py \
      --arm F3 --out docs/superpowers/experiments/_structure_compare_F3.json

  # F5 arm, from a workspace checked out at 282ee65c9309 with HEAD's
  # tools/__init__.py, tools/experiments/__init__.py,
  # tools/experiments/controller_matrix.py, and
  # docs/superpowers/experiments/structure_compare.py copied to their matching
  # historical-workspace paths. `--commit` is the checkout's
  # own commit id; it is recorded in the shard and printed in the table header,
  # so an arm label can be checked against the revision that produced it.
  /path/to/PiFire/.venv/bin/python \
      /path/to/pifire-f5/docs/superpowers/experiments/structure_compare.py \
      --arm F5 --commit 282ee65c9309 --out /path/to/pifire-f5/_structure_compare_F5.json

  # render the committed table from both shards
  .venv/bin/python docs/superpowers/experiments/structure_compare.py \
      --render a.json b.json > docs/superpowers/experiments/_structure_compare.txt
"""

import argparse
import json
import os
import statistics
import sys
import time
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from tools.experiments import controller_matrix  # noqa: E402
from tools.experiments.controller_matrix import SCENARIOS, run_scenario  # noqa: E402

SCENARIO = "steady_325"
SETPOINT_F = 325.0
PLANTS = ("GrillSim", "MAKGrillSim")
COOKS = 3


def _free_set():
    """What this checkout's fitter actually moves. Read rather than assumed:
    the arm label is an argument, and an argument can be wrong."""
    from controller import update_mpc

    return tuple(update_mpc._FREE)


def _model_states():
    """Whether this checkout's grey box still carries the firepot lump."""
    from controller import mpc_model

    return "two-lump (T_f, T_c)" if "C_f" in mpc_model.simulate_grey_box.__code__.co_varnames else "single lump (T_c)"


def _chain(job):
    """One arm/plant/seed: `cooks` successive cooks, each starting from the
    model the previous one got promoted."""
    arm, plant, seed, cooks, learn = job
    rows, config = [], {}
    for cook in range(1, cooks + 1):
        started = time.perf_counter()
        row = run_scenario("mpc", SCENARIOS[SCENARIO], seed, plant=plant, config=dict(config), refit=learn)
        row["arm"] = arm
        row["cook"] = cook
        row["wall_s"] = round(time.perf_counter() - started, 1)
        row["peak_temp_f"] = SETPOINT_F + row["overshoot_f"]
        row["model_in"] = dict(config)
        rows.append(row)
        refit = row.get("refit") or {}
        if refit.get("accepted") and refit.get("params"):
            config = dict(refit["params"])
    return rows


def _run(arm, seeds, workers, cooks):
    learning = arm != "U"
    jobs = [(arm, plant, seed, cooks if learning else 1, learning) for plant in PLANTS for seed in seeds]
    # The uncalibrated floor for THIS checkout, run alongside: it is a property
    # of the shipped defaults here, and the two checkouts' defaults differ.
    floor = "U" + arm[-1] if learning else arm
    jobs += [(floor, plant, seed, 1, False) for plant in PLANTS for seed in seeds]
    with Pool(workers) as pool:
        return [row for rows in pool.map(_chain, jobs) for row in rows]


def _agg(rows, key):
    values = [r[key] for r in rows if r.get(key) is not None]
    return None if not values else statistics.median(values)


def _fit_rmses(refit):
    """The candidate and incumbent fit errors this refit reported.

    They exist only in the line `refit_from_cook` prints, which the harness
    captures verbatim into the shard, so they are read back out of it rather
    than recomputed -- recomputing would be a second measurement wearing the
    first one's name. `refit["rmse"]` is not a substitute: it is populated from
    the model snapshot, so it is null on exactly the refusals worth reading.
    """
    import re

    for line in refit.get("log") or []:
        m = re.search(r"candidate RMSE ([\d.]+) C, incumbent ([\d.]+) C", line)
        if m:
            return m.group(1), m.group(2)
    return "-", "-"


def _render(shards):
    rows = [r for shard in shards for r in shard["rows"]]
    meta = {shard["arm"]: shard for shard in shards}
    out = []
    w = out.append

    w("Task A9a -- 3-parameter vs 5-parameter MPC, closed-loop at 325 F")
    w("=" * 78)
    w("")
    w("BUDGET AND RUN LIST (fixed before anything ran; see the header of")
    w("structure_compare.py for what each arm is)")
    w("  budget      90 minutes wall clock")
    w("  scenario    steady_325 -- 3 h at 325 F, cold start, no lid events")
    w("  plants      GrillSim (generic), MAKGrillSim (identified from a real MAK cook)")
    w(f"  seeds       {', '.join(str(s) for s in sorted({r['seed'] for r in rows}))}")
    w(
        f"  arms        U5, F5 (at {meta.get('F5', {}).get('commit', '?')}); "
        f"U3, F3 (at {meta.get('F3', {}).get('commit', '?')})"
    )
    w(f"  cooks       {COOKS} successive per learning arm, 1 per uncalibrated arm")
    w(f"  total       {len(rows)} closed-loop runs")
    w("")
    for shard in shards:
        w(f"  {shard['arm']}: commit {shard['commit']}  _FREE={shard['free']}  model={shard['states']}")
        w(f"      n_delay default {shard['n_delay']}, harness sha256 {shard['harness_sha'][:12]}")
    w("")
    w("MAKGrillSim IS NOT A REPLAY. The real 450 F MAK cook is a fixed log; a")
    w("controller that acts differently gets nothing different back from a CSV, so")
    w("real data cannot be run closed-loop at all. MAKGrillSim carries that log's")
    w("identified parameters (C_c=3115.9, H=958.8, 100 s deadtime), which is the")
    w("closest a closed-loop measurement can get to that grill.")
    w("")

    w("SETPOINT TRACKING -- median over seeds")
    w("-" * 78)
    w("READ THE MAK ROWS ON over F / peak F / IAE, NOT ON %<5F OR settle s. That")
    w("chamber's time constant is about 2 hours, so NO arm -- learned, uncalibrated,")
    w("either structure -- reaches the 5 F band inside a 3 h run, and every settle_s")
    w("below reads `never`. %<5F there is a near-degenerate metric measuring how far")
    w("the tail of one overshoot got, not how well the setpoint was held; a rise from")
    w("0.2% to 5% is NOT a quality score. The GrillSim rows have no such caveat.")
    w("")
    w(f"{'plant':<13}{'arm':<5}{'cook':>5}{'%<5F':>8}{'over F':>9}{'peak F':>9}{'settle s':>10}{'IAE':>11}")
    for plant in PLANTS:
        for arm in ("U5", "F5", "U3", "F3"):
            for cook in range(1, COOKS + 1):
                sel = [r for r in rows if r["plant"] == plant and r["arm"] == arm and r["cook"] == cook]
                if not sel:
                    continue
                settle = _agg(sel, "settle_s")
                w(
                    f"{plant:<13}{arm:<5}{cook:>5}"
                    f"{_agg(sel, 'pct_within_5f'):>8.2f}"
                    f"{_agg(sel, 'overshoot_f'):>9.1f}"
                    f"{_agg(sel, 'peak_temp_f'):>9.1f}"
                    f"{'never' if settle is None else f'{settle:.0f}':>10}"
                    f"{_agg(sel, 'iae'):>11.0f}"
                )
        w("")

    w("SEED SPREAD -- min..max of %<5F, so a difference smaller than this is noise")
    w("-" * 78)
    w(f"{'plant':<13}{'arm':<5}{'cook':>5}{'%<5F min':>11}{'%<5F max':>11}{'spread':>9}")
    for plant in PLANTS:
        for arm in ("U5", "F5", "U3", "F3"):
            for cook in range(1, COOKS + 1):
                sel = [
                    r["pct_within_5f"] for r in rows if r["plant"] == plant and r["arm"] == arm and r["cook"] == cook
                ]
                if not sel:
                    continue
                w(f"{plant:<13}{arm:<5}{cook:>5}{min(sel):>11.2f}{max(sel):>11.2f}{max(sel) - min(sel):>9.2f}")
    w("")

    w("PROMOTION GATE -- every verdict, both arms")
    w("-" * 78)
    w("`cand C` and `inc C` are the fit RMSEs the refit itself reported, in degrees")
    w("C, so a refusal can be read against how well the candidate actually described")
    w("the cook. A refused F5 candidate fits fifty times better than the model it was")
    w("refused in favour of -- what the gate rejects is not the fit.")
    w(f"{'plant':<13}{'arm':<5}{'seed':>5}{'cook':>5}{'cand C':>8}{'inc C':>8}{'accepted':>10}  reason")
    accepted = {}
    for plant in PLANTS:
        for arm in ("F5", "F3"):
            for seed in sorted({r["seed"] for r in rows}):
                for cook in range(1, COOKS + 1):
                    sel = [
                        r
                        for r in rows
                        if r["plant"] == plant and r["arm"] == arm and r["seed"] == seed and r["cook"] == cook
                    ]
                    if not sel or not sel[0].get("refit"):
                        continue
                    v = sel[0]["refit"]
                    accepted.setdefault(arm, []).append(bool(v["accepted"]))
                    cand, inc = _fit_rmses(v)
                    w(f"{plant:<13}{arm:<5}{seed:>5}{cook:>5}{cand:>8}{inc:>8}{v['accepted']!s:>10}  {v['reason']}")
    w("")
    for arm, flags in accepted.items():
        w(f"  {arm}: {sum(flags)} accepted of {len(flags)} refits ({100.0 * sum(flags) / len(flags):.0f}%)")
    w("")
    w("WHICH GUARD ACTUALLY DECIDED, AND OVER WHAT DENOMINATOR. A12's identifiability")
    w("floor could only ever be reached by the F3 refits: `_IDENTIFIABILITY_FLOOR` and")
    w("`update_mpc.identifiability` DO NOT EXIST at 282ee65c9309, so the other half of")
    w("the refits above never evaluated it. Over the 18 refits that could, it never")
    w("bound -- a 3 h hold from a COLD start is not a flat record, and its ramp")
    w("determines the model. F5's refusals never reach that test either: `evaluate`")
    w("short-circuits on the PROMOTION_BOUNDS check for h_amb first. Those bounds are")
    w("byte-identical at both revisions -- h_amb (1e-4, 1e3) -- so what separates the")
    w("arms here is the fitted value, not a change to the gate.")
    w("")

    w("WALL CLOCK")
    w("-" * 78)
    for shard in shards:
        w(f"  {shard['arm']} shard: {shard['wall_s']:.0f} s over {shard['workers']} workers, {shard['n_rows']} runs")
    if any(shard.get("dropped") for shard in shards):
        w("  DROPPED FROM THE PLANNED RUN LIST:")
        for shard in shards:
            for line in shard.get("dropped") or []:
                w(f"    {shard['arm']}: {line}")
    else:
        w("  Nothing was dropped from the planned run list.")
    return "\n".join(out) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--arm", choices=["F3", "F5", "U"], help="which structural arm this checkout is")
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--cooks", type=int, default=COOKS)
    ap.add_argument("-w", "--workers", type=int, default=8)
    ap.add_argument("--commit", default="", help="commit id of this checkout, recorded in the shard")
    ap.add_argument("--dropped", nargs="*", default=[], help="anything cut from the planned run list, verbatim")
    ap.add_argument("--out", default=None)
    ap.add_argument("--render", nargs="+", default=None, help="shard files to render the committed table from")
    args = ap.parse_args(argv)

    if args.render:
        shards = []
        for path in args.render:
            with open(path) as f:
                shards.append(json.load(f))
        sys.stdout.write(_render(shards))
        return

    if not args.arm or not args.out:
        ap.error("--arm and --out are required unless --render is given")

    import hashlib

    from controller.mpc_config import DEFAULT_MPC_CONFIG

    harness = Path(controller_matrix.__file__).resolve()
    harness_sha = hashlib.sha256(harness.read_bytes()).hexdigest()

    started = time.perf_counter()
    rows = _run(args.arm, args.seeds, args.workers, args.cooks)
    shard = {
        "arm": args.arm,
        "commit": args.commit,
        "free": list(_free_set()),
        "states": _model_states(),
        "n_delay": int(DEFAULT_MPC_CONFIG["n_delay"]),
        "harness_sha": harness_sha,
        "workers": args.workers,
        "seeds": list(args.seeds),
        "cooks": args.cooks,
        "wall_s": round(time.perf_counter() - started, 1),
        "n_rows": len(rows),
        "dropped": list(args.dropped),
        "rows": rows,
    }
    with open(args.out, "w") as f:
        json.dump(shard, f, indent=1, sort_keys=True)
    print(f"{len(rows)} runs in {shard['wall_s']:.0f} s -> {args.out}")


if __name__ == "__main__":
    main()
