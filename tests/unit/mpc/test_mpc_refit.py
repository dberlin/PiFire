"""A finished cook improves the model, or is refused with a reason."""

import json
import math
import time
from types import SimpleNamespace

import numpy as np
import pytest

from common.controller_model_state import ControllerModelStore
from controller import update_mpc
from controller.model_learning.grey_runtime import _HISTORY_MAX, _REFIT_INIT, GreyLearningRuntime
from controller.model_promotion import _IDENTIFIABILITY_FLOOR, PROMOTION_BOUNDS, evaluate
from controller.mpc import Controller
from controller.mpc_config import DEFAULT_MPC_CONFIG
from controller.mpc_model import simulate_grey_box
from tools.experiments import promotion_signal

CYCLE = {"u_min": 0.1, "u_max": 0.9}
#: h_amb matches the shipped default rather than differing from it: `_FREE`
#: holds h_amb and sigma both, so a cook whose sigma/h_amb differs from the
#: fitter's is one the fitter cannot represent, and the refit would be judged
#: on a target outside its reach. Everything else here is far from the default.
TRUTH = {"C_c": 11000.0, "h_amb": 0.5, "K_Q": 3200.0, "theta": 110.0}

# The fitted free parameters, plus the held ones a refit's starting point
# supplies alongside them -- see update_mpc._FREE.
FITTED_KEYS = ("C_c", "h_amb", "K_Q", "theta")


def _synthetic_cook(seed=0, noise=0.5, rows=1200):
    """A heat-up then a step down, from a grill that is NOT the default.

    Carries probe noise, without which the fit reaches an RMSE around 1e-12 and
    every error comparison in this file would be a comparison of rounding.
    """
    t = np.arange(0.0, 5.0 * rows, 5.0)
    Q = np.where(t < 2.5 * rows, 1.0, 0.2)
    temp = simulate_grey_box(t, Q, T0=25.0, T_amb=20.0, sigma=1.4e-9, n_delay=DEFAULT_MPC_CONFIG["n_delay"], **TRUTH)
    temp = temp + np.random.default_rng(seed).normal(0.0, noise, size=temp.shape)
    return list(zip(t.tolist(), temp.tolist(), Q.tolist()))


def _c():
    return Controller(dict(DEFAULT_MPC_CONFIG, policy="nlp"), "C", dict(CYCLE))


@pytest.fixture
def model_store():
    """The real store over an in-memory key, so its validator is the one that
    judges what a snapshot may carry."""
    written = {}

    def read(key):
        if key not in written:
            raise TypeError("no such key")  # what read_generic_key does for an absent key
        return written[key]

    return ControllerModelStore(reader=read, writer=written.__setitem__)


@pytest.fixture
def fits(monkeypatch):
    """Every fit_params call this test makes, as {"init":..., "out":...}.

    refit_from_cook imports fit_params inside the call, so patching the module
    attribute intercepts it without the controller knowing.
    """
    real = update_mpc.fit_params
    calls = []

    def spy(*args, **kwargs):
        out = real(*args, **kwargs)
        calls.append({"init": dict(kwargs["init"]), "out": dict(out), "rows": len(args[0])})
        return out

    monkeypatch.setattr(update_mpc, "fit_params", spy)
    return calls


@pytest.fixture(scope="module")
def accepted_refit():
    """One controller refit from one synthetic cook, and what that produced.

    The refit is the expensive thing in this file: a least-squares that
    re-simulates the whole cook on every evaluation. Several tests ask
    different questions of the same one, so it runs once.

    Handed back as values rather than as the live controller, so no test can
    answer its question out of another's mutation.
    """
    c = _c()
    before_pair = c.active_control_pair
    before = c.cfg["C_c"] / c.cfg["h_amb"]
    verdict = c.refit_from_cook(_synthetic_cook())
    result = SimpleNamespace(
        verdict=verdict,
        tau_before=before,
        tau_after=c.cfg["C_c"] / c.cfg["h_amb"],
        snapshot=c.get_model_snapshot(),
        owner_changed=c.active_control_pair is not before_pair,
        owner_valid=c._pair_factory.validate(c.active_control_pair),
        rollback_retained=c.rollback_control_pair is before_pair,
    )
    c.close()
    return result


