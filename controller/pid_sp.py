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
from controller.pid_sp_learning import build_pid_sp_live_learning
from controller.smith_predictor import SmithPredictor
from grillplat.actuator_capabilities import AUGER_TIMING

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
        # `center` serves two roles that are not the same thing: the output the
        # loop sits at when the error is zero, and the authority the integral is
        # allowed. Only the first is the operating point, and only the first is
        # something the identifier can supply, so they are tracked apart.
        # Until a model can name the operating point this stays the heuristic,
        # which is what the controller has always used for both.
        self.feed_forward = self.center
        self._integral_seeded = False

        self.stable_window = config.get("stable_window", 12)
        # Three control cycles is what the guards below mean. The control cycle
        # is the auger's pulse frame: Hold paces the auger from PulseScheduler's
        # timing, which takes no setting.
        self.cycle_time = AUGER_TIMING.frame_s

        # Off is the negative control every measurement of this needs, not a
        # user-facing choice: the identified operating point beats the heuristic
        # at every set point measured.
        self.bias_from_model = config.get("bias_from_model", True)

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
        identifier = self.identifier.status()
        predictor = self.predictor.status()
        return {
            "p": self.p,
            "i": self.i,
            "d": self.d,
            "u": self.u,
            "error": self.error,
            "set_point": self.set_point,
            "center": self.center,
            "feed_forward": self.feed_forward,
            "selected_temp": self._selected,
            "last_selected": self.last,
            "identifier": identifier,
            "predictor": predictor,
            "learning": build_pid_sp_live_learning(identifier, predictor),
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

    def _seed_integral_from_identified_hold(self, error):
        """Put the integral where the identified operating point says it belongs.

        Only inside the stable window, and only once. Outside it the reset above
        clears the accumulator on every tick, so a seed placed there would be
        wiped by the same update that set it; and an operating point is a
        statement about holding, which is not what the loop is doing on the way
        up. After the first seed the integral is the loop's own, and overwriting
        it would discard the correction it exists to make.
        """
        if self._integral_seeded or self.ki == 0 or abs(error) > self.stable_window:
            return
        held = self._held_duty()
        if held is None:
            return
        self.feed_forward = held
        # Against whatever the proportional term actually sits at: when the bias
        # is already the identified duty there is no gap left to seed.
        self.inter = (held - self._bias()) / self.ki
        self._integral_seeded = True

    def _bias(self):
        """The output the proportional term sits at when the error is zero.

        The heuristic `center` reads 0.5 everywhere, where 450 F holds at 0.205
        and 225 F nearer 0.07, so the proportional term asks for far more heat
        than the chamber needs and the integral spends the approach taking it
        back. The identified model names that duty outright.
        """
        if not self.bias_from_model:
            return self.center
        held = self._held_duty()
        return self.center if held is None else held

    def _held_duty(self):
        """The duty that holds THIS cook's set point, not the model's own."""
        return self.identifier.hold_duty(target_f=_to_f(self.set_point, self.units))

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
        # The identified duty that holds the operating point, once there is one.
        # `center` is where the loop sits at zero error, and it is a heuristic
        # that reads 0.225 at a 225 F set point where the grill actually holds
        # near 0.07 -- the whole gap has to be carried by the integral before the
        # loop can sit still. Seeding the integral with it once, rather than
        # substituting it into the proportional term, moves the loop to the right
        # output without softening the approach: the term the heuristic inflates
        # is also what drives the last stretch up to set point, and measuring the
        # substitution showed that cost more than the offset it removed.
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
            # set point after a set point change.
            #
            # Reaching the set point is a crossing, not a band. A band can be
            # stepped over: a chamber whose closest approach is 4.8 F never
            # clears this, so `new_target` latches for the rest of the cook, the
            # reset below then fires on every tick, and the integral is wiped
            # before it can accumulate. Without integral action the loop parks
            # at a standing offset, which is itself far enough out to keep the
            # band unreached -- the state sustains itself, and two cooks
            # differing only in starting temperature settle 8 F apart.
            if self.new_target and (error >= 0.0 or abs(error) <= 3):
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
            self.p = self.kp * error + self._bias()

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

        self._seed_integral_from_identified_hold(error)

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
            # An integrating model has no time constant to report; the trace
            # field predates that form and stays zero for it.
            tau_seconds=(0.0 if trusted is None else float(trusted.get("tau", 0.0))),
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
        if not self._integral_seeded:
            # Until the identifier names the operating point, the heuristic
            # centre is the best estimate of it available.
            self.feed_forward = self.center
        # A carried model describes the chamber temperature it was learned at,
        # and this is the moment the chamber is told to sit somewhere else.
        if self.identifier.retarget(_to_f(set_point, self.units)):
            self.predictor.trust(self.identifier.trusted_model())
