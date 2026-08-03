import collections
import threading
import time

from controller.applied_output import AppliedOutput, OutputSource
from controller.runtime.runner import (
    ThreadedControllerRunner,
    build_runner,
    SyncControllerRunner,
    _MAX_PENDING_OUTPUTS,
)


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
        self.tag = "core-a"

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
        return {"cycle_ratio": self._ratio, "fan": None}

    def set_output(self, applied):
        pass

    def get_status(self):
        return None

    def get_model_snapshot(self):
        return None

    def restore_model(self, snapshot):
        return True


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
        assert snap["tag"] == "core-a"  # well-formed before first solve
        assert snap is not core.__dict__  # a copy, not the live dict
    finally:
        r.stop()


class _StatusBeforeSolveCore(FakeCore):
    """get_status() answers immediately, before any update() -- exercises the
    runner's seeding of the *initial* snapshot in __init__, not just the
    post-solve reseed in _loop."""

    def __init__(self):
        super().__init__()
        self.leaky = object()  # would appear in the snapshot if __init__ used __dict__

    def get_status(self):
        return {"safe": 1.0}


def test_threaded_runner_seeds_initial_snapshot_from_get_status():
    core = _StatusBeforeSolveCore()
    r = ThreadedControllerRunner(core)
    try:
        snap = r.controller_state()
        assert snap["safe"] == 1.0
        assert "leaky" not in snap  # get_status() seeded it, not core.__dict__
    finally:
        r.stop()


class _RaisingStatusCore(FakeCore):
    """get_status() raises -- this runs before the core has proven itself with
    a successful update(), outside _build_core's guard, so __init__ must not
    let it propagate and kill the control process the way an unguarded
    constructor once did."""

    def get_status(self):
        raise RuntimeError("boom")


def test_threaded_runner_survives_get_status_raising_during_init():
    core = _RaisingStatusCore()
    r = ThreadedControllerRunner(core)
    try:
        snap = r.controller_state()
        assert snap["tag"] == "core-a"  # fell back to __dict__ instead of crashing __init__
    finally:
        r.stop()


def test_controller_state_pending_dropped_survives_hold_s_cycle_ratio_mutation():
    # HoldMode._on_auger_on reads controller_state() and adds "cycle_ratio"
    # before publishing to MQTT. pending_dropped is a field only the threaded
    # runner adds to that payload; pin that it survives the mutation
    # unchanged and defaults to 0 when nothing has been dropped -- an
    # additive dict key is a shape a JSON-payload consumer can ignore, not
    # one that breaks it.
    core = FakeCore()
    r = ThreadedControllerRunner(core)
    try:
        r.submit(70.0)
        assert core.updated.wait(2.0)
        state = r.controller_state()
        assert state["pending_dropped"] == 0
        state["cycle_ratio"] = 0.42
        assert state["pending_dropped"] == 0
        assert state["cycle_ratio"] == 0.42
    finally:
        r.stop()


def test_build_runner_selects_threaded_for_wants_async_core(monkeypatch):
    import controller.runtime.runner as runner_mod

    core = FakeCore()  # wants_async() -> True

    monkeypatch.setattr(runner_mod, "_build_core", lambda *a, **k: (core, "Active"))
    r, status = build_runner({}, {})
    try:
        assert isinstance(r, ThreadedControllerRunner)
        assert status == "Active"
    finally:
        r.stop()


def test_build_runner_selects_sync_for_non_async_core(monkeypatch):
    import controller.runtime.runner as runner_mod

    class SyncCore(FakeCore):
        def wants_async(self):
            return False

    monkeypatch.setattr(runner_mod, "_build_core", lambda *a, **k: (SyncCore(), "Active"))
    r, status = build_runner({}, {})
    assert isinstance(r, SyncControllerRunner)
    r.stop()  # no-op


def test_hold_teardown_stops_threaded_runner():
    from controller.runtime.modes.hold import HoldMode

    core = FakeCore()
    runner = ThreadedControllerRunner(core)
    thread = runner._thread
    hold = HoldMode.__new__(HoldMode)
    hold._runner = runner
    # Enough of a mode for teardown to read its identification setting; this
    # test is about the worker thread being joined, not about learning.
    hold.settings = {"controller": {}}
    hold.teardown(70.0)
    assert not thread.is_alive()


class _OrderRecordingCore:
    """Records the interleaving of set_output and update calls."""

    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()
        self.snapshot = {"revision": 1}
        self.restored = []

    def update(self, temp):
        with self.lock:
            self.calls.append(("update", temp))
        return 0.5

    def set_output(self, applied):
        with self.lock:
            self.calls.append(("set_output", applied.timestamp))

    def set_target(self, sp):
        pass

    def get_control_period(self):
        return 0.01

    def commands_fan(self):
        return False

    def wants_async(self):
        return True

    def get_status(self):
        return None

    def get_model_snapshot(self):
        # Cached, not rebuilt per call: a runner that handed this object out
        # as-is would let a caller's mutation reach back into the core.
        return self.snapshot

    def restore_model(self, snapshot):
        with self.lock:
            self.restored.append(snapshot)
        return True


