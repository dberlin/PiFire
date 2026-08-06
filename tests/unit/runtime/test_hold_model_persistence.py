"""Hold restores a controller's model at setup and saves it as it changes."""

import threading

from common.control_trace import ActuationMode
from common.controller_model_state import ControllerModelStore

from controller.applied_output import OutputSource
from controller.runtime.model_checkpoint import ModelCheckpointWorker
from controller.runtime.runner import ControllerUpdateResult

from tests.fakes.runner import FakeControllerRunner
from tests.unit.runtime.conftest import _off, _output


class _FakeModelStore:
    def __init__(self, initial=None):
        self.models = dict(initial or {})
        self.saves = []

    def load(self, name):
        return self.models.get(name)

    def save(self, name, snapshot):
        self.saves.append((name, snapshot))
        self.models[name] = snapshot
        return True


def _deduplicating_store():
    state = {}
    writes = []

    def read(_key):
        if "value" not in state:
            raise TypeError("controller model state is absent")
        return state["value"]

    def write(_key, value):
        writes.append(value)
        state["value"] = value

    return ControllerModelStore(reader=read, writer=write), writes


def _framed_output():
    return ControllerUpdateResult(
        cycle_ratio=0.5,
        fan=None,
        input_temperature=0.0,
        revision=1,
        solve_start_monotonic=0.0,
        solve_end_monotonic=0.0,
        solve_duration_seconds=0.0,
        completed_wall_time=0.0,
    )


def test_setup_restores_a_stored_model_before_seeding(hold_cycle):
    runner = FakeControllerRunner(period=0.01)
    store = _FakeModelStore({"pid_sp": {"revision": 3, "K": 700.0}})
    hold = hold_cycle(runner, model_store=store, controller="pid_sp")
    hold.setup()
    assert runner.restored == [{"revision": 3, "K": 700.0}]
    # the seed report comes after the restore, so it lands on the restored model
    assert [a.source for a in runner.applied] == [OutputSource.SEED]
    # `restored` and `applied` are separate lists and cannot express relative
    # order on their own -- `calls` is the single ordered log that can.
    kinds = [kind for kind, _ in runner.calls]
    assert kinds.index("restore") < kinds.index("apply")


def test_setup_with_no_stored_model_restores_nothing(hold_cycle):
    runner = FakeControllerRunner(period=0.01)
    hold = hold_cycle(runner, model_store=_FakeModelStore(), controller="pid_sp")
    hold.setup()
    assert runner.restored == []


def test_per_tick_saves_the_controller_snapshot(hold_cycle):
    runner = FakeControllerRunner(period=0.0).script([_output(0.5)])
    store = _FakeModelStore()
    hold = hold_cycle(runner, model_store=store, controller="pid_sp")
    hold.setup()
    try:
        runner.snapshot = {"revision": 1, "K": 700.0}
        hold.on_tick(now=100.0, ptemp=200.0, current_output_status=_off())
    finally:
        hold.ctx.clock.advance(400.0)
        hold.teardown(200.0)
    assert store.saves == [("pid_sp", {"revision": 1, "K": 700.0})]


