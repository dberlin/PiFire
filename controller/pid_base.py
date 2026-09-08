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
        """Monotonic elapsed seconds, floored to protect duplicate-reading divisors.

        This effective numerical interval is not evidence of delivered heat.
        """
        return max(current_time - self.last_update, MIN_ELAPSED_SECONDS)

    def set_target(self, set_point):
        self.set_point = set_point
        self.error = 0.0
        self.inter = 0.0
        self.derv = 0.0
        self.last_update = self._clock.monotonic()
