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


#: The modes that mean a cook actually happened, and therefore that the session
#: is worth archiving to a cook file when it ends.
#:
#: Monitor and Prime are deliberately absent. Monitor only watches the
#: temperatures of a grill somebody else lit; Prime only runs the auger to load
#: pellets. Neither is a cook, and archiving one produces a .pifire nobody asked
#: for -- toggling Monitor on and off twice used to leave two of them in
#: ./history/, the second suffixed "-1" because the first already existed.
COOK_MODES = frozenset(
    {
        Mode.STARTUP,
        Mode.REIGNITE,
        Mode.SMOKE,
        Mode.HOLD,
        Mode.SHUTDOWN,
        Mode.MANUAL,
        Mode.RECIPE,
    }
)


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
