"""Golden-master characterization tests for the OUTER control loop
(controller.runtime.controller.Controller), extracted from control.py's old
__main__.

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
import time

import controller.runtime.controller as controller_mod
from controller.runtime.controller import Controller
from controller.runtime.context import ControllerContext, Devices
from controller.runtime.store import InMemoryStore
from controller.runtime.clock import ManualClock
from distance.intervals import HOPPER_LEVEL_REFRESH_INTERVAL
from common.common import ErrorKind
from common.defaults import default_metrics
from tests.characterization.fixtures import base_settings, base_control, base_pellet_db
from tests.fakes.grill import FakeGrillPlatform
from tests.fakes.probes import FakeProbes
from tests.fakes.distance import FakeDistance
from tests.fakes.notifier import FakeNotifier
import control

# The per-mode handlers reference control.eventLogger via `import control as
# _control`; bind it so any stray logging call is harmless. (The loop itself
# logs through ctx.event_log, set below.)
control.eventLogger = logging.getLogger("characterization")
control.controlLogger = logging.getLogger("characterization")


class _RecordingDistance(FakeDistance):
    """FakeDistance that records get_level / request_sample / update_distances."""

    def __init__(self, level=100):
        super().__init__(level)
        self.get_level_calls = 0
        self.update_distances_calls = []

    def get_level(self):
        self.get_level_calls += 1
        return self._level

    def update_distances(self, empty, full):
        self.update_distances_calls.append((empty, full))


class _HostileDistance(_RecordingDistance):
    """A distance sensor that punishes any attempt to wait on it.

    `get_level()` is instant, exactly as the real drivers' cached read is.
    Everything else a caller might reach for in the hope of a fresh reading --
    `request_sample()` here, and any future re-addition of a measure-and-wait
    call -- sleeps for 3 REAL SECONDS, the same budget the deleted
    `get_level(override=True)` used to burn.

    So a control loop that merely reads the cache is fast, and a control loop
    that waits on a measurement by ANY route is caught. The assertions are
    against the wall clock rather than the injected ManualClock on purpose: the
    hazard is that the loop's own thread stops running, which no amount of fake
    time would reveal.
    """

    block_seconds = 3.0

    def request_sample(self):
        time.sleep(self.block_seconds)
        return super().request_sample()

    def __getattr__(self, name):
        # Any measure-and-wait method someone reintroduces later -- under
        # whatever name -- resolves to something slow rather than an
        # AttributeError that a test might mistake for "the loop did not call
        # it".
        if name.startswith("_"):
            raise AttributeError(name)

        def _slow(*args, **kwargs):
            time.sleep(self.block_seconds)

        return _slow


def make_controller(settings, control_data, pellet_db, *, grill=None, dist=None, clock=None):
    store = InMemoryStore(control=control_data, settings=settings, pellet_db=pellet_db)
    grill = grill or FakeGrillPlatform(
        standalone=settings["platform"].get("standalone", True), outputs=tuple(settings["platform"]["outputs"])
    )
    dist = dist or _RecordingDistance()
    notifier = FakeNotifier()
    logger = logging.getLogger("characterization")
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
    c.work_cycle = lambda mode: calls.append(("work_cycle", mode))
    c.next_mode = lambda next_mode, setpoint=0: calls.append(("next_mode", next_mode, setpoint))
    c.recipe_mode = lambda start_step=0: calls.append(("recipe_mode", start_step))
    return calls


def _neutralize_externals(monkeypatch):
    """Stub the module-level notify/cookfile/shutdown helpers the loop calls."""
    sent = []
    monkeypatch.setattr(controller_mod, "check_notify", lambda *a, **k: sent.append(("check_notify", k)))
    monkeypatch.setattr(controller_mod, "send_notifications", lambda *a, **k: sent.append(("send_notifications", a, k)))
    monkeypatch.setattr(controller_mod, "create_cookfile", lambda *a, **k: sent.append(("create_cookfile",)))
    monkeypatch.setattr(controller_mod, "os", _FakeOs(sent))
    return sent


class _FakeOs:
    def __init__(self, sink):
        self._sink = sink

    def system(self, cmd):
        self._sink.append(("os.system", cmd))


# --------------------------------------------------------------------------
# setup()
# --------------------------------------------------------------------------


def test_setup_runs_initial_hopper_check_and_binds_pelletdb(monkeypatch):
    # REGRESSION GUARD: the pre-loop hopper check (dropped during the mode
    # extraction) must run in setup(), binding pelletdb and reading the level.
    _neutralize_externals(monkeypatch)
    settings = base_settings()
    c, ctx, store, grill, dist, notifier = make_controller(settings, base_control(mode="Stop"), base_pellet_db())
    c.setup()
    assert c.pelletdb is not None
    assert dist.get_level_calls == 1  # boot-time hopper read happened
    assert c.pelletdb["current"]["hopper_level"] == 100
    assert store.read_pellet_db()["current"]["hopper_level"] == 100  # persisted


def test_setup_boot_to_monitor_requests_monitor_mode(monkeypatch):
    _neutralize_externals(monkeypatch)
    settings = base_settings()
    settings["globals"]["boot_to_monitor"] = True
    c, ctx, store, grill, dist, notifier = make_controller(settings, base_control(mode="Stop"), base_pellet_db())
    c.setup()
    control = store.read_control()
    assert control["mode"] == "Monitor"
    assert control["updated"] is True


def test_setup_no_boot_to_monitor_leaves_mode(monkeypatch):
    _neutralize_externals(monkeypatch)
    settings = base_settings()
    settings["globals"]["boot_to_monitor"] = False
    c, ctx, store, grill, dist, notifier = make_controller(settings, base_control(mode="Stop"), base_pellet_db())
    c.setup()
    assert store.read_control()["mode"] == "Stop"


# --------------------------------------------------------------------------
# tick(): mode dispatch (spied)
# --------------------------------------------------------------------------


def test_tick_smoke_dispatches_work_cycle_then_next_mode(monkeypatch):
    _neutralize_externals(monkeypatch)
    settings = base_settings()
    control_data = base_control(mode="Smoke")
    control_data["updated"] = True
    control_data["next_mode"] = "Stop"
    c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db())
    calls = _spy_dispatch(c)
    c.setup()
    c.tick()
    assert ("work_cycle", "Smoke") in calls
    assert ("next_mode", "Stop", 0) in calls
    assert calls.index(("work_cycle", "Smoke")) < calls.index(("next_mode", "Stop", 0))


def test_tick_hold_dispatches_work_cycle_then_next_mode(monkeypatch):
    _neutralize_externals(monkeypatch)
    settings = base_settings()
    control_data = base_control(mode="Hold")
    control_data["updated"] = True
    control_data["next_mode"] = "Stop"
    c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db())
    calls = _spy_dispatch(c)
    c.setup()
    c.tick()
    assert ("work_cycle", "Hold") in calls
    assert ("next_mode", "Stop", 0) in calls


def test_tick_monitor_sets_status_monitor_and_runs_cycle(monkeypatch):
    _neutralize_externals(monkeypatch)
    settings = base_settings()
    control_data = base_control(mode="Monitor")
    control_data["updated"] = True
    c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db())
    calls = _spy_dispatch(c)
    c.setup()
    c.tick()
    assert store.read_control()["status"] == "monitor"
    assert ("work_cycle", "Monitor") in calls


def test_tick_manual_runs_cycle_without_next_mode(monkeypatch):
    _neutralize_externals(monkeypatch)
    settings = base_settings()
    control_data = base_control(mode="Manual")
    control_data["updated"] = True
    c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db())
    calls = _spy_dispatch(c)
    c.setup()
    c.tick()
    assert ("work_cycle", "Manual") in calls
    assert not any(name == "next_mode" for name in (x[0] for x in calls))


def test_tick_recipe_dispatches_recipe_mode(monkeypatch):
    _neutralize_externals(monkeypatch)
    settings = base_settings()
    control_data = base_control(mode="Recipe")
    control_data["updated"] = True
    control_data["recipe"]["start_step"] = 2
    c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db())
    calls = _spy_dispatch(c)
    c.setup()
    c.tick()
    assert ("recipe_mode", 2) in calls


def test_tick_shutdown_sets_next_mode_stop_and_dispatches(monkeypatch):
    _neutralize_externals(monkeypatch)
    settings = base_settings()
    control_data = base_control(mode="Shutdown")
    control_data["updated"] = True
    c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db())
    calls = _spy_dispatch(c)
    c.setup()
    c.tick()
    assert ("work_cycle", "Shutdown") in calls
    assert ("next_mode", "Stop", 0) in calls


# --------------------------------------------------------------------------
# tick(): Stop / Error cleanup
# --------------------------------------------------------------------------


def test_tick_stop_mode_cleanup(monkeypatch):
    _neutralize_externals(monkeypatch)
    settings = base_settings()
    control_data = base_control(mode="Stop")
    control_data["updated"] = True
    c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db())
    _spy_dispatch(c)
    c.setup()
    c.tick()
    # Outputs driven off, status reset to Stop, control reset, display cleared.
    names = [name for name, _ in grill.calls]
    assert "auger_off" in names and "igniter_off" in names and "fan_off" in names
    assert "power_off" in names
    assert ("clear", None) in store.display_commands().list()
    assert store.read_status()["mode"] == "Stop"
    control = store.read_control()
    # Stop persists status='inactive' (bug fix): the assignment now runs AFTER the
    # `control = flush_control()` reset, mirroring the Error branch, instead
    # of before it where it was a dead write discarded by the reset (had persisted '').
    assert control["status"] == "inactive"
    assert control["updated"] is False
    assert control["next_mode"] == "Stop"


def test_tick_stop_mode_cookfile_failure_is_contained(monkeypatch, caplog):
    """DESIGN CALL: a failed create_cookfile() must not crash the control loop.
    On a real grill an uncaught exception here kills the whole `control.py`
    process and crash-loops the controller at every cook's end -- see the LIVE
    crash this commit also fixes (common/common.py process_metrics raising
    TypeError on a None starttime). tick()'s Stop/Error cleanup block wraps
    ONLY the `create_cookfile()` call in try/except, logs via
    `self.eventLogger.error`, and continues -- every OTHER Stop-cleanup step
    (outputs off, status/control reset, display clear) still runs
    unconditionally, same as `test_tick_stop_mode_cleanup` above.

    A failed cookfile write is potential cook-data loss and previously was
    only visible on the passive Logs page (self.eventLogger.error above). It
    now ALSO gets surfaced
    through the same active mechanism the dashboard error banners already
    read -- store.read_errors()/write_errors() under ErrorKind.CONTROL (the
    same list build_devices() appends to on hardware-load failure),
    which flows to the web UI via dash_data's "errors" key."""
    settings = base_settings()
    control_data = base_control(mode="Stop")
    control_data["updated"] = True
    c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db())
    # A non-empty metrics list with a non-Prime last mode is what makes tick()
    # reach the create_cookfile() call at all (controller.py's `if len(
    # metrics_list) != 0: ... if metrics_list[-1]["mode"] != Mode.PRIME:`).
    store.append_metric(dict(default_metrics(), mode="Smoke"))

    # Neutralize check_notify/send_notifications/os.system as usual, but let
    # create_cookfile raise instead of the no-op spy.
    sent = []
    monkeypatch.setattr(controller_mod, "check_notify", lambda *a, **k: sent.append(("check_notify", k)))
    monkeypatch.setattr(controller_mod, "send_notifications", lambda *a, **k: sent.append(("send_notifications", a, k)))

    def _boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(controller_mod, "create_cookfile", _boom)
    monkeypatch.setattr(controller_mod, "os", _FakeOs(sent))

    _spy_dispatch(c)
    c.setup()
    with caplog.at_level(logging.ERROR, logger="characterization"):
        c.tick()  # must NOT raise/propagate RuntimeError("disk full")

    assert any("disk full" in rec.message for rec in caplog.records)  # logged loudly

    # ALSO surfaced actively via the errors list the dashboard banners read
    # (store.read_errors(ErrorKind.CONTROL)), not just the passive Logs page.
    errors = store.read_errors(ErrorKind.CONTROL)
    assert any("Cook file could not be created" in e for e in errors)

    # Every other Stop-cleanup step still ran despite the cookfile failure.
    names = [name for name, _ in grill.calls]
    assert "auger_off" in names and "igniter_off" in names and "fan_off" in names
    assert "power_off" in names
    assert ("clear", None) in store.display_commands().list()
    assert store.read_status()["mode"] == "Stop"
    control = store.read_control()
    assert control["status"] == "inactive"
    assert control["updated"] is False
    assert control["next_mode"] == "Stop"


