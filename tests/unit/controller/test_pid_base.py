"""_calculate_gains must never produce a sign-flipped integral gain, and
update() must never divide by an unfloored dt of zero."""

import math

import pytest

from controller.pid import Controller as PIDController
from controller.pid_base import PIDControllerBase
from controller.pid_sp import Controller as PIDSPController
from controller.runtime.clock import ManualClock


class _Gains(PIDControllerBase):
    def __init__(self):
        pass


@pytest.mark.parametrize("ti", [0, 0.0, -1.0, -180.0])
def test_non_positive_ti_disables_the_integral_term(ti):
    gains = _Gains()
    gains._calculate_gains(60.0, ti, 45.0)
    assert gains.ki == 0


def test_positive_ti_is_unchanged():
    gains = _Gains()
    gains._calculate_gains(60.0, 180.0, 45.0)
    assert gains.ki == pytest.approx((-1 / 60.0) / 180.0)


def test_zero_pb_disables_the_proportional_term():
    gains = _Gains()
    gains._calculate_gains(0, 180.0, 45.0)
    assert gains.kp == 0


def _pid(clock):
    core = PIDController({"PB": 20.0, "Ti": 10.0, "Td": 5.0, "center": 0.5}, "F", {}, clock=clock)
    core.set_target(200.0)
    return core


@pytest.mark.parametrize("jump", [-3600.0, 3600.0])
def test_pid_wall_jump_preserves_response(jump):
    a = ManualClock(1_700_000_000.0, monotonic_start=100.0)
    b = ManualClock(1_700_000_000.0, monotonic_start=100.0)
    left, right = _pid(a), _pid(b)
    for index, measured in enumerate((190.0, 195.0, 198.0)):
        a.advance(20.0)
        b.advance(20.0)
        if index == 1:
            b.jump_wall(jump)
        assert right.update(measured) == pytest.approx(left.update(measured))
        dl, dr = left.trace_diagnostics(), right.trace_diagnostics()
        assert dr.observed_dt_seconds == dl.observed_dt_seconds == 20.0
        assert dr.integral_term == pytest.approx(dl.integral_term)
        assert dr.derivative_term == pytest.approx(dl.derivative_term)


def test_pid_target_reset_uses_monotonic_origin():
    clock = ManualClock(1_700_000_000.0, monotonic_start=100.0)
    core = _pid(clock)
    clock.advance(20.0)
    core.update(198.0)
    clock.jump_wall(-3600.0)
    core.set_target(210.0)
    clock.advance(2.0)
    core.update(205.0)
    assert core.trace_diagnostics().observed_dt_seconds == 2.0
    assert core.trace_diagnostics().integral_accumulator == -10.0


@pytest.mark.parametrize("controller_cls", [PIDController, PIDSPController])
def test_duplicate_pid_readings_preserve_response_across_wall_jump(controller_cls):
    clocks = [ManualClock(1_700_000_000.0, monotonic_start=100.0) for _ in range(2)]
    cores = [controller_cls({"PB": 60.0, "Ti": 180.0, "Td": 45.0}, "F", {}, clock=c) for c in clocks]
    for core in cores:
        core.set_target(225.0)
        core.update(150.0)
    clocks[1].jump_wall(-3600.0)
    results = [core.update(155.0) for core in cores]
    assert all(math.isfinite(result) for result in results)
    assert results[0] == results[1]
    assert cores[0].trace_diagnostics() == cores[1].trace_diagnostics()
    for clock in clocks:
        clock.advance(20.0)
    assert cores[0].update(160.0) == cores[1].update(160.0)


def test_pid_restart_does_not_restore_old_elapsed_state():
    old_clock = ManualClock(1_700_000_000.0, monotonic_start=10_000.0)
    old = _pid(old_clock)
    old_clock.advance(20.0)
    old.update(198.0)
    clock = ManualClock(1_700_000_000.0, monotonic_start=5.0)
    core = _pid(clock)
    clock.advance(2.0)
    core.update(198.0)
    assert core.trace_diagnostics().observed_dt_seconds == 2.0
    assert core.trace_diagnostics().integral_accumulator == -4.0
