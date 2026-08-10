"""What the shipped thermal mass costs cook 1, and what a different one would.

Cook 1 is steered entirely by the shipped defaults, and `C_c` is the one that
decides braking distance: it is the chamber's heat capacity, so it sets how
long the grill coasts after the auger backs off. Shipped it is 320, and a real
MAK measures 3115.9 -- the controller plans as though the chamber will stop
roughly ten times sooner than it does, and drives straight through setpoint.

Online learning is one answer. A better default is another, and it is a single
number rather than a feature, so it has to be measured before the feature is
justified. The catch is that the two plants pull opposite ways: a default
chosen to stop overshooting on the slow MAK is, on the base plant's genuinely
fast chamber, a controller that believes it must brake for a coast that is not
coming.

So every candidate runs on both plants. What this decides is whether a default
exists that is honest on both -- and if it does, how much of cook 1's gap it
closes on its own, which is the bar online learning then has to clear.
"""

import argparse
import json
import os
import statistics
import sys
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from controller.grill_sim import GrillSim, MAKGrillSim  # noqa: E402
from controller.mpc import _DEFAULTS as mpc_defaults  # noqa: E402
from controller.mpc import _model_is_identified  # noqa: E402
import tools.experiments.controller_matrix as controller_matrix  # noqa: E402
from tools.experiments.controller_matrix import SCENARIOS, run_scenario  # noqa: E402


def _assert_uncalibrated(core):
    """Refuse to produce a number from a controller that thinks it is calibrated.

    This is the experiment's own trip-wire. A candidate that reached the
    controller as an override rather than as the default would silently switch
    on the identified-model path, and the resulting table would read as a
    dramatic win for the value while actually reporting the switch. Checked on
    every run rather than reasoned about once.
    """
    if _model_is_identified(core.cfg, None):
        raise AssertionError(
            "the controller reports an identified model, so this run would compare "
            "controller structures rather than values of C_c"
        )


#: The two plants this has to be honest on at once, with the chamber mass each
#: one actually has. GrillSim is the fast generic chamber, MAKGrillSim the slow
#: measured one; a default is only shippable if it survives both. GrillSim's
#: 300.0 is assigned in its __init__ rather than exposed as a class attribute,
#: so it is spelled out here -- keep the two in step.
PLANTS = {"GrillSim": 300.0, "MAKGrillSim": MAKGrillSim.C_C}
#: Shipped first, then the ladder between the two truths. 3115.9 is not a
#: candidate default -- it is MAK's own answer, included as the ceiling that
#: shows what the rest of the ladder is climbing toward.
CANDIDATES = (320.0, 640.0, 1000.0, 1600.0, 2400.0, 3115.9)
SCENARIO_NAMES = ("steady_225", "steady_350", "steady_450")
#: Chambers lighter than any real plant here, to test the direction a raised
#: default fails in. Raising `C_c` is only safe if a grill lighter than the
#: default approaches slowly and sits low rather than overshooting -- braking
#: too early costs time, braking too late costs the setpoint.
FAST_TRUE_CC = (75.0, 150.0, 300.0)


def _register_fast_plant(true_c_c):
    """Register a GrillSim whose chamber really is `true_c_c` and return its name.

    `run_scenario` resolves a plant by looking its name up in
    `controller_matrix`'s own namespace, so a plant that exists only for an
    experiment has to be put there rather than passed in. These are fixtures,
    not grills anyone owns, which is why they are built here instead of beside
    `MAKGrillSim` in the production module.
    """
    name = f"GrillSim_Cc{int(true_c_c)}"
    if not hasattr(controller_matrix, name):

        class _Plant(GrillSim):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.C_c = float(true_c_c)

        _Plant.__name__ = _Plant.__qualname__ = name
        setattr(controller_matrix, name, _Plant)
        PLANTS[name] = float(true_c_c)
    return name


def _run(job):
    controller, scenario_name, seed, c_c, plant, fast_true_cc = job
    # Registered here, not once in the parent: multiprocessing starts workers
    # with forkserver on this Python, so a worker re-imports this module and
    # inherits nothing the parent added to it at runtime.
    if fast_true_cc is not None:
        _register_fast_plant(fast_true_cc)
    # The shipped default is moved, not overridden. `_model_is_identified`
    # calls a grill calibrated when any physical parameter differs from the
    # shipped default, and a calibrated grill gets an equilibrium feed-forward
    # and a learned-residual objective that an uncalibrated one does not. So
    # passing a candidate as an override alone would change the controller's
    # whole structure alongside the number, and the comparison would be
    # between two different controllers rather than two values of C_c.
    mpc_defaults["C_c"] = c_c
    row = run_scenario(
        controller,
        SCENARIOS[scenario_name],
        seed,
        plant=plant,
        config={"C_c": c_c},
        core_setup=_assert_uncalibrated,
    )
    setpoint_f = SCENARIOS[scenario_name].setpoints[0][1]
    return {
        "C_c": c_c,
        "plant": plant,
        "scenario": scenario_name,
        "seed": seed,
        "overshoot_f": row["overshoot_f"],
        "pct_within_5f": row["pct_within_5f"],
        "settle_s": row["settle_s"],
        "rmse_f": row["rmse_f"],
        "final_temp_f": row["final_temp_f"],
        # Where the grill ended up relative to where it was asked to be. This
        # is the column that answers what a too-heavy model does to a light
        # grill: braking early should leave it sitting low, not high.
        "final_err_f": row["final_temp_f"] - setpoint_f,
        # run_scenario's own `undershoot_f` is the minimum over the whole cook,
        # which every run takes at the cold start, so it reports ambient rather
        # than anything the controller did.
    }


