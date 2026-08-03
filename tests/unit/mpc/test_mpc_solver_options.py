"""The NLP is warm-started and iteration-capped.

do_mpc hands IPOPT the previous solve's duals on every step after the first;
without warm_start_init_point IPOPT throws them away. The iteration cap bounds
the tail: the cold start needs ~60 iterations with nothing to warm from, and
truncating it is what keeps the worst case under the control period.
"""

import numpy as np
import pytest

from controller.grill_sim import MAKGrillSim
from controller.mpc import Controller

CONFIG = dict(
    n_horizon=24,
    t_step=25.0,
    control_period=5.0,
    Q_w=1.0,
    R_dQ=0.1,
    Q_min=5.0,
    Q_max=100.0,
    C_c=320.0,
    h_amb=0.5,
    T_amb=20.0,
    theta=50.0,
    n_delay=4,
    K_Q=3.5,
    sigma=1.4e-9,
    policy="nlp",
    estimator="ekf",
    est_q_temp=1e-2,
    est_q_dist=0.05,
    est_r_meas=0.04,
    enable_fan_input=True,
    fan_min_pct=40.0,
    fan_max_pct=100.0,
)
CYCLE = {"u_min": 0.1, "u_max": 0.9, "HoldCycleTime": 25}


def _opts(controller):
    """The options dict handed to nlpsol, as do_mpc stored it."""
    return dict(controller.mpc.settings.nlpsol_opts)


def test_warm_start_is_enabled():
    c = Controller(dict(CONFIG), "C", dict(CYCLE))
    assert _opts(c)["ipopt.warm_start_init_point"] == "yes"


def test_iterations_are_capped():
    c = Controller(dict(CONFIG), "C", dict(CYCLE))
    assert int(_opts(c)["ipopt.max_iter"]) == 10


def test_the_cap_does_not_change_the_commanded_trajectory():
    """The cap truncates 7 of 180 solves in the measured run; the resulting
    command differs by well under 1% of the [u_min, u_max] span. This pins
    that, so a future cap change that actually alters control fails here."""
    ratios = {}
    for label, cap in (("capped", 10), ("uncapped", 3000)):
        c = Controller(dict(CONFIG), "C", dict(CYCLE))
        c.mpc.settings.nlpsol_opts["ipopt.max_iter"] = cap
        c.mpc.setup()
        c.set_target(110.0)
        sim = MAKGrillSim(seed=0, T0=40.7, fixed_fan=1.0)
        seq, ratio = [], 0.1
        for t in range(300):
            if t % 5 == 0:
                ratio = float(np.clip(c.update(sim.measured())["cycle_ratio"], 0.1, 0.9))
                seq.append(ratio)
            sim.step((t % 25) < ratio * 25, 1.0)
        ratios[label] = np.array(seq)
    assert np.abs(ratios["capped"] - ratios["uncapped"]).max() < 1e-2
