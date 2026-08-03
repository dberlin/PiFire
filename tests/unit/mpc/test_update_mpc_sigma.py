"""The fitter learns the radiative coefficient when, and only when, it can.

`sigma` and `h_amb` are both chamber loss terms, separable only by their
different temperature dependence. These tests pin both halves of that: a record
that covers enough temperature recovers a known `sigma`, and one that does not
returns the incoming value untouched rather than inventing a split.

The threshold the gate uses is derived in
docs/superpowers/experiments/sigma_identifiability.py, which fits synthetic
cooks from a known `sigma` over a grid of temperature ranges.
"""

import os

import numpy as np
import pandas as pd
import pytest

from controller.model_promotion import PROMOTION_BOUNDS, _T_FLOOR_C, _T_HAZARD_C, _effective_tau
from controller.mpc_model import simulate_grey_box
from controller.update_mpc import (
    _BOUNDS,
    _MIN_RADIATIVE_SPREAD,
    can_identify_sigma,
    fit_params,
    fit_quality,
    radiative_spread,
)

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "mak_cook_2026-08-02.csv")

T_AMB = 20.0

#: A grill whose radiative coefficient is well away from the shipped 1.4e-9,
#: so "recovered" means the solver travelled rather than sat still.
TRUTH = dict(C_f=9.0, C_c=800.0, h_fc=0.90, h_amb=0.35, K_Q=5.0, theta=80.0, n_delay=4, sigma=3.0e-9)

#: What the fit starts from. K_Q matches truth because the fitter holds K_Q
#: whenever it frees sigma -- the two are not separately identifiable, so the
#: recovered sigma is only as good as the K_Q it is measured against. This is
#: the re-calibration case (a previously fitted model refitted from a new
#: cook), which is what task A7 does.
INIT = dict(C_f=9.0, C_c=320.0, h_fc=1.3, h_amb=0.50, K_Q=TRUTH["K_Q"], theta=50.0)

SHIPPED_SIGMA = 1.4e-9


def make_cook(t_cold, t_hot, seed=0, noise_c=0.4, leg_s=1350.0, dt=5.0):
    """A record that holds `t_cold`, steps to `t_hot`, and holds again.

    The plant starts at `T_f = T_c = t_cold` with an empty lag chain, which is
    exactly how `simulate_grey_box` initialises, so the truth parameters
    reproduce the record to within the noise and the fit is not spending its
    freedom on an initial-condition mismatch.
    """
    n = int(2 * leg_s / dt)
    t = np.arange(n, dtype=float) * dt
    setpoint = np.where(t < leg_s, t_cold, t_hot)
    p = TRUTH
    n_lag = int(p["n_delay"])
    lag_tau = p["theta"] / n_lag
    lags = np.zeros(n_lag)
    T_f = T_c = float(t_cold)
    temp = np.empty(n)
    Q = np.empty(n)
    amb4 = (T_AMB + 273.15) ** 4
    for i in range(n):
        temp[i] = T_c
        Q[i] = float(np.clip(5.0 + 3.0 * (setpoint[i] - T_c), 5.0, 100.0))
        if i == n - 1:
            break
        for _ in range(int(dt)):
            prev = Q[i]
            for j in range(n_lag):
                lags[j] += (prev - lags[j]) / lag_tau
                prev = lags[j]
            T_f += (p["K_Q"] * lags[-1] - p["h_fc"] * (T_f - T_c)) / p["C_f"]
            T_c += (
                p["h_fc"] * (T_f - T_c) - p["h_amb"] * (T_c - T_AMB) - p["sigma"] * ((T_c + 273.15) ** 4 - amb4)
            ) / p["C_c"]
    noise = np.random.default_rng(seed).normal(0.0, noise_c, n)
    return t, temp + noise, Q


def fit(t, temp, Q, sigma=SHIPPED_SIGMA, init=None):
    return fit_params(t, temp, Q, T_amb=T_AMB, init=init or INIT, sigma=sigma, n_delay=int(TRUTH["n_delay"]))


