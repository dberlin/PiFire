#!/usr/bin/env python3
"""
Fix the high-temp undershoot caused by fan-slaving. Three strategies, on the
realistic plant (deadtime=20, fan lever, wind, sensor lag 4.5):

  A) BASELINE: production linear deadtime model + KF, cascade fan (fan tracks Q).
  B) FAN-LOSS TERM: model's chamber loss h_amb depends on Q through the fan map
     (so the MPC knows firing harder raises loss) + radiative T^4, MHE, cascade.
  C) DECOUPLED FAN: production linear + KF, but the fan is held FIXED (not slaved
     to fuel), so loss does not climb with firing.

Reports the steady 110C hold and the 95->130C step settling for each.
"""

import warnings, sys

warnings.filterwarnings("ignore")
sys.path.insert(0, "docs/superpowers/experiments")
sys.path.insert(0, ".")
import numpy as np
import do_mpc
from collections import deque
from controller.grill_sim import GrillSim
from controller.mpc import Controller, _DEFAULTS

THETA, ND, TSTEP, NH, CP = 50.0, 4, 25.0, 20, 25.0
Cf, Cc, hfc, Tamb = 60.0, 306.0, 2.0, 20.0
QMIN, QMAX, UMIN, UMAX = 5.0, 100.0, 0.1, 0.9
K = 273.15
FIXED_FAN_PCT = 100.0


def alloc(Q):
    f = (Q - QMIN) / (QMAX - QMIN)
    return UMIN + f * (UMAX - UMIN), 40.0 + f * 60.0


class FanLossMHE:
    """Deadtime + radiative + FAN-LOSS (h_amb grows with Q via fan map), MHE."""

    def __init__(self, h_amb=0.42, sigma=1.4e-9):
        self.sp = 110.0
        self._last_Q = QMIN
        self.n = ND + 3
        tau = THETA / ND

        def rad(Tc):
            return sigma * ((Tc + K) ** 4 - (Tamb + K) ** 4)

        def hamb_of(Q):
            frac = (Q - QMIN) / (QMAX - QMIN)  # fan tracks Q (allocator)
            fan_frac = 0.4 + 0.6 * frac  # -> plant fan fraction
            return h_amb * (0.8 + 0.5 * fan_frac)

        def chamber_rhs(Q, Tf, Tc, d):
            return (hfc * (Tf - Tc) - hamb_of(Q) * (Tc - Tamb) - rad(Tc) + d) / Cc

        # MPC model
        m = do_mpc.model.Model("continuous")
        q = [m.set_variable("_x", f"q{i}") for i in range(ND)]
        Tf = m.set_variable("_x", "T_f")
        Tc = m.set_variable("_x", "T_c")
        d = m.set_variable("_x", "d")
        Q = m.set_variable("_u", "Q")
        Ts = m.set_variable("_tvp", "T_set")
        m.set_rhs("q0", (Q - q[0]) / tau)
        for i in range(1, ND):
            m.set_rhs(f"q{i}", (q[i - 1] - q[i]) / tau)
        m.set_rhs("T_f", (q[ND - 1] - hfc * (Tf - Tc)) / Cf)
        m.set_rhs("T_c", chamber_rhs(Q, Tf, Tc, d))
        m.set_rhs("d", d * 0)
        m.setup()
        mpc = do_mpc.controller.MPC(m)
        mpc.set_param(
            n_horizon=NH,
            t_step=TSTEP,
            store_full_solution=False,
            nlpsol_opts={"ipopt.print_level": 0, "print_time": 0, "ipopt.sb": "yes"},
        )
        mpc.set_objective(mterm=(Tc - Ts) ** 2, lterm=(Tc - Ts) ** 2)
        mpc.set_rterm(Q=0.02)
        mpc.bounds["lower", "_u", "Q"] = QMIN
        mpc.bounds["upper", "_u", "Q"] = QMAX
        tv = mpc.get_tvp_template()
        mpc.set_tvp_fun(lambda t: self._sp(tv))
        mpc.setup()
        x0 = np.zeros((self.n, 1))
        x0[ND] = Tamb
        x0[ND + 1] = Tamb
        mpc.x0 = x0
        mpc.set_initial_guess()
        self.mpc = mpc

        # MHE model (Q known tvp)
        me = do_mpc.model.Model("continuous")
        q2 = [me.set_variable("_x", f"q{i}") for i in range(ND)]
        Tf2 = me.set_variable("_x", "T_f")
        Tc2 = me.set_variable("_x", "T_c")
        d2 = me.set_variable("_x", "d")
        Qa = me.set_variable("_tvp", "Q_app")
        me.set_rhs("q0", (Qa - q2[0]) / tau, process_noise=True)
        for i in range(1, ND):
            me.set_rhs(f"q{i}", (q2[i - 1] - q2[i]) / tau, process_noise=True)
        me.set_rhs("T_f", (q2[ND - 1] - hfc * (Tf2 - Tc2)) / Cf, process_noise=True)
        me.set_rhs("T_c", chamber_rhs(Qa, Tf2, Tc2, d2), process_noise=True)
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
        mhe.set_default_objective(
            np.diag([1.0] * (ND + 2) + [0.01]), np.array([[1.0 / 0.04]]), P_w=np.diag([10.0] * (ND + 2) + [0.05])
        )
        self.qh = deque([QMIN] * 11, maxlen=11)
        tv2 = mhe.get_tvp_template()
        mhe.set_tvp_fun(lambda t: self._q(tv2))
        mhe.setup()
        x0m = np.zeros((self.n, 1))
        x0m[ND] = Tamb
        x0m[ND + 1] = Tamb
        mhe.x0 = x0m
        mhe.set_initial_guess()
        self.mhe = mhe

    def _sp(self, tv):
        for k in range(NH + 1):
            tv["_tvp", k, "T_set"] = self.sp
        return tv

    def _q(self, tv):
        for k in range(11):
            tv["_tvp", k, "Q_app"] = self.qh[k]
        return tv

    def set_target(self, sp):
        self.sp = sp

    def update(self, y):
        self.qh.append(self._last_Q)
        xh = np.asarray(self.mhe.make_step(np.array([[y]]))).flatten()
        try:
            Q = float(np.asarray(self.mpc.make_step(xh.reshape(-1, 1))).flatten()[0])
        except Exception:
            Q = self._last_Q
        Q = float(np.clip(Q, QMIN, QMAX))
        self._last_Q = Q
        return alloc(Q)


