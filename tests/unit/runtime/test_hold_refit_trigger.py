"""Hold schedules persistent-corpus fits without owning synchronous fitting."""

import threading
from collections.abc import Callable
from types import SimpleNamespace

import pytest

from common.control_trace import ActuationMode
from controller.applied_output import AppliedOutput, OutputSource
from controller.base import ControllerLearningDiagnostics
from controller.model_learning.calibration import (
    CalibrationDecision,
    CalibrationEvent,
    CalibrationProgress,
)
from controller.model_learning.contracts import CandidateOrigin, FrameObservation
from controller.mpc import Controller as MpcController
from controller.runtime.modes.hold_learning import HoldLearningRuntime
from controller.runtime.runner import (
    ControllerRunner,
    SyncControllerRunner,
    ThreadedControllerRunner,
)
from tests.fakes.runner import FakeControllerRunner


class _PersistenceBarrier:
    failed = False
    evidence_blocked = False

    def __init__(self, events):
        self.events = events
        self.checkpoints = []

    def submit_checkpoint(self, name, snapshot):
        self.checkpoints.append((name, snapshot))
        return True

    def submit_evidence_batch(self, records):
        return SimpleNamespace(accepted=True, records=tuple(records))

    def barrier(self, timeout=2.0):
        assert timeout == 2.0
        self.events.extend(("finalize", "barrier"))
        return True


class _CorpusFitRunner(FakeControllerRunner):
    def __init__(self, events, *, schedule_error=None):
        super().__init__(period=0.01)
        self.events = events
        self.fit_requests = []
        self.legacy_refits = 0
        self.legacy_finalizations = 0
        self.schedule_error = schedule_error
        self.snapshot = {
            "version": 4,
            "revision": 7,
            "identities": {"active_digest": "a" * 64, "rollback_digest": "b" * 64},
        }

    def stop_and_retain_for_teardown(self):
        self.events.append("stop")
        self.stop()

    def schedule_corpus_fit(self, origin):
        return self._schedule_corpus_fit_after_barrier(origin, None)

    def _schedule_corpus_fit_after_barrier(self, origin, before_schedule):
        if self.schedule_error is not None:
            raise self.schedule_error
        if before_schedule is not None and not before_schedule():
            return False
        self.events.append("schedule")
        self.fit_requests.append(origin)
        return True

    def refit_from_cook(self):
        self.legacy_refits += 1
        raise RuntimeError("volatile cook refit must not be called")

    def finalize_cook_refit(self, _outcome):
        self.legacy_finalizations += 1
        raise RuntimeError("legacy cook-refit finalization must not be called")

    def finish_teardown(self):
        self.events.append("close")
        super().finish_teardown()

    def adopt_model(self, *_args, **_kwargs):
        raise AssertionError("Stop must not adopt a fitted model")

    def handoff_candidate(self, *_args, **_kwargs):
        raise AssertionError("Stop must not hand off a fitted model")


def _hold(
    hold_cycle,
    runner,
    *,
    identification,
    online_adaptation=False,
    persistence=None,
    controller="mpc",
):
    hold = hold_cycle(runner, controller=controller)
    hold.settings["controller"]["config"][controller] = {
        "enable_identification": identification,
        "enable_online_adaptation": online_adaptation,
    }
    if persistence is not None:
        hold.ctx.model_persistence = persistence
        hold.ctx.trajectory_repository = object()
    hold.setup()
    return hold


@pytest.mark.parametrize(
    ("identification", "online_adaptation", "scheduled"),
    (
        (False, False, False),
        (False, True, False),
        (True, False, True),
        (True, True, True),
    ),
)
def test_stop_corpus_fit_gate_follows_identification_not_online_adaptation(
    hold_cycle,
    identification,
    online_adaptation,
    scheduled,
) -> None:
    events = []
    persistence = _PersistenceBarrier(events)
    runner = _CorpusFitRunner(events)
    hold = _hold(
        hold_cycle,
        runner,
        identification=identification,
        online_adaptation=online_adaptation,
        persistence=persistence,
    )

    result = hold.teardown(225.0)

    assert runner.fit_requests == ([CandidateOrigin.PASSIVE_ONLINE] if scheduled else [])
    assert runner.legacy_refits == 0
    assert runner.legacy_finalizations == 0
    assert result is None


