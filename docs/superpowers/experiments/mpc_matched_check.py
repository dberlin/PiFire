#!/usr/bin/env python3
"""
Is the 225->275F limit cycle a control problem, or an artifact of the deliberately
MISMATCHED plant? Run the same brisket step against a PERFECTLY MATCHED plant (the
controller's own grey-box ODE, driven by its own Q, no combustion/deadtime/gust
realism) and compare to GrillSim. If the cycle vanishes on the matched plant, it's
driven by plant mismatch/realism, not the MPC+estimator loop.
"""
import warnings, sys
warnings.filterwarnings("ignore")
sys.path.insert(0, '.')
import numpy as np
from controller.mpc import Controller, _DEFAULTS
from controller.mpc_model import _rad_loss
from controller.grill_sim import GrillSim

CYCLE = {'u_min': 0.1, 'u_max': 0.9, 'HoldCycleTime': 25}
C2F = lambda c: c * 9 / 5 + 32
CFG = _DEFAULTS
ND = int(CFG['n_delay'])
SP0, SP1 = 225.0, 275.0


def recon_Q(cr):
    frac = (cr - CYCLE['u_min']) / (CYCLE['u_max'] - CYCLE['u_min'])
    return CFG['Q_min'] + frac * (CFG['Q_max'] - CFG['Q_min'])


def model_deriv(x, Q):                 # controller's own grey-box rhs, d held 0
    tau_d = CFG['theta'] / ND
    dx = np.zeros_like(x)
    dx[0] = (Q - x[0]) / tau_d
    for i in range(1, ND):
        dx[i] = (x[i - 1] - x[i]) / tau_d
    heat_in = x[ND - 1]
    Tf, Tc = x[ND], x[ND + 1]
    dx[ND] = (CFG['K_Q'] * heat_in - CFG['h_fc'] * (Tf - Tc)) / CFG['C_f']
    dx[ND + 1] = (CFG['h_fc'] * (Tf - Tc) - CFG['h_amb'] * (Tc - CFG['T_amb'])
                  - _rad_loss(Tc, CFG['T_amb'], CFG['sigma'])) / CFG['C_c']
    return dx


def run_matched(seed=0, settle_min=60, hold_min=60):
    cfg = dict(CFG); cfg['est_q_dist'] = 0.05
    c = Controller(cfg, 'F', CYCLE); c.set_target(SP0)
    rng = np.random.default_rng(seed)
    x = np.zeros(ND + 3); x[ND] = x[ND + 1] = 20.0      # ambient start
    PT = []; step_sec = settle_min * 60; total = (settle_min + hold_min) * 60
    cr = 0.1
    for sec in range(total):
        if sec == step_sec:
            c.set_target(SP1)
        if sec % 25 == 0:
            ymeas = C2F(x[ND + 1]) + rng.normal(0, 0.15)
            out = c.update(ymeas); cr = float(np.clip(out['cycle_ratio'], 0.1, 0.9))
        Q = recon_Q(cr)
        x[ND + 2] = 0.0                                  # matched plant has no disturbance
        x = x + model_deriv(x, Q)
        PT.append(C2F(x[ND + 1]))
    return np.array(PT), step_sec


def run_mismatched(seed=0, settle_min=60, hold_min=60):
    cfg = dict(CFG); cfg['est_q_dist'] = 0.05
    c = Controller(cfg, 'F', CYCLE); c.set_target(SP0)
    p = GrillSim(seed=seed)
    PT = []; step_sec = settle_min * 60; total = (settle_min + hold_min) * 60
    cr, fan, on = 0.1, 100.0, 1; period_start = 0
    for sec in range(total):
        if sec == step_sec:
            c.set_target(SP1)
        if sec % 25 == 0:
            out = c.update(C2F(p.measured())); cr = float(np.clip(out['cycle_ratio'], 0.1, 0.9))
            fan = out['fan']['duty'] or 100.0; on = int(round(cr * 25)); period_start = sec
        p.step(auger_on=(sec - period_start) < on, fan_frac=fan / 100.0)
        PT.append(C2F(p.true_Tc))
    return np.array(PT), step_sec


def metrics(PT, step_s):
    post = PT[step_s:]
    over = post.max() - SP1
    reach = int(np.argmax(post >= SP1 - 2.0))
    rise = reach / 60.0 if post[reach] >= SP1 - 2.0 else float('nan')
    late = post[-20 * 60:]
    return over, rise, late.std(), post.max()


if __name__ == '__main__':
    print(f"{'plant':>14} {'overshoot':>10} {'rise':>6} {'osc_late':>9} {'peakF':>7}")
    for name, fn in (("MATCHED", run_matched), ("MISMATCHED", run_mismatched)):
        PT, ss = fn()
        o, ri, ol, pk = metrics(PT, ss)
        print(f"{name:>14} {o:9.1f}F {ri:5.1f}m {ol:8.2f}F {pk:6.0f}F")