def tau_error(params):
    """Worst relative error in effective tau across the operating range.

    Read at both ends, exactly as controller/model_promotion.py judges a
    candidate, because a single reference temperature lets a model trade sigma
    against h_amb so that its one crossing lands there and reads as correct.
    """
    return max(
        abs(_effective_tau(params, T) - _effective_tau(TRUTH, T)) / _effective_tau(TRUTH, T)
        for T in (_T_FLOOR_C, _T_HAZARD_C)
    )


def test_synthetic_cook_reproduces_truth_within_noise():
    """Negative control on the generator: without this the rest proves nothing.

    If the truth parameters could not explain the synthetic record, the fit
    would be absorbing that error into sigma and every recovery number below
    would be about the harness.
    """
    t, temp, Q = make_cook(40.0, 260.0)
    sim = simulate_grey_box(t, Q, T_amb=T_AMB, T0=float(temp[0]), **TRUTH)
    assert np.sqrt(np.mean((sim - temp) ** 2)) < 0.6


@pytest.mark.parametrize("seed", range(4))
def test_wide_span_cook_recovers_known_sigma(seed):
    """Requirement 1: a record covering enough temperature learns sigma.

    The 25% tolerance is the sweep's own scoring band; within it, every grid
    cell this gate accepts recovered sigma on every seed.
    """
    t, temp, Q = make_cook(40.0, 260.0, seed=seed)
    assert can_identify_sigma(temp, h_amb=INIT["h_amb"])
    out = fit(t, temp, Q)
    assert out["sigma"] == pytest.approx(TRUTH["sigma"], rel=0.25)


@pytest.mark.parametrize("t_hold", [100.0, 160.0, 220.0])
def test_isothermal_cook_returns_incoming_sigma_byte_identical(t_hold):
    """Requirement: the gate's default is to hold, and to hold EXACTLY."""
    t, temp, Q = make_cook(t_hold, t_hold)
    assert not can_identify_sigma(temp, h_amb=INIT["h_amb"])
    out = fit(t, temp, Q, sigma=SHIPPED_SIGMA)
    assert out["sigma"] == SHIPPED_SIGMA  # byte-identical, not merely close
    # And an arbitrary incoming value survives just as exactly.
    odd = 2.7182818e-9
    assert fit(t, temp, Q, sigma=odd)["sigma"] == odd


def test_a_zero_sigma_is_left_alone_even_on_an_identifiable_cook():
    """Zero means "no radiative term", not "unknown radiative term".

    Fitting one here would change the model's structure rather than its
    parameters, which is a different question than the caller asked.
    """
    t, temp, Q = make_cook(40.0, 260.0)
    assert can_identify_sigma(temp, h_amb=INIT["h_amb"])
    assert fit(t, temp, Q, sigma=0.0)["sigma"] == 0.0


def test_short_record_holds_sigma():
    """A handful of samples cannot identify a loss term's temperature shape."""
    t, temp, Q = make_cook(180.0, 260.0)
    out = fit(t[:12], temp[:12], Q[:12], sigma=SHIPPED_SIGMA)
    assert out["sigma"] == SHIPPED_SIGMA


def test_fitted_sigma_stays_inside_the_promotion_policys_bounds():
    """Requirement 3: the fitter cannot produce what its own gate would refuse."""
    lo, hi = PROMOTION_BOUNDS["sigma"]
    assert _BOUNDS["sigma"] == (lo, hi), "fitter bound and promotion bound have drifted apart"
    for cold, hot in ((40.0, 260.0), (160.0, 280.0), (220.0, 280.0)):
        for seed in range(3):
            t, temp, Q = make_cook(cold, hot, seed=seed)
            sigma = fit(t, temp, Q)["sigma"]
            assert lo <= sigma <= hi
            assert sigma >= 0.0


