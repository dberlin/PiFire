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
from common import *  # Common Module for WebUI and Control Program
from controller.runtime.context import ControllerContext
from controller.runtime.devices import build_devices
from controller.runtime.store import ValkeyStore
from controller.runtime.clock import RealClock
from controller.runtime.notifier import ValkeyNotifier
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
if __name__ == '__main__':
	# When launched as `python control.py`, this module is named `__main__`. The
	# per-mode handlers do `import control as _control` to reach the loggers
	# bound below; without this alias that import would load a SECOND, separate
	# `control` module whose `__main__` block never ran (loggers unbound ->
	# AttributeError on the first mode log). Alias `control` to this running
	# module so those imports see the bound loggers.
	import sys
	sys.modules['control'] = sys.modules['__main__']

	settings = read_settings(init=True)

	# Setup logging
	log_level = logging.DEBUG if settings['globals']['debug_mode'] else logging.ERROR
	controlLogger = create_logger(
		'control',
		filename='./logs/control.log',
		messageformat='%(asctime)s [%(levelname)s] %(message)s',
		level=log_level,
	)

	log_level = logging.DEBUG if settings['globals']['debug_mode'] else logging.INFO
	eventLogger = create_logger(
		'events', filename='./logs/events.log', messageformat='%(asctime)s [%(levelname)s] %(message)s', level=log_level
	)

	event_message = f'PiFire Control Process started. PiFire Version: {settings["versions"]["server"]} Build: {settings["versions"]["build"]}, Debug Mode: {settings["globals"]["debug_mode"]}'

	eventLogger.info(event_message)
	controlLogger.info(event_message)

	# Flush Valkey DB and create JSON structure
	control = read_control(flush=True)
	# Delete Valkey DB for history / current
	read_history(0, flushhistory=True)
	# Flush metrics DB for tracking certain metrics
	write_metrics(flush=True)
	# Create/Flush errors list
	errors = read_errors(flush=True)

	eventLogger.info('Flushing Valkey DB and creating new control structure')

	devices, errors = build_devices(
		settings, errors=errors, event_log=eventLogger, control_log=controlLogger
	)

	# Build the injected context used by the controller / mode functions instead of bare globals
	ctx = ControllerContext(
		devices=devices,
		store=ValkeyStore(),
		notifications=ValkeyNotifier(),
		clock=RealClock(),
		event_log=eventLogger,
		control_log=controlLogger,
	)

	# Hand off to the orchestrator: setup() + the control loop.
	controller = Controller(ctx)

	# Register the exit handler (logs + grill_platform.cleanup())
	atexit.register(controller.cleanup)

	controller.run()
