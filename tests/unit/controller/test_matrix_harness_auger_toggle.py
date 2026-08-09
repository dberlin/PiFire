"""Pins controller_matrix.py to the production framed pulse scheduler.

The harness receives controller requests on a configurable solve cadence, but
all executable experiments must realize those requests through the same 2 s
pulse / 20 s frame scheduler.  These probes use a non-thermal plant so the
recorded commands expose scheduler timing directly.
"""

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

import docs.superpowers.experiments.controller_matrix as controller_matrix

PULSE_FRAME_S = 20
PULSE_S = 2
LOW_DUTY = 0.05
DURATION = 81


class _StubController:
    """Constant-request controller; it intentionally has no mode capability."""

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
    """Records each actual auger command without thermal dynamics."""

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


def _run_stub(control_period, monkeypatch, *, cycle_config=None):
    name = "_stub_pulse_probe"
    fake_mod = types.ModuleType(f"controller.{name}")
    fake_mod.Controller = _StubController
    monkeypatch.setitem(sys.modules, f"controller.{name}", fake_mod)
    monkeypatch.setattr(controller_matrix, "GrillSim", _FakePlant)
    _FakePlant.instances.clear()

    scenario = controller_matrix.Scenario(name, DURATION, [(0, 999.0)])
    row = controller_matrix.run_scenario(
        name,
        scenario,
        seed=0,
        config={"_period": control_period, "_ratio": LOW_DUTY},
        cycle_config=cycle_config,
    )
    return row, _FakePlant.instances[-1]


@pytest.mark.parametrize("control_period", [5, 20])
def test_low_duty_credit_becomes_two_second_pulses_on_every_solve_cadence(control_period, monkeypatch):
    row, plant = _run_stub(control_period, monkeypatch)

    assert row["effective_run"]["actuation_mode"] == "framed_pulse"
    assert row["effective_run"]["pulse_timing"] == {
        "frame_seconds": float(PULSE_FRAME_S),
        "pulse_seconds": float(PULSE_S),
    }
    assert [tick for tick, on in enumerate(plant.on_fracs) if on] == [20, 21, 60, 61]
    assert set(plant.on_fracs) <= {0.0, 1.0}


def test_cycle_override_cannot_retime_the_framed_scheduler(monkeypatch):
    row, plant = _run_stub(20, monkeypatch, cycle_config={"SmokeOnCycleTime": 4, "u_max": 0.7})

    assert row["effective_run"]["cycle_config"]["SmokeOnCycleTime"] == 4
    assert row["effective_run"]["pulse_timing"] == {"frame_seconds": 20.0, "pulse_seconds": 2.0}
    assert [tick for tick, on in enumerate(plant.on_fracs) if on] == [20, 21, 60, 61]
