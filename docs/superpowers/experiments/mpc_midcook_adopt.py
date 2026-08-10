"""Does refitting DURING cook 1 actually save cook 1?

The window measurement (mpc_online_window.py) established that the gate will
accept a model 10-12 minutes into a first cook, and that on the hotter
setpoints this lands minutes before the chamber even reaches target -- so a
mid-cook adoption is not too late in principle. Whether it helps in practice
is a different claim, and this measures it.

Adoption mid-cook is not free. Taking a new model means rebuilding the
estimator and the NLP, which discards the solver's warm start and, unless the
state is deliberately carried across, hands the new estimator a chamber it
believes is sitting at ambient while the real one is at 175 C. Either could
cost more than the better model buys.

So the arms here separate those effects rather than bundling them:

  baseline      what ships now -- one batch refit at teardown, nothing during
  rebuild_noop  rebuild at the same moment with the SAME parameters. The
                negative control that prices everything adoption does EXCEPT
                change the numbers: the discarded warm start, and the switch
                into identified mode, which by itself turns on the
                equilibrium feed-forward and the learned-residual objective.
                Any difference it shows is not the model, and has to be
                subtracted from what the real arms appear to earn.
  adopt_warm    refit, rebuild, and carry the estimator's whole state across
  adopt_cold    refit, rebuild, and let the estimator restart at ambient.
                Isolates the state carry, which is the part with no
                production precedent.
  adopt_zerod   carry the chamber and the lag chain but drop the disturbance
                estimate. Under the shipped model the filter's `d` was the
                only place a ten-fold error in the chamber could go, so it
                holds a large fictitious load; carried onto parameters that
                no longer need it, the same number is counted twice and the
                controller under-fires for the rest of the cook.

A run is only evidence if `rebuild_noop` sits on top of `baseline`, and the
holding columns are read separately from the ramp: an arm can kill the
overshoot by becoming too timid to hold, which is not an improvement.
"""

import argparse
import json
import os
import statistics
import sys
import time
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import numpy as np  # noqa: E402

from tools.experiments.controller_matrix import SCENARIOS, run_scenario  # noqa: E402

PLANT = "MAKGrillSim"
SCENARIO_NAMES = ("steady_225", "steady_350", "steady_450")
#: Minutes into the cook at which to attempt the adoption. 12 is the earliest
#: the gate accepted at every setpoint measured; the later ones show what the
#: extra data is worth against the lead time it costs.
ADOPT_MIN = (12, 20, 30)
ARMS = ("baseline", "rebuild_noop", "adopt_warm", "adopt_cold", "adopt_zerod", "periodic")
#: How often the `periodic` arm re-asks, before and after it first succeeds.
#: A single adoption has to guess when the cook will have taught enough, and
#: the right moment moves with the setpoint -- 225 F peaks before the gate will
#: even look, 450 F peaks eight minutes after. Asking repeatedly removes the
#: guess, which is the only version of this a grill owner never has to
#: configure.
#:
#: The two cadences are not a tuning knob but the cost curve: a fit re-simulates
#: the whole history per evaluation, so an early one is a fraction of a second
#: and a late one is seconds. Early is also where the value is, since the
#: overshoot it prevents happens in the first half hour. So ask often while
#: it is cheap and decisive, and rarely once it is neither.
PROBE_EARLY_MIN = 1.0
PROBE_LATE_MIN = 30.0
#: Seconds at the end of the cook that count as holding rather than reaching.
#: The ramp is minutes and the cook is hours, so a whole-cook average hides a
#: controller that stopped overshooting by never arriving.
TAIL_S = 7200.0


