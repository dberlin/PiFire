"""Pins controller_matrix.py's lid-open model against hold.py's real sequence.

hold.py's lid-open pause is not "auger off for the whole window". At the
detection instant (hold.py:247-264) the auger is forced off and exactly one
AppliedOutput(ratio=0.0, source=LID_OPEN) is reported; that block clears
target_temp_achieved (hold.py:266), and the pause's own heat loss keeps it
clear, so hold.py:234 cannot re-arm it -- and thus this report cannot
re-fire -- until the plant is back at setpoint. For the rest of the pause
hold.py:171-173 pins the commanded ratio to u_min, hold.py:206-217 reports
that u_min once per solve, and hold.py:228's unconditional call to
_auger_cycle_tick (base.py:118-147, which has no lid gate) keeps the physical
auger cycling at that u_min duty rather than held off.

Nor does the pause last as long as the lid is open. hold.py:265 and hold.py:296
both arm it for exactly LidOpenPauseTime seconds and hold.py:269-271 clears it
on that timer, restarting the fan, with no reference to the lid. A lid held open
longer than the timer therefore ends with the controller at full authority and
the fan running while the chamber is still leaking heat, so the scenarios below
open the lid for longer than the pause to exercise all three phases.

A test asserting only "the auger is off at some point during the pause"
would pass a model that holds it off for the ENTIRE pause just as easily as
this one; the assertions below pin the exact sequence of reports and the
auger's on/off pattern, and the negative controls demonstrate that a
whole-pause-off model and a pause-lasts-as-long-as-the-lid model fail them.
"""

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

import docs.superpowers.experiments.controller_matrix as controller_matrix
from controller.applied_output import OutputSource

CYCLE_TIME = controller_matrix.CYCLE_DATA["HoldCycleTime"]
U_MIN = controller_matrix.CYCLE_DATA["u_min"]
# Distinct from U_MIN so a report that should show u_min but leaks the
# controller's own output (or vice versa) is caught.
RATIO = 0.5
PAUSE = controller_matrix.LID_PAUSE_S
LID_START = 2 * CYCLE_TIME
# Deliberately longer than the pause, so the third phase -- lid still open,
# actuators released -- is inside the window under test.
LID_DURATION = PAUSE + 2 * CYCLE_TIME
DURATION = LID_START + LID_DURATION + 3 * CYCLE_TIME


class _StubController:
    """Constant-ratio controller with a set_output capability, exactly like
    the real controllers this harness drives -- records every applied-output
    report it is given, in order."""

    instances = []

    def __init__(self, config, units, cycle_data):
        del units, cycle_data
        self._ratio = config["_ratio"]
        self.reports = []
        _StubController.instances.append(self)

    def set_target(self, set_point):
        del set_point

    def get_control_period(self):
        return CYCLE_TIME

    def update(self, temp_f):
        del temp_f
        return self._ratio

    def set_output(self, applied):
        self.reports.append(applied)


class _FakePlant:
    """Records the exact auger on-fraction the real run_scenario loop drives
    each window; thermal physics is irrelevant here, only the sequence of
    reports and auger commands is under test."""

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


def _run_stub(monkeypatch, lid_open):
    name = "_stub_lid_probe"
    fake_mod = types.ModuleType(f"controller.{name}")
    fake_mod.Controller = _StubController
    monkeypatch.setitem(sys.modules, f"controller.{name}", fake_mod)
    monkeypatch.setitem(controller_matrix.CONTROLLER_CONFIGS, name, {"_ratio": RATIO})
    monkeypatch.setattr(controller_matrix, "GrillSim", _FakePlant)
    _StubController.instances.clear()
    _FakePlant.instances.clear()

    scenario = controller_matrix.Scenario(name, DURATION, [(0, 999.0)], lid_open)
    controller_matrix.run_scenario(name, scenario, seed=0)
    return _StubController.instances[-1], _FakePlant.instances[-1]