def test_tick_error_mode_cleanup(monkeypatch):
    _neutralize_externals(monkeypatch)
    settings = base_settings()
    control_data = base_control(mode="Error")
    control_data["updated"] = True
    clock = ManualClock()
    c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db(), clock=clock)
    _spy_dispatch(c)
    c.setup()
    c.tick()
    control = store.read_control()
    assert control["mode"] == "Error"
    assert control["status"] == "inactive"
    names = [name for name, _ in grill.calls]
    assert "power_off" in names
    assert ("clear", None) in store.display_commands().list()
    assert clock.now() >= 3  # the 3s error dwell went through ctx.clock.sleep


# --------------------------------------------------------------------------
# tick(): switch, timer, hopper, settings
# --------------------------------------------------------------------------


def test_tick_switch_off_triggers_stop(monkeypatch):
    _neutralize_externals(monkeypatch)
    settings = base_settings()
    settings["platform"]["standalone"] = False
    control_data = base_control(mode="Smoke")
    control_data["updated"] = False
    grill = FakeGrillPlatform(standalone=False, outputs=tuple(settings["platform"]["outputs"]))
    c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db(), grill=grill)
    _spy_dispatch(c)
    c.setup()  # binds last = input status (on)
    grill.set_input(False)  # user flips switch off
    c.tick()
    # switch-off writes Stop + updated, which then runs the Stop cleanup path
    control = store.read_control()
    assert control["next_mode"] == "Stop"  # Stop cleanup ran
    assert store.read_status()["mode"] == "Stop"


