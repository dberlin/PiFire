"""The two operator-visible loggers a display driver writes to.

`create_logger` configures the "events" and "control" loggers once per process
(display_process.py, mirroring control.py for the controller process). Drivers
receive them from `build_display()` through their constructor rather than
calling `logging.getLogger(...)` themselves, so a test can substitute either
without touching global state -- the same substitution point
`ControllerContext.event_log` / `.control_log` provides on the controller side.

An omitted argument resolves to the correctly named logger rather than None, so
every driver holds a usable logger: no log call site needs a None guard, and an
un-injected driver still reaches the operator's log files instead of silently
discarding the message.

Which logger a message belongs to follows the level each name is configured
with: "events" runs at INFO (DEBUG in debug_mode) and is the log a user reads,
so operator-facing messages go there; "control" runs at ERROR (DEBUG in
debug_mode), where anything below ERROR is invisible in production, so it
carries developer diagnostics and tracebacks.
"""

import logging

EVENT_LOG_NAME = "events"
CONTROL_LOG_NAME = "control"


def resolve_loggers(event_log, control_log):
    """Return (event_log, control_log), substituting the named logger for None."""
    return (
        event_log if event_log is not None else logging.getLogger(EVENT_LOG_NAME),
        control_log if control_log is not None else logging.getLogger(CONTROL_LOG_NAME),
    )
