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
    Q_min=5.0,
    Q_max=100.0,
    C_f=60.0,
    C_c=306.0,
    h_fc=2.0,
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


def test_history_records_the_applied_rate_not_the_command():
    """The estimator is fed _applied_Q for the same reason: a lid-open pause
    means the plant did not receive what the controller asked for, and a fit
    against the command would attribute the resulting cooling to the model."""
    c = Controller(dict(CONFIG), "C", dict(CYCLE))
    c.set_target(110.0)
    c.update(100.0)
    c.set_output(AppliedOutput(0.0, OutputSource.LID_OPEN, 1.0))
    # The rate the plant received for the interval about to be measured, before
    # the next update() assumes a freshly solved rate for the interval after.
    applied_before_solve = c._applied_Q
    c.update(100.0)
    _t, _temp, q_applied = c.cook_history()[-1]
    assert q_applied == pytest.approx(applied_before_solve)
    assert q_applied != pytest.approx(c._last_Q)


def test_history_is_bounded(monkeypatch):
    """Only the deque's bound is under test here, not the NLP -- the solve is
    stubbed to keep _HISTORY_MAX-scale runs fast."""
    c = Controller(dict(CONFIG), "C", dict(CYCLE))
    monkeypatch.setattr(c.mpc, "make_step", lambda x: np.array([[50.0]]))
    c.set_target(110.0)
    for _ in range(_HISTORY_MAX + 50):
        c.update(100.0)
    assert len(c.cook_history()) == _HISTORY_MAX


def test_history_keeps_the_most_recent_rows_when_it_overflows(monkeypatch):
    """Only the deque's overflow order is under test here, not the NLP -- see
    test_history_is_bounded for why the solve is stubbed."""
    c = Controller(dict(CONFIG), "C", dict(CYCLE))
    monkeypatch.setattr(c.mpc, "make_step", lambda x: np.array([[50.0]]))
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