def _adopt_midcook(core, *, mode):
    """Refit from the cook so far and take the result, as production would.

    `restore_model` is the production path for putting a model into a live
    controller -- it rebuilds the estimator, the horizon and the policy
    together -- so the adoption goes through it rather than around it. What is
    NOT production is the state carry afterwards: `restore_model` is written
    for the start of a cook, where dropping the state estimate costs nothing
    because there is no cook yet. Mid-cook there is, and `warm` is the
    candidate mechanism this experiment exists to price.
    """
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        if mode == "rebuild_noop":
            # No refit at all: snapshot whatever is already configured so the
            # rebuild happens with the parameters the run already had.
            core._adopt_model(
                {k: float(core.cfg[k]) for k in core._MODEL_PARAM_KEYS},
                rmse=0.0,
                samples=0,
                band_c=(0.0, 0.0),
            )
            accepted = True
        else:
            accepted = bool(core.refit_from_cook().accepted)
        if not accepted:
            return {"attempted": True, "accepted": False, "log": buf.getvalue().strip().splitlines()}
        snapshot = core.get_model_snapshot()
        carried = np.array(core.estimator.x, dtype=float, copy=True)
        if mode == "adopt_zerod":
            # Last element is the integrating disturbance state; everything
            # before it is the lag chain and the chamber, which the new
            # parameters do not invalidate.
            carried[-1] = 0.0
        restored = bool(core.restore_model(snapshot))
        if restored and mode in ("rebuild_noop", "adopt_warm", "adopt_zerod"):
            # The state vector is [q0..q_n-1, T_c, d] and n_delay is the one
            # parameter a refit never learns, so the rebuilt estimator's state
            # has the same length and the same meaning element for element.
            # The lag states are in normalized-load units regardless of theta,
            # and d is an absolute power, so neither is rescaled by the new
            # parameters -- they carry across as they are.
            core.estimator.x = carried
            core._x_hat = carried
    return {
        "attempted": True,
        "accepted": accepted,
        "restored": restored,
        "params": None if snapshot is None else dict(snapshot["params"]),
        "log": buf.getvalue().strip().splitlines(),
    }


def _holding(core, scenario):
    """How the controller did once it had stopped trying to get there.

    Read off the controller's own cook history rather than the scenario
    summary, because the summary averages the ramp in with the hold, and the
    whole question about a timid model is which of those two it spoiled.
    """
    rows = core.cook_history()
    if not rows:
        return {"tail_in5_pct": None, "tail_mean_err_f": None, "tail_max_err_f": None}
    end = rows[-1][0]
    setpoint_f = scenario.setpoints[0][1]
    err = np.array([r[1] * 9.0 / 5.0 + 32.0 - setpoint_f for r in rows if r[0] >= end - TAIL_S])
    if err.size == 0:
        return {"tail_in5_pct": None, "tail_mean_err_f": None, "tail_max_err_f": None}
    return {
        "tail_in5_pct": float((np.abs(err) <= 5.0).mean() * 100.0),
        "tail_mean_err_f": float(err.mean()),
        "tail_max_err_f": float(np.abs(err).max()),
    }


def _run(job):
    controller, scenario_name, seed, arm, adopt_min, plant = job
    holder = {"adoptions": 0, "fit_s": 0.0}
    trigger_rows = int(adopt_min * 60.0 / 5.0)  # history grows one row per solve
    early_rows = int(PROBE_EARLY_MIN * 60.0 / 5.0)
    late_rows = int(PROBE_LATE_MIN * 60.0 / 5.0)

    def setup(core):
        holder["core"] = core

    def per_solve(requested):
        """Fires once per controller solve; the adoption seam for this run."""
        core = holder.get("core")
        if core is None or arm == "baseline":
            return requested
        rows = len(core.cook_history())
        if arm == "periodic":
            # Re-ask on a fixed cadence rather than at one chosen moment. The
            # gate refuses until the cook has taught enough, so the first
            # acceptance IS the earliest one available -- no threshold to pick
            # and none to get wrong on a grill nobody measured.
            if rows < holder.get("next_at", trigger_rows):
                return requested
            started = time.perf_counter()
            result = _adopt_midcook(core, mode="adopt_cold")
            holder["fit_s"] += time.perf_counter() - started
            if result.get("accepted"):
                holder["adoptions"] += 1
                holder["result"] = result
            holder["next_at"] = rows + (late_rows if holder["adoptions"] else early_rows)
            return requested
        if holder.get("done"):
            return requested
        if rows >= trigger_rows:
            holder["done"] = True
            started = time.perf_counter()
            holder["result"] = _adopt_midcook(core, mode=arm)
            holder["fit_s"] += time.perf_counter() - started
            holder["adoptions"] += int(bool(holder["result"].get("accepted")))
        return requested

    row = run_scenario(
        controller,
        SCENARIOS[scenario_name],
        seed,
        plant=plant,
        core_setup=setup,
        output_transform=None if arm == "baseline" else per_solve,
    )
    row.update(_holding(holder["core"], SCENARIOS[scenario_name]))
    return {
        "arm": arm,
        "adopt_min": None if arm == "baseline" else adopt_min,
        "scenario": scenario_name,
        "seed": seed,
        "overshoot_f": row["overshoot_f"],
        "undershoot_f": row["undershoot_f"],
        "pct_within_5f": row["pct_within_5f"],
        "settle_s": row["settle_s"],
        "rmse_f": row["rmse_f"],
        "tail_in5_pct": row["tail_in5_pct"],
        "tail_mean_err_f": row["tail_mean_err_f"],
        "tail_max_err_f": row["tail_max_err_f"],
        "adoptions": holder["adoptions"],
        # Wall-clock the grill would have to find somewhere other than the
        # control path. Measured on this machine, which is not the Pi the
        # nominal target is -- a bound to scale, not a budget to quote.
        "fit_s": round(holder["fit_s"], 2),
        "adoption": holder.get("result"),
    }


