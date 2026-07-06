"""Golden-master characterization tests for the OUTER control loop
(controller.runtime.controller.Controller), extracted from control.py's old
__main__ in Task 10.1.

These pin the ORCHESTRATION behavior -- mode dispatch, Stop/Error cleanup,
boot-to-monitor, switch-off, timers, hopper/settings/probe-profile handling --
that the golden `test_modes_golden.py` suite does NOT cover (that suite pins the
inner work cycle). To isolate the loop from the mode internals, the per-mode
dispatch methods (work_cycle/next_mode/recipe_mode) are replaced with spies, so
a scenario asserts "the loop called work_cycle('Smoke') then next_mode(...)"
rather than re-running a full Smoke cycle.

METHOD: run-then-freeze (same as test_modes_golden.py) -- assertions capture the
behavior of the current code, verified by running it.

REGRESSION GUARD: `test_setup_runs_initial_hopper_check_and_binds_pelletdb`
locks in the pre-loop hopper check that the mode-extraction refactor had dropped
(without it, pelletdb went unbound before the loop's first check_notify and the
boot-time hopper level was never read). See Controller.setup().
"""

import logging

import controller.runtime.controller as controller_mod
from controller.runtime.controller import Controller
from controller.runtime.context import ControllerContext, Devices
from controller.runtime.store import InMemoryStore
from controller.runtime.clock import ManualClock
from tests.characterization.fixtures import base_settings, base_control, base_pellet_db
from tests.fakes.grill import FakeGrillPlatform
from tests.fakes.probes import FakeProbes
from tests.fakes.distance import FakeDistance
from tests.fakes.notifier import FakeNotifier
import control

# The per-mode handlers reference control.eventLogger via `import control as
# _control`; bind it so any stray logging call is harmless. (The loop itself
# logs through ctx.event_log, set below.)
control.eventLogger = logging.getLogger('characterization')
control.controlLogger = logging.getLogger('characterization')


class _RecordingDistance(FakeDistance):
	"""FakeDistance that records get_level / update_distances calls."""

	def __init__(self, level=100):
		super().__init__(level)
		self.get_level_calls = 0
		self.update_distances_calls = []

	def get_level(self, override=False):
		self.get_level_calls += 1
		return self._level

	def update_distances(self, empty, full):
		self.update_distances_calls.append((empty, full))


def make_controller(settings, control_data, pellet_db, *, grill=None, dist=None, clock=None):
	store = InMemoryStore(control=control_data, settings=settings, pellet_db=pellet_db)
	grill = grill or FakeGrillPlatform(
		standalone=settings['platform'].get('standalone', True), outputs=tuple(settings['platform']['outputs'])
	)
	dist = dist or _RecordingDistance()
	notifier = FakeNotifier()
	logger = logging.getLogger('characterization')
	ctx = ControllerContext(
		devices=Devices(grill_platform=grill, probe_complex=FakeProbes().script([70] * 4), dist_device=dist),
		store=store,
		notifications=notifier,
		clock=clock or ManualClock(),
		event_log=logger,
		control_log=logger,
	)
	c = Controller(ctx)
	return c, ctx, store, grill, dist, notifier


def _spy_dispatch(c):
	"""Replace the per-mode dispatch methods with recording spies so a tick
	exercises only the loop, not real work cycles. Returns the call log."""
	calls = []
	c.work_cycle = lambda mode: calls.append(('work_cycle', mode))
	c.next_mode = lambda next_mode, setpoint=0: calls.append(('next_mode', next_mode, setpoint))
	c.recipe_mode = lambda start_step=0: calls.append(('recipe_mode', start_step))
	return calls


def _neutralize_externals(monkeypatch):
	"""Stub the module-level notify/cookfile/shutdown helpers the loop calls."""
	sent = []
	monkeypatch.setattr(controller_mod, 'check_notify', lambda *a, **k: sent.append(('check_notify', k)))
	monkeypatch.setattr(controller_mod, 'send_notifications', lambda *a, **k: sent.append(('send_notifications', a, k)))
	monkeypatch.setattr(controller_mod, 'create_cookfile', lambda *a, **k: sent.append(('create_cookfile',)))
	monkeypatch.setattr(controller_mod, 'os', _FakeOs(sent))
	return sent


class _FakeOs:
	def __init__(self, sink):
		self._sink = sink

	def system(self, cmd):
		self._sink.append(('os.system', cmd))


# --------------------------------------------------------------------------
# setup()
# --------------------------------------------------------------------------


