import threading

from controller.runtime.runner import ThreadedControllerRunner, build_runner, SyncControllerRunner


class FakeCore:
	"""Deterministic core. update() records temps, returns a fixed dict, and
	sets `updated` so tests synchronize on a real event, not a sleep."""

	def __init__(self, period=0.01, commands_fan=False, ratio=0.5):
		self._period = period
		self._commands_fan = commands_fan
		self._ratio = ratio
		self.target = None
		self.updates = []
		self.updated = threading.Event()
		self.tag = 'core-a'

	def get_control_period(self):
		return self._period

	def commands_fan(self):
		return self._commands_fan

	def wants_async(self):
		return True

	def set_target(self, sp):
		self.target = sp

	def update(self, temp):
		self.updates.append(temp)
		self.updated.set()
		return {'cycle_ratio': self._ratio, 'fan': None}


class BlockingCore(FakeCore):
	"""update() blocks on `gate` so a test can observe latest() not blocking
	while a solve is in flight."""

	def __init__(self, **kw):
		super().__init__(**kw)
		self.entered = threading.Event()
		self.gate = threading.Event()

	def update(self, temp):
		self.entered.set()
		self.gate.wait(2.0)
		return super().update(temp)


def test_threaded_runner_solves_submitted_temp():
	core = FakeCore()
	r = ThreadedControllerRunner(core)
	try:
		r.submit(70.0)
		assert core.updated.wait(2.0)  # thread ran update(70.0)
		assert 70.0 in core.updates
		out = r.latest()
		assert out.cycle_ratio == 0.5 and out.fan is None
		assert r.control_period() == 0.01
		assert r.wants_async() is True
	finally:
		r.stop()


def test_threaded_runner_latest_does_not_block_during_solve():
	core = BlockingCore()
	r = ThreadedControllerRunner(core)
	try:
		r.submit(70.0)
		assert core.entered.wait(2.0)  # thread is inside a blocked update()
		# latest() must return promptly (the default snapshot), not wait for the solve.
		out = r.latest()
		assert out.cycle_ratio == 0.0  # initial default; solve has not stored yet
		core.gate.set()  # let the solve finish
		assert core.updated.wait(2.0)
		assert r.latest().cycle_ratio == 0.5
	finally:
		core.gate.set()
		r.stop()


def test_threaded_runner_stop_terminates_thread():
	core = FakeCore()
	r = ThreadedControllerRunner(core)
	thread = r._thread
	assert thread.is_alive()
	r.stop()
	assert not thread.is_alive()
	r.stop()  # idempotent


def test_threaded_runner_set_target_and_reconfigure_applied_by_thread():
	core = FakeCore()
	r = ThreadedControllerRunner(core)
	try:
		r.submit(70.0)
		assert core.updated.wait(2.0)
		r.set_target(225)
		# target is applied on the thread's next iteration; observe via the core
		deadline = threading.Event()
		for _ in range(200):
			if core.target == 225:
				break
			deadline.wait(0.01)
		assert core.target == 225
	finally:
		r.stop()


def test_threaded_runner_controller_state_snapshot():
	core = FakeCore()
	r = ThreadedControllerRunner(core)
	try:
		snap = r.controller_state()
		assert snap['tag'] == 'core-a'  # well-formed before first solve
		assert snap is not core.__dict__  # a copy, not the live dict
	finally:
		r.stop()


def test_build_runner_selects_threaded_for_wants_async_core(monkeypatch):
	import controller.runtime.runner as runner_mod

	core = FakeCore()  # wants_async() -> True

	monkeypatch.setattr(runner_mod, '_build_core', lambda *a, **k: (core, 'Active'))
	r, status = build_runner({}, {})
	try:
		assert isinstance(r, ThreadedControllerRunner)
		assert status == 'Active'
	finally:
		r.stop()


def test_build_runner_selects_sync_for_non_async_core(monkeypatch):
	import controller.runtime.runner as runner_mod

	class SyncCore(FakeCore):
		def wants_async(self):
			return False

	monkeypatch.setattr(runner_mod, '_build_core', lambda *a, **k: (SyncCore(), 'Active'))
	r, status = build_runner({}, {})
	assert isinstance(r, SyncControllerRunner)
	r.stop()  # no-op
