from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import FrozenInstanceError, replace
from typing import cast

import pytest

from common.control_trace import (
    ActuationMode,
    CalibrationEventType,
    CalibrationTracePayload,
    ControllerType,
    ControlTraceRecord,
    HorizonScorePayload,
    InhibitReason,
    ModelEvaluationPayload,
    ModelEventPayload,
    ModelEventType,
    ModelObservationPayload,
    RecorderGapPayload,
    TraceEventKind,
    TraceSetting,
)
from common.model_evidence import (
    CalibrationSummaryEvidence,
    ConfidenceDecisionEvidence,
    EvidenceKind,
    FallbackEvidence,
    ModelEvidenceRecord,
    RecorderGapEvidence,
    RollbackEvidence,
)
from controller.applied_output import AppliedOutput, OutputSource
from controller.model_learning.activation import ActivationPhase
from controller.model_learning.calibration import (
    CalibrationDecision,
    CalibrationEvent,
    CalibrationProgress,
)
from controller.model_learning.contracts import CandidateOrigin, FrameObservation
from controller.mpc_allocator import allocate
from controller.runtime.control_trace_session import (
    ControlTraceSession,
    TraceSessionContext,
)
from controller.runtime.framed_pulse import (
    FramedPulseRuntime,
    FramedPulseSample,
    PulseControllerState,
)
from controller.runtime.logic.pulse import PulseResetReason
from controller.runtime.model_fitting import (
    TeardownRefitOutcome,
    TeardownRefitResult,
)
from controller.runtime.model_persistence import DurableActivationReceipt, EvidenceSubmission
from controller.runtime.modes.hold_learning import (
    HoldLearningRuntime,
    HoldRefitResult,
)
from controller.runtime.runner import (
    ObservationOutcomeDrain,
    ObservationOutcomeEnvelope,
    ObservationSubmission,
    ObservationTerminalDrop,
)
from controller.runtime.state import ControllerState
from grillplat.actuator_capabilities import AugerTiming
from tests.unit.runtime._persistence_helpers import _pair_phase_state


class _Recorder:
    def __init__(
        self,
        *,
        events: list[object] | None = None,
        close_error: BaseException | None = None,
    ) -> None:
        self.records: list[ControlTraceRecord] = []
        self.events = events
        self.close_error = close_error
        self.close_calls = 0

    def record(self, record: ControlTraceRecord) -> None:
        self.records.append(record)

    def flush_due(self, now_ms: int) -> None:
        del now_ms

    def close(self) -> None:
        self.close_calls += 1
        if self.events is not None:
            self.events.append("trace:close")
        if self.close_error is not None:
            raise self.close_error


class _Persistence:
    def __init__(
        self,
        *,
        accepted: bool = True,
        blocked: bool = False,
        checkpoint_results: Sequence[bool] = (True,),
        barrier_result: bool = True,
        barrier_error: BaseException | None = None,
        failed: bool = False,
        events=None,
    ) -> None:
        self.accepted = accepted
        self.evidence_blocked = blocked
        self.checkpoint_results = list(checkpoint_results)
        self.barrier_result = barrier_result
        self.barrier_error = barrier_error
        self.failed = failed
        self.batches: list[tuple[ModelEvidenceRecord, ...]] = []
        self.checkpoints: list[tuple[str, dict[str, object]]] = []
        self.barrier_calls = 0
        self.events = [] if events is None else events

    def submit_evidence_batch(self, records: Sequence[ModelEvidenceRecord]) -> EvidenceSubmission:
        batch = tuple(records)
        self.batches.append(batch)
        self.events.append(("batch", batch))
        return EvidenceSubmission(accepted=self.accepted)

    def submit_checkpoint(self, name: str, snapshot: dict[str, object]) -> bool:
        self.checkpoints.append((name, snapshot))
        self.events.append(("checkpoint", snapshot))
        if self.checkpoint_results:
            return self.checkpoint_results.pop(0)
        return False

    def barrier(self, timeout: float = 2.0) -> bool:
        del timeout
        self.barrier_calls += 1
        self.events.append("persistence:barrier")
        if self.barrier_error is not None:
            raise self.barrier_error
        return self.barrier_result


class _Runner:
    def __init__(self, *, generation: int = 0, events=None) -> None:
        self.generation = generation
        self.next_sequence = 1
        self.next_evicted_sequence: int | None = None
        self.submissions: list[FrameObservation] = []
        self.completed: list[tuple[AppliedOutput, FrameObservation]] = []
        self.bindings: list[tuple[int, str, str | None]] = []
        self.retirements: list[int] = []
        self.confidence: list[ModelEvidenceRecord] = []
        self.confidence_accepted = True
        self.drains: list[ObservationOutcomeDrain] = []
        self.drain_count = 0
        self.raise_on_observe: BaseException | None = None
        self.restore_outcome: bool | None = None
        self.events = [] if events is None else events

    def observe_frame(self, observation: FrameObservation) -> ObservationSubmission | None:
        if self.raise_on_observe is not None:
            raise self.raise_on_observe
        sequence = self.next_sequence
        self.next_sequence += 1
        self.submissions.append(observation)
        submission = ObservationSubmission(sequence, self.generation, self.next_evicted_sequence)
        self.next_evicted_sequence = None
        return submission

    def complete_frame(self, applied: AppliedOutput, observation: FrameObservation) -> ObservationSubmission | None:
        self.completed.append((applied, observation))
        return self.observe_frame(observation)

    def drain_observation_outcomes(self) -> ObservationOutcomeDrain:
        self.drain_count += 1
        if self.drains:
            return self.drains.pop(0)
        return _drain()

    def bind_evidence_context(self, generation: int, session_id: str, cook_id: str | None) -> None:
        self.bindings.append((generation, session_id, cook_id))

    def retire_evidence_context(self, generation: int) -> None:
        self.retirements.append(generation)

    def submit_activation_confidence(self, record: ModelEvidenceRecord) -> DurableActivationReceipt:
        self.confidence.append(record)
        self.events.append(("confidence", record))
        return DurableActivationReceipt(accepted=self.confidence_accepted)

    def restore_model(self, snapshot: dict[str, object]) -> bool:
        del snapshot
        return False

    def drain_restore_outcome(self) -> bool | None:
        outcome = self.restore_outcome
        self.restore_outcome = None
        return outcome

    def runs_async(self) -> bool:
        return False

    def restore_activation(
        self,
        persisted: object,
        records: Sequence[ModelEvidenceRecord],
    ) -> bool:
        del persisted, records
        return False

    def activation_runtime_failure(self, reason: str) -> bool:
        del reason
        return False

    def rollback_activation(self, reason: str) -> bool:
        del reason
        return False

    def drain_activation_events(self) -> tuple[ModelEvidenceRecord, ...]:
        return ()

    def stop_for_refit(self) -> bool | None:
        return None

    def controller_state(self) -> object:
        return {}

    def refit_from_cook(self) -> object:
        return None

    def get_model_snapshot(self) -> object:
        return None

    def finalize_cook_refit(self, outcome: TeardownRefitOutcome) -> bool:
        del outcome
        return False

    def finish_teardown(self) -> None:
        return None


class _NoSubmissionRunner(_Runner):
    def observe_frame(self, observation: FrameObservation) -> None:
        self.submissions.append(observation)


class _LifecycleRunner(_Runner):
    def __init__(
        self,
        *,
        restore_accepted: bool = True,
        asynchronous: bool = False,
        events: list[object] | None = None,
    ) -> None:
        super().__init__(events=events)
        self.restore_accepted = restore_accepted
        self.asynchronous = asynchronous
        self.restored_models: list[dict[str, object]] = []
        self.activation_restores: list[tuple[object, tuple[ModelEvidenceRecord, ...]]] = []
        self.rollbacks: list[str] = []
        self.fallbacks: list[str] = []
        self.activation_events: list[ModelEvidenceRecord] = []
        self.status: object = {}
        self.status_error: BaseException | None = None
        self.refit_result: object = None
        self.refit_error: BaseException | None = None
        self.refit_calls = 0
        self.snapshot: object = {"revision": 1}
        self.finalize_results: list[bool] = [True]
        self.finalize_errors: list[BaseException | None] = []
        self.finalized: list[TeardownRefitOutcome] = []
        self.finish_calls = 0
        self.finish_error: BaseException | None = None

    def restore_model(self, snapshot: dict[str, object]) -> bool:
        self.restored_models.append(snapshot)
        return self.restore_accepted

    def runs_async(self) -> bool:
        return self.asynchronous

    def restore_activation(
        self,
        persisted: object,
        records: Sequence[ModelEvidenceRecord],
    ) -> bool:
        self.activation_restores.append((persisted, tuple(records)))
        return True

    def rollback_activation(self, reason: str) -> bool:
        self.rollbacks.append(reason)
        return True

    def activation_runtime_failure(self, reason: str) -> bool:
        self.fallbacks.append(reason)
        return True

    def drain_activation_events(self) -> tuple[ModelEvidenceRecord, ...]:
        events = tuple(self.activation_events)
        self.activation_events.clear()
        return events

    def retire_evidence_context(self, generation: int) -> None:
        self.events.append(("runner:retire", generation))
        super().retire_evidence_context(generation)

    def controller_state(self) -> object:
        if self.status_error is not None:
            raise self.status_error
        return self.status

    def refit_from_cook(self) -> object:
        self.refit_calls += 1
        if self.refit_error is not None:
            raise self.refit_error
        return self.refit_result

    def finalize_cook_refit(self, outcome: TeardownRefitOutcome) -> bool:
        self.finalized.append(outcome)
        if self.finalize_errors:
            error = self.finalize_errors.pop(0)
            if error is not None:
                raise error
        if outcome is TeardownRefitOutcome.CHECKPOINT_FAILURE:
            self.snapshot = {
                "revision": len(self.finalized) + 1,
                "cook_refit": {"latest": outcome.value},
            }
        if self.finalize_results:
            return self.finalize_results.pop(0)
        return False

    def get_model_snapshot(self) -> object:
        return self.snapshot

    def finish_teardown(self) -> None:
        self.finish_calls += 1
        self.events.append("runner:finish")
        if self.finish_error is not None:
            raise self.finish_error


class _ModelStore:
    def __init__(
        self,
        snapshot: dict[str, object] | None = None,
        *,
        load_error: BaseException | None = None,
    ) -> None:
        self.snapshot = snapshot
        self.load_error = load_error
        self.loads: list[str] = []

    def load(self, name: str) -> dict[str, object] | None:
        self.loads.append(name)
        if self.load_error is not None:
            raise self.load_error
        return self.snapshot


