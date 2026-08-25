import numpy as np

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
