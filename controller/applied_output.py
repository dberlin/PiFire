#!/usr/bin/env python3

"""
*****************************************
 PiFire Applied Controller Output
*****************************************

 Description: The duty that actually reached the auger, and why.

 A controller's request and the grill's behavior are not the same thing: the
 mode clamps the request to a u_max ceiling, pins the auger off for a lid-open
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
    SEED = "seed"


class FrameFeedbackDisposition(Enum):
    """Whether an output report is routine state or terminal frame feedback."""

    PROGRESS = "progress"
    COMPLETE = "complete"
    DISCARDED = "discarded"


_CALIBRATION_ACTIONS = frozenset({"none", "start", "pause", "resume", "stop", "reset-progress", "safety-cancel"})


def _non_negative_int(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


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
    producing_result_revision: int = 0
    producing_calibration_revision: int = 0
    producing_calibration_action: str = "none"
    producing_calibration_generation: int = 0
    feedback_disposition: FrameFeedbackDisposition = FrameFeedbackDisposition.PROGRESS
    sample_complete: bool = False

    def __post_init__(self) -> None:
        _non_negative_int(self.producing_result_revision, "producing_result_revision")
        _non_negative_int(self.producing_calibration_revision, "producing_calibration_revision")
        _non_negative_int(self.producing_calibration_generation, "producing_calibration_generation")
        if self.producing_calibration_action not in _CALIBRATION_ACTIONS:
            raise ValueError("invalid producing calibration action")
        if not isinstance(self.feedback_disposition, FrameFeedbackDisposition):
            raise TypeError("feedback_disposition must be FrameFeedbackDisposition")
        if not isinstance(self.sample_complete, bool):
            raise TypeError("sample_complete must be bool")

    @property
    def controller_commanded(self):
        """Whether the controller's own request is what reached the auger.

        Derived rather than stored so it cannot drift out of agreement with the
        reason, and so no call site can assert "not the controller's" without
        saying why.
        """
        return self.source is OutputSource.CONTROLLER


def classify_output_source(lid_open, manual_override_active):
    """The reason the auger is at its current duty.

    A human toggling the auger during a lid-open pause reads as manual, not as
    lid-open: the more specific cause wins.
    """
    if manual_override_active:
        return OutputSource.MANUAL_OVERRIDE
    if lid_open:
        return OutputSource.LID_OPEN
    return OutputSource.CONTROLLER


def seed_output(ratio, timestamp, *, lid_open, manual_override_active, auger_output):
    """Actuator state that no command produced -- at setup, or after a rebuild.

    `auger_output` is the platform's current auger state; an auger that is off
    is applying zero duty whatever the cycle ratio says.
    """
    source = classify_output_source(lid_open, manual_override_active)
    if source is OutputSource.CONTROLLER:
        source = OutputSource.SEED
    return AppliedOutput(
        ratio=float(ratio) if auger_output else 0.0,
        source=source,
        timestamp=float(timestamp),
    )
