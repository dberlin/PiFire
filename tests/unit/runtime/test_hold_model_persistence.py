"""Hold restores a controller's model at setup and saves it as it changes."""

from copy import deepcopy
import itertools
import time
from types import SimpleNamespace

import threading
import pytest

from common.control_trace import ActuationMode
from common.controller_model_state import CheckpointSaveOutcome, ControllerModelStore
from common.persistence.model_evidence import (
    append_model_evidence,
    commit_model_activation_phase,
    commit_model_rollback,
    read_model_activation,
    read_model_evidence,
)
from common.model_evidence import (
    ActivationEvidence,
    ConfidenceDecisionEvidence,
    EvidenceKind,
    FallbackEvidence,
    ModelEvidenceRecord,
    RollbackEvidence,
)

from controller.applied_output import OutputSource
from controller.runtime.model_persistence import ModelPersistenceWorker
from controller.runtime.runner import (
    ControllerUpdateResult,
    PreparedPairTransition,
    ThreadedControllerRunner,
)
from controller.model_learning.activation import (
    ActivationPhase,
    PreparedActivationRecord,
)
from controller.model_learning.contracts import ActivationPolicy, CandidateOrigin
from tests.unit.runtime._persistence_helpers import _pair_phase_state
from controller.mpc import Controller as MpcController
from controller.mpc_config import DEFAULT_MPC_CONFIG as MPC_DEFAULTS
import controller.mpc_core as _mpc_core
from controller.mpc_snapshot import migrate_grey_learning_snapshot

from tests.fakes.runner import FakeControllerRunner
from tests.unit.runtime.conftest import _off, _output


class _FakeModelStore:
    def __init__(self, initial=None):
        self.models = dict(initial or {})
        self.saves = []

    def load(self, name):
        return self.models.get(name)

    def save_outcome(self, name, snapshot):
        self.saves.append((name, snapshot))
        self.models[name] = snapshot
        return CheckpointSaveOutcome.SAVED


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


def test_mpc_setup_migrates_v3_before_restore_and_activation_reconcile(hold_cycle, monkeypatch):
    from controller.runtime.modes import hold as hold_module

    calls = []
    v3 = {
        "version": 3,
        "revision": 1,
        "params": {
            "C_c": 2520.0,
            "h_amb": 18.5,
            "T_amb": 21.0,
            "theta": 47.0,
            "n_delay": 8,
            "K_Q": 910.0,
            "sigma": 0.0,
        },
        "rmse": None,
        "samples": 0,
        "band_c": [0.0, 0.0],
        "nfev": None,
    }

    class OrderedStore(_FakeModelStore):
        def load(self, name):
            calls.append("restore-load")
            return super().load(name)

    store = OrderedStore({"mpc": v3})

    def migrate_before_restore(*, defaults):
        calls.append("migrate")
        store.models["mpc"] = migrate_grey_learning_snapshot(v3)

    monkeypatch.setattr(hold_module, "migrate_mpc_learning_authority", migrate_before_restore)
    monkeypatch.setattr(hold_module, "read_model_activation", lambda: None)
    monkeypatch.setattr(hold_module, "read_model_evidence", lambda: ())
    runner = FakeControllerRunner(period=0.01)
    hold = hold_cycle(runner, model_store=store, controller="mpc")

    hold.setup()

    assert calls[:2] == ["migrate", "restore-load"]
    assert runner.restored[0]["version"] == 4


@pytest.mark.parametrize(
    "phase",
    (ActivationPhase.PREPARED, ActivationPhase.ACTIVE, ActivationPhase.ABORTED),
)
def test_setup_routes_new_prepared_pair_authority_without_legacy_activation_evidence(hold_cycle, monkeypatch, phase):
    from controller.runtime.modes import hold as hold_module

    persisted, _record = _pair_phase_state(phase)
    monkeypatch.setattr(hold_module, "read_model_activation", lambda: persisted)
    monkeypatch.setattr(hold_module, "read_model_evidence", lambda: [])
    runner = FakeControllerRunner(period=0.01)
    hold = hold_cycle(runner, model_store=_FakeModelStore(), controller="mpc")

    hold.setup()

    assert runner.activation_restores == [(persisted, ())]
    assert hold._activation_state_identity[0:2] == (phase.value, persisted.transaction_id)


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


