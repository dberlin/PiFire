"""Hold asks the controller to refit when the cook ends -- and never during.

Also covers the two runner surfaces the request travels through, since a
refit that never reaches the store changes nothing: the NEXT cook's restore is
what puts a learned model on the grill.
"""

import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest
from common.controller_model_state import CheckpointSaveOutcome

from common.model_evidence import CandidateAssessmentEvidence, ConfidenceDecisionEvidence
from common.control_trace import ActuationMode

from controller.applied_output import AppliedOutput, OutputSource
from controller.model_learning.contracts import (
    CandidateOrigin,
    FrameObservation,
)
from controller.runtime.model_fitting import (
    PassiveGreyHistory,
    TeardownGreyHistory,
    TeardownRefitOutcome,
    TeardownRefitResult,
)
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

    def save_outcome(self, name, snapshot):
        self.saved.append((name, snapshot))
        return CheckpointSaveOutcome.SAVED


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


def test_online_adaptation_without_identification_checkpoints_before_trace_close(hold_cycle, monkeypatch):
    import controller.runtime.modes.hold as hold_module
    events = []

    class _CloseRecorder:
        def record(self, record):
            raise AssertionError(f"trace record was not expected: {record.event_kind}")

        def flush_due(self, now_ms):
            raise AssertionError(f"trace flush was not expected: {now_ms}")

        def close(self):
            events.append("close")

    class _OrderedStore(_RecordingStore):
        def save_outcome(self, name, snapshot):
            events.append("save")
            return super().save_outcome(name, snapshot)

    runner = FakeControllerRunner(period=0.01)
    runner.snapshot = {"version": 1, "revision": 7, "params": {}, "online_adaptation": {}}
    store = _OrderedStore()
    monkeypatch.setattr(
        hold_module,
        "ControlTraceRecorder",
        lambda *, warning: _CloseRecorder(),
    )
    hold = _hold(hold_cycle, runner, identification=False, online_adaptation=True, store=store)

    hold.teardown(225)

    assert runner.stops_before_each_refit == []
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


def test_online_adaptation_never_invokes_refit_when_identification_is_disabled(hold_cycle):
    store = _RecordingStore()
    runner = FakeControllerRunner(period=0.01)
    runner.refit_raises = RuntimeError("solver exploded")
    runner.snapshot = {"version": 1, "revision": 8, "params": {}, "online_adaptation": {}}
    hold = _hold(hold_cycle, runner, identification=False, online_adaptation=True, store=store)

    hold.teardown(225)

    assert runner.stops_before_each_refit == []
    assert store.saved == [("mpc", runner.snapshot)]


def test_recorder_construction_failure_still_restores_the_final_disabled_checkpoint_once(hold_cycle, monkeypatch):
    """A trace outage cannot discard learning that finished after the last evaluation."""

    class _RestoringStore:
        def __init__(self):
            self.models = {}
            self.saved = []

        def load(self, name):
            return self.models.get(name)

        def save_outcome(self, name, snapshot):
            persisted = dict(snapshot)
            self.models[name] = persisted
            self.saved.append((name, persisted))
            return CheckpointSaveOutcome.SAVED

    class _FinalizingRunner(FakeControllerRunner):
        def refit_from_cook(self):
            super().refit_from_cook()
            self.snapshot = final_snapshot
            return object()

    def recorder_unavailable(**_kwargs):
        raise OSError("trace database unavailable")

    import controller.runtime.modes.hold as hold_module

    final_snapshot = {
        "version": 1,
        "revision": 8,
        "coefficients": {"a": -0.92, "b": 0.037, "c": 1.25},
        "online_adaptation": {},
    }
    store = _RestoringStore()
    runner = _FinalizingRunner(period=0.0).script(
        [ControllerUpdateResult(cycle_ratio=0.4, fan=None, input_temperature=225.0)]
    )
    monkeypatch.setattr(hold_module, "ControlTraceRecorder", recorder_unavailable)
    hold = _hold(hold_cycle, runner, identification=False, online_adaptation=True, store=store)
    runner.snapshot = {
        "version": 1,
        "revision": 7,
        "coefficients": {"a": -0.85, "b": 0.031, "c": 1.1},
        "online_adaptation": {},
    }
    hold.ctx.clock.advance(100.0)
    hold.on_tick(100.0, 225.0, {"auger": False, "fan": False, "igniter": False, "power": False, "pwm": 100})
    hold.teardown(225.0)

    restarted_runner = FakeControllerRunner(period=0.01)
    restarted = hold_cycle(restarted_runner, controller="mpc", model_store=store)
    restarted.setup()

    assert runner.refits == 0
    assert [snapshot for _name, snapshot in store.saved].count(runner.snapshot) == 1
    assert restarted_runner.restored == [runner.snapshot]


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

