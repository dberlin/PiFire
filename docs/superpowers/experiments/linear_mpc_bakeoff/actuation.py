"""Production pulse-scheduler adapter for the linear MPC bake-off simulator."""

from __future__ import annotations

from dataclasses import dataclass

from controller.runtime.logic.pulse import PulseFrameResult, PulseScheduler


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


class PulseSimulationDriver:
    """Drive the production pulse scheduler against a perfect simulated relay."""

    __slots__ = ("_actual_auger_on", "_at_s", "_last_completed_frame", "_scheduler")

    def __init__(self) -> None:
        self._scheduler = PulseScheduler()
        self._actual_auger_on = False
        self._at_s = 0.0
        self._last_completed_frame: PulseFrameResult | None = None

    @property
    def last_completed_frame(self) -> PulseFrameResult | None:
        """Return the most recently completed production scheduler frame."""
        return self._last_completed_frame

    def frame(self, requested_duty: float) -> PulseFrame:
        """Return one production-scheduled frame under perfect actuation."""
        decision = self._scheduler.advance(requested_duty, self._at_s, self._actual_auger_on)
        if decision.completed_frames:
            self._last_completed_frame = decision.completed_frames[-1]
        on_transitions = off_transitions = 0
        if decision.command_on != self._actual_auger_on:
            on_transitions += int(decision.command_on)
            off_transitions += int(not decision.command_on)
            self._actual_auger_on = decision.command_on
            self._scheduler.advance(requested_duty, self._at_s, self._actual_auger_on)

        on_seconds = float(decision.scheduled_on_s)
        if 0.0 < on_seconds < self._scheduler.timing.frame_s:
            cutoff = self._scheduler.advance(
                requested_duty,
                self._at_s + on_seconds,
                self._actual_auger_on,
            )
            if cutoff.command_on != self._actual_auger_on:
                on_transitions += int(cutoff.command_on)
                off_transitions += int(not cutoff.command_on)
                self._actual_auger_on = cutoff.command_on
                self._scheduler.advance(
                    requested_duty,
                    self._at_s + on_seconds,
                    self._actual_auger_on,
                )

        self._at_s += self._scheduler.timing.frame_s
        return PulseFrame(
            requested_duty=decision.latched_request,
            realized_duty=on_seconds / self._scheduler.timing.frame_s,
            on_seconds=on_seconds,
            on_transitions=on_transitions,
            off_transitions=off_transitions,
        )
