from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import floor, isfinite

from grillplat.actuator_capabilities import AUGER_TIMING, AugerTiming


class PulseReason(str, Enum):
    FRAME_STARTED = "frame_started"
    FRAME_CONTINUED = "frame_continued"
    FRAME_SKIPPED = "frame_skipped"
    RESET = "reset"


class PulseResetReason(str, Enum):
    SAFETY = "safety"
    LID = "lid"
    MANUAL = "manual"
    MODE_CHANGE = "mode_change"


@dataclass(frozen=True, slots=True)
class PulseTransition:
    """A requested command correction, not an observed hardware edge."""

    at_s: float
    command_on: bool


@dataclass(frozen=True, slots=True)
class PulseFrameResult:
    """Immutable accounting for one nominal scheduler frame."""

    nominal_start_s: float
    nominal_end_s: float
    ended_at_s: float
    complete: bool
    skipped: bool
    latched_request: float
    credit_before_s: float
    credit_after_s: float
    scheduled_on_s: int
    delivered_on_s: float
    observed_transition_count: int
    actual_start_on: bool
    actual_end_on: bool
    reset_reason: PulseResetReason | None


@dataclass(frozen=True, slots=True)
class PulseDecision:
    reason: PulseReason
    frame_start_s: float
    latched_request: float
    scheduled_on_s: int
    credit_s: float
    command_on: bool
    transition: PulseTransition | None
    delivered_on_s: float
    frame_delivered_on_s: float
    reset_reason: PulseResetReason | None
    completed_frames: tuple[PulseFrameResult, ...]


