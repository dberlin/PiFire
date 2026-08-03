"""A finished cook improves the model, or is refused with a reason."""

import json
import time

import numpy as np
import pytest

import controller.update_mpc as update_mpc
from controller.model_promotion import PROMOTION_BOUNDS
from controller.mpc import _DEFAULTS, _REFIT_INIT, _REFIT_MAX_SAMPLES, Controller
from controller.mpc_model import simulate_grey_box

CYCLE = {"u_min": 0.1, "u_max": 0.9, "HoldCycleTime": 25}
TRUTH = dict(C_f=9.0, C_c=11000.0, h_fc=1.3, h_amb=2.7, K_Q=32.0, theta=110.0)

# The fitted free parameters, plus the C_f that holds the scale they are
# measured against -- exactly what a refit's starting point supplies.
FITTED_KEYS = ("C_f", "C_c", "h_fc", "h_amb", "K_Q", "theta")


def _synthetic_cook(seed=0, noise=0.5):
    """A heat-up then a step down, from a grill that is NOT the default.

    Carries probe noise, without which the fit reaches an RMSE around 1e-12 and
    every error comparison in this file would be a comparison of rounding.
    """
    t = np.arange(0.0, 6000.0, 5.0)
    Q = np.where(t < 3000.0, 100.0, 20.0)
    temp = simulate_grey_box(t, Q, T0=25.0, T_amb=20.0, sigma=1.4e-9, n_delay=4, **TRUTH)
    temp = temp + np.random.default_rng(seed).normal(0.0, noise, size=temp.shape)
    return list(zip(t.tolist(), temp.tolist(), Q.tolist()))


def _c():
    return Controller(dict(_DEFAULTS, policy="nlp"), "C", dict(CYCLE))


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


def test_a_second_worse_cook_does_not_replace_a_good_model():
    c = _c()
    assert c.refit_from_cook(_synthetic_cook()).accepted is True
    good = c.cfg["C_c"]
    # A flat, uninformative cook: no excitation, so any model fits it equally.
    flat = [(float(i * 5), 100.0, 50.0) for i in range(400)]
    assert c.refit_from_cook(flat).accepted is False
    assert c.cfg["C_c"] == pytest.approx(good)


def test_the_refit_is_bounded_in_time(fits):
    """A 12-hour cook is ~8640 rows and each least-squares evaluation
    re-simulates all of them. Decimation keeps this off the minutes scale.

    The wall-clock bound alone is a weak guard on a developer machine, which
    swallows the undecimated cook inside it; the row count is what has to hold
    for the same bound to survive on a Raspberry Pi.
    """
    t = np.arange(0.0, 43200.0, 5.0)
    Q = np.where((t // 1800) % 2 == 0, 100.0, 20.0)
    temp = simulate_grey_box(t, Q, T0=25.0, T_amb=20.0, sigma=1.4e-9, n_delay=4, **TRUTH)
    history = list(zip(t.tolist(), temp.tolist(), Q.tolist()))
    assert len(history) > _REFIT_MAX_SAMPLES
    c = _c()
    t0 = time.perf_counter()
    c.refit_from_cook(history)
    assert time.perf_counter() - t0 < 30.0
    assert fits[0]["rows"] <= _REFIT_MAX_SAMPLES


# ---- the fit's starting point is fixed, and stays fixed across cooks ----


def test_every_refit_starts_from_the_same_fixed_reference(fits):
    """Several cooks in a row, each accepted result left in place for the next.

    A refit seeded from the running model makes each cook's answer a function
    of every cook before it, and there is no single refit that shows it -- the
    first one starts from the shipped model whichever way the code is written.
    So this drives the loop and reads the starting point each time round.
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
        out.update(C_c=900.0, h_amb=0.2, h_fc=0.4, K_Q=1.0, theta=5.0, converged=True, nfev=1)
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