def _median(rows, key):
    values = [r[key] for r in rows if r.get(key) is not None]
    return None if not values else statistics.median(values)


def _render(rows, arms, adopt_minutes, scenario_names):
    out = []

    def w(line=""):
        out.append(line)

    w("Cook 1 on a grill the controller has never met, with a mid-cook refit.")
    w(f"Plant {PLANT}. Median over seeds. baseline = what ships now.")
    w("rebuild_noop is the negative control: same moment, same rebuild, same")
    w("parameters. Read every gain against IT, not against baseline.")
    w()
    w("overshoot is the ramp; tail_* is the last 2 h, which is the hold. An arm")
    w("that improves the first and spoils the second has not improved anything.")
    w("undershoot is omitted: every arm starts cold, so it only reports ambient.")
    w()
    header = (
        f"{'arm':<15}{'at_min':>8}{'overshoot':>11}{'in5%':>8}{'settle_s':>10}"
        f"{'tail_in5%':>11}{'tail_mean':>11}{'tail_max':>10}{'adopts':>8}{'fit_s':>8}"
    )

    def line(label, at_min, sel):
        settle = _median(sel, "settle_s")
        return (
            f"{label:<15}{at_min:>8}{_median(sel, 'overshoot_f'):>11.1f}"
            f"{_median(sel, 'pct_within_5f'):>8.1f}"
            f"{'--' if settle is None else int(settle):>10}"
            f"{_median(sel, 'tail_in5_pct'):>11.1f}{_median(sel, 'tail_mean_err_f'):>11.1f}"
            f"{_median(sel, 'tail_max_err_f'):>10.1f}"
            f"{_median(sel, 'adoptions'):>8.0f}{_median(sel, 'fit_s'):>8.1f}"
        )

    for scenario_name in scenario_names:
        w(f"== {scenario_name} ==")
        w(header)
        base = [r for r in rows if r["scenario"] == scenario_name and r["arm"] == "baseline"]
        if base:
            w(line("baseline", "--", base))
        for arm in arms:
            if arm == "baseline":
                continue
            for adopt_min in adopt_minutes:
                sel = [
                    r
                    for r in rows
                    if r["scenario"] == scenario_name and r["arm"] == arm and r["adopt_min"] == adopt_min
                ]
                if not sel:
                    continue
                w(line(arm, f"{adopt_min}+" if arm == "periodic" else adopt_min, sel))
        w()
    return "\n".join(out)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--controller", default="mpc")
    parser.add_argument("--scenarios", default=",".join(SCENARIO_NAMES))
    parser.add_argument("--seeds", type=int, default=2)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--plant", default=PLANT)
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--adopt-min", default=",".join(str(m) for m in ADOPT_MIN))
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    scenario_names = args.scenarios.split(",")
    arms = args.arms.split(",")
    adopt_minutes = [int(m) for m in args.adopt_min.split(",")]

    jobs = []
    for scenario_name in scenario_names:
        for seed in range(args.seeds):
            for arm in arms:
                if arm == "baseline":
                    jobs.append((args.controller, scenario_name, seed, arm, 0, args.plant))
                elif arm == "periodic":
                    # One run, not one per candidate moment: the cadence is the
                    # point, and its start is simply the earliest the sample
                    # floor allows anything to be judged at all.
                    jobs.append((args.controller, scenario_name, seed, arm, min(adopt_minutes), args.plant))
                else:
                    jobs.extend(
                        (args.controller, scenario_name, seed, arm, minutes, args.plant) for minutes in adopt_minutes
                    )
    with Pool(args.workers) as pool:
        rows = pool.map(_run, jobs)

    report = _render(rows, arms, adopt_minutes, scenario_names)
    print(report)
    if args.out:
        with open(args.out, "w") as handle:
            json.dump(rows, handle, indent=2, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
