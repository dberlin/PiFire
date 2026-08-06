"""Hold restores a controller's model at setup and saves it as it changes."""

from common.control_trace import ActuationMode
from common.controller_model_state import ControllerModelStore

from controller.applied_output import OutputSource
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
    runner.snapshot = {"revision": 1, "K": 700.0}
    hold.on_tick(now=100.0, ptemp=200.0, current_output_status=_off())
    assert store.saves == [("pid_sp", {"revision": 1, "K": 700.0})]


def test_framed_ticks_persist_only_advancing_model_revisions(hold_cycle):
    runner = FakeControllerRunner(period=0.0, actuation_mode=ActuationMode.FRAMED_PULSE).script([_framed_output()])
    store, writes = _deduplicating_store()
    hold = hold_cycle(runner, model_store=store, controller="mpc")
    hold.setup()

    runner.snapshot = {"revision": 1, "params": {}}
    hold.on_tick(now=100.0, ptemp=200.0, current_output_status=_off())
    hold.on_tick(now=101.0, ptemp=200.0, current_output_status=_off())
    runner.snapshot = {"revision": 2, "params": {}}
    hold.on_tick(now=122.0, ptemp=200.0, current_output_status=_off())

    assert [record["models"]["mpc"]["revision"] for record in writes] == [1, 2]


def test_framed_ticks_leave_malformed_snapshots_to_the_model_store(hold_cycle):
    runner = FakeControllerRunner(period=0.0, actuation_mode=ActuationMode.FRAMED_PULSE).script([_framed_output()])
    store, writes = _deduplicating_store()
    hold = hold_cycle(runner, model_store=store, controller="mpc")
    hold.setup()

    runner.snapshot = {"revision": 1, "params": {"non_finite": float("nan")}}
    hold.on_tick(now=100.0, ptemp=200.0, current_output_status=_off())

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
    runner.snapshot = {"revision": 1, "K": 700.0}
    hold.on_tick(now=100.0, ptemp=200.0, current_output_status=_off())
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
