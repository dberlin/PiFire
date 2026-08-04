"""A finished cook improves the model, or is refused with a reason."""

import json
import time

import numpy as np
import pytest

import controller.update_mpc as update_mpc
from common.controller_model_state import ControllerModelStore
from controller.model_promotion import _IDENTIFIABILITY_FLOOR, PROMOTION_BOUNDS, evaluate
from controller.mpc import _DEFAULTS, _HISTORY_MAX, _REFIT_INIT, Controller
from controller.mpc_model import simulate_grey_box

CYCLE = {"u_min": 0.1, "u_max": 0.9, "HoldCycleTime": 25}
#: h_amb matches the shipped default rather than differing from it: `_FREE`
#: holds h_amb and sigma both, so a cook whose sigma/h_amb differs from the
#: fitter's is one the fitter cannot represent, and the refit would be judged
#: on a target outside its reach. Everything else here is far from the default.
TRUTH = dict(C_c=11000.0, h_amb=0.5, K_Q=32.0, theta=110.0)

# The fitted free parameters, plus the held ones a refit's starting point
# supplies alongside them -- see update_mpc._FREE.
FITTED_KEYS = ("C_c", "h_amb", "K_Q", "theta")


def _synthetic_cook(seed=0, noise=0.5, rows=1200):
    """A heat-up then a step down, from a grill that is NOT the default.

    Carries probe noise, without which the fit reaches an RMSE around 1e-12 and
    every error comparison in this file would be a comparison of rounding.
    """
    t = np.arange(0.0, 5.0 * rows, 5.0)
    Q = np.where(t < 2.5 * rows, 100.0, 20.0)
    temp = simulate_grey_box(t, Q, T0=25.0, T_amb=20.0, sigma=1.4e-9, n_delay=_DEFAULTS["n_delay"], **TRUTH)
    temp = temp + np.random.default_rng(seed).normal(0.0, noise, size=temp.shape)
    return list(zip(t.tolist(), temp.tolist(), Q.tolist()))


def _c():
    return Controller(dict(_DEFAULTS, policy="nlp"), "C", dict(CYCLE))


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


def test_a_refit_moves_the_model_toward_the_grill_that_produced_the_cook():
    c = _c()
    before = c.cfg["C_c"] / c.cfg["h_amb"]
    verdict = c.refit_from_cook(_synthetic_cook())
    assert verdict.accepted is True
    after = c.cfg["C_c"] / c.cfg["h_amb"]
    truth = TRUTH["C_c"] / TRUTH["h_amb"]
    assert abs(after - truth) < abs(before - truth)


def test_a_refit_records_the_band_it_learned_in():
    c = _c()
    c.refit_from_cook(_synthetic_cook())
    lo, hi = c.get_model_snapshot()["band_c"]
    assert lo < hi
    assert hi > 200.0  # the synthetic cook is a high-temperature run


def test_too_few_samples_is_refused_without_fitting():
    c = _c()
    v = c.refit_from_cook([(0.0, 20.0, 50.0), (5.0, 21.0, 50.0)])
    assert v.accepted is False
    assert "sample" in v.reason.lower()
    assert c.get_model_snapshot() is None


def test_a_refit_uses_the_live_history_when_given_none():
    c = _c()
    c.set_target(110.0)
    for _ in range(3):
        c.update(100.0)
    assert c.refit_from_cook().accepted is False  # too short to accept, but must not raise

    # A refused short history alone would read the same whether the live
    # history was consulted or an empty list was, so drive one that can be fit.
    live = _c()
    live._history.extend(_synthetic_cook())
    assert live.refit_from_cook().accepted is True


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
    incumbent = {k: float(c.cfg[k]) for k in Controller._MODEL_PARAM_KEYS}
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
        n_horizon=int(c.cfg["n_horizon"]),
        t_step=float(c.cfg["t_step"]),
    )
    assert permitted.accepted is True
    assert fitted["theta"] < 0.2 * learned_theta


