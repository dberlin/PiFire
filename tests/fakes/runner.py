class FakeControllerRunner:
	def __init__(self, period=None):
		self._script = []
		self._i = 0
		self.target = None
		self._period = period

	def script(self, outputs):
		self._script = list(outputs)
		self._i = 0
		return self

	def set_target(self, setpoint):
		self.target = setpoint

	def submit(self, temp):
		pass

	def reconfigure(self, settings, control):
		return 'Active'

	def control_period(self):
		return self._period

	def latest(self):
		if not self._script:
			return None
		out = self._script[min(self._i, len(self._script) - 1)]
		self._i += 1
		return out
