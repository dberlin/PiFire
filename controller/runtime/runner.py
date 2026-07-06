"""Temperature-controller execution seam (PID/MPC/etc). Sync impl == today's
inline behavior; a ThreadedControllerRunner may be added later for MPC."""
import importlib
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

	def controller_state(self):
		return dict(self._core.__dict__)


def _build_core(settings, control, logger=None):
	try:
		controller_type = settings['controller']['selected']
		module = importlib.import_module(f'controller.{controller_type}')
	except Exception:
		if logger is not None:
			logger.exception('Error occurred loading controller module. Trace dump: ')
		return None, 'Inactive'
	core = module.Controller(
		settings['controller']['config'][controller_type],
		settings['globals']['units'], settings['cycle_data'])
	core.set_target(control['primary_setpoint'])
	return core, 'Active'


def build_runner(settings, control, logger=None):
	core, status = _build_core(settings, control, logger=logger)
	if core is None:
		return None, status
	return SyncControllerRunner(core), status
