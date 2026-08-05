"""The controller keeps the record its own refit will consume."""

import numpy as np
import pytest

from controller.applied_output import AppliedOutput, OutputSource
from controller.mpc import _HISTORY_MAX, Controller

CONFIG = dict(
    n_horizon=20,
    t_step=25.0,
    control_period=1.0,
    Q_w=1.0,
    R_dQ=0.02,
    C_c=306.0,
    h_amb=0.55,
    T_amb=20.0,
    enable_fan_input=True,
    fan_min_pct=40.0,
    fan_max_pct=100.0,
    est_q_temp=1e-2,
    est_q_dist=0.5,
    est_r_meas=0.04,
)
CYCLE = {"u_min": 0.1, "u_max": 0.9, "HoldCycleTime": 25}


def test_history_records_one_row_per_update():
    c = Controller(dict(CONFIG), "C", dict(CYCLE))
    c.set_target(110.0)
    for _ in range(5):
        c.update(100.0)
    assert len(c.cook_history()) == 5


def test_history_records_the_applied_normalized_load_not_the_command():
    c = Controller(dict(CONFIG), "C", dict(CYCLE))
    c.set_target(110.0)
    c.update(100.0)
    c.set_output(AppliedOutput(0.0, OutputSource.LID_OPEN, 1.0))
    applied_before_solve = c._applied_combustion_load
    c.update(100.0)
    _t, _temp, load_applied = c.cook_history()[-1]
    assert load_applied == pytest.approx(applied_before_solve)
    assert load_applied != pytest.approx(c._last_combustion_load)


def test_history_is_bounded(monkeypatch):
    """Only the deque's bound is under test here, not the NLP -- the solve is
    stubbed to keep _HISTORY_MAX-scale runs fast."""
    c = Controller(dict(CONFIG), "C", dict(CYCLE))
    monkeypatch.setattr(c.mpc, "make_step", lambda x: np.array([[0.5]]))
    c.set_target(110.0)
    for _ in range(_HISTORY_MAX + 50):
        c.update(100.0)
    assert len(c.cook_history()) == _HISTORY_MAX


def test_history_keeps_the_most_recent_rows_when_it_overflows(monkeypatch):
    """Only the deque's overflow order is under test here, not the NLP -- see
    test_history_is_bounded for why the solve is stubbed."""
    c = Controller(dict(CONFIG), "C", dict(CYCLE))
    monkeypatch.setattr(c.mpc, "make_step", lambda x: np.array([[0.5]]))
    c.set_target(110.0)
    for i in range(_HISTORY_MAX + 10):
        c.update(100.0 + i * 1e-3)
    temps = [row[1] for row in c.cook_history()]
    assert temps == sorted(temps)  # oldest dropped, order preserved


def test_history_survives_a_setpoint_change():
    """A setpoint change is not a new cook. The grill is the same grill, and
    the samples either side of the change are the excitation a fit wants most."""
    c = Controller(dict(CONFIG), "C", dict(CYCLE))
    c.set_target(110.0)
    c.update(100.0)
    c.set_target(150.0)
    c.update(100.0)
    assert len(c.cook_history()) == 2
