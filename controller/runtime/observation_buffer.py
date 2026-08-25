"""Generation-bound buffering for controller observation outcomes."""

from __future__ import annotations

import collections
from copy import deepcopy
from dataclasses import dataclass, replace

from controller.runtime.runner import (
    ObservationOutcomeDrain,
    ObservationOutcomeEnvelope,
    ObservationTerminalDrop,
    _freeze_evidence,
)

_MAX_DROPPED_SEQUENCES = 60


@dataclass(frozen=True, slots=True)
class _BufferedTerminalDrop:
    drop: ObservationTerminalDrop
    counted_eviction: bool


class ObservationOutcomeBuffer:
    """Own bounded outcomes until their configuration generation can drain.

    The caller owns synchronization. Outcomes and terminal drops are released
    only while their generation has a bound evidence context.
    """

    def __init__(self, capacity: int):
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._capacity = capacity
        self._outcomes: collections.deque[ObservationOutcomeEnvelope] = collections.deque(maxlen=capacity)
        self._terminal_drops: collections.deque[_BufferedTerminalDrop] = collections.deque()
        self._contexts: dict[int, tuple[str, str | None]] = {}

    def bind_context(
        self,
        generation: int,
        session_id: str,
        cook_id: str | None,
    ) -> None:
        self._contexts[generation] = (session_id, cook_id)

    def retire_context(self, generation: int) -> None:
        self._contexts.pop(generation, None)

    def append_outcome(self, envelope: ObservationOutcomeEnvelope) -> None:
        owned = replace(envelope, outcome=deepcopy(envelope.outcome), evidence=())
        if len(self._outcomes) == self._capacity:
            evicted = self._outcomes.popleft()
            self._terminal_drops.append(
                _BufferedTerminalDrop(
                    ObservationTerminalDrop(
                        evicted.submission_sequence,
                        evicted.configuration_generation,
                        evicted.observation,
                        "runner-outcome-evicted",
                    ),
                    counted_eviction=True,
                )
            )
        self._outcomes.append(owned)

    def append_terminal_drop(self, drop: ObservationTerminalDrop) -> None:
        self._terminal_drops.append(_BufferedTerminalDrop(drop, counted_eviction=False))

    def drain(self) -> ObservationOutcomeDrain:
        envelopes: list[ObservationOutcomeEnvelope] = []
        withheld_outcomes: collections.deque[ObservationOutcomeEnvelope] = collections.deque(maxlen=self._capacity)
        for envelope in self._outcomes:
            context = self._contexts.get(envelope.configuration_generation)
            if context is None:
                withheld_outcomes.append(envelope)
                continue
            session_id, cook_id = context
            envelopes.append(
                replace(
                    envelope,
                    evidence=_freeze_evidence(
                        envelope.outcome,
                        session_id,
                        cook_id,
                        envelope.observation,
                    ),
                )
            )
        self._outcomes = withheld_outcomes

        terminal_drops: list[ObservationTerminalDrop] = []
        dropped_sequences: list[int] = []
        dropped_count = 0
        withheld_drops: collections.deque[_BufferedTerminalDrop] = collections.deque()
        for buffered in self._terminal_drops:
            drop = buffered.drop
            if drop.configuration_generation not in self._contexts:
                withheld_drops.append(buffered)
                continue
            terminal_drops.append(drop)
            if buffered.counted_eviction:
                dropped_count += 1
                dropped_sequences.append(drop.submission_sequence)
        self._terminal_drops = withheld_drops

        return ObservationOutcomeDrain(
            envelopes=tuple(envelopes),
            terminal_drops=tuple(terminal_drops),
            dropped_count=dropped_count,
            dropped_sequences=tuple(dropped_sequences[-_MAX_DROPPED_SEQUENCES:]),
        )