def _wait_for(predicate, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def test_applied_outputs_replay_in_timestamp_order_before_update():
    core = _OrderRecordingCore()
    runner = ThreadedControllerRunner(core)
    try:
        # queue out of order; the worker must sort them
        runner.set_output(AppliedOutput(0.2, OutputSource.CONTROLLER, 20.0))
        runner.set_output(AppliedOutput(0.1, OutputSource.CONTROLLER, 10.0))
        runner.submit(212.0)
        assert _wait_for(lambda: ("update", 212.0) in core.calls)
        with core.lock:
            calls = list(core.calls)
        first_update = next(i for i, c in enumerate(calls) if c[0] == "update")
        reports = [c for c in calls[:first_update] if c[0] == "set_output"]
        assert reports == [("set_output", 10.0), ("set_output", 20.0)]
    finally:
        runner.stop()


def test_restore_model_is_applied_on_the_worker_thread():
    core = _OrderRecordingCore()
    runner = ThreadedControllerRunner(core)
    try:
        assert runner.restore_model({"revision": 7}) is True
        runner.submit(212.0)
        assert _wait_for(lambda: core.restored == [{"revision": 7}])
    finally:
        runner.stop()


def test_restore_model_copies_the_snapshot_on_the_way_in():
    core = _OrderRecordingCore()
    runner = ThreadedControllerRunner(core)
    try:
        snapshot = {"revision": 7}
        assert runner.restore_model(snapshot) is True
        snapshot["revision"] = 999  # mutate the caller's dict after queuing it
        runner.submit(212.0)
        assert _wait_for(lambda: core.restored == [{"revision": 7}])
    finally:
        runner.stop()


def test_restore_model_rejects_none_without_touching_the_core():
    core = _OrderRecordingCore()
    runner = ThreadedControllerRunner(core)
    try:
        assert runner.restore_model(None) is False
        runner.submit(212.0)
        assert _wait_for(lambda: ("update", 212.0) in core.calls)
        assert core.restored == []
    finally:
        runner.stop()


def test_get_model_snapshot_reads_the_worker_s_snapshot():
    core = _OrderRecordingCore()
    runner = ThreadedControllerRunner(core)
    try:
        core.snapshot = {"revision": 4}
        runner.submit(212.0)
        assert _wait_for(lambda: runner.get_model_snapshot() == {"revision": 4})
    finally:
        runner.stop()


def test_get_model_snapshot_returns_a_copy_not_the_core_s_object():
    # _OrderRecordingCore.get_model_snapshot() hands back the same cached
    # dict every call, so a runner that passed it through as-is would fail
    # both assertions below; a fresh-dict-per-call core could not.
    core = _OrderRecordingCore()
    runner = ThreadedControllerRunner(core)
    try:
        runner.submit(212.0)
        assert _wait_for(lambda: ("update", 212.0) in core.calls)
        snap = runner.get_model_snapshot()
        assert snap is not core.snapshot
        snap["revision"] = 999
        assert core.snapshot == {"revision": 1}
    finally:
        runner.stop()


class _BlockedWorkerCore(_OrderRecordingCore):
    """A core whose update() blocks on `gate` until the test lets it through,
    then re-arms so a subsequent update() blocks again. This holds the
    worker's loop at "inside one iteration's update()" for as long as the
    test needs, so appends made meanwhile are provably undrained."""

    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.gate = threading.Event()
        self._released = False

    def update(self, temp):
        self.entered.set()
        if not self._released:
            self.gate.wait()
            self.gate.clear()
        return super().update(temp)

    def release(self):
        """Stop blocking for good, for teardown. Setting `gate` alone can
        race: the currently-blocked call clears it on its way out, so the
        *next* call would re-block and leave stop()'s join() to time out on a
        thread that is never coming back."""
        self._released = True
        self.gate.set()


def _threaded_runner_with_blocked_worker():
    core = _BlockedWorkerCore()
    runner = ThreadedControllerRunner(core)
    runner.submit(212.0)
    assert core.entered.wait(2.0)  # worker is now stuck inside update()
    return runner, core


def _output(ratio, timestamp):
    return AppliedOutput(ratio=ratio, source=OutputSource.CONTROLLER, timestamp=timestamp)


def _drain_once(runner):
    """Let the worker's current blocked update() return, forcing its next loop
    iteration -- which drains _pending_outputs before calling update() again --
    to run, then leave the worker blocked again inside that next update()."""
    core = runner._core
    core.entered.clear()
    core.gate.set()
    assert core.entered.wait(2.0)  # the next update() call has begun and re-armed


def test_a_stalled_worker_bounds_the_backlog_and_counts_the_drops():
    runner, core = _threaded_runner_with_blocked_worker()
    try:
        for i in range(_MAX_PENDING_OUTPUTS + 50):
            runner.set_output(_output(ratio=0.3, timestamp=float(i)))

        assert len(runner._pending_outputs) == _MAX_PENDING_OUTPUTS
        assert runner._pending_dropped == 50
        assert runner.controller_state()["pending_dropped"] == 50

        # the survivors are the newest, and the oldest are what went
        oldest = min(a.timestamp for a in runner._pending_outputs)
        assert oldest == 50.0
    finally:
        core.release()
        runner.stop()


def test_the_backlog_stays_bounded_after_a_drain():
    runner, core = _threaded_runner_with_blocked_worker()
    try:
        for i in range(10):
            runner.set_output(_output(ratio=0.3, timestamp=float(i)))
        _drain_once(runner)

        for i in range(_MAX_PENDING_OUTPUTS + 25):
            runner.set_output(_output(ratio=0.3, timestamp=float(100 + i)))
        assert len(runner._pending_outputs) == _MAX_PENDING_OUTPUTS
        assert isinstance(runner._pending_outputs, collections.deque)
    finally:
        core.release()
        runner.stop()