class _LifecycleLogger:
    def __init__(self) -> None:
        self.infos: list[str] = []
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def info(self, message: str) -> None:
        self.infos.append(message)

    def warning(self, message: str) -> None:
        self.warnings.append(message)

    def error(self, message: str) -> None:
        self.errors.append(message)


def _drain(
    *envelopes: ObservationOutcomeEnvelope,
    terminal_drops: tuple[ObservationTerminalDrop, ...] = (),
    dropped_sequences: tuple[int, ...] = (),
) -> ObservationOutcomeDrain:
    return ObservationOutcomeDrain(
        envelopes=tuple(envelopes),
        terminal_drops=terminal_drops,
        dropped_count=len(dropped_sequences),
        dropped_sequences=dropped_sequences,
    )


def _observation(index: int = 0, **changes) -> FrameObservation:
    observation = FrameObservation(
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
        result_revision=index + 1,
        output_source="controller",
        lid_open=False,
        safety_inhibited=False,
        manual_override=False,
        stale=False,
        skipped=False,
        reset=False,
        continuous=True,
        role_generation=0,
        observation_sequence=index + 1,
    )
    return replace(observation, **changes)


def _outcome(*, role_generation: int = 0, evaluation: ModelEvaluationPayload | None = None):
    return {
        "role_generation": role_generation,
        "eligible": False,
        "rejection_reasons": ("insufficient-excitation",),
        "input_variance": 0.2,
        "input_levels": 2,
        "effective_updates": 0,
        "model_digest": "a" * 64,
        "evaluation_payload": evaluation,
    }


def _evaluation() -> ModelEvaluationPayload:
    return ModelEvaluationPayload(
        decision_id="evaluation-1",
        evaluated_at_ms=20_000,
        role_generation=0,
        promoted=False,
        committed=False,
        consecutive_wins=0,
        rejection_reasons=("insufficient-excitation",),
        incumbent_prediction_score=None,
        challenger_prediction_score=None,
        incumbent_braking_score=None,
        challenger_braking_score=None,
        sample_count=0,
        prospective_digest=None,
        window_start_ms=20_000,
        window_end_ms=20_000,
        incumbent_digest="a" * 64,
        challenger_digest="b" * 64,
        completed_origins=(),
        horizon_scores=(
            HorizonScorePayload(3, None, None, 0),
            HorizonScorePayload(15, None, None, 0),
        ),
        evaluation_duration_ms=0.0,
    )


def _ordinary_evidence(name: str) -> ModelEvidenceRecord:
    return ModelEvidenceRecord(
        evidence_id=name,
        kind=EvidenceKind.RECORDER_GAP,
        session_id="session",
        cook_id="cook",
        timestamp_ms=20_000,
        role_generation=0,
        model_digest=None,
        provenance_digest=None,
        payload=RecorderGapEvidence(lost_record_count=1, reason=name),
    )


def _confidence_evidence(name: str) -> ModelEvidenceRecord:
    return ModelEvidenceRecord(
        evidence_id=name,
        kind=EvidenceKind.CONFIDENCE_DECISION,
        session_id="session",
        cook_id="cook",
        timestamp_ms=20_000,
        role_generation=0,
        model_digest="b" * 64,
        provenance_digest="a" * 64,
        payload=ConfidenceDecisionEvidence(decision_id=name, blocked=False, reason=None),
    )


def _trace_context() -> TraceSessionContext:
    return TraceSessionContext(
        controller=ControllerType.MPC,
        controller_config={},
        temperature_unit="C",
        control_period_seconds=1.0,
        fallback_model=None,
        runner_snapshot_fallback_safe=False,
        pulse_slot_seconds=1.0,
        pulse_frame_seconds=20.0,
        fan_authority=True,
        fan_pwm_capable=True,
        fan_min_duty=10.0,
        fan_max_duty=100.0,
        setpoint=120.0,
        ambient_temperature=20.0,
        software_version="test",
        build_version="test",
        cook_id="cook",
        runner_generation=0,
    )


def _trace(
    *,
    opened: bool = True,
    recorder: _Recorder | None = None,
    warnings: list[str] | None = None,
):
    actual_recorder = _Recorder() if recorder is None else recorder
    warning_messages = [] if warnings is None else warnings
    trace = ControlTraceSession(actual_recorder, warning=warning_messages.append)
    if opened:
        identity = trace.ensure_open(_trace_context(), timestamp_ms=0)
        assert identity is not None
    return trace, actual_recorder


def _runtime(
    *,
    opened: bool = True,
    runner: _Runner | None = None,
    persistence: _Persistence | None = None,
    logger: _LifecycleLogger | None = None,
):
    trace, recorder = _trace(opened=opened)
    actual_runner = _Runner() if runner is None else runner
    actual_persistence = _Persistence() if persistence is None else persistence
    runtime = HoldLearningRuntime(
        runner=actual_runner,
        model_store=None,
        persistence=actual_persistence,
        trace=trace,
        controller_name="mpc",
        logger=_LifecycleLogger() if logger is None else logger,
        initial_generation=actual_runner.generation,
    )
    return runtime, actual_runner, actual_persistence, trace, recorder


def _lifecycle_runtime(
    *,
    snapshot: dict[str, object] | None = None,
    restore_accepted: bool = True,
    asynchronous: bool = False,
    load_error: BaseException | None = None,
    runner: _LifecycleRunner | None = None,
    persistence: _Persistence | None = None,
    trace: ControlTraceSession | None = None,
    recorder: _Recorder | None = None,
    logger: _LifecycleLogger | None = None,
    controller_name: str = "pid_sp",
):
    actual_trace, actual_recorder = _trace(recorder=recorder) if trace is None else (trace, recorder)
    actual_trace.set_model_authority({"revision": 1}, "online")
    actual_runner = (
        _LifecycleRunner(
            restore_accepted=restore_accepted,
            asynchronous=asynchronous,
        )
        if runner is None
        else runner
    )
    store = _ModelStore(snapshot, load_error=load_error)
    actual_persistence = _Persistence() if persistence is None else persistence
    actual_logger = _LifecycleLogger() if logger is None else logger
    runtime = HoldLearningRuntime(
        runner=actual_runner,
        model_store=store,
        persistence=actual_persistence,
        trace=actual_trace,
        controller_name=controller_name,
        logger=actual_logger,
        initial_generation=actual_runner.generation,
    )
    return (
        runtime,
        actual_runner,
        store,
        actual_trace,
        cast(_Recorder, actual_recorder),
        actual_logger,
    )


def _records(recorder: _Recorder, kind: TraceEventKind):
    return [record for record in recorder.records if record.event_kind is kind]


def _gap_payloads(recorder: _Recorder) -> list[RecorderGapPayload]:
    return [record.payload for record in recorder.records if isinstance(record.payload, RecorderGapPayload)]


def _observation_payloads(recorder: _Recorder) -> list[ModelObservationPayload]:
    return [record.payload for record in recorder.records if isinstance(record.payload, ModelObservationPayload)]


def _evaluation_payloads(recorder: _Recorder) -> list[ModelEvaluationPayload]:
    return [record.payload for record in recorder.records if isinstance(record.payload, ModelEvaluationPayload)]


def _reset_shortened_observation() -> FrameObservation:
    """Take a lid-reset frame straight from the framed-pulse producer.

    The reset stops the frame where the control loop stopped it, so the frame
    is short, the auger delivery runs to that instant, and the schedule still
    describes the full frame the reset cut off.
    """
    controller = cast(PulseControllerState, ControllerState())
    runtime = FramedPulseRuntime()
    runtime.configure(
        ActuationMode.FRAMED_PULSE,
        controller=controller,
        timing=AugerTiming(pulse_s=2, frame_s=20),
        now=0.0,
        calibration_command_revision=0,
    )
    controller.pulse_result_revision = 9
    controller.pulse_requested_duty = 1.0
    controller.pulse_maximum_duty = 1.0
    sample = FramedPulseSample(
        temperature=212.0,
        setpoint=392.0,
        ambient_c=20.0,
        units="F",
        role_generation=0,
    )
    runtime.advance(0.0, True, sample=sample)
    runtime.advance(3.0, True, sample=sample)
    result = runtime.reset(
        PulseResetReason.LID,
        7.3333331,
        InhibitReason.LID_OPEN,
        actual_auger_on=True,
        sample=sample,
        terminal_feedback=True,
    )
    observation = result.completions[-1].observation
    assert observation is not None
    assert observation.reset is True
    assert observation.calibration_status == "inactive"
    return observation


def test_reset_shortened_frame_reaches_the_trace_as_a_model_observation() -> None:
    runtime, runner, _persistence, _trace_session, recorder = _runtime()
    observation = _reset_shortened_observation()

    runtime.submit_completed_observation((0, 7_333), observation)
    runner.drains.append(_drain(ObservationOutcomeEnvelope(1, 0, observation, _outcome())))
    runtime.reconcile_outcomes(10.0)

    payloads = _observation_payloads(recorder)
    assert [(payload.frame_start_ms, payload.frame_end_ms) for payload in payloads] == [(0, 7_333)]
    assert payloads[0].rejection_reasons == ("insufficient-excitation",)
    assert _gap_payloads(recorder) == []


def test_rejected_observation_payload_failure_never_escapes_invalid_probe_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = _LifecycleLogger()
    runtime, _runner, _persistence, _trace_session, recorder = _runtime(logger=logger)

    def _explode(cls, observation, reason):
        del cls, observation, reason
        raise RuntimeError("validator refused the payload")

    monkeypatch.setattr(
        HoldLearningRuntime,
        "_rejected_model_observation",
        classmethod(_explode),
    )
    observation = _observation(probe_valid=False, continuous=False)

    runtime.submit_completed_observation((0, 0), observation)

    assert _observation_payloads(recorder) == []
    assert [gap.reason for gap in _gap_payloads(recorder)] == ["invalid-probe"]
    assert any("validator refused the payload" in message for message in logger.warnings)


def test_rejected_observation_payload_failure_never_escapes_outcome_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = _LifecycleLogger()
    runtime, runner, _persistence, _trace_session, recorder = _runtime(logger=logger)
    observation = _observation()
    runtime.submit_completed_observation((0, 0), observation)

    def _explode(cls, observation, reason):
        del cls, observation, reason
        raise RuntimeError("validator refused the payload")

    monkeypatch.setattr(
        HoldLearningRuntime,
        "_rejected_model_observation",
        classmethod(_explode),
    )
    runner.drains.append(_drain(ObservationOutcomeEnvelope(1, 1, observation, _outcome())))

    runtime.reconcile_outcomes(22.0)

    assert _observation_payloads(recorder) == []
    assert [gap.reason for gap in _gap_payloads(recorder)] == ["observation-configuration-mismatch"]
    assert any("validator refused the payload" in message for message in logger.warnings)


