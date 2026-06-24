#!/usr/bin/env python3
"""
ApproxMPC proof of concept (single setpoint, 110C). Approximate the production
nonlinear MPC's policy (state -> firing rate Q) with a small neural net so the
~15ms NLP solve becomes a ~us net evaluation. Estimation still uses the MHE.

Pipeline (do-mpc approximateMPC): sample the real MPC -> train a FeedforwardNN ->
run closed-loop with MHE + the net, comparing band and per-step solve time vs
the full MPC.
"""
import warnings, sys, time, os, shutil
warnings.filterwarnings("ignore")
sys.path.insert(0, '.')
import numpy as np
import torch
from controller.mpc import Controller, _DEFAULTS
from controller.grill_sim import GrillSim
from do_mpc.approximateMPC import AMPCSampler, ApproxMPC, Trainer

SP = 110.0
CYCLE = {'u_min': 0.1, 'u_max': 0.9, 'HoldCycleTime': 25}
DATA = './docs/superpowers/experiments/_ampc_data'
ND = int(_DEFAULTS['n_delay'])
N = ND + 3                                    # state dim [q0..q3, T_f, T_c, d]

# state / input box for sampling + scaling
LBX = [0.0] * ND + [20.0, 20.0, -80.0]
UBX = [100.0] * ND + [220.0, 170.0, 80.0]
LBU, UBU = [5.0], [100.0]


def make_controller():
    c = Controller(dict(_DEFAULTS), 'C', dict(CYCLE))
    c.set_target(SP)
    return c


def build_approx():
    c = make_controller()
    mpc = c.mpc

    # 1) sample the real MPC (skip if data already present)
    have_data = os.path.isdir(DATA) and any(f.startswith('pifire') for f in os.listdir(DATA))
    if not have_data:
        sampler = AMPCSampler(mpc)
        sampler.settings.n_samples = 2000
        sampler.settings.dataset_name = 'pifire'
        sampler.settings.data_dir = DATA
        sampler.settings.closed_loop_flag = False
        sampler.settings.lbx, sampler.settings.ubx = LBX, UBX
        sampler.settings.lbu, sampler.settings.ubu = LBU, UBU
        sampler.setup()
        t0 = time.perf_counter()
        sampler.default_sampling()
        print(f"  sampled 2000 in {time.perf_counter()-t0:.0f}s", flush=True)
    else:
        print("  reusing existing sampled data", flush=True)

    # 2) configure + setup the approximator
    ampc = ApproxMPC(mpc)
    ampc.settings.n_hidden_layers = 2
    ampc.settings.n_neurons = 40
    tt = lambda v: torch.tensor(v, dtype=torch.float32).reshape(-1, 1)
    ampc.settings.lbx, ampc.settings.ubx = tt(LBX), tt(UBX)
    ampc.settings.lbu, ampc.settings.ubu = tt(LBU), tt(UBU)
    ampc.setup()

    # 3) train
    trainer = Trainer(ampc)
    trainer.settings.dataset_name = 'pifire'
    trainer.settings.data_dir = DATA
    trainer.settings.n_epochs = 200
    trainer.setup()
    t0 = time.perf_counter()
    trainer.default_training()
    print(f"  trained 200 epochs in {time.perf_counter()-t0:.0f}s", flush=True)
    return ampc


def run_full(seed=0, minutes=75):
    c = make_controller(); plant = GrillSim(seed=seed)
    T, ms = [], []
    for w in range(int(minutes * 60 / 25)):
        a = time.perf_counter(); out = c.update(plant.measured()); ms.append((time.perf_counter() - a) * 1e3)
        r = float(np.clip(out['cycle_ratio'], 0.1, 0.9))
        fan = out['fan']['duty'] or 100.0
        on = int(round(r * 25))
        for s in range(25):
            plant.step(auger_on=(s < on), fan_frac=fan / 100.0); T.append(plant.true_Tc)
    return np.array(T), np.array(ms)


def run_approx(ampc, seed=0, minutes=75):
    # MHE for estimation, approx-net for the policy
    c = make_controller(); est = c.estimator
    plant = GrillSim(seed=seed)
    qmin, qmax = _DEFAULTS['Q_min'], _DEFAULTS['Q_max']
    umin, umax = 0.1, 0.9
    T, ms_est, ms_net = [], [], []
    lastQ = qmin
    for w in range(int(minutes * 60 / 25)):
        y = plant.measured()
        a = time.perf_counter(); xh = est.update(lastQ, y); ms_est.append((time.perf_counter() - a) * 1e3)
        a = time.perf_counter()
        Q = float(np.asarray(ampc.make_step(xh.reshape(-1, 1), u_prev=np.array([[lastQ]]))).flatten()[0])
        ms_net.append((time.perf_counter() - a) * 1e3)
        Q = float(np.clip(Q, qmin, qmax)); lastQ = Q
        r = umin + (Q - qmin) / (qmax - qmin) * (umax - umin)
        on = int(round(r * 25))
        for s in range(25):
            plant.step(auger_on=(s < on), fan_frac=1.0); T.append(plant.true_Tc)
    return np.array(T), np.array(ms_est), np.array(ms_net)


def band(T, win=0.4):
    sm = np.arange(len(T)) >= len(T) * win; e = T[sm] - SP
    return np.sqrt(np.mean(e ** 2)), np.max(np.abs(e)), np.mean(e)


if __name__ == '__main__':
    print("Building approxMPC (sample + train) ...", flush=True)
    ampc = build_approx()
    print("\nClosed loop at 110C:", flush=True)
    Tf, msf = run_full()
    r, m, b = band(Tf)
    print(f"  FULL  MPC  : band RMS={r:.2f} max={m:.2f} bias={b:+.2f}  solve(MHE+MPC) median={np.median(msf[2:]):.0f}ms")
    Ta, mse, msn = run_approx(ampc)
    r, m, b = band(Ta)
    print(f"  APPROX MPC : band RMS={r:.2f} max={m:.2f} bias={b:+.2f}  MHE median={np.median(mse[2:]):.1f}ms  net median={np.median(msn[2:]):.2f}ms")