def test_tick_timer_expiry_sends_notification(monkeypatch):
    sent = _neutralize_externals(monkeypatch)
    settings = base_settings()
    control_data = base_control(mode="Stop")
    control_data["updated"] = False
    control_data["timer"] = {"start": 1, "paused": 0, "end": 5}
    control_data["notify_data"] = [{"type": "timer", "req": True, "shutdown": True, "keep_warm": True}]
    clock = ManualClock(start=10)  # now (10) >= end (5)
    c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db(), clock=clock)
    _spy_dispatch(c)
    c.setup()
    c.tick()
    assert any(x[0] == "send_notifications" and x[1] == ("Timer_Expired",) for x in sent)
    control = store.read_control()
    assert control["notify_data"][0]["req"] is False
    assert control["timer"]["end"] == 0


def test_tick_hopper_check_requests_a_sample_and_clears(monkeypatch):
    """`hopper_check` is now a REQUEST, not a measurement.

    It used to force a fresh reading and block the loop up to 3s waiting for
    it. The loop now asks the sampling thread and carries on; the requested
    reading reaches the datastore via the timed refresh. The flag itself is
    unchanged and still serviced -- the attached display (_base_flex.py:1462)
    and the Flask pellet pages raise it."""
    _neutralize_externals(monkeypatch)
    settings = base_settings()
    control_data = base_control(mode="Stop")
    control_data["updated"] = False
    control_data["hopper_check"] = True
    dist = _RecordingDistance(level=42)
    c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db(), dist=dist)
    _spy_dispatch(c)
    c.setup()
    dist.sample_requests = 0
    c.tick()
    assert dist.sample_requests == 1
    assert store.read_control()["hopper_check"] is False