def _heatup_only(rows, seed=0, noise=0.5):
    """A ramp from cold at full fire and nothing else -- no step, no coast.

    The record shape that determines the model LEAST while still being a real
    cook: it pins the steady gain and the early curvature, and says
    progressively less about the chamber's time constant the shorter it is
    cut. That makes its length a dial on identifiability, which is what the
    two bound tests below need.
    """
    t = np.arange(0.0, 5.0 * rows, 5.0)
    Q = np.full_like(t, 100.0)
    temp = simulate_grey_box(t, Q, T0=25.0, T_amb=20.0, sigma=1.4e-9, n_delay=_DEFAULTS["n_delay"], **TRUTH)
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
        sigma=float(_DEFAULTS["sigma"]),
        n_delay=int(_DEFAULTS["n_delay"]),
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
    t, temp, Q = t[:n], df["temp_c"].values.astype(float)[:n], df["Q"].values.astype(float)[:n]

    _, s_min = _shipped_fit(t, temp, Q)
    assert s_min == pytest.approx(1.098188, abs=1e-5)
    assert s_min >= _IDENTIFIABILITY_FLOOR


def test_identifiability_does_not_look_at_the_measured_temperatures():
    """The statistic reads the record's inputs, never its residual.

    This is the property that makes it worth sitting beside the RMSE rather
    than duplicating it. Two records with identical `t`, `Q` and starting
    temperature, judged at the same fitted point, must score identically
    however differently the thermometer behaved after the first sample -- the
    Jacobian is the model's, and the model is not told the measurements. A
    refactor that let the residual leak in here would quietly turn the floor
    back into a second reading of the error it was installed to distrust.
    """
    t, temp, Q = _heatup_only(240)
    fitted, _ = _shipped_fit(t, temp, Q)

    other = temp.copy()
    other[1:] = other[1:] + np.linspace(0.0, 60.0, len(other) - 1)  # a wildly different cook
    assert not np.allclose(temp[1:], other[1:])
    assert other[0] == temp[0]

    a = update_mpc.identifiability(t, Q, fitted, T_amb=20.0, T0=float(temp[0]))
    b = update_mpc.identifiability(t, Q, fitted, T_amb=20.0, T0=float(other[0]))
    assert a == pytest.approx(b, rel=1e-12)


