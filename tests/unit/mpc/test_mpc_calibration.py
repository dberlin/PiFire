"""The fitter must recover parameters through the same dynamics the MPC uses."""

import os

import numpy as np
import pandas as pd
import pytest

from controller.model_promotion import T_FLOOR_C, T_HAZARD_C, effective_tau, slowest_tau
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
    """Why `_FREE` has to hold a parameter at all.

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
    """Radiative conductance is most of the chamber's loss on a hot grill.

    Asserted against the formula rather than a ratio: what makes this the right
    number is that it is C_c over the linear PLUS linearized-radiative
    conductance, and an equality says so exactly where a threshold would only
    say "smaller than C_c/h_amb by roughly enough".
    """
    t, Q, temp = _dataset()
    fitted = fit_params(t, temp, Q, T_amb=T_AMB, init=_init(), sigma=SIGMA, n_delay=N_DELAY)
    payload = {k: fitted[k] for k in CONFIG_KEYS}
    for t_ref in (T_FLOOR_C, T_HAZARD_C):
        expected = payload["C_c"] / (payload["h_amb"] + 4.0 * payload["sigma"] * (t_ref + 273.15) ** 3)
        assert effective_tau(payload, t_ref) == pytest.approx(expected, rel=1e-12)
    # Radiation shortens it, and more so the hotter the chamber gets.
    assert effective_tau(payload, T_HAZARD_C) < effective_tau(payload, T_FLOOR_C) < slowest_tau(payload)
    # ...and the radiation-free bound is still what sizes the horizon, so one
    # horizon covers the whole operating range rather than only the hot end.
    assert slowest_tau(payload) == pytest.approx(payload["C_c"] / payload["h_amb"])


def test_the_cli_reports_the_radiation_aware_time_constant(tmp_path, capsys, monkeypatch):
    """The kept CLI fix, exercised through `main()` rather than around it.

    The warning's threshold is deliberately unchanged -- it still fires on
    C_c/h_amb, the radiation-free supremum, so one horizon covers the whole
    operating range. What changed is what the utility TELLS you: the effective
    time constant at each end, which is what the chamber's response actually
    is. This checks the reporting, not the trigger.
    """
    import controller.update_mpc as U

    t, Q, temp = _dataset()
    csv = tmp_path / "cook.csv"
    csv.write_text("time_s,temp_c,Q\n" + "".join(f"{a},{b},{c}\n" for a, b, c in zip(t, temp, Q)))
    monkeypatch.setattr("sys.argv", ["update_mpc", str(csv), "--t-amb", str(T_AMB)])
    U.main()
    out = capsys.readouterr().out

    fitted = fit_params(t, temp, Q, T_amb=T_AMB, init=_init(), sigma=SIGMA, n_delay=N_DELAY)
    payload = {k: fitted[k] for k in CONFIG_KEYS}
    hot = effective_tau(payload, T_HAZARD_C)
    cold = effective_tau(payload, T_FLOOR_C)
    assert f"{hot:.0f} s at {T_HAZARD_C:.0f} C" in out
    assert f"{cold:.0f} s at {T_FLOOR_C:.0f} C" in out
    # The radiation-free number must not be what got printed as the response.
    assert f"Chamber time constant: {slowest_tau(payload):.0f} s" not in out


def test_the_cli_says_so_when_the_solver_ran_out_of_evaluations(tmp_path, capsys, monkeypatch):
    """An exhausted solve must not be presented as a finished fit."""
    import controller.update_mpc as U

    t, Q, temp = _dataset()
    csv = tmp_path / "cook.csv"
    csv.write_text("time_s,temp_c,Q\n" + "".join(f"{a},{b},{c}\n" for a, b, c in zip(t, temp, Q)))
    monkeypatch.setattr("sys.argv", ["update_mpc", str(csv), "--t-amb", str(T_AMB)])

    monkeypatch.setattr(U, "_MAX_NFEV", 3)  # far too few to converge
    U.main()
    starved = capsys.readouterr().out
    assert "ran out of evaluations" in starved

    monkeypatch.setattr(U, "_MAX_NFEV", 2000)
    U.main()
    assert "ran out of evaluations" not in capsys.readouterr().out


def test_the_fit_reports_whether_it_converged():
    t, Q, temp = _dataset()
    fitted = fit_params(t, temp, Q, T_amb=T_AMB, init=_init(), sigma=SIGMA, n_delay=N_DELAY)
    assert fitted["converged"] is True
    assert 0 < fitted["nfev"] <= 2000

    import controller.update_mpc as U

    original = U._MAX_NFEV
    U._MAX_NFEV = 3
    try:
        starved = fit_params(t, temp, Q, T_amb=T_AMB, init=_init(), sigma=SIGMA, n_delay=N_DELAY)
    finally:
        U._MAX_NFEV = original
    assert starved["converged"] is False
    # The parameters still come back -- that is exactly why the flag is needed.
    assert all(key in starved for key in CONFIG_KEYS)


def test_the_solve_is_conditioned_by_scaling_each_parameter_to_its_own_size():
    """The free parameters differ by orders of magnitude.

    scipy's finite-difference step is eps**0.5 * max(1, |x|), so without
    scaling the small parameters are probed with an absolute step and the large
    ones with a relative one, and the Jacobian columns are not comparable. This
    fits a real cook -- one the solver does not fully converge on either way --
    and requires the scaled solve to reach a materially better answer than the
    unscaled one, rather than merely a different one.
    """
    import controller.update_mpc as U

    mak = pd.read_csv(os.path.join(os.path.dirname(__file__), "fixtures", "mak_cook_2026-08-02.csv"))
    t, temp, Q = mak["time_s"].values, mak["temp_c"].values, mak["Q"].values
    kwargs = dict(T_amb=T_AMB, init=_init(), sigma=SIGMA, n_delay=N_DELAY)

    scaled_rmse, _ = fit_quality(t, temp, Q, fit_params(t, temp, Q, **kwargs), T_amb=T_AMB)

    original = U._solve_scale
    U._solve_scale = lambda init: np.ones(len(U._FREE))
    try:
        unscaled_rmse, _ = fit_quality(t, temp, Q, fit_params(t, temp, Q, **kwargs), T_amb=T_AMB)
    finally:
        U._solve_scale = original

    assert scaled_rmse < unscaled_rmse * 0.99


def test_the_solve_scale_follows_the_caller_rather_than_the_shipped_defaults():
    """A refit starts from an already-fitted model, not from the defaults.

    Scaling against a fixed table would be scaling against the wrong reference
    on exactly that path, so the scale is read from `init`.
    """
    from controller.update_mpc import _FREE, _solve_scale

    init = dict(_init(), C_c=8000.0, h_amb=2.5)
    scale = _solve_scale(init)
    assert dict(zip(_FREE, scale))["C_c"] == 8000.0
    assert dict(zip(_FREE, scale))["h_amb"] == 2.5
    # A starting value with no magnitude to scale by falls back to 1.
    assert dict(zip(_FREE, _solve_scale(dict(_init(), theta=0.0))))["theta"] == 1.0


@pytest.mark.parametrize(
    "label,sigma,n_delay",
    [
        ("no radiative term", 0.0, N_DELAY),
        ("no delay chain", SIGMA, 0),
        ("neither", 0.0, 0),
    ],
)
def test_a_structure_missing_either_term_cannot_explain_this_dataset(label, sigma, n_delay):
    """The negative control for the defect this replaces: the old fitter had no
    delay chain and no radiative term, so it could only absorb them into the
    capacitances.

    Each term is stripped SEPARATELY as well as together, because a single
    combined case only proves that *something* was missing. The threshold sits
    just above the full structure's own error rather than at a round number:
    with both terms present the fit reproduces this dataset exactly, so any
    residual at all is the missing term showing through -- and a bar set high
    enough to catch only the worse of the two cripplings would let the other
    pass, which is what this test previously did.
    """
    t, Q, temp = _dataset()

    full = fit_params(t, temp, Q, T_amb=T_AMB, init=_init(), sigma=SIGMA, n_delay=N_DELAY)
    exact, _ = fit_quality(t, temp, Q, full, T_amb=T_AMB)
    assert exact < 0.01, "the full structure should reproduce its own generating model"

    crippled = fit_params(t, temp, Q, T_amb=T_AMB, init=_init(), sigma=sigma, n_delay=n_delay)
    rmse, _ = fit_quality(t, temp, Q, crippled, T_amb=T_AMB)
    assert rmse > 0.5, f"{label} was absorbed rather than showing up as error"