def test_checkpoint_writer_does_not_block_hold_or_teardown_and_finishes_latest_snapshot(hold_cycle):
    """Checkpoint I/O remains owned after nonblocking Hold teardown."""

    class _BlockingStore:
        def __init__(self):
            self.models = {}
            self.first_write_started = threading.Event()
            self.allow_first_write = threading.Event()
            self.latest_saved = threading.Event()
            self.writer_threads = []
            self.saved_snapshots = []
            self._writes = 0

        def load(self, _name):
            return None

        def save(self, name, snapshot):
            owned_snapshot = dict(snapshot)
            self.writer_threads.append(threading.current_thread())
            self._writes += 1
            if self._writes == 1:
                self.first_write_started.set()
                self.allow_first_write.wait()
            self.models[name] = owned_snapshot
            self.saved_snapshots.append((name, owned_snapshot))
            if owned_snapshot["revision"] == 2:
                self.latest_saved.set()
            return True

    runner = FakeControllerRunner(period=0.01).script([_output(0.5)])
    store = _BlockingStore()
    hold = hold_cycle(runner, model_store=store, controller="pid_sp")
    hold.setup()
    runner.snapshot = {"revision": 1, "K": 700.0}
    tick_finished = threading.Event()
    tick_errors = []

    def tick():
        try:
            hold.on_tick(now=100.0, ptemp=200.0, current_output_status=_off())
        except BaseException as error:
            tick_errors.append(error)
        finally:
            tick_finished.set()

    tick_thread = threading.Thread(target=tick)
    teardown_thread = None
    teardown_errors = []
    teardown_clock_advanced = False
    tick_thread.start()
    try:
        assert store.first_write_started.wait(timeout=1.0), "checkpoint writer never received the first snapshot"
        assert tick_finished.wait(timeout=0.2), "on_tick waited for checkpoint I/O"
        assert not tick_errors

        runner.snapshot = {"revision": 2, "K": 701.0}
        hold.on_tick(now=101.0, ptemp=200.0, current_output_status=_off())
        teardown_finished = threading.Event()
        hold.ctx.clock.advance(400.0)
        teardown_clock_advanced = True

        def teardown():
            try:
                hold.teardown(200.0)
            except BaseException as error:
                teardown_errors.append(error)
            finally:
                teardown_finished.set()

        teardown_thread = threading.Thread(target=teardown)
        teardown_thread.start()
        assert teardown_finished.wait(timeout=0.2), "teardown waited for blocked checkpoint I/O"
        assert not teardown_errors
        store.allow_first_write.set()
        assert store.latest_saved.wait(timeout=1.0), "latest owned snapshot was not persisted"
    finally:
        # Every failure path releases the store before joining the tick or owned
        # writer, so this test neither deadlocks nor leaves a blocked writer.
        store.allow_first_write.set()
        tick_thread.join(timeout=1.0)
        if teardown_thread is None:
            if not teardown_clock_advanced:
                hold.ctx.clock.advance(400.0)
            hold.teardown(200.0)
        else:
            teardown_thread.join(timeout=1.0)

    assert not tick_thread.is_alive()
    assert teardown_thread is not None and not teardown_thread.is_alive()
    assert store.models["pid_sp"] == {"revision": 2, "K": 701.0}
    assert [snapshot["revision"] for _, snapshot in store.saved_snapshots] == [1, 2]
    assert store.writer_threads and all(not thread.is_alive() for thread in store.writer_threads)


def test_framed_ticks_persist_only_advancing_model_revisions(hold_cycle):
    runner = FakeControllerRunner(period=0.0, actuation_mode=ActuationMode.FRAMED_PULSE).script([_framed_output()])
    store, writes = _deduplicating_store()
    hold = hold_cycle(runner, model_store=store, controller="mpc")
    hold.setup()

    try:
        runner.snapshot = {"revision": 1, "params": {}}
        hold.on_tick(now=100.0, ptemp=200.0, current_output_status=_off())
        hold.on_tick(now=101.0, ptemp=200.0, current_output_status=_off())
        runner.snapshot = {"revision": 2, "params": {}}
        hold.on_tick(now=122.0, ptemp=200.0, current_output_status=_off())
    finally:
        hold.ctx.clock.advance(400.0)
        hold.teardown(200.0)

    persisted_revisions = [record["models"]["mpc"]["revision"] for record in writes]
    assert persisted_revisions
    assert persisted_revisions[-1] == 2
    assert persisted_revisions == sorted(set(persisted_revisions))
    assert set(persisted_revisions) <= {1, 2}


