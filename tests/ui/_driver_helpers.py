"""Driver-loading helpers shared by the fixed-base display driver test files.

`_load_driver`/`_instantiate` were copy-pasted identically into
test_driver_input_behavior.py and test_fixed_base_drivers_load.py, and
test_pygame_qt_drivers.py's `_instantiate_fixed` turned out, on inspection,
to be the exact same construction helper under a different name -- all three
sites patch `threading.Thread` and `os.system` the same way and build the
same default `dev_pins`/`buttonslevel`/`rotation`/`units`/`config` kwargs.

Each file still builds its own hardware-stub `sys.modules` overlay locally
(the per-file `_hardware_stubs` helpers are NOT identical -- e.g.
test_fixed_base_drivers_load.py's version additionally stubs `spidev` for
the `*em` drivers), so `load_driver` takes the already-built overlay dict
rather than stub kwargs, keeping this module decoupled from any one file's
stub set.
"""

import contextlib
import importlib
import sys
import threading
from unittest import mock

#: The module-level names each display base binds from common/system.py, and
#: through which every power action in display code now runs.
_POWER_SEAMS = (
    ("display._base_fixed", ("reboot_system", "shutdown_system")),
    ("display._base_flex", ("reboot_system", "shutdown_system", "restart_scripts")),
)


@contextlib.contextmanager
def block_power_actions(what):
    """Block every way display code can power-cycle or restart the host.

    The power menu used to shell out with `os.system("... sudo reboot &")`, and
    every harness in this directory blocked exactly that -- see the history of
    real reboot incidents the docstrings here keep referring to. It now calls
    common/system.py's `reboot_system`/`shutdown_system`/`restart_scripts`,
    which use `subprocess`, so a patched `os.system` stopped covering it.

    That gap is not theoretical: `real_hw` defaults to True in a fresh test
    datastore (asserted in tests/characterization/test_process_command_golden.py),
    so `is_real_hardware()` is True here and an unblocked call really does run
    `sudo systemctl reboot`.

    Patch where the names are BOUND rather than in common.system: the bases
    import them at module level, so `mock.patch.object(common.system, ...)`
    would be looked straight past.
    """
    with contextlib.ExitStack() as stack:
        for module_path, names in _POWER_SEAMS:
            module = importlib.import_module(module_path)
            for name in names:
                stack.enter_context(
                    mock.patch.object(module, name, side_effect=AssertionError(f"{name} blocked for {what}"))
                )
        yield


def load_driver(module_path, overlay):
    """Import a driver module with its hardware libraries stubbed for the
    duration of the import only. The module then stays cached in
    sys.modules exactly like any normal import (a second call is a cache hit
    and does not need the overlay any more)."""
    with mock.patch.dict(sys.modules, overlay):
        return importlib.import_module(module_path)


def instantiate(mod, **overrides):
    """Construct mod.Display with the display/encoder thread(s) and
    os.system blocked, so no real SPI/pygame thread ever starts and no
    `sudo reboot` can be shelled out.

    Patches the shared `threading` module's `Thread` attribute directly
    (rather than `mod.threading.Thread`): every driver module's own
    `import threading` binds the same singleton `threading` module object,
    so this one patch covers every `threading.Thread(...)` call site --
    whether it lives in the driver itself (e.g. st7789e, the pygame/st7789
    drivers) or in a shared mixin module (`display._luma_panel`,
    `display._encoder_input`) -- without requiring each driver to keep its
    own `import threading` around just so tests can reach it."""
    kwargs = {
        "dev_pins": {
            "display": {"dc": 24, "led": 5, "rst": 25},
            "input": {"up_clk": 16, "down_dt": 20, "enter_sw": 21},
        },
        "buttonslevel": "HIGH",
        "rotation": 0,
        "units": "F",
        "config": {},
    }
    kwargs.update(overrides)
    with (
        mock.patch.object(threading, "Thread") as mock_thread,
        mock.patch("os.system", side_effect=AssertionError(f"os.system blocked for {mod.__name__}")),
        block_power_actions(mod.__name__),
    ):
        mock_thread.return_value.start = lambda: None
        return mod.Display(**kwargs)


class RecordingLogger:
    """Substitutable stand-in for a stdlib logger, recording (level, message)
    per call so a test can assert *which* of a driver's two loggers a message
    went to. Mirrors tests/unit/runtime/test_logger_idiom.py's controller-side
    equivalent."""

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
