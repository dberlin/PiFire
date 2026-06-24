#!/usr/bin/env python3
"""
Spike: add DEADTIME to the controller model (transport-lag states) and run the
KF/MPC at a faster, correct cadence (t_step = control_period). Re-test on the
higher-fidelity plant from mpc_hifi_sim.

Model: Q -> [M cascaded first-order lags, total mean delay ~theta] -> firepot.
This is a continuous distributed-delay (Erlang) approximation of the
feed+ignition transport lag -- do-mpc friendly, and more physical than a pure
delay. States: [q1..qM, T_f, T_c, d].

Compares the production-style model (no deadtime, t_step=25) against the
augmented deadtime model at a few control periods.
"""
import warnings, sys
warnings.filterwarnings("ignore")
sys.path.insert(0, 'docs/superpowers/experiments')
sys.path.insert(0, '.')
import numpy as np
from scipy.linalg import expm
import do_mpc
from mpc_hifi_sim import HiFiGrill, SETPOINT

U_MIN, U_MAX = 0.10, 0.90
Q_MIN, Q_MAX = 5.0, 100.0
HOLD = 25.0


class DeadtimeMPC:
    """Augmented grey-box MPC + Kalman filter with transport-lag deadtime."""

    def __init__(self, *, theta=50.0, M=4, t_step=5.0, n_horizon=40,
                 C_f=60.0, C_c=306.0, h_fc=2.0, h_amb=0.55, T_amb=20.0,
                 q_proc=1e-2, q_dist=0.5, r_meas=0.04):
        self.M = M
        self.t_step = t_step
        tau_d = theta / M if M > 0 else 1.0
        n = M + 3                                # [q1..qM, T_f, T_c, d]
        self._last_Q = Q_MIN
        self._set_c = 0.0

        # ---- do-mpc model ----
        model = do_mpc.model.Model('continuous')
        q = [model.set_variable('_x', f'q{i}') for i in range(M)]
        T_f = model.set_variable('_x', 'T_f')
        T_c = model.set_variable('_x', 'T_c')
        d = model.set_variable('_x', 'd')
        Q = model.set_variable('_u', 'Q')
        T_set = model.set_variable('_tvp', 'T_set')
        if M > 0:
            model.set_rhs('q0', (Q - q[0]) / tau_d)
            for i in range(1, M):
                model.set_rhs(f'q{i}', (q[i - 1] - q[i]) / tau_d)
            heat_in = q[M - 1]
        else:
            heat_in = Q
        model.set_rhs('T_f', (heat_in - h_fc * (T_f - T_c)) / C_f)
        model.set_rhs('T_c', (h_fc * (T_f - T_c) - h_amb * (T_c - T_amb) + d) / C_c)
        model.set_rhs('d', d * 0)
        model.setup()
        self.model = model

        mpc = do_mpc.controller.MPC(model)
        mpc.set_param(n_horizon=n_horizon, t_step=t_step, store_full_solution=False,
                      nlpsol_opts={'ipopt.print_level': 0, 'print_time': 0, 'ipopt.sb': 'yes'})
        mpc.set_objective(mterm=(T_c - T_set) ** 2, lterm=(T_c - T_set) ** 2)
        mpc.set_rterm(Q=0.02)
        mpc.bounds['lower', '_u', 'Q'] = Q_MIN
        mpc.bounds['upper', '_u', 'Q'] = Q_MAX
        tvp = mpc.get_tvp_template()
        def tvp_fun(t_now):
            for k in range(n_horizon + 1):
                tvp['_tvp', k, 'T_set'] = self._set_c
            return tvp
        mpc.set_tvp_fun(tvp_fun)
        mpc.setup()
        self.mpc = mpc

        # ---- Kalman filter on the augmented linear model (discretized at t_step) ----
        A = np.zeros((n, n))
        for i in range(M):
            A[i, i] = -1.0 / tau_d
            if i > 0:
                A[i, i - 1] = 1.0 / tau_d
        iTf, iTc, iD = M, M + 1, M + 2
        if M > 0:
            A[iTf, M - 1] = 1.0 / C_f
        A[iTf, iTf] = -h_fc / C_f
        A[iTf, iTc] = h_fc / C_f
        A[iTc, iTf] = h_fc / C_c
        A[iTc, iTc] = -(h_fc + h_amb) / C_c
        A[iTc, iD] = 1.0 / C_c
        Baug = np.zeros((n, 2))
        if M > 0:
            Baug[0, 0] = 1.0 / tau_d             # Q enters first transport lag
        else:
            Baug[iTf, 0] = 1.0 / C_f             # no deadtime: Q enters firepot
        Baug[iTc, 1] = h_amb * T_amb / C_c       # affine ambient (const input = 1)
        Mblk = np.zeros((n + 2, n + 2))
        Mblk[:n, :n] = A
        Mblk[:n, n:] = Baug
        Md = expm(Mblk * t_step)
        self.Ad = Md[:n, :n]
        self.Bd = Md[:n, n:n + 1]
        self.bd = Md[:n, n + 1:n + 2]
        self.H = np.zeros((1, n)); self.H[0, iTc] = 1.0
        self.Qkf = np.diag([q_proc] * (M + 2) + [q_dist])
        self.Rkf = np.array([[r_meas]])
        self.x = np.zeros(n); self.x[iTf] = T_amb; self.x[iTc] = T_amb
        self.P = np.eye(n) * 5.0
        self.n = n

    def set_target(self, sp_c):
        self._set_c = sp_c

    def update(self, y):
        # KF predict+update
        self.x = self.Ad @ self.x + self.Bd.flatten() * self._last_Q + self.bd.flatten()
        self.P = self.Ad @ self.P @ self.Ad.T + self.Qkf
        S = self.H @ self.P @ self.H.T + self.Rkf
        K = (self.P @ self.H.T) / S
        self.x = self.x + K.flatten() * (y - (self.H @ self.x)[0])
        self.P = (np.eye(self.n) - K @ self.H) @ self.P
        # optimize
        try:
            Q = float(np.asarray(self.mpc.make_step(self.x.reshape(-1, 1))).flatten()[0])
        except Exception:
            Q = self._last_Q
        Q = float(np.clip(Q, Q_MIN, Q_MAX))
        self._last_Q = Q
        # allocate firing rate -> auger cycle ratio
        frac = (Q - Q_MIN) / (Q_MAX - Q_MIN)
        ratio = U_MIN + frac * (U_MAX - U_MIN)
        fan = 40.0 + frac * 60.0
        return ratio, fan