def test_sub_millisecond_frame_leaves_the_control_loop_running() -> None:
    logger = _LifecycleLogger()
    runtime, _runner, persistence, _trace_session, recorder = _runtime(logger=logger)
    # Both frame bounds truncate to the same millisecond, so neither the
    # observation payload nor the gap that replaces it can state an interval.
    observation = _observation(
        frame_start_s=40.0,
        frame_end_s=40.0004,
        delivered_on_s=0.0002,
        probe_valid=False,
        continuous=False,
    )

    runtime.submit_completed_observation((40_000, 40_000), observation)

    assert _observation_payloads(recorder) == []
    assert _gap_payloads(recorder) == []
    assert [record.kind for batch in persistence.batches for record in batch] == [EvidenceKind.RECORDER_GAP]
    assert [message.split(":")[0] for message in logger.warnings] == [
        "Rejected model observation failed",
        "Recorder gap trace failed",
    ]


class _TrajectoryObserver:
    def __init__(self) -> None:
        self.observations: list[FrameObservation] = []

    def observe_hold_frame(self, observation: FrameObservation) -> None:
        self.observations.append(observation)


def test_eligible_reconciled_hold_observation_enters_trajectory_exactly_once() -> None:
    trace, _recorder = _trace()
    runner = _Runner()
    trajectory = _TrajectoryObserver()
    runtime = HoldLearningRuntime(
        runner=runner,
        model_store=None,
        persistence=_Persistence(),
        trace=trace,
        controller_name="mpc",
        logger=_LifecycleLogger(),
        initial_generation=runner.generation,
        learning_trajectory=trajectory,
    )
    observation = _observation()
    eligible = {
        **_outcome(),
        "eligible": True,
        "rejection_reasons": (),
    }

    runtime.submit_completed_observation((0, 20_000), observation)
    runner.drains.append(
        _drain(ObservationOutcomeEnvelope(1, 0, observation, eligible))
    )
    runtime.reconcile_outcomes(22.0)
    runtime.reconcile_outcomes(23.0)

    assert trajectory.observations == [observation]


def test_accepted_outcome_keeps_exact_frame_feedback_identity_and_reconciles_once() -> None:
    runtime, runner, _persistence, _trace_session, recorder = _runtime()
    observation = _observation()
    feedback = AppliedOutput(0.25, OutputSource.CONTROLLER, 20.0, requested=0.25)

    runtime.submit_completed_observation((1, 2), observation, feedback)
    runner.drains.append(_drain(ObservationOutcomeEnvelope(1, 0, observation, _outcome())))
    runtime.reconcile_outcomes(22.0)
    runtime.reconcile_outcomes(23.0)

    assert runner.completed == [(feedback, observation)]
    records = _records(recorder, TraceEventKind.MODEL_OBSERVATION)
    assert len(records) == 1
    payload = cast(ModelObservationPayload, records[0].payload)
    assert (payload.frame_start_ms, payload.frame_end_ms, payload.observation_sequence) == (
        0,
        20_000,
        1,
    )


def test_complete_grey_outcome_without_obsolete_scores_stays_eligible() -> None:
    runtime, runner, _persistence, _trace_session, recorder = _runtime()
    observation = _observation()
    outcome = {
        "role_generation": 0,
        "eligible": True,
        "rejection_reasons": (),
        "input_variance": 0.0,
        "input_levels": 0,
        "effective_updates": 21,
        "model_digest": "a" * 64,
        "forecast_origin_evidence": (),
        "learning_evaluation": None,
        "evaluation_payload": None,
        "confidence_accepted": None,
        "confidence_already_persisted": False,
    }

    runtime.submit_completed_observation((0, 20_000), observation)
    runner.drains.append(_drain(ObservationOutcomeEnvelope(1, 0, observation, outcome)))
    runtime.reconcile_outcomes(22.0)

    payloads = _observation_payloads(recorder)
    assert len(payloads) == 1
    assert payloads[0].eligible is True
    assert payloads[0].rejection_reasons == ()
    assert _gap_payloads(recorder) == []


def test_submission_eviction_terminal_drop_and_dropped_sequence_are_consumed_once() -> None:
    runtime, runner, persistence, _trace_session, recorder = _runtime()
    first, second = _observation(0), _observation(1)
    runtime.submit_completed_observation((0, 0), first)
    runner.next_evicted_sequence = 1
    runtime.submit_completed_observation((0, 1), second)
    runner.drains.append(
        _drain(
            terminal_drops=(ObservationTerminalDrop(2, 0, second, "runner-outcome-evicted"),),
            dropped_sequences=(2,),
        )
    )

    runtime.reconcile_outcomes(42.0)
    runtime.reconcile_outcomes(43.0)

    trace_reasons = [
        cast(RecorderGapPayload, record.payload).reason for record in _records(recorder, TraceEventKind.RECORDER_GAP)
    ]
    compact_reasons = [
        cast(RecorderGapEvidence, batch[0].payload).reason
        for batch in persistence.batches
        if batch and batch[0].kind is EvidenceKind.RECORDER_GAP
    ]
    assert trace_reasons == ["runner-observation-evicted", "runner-outcome-evicted"]
    assert compact_reasons == trace_reasons


def test_pending_capacity_overflow_records_oldest_gap_and_retains_fifo() -> None:
    runtime, runner, _persistence, _trace_session, recorder = _runtime()
    observations = [_observation(index) for index in range(61)]
    for index, observation in enumerate(observations):
        runtime.submit_completed_observation((0, index), observation)
    runner.drains.append(
        _drain(
            ObservationOutcomeEnvelope(1, 0, observations[0], _outcome()),
            ObservationOutcomeEnvelope(2, 0, observations[1], _outcome()),
        )
    )

    runtime.reconcile_outcomes(1_222.0)

    gaps = _gap_payloads(recorder)
    observation_payloads = _observation_payloads(recorder)
    assert [(gap.observation_sequence, gap.reason) for gap in gaps] == [(1, "pending-observation-overflow")]
    assert [payload.observation_sequence for payload in observation_payloads] == [2]


def test_missing_trace_identity_and_generation_mismatch_become_visible_rejections() -> None:
    runtime, runner, _persistence, trace, recorder = _runtime(opened=False)
    first = _observation(0)
    runtime.submit_completed_observation((0, 0), first)
    identity = trace.ensure_open(_trace_context(), timestamp_ms=1)
    assert identity is not None
    second = _observation(1)
    runtime.submit_completed_observation((0, 1), second)
    runner.drains.append(
        _drain(
            ObservationOutcomeEnvelope(1, 0, first, _outcome()),
            ObservationOutcomeEnvelope(2, 1, second, _outcome()),
        )
    )

    runtime.reconcile_outcomes(42.0)

    payloads = [
        cast(ModelObservationPayload, record.payload) for record in _records(recorder, TraceEventKind.MODEL_OBSERVATION)
    ]
    assert [payload.rejection_reasons for payload in payloads] == [
        ("observation-configuration-mismatch",),
        ("observation-configuration-mismatch",),
    ]


def test_retired_generation_fences_late_outcome_without_reopening_pending_state() -> None:
    runtime, runner, _persistence, _trace_session, recorder = _runtime()
    observation = _observation()
    runtime.submit_completed_observation((0, 0), observation)

    remaining_generations = runtime.retire_generation(0)
    runner.drains.append(_drain(ObservationOutcomeEnvelope(1, 0, observation, _outcome())))
    runtime.reconcile_outcomes(22.0)

    assert remaining_generations == ()
    assert runner.retirements == [0]
    assert _records(recorder, TraceEventKind.MODEL_OBSERVATION) == []


def test_evidence_preserves_split_channel_order_and_evaluation_trace() -> None:
    events = []
    runner = _Runner(events=events)
    persistence = _Persistence(events=events)
    runtime, _runner, _persistence, _trace_session, recorder = _runtime(runner=runner, persistence=persistence)
    observation = _observation()
    runtime.submit_completed_observation((0, 0), observation)
    ordinary_one = _ordinary_evidence("ordinary-1")
    confidence_one = _confidence_evidence("confidence-1")
    ordinary_two = _ordinary_evidence("ordinary-2")
    confidence_two = _confidence_evidence("confidence-2")
    runner.drains.append(
        _drain(
            ObservationOutcomeEnvelope(
                1,
                0,
                observation,
                _outcome(evaluation=_evaluation()),
                (ordinary_one, confidence_one, ordinary_two, confidence_two),
            )
        )
    )

    runtime.reconcile_outcomes(22.0)

    assert events == [
        ("confidence", confidence_one),
        ("confidence", confidence_two),
        ("batch", (ordinary_one, ordinary_two)),
    ]
    assert [payload.decision_id for payload in _evaluation_payloads(recorder)] == ["evaluation-1"]


def test_persistence_refusals_mark_evidence_unavailable_without_skipping_a_channel() -> None:
    events = []
    runner = _Runner(events=events)
    runner.confidence_accepted = False
    persistence = _Persistence(accepted=False, events=events)
    runtime, _runner, _persistence, _trace_session, _recorder = _runtime(runner=runner, persistence=persistence)
    confidence = _confidence_evidence("confidence-refused")
    ordinary = _ordinary_evidence("ordinary-refused")

    runtime.persist_evidence((ordinary, confidence))

    assert events == [
        ("confidence", confidence),
        ("batch", (ordinary,)),
    ]
    assert runtime.evidence_available is False


def test_blocked_worker_records_gap_without_submitting_to_learner() -> None:
    persistence = _Persistence(accepted=False, blocked=True)
    runtime, runner, _persistence, _trace_session, recorder = _runtime(persistence=persistence)
    observation = _observation()

    runtime.submit_completed_observation((0, 0), observation)

    assert runner.submissions == []
    assert runtime.evidence_available is False
    assert [payload.reason for payload in _gap_payloads(recorder)] == ["model-persistence-unavailable"]


def test_runner_submission_exception_is_not_hidden_or_partially_accepted() -> None:
    runner = _Runner()
    runner.raise_on_observe = RuntimeError("learner submission failed")
    runtime, _runner, persistence, _trace_session, recorder = _runtime(runner=runner)

    with pytest.raises(RuntimeError, match="learner submission failed"):
        runtime.submit_completed_observation((0, 0), _observation())

    assert persistence.batches == []
    assert _records(recorder, TraceEventKind.MODEL_OBSERVATION) == []


