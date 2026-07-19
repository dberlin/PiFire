"""Canonical controller mode names as a StrEnum. Members ARE their string
values (Mode.SMOKE == "Smoke", str(Mode.SMOKE) == "Smoke"), so they serialize to
plain JSON, compare/stringify equal to the persisted control["mode"] string, and
interoperate as dict keys / set members with plain strings written by other
processes and recipe files. StrEnum (not `class Mode(str, Enum)`) is required so
str()/format yield the value, not "Mode.SMOKE"."""

from enum import StrEnum


class Mode(StrEnum):
    STARTUP = "Startup"
    SMOKE = "Smoke"
    HOLD = "Hold"
    MONITOR = "Monitor"
    MANUAL = "Manual"
    PRIME = "Prime"
    REIGNITE = "Reignite"
    SHUTDOWN = "Shutdown"
    STOP = "Stop"
    ERROR = "Error"
    RECIPE = "Recipe"


class StatusState(StrEnum):
    """The controller's second state axis: control["status"], orthogonal to
    Mode. StrEnum so the four string values -- a published contract the
    web/mobile UI display verbatim and which persist in control["status"] --
    stay byte-identical (str(StatusState.UNSET) == "", not "StatusState.UNSET").
    """

    ACTIVE = "active"
    MONITOR = "monitor"
    INACTIVE = "inactive"
    UNSET = ""