def _median(rows, key):
    values = [r[key] for r in rows if r.get(key) is not None]
    return None if not values else statistics.median(values)


def _render(rows, plants, candidates, scenario_names):
    out = []

    def w(line=""):
        out.append(line)

    w("Cook 1 from shipped defaults with only C_c varied. Median over seeds.")
    w("Each plant's own measured chamber mass is in the heading; a candidate at")
    w("that value is the controller being handed the right answer, not learning it.")
    w()
    w("Broken out per setpoint rather than pooled, because the effects run in")
    w("opposite directions across the range -- on the slow plant a heavier model")
    w("costs overshoot at 225 F while saving it at 450 -- and a median over the")
    w("three reports their cancellation as if it were a small effect.")
    w()
    w("`final` is the finishing temperature minus setpoint: negative means the")
    w("run ended below target, which is the direction a too-heavy model should")
    w("fail in. `over` is the worst excursion above target at any point.")
    w()
    for plant in plants:
        # A re-render from saved rows may name a synthesized plant this process
        # never registered, so an unknown truth is reported as unknown rather
        # than crashing the report that holds the rest of the run.
        truth = PLANTS.get(plant)
        w(f"== {plant} (true C_c {'?' if truth is None else f'{truth:.0f}'}) ==")
        w(f"{'C_c':>7} | " + " | ".join(f"{name.replace('steady_', '') + ' F':>22}" for name in scenario_names))
        w(f"{'':>7} | " + " | ".join(f"{'over':>7}{'in5%':>8}{'final':>7}" for _ in scenario_names))
        for c_c in candidates:
            cells = []
            for name in scenario_names:
                sel = [r for r in rows if r["plant"] == plant and r["C_c"] == c_c and r["scenario"] == name]
                if not sel:
                    cells.append(f"{'--':>22}")
                    continue
                cells.append(
                    f"{_median(sel, 'overshoot_f'):>7.1f}{_median(sel, 'pct_within_5f'):>8.1f}"
                    f"{_median(sel, 'final_err_f'):>+7.1f}"
                )
            mark = "  <- shipped" if c_c == CANDIDATES[0] else ("  <- truth" if c_c == truth else "")
            w(f"{c_c:>7.0f} | " + " | ".join(cells) + mark)
        w()
    w("A candidate is shippable only if it improves the slow plant without")
    w("degrading the fast one; read the two tables together, never one alone.")
    return "\n".join(out)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--controller", default="mpc")
    parser.add_argument("--scenarios", default=",".join(SCENARIO_NAMES))
    parser.add_argument("--seeds", type=int, default=2)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--plants", default=",".join(PLANTS))
    parser.add_argument(
        "--fast",
        default="",
        help=(
            "comma-separated true C_c values to synthesize lighter plants for "
            f"(e.g. '{','.join(str(c) for c in FAST_TRUE_CC)}'), appended to --plants"
        ),
    )
    parser.add_argument("--candidates", default=",".join(str(c) for c in CANDIDATES))
    parser.add_argument("--out", default=None)
    parser.add_argument(
        "--from",
        dest="from_json",
        default=None,
        help="re-render a saved --out file instead of running the cooks again",
    )
    args = parser.parse_args(argv)

    plants = [(p, None) for p in args.plants.split(",") if p]
    plants += [(_register_fast_plant(float(c)), float(c)) for c in args.fast.split(",") if c]
    candidates = [float(c) for c in args.candidates.split(",")]
    scenario_names = args.scenarios.split(",")
    if args.from_json:
        with open(args.from_json) as handle:
            rows = json.load(handle)
    else:
        jobs = [
            (args.controller, name, seed, c_c, plant, fast_true_cc)
            for plant, fast_true_cc in plants
            for c_c in candidates
            for name in scenario_names
            for seed in range(args.seeds)
        ]
        with Pool(args.workers) as pool:
            rows = pool.map(_run, jobs)

    report = _render(rows, [name for name, _ in plants], candidates, scenario_names)
    print(report)
    if args.out:
        with open(args.out, "w") as handle:
            json.dump(rows, handle, indent=2, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