def test_framed_ticks_leave_malformed_snapshots_to_the_model_store(hold_cycle):
    runner = FakeControllerRunner(period=0.0, actuation_mode=ActuationMode.FRAMED_PULSE).script([_framed_output()])
    store, writes = _deduplicating_store()
    hold = hold_cycle(runner, model_store=store, controller="mpc")
    hold.setup()

    try:
        runner.snapshot = {"revision": 1, "params": {"non_finite": float("nan")}}
        hold.on_tick(now=100.0, ptemp=200.0, current_output_status=_off())
    finally:
        hold.ctx.clock.advance(400.0)
        hold.teardown(200.0)

    assert writes == []


def test_save_does_not_fire_before_the_control_interval_elapses(hold_cycle):
    # A long control_period means the interval gate never opens for this tick;
    # a save wired outside that gate would still fire here.
    runner = FakeControllerRunner(period=1000.0).script([_output(0.5)])
    store = _FakeModelStore()
    hold = hold_cycle(runner, model_store=store, controller="pid_sp")
    hold.setup()
    runner.snapshot = {"revision": 1, "K": 700.0}
    hold.on_tick(now=100.0, ptemp=200.0, current_output_status=_off())
    assert store.saves == []


def test_a_controller_with_no_snapshot_saves_nothing(hold_cycle):
    runner = FakeControllerRunner(period=0.0).script([_output(0.5)])
    store = _FakeModelStore()
    hold = hold_cycle(runner, model_store=store, controller="pid")
    hold.setup()
    runner.snapshot = None
    hold.on_tick(now=100.0, ptemp=200.0, current_output_status=_off())
    assert store.saves == []


def test_setup_wires_the_default_store_through_ctx_store(hold_cycle):
    """A ControllerModelStore built without an injected model_store must read and
    write through ctx.store's generic-key methods, not the module-level SQLite
    functions -- otherwise a save never round-trips through the same store the
    rest of the process (and this same test's ctx) reads from.
    """
    runner = FakeControllerRunner(period=0.0).script([_output(0.5)])
    hold = hold_cycle(runner, controller="pid_sp")  # no model_store injected
    hold.setup()
    try:
        runner.snapshot = {"revision": 1, "K": 700.0}
        hold.on_tick(now=100.0, ptemp=200.0, current_output_status=_off())
    finally:
        hold.ctx.clock.advance(400.0)
        hold.teardown(200.0)
    saved = hold.ctx.store.read_generic_key("controller_model_state")
    assert saved["models"]["pid_sp"] == {"revision": 1, "K": 700.0}


def test_reconfigure_restores_the_model_and_reseeds(hold_cycle):
    runner = FakeControllerRunner(period=0.0).script([_output(0.5)])
    store = _FakeModelStore({"pid_sp": {"revision": 3, "K": 700.0}})
    hold = hold_cycle(runner, model_store=store, controller="pid_sp")
    hold.setup()
    runner.restored.clear()
    runner.applied.clear()
    runner.calls.clear()
    hold.control["controller_update"] = True
    hold.on_tick(now=100.0, ptemp=200.0, current_output_status=_off())
    assert runner.restored == [{"revision": 3, "K": 700.0}]
    assert runner.applied[0].source is OutputSource.SEED
    kinds = [kind for kind, _ in runner.calls]
    assert kinds.index("restore") < kinds.index("apply")


class _EventGatedCheckpointStore:
    def __init__(self):
        self.first_write_started = threading.Event()
        self.allow_first_write = threading.Event()
        self.saved = []
        self.writer_threads = []
        self._lock = threading.Lock()
        self._write_count = 0

    def save(self, name, snapshot):
        owned_snapshot = dict(snapshot)
        with self._lock:
            self._write_count += 1
            is_first_write = self._write_count == 1
            self.writer_threads.append(threading.current_thread())
        if is_first_write:
            self.first_write_started.set()
            if not self.allow_first_write.wait(timeout=1.0):
                raise TimeoutError("test did not release the first checkpoint write")
        with self._lock:
            self.saved.append((name, owned_snapshot))
        return True


