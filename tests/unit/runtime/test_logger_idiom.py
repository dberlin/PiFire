"""Pins the ONE logging idiom the controller runtime uses.

Rules being pinned:
  - CONFIGURE happens once per process via `common.common.create_logger`.
  - ACQUIRE inside the controller runtime happens through the injected
    `ControllerContext.event_log` / `.control_log`, never through a module
    global on the `control` entry-point script.
  - Those context fields always hold a usable logger, so no call site needs a
    None guard or a defensive try/except around a log call.
"""

import ast
import logging
import pathlib

from controller.runtime.context import ControllerContext, Devices
from controller.runtime.state import WorkCycleState
from controller.runtime.modes.monitor import MonitorMode
from controller.runtime.modes.shutdown import ShutdownMode
from tests.fakes.grill import FakeGrillPlatform
from tests.fakes.distance import FakeDistance
from tests.fakes.notifier import FakeNotifier
from tests.fakes.probes import FakeProbes
from tests.characterization.fixtures import base_settings, base_control, base_pellet_db
from controller.runtime.store import InMemoryStore
from controller.runtime.clock import ManualClock


_RUNTIME_ROOT = pathlib.Path(__file__).resolve().parents[3] / "controller"


class _RecordingLogger:
    """Substitutable stand-in for a stdlib logger."""

    def __init__(self):
        self.calls = []

    def _record(self, level):
        def log(message, *args, **kwargs):
            self.calls.append((level, message))

        return log

    def __getattr__(self, name):
        if name in ("debug", "info", "warning", "error", "exception", "critical"):
            return self._record(name)
        raise AttributeError(name)


def _ctx(event_log=None, control_log=None):
    settings = base_settings()
    control_data = base_control(mode="Monitor")
    kwargs = {}
    if event_log is not None:
        kwargs["event_log"] = event_log
    if control_log is not None:
        kwargs["control_log"] = control_log
    return ControllerContext(
        devices=Devices(
            grill_platform=FakeGrillPlatform(outputs=tuple(settings["platform"]["outputs"])),
            probe_complex=FakeProbes().script([120]),
            dist_device=FakeDistance(),
        ),
        store=InMemoryStore(control=control_data, settings=settings, pellet_db=base_pellet_db()),
        notifications=FakeNotifier(),
        clock=ManualClock(),
        **kwargs,
    )


def test_context_yields_usable_loggers_without_explicit_injection():
    """No injection still gives a real logger -- not None, no AttributeError."""
    ctx = _ctx()

    assert ctx.event_log is logging.getLogger("events")
    assert ctx.control_log is logging.getLogger("control")
    # The names are the ones create_logger configures at process startup, so an
    # un-injected context writes to the operator-visible files, not to nothing.
    ctx.event_log.debug("usable")
    ctx.control_log.debug("usable")


def test_each_context_gets_its_own_default_rather_than_a_shared_mutable():
    """Defaults are built per instance, so one test's substitution is local."""
    first = _ctx()
    second = _ctx()
    substitute = _RecordingLogger()
    first.event_log = substitute

    assert second.event_log is logging.getLogger("events")
    assert first.event_log is substitute


def test_a_mode_logs_through_the_injected_context_logger():
    """A substituted ctx.event_log captures what the mode logs."""
    recorder = _RecordingLogger()
    ctx = _ctx(event_log=recorder, control_log=_RecordingLogger())
    mode = MonitorMode(ctx, WorkCycleState())
    mode.settings = ctx.store.read_settings()
    mode.control = ctx.store.read_control()

    mode.setup()
    mode.teardown(120)

    assert recorder.calls == [
        ("debug", "Power OFF, Fan OFF, Igniter OFF, Auger OFF"),
        ("debug", "Fan OFF, Power OFF"),
    ]


def test_substituting_the_context_logger_is_per_instance():
    """Two modes with different injected loggers do not cross-talk."""
    quiet = _RecordingLogger()
    loud = _RecordingLogger()
    monitor = MonitorMode(_ctx(event_log=quiet), WorkCycleState())
    shutdown = ShutdownMode(_ctx(event_log=loud), WorkCycleState())
    shutdown.settings = shutdown.ctx.store.read_settings()

    shutdown.setup()

    assert loud.calls == [("debug", "Power ON, Fan ON, Igniter OFF, Auger OFF")]
    assert quiet.calls == []
    assert monitor.ctx.event_log is quiet


def test_no_controller_module_reaches_the_control_entry_point_for_a_logger():
    """The `import control as _control` module-global idiom is gone for good.

    Reaching into the entry-point script for `eventLogger` forced a
    function-local import (control.py imports the controller package, so a
    module-level one is circular) and made the logger rebindable from anywhere.
    The injected context replaces it.
    """
    offenders = []
    for path in sorted(_RUNTIME_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(alias.name == "control" for alias in node.names):
                offenders.append(f"{path}:{node.lineno}")
            if isinstance(node, ast.ImportFrom) and node.module == "control":
                offenders.append(f"{path}:{node.lineno}")

    assert offenders == []


def test_the_control_entry_point_exposes_no_rebindable_logger_globals():
    """Nothing may rebind `control.eventLogger` to redirect controller logging."""
    import control

    assert not hasattr(control, "eventLogger")
    assert not hasattr(control, "controlLogger")
