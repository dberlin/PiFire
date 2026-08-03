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

from controller.model_promotion import _T_FLOOR_C, _T_HAZARD_C, _effective_tau, _slowest_tau
from controller.mpc_model import simulate_grey_box

# Keys the controller reads back out of a fitted result.
CONFIG_KEYS = ("C_f", "C_c", "h_fc", "h_amb", "T_amb", "theta", "n_delay", "K_Q", "sigma")

# Fitted free parameters. C_f and sigma are held at their init values.
#
# The dynamics are invariant under scaling every capacitance, every conductance
# and the input gain -- (C_f, C_c, h_fc, h_amb, K_Q, sigma) -- by one common
# factor: both state equations are homogeneous in them, so the trajectory of
# the one measured state is bit-identical. Six parameters therefore carry five
# identifiable degrees of freedom, and one must be held to fix the scale. What
# a log determines is the ratios among them, which is what the controller
# plans against: the effective time constant
# C_c/(h_amb + 4*sigma*(T+273.15)**3) is one of them, and is unchanged by the
# choice of which parameter is held.
#
# So holding sigma is not a limitation to be lifted -- freeing it while K_Q is
# also free simply lets the solver wander along the unobservable direction and
# report a confident, arbitrary value. See
# docs/superpowers/experiments/sigma_identifiability.py.
_FREE = ("K_Q", "C_c", "h_fc", "h_amb", "theta")

_SIM_KEYS = ("C_f", "C_c", "h_fc", "h_amb", "K_Q", "sigma", "theta", "n_delay")

# Magnitude each free parameter is scaled by before the solve. The free set
# spans roughly 1e-1 to 1e3, and scipy's finite-difference step is
# eps**0.5 * max(1, |x|) -- the max(1, ...) makes that step absolute rather
# than relative for anything below 1, so parameters far apart in magnitude are
# probed with wildly different effective precision. Solving in x/nominal puts
# every free parameter near 1, which makes the step a true relative one: on the
# reference cook the unscaled solve never converged at all, exhausting
# max_nfev, where the scaled solve reaches a lower cost in ~70 evaluations.
_NOMINAL = {"K_Q": 3.5, "C_c": 320.0, "h_fc": 1.3, "h_amb": 0.5, "theta": 50.0}

# Strictly positive: theta divides the lag time constant, and every other free
# parameter is a capacitance or a conductance.
_LOWER_BOUND = 1e-9


def _sim_kwargs(params):
    return {k: params[k] for k in _SIM_KEYS}


def fit_params(t, temp, Q, *, T_amb, init, sigma=0.0, n_delay=0):
    """Fit the free grey-box parameters to a logged temperature series.

    `sigma` is returned exactly as passed: it is what fixes the scale the other
    parameters are measured against, and a log cannot determine it -- see
    `_FREE`.
    """
    temp = np.asarray(temp, dtype=float)
    C_f = float(init["C_f"])
    scale = np.array([_NOMINAL[k] for k in _FREE], dtype=float)
    x0 = np.array([float(init[k]) for k in _FREE], dtype=float) / scale
    lo = _LOWER_BOUND / scale

    def residual(z):
        params = dict(zip(_FREE, z * scale))
        params.update(C_f=C_f, sigma=sigma, n_delay=n_delay)
        return simulate_grey_box(t, Q, T_amb=T_amb, T0=float(temp[0]), **_sim_kwargs(params)) - temp

    res = least_squares(residual, np.maximum(x0, lo), method="trf", bounds=(lo, np.inf), max_nfev=2000)
    out = dict(zip(_FREE, (float(v) for v in res.x * scale)))
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
    # Both quantities come from controller/model_promotion.py rather than being
    # recomputed here, so the horizon this utility asks for and the horizon the
    # promotion policy reports cannot drift apart. The two are deliberately
    # different: C_c/h_amb bounds the effective time constant from above at
    # every temperature and so sizes one horizon for the whole operating range,
    # while the effective values below include radiative conductance and are
    # what the chamber's response actually is at each end of that range.
    horizon = float(_DEFAULTS["n_horizon"]) * float(_DEFAULTS["t_step"])
    tau = _slowest_tau(payload)
    print(
        f"Chamber time constant: {_effective_tau(payload, _T_HAZARD_C):.0f} s at "
        f"{_T_HAZARD_C:.0f} C rising to {_effective_tau(payload, _T_FLOOR_C):.0f} s at {_T_FLOOR_C:.0f} C"
    )
    if horizon < tau:
        print(
            f"WARNING: fitted chamber time constant reaches {tau:.0f} s but the default prediction\n"
            f"         horizon is only {horizon:.0f} s. Raise n_horizon or t_step, or the\n"
            "         controller cannot see far enough ahead to stop in time."
        )
    print("\nPaste into Settings > Controller (controller.config.mpc):")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
