from __future__ import annotations

from dataclasses import replace
from collections.abc import Sequence
from typing import cast

import pytest

from common.control_trace import (
    ControllerType,
    HorizonScorePayload,
    ModelEvaluationPayload,
    ModelEventPayload,
    ModelObservationPayload,
    RecorderGapPayload,
    TraceEventKind,
    TraceSetting,
    ControlTraceRecord,
)
from common.model_evidence import (
    ConfidenceDecisionEvidence,
    CalibrationSummaryEvidence,
    EvidenceKind,
    ModelEvidenceRecord,
    RecorderGapEvidence,
)
from controller.applied_output import AppliedOutput, OutputSource
from controller.model_learning.contracts import FrameObservation
from controller.mpc_allocator import allocate
from controller.runtime.control_trace_session import (
    ControlTraceSession,
    TraceSessionContext,
)
from controller.runtime.model_persistence import DurableActivationReceipt, EvidenceSubmission
from controller.runtime.modes.hold_learning import HoldLearningRuntime
from controller.runtime.runner import (
    ObservationOutcomeDrain,
    ObservationOutcomeEnvelope,
    ObservationSubmission,
    ObservationTerminalDrop,
)


class _Recorder:
    def __init__(self) -> None:
        self.records: list[ControlTraceRecord] = []

    def record(self, record: ControlTraceRecord) -> None:
        self.records.append(record)

    def flush_due(self, now_ms: int) -> None:
        del now_ms

    def close(self) -> None:
        return None


class _Persistence:
    def __init__(self, *, accepted: bool = True, blocked: bool = False, events=None) -> None:
        self.accepted = accepted
        self.evidence_blocked = blocked
        self.batches: list[tuple[ModelEvidenceRecord, ...]] = []
        self.events = [] if events is None else events

    def submit_evidence_batch(
        self, records: Sequence[ModelEvidenceRecord]
    ) -> EvidenceSubmission:
        batch = tuple(records)
        self.batches.append(batch)
        self.events.append(("batch", batch))
        return EvidenceSubmission(accepted=self.accepted)


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

    def complete_frame(
        self, applied: AppliedOutput, observation: FrameObservation
    ) -> ObservationSubmission | None:
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


class _NoSubmissionRunner(_Runner):
    def observe_frame(self, observation: FrameObservation) -> None:
        self.submissions.append(observation)
        return None


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
        "incumbent_innovation_c": None,
        "challenger_innovation_c": None,
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


def _trace(*, opened: bool = True):
    recorder = _Recorder()
    trace = ControlTraceSession(recorder, warning=lambda _message: None)
    if opened:
        identity = trace.ensure_open(_trace_context(), timestamp_ms=0)
        assert identity is not None
    return trace, recorder


def _runtime(*, opened: bool = True, runner=None, persistence=None):
    trace, recorder = _trace(opened=opened)
    actual_runner = _Runner() if runner is None else runner
    actual_persistence = _Persistence() if persistence is None else persistence
    runtime = HoldLearningRuntime(
        runner=actual_runner,
        persistence=actual_persistence,
        trace=trace,
        initial_generation=actual_runner.generation,
    )
    return runtime, actual_runner, actual_persistence, trace, recorder


def _records(recorder: _Recorder, kind: TraceEventKind):
    return [record for record in recorder.records if record.event_kind is kind]

def _gap_payloads(recorder: _Recorder) -> list[RecorderGapPayload]:
    return [
        record.payload
        for record in recorder.records
        if isinstance(record.payload, RecorderGapPayload)
    ]


def _observation_payloads(recorder: _Recorder) -> list[ModelObservationPayload]:
    return [
        record.payload
        for record in recorder.records
        if isinstance(record.payload, ModelObservationPayload)
    ]


def _evaluation_payloads(recorder: _Recorder) -> list[ModelEvaluationPayload]:
    return [
        record.payload
        for record in recorder.records
        if isinstance(record.payload, ModelEvaluationPayload)
    ]


def test_accepted_outcome_keeps_exact_frame_feedback_identity_and_reconciles_once() -> None:
    runtime, runner, _persistence, _trace_session, recorder = _runtime()
    observation = _observation()
    feedback = AppliedOutput(0.25, OutputSource.CONTROLLER, 20.0, requested=0.25)

    runtime.submit_completed_observation((1, 2), observation, feedback)
    runner.drains.append(
        _drain(ObservationOutcomeEnvelope(1, 0, observation, _outcome()))
    )
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


