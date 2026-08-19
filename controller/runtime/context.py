# controller/runtime/context.py
"""Bundle of everything a control cycle needs. Passed instead of globals."""

import logging
from dataclasses import dataclass, field

from common.persistence.protocols import ControllerStore

#: The two operator-visible logger names. `create_logger` configures their
#: handlers, level and file once at process startup (control.py); everything
#: inside the controller runtime acquires them through the context below, so a
#: test can substitute either without touching global state.
EVENT_LOG_NAME = "events"
CONTROL_LOG_NAME = "control"


@dataclass
class Devices:
    grill_platform: object
    probe_complex: object
    dist_device: object


@dataclass
class ControllerContext:
    devices: object  # Devices
    store: ControllerStore
    notifications: object  # Notifier
    clock: object  # Clock
    #  Defaulted to the named loggers rather than None so every context carries
    #  a usable logger: no call site needs a None check or a try/except around a
    #  log call, and an un-injected context still reaches the operator's log
    #  files instead of silently discarding the message.
    event_log: object = field(default_factory=lambda: logging.getLogger(EVENT_LOG_NAME))
    control_log: object = field(default_factory=lambda: logging.getLogger(CONTROL_LOG_NAME))
