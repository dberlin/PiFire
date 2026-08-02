#!/usr/bin/env python3

"""
*****************************************
 PiFire MPC Offline Calibration Utility
*****************************************

 Fits the grey-box thermal parameters to a logged history CSV so the MPC model
 describes a specific grill. Fitting runs through controller.mpc_model's shared
 forward simulator, so the parameters produced describe the same dynamics the
 controller plans against -- radiative loss and transport deadtime included.

 CSV columns: time_s, temp_c, Q  (Q is the firing-rate demand; if you logged
 auger duty instead, map it back through the allocator first). Capture the log
 with the fan under the controller's command: a log taken with the fan pinned
 at one duty only describes the grill at that duty.

 Usage: python -m controller.update_mpc history.csv [--t-amb 20] [--json]
*****************************************
"""

import argparse
import json

import numpy as np
from scipy.optimize import least_squares

from controller.mpc_model import simulate_grey_box

# Keys the controller reads back out of a fitted result.
CONFIG_KEYS = ("C_f", "C_c", "h_fc", "h_amb", "T_amb", "theta", "n_delay", "K_Q", "sigma")

# Fitted free parameters. C_f is held at its init value: it is redundant with
# K_Q for the steady gain, so fitting both is ill-posed.
_FREE = ("K_Q", "C_c", "h_fc", "h_amb", "theta")

_SIM_KEYS = ("C_f", "C_c", "h_fc", "h_amb", "K_Q", "sigma", "theta", "n_delay")


def _sim_kwargs(params):
    return {k: params[k] for k in _SIM_KEYS}


def fit_params(t, temp, Q, *, T_amb, init, sigma=0.0, n_delay=0):
    """Fit the free grey-box parameters to a logged temperature series."""
    temp = np.asarray(temp, dtype=float)
    C_f = float(init["C_f"])
    x0 = np.array([float(init[k]) for k in _FREE], dtype=float)

    def residual(x):
        params = dict(zip(_FREE, x))
        params.update(C_f=C_f, sigma=sigma, n_delay=n_delay)
        return simulate_grey_box(t, Q, T_amb=T_amb, T0=float(temp[0]), **_sim_kwargs(params)) - temp

    # Strictly positive: theta divides the lag time constant, and every other
    # free parameter is a capacitance or a conductance.
    res = least_squares(residual, x0, method="trf", bounds=(1e-9, np.inf), max_nfev=2000)
    out = dict(zip(_FREE, (float(v) for v in res.x)))
    out.update(C_f=C_f, sigma=float(sigma), n_delay=int(n_delay), T_amb=float(T_amb))
    return out


def fit_quality(t, temp, Q, fitted, *, T_amb):
    """(RMSE, max absolute error) in degrees C between the fit and the log."""
    temp = np.asarray(temp, dtype=float)
    params = dict(fitted)
    params["T_amb"] = T_amb
    sim = simulate_grey_box(t, Q, T_amb=T_amb, T0=float(temp[0]), **_sim_kwargs(params))
    err = sim - temp
    return float(np.sqrt(np.mean(err**2))), float(np.max(np.abs(err)))


def main():
    ap = argparse.ArgumentParser(description="Fit MPC grey-box parameters to a calibration log.")
    ap.add_argument("csv")
    ap.add_argument("--t-amb", type=float, default=None, help="Ambient temperature in C")
    ap.add_argument("--json", action="store_true", help="Print only the fitted config JSON")
    args = ap.parse_args()

    import pandas as pd

    from controller.mpc import _DEFAULTS

    df = pd.read_csv(args.csv)
    t = df["time_s"].values
    temp = df["temp_c"].values
    Q = df["Q"].values

    T_amb = args.t_amb if args.t_amb is not None else float(_DEFAULTS["T_amb"])
    init = {k: float(_DEFAULTS[k]) for k in ("C_f", "C_c", "h_fc", "h_amb", "K_Q", "theta")}
    fitted = fit_params(
        t,
        temp,
        Q,
        T_amb=T_amb,
        init=init,
        sigma=float(_DEFAULTS["sigma"]),
        n_delay=int(_DEFAULTS["n_delay"]),
    )
    payload = {k: fitted[k] for k in CONFIG_KEYS}

    if args.json:
        print(json.dumps(payload, indent=2))
        return

    rmse, max_err = fit_quality(t, temp, Q, fitted, T_amb=T_amb)
    print(f"Fit quality: RMSE {rmse:.2f} C, max error {max_err:.2f} C")
    if rmse > 10.0:
        print(
            "WARNING: RMSE above 10 C. This fit does not describe the log. Check that the log\n"
            "         covers a full heat-up and at least one step down, and that the fan was\n"
            "         under the controller's command throughout."
        )
    horizon = float(_DEFAULTS["n_horizon"]) * float(_DEFAULTS["t_step"])
    tau = payload["C_c"] / payload["h_amb"]
    if horizon < tau:
        print(
            f"WARNING: fitted chamber time constant is {tau:.0f} s but the default prediction\n"
            f"         horizon is only {horizon:.0f} s. Raise n_horizon or t_step, or the\n"
            "         controller cannot see far enough ahead to stop in time."
        )
    print("\nPaste into Settings > Controller (controller.config.mpc):")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
