"""
*****************************************
 PiFire MPC Braking Distance: the bound over the model's dead-time shortfall
*****************************************

 controller/model_promotion.braking_distance sizes the prediction horizon from
 how long the chamber keeps rising after a full fuel cut. Read straight off the
 fitted model it UNDER-states that on a real grill, and this measures by how
 much so the estimate can be made a bound again.

 Why it under-states is not the closed form -- braking_distance_check.py
 measures that separately and finds it errs long. It is the model itself: the
 Erlang chain recovers only part of a grill's real transport delay (0.71x of
 the reference plant's at the shipped n_delay=8, see _ndelay_sweep.txt), and
 the estimate integrates the chain's own survival, so a chain that is short
 predicts a coast that is short.

 WHAT IS MEASURED. For each plant in controller/grill_sim.py, the single-lump
 grey box is fitted to that plant across the normal scenario set -- the fit a
 real calibration produces -- and then, for reference temperatures across the
 operating range:

   * the PLANT's own braking time: drive it at full fire from cold, and at the
     instant the chamber crosses the reference temperature, fork it and cut
     fuel to zero. Seconds from the cut to the chamber's peak.
   * the ESTIMATE the model itself gives -- _model_coast(fitted, t_ref), the
     reading BEFORE the bound, since the bound is what is being derived.

 The ratio of the two is what the bound has to cover. It is taken at the WORST
 observed recovery rather than the mean, because a bound is set by its worst
 case, and the factor is 1/worst rounded up.

 Two things this deliberately does not do. It does not measure against a coast
 in DEGREES: the factor multiplies a time, so it has to be derived from a time.
 And it does not touch the real MAK cook -- that record is the independent
 validation of the resulting factor, and a factor derived from it could not be
 checked against it.

 The cut is only taken once the plant has been at full fire long enough for its
 own transport to be charged, which is what braking_distance assumes at the
 instant of the cut. Reference temperatures reached before that are skipped
 rather than scored, and so are ones where the plant has nearly no coast left
 to measure.

 Usage:
   uv run --with numba python -m docs.superpowers.experiments.braking_bound
*****************************************
"""

import copy
import os
import sys

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from controller.grill_sim import DT, GrillSim, MAKGrillSim  # noqa: E402

# The MODEL's own reading, not the public `braking_distance` -- that one already
# carries `_COAST_BOUND`, which is the number this script exists to derive, so
# importing it would measure the factor against itself and report that whatever
# is currently shipped is exactly right.
from controller.model_promotion import _model_coast  # noqa: E402
from controller.mpc_config import DEFAULT_MPC_CONFIG  # noqa: E402
from docs.superpowers.experiments.ndelay_sweep_plants import (  # noqa: E402
    LUMP_FREE,
    NORMAL,
    fit_joint,
    run_plant,
)

#: The chain has to be charged at the instant of the cut, which is what the
#: estimate assumes. Three chain means is 95% of the way there for an Erlang of
#: any order, so a cut taken earlier than this is measuring the charge-up
#: instead of the coast and is not scored.
CHARGE_MEANS = 3.0

#: Below this the plant's coast is at the resolution of its own 1 s step and
#: the ratio measures rounding rather than the model.
MIN_COAST_S = 5.0

#: Reference temperatures, in C, across the operating range the promotion gate
#: reads. Literals rather than a linspace so the table is stable to read.
T_REFS = (60.0, 100.0, 140.0, 180.0, 220.0, 260.0)

#: What the fan does during the coast. 1.0 is the condition the fits are
#: calibrated under; 0.40 is controllers.json's default fan_min_pct; 0.0 is
#: what that field's own minimum permits.
CUT_FANS = (1.0, 0.40, 0.0)


