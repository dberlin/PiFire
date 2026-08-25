"""The controller keeps the record its own refit will consume."""

from types import SimpleNamespace


import numpy as np
import pytest

from controller.applied_output import AppliedOutput, OutputSource
from controller.model_learning.grey_runtime import _HISTORY_MAX
from controller.mpc import Controller

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
CYCLE = {"u_min": 0.1, "u_max": 0.9}


def _stub_native_solver(monkeypatch, controller):
    horizon = int(controller.cfg["n_horizon"])
    result = SimpleNamespace(
        sequence_q=np.full(horizon, 0.5),
        sequence_residual=np.zeros(horizon),
        objective=1.0,
        diagnostics=SimpleNamespace(
            status=0,
            backend_status=0,
            iterations=1,
            solve_time_s=0.0,
            objective=1.0,
            kkt_residual=0.0,
            constraint_residual=0.0,
            warm_started=True,
        ),
    )
    monkeypatch.setattr(type(controller.mpc), "solve", lambda self, state, **kwargs: result)


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
    applied_before_solve = c.get_status()["applied_combustion_load"]
    c.update(100.0)
    _t, _temp, load_applied = c.cook_history()[-1]
    assert load_applied == pytest.approx(applied_before_solve)
    assert load_applied != pytest.approx(c.get_status()["last_combustion_load"])


def test_history_is_bounded(monkeypatch):
    """Only the deque's bound is under test here, not the NLP -- the solve is
    stubbed to keep _HISTORY_MAX-scale runs fast."""
    c = Controller(dict(CONFIG), "C", dict(CYCLE))
    _stub_native_solver(monkeypatch, c)
    c.set_target(110.0)
    for _ in range(_HISTORY_MAX + 50):
        c.update(100.0)
    assert len(c.cook_history()) == _HISTORY_MAX


def test_history_keeps_the_most_recent_rows_when_it_overflows(monkeypatch):
    """Only the deque's overflow order is under test here, not the NLP -- see
    test_history_is_bounded for why the solve is stubbed."""
    c = Controller(dict(CONFIG), "C", dict(CYCLE))
    _stub_native_solver(monkeypatch, c)
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
