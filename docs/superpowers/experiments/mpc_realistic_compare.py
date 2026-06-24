#!/usr/bin/env python3
"""
Controller comparison on the NOW-REALISTIC plant (light wind), using the PRODUCTION
controller for the MPC variants:

  - MPC nonlinear + EKF : shipped default (radiative model, est_q_dist=0.05)
  - MPC linear + KF     : estimator='kf', sigma=0 (linear model, linear Kalman)
  - PID                 : controller/pid.py        (PiFire's default PID)
  - PID-SP              : controller/pid_sp.py      (FOPDT / Smith-predictor PID)

Steady band (RMS / max|e| / bias) at 110/190/220 C and a 225->275 F setpoint step
(overshoot / rise). Same seeds; auger-only, fan held 100% for every controller.
"""
import warnings, sys, json, importlib
warnings.filterwarnings("ignore")
sys.path.insert(0, '.')
import numpy as np
from controller.mpc import Controller as MPCController, _DEFAULTS
from controller.grill_sim import GrillSim

CYCLE = {'u_min': 0.1, 'u_max': 0.9, 'HoldCycleTime': 25}
TS = 25.0
C2F = lambda c: c * 9 / 5 + 32


class _Clk:
    def __init__(self): self.t = 0.0
    def time(self): return self.t


_clk = _Clk()
import controller.pid as _pid; _pid.time = _clk
import controller.pid_sp as _pidsp; _pidsp.time = _clk


class MPCAdapter:
    def __init__(self, **over):
        cfg = dict(_DEFAULTS); cfg.update(over)
        self.c = MPCController(cfg, 'C', dict(CYCLE))
    def set_target(self, sc): self.c.set_target(sc)
    def update(self, yc): return float(np.clip(self.c.update(yc)['cycle_ratio'], 0.1, 0.9))


class PIDAdapter:
    def __init__(self, module):
        _clk.t = 0.0
        meta = json.load(open('controller/controllers.json'))['metadata'][module]
        cfg = {o['option_name']: o['option_default'] for o in meta['config']}
        self.c = importlib.import_module(f'controller.{module}').Controller(cfg, 'F', dict(CYCLE))
    def set_target(self, sc): self.c.set_target(C2F(sc))
    def update(self, yc):
        _clk.t += TS
        return float(np.clip(self.c.update(C2F(yc)), 0.1, 0.9))


def drive(make, set_c, seed=0, minutes=90, step=None):
    a = make(); a.set_target(set_c); plant = GrillSim(seed=seed)
    ts, T = [], []; stepped = False
    for w in range(int(minutes * 60 / TS)):
        t = w * TS
        if step and not stepped and t >= step[0]:
            a.set_target(step[1]); stepped = True       # change setpoint ONCE
        ratio = a.update(plant.measured())
        on = int(round(ratio * TS))
        for s in range(int(TS)):
            plant.step(auger_on=(s < on), fan_frac=1.0)
            T.append(plant.true_Tc); ts.append(t + s)
    return np.array(ts), np.array(T)


CTRLS = {
    'MPC nl+EKF': lambda: MPCAdapter(),
    'MPC lin+KF': lambda: MPCAdapter(estimator='kf', sigma=0.0),
    'PID':        lambda: PIDAdapter('pid'),
    'PID-SP':     lambda: PIDAdapter('pid_sp'),
}


def steady(make, set_c, seeds=(0, 1, 2)):
    rms, mx, bias = [], [], []
    for sd in seeds:
        ts, T = drive(make, set_c, seed=sd, minutes=90)
        e = T[ts >= 1800] - set_c
        rms.append(np.sqrt(np.mean(e ** 2))); mx.append(np.max(np.abs(e))); bias.append(np.mean(e))
    return np.mean(rms), np.max(mx), np.mean(bias)


def step_resp(make, seed=0):
    # hold 225F (107.2C), step to 275F (135C) at 60 min
    s0, s1 = (225 - 32) * 5 / 9, (275 - 32) * 5 / 9
    ts, T = drive(make, s0, seed=seed, minutes=120, step=(60 * 60, s1))
    post = T[ts >= 60 * 60]
    over = (post.max() - s1) * 9 / 5                  # overshoot in F
    reach = int(np.argmax(post >= s1 - 1.0))
    rise = (reach / 60.0) if post[reach] >= s1 - 1.0 else float('nan')
    return over, rise


if __name__ == '__main__':
    print("STEADY band on realistic plant (RMS / max|e| / bias, deg C; 3-seed):")
    print(f"{'controller':>12} | {'110C(230F)':>18} | {'190C(374F)':>18} | {'220C(428F)':>18}")
    for name, mk in CTRLS.items():
        cells = []
        for sc in (110.0, 190.0, 220.0):
            r, m, b = steady(mk, sc)
            cells.append(f"{r:4.1f}/{m:4.1f}/{b:+4.1f}")
        print(f"{name:>12} | {cells[0]:>18} | {cells[1]:>18} | {cells[2]:>18}", flush=True)

    print("\n225->275F setpoint step:")
    print(f"{'controller':>12}  {'overshoot':>10}  {'rise':>7}")
    for name, mk in CTRLS.items():
        o, ri = step_resp(mk)
        print(f"{name:>12}  {o:8.1f}F  {ri:5.1f}min", flush=True)