def plant_braking_time(plant_name, t_ref_c, *, fan=1.0, cut_fan=None, max_s=20000):
    """Seconds the plant keeps rising after a full fuel cut taken at `t_ref_c`.

    Driven at full fire from cold; at the first step where the true chamber
    temperature crosses `t_ref_c` the simulator is forked and the fork is run
    with the auger off. Returns (coast_s, full_fire_s_before_the_cut), or
    (None, None) if the plant never reaches the reference temperature.

    `cut_fan` is what the fan does during the coast, and it is not a detail:
    controller/grill_sim.py scales the chamber's ambient conductance by
    (0.8 + 0.5 * fan), so a fan left at full dumps heat 1.6x faster than one at
    rest and the plant stops rising sooner. `braking_distance` models neither
    -- it reads the loss the fitted h_amb carries. Both ends are measured
    because the bound has to cover the slower one.
    """
    cls = GrillSim if plant_name == "generic" else MAKGrillSim
    kw = {"seed": 0, "fixed_fan": fan}
    if plant_name == "mak":
        kw["T0"] = 20.0
    s = cls(**kw)
    if plant_name == "generic":
        s.T_f = s.T_c = s.T_meas = 20.0
    coast_fan = fan if cut_fan is None else float(cut_fan)

    for i in range(int(max_s / DT)):
        s.step(auger_on=1.0, fan_frac=fan, lid_open=False)
        if s.true_Tc >= t_ref_c:
            charged_s = (i + 1) * DT
            cut = copy.deepcopy(s)
            cut.fixed_fan = coast_fan
            peak = cut.true_Tc
            for j in range(int(max_s / DT)):
                cut.step(auger_on=0.0, fan_frac=coast_fan, lid_open=False)
                if cut.true_Tc <= peak:
                    return j * DT, charged_s
                peak = cut.true_Tc
            return float("inf"), charged_s
    return None, None


def fitted_model(plant_name, n_delay):
    """The single-lump parameters a calibration of this plant lands on."""
    records = [run_plant(plant_name, scen) for scen in NORMAL if scen != "lid"]
    _, params, _ = fit_joint(records, LUMP_FREE, two_state=False, starts=6)
    out = {k: float(params[k]) for k in ("C_c", "h_amb", "T_amb", "theta", "K_Q", "sigma")}
    out["n_delay"] = int(n_delay)
    return out


def main():
    n_delay = int(DEFAULT_MPC_CONFIG["n_delay"])
    print(f"single-lump fits at the shipped n_delay={n_delay}, fitted per plant with {list(LUMP_FREE)}")
    print()

    ratios = []
    for plant_name in ("generic", "mak"):
        p = fitted_model(plant_name, n_delay)
        charge_floor = CHARGE_MEANS * p["theta"]
        print(
            f"=== {plant_name} === C_c={p['C_c']:.1f} h_amb={p['h_amb']:.3f} "
            f"K_Q={p['K_Q']:.3f} theta={p['theta']:.1f} (chain charged after {charge_floor:.0f}s)"
        )
        # The fan during the coast is swept, not assumed. 1.0 is the condition
        # the fit was calibrated under, so it is the like-for-like reading;
        # 0.40 is controller/controllers.json's default fan_min_pct, the
        # slowest a DEFAULT install ever coasts; 0.0 is what that field's own
        # minimum permits an operator to select.
        head = "".join(f" | {'fan=' + f'{f:.2f}':>11} {'ratio':>7}" for f in CUT_FANS)
        print(f"  {'T_ref':>6}{head} | {'estimate':>9}  full fire before cut")
        for t_ref in T_REFS:
            coasts = {}
            charged = None
            for f in CUT_FANS:
                c, ch = plant_braking_time(plant_name, t_ref, cut_fan=f)
                coasts[f] = c
                charged = ch if charged is None else charged
            if coasts[CUT_FANS[0]] is None:
                print(f"  {t_ref:6.0f} | {'never reached':>11}")
                continue
            est = _model_coast(p, t_ref)
            note = ""
            scored = True
            if charged < charge_floor:
                note, scored = "  (chain not yet charged -- not scored)", False
            elif min(coasts.values()) < MIN_COAST_S:
                note, scored = "  (coast below the plant's own resolution -- not scored)", False
            elif not np.isfinite(est) or est <= 0.0:
                note, scored = "  (no finite estimate -- not scored)", False
            body = "".join(f" | {coasts[f]:10.1f}s {est / coasts[f]:7.3f}" for f in CUT_FANS)
            print(f"  {t_ref:6.0f}{body} | {est:8.1f}s  {charged:.0f}s{note}")
            if scored:
                for f in CUT_FANS:
                    ratios.append((est / coasts[f], plant_name, t_ref, f))
        print()

    print(f"{len(ratios)} scored readings across both plants and {len(CUT_FANS)} coast-fan conditions")
    for f in CUT_FANS:
        sub = np.array([x[0] for x in ratios if x[3] == f])
        worst = sub.min()
        print(
            f"  coast fan {f:4.2f}: worst recovery {worst:.3f}  median {np.median(sub):.3f}"
            f"  -> a bound needs {1.0 / worst:.3f}   ({(sub >= 1.0).sum()}/{len(sub)} already bound)"
        )
    print()
    print("  the fan floor is a CONFIGURATION choice (controllers.json fan_min_pct,")
    print("  default 40%, settable to 0), so which row above sets the factor is a")
    print("  decision about which configurations the bound is claimed to cover.")


if __name__ == "__main__":
    main()