def test_tick_hopper_check_does_not_delay_the_next_refresh(monkeypatch):
    """The requested reading is published by the timed refresh, so servicing a
    request must NOT restamp the refresh timer -- doing so would delay the very
    sample that was just asked for by a whole interval."""
    _neutralize_externals(monkeypatch)
    settings = base_settings()
    control_data = base_control(mode="Stop")
    control_data["updated"] = False
    control_data["hopper_check"] = True
    clock = ManualClock(start=1000)
    dist = _RecordingDistance(level=42)
    c, ctx, store, grill, dist, notifier = make_controller(
        settings, control_data, base_pellet_db(), dist=dist, clock=clock
    )
    _spy_dispatch(c)
    c.setup()
    clock.advance(HOPPER_LEVEL_REFRESH_INTERVAL + 1)
    c.tick()  # services the request AND the due refresh, in the same tick
    assert dist.sample_requests == 1
    assert store.read_pellet_db()["current"]["hopper_level"] == 42


# --------------------------------------------------------------------------
# Automatic hopper refresh
#
# The dashboard's "Refresh Status" button is gone -- the repo owner's call:
# poll automatically at a reasonable speed instead. That makes the loop below
# the only thing that keeps the hopper reading current outside an active cook,
# and makes HOW it reads the sensor a control-loop safety property rather than
# a UI detail. See distance/intervals.py.
# --------------------------------------------------------------------------


