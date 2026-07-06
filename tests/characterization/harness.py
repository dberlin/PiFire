"""Harness for characterization ("golden master") tests of control._work_cycle.

TERMINATION SAFETY is the whole point of this file. `_work_cycle` runs
`while status == 'Active': ...` and the ONLY things that break out of that
loop are:
  - control['updated'] becoming True (mode change / error / reignite / etc.)
  - mode-specific timer/temp exits (Startup/Reignite/Shutdown/Prime)
  - the max-temp safety check
  - a Recipe-mode step trigger

Smoke (steady state), Hold, Monitor, and Manual have NO such natural exit --
they run forever under real hardware (the outer process is killed/restarted
instead). To bound those scenarios without changing control.py, `run_mode`
accepts `probe_cap`: after that many `read_probes()` calls, the harness
injects a MERGE write of `{'updated': True}` into the store. The loop reads
that at the *top* of its next iteration (`execute_control_writes()` +
`read_control()`), sees `control['updated']` is True, and breaks cleanly --
so post-loop cleanup (auger/igniter off, metrics, monitor.stop_monitor())
still runs, exactly as it would for any other mode-change request.

Other pitfalls handled here (see task brief):
  1. `control.eventLogger` / `control.controlLogger` are only bound inside
     control.py's `if __name__ == '__main__':` block, but `_work_cycle` calls
     them directly. We bind them to stdlib loggers before running.
  2. `Process_Monitor` spawns a heartbeat thread and shells out to
     `supervisorctl` on timeout. We monkeypatch `control.Process_Monitor` to
     a no-op stand-in for the duration of `run_mode`.
"""
import logging
from dataclasses import dataclass, field

from controller.runtime.context import ControllerContext, Devices
from controller.runtime.store import InMemoryStore
from controller.runtime.clock import ManualClock
from common.common import WriteKind
from tests.fakes.grill import FakeGrillPlatform
from tests.fakes.distance import FakeDistance
from tests.fakes.notifier import FakeNotifier
import control


# --- Pitfall 1: loggers are normally bound in `if __name__ == '__main__':` ---
control.eventLogger = logging.getLogger('characterization')
control.controlLogger = logging.getLogger('characterization')


# --- Pitfall 2: Process_Monitor spawns a heartbeat thread + shells out to
# supervisorctl. It is neutralized globally for all tests by the autouse
# `_neutralize_process_monitor` fixture in tests/conftest.py (which no-ops the
# shared class's methods), so nothing here needs to patch it. ---


@dataclass
class CaptureResult:
	grill_calls: list = field(default_factory=list)
	display_commands: list = field(default_factory=list)
	notifications: list = field(default_factory=list)
	final_control: dict = field(default_factory=dict)
	final_status: dict = field(default_factory=dict)
	final_metrics: dict = field(default_factory=dict)


class _CappedProbes:
	"""Wraps a probe fake; after `cap` reads, injects `{'updated': True}` into
	the store (MERGE) so the work-cycle loop breaks cleanly on its next
	top-of-iteration read_control(). This is the belt-and-suspenders bound for
	modes with no natural timer/temp exit (Smoke steady-state, Hold, Monitor,
	Manual)."""

	def __init__(self, probes, store, cap):
		self._probes = probes
		self._store = store
		self._cap = cap
		self._n = 0

	def read_probes(self):
		self._n += 1
		if self._n >= self._cap:
			self._store.write_control({'updated': True}, WriteKind.MERGE, origin='test-cap')
		return self._probes.read_probes()

	def __getattr__(self, name):
		return getattr(self._probes, name)


def make_ctx(settings, control_data, pellet_db, probes, grill=None, runner=None):
	# `runner` is accepted for signature symmetry with `run_mode` (which does
	# the actual `control.build_runner` monkeypatching around `_work_cycle`);
	# `make_ctx` itself never constructs a runner, so this is unused here.
	store = InMemoryStore(control=control_data, settings=settings, pellet_db=pellet_db)
	grill = grill or FakeGrillPlatform(
		dc_fan=settings['platform'].get('dc_fan', False),
		standalone=settings['platform'].get('standalone', True),
		outputs=tuple(settings['platform']['outputs']),
	)
	notifier = FakeNotifier()
	ctx = ControllerContext(
		devices=Devices(grill_platform=grill, probe_complex=probes, dist_device=FakeDistance()),
		store=store, notifications=notifier, clock=ManualClock(),
	)
	return ctx, grill, notifier


def run_mode(mode, *, settings, control_data, pellet_db, probes, grill=None, probe_cap=None, runner=None):
	"""Run one `control._work_cycle` invocation hermetically and capture its
	observable effects.

	`probe_cap`: if set, bounds modes with no natural exit -- see
	`_CappedProbes` above. Pick a value comfortably larger than the number of
	iterations needed to exercise the behavior under test (e.g. enough for a
	couple of auger on/off cycles) but bounded so the test can't hang.

	`runner`: if set, monkeypatches `control.build_runner` for the duration of
	the call so Hold mode uses this object (e.g. a scripted
	`FakeControllerRunner`) instead of constructing a real PID/MPC core. Lets
	Hold-mode scenarios pin the runner's `.latest()` output deterministically
	without depending on real controller math.
	"""
	ctx, grill, notifier = make_ctx(settings, control_data, pellet_db, probes, grill)

	if probe_cap is not None:
		probes = _CappedProbes(probes, ctx.store, probe_cap)
		ctx.devices.probe_complex = probes

	# Process_Monitor is neutralized globally by the autouse fixture in
	# tests/conftest.py, so we only need to (optionally) inject a fake runner.
	prev_build_runner = control.build_runner
	if runner is not None:
		control.build_runner = lambda *a, **k: (runner, 'Active')
	try:
		control._work_cycle(mode, ctx)
	finally:
		control.build_runner = prev_build_runner

	return CaptureResult(
		grill_calls=grill.calls,
		display_commands=ctx.store.display_commands().list(),
		notifications=notifier.sent,
		final_control=ctx.store.read_control(),
		final_status=ctx.store.read_status(),
		final_metrics=ctx.store.read_metrics(),
	)
