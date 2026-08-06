import collections
import threading
import time
import json
from dataclasses import replace


from controller.applied_output import AppliedOutput, OutputSource
from controller.linear_mpc.contracts import FrameObservation
from common.control_trace import ActuationMode, ModelObservationPayload, ResultStaleState, TraceEventKind
from controller.runtime.runner import (
    ThreadedControllerRunner,
    build_runner,
    SyncControllerRunner,
    _MAX_PENDING_OUTPUTS,
    _MAX_PENDING_OBSERVATIONS,
)
from controller.mpc import Controller as MpcController, _DEFAULTS as MPC_DEFAULTS


def _frame(index: int) -> FrameObservation:
    return FrameObservation(
        frame_start_s=index * 20.0,
        frame_end_s=(index + 1) * 20.0,
        temp_c=100.0,
        setpoint_c=120.0,
        ambient_c=20.0,
        requested_q=0.25,
        realized_q=0.25,
        requested_auger_duty=0.25,
        delivered_on_s=5.0,
        requested_fan_duty=None,
        actual_fan_duty=None,
        result_revision=1,
        output_source="controller",
        lid_open=False,
        safety_inhibited=False,
        manual_override=False,
        stale=False,
        skipped=False,
        reset=False,
        continuous=True,
        role_generation=0,
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

    def actuation_mode(self):
        return ActuationMode.FRAMED_PULSE

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

    def trace_diagnostics(self):
        return None

    def trace_allocation(self):
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


class CloseAwarePeriodWait:
    """Blocks the runner worker until stop() closes the injected wait."""

    def __init__(self):
        self.waiting = threading.Event()
        self.release = threading.Event()
        self.closed = threading.Event()

    def __call__(self, _seconds):
        self.waiting.set()
        self.release.wait()

    def close(self):
        self.closed.set()
        self.release.set()


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


def test_threaded_result_retains_consumed_temperature_after_newer_submit():
    class FirstBlockedCore(FakeCore):
        def __init__(self):
            super().__init__(period=0.01)
            self.first_entered = threading.Event()
            self.first_release = threading.Event()
            self.second_release = threading.Event()

        def update(self, temp):
            self.updates.append(temp)
            if len(self.updates) == 1:
                self.first_entered.set()
                self.first_release.wait(2.0)
            else:
                self.second_release.wait(2.0)
            return {"cycle_ratio": self._ratio, "fan": None}

    core = FirstBlockedCore()
    runner = ThreadedControllerRunner(core)
    try:
        runner.submit(70.0)
        assert core.first_entered.wait(2.0)
        runner.submit(80.0)
        core.first_release.set()
        assert _wait_for(lambda: runner.latest().revision == 1)
        assert runner.latest().input_temperature == 70.0
    finally:
        core.first_release.set()
        core.second_release.set()
        runner.stop()


def test_threaded_runner_repoll_preserves_completed_revision_and_advances_age():
    core = FakeCore()
    runner = ThreadedControllerRunner(core)
    try:
        runner.submit(70.0)
        assert core.updated.wait(2.0)
        first = runner.latest()
        second = runner.latest()
        assert second.revision == first.revision >= 1
        assert second.result_age_seconds >= first.result_age_seconds
        assert first.solve_duration_seconds == first.solve_end_monotonic - first.solve_start_monotonic
    finally:
        runner.stop()


def test_threaded_result_recursively_freezes_nested_status_across_repolls():
    class _NestedStatusCore(FakeCore):
        def __init__(self):
            super().__init__()
            self.status = {"nested": {"samples": [1.0]}}

        def get_status(self):
            return self.status

    core = _NestedStatusCore()
    runner = ThreadedControllerRunner(core)
    try:
        runner.submit(70.0)
        assert core.updated.wait(2.0)
        result = runner.latest()
        core.status["nested"]["samples"].append(2.0)
        core.status["nested"]["extra"] = 3.0

        repolled = runner.latest()
        state = runner.controller_state()
        assert repolled.revision == result.revision
        assert repolled.result_age_seconds >= result.result_age_seconds
        assert state["nested"] == {"samples": [1.0]}
        assert {key: state[key] for key in ("pending_dropped", "pending_observations", "dropped_observations")} == {
            "pending_dropped": 0,
            "pending_observations": 0,
            "dropped_observations": 0,
        }
        assert state["result_age_seconds"] >= repolled.result_age_seconds
        assert json.loads(json.dumps(state)) == state
        state["nested"]["samples"].append(3.0)
        assert result.status == {"nested": {"samples": (1.0,)}}
        assert repolled.status == {"nested": {"samples": (1.0,)}}
        current = runner.controller_state()
        assert current["nested"] == {"samples": [1.0]}
        assert {key: current[key] for key in ("pending_dropped", "pending_observations", "dropped_observations")} == {
            "pending_dropped": 0,
            "pending_observations": 0,
            "dropped_observations": 0,
        }
    finally:
        runner.stop()


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


def test_threaded_runner_publishes_one_atomic_quality_snapshot_without_blocking():
    class Clock:
        def __init__(self):
            self.value = 0.0

        def __call__(self):
            return self.value

        def advance(self, seconds):
            self.value += seconds

    class PeriodBarrier:
        def __init__(self):
            self.release = threading.Event()
            self.first_waiting = threading.Event()
            self.first_publish_complete = threading.Event()
            self.recovery_publish_complete = threading.Event()
            self.calls = 0
            self.lock = threading.Lock()

        def __call__(self, seconds):
            with self.lock:
                self.calls += 1
                call = self.calls
            if call == 1:
                self.first_waiting.set()
            elif call == 2:
                self.first_publish_complete.set()
            elif call == 3:
                self.recovery_publish_complete.set()
            assert self.release.wait(2.0)
            self.release.clear()

    class BarrierCore(FakeCore):
        def __init__(self, clock):
            super().__init__(period=5.0)
            self.clock = clock
            self.entered = threading.Event()
            self.solve_duration = 6.0

        def actuation_mode(self):
            return ActuationMode.FRAMED_PULSE

        def update(self, temp):
            self.entered.set()
            self.clock.advance(self.solve_duration)
            return super().update(temp)

    clock = Clock()
    warnings = []
    barrier = PeriodBarrier()
    core = BarrierCore(clock)
    runner = ThreadedControllerRunner(
        core,
        monotonic_clock=clock,
        wall_clock=clock,
        warning_callback=warnings.append,
        wait_for_period=barrier,
    )
    try:
        assert barrier.first_waiting.wait(2.0)
        runner.submit(70.0)
        barrier.release.set()
        assert core.entered.wait(2.0)
        assert barrier.first_publish_complete.wait(2.0)
        completed = runner.latest()
        assert completed.revision == 1
        assert runner.actuation_mode() is ActuationMode.FRAMED_PULSE
        assert completed.solve_duration_seconds == 6.0
        assert completed.deadline_miss_count == completed.consecutive_deadline_miss_count == 1

        clock.advance(10.0)
        stale = runner.latest()
        assert stale.revision == completed.revision
        assert stale.result_age_seconds == 10.0
        assert stale.stale_state is ResultStaleState.STALE
        assert warnings == [ResultStaleState.STALE]
        assert runner.latest() is stale
        assert warnings == [ResultStaleState.STALE]
        clock.advance(1.0)
        aged = runner.latest()
        assert aged.revision == stale.revision
        assert aged.result_age_seconds == 11.0
        assert aged.stale_state is ResultStaleState.STALE
        assert warnings == [ResultStaleState.STALE]

        core.solve_duration = 0.0
        barrier.release.set()
        assert barrier.recovery_publish_complete.wait(2.0)
        recovered = runner.latest()
        assert recovered.revision == completed.revision + 1
        assert recovered.deadline_miss_count == 1
        assert recovered.consecutive_deadline_miss_count == 0
        assert recovered.stale_state is ResultStaleState.FRESH
        assert recovered.recovered is True
        assert warnings == [ResultStaleState.STALE, ResultStaleState.FRESH]
        assert runner.latest() is recovered
        assert recovered.recovered is True
        assert warnings == [ResultStaleState.STALE, ResultStaleState.FRESH]
    finally:
        barrier.release.set()
        runner.stop()


def test_threaded_reconfigure_atomically_refreshes_capabilities_and_stale_budget(monkeypatch):
    import controller.runtime.runner as runner_module

    class Clock:
        def __init__(self):
            self.value = 0.0

        def __call__(self):
            return self.value

        def advance(self, seconds):
            self.value += seconds

    class PeriodBarrier:
        def __init__(self):
            self.release = threading.Event()
            self.first_waiting = threading.Event()
            self.swapped_waiting = threading.Event()
            self.calls = 0

        def __call__(self, seconds):
            self.calls += 1
            if self.calls == 1:
                self.first_waiting.set()
            elif self.calls == 2:
                self.swapped_waiting.set()
            assert self.release.wait(2.0)
            self.release.clear()

    class Core(FakeCore):
        def __init__(self, *, period, commands_fan, mode):
            super().__init__(period=period, commands_fan=commands_fan)
            self._mode = mode

        def actuation_mode(self):
            return self._mode

    clock = Clock()
    barrier = PeriodBarrier()
    replacement = Core(period=2.0, commands_fan=True, mode=ActuationMode.FRAMED_PULSE)
    monkeypatch.setattr(runner_module, "_build_core", lambda *args, **kwargs: (replacement, "Active"))
    runner = ThreadedControllerRunner(
        Core(period=5.0, commands_fan=False, mode=ActuationMode.FRAMED_PULSE),
        monotonic_clock=clock,
        wall_clock=clock,
        wait_for_period=barrier,
    )
    try:
        assert runner.actuation_mode() is ActuationMode.FRAMED_PULSE
        assert barrier.first_waiting.wait(2.0)
        assert runner.reconfigure({}, {}) == "Active"
        runner.submit(70.0)
        barrier.release.set()
        assert barrier.swapped_waiting.wait(2.0)
        assert runner.control_period() == 2.0
        assert runner.commands_fan() is True
        assert runner.actuation_mode() is ActuationMode.FRAMED_PULSE

        clock.advance(4.0)
        stale = runner.latest()
        assert stale.revision == 1
        assert stale.stale_state is ResultStaleState.STALE
    finally:
        barrier.release.set()
        runner.stop()


def test_threaded_runner_stop_terminates_thread():
    core = FakeCore()
    r = ThreadedControllerRunner(core)
    thread = r._thread
    assert thread.is_alive()
    r.stop()
    assert not thread.is_alive()
    r.stop()  # idempotent


def test_threaded_runner_stop_closes_blocking_period_wait_before_joining():
    wait_for_period = CloseAwarePeriodWait()
    runner = ThreadedControllerRunner(FakeCore(), wait_for_period=wait_for_period)
    thread = runner._thread
    try:
        assert wait_for_period.waiting.wait(2.0)
        runner.stop()
        assert wait_for_period.closed.is_set()
        assert not thread.is_alive()
    finally:
        wait_for_period.close()
        runner.stop()


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


def test_threaded_runner_never_exposes_core_internals_before_first_result():
    core = FakeCore()
    r = ThreadedControllerRunner(core)
    try:
        assert r.controller_state() == {
            "pending_dropped": 0,
            "pending_observations": 0,
            "dropped_observations": 0,
        }
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
        assert r.controller_state() == {
            "pending_dropped": 0,
            "pending_observations": 0,
            "dropped_observations": 0,
        }
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
    # Scaffolding for a mode built without setup(): the shape a real HoldMode
    # always has by teardown, with identification simply off. Without it the
    # refit step logs a real ERROR about a mode this test never intended to
    # build. That teardown survives a malformed settings dict at all is
    # asserted in test_hold_refit_trigger.py, not here.
    hold.settings = {"controller": {"config": {}}}
    hold._controller_name = "mpc"
    hold.ctx = type("_Context", (), {"clock": type("_Clock", (), {"now": staticmethod(lambda: 0.0)})()})()
    hold._pulse_scheduler = None
    hold._trace_closed = True
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

    def actuation_mode(self):
        return ActuationMode.FRAMED_PULSE

    def get_status(self):
        return None

    def trace_diagnostics(self):
        return None

    def trace_allocation(self):
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


def test_restore_model_deep_copies_the_snapshot_on_the_way_in():
    core = _OrderRecordingCore()
    runner = ThreadedControllerRunner(core)
    try:
        snapshot = {"revision": 7, "online_adaptation": {"challenger": {"params": [1.0]}}}
        assert runner.restore_model(snapshot) is True
        snapshot["online_adaptation"]["challenger"]["params"][0] = 999.0
        runner.submit(212.0)
        assert _wait_for(
            lambda: core.restored == [{"revision": 7, "online_adaptation": {"challenger": {"params": [1.0]}}}]
        )
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


def test_get_model_snapshot_returns_a_deep_copy_not_the_core_s_object():
    core = _OrderRecordingCore()
    core.snapshot = {"revision": 1, "online_adaptation": {"challenger": {"params": [1.0]}}}
    runner = ThreadedControllerRunner(core)
    try:
        runner.submit(212.0)
        assert _wait_for(lambda: ("update", 212.0) in core.calls)
        snapshot = runner.get_model_snapshot()
        assert snapshot is not core.snapshot
        snapshot["online_adaptation"]["challenger"]["params"][0] = 999.0
        expected = {"revision": 1, "online_adaptation": {"challenger": {"params": [1.0]}}}
        assert core.snapshot == expected
        assert runner.get_model_snapshot() == expected
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


class _ObservationBarrier:
    def __init__(self):
        self.calls = 0
        self.first_waiting = threading.Event()
        self.release = threading.Event()

    def __call__(self, seconds):
        self.calls += 1
        if self.calls == 1:
            self.first_waiting.set()
        assert self.release.wait(2.0)
        self.release.clear()


class _ObservationRecordingCore(_OrderRecordingCore):
    def __init__(self):
        super().__init__()
        self.observations = []
        self.learner_lock_free = []
        self.runner = None

    def observe_frame(self, observation):
        acquired = self.runner._lock.acquire(blocking=False)
        if acquired:
            self.runner._lock.release()
        self.learner_lock_free.append(acquired)
        with self.lock:
            self.observations.append(observation)
            self.calls.append(("observe_frame", observation.frame_end_s))


def test_threaded_runner_delivers_frame_observations_in_timestamp_order_before_update():
    barrier = _ObservationBarrier()
    core = _ObservationRecordingCore()
    runner = ThreadedControllerRunner(core, wait_for_period=barrier)
    core.runner = runner
    try:
        assert barrier.first_waiting.wait(2.0)
        runner.observe_frame(_frame(1))
        runner.observe_frame(_frame(0))
        runner.observe_frame(_frame(2))
        runner.submit(212.0)
        barrier.release.set()

        assert _wait_for(lambda: len(core.observations) == 3 and ("update", 212.0) in core.calls)
        with core.lock:
            calls = list(core.calls)
        first_update = next(index for index, call in enumerate(calls) if call == ("update", 212.0))
        assert calls[:first_update] == [
            ("observe_frame", 20.0),
            ("observe_frame", 40.0),
            ("observe_frame", 60.0),
        ]
        assert core.learner_lock_free == [True, True, True]
    finally:
        barrier.release.set()
        runner.stop()


def test_threaded_runner_drains_outcomes_for_exact_delivered_observations():
    barrier = _ObservationBarrier()

    class OutcomeCore(_ObservationRecordingCore):
        def observe_frame(self, observation):
            super().observe_frame(observation)
            return {"role_generation": observation.role_generation, "eligible": False}

    core = OutcomeCore()
    runner = ThreadedControllerRunner(core, wait_for_period=barrier)
    core.runner = runner
    try:
        assert barrier.first_waiting.wait(2.0)
        later = _frame(1)
        earlier = _frame(0)
        later_sequence = runner.observe_frame(later)
        earlier_sequence = runner.observe_frame(earlier)
        runner.submit(212.0)
        barrier.release.set()
        assert _wait_for(lambda: len(core.observations) == 2)

        outcomes = runner.drain_observation_outcomes()

        assert [(item.submission_sequence, item.observation) for item in outcomes] == [
            (earlier_sequence.submission_sequence, earlier),
            (later_sequence.submission_sequence, later),
        ]
        assert runner.drain_observation_outcomes().envelopes == ()
    finally:
        barrier.release.set()
        runner.stop()


def test_threaded_runner_isolates_observation_failure_and_marks_the_next_frame_discontinuous():
    barrier = _ObservationBarrier()

    class RaisingCore(_ObservationRecordingCore):
        def __init__(self):
            super().__init__()
            self.failed = False
            self.failures = []

        def observe_frame(self, observation):
            super().observe_frame(observation)
            if not self.failed:
                self.failed = True
                raise FloatingPointError("learner failed")
            return None

        def observation_failure(self, observation, error):
            self.failures.append((observation, error))
            return {
                "role_generation": observation.role_generation,
                "eligible": False,
                "rejection_reasons": ("learner-exception",),
            }

    core = RaisingCore()
    runner = ThreadedControllerRunner(core, wait_for_period=barrier)
    core.runner = runner
    try:
        assert barrier.first_waiting.wait(2.0)
        first = _frame(0)
        first_submission = runner.observe_frame(first)
        runner.submit(212.0)
        barrier.release.set()

        assert _wait_for(lambda: ("update", 212.0) in core.calls)
        first_drain = runner.drain_observation_outcomes()
        assert len(first_drain.envelopes) == 1
        assert first_drain.envelopes[0].submission_sequence == first_submission.submission_sequence
        assert first_drain.envelopes[0].outcome["rejection_reasons"] == ("learner-exception",)
        assert core.failures and isinstance(core.failures[0][1], FloatingPointError)
        assert runner._thread.is_alive()

        runner.observe_frame(_frame(1))
        barrier.release.set()
        assert _wait_for(lambda: len(core.observations) == 2)
        assert core.observations[1].continuous is False
    finally:
        barrier.release.set()
        runner.stop()


def test_threaded_runner_evicts_oldest_timestamps_and_marks_the_next_retained_frame():
    barrier = _ObservationBarrier()
    core = _ObservationRecordingCore()
    runner = ThreadedControllerRunner(core, wait_for_period=barrier)
    core.runner = runner
    try:
        assert barrier.first_waiting.wait(2.0)
        observations = [
            _frame(30),
            *(_frame(index) for index in range(29)),
            _frame(31),
            _frame(32),
            _frame(1),
        ]
        for observation in observations:
            runner.observe_frame(observation)

        assert runner.controller_state()["pending_observations"] == _MAX_PENDING_OBSERVATIONS
        assert runner.controller_state()["dropped_observations"] == 3
        barrier.release.set()

        assert _wait_for(lambda: len(core.observations) == _MAX_PENDING_OBSERVATIONS)
        retained_indexes = [*range(2, 29), 30, 31, 32]
        assert [observation.frame_end_s for observation in core.observations] == [
            float((index + 1) * 20) for index in retained_indexes
        ]
        assert core.observations[0].continuous is False
        assert all(observation.continuous for observation in core.observations[1:])
        assert next(observation for observation in observations if observation.frame_end_s == 60.0).continuous is True
        assert runner.controller_state()["pending_observations"] == 0
        assert runner.controller_state()["dropped_observations"] == 3
    finally:
        barrier.release.set()
        runner.stop()


def test_threaded_runner_stop_flushes_accepted_observation_outcome():
    barrier = _ObservationBarrier()

    class OutcomeCore(_ObservationRecordingCore):
        def observe_frame(self, observation):
            super().observe_frame(observation)
            return {"role_generation": observation.role_generation, "eligible": False}

    core = OutcomeCore()
    runner = ThreadedControllerRunner(core, wait_for_period=barrier)
    core.runner = runner
    assert barrier.first_waiting.wait(2.0)
    submission = runner.observe_frame(_frame(0))
    errors = []
    stopper = threading.Thread(target=lambda: errors.append(runner.stop()))
    stopper.start()
    assert _wait_for(lambda: runner._stop_event.is_set())
    barrier.release.set()
    assert runner.observe_frame(_frame(1)) is None
    stopper.join(2.0)
    assert not stopper.is_alive()
    assert errors == [None]
    drain = runner.drain_observation_outcomes()
    assert [(item.submission_sequence, item.observation.frame_end_s) for item in drain] == [
        (submission.submission_sequence, 20.0)
    ]


def test_threaded_runner_swap_delivers_pre_swap_observation_to_old_core():
    barrier = _ObservationBarrier()

    class OutcomeCore(_ObservationRecordingCore):
        def __init__(self, name):
            super().__init__()
            self.name = name

        def observe_frame(self, observation):
            super().observe_frame(observation)
            return {"core": self.name, "role_generation": observation.role_generation, "eligible": False}

    old = OutcomeCore("old")
    new = OutcomeCore("new")
    runner = ThreadedControllerRunner(old, wait_for_period=barrier)
    old.runner = runner
    new.runner = runner
    try:
        assert barrier.first_waiting.wait(2.0)
        submission = runner.observe_frame(_frame(0))
        with runner._lock:
            runner._pending_core = new
            runner._pending_controller_type = None
        later = runner.observe_frame(_frame(1))
        barrier.release.set()
        assert _wait_for(lambda: len(old.observations) == 1 and len(new.observations) == 1)
        envelopes = {item.submission_sequence: item for item in runner.drain_observation_outcomes()}
        assert envelopes[submission.submission_sequence].outcome["core"] == "old"
        assert envelopes[submission.submission_sequence].configuration_generation == submission.configuration_generation
        assert envelopes[later.submission_sequence].outcome["core"] == "new"
        assert envelopes[later.submission_sequence].configuration_generation == later.configuration_generation
    finally:
        barrier.release.set()
        runner.stop()


def test_threaded_runner_drains_reserved_new_generation_before_first_swapped_solve():
    barrier = _ObservationBarrier()
    old = _ObservationRecordingCore()
    new = _ObservationRecordingCore()
    runner = ThreadedControllerRunner(old, wait_for_period=barrier)
    old.runner = runner
    new.runner = runner
    try:
        assert barrier.first_waiting.wait(2.0)
        with runner._lock:
            runner._pending_core = new
            runner._pending_controller_type = None
        runner.observe_frame(_frame(0))
        runner.submit(212.0)
        barrier.release.set()

        assert _wait_for(lambda: len(new.observations) == 1 and ("update", 212.0) in new.calls)
        with new.lock:
            calls = list(new.calls)
        assert calls.index(("observe_frame", 20.0)) < calls.index(("update", 212.0))
    finally:
        barrier.release.set()
        runner.stop()


def test_threaded_submission_reports_exact_out_of_order_input_eviction():
    barrier = _ObservationBarrier()
    core = _ObservationRecordingCore()
    runner = ThreadedControllerRunner(core, wait_for_period=barrier)
    core.runner = runner
    try:
        assert barrier.first_waiting.wait(2.0)
        first = runner.observe_frame(_frame(100))
        for index in range(30):
            submission = runner.observe_frame(_frame(index))
        assert submission.evicted_sequence == 2
        assert first.evicted_sequence is None
    finally:
        barrier.release.set()
        runner.stop()


def test_threaded_submission_reports_self_eviction_for_new_earliest_frame():
    barrier = _ObservationBarrier()
    core = _ObservationRecordingCore()
    runner = ThreadedControllerRunner(core, wait_for_period=barrier)
    core.runner = runner
    try:
        assert barrier.first_waiting.wait(2.0)
        for index in range(1, 31):
            runner.observe_frame(_frame(index))
        self_evicted = runner.observe_frame(_frame(0))
        assert self_evicted.evicted_sequence == self_evicted.submission_sequence
    finally:
        barrier.release.set()
        runner.stop()


def test_threaded_stop_drains_reserved_swap_generation():
    barrier = _ObservationBarrier()

    class OutcomeCore(_ObservationRecordingCore):
        def __init__(self, name):
            super().__init__()
            self.name = name

        def observe_frame(self, observation):
            super().observe_frame(observation)
            return {"core": self.name, "role_generation": observation.role_generation, "eligible": False}

    old = OutcomeCore("old")
    new = OutcomeCore("new")
    runner = ThreadedControllerRunner(old, wait_for_period=barrier)
    old.runner = new.runner = runner
    assert barrier.first_waiting.wait(2.0)
    runner.observe_frame(_frame(0))
    with runner._lock:
        runner._pending_core = new
        runner._pending_controller_type = None
    runner.observe_frame(_frame(1))
    stopper = threading.Thread(target=runner.stop)
    stopper.start()
    barrier.release.set()
    stopper.join(2.0)
    assert not stopper.is_alive()
    outcomes = runner.drain_observation_outcomes().envelopes
    assert [item.outcome["core"] for item in outcomes] == ["old", "new"]


def test_threaded_generation_specific_overflow_marks_only_reserved_generation():
    barrier = _ObservationBarrier()
    old = _ObservationRecordingCore()
    new = _ObservationRecordingCore()
    runner = ThreadedControllerRunner(old, wait_for_period=barrier)
    old.runner = new.runner = runner
    try:
        assert barrier.first_waiting.wait(2.0)
        runner.observe_frame(_frame(100))
        with runner._lock:
            runner._pending_core = new
            runner._pending_controller_type = None
        for index in range(1, 32):
            runner.observe_frame(_frame(index))
        barrier.release.set()
        assert _wait_for(lambda: len(old.observations) == 1)
        assert old.observations[0].continuous is True
        barrier.release.set()
        assert _wait_for(lambda: len(new.observations) > 0)
        assert new.observations[0].continuous is False
    finally:
        barrier.release.set()
        runner.stop()


def test_threaded_runner_ignores_observations_for_a_core_without_a_learner():
    barrier = _ObservationBarrier()
    core = FakeCore()
    runner = ThreadedControllerRunner(core, wait_for_period=barrier)
    try:
        assert barrier.first_waiting.wait(2.0)
        runner.observe_frame(_frame(0))
        runner.submit(212.0)
        barrier.release.set()

        assert core.updated.wait(2.0)
        assert runner.controller_state()["pending_observations"] == 0
        assert runner.controller_state()["dropped_observations"] == 0
    finally:
        barrier.release.set()
        runner.stop()


class _DeliveryBlockingObservationCore(_ObservationRecordingCore):
    def __init__(self):
        super().__init__()
        self.first_delivery_started = threading.Event()
        self.release_first_delivery = threading.Event()

    def observe_frame(self, observation):
        super().observe_frame(observation)
        if len(self.observations) == 1:
            self.first_delivery_started.set()
            assert self.release_first_delivery.wait(2.0)


def test_threaded_runner_redrains_observations_enqueued_during_learner_delivery():
    barrier = _ObservationBarrier()
    core = _DeliveryBlockingObservationCore()
    runner = ThreadedControllerRunner(core, wait_for_period=barrier)
    core.runner = runner
    try:
        assert barrier.first_waiting.wait(2.0)
        runner.observe_frame(_frame(0))
        runner.submit(212.0)
        barrier.release.set()
        assert core.first_delivery_started.wait(2.0)

        runner.observe_frame(_frame(1))
        core.release_first_delivery.set()

        assert _wait_for(lambda: len(core.observations) == 2 and ("update", 212.0) in core.calls)
        with core.lock:
            calls = list(core.calls)
        first_update = next(index for index, call in enumerate(calls) if call == ("update", 212.0))
        assert calls[:first_update] == [("observe_frame", 20.0), ("observe_frame", 40.0)]
    finally:
        core.release_first_delivery.set()
        barrier.release.set()
        runner.stop()


def test_hold_submission_overflow_marks_exact_gap_and_rebuilds_online_learning_gates(hold_cycle, monkeypatch):
    """Dropped frame identity reaches the real learner as one discontinuity."""
    import controller.runtime.modes.hold as hold_module

    class _Recorder:
        def __init__(self):
            self.records = []

        def record(self, record):
            self.records.append(record)

        def flush_due(self, _now_ms):
            pass

        def close(self):
            pass

    class _Gate:
        def __init__(self):
            self.waiting = threading.Event()
            self.release = threading.Event()
            self.closed = threading.Event()

        def __call__(self, _seconds):
            self.waiting.set()
            assert self.release.wait(2.0)
            if not self.closed.is_set():
                self.release.clear()

        def close(self):
            self.closed.set()
            self.release.set()

    class _ObservedMpcController(MpcController):
        def __init__(self):
            super().__init__(
                dict(MPC_DEFAULTS, policy="net", enable_online_adaptation=True),
                "C",
                {"u_min": 0.1, "u_max": 0.9, "HoldCycleTime": 25},
            )
            self.set_target(110.0)
            self.observed = []
            self._observed_condition = threading.Condition()

        def observe_frame(self, observation):
            outcome = super().observe_frame(observation)
            with self._observed_condition:
                self.observed.append((observation, outcome))
                self._observed_condition.notify_all()
            return outcome

        def wait_for_observations(self, count):
            with self._observed_condition:
                return self._observed_condition.wait_for(lambda: len(self.observed) >= count, timeout=2.0)

    def frame(index, realized_q):
        return replace(
            _frame(index),
            temp_c=100.0,
            requested_q=realized_q,
            realized_q=realized_q,
            requested_auger_duty=realized_q,
        )

    recorder = _Recorder()
    monkeypatch.setattr(hold_module, "ControlTraceRecorder", lambda **_kwargs: recorder)
    gate = _Gate()
    core = _ObservedMpcController()
    runner = ThreadedControllerRunner(core, wait_for_period=gate)
    mode = hold_cycle(runner, controller="mpc")
    try:
        assert gate.waiting.wait(2.0)
        mode.setup()
        mode.state.metrics = {"id": "hold-observation-overflow"}
        mode._ensure_trace_session(0.0)

        for index in range(31):
            mode._deliver_completed_pulse_observation((index * 20, (index + 1) * 20), frame(index, 0.3))

        assert list(mode._pending_model_observations) == list(range(2, 32))
        assert runner.controller_state()["dropped_observations"] == 1

        gate.release.set()
        assert core.wait_for_observations(30)
        mode._reconcile_model_observation_outcomes(now=620.0)
        traced_observations = [
            record for record in recorder.records if record.event_kind is TraceEventKind.MODEL_OBSERVATION
        ]
        first_traced = traced_observations[0]
        assert isinstance(first_traced.payload, ModelObservationPayload)
        assert (
            first_traced.payload.frame_start_ms,
            first_traced.payload.frame_end_ms,
            first_traced.payload.role_generation,
            first_traced.payload.continuous,
            first_traced.payload.rejection_reasons,
        ) == (20_000, 40_000, 0, False, ("discontinuity",))
        assert mode._pending_model_observations == {}

        initial = tuple(core.observed)
        assert [observation.frame_end_s for observation, _outcome in initial] == [
            float((index + 1) * 20) for index in range(1, 31)
        ]
        assert initial[0][0].continuous is False
        assert all(observation.continuous for observation, _outcome in initial[1:])
        assert initial[0][1]["rejection_reasons"] == ("discontinuity",)
        assert [outcome["rejection_reasons"] for _observation, outcome in initial[1:16]] == [("lag-warmup",)] * 15
        assert all(
            outcome["rejection_reasons"] == ("insufficient-excitation",) for _observation, outcome in initial[16:]
        )
        adaptation = core.get_status()["adaptation"]
        assert (adaptation["active_model_kind"], adaptation["promotion_count"]) == ("grey-box", 0)

        for index in range(31, 61):
            realized_q = 0.1 if index % 2 else 0.5
            mode._deliver_completed_pulse_observation((index * 20, (index + 1) * 20), frame(index, realized_q))

        gate.release.set()
        assert core.wait_for_observations(60)

        recovered = tuple(core.observed[30:])
        assert all(outcome["eligible"] for _observation, outcome in recovered)
        assert any(
            "samples" in outcome["evaluation"]["rejection_reasons"]
            for _observation, outcome in recovered
            if "evaluation" in outcome
        )
        adaptation = core.get_status()["adaptation"]
        assert adaptation["effective_samples"] >= 20
        assert (adaptation["active_model_kind"], adaptation["promotion_count"]) == ("grey-box", 0)
    finally:
        gate.close()
        runner.stop()
