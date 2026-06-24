import numpy as np
from controller.mpc import Controller, _DEFAULTS
from controller.grill_sim import GrillSim

# Exercises the PRODUCTION defaults (nonlinear radiative model + EKF) against the
# realistic plant (pellet pulses, ~20s deadtime, fan lever, light wind, sensor lag).
CYCLE = {'u_min': 0.1, 'u_max': 0.9, 'HoldCycleTime': 25}
TS = 25.0
SETPOINT = 110.0


def _run(seed=0, minutes=90, setpoint=SETPOINT):
    c = Controller(dict(_DEFAULTS), 'C', dict(CYCLE))
    c.set_target(setpoint)
    plant = GrillSim(seed=seed)                 # default H=420 (~600F max), deadtime=20
    ts, temps = [], []
    for w in range(int(minutes * 60 / TS)):
        out = c.update(plant.measured())
        ratio = float(np.clip(out['cycle_ratio'], CYCLE['u_min'], CYCLE['u_max']))
        fan = out['fan']['duty'] if out['fan']['duty'] is not None else 100.0
        on = int(round(ratio * TS))
        for s in range(int(TS)):
            plant.step(auger_on=(s < on), fan_frac=fan / 100.0)
            ts.append(w * TS + s); temps.append(plant.true_Tc)
    return np.array(ts), np.array(temps)


def test_realistic_steady_band():
    # On a realistic plant (deadtime, sensor lag, pellet pulses, light wind) the
    # steady band is ~1C RMS / ~2.5C peak across seeds. Gates have ~2x margin to
    # guard the band without flaking on disturbance realizations.
    ts, temps = _run()
    sm = ts >= 1800                             # after 30 min warmup
    err = temps[sm] - SETPOINT
    assert np.sqrt(np.mean(err ** 2)) <= 2.0    # RMS (measured ~1.0C)
    assert np.mean(np.abs(err) <= 2.5) >= 0.90  # almost always within +-2.5C
    assert np.max(np.abs(err)) <= 5.0           # peak excursion (measured ~2.6C)


def test_offset_free_no_steady_bias():
    # The integrating-disturbance estimate (EKF) removes steady-state offset
    # despite model mismatch and the fan-as-lever the controller does not model.
    ts, temps = _run()
    sm = ts >= 1800
    assert abs(np.mean(temps[sm] - SETPOINT)) <= 1.0