def test_setup_runs_initial_hopper_check_and_binds_pelletdb(monkeypatch):
	# REGRESSION GUARD: the pre-loop hopper check (dropped during the mode
	# extraction) must run in setup(), binding pelletdb and reading the level.
	_neutralize_externals(monkeypatch)
	settings = base_settings()
	c, ctx, store, grill, dist, notifier = make_controller(settings, base_control(mode='Stop'), base_pellet_db())
	c.setup()
	assert c.pelletdb is not None
	assert dist.get_level_calls == 1  # boot-time hopper read happened
	assert c.pelletdb['current']['hopper_level'] == 100
	assert store.read_pellet_db()['current']['hopper_level'] == 100  # persisted


def test_setup_boot_to_monitor_requests_monitor_mode(monkeypatch):
	_neutralize_externals(monkeypatch)
	settings = base_settings()
	settings['globals']['boot_to_monitor'] = True
	c, ctx, store, grill, dist, notifier = make_controller(settings, base_control(mode='Stop'), base_pellet_db())
	c.setup()
	control = store.read_control()
	assert control['mode'] == 'Monitor'
	assert control['updated'] is True


def test_setup_no_boot_to_monitor_leaves_mode(monkeypatch):
	_neutralize_externals(monkeypatch)
	settings = base_settings()
	settings['globals']['boot_to_monitor'] = False
	c, ctx, store, grill, dist, notifier = make_controller(settings, base_control(mode='Stop'), base_pellet_db())
	c.setup()
	assert store.read_control()['mode'] == 'Stop'


# --------------------------------------------------------------------------
# tick(): mode dispatch (spied)
# --------------------------------------------------------------------------


def test_tick_smoke_dispatches_work_cycle_then_next_mode(monkeypatch):
	_neutralize_externals(monkeypatch)
	settings = base_settings()
	control_data = base_control(mode='Smoke')
	control_data['updated'] = True
	control_data['next_mode'] = 'Stop'
	c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db())
	calls = _spy_dispatch(c)
	c.setup()
	c.tick()
	assert ('work_cycle', 'Smoke') in calls
	assert ('next_mode', 'Stop', 0) in calls
	assert calls.index(('work_cycle', 'Smoke')) < calls.index(('next_mode', 'Stop', 0))


def test_tick_hold_dispatches_work_cycle_then_next_mode(monkeypatch):
	_neutralize_externals(monkeypatch)
	settings = base_settings()
	control_data = base_control(mode='Hold')
	control_data['updated'] = True
	control_data['next_mode'] = 'Stop'
	c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db())
	calls = _spy_dispatch(c)
	c.setup()
	c.tick()
	assert ('work_cycle', 'Hold') in calls
	assert ('next_mode', 'Stop', 0) in calls


def test_tick_monitor_sets_status_monitor_and_runs_cycle(monkeypatch):
	_neutralize_externals(monkeypatch)
	settings = base_settings()
	control_data = base_control(mode='Monitor')
	control_data['updated'] = True
	c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db())
	calls = _spy_dispatch(c)
	c.setup()
	c.tick()
	assert store.read_control()['status'] == 'monitor'
	assert ('work_cycle', 'Monitor') in calls


def test_tick_manual_runs_cycle_without_next_mode(monkeypatch):
	_neutralize_externals(monkeypatch)
	settings = base_settings()
	control_data = base_control(mode='Manual')
	control_data['updated'] = True
	c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db())
	calls = _spy_dispatch(c)
	c.setup()
	c.tick()
	assert ('work_cycle', 'Manual') in calls
	assert not any(name == 'next_mode' for name in (x[0] for x in calls))


def test_tick_recipe_dispatches_recipe_mode(monkeypatch):
	_neutralize_externals(monkeypatch)
	settings = base_settings()
	control_data = base_control(mode='Recipe')
	control_data['updated'] = True
	control_data['recipe']['start_step'] = 2
	c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db())
	calls = _spy_dispatch(c)
	c.setup()
	c.tick()
	assert ('recipe_mode', 2) in calls


def test_tick_shutdown_sets_next_mode_stop_and_dispatches(monkeypatch):
	_neutralize_externals(monkeypatch)
	settings = base_settings()
	control_data = base_control(mode='Shutdown')
	control_data['updated'] = True
	c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db())
	calls = _spy_dispatch(c)
	c.setup()
	c.tick()
	assert ('work_cycle', 'Shutdown') in calls
	assert ('next_mode', 'Stop', 0) in calls


# --------------------------------------------------------------------------
# tick(): Stop / Error cleanup
# --------------------------------------------------------------------------