def test_checkpoint_persistence_failure_disables_hold_learning(hold_cycle):
    state = {"version": 1, "models": {}}
    store = ControllerModelStore(
        reader=lambda _key: state,
        writer=lambda _key, _value: None,
        conditional_writer=lambda _name, _snapshot: False,
    )
    hold = hold_cycle(FakeControllerRunner(period=0.01), model_store=store, controller="pid_sp")
    hold.setup()
    worker = hold._persistence_worker
    assert worker is not None
    try:
        hold._checkpoint_model({"revision": 1})
        assert worker.flush_and_stop(timeout=1.0)
        assert worker.evidence_blocked
        hold._checkpoint_model({"revision": 2})
        assert not hold._learning_evidence_available
    finally:
        hold.ctx.clock.advance(400.0)
        hold.teardown(200.0)


def test_checkpoint_writer_does_not_block_hold_or_teardown_and_finishes_latest_snapshot(hold_cycle):
    """Checkpoint I/O remains owned after nonblocking Hold teardown.

    "Does not block" used to be measured by wait(timeout=0.2) -- a wall-clock
    budget assertion of exactly the kind pyproject.toml's addopts comment
    warns about: under `-n auto` on a loaded machine, a thread that is not
    actually blocked on anything can still take >200ms just to get scheduled,
    which fails this test for a reason that has nothing to do with the code.
    Proof now comes from a recorded happens-before order instead of a
    stopwatch: `store.order` only gains "write_unblocked" once this test
    calls `store.allow_first_write.set()`, which happens strictly later in
    program order than the tick/teardown completion checks below, so
    `"write_unblocked" not in store.order` there is a structural guarantee,
    not a timing race. The wait() calls that remain use generous bounds
    purely as a deadlock safety net (a genuinely blocked on_tick/teardown
    would hang until this test releases the write, far below -- so any bound
    that fits comfortably under pytest's own --timeout=120 tells them apart
    just as well as a tight one, without flaking on a busy machine).
    """

    class _BlockingStore:
        def __init__(self):
            self.models = {}
            self.first_write_started = threading.Event()
            self.allow_first_write = threading.Event()
            self.latest_saved = threading.Event()
            self.writer_threads = []
            self.saved_snapshots = []
            self.order = []
            self._writes = 0

        def load(self, _name):
            return None

        def save_outcome(self, name, snapshot):
            owned_snapshot = dict(snapshot)
            self.writer_threads.append(threading.current_thread())
            self._writes += 1
            if self._writes == 1:
                self.first_write_started.set()
                self.allow_first_write.wait()
                self.order.append("write_unblocked")
            self.models[name] = owned_snapshot
            self.saved_snapshots.append((name, owned_snapshot))
            if owned_snapshot["revision"] == 2:
                self.latest_saved.set()
            return CheckpointSaveOutcome.SAVED

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
            store.order.append("tick_finished")
            tick_finished.set()

    tick_thread = threading.Thread(target=tick)
    teardown_thread = None
    teardown_errors = []
    teardown_clock_advanced = False
    tick_thread.start()
    try:
        assert store.first_write_started.wait(timeout=1.0), "checkpoint writer never received the first snapshot"
        assert tick_finished.wait(timeout=10.0), "on_tick waited for checkpoint I/O"
        assert not tick_errors
        #  The write cannot have been unblocked yet -- that only happens below
        #  -- so on_tick returning here proves it did not wait on the write.
        assert "write_unblocked" not in store.order
        assert store.writer_threads and store.writer_threads[0] is not tick_thread

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
                store.order.append("teardown_finished")
                teardown_finished.set()

        teardown_thread = threading.Thread(target=teardown)
        teardown_thread.start()
        assert teardown_finished.wait(timeout=10.0), "teardown waited for blocked checkpoint I/O"
        assert not teardown_errors
        assert "write_unblocked" not in store.order
        assert store.writer_threads[0] is not teardown_thread
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
    #  store.latest_saved firing only proves save_outcome returned -- the
    #  persistence worker's own run loop still needs a moment afterward to
    #  notice flush_and_stop's earlier stop request and let its thread exit.
    #  Join (bounded, as a deadlock safety net -- see the module docstring
    #  above) before checking is_alive() instead of checking instantaneously,
    #  the same way tick_thread/teardown_thread are joined above: an
    #  un-joined check here is itself a wall-clock race, just one that only
    #  shows up under enough concurrent load to widen the window.
    for writer_thread in {*store.writer_threads}:
        writer_thread.join(timeout=5.0)
    assert store.writer_threads and all(not thread.is_alive() for thread in store.writer_threads)
    #  "write_unblocked" was recorded strictly after both completion markers
    #  above -- proving, by order rather than duration, that neither call
    #  waited on the checkpoint write.
    assert store.order.index("tick_finished") < store.order.index("write_unblocked")
    assert store.order.index("teardown_finished") < store.order.index("write_unblocked")


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

    def save_outcome(self, name, snapshot):
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
        return CheckpointSaveOutcome.SAVED


