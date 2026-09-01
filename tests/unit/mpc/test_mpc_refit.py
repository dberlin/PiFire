"""Offline segmented-fit and promotion-gate contracts."""

import math

import numpy as np
import pytest

from controller import update_mpc
from controller.model_promotion import _IDENTIFIABILITY_FLOOR, evaluate
from controller.mpc_config import DEFAULT_MPC_CONFIG
from controller.mpc_model import simulate_grey_box
from tools.experiments import promotion_signal

#: h_amb matches the shipped default rather than differing from it: `_FREE`
#: holds h_amb and sigma both, so corpus segments whose sigma/h_amb differs
#: from the fitter's represent a target the fitted model cannot reach.
TRUTH = {"C_c": 11000.0, "h_amb": 0.5, "K_Q": 3200.0, "theta": 110.0}

# The fitted free parameters, plus the held ones supplied by the segmented
# fit reference -- see update_mpc._FREE.
FITTED_KEYS = ("C_c", "h_amb", "K_Q", "theta")


def _fit_quality(t, temp, Q, fitted, *, T_amb):
    temperatures = np.asarray(temp, dtype=float)
    try:
        simulated = promotion_signal._sim(fitted, t, Q, float(temperatures[0]))
    except OverflowError:
        return math.inf, math.inf
    if not np.all(np.isfinite(simulated)):
        return math.inf, math.inf
    errors = simulated - temperatures
    return float(np.sqrt(np.mean(errors**2))), float(np.max(np.abs(errors)))


def _identifiability(t, Q, fitted, *, T_amb, T0):
    singular_values = promotion_signal.log_svals(t, Q, fitted, T0)
    return None if singular_values is None else float(singular_values[-1])


def _sim_at(t, Q, fitted, key, value, *, T_amb, T0):
    return promotion_signal._sim(dict(fitted, **{key: value}), t, Q, T0)


