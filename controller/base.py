#!/usr/bin/env python3

"""
*****************************************
 PiFire PID Controller Base Class
*****************************************

 Description: Base class for the controller.  Inherited by all controller
 modules in this package.

*****************************************
"""

"""
Imported Libraries
"""
import time

"""
Class Definition
"""


class ControllerBase:
    def __init__(self, config, units, cycle_data):
        self.config = config
        self.units = units
        self.cycle_data = cycle_data

    def update(self, current):
        """
            Input:
            current :: Current temperature
        Output:
        cycle_ratio(u) :: Raw Cycle Ratio
        """
        return 0.0

    def set_target(self, set_point):
        """
        Input:
        set_point :: Temperature Target
        """
        self.set_point = set_point
        self.last_update = time.time()

    def get_control_period(self):
        """
        Desired re-solve / actuation period in seconds. Return None to use the
        mode's CycleTime (legacy behavior). Controllers that run faster than the
        auger cycle (e.g. MPC) return a fixed period such as 5.0.
        """
        return None

    def commands_fan(self):
        """Whether this controller issues fan duty commands (vs. auger-only)."""
        return False

    def wants_async(self):
        """Whether this controller's update() should run on a background thread
        (expensive solve) rather than inline in the control loop."""
        return False

    def set_output(self, applied):
        """Report the duty that actually reached the auger.

        `applied` is a controller.applied_output.AppliedOutput. Controllers that
        model the plant use it so their model follows the grill rather than the
        request. A report whose `controller_commanded` is False is NOT a report
        to discard -- the grill really did run at that duty, so it belongs in
        the command history. What it suppresses is *identification* across the
        interval, so no estimator computes a temperature slope over time the
        controller did not drive.
        """

    def get_status(self):
        """JSON-safe diagnostics for the MQTT payload.

        Return None to publish the controller's __dict__, which is the legacy
        behavior and correct for controllers whose attributes are all scalars.
        """
        return None

    def get_model_snapshot(self):
        """A JSON-encodable record of learned plant parameters, or None.

        Must carry an integer `revision` that increases whenever the model
        changes; the store uses it to skip writes that would learn nothing.
        """
        return None

    def restore_model(self, snapshot):
        """Adopt a persisted snapshot. True when it was adopted.

        The store validates that a snapshot is a bounded, JSON-safe record; the
        controller validates that its numbers describe a possible grill.
        """
        return False


def normalize_controller_output(output):
    """
    Normalize a controller's update() return into (cycle_ratio, fan).

    Legacy controllers return a float cycle ratio; the MPC controller returns
    {'cycle_ratio': float, 'fan': {'duty': pct or None}}. fan is returned only
    when a duty is present.
    """
    if isinstance(output, dict):
        ratio = float(output.get("cycle_ratio", 0.0))
        fan = output.get("fan")
        if isinstance(fan, dict) and fan.get("duty") is not None:
            return ratio, fan
        return ratio, None
    return float(output), None
