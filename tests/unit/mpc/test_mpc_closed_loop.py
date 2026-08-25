import numpy as np
import pytest

from controller.applied_output import AppliedOutput, OutputSource
from controller.grill_sim import GrillSim
from controller.mpc import Controller
from controller.mpc_config import DEFAULT_MPC_CONFIG

# Exercises the PRODUCTION defaults (nonlinear radiative model + EKF) against the
# realistic plant (pellet pulses, ~20s deadtime, fan lever, light wind, sensor lag).
CYCLE = {"u_min": 0.1, "u_max": 0.9}
TS = 25.0
SETPOINT = 110.0


def _run(seed=0, minutes=90, setpoint=SETPOINT):
    # This harness advances the plant TS seconds per update(), so the estimator
    # discretization (control_period) must equal TS regardless of the shipped
    # default (which is 5.0 for a faster production re-solve cadence).
    c = Controller({**DEFAULT_MPC_CONFIG, "control_period": TS}, "C", dict(CYCLE))
    c.set_target(setpoint)
    plant = GrillSim(seed=seed)  # default H=420 (~600F max), deadtime=20
    ts, temps = [], []
    for w in range(int(minutes * 60 / TS)):
        out = c.update(plant.measured())
        ratio = float(np.clip(out["cycle_ratio"], 0.0, CYCLE["u_max"]))
        fan = out["fan"]["duty"] if out["fan"]["duty"] is not None else 100.0
        on = int(round(ratio * TS))
        for s in range(int(TS)):
            plant.step(auger_on=(s < on), fan_frac=fan / 100.0)
            ts.append(w * TS + s)
            temps.append(plant.true_Tc)
        c.set_output(
            AppliedOutput(
                ratio=on / TS,
                source=OutputSource.CONTROLLER,
                timestamp=(w + 1) * TS,
                requested=ratio,
            )
        )
    return np.array(ts), np.array(temps)


@pytest.fixture(scope="module")
def steady_run():
    """One cook, shared by every test below that asks it a different question.

    `_run` is deterministic at a fixed seed, so a second call spends another
    216 nonlinear solves rebuilding an array this one already holds. The tests
    only read it.
    """
    return _run()


def test_realistic_steady_band(steady_run):
    # The shipped model is deliberately uncalibrated and warns that tight tracking
    # requires a grill-specific fit. This contract guards bounded convergence
    # through the production applied-output feedback path.
    ts, temps = steady_run
    sm = ts >= 1800  # after 30 min warmup
    err = temps[sm] - SETPOINT
    assert np.sqrt(np.mean(err**2)) <= 5.5  # measured 4.52 C
    assert np.mean(np.abs(err) <= 2.5) >= 0.30  # measured 0.37
    assert np.max(np.abs(err)) <= 10.0  # measured 8.31 C


def test_offset_free_no_steady_bias(steady_run):
    # Even before calibration, applied-load feedback keeps the residual
    # estimator from developing an unbounded offset.
    ts, temps = steady_run
    sm = ts >= 1800
    assert abs(np.mean(temps[sm] - SETPOINT)) <= 4.0  # measured 3.04 C