class _CheckpointLogger:
    def __init__(self):
        self.errors = []

    def error(self, message):
        self.errors.append(message)


def test_checkpoint_worker_preserves_a_pending_checkpoint_when_b_is_submitted():
    """A blocked A1 write must not let B1 replace the pending latest A2 write."""
    store = _EventGatedCheckpointStore()
    logger = _CheckpointLogger()
    worker = ModelCheckpointWorker(store, logger)
    pending_submission_finished = threading.Event()
    pending_submission_errors = []
    pending_submission_results = []

    def submit_pending_checkpoints():
        try:
            pending_submission_results.extend(
                [
                    worker.submit("controller_a", {"revision": 2}),
                    worker.submit("controller_b", {"revision": 1}),
                ]
            )
        except BaseException as error:
            pending_submission_errors.append(error)
        finally:
            pending_submission_finished.set()

    pending_submission_thread = None
    try:
        assert worker.submit("controller_a", {"revision": 1})
        assert store.first_write_started.wait(timeout=1.0), "writer never began the active A1 save"

        pending_submission_thread = threading.Thread(target=submit_pending_checkpoints)
        pending_submission_thread.start()
        assert pending_submission_finished.wait(timeout=0.2), "checkpoint submission blocked a control tick"
        assert not pending_submission_errors
        assert pending_submission_results == [True, True]

        store.allow_first_write.set()
    finally:
        store.allow_first_write.set()
        if pending_submission_thread is not None:
            pending_submission_thread.join(timeout=1.0)
        worker.flush_and_stop()

    assert pending_submission_thread is not None and not pending_submission_thread.is_alive()
    assert not logger.errors
    assert {name: snapshot for name, snapshot in store.saved} == {
        "controller_a": {"revision": 2},
        "controller_b": {"revision": 1},
    }
    assert [snapshot for name, snapshot in store.saved if name == "controller_a"] == [
        {"revision": 1},
        {"revision": 2},
    ]
    assert [snapshot for name, snapshot in store.saved if name == "controller_b"] == [{"revision": 1}]
    assert store.writer_threads and all(not thread.is_alive() for thread in store.writer_threads)


def test_checkpoint_worker_coalesces_pending_revisions_per_controller():
    """A3 submitted behind a blocked A1 save must supersede unpicked A2."""
    store = _EventGatedCheckpointStore()
    logger = _CheckpointLogger()
    worker = ModelCheckpointWorker(store, logger)
    pending_submission_finished = threading.Event()
    pending_submission_errors = []
    pending_submission_results = []

    def submit_pending_revisions():
        try:
            pending_submission_results.extend(
                [
                    worker.submit("controller_a", {"revision": 2}),
                    worker.submit("controller_a", {"revision": 3}),
                ]
            )
        except BaseException as error:
            pending_submission_errors.append(error)
        finally:
            pending_submission_finished.set()

    pending_submission_thread = None
    try:
        assert worker.submit("controller_a", {"revision": 1})
        assert store.first_write_started.wait(timeout=1.0), "writer never began the active A1 save"

        pending_submission_thread = threading.Thread(target=submit_pending_revisions)
        pending_submission_thread.start()
        assert pending_submission_finished.wait(timeout=0.2), "checkpoint submission blocked a control tick"
        assert not pending_submission_errors
        assert pending_submission_results == [True, True]

        store.allow_first_write.set()
    finally:
        store.allow_first_write.set()
        if pending_submission_thread is not None:
            pending_submission_thread.join(timeout=1.0)
        worker.flush_and_stop()

    assert pending_submission_thread is not None and not pending_submission_thread.is_alive()
    assert not logger.errors
    assert [snapshot for name, snapshot in store.saved if name == "controller_a"] == [
        {"revision": 1},
        {"revision": 3},
    ]
    assert store.writer_threads and all(not thread.is_alive() for thread in store.writer_threads)
