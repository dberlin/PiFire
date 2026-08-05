"""_calculate_gains must never produce a sign-flipped integral gain, and
update() must never divide by an unfloored dt of zero."""

import math
import time

import pytest

from controller.pid_base import PIDControllerBase
from controller.pid_sp import Controller as PIDSPController


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


def test_update_twice_at_the_same_clock_value_stays_finite(monkeypatch):
    fixed_time = 1_700_000_000.0
    monkeypatch.setattr(time, "time", lambda: fixed_time)

    config = {"PB": 60.0, "Ti": 180.0, "Td": 45.0}
    cycle_data = {"HoldCycleTime": 1}
    controller = PIDSPController(config, "F", cycle_data)
    controller.set_target(225.0)

    controller.update(150.0)
    result = controller.update(155.0)

    assert math.isfinite(result)
    assert -1e6 < result < 1e6
