from controller.runtime.modes.base import ControlMode


class MonitorMode(ControlMode):
	"""Monitor mode: idles with fan/power off. No auger cycle, no controller,
	no mode-specific safety checks or exit conditions. Relies entirely on
	the shared skeleton's universal breaks (mode-change, switch-off,
	max-temp, Recipe)."""

	name = 'Monitor'

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
