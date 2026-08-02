"""The fitter must recover parameters through the same dynamics the MPC uses."""

import numpy as np
import pytest

from controller.mpc_model import simulate_grey_box
from controller.update_mpc import fit_params, fit_quality

TRUTH = dict(C_f=9.0, C_c=11000.0, h_fc=1.3, h_amb=2.7, K_Q=32.0, theta=110.0)
T_AMB = 20.0
N_DELAY = 4
SIGMA = 1.4e-9


def _dataset():
    """A heat-up to a plateau then a step down -- enough excitation to identify
    the gain, the loss and the deadtime."""
    t = np.arange(0.0, 6000.0, 5.0)
    Q = np.where(t < 3000.0, 100.0, 20.0)
    temp = simulate_grey_box(t, Q, T0=25.0, T_amb=T_AMB, sigma=SIGMA, n_delay=N_DELAY, **TRUTH)
    return t, Q, temp


def _init():
    return dict(C_f=9.0, C_c=320.0, h_fc=1.3, h_amb=0.5, K_Q=3.5, theta=50.0)


def test_fit_recovers_the_generating_parameters():
    t, Q, temp = _dataset()
    fitted = fit_params(t, temp, Q, T_amb=T_AMB, init=_init(), sigma=SIGMA, n_delay=N_DELAY)
    # C_c and K_Q are recovered as a ratio with h_amb, so compare the quantity
    # the controller's braking distance actually depends on: the time constant.
    assert fitted["C_c"] / fitted["h_amb"] == pytest.approx(TRUTH["C_c"] / TRUTH["h_amb"], rel=0.20)
    assert fitted["theta"] == pytest.approx(TRUTH["theta"], rel=0.30)


def test_fit_quality_is_reported_and_is_tight_on_its_own_data():
    t, Q, temp = _dataset()
    fitted = fit_params(t, temp, Q, T_amb=T_AMB, init=_init(), sigma=SIGMA, n_delay=N_DELAY)
    rmse, max_err = fit_quality(t, temp, Q, fitted, T_amb=T_AMB)
    assert rmse < 2.0
    assert max_err < 10.0


def test_the_fitted_dict_carries_every_key_the_controller_config_needs():
    t, Q, temp = _dataset()
    fitted = fit_params(t, temp, Q, T_amb=T_AMB, init=_init(), sigma=SIGMA, n_delay=N_DELAY)
    for key in ("C_f", "C_c", "h_fc", "h_amb", "T_amb", "theta", "n_delay", "K_Q", "sigma"):
        assert key in fitted


def test_a_deadtime_dataset_is_not_explained_by_a_zero_deadtime_structure():
    """The negative control for the defect this replaces: the old fitter had no
    delay chain and no radiative term, so it could only absorb them into the
    capacitances."""
    t, Q, temp = _dataset()
    crippled = fit_params(t, temp, Q, T_amb=T_AMB, init=_init(), sigma=0.0, n_delay=0)
    rmse, _ = fit_quality(t, temp, Q, crippled, T_amb=T_AMB)
    assert rmse > 2.0
