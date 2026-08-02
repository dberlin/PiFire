"""Hold restores a controller's model at setup and saves it as it changes."""

from controller.applied_output import OutputSource
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


def test_setup_restores_a_stored_model_before_seeding(hold_cycle):
    runner = FakeControllerRunner(period=0.01)
    store = _FakeModelStore({"pid_sp": {"revision": 3, "K": 700.0}})
    hold = hold_cycle(runner, model_store=store, controller="pid_sp")
    hold.setup()
    assert runner.restored == [{"revision": 3, "K": 700.0}]
    # the seed report comes after the restore, so it lands on the restored model
    assert [a.source for a in runner.applied] == [OutputSource.SEED]


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


def test_reconfigure_restores_the_model_and_reseeds(hold_cycle):
    runner = FakeControllerRunner(period=0.0).script([_output(0.5)])
    store = _FakeModelStore({"pid_sp": {"revision": 3, "K": 700.0}})
    hold = hold_cycle(runner, model_store=store, controller="pid_sp")
    hold.setup()
    runner.restored.clear()
    runner.applied.clear()
    hold.control["controller_update"] = True
    hold.on_tick(now=100.0, ptemp=200.0, current_output_status=_off())
    assert runner.restored == [{"revision": 3, "K": 700.0}]
    assert runner.applied[0].source is OutputSource.SEED
