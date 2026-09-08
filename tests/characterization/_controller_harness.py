"""Shared harness for constructing and driving a real Controller in tests.

Extracted from test_controller_loop_golden.py so other test modules can reuse
`make_controller` / `_spy_dispatch` / `_neutralize_externals` without importing
a test module directly (importing a test module runs its module-level code and
collection side effects, and couples the two files together).
"""

import logging

import controller.runtime.controller as controller_mod
from common.clock_domain import RuntimeClockDomain
from controller.runtime.clock import Clock, ManualClock
from controller.runtime.context import ControllerContext, Devices
from controller.runtime.controller import Controller
from controller.runtime.store import InMemoryStore
from tests.fakes.distance import FakeDistance
from tests.fakes.grill import FakeGrillPlatform
from tests.fakes.notifier import FakeNotifier
from tests.fakes.probes import FakeProbes


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


def make_controller(settings, control_data, pellet_db, *, grill=None, dist=None, clock: Clock | None = None):
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
        clock=ManualClock() if clock is None else clock,
        event_log=logger,
        control_log=logger,
    )
    ctx.clock_domain = RuntimeClockDomain(
        monotonic=lambda: ctx.clock.monotonic(),
        wall_time=lambda: ctx.clock.wall_time(),
        boot_id="00000000-0000-0000-0000-000000000001",
        boottime=lambda: ctx.clock.monotonic(),
    )
    ctx.last_clock_stamp = ctx.clock_domain.capture()
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
    monkeypatch.setattr(
        controller_mod,
        "create_cookfile",
        lambda *, cook_id, learning_report_provider: sent.append(
            ("create_cookfile", cook_id, learning_report_provider)
        ),
    )
    monkeypatch.setattr(controller_mod, "shutdown_system", lambda: sent.append(("shutdown_system",)))
    return sent
