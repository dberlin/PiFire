#!/usr/bin/env python3
"""
Feasibility spike: cascade MPC (firing-rate Q -> combustion allocator -> auger/fan)
vs. direct two-input MPC, validated against a deliberately-MISMATCHED nonlinear
grill plant. Answers: does the cascade hold +-1.0 C, and does it keep fuel/air
sensible, while the direct model wanders into bad air-fuel ratios?

Not integrated PiFire code -- a throwaway experiment.
"""
import warnings
warnings.filterwarnings("ignore")
import numpy as np
from scipy.linalg import expm
import do_mpc
from casadi import exp as cexp

np.random.seed(0)

# ---------------- shared constants ----------------
Ts = 25.0                 # control interval (s)
T_END = 7200.0            # 2 h
N = int(T_END / Ts)
Q_MIN, Q_MAX = 5.0, 100.0 # firing-rate demand range
U_MIN, U_MAX = 0.10, 0.90 # auger duty range
F_MIN, F_MAX = 0.10, 0.90 # fan range
AFR_OPT = 1.0             # normalized optimal air/fuel (allocator targets this)
AFR_SIGMA = 0.28          # combustion efficiency falloff width
FUEL_TO_HEAT = Q_MAX / U_MAX  # so auger=U_MAX at optimal AFR -> ~Q_MAX heat

# nominal thermal params (used by MPC model + Kalman filter)
C_f, C_c = 60.0, 306.0
h_fc, h_amb = 2.0, 0.55
T_AMB_NOM = 20.0

# truth plant params (MISMATCHED ~15%)
C_f_t, C_c_t = 70.0, 350.0
h_fc_t, h_amb_t = 1.70, 0.62

# ---------------- combustion allocator (the cascade inner layer) ----------------
def allocator(Q):
    """Map firing-rate demand Q -> (auger, fan) along a sensible AFR curve."""
    frac = (np.clip(Q, Q_MIN, Q_MAX) - Q_MIN) / (Q_MAX - Q_MIN)
    auger = U_MIN + frac * (U_MAX - U_MIN)
    fan = F_MIN + frac * (F_MAX - F_MIN)   # air tracks fuel -> AFR ~ AFR_OPT
    return auger, fan

def combustion_heat(auger, fan):
    """Truth: actual heat release from a fuel/air pair (AFR-dependent efficiency)."""
    fuel = max(auger, 1e-6)
    afr = fan / fuel
    eff = np.exp(-((afr - AFR_OPT) ** 2) / (2 * AFR_SIGMA ** 2))
    return FUEL_TO_HEAT * fuel * eff, afr, eff

# ---------------- MPC model: input Q, states [T_f, T_c, d] ----------------
def build_mpc(direct=False):
    model = do_mpc.model.Model('continuous')
    T_f = model.set_variable('_x', 'T_f')
    T_c = model.set_variable('_x', 'T_c')
    d = model.set_variable('_x', 'd')            # integrating disturbance (offset-free)
    T_set = model.set_variable('_tvp', 'T_set')

    if direct:
        u_a = model.set_variable('_u', 'u_a')    # auger
        u_f = model.set_variable('_u', 'u_f')    # fan
        # direct LINEAR heat model -- no AFR knowledge (this is the point)
        ka = FUEL_TO_HEAT
        kf = 20.0
        Q = ka * u_a + kf * (u_f - 0.5)
    else:
        Q = model.set_variable('_u', 'Q')        # firing-rate demand

    model.set_rhs('T_f', (Q - h_fc * (T_f - T_c)) / C_f)
    model.set_rhs('T_c', (h_fc * (T_f - T_c) - h_amb * (T_c - T_AMB_NOM) + d) / C_c)
    model.set_rhs('d', d * 0)
    model.setup()

    mpc = do_mpc.controller.MPC(model)
    mpc.set_param(n_horizon=20, t_step=Ts, store_full_solution=False,
                  nlpsol_opts={'ipopt.print_level': 0, 'print_time': 0})
    mterm = (T_c - T_set) ** 2
    lterm = (T_c - T_set) ** 2
    mpc.set_objective(mterm=mterm, lterm=lterm)
    if direct:
        mpc.set_rterm(u_a=0.5, u_f=0.5)
        mpc.bounds['lower', '_u', 'u_a'] = U_MIN
        mpc.bounds['upper', '_u', 'u_a'] = U_MAX
        mpc.bounds['lower', '_u', 'u_f'] = F_MIN
        mpc.bounds['upper', '_u', 'u_f'] = F_MAX
    else:
        mpc.set_rterm(Q=0.02)
        mpc.bounds['lower', '_u', 'Q'] = Q_MIN
        mpc.bounds['upper', '_u', 'Q'] = Q_MAX

    tvp_t = mpc.get_tvp_template()
    def tvp_fun(t_now):
        sp = 107.0 if t_now < 3600 else 121.0   # setpoint change at 1 h
        for k in range(21):
            tvp_t['_tvp', k, 'T_set'] = sp
        return tvp_t
    mpc.set_tvp_fun(tvp_fun)
    mpc.setup()
    return mpc, model