def _synthetic_cook(seed=0, noise=0.5, rows=1200):
    """A heat-up then a step down, from a grill that is NOT the default.

    Carries probe noise, without which the fit reaches an RMSE around 1e-12 and
    every error comparison in this file would be a comparison of rounding.
    """
    t = np.arange(0.0, 5.0 * rows, 5.0)
    row = np.arange(rows)
    Q = np.where(row < rows // 3, 1.0, np.where(row < 2 * rows // 3, 0.5, 0.2))
    temp = simulate_grey_box(t, Q, T0=25.0, T_amb=20.0, sigma=1.4e-9, n_delay=DEFAULT_MPC_CONFIG["n_delay"], **TRUTH)
    temp = temp + np.random.default_rng(seed).normal(0.0, noise, size=temp.shape)
    return list(zip(t.tolist(), temp.tolist(), Q.tolist()))




def _heatup_only(rows, seed=0, noise=0.5):
    """A ramp from cold at full fire and nothing else -- no step, no coast.

    The record shape that determines the model LEAST while still being a real
    cook: it pins the steady gain and the early curvature, and says
    progressively less about the chamber's time constant the shorter it is
    cut. That makes its length a dial on identifiability, which is what the
    two bound tests below need.
    """
    t = np.arange(0.0, 5.0 * rows, 5.0)
    Q = np.full_like(t, 1.0)
    temp = simulate_grey_box(t, Q, T0=25.0, T_amb=20.0, sigma=1.4e-9, n_delay=DEFAULT_MPC_CONFIG["n_delay"], **TRUTH)
    temp = temp + np.random.default_rng(seed).normal(0.0, noise, size=temp.shape)
    return t, temp, Q


def _shipped_fit(t, temp, Q):
    """Run the segmented production authority and its explicit-lineage Jacobian."""
    fitted = promotion_signal.shipped_fit(t, temp, Q)
    singular_values = promotion_signal.log_svals(t, Q, fitted, float(temp[0]))
    return fitted, None if singular_values is None else float(singular_values[-1])


def test_constant_load_without_preroll_cannot_identify_delay() -> None:
    t, temp, Q = _heatup_only(180)
    fitted, s_min = _shipped_fit(t, temp, Q)

    assert s_min == pytest.approx(0.0, abs=1e-8)
    assert fitted["theta"] == pytest.approx(float(DEFAULT_MPC_CONFIG["theta"]))


def test_shortest_legacy_real_cook_lacks_120_effective_rows_after_warmup():
    import os

    import pandas as pd

    df = pd.read_csv(os.path.join(os.path.dirname(__file__), "fixtures", "mak_cook_2026-08-02.csv"))
    t = df["time_s"].values.astype(float)
    t = t - t[0]
    n = int(np.searchsorted(t, t[0] + 600.0, side="right"))
    assert n == 120
    temp = df["temp_c"].values.astype(float)[:n]
    Q = df["Q"].values.astype(float)[:n] / 100.0
    t = t[:n]
    job = update_mpc.trace_fit_job(
        t,
        temp,
        Q,
        T_amb=float(DEFAULT_MPC_CONFIG["T_amb"]),
        init={key: float(DEFAULT_MPC_CONFIG[key]) for key in FITTED_KEYS},
        sigma=float(DEFAULT_MPC_CONFIG["sigma"]),
        n_delay=int(DEFAULT_MPC_CONFIG["n_delay"]),
        initial_load=0.0,
    )
    segment = job.segments[0]
    assert len(segment.scored_duration_s) == math.floor(float(t[-1] - t[0]) / 20.0)
    assert np.all(segment.scored_duration_s == 20.0)

    _, s_min = _shipped_fit(t, temp, Q)
    assert s_min is not None and math.isfinite(s_min)


def test_promotion_experiment_normalizes_every_model_input(monkeypatch):
    """Runnable promotion evidence must use the same normalized load as production."""

    def fake_drive(_plant, duty, _warm_duty, _warm_s, seed=0):
        count = len(duty)
        return np.arange(count, dtype=float), np.zeros(count), np.zeros(count)

    monkeypatch.setattr(promotion_signal, "_drive", fake_drive)
    monkeypatch.setattr(promotion_signal, "_VAL_CACHE", {})

    plant = promotion_signal.plant_record("mak", "ramp_coast")
    validation = promotion_signal.validation_runs("mak")
    assert np.min(plant["Q"]) >= 0.0 and np.max(plant["Q"]) <= 1.0
    assert all(np.min(Q) >= 0.0 and np.max(Q) <= 1.0 for _t, Q, _true in validation.values())
    assert np.min(promotion_signal.PROBE_Q) >= 0.0 and np.max(promotion_signal.PROBE_Q) <= 1.0
    assert np.min(promotion_signal.flat_synthetic(0.05)["Q"]) >= 0.0
    assert np.max(promotion_signal.flat_synthetic(0.05)["Q"]) <= 1.0
    assert np.min(promotion_signal.real_cook()["Q"]) >= 0.0
    assert np.max(promotion_signal.real_cook()["Q"]) <= 1.0


def test_the_two_statistics_rank_the_same_pair_of_records_in_opposite_orders():
    """The whole claim the floor rests on: fit quality ranks records backwards.

    A flat cook and a cook with a step in it, scored both ways. The flat cook
    -- which determines nothing beyond the steady gain -- fits its own record
    BETTER, because there is less in it for a model to disagree with, so the
    statistic the gate used to decide by prefers precisely the record that
    should never promote anything. `identifiability` puts them the other way
    round, and puts them either side of the floor: the empty record is refused
    and the informative one is free to be judged on its merits.

    This is the property worth defending. That the measured temperatures cannot
    reach `identifiability` is guaranteed by its signature rather than by a
    test -- the series is not one of its arguments. What a test can catch is a
    refactor that reintroduces a dependence on the residual by some other
    route, and any such dependence lands here: a record informative enough to
    promote a model is one whose temperature moves a long way, so a statistic
    that shrinks with the spread of the data deflates exactly the records that
    should clear the floor, and this pair stops straddling it.
    """
    flat_rng = np.random.default_rng(0)
    flat_t = np.arange(400, dtype=float) * 5.0
    flat_temp = 100.0 + flat_rng.normal(0.0, 0.05, size=400)
    flat_Q = np.full(400, 0.5)
    flat_fit, flat_s_min = _shipped_fit(flat_t, flat_temp, flat_Q)
    flat_rmse, _ = _fit_quality(flat_t, flat_temp, flat_Q, flat_fit, T_amb=20.0)

    rows = _synthetic_cook()
    step_t = np.array([r[0] for r in rows])
    step_temp = np.array([r[1] for r in rows])
    step_Q = np.array([r[2] for r in rows])
    step_fit, step_s_min = _shipped_fit(step_t, step_temp, step_Q)
    step_rmse, _ = _fit_quality(step_t, step_temp, step_Q, step_fit, T_amb=20.0)

    # Ranked by fit quality, the record that determines nothing wins.
    assert flat_rmse < step_rmse
    # Ranked by identifiability, it loses -- the opposite order, on the same pair.
    assert flat_s_min < step_s_min
    # And the disagreement is the whole of the decision: the floor falls between
    # them, so the two statistics do not merely differ, they decide differently.
    assert flat_s_min < _IDENTIFIABILITY_FLOOR <= step_s_min


def test_a_fitted_point_with_no_logarithm_is_unmeasurable_and_is_refused():
    """A free parameter that is not a positive scale has no e-fold to perturb.

    `identifiability` says None rather than guessing, and the gate treats that
    as it treats a low score: the record has not been shown to determine
    anything, so nothing may be promoted on it. All three cases here are the
    same guard reached by its three doors -- zero, negative, and not a number.
    The two ways the SIMULATION can fail are separate branches: the raised
    OverflowError has its own test below, and the quiet non-finite result has
    none, because no parameter set found so far reaches it within a probe short
    enough to test. It is kept because a diverging simulation that returns NaN
    without raising is a real shape, pinned for the fitter itself in
    tests/unit/mpc/test_mpc_calibration.py.
    """
    t, temp, Q = _heatup_only(240)
    fitted, _ = _shipped_fit(t, temp, Q)

    assert _identifiability(t, Q, dict(fitted, theta=0.0), T_amb=20.0, T0=float(temp[0])) is None
    assert _identifiability(t, Q, dict(fitted, K_Q=-1.0), T_amb=20.0, T0=float(temp[0])) is None
    assert _identifiability(t, Q, dict(fitted, C_c=float("nan")), T_amb=20.0, T0=float(temp[0])) is None

    verdict = evaluate(
        dict(TRUTH, T_amb=20.0, sigma=1.4e-9, n_delay=4),
        dict(TRUTH, T_amb=20.0, sigma=1.4e-9, n_delay=4, C_c=2000.0),
        candidate_rmse=1.0,
        incumbent_rmse=5.0,
        identifiability=None,
    )
    assert verdict.accepted is False
    assert "identifiability" in verdict.reason


def test_a_simulation_that_overflows_is_unmeasurable_rather_than_an_exception():
    """A diverging off-path simulation is refused without escaping its worker.

    The parameter set below is checked to raise before the answer is asserted,
    so this reaches the raising branch rather than passing through the
    non-positive guard or the NaN guard on its way.
    """
    t, temp, Q = _heatup_only(240)
    runaway = {"C_c": 1e-9, "h_amb": 0.5, "K_Q": 1e12, "T_amb": 20.0, "sigma": 1e3, "theta": 110.0, "n_delay": 8}

    # Every free parameter is a positive finite scale, so the first guard does
    # not fire and the simulation is actually attempted.
    assert all(runaway[k] > 0.0 and math.isfinite(runaway[k]) for k in update_mpc._FREE)
    with pytest.raises(OverflowError):
        _sim_at(t, Q, runaway, "K_Q", 1e12 * math.e, T_amb=20.0, T0=float(temp[0]))

    assert _identifiability(t, Q, runaway, T_amb=20.0, T0=float(temp[0])) is None


def test_the_identifiability_argument_is_required_of_every_caller():
    """Omitting it is a TypeError, not a silent return to the old behaviour.

    This gate has already lost one safety property that was never written
    down: flat cooks used to be refused because the two-state fit ran into the
    C_c ceiling, and log-space fitting closed that escape and took the
    accidental refusal with it. A default here would be the same shape of
    accident -- a caller that never measured whether its cook determined
    anything, passing anyway.
    """
    with pytest.raises(TypeError):
        evaluate(
            dict(TRUTH, T_amb=20.0, sigma=1.4e-9, n_delay=4),
            None,
            candidate_rmse=1.0,
            incumbent_rmse=None,
        )


def test_an_undetermined_first_fit_cannot_slip_through_on_having_no_incumbent():
    """The floor is checked before the no-incumbent shortcut, not after it.

    A grill with nothing to compare against is where an undetermined model
    does the most damage -- it is adopted on the strength of being the only
    candidate. `Verdict(True, "no incumbent")` must not be reachable from a
    record that determined nothing.
    """
    verdict = evaluate(
        dict(TRUTH, T_amb=20.0, sigma=1.4e-9, n_delay=4),
        None,
        candidate_rmse=1.0,
        incumbent_rmse=None,
        identifiability=0.4,
    )
    assert verdict.accepted is False
    assert "does not determine the model" in verdict.reason


