#!/usr/bin/env python3

"""
*****************************************
 PiFire Smith Predictor
*****************************************

 Description: Removes identified dead time from the temperature a controller
 sees, without giving up feedback.

 Two model states are driven by the same applied-duty history: x0 by the duty as
 it is applied, xd by the same history shifted back by theta. The controller
 input is the measured-output Smith form

     T_smith = T_measured + x0 - xd

 The measured term preserves feedback for plant/model mismatch and for
 disturbances the model knows nothing about; the difference removes the
 identified delay from that signal. The unknown constant offset appears in both
 branches identically and cancels, which is why it is estimated for
 identification but never persisted.

 Canonically Fahrenheit inside, so a persisted gain means the same thing
 regardless of the configured units.

*****************************************
"""

import math

from controller.fopdt_identifier import DELAYS, DutyHistory

#: Prediction outside this band is not a grill temperature.
TEMP_MIN_F, TEMP_MAX_F = -100.0, 1200.0
#: A one-step residual this large means the model has stopped describing the plant.
MAX_RESIDUAL_F = 100.0
MAX_RESIDUAL_STREAK = 4
#: Retention must outlast the deepest delayed window by more than any control
#: cycle a deployment could plausibly configure: nothing bounds HoldCycleTime,
#: so a constant margin can only make truncation unlikely, never impossible --
#: _integrate detects and refuses it instead of silently answering wrong.
HISTORY_MARGIN_S = 1800.0