def test_calibration_handoff_projects_public_terminal_decision() -> None:
    runtime, _runner, _persistence, _trace_session, _recorder = _runtime()
    decision = CalibrationDecision(
        active=False,
        probe_q=0.0,
        stage=None,
        progress=CalibrationProgress(),
        command_revision=17,
        command_action="start",
        command_generation=4,
        completed_stages=("low",),
        outcome="start_rejected",
        outcome_reasons=("lid_open", "safety"),
    )

    handoff = runtime.handoff_calibration(
        decision,
        result_revision=41,
        timestamp_ms=12_500,
    )

    assert (
        handoff.status,
        handoff.reason,
        handoff.command_revision,
        handoff.command_action,
        handoff.command_generation,
        handoff.completed_stages,
    ) == ("rejected", "lid_open, safety", 17, "start", 4, ("low",))


@pytest.mark.parametrize(
    ("decision", "expected"),
    (
        (None, ("inactive", None, 0.0, None, 0, "none", 0, ())),
        (
            CalibrationDecision(
                True,
                0.08,
                "middle",
                CalibrationProgress(),
                command_revision=8,
                command_action="resume",
                command_generation=3,
                completed_stages=("low",),
            ),
            ("active", None, 0.08, "middle", 8, "resume", 3, ("low",)),
        ),
        (
            CalibrationDecision(
                False,
                0.0,
                None,
                CalibrationProgress(),
                command_revision=9,
                command_action="safety-cancel",
                outcome="safety_aborted",
                outcome_reasons=("lid_open",),
            ),
            ("cancelled", "lid_open", 0.0, None, 9, "safety-cancel", 0, ()),
        ),
        (
            CalibrationDecision(
                False,
                0.0,
                "low",
                CalibrationProgress(),
                outcome="stage_timeout",
                outcome_reasons=("insufficient_excitation",),
            ),
            ("cancelled", "insufficient_excitation", 0.0, None, 0, "none", 0, ()),
        ),
        (
            CalibrationDecision(
                False,
                0.0,
                None,
                CalibrationProgress(),
                command_revision=10,
                command_action="stop",
                outcome="stopped",
            ),
            ("cancelled", None, 0.0, None, 10, "stop", 0, ()),
        ),
        (
            CalibrationDecision(
                False,
                0.0,
                "high",
                CalibrationProgress(),
                command_revision=11,
                command_action="start",
                completed_stages=("low", "middle", "high"),
                outcome="completed",
            ),
            ("accepted", None, 0.0, None, 11, "start", 0, ("low", "middle", "high")),
        ),
        (
            CalibrationDecision(
                False,
                0.0,
                None,
                CalibrationProgress(),
                events=(CalibrationEvent("start_accepted", "low", 0.08, 0.08, 0.0),),
            ),
            ("accepted", None, 0.0, None, 0, "none", 0, ()),
        ),
        (
            CalibrationDecision(
                False,
                0.0,
                None,
                CalibrationProgress(),
                outcome="future_terminal",
                outcome_reasons=("future_reason",),
            ),
            ("inactive", "future_reason", 0.0, None, 0, "none", 0, ()),
        ),
    ),
    ids=(
        "absent",
        "active",
        "safety-aborted",
        "stage-timeout",
        "stopped",
        "completed",
        "accepted-event",
        "unknown",
    ),
)
def test_calibration_handoff_projects_every_public_outcome(
    decision: CalibrationDecision | None,
    expected: tuple[object, ...],
) -> None:
    runtime, _runner, _persistence, _trace_session, _recorder = _runtime()

    handoff = runtime.handoff_calibration(
        decision,
        result_revision=41,
        timestamp_ms=12_500,
    )

    assert (
        handoff.status,
        handoff.reason,
        handoff.probe_load,
        handoff.stage,
        handoff.command_revision,
        handoff.command_action,
        handoff.command_generation,
        handoff.completed_stages,
    ) == expected


def test_calibration_handoff_returns_immutable_frame_projection() -> None:
    runtime, _runner, _persistence, _trace_session, _recorder = _runtime()
    decision = CalibrationDecision(
        True,
        0.08,
        "middle",
        CalibrationProgress(),
        command_revision=8,
        command_action="resume",
        command_generation=3,
        completed_stages=("low",),
    )

    handoff = runtime.handoff_calibration(
        decision,
        result_revision=41,
        timestamp_ms=12_500,
    )

    assert handoff.completed_stages == ("low",)
    assert isinstance(handoff.completed_stages, tuple)
    with pytest.raises(FrozenInstanceError):
        handoff.status = "cancelled"


def test_calibration_handoff_records_known_events_in_order_and_ignores_unknown() -> None:
    runtime, _runner, _persistence, _trace_session, recorder = _runtime()
    progress = CalibrationProgress(
        eligible_observations=12,
        positive_observations=7,
        negative_observations=5,
    )
    decision = CalibrationDecision(
        True,
        0.08,
        "low",
        progress,
        events=(
            CalibrationEvent("start_requested", None, 0.0, 0.0, 0.0),
            CalibrationEvent("future_event", "low", 0.08, 0.07, 0.15, ("future",)),
            CalibrationEvent("stage_timeout", "low", 0.08, 0.07, 0.15, ("bounded",)),
        ),
        command_revision=17,
        command_action="start",
    )

    runtime.handoff_calibration(
        decision,
        result_revision=41,
        timestamp_ms=12_500,
    )

    calibration = _records(recorder, TraceEventKind.CALIBRATION)
    assert [record.ts_ms for record in calibration] == [12_500, 12_500]
    assert [record.payload for record in calibration] == [
        CalibrationTracePayload(
            event=CalibrationEventType.START_REQUESTED,
            command_revision=17,
            command_action="start",
            result_revision=41,
            stage=None,
            intended_probe_load=0.0,
            bounded_probe_load=0.0,
            cumulative_probe_load=0.0,
            eligible_observations=12,
            positive_observations=7,
            negative_observations=5,
            reasons=(),
        ),
        CalibrationTracePayload(
            event=CalibrationEventType.STAGE_TIMEOUT,
            command_revision=17,
            command_action="start",
            result_revision=41,
            stage="low",
            intended_probe_load=0.08,
            bounded_probe_load=0.07,
            cumulative_probe_load=0.15,
            eligible_observations=12,
            positive_observations=7,
            negative_observations=5,
            reasons=("bounded",),
        ),
    ]


def test_valid_and_invalid_calibration_frames_persist_without_invalid_learner_submission() -> None:
    runtime, runner, persistence, _trace_session, recorder = _runtime()
    baseline = allocate(
        0.25,
        u_max=0.5,
        fan_min_pct=10.0,
        fan_max_pct=100.0,
        enable_fan=True,
    )
    combined = allocate(
        0.35,
        u_max=0.5,
        fan_min_pct=10.0,
        fan_max_pct=100.0,
        enable_fan=True,
    )
    valid = _observation(
        0,
        baseline_q=0.25,
        probe_q=0.10,
        requested_q=0.35,
        calibration_command_revision=7,
        calibration_command_action="start",
        baseline_allocation=baseline,
        combined_allocation=combined,
        calibration_status="active",
        calibration_stage="low",
        scheduled_on_s=7.0,
        completed_calibration_stages=("low",),
    )
    invalid = replace(
        valid,
        frame_start_s=20.0,
        frame_end_s=40.0,
        observation_sequence=2,
        probe_valid=False,
    )

    runtime.submit_completed_observation((0, 1), invalid)
    runtime.submit_completed_observation((0, 0), valid)
    runtime.reconcile_outcomes(42.0)

    calibration_records = [
        batch[0] for batch in persistence.batches if batch[0].kind is EvidenceKind.CALIBRATION_SUMMARY
    ]
    calibration_payloads = [cast(CalibrationSummaryEvidence, record.payload) for record in calibration_records]
    assert len(calibration_payloads) == 2
    assert [payload.command_revision for payload in calibration_payloads] == [7, 7]
    assert [payload.delivered_on_seconds for payload in calibration_payloads] == [5.0, 5.0]
    assert runner.submissions == [valid]
    invalid_payloads = [payload for payload in _observation_payloads(recorder) if payload.observation_sequence == 2]
    assert [payload.rejection_reasons for payload in invalid_payloads] == [("invalid-probe",)]


def _calibration_observation() -> FrameObservation:
    baseline = allocate(0.25, u_max=0.5, fan_min_pct=10.0, fan_max_pct=100.0, enable_fan=True)
    combined = allocate(0.35, u_max=0.5, fan_min_pct=10.0, fan_max_pct=100.0, enable_fan=True)
    return _observation(
        0,
        baseline_q=0.25,
        probe_q=0.10,
        requested_q=0.35,
        calibration_command_revision=7,
        calibration_command_action="start",
        baseline_allocation=baseline,
        combined_allocation=combined,
        calibration_status="active",
        calibration_stage="low",
        scheduled_on_s=7.0,
        completed_calibration_stages=("low",),
    )


def test_calibration_evidence_failure_never_aborts_the_completed_observation(
    monkeypatch,
) -> None:
    """Evidence is telemetry: a payload the model rejects must not take the fire out."""
    logger = _LifecycleLogger()
    runtime, runner, persistence, _trace_session, _recorder = _runtime(logger=logger)
    observation = _calibration_observation()

    def _invalid(cls, observation, session_id, cook_id):
        # An incomplete completed-frame payload raises the same pydantic
        # ValidationError shape the runtime hit on the grill.
        return CalibrationSummaryEvidence(
            accepted=True,
            probe_count=1,
            result_revision=1,
        )

    monkeypatch.setattr(HoldLearningRuntime, "_calibration_frame_evidence", classmethod(_invalid))

    runtime.submit_completed_observation((0, 0), observation)
    runtime.reconcile_outcomes(22.0)

    assert not [batch for batch in persistence.batches if batch[0].kind is EvidenceKind.CALIBRATION_SUMMARY]
    assert len(logger.warnings) == 1
    assert logger.warnings[0].startswith("Calibration frame evidence failed: ")
    assert runtime.evidence_available is True
    assert runner.submissions == [observation]


def test_refused_calibration_evidence_batch_still_marks_evidence_unavailable() -> None:
    persistence = _Persistence(accepted=False)
    runtime, _runner, _persistence, _trace_session, _recorder = _runtime(persistence=persistence)

    runtime.submit_completed_observation((0, 0), _calibration_observation())

    assert persistence.batches[0][0].kind is EvidenceKind.CALIBRATION_SUMMARY
    assert runtime.evidence_available is False


def test_record_gap_publishes_matching_trace_and_compact_evidence() -> None:
    runtime, _runner, persistence, _trace_session, recorder = _runtime()
    observation = _observation()

    runtime.record_gap(observation, "runner-stop-timeout")

    trace_gap = cast(RecorderGapPayload, _records(recorder, TraceEventKind.RECORDER_GAP)[0].payload)
    compact_gap = cast(RecorderGapEvidence, persistence.batches[0][0].payload)
    assert (trace_gap.observation_sequence, trace_gap.reason) == (1, "runner-stop-timeout")
    assert (compact_gap.lost_record_count, compact_gap.reason) == (1, "runner-stop-timeout")


