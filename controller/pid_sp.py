#!/usr/bin/env python3

"""
*****************************************
 PiFire PID Controller with a Smith Predictor
*****************************************

 Description: The auto-centering PID controller, regulating on a temperature
 with identified dead time removed instead of on the raw probe reading.

 This controller models nothing itself. controller/fopdt_identifier.py learns
 the grill's FOPDT parameters passively from applied duty, and
 controller/smith_predictor.py turns those parameters into one temperature per
 tick. That single value drives P, I and D -- the derivative compares
 consecutive SELECTED temperatures and never subtracts a measured sample from a
 predicted one.

 Until the identifier's gates clear, the selected temperature is the measured
 one, and outside the three-cycle window after a setpoint change (where the
 startup reduction applies here but is a dead store in pid_ac) this matches
 pid_ac term for term. start_change_temp is seeded from that same first real
 reading rather than pid_ac's fixed 150, so the two also diverge on how much
 integral windup a setpoint change permits before that window ends.

 PID controller based on proportional band in standard PID form
 https://en.wikipedia.org/wiki/PID_controller#Ideal_versus_standard_PID_form
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
from controller.fopdt_identifier import FOPDTIdentifier
from controller.pid_base import PIDControllerBase
from controller.smith_predictor import SmithPredictor

#: Output reduction for the first three cycles after a setpoint change.
STARTUP_REDUCTION = 0.65


def _to_f(value, units):
    return value if units == "F" else value * 9.0 / 5.0 + 32.0


def _from_f(value, units):
    return value if units == "F" else (value - 32.0) * 5.0 / 9.0


class Controller(PIDControllerBase):
    def __init__(self, config, units, cycle_data):
        super().__init__(config, units, cycle_data)

        self._calculate_gains(config.get("PB", 60.0), config.get("Ti", 180.0), config.get("Td", 45.0))

        self.p = 0.0
        self.i = 0.0
        self.d = 0.0
        self.u = 0

        self.pb = config.get("PB", 60.0)

        self.units = units

        self.last_update = time.time()
        self.last_set_time = time.time()
        self.error = 0.0
        self.set_point = 0

        self.center = 0.5
        self.center_factor = config.get("center_factor", 0.0010)

        self.stable_window = config.get("stable_window", 12)
        self.cycle_time = cycle_data["HoldCycleTime"]

        self.derv = 0.0
        self.inter = 0.0

        # Seeded from the first observed temperature, so the first derivative
        # is exactly zero rather than measured against a value that was never
        # sampled. start_change_temp inherits the same None until set_target()
        # copies it in, which happens immediately below.
        self.last = None
        self.start_change_temp = None
        self.new_target = False

        self._trace_diagnostics = None
        self.identifier = FOPDTIdentifier()
        self.predictor = SmithPredictor()
        self._selected = None

        self.set_target(0.0)

    # ------------------------------------------------------------ capabilities
    def set_output(self, applied):
        self.identifier.record_output(applied)
        self.predictor.record_output(applied)

    def get_status(self):
        return {
            "p": self.p,
            "i": self.i,
            "d": self.d,
            "u": self.u,
            "error": self.error,
            "set_point": self.set_point,
            "center": self.center,
            "selected_temp": self._selected,
            "last_selected": self.last,
            "identifier": self.identifier.status(),
            "predictor": self.predictor.status(),
        }

    def get_model_snapshot(self):
        model = self.identifier.trusted_model()
        if model is None:
            return None
        # Provenance only: status() and any future policy can see where the
        # model came from, but restore() never refuses on it -- a K/tau fit
        # near one setpoint is still a reasonable starting estimate at another.
        return {**model, "setpoint_f": _to_f(self.set_point, self.units)}

    def restore_model(self, snapshot):
        if not self.identifier.restore(snapshot):
            return False
        self.predictor.trust(self.identifier.trusted_model())
        return True

    # ------------------------------------------------------------------ control
    def update(self, current):
        current_time = time.time()
        previous_update_time = self.last_update
        previous_temperature = self.last
        dt = self._elapsed_since_last_update(current_time)
        branch = ControllerBranch.NONE
        new_target_before = self.new_target

        measured_f = _to_f(current, self.units)
        self.identifier.observe(measured_f, current_time)
        trusted = self.identifier.trusted_model()
        self.predictor.trust(trusted)
        selected = _from_f(self.predictor.temperature(measured_f, current_time), self.units)
        self._selected = selected
        # Zero on the first update, where there is no earlier reading to
        # difference against rather than a rate that happens to be zero.
        measured_rate = 0.0 if previous_temperature is None else (selected - previous_temperature) / dt

        # Seed both at startup: self.last has no measured value on the very
        # first update after construction, and start_change_temp inherits the
        # same None until this same update seeds it. Neither can fire again
        # afterward -- self.last is a real number from here on, and a later
        # setpoint change copies that real value into start_change_temp
        # rather than None.
        if self.last is None:
            self.last = selected
            # There is no earlier reading to difference against on this tick, so
            # the derivative is taken against this one and is exactly zero.
            previous_temperature = selected
        # In the untrusted regime, where selected is the measured value, a
        # reading of exactly 0.0 in native units is not a temperature to
        # differentiate against; repair it the same way on a setpoint change.
        # Once a model is trusted, selected includes the predictor's
        # correction, so a faulted reading no longer reaches self.last as an
        # exact zero -- this repair's reach ends there.
        if self.last == 0.0 and self.new_target:
            self.last = selected
            previous_temperature = selected
            branch = ControllerBranch.INITIALIZATION
        if self.start_change_temp is None:
            self.start_change_temp = selected

        error = selected - self.set_point

        if error < -self.pb:
            self.u = 1.0
            if branch is ControllerBranch.NONE:
                branch = ControllerBranch.FULL_HEAT
        elif error > self.stable_window:
            self.u = 0.0
            if branch is ControllerBranch.NONE:
                branch = ControllerBranch.OVERSHOOT
        else:
            # Reset integral term when the temperature first reaches or exceeds
            # set point after a set point change
            if self.new_target and abs(error) <= 3:
                self.new_target = False
                if branch is ControllerBranch.NONE:
                    branch = ControllerBranch.TARGET_REACHED

            # Reset integral if the system is not within the stable window, or
            # has not reached halfway to the set point within 3 cycles. Prevents
            # overshoots on small set point changes.
            reset_integral = (abs(error) > self.stable_window) or (
                self.new_target
                and current_time - self.last_set_time >= self.cycle_time * 3
                and abs(error) <= abs(self.start_change_temp - self.set_point) / 2
            )
            if reset_integral:
                self.inter = 0.0
                if branch is ControllerBranch.NONE:
                    branch = ControllerBranch.RESET

            # Minimize derivative to maximize descent rate when setting a new
            # lower set point
            if (self.new_target and self.set_point < current) or (abs(error) > self.pb / 2):
                self.derv = 0.0

            # P
            self.p = self.kp * error + self.center

            # I
            self.inter += error * dt
            self.i = self.ki * self.inter
            unclamped_integral_term = self.i
            self.i = max(min(self.i, self.center), -self.center)
            integral_clamped = self.i != unclamped_integral_term

            # D
            self.derv = (selected - self.last) / dt
            self.d = self.kd * self.derv

            # PID
            self.u = self.p + self.i + self.d

            # Ease off for the first three cycles after a set point change, so a
            # small change does not overshoot.
            if error < self.pb and current_time - self.last_set_time < self.cycle_time * 3:
                self.u = self.u * STARTUP_REDUCTION
        if error < -self.pb or error > self.stable_window:
            # The saturating branches never ran the integral, so no clamp of it
            # was reached on this tick whatever the previous one left behind.
            integral_clamped = False

        self.error = error
        self.last = selected
        self.last_update = current_time
        self._trace_diagnostics = PidSpTraceDiagnostics(
            observed_dt_seconds=dt,
            error=error,
            proportional_term=self.p,
            integral_term=self.i,
            derivative_term=self.d,
            integral_accumulator=self.inter,
            integral_clamped=integral_clamped,
            derivative_input=selected - previous_temperature,
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
            # This controller no longer extrapolates a future temperature from a
            # configured tau/theta: the Smith predictor removes the identified
            # dead time from the reading instead, so the temperature it selects
            # IS the prediction and the error taken from it IS the predicted
            # error. tau/theta come from the identified model, and read zero
            # while nothing is trusted and the selected value is the measured one.
            measured_rate=measured_rate,
            predicted_temperature=selected,
            predicted_error=error,
            tau_seconds=(0.0 if trusted is None else trusted["tau"]),
            theta_seconds=(0.0 if trusted is None else trusted["theta"]),
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
        # Higher centers are needed to reach higher temps, lower centers keep
        # low set points stable.
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
