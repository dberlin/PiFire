#!/usr/bin/env python3

"""
*****************************************
 PiFire PID Controller
*****************************************

 Description: This object will be used to calculate PID for maintaining
 temperature in the grill.

 This software was developed by GitHub user DBorello as part of his excellent
 PiSmoker project: https://github.com/DBorello/PiSmoker

 Adapted for PiFire

 PID controller based on proportional band in standard PID form https://en.wikipedia.org/wiki/PID_controller#Ideal_versus_standard_PID_form
   u = Kp (e(t)+ 1/Ti INT + Td de/dt)
  PB = Proportional Band
  Ti = Goal of eliminating in Ti seconds
  Td = Predicts error value at Td in seconds

  Configuration Defaults:
  "config": {
      "PB": 60.0,
      "Td": 45.0,
      "Ti": 180.0,
      "center": 0.5
   }

*****************************************
"""

"""
Imported Libraries
"""
import time
from controller.base import PidTraceDiagnostics
from controller.pid_base import PIDControllerBase

"""
Class Definition
"""


class Controller(PIDControllerBase):
    def __init__(self, config, units, cycle_data, *, logger=None):
        super().__init__(config, units, cycle_data)

        self._calculate_gains(config.get("PB", 60.0), config.get("Ti", 180.0), config.get("Td", 45.0))

        self.p = 0.0
        self.i = 0.0
        self.d = 0.0
        self.u = 0

        self.last_update = time.time()
        self.error = 0.0
        self.set_point = 0

        self.center = config.get("center", 0.5)

        self.derv = 0.0
        self.inter = 0.0
        if self.ki != 0:
            self.inter_max = abs(self.center / self.ki)
        else:
            self.inter_max = 0

        self._trace_diagnostics = None
        self.last = 150

        self.set_target(0.0)

    def update(self, current):
        previous_temperature = self.last
        previous_update_time = self.last_update
        error = current - self.set_point
        self.p = self.kp * error + self.center

        # I
        dt = self._elapsed_since_last_update(time.time())
        # if self.p > 0 and self.p < 1: # Ensure we are in the pb, otherwise do not calculate i to avoid windup
        unclamped_integral = self.inter + error * dt
        self.inter = unclamped_integral
        if self.center != 0:
            self.inter = max(self.inter, -self.inter_max)
            self.inter = min(self.inter, self.inter_max)
        integral_clamped = self.inter != unclamped_integral
        self.i = self.ki * self.inter

        self.derv = (current - previous_temperature) / dt
        self.d = self.kd * self.derv
        self.u = self.p + self.i + self.d

        self.error = error
        self.last = current
        self.last_update = time.time()
        self._trace_diagnostics = PidTraceDiagnostics(
            observed_dt_seconds=dt,
            error=error,
            proportional_term=self.p,
            integral_term=self.i,
            derivative_term=self.d,
            integral_accumulator=self.inter,
            integral_clamped=integral_clamped,
            derivative_input=current - previous_temperature,
            derivative_state=self.derv,
            proportional_band=float(self.config.get("PB", 60.0)),
            kp=self.kp,
            ki=self.ki,
            kd=self.kd,
            center=self.center,
            previous_temperature=previous_temperature,
            previous_update_time=previous_update_time,
            raw_output=self.u,
            final_output=self.u,
        )
        return self.u

    def trace_diagnostics(self) -> PidTraceDiagnostics | None:
        return self._trace_diagnostics