def test_stop_finalizes_and_barriers_before_submit_then_closes_without_adoption(
    hold_cycle,
) -> None:
    events = []
    persistence = _PersistenceBarrier(events)
    runner = _CorpusFitRunner(events)
    authority = runner.get_model_snapshot()
    hold = _hold(
        hold_cycle,
        runner,
        identification=True,
        online_adaptation=False,
        persistence=persistence,
    )

    hold.teardown(225.0)
    hold.teardown(225.0)

    assert events == ["stop", "finalize", "barrier", "schedule", "close"]
    assert runner.fit_requests == [CandidateOrigin.PASSIVE_ONLINE]
    assert runner.get_model_snapshot() == authority
    assert persistence.checkpoints == []
    assert runner.legacy_refits == 0
    assert runner.legacy_finalizations == 0


def test_stop_fit_waits_for_trajectory_publication_and_quarantine_barrier(
    hold_cycle,
    monkeypatch,
) -> None:
    events: list[str] = []
    persistence = _PersistenceBarrier(events)
    runner = _CorpusFitRunner(events)
    hold = _hold(
        hold_cycle,
        runner,
        identification=True,
        persistence=persistence,
    )
    trajectory = hold.ctx.learning_trajectory
    assert trajectory is not None

    def trajectory_barrier(timeout: float = 2.0) -> bool:
        assert timeout == 2.0
        events.append("trajectory-barrier")
        return False

    monkeypatch.setattr(
        trajectory,
        "barrier",
        trajectory_barrier,
    )

    hold.teardown(225.0)

    assert "trajectory-barrier" in events
    assert runner.fit_requests == []


def test_mpc_start_command_authorizes_collection_without_scheduling_a_fit():
    controller = object.__new__(MpcController)
    commands = []
    controller._calibration = SimpleNamespace(
        request=commands.append,
    )
    controller._grey_learning_runtime = SimpleNamespace(
        request_corpus_fit=lambda _origin: pytest.fail(
            "calibration start must not schedule before collection completes"
        ),
    )
    command = SimpleNamespace(action="start")

    MpcController.request_calibration(controller, command)

    assert commands == [command]


def _calibration_decision(
    *,
    active,
    outcome=None,
    event=None,
    generation=1,
) -> CalibrationDecision:
    events = () if event is None else (CalibrationEvent(event, "low", 0.1, 0.1, 0.0),)
    return CalibrationDecision(
        active=active,
        probe_q=0.1 if active else 0.0,
        stage="low" if active else None,
        progress=CalibrationProgress(),
        events=events,
        command_revision=7,
        command_action="start",
        command_generation=generation,
        completed_stages=("low", "middle", "high") if outcome == "completed" else (),
        outcome=outcome,
    )


def test_calibration_fit_waits_for_completed_collection_and_durable_trajectory():
    events = []
    persistence = _PersistenceBarrier(events)
    runner = _CorpusFitRunner(events)
    learning = HoldLearningRuntime(
        runner=runner,
        model_store=None,
        persistence=persistence,
        trace=None,
        controller_name="mpc",
        logger=SimpleNamespace(
            info=lambda _message: None,
            warning=lambda _message: None,
            error=lambda _message: None,
        ),
        initial_generation=0,
    )

    learning.handoff_calibration(
        _calibration_decision(active=True, event="start_accepted"),
        result_revision=1,
        timestamp_ms=1_000,
    )
    assert runner.fit_requests == []

    learning.handoff_calibration(
        _calibration_decision(
            active=False,
            outcome="completed",
            event="stage_completed",
        ),
        result_revision=2,
        timestamp_ms=2_000,
    )

    assert events == ["finalize", "barrier", "schedule"]
    assert runner.fit_requests == [CandidateOrigin.OPERATOR_CALIBRATION]


