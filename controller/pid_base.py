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
from controller.base import ControllerBase


class PIDControllerBase(ControllerBase):
    def _calculate_gains(self, pb, ti, td):
        if pb == 0:
            self.kp = 0
        else:
            self.kp = -1 / pb
        if ti == 0:
            self.ki = 0
        else:
            self.ki = self.kp / ti
        self.kd = self.kp * td

    def set_target(self, set_point):
        self.set_point = set_point
        self.error = 0.0
        self.inter = 0.0
        self.derv = 0.0
        self.last_update = time.time()
