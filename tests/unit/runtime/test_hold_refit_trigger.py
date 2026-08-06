"""Hold asks the controller to refit when the cook ends -- and never during.

Also covers the two runner surfaces the request travels through, since a
refit that never reaches the store changes nothing: the NEXT cook's restore is
what puts a learned model on the grill.
"""

import pytest
from common.control_trace import ActuationMode

from controller.runtime.runner import (
    ControllerRunner,
    ControllerUpdateResult,
    SyncControllerRunner,
    ThreadedControllerRunner,
)
from tests.fakes.runner import FakeControllerRunner


class _RecordingStore:
    def __init__(self):
        self.saved = []

    def load(self, name):
        return None

    def save(self, name, snapshot):
        self.saved.append((name, snapshot))
        return True


def _hold(
    hold_cycle,
    runner,
    *,
    identification,
    online_adaptation=False,
    store=None,
    controller="mpc",
    configure="mpc",
):
    hold = hold_cycle(runner, controller=controller, model_store=store)
    hold.settings["controller"]["config"][configure] = {
        "enable_identification": identification,
        "enable_online_adaptation": online_adaptation,
    }
    hold.setup()
    return hold


def test_teardown_requests_a_refit(hold_cycle):
    runner = FakeControllerRunner(period=0.01)
    hold = _hold(hold_cycle, runner, identification=True)
    hold.teardown(225)
    assert runner.refits == 1


def test_no_refit_when_identification_is_off(hold_cycle):
    runner = FakeControllerRunner(period=0.01)
    hold = _hold(hold_cycle, runner, identification=False)
    hold.teardown(225)
    assert runner.refits == 0


def test_online_adaptation_checkpoints_and_persists_before_trace_close(hold_cycle, monkeypatch):
    events = []

    class _CloseRecorder:
        def close(self):
            events.append("close")

    class _OrderedStore(_RecordingStore):
        def save(self, name, snapshot):
            events.append("save")
            return super().save(name, snapshot)

    runner = FakeControllerRunner(period=0.01)
    runner.snapshot = {"version": 1, "revision": 7, "params": {}, "online_adaptation": {}}
    store = _OrderedStore()
    hold = _hold(hold_cycle, runner, identification=False, online_adaptation=True, store=store)
    hold._trace_recorder = _CloseRecorder()
    monkeypatch.setattr(hold, "_trace_record", lambda *_args: True)

    hold.teardown(225)

    assert runner.stops_before_each_refit == [1]
    assert store.saved == [("mpc", runner.snapshot)]
    assert events == ["save", "close"]


def test_no_refit_during_the_cook(hold_cycle):
    runner = FakeControllerRunner(period=0.01).script(
        [ControllerUpdateResult(cycle_ratio=0.3, fan=None, input_temperature=0.0)] * 10
    )
    hold = _hold(hold_cycle, runner, identification=True)
    for tick in range(5):
        hold.on_tick(
            now=100.0 + tick,
            ptemp=225.0,
            current_output_status={"auger": False, "fan": False, "igniter": False, "power": False, "pwm": 100},
        )
    assert runner.refits == 0


def test_the_refit_waits_for_the_runner_to_stop(hold_cycle):
    """A refit mutates the same core a background solve reads, so it may only
    run once the worker has been joined."""
    runner = FakeControllerRunner(period=0.01)
    hold = _hold(hold_cycle, runner, identification=True)
    hold.teardown(225)
    assert runner.stops_before_each_refit == [1]


def test_the_gate_follows_the_selected_controller(hold_cycle):
    """Identification is enabled under `mpc`, but a PID grill is running: the
    setting belongs to the controller it names, not to whatever is loaded."""
    runner = FakeControllerRunner(period=0.01)
    hold = _hold(hold_cycle, runner, identification=True, controller="pid_sp", configure="mpc")
    hold.teardown(225)
    assert runner.refits == 0


def test_settings_that_lost_their_shape_do_not_break_teardown(hold_cycle):
    runner = FakeControllerRunner(period=0.01)
    hold = _hold(hold_cycle, runner, identification=True)
    hold.settings = {}
    hold.teardown(225)  # must not raise
    assert runner.stops == 1
    assert runner.refits == 0


def test_a_refit_failure_does_not_break_teardown(hold_cycle):
    """Teardown runs on the way out of a cook. A refit is a nicety; losing the
    orderly shutdown is not."""
    runner = FakeControllerRunner(period=0.01)
    runner.refit_raises = RuntimeError("solver exploded")
    hold = _hold(hold_cycle, runner, identification=True)
    hold.teardown(225)  # must not raise
    assert runner.stops == 1


def test_missing_checkpoint_does_not_suppress_a_base_refit_exception(hold_cycle):
    runner = FakeControllerRunner(period=0.01)
    runner.refit_raises = KeyboardInterrupt()
    hold = _hold(hold_cycle, runner, identification=True)

    with pytest.raises(KeyboardInterrupt):
        hold.teardown(225)


def test_exceptional_online_refit_still_persists_the_published_checkpoint(hold_cycle):
    store = _RecordingStore()
    runner = FakeControllerRunner(period=0.01)
    runner.refit_raises = RuntimeError("solver exploded")
    runner.snapshot = {"version": 1, "revision": 8, "params": {}, "online_adaptation": {}}
    hold = _hold(hold_cycle, runner, identification=False, online_adaptation=True, store=store)

    hold.teardown(225)

    assert runner.stops_before_each_refit == [1]
    assert store.saved == [("mpc", runner.snapshot)]


