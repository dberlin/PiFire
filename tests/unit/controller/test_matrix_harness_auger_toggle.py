"""Pins the fix-round-1 correction to controller_matrix.py's auger PWM model.

The harness used to re-anchor its duty-cycle window every controller re-solve
(`cycle_anchor = t`), which only reproduces production for controllers whose
re-solve period equals HoldCycleTime. MPC re-solves every 5s against a 20s
cycle_time, so the realized auger on-fraction came out far higher than the
requested ratio -- `_auger_toggle_tick` ports the free-running toggle in
`ControlMode._auger_cycle_tick` (controller/runtime/modes/base.py) instead,
which is independent of the caller's re-solve cadence.

The integration test below drives the real `run_scenario` loop (not just the
extracted helper) with a stub controller whose `get_control_period()` varies,
so a regression that reintroduces re-anchoring inside the loop itself would
still be caught -- exercising `_auger_toggle_tick` in isolation would not
catch that class of regression. The legacy-model test is a negative control
proving the fix is not vacuous: the pre-fix model passes when the control
period matches HoldCycleTime and fails when it does not.
"""

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

import docs.superpowers.experiments.controller_matrix as controller_matrix

CYCLE_TIME = controller_matrix.CYCLE_DATA["HoldCycleTime"]
RATIO = 0.35
DURATION = 20 * CYCLE_TIME  # enough full cycles to average out the startup transient


def _legacy_on_fraction(ratio, cycle_time, period, duration):
    """Reproduces the pre-fix model: re-anchors the PWM window on every re-solve.

    Kept local to this test, not reintroduced into the harness -- it exists only
    to prove the negative-control test below actually discriminates.
    """
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
    """Records every `auger_on` the real run_scenario loop drives, ignoring
    thermal physics entirely -- only the toggle sequence is under test here."""

    instances = []

    def __init__(self, seed=0):
        del seed
        self.on_ticks = []
        _FakePlant.instances.append(self)

    def measured(self):
        return 0.0

    def step(self, auger_on, fan_frac):
        del fan_frac
        self.on_ticks.append(bool(auger_on))


@pytest.mark.parametrize("control_period", [20, 5])
def test_run_scenario_realized_duty_matches_ratio_regardless_of_control_period(control_period, monkeypatch):
    """Drives the real run_scenario loop -- not just _auger_toggle_tick in
    isolation -- so a regression that reintroduces re-anchoring inside the loop
    itself would still be caught."""
    name = "_stub_toggle_probe"
    fake_mod = types.ModuleType(f"controller.{name}")
    fake_mod.Controller = _StubController
    monkeypatch.setitem(sys.modules, f"controller.{name}", fake_mod)
    monkeypatch.setitem(controller_matrix.CONTROLLER_CONFIGS, name, {"_period": control_period, "_ratio": RATIO})
    monkeypatch.setattr(controller_matrix, "GrillSim", _FakePlant)
    _FakePlant.instances.clear()

    scenario = controller_matrix.Scenario(name, DURATION, [(0, 999.0)])
    controller_matrix.run_scenario(name, scenario, seed=0)

    plant = _FakePlant.instances[-1]
    realized = sum(plant.on_ticks) / len(plant.on_ticks)
    assert realized == pytest.approx(RATIO, abs=0.02)


def test_legacy_model_actually_fails_the_mismatched_period_case():
    """Negative control: proves the test above is not vacuous by showing the
    pre-fix model passes at period==cycle_time and fails at period=5."""
    matched = _legacy_on_fraction(RATIO, CYCLE_TIME, CYCLE_TIME, DURATION)
    assert matched == pytest.approx(RATIO, abs=0.02)

    mismatched = _legacy_on_fraction(RATIO, CYCLE_TIME, 5, DURATION)
    assert mismatched != pytest.approx(RATIO, abs=0.02)
