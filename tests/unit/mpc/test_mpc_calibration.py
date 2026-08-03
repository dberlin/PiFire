"""The fitter must recover parameters through the same dynamics the MPC uses."""

import os

import numpy as np
import pandas as pd
import pytest

from controller.model_promotion import T_FLOOR_C, T_HAZARD_C, effective_tau, longest_braking_distance
from controller.mpc_model import simulate_grey_box
from controller.update_mpc import CONFIG_KEYS, fit_params, fit_quality

#: The grill these tests fit: an order of magnitude slower than the shipped
#: default, nearly ten times its gain, and twice its dead time.
#:
#: h_amb matches `_init()` rather than differing from it, because `_FREE` holds
#: h_amb and sigma both -- so what a fit can express is C_c, K_Q and theta
#: against THAT pair. It used to be 2.7, which put sigma/h_amb, the share of
#: the chamber's loss that is radiative, 5.4x away from the one the fitter
#: holds; the fit then absorbed the difference into C_c and recovered the time
#: constant 2.1x wrong. That is a real limit of the parameterization and it is
#: recorded as such in this task's report, not papered over here -- but a
#: dataset outside what the fitter can represent is the wrong instrument for
#: asking whether the fitter recovers what it is given.
TRUTH = dict(C_f=9.0, C_c=11000.0, h_fc=1.3, h_amb=0.5, K_Q=32.0, theta=110.0)
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
    """The shipped starting point, exactly as controller/mpc.py's _REFIT_INIT."""
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


def test_a_held_parameter_is_returned_exactly_as_it_was_passed():
    """Whatever `_FREE` does not name comes back byte-identical.

    This used to assert it of sigma alone. sigma is still held, but it is no
    longer the only thing pinning a scale -- h_amb holds the chamber's, see
    `_FREE` -- so the property is asserted of every parameter `_FREE` leaves
    out. A later change to that set then cannot leave this checking something
    it no longer covers.

    Byte-identical, not merely close: a caller that round-trips a model through
    this fitter must get the same held values back, or a parameter that was
    supposed to be pinning a gauge has drifted.
    """
    from controller.update_mpc import _FIT_KEYS, _FREE

    t, Q, temp = _dataset()
    held = [key for key in _FIT_KEYS if key not in _FREE]
    assert {"C_f", "h_fc", "h_amb"} <= set(held)
    for sigma in (0.0, SIGMA, 2.7182818e-9):
        for scale in (1.0, 0.5, 2.7182818):
            init = {k: v * scale for k, v in _init().items()}
            fitted = fit_params(t, temp, Q, T_amb=T_AMB, init=init, sigma=sigma, n_delay=N_DELAY)
            supplied = dict(init, sigma=sigma)
            for key in held:
                assert fitted[key] == supplied[key]


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
    # Radiation shortens it, and more so the hotter the chamber gets. The
    # radiation-free C_c/h_amb is written out rather than imported: it no
    # longer names anything the module computes, and it is here only as the
    # value the effective tau has to stay under.
    assert effective_tau(payload, T_HAZARD_C) < effective_tau(payload, T_FLOOR_C) < payload["C_c"] / payload["h_amb"]