def test_tick_refreshes_hopper_level_on_a_timer(monkeypatch):
    _neutralize_externals(monkeypatch)
    settings = base_settings()
    control_data = base_control(mode="Stop")
    control_data["updated"] = False
    control_data["hopper_check"] = False
    clock = ManualClock(start=1000)
    dist = _RecordingDistance(level=77)
    c, ctx, store, grill, dist, notifier = make_controller(
        settings, control_data, base_pellet_db(), dist=dist, clock=clock
    )
    _spy_dispatch(c)
    c.setup()
    dist.get_level_calls = 0  # ignore the setup() boot-time read

    # Well inside the interval: nothing happens. The timer is not a free-running
    # "read every tick" -- ticks run ten times a second.
    clock.advance(HOPPER_LEVEL_REFRESH_INTERVAL - 1)
    c.tick()
    assert dist.get_level_calls == 0

    # Past it: the level is re-read and persisted, with no hopper_check flag in
    # sight. Against the pre-change loop this is 0 calls -- it only ever read
    # the sensor when a user pressed a button.
    clock.advance(2)
    c.tick()
    assert dist.get_level_calls == 1
    assert store.read_pellet_db()["current"]["hopper_level"] == 77
    assert store.read_control()["hopper_check"] is False


def test_the_control_loop_never_waits_on_a_hopper_reading(monkeypatch):
    """THE RISK THIS WHOLE CHANGE CARRIES, AND THE OWNER'S BINDING REQUIREMENT:
    the hopper refresh must never make the control loop wait. Not 3 seconds,
    not 300ms, not "briefly on the first read".

    _HostileDistance serves the cache instantly and makes EVERY other call --
    request_sample, and any measure-and-wait method reintroduced later under
    any name -- sleep 3 real seconds. So this catches a blocking read no matter
    which route it comes back by, not just `override=True` specifically.

    Covers boot (setup) and steady state (five due refreshes) in one run,
    against the WALL clock: the hazard is the loop's own thread stopping, which
    fake time cannot reveal."""
    _neutralize_externals(monkeypatch)
    settings = base_settings()
    control_data = base_control(mode="Stop")
    control_data["updated"] = False
    control_data["hopper_check"] = False
    clock = ManualClock(start=1000)
    dist = _HostileDistance()
    c, ctx, store, grill, dist, notifier = make_controller(
        settings, control_data, base_pellet_db(), dist=dist, clock=clock
    )
    _spy_dispatch(c)

    started = time.monotonic()
    c.setup()  # startup must not wait for a first sample either
    for _ in range(5):
        clock.advance(HOPPER_LEVEL_REFRESH_INTERVAL + 1)
        c.tick()
    elapsed = time.monotonic() - started

    # Both halves matter. Without the first, a loop that simply never refreshes
    # (which is what the code did before this change) would pass the timing
    # assertion trivially.
    assert dist.get_level_calls == 6, "the loop did not publish the hopper level"
    assert elapsed < 1.0, f"the control loop waited {elapsed:.1f}s on a hopper reading"