@pytest.mark.parametrize("outcome", ("start_rejected", "stopped", "safety_aborted"))
def test_cancelled_or_rejected_calibration_consumes_fit_authorization(outcome):
    events = []
    runner = _CorpusFitRunner(events)
    learning = HoldLearningRuntime(
        runner=runner,
        model_store=None,
        persistence=_PersistenceBarrier(events),
        trace=None,
        controller_name="mpc",
        logger=SimpleNamespace(
            info=lambda _message: None,
            warning=lambda _message: None,
            error=lambda _message: None,
        ),
        initial_generation=0,
    )
    learning.handoff_calibration(
        _calibration_decision(active=True, event="start_accepted"),
        result_revision=1,
        timestamp_ms=1_000,
    )

    learning.handoff_calibration(
        _calibration_decision(active=False, outcome=outcome),
        result_revision=2,
        timestamp_ms=2_000,
    )

    assert runner.fit_requests == []


def test_stop_fit_submission_failure_is_learning_only_and_teardown_still_closes(
    hold_cycle,
) -> None:
    events = []
    persistence = _PersistenceBarrier(events)
    runner = _CorpusFitRunner(
        events,
        schedule_error=RuntimeError("corpus snapshot failed"),
    )
    authority = runner.get_model_snapshot()
    hold = _hold(
        hold_cycle,
        runner,
        identification=True,
        persistence=persistence,
    )

    hold.teardown(225.0)

    assert runner.get_model_snapshot() == authority
    assert events[-1] == "close"
    assert runner.stops == 1
    assert runner.legacy_refits == 0
    assert runner.legacy_finalizations == 0


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


class _MpcLearningCoreMixin:
    """Complete MPC-learning contract for fixtures that model production MPC."""

    def __init__(self):
        super().__init__()
        self.seed_requirement_calls = 0
        self.learning_diagnostics_calls = 0
        self.seed_source_bindings = []
        self.learning_identity_bindings = []
        self.observations = []
        self.observation_failures = []
        self.fit_schedules = []
        self.fit_ticket_schedules = []
        self.fit_polls = []
        self.consumed_fit_tickets = []
        self.fit_failures = []

    def estimator_seed_requirements(self) -> tuple[float, int]:
        self.seed_requirement_calls += 1
        return 60.0, 8

    def bind_estimator_seed_source(
        self,
        source: Callable[[float, int], object] | None,
    ) -> None:
        self.seed_source_bindings.append(source)

    def bind_learning_identity(
        self,
        session_id: str,
        cook_id: str | None,
        role_generation: int,
    ) -> None:
        self.learning_identity_bindings.append(
            (session_id, cook_id, role_generation),
        )

    def observe_frame(self, observation: FrameObservation) -> object:
        self.observations.append(observation)
        return {
            "role_generation": observation.role_generation,
            "eligible": False,
        }

    def observation_failure(
        self,
        observation: FrameObservation,
        error: BaseException,
    ) -> object:
        self.observation_failures.append((observation, error))
        return {
            "role_generation": observation.role_generation,
            "eligible": False,
            "rejection_reasons": ("learner-exception",),
        }

    def poll_learning_off_path(
        self,
        *,
        live_origin: CandidateOrigin | None = None,
    ) -> object:
        self.fit_polls.append((threading.get_ident(), live_origin))
        return None

    def schedule_corpus_fit(self, origin: CandidateOrigin) -> bool:
        self.fit_schedules.append(origin)
        return False

    def _schedule_corpus_fit_ticket(
        self,
        origin: CandidateOrigin,
    ) -> str | None:
        self.fit_ticket_schedules.append(origin)
        return None

    def _consume_terminal_corpus_fit_ticket(
        self,
        ticket: str,
        origin: CandidateOrigin,
    ) -> bool:
        self.consumed_fit_tickets.append((ticket, origin))
        return True

    def fail_corpus_fit(
        self,
        ticket: str,
        error: BaseException | str,
    ) -> None:
        self.fit_failures.append((ticket, error))

    def get_learning_diagnostics(self) -> ControllerLearningDiagnostics:
        self.learning_diagnostics_calls += 1
        return ControllerLearningDiagnostics(
            schema_version=1,
            state={},
        )


