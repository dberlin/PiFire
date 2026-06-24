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
import warnings, sys, os
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # for approxmpc_span
sys.path.insert(0, os.getcwd())                                  # repo root for controller
import numpy as np
import torch
from controller.mpc import _DEFAULTS
from approxmpc_span import build_span_net, Q_ss, SPAN_NPZ, DIDX, ND   # noqa: E402

OUT = './controller/mpc_policy_net.npz'


def main():
    net, stats = build_span_net()
    xm, xs, rm, rs = stats
    # extract Linear layers from the torch Sequential, transpose W to [in,out]
    layers = [m for m in net.net if isinstance(m, torch.nn.Linear)]
    blob = {'n_layers': len(layers)}
    for i, lin in enumerate(layers):
        blob[f'W{i}'] = lin.weight.detach().numpy().T.astype(np.float32)   # [in,out]
        blob[f'b{i}'] = lin.bias.detach().numpy().astype(np.float32)
    blob['x_mean'] = xm.numpy().astype(np.float32)
    blob['x_std'] = xs.numpy().astype(np.float32)
    blob['r_mean'] = np.float32(rm)
    blob['r_std'] = np.float32(rs)
    # embed the FULL calibration the policy depends on (model + MPC tuning + bounds)
    from controller.mpc_net import _CALIB_FLOATS, _CALIB_INTS
    for k in _CALIB_FLOATS:
        blob[k] = np.float32(_DEFAULTS[k])
    for k in _CALIB_INTS:
        blob[k] = np.int64(_DEFAULTS[k])
    z = np.load(SPAN_NPZ)
    blob['sp_lo'] = np.float32(z['sp_lo']); blob['sp_hi'] = np.float32(z['sp_hi'])

    # reference pairs: full torch firing-rate (Q_ss + net) on real sampled states
    rng = np.random.default_rng(0)
    idx = rng.choice(len(z['u0']), size=64, replace=False)
    X0 = z['X0'][idx]; UP = z['u_prev'].flatten()[idx]; TS = z['t_set'].flatten()[idx]
    Xin = np.column_stack([X0, UP, TS])
    with torch.no_grad():
        inp = (torch.tensor(Xin, dtype=torch.float32) - xm) / xs
        resid = (net(inp).numpy().flatten() * rs + rm)
    Qref = np.clip(Q_ss(X0[:, DIDX], TS) + resid, _DEFAULTS['Q_min'], _DEFAULTS['Q_max'])
    blob['ref_state'] = X0.astype(np.float32)
    blob['ref_uprev'] = UP.astype(np.float32)
    blob['ref_set'] = TS.astype(np.float32)
    blob['ref_Q'] = Qref.astype(np.float32)

    np.savez_compressed(OUT, **blob)
    sz = os.path.getsize(OUT) / 1024
    print(f"exported {OUT} ({sz:.0f} KB): {len(layers)} layers, "
          f"input dim {blob['x_mean'].shape[0]}, span [{blob['sp_lo']:.0f},{blob['sp_hi']:.0f}]C")


if __name__ == '__main__':
    main()