def test_servicing_a_hopper_check_does_not_wait_either(monkeypatch):
    """The on-demand path is held to the same rule. It used to be the ONE place
    a 3s block was considered acceptable ("someone asked and is waiting"); the
    owner's requirement admits no such exception, so it asks and returns."""
    _neutralize_externals(monkeypatch)
    settings = base_settings()
    control_data = base_control(mode="Stop")
    control_data["updated"] = False
    control_data["hopper_check"] = True
    clock = ManualClock(start=1000)
    # NOT hostile on request_sample: the loop is SUPPOSED to call it. Hostile on
    # everything else, so a wait bolted onto the request is still caught.
    dist = _HostileDistance()
    dist.request_sample = lambda: None
    c, ctx, store, grill, dist, notifier = make_controller(
        settings, control_data, base_pellet_db(), dist=dist, clock=clock
    )
    _spy_dispatch(c)
    c.setup()

    started = time.monotonic()
    c.tick()
    elapsed = time.monotonic() - started

    assert store.read_control()["hopper_check"] is False, "the request was not serviced"
    assert elapsed < 1.0, f"servicing hopper_check waited {elapsed:.1f}s"


def test_tick_distance_update_updates_distances_and_clears(monkeypatch):
    _neutralize_externals(monkeypatch)
    settings = base_settings()
    control_data = base_control(mode="Stop")
    control_data["updated"] = False
    control_data["distance_update"] = True
    dist = _RecordingDistance()
    c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db(), dist=dist)
    _spy_dispatch(c)
    c.setup()
    c.tick()
    assert len(dist.update_distances_calls) == 1
    assert store.read_control()["distance_update"] is False


def test_tick_settings_update_clears_flag(monkeypatch):
    _neutralize_externals(monkeypatch)
    settings = base_settings()
    control_data = base_control(mode="Stop")
    control_data["updated"] = False
    control_data["settings_update"] = True
    c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db())
    _spy_dispatch(c)
    c.setup()
    c.tick()
    assert store.read_control()["settings_update"] is False


def test_tick_probe_profile_update_clears_flag(monkeypatch):
    _neutralize_externals(monkeypatch)
    settings = base_settings()
    control_data = base_control(mode="Stop")
    control_data["updated"] = False
    control_data["probe_profile_update"] = True
    c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db())
    _spy_dispatch(c)
    c.setup()
    c.tick()
    assert store.read_control()["probe_profile_update"] is False


def test_tick_probe_map_update_rebuilds_devices_and_clears_flag(monkeypatch):
    """POST /api/probe_map sets this flag. probe_profile_update is NOT enough:
    it only refills per-port profiles on already-constructed devices
    (probes/base.py:393-401) and cannot see an added/removed/renamed probe."""
    _neutralize_externals(monkeypatch)
    settings = base_settings()
    control_data = base_control(mode="Stop")
    control_data["updated"] = False
    control_data["probe_map_update"] = True
    c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db())
    _spy_dispatch(c)
    c.setup()
    c.tick()
    assert ctx.devices.probe_complex.update_probe_map_calls == [settings["probe_settings"]["probe_map"]]
    assert store.read_control()["probe_map_update"] is False


