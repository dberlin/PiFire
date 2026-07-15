#!/usr/bin/env python3
"""
Re-test KF vs do-mpc MHE, now on the AUGMENTED deadtime model (transport-lag
states), against the realistic plant (deadtime=20, sensor lag 4.5).

The augmented model is still LINEAR, so the expectation is KF == MHE. This
confirms it empirically. KF = the production controller. MHE = a parallel
controller with a do-mpc MHE over the same augmented model (input Q modeled as
a KNOWN tvp so the disturbance state stays offset-free).
"""

import warnings, sys, time

warnings.filterwarnings("ignore")
sys.path.insert(0, "docs/superpowers/experiments")
sys.path.insert(0, ".")
import numpy as np
import do_mpc
from collections import deque
from mpc_hifi_sim import HiFiGrill, SETPOINT
from controller.mpc import Controller, _DEFAULTS

THETA, NDELAY, TSTEP, NH = 50.0, 4, 25.0, 20
CP = 25.0
Cf, Cc, hfc, hamb, Tamb = 60.0, 306.0, 2.0, 0.55, 20.0
QMIN, QMAX, UMIN, UMAX = 5.0, 100.0, 0.1, 0.9


def alloc(Q):
    f = (Q - QMIN) / (QMAX - QMIN)
    return UMIN + f * (UMAX - UMIN), 40.0 + f * 60.0


class MHEController:
    """MPC over the augmented deadtime model, estimated with a do-mpc MHE."""

    def __init__(self):
        self._set_c = SETPOINT
        self._last_Q = QMIN
        self.n = NDELAY + 3
        tau = THETA / NDELAY

        # ---- MPC model (Q is a control input) ----
        m = do_mpc.model.Model("continuous")
        q = [m.set_variable("_x", f"q{i}") for i in range(NDELAY)]
        Tf = m.set_variable("_x", "T_f")
        Tc = m.set_variable("_x", "T_c")
        d = m.set_variable("_x", "d")
        Q = m.set_variable("_u", "Q")
        Tset = m.set_variable("_tvp", "T_set")
        m.set_rhs("q0", (Q - q[0]) / tau)
        for i in range(1, NDELAY):
            m.set_rhs(f"q{i}", (q[i - 1] - q[i]) / tau)
        m.set_rhs("T_f", (q[NDELAY - 1] - hfc * (Tf - Tc)) / Cf)
        m.set_rhs("T_c", (hfc * (Tf - Tc) - hamb * (Tc - Tamb) + d) / Cc)
        m.set_rhs("d", d * 0)
        m.setup()
        mpc = do_mpc.controller.MPC(m)
        mpc.set_param(
            n_horizon=NH,
            t_step=TSTEP,
            store_full_solution=False,
            nlpsol_opts={"ipopt.print_level": 0, "print_time": 0, "ipopt.sb": "yes"},
        )
        mpc.set_objective(mterm=(Tc - Tset) ** 2, lterm=(Tc - Tset) ** 2)
        mpc.set_rterm(Q=0.02)
        mpc.bounds["lower", "_u", "Q"] = QMIN
        mpc.bounds["upper", "_u", "Q"] = QMAX
        tvp = mpc.get_tvp_template()
        mpc.set_tvp_fun(lambda t: self._fill(tvp))
        mpc.setup()
        x0 = np.zeros((self.n, 1))
        x0[NDELAY] = Tamb
        x0[NDELAY + 1] = Tamb
        mpc.x0 = x0
        mpc.set_initial_guess()
        self.mpc = mpc

        # ---- MHE model (Q is a KNOWN tvp -> disturbance must absorb mismatch) ----
        me = do_mpc.model.Model("continuous")
        q2 = [me.set_variable("_x", f"q{i}") for i in range(NDELAY)]
        Tf2 = me.set_variable("_x", "T_f")
        Tc2 = me.set_variable("_x", "T_c")
        d2 = me.set_variable("_x", "d")
        Qa = me.set_variable("_tvp", "Q_app")
        me.set_rhs("q0", (Qa - q2[0]) / tau, process_noise=True)
        for i in range(1, NDELAY):
            me.set_rhs(f"q{i}", (q2[i - 1] - q2[i]) / tau, process_noise=True)
        me.set_rhs("T_f", (q2[NDELAY - 1] - hfc * (Tf2 - Tc2)) / Cf, process_noise=True)
        me.set_rhs("T_c", (hfc * (Tf2 - Tc2) - hamb * (Tc2 - Tamb) + d2) / Cc, process_noise=True)
        me.set_rhs("d", d2 * 0, process_noise=True)
        me.set_meas("T_c_meas", Tc2, meas_noise=True)
        me.setup()
        mhe = do_mpc.estimator.MHE(me, [])
        mhe.set_param(
            n_horizon=10,
            t_step=CP,
            store_full_solution=False,
            meas_from_data=True,
            nlpsol_opts={"ipopt.print_level": 0, "print_time": 0, "ipopt.sb": "yes"},
        )
        P_x = np.diag([1.0] * (NDELAY + 2) + [0.01])
        P_w = np.diag([10.0] * (NDELAY + 2) + [0.05])
        mhe.set_default_objective(P_x, np.array([[1.0 / 0.04]]), P_w=P_w)
        self.qhist = deque([QMIN] * 11, maxlen=11)
        tvp2 = mhe.get_tvp_template()
        mhe.set_tvp_fun(lambda t: self._fillq(tvp2))
        mhe.setup()
        x0m = np.zeros((self.n, 1))
        x0m[NDELAY] = Tamb
        x0m[NDELAY + 1] = Tamb
        mhe.x0 = x0m
        mhe.set_initial_guess()
        self.mhe = mhe

    def _fill(self, tvp):
        for k in range(NH + 1):
            tvp["_tvp", k, "T_set"] = self._set_c
        return tvp

    def _fillq(self, tvp):
        for k in range(11):
            tvp["_tvp", k, "Q_app"] = self.qhist[k]
        return tvp

    def update(self, y):
        self.qhist.append(self._last_Q)
        xhat = np.asarray(self.mhe.make_step(np.array([[y]]))).flatten()
        try:
            Q = float(np.asarray(self.mpc.make_step(xhat.reshape(-1, 1))).flatten()[0])
        except Exception:
            Q = self._last_Q
        Q = float(np.clip(Q, QMIN, QMAX))
        self._last_Q = Q
        return alloc(Q)


