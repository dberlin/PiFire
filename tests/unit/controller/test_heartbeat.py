"""The control process's liveness stamp.

The web process decides "is control alive?" by READING this stamp and comparing
it against its own clock, rather than by pushing a `check_alive` command and
waiting for an answer. That shape matters: a stopped control process cannot
answer a request, so the request/response probe always cost its caller a full
timeout in exactly the case it existed to detect -- which forced it onto a 30s
throttle, which is what made a RECOVERED control process keep reading as down
for up to half a minute.

Two properties are load-bearing and pinned here:

* The stamp is refreshed from BOTH the idle tick and the per-mode work cycle. A
  cook never returns to the idle tick, so stamping in only one of the two would
  read as "control is down" for the whole cook.
* It is throttled. Both call sites run far hotter (0.1s and 0.05s) than anything
  reads it, so an unthrottled stamp would be 10-20 datastore writes a second.
"""

import controller.runtime.heartbeat as heartbeat_mod
from common.persistence.runtime import CONTROL_HEARTBEAT_KEY, CONTROL_HEARTBEAT_STALE_AFTER
from controller.runtime.clock import ManualClock
from controller.runtime.heartbeat import HEARTBEAT_WRITE_INTERVAL, stamp_control_heartbeat
from controller.runtime.store import InMemoryStore


class _Ctx:
    def __init__(self, clock, store):
        self.clock = clock
        self.store = store


def _ctx(start=1000.0):
    heartbeat_mod.reset_for_tests()
    return _Ctx(ManualClock(start), InMemoryStore())


def _stamp(ctx):
    return ctx.store._generic.get(CONTROL_HEARTBEAT_KEY)


def test_first_stamp_writes_the_clock_reading():
    ctx = _ctx(1000.0)
    stamp_control_heartbeat(ctx)
    assert _stamp(ctx) == 1000.0


def test_a_second_stamp_inside_the_interval_does_not_rewrite():
    ctx = _ctx(1000.0)
    stamp_control_heartbeat(ctx)
    ctx.clock.advance(HEARTBEAT_WRITE_INTERVAL / 2)
    stamp_control_heartbeat(ctx)
    assert _stamp(ctx) == 1000.0  # still the first reading


def test_a_stamp_past_the_interval_refreshes():
    ctx = _ctx(1000.0)
    stamp_control_heartbeat(ctx)
    ctx.clock.advance(HEARTBEAT_WRITE_INTERVAL + 0.01)
    stamp_control_heartbeat(ctx)
    assert _stamp(ctx) == 1000.0 + HEARTBEAT_WRITE_INTERVAL + 0.01


def test_the_hot_loops_cost_one_write_per_interval_not_one_per_tick():
    """20 ticks at the mode loop's 0.05s cadence spans one write interval."""
    ctx = _ctx(1000.0)
    writes = []
    real_write = ctx.store.write_generic_key
    ctx.store.write_generic_key = lambda k, v: (writes.append(k), real_write(k, v))[1]

    for _ in range(20):
        stamp_control_heartbeat(ctx)
        ctx.clock.advance(0.05)

    assert writes.count(CONTROL_HEARTBEAT_KEY) == 1


def test_the_write_interval_leaves_room_under_the_readers_staleness_window():
    # A single missed write must never trip the reader; the window has to hold
    # several intervals, or a mode transition that blocks the loop briefly
    # would flash a control-down banner.
    assert CONTROL_HEARTBEAT_STALE_AFTER > HEARTBEAT_WRITE_INTERVAL * 3


# ---------------------------------------------------------------------------
# Both call sites. These are the tests that would have caught "stamped in the
# idle tick only", which reads as control-down for the entire duration of a
# cook -- the loop is inside the per-mode work cycle the whole time.
# ---------------------------------------------------------------------------


def test_the_idle_tick_stamps(monkeypatch):
    from tests.characterization._controller_harness import _neutralize_externals, make_controller
    from tests.characterization.fixtures import base_control, base_pellet_db, base_settings

    heartbeat_mod.reset_for_tests()
    _neutralize_externals(monkeypatch)
    c, ctx, store, _grill, _dist, _notifier = make_controller(
        base_settings(), base_control(mode="Stop"), base_pellet_db()
    )
    c.setup()
    assert CONTROL_HEARTBEAT_KEY not in store._generic

    c.tick()

    assert store._generic[CONTROL_HEARTBEAT_KEY] == ctx.clock.now()


def test_the_per_mode_work_cycle_stamps():
    from tests.characterization.fixtures import base_control, base_pellet_db, base_settings
    from tests.characterization.harness import run_mode
    from tests.fakes.probes import FakeProbes

    heartbeat_mod.reset_for_tests()
    settings = base_settings()
    store = InMemoryStore(control=base_control(mode="Smoke"), settings=settings, pellet_db=base_pellet_db())
    run_mode(
        "Smoke",
        settings=settings,
        control_data=base_control(mode="Smoke"),
        pellet_db=base_pellet_db(),
        probes=FakeProbes().script([225] * 8),
        probe_cap=4,
        store=store,
    )

    assert CONTROL_HEARTBEAT_KEY in store._generic
