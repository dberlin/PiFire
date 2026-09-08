"""Injectable time source so the control loop is deterministically testable."""

import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import override


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
    @override
    def now(self) -> float:
        return time.time()

    @override
    def monotonic(self) -> float:
        return time.monotonic()

    @override
    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class CallableClock(Clock):
    """Adapt existing callable timing seams without changing either domain."""

    def __init__(
        self,
        *,
        monotonic_clock: Callable[[], float],
        wall_clock: Callable[[], float],
    ) -> None:
        self._monotonic_clock: Callable[[], float] = monotonic_clock
        self._wall_clock: Callable[[], float] = wall_clock

    @override
    def now(self) -> float:
        return self._wall_clock()

    @override
    def monotonic(self) -> float:
        return self._monotonic_clock()

    @override
    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class ManualClock(Clock):
    def __init__(self, start: float = 0.0, *, monotonic_start: float = 0.0) -> None:
        self._t: float = float(start)
        self._monotonic: float = float(monotonic_start)

    @override
    def now(self) -> float:
        return self._t

    @override
    def monotonic(self) -> float:
        return self._monotonic

    @override
    def sleep(self, seconds: float) -> None:
        self.advance(seconds)

    def advance(self, seconds: float) -> None:
        self._t += seconds
        self._monotonic += seconds

    def jump_wall(self, seconds: float) -> None:
        """Adjust epoch provenance without advancing physical time."""
        self._t += seconds