def test_submission_eviction_terminal_drop_and_dropped_sequence_are_consumed_once() -> None:
    runtime, runner, persistence, _trace_session, recorder = _runtime()
    first, second = _observation(0), _observation(1)
    runtime.submit_completed_observation((0, 0), first)
    runner.next_evicted_sequence = 1
    runtime.submit_completed_observation((0, 1), second)
    runner.drains.append(
        _drain(
            terminal_drops=(
                ObservationTerminalDrop(2, 0, second, "runner-outcome-evicted"),
            ),
            dropped_sequences=(2,),
        )
    )

    runtime.reconcile_outcomes(42.0)
    runtime.reconcile_outcomes(43.0)

    trace_reasons = [
        cast(RecorderGapPayload, record.payload).reason
        for record in _records(recorder, TraceEventKind.RECORDER_GAP)
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
    assert [(gap.observation_sequence, gap.reason) for gap in gaps] == [
        (1, "pending-observation-overflow")
    ]
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
        cast(ModelObservationPayload, record.payload)
        for record in _records(recorder, TraceEventKind.MODEL_OBSERVATION)
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
    runner.drains.append(
        _drain(ObservationOutcomeEnvelope(1, 0, observation, _outcome()))
    )
    runtime.reconcile_outcomes(22.0)

    assert remaining_generations == ()
    assert runner.retirements == [0]
    assert _records(recorder, TraceEventKind.MODEL_OBSERVATION) == []


def test_evidence_preserves_split_channel_order_and_evaluation_trace() -> None:
    events = []
    runner = _Runner(events=events)
    persistence = _Persistence(events=events)
    runtime, _runner, _persistence, _trace_session, recorder = _runtime(
        runner=runner, persistence=persistence
    )
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
    assert [payload.decision_id for payload in _evaluation_payloads(recorder)] == [
        "evaluation-1"
    ]


def test_persistence_refusals_mark_evidence_unavailable_without_skipping_a_channel() -> None:
    events = []
    runner = _Runner(events=events)
    runner.confidence_accepted = False
    persistence = _Persistence(accepted=False, events=events)
    runtime, _runner, _persistence, _trace_session, _recorder = _runtime(
        runner=runner, persistence=persistence
    )
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
    assert [payload.reason for payload in _gap_payloads(recorder)] == [
        "model-persistence-unavailable"
    ]


def test_runner_submission_exception_is_not_hidden_or_partially_accepted() -> None:
    runner = _Runner()
    runner.raise_on_observe = RuntimeError("learner submission failed")
    runtime, _runner, persistence, _trace_session, recorder = _runtime(runner=runner)

    with pytest.raises(RuntimeError, match="learner submission failed"):
        runtime.submit_completed_observation((0, 0), _observation())

    assert persistence.batches == []
    assert _records(recorder, TraceEventKind.MODEL_OBSERVATION) == []


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
        batch[0]
        for batch in persistence.batches
        if batch[0].kind is EvidenceKind.CALIBRATION_SUMMARY
    ]
    calibration_payloads = [
        cast(CalibrationSummaryEvidence, record.payload)
        for record in calibration_records
    ]
    assert len(calibration_payloads) == 2
    assert [payload.command_revision for payload in calibration_payloads] == [7, 7]
    assert [payload.delivered_on_seconds for payload in calibration_payloads] == [5.0, 5.0]
    assert runner.submissions == [valid]
    invalid_payloads = [
        payload
        for payload in _observation_payloads(recorder)
        if payload.observation_sequence == 2
    ]
    assert [payload.rejection_reasons for payload in invalid_payloads] == [
        ("invalid-probe",)
    ]


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
    assert [
        payload.observation_sequence
        for payload in _observation_payloads(recorder)
    ] == [1, 2]


def test_missing_collaborators_preserve_public_noop_boundaries() -> None:
    runtime = HoldLearningRuntime(
        runner=None,
        persistence=None,
        trace=None,
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
    runtime, _runner, _persistence, _trace_session, recorder = _runtime(
        runner=runner
    )
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

    assert [
        payload.rejection_reasons
        for payload in _observation_payloads(recorder)
    ] == [
        ("allocation-result-missing",),
        ("invalid-probe",),
        ("observation-role-generation-mismatch",),
        ("observation-gate-mismatch",),
        ("observation-outcome-malformed",),
    ]
    assert [
        payload.decision_id for payload in _evaluation_payloads(recorder)
    ] == ["evaluation-1"]


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
    assert [
        payload.rejection_reasons
        for payload in _observation_payloads(recorder)
    ] == [
        ("insufficient-excitation",),
        ("observation-configuration-mismatch",),
    ]


def test_public_evidence_path_handles_absent_runner_and_blocked_store() -> None:
    persistence = _Persistence(blocked=True)
    runtime = HoldLearningRuntime(
        runner=None,
        persistence=persistence,
        trace=None,
        initial_generation=0,
    )
    ordinary = _ordinary_evidence("ordinary")
    confidence = _confidence_evidence("confidence")

    runtime.persist_evidence((confidence, ordinary))

    assert persistence.batches == [(ordinary,)]
    assert runtime.evidence_available is False


def test_calibration_persistence_refusal_blocks_learner_submission() -> None:
    persistence = _Persistence(accepted=False)
    runtime, runner, _persistence, _trace_session, recorder = _runtime(
        persistence=persistence
    )
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
    assert [payload.reason for payload in _gap_payloads(recorder)] == [
        "model-persistence-unavailable"
    ]


def test_trace_refusal_retains_record_for_public_reconciliation_retry() -> None:
    runtime, runner, _persistence, trace, recorder = _runtime()
    observation = _observation()
    runtime.submit_completed_observation((0, 0), observation)
    runner.drains.append(
        _drain(ObservationOutcomeEnvelope(1, 0, observation, _outcome()))
    )
    original_record = trace.record
    trace.record = lambda *_args, **_kwargs: False

    runtime.reconcile_outcomes(22.0)
    assert _observation_payloads(recorder) == []

    trace.record = original_record
    runtime.reconcile_outcomes(23.0)

    assert [
        payload.observation_sequence
        for payload in _observation_payloads(recorder)
    ] == [1]


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
