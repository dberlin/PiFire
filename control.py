#!/usr/bin/env python3

"""
==============================================================================
 PiFire Main Control Process
==============================================================================

Description: This script starts at boot, initializes the datastore and
  hardware, and hands off to the Controller orchestrator, which runs the
  outer control loop and dispatches to the per-mode work cycles.

 This script runs as a separate process from the Flask / Gunicorn
 implementation (web interface) and from the display process
 (display_process.py). See controller/runtime/README.md for the two-process
 model.

 The control loop itself lives in controller/runtime/controller.py
 (Controller); the per-mode logic lives in controller/runtime/modes/. This
 file is only the process entry point: read settings, set up logging, flush
 the datastore, build devices + the injected ControllerContext, then
 Controller(ctx).run().
==============================================================================
"""

import logging
import atexit
from common.common import create_logger  # Common Module for WebUI and Control Program
from common.datastore_accessors import read_settings, flush_control, flush_history, flush_metrics, flush_errors
from common import datastore
from controller.runtime.context import ControllerContext
from controller.runtime.devices import build_devices
from controller.runtime.store import SqliteStore
from controller.runtime.clock import RealClock
from controller.runtime.notifier import LiveNotifier
from controller.runtime.controller import Controller


# ---------------------------------------------------------------------------
# Module-level loggers. Bound below in the __main__ block. The per-mode
# handlers reference these via `import control as _control; _control.eventLogger`
# (a deliberate module-global logging contract), so they must remain top-level
# names on this module. Tests bind them directly (see the characterization
# harness).
# ---------------------------------------------------------------------------
eventLogger = None
controlLogger = None


# Only run hardware init and the control loop when executed as the main
# program. Guarding this lets the module be imported (e.g. by tests, and by the
# per-mode handlers that reference control.eventLogger) without initializing
# hardware, flushing the datastore, or entering the control loop.
if __name__ == "__main__":
    # When launched as `python control.py`, this module is named `__main__`. The
    # per-mode handlers do `import control as _control` to reach the loggers
    # bound below; without this alias that import would load a SECOND, separate
    # `control` module whose `__main__` block never ran (loggers unbound ->
    # AttributeError on the first mode log). Alias `control` to this running
    # module so those imports see the bound loggers.
    import sys

    sys.modules["control"] = sys.modules["__main__"]

    # First-boot migration: import existing settings.json / pelletdb.json into
    # SQLite if it hasn't happened yet. Must run before the first
    # read_settings()/read_control() call below -- this is the ONLY trigger of
    # that import in production (both control.py and app.py call it; it is
    # idempotent, so running it from both independently-supervised processes,
    # in either order, is safe).
    datastore.init()

    # NOTE: this used to read `read_settings(init=True)`, but that `init` flag
    # has been dead since the JSON->SQLite move -- it never seeded anything, so
    # dropping it changed nothing. There is no cross-process ordering
    # dependency here: the datastore.init() call above already seeds
    # settings:general (and pellets:general) when absent -- see
    # common/datastore.py's init(), which upserts them after importing the
    # legacy JSON files. app.py runs the same init() at import, so either
    # process can start first.
    settings = read_settings()

    # Setup logging
    log_level = logging.DEBUG if settings["globals"]["debug_mode"] else logging.ERROR
    controlLogger = create_logger(
        "control",
        filename="./logs/control.log",
        messageformat="%(asctime)s [%(levelname)s] %(message)s",
        level=log_level,
    )

    log_level = logging.DEBUG if settings["globals"]["debug_mode"] else logging.INFO
    eventLogger = create_logger(
        "events", filename="./logs/events.log", messageformat="%(asctime)s [%(levelname)s] %(message)s", level=log_level
    )

    event_message = f"PiFire Control Process started. PiFire Version: {settings['versions']['server']} Build: {settings['versions']['build']}, Debug Mode: {settings['globals']['debug_mode']}"

    eventLogger.info(event_message)
    controlLogger.info(event_message)

    # Flush datastore and create JSON structure
    control = flush_control()
    # Delete datastore entries for history / current
    flush_history()
    # Flush metrics DB for tracking certain metrics
    flush_metrics()
    # Clear the errors list; flush_errors() hands back the fresh (empty)
    # accumulator that build_devices() appends into below.
    errors = flush_errors()

    eventLogger.info("Flushing datastore and creating new control structure")

    devices, errors = build_devices(settings, errors=errors, event_log=eventLogger, control_log=controlLogger)

    # Build the injected context used by the controller / mode functions instead of bare globals
    ctx = ControllerContext(
        devices=devices,
        store=SqliteStore(),
        notifications=LiveNotifier(),
        clock=RealClock(),
        event_log=eventLogger,
        control_log=controlLogger,
    )

    # Hand off to the orchestrator: setup() + the control loop.
    controller = Controller(ctx)

    # Register the exit handler (logs + grill_platform.cleanup())
    atexit.register(controller.cleanup)

    controller.run()