# ---------------- Kalman filter on nominal augmented linear model ----------------
# states x=[T_f,T_c,d], input u=Q, plus affine ambient term
Ac = np.array([[-h_fc / C_f,  h_fc / C_f,        0.0],
               [ h_fc / C_c, -(h_fc + h_amb)/C_c, 1.0 / C_c],
               [ 0.0,         0.0,                0.0]])
Baug = np.array([[1.0 / C_f, 0.0],
                 [0.0,        h_amb * T_AMB_NOM / C_c],  # affine const input=1
                 [0.0,        0.0]])
M = np.zeros((5, 5)); M[:3, :3] = Ac; M[:3, 3:] = Baug
Md = expm(M * Ts)
Ad = Md[:3, :3]
Bd = Md[:3, 3:4]     # for Q
bd = Md[:3, 4:5]     # affine (input const = 1)
Hk = np.array([[0.0, 1.0, 0.0]])
Qkf = np.diag([1e-2, 1e-2, 0.5])     # let d random-walk to track slow drift
Rkf = np.array([[0.04]])             # meas var (0.2 C std)

def kf_step(x, P, Q_in, y):
    x = Ad @ x + Bd.flatten() * Q_in + bd.flatten()
    P = Ad @ P @ Ad.T + Qkf
    S = Hk @ P @ Hk.T + Rkf
    K = (P @ Hk.T) / S
    x = x + (K.flatten() * (y - (Hk @ x)[0]))
    P = (np.eye(3) - K @ Hk) @ P
    return x, P

# ---------------- truth plant simulator (mismatched, nonlinear) ----------------
def build_truth():
    m = do_mpc.model.Model('continuous')
    T_f = m.set_variable('_x', 'T_f')
    T_c = m.set_variable('_x', 'T_c')
    Qh = m.set_variable('_u', 'Qh')        # actual heat release (from combustion)
    T_amb = m.set_variable('_tvp', 'T_amb')
    lid = m.set_variable('_tvp', 'lid')
    m.set_rhs('T_f', (Qh - h_fc_t * (T_f - T_c)) / C_f_t)
    m.set_rhs('T_c', (h_fc_t * (T_f - T_c) - h_amb_t * lid * (T_c - T_amb)) / C_c_t)
    m.setup()
    sim = do_mpc.simulator.Simulator(m)
    sim.set_param(t_step=Ts)
    tvp_t = sim.get_tvp_template()
    state = {'t': 0.0}
    def tvp_fun(t_now):
        tvp_t['T_amb'] = 18.0 - 8.0 * (t_now / T_END)          # ambient drift down
        tvp_t['lid'] = 4.0 if 3000.0 <= t_now < 3090.0 else 1.0  # lid-open event (90s)
        return tvp_t
    sim.set_tvp_fun(tvp_fun)
    sim.setup()
    return sim