def run_kf(deadtime, seed=0, n_minutes=120):
    cfg = dict(_DEFAULTS)
    cfg.update(enable_fan_input=True)
    c = Controller(cfg, "C", {"u_min": 0.1, "u_max": 0.9, "HoldCycleTime": 25})
    c.set_target(SETPOINT)
    return _drive(lambda y: (lambda o: (o["cycle_ratio"], o["fan"]["duty"]))(c.update(y)), deadtime, seed, n_minutes)


def run_mhe(deadtime, seed=0, n_minutes=120):
    c = MHEController()
    return _drive(c.update, deadtime, seed, n_minutes)


def _drive(update_fn, deadtime, seed, n_minutes):
    plant = HiFiGrill(seed=seed, fan_is_lever=True, fixed_fan=None, deadtime=deadtime)
    temps, times, solve = [], [], []
    for w in range(int(n_minutes * 60 / TSTEP)):
        t0 = time.perf_counter()
        ratio, fan = update_fn(plant.measured())
        solve.append((time.perf_counter() - t0) * 1e3)
        on = int(round(ratio * TSTEP))
        for s in range(int(TSTEP)):
            plant.step(auger_on=(s < on), fan_frac=fan / 100.0)
            temps.append(plant.T_c)
            times.append(w * TSTEP + s)
    return np.array(times), np.array(temps), np.array(solve)


def report(name, t, T, ms):
    sm = t >= 1800
    e = T[sm] - SETPOINT
    print(
        f"  {name:14s} RMS={np.sqrt(np.mean(e**2)):4.2f}C  max={np.max(np.abs(e)):5.2f}C  "
        f"bias={np.mean(e):+5.2f}C  <3C={100 * np.mean(np.abs(e) <= 3):4.1f}%  "
        f"solve={np.median(ms[2:]):4.1f}ms"
    )


if __name__ == "__main__":
    print("KF vs MHE on the augmented deadtime model (plant deadtime=20, sensor 4.5):")
    report("Kalman (prod)", *run_kf(20))
    report("do-mpc MHE", *run_mhe(20))
