"""Pins controller_matrix.py's auger PWM model.

`_auger_toggle_tick` ports the free-running toggle in
`ControlMode._auger_cycle_tick` (controller/runtime/modes/base.py): a toggle
keyed on elapsed time since its own last flip, independent of the caller's
re-solve cadence, returning the auger's exact fractional on-time over the
caller's 1 s window rather than a boolean sample of it (paired with
`GrillSim.step`'s `float(auger_on)`, which accepts that fraction directly).

The integration test below drives the real `run_scenario` loop (not just the
extracted helper) with a stub controller whose `get_control_period()` varies,
so a regression that reintroduces re-anchoring the toggle to each re-solve, or
reintroduces boolean-per-tick sampling, would be caught regardless of which
line inside the loop reintroduced it. It covers a ratio whose cycle duration
is an exact number of ticks (`u_min`) and one that is not (0.4237), so both
the toggle's timing logic and its fractional-remainder arithmetic are
exercised. The legacy-model test is a negative control proving the
period-mismatch assertion is not vacuous: that model passes when the control
period matches HoldCycleTime and fails when it does not.
"""

import os
import sys
import types

import pytest

from common.defaults import default_settings

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

import docs.superpowers.experiments.controller_matrix as controller_matrix

CYCLE_TIME = default_settings()["cycle_data"]["HoldCycleTime"]
RATIO = 0.35
U_MIN = default_settings()["cycle_data"]["u_min"]
# cycle_time * NON_INTEGER_RATIO is not a whole number, so the toggle's
# transition instants fall strictly inside a tick and the fraction returned
# is neither 0.0 nor 1.0 -- exercises the remainder arithmetic itself, which
# a ratio like 0.35 or u_min (both exact at cycle_time=20) cannot.
NON_INTEGER_RATIO = 0.4237
DURATION = 20 * CYCLE_TIME  # enough full cycles to average out the startup transient


def _legacy_on_fraction(ratio, cycle_time, period, duration):
    """Reproduces a toggle re-anchored to each re-solve, kept local to this
    test as a negative control -- proves the assertion above actually
    discriminates rather than always passing."""
    cycle_anchor = 0.0
    next_solve = 0.0
    on_ticks = 0
    for t in range(duration):
        if t >= next_solve:
            next_solve = t + period
            cycle_anchor = t
        phase = (t - cycle_anchor) % cycle_time
        on_ticks += phase < cycle_time * ratio
    return on_ticks / duration


class _StubController:
    """Constant-ratio controller whose re-solve cadence is configurable."""

    def __init__(self, config, units, cycle_data):
        del units, cycle_data
        self._period = config["_period"]
        self._ratio = config["_ratio"]

    def set_target(self, set_point):
        del set_point

    def get_control_period(self):
        return self._period

    def update(self, temp_f):
        del temp_f
        return self._ratio


class _FakePlant:
    """Records the exact auger on-fraction the real run_scenario loop drives
    each window, ignoring thermal physics entirely -- only the toggle's timing
    is under test here."""

    instances = []

    def __init__(self, seed=0):
        del seed
        self.on_fracs = []
        _FakePlant.instances.append(self)

    def measured(self):
        return 0.0

    def step(self, auger_on, fan_frac, lid_open=False):
        del fan_frac, lid_open
        self.on_fracs.append(float(auger_on))


def _run_stub(control_period, ratio, monkeypatch):
    name = "_stub_toggle_probe"
    fake_mod = types.ModuleType(f"controller.{name}")
    fake_mod.Controller = _StubController
    monkeypatch.setitem(sys.modules, f"controller.{name}", fake_mod)
    monkeypatch.setattr(controller_matrix, "GrillSim", _FakePlant)
    _FakePlant.instances.clear()

    scenario = controller_matrix.Scenario(name, DURATION, [(0, 999.0)])
    controller_matrix.run_scenario(name, scenario, seed=0, config={"_period": control_period, "_ratio": ratio})
    return _FakePlant.instances[-1]


@pytest.mark.parametrize(
    "control_period,ratio",
    [
        (20, RATIO),
        (5, RATIO),
        # u_min is where boolean-per-tick sampling would be biased worst
        # (both controllers sit here for the whole 225 F scenarios), so it is
        # the case most likely to catch a regression back to boolean sampling.
        (20, U_MIN),
        (5, U_MIN),
        (20, NON_INTEGER_RATIO),
        (5, NON_INTEGER_RATIO),
    ],
)
def test_run_scenario_realized_duty_matches_ratio_regardless_of_control_period(control_period, ratio, monkeypatch):
    plant = _run_stub(control_period, ratio, monkeypatch)
    realized = sum(plant.on_fracs) / len(plant.on_fracs)
    # Tight enough that boolean-per-tick sampling would fail this assertion at
    # every ratio tested; the fractional model should be exact modulo
    # floating point.
    assert realized == pytest.approx(ratio, abs=0.005)


def test_non_integer_ratio_produces_genuine_fractional_on_time(monkeypatch):
    """u_min and 0.35 both give an exact number of ticks per phase at
    cycle_time=20, so a toggle that only ever returns 0.0 or 1.0 would still
    pass the parametrized test above. This asserts the fraction formula
    itself runs: at least one tick's on-time must be strictly between 0 and 1."""
    plant = _run_stub(20, NON_INTEGER_RATIO, monkeypatch)
    assert any(0.0 < frac < 1.0 for frac in plant.on_fracs)


def test_legacy_model_actually_fails_the_mismatched_period_case():
    """Negative control: proves the test above is not vacuous by showing the
    pre-fix model passes at period==cycle_time and fails at period=5."""
    matched = _legacy_on_fraction(RATIO, CYCLE_TIME, CYCLE_TIME, DURATION)
    assert matched == pytest.approx(RATIO, abs=0.02)

    mismatched = _legacy_on_fraction(RATIO, CYCLE_TIME, 5, DURATION)
    assert mismatched != pytest.approx(RATIO, abs=0.02)