class PulseScheduler:
    """Realize bounded mean auger duty as contiguous fixed pulse quanta.

    Time and observed auger state are injected by the caller. The class has no
    clock, hardware, controller, or settings dependencies.
    """

    __slots__ = (
        "_credit_before_s",
        "_credit_s",
        "_delivered_on_s",
        "_frame_actual_start_on",
        "_frame_delivered_on_s",
        "_frame_start_s",
        "_last_actual_on",
        "_last_at_s",
        "_latched_request",
        "_maximum_request",
        "_observed_transition_count",
        "_pending_reset",
        "_scheduled_on_s",
        "timing",
    )

    def __init__(self, timing: AugerTiming = AUGER_TIMING, maximum_request: float = 1.0) -> None:
        self._validate_finite("maximum_request", maximum_request)
        if not 0 < maximum_request <= 1:
            raise ValueError("maximum_request must be in (0, 1]")
        self.timing = timing
        self._maximum_request = maximum_request
        self._credit_before_s = 0.0
        self._credit_s = 0.0
        self._delivered_on_s = 0.0
        self._frame_actual_start_on = False
        self._frame_delivered_on_s = 0.0
        self._frame_start_s: float | None = None
        self._last_actual_on = False
        self._last_at_s: float | None = None
        self._latched_request = 0.0
        self._observed_transition_count = 0
        self._pending_reset: PulseResetReason | None = None
        self._scheduled_on_s = 0

    def reset(self, reason: PulseResetReason) -> PulseFrameResult | None:
        if not isinstance(reason, PulseResetReason):
            raise TypeError("reset reason must be a PulseResetReason")
        interrupted = self._finish_frame(
            ended_at_s=self._last_at_s,
            complete=False,
            skipped=False,
            reset_reason=reason,
        )
        self._credit_before_s = 0.0
        self._credit_s = 0.0
        self._frame_delivered_on_s = 0.0
        self._frame_start_s = None
        self._last_at_s = None
        self._last_actual_on = False
        self._latched_request = 0.0
        self._observed_transition_count = 0
        self._scheduled_on_s = 0
        self._pending_reset = reason
        return interrupted

    def advance(self, request: float, at_s: float, actual_auger_on: bool) -> PulseDecision:
        self._validate_request(request)
        self._validate_finite("at_s", at_s)
        if not isinstance(actual_auger_on, bool):
            raise TypeError("actual_auger_on must be a bool")
        if self._last_at_s is not None and at_s < self._last_at_s:
            raise ValueError("at_s must be monotone")

        reset_reason = self._pending_reset
        if self._frame_start_s is None:
            reason = PulseReason.RESET if reset_reason is not None else PulseReason.FRAME_STARTED
            self._begin_frame(request, at_s, actual_auger_on)
            completed_frames: tuple[PulseFrameResult, ...] = ()
            self._pending_reset = None
        else:
            completed_frames = self._advance_frames(request, at_s)
            if any(frame.skipped for frame in completed_frames):
                reason = PulseReason.FRAME_SKIPPED
            elif completed_frames:
                reason = PulseReason.FRAME_STARTED
            else:
                reason = PulseReason.FRAME_CONTINUED
            self._observe_actual(actual_auger_on)

        assert self._frame_start_s is not None
        elapsed_s = at_s - self._frame_start_s
        command_on = elapsed_s < self._scheduled_on_s
        transition = PulseTransition(at_s=at_s, command_on=command_on) if command_on != actual_auger_on else None
        return PulseDecision(
            reason=reason,
            frame_start_s=self._frame_start_s,
            latched_request=self._latched_request,
            scheduled_on_s=self._scheduled_on_s,
            credit_s=self._credit_s,
            command_on=command_on,
            transition=transition,
            delivered_on_s=self._delivered_on_s,
            frame_delivered_on_s=self._frame_delivered_on_s,
            reset_reason=reset_reason if reason is PulseReason.RESET else None,
            completed_frames=completed_frames,
        )

    def _advance_frames(self, request: float, at_s: float) -> tuple[PulseFrameResult, ...]:
        assert self._frame_start_s is not None
        nominal_end_s = self._frame_start_s + self.timing.frame_s
        if at_s < nominal_end_s:
            self._account_until(at_s)
            return ()

        crossed = int(floor((at_s - self._frame_start_s) / self.timing.frame_s))
        self._account_until(nominal_end_s)
        first_completed = self._finish_frame(nominal_end_s, complete=True, skipped=False, reset_reason=None)
        assert first_completed is not None
        completed = [first_completed]

        if crossed > 1:
            for offset in range(1, crossed):
                skipped_start_s = nominal_end_s + (offset - 1) * self.timing.frame_s
                completed.append(self._skip_frame(skipped_start_s))
            self._begin_frame(request, nominal_end_s + (crossed - 1) * self.timing.frame_s, self._last_actual_on)
        else:
            self._begin_frame(request, nominal_end_s, self._last_actual_on)
        self._account_until(at_s)
        return tuple(completed)

    def _begin_frame(self, request: float, nominal_start_s: float, actual_start_on: bool) -> None:
        self._credit_before_s = self._credit_s
        requested_s = request * self.timing.frame_s + self._credit_s
        authority_s = self._maximum_request * self.timing.frame_s
        capped_s = min(requested_s, authority_s, self.timing.frame_s)
        self._scheduled_on_s = int(floor(capped_s / self.timing.pulse_s) * self.timing.pulse_s)
        self._credit_s = requested_s - self._scheduled_on_s
        self._frame_actual_start_on = actual_start_on
        self._last_actual_on = actual_start_on
        self._frame_delivered_on_s = 0.0
        self._frame_start_s = nominal_start_s
        self._latched_request = request
        self._observed_transition_count = 0
        self._last_at_s = nominal_start_s

    def _skip_frame(self, nominal_start_s: float) -> PulseFrameResult:
        credit_before_s = self._credit_s
        self._credit_s = 0.0
        delivered_on_s = float(self.timing.frame_s) if self._last_actual_on else 0.0
        self._delivered_on_s += delivered_on_s
        return PulseFrameResult(
            nominal_start_s=nominal_start_s,
            nominal_end_s=nominal_start_s + self.timing.frame_s,
            ended_at_s=nominal_start_s + self.timing.frame_s,
            complete=False,
            skipped=True,
            latched_request=self._latched_request,
            credit_before_s=credit_before_s,
            credit_after_s=self._credit_s,
            scheduled_on_s=self.timing.frame_s if self._last_actual_on else 0,
            delivered_on_s=delivered_on_s,
            observed_transition_count=0,
            actual_start_on=self._last_actual_on,
            actual_end_on=self._last_actual_on,
            reset_reason=None,
        )

    def _finish_frame(
        self,
        ended_at_s: float | None,
        *,
        complete: bool,
        skipped: bool,
        reset_reason: PulseResetReason | None,
    ) -> PulseFrameResult | None:
        if self._frame_start_s is None or ended_at_s is None:
            return None
        return PulseFrameResult(
            nominal_start_s=self._frame_start_s,
            nominal_end_s=self._frame_start_s + self.timing.frame_s,
            ended_at_s=ended_at_s,
            complete=complete,
            skipped=skipped,
            latched_request=self._latched_request,
            credit_before_s=self._credit_before_s,
            credit_after_s=self._credit_s,
            scheduled_on_s=self._scheduled_on_s,
            delivered_on_s=self._frame_delivered_on_s,
            observed_transition_count=self._observed_transition_count,
            actual_start_on=self._frame_actual_start_on,
            actual_end_on=self._last_actual_on,
            reset_reason=reset_reason,
        )

    def _account_until(self, at_s: float) -> None:
        assert self._last_at_s is not None
        elapsed_s = at_s - self._last_at_s
        if self._last_actual_on:
            self._delivered_on_s += elapsed_s
            self._frame_delivered_on_s += elapsed_s
        self._last_at_s = at_s

    def _observe_actual(self, actual_auger_on: bool) -> None:
        if actual_auger_on != self._last_actual_on:
            self._observed_transition_count += 1
            self._last_actual_on = actual_auger_on

    def _validate_request(self, request: float) -> None:
        self._validate_finite("request", request)
        if not 0 <= request <= self._maximum_request:
            raise ValueError("request must be within scheduler authority")

    @staticmethod
    def _validate_finite(name: str, value: float) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
            raise ValueError(f"{name} must be finite")
