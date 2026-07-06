from controller.runtime.runner import SyncControllerRunner, NormalizedOutput


class _Core:
	def __init__(self): self.target = None; self.period = 5.0
	def set_target(self, sp): self.target = sp
	def update(self, temp): return {'cycle_ratio': 0.4, 'fan': {'duty': 60}}
	def get_control_period(self): return self.period


def test_sync_runner_normalizes_dict_output():
	r = SyncControllerRunner(_Core())
	r.set_target(225)
	out = r.latest_from(200.0)
	assert isinstance(out, NormalizedOutput)
	assert out.cycle_ratio == 0.4
	assert out.fan == {'duty': 60}


def test_sync_runner_float_output_has_no_fan():
	class FloatCore(_Core):
		def update(self, temp): return 0.25
	out = SyncControllerRunner(FloatCore()).latest_from(190.0)
	assert out.cycle_ratio == 0.25 and out.fan is None
