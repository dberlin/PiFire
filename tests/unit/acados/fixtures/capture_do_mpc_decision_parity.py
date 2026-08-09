#!/usr/bin/env python3
"""Recapture the Task 8 corpus from PiFire revision cd329fe72c7c.

Run this file only from an isolated checkout of the pinned source revision. It
prints the raw do-mpc/IPOPT first-command rows; the live parity test never
imports or executes this legacy capture utility.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from typing import Any

import numpy as np

from controller.mpc import Controller, _DEFAULTS

def capture_case(config: dict[str, Any], case: dict[str, Any]) -> dict[str, object]:
    controller_config = dict(
        _DEFAULTS,
        C_c=config["C_c"],
        h_amb=config["h_amb"],
        T_amb=config["T_amb"],
        theta=config["theta"],
        K_Q=config["K_Q"],
        sigma=config["sigma"],
        n_horizon=config["horizon_steps"],
        n_delay=8,
        Q_w=config["temperature_weight"],
        R_dQ=config["move_weight"],
        policy="nlp",
        estimator="ekf",
        enable_online_adaptation=False,
    )
    with redirect_stdout(io.StringIO()):
        controller = Controller(controller_config, "C", {"u_min": 0.1, "u_max": 0.93})
    controller.set_target(float(case["setpoint_c"]))
    equilibrium_q = float(case["equilibrium_q"])
    controller._policy_equilibrium_load = equilibrium_q
    state = np.asarray(case["state"], dtype=float).reshape(-1, 1)
    legacy_mpc: Any = controller.mpc
    legacy_mpc.x0 = state
    legacy_mpc.u0 = np.asarray(
        [[float(case["q_previous"]) - equilibrium_q]],
        dtype=float,
    )
    legacy_mpc.set_initial_guess()
    residual = float(np.asarray(legacy_mpc.make_step(state)).reshape(-1)[0])
    return {
        "name": case["name"],
        "do_mpc_first_q": residual + equilibrium_q,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("fixture", type=Path)
    args = parser.parse_args()
    corpus = json.loads(args.fixture.read_text(encoding="utf-8"))
    rows = [capture_case(corpus["config"], case) for case in corpus["cases"]]
    print(json.dumps(rows, sort_keys=True, separators=(",", ":"), allow_nan=False))


if __name__ == "__main__":
    main()
