#!/usr/bin/env python3
"""PiFire Display Process -- renders from the datastore, independent of the controller.

Note: this file cannot be named display.py -- a top-level display/ package
(display drivers: display.none, display.ili9341f, etc.) already exists in
this repo, and Python's import system always resolves `import display` to
that package over a same-named top-level module. Naming this file
display.py would make it permanently unreachable via `from display import
...` (the package always wins), so it is named display_process.py instead.
Run it directly as a script, e.g. `python display_process.py`.
"""

import logging

from common import read_settings, create_logger
from controller.runtime.devices import build_display
from controller.runtime.store import SqliteStore
from controller.runtime.clock import RealClock


class DisplayFeeder:
    def __init__(self, display, store, clock):
        self.display, self.store, self.clock = display, store, clock

    def tick(self):
        in_data = self.store.read_current()
        status = self.store.read_status()
        if in_data and status:
            self.display.display_status(in_data, status)
        for cmd, arg in self.store.display_commands().drain():
            if cmd == "text":
                self.display.display_text(arg)
            elif cmd == "clear":
                self.display.clear_display()
            elif cmd == "splash":
                self.display.display_splash()

    def run(self):
        while True:
            self.tick()
            self.clock.sleep(0.1)


if __name__ == "__main__":
    settings = read_settings()

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

    display_device, _errors = build_display(settings, errors=[], event_log=eventLogger, control_log=controlLogger)

    eventLogger.info("PiFire Display Process started.")

    DisplayFeeder(display_device, SqliteStore(), RealClock()).run()