def run(ctrl, deadtime=40, n_minutes=120, seed=0):
    plant = HiFiGrill(seed=seed, fan_is_lever=True, fixed_fan=0.65, deadtime=deadtime)
    n_windows = int(n_minutes * 60 / ctrl.t_step)
    temps, times = [], []
    for w in range(n_windows):
        ratio, fan = ctrl.update(plant.measured())
        on_secs = int(round(ratio * ctrl.t_step))
        for s in range(int(ctrl.t_step)):
            plant.step(auger_on=(s < on_secs), fan_frac=fan / 100.0)
            temps.append(plant.T_c); times.append(w * ctrl.t_step + s)
    return np.array(times), np.array(temps)


def report(name, times, temps):
    sm = times >= 1800
    err = temps[sm] - SETPOINT
    print(f"  {name:42s} maxE={np.max(np.abs(err)):5.2f}  RMS={np.sqrt(np.mean(err**2)):4.2f}  "
          f"bias={np.mean(err):+5.2f}  <1C={100*np.mean(np.abs(err)<=1.0):4.1f}%  "
          f"<3C={100*np.mean(np.abs(err)<=3.0):4.1f}%")


if __name__ == '__main__':
    print("Plant deadtime = 40s. Comparing models/cadences:\n")
    # Baseline: no-deadtime model at t_step=25 (the production config)
    base = DeadtimeMPC(theta=0.0, M=0, t_step=25.0, n_horizon=20)
    base.set_target(SETPOINT)
    report("no-deadtime model, t_step=25 (production)", *run(base))
    # Deadtime model at progressively faster cadence
    for tstep in (25.0, 10.0, 5.0):
        horizon = int(round(220.0 / tstep))
        c = DeadtimeMPC(theta=50.0, M=4, t_step=tstep, n_horizon=horizon)
        c.set_target(SETPOINT)
        report(f"deadtime model theta=50, t_step={tstep:.0f}, N={horizon}", *run(c))

    print("\n  -- better-matched delay (theta~plant, more lags) --")
    for theta, M, tstep in [(40.0, 6, 25.0), (40.0, 8, 15.0), (40.0, 6, 10.0)]:
        horizon = int(round(220.0 / tstep))
        c = DeadtimeMPC(theta=theta, M=M, t_step=tstep, n_horizon=horizon)
        c.set_target(SETPOINT)
        report(f"theta={theta:.0f}, M={M}, t_step={tstep:.0f}, N={horizon}", *run(c))
