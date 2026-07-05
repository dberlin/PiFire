class FakeProbes:
	def __init__(self):
		self._script = []
		self._i = 0
		self._info = {}
		self._errors = []

	def script(self, items):
		norm = []
		for it in items:
			if isinstance(it, dict):
				norm.append(it)
			else:
				norm.append({'primary': {'Grill': it}, 'food': {}, 'aux': {}, 'tr': {}})
		self._script = norm
		self._i = 0
		return self

	def read_probes(self):
		if not self._script:
			return {'primary': {'Grill': 0}, 'food': {}, 'aux': {}, 'tr': {}}
		item = self._script[min(self._i, len(self._script) - 1)]
		self._i += 1
		return item

	def get_device_info(self):
		return self._info

	def get_errors(self):
		return self._errors

	def update_probe_profiles(self, x):
		pass

	def update_units(self, x):
		pass