def test_bind_and_retire_generation_use_current_trace_identity_and_typed_runner_api() -> None:
    runtime, runner, _persistence, trace, _recorder = _runtime()
    identity = trace.identity
    assert identity is not None

    runtime.bind_generation(3)
    remaining = runtime.retire_generation(3)

    assert runner.bindings == [(3, identity.session_id, identity.cook_id)]
    assert runner.retirements == [3]
    assert remaining == ()


def test_no_submission_result_is_ignored_without_exposing_mutable_pending_state() -> None:
    runner = _NoSubmissionRunner()
    runtime, _runner, _persistence, _trace_session, recorder = _runtime(runner=runner)
    observation = _observation()

    runtime.submit_completed_observation((2, 4), observation)
    runtime.reconcile_outcomes(22.0)

    assert runner.submissions == [observation]
    assert _records(recorder, TraceEventKind.MODEL_OBSERVATION) == []


def test_multiple_invalid_probes_keep_distinct_bounded_fifo_entries() -> None:
    runtime, runner, _persistence, _trace_session, recorder = _runtime()
    first = _observation(0, probe_valid=False)
    second = _observation(1, probe_valid=False)

    runtime.submit_completed_observation((0, 0), first)
    runtime.submit_completed_observation((0, 1), second)
    runtime.reconcile_outcomes(42.0)

    assert runner.submissions == []
    assert [payload.observation_sequence for payload in _observation_payloads(recorder)] == [1, 2]


def test_missing_collaborators_preserve_public_noop_boundaries() -> None:
    runtime = HoldLearningRuntime(
        runner=None,
        model_store=None,
        persistence=None,
        trace=None,
        controller_name="mpc",
        logger=_LifecycleLogger(),
        initial_generation=0,
    )
    observation = _observation()

    runtime.submit_completed_observation((0, 0), observation)
    runtime.record_gap(observation, "no-evidence-sinks")
    runtime.bind_generation(0)
    remaining = runtime.retire_generation(0)

    assert remaining == ()
    assert runtime.evidence_available is True


def test_malformed_submission_identity_is_not_partially_retained() -> None:
    class MalformedSubmissionRunner(_Runner):
        def observe_frame(
            self,
            observation: FrameObservation,
        ) -> ObservationSubmission:
            self.submissions.append(observation)
            return ObservationSubmission(cast(int, True), 0)

    runner = MalformedSubmissionRunner()
    runtime, _runner, _persistence, _trace_session, recorder = _runtime(runner=runner)
    observation = _observation()

    runtime.submit_completed_observation((0, 0), observation)
    runtime.reconcile_outcomes(22.0)

    assert runner.submissions == [observation]
    assert runner.drain_count == 0
    assert _observation_payloads(recorder) == []


def test_runner_outcome_rejections_and_malformed_payloads_stay_visible() -> None:
    runtime, runner, _persistence, _trace_session, recorder = _runtime()
    observations = [_observation(index) for index in range(5)]
    for index, observation in enumerate(observations):
        runtime.submit_completed_observation((0, index), observation)

    allocation_mismatch = replace(
        observations[0],
        allocation_join_reason="allocation-result-missing",
    )
    invalid_probe = replace(observations[1], probe_valid=False)
    gate_mismatch = replace(observations[3], lid_open=True)
    gate_outcome = _outcome()
    gate_outcome["eligible"] = True
    runner.drains.append(
        _drain(
            ObservationOutcomeEnvelope(
                1,
                0,
                allocation_mismatch,
                _outcome(evaluation=_evaluation()),
            ),
            ObservationOutcomeEnvelope(2, 0, invalid_probe, _outcome()),
            ObservationOutcomeEnvelope(
                3,
                0,
                observations[2],
                _outcome(role_generation=1),
            ),
            ObservationOutcomeEnvelope(4, 0, gate_mismatch, gate_outcome),
            ObservationOutcomeEnvelope(
                5,
                0,
                observations[4],
                cast(object, "malformed"),
            ),
        )
    )

    runtime.reconcile_outcomes(102.0)

    assert [payload.rejection_reasons for payload in _observation_payloads(recorder)] == [
        ("allocation-result-missing",),
        ("invalid-probe",),
        ("observation-role-generation-mismatch",),
        ("observation-gate-mismatch",),
        ("observation-outcome-malformed",),
    ]
    assert [payload.decision_id for payload in _evaluation_payloads(recorder)] == ["evaluation-1"]


def test_lifecycle_payload_validation_records_only_well_formed_event() -> None:
    runtime, runner, _persistence, _trace_session, recorder = _runtime()
    observations = [_observation(index) for index in range(3)]
    for index, observation in enumerate(observations):
        runtime.submit_completed_observation((0, index), observation)

    not_a_mapping = _outcome()
    not_a_mapping["lifecycle"] = cast(object, "invalid")
    invalid_parameters = _outcome()
    invalid_parameters["lifecycle"] = {
        "event": "reject",
        "model_revision": 1,
        "provenance": "test",
        "detail": "invalid parameters",
        "model_kind": "grey-box",
        "model_schema": "grey-box/v1",
        "role_generation": 0,
        "snapshot_digest": "a" * 64,
        "parameters": cast(object, {"key": "alpha", "value": 1}),
    }
    valid = _outcome()
    valid["lifecycle"] = {
        "event": "reject",
        "model_revision": 2,
        "provenance": "test",
        "detail": "well formed",
        "model_kind": "grey-box",
        "model_schema": "grey-box/v1",
        "role_generation": 0,
        "snapshot_digest": "b" * 64,
        "parameters": (
            TraceSetting(key="alpha", value=1),
            {"key": "beta", "value": 2},
        ),
    }
    runner.drains.append(
        _drain(
            ObservationOutcomeEnvelope(1, 0, observations[0], not_a_mapping),
            ObservationOutcomeEnvelope(2, 0, observations[1], invalid_parameters),
            ObservationOutcomeEnvelope(3, 0, observations[2], valid),
        )
    )

    runtime.reconcile_outcomes(62.0)

    lifecycle = [
        record.payload
        for record in _records(recorder, TraceEventKind.MODEL_EVENT)
        if isinstance(record.payload, ModelEventPayload)
    ]
    assert [(payload.detail, payload.parameters) for payload in lifecycle] == [
        (
            "well formed",
            (
                TraceSetting(key="alpha", value=1),
                TraceSetting(key="beta", value=2),
            ),
        )
    ]


def test_generation_binding_updates_only_matching_pending_identity() -> None:
    runner = _Runner(generation=3)
    runtime, _runner, _persistence, trace, recorder = _runtime(
        opened=False,
        runner=runner,
    )
    first, second = _observation(0), _observation(1)
    runtime.submit_completed_observation((0, 0), first)
    runner.generation = 4
    runtime.submit_completed_observation((0, 1), second)
    identity = trace.ensure_open(_trace_context(), timestamp_ms=1)
    assert identity is not None

    runtime.bind_generation(3)
    runner.drains.append(
        _drain(
            ObservationOutcomeEnvelope(1, 3, first, _outcome()),
            ObservationOutcomeEnvelope(2, 4, second, _outcome()),
        )
    )
    runtime.reconcile_outcomes(42.0)

    assert runner.bindings == [(3, identity.session_id, identity.cook_id)]
    assert [payload.rejection_reasons for payload in _observation_payloads(recorder)] == [
        ("insufficient-excitation",),
        ("observation-configuration-mismatch",),
    ]


def test_public_evidence_path_handles_absent_runner_and_blocked_store() -> None:
    persistence = _Persistence(blocked=True)
    runtime = HoldLearningRuntime(
        runner=None,
        model_store=None,
        persistence=persistence,
        trace=None,
        controller_name="mpc",
        logger=_LifecycleLogger(),
        initial_generation=0,
    )
    ordinary = _ordinary_evidence("ordinary")
    confidence = _confidence_evidence("confidence")

    runtime.persist_evidence((confidence, ordinary))

    assert persistence.batches == [(ordinary,)]
    assert runtime.evidence_available is False


def test_calibration_persistence_refusal_blocks_learner_submission() -> None:
    persistence = _Persistence(accepted=False)
    runtime, runner, _persistence, _trace_session, recorder = _runtime(persistence=persistence)
    allocation = allocate(
        0.25,
        u_max=0.5,
        fan_min_pct=10.0,
        fan_max_pct=100.0,
        enable_fan=True,
    )
    combined = allocate(
        0.35,
        u_max=0.5,
        fan_min_pct=10.0,
        fan_max_pct=100.0,
        enable_fan=True,
    )
    calibration = _observation(
        baseline_q=0.25,
        probe_q=0.10,
        requested_q=0.35,
        calibration_command_revision=7,
        calibration_command_action="start",
        baseline_allocation=allocation,
        combined_allocation=combined,
        calibration_status="active",
        calibration_stage="low",
        scheduled_on_s=5.0,
    )
    runtime.submit_completed_observation((0, 0), calibration)

    assert runtime.evidence_available is False
    assert runner.submissions == []
    assert [payload.reason for payload in _gap_payloads(recorder)] == ["model-persistence-unavailable"]


def test_trace_refusal_retains_record_for_public_reconciliation_retry() -> None:
    runtime, runner, _persistence, trace, recorder = _runtime()
    observation = _observation()
    runtime.submit_completed_observation((0, 0), observation)
    runner.drains.append(_drain(ObservationOutcomeEnvelope(1, 0, observation, _outcome())))
    original_record = trace.record
    trace.record = lambda *_args, **_kwargs: False

    runtime.reconcile_outcomes(22.0)
    assert _observation_payloads(recorder) == []

    trace.record = original_record
    runtime.reconcile_outcomes(23.0)

    assert [payload.observation_sequence for payload in _observation_payloads(recorder)] == [1]


def test_invalid_probe_trace_retry_is_fenced_by_generation_retirement() -> None:
    runtime, runner, _persistence, trace, recorder = _runtime()
    invalid = _observation(probe_valid=False)
    original_record = trace.record
    trace.record = lambda *_args, **_kwargs: False
    runtime.submit_completed_observation((0, 0), invalid)
    runtime.reconcile_outcomes(22.0)

    remaining = runtime.retire_generation(0)
    trace.record = original_record
    trace.rotate(runner_snapshot_fallback_safe=True)
    next_context = replace(_trace_context(), runner_generation=1)
    assert trace.ensure_open(next_context, timestamp_ms=23_000) is not None
    runtime.bind_generation(1)
    runtime.reconcile_outcomes(23.0)

    assert runner.submissions == []
    assert runner.retirements == [0]
    assert remaining == ()
    assert _observation_payloads(recorder) == []


