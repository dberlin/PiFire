from controller.runtime.runner import SyncControllerRunner, NormalizedOutput, build_runner, _build_core


class _Core:
	def __init__(self):
		self.target = None
		self.period = 5.0

	def set_target(self, sp):
		self.target = sp

	def update(self, temp):
		return {'cycle_ratio': 0.4, 'fan': {'duty': 60}}

	def get_control_period(self):
		return self.period


def test_sync_runner_normalizes_dict_output():
	r = SyncControllerRunner(_Core())
	r.set_target(225)
	out = r.latest_from(200.0)
	assert isinstance(out, NormalizedOutput)
	assert out.cycle_ratio == 0.4
	assert out.fan == {'duty': 60}


def test_sync_runner_float_output_has_no_fan():
	class FloatCore(_Core):
		def update(self, temp):
			return 0.25

	out = SyncControllerRunner(FloatCore()).latest_from(190.0)
	assert out.cycle_ratio == 0.25 and out.fan is None


class _RecordingLogger:
	def __init__(self):
		self.exceptions = []

	def exception(self, msg):
		self.exceptions.append(msg)


def test_build_runner_logs_on_load_failure_when_logger_given():
	settings = {'controller': {'selected': 'does_not_exist', 'config': {}}, 'globals': {'units': 'F'}, 'cycle_data': {}}
	control = {'primary_setpoint': 225}
	logger = _RecordingLogger()

	runner, status = build_runner(settings, control, logger=logger)

	assert runner is None
	assert status == 'Inactive'
	assert len(logger.exceptions) == 1
	assert 'Error occurred loading controller module' in logger.exceptions[0]


def test_build_runner_does_not_require_logger():
	settings = {'controller': {'selected': 'does_not_exist', 'config': {}}, 'globals': {'units': 'F'}, 'cycle_data': {}}
	control = {'primary_setpoint': 225}

	runner, status = build_runner(settings, control)

	assert runner is None
	assert status == 'Inactive'


def test_build_core_logs_on_load_failure_when_logger_given():
	settings = {'controller': {'selected': 'does_not_exist', 'config': {}}, 'globals': {'units': 'F'}, 'cycle_data': {}}
	control = {'primary_setpoint': 225}
	logger = _RecordingLogger()

	core, status = _build_core(settings, control, logger=logger)

	assert core is None
	assert status == 'Inactive'
	assert len(logger.exceptions) == 1


def test_sync_runner_wants_async_reflects_core_and_stop_is_noop():
	from controller.runtime.runner import SyncControllerRunner

	class _Core:
		def wants_async(self):
			return False

	r = SyncControllerRunner(_Core())
	assert r.wants_async() is False
	r.stop()  # must exist and be a harmless no-op for the sync runner
