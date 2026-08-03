#!/usr/bin/env python3

"""
*****************************************
 PiFire MPC Free-Set Identifiability Experiment
*****************************************

 Chooses controller/update_mpc.py's `_FREE` set on evidence rather than on
 RMSE. A calibration log is judged here by whether the model it produces
 recovers the two quantities the controller brakes with -- the dead time
 before the chamber responds to more fuel, and the coast after fuel is cut --
 so those are what the candidate free sets are scored on.

 Both quantities are read off the fitted model and off the plant by the same
 rule, so the pair is one quantity measured on two systems rather than two
 definitions compared to each other. Dead time is read by differencing a
 stepped run against an unstepped one from an identical state, which removes
 the warm-up drift a threshold-on-absolute-temperature rule would mistake for
 a response. Coast is read from a chamber driven to a fixed reference
 temperature under full fire and then cut, which needs no steady state -- the
 fitted models' implied steady states are far outside the range any of these
 grills reach.

 Every candidate is fitted from the shipped defaults and from perturbed
 restarts of them, on the real MAK cook and on synthetic cooks driven through
 both plants in controller/grill_sim.py. The scatter across restarts is the
 measurement of interest: a free set the log cannot determine still reports a
 confident answer, but one that moves when the starting point does.

 Usage: python -m docs.superpowers.experiments.free_set_identifiability
*****************************************
"""

import itertools
import os
import zlib
from concurrent.futures import ProcessPoolExecutor

import numpy as np

from controller.grill_sim import DT, GrillSim, MAKGrillSim
from controller.mpc_model import simulate_grey_box
from controller.update_mpc import _sim_kwargs, fit_params, fit_quality

#: Shipped grey-box defaults -- the point a calibration actually starts from.
DEFAULTS = dict(C_f=9.0, C_c=320.0, h_fc=1.3, h_amb=0.50, T_amb=20.0, theta=50.0, n_delay=4, K_Q=3.5, sigma=1.4e-9)

Q_MIN, Q_MAX = 5.0, 100.0
U_MIN, U_MAX = 0.15, 0.9
CYCLE_S = 20.0
LOG_DT = 5.0

#: The incident's setpoint, in the Celsius the model works in. Both probes are
#: read at this chamber temperature so the plant and the model are asked about
#: the same operating point.
T_REF_C = (450.0 - 32.0) * 5.0 / 9.0

#: Firing demand the dead-time probe steps from and to.
Q_LO, Q_HI = 30.0, 100.0

#: Chamber movement that counts as the response having started.
MOVE_C = 0.5

#: Free sets considered. Every one is a subset of the five parameters a log
#: could in principle move; sigma stays held for the scale argument in
#: update_mpc.py's `_FREE` comment. The sets in the second group hold h_amb
#: instead of C_f: with the firepot fast enough to be quasi-static the
#: chamber depends only on C_c/h_amb, K_Q/h_amb and sigma/h_amb, so freeing
#: C_c, h_amb and K_Q together leaves a direction the log cannot see, and a
#: held sigma is what the solver runs the other three away from.
CANDIDATES = [
    ("h_amb",),
    ("h_amb", "theta"),
    ("K_Q", "h_amb"),
    ("K_Q", "h_amb", "theta"),
    ("C_c", "h_amb", "theta"),
    ("K_Q", "C_c", "h_amb"),
    ("K_Q", "C_c", "h_amb", "theta"),
    ("K_Q", "C_c", "h_fc", "h_amb"),
    ("K_Q", "C_c", "h_fc", "h_amb", "theta"),
    ("C_c", "theta"),
    ("K_Q", "C_c"),
    ("K_Q", "C_c", "theta"),
    ("K_Q", "C_c", "h_fc", "theta"),
    ("K_Q", "C_c", "sigma", "theta"),
    ("K_Q", "C_c", "h_fc", "sigma", "theta"),
]

RESTARTS = 5
PERTURB = 0.35


# ------------------------------------------------------------------- the model


