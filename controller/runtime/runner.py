"""Temperature-controller execution seam (PID/MPC/etc).

`ControllerRunner` is the abstract interface `HoldMode.on_tick` drives:
set_target/submit/latest to run the control math, reconfigure() to rebuild the
core on a settings change, control_period() for the mode's poll interval, and
commands_fan() so the caller knows whether this controller issues its own fan
command (MPC) or leaves fan control to the temperature-profile logic.
`SyncControllerRunner` runs the underlying controller module's `update()`
synchronously on submit/latest -- control math and probe-read cadence are the
same cadence. `ThreadedControllerRunner` runs the core on a background thread
at its own control period and hands back non-blocking snapshots via
`.latest()`, decoupling control-math cadence from the probe-read cadence.
`build_runner` selects between the two by the core's `wants_async()` (MPC
requests the threaded runner; other controllers get the sync runner), and -- if
the selected controller will not build at all -- substitutes FALLBACK_CONTROLLER
so a live fire never ends up unregulated. See `_build_core` and `build_runner`
for why both of those matter.
"""

import importlib
import threading
from abc import ABC, abstractmethod
from collections import namedtuple

from controller.base import normalize_controller_output

NormalizedOutput = namedtuple("NormalizedOutput", ["cycle_ratio", "fan"])


class ControllerRunner(ABC):
    @abstractmethod
    def set_target(self, setpoint): ...
    @abstractmethod
    def submit(self, temp): ...
    @abstractmethod
    def latest(self): ...
    @abstractmethod
    def reconfigure(self, settings, control, logger=None): ...
    @abstractmethod
    def control_period(self): ...
    @abstractmethod
    def commands_fan(self): ...
    @abstractmethod
    def wants_async(self): ...
    @abstractmethod
    def set_output(self, applied): ...
    @abstractmethod
    def get_model_snapshot(self): ...
    @abstractmethod
    def restore_model(self, snapshot): ...
    @abstractmethod
    def controller_state(self): ...
    @abstractmethod
    def stop(self): ...


class SyncControllerRunner(ControllerRunner):
    def __init__(self, core):
        self._core = core
        self._temp = None

    def set_target(self, setpoint):
        self._core.set_target(setpoint)

    def submit(self, temp):
        self._temp = temp

    def latest(self):
        raw = self._core.update(self._temp)
        ratio, fan = normalize_controller_output(raw)
        return NormalizedOutput(cycle_ratio=ratio, fan=fan)

    def latest_from(self, temp):
        self.submit(temp)
        return self.latest()

    def reconfigure(self, settings, control, logger=None):
        core, status = _build_core(settings, control, logger=logger)
        if status == "Active":
            self._core = core
        else:
            report_reconfigure_failure(settings, logger=logger)
        return status

    def control_period(self):
        return self._core.get_control_period()

    def commands_fan(self):
        return self._core.commands_fan()

    def wants_async(self):
        return self._core.wants_async()

    def stop(self):
        pass

    def set_output(self, applied):
        self._core.set_output(applied)

    def get_model_snapshot(self):
        return self._core.get_model_snapshot()

    def restore_model(self, snapshot):
        return self._core.restore_model(snapshot)

    def controller_state(self):
        status = self._core.get_status()
        if status is None:
            return dict(self._core.__dict__)
        return status


_UNSET = object()


class ThreadedControllerRunner(ControllerRunner):
    """Runs core.update() on a background thread at the core's control period, so
    an expensive solve never blocks the caller. submit()/latest() are
    non-blocking snapshots; the running core is mutated only by the thread."""

    def __init__(self, core):
        self._core = core
        self._lock = threading.Lock()
        self._temp = None
        self._output = NormalizedOutput(cycle_ratio=0.0, fan=None)
        self._pending_target = _UNSET
        self._pending_core = None
        self._state_snapshot = dict(core.__dict__)
        self._control_period = core.get_control_period()
        self._commands_fan = core.commands_fan()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while not self._stop_event.is_set():
            with self._lock:
                temp = self._temp
                target = self._pending_target
                self._pending_target = _UNSET
                new_core = self._pending_core
                self._pending_core = None
            if new_core is not None:
                self._core = new_core
            if target is not _UNSET:
                self._core.set_target(target)
            if temp is not None:
                raw = self._core.update(temp)
                ratio, fan = normalize_controller_output(raw)
                snap = dict(self._core.__dict__)
                with self._lock:
                    self._output = NormalizedOutput(cycle_ratio=ratio, fan=fan)
                    self._state_snapshot = snap
            # Interruptible sleep; wait(None/0) would block forever, so floor it.
            self._stop_event.wait(self._control_period or 1.0)

    def set_target(self, setpoint):
        with self._lock:
            self._pending_target = setpoint

    def submit(self, temp):
        with self._lock:
            self._temp = temp

    def latest(self):
        with self._lock:
            return self._output

    def reconfigure(self, settings, control, logger=None):
        core, status = _build_core(settings, control, logger=logger)
        if status == "Active":
            with self._lock:
                self._pending_core = core
        else:
            report_reconfigure_failure(settings, logger=logger)
        return status

    def control_period(self):
        return self._control_period

    def commands_fan(self):
        return self._commands_fan

    def wants_async(self):
        return True

    def controller_state(self):
        with self._lock:
            return dict(self._state_snapshot)

    def stop(self):
        self._stop_event.set()
        self._thread.join(timeout=2.0)


# The controller every PiFire install can always build: pure Python, no optional
# dependencies, and the shipped default.
FALLBACK_CONTROLLER = "pid"