class _CoreWithCorpusScheduling(_MpcLearningCoreMixin, _CoreWithoutRefit):
    def __init__(self):
        super().__init__()
        self._fit_requests = []
        self.fit_request_recorded = threading.Event()
        self.snapshot = {"version": 4, "revision": 3}

    @property
    def fit_requests(self):
        self.fit_request_recorded.wait(1.0)
        return self._fit_requests

    def schedule_corpus_fit(self, origin: CandidateOrigin) -> bool:
        self.fit_schedules.append(origin)
        self._fit_requests.append(origin)
        self.fit_request_recorded.set()
        return True

    def _schedule_corpus_fit_ticket(
        self,
        origin: CandidateOrigin,
    ) -> str | None:
        self.fit_ticket_schedules.append(origin)
        self._fit_requests.append(origin)
        self.fit_request_recorded.set()
        return "fit-ticket"

    def get_model_snapshot(self):
        return self.snapshot

    def close(self):
        return None


def test_sync_runner_delegates_only_the_corpus_fit_request_to_its_core() -> None:
    core = _CoreWithCorpusScheduling()
    runner = SyncControllerRunner(core)

    assert runner.schedule_corpus_fit(CandidateOrigin.PASSIVE_ONLINE) is True
    assert core.fit_requests == [CandidateOrigin.PASSIVE_ONLINE]
    assert runner.get_model_snapshot() == core.snapshot


def test_threaded_runner_schedules_after_stop_without_republishing_or_adopting() -> None:
    core = _CoreWithCorpusScheduling()
    runner = ThreadedControllerRunner(core)
    before = runner.get_model_snapshot()
    runner.stop()

    assert runner.schedule_corpus_fit(CandidateOrigin.PASSIVE_ONLINE) is True
    assert core.fit_requests == [CandidateOrigin.PASSIVE_ONLINE]
    assert runner.get_model_snapshot() == before


def test_runner_contract_requires_corpus_scheduling_instead_of_raw_cook_refit() -> None:
    everything_else = {
        name: (lambda self, *args, **kwargs: None)
        for name in ControllerRunner.__abstractmethods__
        if name != "schedule_corpus_fit"
    }
    incomplete = type("Incomplete", (ControllerRunner,), everything_else)

    with pytest.raises(TypeError, match="schedule_corpus_fit"):
        incomplete()


def _completed_frame(sequence: int) -> FrameObservation:
    start = float(sequence - 1) * 20.0
    return FrameObservation(
        frame_start_s=start,
        frame_end_s=start + 20.0,
        temp_c=100.0 + sequence,
        setpoint_c=120.0,
        ambient_c=20.0,
        requested_q=0.3,
        realized_q=0.3,
        requested_auger_duty=0.3,
        delivered_on_s=6.0,
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
    )


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


class _ActivationOrderingCore(_MpcLearningCoreMixin, _CoreWithoutRefit):
    def __init__(self):
        super().__init__()
        self.events = []
        self.installed = threading.Event()
        self.activation_terminated = False
        self._activation_queued = False

    def update(self, _temperature):
        self.events.append("solve")
        return 0.2

    def set_output(self, _applied):
        self.events.append("feedback")

    def observe_frame(self, observation: FrameObservation) -> object:
        self.observations.append(observation)
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