def test_an_accepted_refit_installs_the_complete_validated_candidate_owner(accepted_refit):
    assert accepted_refit.owner_changed is True
    assert accepted_refit.owner_valid is True
    assert accepted_refit.rollback_retained is True


def test_a_refit_moves_the_model_toward_the_grill_that_produced_the_cook(accepted_refit):
    assert accepted_refit.verdict.accepted is True
    truth = TRUTH["C_c"] / TRUTH["h_amb"]
    assert abs(accepted_refit.tau_after - truth) < abs(accepted_refit.tau_before - truth)


def test_a_refit_records_the_band_it_learned_in(accepted_refit):
    lo, hi = accepted_refit.snapshot["active"]["metadata"]["band_c"]
    assert lo < hi
    assert hi > 200.0  # the synthetic cook is a high-temperature run


def test_too_few_samples_is_refused_without_fitting():
    c = _c()
    v = c.refit_from_cook([(0.0, 20.0, 50.0), (5.0, 21.0, 50.0)])
    assert v.accepted is False
    assert "sample" in v.reason.lower()
    assert c.get_model_snapshot()["identification"] == {"status": "unidentified"}


def test_a_refit_without_explicit_history_uses_completed_frames_not_solver_samples():
    c = _c()
    c.set_target(110.0)
    for _ in range(3):
        c.update(100.0)

    verdict = c.refit_from_cook()

    assert verdict.accepted is False
    assert "0 samples" in verdict.reason
    assert len(c.cook_history()) == 3


def test_an_uninformative_cook_is_refused_because_it_determines_nothing():
    """A record that pins nothing down cannot promote a model, however well it fits.

    A flat cook -- constant Q, constant temperature -- carries exactly one
    piece of information, the steady gain K_Q/h_amb. The candidate learns it
    and describes the record essentially perfectly; every other parameter it
    reports is whatever the solve happened to leave, here a dead time near the
    bottom of its range against an incumbent's 110 s, which is the "brakes
    late" shape the whole promotion policy exists to refuse.

    THE REFUSAL IS NOT ABOUT THE ERROR, and this test asserts that explicitly
    rather than leaving it to be inferred: the candidate's RMSE is the best in
    this file and beats the incumbent by a mile, so every error comparison in
    the gate passes it. What refuses it is that the record leaves a direction
    in (log K_Q, log C_c, log theta) free to move by a factor of e without the
    prediction moving -- so the model that came out is the starting point, not
    the grill. No residual statistic can see that, which is why the gate now
    asks a question that is not one.
    """
    c = _c()
    assert c.refit_from_cook(_synthetic_cook()).accepted is True
    learned_theta = c.cfg["theta"]

    # Carrying a little sensor noise, so the record is uninformative on its
    # merits rather than by fitting exactly. A noiseless flat cook scores an
    # in-sample RMSE of exactly zero, which `evaluate` refuses under its
    # non-positive-RMSE guard -- a refusal about arithmetic, not about whether
    # the record determines anything. This cook carries the same information,
    # none, and reaches the judgement the gate actually makes.
    rng = np.random.default_rng(0)
    flat = [(float(i * 5), 100.0 + float(rng.normal(0.0, 0.05)), 50.0) for i in range(400)]
    verdict = c.refit_from_cook(flat)
    assert verdict.accepted is False
    assert "does not determine the model" in verdict.reason

    # The model driving the grill is the one the informative cook produced. The
    # dead time this record would have collapsed is still standing.
    assert c.cfg["theta"] == learned_theta

    # And the refusal is not the error comparison wearing a different hat. The
    # candidate this record produces fits it far better than the incumbent
    # does, so every RMSE bar in the gate would wave it through.
    t = np.array([r[0] for r in flat])
    temp = np.array([r[1] for r in flat])
    Q = np.array([r[2] for r in flat])
    T_amb = float(c.cfg["T_amb"])
    fitted = update_mpc.fit_params(
        t, temp, Q, T_amb=T_amb, init=dict(_REFIT_INIT), sigma=float(c.cfg["sigma"]), n_delay=int(c.cfg["n_delay"])
    )
    cand_rmse, _ = update_mpc.fit_quality(t, temp, Q, fitted, T_amb=T_amb)
    incumbent = {k: float(c.cfg[k]) for k in GreyLearningRuntime.MODEL_PARAM_KEYS}
    inc_rmse, _ = update_mpc.fit_quality(t, temp, Q, incumbent, T_amb=T_amb)
    assert cand_rmse < 0.5 * inc_rmse
    # Handed an identifiability that clears the floor and nothing else changed,
    # the very same candidate is adopted -- so the floor is the whole of the
    # difference, and the dead time it was protecting does collapse without it.
    permitted = evaluate(
        fitted,
        incumbent,
        candidate_rmse=cand_rmse,
        incumbent_rmse=inc_rmse,
        identifiability=2.0,
    )
    assert permitted.accepted is True
    assert fitted["theta"] < 0.5 * learned_theta


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
    """The refit controller/mpc.py performs, and the two numbers it judges on."""
    fitted = update_mpc.fit_params(
        t,
        temp,
        Q,
        T_amb=20.0,
        init=dict(_REFIT_INIT),
        sigma=float(DEFAULT_MPC_CONFIG["sigma"]),
        n_delay=int(DEFAULT_MPC_CONFIG["n_delay"]),
    )
    s_min = update_mpc.identifiability(t, Q, fitted, T_amb=20.0, T0=float(temp[0]))
    return fitted, s_min