class SmithPredictor:
    def __init__(self):
        self._history = DutyHistory(float(DELAYS.max()) + HISTORY_MARGIN_S)
        self._model = None
        self._x0 = 0.0
        self._xd = 0.0
        self._last_t = None
        self._last_measured = None
        self._prev_xd = None
        self._residual_streak = 0
        self._disabled = False
        #: The earliest timestamp ever recorded, never moved by pruning: the
        #: line `_integrate` uses to tell "history was pruned past where a
        #: window needs to reach" from "history legitimately begins here".
        self._earliest_seen = None
        #: Count of truncation detections. A detection resets `_last_t`, so
        #: the following tick re-seeds instead of re-checking: a persistent
        #: truncation is only detected on every OTHER affected tick, not
        #: every one. Not sticky, but never cleared, so it stays diagnosable.
        self._truncated = 0

    @property
    def active(self):
        return self._model is not None and not self._disabled

    def record_output(self, applied):
        self._history.record(applied.timestamp, applied.ratio)
        if self._earliest_seen is None:
            self._earliest_seen = self._history.earliest()
        self._history.prune(applied.timestamp)

    def trust(self, model):
        """Adopt identified parameters.

        Going from untrusted to trusted starts both branches equal, so the
        correction begins at exactly zero and control never steps.

        A later revision of K or tau updates in place and KEEPS the states: the
        identifier only revises after a confirmation window, and snapping a
        live correction back to zero would be the very step equal-state
        initialization exists to avoid. A revision of theta does reinitialize,
        because the delayed state was accumulated under the old delay and no
        longer means what it did.

        A disable is sticky. PID-SP re-asserts the trusted model every tick, so
        clearing the flag on any trust() call would undo a safety disable on the
        next tick and leave the envelope doing nothing.
        """
        if model is None:
            return
        incoming = {"K": float(model["K"]), "tau": float(model["tau"]), "theta": float(model["theta"])}
        if self._model is None:
            self._model = incoming
            self.reset()
            return
        if self._disabled:
            if incoming != self._model:
                self._model = incoming
                self.reset()
            return
        if incoming["theta"] != self._model["theta"]:
            self._model = incoming
            self.reset()
            return
        self._model = incoming

    def _disable(self):
        """Fall back to measured temperature and reinitialize both branches
        equally, so a later re-trust starts from a zero correction. The streak
        counter stands: it is what status() reports to explain the disable."""
        self._x0 = 0.0
        self._xd = 0.0
        self._last_t = None
        self._last_measured = None
        self._prev_xd = None
        self._disabled = True

    def reset(self):
        self._x0 = 0.0
        self._xd = 0.0
        self._last_t = None
        self._last_measured = None
        self._prev_xd = None
        self._residual_streak = 0
        self._disabled = False

    def temperature(self, measured_f, timestamp):
        """The temperature the controller should regulate on."""
        measured = float(measured_f)
        now = float(timestamp)
        if not self.active:
            self._last_t = now
            return measured
        if self._last_t is None:
            self._last_t = now
            self._last_measured = measured
            self._prev_xd = self._xd
            return measured

        truncated = self._integrate(self._last_t, now)
        self._last_t = now
        if truncated:
            self._truncated += 1
            self.reset()
            return measured

        predicted = measured + self._x0 - self._xd
        expected_measured = self._last_measured + (self._xd - self._prev_xd)
        residual = abs(measured - expected_measured)
        if not self._safe(predicted, residual):
            self._disable()
            return measured
        self._last_measured = measured
        self._prev_xd = self._xd
        return predicted

    def _integrate(self, t0, t1):
        """Advance both branches over the same recorded duty history: x0 over
        [t0, t1], xd over the same window shifted back by theta. Returns
        whether either window needed history that retention no longer has.

        Both windows are clamped to never precede the earliest duty still on
        record. `DutyHistory.segments` resolves a query before its earliest
        recorded timestamp by holding that duty constant backward, which is
        correct for filling a gap inside recorded history but would otherwise
        manufacture duty that was never applied, for either branch. Clamping
        both identically is what keeps xd(t) equal to x0(t - theta): before t
        reaches theta the delayed branch does not advance at all, and from
        then on the two windows differ by exactly theta.

        That clamp alone cannot tell "nothing was ever recorded this far
        back" (the normal startup case, harmless) from "recorded duty a
        window still needs was pruned away" (retention outrun by
        configuration, a wrong `xd` masquerading as a correct one). Pruning
        can only ever move `earliest()` forward from `_earliest_seen`, never
        the reverse, so `earliest() > _earliest_seen` is exactly "something
        was pruned" -- combined with a window whose natural bound reaches
        before that pruned boundary, it is truncation, and the caller must
        refuse the result rather than silently clamp it.
        """
        tau = self._model["tau"]
        gain = self._model["K"]
        theta = self._model["theta"]
        start = self._history.earliest()
        if start is None:
            return False
        pruned_past_start = start > self._earliest_seen
        if pruned_past_start and (t0 < start or t0 - theta < start):
            return True
        lo0, hi0 = max(t0, start), max(t1, start)
        for duration, duty in self._history.segments(lo0, hi0):
            self._x0 = self._step(self._x0, duty, duration, gain, tau)
        lod, hid = max(t0 - theta, start), max(t1 - theta, start)
        for duration, duty in self._history.segments(lod, hid):
            self._xd = self._step(self._xd, duty, duration, gain, tau)
        return False

    @staticmethod
    def _step(x, u, dt, gain, tau):
        """Exact first-order response to a constant input over dt."""
        decay = math.exp(-dt / tau)
        return x * decay + gain * u * (1.0 - decay)

    def _safe(self, predicted, residual):
        """Whether the prediction and the plant/model agreement are within bounds.

        The residual is the delayed branch's own one-step forecast error of
        the MEASURED signal (xd models measured output, per the FOPDT
        T(t) = T_offset + x_d(t)), not the Smith output's forecast error: the
        output predicts temperature after the dead time elapses, not the next
        measurement, and comparing against it would flag the correction's own
        magnitude as model failure. The isfinite guard is redundant with the
        band check below (every comparison against NaN is False, and an
        infinite prediction fails the band) but is kept explicit on a safety
        path.
        """
        if not math.isfinite(predicted) or not math.isfinite(self._x0) or not math.isfinite(self._xd):
            return False
        if not TEMP_MIN_F <= predicted <= TEMP_MAX_F:
            return False
        if residual > MAX_RESIDUAL_F:
            self._residual_streak += 1
        else:
            self._residual_streak = 0
        return self._residual_streak < MAX_RESIDUAL_STREAK

    def status(self):
        return {
            "active": self.active,
            "disabled": self._disabled,
            "x0": self._x0,
            "xd": self._xd,
            "residual_streak": self._residual_streak,
            "truncated": self._truncated,
            "model": None if self._model is None else dict(self._model),
        }
