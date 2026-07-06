"""Structural test for `ControlMode.run()`'s shared skeleton: a trivial
subclass records every hook invocation and we assert the ORDER matches the
template method's contract, mirroring control.py's original `_work_cycle`
block order: setup -> setup_safety -> [loop: on_tick -> status_fragment
(only when the 0.5s publish gate fires, BEFORE the safety block) ->
check_safety -> should_exit] -> teardown.

This complements (does not replace) the characterization oracle in
tests/characterization/, which is the real behavior-preservation gate.
"""

from controller.runtime.context import ControllerContext, Devices
from controller.runtime.store import InMemoryStore
from controller.runtime.clock import ManualClock
from controller.runtime.state import WorkCycleState
from controller.runtime.modes.base import ControlMode
from common.common import WriteKind
from tests.fakes.grill import FakeGrillPlatform
from tests.fakes.distance import FakeDistance
from tests.fakes.notifier import FakeNotifier
from tests.fakes.probes import FakeProbes
from tests.characterization.fixtures import base_settings, base_control, base_pellet_db
import tests.characterization.harness as harness  # noqa: F401  (binds control.eventLogger/Process_Monitor)


class _RecordingMode(ControlMode):
	name = 'Recording'

	def __init__(self, ctx, state):
		super().__init__(ctx, state)
		self.calls = []

	def setup(self):
		self.calls.append('setup')

	def setup_safety(self, ptemp):
		self.calls.append('setup_safety')
		return 'Active'

	def on_tick(self, now, current_output_status):
		self.calls.append('on_tick')

	def check_safety(self, now, ptemp):
		self.calls.append('check_safety')

	def status_fragment(self):
		self.calls.append('status_fragment')
		return {}

	def should_exit(self, now, ptemp):
		self.calls.append('should_exit')
		# Bound the loop to exactly one iteration.
		return True

	def teardown(self, ptemp):
		self.calls.append('teardown')


def _make_ctx():
	settings = base_settings()
	control_data = base_control(mode='Recording')
	pellet_db = base_pellet_db()
	probes = FakeProbes().script([120])
	store = InMemoryStore(control=control_data, settings=settings, pellet_db=pellet_db)
	grill = FakeGrillPlatform(outputs=tuple(settings['platform']['outputs']))
	notifier = FakeNotifier()
	ctx = ControllerContext(
		devices=Devices(grill_platform=grill, probe_complex=probes, dist_device=FakeDistance()),
		store=store,
		notifications=notifier,
		clock=ManualClock(),
	)
	return ctx


def test_control_mode_hook_order_one_bounded_tick():
	ctx = _make_ctx()
	# ControlMode.run() reads ctx.clock.now() exactly once pre-loop (for
	# start_time/display_toggle_time/etc.) before entering `while status ==
	# 'Active':`. Advance the clock right after that first read so the loop's
	# first `now = ctx.clock.now()` is > 0.5s past display_toggle_time,
	# firing the status-publish gate (and status_fragment()) within this
	# single bounded iteration.
	real_now = ctx.clock.now
	calls = {'n': 0}

	def _now():
		calls['n'] += 1
		if calls['n'] == 1:
			return real_now()
		return real_now() + 0.6

	ctx.clock.now = _now

	mode = _RecordingMode(ctx, WorkCycleState())
	mode.run()

	assert mode.calls == [
		'setup',
		'setup_safety',
		'on_tick',
		'status_fragment',
		'check_safety',
		'should_exit',
		'teardown',
	]