def test_what_the_refit_learned_is_persisted_for_the_next_cook(hold_cycle):
    store = _RecordingStore()
    runner = FakeControllerRunner(period=0.01)
    hold = _hold(hold_cycle, runner, identification=True, store=store)
    runner.snapshot = {"version": 1, "revision": 7, "params": {}}
    hold.teardown(225)
    assert [snapshot for _name, snapshot in store.saved] == [runner.snapshot]


def test_online_checkpoint_is_persisted_when_identification_rejects(hold_cycle):
    store = _RecordingStore()
    runner = FakeControllerRunner(period=0.01)
    runner.refit_verdict = object()
    runner.snapshot = {"version": 1, "revision": 8, "params": {}, "online_adaptation": {}}
    hold = _hold(hold_cycle, runner, identification=True, online_adaptation=True, store=store)

    hold.teardown(225)

    assert runner.stops_before_each_refit == [1]
    assert store.saved == [("mpc", runner.snapshot)]


def test_nothing_is_persisted_when_the_controller_learned_nothing(hold_cycle):
    store = _RecordingStore()
    runner = FakeControllerRunner(period=0.01)
    hold = _hold(hold_cycle, runner, identification=True, store=store)
    runner.snapshot = None
    hold.teardown(225)
    assert store.saved == []


# ---- the runner surface the request travels through ----


class _CoreWithoutRefit:
    def get_control_period(self):
        return 0.01

    def commands_fan(self):
        return False

    def actuation_mode(self):
        return ActuationMode.FRAMED_PULSE

    def get_status(self):
        return {}

    def get_model_snapshot(self):
        return None


class _CoreWithRefit(_CoreWithoutRefit):
    def __init__(self):
        self.refits = 0
        self.snapshot = None

    def refit_from_cook(self):
        self.refits += 1
        self.snapshot = {"version": 1, "revision": 3, "params": {}}
        return "verdict"

    def get_model_snapshot(self):
        return self.snapshot



class _CoreWithExceptionalRefit(_CoreWithRefit):
    def refit_from_cook(self):
        self.refits += 1
        self.snapshot = {"version": 1, "revision": 4, "params": {}}
        raise RuntimeError("refit failed after checkpoint")

def test_the_sync_runner_delegates_a_refit_to_its_core():
    core = _CoreWithRefit()
    assert SyncControllerRunner(core).refit_from_cook() == "verdict"
    assert core.refits == 1


def test_a_controller_that_cannot_refit_is_not_an_error():
    assert SyncControllerRunner(_CoreWithoutRefit()).refit_from_cook() is None


def test_the_threaded_runner_republishes_the_snapshot_a_refit_produced():
    """The worker normally publishes it, and by teardown the worker is gone --
    so an adopted model would otherwise be invisible to the caller that
    persists it."""
    core = _CoreWithRefit()
    runner = ThreadedControllerRunner(core)
    runner.stop()
    assert runner.get_model_snapshot() is None
    runner.refit_from_cook()
    assert runner.get_model_snapshot() == {"version": 1, "revision": 3, "params": {}}



def test_threaded_runner_republishes_snapshot_when_refit_raises():
    core = _CoreWithExceptionalRefit()
    runner = ThreadedControllerRunner(core)
    runner.stop()

    with pytest.raises(RuntimeError, match="after checkpoint"):
        runner.refit_from_cook()

    assert runner.get_model_snapshot() == {"version": 1, "revision": 4, "params": {}}

def test_a_worker_that_would_not_stop_refuses_the_refit_out_loud():
    """`stop()` joins with a timeout, so it cannot promise the worker is gone.
    A worker still running would overwrite the republished snapshot on its
    next pass and the cook's learning would disappear without a trace."""
    core = _CoreWithRefit()
    runner = ThreadedControllerRunner(core)
    try:
        with pytest.raises(RuntimeError, match="did not stop"):
            runner.refit_from_cook()  # the worker is still running
        assert core.refits == 0
    finally:
        runner.stop()


def test_a_refit_that_refuses_still_reaches_the_operator(hold_cycle):
    """Hold logs what escapes the runner, so the refusal above is not silent."""
    runner = FakeControllerRunner(period=0.01)
    runner.refit_raises = RuntimeError("the controller worker did not stop")
    hold = _hold(hold_cycle, runner, identification=True)
    import control as _control

    logged = []
    original = _control.eventLogger.error
    _control.eventLogger.error = logged.append
    try:
        hold.teardown(225)
    finally:
        _control.eventLogger.error = original
    assert any("did not stop" in line for line in logged)


def test_a_runner_that_forgets_to_refit_cannot_be_built():
    """The ABC is what makes the next runner implement this, rather than
    inherit a silent no-op that loses every cook's evidence."""
    everything_else = {
        name: (lambda self, *args, **kwargs: None)
        for name in ControllerRunner.__abstractmethods__
        if name != "refit_from_cook"
    }
    incomplete = type("Incomplete", (ControllerRunner,), everything_else)
    with pytest.raises(TypeError, match="refit_from_cook"):
        incomplete()
