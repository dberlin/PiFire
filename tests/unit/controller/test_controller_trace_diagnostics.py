from __future__ import annotations

import pytest

from common.control_trace import ControllerBranch
from controller import pid, pid_sp
from controller.base import PidSpTraceDiagnostics, PidTraceDiagnostics

CYCLE_DATA = {"u_min": 0.1, "u_max": 0.9}


def test_pid_trace_diagnostics_reproduce_completed_update(monkeypatch):
    clock = iter((0.0, 0.0, 0.0, 100.0, 102.0))
    monkeypatch.setattr(pid.time, "time", lambda: next(clock))
    core = pid.Controller({"PB": 20.0, "Ti": 10.0, "Td": 5.0, "center": 0.5}, "F", dict(CYCLE_DATA))
    core.set_target(200.0)
    core.last = 190.0
    core.last_update = 98.0

    assert core.update(198.0) == pytest.approx(-0.38)

    diagnostic = core.trace_diagnostics()
    assert isinstance(diagnostic, PidTraceDiagnostics)
    assert diagnostic.error == pytest.approx(-2.0)
    assert diagnostic.proportional_term == pytest.approx(0.6)
    assert diagnostic.integral_accumulator == pytest.approx(-4.0)
    assert diagnostic.integral_term == pytest.approx(0.02)
    assert diagnostic.derivative_input == pytest.approx(8.0)
    assert diagnostic.derivative_state == pytest.approx(4.0)
    assert diagnostic.raw_output == pytest.approx(-0.38)
    assert diagnostic.final_output == pytest.approx(-0.38)
    assert diagnostic.previous_temperature == pytest.approx(190.0)
    assert diagnostic.previous_update_time == pytest.approx(98.0)


def test_pid_sp_trace_diagnostics_reproduce_completed_update(monkeypatch):
    clock = iter((0.0, 0.0, 0.0, 0.0, 100.0))
    monkeypatch.setattr(pid_sp.time, "time", lambda: next(clock))
    # tau/theta are no longer configuration: the identifier learns them, so a
    # controller built here carries no model at all.
    core = pid_sp.Controller(
        {"PB": 20.0, "Ti": 10.0, "Td": 5.0, "stable_window": 12.0},
        "F",
        dict(CYCLE_DATA),
    )
    core.set_target(200.0)
    core.last = 190.0
    core.last_update = 98.0
    core.last_set_time = 0.0
    core.new_target = False

    core.update(198.0)

    diagnostic = core.trace_diagnostics()
    assert isinstance(diagnostic, PidSpTraceDiagnostics)
    assert diagnostic.error == pytest.approx(-2.0)
    assert diagnostic.measured_rate == pytest.approx(4.0)
    # This controller no longer extrapolates a future temperature from a
    # configured tau/theta. The Smith predictor removes identified dead time
    # from the reading instead, and nothing is identified on a fresh core, so
    # the temperature it selects is the measured one and the error taken from it
    # is the plain error. tau/theta read zero for the same reason.
    assert diagnostic.predicted_temperature == pytest.approx(198.0)
    assert diagnostic.predicted_error == pytest.approx(-2.0)
    assert diagnostic.tau_seconds == pytest.approx(0.0)
    assert diagnostic.theta_seconds == pytest.approx(0.0)
    assert diagnostic.stable_window_seconds == pytest.approx(12.0)
    assert diagnostic.branch is ControllerBranch.NONE
    assert diagnostic.previous_temperature == pytest.approx(190.0)
    assert diagnostic.previous_update_time == pytest.approx(98.0)


@pytest.mark.parametrize(
    ("current", "new_target", "last", "expected"),
    [
        (198.0, True, 0.0, ControllerBranch.INITIALIZATION),
        (150.0, False, 190.0, ControllerBranch.FULL_HEAT),
        (199.0, True, 190.0, ControllerBranch.TARGET_REACHED),
        (185.0, False, 190.0, ControllerBranch.RESET),
        (230.0, False, 190.0, ControllerBranch.OVERSHOOT),
    ],
)
def test_pid_sp_trace_diagnostics_identify_each_control_branch(monkeypatch, current, new_target, last, expected):
    clock = iter((0.0, 0.0, 0.0, 0.0, 100.0))
    monkeypatch.setattr(pid_sp.time, "time", lambda: next(clock))
    core = pid_sp.Controller({"PB": 20.0, "tau": 10.0, "theta": 5.0, "stable_window": 12.0}, "F", dict(CYCLE_DATA))
    core.set_target(200.0)
    core.last = last
    core.last_update = 98.0
    core.last_set_time = 0.0
    core.new_target = new_target

    core.update(current)

    diagnostic = core.trace_diagnostics()
    assert diagnostic.branch is expected
    assert diagnostic.new_target_before is new_target
    assert diagnostic.new_target_after is core.new_target
