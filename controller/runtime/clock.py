"""Injectable time source so the control loop is deterministically testable."""

import time
from abc import ABC, abstractmethod


class Clock(ABC):
    @abstractmethod
    def now(self) -> float: ...

    @abstractmethod
    def monotonic(self) -> float:
        """Return steady seconds in this runtime, never an epoch timestamp."""
        ...

    @abstractmethod
    def sleep(self, seconds: float) -> None: ...


class RealClock(Clock):
    def now(self):
        return time.time()

    def monotonic(self):
        return time.monotonic()

    def sleep(self, seconds):
        time.sleep(seconds)


class ManualClock(Clock):
    def __init__(self, start: float = 0.0, *, monotonic_start: float = 0.0):
        self._t = float(start)
        self._monotonic = float(monotonic_start)

    def now(self):
        return self._t

    def monotonic(self):
        return self._monotonic

    def sleep(self, seconds):
        self.advance(seconds)

    def advance(self, seconds):
        self._t += seconds
        self._monotonic += seconds

    def jump_wall(self, seconds):
        """Adjust epoch provenance without advancing physical time."""
        self._t += seconds
