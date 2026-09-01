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

from controller.fopdt_identifier import FORM_FOPDT, FORM_IPDT, DutyHistory
from controller.pid_sp_delay_evidence import MAX_DELAY_BOUND_S

FORM_SOPDT = "sopdt"

#: Prediction outside this band is not a grill temperature.
TEMP_MIN_F, TEMP_MAX_F = -100.0, 1200.0
#: A one-step residual this large means the model has stopped describing the plant.
MAX_RESIDUAL_F = 100.0
MAX_RESIDUAL_STREAK = 4
#: Retention must outlast the deepest delayed window by more than the gap
#: between the last recorded command and the tick that integrates it. The
#: predictor does not choose that gap: it is whatever its caller's wall clock
#: reports between one tick and the next, so a constant margin can only make
#: truncation unlikely, never impossible -- _integrate detects and refuses it
#: instead of silently answering wrong.
HISTORY_MARGIN_S = 1800.0


def _incoming_model(model):
    """The identified model reduced to what propagation needs, by form."""
    form = model.get("form", FORM_FOPDT)
    common = {"form": form, "theta": float(model["theta"])}
    if form == FORM_IPDT:
        return {**common, "K_i": float(model["K_i"]), "c0": float(model["c0"])}
    if form == FORM_FOPDT:
        return {**common, "K": float(model["K"]), "tau": float(model["tau"])}
    if form == FORM_SOPDT:
        return {
            **common,
            "K": float(model["K"]),
            "tau_1": float(model["tau_1"]),
            "tau_2": float(model["tau_2"]),
        }
    raise ValueError("unsupported Smith predictor model form")


def _retention_s(model):
    """Return the retained horizon without adding evidence to propagation."""
    theta = float(model["theta"])
    if "basin_upper_s" not in model:
        return theta + HISTORY_MARGIN_S
    raw_upper = model["basin_upper_s"]
    if isinstance(raw_upper, bool):
        return None
    try:
        upper = float(raw_upper)
    except TypeError, ValueError:
        return None
    if not math.isfinite(upper) or upper < theta or upper > MAX_DELAY_BOUND_S:
        return None
    return upper + HISTORY_MARGIN_S