def integrate(p, *, q, T_c0, T_f0, lags0, dt=1.0):
    """The grey box advanced from an explicit state, one `dt` per entry of `q`.

    controller/mpc_model.py's `simulate_grey_box` always starts with an empty
    lag chain and a firepot at chamber temperature, which is the right initial
    condition for fitting a log that begins at rest and the wrong one for
    asking what a hot, fully-fired grill does when its fuel is cut. Same
    dynamics, initial state exposed; `same_as_simulate_grey_box` pins that.
    """
    n = max(int(p["n_delay"]), 0)
    theta = float(p["theta"])
    lag_tau = (theta / n) if (n > 0 and theta > 0.0) else 0.0
    lags = np.array(lags0, dtype=float)
    C_f, C_c = float(p["C_f"]), float(p["C_c"])
    h_fc, h_amb = float(p["h_fc"]), float(p["h_amb"])
    K_Q, sigma, T_amb = float(p["K_Q"]), float(p["sigma"]), float(p["T_amb"])
    T_f, T_c = float(T_f0), float(T_c0)
    out = np.empty(len(q))
    for i, u in enumerate(q):
        out[i] = T_c
        if lag_tau > 0.0:
            prev = float(u)
            for j in range(n):
                lags[j] += dt * (prev - lags[j]) / lag_tau
                prev = lags[j]
            heat_in = lags[-1]
        else:
            heat_in = float(u)
        rad = sigma * ((T_c + 273.15) ** 4 - (T_amb + 273.15) ** 4)
        dT_f = (K_Q * heat_in - h_fc * (T_f - T_c)) / C_f
        dT_c = (h_fc * (T_f - T_c) - h_amb * (T_c - T_amb) - rad) / C_c
        T_f += dt * dT_f
        T_c += dt * dT_c
    return out, dict(T_c=T_c, T_f=T_f, lags=lags)


def same_as_simulate_grey_box(p):
    """Whether `integrate` reproduces the shipped simulator from rest."""
    q = np.concatenate([np.full(300, 40.0), np.full(300, 95.0)])
    t = np.arange(0.0, len(q), 1.0)
    ref = simulate_grey_box(t, q, T_amb=p["T_amb"], T0=p["T_amb"], **_sim_kwargs(p))
    mine, _ = integrate(p, q=q, T_c0=p["T_amb"], T_f0=p["T_amb"], lags0=np.zeros(int(p["n_delay"])))
    return float(np.max(np.abs(ref - mine)))