def test_restore_model_clears_stale_authority_before_absent_checkpoint_noop() -> None:
    runtime, runner, store, trace, recorder, logger = _lifecycle_runtime()

    runtime.restore_model(timestamp_ms=1_250)

    assert store.loads == ["pid_sp"]
    assert runner.restored_models == []
    assert trace.model_authority is None
    assert _records(recorder, TraceEventKind.MODEL_EVENT) == []
    assert logger.infos == []
    assert logger.warnings == []


@pytest.mark.parametrize(
    ("asynchronous", "authority_provenance"),
    ((False, "restored"), (True, "restore_submitted")),
    ids=("synchronous", "asynchronous"),
)
def test_restore_model_records_accepted_sync_and_async_provenance(
    asynchronous: bool,
    authority_provenance: str,
) -> None:
    snapshot: dict[str, object] = {"revision": 3, "K": 700.0}
    runtime, runner, store, trace, recorder, logger = _lifecycle_runtime(
        snapshot=snapshot,
        asynchronous=asynchronous,
    )

    runtime.restore_model(timestamp_ms=1_250)

    assert store.loads == ["pid_sp"]
    assert runner.restored_models == [snapshot]
    authority = trace.model_authority
    assert authority is not None
    assert authority.snapshot == snapshot
    assert authority.provenance == authority_provenance
    [record] = _records(recorder, TraceEventKind.MODEL_EVENT)
    assert isinstance(record.payload, ModelEventPayload)
    assert record.ts_ms == 1_250
    assert record.payload.event is ModelEventType.RESTORE
    assert record.payload.provenance == "persisted"
    assert record.payload.detail == "stored model submitted for restore"
    assert logger.infos == ["Submitted the stored pid_sp model for restore"]
    assert logger.warnings == []


def test_an_async_restore_the_worker_refuses_is_reported_when_its_verdict_arrives() -> None:
    """An async runner answers "queued", so the verdict has to correct the record.

    Between submission and adoption the session has already logged a restore and
    stamped model authority from the stored snapshot. If the worker then refuses
    it, the controller is running the configured model while the trace still
    claims the persisted one.
    """
    snapshot: dict[str, object] = {"revision": 4, "K": 710.0}
    runtime, runner, _store, trace, recorder, logger = _lifecycle_runtime(
        snapshot=snapshot,
        restore_accepted=True,
        asynchronous=True,
    )

    runtime.restore_model(timestamp_ms=2_500)

    assert trace.model_authority is not None
    assert logger.infos == ["Submitted the stored pid_sp model for restore"]

    runner.restore_outcome = False
    runtime.reconcile_outcomes(3.0)

    assert trace.model_authority is None
    assert logger.warnings == ["Stored pid_sp model was rejected; starting fresh"]
    rejected = [
        record.payload.detail
        for record in _records(recorder, TraceEventKind.MODEL_EVENT)
        if isinstance(record.payload, ModelEventPayload) and record.payload.event is ModelEventType.REJECT
    ]
    assert rejected == ["stored model rejected for restore"]


def test_restore_model_records_runner_rejection_and_starts_fresh() -> None:
    snapshot: dict[str, object] = {"revision": 4, "K": 710.0}
    runtime, runner, store, trace, recorder, logger = _lifecycle_runtime(
        snapshot=snapshot,
        restore_accepted=False,
    )

    runtime.restore_model(timestamp_ms=2_500)

    assert store.loads == ["pid_sp"]
    assert runner.restored_models == [snapshot]
    assert trace.model_authority is None
    [record] = _records(recorder, TraceEventKind.MODEL_EVENT)
    assert isinstance(record.payload, ModelEventPayload)
    assert record.ts_ms == 2_500
    assert record.payload.event is ModelEventType.REJECT
    assert record.payload.provenance == "persisted"
    assert record.payload.detail == "stored model rejected for restore"
    assert logger.infos == []
    assert logger.warnings == ["Stored pid_sp model was rejected; starting fresh"]


def test_restore_model_leaves_invalid_checkpoint_behavior_with_the_store() -> None:
    runtime, runner, store, trace, recorder, logger = _lifecycle_runtime(
        load_error=ValueError("invalid checkpoint"),
    )

    with pytest.raises(ValueError, match="invalid checkpoint"):
        runtime.restore_model(timestamp_ms=3_750)

    assert store.loads == ["pid_sp"]
    assert runner.restored_models == []
    assert trace.model_authority is None
    assert _records(recorder, TraceEventKind.MODEL_EVENT) == []
    assert logger.infos == []
    assert logger.warnings == []


def _activation_lifecycle_record(
    state,
    *,
    evidence_id: str,
    timestamp_ms: int,
    fallback: bool,
) -> ModelEvidenceRecord:
    candidate = state.candidate_pair
    assert candidate is not None
    payload = (
        FallbackEvidence(
            decision_id=state.evidence_decision_id,
            reason="confidence-window-regressed",
            failed_digest=candidate.model_digest,
            failed_generation=candidate.role_generation,
        )
        if fallback
        else RollbackEvidence(
            decision_id=state.evidence_decision_id,
            reason="operator rollback",
        )
    )
    return ModelEvidenceRecord(
        evidence_id=evidence_id,
        kind=EvidenceKind.FALLBACK if fallback else EvidenceKind.ROLLBACK,
        session_id="activation-direct",
        cook_id=None,
        timestamp_ms=timestamp_ms,
        role_generation=candidate.role_generation,
        model_digest=candidate.model_digest,
        provenance_digest=None,
        payload=payload,
    )


def test_reconcile_activation_treats_absent_durable_state_as_noop(
    monkeypatch,
) -> None:
    from controller.runtime.modes import hold_learning as learning_module

    monkeypatch.setattr(learning_module, "read_model_activation", lambda: None)
    monkeypatch.setattr(learning_module, "read_model_evidence", lambda: ())
    runtime, runner, _store, _trace_session, _recorder, logger = _lifecycle_runtime(controller_name="mpc")

    runtime.reconcile_activation()
    runtime.reconcile_activation()

    assert runner.activation_restores == []
    assert runtime.evidence_available
    assert logger.warnings == []


def test_reconcile_activation_restores_each_prepared_active_and_aborted_identity_once(
    monkeypatch,
) -> None:
    from controller.runtime.modes import hold_learning as learning_module

    states = [
        _pair_phase_state(phase)[0]
        for phase in (
            ActivationPhase.PREPARED,
            ActivationPhase.ACTIVE,
            ActivationPhase.ABORTED,
        )
    ]
    selected = 0
    records: list[ModelEvidenceRecord] = []
    monkeypatch.setattr(
        learning_module,
        "read_model_activation",
        lambda: states[selected],
    )
    monkeypatch.setattr(learning_module, "read_model_evidence", lambda: records)
    runtime, runner, _store, _trace_session, _recorder, _logger = _lifecycle_runtime(controller_name="mpc")

    for index in range(len(states)):
        selected = index
        runtime.reconcile_activation()
        runtime.reconcile_activation()

    assert runner.activation_restores == [(state, ()) for state in states]


def test_reconcile_activation_rejects_retired_schema_identity(
    monkeypatch,
) -> None:
    from controller.runtime.modes import hold_learning as learning_module

    state, _record = _pair_phase_state()
    retired = replace(state, transaction_id=None)
    monkeypatch.setattr(learning_module, "read_model_activation", lambda: retired)
    monkeypatch.setattr(learning_module, "read_model_evidence", lambda: ())
    runtime, runner, _store, _trace_session, _recorder, logger = _lifecycle_runtime(controller_name="mpc")

    runtime.reconcile_activation()

    assert runner.activation_restores == []
    assert not runtime.evidence_available
    assert logger.warnings == ["Model activation authority uses a retired schema"]


@pytest.mark.parametrize("fallback", (False, True), ids=("rollback", "fallback"))
def test_reconcile_activation_applies_each_later_lifecycle_high_water_once(
    monkeypatch,
    fallback: bool,
) -> None:
    from controller.runtime.modes import hold_learning as learning_module

    state, _record = _pair_phase_state(ActivationPhase.ACTIVE)
    records: list[ModelEvidenceRecord] = []
    monkeypatch.setattr(learning_module, "read_model_activation", lambda: state)
    monkeypatch.setattr(learning_module, "read_model_evidence", lambda: records)
    runtime, runner, _store, _trace_session, _recorder, _logger = _lifecycle_runtime(controller_name="mpc")
    runtime.reconcile_activation()
    first = _activation_lifecycle_record(
        state,
        evidence_id="lifecycle-1",
        timestamp_ms=2_000,
        fallback=fallback,
    )
    second = _activation_lifecycle_record(
        state,
        evidence_id="lifecycle-2",
        timestamp_ms=3_000,
        fallback=fallback,
    )

    records.append(first)
    runtime.reconcile_activation()
    runtime.reconcile_activation()
    records.append(second)
    runtime.reconcile_activation()
    runtime.reconcile_activation()

    assert runner.activation_restores == [(state, ())]
    if fallback:
        assert runner.fallbacks == [
            "confidence-window-regressed",
            "confidence-window-regressed",
        ]
        assert runner.rollbacks == []
    else:
        assert runner.rollbacks == ["operator rollback", "operator rollback"]
        assert runner.fallbacks == []


def test_reconcile_activation_read_failure_marks_evidence_unavailable(
    monkeypatch,
) -> None:
    from controller.runtime.modes import hold_learning as learning_module

    def unavailable():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(learning_module, "read_model_activation", unavailable)
    monkeypatch.setattr(learning_module, "read_model_evidence", lambda: ())
    runtime, runner, _store, _trace_session, _recorder, logger = _lifecycle_runtime(controller_name="mpc")

    runtime.reconcile_activation()

    assert runner.activation_restores == []
    assert not runtime.evidence_available
    assert logger.warnings == ["Model activation state unavailable: database unavailable"]


@pytest.mark.parametrize("accepted", (True, False), ids=("accepted", "refused"))
def test_drain_activation_events_submits_one_ordered_atomic_batch(
    accepted: bool,
) -> None:
    first = _ordinary_evidence("activation-1")
    second = _ordinary_evidence("activation-2")
    runner = _LifecycleRunner()
    runner.activation_events.extend((first, second))
    persistence = _Persistence(accepted=accepted)
    runtime, _runner, _store, _trace_session, _recorder, logger = _lifecycle_runtime(
        runner=runner, persistence=persistence
    )

    runtime.drain_activation_events()
    runtime.drain_activation_events()

    assert persistence.batches == [(first, second)]
    assert runtime.evidence_available is accepted
    assert logger.warnings == ([] if accepted else ["Model activation fallback evidence was not persisted"])


