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
enqueues a validated delta setting `updated=True`. The loop reads that at the
*top* of its next iteration (`execute_control_writes()` +
`read_control()`), sees `control['updated']` is True, and breaks cleanly --
so post-loop cleanup (auger/igniter off, metrics, monitor.stop_monitor())
still runs, exactly as it would for any other mode-change request.

Other pitfalls handled here:
  1. Controller logging goes to the operator's `events` / `control` loggers,
     which `create_logger` points at real files at process startup. `make_ctx`
     injects the `characterization` logger into `ControllerContext` instead, so
     a captured run logs where the test can see it and nowhere else. Injection
     is the only redirection point -- there is no module global to rebind.
  2. `Process_Monitor` spawns a NON-DAEMON heartbeat thread and, 30s after a
     missed heartbeat, writes a critical_error and shells out to
     `supervisorctl restart control`. `is_real_hw` reads
     settings['platform']['real_hw'] ONCE, in `__init__`, so what protects a
     captured run is whatever that blob held at construction -- the suite-wide
     False (tests/conftest.py), unless the run under capture has seeded
     settings of its own. base_settings() does not protect you. See the note
     below `make_ctx` for why nothing patches it any more.
"""

import logging
from dataclasses import dataclass, field

import controller.runtime.controller as controller_mod
import controller.runtime.runner
from common.control_delta import control_delta
from controller.runtime.clock import ManualClock
from controller.runtime.context import ControllerContext, Devices
from controller.runtime.store import InMemoryStore
from tests.fakes.distance import FakeDistance
from tests.fakes.grill import FakeGrillPlatform
from tests.fakes.notifier import FakeNotifier

#: Pitfall 1. The logger every captured run is redirected to. Handed to
#: `ControllerContext` by `make_ctx` below; add a handler to it to read back
#: what a work cycle logged.
CAPTURE_LOGGER_NAME = "characterization"


# Pitfall 2: Process_Monitor. There USED to be an autouse
# `_neutralize_process_monitor` fixture in tests/conftest.py that no-oped
# _heartbeat_check. It was deliberately deleted once stop_monitor() was fixed to
# actually terminate the thread (`fix(process-mon): stop_monitor terminates the
# heartbeat thread`) -- do not reinstate it; that would re-hide the real bug it
# replaced.
#
# What that leaves: base.run() ends with monitor.stop_monitor(), which is NOT in
# a `finally`. So any test that deliberately drives an exception out of
# base.run() leaks a live non-daemon monitor thread -- pytest then hangs at exit
# and, 30 seconds in, that thread runs `supervisorctl restart control` against
# the real machine. A test doing that must neutralize Process_Monitor itself.
# Tests whose work cycle completes normally (i.e. all of them) need nothing. ---


@dataclass
class CaptureResult:
    grill_calls: list = field(default_factory=list)
    display_commands: list = field(default_factory=list)
    notifications: list = field(default_factory=list)
    final_control: dict = field(default_factory=dict)
    final_status: dict = field(default_factory=dict)
    final_metrics: dict = field(default_factory=dict)


class _CappedProbes:
    """Wraps a probe fake; after `cap` reads, enqueue `updated=True` so the
    work-cycle loop breaks cleanly on its next
    top-of-iteration read_control(). This is the belt-and-suspenders bound for
    modes with no natural timer/temp exit (Smoke steady-state, Hold, Monitor,
    Manual)."""

    def __init__(self, probes, store, cap):
        self._probes = probes
        self._store = store
        self._cap = cap
        self._n = 0

    def read_probes(self, *, excitation=None, now=None):
        self._n += 1
        if self._n >= self._cap:
            self._store.enqueue_control_delta(control_delta(set_values={"updated": True}), origin="test-cap")
        return self._probes.read_probes(excitation=excitation, now=now)

    def __getattr__(self, name):
        return getattr(self._probes, name)


def make_ctx(settings, control_data, pellet_db, probes, grill=None, runner=None, store=None):
    # `runner` is accepted for signature symmetry with `run_mode` (which does
    # the actual `control.build_runner` monkeypatching around `_work_cycle`);
    # `make_ctx` itself never constructs a runner, so this is unused here.
    #
    # `store`: when None (the default, used by every InMemoryStore golden
    # scenario) a fresh InMemoryStore is built and seeded from the args. When
    # provided (the E2E suite passes a `SqliteStore`), it is used as-is --
    # the caller is responsible for seeding it, since a real store can't be
    # seeded through a constructor.
    store = store if store is not None else InMemoryStore(control=control_data, settings=settings, pellet_db=pellet_db)
    grill = grill or FakeGrillPlatform(
        dc_fan=settings["platform"].get("dc_fan", False),
        standalone=settings["platform"].get("standalone", True),
        outputs=tuple(settings["platform"]["outputs"]),
    )
    notifier = FakeNotifier()
    capture_log = logging.getLogger(CAPTURE_LOGGER_NAME)
    ctx = ControllerContext(
        devices=Devices(grill_platform=grill, probe_complex=probes, dist_device=FakeDistance()),
        store=store,
        notifications=notifier,
        clock=ManualClock(),
        event_log=capture_log,
        control_log=capture_log,
    )
    return ctx, grill, notifier


def run_mode(mode, *, settings, control_data, pellet_db, probes, grill=None, probe_cap=None, runner=None, store=None):
    """Run one `control._work_cycle` invocation hermetically and capture its
    observable effects.

    `probe_cap`: if set, bounds modes with no natural exit -- see
    `_CappedProbes` above. Pick a value comfortably larger than the number of
    iterations needed to exercise the behavior under test (e.g. enough for a
    couple of auger on/off cycles) but bounded so the test can't hang.

    `runner`: if set, monkeypatches `controller.runtime.runner.build_runner` for
    the duration of the call so Hold mode uses this object (e.g. a scripted
    `FakeControllerRunner`) instead of constructing a real PID/MPC core. Lets
    Hold-mode scenarios pin the runner's `.latest()` output deterministically
    without depending on real controller math.

    NOTE: HoldMode (controller/runtime/modes/hold.py) calls
    `controller.runtime.runner.build_runner(...)` directly (a patchable
    module-level reference). The legacy inline Hold code in `control.py` that
    used to call `control.build_runner` has been deleted (all modes are
    migrated to `ControlMode` handlers), so only the runtime reference needs
    patching now.
    """
    ctx, grill, notifier = make_ctx(settings, control_data, pellet_db, probes, grill, store=store)

    if probe_cap is not None:
        probes = _CappedProbes(probes, ctx.store, probe_cap)
        ctx.devices.probe_complex = probes

    # Process_Monitor needs no patching here -- see the Pitfall 2 note above --
    # so we only need to (optionally) inject a fake runner.
    prev_runtime_build_runner = controller.runtime.runner.build_runner
    if runner is not None:
        fake_build_runner = lambda *a, **k: (runner, "Active")
        controller.runtime.runner.build_runner = fake_build_runner
    try:
        controller_mod.run_work_cycle(mode, ctx)
    finally:
        controller.runtime.runner.build_runner = prev_runtime_build_runner

    return CaptureResult(
        grill_calls=grill.calls,
        display_commands=ctx.store.display_commands().list(),
        notifications=notifier.sent,
        final_control=ctx.store.read_control(),
        final_status=ctx.store.read_status(),
        final_metrics=ctx.store.read_metrics(),
    )
