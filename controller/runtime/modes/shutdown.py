from controller.runtime.modes.base import ControlMode
from controller.runtime.logic.fan import start_fan


class ShutdownMode(ControlMode):
	"""Shutdown mode: fan+power on at setup (same branch as Startup/Reignite/
	Smoke/Hold, minus the smart-start duty-cycle special case), exits once
	`shutdown_duration` has elapsed since start, then fan+power off at
	teardown (shared with Monitor/Manual/Prime). No mode-specific on_tick,
	check_safety, or status_fragment."""

	name = 'Shutdown'

	def setup(self):
		start_fan(self.grill, self.settings)
		self.grill.power_on()
		import control as _control
		_control.eventLogger.debug('Power ON, Fan ON, Igniter OFF, Auger OFF')

	def should_exit(self, now, ptemp) -> bool:
		return (now - self.state.start_time) > self.settings['shutdown']['shutdown_duration']

	def teardown(self, ptemp):
		self.grill.fan_off()
		self.grill.power_off()
		import control as _control
		_control.eventLogger.debug('Fan OFF, Power OFF')