def test_freeing_sigma_gets_the_effective_time_constant_closer_to_truth():
    """Requirement 5 -- the test that justifies the task.

    A sigma held at the wrong value does not simply leave a wrong sigma: the
    fit still has to explain the observed loss, so the error lands in h_amb and
    C_c, which is what the promotion policy's time-constant guard reads.
    """
    errs_free, errs_held = [], []
    for seed in range(4):
        t, temp, Q = make_cook(40.0, 260.0, seed=seed)
        # Same solver and same data on both arms; only the gate differs, so
        # sigma stays at the shipped value on the held arm while truth is 2.1x
        # that.
        errs_free.append(tau_error(fit(t, temp, Q)))
        errs_held.append(tau_error(_held_arm(t, temp, Q)))
    assert np.median(errs_free) < np.median(errs_held)


def _held_arm(t, temp, Q):
    """The fit this change replaces: sigma pinned, K_Q free."""
    import controller.update_mpc as U

    saved = U._MIN_RADIATIVE_SPREAD
    U._MIN_RADIATIVE_SPREAD = float("inf")
    try:
        return fit(t, temp, Q, sigma=SHIPPED_SIGMA)
    finally:
        U._MIN_RADIATIVE_SPREAD = saved


def test_radiative_spread_ignores_a_temperature_merely_passed_through():
    """The gate weights by dwell, not by min-to-max.

    A record that starts off its setpoint, sags once and then sits there covers
    a wide min-to-max while holding exactly one operating point. Min-to-max
    cannot tell it from a genuine two-level cook; the percentile spread can.
    """
    sagged = np.concatenate([np.linspace(220.0, 196.0, 20), np.full(500, 196.0)])
    stepped = np.concatenate([np.full(260, 196.0), np.full(260, 220.0)])
    assert sagged.max() - sagged.min() == pytest.approx(stepped.max() - stepped.min())
    assert radiative_spread(sagged) < radiative_spread(stepped)


def test_gate_threshold_is_relative_to_the_linear_loss_coefficient():
    t, temp, Q = make_cook(160.0, 280.0)
    spread = radiative_spread(temp)
    assert can_identify_sigma(temp, h_amb=spread / _MIN_RADIATIVE_SPREAD * 0.99)
    assert not can_identify_sigma(temp, h_amb=spread / _MIN_RADIATIVE_SPREAD * 1.01)
    assert not can_identify_sigma(temp, h_amb=0.0)


def _mak_cook():
    df = pd.read_csv(FIXTURE)
    return df["time_s"].values, df["temp_c"].values, df["Q"].values


def test_real_mak_cook_is_not_fitted_worse_with_sigma_free():
    """Requirement 4: the real cook this work came from must not regress."""
    t, temp, Q = _mak_cook()
    init = dict(INIT, K_Q=3.5)  # the shipped default, as the CLI uses
    free = fit_params(t, temp, Q, T_amb=T_AMB, init=init, sigma=SHIPPED_SIGMA, n_delay=4)
    assert free["sigma"] != SHIPPED_SIGMA, "the MAK cook spans enough temperature to fit sigma"

    import controller.update_mpc as U

    saved = U._MIN_RADIATIVE_SPREAD
    U._MIN_RADIATIVE_SPREAD = float("inf")
    try:
        held = fit_params(t, temp, Q, T_amb=T_AMB, init=init, sigma=SHIPPED_SIGMA, n_delay=4)
    finally:
        U._MIN_RADIATIVE_SPREAD = saved

    rmse_free, _ = fit_quality(t, temp, Q, free, T_amb=T_AMB)
    rmse_held, _ = fit_quality(t, temp, Q, held, T_amb=T_AMB)
    assert rmse_free <= rmse_held


def test_the_real_mak_cook_can_identify_sigma():
    t, temp, Q = _mak_cook()
    assert can_identify_sigma(temp, h_amb=0.5)