def test_the_floor_sits_above_the_weakest_record_that_determines_nothing():
    """The floor may not fall to the bottom of the interval that brackets it.

    `generic/steady_hold/3600s` scores 0.261203 -- the strongest record in the
    measured population that still determines nothing, and therefore the
    lowest the floor could conceivably be set. This record sits in the gap
    between that number and the shipped floor, and it is refused. A floor
    dropped to the bare lower bound would admit it, and with it the models the
    measurement recorded at 200.5 C worse than the incumbent.

    THE RECORD HERE IS SIMULATED WHILE 0.261203 CAME FROM A PLANT-GENERATED
    ONE, which is a real limitation and not a convenience. No genuine record
    lands in this gap: the only real cook available scores 1.098 at 600 s, and
    every shorter truncation of it falls below `_REFIT_MIN_SAMPLES`, which
    controller/mpc.py refuses before the gate is reached -- so the production
    path cannot produce a real record in the gap to test with. What keeps this
    honest is that the score is not asserted as a literal: the record is fitted
    by the shipped fitter here, and its identifiability measured, so the number
    below is produced rather than quoted.
    """
    t, temp, Q = _heatup_only(180)
    fitted, s_min = _shipped_fit(t, temp, Q)

    # In the gap: above the highest score a record that determines nothing
    # reached, below the floor.
    assert 0.261203 < s_min < _IDENTIFIABILITY_FLOOR

    c = _c()
    verdict = c.refit_from_cook(list(zip(t.tolist(), temp.tolist(), Q.tolist())))
    assert verdict.accepted is False
    assert "does not determine the model" in verdict.reason
    # Refused for its identifiability and not for its fit: this record produces
    # a good model, which is exactly why a floor set too high is expensive.
    assert fitted["theta"] == pytest.approx(TRUTH["theta"], rel=0.05)