class SmithPredictor:
    def __init__(self):
        self._history = DutyHistory(MAX_DELAY_BOUND_S + HISTORY_MARGIN_S)
        self._model = None
        self._authority_identity = None
        self._x0 = 0.0
        self._xd = 0.0
        self._last_t = None
        self._z0 = 0.0
        self._zd = 0.0
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

    def governing_model(self) -> dict[str, float | str] | None:
        """Return the model currently selecting temperature, or measured fallback."""
        return dict(self._model) if self.active else None

    def record_output(self, applied):
        self._history.record(applied.timestamp, applied.ratio)
        if self._earliest_seen is None:
            self._earliest_seen = self._history.earliest()
        self._history.prune(applied.timestamp)

    def record_interval(self, start_s, end_s, realized_duty):
        """Record duty owned by one exact completed actuator interval."""
        self._history.record_interval(start_s, end_s, realized_duty)
        if self._earliest_seen is None:
            self._earliest_seen = self._history.earliest()
        self._history.prune(end_s)

    @staticmethod
    def _validated_trust(model, authority_digest=None):
        if model is None or not isinstance(model, dict):
            return None
        try:
            retention_s = _retention_s(model)
            incoming = _incoming_model(model)
        except KeyError, TypeError, ValueError:
            return None
        if retention_s is None or any(not math.isfinite(value) for key, value in incoming.items() if key != "form"):
            return None
        if authority_digest is None:
            authority_identity = tuple(sorted(incoming.items()))
        elif (
            isinstance(authority_digest, str)
            and len(authority_digest) == 64
            and all(character in "0123456789abcdef" for character in authority_digest)
        ):
            authority_identity = authority_digest
        else:
            return None
        return incoming, retention_s, authority_identity

    def preflight_trust(self, model, *, authority_digest=None) -> bool:
        """Validate activation and sticky-disable authority without mutation."""

        validated = self._validated_trust(model, authority_digest)
        if validated is None:
            return False
        incoming, _retention_s_value, authority_identity = validated
        if self._authority_identity == authority_identity and self._model is not None and incoming != self._model:
            return False
        return not (self._disabled and self._authority_identity == authority_identity)

    def trust(self, model, *, authority_digest=None) -> bool:
        """Adopt a preflight-valid authority, preserving sticky safety disables."""

        validated = self._validated_trust(model, authority_digest)
        if validated is None:
            return False
        incoming, retention_s, authority_identity = validated
        if not self.preflight_trust(model, authority_digest=authority_digest):
            return False
        self._history.set_retention_s(retention_s)
        prior_authority = self._authority_identity
        self._authority_identity = authority_identity
        if self._model is None:
            self._model = incoming
            self.reset()
            return self.active
        if self._disabled:
            assert prior_authority != authority_identity
            self._model = incoming
            self.reset()
            return self.active
        if incoming["theta"] != self._model["theta"]:
            self._model = incoming
            self.reset()
            return self.active
        self._model = incoming
        return self.active

    def _disable(self):
        """Fall back to measured temperature and reinitialize both branches
        equally, so a later re-trust starts from a zero correction. The streak
        counter stands: it is what status() reports to explain the disable."""
        self._x0 = 0.0
        self._xd = 0.0
        self._last_t = None
        self._last_measured = None
        self._z0 = 0.0
        self._zd = 0.0
        self._prev_xd = None
        self._disabled = True

    def reset(self):
        self._x0 = 0.0
        self._xd = 0.0
        self._last_t = None
        self._last_measured = None
        self._prev_xd = None
        self._z0 = 0.0
        self._zd = 0.0
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
        model = self._model
        theta = model["theta"]
        start = self._history.earliest()
        if start is None:
            return False
        pruned_past_start = start > self._earliest_seen
        if pruned_past_start and (t0 < start or t0 - theta < start):
            return True
        lo0, hi0 = max(t0, start), max(t1, start)
        lod, hid = max(t0 - theta, start), max(t1 - theta, start)
        if not self._history.covers(lo0, hi0) or not self._history.covers(lod, hid):
            return True
        for duration, duty in self._history.segments(lo0, hi0):
            self._x0, self._z0 = self._step_pair(
                self._x0,
                self._z0,
                duty,
                duration,
                model,
            )
        for duration, duty in self._history.segments(lod, hid):
            self._xd, self._zd = self._step_pair(
                self._xd,
                self._zd,
                duty,
                duration,
                model,
            )
        return False

    @staticmethod
    def _step(x, u, dt, model):
        """Exact scalar response retained for IPDT/FOPDT callers."""
        return SmithPredictor._step_pair(x, 0.0, u, dt, model)[0]

    @staticmethod
    def _step_pair(x, z, u, dt, model):
        """Exact identified response to one piecewise-constant duty segment."""
        if model["form"] == FORM_IPDT:
            return x + (model["K_i"] * u + model["c0"]) * dt, z
        if model["form"] == FORM_FOPDT:
            decay = math.exp(-dt / model["tau"])
            return x * decay + model["K"] * u * (1.0 - decay), z

        tau_1 = model["tau_1"]
        tau_2 = model["tau_2"]
        forcing = model["K"] * u
        decay_1 = math.exp(-dt / tau_1)
        decay_2 = math.exp(-dt / tau_2)
        next_z = forcing + (z - forcing) * decay_1
        if tau_1 == tau_2:
            coupling = (dt / tau_1) * decay_1
        else:
            exponent_delta = dt * (tau_1 - tau_2) / (tau_1 * tau_2)
            coupling = tau_1 / (tau_1 - tau_2) * decay_2 * math.expm1(exponent_delta)
        next_x = x * decay_2 + forcing * (1.0 - decay_2) + (z - forcing) * coupling
        return next_x, next_z

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
            "z0": self._z0,
            "zd": self._zd,
            "residual_streak": self._residual_streak,
            "truncated": self._truncated,
            "model": None if self._model is None else dict(self._model),
        }
