"""The fitter must recover parameters through the same dynamics the MPC uses."""

import numpy as np
import pytest

from controller.model_promotion import _T_FLOOR_C, _T_HAZARD_C, _effective_tau, _slowest_tau
from controller.mpc_model import simulate_grey_box
from controller.update_mpc import CONFIG_KEYS, fit_params, fit_quality

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


def test_the_model_is_invariant_under_a_common_scaling_of_its_parameters():
    """Why `_FREE` holds two parameters rather than one.

    Both state equations are homogeneous in the capacitances, the conductances
    and the input gain, so scaling all six together leaves the trajectory of
    the one measured state identical. Six parameters, five identifiable
    degrees of freedom -- a log determines the ratios, not the values, and one
    parameter must be held to fix the scale. If this ever stops holding, the
    reasoning in `_FREE` needs revisiting rather than quietly rotting.
    """
    t, Q, temp = _dataset()

    # The quantity the braking argument rests on is one of the invariants.
    def tau(params, sigma, T=250.0):
        return params["C_c"] / (params["h_amb"] + 4.0 * sigma * (T + 273.15) ** 3)

    # Powers of two scale every intermediate exactly, so the invariance shows
    # up bit-for-bit rather than merely to a tolerance; other factors differ
    # only by float rounding, which is what the looser bound below allows.
    for lam, exact in ((0.25, True), (0.5, True), (2.0, True), (4.0, True), (0.1, False), (7.0, False)):
        scaled = {k: (v * lam if k != "theta" else v) for k, v in TRUTH.items()}
        other = simulate_grey_box(t, Q, T0=25.0, T_amb=T_AMB, sigma=SIGMA * lam, n_delay=N_DELAY, **scaled)
        if exact:
            assert np.max(np.abs(other - temp)) == 0.0
        else:
            assert np.max(np.abs(other - temp)) < 1e-9
        assert tau(scaled, SIGMA * lam) == pytest.approx(tau(TRUTH, SIGMA), rel=1e-12)


def test_sigma_is_returned_exactly_as_it_was_passed():
    """sigma fixes the scale the rest are measured against; it is never fitted.

    Byte-identical, not merely close: a caller that round-trips a model through
    this fitter must get the same coefficient back, or the parameter that was
    supposed to be pinning the gauge has drifted.
    """
    t, Q, temp = _dataset()
    for sigma in (0.0, SIGMA, 2.7182818e-9):
        fitted = fit_params(t, temp, Q, T_amb=T_AMB, init=_init(), sigma=sigma, n_delay=N_DELAY)
        assert fitted["sigma"] == sigma


def test_the_reported_time_constant_accounts_for_radiative_conductance():
    """The horizon warning's whole job is to catch a chamber too slow to stop.

    Radiative conductance is most of the loss on a hot grill, so a tau that
    ignores it is not the number the warning is about. Read at the hot end,
    where 4*sigma*(T+273.15)**3 is largest, it must be well below the
    radiation-free C_c/h_amb.
    """
    t, Q, temp = _dataset()
    fitted = fit_params(t, temp, Q, T_amb=T_AMB, init=_init(), sigma=SIGMA, n_delay=N_DELAY)
    payload = {k: fitted[k] for k in CONFIG_KEYS}
    assert _effective_tau(payload, _T_HAZARD_C) < 0.75 * _slowest_tau(payload)
    # ...and the radiation-free bound is still what sizes the horizon, so one
    # horizon covers the whole operating range rather than only the hot end.
    assert _slowest_tau(payload) == pytest.approx(payload["C_c"] / payload["h_amb"])
    assert _effective_tau(payload, _T_FLOOR_C) > _effective_tau(payload, _T_HAZARD_C)


def test_a_deadtime_dataset_is_not_explained_by_a_zero_deadtime_structure():
    """The negative control for the defect this replaces: the old fitter had no
    delay chain and no radiative term, so it could only absorb them into the
    capacitances."""
    t, Q, temp = _dataset()
    crippled = fit_params(t, temp, Q, T_amb=T_AMB, init=_init(), sigma=0.0, n_delay=0)
    rmse, _ = fit_quality(t, temp, Q, crippled, T_amb=T_AMB)
    assert rmse > 2.0