class _CheckpointLogger:
    def __init__(self):
        self.errors = []

    def error(self, message):
        self.errors.append(message)


def test_new_store_loads_owned_checkpoint_while_prior_writer_is_blocked():
    state = {"version": 1, "models": {"mpc": {"revision": 0}}}
    write_started = threading.Event()
    release_write = threading.Event()
    load_finished = threading.Event()
    loaded = []
    write_calls = 0

    def read(_key):
        return state

    def write(_key, value):
        nonlocal write_calls
        write_calls += 1
        if write_calls == 1:
            write_started.set()
            assert release_write.wait(1.0)
            raise RuntimeError("first checkpoint write failed")
        state.clear()
        state.update(value)

    prior_store = ControllerModelStore(reader=read, writer=write)
    replacement_store = ControllerModelStore(reader=read, writer=write)
    worker = ModelPersistenceWorker(prior_store, _CheckpointLogger())
    load_thread = threading.Thread(target=lambda: (loaded.append(replacement_store.load("mpc")), load_finished.set()))
    try:
        assert worker.submit_checkpoint("mpc", {"revision": 2})
        assert write_started.wait(1.0)
        load_thread.start()
        assert load_finished.wait(0.2), "replacement Hold blocked behind checkpoint I/O"
        assert loaded == [{"revision": 2}]
        release_write.set()
        assert worker.flush_and_stop(timeout=1.0)
        assert replacement_store.save("mpc", {"revision": 2}) is True
        assert state["models"]["mpc"] == {"revision": 2}
    finally:
        release_write.set()
        load_thread.join(timeout=1.0)
        worker.flush_and_stop()


def test_checkpoint_worker_preserves_a_pending_checkpoint_when_b_is_submitted():
    """A blocked A1 write must not let B1 replace the pending latest A2 write."""
    store = _EventGatedCheckpointStore()
    logger = _CheckpointLogger()
    worker = ModelPersistenceWorker(store, logger)
    pending_submission_finished = threading.Event()
    pending_submission_errors = []
    pending_submission_results = []

    def submit_pending_checkpoints():
        try:
            pending_submission_results.extend(
                [
                    worker.submit_checkpoint("controller_a", {"revision": 2}),
                    worker.submit_checkpoint("controller_b", {"revision": 1}),
                ]
            )
        except BaseException as error:
            pending_submission_errors.append(error)
        finally:
            pending_submission_finished.set()

    pending_submission_thread = None
    try:
        assert worker.submit_checkpoint("controller_a", {"revision": 1})
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
    worker = ModelPersistenceWorker(store, logger)
    pending_submission_finished = threading.Event()
    pending_submission_errors = []
    pending_submission_results = []

    def submit_pending_revisions():
        try:
            pending_submission_results.extend(
                [
                    worker.submit_checkpoint("controller_a", {"revision": 2}),
                    worker.submit_checkpoint("controller_a", {"revision": 3}),
                ]
            )
        except BaseException as error:
            pending_submission_errors.append(error)
        finally:
            pending_submission_finished.set()

    pending_submission_thread = None
    try:
        assert worker.submit_checkpoint("controller_a", {"revision": 1})
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