def test_hold_warns_and_skips_refit_when_runner_reports_stop_timeout(
    hold_cycle,
) -> None:
    runner = FakeControllerRunner(period=0.01)
    hold = _hold(hold_cycle, runner, identification=True)
    refits_before = len(runner.finalized_refits)
    warnings = []
    hold.ctx.control_log = SimpleNamespace(warning=warnings.append)
    runner.stop_for_refit = lambda: False

    hold.teardown(225)

    assert len(runner.finalized_refits) == refits_before
    assert warnings == [
        "Controller worker did not stop; final checkpoint was not queued"
    ]


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


def _completed_frame(sequence: int, *, probe: bool = False) -> FrameObservation:
    start = float(sequence - 1)
    probe_q = 0.1 if probe else 0.0
    return FrameObservation(
        frame_start_s=start,
        frame_end_s=start + 1.0,
        temp_c=100.0 + sequence,
        setpoint_c=120.0,
        ambient_c=20.0,
        requested_q=0.3 + probe_q,
        realized_q=0.3 + probe_q,
        requested_auger_duty=0.3 + probe_q,
        delivered_on_s=0.3 + probe_q,
        requested_fan_duty=None,
        actual_fan_duty=None,
        result_revision=sequence,
        output_source=OutputSource.CONTROLLER.value,
        lid_open=False,
        safety_inhibited=False,
        manual_override=False,
        stale=False,
        skipped=False,
        reset=False,
        continuous=True,
        role_generation=0,
        observation_sequence=sequence,
        baseline_q=0.3,
        probe_q=probe_q,
        calibration_stage="low" if probe else None,
        calibration_fit=probe,
    )


def test_complete_teardown_window_keeps_probe_frames_and_uses_most_restrictive_origin():
    history = TeardownGreyHistory(role_generation=0, max_observations=4)

    assert history.observe(_completed_frame(1)).accepted
    assert history.observe(_completed_frame(2, probe=True)).accepted
    assert history.observe(_completed_frame(3)).accepted

    assert [frame.observation_sequence for frame in history.observations] == [1, 2, 3]
    assert history.origin is CandidateOrigin.OPERATOR_CALIBRATION


def test_probe_frame_is_excluded_from_passive_online_validation_without_being_lost_from_teardown():
    probe = _completed_frame(1, probe=True)
    passive = PassiveGreyHistory(role_generation=0)
    teardown = TeardownGreyHistory(role_generation=0)

    assert passive.observe(probe).reasons == ("calibration-frame",)
    assert passive.observations == ()
    assert teardown.observe(probe).accepted
    assert teardown.observations == (probe,)




class _FinalLifecycleRunner(FakeControllerRunner):
    def __init__(self, *, result=None, error=None):
        super().__init__(period=0.01)
        self.refit_verdict = result
        self.refit_raises = error
        self.snapshot = {"version": 1, "revision": 1, "params": {}}
        self.final_outcomes = []
        self.lifecycle = []

    def stop_for_refit(self):
        self.lifecycle.append("join")
        self.stop()

    def finalize_cook_refit(self, outcome):
        normalized = outcome if isinstance(outcome, TeardownRefitOutcome) else TeardownRefitOutcome(outcome)
        self.final_outcomes.append(normalized)
        self.snapshot = {
            **self.snapshot,
            "revision": self.snapshot["revision"] + 1,
            "cook_refit": {"status": "idle", "latest": normalized.value},
        }
        return True

    def finish_teardown(self):
        self.lifecycle.append("close")


