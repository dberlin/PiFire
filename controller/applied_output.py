#!/usr/bin/env python3

"""
*****************************************
 PiFire Applied Controller Output
*****************************************

 Description: The duty that actually reached the auger, and why.

 A controller's request and the grill's behavior are not the same thing: the
 mode clamps the request to [u_min, u_max], pins the auger off for a lid-open
 pause, and hands the auger to a human on a manual override. A controller that
 models the plant has to follow what the grill did, not what it asked for.

 Deliberately a leaf module -- standard library only, importing from neither
 controller/*.py nor controller/runtime/** -- so the controller layer and the
 runtime layer can both use it without an inversion.

*****************************************
"""

from dataclasses import dataclass
from enum import Enum


class OutputSource(Enum):
    """Why the auger is running at the duty it is running at."""

    CONTROLLER = "controller"
    LID_OPEN = "lid_open"
    MANUAL_OVERRIDE = "manual_override"
    FAN_ASSIST = "fan_assist"
    SEED = "seed"


@dataclass(frozen=True)
class AppliedOutput:
    """One report of the duty that reached the auger.

    `requested` carries the controller's pre-clamp value when there was one, so
    a saturated interval is distinguishable from an unsaturated one: an auger
    held at u_max while the controller asked for 1.4 describes the clamp, not
    the process gain.
    """

    ratio: float
    source: OutputSource
    timestamp: float
    requested: float | None = None

    @property
    def controller_commanded(self):
        """Whether the controller's own request is what reached the auger.

        Derived rather than stored so it cannot drift out of agreement with the
        reason, and so no call site can assert "not the controller's" without
        saying why.
        """
        return self.source is OutputSource.CONTROLLER


def classify_output_source(lid_open, manual_override_active, fan_assist_active):
    """The reason the auger is at its current duty.

    A human toggling the auger during a lid-open pause reads as manual, not as
    lid-open: the more specific cause wins.
    """
    if manual_override_active:
        return OutputSource.MANUAL_OVERRIDE
    if lid_open:
        return OutputSource.LID_OPEN
    if fan_assist_active:
        return OutputSource.FAN_ASSIST
    return OutputSource.CONTROLLER


def seed_output(ratio, timestamp, *, lid_open, manual_override_active, fan_assist_active, auger_output):
    """Actuator state that no command produced -- at setup, or after a rebuild.

    `auger_output` is the platform's current auger state; an auger that is off
    is applying zero duty whatever the cycle ratio says.
    """
    source = classify_output_source(lid_open, manual_override_active, fan_assist_active)
    if source is OutputSource.CONTROLLER:
        source = OutputSource.SEED
    return AppliedOutput(
        ratio=float(ratio) if auger_output else 0.0,
        source=source,
        timestamp=float(timestamp),
    )