def test_a_fitted_point_with_no_logarithm_is_unmeasurable_and_is_refused():
    """A free parameter that is not a positive scale has no e-fold to perturb.

    `identifiability` says None rather than guessing, and the gate treats that
    as it treats a low score: the record has not been shown to determine
    anything, so nothing may be promoted on it.
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
        n_horizon=24,
        t_step=25.0,
    )
    assert verdict.accepted is False
    assert "identifiability" in verdict.reason
    # A refused model imposes no horizon demand on the grill.
    assert verdict.horizon_needed is None


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
            n_horizon=24,
            t_step=25.0,
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
        n_horizon=24,
        t_step=25.0,
    )
    assert verdict.accepted is False
    assert "does not determine the model" in verdict.reason


def test_the_longest_cook_stays_inside_the_teardown_budget(fits):
    """The refit runs synchronously in HoldMode.teardown, so its cost is time
    the shutdown fan's cool-down starts late.

    The budget is 30 s. The shipped `shutdown_duration` is 240 s, so a refit
    inside this bound delays the cool-down by at most an eighth of itself,
    with the auger and igniter already off.

    This is the worst case there is: `_HISTORY_MAX` rows at the shipped
    control period is a full 12-hour history, and the companion test below
    holds the history to that length, so no cook can present the teardown path
    with more work than this one does.
    """
    t = np.arange(0.0, 5.0 * _HISTORY_MAX, 5.0)
    Q = np.where((t // 1800) % 2 == 0, 100.0, 20.0)
    temp = simulate_grey_box(t, Q, T0=25.0, T_amb=20.0, sigma=1.4e-9, n_delay=_DEFAULTS["n_delay"], **TRUTH)
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
    c._history.extend(_synthetic_cook(rows=_HISTORY_MAX + 500))
    assert len(c.cook_history()) == _HISTORY_MAX


# ---- the fit's starting point is fixed, and stays fixed across cooks ----


def test_every_refit_starts_from_the_same_fixed_reference(fits):
    """Several cooks in a row, each accepted result left in place for the next.

    What this pins is that the starting point is the same every time, not that
    the finished parameters happen to agree: seeded from the running model
    they would be a function of every cook before them, and the fit's start is
    the only place that shows. One refit cannot show it either -- the first
    one begins at the shipped model whichever way the code is written -- so
    this drives the loop and reads the starting point each time round.
    """
    c = _c()
    shipped = {key: c.cfg[key] for key in FITTED_KEYS}
    verdicts = [
        # Distinct excitation per cook, so each is genuinely new evidence and
        # the loop actually feeds results forward rather than idling.
        c.refit_from_cook(_synthetic_cook(seed=seed, noise=0.4 + 0.3 * seed))
        for seed in range(4)
    ]
    assert len(fits) == 4
    # Without this the later fits would still be starting from the shipped
    # model by accident, and reading their starting point would prove nothing.
    assert verdicts[0].accepted is True
    assert {key: c.cfg[key] for key in FITTED_KEYS} != shipped
    for call in fits:
        assert call["init"] == _REFIT_INIT


def test_the_fixed_reference_is_the_shipped_model_and_is_not_written_through():
    expected = {key: float(_DEFAULTS[key]) for key in FITTED_KEYS}
    assert _REFIT_INIT == expected
    before = dict(_DEFAULTS)
    c = _c()
    assert c.refit_from_cook(_synthetic_cook()).accepted is True
    assert _REFIT_INIT == expected
    assert _DEFAULTS == before


def test_repeated_refits_leave_the_model_inside_the_promotion_bounds(fits):
    """The parameters must not ratchet out of the range a model may live in."""
    c = _c()
    for seed in range(4):
        c.refit_from_cook(_synthetic_cook(seed=seed, noise=0.4 + 0.3 * seed))
    for key, (lo, hi) in PROMOTION_BOUNDS.items():
        assert lo <= float(c.cfg[key]) <= hi, key
    first_tau = fits[0]["out"]["C_c"] / fits[0]["out"]["h_amb"]
    last_tau = fits[-1]["out"]["C_c"] / fits[-1]["out"]["h_amb"]
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
    assert c.get_model_snapshot() is None


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


# ---- the fitter's bookkeeping is not part of the model ----


def test_solver_bookkeeping_never_becomes_part_of_the_model():
    c = _c()
    assert c.refit_from_cook(_synthetic_cook()).accepted is True
    assert "converged" not in c.cfg
    assert "nfev" not in c.cfg
    snapshot = c.get_model_snapshot()
    assert set(snapshot["params"]) == set(Controller._MODEL_PARAM_KEYS)
    assert "converged" not in snapshot["params"]
    assert "nfev" not in snapshot["params"]
    # Provenance, beside the model rather than inside it.
    assert snapshot["nfev"] > 0
    # The store persists this verbatim, and rejects anything json cannot carry.
    json.dumps(snapshot, allow_nan=False)


def test_unrecorded_solver_effort_is_none_rather_than_zero():
    """Zero evaluations is a claim about a solve; "not recorded" is not."""
    c = _c()
    c.refit_from_cook(_synthetic_cook())
    snapshot = c.get_model_snapshot()
    snapshot.pop("nfev")
    restored = _c()
    assert restored.restore_model(snapshot) is True
    assert restored.get_model_snapshot()["nfev"] is None


def test_an_unmeasured_error_is_none_and_survives_the_store(model_store):
    """None, not 0.0 and not inf: 0.0 is an unbeatable incumbent the promotion
    gate could never dislodge, and inf is a float the store cannot write."""
    c = _c()
    c.refit_from_cook(_synthetic_cook())
    snapshot = c.get_model_snapshot()
    snapshot.pop("rmse")
    restored = _c()
    assert restored.restore_model(snapshot) is True
    out = restored.get_model_snapshot()
    assert out["rmse"] is None
    restored.set_target(110.0)
    assert restored.get_status()["model"]["rmse"] is None
    # The store's own validator, not a re-implementation of it.
    assert model_store.save("mpc", out) is True
    # And an error nobody measured cannot be compared against.
    verdict = evaluate(
        dict(TRUTH, T_amb=20.0, sigma=1.4e-9, n_delay=4),
        {k: float(restored.cfg[k]) for k in Controller._MODEL_PARAM_KEYS},
        candidate_rmse=1.0,
        incumbent_rmse=out["rmse"],
        # Clear of the floor: what is being pinned is the missing incumbent
        # error, not whether the record determined the candidate.
        identifiability=2.0,
        n_horizon=24,
        t_step=25.0,
    )
    assert verdict.accepted is False
    assert "not recorded" in verdict.reason


def test_the_snapshot_does_not_alias_the_running_model():
    """A caller holding a snapshot must not watch it change under them when
    the next cook adopts something."""
    c = _c()
    c.refit_from_cook(_synthetic_cook())
    snapshot = c.get_model_snapshot()
    band = list(snapshot["band_c"])
    snapshot["band_c"][0] = -999.0
    assert c.get_model_snapshot()["band_c"] == band
