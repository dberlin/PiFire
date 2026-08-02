#!/usr/bin/env python3
"""Pointwise disagreement between the MPC net policy and the NLP it approximates.

Two closed-loop runs diverge on their own and cannot settle whether the net is
still the same policy. This runs the loop ONCE under the NLP, logs every
(x_hat, u_prev, set_point_c) the policy was asked about, and replays those exact
triples through the net. The difference is the approximation error on the states
the controller actually visits -- including the lid-open interval, which no
training episode contains.
"""

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from controller.grill_sim import GrillSim  # noqa: E402
from controller.mpc import Controller  # noqa: E402
from controller.mpc_net import NetPolicy, net_path_for  # noqa: E402

OUT = "./docs/superpowers/experiments/_net_vs_nlp_baseline.json"
ARTIFACT = "./controller/mpc_policy_net.npz"


def _c_to_f(c):
    return c * 9.0 / 5.0 + 32.0


def replay(seed=0, duration_s=3 * 3600, lid_open_at=2 * 3600, lid_open_for=120, setpoint_f=225.0):
    core = Controller({"policy": "nlp"}, "F", {"HoldCycleTime": 20, "u_min": 0.15, "u_max": 0.9})
    assert core._net is None, "configure policy=nlp; the point is to log the NLP's answers"
    core.set_target(setpoint_f)
    plant = GrillSim(seed=seed)
    period = core.get_control_period()

    triples, q_nlp, in_lid = [], [], []
    ratio, fan_frac, next_solve, anchor = core.u_min, 1.0, 0.0, 0.0
    for t in range(duration_s):
        lid = lid_open_at <= t < lid_open_at + lid_open_for
        if t >= next_solve:
            next_solve = t + period
            # snapshot the policy inputs BEFORE update() mutates them
            raw = core.update(_c_to_f(plant.measured()))
            triples.append(
                (np.asarray(core._x_hat).reshape(-1).copy(), float(core._policy_u_prev), float(core._set_point_c))
            )
            q_nlp.append(float(core._last_Q))
            in_lid.append(lid)
            ratio = min(max(float(raw["cycle_ratio"]), core.u_min), core.u_max)
            fan = raw.get("fan") or {}
            if fan.get("duty") is not None:
                fan_frac = float(fan["duty"]) / 100.0
            anchor = t
            if hasattr(core, "set_output"):
                from controller.applied_output import AppliedOutput, OutputSource

                core.set_output(
                    AppliedOutput(
                        ratio=0.0 if lid else ratio,
                        source=OutputSource.LID_OPEN if lid else OutputSource.CONTROLLER,
                        timestamp=float(t),
                    )
                )
        on = (not lid) and ((t - anchor) % 20) < 20 * ratio
        plant.step(auger_on=on, fan_frac=0.0 if lid else fan_frac)

    net = NetPolicy.load(net_path_for(ARTIFACT, bool(core.cfg["enable_fan_input"])))
    diffs = np.asarray([abs(net.firing_rate(x, u, sp) - q) for (x, u, sp), q in zip(triples, q_nlp)])
    lid_mask = np.asarray(in_lid)
    return {
        "seed": seed,
        "n": int(diffs.size),
        "n_lid": int(lid_mask.sum()),
        "rms_all": float(np.sqrt((diffs**2).mean())),
        "max_all": float(diffs.max()),
        "rms_lid": float(np.sqrt((diffs[lid_mask] ** 2).mean())) if lid_mask.any() else None,
        "max_lid": float(diffs[lid_mask].max()) if lid_mask.any() else None,
        "q_span": float(core.cfg["Q_max"] - core.cfg["Q_min"]),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Measure net-vs-NLP policy disagreement.")
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args(argv)
    rows = [replay(seed=s) for s in args.seeds]
    with open(args.out, "w") as f:
        json.dump(rows, f, indent=1, sort_keys=True)
    for r in rows:
        print(
            f"seed {r['seed']}: rms_all={r['rms_all']:.3f} max_all={r['max_all']:.3f} "
            f"rms_lid={r['rms_lid']} max_lid={r['max_lid']} (Q span {r['q_span']:.0f})"
        )


if __name__ == "__main__":
    main()
