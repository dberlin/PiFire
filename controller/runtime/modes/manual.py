from controller.runtime.modes.base import ControlMode


class ManualMode(ControlMode):
	"""Manual mode: idles with fan/power off at setup, same as Monitor. Its
	actual behavior is the SHARED manual-override block in ControlMode.run(),
	which fires because `self.name == 'Manual'` satisfies that block's gate
	unconditionally (regardless of settings['safety']['allow_manual_changes']).
	No mode-specific on_tick/check_safety/should_exit/status_fragment needed."""

	name = 'Manual'

	def setup(self):
		self.grill.fan_off()
		self.grill.power_off()
		import control as _control

		_control.eventLogger.debug('Power OFF, Fan OFF, Igniter OFF, Auger OFF')

	def teardown(self, ptemp):
		self.grill.fan_off()
		self.grill.power_off()
		import control as _control

		_control.eventLogger.debug('Fan OFF, Power OFF')