@pytest.mark.parametrize(
    ("identification", "result", "error", "expected"),
    (
        (False, None, None, TeardownRefitOutcome.DISABLED),
        (
            True,
            TeardownRefitResult.insufficient("minimum-samples"),
            None,
            TeardownRefitOutcome.INSUFFICIENT,
        ),
        (
            True,
            TeardownRefitResult.rejected("physical-bounds", origin=CandidateOrigin.COOK_REFIT),
            None,
            TeardownRefitOutcome.REJECTED,
        ),
        (
            True,
            TeardownRefitResult.ready_for_review("accepted", candidate_digest="a" * 64),
            None,
            TeardownRefitOutcome.READY_FOR_REVIEW,
        ),
        (
            True,
            TeardownRefitResult.accepted_next_cook("accepted", candidate_digest="b" * 64),
            None,
            TeardownRefitOutcome.ACCEPTED_NEXT_COOK,
        ),
        (True, None, RuntimeError("fit exploded"), TeardownRefitOutcome.FAILED),
    ),
)
def test_teardown_emits_one_final_checkpoint_for_every_refit_outcome(
    hold_cycle,
    identification,
    result,
    error,
    expected,
):
    store = _RecordingStore()
    runner = _FinalLifecycleRunner(result=result, error=error)
    hold = _hold(
        hold_cycle,
        runner,
        identification=identification,
        online_adaptation=True,
        store=store,
    )
    stops_before_teardown = runner.stops
    lifecycle_before_teardown = len(runner.lifecycle)

    hold.teardown(225)
    hold.teardown(225)

    assert runner.refits == int(identification)
    assert runner.final_outcomes == [expected]
    assert runner.stops - stops_before_teardown == 1
    assert runner.lifecycle[lifecycle_before_teardown:] == ["join", "close"]
    assert len(store.saved) == 1
    assert store.saved[0][1]["cook_refit"]["latest"] == expected.value


def test_final_checkpoint_failure_is_terminal_and_is_not_retried(hold_cycle):
    class _FailingStore(_RecordingStore):
        def save_outcome(self, name, snapshot):
            self.saved.append((name, snapshot))
            return CheckpointSaveOutcome.FAILED

    store = _FailingStore()
    runner = _FinalLifecycleRunner(result=TeardownRefitResult.accepted_next_cook("accepted", candidate_digest="c" * 64))
    hold = _hold(hold_cycle, runner, identification=True, store=store)

    hold.teardown(225)
    hold.teardown(225)

    assert len(store.saved) == 1
    assert runner.final_outcomes == [
        TeardownRefitOutcome.ACCEPTED_NEXT_COOK,
        TeardownRefitOutcome.CHECKPOINT_FAILURE,
    ]


def test_production_teardown_retries_only_authoritative_checkpoint_before_flush_and_close(
    hold_cycle, monkeypatch
):
    import controller.runtime.modes.hold as hold_module

    runner = _FinalLifecycleRunner(
        result=TeardownRefitResult.accepted_next_cook(
            "accepted",
            candidate_digest="d" * 64,
        )
    )

    class _Queue:
        failed = False
        evidence_blocked = False

        def __init__(self):
            self.attempts = []

        def submit_checkpoint(self, _name, snapshot):
            runner.lifecycle.append(
                f"checkpoint:{snapshot['cook_refit']['latest']}"
            )
            self.attempts.append(snapshot)
            return len(self.attempts) > 1

        def submit_evidence_batch(self, _records):
            raise AssertionError("evidence submission was not expected")

        def flush_and_stop(self):
            runner.lifecycle.append("flush")
            return True

    queue = _Queue()
    monkeypatch.setattr(
        hold_module,
        "ModelPersistenceWorker",
        lambda _store, _logger: queue,
    )
    hold = _hold(hold_cycle, runner, identification=True)

    hold.teardown(225)

    assert [snapshot["cook_refit"]["latest"] for snapshot in queue.attempts] == [
        "accepted-next-cook",
        "checkpoint-failure",
    ]
    assert runner.final_outcomes[-1] is TeardownRefitOutcome.CHECKPOINT_FAILURE
    assert (
        runner.lifecycle.index("checkpoint:checkpoint-failure")
        < runner.lifecycle.index("flush")
    )
    assert runner.lifecycle.index("flush") < runner.lifecycle.index("close")


def test_finalize_exception_never_queues_a_stale_snapshot(
    hold_cycle,
    monkeypatch,
):
    import controller.runtime.modes.hold as hold_module

    class _ExplodingFinalizeRunner(_FinalLifecycleRunner):
        def finalize_cook_refit(self, outcome):
            if outcome is not TeardownRefitOutcome.CHECKPOINT_FAILURE:
                raise RuntimeError("finalize exploded")
            return super().finalize_cook_refit(outcome)

    runner = _ExplodingFinalizeRunner()
    queued = []
    persistence = SimpleNamespace(
        failed=False,
        evidence_blocked=False,
        submit_checkpoint=lambda _name, snapshot: (
            queued.append(snapshot),
            True,
        )[1],
        submit_evidence_batch=lambda _records: None,
        flush_and_stop=lambda: True,
    )
    monkeypatch.setattr(
        hold_module,
        "ModelPersistenceWorker",
        lambda _store, _logger: persistence,
    )
    hold = _hold(hold_cycle, runner, identification=True)

    hold.teardown(225)

    assert len(queued) == 1
    assert queued[0]["cook_refit"]["latest"] == "checkpoint-failure"