def test_timed_out_checkpoint_worker_cannot_overwrite_newer_replacement_checkpoint():
    state = {}
    first_write_started = threading.Event()
    release_old_write = threading.Event()

    def read(_key):
        if not state:
            raise TypeError("controller model state is absent")
        return deepcopy(state)

    def write(_key, value):
        state.clear()
        state.update(deepcopy(value))

    def conditional_write(name, snapshot):
        if snapshot["revision"] == 1:
            first_write_started.set()
            assert release_old_write.wait(1.0)
        existing = state.get("models", {}).get(name)
        if existing is not None and existing["revision"] >= snapshot["revision"]:
            return False
        state.clear()
        state.update({"version": 1, "models": {name: deepcopy(snapshot)}})
        return True

    old_store = ControllerModelStore(reader=read, writer=write, conditional_writer=conditional_write)
    new_store = ControllerModelStore(reader=read, writer=write, conditional_writer=conditional_write)
    old_worker = ModelPersistenceWorker(old_store, _CheckpointLogger())
    new_worker = ModelPersistenceWorker(new_store, _CheckpointLogger())
    try:
        assert old_worker.submit_checkpoint("mpc", {"revision": 1})
        assert first_write_started.wait(timeout=1.0)
        assert not old_worker.flush_and_stop(timeout=0.01)

        assert new_worker.submit_checkpoint("mpc", {"revision": 2})
        assert new_worker.flush_and_stop(timeout=0.2)
        assert state["models"]["mpc"] == {"revision": 2}

        release_old_write.set()
        assert old_worker.flush_and_stop(timeout=1.0)
        assert new_store.load("mpc") == {"revision": 2}
    finally:
        release_old_write.set()
        old_worker.flush_and_stop(timeout=1.0)
        new_worker.flush_and_stop(timeout=1.0)


class _CrashRecoveryEstimator:
    created = []

    def __init__(self, **_kwargs):
        self.closed = 0
        self.created.append(self)

    def update(self, _load, temperature):
        return [0.0] * 8 + [float(temperature), 0.0]

    def close(self):
        self.closed += 1


class _CrashRecoverySolver:
    created = []
    solve_order = []

    def __init__(self, config):
        self.config = config
        self.calls = []
        self.closed = 0
        self.created.append(self)

    def solve(self, state, *, setpoint_c, q_previous, equilibrium_q):
        self.solve_order.append(self)
        self.calls.append((state, setpoint_c, q_previous, equilibrium_q))
        return SimpleNamespace(
            sequence_q=[0.5] * self.config.horizon_steps,
            sequence_residual=[0.0] * self.config.horizon_steps,
            objective=1.0,
            diagnostics=SimpleNamespace(
                status=0,
                backend_status=0,
                iterations=1,
                solve_time_s=0.001,
                objective=1.0,
                kkt_residual=0.0,
                constraint_residual=0.0,
                warm_started=False,
            ),
        )

    def close(self):
        self.closed += 1


#: Well above any real stall on a loaded machine, and below the suite's
#: --timeout=120 ceiling, so a genuinely stuck runner still fails by name rather
#: than hanging the worker.
_GATE_TIMEOUT_S = 60.0


def _deterministic_monotonic():
    """A monotonic clock whose steps cannot be stretched by host load.

    `_ResultQualityTracker.completed` calls a solve a deadline miss when its
    measured `solve_duration_seconds` exceeds the control period, and two
    consecutive misses make the runner invoke `activation_runtime_failure`,
    which is a real safety behaviour: it rolls the active pair back to the
    retained one. Measured against `time.monotonic`, a fake solver that does no
    work still takes however long a loaded machine takes to get back to it, so
    running this suite under `-n auto` could induce that rollback and replace
    `active_control_pair` underneath assertions that pin its identity.

    Steps of a microsecond keep every solve far below any control period here
    while still advancing, so age and staleness arithmetic sees real progress.
    """
    counter = itertools.count()

    def _clock():
        return next(counter) * 1e-6

    return _clock