def test_tick_stop_mode_cleanup(monkeypatch):
	_neutralize_externals(monkeypatch)
	settings = base_settings()
	control_data = base_control(mode='Stop')
	control_data['updated'] = True
	c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db())
	_spy_dispatch(c)
	c.setup()
	c.tick()
	# Outputs driven off, status reset to Stop, control reset, display cleared.
	names = [name for name, _ in grill.calls]
	assert 'auger_off' in names and 'igniter_off' in names and 'fan_off' in names
	assert 'power_off' in names
	assert ('clear', None) in store.display_commands().list()
	assert store.read_status()['mode'] == 'Stop'
	control = store.read_control()
	# NOTE (faithful freeze): the Stop path sets control['status']='inactive'
	# and THEN does `control = read_control(flush=True)`, which rebinds control
	# to a fresh default_control() -- so the 'inactive' assignment is discarded
	# and the persisted status is default_control()'s status (''). This dead
	# assignment exists in the original __main__ too; preserved as-is.
	assert control['status'] == ''
	assert control['updated'] is False
	assert control['next_mode'] == 'Stop'


def test_tick_error_mode_cleanup(monkeypatch):
	_neutralize_externals(monkeypatch)
	settings = base_settings()
	control_data = base_control(mode='Error')
	control_data['updated'] = True
	clock = ManualClock()
	c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db(), clock=clock)
	_spy_dispatch(c)
	c.setup()
	c.tick()
	control = store.read_control()
	assert control['mode'] == 'Error'
	assert control['status'] == 'inactive'
	names = [name for name, _ in grill.calls]
	assert 'power_off' in names
	assert ('clear', None) in store.display_commands().list()
	assert clock.now() >= 3  # the 3s error dwell went through ctx.clock.sleep


# --------------------------------------------------------------------------
# tick(): switch, timer, hopper, settings
# --------------------------------------------------------------------------


def test_tick_switch_off_triggers_stop(monkeypatch):
	_neutralize_externals(monkeypatch)
	settings = base_settings()
	settings['platform']['standalone'] = False
	control_data = base_control(mode='Smoke')
	control_data['updated'] = False
	grill = FakeGrillPlatform(standalone=False, outputs=tuple(settings['platform']['outputs']))
	c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db(), grill=grill)
	_spy_dispatch(c)
	c.setup()  # binds last = input status (on)
	grill.set_input(False)  # user flips switch off
	c.tick()
	# switch-off writes Stop + updated, which then runs the Stop cleanup path
	control = store.read_control()
	assert control['next_mode'] == 'Stop'  # Stop cleanup ran
	assert store.read_status()['mode'] == 'Stop'


def test_tick_timer_expiry_sends_notification(monkeypatch):
	sent = _neutralize_externals(monkeypatch)
	settings = base_settings()
	control_data = base_control(mode='Stop')
	control_data['updated'] = False
	control_data['timer'] = {'start': 1, 'paused': 0, 'end': 5}
	control_data['notify_data'] = [{'type': 'timer', 'req': True, 'shutdown': True, 'keep_warm': True}]
	clock = ManualClock(start=10)  # now (10) >= end (5)
	c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db(), clock=clock)
	_spy_dispatch(c)
	c.setup()
	c.tick()
	assert any(x[0] == 'send_notifications' and x[1] == ('Timer_Expired',) for x in sent)
	control = store.read_control()
	assert control['notify_data'][0]['req'] is False
	assert control['timer']['end'] == 0


def test_tick_hopper_check_reads_and_clears(monkeypatch):
	_neutralize_externals(monkeypatch)
	settings = base_settings()
	control_data = base_control(mode='Stop')
	control_data['updated'] = False
	control_data['hopper_check'] = True
	dist = _RecordingDistance(level=42)
	c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db(), dist=dist)
	_spy_dispatch(c)
	c.setup()
	dist.get_level_calls = 0  # ignore the setup() boot-time read
	c.tick()
	assert dist.get_level_calls == 1
	assert store.read_pellet_db()['current']['hopper_level'] == 42
	assert store.read_control()['hopper_check'] is False


def test_tick_distance_update_updates_distances_and_clears(monkeypatch):
	_neutralize_externals(monkeypatch)
	settings = base_settings()
	control_data = base_control(mode='Stop')
	control_data['updated'] = False
	control_data['distance_update'] = True
	dist = _RecordingDistance()
	c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db(), dist=dist)
	_spy_dispatch(c)
	c.setup()
	c.tick()
	assert len(dist.update_distances_calls) == 1
	assert store.read_control()['distance_update'] is False


def test_tick_settings_update_clears_flag(monkeypatch):
	_neutralize_externals(monkeypatch)
	settings = base_settings()
	control_data = base_control(mode='Stop')
	control_data['updated'] = False
	control_data['settings_update'] = True
	c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db())
	_spy_dispatch(c)
	c.setup()
	c.tick()
	assert store.read_control()['settings_update'] is False


def test_tick_probe_profile_update_clears_flag(monkeypatch):
	_neutralize_externals(monkeypatch)
	settings = base_settings()
	control_data = base_control(mode='Stop')
	control_data['updated'] = False
	control_data['probe_profile_update'] = True
	c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db())
	_spy_dispatch(c)
	c.setup()
	c.tick()
	assert store.read_control()['probe_profile_update'] is False