def test_the_floor_still_admits_the_shortest_real_cook_the_controller_will_fit():
    """The floor may not rise far enough to refuse real cooks.

    The commercial guard on the other side. The only real record there is --
    the 450 F MAK cook that overshot -- scores 1.098188 when cut to the
    shortest length `_REFIT_MIN_SAMPLES` allows to be fitted at all, and it
    clears the floor. A floor above that number refuses every genuine cook
    this feature would ever see, and the learning never promotes anything.
    """
    import os

    import pandas as pd

    df = pd.read_csv(os.path.join(os.path.dirname(__file__), "fixtures", "mak_cook_2026-08-02.csv"))
    t = df["time_s"].values.astype(float)
    t = t - t[0]
    # 600 s at the 5 s log cadence is exactly _REFIT_MIN_SAMPLES samples, the
    # shortest record controller/mpc.py will refit at all.
    n = int(np.searchsorted(t, t[0] + 600.0, side="right"))
    assert n == 120
    t, temp, Q = t[:n], df["temp_c"].values.astype(float)[:n], df["Q"].values.astype(float)[:n] / 100.0

    _, s_min = _shipped_fit(t, temp, Q)
    assert s_min == pytest.approx(1.098188, abs=1e-5)
    assert s_min >= _IDENTIFIABILITY_FLOOR


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
    flat_rmse, _ = update_mpc.fit_quality(flat_t, flat_temp, flat_Q, flat_fit, T_amb=20.0)

    rows = _synthetic_cook()
    step_t = np.array([r[0] for r in rows])
    step_temp = np.array([r[1] for r in rows])
    step_Q = np.array([r[2] for r in rows])
    step_fit, step_s_min = _shipped_fit(step_t, step_temp, step_Q)
    step_rmse, _ = update_mpc.fit_quality(step_t, step_temp, step_Q, step_fit, T_amb=20.0)

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

    assert update_mpc.identifiability(t, Q, dict(fitted, theta=0.0), T_amb=20.0, T0=float(temp[0])) is None
    assert update_mpc.identifiability(t, Q, dict(fitted, K_Q=-1.0), T_amb=20.0, T0=float(temp[0])) is None
    assert update_mpc.identifiability(t, Q, dict(fitted, C_c=float("nan")), T_amb=20.0, T0=float(temp[0])) is None

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
    """The other way the measurement can fail, and the one that would escape.

    A parameter set can drive the chamber integration away, and where the
    radiative term goes past what a Python float holds it RAISES rather than
    returning a number. `Controller.refit_from_cook` runs this inside a `try`
    that catches ValueError and FloatingPointError only, so an OverflowError
    getting out of here would get out of `refit_from_cook` -- and that runs in
    HoldMode's teardown, where the next thing owed to the grill is the
    cool-down fan. It is answered as `None` instead, which the gate already
    knows how to refuse.

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
        update_mpc._sim_at(t, Q, runaway, "K_Q", 1e12 * math.e, T_amb=20.0, T0=float(temp[0]))

    assert update_mpc.identifiability(t, Q, runaway, T_amb=20.0, T0=float(temp[0])) is None


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


@pytest.mark.slow
def test_the_longest_cook_stays_inside_the_teardown_budget(fits):
    """The refit runs synchronously in HoldMode.teardown, so its cost is time
    the shutdown fan's cool-down starts late.

    Marked slow, which is where a wall-clock budget belongs: it fits a full
    12-hour history to find out, and the answer is a reading of the machine it
    ran on, so a loaded default run fails it on timing alone rather than on
    anything about the code. `pytest -m slow` is where it gets asked.

    The budget is 30 s. The shipped `shutdown_duration` is 240 s, so a refit
    inside this bound delays the cool-down by at most an eighth of itself,
    with the auger and igniter already off.

    This is the worst case there is: `_HISTORY_MAX` rows at the shipped
    control period is a full 12-hour history, and the companion test below
    holds the history to that length, so no cook can present the teardown path
    with more work than this one does.
    """
    t = np.arange(0.0, 5.0 * _HISTORY_MAX, 5.0)
    Q = np.where((t // 1800) % 2 == 0, 1.0, 0.2)
    temp = simulate_grey_box(t, Q, T0=25.0, T_amb=20.0, sigma=1.4e-9, n_delay=DEFAULT_MPC_CONFIG["n_delay"], **TRUTH)
    history = list(zip(t.tolist(), temp.tolist(), Q.tolist()))
    assert len(history) == _HISTORY_MAX
    c = _c()
    t0 = time.perf_counter()
    c.refit_from_cook(history)
    assert time.perf_counter() - t0 < 30.0
    # The whole cook was fit: thinning it first would cost accuracy in
    # C_c/h_amb and, since simulate_grey_box sub-steps to max_dt regardless,
    # would not even buy the time it appears to.
    assert fits[0]["rows"] == _HISTORY_MAX


def test_the_cook_a_refit_can_be_handed_is_bounded_by_the_history():
    """What keeps the timed budget above meaningful: the live history is a
    bounded deque, so the longest cook a teardown refit can ever see is the
    one that test measures."""
    c = _c()
    c.active_control_pair.core.history.extend(_synthetic_cook(rows=_HISTORY_MAX + 500))
    assert len(c.cook_history()) == _HISTORY_MAX


# ---- the fit's starting point is fixed, and stays fixed across cooks ----


@pytest.fixture(scope="module")
def four_cooks_in_a_row():
    """Four cooks refit in sequence, each accepted result left in for the next.

    Two tests below ask different things of this same run -- where each fit
    started, and where the parameters ended up -- and it is four
    least-squares solves, so it happens once. The spy is installed by hand
    rather than through `monkeypatch`, which is function-scoped and cannot
    reach a fixture shared across tests.
    """
    real = update_mpc.fit_params
    calls = []

    def spy(*args, **kwargs):
        out = real(*args, **kwargs)
        calls.append({"init": dict(kwargs["init"]), "out": dict(out), "rows": len(args[0])})
        return out

    update_mpc.fit_params = spy
    try:
        c = _c()
        shipped = {key: c.cfg[key] for key in FITTED_KEYS}
        verdicts = [
            # Distinct excitation per cook, so each is genuinely new evidence
            # and the loop actually feeds results forward rather than idling.
            c.refit_from_cook(_synthetic_cook(seed=seed, noise=0.4 + 0.3 * seed))
            for seed in range(4)
        ]
    finally:
        update_mpc.fit_params = real
    return SimpleNamespace(fits=calls, verdicts=verdicts, shipped=shipped, cfg=dict(c.cfg))


def test_every_refit_starts_from_the_same_fixed_reference(four_cooks_in_a_row):
    """Several cooks in a row, each accepted result left in place for the next.

    What this pins is that the starting point is the same every time, not that
    the finished parameters happen to agree: seeded from the running model
    they would be a function of every cook before them, and the fit's start is
    the only place that shows. One refit cannot show it either -- the first
    one begins at the shipped model whichever way the code is written -- so
    the fixture drives the loop and this reads the starting point each time
    round.
    """
    run = four_cooks_in_a_row
    assert len(run.fits) == 4
    # Without this the later fits would still be starting from the shipped
    # model by accident, and reading their starting point would prove nothing.
    assert run.verdicts[0].accepted is True
    assert {key: run.cfg[key] for key in FITTED_KEYS} != run.shipped
    for call in run.fits:
        assert call["init"] == _REFIT_INIT


def test_the_fixed_reference_is_the_shipped_model_and_is_not_written_through():
    expected = {key: float(DEFAULT_MPC_CONFIG[key]) for key in FITTED_KEYS}
    assert _REFIT_INIT == expected
    before = dict(DEFAULT_MPC_CONFIG)
    c = _c()
    assert c.refit_from_cook(_synthetic_cook()).accepted is True
    assert _REFIT_INIT == expected
    assert DEFAULT_MPC_CONFIG == before


def test_repeated_refits_leave_the_model_inside_the_promotion_bounds(four_cooks_in_a_row):
    """The parameters must not ratchet out of the range a model may live in."""
    run = four_cooks_in_a_row
    for key, (lo, hi) in PROMOTION_BOUNDS.items():
        assert lo <= float(run.cfg[key]) <= hi, key
    first_tau = run.fits[0]["out"]["C_c"] / run.fits[0]["out"]["h_amb"]
    last_tau = run.fits[-1]["out"]["C_c"] / run.fits[-1]["out"]["h_amb"]
    assert 0.5 < last_tau / first_tau < 2.0


def test_the_model_judged_is_the_one_driving_the_grill():
    """The candidate starts from a fixed reference; what it has to beat is the
    model actually running, since replacing THAT is the question."""
    cook = _synthetic_cook()
    already_right = _c()
    already_right.cfg.update(TRUTH)
    assert already_right.refit_from_cook(cook).accepted is False
    from_the_shipped_model = _c()
    assert from_the_shipped_model.refit_from_cook(cook).accepted is True


# ---- convergence vetoes, and never warrants ----


def test_a_solve_that_ran_out_of_evaluations_is_refused(monkeypatch):
    monkeypatch.setattr(update_mpc, "_MAX_NFEV", 1)
    c = _c()
    v = c.refit_from_cook(_synthetic_cook())
    assert v.accepted is False
    assert "converge" in v.reason
    assert c.get_model_snapshot()["identification"] == {"status": "unidentified"}


def test_a_converged_solve_is_not_by_itself_a_promotion(monkeypatch):
    """scipy calls a stalled step and a one-evaluation no-op "converged" too."""
    real = update_mpc.fit_params

    def stub(*args, **kwargs):
        out = real(*args, **kwargs)
        out.update(C_c=900.0, h_amb=0.2, K_Q=1.0, theta=5.0, converged=True, nfev=1)
        return out

    monkeypatch.setattr(update_mpc, "fit_params", stub)
    c = _c()
    c.cfg.update(TRUTH)  # an incumbent the stub cannot beat
    v = c.refit_from_cook(_synthetic_cook())
    assert v.accepted is False
    assert c.cfg["C_c"] == pytest.approx(TRUTH["C_c"])


# ---- a model that cannot be simulated is a verdict, not an exception ----


def _columns(cook):
    rows = list(cook)
    return (
        np.array([r[0] for r in rows]),
        np.array([r[1] for r in rows]),
        np.array([r[2] for r in rows]),
    )


def test_an_incumbent_the_model_cannot_be_simulated_at_ends_the_cook_with_a_verdict():
    """The incumbent comes from `cfg`, which imported settings can populate.

    Nothing between a settings file and this call asks whether the parameters
    in it can be simulated, so the first thing to find out is the score taken
    against them -- at the end of a real cook, in HoldMode's teardown, where
    the next thing owed to the grill is the cool-down fan.
    """
    cook = _synthetic_cook()
    t, temp, Q = _columns(cook)

    c = _c()
    c.cfg["C_c"] = 1e-9
    incumbent = {k: float(c.cfg[k]) for k in GreyLearningRuntime.MODEL_PARAM_KEYS}
    # The premise: this really is an incumbent no score can be taken against.
    assert math.isinf(update_mpc.fit_quality(t, temp, Q, incumbent, T_amb=float(c.cfg["T_amb"]))[0])

    verdict = c.refit_from_cook(cook)
    assert verdict.accepted is False
    # And the refusal names which of the two models could not be scored.
    assert "incumbent RMSE" in verdict.reason
    assert c.get_model_snapshot() is None

    # The same cook against a simulable incumbent is promoted, so what the
    # refusal reports is the incumbent and not the record.
    assert _c().refit_from_cook(cook).accepted is True


def test_a_solve_that_diverged_is_refused_before_anything_is_measured_on_it(monkeypatch):
    """A solve that ran out of evaluations returns its best point so far, and
    that point can be one the model cannot be simulated at.

    The convergence veto stands above both scores, so such a point is refused
    for the reason that is actually true of it and is never simulated.
    """
    real_params = update_mpc.fit_params
    real_quality = update_mpc.fit_quality
    landed = []
    scored = []

    def diverged(*args, **kwargs):
        out = real_params(*args, **kwargs)
        out.update(C_c=1e-9, converged=False, nfev=update_mpc._MAX_NFEV)
        landed.append(dict(out))
        return out

    def spy(t, temp, Q, params, **kwargs):
        scored.append(dict(params))
        return real_quality(t, temp, Q, params, **kwargs)

    monkeypatch.setattr(update_mpc, "fit_params", diverged)
    monkeypatch.setattr(update_mpc, "fit_quality", spy)

    cook = _synthetic_cook()
    c = _c()
    verdict = c.refit_from_cook(cook)

    assert verdict.accepted is False
    assert "converge" in verdict.reason
    assert c.get_model_snapshot()["identification"] == {"status": "unidentified"}
    # Nothing was scored at all -- neither the candidate nor the incumbent.
    assert scored == []

    # The premise, checked after the fact so the assertion above is not merely
    # about ordering: the point that solve landed on is one no score can be
    # taken against, which is what makes not taking one there matter.
    t, temp, Q = _columns(cook)
    assert math.isinf(real_quality(t, temp, Q, landed[0], T_amb=20.0)[0])


def test_an_unmeasurable_candidate_is_refused_by_the_gate_that_owns_the_judgement():
    """An infinite score is a verdict the gate can already read, and it reads
    it by name: the reason says which model could not be scored, which a raise
    out of `fit_quality` could not have said."""
    t, temp, Q = _columns(_synthetic_cook())
    runaway = dict(TRUTH, C_c=1e-9, T_amb=20.0, sigma=1.4e-9, n_delay=4)
    cand_rmse, _ = update_mpc.fit_quality(t, temp, Q, runaway, T_amb=20.0)
    assert math.isinf(cand_rmse)

    verdict = evaluate(
        dict(TRUTH, T_amb=20.0, sigma=1.4e-9, n_delay=4),
        dict(TRUTH, T_amb=20.0, sigma=1.4e-9, n_delay=4, C_c=2000.0),
        candidate_rmse=cand_rmse,
        incumbent_rmse=5.0,
        # Clear of the floor: what is pinned here is the unmeasurable
        # candidate, not whether the record determined it.
        identifiability=_IDENTIFIABILITY_FLOOR * 2.0,
    )
    assert verdict.accepted is False
    assert "candidate RMSE" in verdict.reason


def test_an_infinite_error_never_reaches_the_store(model_store):
    """The gate's refusal is the only thing standing between the two.

    `_adopt_model(rmse=cand_rmse, ...)` writes whatever the score was into the
    model's provenance, and the store's validator encodes with
    allow_nan=False. A promotion that stopped covering the unmeasurable case
    would therefore not fail at the gate, where it would say why, but at the
    write, losing the model the cook did learn.
    """
    c = _c()
    assert c.refit_from_cook(_synthetic_cook()).accepted is True
    assert model_store.save("mpc", c.get_model_snapshot()) is True

    active = c.active_control_pair.descriptor
    pair = c._pair_factory.build(
        c._pair_factory.configured(
            c.cfg,
            candidate_generation=active.candidate_generation + 1,
            role_generation=active.role_generation + 1,
            model_identified=True,
        ),
        authorized=False,
    )
    c._grey_learning_runtime.adopt_model(
        pair,
        rmse=math.inf,
        samples=1200,
        band_c=(25.0, 240.0),
    )
    assert model_store.save("mpc", c.get_model_snapshot()) is False


# ---- the fitter's bookkeeping is not part of the model ----


def test_solver_bookkeeping_never_becomes_part_of_the_model():
    c = _c()
    assert c.refit_from_cook(_synthetic_cook()).accepted is True
    assert "converged" not in c.cfg
    assert "nfev" not in c.cfg
    snapshot = c.get_model_snapshot()
    parameters = snapshot["active"]["parameters"]
    metadata = snapshot["active"]["metadata"]
    assert set(parameters) == set(GreyLearningRuntime.MODEL_PARAM_KEYS)
    assert "converged" not in parameters
    assert "nfev" not in parameters
    # Provenance, beside the model rather than inside it.
    assert metadata["nfev"] > 0
    # The store persists this verbatim, and rejects anything json cannot carry.
    json.dumps(snapshot, allow_nan=False)


def test_unrecorded_solver_effort_is_none_rather_than_zero():
    """Zero evaluations is a claim about a solve; "not recorded" is not."""
    c = _c()
    c.refit_from_cook(_synthetic_cook())
    snapshot = c.get_model_snapshot()
    snapshot["active"]["metadata"]["nfev"] = None
    restored = _c()
    assert restored.restore_model(snapshot) is True
    assert restored.get_model_snapshot()["active"]["metadata"]["nfev"] is None


def test_an_unmeasured_error_is_none_and_survives_the_store(model_store):
    """None, not 0.0 and not inf: 0.0 is an unbeatable incumbent the promotion
    gate could never dislodge, and inf is a float the store cannot write."""
    c = _c()
    c.refit_from_cook(_synthetic_cook())
    snapshot = c.get_model_snapshot()
    snapshot["active"]["metadata"]["rmse"] = None
    restored = _c()
    assert restored.restore_model(snapshot) is True
    out = restored.get_model_snapshot()
    assert out["active"]["metadata"]["rmse"] is None
    restored.set_target(110.0)
    assert restored.get_status()["model"]["rmse"] is None
    # The store's own validator, not a re-implementation of it.
    assert model_store.save("mpc", out) is True
    # And an error nobody measured cannot be compared against.
    verdict = evaluate(
        dict(TRUTH, T_amb=20.0, sigma=1.4e-9, n_delay=4),
        {k: float(restored.cfg[k]) for k in GreyLearningRuntime.MODEL_PARAM_KEYS},
        candidate_rmse=1.0,
        incumbent_rmse=out["active"]["metadata"]["rmse"],
        # Clear of the floor: what is being pinned is the missing incumbent
        # error, not whether the record determined the candidate.
        identifiability=2.0,
    )
    assert verdict.accepted is False
    assert "not recorded" in verdict.reason


def test_the_snapshot_does_not_alias_the_running_model():
    """A caller holding a snapshot must not watch it change under them when
    the next cook adopts something."""
    c = _c()
    c.refit_from_cook(_synthetic_cook())
    snapshot = c.get_model_snapshot()
    band = list(snapshot["active"]["metadata"]["band_c"])
    snapshot["active"]["metadata"]["band_c"][0] = -999.0
    assert c.get_model_snapshot()["active"]["metadata"]["band_c"] == band