class _RetainedRefitCore:
    def __init__(self):
        self.events = []
        self.refit_started = threading.Event()
        self.release_refit = threading.Event()

    def get_control_period(self):
        return 0.01

    def commands_fan(self):
        return False

    def actuation_mode(self):
        return ActuationMode.FRAMED_PULSE

    def get_status(self):
        return {}

    def get_model_snapshot(self):
        return {"version": 1, "revision": 1, "params": {}}

    def refit_from_cook(self):
        self.events.append("fit")
        self.refit_started.set()
        self.release_refit.wait(timeout=1.0)
        return TeardownRefitResult.insufficient("minimum-samples")

    def close(self):
        self.events.append("close")


def test_threaded_teardown_joins_then_refits_while_latest_remains_nonblocking_then_closes():
    core = _RetainedRefitCore()
    runner = ThreadedControllerRunner(core)
    runner.stop_for_refit()

    assert not runner._thread.is_alive()
    assert core.events == []

    refit_thread = threading.Thread(target=runner.refit_from_cook)
    refit_thread.start()
    assert core.refit_started.wait(timeout=1.0)

    latest_done = threading.Event()
    threading.Thread(target=lambda: (runner.latest(), latest_done.set())).start()
    assert latest_done.wait(timeout=0.2), "latest() blocked behind teardown fitting"

    core.release_refit.set()
    refit_thread.join(timeout=1.0)
    runner.finish_teardown()
    runner.finish_teardown()

    assert core.events == ["fit", "close"]


class _StepGate:
    def __init__(self):
        self._condition = threading.Condition()
        self._permits = 0
        self._arrivals = 0
        self._open = False

    def __call__(self, _period):
        with self._condition:
            self._arrivals += 1
            self._condition.notify_all()
            self._condition.wait_for(lambda: self._permits > 0 or self._open, timeout=1.0)
            if self._permits:
                self._permits -= 1

    def wait_for_arrivals(self, count):
        with self._condition:
            return self._condition.wait_for(lambda: self._arrivals >= count, timeout=1.0)

    def release(self):
        with self._condition:
            self._permits += 1
            self._condition.notify_all()

    def close(self):
        with self._condition:
            self._open = True
            self._condition.notify_all()


class _CloseHandle:
    def close(self):
        pass


class _ActivationOrderingCore(_CoreWithoutRefit):
    def __init__(self):
        self.events = []
        self.installed = threading.Event()
        self.activation_terminated = False
        self._activation_queued = False

    def update(self, _temperature):
        self.events.append("solve")
        return 0.2

    def set_output(self, _applied):
        self.events.append("feedback")

    def observe_frame(self, observation):
        self.events.append(("observation", observation.role_generation))
        return {"role_generation": observation.role_generation, "eligible": True}

    def queue_activation(self):
        self._activation_queued = True

    def advance_activation(self):
        if self._activation_queued:
            self._activation_queued = False
            self.events.append("install")
            self.installed.set()
        return True

    def restore_activation(self, _persisted, _records):
        return False

    def activation_runtime_failure(self, _reason):
        return False

    def rollback_activation(self, _reason):
        return False

    def drain_activation_events(self):
        return ()

    def submit_activation_confidence(self, _record):
        return None

    def terminate_mpc_activation(self, _reason):
        self.activation_terminated = True


def test_completed_frame_feedback_and_observation_reach_incumbent_before_activation_install():

    core = _ActivationOrderingCore()
    gate = _StepGate()
    runner = ThreadedControllerRunner(core, wait_for_period=gate)
    try:
        assert gate.wait_for_arrivals(1)
        runner.submit(100.0)
        gate.release()
        assert gate.wait_for_arrivals(2)
        assert runner.latest().revision == 1

        core.queue_activation()
        submission = runner.complete_frame(
            AppliedOutput(
                ratio=0.3,
                source=OutputSource.CONTROLLER,
                timestamp=1.0,
            ),
            _completed_frame(1),
        )
        assert submission.configuration_generation == 0

        gate.release()
        assert core.installed.wait(timeout=1.0)

        assert core.events.index("feedback") < core.events.index(("observation", 0))
        assert core.events.index(("observation", 0)) < core.events.index("install")
    finally:
        runner.stop()




