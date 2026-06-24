#!/usr/bin/env python3
"""
Final apples-to-apples comparison on the realistic plant (H=420 ~ 600F max,
deadtime=20, fan lever, wind, sensor lag 4.5; fixed fan 100%). Same seed.

Controllers:
  - MPC linear + KF      (production model, K_Q=3.5 calibrated)
  - MPC nonlinear + MHE  (radiative model, R_dQ=1.0 tuned)
  - PID                  controller/pid.py
  - PID-SP               controller/pid_sp.py  (FOPDT / Smith predictor)

Holds at 220/375/425 F and a 225 -> 375 F step.
"""
import warnings, sys, json, importlib
warnings.filterwarnings("ignore")
sys.path.insert(0, '.'); sys.path.insert(0, 'docs/superpowers/experiments')
import numpy as np
from mpc_nl_tune import NLCascade, LinKF, TSTEP, PLANT_H, F2C, C2F, UMIN, UMAX
from controller.grill_sim import GrillSim


class _Clk:
    def __init__(self): self.t = 0.0
    def time(self): return self.t


_clk = _Clk()
import controller.pid as _pid; _pid.time = _clk
import controller.pid_sp as _pidsp; _pidsp.time = _clk


class PIDWrap:
    def __init__(self, module):
        _clk.t = 0.0
        meta = json.load(open('controller/controllers.json'))['metadata'][module]
        cfg = {o['option_name']: o['option_default'] for o in meta['config']}
        self.c = importlib.import_module(f'controller.{module}').Controller(
            cfg, 'F', {'u_min': 0.1, 'u_max': 0.9, 'HoldCycleTime': 25})
    def set_target(self, sp_c): self.c.set_target(C2F(sp_c))
    def update(self, y_c):
        _clk.t += TSTEP
        return float(np.clip(self.c.update(C2F(y_c)), UMIN, UMAX)), 100.0


def drive(ctrl, sp_c, minutes=70, step=None, seed=0):
    plant = GrillSim(seed=seed, fan_is_lever=True, fixed_fan=1.0, deadtime=20, H=PLANT_H)
    ctrl.set_target(sp_c)
    T, ts = [], []
    for w in range(int(minutes * 60 / TSTEP)):
        t = w * TSTEP
        if step is not None and t >= step[0]:
            ctrl.set_target(step[1])
        ratio, fan = ctrl.update(plant.measured())
        on = int(round(ratio * TSTEP))
        for s in range(int(TSTEP)):
            plant.step(auger_on=(s < on), fan_frac=fan / 100.0)
            T.append(plant.true_Tc); ts.append(t + s)
    return np.array(ts), np.array(T)


BUILD = [
    ("MPC linear+KF", lambda: LinKF()),
    ("MPC nonlin+MHE", lambda: NLCascade(rterm=1.0, pwd=0.5)),
    ("PID", lambda: PIDWrap('pid')),
    ("PID-SP", lambda: PIDWrap('pid_sp')),
]


def hold_cell(ts, T, sp_c):
    sm = ts >= ts[-1] * 0.45; e = T[sm] - sp_c
    return f"RMS{np.sqrt(np.mean(e**2)):4.1f} mean{C2F(np.mean(T[sm])):3.0f}F"


def step_cell(ts, T, st, sp2):
    post = ts >= st; tt = ts[post] - st; Tp = T[post]; e = np.abs(Tp - sp2)
    s = next((tt[i] / 60 for i in range(len(tt)) if e[i] <= 3 and np.all(e[i:] <= 5)), None)
    return f"over{max(0,np.max(Tp)-sp2):4.1f}C " + (f"settle+{s:.0f}m" if s else f"end{C2F(Tp[-1]):.0f}F")


if __name__ == '__main__':
    sps = [220, 375, 425]
    print("HOLD (RMS in C, mean temp in F):")
    print(f"  {'controller':16s} " + "  ".join(f"{s}F{'':9s}" for s in sps))
    for name, mk in BUILD:
        cells = [hold_cell(*drive(mk(), F2C(s)), F2C(s)) for s in sps]
        print(f"  {name:16s} " + "  ".join(f"{c:13s}" for c in cells), flush=True)
    print("\nSTEP 225 -> 375 F at +25min:")
    for name, mk in BUILD:
        print(f"  {name:16s} " + step_cell(*drive(mk(), F2C(225), minutes=110, step=(1500, F2C(375))), 1500, F2C(375)), flush=True)
