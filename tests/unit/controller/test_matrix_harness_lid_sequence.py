"""Pins lid preemption in controller_matrix.py's framed scheduler path."""

import sys
import types

from common.defaults import default_settings
from controller.applied_output import OutputSource
from tools.experiments import controller_matrix

PULSE_FRAME_S = 20
RATIO = 0.5
PAUSE = default_settings()["cycle_data"]["LidOpenPauseTime"]
LID_START = 2 * PULSE_FRAME_S
LID_DURATION = PAUSE + 2 * PULSE_FRAME_S
DURATION = LID_START + LID_DURATION + 3 * PULSE_FRAME_S


class _StubController:
    """Constant request controller with no actuation-mode capability."""

    instances = []

    def __init__(self, config, units, cycle_data):
        del units, cycle_data
        self._ratio = config["_ratio"]
        self.reports = []
        _StubController.instances.append(self)

    def set_target(self, set_point):
        del set_point

    def get_control_period(self):
        return PULSE_FRAME_S

    def update(self, temp_f):
        del temp_f
        return self._ratio

    def set_output(self, applied):
        self.reports.append(applied)


class _FakePlant:
    """Records the requested plant inputs for one deterministic run."""

    instances = []

    def __init__(self, seed=0):
        del seed
        self.on_fracs = []
        self.fan_fracs = []
        self.lid_opens = []
        _FakePlant.instances.append(self)

    def measured(self):
        return 0.0

    def step(self, auger_on, fan_frac, lid_open=False):
        self.on_fracs.append(float(auger_on))
        self.fan_fracs.append(float(fan_frac))
        self.lid_opens.append(bool(lid_open))


def _run_stub(monkeypatch):
    name = "_stub_lid_probe"
    fake_mod = types.ModuleType(f"controller.{name}")
    fake_mod.Controller = _StubController
    monkeypatch.setitem(sys.modules, f"controller.{name}", fake_mod)
    monkeypatch.setattr(controller_matrix, "GrillSim", _FakePlant)
    _StubController.instances.clear()
    _FakePlant.instances.clear()

    scenario = controller_matrix.Scenario(name, DURATION, [(0, 999.0)], [(LID_START, LID_DURATION)])
    row = controller_matrix.run_scenario(name, scenario, seed=0, config={"_ratio": RATIO})
    return row, _StubController.instances[-1], _FakePlant.instances[-1]


def test_lid_preemption_resets_framed_credit_before_fresh_pulses_resume(monkeypatch):
    row, core, plant = _run_stub(monkeypatch)

    assert row["effective_run"]["actuation_mode"] == "framed_pulse"
    assert row["effective_run"]["pulse_timing"] == {"frame_seconds": 20.0, "pulse_seconds": 2.0}
    # A lid reset preempts the scheduler at t=40. The configured pause keeps
    # the auger off through t=45; after it ends, a fresh 50% frame begins.
    assert plant.on_fracs[LID_START : LID_START + PAUSE] == [0.0] * PAUSE
    assert plant.on_fracs[LID_START + PAUSE : LID_START + PAUSE + 10] == [1.0] * 10
    assert not any(report.source is OutputSource.LID_OPEN for report in core.reports)


def test_lid_event_and_fan_inhibit_still_follow_the_configured_pause(monkeypatch):
    _, _, plant = _run_stub(monkeypatch)

    assert plant.lid_opens[LID_START : LID_START + LID_DURATION] == [True] * LID_DURATION
    assert not any(plant.lid_opens[:LID_START])
    assert not any(plant.lid_opens[LID_START + LID_DURATION :])
    assert plant.fan_fracs[LID_START : LID_START + PAUSE] == [0.0] * PAUSE
    assert all(frac > 0.0 for frac in plant.fan_fracs[LID_START + PAUSE : LID_START + LID_DURATION])