class _CrashRecoveryRunnerGate:
    """A barrier the runner thread cannot slip past.

    This used to fall through when its `wait_for` timed out, which made it a
    suggestion rather than a barrier: under parallel load a five-second stall let
    the control loop run free, `_arrivals` advanced without the test releasing
    anything, and `wait_for_arrivals` returned while the loop was mid-iteration.
    The assertions downstream then read whatever state that free-running loop
    happened to leave, surfacing as an `active_control_pair` identity mismatch
    far from the actual cause. A timeout is now recorded and reported instead of
    being silently ignored.
    """

    def __init__(self):
        self._condition = threading.Condition()
        self._permits = 0
        self._arrivals = 0
        self._open = False
        self._timed_out = False

    def __call__(self, _period):
        with self._condition:
            self._arrivals += 1
            self._condition.notify_all()
            released = self._condition.wait_for(
                lambda: self._open or self._permits > 0,
                timeout=_GATE_TIMEOUT_S,
            )
            if not released:
                self._timed_out = True
                self._condition.notify_all()
                return
            if not self._open and self._permits > 0:
                self._permits -= 1

    def wait_for_arrivals(self, count, timeout=1.0):
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._arrivals < count and not self._timed_out:
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    return False
                self._condition.wait(remaining)
            # A timed-out gate means the runner advanced without being released,
            # so any arrival count from here on describes an unsequenced loop.
            return self._arrivals >= count and not self._timed_out

    def release(self):
        with self._condition:
            self._permits += 1
            self._condition.notify_all()

    def open(self):
        with self._condition:
            self._open = True
            self._condition.notify_all()