class ProdWrap:
    def __init__(self, sp=110.0, enable_fan=True, **over):
        cfg = dict(_DEFAULTS)
        cfg.update(enable_fan_input=enable_fan)
        cfg.update(over)
        self.c = Controller(cfg, "C", {"u_min": 0.1, "u_max": 0.9, "HoldCycleTime": 25})
        self.c.set_target(sp)

    def set_target(self, sp):
        self.c.set_target(sp)

    def update(self, y):
        o = self.c.update(y)
        return o["cycle_ratio"], o["fan"]["duty"]


def drive(ctrl, fixed_fan=None, deadtime=20, seed=0, minutes=180, step_at=None, sp2=None):
    plant = GrillSim(seed=seed, fan_is_lever=True, fixed_fan=None, deadtime=deadtime)
    ts, T = [], []
    for w in range(int(minutes * 60 / TSTEP)):
        t = w * TSTEP
        if step_at is not None and t >= step_at:
            ctrl.set_target(sp2)
        ratio, fan = ctrl.update(plant.measured())
        if fixed_fan is not None or fan is None:
            fan = fixed_fan if fixed_fan is not None else FIXED_FAN_PCT
        on = int(round(ratio * TSTEP))
        for s in range(int(TSTEP)):
            plant.step(auger_on=(s < on), fan_frac=fan / 100.0)
            ts.append(t + s)
            T.append(plant.true_Tc)
    return np.array(ts), np.array(T)


def steady(name, ts, T, sp=110.0):
    sm = ts >= 1800
    e = T[sm] - sp
    print(f"  {name:22s} RMS={np.sqrt(np.mean(e**2)):4.2f}  max={np.max(np.abs(e)):5.2f}  bias={np.mean(e):+5.2f}")


def settle(name, ts, T, step_t=3600.0, sp=130.0, band=2.0):
    post = ts >= step_t
    tt = ts[post] - step_t
    Tp = T[post]
    e = np.abs(Tp - sp)

    def at(m):
        return Tp[min(np.searchsorted(tt, m * 60), len(Tp) - 1)]

    st = next((tt[i] / 60.0 for i in range(len(tt)) if e[i] <= band and np.all(e[i:] <= band + 2)), None)
    tag = f"settled +-{band}C at +{st:.0f}min" if st is not None else f"NEVER (final {Tp[-1]:.1f}C)"
    print(f"  {name:22s} +5/+15/+30/+60min={at(5):.1f}/{at(15):.1f}/{at(30):.1f}/{at(60):.1f}C  {tag}")


if __name__ == "__main__":
    print("Steady hold at 110C:")
    steady("A) baseline cascade", *drive(ProdWrap(enable_fan=True), minutes=120))
    steady("B) fan-loss + MHE", *drive(FanLossMHE(), minutes=120))
    steady("C) decoupled fan", *drive(ProdWrap(enable_fan=False), fixed_fan=FIXED_FAN_PCT, minutes=120))

    print("\nStep 95 -> 130C at +60min:")
    settle("A) baseline cascade", *drive(ProdWrap(95.0, enable_fan=True), step_at=3600, sp2=130.0))
    settle("B) fan-loss + MHE", *drive(FanLossMHE(), step_at=3600, sp2=130.0))
    settle(
        "C) decoupled fan", *drive(ProdWrap(95.0, enable_fan=False), fixed_fan=FIXED_FAN_PCT, step_at=3600, sp2=130.0)
    )

    print("\nIs it the disturbance estimator? baseline cascade, slower d (est_q_dist):")
    for qd in (0.5, 0.1, 0.02):
        settle(f"  est_q_dist={qd}", *drive(ProdWrap(95.0, enable_fan=True, est_q_dist=qd), step_at=3600, sp2=130.0))
