"""Pins controller_matrix.py's auger PWM model against two regressions.

1. The harness used to re-anchor its duty-cycle window every controller
   re-solve (`cycle_anchor = t`), which only reproduces production for
   controllers whose re-solve period equals HoldCycleTime. MPC re-solves every
   5s against a 20s cycle_time, so the realized auger on-fraction came out far
   higher than the requested ratio.
2. Sampling the toggle as a boolean once per simulated second (GrillSim's
   integration step) quantizes fuel delivery to whichever side of a
   transition the sample lands on, biasing the realized duty above the
   requested ratio -- worst at small ratios, e.g. ~21% high at `u_min`, which
   both controllers sit at for the entire 225 F scenarios.

`_auger_toggle_tick` ports the free-running toggle in
`ControlMode._auger_cycle_tick` (controller/runtime/modes/base.py), which is
independent of the caller's re-solve cadence, and returns the toggle's exact
fractional on-time over the caller's 1 s window rather than a boolean sample
of it (paired with `GrillSim.step`'s `float(auger_on)`, which accepts that
fraction directly).

The integration test below drives the real `run_scenario` loop (not just the
extracted helper) with a stub controller whose `get_control_period()` varies,
so a regression that reintroduces re-anchoring inside the loop itself would
still be caught -- exercising `_auger_toggle_tick` in isolation would not
catch that class of regression. Tolerances are tight enough to fail under
boolean-per-tick sampling (tested at `ratio=0.35` and at `ratio=u_min`, the
worst case). The legacy-model test is a negative control proving the
period-mismatch fix is not vacuous: the pre-fix model passes when the control
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
U_MIN = controller_matrix.CYCLE_DATA["u_min"]
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

    def step(self, auger_on, fan_frac):
        del fan_frac
        self.on_fracs.append(float(auger_on))


@pytest.mark.parametrize(
    "control_period,ratio",
    [
        (20, RATIO),
        (5, RATIO),
        # u_min is where boolean-per-tick sampling costs the most relative
        # error (~21% high, both controllers sit here for the whole 225 F
        # scenarios), so it is the case most likely to catch a regression
        # back to boolean sampling.
        (20, U_MIN),
        (5, U_MIN),
    ],
)
def test_run_scenario_realized_duty_matches_ratio_regardless_of_control_period(control_period, ratio, monkeypatch):
    """Drives the real run_scenario loop -- not just _auger_toggle_tick in
    isolation -- so a regression that reintroduces re-anchoring, or reintroduces
    boolean-per-tick sampling, inside the loop itself would still be caught."""
    name = "_stub_toggle_probe"
    fake_mod = types.ModuleType(f"controller.{name}")
    fake_mod.Controller = _StubController
    monkeypatch.setitem(sys.modules, f"controller.{name}", fake_mod)
    monkeypatch.setitem(controller_matrix.CONTROLLER_CONFIGS, name, {"_period": control_period, "_ratio": ratio})
    monkeypatch.setattr(controller_matrix, "GrillSim", _FakePlant)
    _FakePlant.instances.clear()

    scenario = controller_matrix.Scenario(name, DURATION, [(0, 999.0)])
    controller_matrix.run_scenario(name, scenario, seed=0)

    plant = _FakePlant.instances[-1]
    realized = sum(plant.on_fracs) / len(plant.on_fracs)
    # Tight enough to fail boolean-per-tick sampling (~0.0136 off at ratio=0.35,
    # ~0.0318 off at ratio=u_min); the fractional model should be exact modulo
    # floating point.
    assert realized == pytest.approx(ratio, abs=0.005)


def test_legacy_model_actually_fails_the_mismatched_period_case():
    """Negative control: proves the test above is not vacuous by showing the
    pre-fix model passes at period==cycle_time and fails at period=5."""
    matched = _legacy_on_fraction(RATIO, CYCLE_TIME, CYCLE_TIME, DURATION)
    assert matched == pytest.approx(RATIO, abs=0.02)

    mismatched = _legacy_on_fraction(RATIO, CYCLE_TIME, 5, DURATION)
    assert mismatched != pytest.approx(RATIO, abs=0.02)
