"""Injectable time source so the control loop is deterministically testable."""

import time
from abc import ABC, abstractmethod


class Clock(ABC):
	@abstractmethod
	def now(self) -> float: ...

	@abstractmethod
	def sleep(self, seconds: float) -> None: ...


class RealClock(Clock):
	def now(self):
		return time.time()

	def sleep(self, seconds):
		time.sleep(seconds)


class ManualClock(Clock):
	def __init__(self, start: float = 0.0):
		self._t = float(start)

	def now(self):
		return self._t

	def sleep(self, seconds):
		self._t += seconds

	def advance(self, seconds):
		self._t += seconds