def test_tick_tolerates_a_control_blob_without_the_new_flag(monkeypatch):
    """An install upgraded in place has a control blob written before this key
    existed. probe_profile_update indexes control[...] directly and would
    KeyError on such a blob; the new handler must use .get()."""
    _neutralize_externals(monkeypatch)
    control_data = base_control(mode="Stop")
    control_data["updated"] = False
    control_data.pop("probe_map_update", None)
    c, ctx, store, grill, dist, notifier = make_controller(base_settings(), control_data, base_pellet_db())
    _spy_dispatch(c)
    c.setup()
    c.tick()  # must not raise
    assert ctx.devices.probe_complex.update_probe_map_calls == []


# ---------------------------------------------------------------------------
# Which sessions earn a cook file.
#
# The Stop branch used to ask "was the LAST metric's mode Prime?" and archive
# whenever the answer was no -- a test for one specific non-cook rather than a
# test for a cook. A Monitor session -- temperatures watched, nothing ever lit
# -- answered "no" and wrote a full .pifire on its way out: toggling Monitor on
# and off twice left two of them in ./history/, the second suffixed "-1"
# because the first already existed.
#
# The trap in fixing it is that create_cookfile() ends with flush_history()
# (which clears metrics and current as well), so simply not calling it also
# stops the session being cleared -- and a Monitor session would then bleed its
# temperatures into the chart of the next real cook. Each test below therefore
# asserts BOTH halves: whether a file was written, and whether the session was
# flushed.
# ---------------------------------------------------------------------------


def _stop_after(monkeypatch, modes):
    """Run one Stop tick for a session that recorded `modes`, in order."""
    sent = _neutralize_externals(monkeypatch)
    control_data = base_control(mode="Stop")
    control_data["updated"] = True
    c, ctx, store, grill, dist, notifier = make_controller(base_settings(), control_data, base_pellet_db())
    for mode in modes:
        store.append_metric(dict(default_metrics(), mode=mode))
    store.write_history({"probe": 225})
    _spy_dispatch(c)
    c.setup()
    c.tick()
    archived = ("create_cookfile",) in sent
    return archived, store


def test_a_monitor_only_session_is_not_archived_but_is_still_flushed(monkeypatch):
    archived, store = _stop_after(monkeypatch, ["Monitor"])
    assert not archived, "watching temperatures is not a cook and must not write a .pifire"
    assert store.read_history() == [], (
        "the session was not flushed -- create_cookfile() is what normally flushes, so "
        "skipping it must not also skip the flush, or Monitor's temperatures ride into "
        "the next real cook's chart"
    )
    assert store.read_all_metrics() == []


def test_a_real_cook_is_archived(monkeypatch):
    archived, _store = _stop_after(monkeypatch, ["Startup", "Smoke", "Hold"])
    assert archived


def test_a_cook_that_ends_in_monitor_is_still_archived(monkeypatch):
    # Regression guard, not a bug proof: the old last-mode test archived this
    # one correctly too (Monitor != Prime). It is here because the new test
    # looks at the whole session, and "any cook mode present" must not become
    # "the last mode must be a cook mode" in some later edit.
    archived, _store = _stop_after(monkeypatch, ["Startup", "Smoke", "Hold", "Monitor"])
    assert archived


def test_a_monitor_session_before_a_cook_does_not_suppress_the_archive(monkeypatch):
    archived, _store = _stop_after(monkeypatch, ["Monitor", "Startup", "Smoke"])
    assert archived


def test_a_prime_only_session_keeps_its_carry_over(monkeypatch):
    # "Prime, then start up" is deliberately ONE session: Prime is neither
    # archived nor flushed, so its history joins the cook that follows.
    archived, store = _stop_after(monkeypatch, ["Prime"])
    assert not archived
    assert store.read_history() != [], "Prime's carry-over into the following cook was dropped"


def test_manual_counts_as_a_cook(monkeypatch):
    # Manual drives the relays directly -- it can absolutely be a cook.
    archived, _store = _stop_after(monkeypatch, ["Manual"])
    assert archived