def test_status_fragment_returns_a_copy_of_live_learning_status() -> None:
    learning = {
        "status": "fitting",
        "fit_status": "running",
        "role_generation": 7,
        "progress": {"accepted": 3},
    }
    runner = _LifecycleRunner()
    runner.status = {"learning": learning}
    runtime, _runner, _store, _trace_session, _recorder, _logger = _lifecycle_runtime(runner=runner)

    fragment = runtime.status_fragment()

    assert fragment == {"learning": learning}
    assert fragment["learning"] is not learning
    copied = fragment["learning"]
    assert isinstance(copied, dict)
    copied["status"] = "idle"
    assert learning["status"] == "fitting"
    progress = copied["progress"]
    assert isinstance(progress, dict)
    progress["accepted"] = 99
    assert learning["progress"] == {"accepted": 3}


@pytest.mark.parametrize("accepted", (True, False), ids=("accepted", "refused"))
def test_submit_online_checkpoint_uses_nonblocking_worker_and_availability(
    accepted: bool,
) -> None:
    persistence = _Persistence(checkpoint_results=(accepted,))
    runtime, _runner, _store, _trace_session, _recorder, _logger = _lifecycle_runtime(persistence=persistence)
    snapshot: dict[str, object] = {"revision": 9, "params": {"theta": 40.0}}

    result = runtime.submit_online_checkpoint(snapshot)

    assert result is accepted
    assert persistence.checkpoints == [("pid_sp", snapshot)]
    assert runtime.evidence_available is accepted


def _learning_settings(enabled: bool):
    return {
        "controller": {
            "config": {
                "pid_sp": {
                    "enable_identification": enabled,
                }
            }
        }
    }


@pytest.mark.parametrize(
    ("enabled", "verdict", "expected_outcome", "reason"),
    (
        (False, None, TeardownRefitOutcome.DISABLED, None),
        (True, None, TeardownRefitOutcome.INSUFFICIENT, "no reason recorded"),
        (
            True,
            TeardownRefitResult.rejected(
                "physical-bounds",
                origin=CandidateOrigin.COOK_REFIT,
            ),
            TeardownRefitOutcome.REJECTED,
            "physical-bounds",
        ),
        (
            True,
            TeardownRefitResult.ready_for_review(
                "operator review",
                candidate_digest="a" * 64,
            ),
            TeardownRefitOutcome.READY_FOR_REVIEW,
            "operator review",
        ),
        (
            True,
            TeardownRefitResult.accepted_next_cook(
                "accepted",
                candidate_digest="b" * 64,
            ),
            TeardownRefitOutcome.ACCEPTED_NEXT_COOK,
            "accepted",
        ),
    ),
    ids=(
        "disabled",
        "insufficient",
        "rejected",
        "ready-for-review",
        "accepted-next-cook",
    ),
)
def test_refit_once_returns_immutable_typed_outcome_and_never_repeats(
    enabled: bool,
    verdict: TeardownRefitResult | None,
    expected_outcome: TeardownRefitOutcome,
    reason: str | None,
) -> None:
    runner = _LifecycleRunner()
    runner.refit_result = verdict
    runtime, _runner, _store, _trace_session, _recorder, logger = _lifecycle_runtime(runner=runner)

    first = runtime.refit_once(_learning_settings(enabled))
    second = runtime.refit_once(_learning_settings(not enabled))

    assert first is second
    assert first.outcome is expected_outcome
    assert first.verdict is verdict
    assert runner.refit_calls == int(enabled)
    with pytest.raises(FrozenInstanceError):
        first.outcome = TeardownRefitOutcome.FAILED
    if not enabled:
        assert logger.infos == ["Model refit skipped at cook end: Learn This Grill is disabled."]
    else:
        assert logger.infos == [f"Model refit at cook end: {expected_outcome.value} ({reason})."]


@pytest.mark.parametrize(
    ("runner_result", "runner_error", "expected_error"),
    (
        (
            {"outcome": "accepted-next-cook"},
            None,
            "Model refit failed at cook end: invalid refit result",
        ),
        (
            None,
            RuntimeError("fit exploded"),
            "Model refit failed at cook end: fit exploded",
        ),
    ),
    ids=("malformed", "exception"),
)
def test_refit_once_turns_malformed_and_exception_results_into_typed_failure(
    runner_result: object,
    runner_error: BaseException | None,
    expected_error: str,
) -> None:
    runner = _LifecycleRunner()
    runner.refit_result = runner_result
    runner.refit_error = runner_error
    runtime, _runner, _store, _trace_session, _recorder, logger = _lifecycle_runtime(runner=runner)

    result = runtime.refit_once(_learning_settings(True))

    assert result.outcome is TeardownRefitOutcome.FAILED
    assert result.verdict is None
    assert runner.refit_calls == 1
    assert logger.errors == [expected_error]


@pytest.mark.parametrize(
    ("enabled", "verdict", "expected_events"),
    (
        (
            False,
            None,
            (ModelEventType.REFIT,),
        ),
        (
            True,
            TeardownRefitResult.rejected(
                "physical-bounds",
                origin=CandidateOrigin.COOK_REFIT,
            ),
            (ModelEventType.REFIT, ModelEventType.REJECT),
        ),
        (
            True,
            TeardownRefitResult.ready_for_review(
                "operator review",
                candidate_digest="c" * 64,
            ),
            (ModelEventType.REFIT, ModelEventType.ADOPT),
        ),
    ),
    ids=("disabled", "rejected", "adopted"),
)
def test_publish_final_checkpoint_records_exact_trace_events_and_authority(
    enabled: bool,
    verdict: TeardownRefitResult | None,
    expected_events: tuple[ModelEventType, ...],
) -> None:
    runner = _LifecycleRunner()
    runner.refit_result = verdict
    runner.snapshot = {"revision": 11, "params": {"theta": 40.0}}
    persistence = _Persistence()
    runtime, _runner, _store, trace, recorder, _logger = _lifecycle_runtime(
        runner=runner,
        persistence=persistence,
    )
    refit = runtime.refit_once(_learning_settings(enabled))

    assert runtime.publish_final_checkpoint_once(refit, timestamp_ms=6_000)

    payloads = [
        record.payload
        for record in _records(recorder, TraceEventKind.MODEL_EVENT)
        if isinstance(record.payload, ModelEventPayload)
    ]
    assert tuple(payload.event for payload in payloads) == expected_events
    assert all(record.ts_ms == 6_000 for record in _records(recorder, TraceEventKind.MODEL_EVENT))
    assert trace.model_authority is None
    assert persistence.checkpoints == [("pid_sp", runner.snapshot)]
    before = (tuple(runner.finalized), tuple(persistence.checkpoints))
    assert runtime.publish_final_checkpoint_once(refit, timestamp_ms=7_000)
    assert (tuple(runner.finalized), tuple(persistence.checkpoints)) == before


@pytest.mark.parametrize("failure", ("refusal", "exception"))
def test_publish_final_checkpoint_never_queues_snapshot_stale_before_finalization_failure(
    failure: str,
) -> None:
    runner = _LifecycleRunner()
    runner.snapshot = {"revision": 1, "stale": True}
    if failure == "refusal":
        runner.finalize_results = [False, True]
    else:
        runner.finalize_errors = [RuntimeError("finalize exploded"), None]
        runner.finalize_results = [True]
    persistence = _Persistence()
    runtime, _runner, _store, _trace_session, _recorder, _logger = _lifecycle_runtime(
        runner=runner, persistence=persistence
    )
    refit = runtime.refit_once(_learning_settings(False))

    assert runtime.publish_final_checkpoint_once(refit, timestamp_ms=8_000)

    assert runner.finalized == [
        TeardownRefitOutcome.DISABLED,
        TeardownRefitOutcome.CHECKPOINT_FAILURE,
    ]
    assert len(persistence.checkpoints) == 1
    submitted = persistence.checkpoints[0][1]
    assert submitted["cook_refit"] == {"latest": "checkpoint-failure"}
    assert "stale" not in submitted


@pytest.mark.parametrize("snapshot", (None, ["malformed"]), ids=("missing", "malformed"))
def test_publish_final_checkpoint_makes_missing_or_malformed_snapshot_terminal(
    snapshot: object,
) -> None:
    runner = _LifecycleRunner()
    runner.snapshot = snapshot
    persistence = _Persistence()
    runtime, _runner, _store, _trace_session, _recorder, _logger = _lifecycle_runtime(
        runner=runner, persistence=persistence
    )
    refit = runtime.refit_once(_learning_settings(False))

    first = runtime.publish_final_checkpoint_once(refit, timestamp_ms=9_000)
    second = runtime.publish_final_checkpoint_once(refit, timestamp_ms=10_000)

    assert not first
    assert not second
    assert runner.finalized == [
        TeardownRefitOutcome.DISABLED,
        TeardownRefitOutcome.CHECKPOINT_FAILURE,
    ]
    assert persistence.checkpoints == []
    assert not runtime.evidence_available


def test_publish_final_checkpoint_bounds_authoritative_retry_and_is_idempotent() -> None:
    runner = _LifecycleRunner()
    verdict = TeardownRefitResult.accepted_next_cook(
        "accepted",
        candidate_digest="d" * 64,
    )
    runner.refit_result = verdict
    runner.finalize_results = [True, True]
    runner.snapshot = {"revision": 12, "cook_refit": {"latest": "accepted-next-cook"}}
    persistence = _Persistence(checkpoint_results=(False, True))
    runtime, _runner, _store, _trace_session, _recorder, _logger = _lifecycle_runtime(
        runner=runner, persistence=persistence
    )
    refit = runtime.refit_once(_learning_settings(True))

    assert runtime.publish_final_checkpoint_once(refit, timestamp_ms=11_000)
    assert runtime.publish_final_checkpoint_once(refit, timestamp_ms=12_000)

    assert runner.finalized == [
        TeardownRefitOutcome.ACCEPTED_NEXT_COOK,
        TeardownRefitOutcome.CHECKPOINT_FAILURE,
    ]
    assert [
        cast(Mapping[str, object], snapshot["cook_refit"])["latest"] for _name, snapshot in persistence.checkpoints
    ] == ["accepted-next-cook", "checkpoint-failure"]
    assert not runtime.evidence_available


def test_finish_teardown_orders_retire_barrier_trace_close_runner_finish_once() -> None:
    events: list[object] = []
    runner = _LifecycleRunner(events=events)
    persistence = _Persistence(events=events)
    recorder = _Recorder(events=events)
    trace, _recorder = _trace(recorder=recorder)
    runtime, _runner, _store, _trace_session, _recorder, _logger = _lifecycle_runtime(
        runner=runner,
        persistence=persistence,
        trace=trace,
        recorder=recorder,
    )

    runtime.finish_teardown(generation=4)
    runtime.finish_teardown(generation=4)

    assert events == [
        ("runner:retire", 4),
        "persistence:barrier",
        "trace:close",
        "runner:finish",
    ]
    assert runner.retirements == [4]
    assert persistence.barrier_calls == 1
    assert recorder.close_calls == 1
    assert runner.finish_calls == 1


