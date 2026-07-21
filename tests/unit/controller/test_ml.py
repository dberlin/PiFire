"""Coverage for controller/ml.py's Controller.

The model loader is `from joblib import load` at module scope, so it's bound
as `controller.ml.load` -- tests patch that name (not the global joblib.load)
per Harness A style, per the module's own docstring guidance.
"""

import pytest

import controller.ml as ml


class FakeModel:
    def __init__(self, ratio=0.42):
        self.ratio = ratio
        self.calls = []

    def predict(self, X):
        self.calls.append(X)
        return [self.ratio]


def _controller(monkeypatch, model=None, units="F", hold_cycle_time=20):
    monkeypatch.setattr(ml, "load", lambda path: model or FakeModel())
    return ml.Controller(config={}, units=units, cycle_data={"HoldCycleTime": hold_cycle_time})


# --- construction -------------------------------------------------------------


def test_controller_loads_model_via_bound_load_name(monkeypatch):
    seen = {}
    monkeypatch.setattr(ml, "load", lambda path: seen.setdefault("path", path) or FakeModel())

    ml.Controller(config={}, units="F", cycle_data={"HoldCycleTime": 20})

    assert seen["path"] == "./controller/ml_model.joblib"


def test_controller_reraises_when_load_fails(monkeypatch):
    def boom(path):
        raise FileNotFoundError("no model here")

    monkeypatch.setattr(ml, "load", boom)

    with pytest.raises(FileNotFoundError):
        ml.Controller(config={}, units="F", cycle_data={"HoldCycleTime": 20})


# --- set_target -----------------------------------------------------------------


def test_set_target_converts_celsius_to_fahrenheit(monkeypatch):
    c = _controller(monkeypatch, units="C")
    c.set_target(100)
    assert c.set_point == 212


def test_set_target_fahrenheit_passthrough(monkeypatch):
    c = _controller(monkeypatch, units="F")
    c.set_target(225)
    assert c.set_point == 225


# --- update: happy path ----------------------------------------------------------


def test_ml_controller_predicts_cycle_ratio(monkeypatch):
    model = FakeModel(ratio=0.42)
    c = _controller(monkeypatch, model=model)
    c.set_target(225)

    ratio = c.update(200)

    assert ratio == 0.42
    # predict() is called with a single [current, set_point, rate_of_change] row.
    (call_args,) = model.calls
    (row,) = call_args
    current, set_point, rate_of_change = row
    assert current == 200
    assert set_point == 225


def test_update_converts_celsius_current_to_fahrenheit(monkeypatch):
    model = FakeModel(ratio=0.7)
    c = _controller(monkeypatch, model=model, units="C")
    c.set_target(100)  # -> 212F

    c.update(93)  # ~200F

    (call_args,) = model.calls
    (row,) = call_args
    current, _, _ = row
    assert current == int(93 * (9 / 5) + 32)


# --- LATENT BUG (pinned, NOT fixed -- out of scope for this task) ----------------


def test_last_temp_never_updates_so_rate_of_change_stays_pinned_to_neg99(monkeypatch):
    """PIN, not a fix: controller/ml.py has the exact same `self.last_temp ==
    current` no-op-comparison typo as controller/fuzzy.py's first-run branch
    (line ~57), but unlike fuzzy.py, ml.py's update() has no compensating
    `self.last_temp = current` assignment anywhere else in the method. That
    means `self.last_temp` is stuck at its __init__ sentinel of -99 forever:
    every single call (not just the first) takes the `last_temp == -99`
    branch, overrides cycle_time to the configured HoldCycleTime, and computes
    rate_of_change as (current - (-99)) / HoldCycleTime rather than an actual
    rate between consecutive readings.

    This task's authorized fix is scoped to controller/fuzzy.py only (see
    latent-bug rule), so this is documented/pinned here, not corrected. Filed
    for a follow-up task.
    """
    model = FakeModel(ratio=0.5)
    c = _controller(monkeypatch, model=model, units="F", hold_cycle_time=20)
    c.set_target(225)

    c.update(200)
    c.update(205)  # a "second" call -- still hits the -99 branch every time

    first_rate = c.model.calls[0][0][2]
    second_rate = c.model.calls[1][0][2]

    assert c.last_temp == -99  # never advances
    assert first_rate == pytest.approx((200 - (-99)) / 20)
    assert second_rate == pytest.approx((205 - (-99)) / 20)
