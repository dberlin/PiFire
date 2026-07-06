"""Temperature-controller execution seam (PID/MPC/etc).

`ControllerRunner` is the abstract interface `HoldMode.on_tick` drives:
set_target/submit/latest to run the control math, reconfigure() to rebuild the
core on a settings change, control_period() for the mode's poll interval, and
commands_fan() so the caller knows whether this controller issues its own fan
command (MPC) or leaves fan control to the temperature-profile logic.
`SyncControllerRunner` runs the underlying controller module's `update()`
synchronously on submit/latest -- control math and probe-read cadence are the
same cadence. `ThreadedControllerRunner` runs the core on a background thread
at its own control period and hands back non-blocking snapshots via
`.latest()`, decoupling control-math cadence from the probe-read cadence.
`build_runner` selects between the two by the core's `wants_async()` (MPC
requests the threaded runner; other controllers get the sync runner).
"""

import importlib
import threading
from abc import ABC, abstractmethod
from collections import namedtuple

from controller.base import normalize_controller_output

NormalizedOutput = namedtuple('NormalizedOutput', ['cycle_ratio', 'fan'])


class ControllerRunner(ABC):
	@abstractmethod
	def set_target(self, setpoint): ...
	@abstractmethod
	def submit(self, temp): ...
	@abstractmethod
	def latest(self): ...
	@abstractmethod
	def reconfigure(self, settings, control): ...
	@abstractmethod
	def control_period(self): ...
	@abstractmethod
	def commands_fan(self): ...
	@abstractmethod
	def wants_async(self): ...
	@abstractmethod
	def stop(self): ...


class SyncControllerRunner(ControllerRunner):
	def __init__(self, core):
		self._core = core
		self._temp = None

	def set_target(self, setpoint):
		self._core.set_target(setpoint)

	def submit(self, temp):
		self._temp = temp

	def latest(self):
		raw = self._core.update(self._temp)
		ratio, fan = normalize_controller_output(raw)
		return NormalizedOutput(cycle_ratio=ratio, fan=fan)

	def latest_from(self, temp):
		self.submit(temp)
		return self.latest()

	def reconfigure(self, settings, control, logger=None):
		core, status = _build_core(settings, control, logger=logger)
		if status == 'Active':
			self._core = core
		return status

	def control_period(self):
		return self._core.get_control_period()

	def commands_fan(self):
		return self._core.commands_fan()

	def wants_async(self):
		return self._core.wants_async()

	def stop(self):
		pass

	def controller_state(self):
		return dict(self._core.__dict__)


_UNSET = object()


class ThreadedControllerRunner(ControllerRunner):
	"""Runs core.update() on a background thread at the core's control period, so
	an expensive solve never blocks the caller. submit()/latest() are
	non-blocking snapshots; the running core is mutated only by the thread."""

	def __init__(self, core):
		self._core = core
		self._lock = threading.Lock()
		self._temp = None
		self._output = NormalizedOutput(cycle_ratio=0.0, fan=None)
		self._pending_target = _UNSET
		self._pending_core = None
		self._state_snapshot = dict(core.__dict__)
		self._control_period = core.get_control_period()
		self._commands_fan = core.commands_fan()
		self._stop_event = threading.Event()
		self._thread = threading.Thread(target=self._loop, daemon=True)
		self._thread.start()

	def _loop(self):
		while not self._stop_event.is_set():
			with self._lock:
				temp = self._temp
				target = self._pending_target
				self._pending_target = _UNSET
				new_core = self._pending_core
				self._pending_core = None
			if new_core is not None:
				self._core = new_core
			if target is not _UNSET:
				self._core.set_target(target)
			if temp is not None:
				raw = self._core.update(temp)
				ratio, fan = normalize_controller_output(raw)
				snap = dict(self._core.__dict__)
				with self._lock:
					self._output = NormalizedOutput(cycle_ratio=ratio, fan=fan)
					self._state_snapshot = snap
			# Interruptible sleep; wait(None/0) would block forever, so floor it.
			self._stop_event.wait(self._control_period or 1.0)

	def set_target(self, setpoint):
		with self._lock:
			self._pending_target = setpoint

	def submit(self, temp):
		with self._lock:
			self._temp = temp

	def latest(self):
		with self._lock:
			return self._output

	def reconfigure(self, settings, control, logger=None):
		core, status = _build_core(settings, control, logger=logger)
		if status == 'Active':
			with self._lock:
				self._pending_core = core
		return status

	def control_period(self):
		return self._control_period

	def commands_fan(self):
		return self._commands_fan

	def wants_async(self):
		return True

	def controller_state(self):
		with self._lock:
			return dict(self._state_snapshot)

	def stop(self):
		self._stop_event.set()
		self._thread.join(timeout=2.0)


def _build_core(settings, control, logger=None):
	try:
		controller_type = settings['controller']['selected']
		module = importlib.import_module(f'controller.{controller_type}')
	except Exception:
		if logger is not None:
			logger.exception('Error occurred loading controller module. Trace dump: ')
		return None, 'Inactive'
	core = module.Controller(
		settings['controller']['config'][controller_type], settings['globals']['units'], settings['cycle_data']
	)
	core.set_target(control['primary_setpoint'])
	return core, 'Active'


def build_runner(settings, control, logger=None):
	core, status = _build_core(settings, control, logger=logger)
	if core is None:
		return None, status
	if core.wants_async():
		return ThreadedControllerRunner(core), status
	return SyncControllerRunner(core), status