def _model_hot_state(p, *, q, t_ref_c, cap=40000):
    """The model's state the first time full fire carries it up to `t_ref_c`."""
    T_c, T_f = float(p["T_amb"]), float(p["T_amb"])
    lags = np.zeros(max(int(p["n_delay"]), 0))
    block = 200
    for _ in range(cap // block):
        temps, st = integrate(p, q=np.full(block, q), T_c0=T_c, T_f0=T_f, lags0=lags)
        T_c, T_f, lags = st["T_c"], st["T_f"], st["lags"]
        if T_c >= t_ref_c:
            return st
    return dict(T_c=T_c, T_f=T_f, lags=lags)


def model_coast(p, *, t_ref_c=T_REF_C, span=20000):
    """Seconds the model's chamber keeps rising after its fuel is cut."""
    st = _model_hot_state(p, q=Q_MAX, t_ref_c=t_ref_c)
    temps, _ = integrate(p, q=np.zeros(span), T_c0=st["T_c"], T_f0=st["T_f"], lags0=st["lags"])
    return float(np.argmax(temps))


def model_dead_time(p, *, t_ref_c=T_REF_C, span=3000):
    """Seconds before the model's chamber notices a step up in firing demand."""
    st = _model_hot_state(p, q=Q_LO, t_ref_c=t_ref_c)
    kw = dict(T_c0=st["T_c"], T_f0=st["T_f"], lags0=st["lags"])
    held, _ = integrate(p, q=np.full(span, Q_LO), **kw)
    stepped, _ = integrate(p, q=np.full(span, Q_HI), **kw)
    moved = np.flatnonzero(stepped - held > MOVE_C)
    return float(moved[0]) if len(moved) else float("nan")


# ------------------------------------------------------------------ the plants


def _auger(q, t):
    """The auger's on/off state under demand `q` at plant time `t`.

    Q reaches the plant the way it reaches the grill: through the combustion
    allocator's affine Q -> duty map and the 20 s auger cycle, so the pulse
    structure a synthetic log carries is the one a real capture has.
    """
    frac = min(max((q - Q_MIN) / (Q_MAX - Q_MIN), 0.0), 1.0)
    ratio = U_MIN + frac * (U_MAX - U_MIN)
    return 1.0 if (t % CYCLE_S) < ratio * CYCLE_S else 0.0


def plant_coast(make_plant, *, t_ref_c=T_REF_C, seeds=(0, 1, 2), warm_cap=20000, span=3000):
    """Seconds the plant's chamber keeps rising after its fuel is cut."""
    out = []
    for seed in seeds:
        plant = make_plant(seed)
        for _ in range(warm_cap):
            plant.step(auger_on=_auger(Q_MAX, plant.t), fan_frac=1.0)
            if plant.true_Tc >= t_ref_c:
                break
        temps = [plant.step(auger_on=0.0, fan_frac=1.0) for _ in range(span)]
        out.append(float(np.argmax(temps)))
    return float(np.median(out))


def plant_dead_time(make_plant, *, t_ref_c=T_REF_C, seeds=(0, 1, 2), warm_cap=20000, span=1500):
    """Seconds before the plant's chamber notices a step up in firing demand.

    Two copies of the plant from one seed draw their noise and their wind in
    lockstep, so differencing a stepped copy against a held one leaves the
    input change and nothing else -- no warm-up drift, no combustion noise.
    """
    out = []
    for seed in seeds:
        held, stepped = make_plant(seed), make_plant(seed)
        for _ in range(warm_cap):
            a = _auger(Q_LO, held.t)
            held.step(auger_on=a, fan_frac=1.0)
            stepped.step(auger_on=a, fan_frac=1.0)
            if held.true_Tc >= t_ref_c:
                break
        diff = []
        for _ in range(span):
            h = held.step(auger_on=_auger(Q_LO, held.t), fan_frac=1.0)
            s = stepped.step(auger_on=_auger(Q_HI, stepped.t), fan_frac=1.0)
            diff.append(s - h)
        moved = np.flatnonzero(np.array(diff) > MOVE_C)
        out.append(float(moved[0]) if len(moved) else float("nan"))
    return float(np.median(out))


# ------------------------------------------------------------- synthetic cooks


class _PI:
    """A plain PI loop, only to shape the firing demand a synthetic log carries.

    A calibration log records a driven grill, not a controller, so nothing
    here is under test: the loop exists because a constant open-loop demand
    cannot produce a hold or a setpoint step to fit against.
    """

    def __init__(self, kp=3.0, ki=0.02):
        self.kp, self.ki, self.i = kp, ki, 0.0

    def __call__(self, sp, meas):
        err = sp - meas
        self.i = float(np.clip(self.i + err, -3000.0, 3000.0))
        return float(np.clip(self.kp * err + self.ki * self.i, 0.0, Q_MAX))


SCENARIOS = {
    "heatup": [(T_REF_C, 2000)],
    "step": [(148.9, 1500), (T_REF_C, 1500)],
    "hold": [(176.7, 4500)],
}


def synth_log(make_plant, scenario, *, seed=0):
    """(t, temp_c, Q) for one synthetic cook, at the real fixture's cadence."""
    plant = make_plant(seed)
    pi = _PI()
    qs, temps = [], []
    for sp, dur in SCENARIOS[scenario]:
        for _ in range(int(dur)):
            q = pi(sp, plant.measured())
            plant.step(auger_on=_auger(q, plant.t), fan_frac=1.0)
            qs.append(q)
            temps.append(plant.measured())
    step = int(LOG_DT / DT)
    return np.arange(0.0, len(qs), DT)[::step], np.array(temps)[::step], np.array(qs)[::step]


# ------------------------------------------------------------------- the sweep


def _init_point(free, log_name, restart):
    init = {k: DEFAULTS[k] for k in ("C_f", "C_c", "h_fc", "h_amb", "K_Q", "theta", "sigma")}
    if restart == 0:
        return init
    key = f"{'+'.join(free)}|{log_name}|{restart}".encode()
    rng = np.random.default_rng(zlib.crc32(key))
    return {k: v * float(np.exp(rng.normal(0.0, PERTURB))) for k, v in init.items()}


def _in_bounds(p):
    """Whether a fit landed somewhere model_promotion.evaluate would accept.

    A fit whose parameters leave PROMOTION_BOUNDS is refused outright, so a
    free set that keeps walking out of them cannot promote a model however
    well it describes the log.
    """
    from controller.model_promotion import PROMOTION_BOUNDS

    return all(lo <= float(p[k]) <= hi for k, (lo, hi) in PROMOTION_BOUNDS.items() if k in p)


def _one_fit(job):
    free, log_name, t, temp, Q, T_amb, restart = job
    import controller.update_mpc as um

    um._FREE = free
    init = _init_point(free, log_name, restart)
    fitted = fit_params(t, temp, Q, T_amb=T_amb, init=init, sigma=init["sigma"], n_delay=DEFAULTS["n_delay"])
    rmse, _ = fit_quality(t, temp, Q, fitted, T_amb=T_amb)
    p = {k: float(fitted[k]) for k in ("C_f", "C_c", "h_fc", "h_amb", "K_Q", "sigma", "theta")}
    p["n_delay"] = int(fitted["n_delay"])
    p["T_amb"] = float(T_amb)
    return dict(
        free="+".join(free),
        log=log_name,
        restart=restart,
        rmse=rmse,
        converged=bool(fitted["converged"]),
        in_bounds=_in_bounds(p),
        C_c=p["C_c"],
        h_amb=p["h_amb"],
        K_Q=p["K_Q"],
        h_fc=p["h_fc"],
        sigma=p["sigma"],
        tau=p["C_c"] / p["h_amb"],
        theta=p["theta"],
        dead=model_dead_time(p),
        coast=model_coast(p),
    )


def main():
    import pandas as pd

    print("integrate vs simulate_grey_box, max |diff| C:", f"{same_as_simulate_grey_box(DEFAULTS):.3e}")

    logs = []
    df = pd.read_csv("tests/unit/mpc/fixtures/mak_cook_2026-08-02.csv")
    logs.append(("real_mak", df.time_s.values - df.time_s.values[0], df.temp_c.values, df.Q.values, 20.0))

    plants = {"grillsim": lambda s: GrillSim(seed=s), "mak": lambda s: MAKGrillSim(seed=s)}
    print("\n=== plant truth, measured on the plant itself ===")
    truth = {}
    for pname, mk in plants.items():
        truth[pname] = dict(dead=plant_dead_time(mk), coast=plant_coast(mk))
        print(f"{pname:9s} dead={truth[pname]['dead']:.0f} s  coast={truth[pname]['coast']:.0f} s")
        for scen in SCENARIOS:
            t, temp, Q = synth_log(mk, scen)
            logs.append((f"{pname}_{scen}", t, temp, Q, float(mk(0).T_amb)))
    pd.DataFrame(truth).T.to_csv("docs/superpowers/experiments/_free_set_truth.csv")

    jobs = [
        (free, name, t, temp, Q, T_amb, r)
        for (name, t, temp, Q, T_amb), free, r in itertools.product(logs, CANDIDATES, range(RESTARTS))
    ]
    workers = max(1, (os.cpu_count() or 4) - 2)
    print(f"\n{len(jobs)} fits on {workers} workers")
    with ProcessPoolExecutor(max_workers=workers) as ex:
        rows = list(ex.map(_one_fit, jobs, chunksize=1))

    pd.DataFrame(rows).to_csv("docs/superpowers/experiments/_free_set_sweep.csv", index=False)
    print("-> docs/superpowers/experiments/_free_set_sweep.csv")


if __name__ == "__main__":
    main()
