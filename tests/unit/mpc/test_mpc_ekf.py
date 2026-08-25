import numpy as np
import pytest

from controller.mpc_model import GreyBoxEKF, GreyBoxKF

# Shared grey-box params (a representative calibration).
P = {
    "C_c": 320.0,
    "h_amb": 0.50,
    "T_amb": 20.0,
    "t_step": 25.0,
    "q_temp": 1e-2,
    "q_dist": 0.5,
    "r_meas": 0.04,
    "theta": 50.0,
    "n_delay": 4,
    "K_Q": 350.0,
}

HOT_START = {
    "C_c": 320.0,
    "h_amb": 0.5,
    "T_amb": 20.0,
    "t_step": 5.0,
    "q_temp": 0.01,
    "q_dist": 0.05,
    "r_meas": 0.04,
    "theta": 50.0,
    "n_delay": 8,
    "K_Q": 350.0,
}


def _hot_start_estimator(estimator_type, *, initialized: bool = False):
    kwargs = dict(HOT_START)
    if estimator_type is GreyBoxEKF:
        kwargs["sigma"] = 1.4e-9
    if initialized:
        kwargs["x0"] = [0.0] * 8 + [20.0, 0.0]
    return estimator_type(**kwargs)


@pytest.mark.parametrize("estimator_type", [GreyBoxKF, GreyBoxEKF])
def test_hot_takeover_seeds_physical_state_from_applied_load_and_measurement(estimator_type):
    estimator = _hot_start_estimator(estimator_type)
    measured_c = (175.4 - 32.0) * 5.0 / 9.0

    state = estimator.update(0.189, measured_c)

    assert state[:8] == pytest.approx([0.189] * 8)
    assert state[8] == pytest.approx(measured_c)
    assert state[9] == 0.0


@pytest.mark.parametrize("estimator_type", [GreyBoxKF, GreyBoxEKF])
def test_measurement_correction_cannot_create_nonphysical_delay_loads(estimator_type):
    estimator = _hot_start_estimator(estimator_type, initialized=True)

    state = estimator.update(0.0, (175.4 - 32.0) * 5.0 / 9.0)

    assert np.all(state[:8] >= 0.0)
    assert np.all(state[:8] <= 1.0)


@pytest.mark.parametrize("estimator_type", [GreyBoxKF, GreyBoxEKF])
def test_bound_projection_keeps_covariance_coherent_under_repeated_observation(estimator_type):
    estimator = _hot_start_estimator(estimator_type, initialized=True)
    measured_c = (175.4 - 32.0) * 5.0 / 9.0

    first = estimator.update(0.0, measured_c).copy()
    first_covariance = estimator.P.copy()
    active = np.flatnonzero((first[:8] == 0.0) | (first[:8] == 1.0))
    second = estimator.update(0.0, measured_c).copy()

    assert active.size > 0
    assert first_covariance[active, 8:] == pytest.approx(0.0)
    assert first_covariance[8:, active] == pytest.approx(0.0)
    assert np.all(second[:8] >= 0.0)
    assert np.all(second[:8] <= 1.0)
    assert estimator.P == pytest.approx(estimator.P.T)
    assert np.linalg.eigvalsh(estimator.P).min() >= -1e-10


def test_ekf_reduces_to_kf_when_sigma_zero():
    # With no radiative term the EKF linearization is empty, so it must track the
    # linear Kalman filter step-for-step.
    kf = GreyBoxKF(**P)
    ekf = GreyBoxEKF(sigma=0.0, **P)
    rng = np.random.default_rng(0)
    for _ in range(40):
        Q = float(rng.uniform(0.0, 1.0))
        y = float(rng.uniform(20.0, 200.0))
        xk = kf.update(Q, y)
        xe = ekf.update(Q, y)
        assert np.allclose(xk, xe, atol=1e-9)


def test_ekf_radiative_changes_estimate():
    # A nonzero radiative term must actually alter the propagation (otherwise the
    # EKF would be ignoring the nonlinearity it exists to handle).
    ekf0 = GreyBoxEKF(sigma=0.0, **P)
    ekf1 = GreyBoxEKF(sigma=1.4e-9, **P)
    for _ in range(20):
        ekf0.update(0.8, 180.0)
        ekf1.update(0.8, 180.0)
    iTc = P["n_delay"]
    # at a hot chamber the radiative loss pulls the disturbance/temperature
    # estimates apart from the linear-only case
    assert abs(ekf0.x[iTc] - ekf1.x[iTc]) > 1e-3


def test_ekf_offset_free_constant_input():
    # Feeding a constant Q and a measurement the model can explain, the
    # integrating disturbance state settles and the chamber estimate converges to
    # the measurement (offset-free).
    ekf = GreyBoxEKF(sigma=1.4e-9, **P)
    iTc = P["n_delay"]
    y = 150.0
    for _ in range(400):
        ekf.update(0.4, y)
    assert abs(ekf.x[iTc] - y) < 1.0
