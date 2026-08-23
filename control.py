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
 file is only the process entry point: read settings, set up logging, reset
 ephemeral control/current state without deleting an unfinished cook, build
 devices + the injected ControllerContext, then Controller(ctx).run().
==============================================================================
"""

import logging
import atexit
from common.common import ErrorKind, create_logger  # Common Module for WebUI and Control Program
from common import datastore
from controller.runtime.context import ControllerContext
from controller.runtime.devices import build_devices
from controller.runtime.store import SqliteStore
from controller.runtime.clock import RealClock
from controller.runtime.notifier import LiveNotifier
from controller.runtime.controller import Controller


def _initialize_runtime_state(store):
    """Reset process-ephemeral state while retaining an unfinished cook session."""
    persisted_control = store.read_control()
    cook_id = persisted_control.get("cook_id")
    unfinished = bool(store.read_history(num_items=1) or store.read_all_metrics())
    retained_cook_id = (
        cook_id
        if unfinished
        and isinstance(cook_id, str)
        and bool(cook_id)
        and cook_id == cook_id.strip()
        else None
    )
    control = store.flush_control(cook_id=retained_cook_id)
    store.flush_current()
    return control


# Only run hardware init and the control loop when executed as the main
# program. Guarding this lets the module be imported (e.g. by tests) without
# initializing hardware, flushing the datastore, or entering the control loop.
if __name__ == "__main__":
    # First-boot migration: import existing settings.json / pelletdb.json into
    # SQLite if it hasn't happened yet. Must run before the first
    # read_settings()/read_control() call below -- this is the ONLY trigger of
    # that import in production (both control.py and app.py call it; it is
    # idempotent, so running it from both independently-supervised processes,
    # in either order, is safe).
    datastore.init()
    store = SqliteStore()

    # NOTE: this used to read `read_settings(init=True)`, but that `init` flag
    # has been dead since the JSON->SQLite move -- it never seeded anything, so
    # dropping it changed nothing. There is no cross-process ordering
    # dependency here: the datastore.init() call above already seeds
    # settings:general (and pellets:general) when absent -- see
    # common/datastore.py's init(), which upserts them after importing the
    # legacy JSON files. app.py runs the same init() at import, so either
    # process can start first.
    settings = store.read_settings()

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

    # Reset process-ephemeral state. Unfinished cook history/metrics and their
    # durable identity survive routine supervisor restarts.
    control = _initialize_runtime_state(store)
    # Clear the errors list; flush_errors() hands back the fresh (empty)
    # accumulator that build_devices() appends into below.
    errors = store.flush_errors(ErrorKind.CONTROL)

    eventLogger.info("Resetting ephemeral control state and preserving unfinished cook data")

    devices, errors = build_devices(settings, errors=errors, event_log=eventLogger, control_log=controlLogger)

    # Build the injected context used by the controller / mode functions instead of bare globals
    ctx = ControllerContext(
        devices=devices,
        store=store,
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
