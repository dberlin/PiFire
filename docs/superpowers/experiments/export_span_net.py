#!/usr/bin/env python3
"""
Train the setpoint-spanning residual net and export it as a portable, pure-numpy
artifact (controller/mpc_policy_net.npz) for the production NetPolicy.

The artifact embeds: layer weights (transposed to [in,out] for z@W+b), input and
residual scaling, the calibration the net was trained for (so the controller can
verify it matches config), the trained setpoint span, and a handful of torch-
computed reference (state,u_prev,T_set)->Q pairs so a numpy-only test can verify
forward fidelity without torch.
"""

import warnings, sys, os, argparse

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # for approxmpc_span
sys.path.insert(0, os.getcwd())  # repo root for controller
import numpy as np
import torch
from approxmpc_span import DIDX, Q_ss, build_span_net, load_span_dataset  # noqa: E402
from controller.mpc_net import _CALIB_FLOATS, _CALIB_INTS


def main(data_path, out, enable_fan, *, expected_episodes=500, expected_seed=0):
    dataset = load_span_dataset(
        data_path,
        expected_enable_fan=enable_fan,
        expected_episodes=expected_episodes,
        expected_seed=expected_seed,
    )
    net, stats, provenance = build_span_net(dataset=dataset)
    xm, xs, rm, rs = stats
    # extract Linear layers from the torch Sequential, transpose W to [in,out]
    layers = [m for m in net.net if isinstance(m, torch.nn.Linear)]
    blob = {"n_layers": len(layers)}
    for i, lin in enumerate(layers):
        blob[f"W{i}"] = lin.weight.detach().numpy().T.astype(np.float32)  # [in,out]
        blob[f"b{i}"] = lin.bias.detach().numpy().astype(np.float32)
    blob["x_mean"] = xm.numpy().astype(np.float32)
    blob["x_std"] = xs.numpy().astype(np.float32)
    blob["r_mean"] = np.float32(rm)
    blob["r_std"] = np.float32(rs)
    # The runtime fields are copied only from provenance which has already
    # matched the active model; never restamp them from current constants.
    for key, value in provenance.items():
        blob[f"source_{key}"] = value
    for key in ("model_schema", "allocator_revision", *_CALIB_FLOATS, *_CALIB_INTS):
        blob[key] = provenance[key]
    blob["sp_lo"] = provenance["sp_lo"]
    blob["sp_hi"] = provenance["sp_hi"]

    # Reference pairs: full torch normalized command on real sampled states.
    if len(dataset["u0"]) < 64:
        raise ValueError("dataset sampled_state_count must be at least 64 for reference-pair fidelity")
    rng = np.random.default_rng(0)
    idx = rng.choice(len(dataset["u0"]), size=64, replace=False)
    X0 = dataset["X0"][idx]
    UP = dataset["u_prev"][idx]
    TS = dataset["t_set"][idx]
    Xin = np.column_stack([X0, UP, TS])
    with torch.no_grad():
        inp = (torch.tensor(Xin, dtype=torch.float32) - xm) / xs
        resid = net(inp).numpy().flatten() * rs + rm
    normalized_load = np.clip(Q_ss(X0[:, DIDX], TS) + resid, 0.0, 1.0)
    blob["ref_state"] = X0.astype(np.float32)
    blob["ref_uprev"] = UP.astype(np.float32)
    blob["ref_set"] = TS.astype(np.float32)
    blob["ref_combustion_load"] = normalized_load.astype(np.float32)

    np.savez_compressed(out, **blob)
    sz = os.path.getsize(out) / 1024
    print(
        f"exported {out} ({sz:.0f} KB): {len(layers)} layers, fan={bool(enable_fan)}, "
        f"span [{float(blob['sp_lo']):.0f},{float(blob['sp_hi']):.0f}]C, "
        f"episodes={int(provenance['episode_count'])}, samples={int(provenance['sampled_state_count'])}"
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="./docs/superpowers/experiments/_ampc_data/pifire_span.npz")
    ap.add_argument("--out", default="./controller/mpc_policy_net.npz")
    ap.add_argument("--enable-fan", action="store_true")
    ap.add_argument("--expected-episodes", type=int, default=500)
    ap.add_argument("--expected-seed", type=int, default=0)
    a = ap.parse_args()
    main(
        a.data,
        a.out,
        a.enable_fan,
        expected_episodes=a.expected_episodes,
        expected_seed=a.expected_seed,
    )
