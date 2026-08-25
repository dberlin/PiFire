"""
PINNED TO THE TWO-LUMP GREY BOX (model schema 1). controller/mpc_model.py
now implements a single chamber lump, so the numbers below describe a model
this repo no longer has. Kept as a record of a finished question, not re-run.
See _pinned_two_lump.py.

Localize WHICH plant-mismatch element drives the 225->275F limit cycle. Against a
matched plant the cycle vanishes (osc_late ~0.08F); against full GrillSim it is
~7.4F. Here we disable realism elements one at a time and watch osc_late -- the
one whose removal collapses the oscillation is the culprit to model/robustify.

Toggles:
  no deadtime   : deadtime=1s (no transport delay)
  no wind       : disable the random gust loss multiplier
  no sensorlag  : probe_tau ~ instant
  no fuelbuffer : k_burn huge -> fuel burns the instant it arrives (no accumulation)
  no fan-lever  : fan_is_lever=False (fan stops modulating burn/transfer/loss)
  match params  : set C_c/T_amb/h_amb0/h_fc0 to the controller's model values
  ALL off       : everything above combined -> should approach the matched plant
"""

import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, ".")
import numpy as np

from controller.grill_sim import GrillSim
from controller.mpc import Controller
from controller.mpc_config import DEFAULT_MPC_CONFIG
from docs.superpowers.experiments import _pinned_two_lump  # noqa: F401,E402

_pinned_two_lump.require_pinned_model(__name__)

CYCLE = {"u_min": 0.1, "u_max": 0.9, "HoldCycleTime": 25}
C2F = lambda c: c * 9 / 5 + 32
SP0, SP1 = 225.0, 275.0


def make_plant(seed, *, deadtime=20, fan_lever=True, probe_tau=4.5, no_wind=False, no_buffer=False, match_params=False):
    p = GrillSim(seed=seed, deadtime=deadtime, fan_is_lever=fan_lever, probe_tau=probe_tau)
    if no_wind:
        p._wind = lambda: 1.0
    if no_buffer:
        p.k_burn = 50.0  # burn clamps to available fuel -> no pile-up
    if match_params:
        p.C_c = 320.0
        p.T_amb = 20.0
        p.h_amb0 = 0.385
        p.h_fc0 = 1.0
    return p


def run(plant_factory, seed=0, settle_min=60, hold_min=60):
    cfg = dict(DEFAULT_MPC_CONFIG)
    cfg["est_q_dist"] = 0.05
    c = Controller(cfg, "F", CYCLE)
    c.set_target(SP0)
    p = plant_factory(seed)
    PT = []
    step_sec = settle_min * 60
    total = (settle_min + hold_min) * 60
    cr, fan, on, period_start = 0.1, 100.0, 1, 0
    for sec in range(total):
        if sec == step_sec:
            c.set_target(SP1)
        if sec % 25 == 0:
            out = c.update(C2F(p.measured()))
            cr = float(np.clip(out["cycle_ratio"], 0.1, 0.9))
            fan = out["fan"]["duty"] or 100.0
            on = int(round(cr * 25))
            period_start = sec
        p.step(auger_on=(sec - period_start) < on, fan_frac=fan / 100.0)
        PT.append(C2F(p.true_Tc))
    PT = np.array(PT)
    post = PT[step_sec:]
    over = post.max() - SP1
    late = post[-20 * 60 :]
    return over, late.std(), post.max()


if __name__ == "__main__":
    configs = [
        ("full (mismatched)", lambda s: make_plant(s)),
        ("no deadtime", lambda s: make_plant(s, deadtime=1)),
        ("no wind", lambda s: make_plant(s, no_wind=True)),
        ("no sensorlag", lambda s: make_plant(s, probe_tau=0.5)),
        ("no fuelbuffer", lambda s: make_plant(s, no_buffer=True)),
        ("no fan-lever", lambda s: make_plant(s, fan_lever=False)),
        ("match params", lambda s: make_plant(s, match_params=True)),
        (
            "ALL off",
            lambda s: make_plant(
                s, deadtime=1, no_wind=True, probe_tau=0.5, no_buffer=True, fan_lever=False, match_params=True
            ),
        ),
    ]
    print("  (matched-plant reference: osc_late ~0.08F, overshoot ~1.7F)")
    print(f"{'disable':>20} {'overshoot':>10} {'osc_late':>9} {'peakF':>7}")
    for name, fac in configs:
        o, ol, pk = run(fac)
        print(f"{name:>20} {o:9.1f}F {ol:8.2f}F {pk:6.0f}F", flush=True)
