"""Pins the ONE logging idiom the display process uses.

Mirrors tests/unit/runtime/test_logger_idiom.py for the display side:

  - CONFIGURE happens once per process via `common.common.create_logger`
    (display_process.py), for the same two names the controller uses.
  - ACQUIRE inside a display driver happens through the loggers handed to the
    constructor by `build_display()`, never through a module-level
    `logging.getLogger("events"|"control")` inside the driver.
  - Both constructor parameters default to the correctly named logger, so an
    un-injected driver still writes to the operator's log files and no driver
    attribute can be None.
  - The attribute names are honest: `eventLogger` is the "events" logger and
    `controlLogger` is the "control" logger. Before this, `_base_flex` and
    `qtquick_flex` bound `self.eventLogger` to the *control* logger, so every
    operator-facing message the flex drivers emitted (lost IP address,
    backlight fallback, screen cleared) landed in control.log -- which is
    configured at ERROR in production, i.e. the `.info` ones were invisible.
"""

import ast
import logging
import pathlib

import pytest

import display._base_flex as base_flex
from tests.ui._driver_helpers import RecordingLogger

_DISPLAY_ROOT = pathlib.Path(__file__).resolve().parents[2] / "display"

FULL_DEV_PINS = {
    "display": {"dc": 24, "led": 5, "rst": 25},
    "input": {"up_clk": 16, "down_dt": 20, "enter_sw": 21},
}


class _StubFlexDriver(base_flex.DisplayBase):
    """Smallest possible real DisplayBase subclass: the four hardware/asset
    init hooks are no-ops, so construction exercises the base's own state
    setup (including logger resolution) and nothing else."""

    display_profile = "profile_1"

    def _init_framework(self):
        pass

    def _init_display_canvas(self):
        pass

    def _init_input(self):
        pass

    def _init_display_device(self):
        pass


class _UnreachableSocket:
    """socket.socket stand-in whose connect() fails, driving _init_globals'
    IP-lookup failure branch. close() must still work: the real code closes in
    a `finally`."""

    def settimeout(self, _timeout):
        pass

    def connect(self, _address):
        raise OSError("network unreachable")

    def getsockname(self):  # pragma: no cover - connect() always raises first
        raise AssertionError("connect() should have failed")

    def close(self):
        pass


@pytest.fixture
def flex_env(monkeypatch):
    """DisplayBase.__init__ reads settings for real_hardware; force it False so
    no driver path reaches rpi_backlight/GPIO on this box (same precedent as
    tests/ui/test_pygame_qt_drivers.py's _make_dsi)."""
    monkeypatch.setattr(base_flex, "is_real_hardware", lambda: False)
    return monkeypatch


def _make_flex(**kwargs):
    return _StubFlexDriver(dev_pins=FULL_DEV_PINS, buttonslevel="HIGH", rotation=0, units="F", config={}, **kwargs)


def test_a_driver_built_without_loggers_gets_the_named_ones(flex_env):
    """No injection still gives real loggers -- not None, and the right names."""
    driver = _make_flex()

    assert driver.eventLogger is logging.getLogger("events")
    assert driver.controlLogger is logging.getLogger("control")
    # The names are the ones display_process.py's create_logger calls configure,
    # so an un-injected driver writes to the operator-visible files rather than
    # to nothing.
    driver.eventLogger.debug("usable")
    driver.controlLogger.debug("usable")


def test_a_caller_can_substitute_both_loggers(flex_env):
    events, control = RecordingLogger(), RecordingLogger()

    driver = _make_flex(event_log=events, control_log=control)

    assert driver.eventLogger is events
    assert driver.controlLogger is control


def test_an_operator_facing_failure_is_reported_to_the_event_log(flex_env):
    """A lost IP address changes what the user sees on the panel (127.0.0.1),
    so it belongs in events.log, the log a user reads -- not in control.log,
    where the flex drivers used to send it."""
    flex_env.setattr(base_flex.socket, "socket", lambda *a, **k: _UnreachableSocket())
    events, control = RecordingLogger(), RecordingLogger()

    _make_flex(event_log=events, control_log=control)

    assert ("error", "Unable to get IP address of the system.") in events.calls
    assert control.calls == []


def test_the_none_display_holds_both_loggers():
    """Every driver -- including the fallback one build_display() substitutes
    on a hardware failure -- accepts and holds both loggers."""
    from display.none import Display

    default = Display(dev_pins={}, buttonslevel="HIGH", rotation=0, units="F", config={})
    assert default.eventLogger is logging.getLogger("events")
    assert default.controlLogger is logging.getLogger("control")

    events, control = RecordingLogger(), RecordingLogger()
    injected = Display(
        dev_pins={}, buttonslevel="HIGH", rotation=0, units="F", config={}, event_log=events, control_log=control
    )
    assert injected.eventLogger is events
    assert injected.controlLogger is control


def test_the_qtquick_dispatch_instance_holds_both_loggers():
    """qtquick_flex deliberately skips super().__init__(), and its dispatch-only
    instance skips __init__ entirely -- both paths must still end up with real
    loggers rather than None."""
    from display.qtquick_flex import Display

    default = Display.for_dispatch({}, "F")
    assert default.eventLogger is logging.getLogger("events")
    assert default.controlLogger is logging.getLogger("control")

    events, control = RecordingLogger(), RecordingLogger()
    injected = Display.for_dispatch({}, "F", event_log=events, control_log=control)
    assert injected.eventLogger is events
    assert injected.controlLogger is control


def test_no_display_module_reaches_for_a_named_logger_itself():
    """Acquiring "events"/"control" by name inside a driver is what let an
    attribute called `eventLogger` hold the control logger for years: the name
    at the acquisition site and the name of the attribute it was assigned to
    were never checked against each other. Injection is now the only way in."""
    offenders = []
    for path in sorted(_DISPLAY_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "getLogger" or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and first.value in ("events", "control"):
                offenders.append(f"{path.name}:{node.lineno}")

    assert offenders == []