def test_the_cli_reports_the_radiation_aware_time_constant(tmp_path, capsys, monkeypatch):
    """The kept CLI fix, exercised through `main()` rather than around it.

    The utility reports two different things and must not confuse them: the
    effective time constant at each end of the operating range, which is what
    the chamber's response is, and the braking distance, which is what the
    horizon has to cover. This checks the reporting, not the trigger.
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
    assert f"Chamber time constant: {payload['C_c'] / payload['h_amb']:.0f} s" not in out
    # ...and the braking distance is reported as its own quantity, not folded
    # into the time constant the line above prints.
    assert f"Braking distance after a fuel cut: up to {longest_braking_distance(payload):.0f} s" in out


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


def test_the_reported_sigma_is_the_one_the_fit_actually_simulated():
    """The returned model must be the model that produced the returned error.

    `fit_params` reports `sigma` from the value it was handed while the solve
    uses it separately, so nothing else in this file notices if the two come
    apart -- every other test either reads the reported value or scores the
    reported model, and both agree with each other while disagreeing with what
    was simulated. Re-deriving the residual from the REPORTED dict and matching
    it against the solver's own final cost closes that: they can only agree if
    the model handed back is the model that was fitted.
    """
    t, Q, temp = _dataset()
    fitted = fit_params(t, temp, Q, T_amb=T_AMB, init=_init(), sigma=SIGMA, n_delay=N_DELAY)

    reported = simulate_grey_box(
        t,
        Q,
        T_amb=T_AMB,
        T0=float(temp[0]),
        **{k: fitted[k] for k in ("C_f", "C_c", "h_fc", "h_amb", "K_Q", "sigma", "theta", "n_delay")},
    )
    # This dataset is generated from the model, so a fit that simulated what it
    # reports reproduces it to numerical precision. A solve that used a
    # different sigma than it reported would have optimised a different
    # objective, and the reported model cannot then be this good.
    assert np.sqrt(np.mean((reported - temp) ** 2)) < 1e-3

    # And the radiative term must genuinely be in play, or the check above
    # would pass for any sigma at all.
    without = simulate_grey_box(
        t,
        Q,
        T_amb=T_AMB,
        T0=float(temp[0]),
        **{k: fitted[k] for k in ("C_f", "C_c", "h_fc", "h_amb", "K_Q", "theta", "n_delay")},
        sigma=0.0,
    )
    assert np.max(np.abs(without - reported)) > 1.0


def test_the_json_mode_carries_the_convergence_verdict(tmp_path, capsys, monkeypatch):
    """--json is the mode something else consumes; it must not be the quiet one."""
    import json

    import controller.update_mpc as U

    t, Q, temp = _dataset()
    csv = tmp_path / "cook.csv"
    csv.write_text("time_s,temp_c,Q\n" + "".join(f"{a},{b},{c}\n" for a, b, c in zip(t, temp, Q)))
    monkeypatch.setattr("sys.argv", ["update_mpc", str(csv), "--t-amb", str(T_AMB), "--json"])

    monkeypatch.setattr(U, "_MAX_NFEV", 3)
    U.main()
    captured = capsys.readouterr()
    payload = json.loads(captured.out)  # stdout stays parseable JSON
    assert payload["fit"]["converged"] is False
    assert "ran out of evaluations" in captured.err  # ...and a human is told, on stderr
    assert all(key in payload["config"] for key in CONFIG_KEYS)

    monkeypatch.setattr(U, "_MAX_NFEV", 2000)
    U.main()
    captured = capsys.readouterr()
    assert json.loads(captured.out)["fit"]["converged"] is True
    assert "ran out of evaluations" not in captured.err


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


def test_a_fit_to_a_real_cook_lands_where_the_promotion_policy_can_accept_it():
    """A fit outside PROMOTION_BOUNDS is refused however well it describes the log.

    The firepot is quasi-static over most of a cook, so the chamber's response
    depends only on C_c/h_amb, K_Q/h_amb and sigma/h_amb -- and freeing C_c,
    h_amb and K_Q together leaves a direction along which all three grow while
    the first two ratios hold and the third, with sigma held, shrinks to
    nothing. Moving along it deletes the radiative term for a small residual
    gain, and the solver takes the trade: on this cook, with h_amb free, it
    converges at C_c 2.6e7 and h_amb 7.4e3 against bounds of 1e6 and 1e3.

    `_FREE` holds h_amb to pin that direction. This asserts the outcome on the
    real cook rather than the reasoning, and the counter-case below is what
    shows the assertion can fail.
    """
    import controller.update_mpc as U
    from controller.model_promotion import PROMOTION_BOUNDS

    mak = pd.read_csv(os.path.join(os.path.dirname(__file__), "fixtures", "mak_cook_2026-08-02.csv"))
    t, temp, Q = mak["time_s"].values, mak["temp_c"].values, mak["Q"].values
    kwargs = dict(T_amb=T_AMB, init=_init(), sigma=SIGMA, n_delay=N_DELAY)

    def outside(fitted):
        return [k for k, (lo, hi) in PROMOTION_BOUNDS.items() if k in fitted and not (lo <= fitted[k] <= hi)]

    assert outside(fit_params(t, temp, Q, **kwargs)) == []

    original = U._FREE
    U._FREE = original + ("h_amb",)
    try:
        assert "h_amb" in outside(fit_params(t, temp, Q, **kwargs))
    finally:
        U._FREE = original


def test_the_solve_is_conditioned_by_scaling_each_parameter_to_its_own_size():
    """The free parameters differ by orders of magnitude.

    scipy's finite-difference step is eps**0.5 * max(1, |x|), so above 1 it is
    relative and each parameter is probed on the scale of its own value --
    which leaves the Jacobian columns sized by those values rather than
    comparable to each other. Dividing each by its own starting magnitude puts
    them all at exactly 1, so one trust-region radius means the same relative
    move in every direction.

    This asserts that mechanism, not an outcome. It used to require a
    materially better RMSE on the real MAK cook, and with the previous, larger
    `_FREE` it got one: that solve exhausted its evaluation budget, and where
    the solver stopped depended on how it was conditioned. The narrowed set
    converges in under a hundred evaluations either way, to six matching
    figures -- so that assertion no longer separates a scaled solve from an
    unscaled one and would pass whatever `_solve_scale` returned.
    """
    import controller.update_mpc as U

    init = _init()
    scale = U._solve_scale(init)
    assert len(scale) == len(U._FREE)
    for magnitude, key in zip(scale, U._FREE):
        assert magnitude == pytest.approx(abs(init[key]))

    # The spread the scaling exists to remove has to be in the shipped starting
    # point, or the mechanism is being asserted against a case it never meets.
    magnitudes = [abs(init[key]) for key in U._FREE]
    assert max(magnitudes) / min(magnitudes) > 10.0
    # ...and it is gone afterwards, exactly rather than approximately.
    assert [m / s for m, s in zip(magnitudes, scale)] == [1.0] * len(U._FREE)


def test_the_solve_scale_follows_the_caller_rather_than_the_shipped_defaults():
    """A refit starts from an already-fitted model, not from the defaults.

    Scaling against a fixed table would be scaling against the wrong reference
    on exactly that path, so the scale is read from `init`.
    """
    from controller.update_mpc import _FREE, _solve_scale

    init = dict(_init(), C_c=8000.0, K_Q=2.5)
    scale = _solve_scale(init)
    assert dict(zip(_FREE, scale))["C_c"] == 8000.0
    assert dict(zip(_FREE, scale))["K_Q"] == 2.5
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
    from controller.update_mpc import _FREE

    # Zeroing sigma only removes the radiative term while `_FREE` holds sigma.
    # If it were ever fitted, this arm would be moving the starting point and
    # crippling nothing.
    assert "sigma" not in _FREE

    t, Q, temp = _dataset()

    full = fit_params(t, temp, Q, T_amb=T_AMB, init=_init(), sigma=SIGMA, n_delay=N_DELAY)
    exact, _ = fit_quality(t, temp, Q, full, T_amb=T_AMB)
    assert exact < 0.01, "the full structure should reproduce its own generating model"

    crippled = fit_params(t, temp, Q, T_amb=T_AMB, init=_init(), sigma=sigma, n_delay=n_delay)
    rmse, _ = fit_quality(t, temp, Q, crippled, T_amb=T_AMB)
    assert rmse > 0.5, f"{label} was absorbed rather than showing up as error"
