"""Unit tests for the one mode x status coupling: should_keep_power_on. Must
stay exactly equivalent to the old inline
`self.control["status"] == "monitor" and self.control["mode"] == Mode.ERROR`.
"""

from common.modes import Mode, StatusState
from controller.runtime.transitions import should_keep_power_on


def test_monitor_error_keeps_power_on():
    assert should_keep_power_on(Mode.ERROR, StatusState.MONITOR) is True


def test_active_error_powers_off():
    assert should_keep_power_on(Mode.ERROR, StatusState.ACTIVE) is False


def test_monitor_stop_does_not_keep_power_on():
    assert should_keep_power_on(Mode.STOP, StatusState.MONITOR) is False


def test_inactive_error_powers_off():
    assert should_keep_power_on(Mode.ERROR, StatusState.INACTIVE) is False


def test_unset_error_powers_off():
    assert should_keep_power_on(Mode.ERROR, StatusState.UNSET) is False