def test_lid_open_reports_zero_once_then_u_min_while_cycling(monkeypatch):
    core, plant = _run_stub(monkeypatch, [(LID_START, LID_DURATION)])

    # Reports are (timestamp, ratio, source). The seed report at t=0 (before
    # the loop) and every non-lid solve report RATIO/CONTROLLER; only the
    # solves that land inside the window pin to u_min, and exactly one report
    # in the whole run is the ratio=0.0 detection instant.
    seq = [(r.timestamp, r.ratio, r.source) for r in core.reports]

    zero_reports = [r for r in seq if r[1] == 0.0]
    assert zero_reports == [(float(LID_START), 0.0, OutputSource.LID_OPEN)], (
        f"expected exactly one ratio=0.0 report, at the lid-open detection instant -- got {zero_reports}"
    )

    lid_open_reports = [r for r in seq if r[2] == OutputSource.LID_OPEN]
    # The solve landing exactly on LID_START emits the 0.0 detection report
    # plus its own u_min report; every later solve inside the PAUSE emits
    # u_min. Solves after the pause expires are the controller's again, even
    # though the lid is still open.
    pause_solves = [LID_START + k for k in range(0, PAUSE, CYCLE_TIME)]
    assert lid_open_reports == [
        (float(LID_START), U_MIN, OutputSource.LID_OPEN),
        (float(LID_START), 0.0, OutputSource.LID_OPEN),
    ] + [(float(t), U_MIN, OutputSource.LID_OPEN) for t in pause_solves[1:]], (
        f"lid-open report sequence drifted from hold.py's: {lid_open_reports}"
    )

    controller_reports = [r for r in seq if r[2] == OutputSource.CONTROLLER]
    assert controller_reports and all(ratio == RATIO for _, ratio, _ in controller_reports), (
        f"a non-lid solve reported something other than the controller's own ratio: {controller_reports}"
    )
    # hold.py releases the actuators on the timer, so the solves between the
    # pause expiring and the lid closing carry the controller's own answer.
    released_while_open = [t for t, _, _ in controller_reports if PAUSE <= t - LID_START < LID_DURATION]
    assert released_while_open, (
        "no solve ran at full controller authority while the lid was still open -- the pause is "
        "being modelled as lasting the whole lid window rather than LidOpenPauseTime"
    )

    # The auger is forced off only at the detection instant...
    assert plant.on_fracs[LID_START] == 0.0
    # ...and keeps cycling (not held off) for the rest of the pause.
    rest_of_pause = plant.on_fracs[LID_START + 1 : LID_START + PAUSE]
    assert any(frac > 0.0 for frac in rest_of_pause), (
        f"auger was held off for the whole pause instead of cycling at the pinned u_min ratio: {rest_of_pause}"
    )
    # The lid is open on the plant for exactly the window, so the chamber
    # leaks heat for the whole pause rather than only at the detection instant.
    assert plant.lid_opens[LID_START : LID_START + LID_DURATION] == [True] * LID_DURATION
    assert not any(plant.lid_opens[:LID_START])
    assert not any(plant.lid_opens[LID_START + LID_DURATION :])
    # The fan is cut for exactly the pause -- hold.py:263 stops it at detection
    # and hold.py:271 restarts it on expiry, which is the second half of the
    # lid window here.
    assert plant.fan_fracs[LID_START : LID_START + PAUSE] == [0.0] * PAUSE
    assert all(frac > 0.0 for frac in plant.fan_fracs[LID_START + PAUSE : LID_START + LID_DURATION])
    assert all(frac > 0.0 for frac in plant.fan_fracs[:LID_START])
    # Realized duty over the pause (excluding the forced-off first tick)
    # should track u_min, not 0.0. The window does not land on a whole number
    # of 20 s cycles measured from the forced-off tick, so this is close to
    # u_min rather than exact.
    assert sum(rest_of_pause) / len(rest_of_pause) == pytest.approx(U_MIN, abs=0.02)


def test_whole_pause_off_model_fails_the_pinned_sequence(monkeypatch):
    """Negative control: reintroduces the bug this file guards against, by
    making every lid-open tick look like the detection instant instead of
    just the first one -- auger forced off and ratio=0.0 reported on every
    tick of the pause, never cycling at u_min. That is the behaviour the
    harness had before this fix; the pre-fix code also suppressed the
    per-solve report during the pause, which this reintroduces only the
    auger-and-zero-report half of. A test that only checks "the auger is off
    at some point during the pause" would still pass this buggy model; the
    sequence pinned above does not, which is why it is the one that matters.
    """
    monkeypatch.setattr(controller_matrix, "_lid_pause_start_at", controller_matrix._lid_paused_at)
    core, plant = _run_stub(monkeypatch, [(LID_START, LID_DURATION)])

    window = plant.on_fracs[LID_START : LID_START + PAUSE]
    # The weak assertion a shallower test might make -- passes under the bug.
    assert any(frac == 0.0 for frac in window)
    # What the weak assertion misses: the auger never comes back on at all.
    assert all(frac == 0.0 for frac in window)

    zero_reports = [r for r in core.reports if r.ratio == 0.0 and r.source == OutputSource.LID_OPEN]
    assert len(zero_reports) > 1, "the buggy model should report ratio=0.0 on more than one tick"


def test_pause_lasting_the_whole_lid_window_fails_the_pinned_sequence(monkeypatch):
    """Negative control for the pause timer: makes the pause run as long as the
    lid is open, the model the harness carried before LidOpenPauseTime was
    honoured. hold.py never does this -- its timer is armed once and expires on
    its own -- and the released-while-open solves the test above requires are
    exactly what this model cannot produce."""
    monkeypatch.setattr(controller_matrix, "_lid_paused_at", controller_matrix._lid_open_at)
    core, plant = _run_stub(monkeypatch, [(LID_START, LID_DURATION)])

    lid_window = range(LID_START, LID_START + LID_DURATION)
    assert plant.fan_fracs[LID_START : LID_START + LID_DURATION] == [0.0] * LID_DURATION, (
        "the fan should stay off for the whole lid window under this model"
    )
    released_while_open = [
        r.timestamp for r in core.reports if r.source == OutputSource.CONTROLLER and int(r.timestamp) in lid_window
    ]
    assert not released_while_open, (
        f"this model was expected to keep the controller pinned for the whole window: {released_while_open}"
    )
