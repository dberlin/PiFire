"""Coverage for controller/fuzzy.py's runtime Controller (not update_fuzzy.py,
which builds the pickle -- this module just loads it and evaluates it).

Uses the real ./controller/fuzzy.pickle checked into the repo (relative to the
pytest invocation cwd, which is the repo root) rather than mocking pickle.load,
so these tests exercise the actual scikit-fuzzy simulation end to end.
"""

import pathlib
import subprocess

import pytest

from controller.fuzzy import Controller


def _controller(units="F", hold_cycle_time=20):
    return Controller(config={}, units=units, cycle_data={"HoldCycleTime": hold_cycle_time})


# --- set_target -------------------------------------------------------------


def test_set_target_converts_celsius_to_fahrenheit():
    c = _controller(units="C")
    c.set_target(100)  # 100C -> 212F
    assert c.set_point == 212


def test_set_target_fahrenheit_passthrough():
    c = _controller(units="F")
    c.set_target(225)
    assert c.set_point == 225


# --- update: happy path -------------------------------------------------------


def test_update_returns_cycle_ratio_between_0_and_1():
    c = _controller()
    c.set_target(225)
    ratio = c.update(200)
    assert 0.0 <= ratio <= 1.0


def test_update_converts_celsius_current_to_fahrenheit():
    """units="C" path in update(): current is converted before being used, so a
    Celsius controller and the Fahrenheit-equivalent controller should agree
    on the resulting cycle ratio for the first call."""
    c_f = _controller(units="F")
    c_f.set_target(225)
    ratio_f = c_f.update(200)  # already Fahrenheit

    c_c = _controller(units="C")
    c_c.set_target(107)  # ~225F, matches set_target's own int() truncation
    ratio_c = c_c.update(int((200 - 32) * 5 / 9))  # ~93C -> back to 200F

    assert ratio_c == pytest.approx(ratio_f, abs=0.05)


def test_second_call_uses_measured_cycle_time_and_previous_current():
    """Non-first-run path: cycle_time comes from wall-clock elapsed time (here
    tiny, since the two calls happen back-to-back in-test) rather than the
    configured HoldCycleTime, and last_temp is the previous call's current."""
    c = _controller()
    c.set_target(225)
    c.update(200)
    assert c.last_temp == 200

    ratio = c.update(205)
    assert 0.0 <= ratio <= 1.0
    assert c.last_temp == 205


# --- init: missing-pickle branch (subprocess neutralized) --------------------


def test_missing_pickle_regenerates_via_subprocess_without_running_it(monkeypatch):
    """If ./controller/fuzzy.pickle does not exist, __init__ shells out to
    `python update_fuzzy.py` to regenerate it. We neutralize subprocess.run
    (patched on the bound module attribute, as imported locally inside
    __init__) so this test can never actually spawn that process, and confirm
    it's the thing that would have been invoked."""
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda command: calls.append(command))

    real_exists = pathlib.Path.exists
    target = pathlib.Path("./controller/fuzzy.pickle")

    def fake_exists(self):
        if self == target:
            return False
        return real_exists(self)

    monkeypatch.setattr(pathlib.Path, "exists", fake_exists)

    # The pickle file is still physically present on disk (only Path.exists()
    # is faked), so the subsequent `open(...)` + pickle.load still succeeds
    # and __init__ completes normally -- we're only exercising the
    # regeneration branch, not actually losing the file.
    c = _controller()

    assert calls == [["python", "update_fuzzy.py"]]
    assert c.fuzzy_controller is not None


def test_open_pickle_failure_is_logged_not_raised(monkeypatch, caplog):
    """If opening/unpickling the file raises, __init__ swallows the exception
    via a bare except + controlLogger.exception(...) rather than propagating
    it. Confirm construction still completes (fuzzy_controller left unset)."""
    import builtins

    real_open = builtins.open

    def fake_open(path, *args, **kwargs):
        if "fuzzy.pickle" in str(path):
            raise OSError("boom")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fake_open)

    c = _controller()

    assert not hasattr(c, "fuzzy_controller")
    assert c.last_temp == -99


# --- LATENT BUG: first-run rate_of_change ------------------------------------


def test_first_call_treats_rate_of_change_as_zero():
    """Pin for the `self.last_temp == current` typo in the first-run branch
    (last_temp starts at sentinel -99).

    BEFORE the fix, that line was a no-op comparison (`==` instead of `=`),
    so `self.last_temp` stayed at -99 through the rate_of_change calculation
    a few lines below:

        rate_of_change = (current - self.last_temp) / cycle_time

    On the very first update() call with current=210, that computed
    (210 - (-99)) / 20 = 15.45 -- a huge, meaningless "rate of change" that
    doesn't correspond to any real temperature delta, since there was no
    previous reading yet. Fed into the fuzzy controller (whose
    rate_of_change universe only spans 0..1), this saturated the "High"
    membership function and drove the observed cycle ratio down to
    ~0.560 (delta=8, current=210) -- as if the grill were heating rapidly
    on its very first reading, suppressing the auger feed right when the
    controller should be ramping up hard from a cold start.

    AFTER the fix (`self.last_temp = current`), the first call's
    rate_of_change collapses to (current - current) / cycle_time == 0,
    correctly modeling "no rate-of-change information yet" as "Low" rather
    than a spurious "High" spike. For the same delta=8/current=210 inputs
    this resolves to ~0.954 -- the fuzzy system's intended "ramp up hard"
    response for being far below setpoint with a flat reading.
    """
    c = _controller(units="F")
    c.set_target(218)

    ratio = c.update(210)

    assert ratio == pytest.approx(0.9541892483190864, abs=1e-6)
    assert c.last_temp == 210
