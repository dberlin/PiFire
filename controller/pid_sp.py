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
import math
import time

from common.control_trace import ControllerBranch
from controller.base import PidSpTraceDiagnostics
from controller.pid_base import PIDControllerBase

"""
Class Definition
"""


class Controller(PIDControllerBase):
    def __init__(self, config, units, cycle_data):
        super().__init__(config, units, cycle_data)

        pb = config.get("PB", 60.0)
        ti = config.get("Ti", 180.0)
        td = config.get("Td", 45.0)
        self._calculate_gains(pb, ti, td)

        self.p = 0.0
        self.i = 0.0
        self.d = 0.0
        self.u = 0

        self.pb = pb

        self.units = units

        self.last_update = time.time()
        self.last_set_time = time.time()
        self.error = 0.0
        self.set_point = 0

        self.center = 0.5
        self.center_factor = config.get("center_factor", 0.0010)

        self.tau = config.get("tau", 115)
        self.theta = config.get("theta", 65)

        self.stable_window = config.get("stable_window", 12)
        self.cycle_time = cycle_data["HoldCycleTime"]

        self.derv = 0.0
        self.inter = 0.0

        self.last = 150
        self.start_change_temp = 0.0
        self.new_target = False

        self._trace_diagnostics = None
        self.set_target(0.0)

    def update(self, current):
        current_time = time.time()
        previous_update_time = self.last_update
        previous_temperature = self.last
        dt = self._elapsed_since_last_update(current_time)
        branch = ControllerBranch.NONE
        new_target_before = self.new_target

        # Fix self.last being set to 0.0 on set point change
        if self.last == 0.0 and self.new_target:
            self.last = current
            previous_temperature = current
            branch = ControllerBranch.INITIALIZATION

        error = current - self.set_point
        self.roc = (current - self.last) / dt
        predicted_temp = current + (self.roc * self.theta) * (1 - math.exp(-dt / self.tau))
        predicted_error = predicted_temp - self.set_point

        if predicted_error < -self.pb:
            self.u = 1.0
            if branch is ControllerBranch.NONE:
                branch = ControllerBranch.FULL_HEAT
        elif predicted_error > self.stable_window:
            self.u = 0.0
            if branch is ControllerBranch.NONE:
                branch = ControllerBranch.OVERSHOOT
        else:
            if self.new_target and abs(error) <= 3:
                self.new_target = False
                if branch is ControllerBranch.NONE:
                    branch = ControllerBranch.TARGET_REACHED

            reset_integral = (abs(error) > self.stable_window) or (
                self.new_target
                and current_time - self.last_set_time >= self.cycle_time * 3
                and abs(error) <= abs(self.start_change_temp - self.set_point) / 2
            )
            if reset_integral:
                self.inter = 0.0
                if branch is ControllerBranch.NONE:
                    branch = ControllerBranch.RESET

            if (self.new_target and self.set_point < current) or (abs(error) > self.pb / 2):
                self.derv = 0.0

            self.p = self.kp * predicted_error + self.center
            self.inter += predicted_error * dt
            self.i = self.ki * self.inter
            unclamped_integral_term = self.i
            self.i = max(min(self.i, self.center), -self.center)
            integral_clamped = self.i != unclamped_integral_term

            self.derv = (predicted_temp - self.last) / dt
            self.d = self.kd * self.derv

            if error < self.pb and current_time - self.last_set_time < self.cycle_time * 3:
                self.u = self.u * 0.65

            self.u = self.p + self.i + self.d
        if predicted_error < -self.pb or predicted_error > self.stable_window:
            integral_clamped = False

        self.error = error
        self.last = current
        self.last_update = current_time
        self._trace_diagnostics = PidSpTraceDiagnostics(
            observed_dt_seconds=dt,
            error=error,
            proportional_term=self.p,
            integral_term=self.i,
            derivative_term=self.d,
            integral_accumulator=self.inter,
            integral_clamped=integral_clamped,
            derivative_input=predicted_temp - previous_temperature,
            derivative_state=self.derv,
            proportional_band=self.pb,
            kp=self.kp,
            ki=self.ki,
            kd=self.kd,
            center=self.center,
            previous_temperature=previous_temperature,
            previous_update_time=previous_update_time,
            raw_output=self.u,
            final_output=self.u,
            measured_rate=self.roc,
            predicted_temperature=predicted_temp,
            predicted_error=predicted_error,
            tau_seconds=self.tau,
            theta_seconds=self.theta,
            stable_window_seconds=self.stable_window,
            center_factor=self.center_factor,
            new_target_before=new_target_before,
            new_target_after=self.new_target,
            target_change_temperature=self.start_change_temp,
            target_change_time=self.last_set_time,
            branch=branch,
        )
        return self.u

    def trace_diagnostics(self) -> PidSpTraceDiagnostics | None:
        return self._trace_diagnostics

    def set_target(self, set_point):
        self.set_point = set_point
        self.error = 0.0
        self.inter = 0.0
        self.derv = 0.0
        self.last_update = time.time()
        self.last_set_time = self.last_update
        self.start_change_temp = self.last
        self.new_target = True
        # Dynamically set self.center depending on set_point. Higher centers are needed to achieve higher temps, lower centers for lower temps.
        if self.units == "F":
            if set_point <= 240:
                self.center = set_point * self.center_factor
            else:
                self.center = set_point * self.center_factor * 1.2
        elif self.units == "C":
            if set_point <= 115:
                self.center = (set_point * 9 / 5 + 32) * self.center_factor
            else:
                self.center = (set_point * 9 / 5 + 32) * self.center_factor * 1.2