@pytest.mark.parametrize(
    (
        "crash_boundary",
        "durable_phase",
        "lifecycle_kind",
        "precrash_owner",
        "precrash_authorized",
        "precrash_has_rollback",
    ),
    (
        ("before-prepared-receipt", None, None, "incumbent", True, False),
        (
            "after-prepared-receipt",
            ActivationPhase.PREPARED,
            None,
            "incumbent",
            True,
            False,
        ),
        (
            "after-inert-install-before-active-receipt",
            ActivationPhase.PREPARED,
            None,
            "candidate",
            False,
            True,
        ),
        (
            "after-active-receipt-before-authorization",
            ActivationPhase.ACTIVE,
            None,
            "candidate",
            False,
            True,
        ),
        (
            "after-active-authorization",
            ActivationPhase.ACTIVE,
            None,
            "candidate",
            True,
            True,
        ),
        (
            "after-compensation-abort-receipt",
            ActivationPhase.ABORTED,
            None,
            "incumbent",
            True,
            False,
        ),
        (
            "after-confidence-fallback-receipt",
            ActivationPhase.ACTIVE,
            EvidenceKind.FALLBACK,
            "incumbent",
            True,
            False,
        ),
        (
            "after-operator-rollback-receipt",
            ActivationPhase.ACTIVE,
            EvidenceKind.ROLLBACK,
            "incumbent",
            True,
            False,
        ),
    ),
)
def test_real_hold_sqlite_runner_recovery_converges_every_crash_boundary(
    hold_cycle,
    monkeypatch,
    tmp_path,
    crash_boundary,
    durable_phase,
    lifecycle_kind,
    precrash_owner,
    precrash_authorized,
    precrash_has_rollback,
) -> None:
    from controller.runtime.modes import hold as hold_module

    _CrashRecoveryEstimator.created.clear()
    _CrashRecoverySolver.created.clear()
    _CrashRecoverySolver.solve_order.clear()
    database_path = tmp_path / f"{crash_boundary}.sqlite"
    monkeypatch.setattr(_mpc_core, "GreyBoxEKF", _CrashRecoveryEstimator)
    monkeypatch.setattr(_mpc_core, "AcadosGreyBoxMPC", _CrashRecoverySolver)

    first_core = MpcController(
        dict(MPC_DEFAULTS, enable_online_adaptation=False, control_period=0.001),
        "C",
        {"u_min": 0.1, "u_max": 0.9},
    )
    incumbent_pair = first_core.active_control_pair
    incumbent = incumbent_pair.descriptor
    candidate_controller_config = dict(first_core.cfg)
    candidate_controller_config["theta"] = float(candidate_controller_config["theta"]) + 1.0
    candidate_estimator, candidate_solver = _mpc_core.MpcCore.build_components(
        candidate_controller_config,
        model_identified=True,
    )
    candidate_configuration = first_core._pair_factory.configured(
        candidate_controller_config,
        candidate_generation=incumbent.candidate_generation + 1,
        role_generation=incumbent.role_generation + 1,
        model_identified=True,
    )
    candidate = first_core._pair_factory.descriptor(candidate_configuration)
    candidate_pair = first_core._pair_factory.adopt(
        candidate_configuration,
        candidate_estimator,
        candidate_solver,
        authorized=False,
    )
    prepared = PreparedActivationRecord.prepared(
        timestamp_ms=1_000,
        incumbent=incumbent,
        candidate=candidate,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        policy=ActivationPolicy.PASSIVE_AUTO,
        decision_id=f"crash-{crash_boundary}",
    )
    prepared_write_started = threading.Event()
    prepared_write_release = threading.Event()

    def persist_activation_phase(record, expected):
        if crash_boundary == "before-prepared-receipt" and record.phase is ActivationPhase.PREPARED:
            prepared_write_started.set()
            if not prepared_write_release.wait(timeout=5.0):
                raise TimeoutError("prepared write was not released")
            raise RuntimeError("simulated process death before prepared receipt")
        return commit_model_activation_phase(
            record,
            expected_phase=expected,
            database_path=database_path,
        )

    persistence_worker = ModelPersistenceWorker(
        _FakeModelStore(),
        SimpleNamespace(error=lambda _message: None),
        append_evidence=lambda batch: append_model_evidence(
            batch,
            database_path=database_path,
        ),
        persist_activation_phase=persist_activation_phase,
    )
    first_core._activation_persistence_worker = persistence_worker

    first_core.set_target(225.0)
    first_result = first_core.update(225.0)
    assert 0.0 < first_result["cycle_ratio"] <= 0.5
    assert incumbent_pair.solver.calls

    def require_durable(receipt):
        assert receipt.accepted
        assert receipt.wait(timeout=1.0)
        assert receipt.durable is True

    first_boundary_runner = None
    first_boundary_gate = None
    if crash_boundary == "before-prepared-receipt":
        prepared_receipt = persistence_worker.submit_activation_phase(
            prepared,
            expected_phase=None,
        )
        assert prepared_receipt.accepted
        assert prepared_write_started.wait(timeout=1.0)
        assert prepared_receipt.completed is False
        assert prepared_receipt.durable is False
        first_boundary_gate = _CrashRecoveryRunnerGate()
        first_boundary_runner = ThreadedControllerRunner(
            first_core,
            wait_for_period=first_boundary_gate,
            monotonic_clock=_deterministic_monotonic(),
        )
        assert first_boundary_gate.wait_for_arrivals(1)
        transition = PreparedPairTransition(
            prepared,
            candidate_pair,
            prepared_receipt,
            lambda record, expected: persistence_worker.submit_activation_phase(
                record,
                expected_phase=expected,
            ),
        )
        assert first_boundary_runner.queue_pair_activation(transition) is False
        prepared_write_release.set()
        assert prepared_receipt.wait(timeout=1.0) is False
        assert prepared_receipt.completed is True
        assert prepared_receipt.durable is False
        assert read_model_activation(database_path=database_path) is None

    if durable_phase is not None:
        confidence = ModelEvidenceRecord(
            evidence_id=f"confidence-{crash_boundary}",
            kind=EvidenceKind.CONFIDENCE_DECISION,
            session_id="crash-recovery",
            cook_id=None,
            timestamp_ms=900,
            role_generation=incumbent.role_generation,
            model_digest=candidate.model_digest,
            provenance_digest=incumbent.model_digest,
            payload=ConfidenceDecisionEvidence(
                decision_id=prepared.decision_id,
                blocked=False,
                reason=None,
            ),
        )
        require_durable(first_core.submit_activation_confidence(confidence))
        require_durable(
            persistence_worker.submit_activation_phase(
                prepared,
                expected_phase=None,
            )
        )

    installs_candidate = crash_boundary not in (
        "before-prepared-receipt",
        "after-prepared-receipt",
    )
    if installs_candidate:
        assert first_core.install_candidate_pair_inert(candidate_pair, prepared)

    if durable_phase is ActivationPhase.ACTIVE:
        require_durable(
            persistence_worker.submit_activation_phase(
                prepared.transition(ActivationPhase.ACTIVE),
                expected_phase=ActivationPhase.PREPARED,
            )
        )
    if crash_boundary in (
        "after-active-authorization",
        "after-confidence-fallback-receipt",
        "after-operator-rollback-receipt",
    ):
        assert first_core.authorize_candidate_pair(prepared.transition(ActivationPhase.ACTIVE))
    if durable_phase is ActivationPhase.ABORTED:
        assert first_core.compensate_candidate_pair(
            candidate_pair,
            prepared,
            "compensated",
        )
        require_durable(
            persistence_worker.submit_activation_phase(
                prepared.transition(
                    ActivationPhase.ABORTED,
                    reason="compensated",
                ),
                expected_phase=ActivationPhase.PREPARED,
            )
        )

    if lifecycle_kind is EvidenceKind.FALLBACK:
        assert first_core.activation_runtime_failure("confidence-window-regressed")
        lifecycle_records = first_core.drain_activation_events()
        assert len(lifecycle_records) == 1
        assert isinstance(lifecycle_records[0].payload, FallbackEvidence)
        append_model_evidence(lifecycle_records, database_path=database_path)
    elif lifecycle_kind is EvidenceKind.ROLLBACK:
        lifecycle = ModelEvidenceRecord(
            evidence_id=f"rollback-{crash_boundary}",
            kind=EvidenceKind.ROLLBACK,
            session_id="crash-recovery",
            cook_id=None,
            timestamp_ms=2_000,
            role_generation=candidate.role_generation + 1,
            model_digest=candidate.model_digest,
            provenance_digest=incumbent.model_digest,
            payload=RollbackEvidence(
                decision_id=prepared.decision_id,
                reason="operator rollback",
            ),
        )
        rollback_authority = read_model_activation(database_path=database_path)
        assert rollback_authority is not None
        assert rollback_authority.phase == ActivationPhase.ACTIVE.value
        rollback_outcome = commit_model_rollback(
            lifecycle,
            expected_activation=rollback_authority,
            database_path=database_path,
        )
        assert rollback_outcome.inserted is True
        assert rollback_outcome.record == lifecycle
        first_boundary_gate = _CrashRecoveryRunnerGate()
        first_boundary_runner = ThreadedControllerRunner(
            first_core,
            wait_for_period=first_boundary_gate,
            monotonic_clock=_deterministic_monotonic(),
        )
        assert first_boundary_gate.wait_for_arrivals(1)
        assert first_boundary_runner.rollback_activation("operator rollback")
        first_boundary_gate.release()
        assert first_boundary_gate.wait_for_arrivals(2)

    expected_precrash_pair = candidate_pair if precrash_owner == "candidate" else incumbent_pair
    assert first_core.active_control_pair is expected_precrash_pair
    assert first_core.activation_output_authorized is precrash_authorized
    assert (first_core.rollback_control_pair is incumbent_pair) is precrash_has_rollback
    precrash_authority = read_model_activation(database_path=database_path)
    if durable_phase is None:
        assert precrash_authority is None
    else:
        assert precrash_authority is not None
        assert precrash_authority.phase == durable_phase.value
    precrash_records = tuple(read_model_evidence(database_path=database_path))
    if not installs_candidate:
        candidate_pair.close()
    if first_boundary_runner is None:
        first_core.close()
    else:
        assert first_boundary_gate is not None
        first_boundary_gate.open()
        first_boundary_runner.stop()
    first_runtime_handles = (
        incumbent_pair.estimator,
        incumbent_pair.solver,
        candidate_pair.estimator,
        candidate_pair.solver,
    )
    assert all(type(handle.closed) is int and handle.closed == 1 for handle in first_runtime_handles)

    monkeypatch.setattr(
        hold_module,
        "read_model_activation",
        lambda: read_model_activation(database_path=database_path),
    )
    monkeypatch.setattr(
        hold_module,
        "read_model_evidence",
        lambda: read_model_evidence(database_path=database_path),
    )
    core = MpcController(
        dict(MPC_DEFAULTS, enable_online_adaptation=False, control_period=0.001),
        "C",
        {"u_min": 0.1, "u_max": 0.9},
    )
    core._activation_persistence_worker = ModelPersistenceWorker(
        _FakeModelStore(),
        SimpleNamespace(error=lambda _message: None),
        append_evidence=lambda batch: append_model_evidence(
            batch,
            database_path=database_path,
        ),
        persist_activation_phase=lambda record, expected: commit_model_activation_phase(
            record,
            expected_phase=expected,
            database_path=database_path,
        ),
    )
    restart_gate = _CrashRecoveryRunnerGate()
    runner = ThreadedControllerRunner(
        core, wait_for_period=restart_gate, monotonic_clock=_deterministic_monotonic()
    )
    assert restart_gate.wait_for_arrivals(1)
    hold = hold_cycle(runner, model_store=_FakeModelStore(), controller="mpc")
    expected = candidate if durable_phase is ActivationPhase.ACTIVE and lifecycle_kind is None else incumbent
    try:
        hold.setup()
        restart_solve_offset = len(_CrashRecoverySolver.solve_order)
        runner.set_target(225.0)
        runner.submit(225.0)
        restart_gate.release()
        assert restart_gate.wait_for_arrivals(2, timeout=5.0)
        result = runner.latest()
        assert result.revision == 1
        assert 0.0 < result.cycle_ratio <= 0.5
        assert core.active_control_pair.descriptor == expected
        assert core.estimator is core.active_control_pair.estimator
        assert core.mpc is core.active_control_pair.solver
        assert _CrashRecoverySolver.solve_order[restart_solve_offset:] == [core.active_control_pair.solver]
        assert core.mpc.calls
        assert not runner.mpc_activation_terminated

        restored_pair = core.active_control_pair
        constructed_count = len(_CrashRecoverySolver.created)
        completed_revision = result.revision
        completed_solve_count = len(_CrashRecoverySolver.solve_order)
        if precrash_authority is not None:
            runner.submit(None)
            assert runner.restore_activation(
                precrash_authority,
                precrash_records,
            )
            assert runner.restore_activation(
                precrash_authority,
                precrash_records,
            )
            restart_gate.release()
            assert restart_gate.wait_for_arrivals(3, timeout=5.0)
            assert runner.latest().revision == completed_revision
            assert len(_CrashRecoverySolver.solve_order) == completed_solve_count
            assert core.active_control_pair is restored_pair
            assert len(_CrashRecoverySolver.created) == constructed_count
            assert not runner.mpc_activation_terminated

        converged = read_model_activation(database_path=database_path)
        if durable_phase is ActivationPhase.PREPARED:
            assert converged is not None
            assert converged.phase == ActivationPhase.ABORTED.value
            assert converged.active_pair == incumbent
        elif durable_phase is not None:
            assert converged is not None
            assert converged.phase == durable_phase.value
    finally:
        restart_gate.open()
        hold.ctx.clock.advance(400.0)
        hold.teardown(225.0)
    handles = (
        *_CrashRecoveryEstimator.created,
        *_CrashRecoverySolver.created,
    )
    assert handles
    assert all(type(handle.closed) is int and handle.closed == 1 for handle in handles)
