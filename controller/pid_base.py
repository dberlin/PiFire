#!/usr/bin/env python3

"""
*****************************************
 PiFire PID Controller Shared Base Class
*****************************************

 Description: Shared scaffolding for the standard-form PID controller variants.
 Owns the standard proportional-band defaults for _calculate_gains / set_target
 that most variants share. Variants override only what genuinely differs.
 update() is never defined here.

*****************************************
"""

import time
from common.control_trace import ActuationMode
from controller.base import ControllerBase

# Floor for the elapsed-time denominator PID variants divide by. Real control
# loop cadences run in whole seconds, so this floor is invisible to any live
# reading while still keeping every division finite.
MIN_ELAPSED_SECONDS = 1e-3


class PIDControllerBase(ControllerBase):
    def _calculate_gains(self, pb, ti, td):
        if pb == 0:
            self.kp = 0
        else:
            self.kp = -1 / pb
        if ti <= 0:
            self.ki = 0
        else:
            self.ki = self.kp / ti
        self.kd = self.kp * td

    def _elapsed_since_last_update(self, current_time):
        """Time since last_update, floored so a divisor never hits zero.

        Back-to-back time.time() calls commonly return the identical float
        (float64's ULP at the current epoch is far below a control loop's
        cadence), and derivative terms divide by this value directly with no
        cancellation to protect them.
        """
        return max(current_time - self.last_update, MIN_ELAPSED_SECONDS)

    def actuation_mode(self) -> ActuationMode:
        """PID-family controllers ask for framed pulses.

        A fixed cycle floors the auger at `u_min`, and that floor is a floor on
        temperature: the lowest heat the grill can command is the lowest it can
        hold. On a plant fitted to a logged MAK cook the shipped 25 s cycle
        floors at 0.10 duty while a 225 F hold needs 0.092, so the chamber
        settles near 283 F and no tuning of the loop can reach the set point.

        Framed pulses carry the shortfall as credit instead of rounding it up,
        so a request below one pulse is delivered as a pulse every few frames
        rather than as a floor. Measured on that plant, 225 F goes from 0.2% of
        the cook within 5 F to 84% for this controller and 90% for the Smith
        variant, and 350 F improves for both. It also matches the hardware
        better than a fixed cycle does: the auger is relay-switched and has
        mechanical inertia, so arbitrarily fine on-times are a property of the
        model rather than of the grill.
        """
        return ActuationMode.FRAMED_PULSE

    def set_target(self, set_point):
        self.set_point = set_point
        self.error = 0.0
        self.inter = 0.0
        self.derv = 0.0
        self.last_update = time.time()