@pytest.mark.parametrize("failure", ("refusal", "timeout"))
def test_finish_teardown_marks_barrier_failure_and_still_finishes_resources(
    failure: str,
) -> None:
    runner = _LifecycleRunner()
    persistence = _Persistence(
        barrier_result=failure != "refusal",
        barrier_error=(
            TimeoutError("barrier timed out") if failure == "timeout" else None
        ),
    )
    runtime, _runner, _store, _trace_session, recorder, _logger = _lifecycle_runtime(
        runner=runner, persistence=persistence
    )

    runtime.finish_teardown(generation=5)
    runtime.finish_teardown(generation=5)

    assert runner.finalized == [TeardownRefitOutcome.CHECKPOINT_FAILURE]
    assert persistence.barrier_calls == 1
    assert recorder.close_calls == 1
    assert runner.finish_calls == 1
    assert not runtime.evidence_available


def test_finish_teardown_trace_and_runner_finish_exceptions_are_terminal_once() -> None:
    events: list[object] = []
    warnings: list[str] = []
    recorder = _Recorder(
        events=events,
        close_error=RuntimeError("trace exploded"),
    )
    trace, _recorder = _trace(recorder=recorder, warnings=warnings)
    runner = _LifecycleRunner(events=events)
    runner.finish_error = RuntimeError("runner exploded")
    logger = _LifecycleLogger()
    runtime, _runner, _store, _trace_session, _recorder, _logger = _lifecycle_runtime(
        runner=runner,
        trace=trace,
        recorder=recorder,
        logger=logger,
    )

    runtime.finish_teardown(generation=6)
    runtime.finish_teardown(generation=6)

    assert warnings == ["Control trace close failed: trace exploded"]
    assert logger.warnings == ["Controller teardown close failed: runner exploded"]
    assert recorder.close_calls == 1
    assert runner.finish_calls == 1


def test_finish_teardown_with_missing_persistence_and_trace_still_finishes_runner() -> None:
    runner = _LifecycleRunner()
    logger = _LifecycleLogger()
    runtime = HoldLearningRuntime(
        runner=runner,
        model_store=None,
        persistence=None,
        trace=None,
        controller_name="pid_sp",
        logger=logger,
        initial_generation=0,
    )

    runtime.finish_teardown(generation=0)
    runtime.finish_teardown(generation=0)

    assert runner.retirements == [0]
    assert runner.finish_calls == 1
    assert logger.warnings == []


def test_partial_lifecycle_calls_preserve_noop_and_failure_boundaries() -> None:
    store = _ModelStore({"revision": 1})
    logger = _LifecycleLogger()
    runtime = HoldLearningRuntime(
        runner=None,
        model_store=store,
        persistence=None,
        trace=None,
        controller_name="pid_sp",
        logger=logger,
        initial_generation=0,
    )

    runtime.restore_model(timestamp_ms=1_000, controller_name="mpc")
    runtime.reconcile_activation()
    runtime.drain_activation_events()
    refit = runtime.refit_once(
        {
            "controller": {
                "config": {
                    "mpc": {
                        "enable_identification": True,
                    }
                }
            }
        }
    )
    published = runtime.publish_final_checkpoint_once(
        refit,
        timestamp_ms=2_000,
    )
    runtime.finish_teardown(generation=0)

    assert store.loads == []
    assert runtime.status_fragment() == {}
    assert refit == HoldRefitResult(
        TeardownRefitOutcome.INSUFFICIENT,
        None,
    )
    assert not published
    assert not runtime.evidence_available
    assert logger.warnings == []


@pytest.mark.parametrize(
    ("status", "status_error"),
    (
        (None, None),
        ([], None),
        ({}, None),
        ({"learning": []}, None),
        ({}, RuntimeError("status unavailable")),
    ),
    ids=(
        "none",
        "non-mapping",
        "missing-learning",
        "non-mapping-learning",
        "exception",
    ),
)
def test_status_fragment_rejects_invalid_public_runner_status(
    status: object,
    status_error: BaseException | None,
) -> None:
    runner = _LifecycleRunner()
    runner.status = status
    runner.status_error = status_error
    runtime, _runner, _store, _trace_session, _recorder, _logger = _lifecycle_runtime(runner=runner)

    assert runtime.status_fragment() == {}


@pytest.mark.parametrize(
    "settings",
    (
        {"controller": []},
        {"controller": {"config": []}},
        {"controller": {"config": {"pid_sp": []}}},
    ),
    ids=("controller", "config", "selected"),
)
def test_refit_once_rejects_malformed_settings_as_disabled(
    settings,
) -> None:
    runtime, runner, _store, _trace_session, _recorder, logger = _lifecycle_runtime()

    result = runtime.refit_once(settings)

    assert result == HoldRefitResult(TeardownRefitOutcome.DISABLED, None)
    assert runner.refit_calls == 0
    assert len(logger.errors) == 1
    assert logger.errors[0].startswith("Model refit failed at cook end:")


def test_controller_switch_does_not_dedupe_an_identical_checkpoint_for_new_owner() -> None:
    snapshot: dict[str, object] = {"revision": 7}
    runner = _LifecycleRunner()
    runner.snapshot = snapshot
    store = _ModelStore(snapshot)
    persistence = _Persistence(checkpoint_results=(True, True))
    runtime = HoldLearningRuntime(
        runner=runner,
        model_store=store,
        persistence=persistence,
        trace=None,
        controller_name="pid_sp",
        logger=_LifecycleLogger(),
        initial_generation=0,
    )

    assert runtime.submit_online_checkpoint(snapshot)
    runtime.restore_model(timestamp_ms=1_000, controller_name="mpc")
    published = runtime.publish_final_checkpoint_once(
        HoldRefitResult(TeardownRefitOutcome.DISABLED, None),
        timestamp_ms=2_000,
    )

    assert store.loads == ["mpc"]
    assert runner.restored_models == [snapshot]
    assert published
    assert persistence.checkpoints == [
        ("pid_sp", snapshot),
        ("mpc", snapshot),
    ]


def test_failed_identical_checkpoint_stops_when_failure_finalization_refuses() -> None:
    snapshot: dict[str, object] = {"revision": 8}
    runner = _LifecycleRunner()
    runner.snapshot = snapshot
    runner.finalize_results = [True, False]
    persistence = _Persistence(checkpoint_results=(False,))
    runtime, _runner, _store, _trace_session, _recorder, _logger = _lifecycle_runtime(
        runner=runner, persistence=persistence
    )

    assert not runtime.submit_online_checkpoint(snapshot)
    published = runtime.publish_final_checkpoint_once(
        HoldRefitResult(TeardownRefitOutcome.DISABLED, None),
        timestamp_ms=3_000,
    )

    assert not published
    assert runner.finalized == [
        TeardownRefitOutcome.DISABLED,
        TeardownRefitOutcome.CHECKPOINT_FAILURE,
    ]
    assert persistence.checkpoints == [("pid_sp", snapshot)]


def test_checkpoint_failure_outcome_does_not_retry_a_refused_snapshot() -> None:
    snapshot: dict[str, object] = {"revision": 9}
    runner = _LifecycleRunner()
    runner.snapshot = snapshot
    persistence = _Persistence(checkpoint_results=(False,))
    runtime, _runner, _store, _trace_session, _recorder, _logger = _lifecycle_runtime(
        runner=runner, persistence=persistence
    )

    published = runtime.publish_final_checkpoint_once(
        HoldRefitResult(TeardownRefitOutcome.CHECKPOINT_FAILURE, None),
        timestamp_ms=4_000,
    )

    assert not published
    assert runner.finalized == [TeardownRefitOutcome.CHECKPOINT_FAILURE]
    assert len(persistence.checkpoints) == 1
    submitted = persistence.checkpoints[0][1]
    assert submitted["cook_refit"] == {"latest": "checkpoint-failure"}


def test_authoritative_retry_rejects_a_malformed_refinalized_snapshot() -> None:
    class _MalformedRetryRunner(_LifecycleRunner):
        def finalize_cook_refit(
            self,
            outcome: TeardownRefitOutcome,
        ) -> bool:
            accepted = super().finalize_cook_refit(outcome)
            if outcome is TeardownRefitOutcome.CHECKPOINT_FAILURE:
                self.snapshot = None
            return accepted

    snapshot: dict[str, object] = {"revision": 10}
    runner = _MalformedRetryRunner()
    runner.snapshot = snapshot
    runner.finalize_results = [True, True]
    persistence = _Persistence(checkpoint_results=(False,))
    runtime, _runner, _store, _trace_session, _recorder, _logger = _lifecycle_runtime(
        runner=runner, persistence=persistence
    )

    published = runtime.publish_final_checkpoint_once(
        HoldRefitResult(TeardownRefitOutcome.DISABLED, None),
        timestamp_ms=5_000,
    )

    assert not published
    assert runner.finalized == [
        TeardownRefitOutcome.DISABLED,
        TeardownRefitOutcome.CHECKPOINT_FAILURE,
    ]
    assert persistence.checkpoints == [("pid_sp", snapshot)]


def test_finish_teardown_contains_retirement_and_trace_flush_exceptions() -> None:
    class _RetireFailureRunner(_LifecycleRunner):
        def retire_evidence_context(self, generation: int) -> None:
            raise RuntimeError(f"retire {generation} exploded")

    class _FlushOnceTrace(ControlTraceSession):
        def __init__(self, recorder: _Recorder) -> None:
            super().__init__(recorder, warning=lambda _message: None)
            self.fail_next_flush = False
            self.flush_calls = 0

        def flush_pending(self) -> None:
            self.flush_calls += 1
            if self.fail_next_flush:
                self.fail_next_flush = False
                raise RuntimeError("flush exploded")
            super().flush_pending()

    recorder = _Recorder()
    trace = _FlushOnceTrace(recorder)
    identity = trace.ensure_open(_trace_context(), timestamp_ms=0)
    trace.fail_next_flush = True
    assert identity is not None
    runner = _RetireFailureRunner()
    logger = _LifecycleLogger()
    runtime, _runner, _store, _trace_session, _recorder, _logger = _lifecycle_runtime(
        runner=runner,
        trace=trace,
        recorder=recorder,
        logger=logger,
    )

    runtime.finish_teardown(generation=7)

    assert logger.warnings == [
        "Controller evidence retirement failed: retire 7 exploded",
        "Control trace flush failed: flush exploded",
    ]
    assert recorder.close_calls == 1
    assert runner.finish_calls == 1
