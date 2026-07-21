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


# --- FIXED BUG (was pinned; now asserts corrected behavior) ----------------------


def test_last_temp_advances_after_first_call(monkeypatch):
    """controller/ml.py's first-run branch used to have the same no-op
    `self.last_temp == current` comparison typo as controller/fuzzy.py's
    first-run branch, so `self.last_temp` stayed stuck at its __init__
    sentinel of -99 forever: every call (not just the first) took the
    `last_temp == -99` branch and computed a meaningless rate_of_change of
    (current - (-99)) / HoldCycleTime.

    Fixed: line 57 is now an assignment (`self.last_temp = current`),
    mirroring the already-fixed controller/fuzzy.py. After the first
    update() call, last_temp advances to the current reading, so the first
    call's rate_of_change collapses to 0 (current - current) rather than a
    huge spurious value. The second call then uses that real prior reading
    (200) instead of -99, so rate_of_change reflects the actual delta
    between consecutive readings.
    """
    model = FakeModel(ratio=0.5)
    c = _controller(monkeypatch, model=model, units="F", hold_cycle_time=20)
    c.set_target(225)

    times = iter([100.0, 105.0])  # "now" for the two update() calls below
    monkeypatch.setattr(ml.time, "time", lambda: next(times))
    c.last_time = 100.0

    ratio = c.update(200)

    assert ratio == 0.5
    assert c.last_temp == 200  # advances to current on first call, not stuck at -99

    first_rate = c.model.calls[0][0][2]
    assert first_rate == pytest.approx(0.0)  # (200 - 200) / HoldCycleTime, not (200 - (-99)) / 20

    c.update(205)  # second call: last_temp is 200 now, so the -99 branch no longer fires

    second_rate = c.model.calls[1][0][2]
    # cycle_time here is the measured 5s elapsed (105.0 - 100.0), and the
    # delta is against the real prior reading (200), not the -99 sentinel.
    assert second_rate == pytest.approx((205 - 200) / 5.0)
    assert second_rate != pytest.approx((205 - (-99)) / 20)
