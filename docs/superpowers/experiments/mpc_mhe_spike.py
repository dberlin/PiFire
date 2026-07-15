#!/usr/bin/env python3
"""
Spike: do-mpc MHE as the estimator instead of the hand-rolled Kalman filter.
Reuses the validated cascade plant + MPC from mpc_cascade_spike, swaps the
estimator, and compares steady-state band + per-step timing.
"""

import warnings, sys, time

warnings.filterwarnings("ignore")
sys.path.insert(0, "docs/superpowers/experiments")
import numpy as np
import do_mpc
import mpc_cascade_spike as S

np.random.seed(0)


# --- MHE model: same grey-box, but with process + measurement noise declared ---
def build_mhe():
    m = do_mpc.model.Model("continuous")
    T_f = m.set_variable("_x", "T_f")
    T_c = m.set_variable("_x", "T_c")
    d = m.set_variable("_x", "d")
    Q = m.set_variable("_u", "Q")
    m.set_rhs("T_f", (Q - S.h_fc * (T_f - T_c)) / S.C_f, process_noise=True)
    m.set_rhs("T_c", (S.h_fc * (T_f - T_c) - S.h_amb * (T_c - S.T_AMB_NOM) + d) / S.C_c, process_noise=True)
    m.set_rhs("d", d * 0, process_noise=True)
    m.set_meas("T_c_meas", T_c, meas_noise=True)
    m.setup()

    mhe = do_mpc.estimator.MHE(m, [])
    mhe.set_param(
        n_horizon=10,
        t_step=S.Ts,
        store_full_solution=False,
        meas_from_data=True,
        nlpsol_opts={"ipopt.print_level": 0, "print_time": 0, "ipopt.sb": "yes"},
    )
    # arrival cost P_x, measurement-noise P_v, process-noise P_w
    P_x = np.diag([1.0, 1.0, 0.01])  # loose arrival cost on disturbance
    P_v = np.array([[1.0 / 0.04]])  # 1/meas_var
    P_w = np.diag([1.0, 1.0, 0.01])  # very low penalty on d's process noise
    # => d free to track the disturbance (offset-free)
    mhe.set_default_objective(P_x, P_v, P_w=P_w)
    mhe.setup()
    return mhe, m


def run_mhe():
    mpc, _ = S.build_mpc(direct=False)
    sim = S.build_truth()
    mhe, _ = build_mhe()

    sim.x0 = np.array([[20.0], [20.0]])
    sim.set_initial_guess()
    x0 = np.array([[20.0], [20.0], [0.0]])
    mhe.x0 = x0
    mhe.set_initial_guess()
    mpc.x0 = x0
    mpc.set_initial_guess()

    log = {k: [] for k in ["t", "Tc", "sp", "afr"]}
    solve_ms = []
    Qprev = S.Q_MIN
    for k in range(288):
        t = k * S.Ts
        y = float(sim.x0["T_c"]) + np.random.normal(0, 0.2)
        # MHE needs to know the applied input; feed it before make_step
        mhe.u0 = np.array([[Qprev]])
        t0 = time.perf_counter()
        xhat = np.asarray(mhe.make_step(np.array([[y]]))).flatten()
        t1 = time.perf_counter()
        u = np.asarray(mpc.make_step(xhat.reshape(-1, 1))).flatten()
        t2 = time.perf_counter()
        Qprev = float(np.clip(u[0], S.Q_MIN, S.Q_MAX))
        au, fa = S.allocator(Qprev)
        Qh, afr, eff = S.combustion_heat(au, fa)
        sim.make_step(np.array([[Qh]]))
        sp = 107.0 if t < 3600 else 121.0
        log["t"].append(t)
        log["Tc"].append(float(sim.x0["T_c"]))
        log["sp"].append(sp)
        log["afr"].append(afr)
        log.setdefault("dhat", []).append(xhat[2])
        log.setdefault("Tc_hat", []).append(xhat[1])
        solve_ms.append({"mhe": (t1 - t0) * 1e3, "mpc": (t2 - t1) * 1e3})
    return {k: np.array(v) for k, v in log.items()}, solve_ms


if __name__ == "__main__":
    print("Running MHE-estimator closed loop ...")
    L, ms = run_mhe()
    t, Tc, sp = L["t"], L["Tc"], L["sp"]
    err = Tc - sp
    m1 = (t >= 1500) & (t < 2900)
    m2 = (t >= 4600) & (t < 7200)
    sm = m1 | m2
    print(f"STEADY band |error| max : {np.max(np.abs(err[sm])):.2f} C")
    print(f"STEADY RMS / mean bias  : {np.sqrt(np.mean(err[sm] ** 2)):.2f} C / {np.mean(err[sm]):+.2f} C")
    print(f"within +-1.0C fraction  : {100 * np.mean(np.abs(err[sm]) <= 1.0):.1f} %")
    print(f"AFR range (steady)      : {L['afr'][sm].min():.2f} .. {L['afr'][sm].max():.2f}")
    print(
        f"d-hat range (steady)    : {L['dhat'][sm].min():+.2f} .. {L['dhat'][sm].max():+.2f}   (KF used d to cancel bias)"
    )
    print(f"Tc-hat vs true (steady) : est-true mean = {np.mean(L['Tc_hat'][sm] - L['Tc'][sm]):+.2f} C")
    mhe_t = np.array([d["mhe"] for d in ms])
    mpc_t = np.array([d["mpc"] for d in ms])
    warm = slice(11, None)  # skip horizon-fill warmup
    print(
        f"MHE solve warm  : mean={mhe_t[warm].mean():.1f} median={np.median(mhe_t[warm]):.1f} p95={np.percentile(mhe_t[warm], 95):.1f} max={mhe_t[warm].max():.1f} ms"
    )
    print(f"MPC solve warm  : mean={mpc_t[warm].mean():.1f} ms")
    print(f"TOTAL warm/step : {(mhe_t[warm].mean() + mpc_t[warm].mean()):.1f} ms")
