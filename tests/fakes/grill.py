class FakeGrillPlatform:
	def __init__(self, dc_fan=False, standalone=True, input_on=True, outputs=('power', 'auger', 'fan', 'igniter')):
		self.calls = []
		self._input_on = input_on
		self._status = {k: False for k in outputs}
		self._status['pwm'] = 100
		self._status['frequency'] = 100

	def _rec(self, name, *args):
		self.calls.append((name, args))

	def get_input_status(self):
		return self._input_on

	def set_input(self, on):  # test helper
		self._input_on = on

	def get_output_status(self):
		return dict(self._status)

	def set_pwm_frequency(self, f):
		self._rec('set_pwm_frequency', f)
		self._status['frequency'] = f

	def set_duty_cycle(self, pct):
		self._rec('set_duty_cycle', pct)
		self._status['pwm'] = pct

	def igniter_on(self):
		self._rec('igniter_on')
		self._status['igniter'] = True

	def igniter_off(self):
		self._rec('igniter_off')
		self._status['igniter'] = False

	def auger_on(self):
		self._rec('auger_on')
		self._status['auger'] = True

	def auger_off(self):
		self._rec('auger_off')
		self._status['auger'] = False

	def fan_on(self, dc=None):
		self._rec('fan_on', dc)
		self._status['fan'] = True

	def fan_off(self):
		self._rec('fan_off')
		self._status['fan'] = False

	def power_on(self):
		self._rec('power_on')
		self._status['power'] = True

	def power_off(self):
		self._rec('power_off')
		self._status['power'] = False

	def pwm_fan_ramp(self, *a):
		self._rec('pwm_fan_ramp', *a)

	def supported_commands(self, x):
		return {'data': {'supported_cmds': []}}

	def cleanup(self):
		self._rec('cleanup')