# ---------------- closed-loop run ----------------
def run(direct=False):
    mpc, _ = build_mpc(direct=direct)
    sim = build_truth()
    x_true = np.array([[T_AMB_NOM], [T_AMB_NOM]])   # start cold-ish
    sim.x0 = x_true
    sim.set_initial_guess()
    xhat = np.array([20.0, 20.0, 0.0]); P = np.eye(3) * 5.0
    mpc.x0 = xhat.reshape(-1, 1); mpc.set_initial_guess()

    log = {k: [] for k in ['t', 'Tc', 'sp', 'Q', 'auger', 'fan', 'afr', 'eff', 'dhat']}
    for k in range(N):
        t = k * Ts
        y = float(sim.x0['T_c']) + np.random.normal(0, 0.2)
        Q_applied = log['Q'][-1] if log['Q'] else Q_MIN
        xhat, P = kf_step(xhat, P, Q_applied, y)

        u = np.asarray(mpc.make_step(xhat.reshape(-1, 1))).flatten()
        if direct:
            auger = float(u[0]); fan = float(u[1])
            Qcmd = np.nan
        else:
            Qcmd = float(np.clip(u[0], Q_MIN, Q_MAX))
            auger, fan = allocator(Qcmd)

        Qh, afr, eff = combustion_heat(auger, fan)
        sim.make_step(np.array([[Qh]]))

        sp = 107.0 if t < 3600 else 121.0
        log['t'].append(t); log['Tc'].append(float(sim.x0['T_c'])); log['sp'].append(sp)
        log['Q'].append(Qcmd if not direct else Qh); log['auger'].append(auger)
        log['fan'].append(fan); log['afr'].append(afr); log['eff'].append(eff)
        log['dhat'].append(xhat[2])
    return {k: np.array(v) for k, v in log.items()}

def report(name, L):
    t, Tc, sp, afr, eff = L['t'], L['Tc'], L['sp'], L['afr'], L['eff']
    err = Tc - sp
    # TRUE steady windows: settled holds, excluding setpoint-step + lid transients
    m1 = (t >= 1500) & (t < 2900)          # hold at 107, after warmup, before lid
    m2 = (t >= 4600) & (t < T_END)         # hold at 121, after step settles
    sm = m1 | m2
    band = np.max(np.abs(err[sm]))
    rms = np.sqrt(np.mean(err[sm] ** 2))
    bias = np.mean(err[sm])
    print(f"\n===== {name} =====")
    print(f"STEADY band |error| max  : {band:6.2f} C   (target <= 1.0)")
    print(f"STEADY RMS / mean bias   : {rms:5.2f} C / {bias:+.2f} C")
    print(f"within +-1.0 C fraction  : {100*np.mean(np.abs(err[sm])<=1.0):5.1f} %")
    print(f"AFR range (steady)       : {afr[sm].min():.2f} .. {afr[sm].max():.2f}  (optimal {AFR_OPT})")
    print(f"combustion eff (steady)  : {eff[sm].min():.2f} .. {eff[sm].max():.2f}")
    # transients reported separately (NOT part of the steady band)
    step_mask = (t >= 3600) & (t < 4600)
    if step_mask.any():
        print(f"[transient] +14C setpoint-step peak err : {np.max(np.abs(err[step_mask])):5.2f} C")
    lid_mask = (t >= 3000) & (t < 3600)
    if lid_mask.any():
        dip = np.min(err[lid_mask])
        # recovery: time after lid closes (3090) until |err| back within 1.0 C
        rec = t[(t >= 3090) & (np.abs(err) <= 1.0)]
        rect = (rec[0] - 3090) if len(rec) else float('nan')
        print(f"[disturbance] lid-open dip {dip:6.2f} C, recover to +-1C in {rect:4.0f} s")

if __name__ == '__main__':
    print("Running cascade ...")
    Lc = run(direct=False)
    report("CASCADE (firing-rate Q + allocator)", Lc)
    print("\nRunning direct two-input ...")
    Ld = run(direct=True)
    report("DIRECT (auger+fan, linear model, no AFR)", Ld)

    import json
    step = 4  # downsample for plotting (every 100 s)
    out = {
        't': Lc['t'][::step].tolist(),
        'sp': Lc['sp'][::step].tolist(),
        'tc_cascade': Lc['Tc'][::step].tolist(),
        'tc_direct': Ld['Tc'][::step].tolist(),
        'afr_cascade': Lc['afr'][::step].tolist(),
        'afr_direct': Ld['afr'][::step].tolist(),
    }
    with open('/tmp/mpc_spike_data.json', 'w') as f:
        json.dump(out, f)
    print("\nwrote /tmp/mpc_spike_data.json")
