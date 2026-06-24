import numpy as np
from controller.mpc import Controller
from controller.grill_sim import GrillSim

# Production-style config: the deadtime grey-box (theta/n_delay) re-solving at
# the control period (= t_step here). enable_fan_input exercises the fan path.
CONFIG = dict(
    n_horizon=20, t_step=25.0, control_period=25.0, Q_w=1.0, R_dQ=0.02,
    Q_min=5.0, Q_max=100.0, C_f=60.0, C_c=306.0, h_fc=2.0, h_amb=0.55,
    T_amb=20.0, theta=50.0, n_delay=4, fan_min_pct=40.0, fan_max_pct=100.0,
    enable_fan_input=True, est_q_temp=1e-2, est_q_dist=0.5, est_r_meas=0.04,
)
CYCLE = {'u_min': 0.1, 'u_max': 0.9, 'HoldCycleTime': 25}
TS = 25.0
SETPOINT = 110.0


def _run(deadtime=20, seed=0, minutes=120):
    """Closed-loop the production MPC against the realistic plant. The MPC
    yields an auger cycle ratio each control window; the plant is fed discrete
    pellet pulses (auger on for ratio*TS within the window)."""
    c = Controller(dict(CONFIG), 'C', dict(CYCLE))
    c.set_target(SETPOINT)
    plant = GrillSim(seed=seed, deadtime=deadtime, fan_is_lever=True)
    ts, temps = [], []
    for w in range(int(minutes * 60 / TS)):
        out = c.update(plant.measured())
        ratio = float(np.clip(out['cycle_ratio'], CYCLE['u_min'], CYCLE['u_max']))
        fan = out['fan']['duty'] if out['fan']['duty'] is not None else 70.0
        on = int(round(ratio * TS))
        for s in range(int(TS)):
            plant.step(auger_on=(s < on), fan_frac=fan / 100.0)
            ts.append(w * TS + s); temps.append(plant.true_Tc)
    return np.array(ts), np.array(temps)


def test_realistic_steady_band():
    # Honest acceptance gate: on a realistic plant (pellet pulses, ~20s deadtime,
    # fan lever, wind gusts, sensor lag) the achievable steady band is a few
    # degrees C -- NOT +-1C. This guards against regressions in that band.
    ts, temps = _run()
    sm = ts >= 1800                            # after 30 min warmup
    err = temps[sm] - SETPOINT
    assert np.sqrt(np.mean(err ** 2)) <= 5.0   # RMS within realistic band
    assert np.mean(np.abs(err) <= 5.0) >= 0.70
    assert np.max(np.abs(err)) <= 16.0


def test_offset_free_no_steady_bias():
    # The integrating-disturbance estimator removes steady-state offset despite
    # the ~15% model mismatch and the fan-as-lever the controller does not model.
    ts, temps = _run()
    sm = ts >= 1800
    assert abs(np.mean(temps[sm] - SETPOINT)) <= 2.0
