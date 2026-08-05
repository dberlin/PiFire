"""Deterministic relay pulse realization for the linear MPC bake-off."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True, slots=True)
class PulseFrame:
    """The immutable relay command emitted for one controller frame."""

    requested_duty: float
    realized_duty: float
    on_seconds: float
    on_transitions: int
    off_transitions: int

    @property
    def transitions(self) -> int:
        """Return the total number of relay state transitions in the frame."""
        return self.on_transitions + self.off_transitions


class PulseRealizer:
    """Quantize duty requests while preserving fractional on-time across frames."""

    def __init__(self, *, frame_s: float, quantum_s: float) -> None:
        if not isfinite(frame_s) or frame_s <= 0.0:
            raise ValueError("frame_s must be finite and positive")
        if not isfinite(quantum_s) or quantum_s <= 0.0:
            raise ValueError("quantum_s must be finite and positive")
        quanta = frame_s / quantum_s
        if not quanta.is_integer():
            raise ValueError("frame_s must be an exact multiple of quantum_s")
        self._frame_s = float(frame_s)
        self._quantum_s = float(quantum_s)
        self._quanta_per_frame = int(quanta)
        self._balance_s = 0.0
        self._relay_on = False

    def frame(self, requested_duty: float, *, skipped_frames: int = 0) -> PulseFrame:
        """Realize one frame and discard fractional carry after skipped frames.

        ``skipped_frames`` is a count rather than elapsed duration so recovering
        from a stalled scheduler remains constant-space and constant-time.
        """
        if not isfinite(requested_duty) or not 0.0 <= requested_duty <= 1.0:
            raise ValueError("requested_duty must be finite and within [0, 1]")
        if isinstance(skipped_frames, bool) or skipped_frames < 0:
            raise ValueError("skipped_frames must be a non-negative integer")
        if skipped_frames and int(skipped_frames) != skipped_frames:
            raise ValueError("skipped_frames must be a non-negative integer")
        if skipped_frames:
            self._balance_s = 0.0
            self._relay_on = False

        requested_on_s = self._frame_s * float(requested_duty) + self._balance_s
        requested_quanta = int(requested_on_s // self._quantum_s)
        on_quanta = min(self._quanta_per_frame, requested_quanta)
        on_seconds = on_quanta * self._quantum_s
        self._balance_s = requested_on_s - on_seconds
        if self._balance_s >= self._quantum_s:
            self._balance_s = self._quantum_s - 1e-12

        starts_on = on_quanta > 0
        on_transitions = int(not self._relay_on and starts_on)
        off_transitions = int(
            (self._relay_on and not starts_on)
            or (starts_on and on_quanta < self._quanta_per_frame)
        )
        self._relay_on = starts_on and on_quanta == self._quanta_per_frame
        return PulseFrame(
            requested_duty=float(requested_duty),
            realized_duty=on_seconds / self._frame_s,
            on_seconds=on_seconds,
            on_transitions=on_transitions,
            off_transitions=off_transitions,
        )