def _selected_controller(settings):
    """settings['controller']['selected'], tolerating a malformed settings tree.

    The reporting paths below run precisely when something is already wrong, so
    they must not be the thing that raises.
    """
    try:
        return settings["controller"]["selected"]
    except KeyError, TypeError:
        return None


def _raise_banner(text, logger=None):
    """Put `text` in front of the user (dashboard banner + control log).

    Same idiom build_devices() uses for a display or grill platform that will not
    load. Deliberately best-effort: a datastore hiccup while reporting a
    controller problem must not itself take down the control loop.
    """
    try:
        from common.common import ErrorKind
        from common.datastore_accessors import read_errors, write_errors

        errors = read_errors(ErrorKind.CONTROL)
        errors.append(text)
        write_errors(ErrorKind.CONTROL, errors)
    except Exception:
        pass
    if logger is not None:
        try:
            logger.error(text)
        except Exception:
            pass


def _dependency_hint(controller_type, settings):
    """A sentence naming the uninstalled package, or "" if that is not the problem."""
    try:
        from common.controller_deps import check_controller_dependencies

        config = (settings.get("controller") or {}).get("config", {}).get(controller_type, {})
        missing = check_controller_dependencies(controller_type, config)
    except Exception:
        return ""
    if missing is None:
        return ""
    names = ", ".join(missing.modules)
    return (
        f"It needs the {names} package, which is not part of a standard PiFire install. "
        f"Open Settings > Controller, select {controller_type.upper()} and save: PiFire will install it "
        f"in the background (several minutes on a Pi), after which the controller will start normally. "
    )


def _build_core(settings, control, logger=None, controller_type=None):
    """Construct the selected controller core. NEVER raises.

    Both halves are guarded: `import_module` (a missing or renamed module) and --
    the half that used to be outside the guard -- the CONSTRUCTOR. That gap was
    not theoretical: controller/mpc.py imports do_mpc lazily inside __init__, so
    on an install without the optional `mpc` extra the module imports cleanly and
    then `Controller(...)` raises ModuleNotFoundError. Nothing between here and
    the process entry point catches it, so the exception propagated out of
    HoldMode.setup() -> Controller.run() and killed the control process with the
    auger already commanded on. supervisor restarted it, control was flushed to
    Stop, and the user's cook ended with the fire out and no explanation.
    """
    controller_type = controller_type or settings["controller"]["selected"]
    try:
        module = importlib.import_module(f"controller.{controller_type}")
    except Exception:
        if logger is not None:
            logger.exception("Error occurred loading controller module. Trace dump: ")
        return None, "Inactive"
    try:
        core = module.Controller(
            settings["controller"]["config"][controller_type], settings["globals"]["units"], settings["cycle_data"]
        )
        core.set_target(control["primary_setpoint"])
    except Exception:
        if logger is not None:
            logger.exception(f"Error occurred building the [{controller_type}] controller. Trace dump: ")
        return None, "Inactive"
    return core, "Active"


def _wrap(core, status):
    if core is None:
        return None, status
    if core.wants_async():
        return ThreadedControllerRunner(core), status
    return SyncControllerRunner(core), status


def build_runner(settings, control, logger=None):
    """Build the runner for a work cycle, substituting the default controller if
    the selected one will not build.

    Substituting is the safe choice here, and it is not the obvious one, so:
    this is called from HoldMode.setup() AFTER power/fan/auger have already been
    commanded on. Refusing to build aborts the Hold cycle at setup_safety with
    the fire lit and no controller regulating it. Holding at setpoint on the
    default PID controller -- with a banner saying exactly what was substituted
    and why -- keeps the user's cook alive and the grill controllable, which is
    the whole point. It mirrors build_devices(), which loads display.none or the
    prototype platform rather than letting a bad module stop the loop.

    Nothing is written back to settings: the user's choice is preserved so that
    re-saving it (once the missing package is installed) just works.
    """
    core, status = _build_core(settings, control, logger=logger)
    if core is not None:
        return _wrap(core, status)

    selected = _selected_controller(settings)
    if selected == FALLBACK_CONTROLLER:
        _raise_banner(
            f"The [{selected}] controller could not be started and PiFire has no fallback for it. "
            f"The current cook cycle has been stopped. Check logs/control.log for details.",
            logger=logger,
        )
        return None, status

    hint = _dependency_hint(selected, settings)
    core, status = _build_core(settings, control, logger=logger, controller_type=FALLBACK_CONTROLLER)
    if core is None:
        _raise_banner(
            f"The [{selected}] controller could not be started, and neither could the fallback "
            f"[{FALLBACK_CONTROLLER}] controller. {hint}The current cook cycle has been stopped. "
            f"Check logs/control.log for details.",
            logger=logger,
        )
        return None, status

    _raise_banner(
        f"The [{selected}] controller could not be started. {hint}"
        f"PiFire is running the [{FALLBACK_CONTROLLER}] controller instead so the grill stays under control. "
        f"Your controller selection has not been changed.",
        logger=logger,
    )
    return _wrap(core, status)


def report_reconfigure_failure(settings, logger=None):
    """Tell the user a settings-triggered controller swap did not take.

    The runner keeps the core it was already using (see the `status == "Active"`
    guard in both reconfigure implementations), so a mid-cook switch to a
    controller that will not build is a no-op rather than a loss of control --
    but a silent no-op would leave the user believing MPC is regulating the fire.
    """
    selected = _selected_controller(settings)
    _raise_banner(
        f"Could not switch to the [{selected}] controller. {_dependency_hint(selected, settings)}"
        f"The previous controller is still running your cook.",
        logger=logger,
    )
