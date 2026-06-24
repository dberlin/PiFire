#!/usr/bin/env python3
"""
EKF vs MHE as the production estimator. The MHE solves an NLP each step (~10ms);
the EKF linearizes the one nonlinear (radiative) term and is one small expm/step.
Compares closed-loop band and per-step estimator time on the realistic plant at
several setpoints. control.py must never be imported (module-level while True).
"""
import warnings, sys, time
warnings.filterwarnings("ignore")
sys.path.insert(0, '.')
import numpy as np
from controller.mpc import Controller, _DEFAULTS
from controller.grill_sim import GrillSim

CYCLE = {'u_min': 0.1, 'u_max': 0.9, 'HoldCycleTime': 25}
TS = 25.0


def run(estimator, set_c, seed=0, minutes=90):
    cfg = dict(_DEFAULTS); cfg['estimator'] = estimator
    c = Controller(cfg, 'C', dict(CYCLE)); c.set_target(set_c)
    plant = GrillSim(seed=seed)
    ts, temps, est_ms = [], [], []
    for w in range(int(minutes * 60 / TS)):
        y = plant.measured()
        a = time.perf_counter()
        out = c.update(y)
        est_ms.append((time.perf_counter() - a) * 1e3)
        ratio = float(np.clip(out['cycle_ratio'], CYCLE['u_min'], CYCLE['u_max']))
        fan = out['fan']['duty'] if out['fan']['duty'] is not None else 100.0
        on = int(round(ratio * TS))
        for s in range(int(TS)):
            plant.step(auger_on=(s < on), fan_frac=fan / 100.0)
            ts.append(w * TS + s); temps.append(plant.true_Tc)
    ts, temps = np.array(ts), np.array(temps)
    sm = ts >= 1800
    e = temps[sm] - set_c
    return dict(rms=np.sqrt(np.mean(e ** 2)), mx=np.max(np.abs(e)),
                bias=np.mean(e), within5=np.mean(np.abs(e) <= 5.0),
                step_ms=np.median(est_ms[2:]))


if __name__ == '__main__':
    print(f"{'setpoint':>8} {'est':>5} {'RMS':>6} {'max':>6} {'bias':>6} {'<5C':>6} {'step_ms':>8}")
    for set_c in (110.0, 190.0, 220.0):            # ~230F, 375F, 425F
        for est in ('mhe', 'ekf'):
            r = run(est, set_c)
            print(f"{set_c:8.0f} {est:>5} {r['rms']:6.2f} {r['mx']:6.2f} "
                  f"{r['bias']:+6.2f} {r['within5']:6.2f} {r['step_ms']:8.1f}", flush=True)
