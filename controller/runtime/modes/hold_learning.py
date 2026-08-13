"""Typed Hold observation reconciliation and learning-evidence ownership."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal, Protocol, cast

from common.control_trace import (
    AllocationPayload,
    ControlTracePayload,
    ModelEvaluationPayload,
    ModelEventPayload,
    ModelEventType,
    ModelObservationPayload,
    RecorderGapPayload,
    TraceEventKind,
    TraceSetting,
)
from common.model_evidence import (
    AllocationEvidence,
    CalibrationSummaryEvidence,
    EvidenceKind,
    ModelEvidenceRecord,
    RecorderGapEvidence,
)
from controller.applied_output import AppliedOutput, OutputSource
from controller.model_learning.contracts import FrameObservation
from controller.mpc_allocator import AllocationResult
from controller.runtime.control_trace_session import (
    ControlTraceSession,
    TraceSessionIdentity,
)
from controller.runtime.model_persistence import EvidenceSubmission
from controller.runtime.runner import (
    ObservationOutcomeDrain,
    ObservationSubmission,
)


type _FrameKey = tuple[int, int]
type _OutcomeScalar = None | bool | int | float | str | ModelEvaluationPayload
type _OutcomeValue = (
    _OutcomeScalar
    | Mapping[str, "_OutcomeValue"]
    | tuple["_OutcomeValue", ...]
    | list["_OutcomeValue"]
)
type _Outcome = Mapping[str, _OutcomeValue]
type _TraceRecord = tuple[TraceEventKind, ControlTracePayload]

type _CalibrationCommandAction = Literal[
    "none",
    "start",
    "pause",
    "resume",
    "stop",
    "reset-progress",
    "safety-cancel",
]
type _CancellationCommandAction = Literal[
    "none",
    "pause",
    "stop",
    "reset-progress",
    "safety-cancel",
]
type _CalibrationStatus = Literal[
    "inactive",
    "accepted",
    "rejected",
    "active",
    "cancelled",
]
type _CalibrationStage = Literal["low", "middle", "high", "coast"]


@dataclass(frozen=True, slots=True)
class _ParsedOutcome:
    eligible: bool
    rejection_reasons: tuple[str, ...]
    input_variance: float
    input_levels: int
    incumbent_innovation_c: float | None
    challenger_innovation_c: float | None
    effective_updates: int
    role_generation: int
    model_digest: str | None




def parse_model_lifecycle_payload(
    value: Mapping[str, object] | None,
) -> ModelEventPayload | None:
    """Validate raw lifecycle diagnostics at the learning/trace boundary."""
    if value is None:
        return None
    try:
        raw_parameters = value["parameters"]
        if not isinstance(raw_parameters, tuple | list):
            return None
        parameters = tuple(
            parameter
            if isinstance(parameter, TraceSetting)
            else TraceSetting(
                key=cast(str, cast(Mapping[str, object], parameter)["key"]),
                value=cast(
                    str | int | float | bool,
                    cast(Mapping[str, object], parameter)["value"],
                ),
            )
            for parameter in raw_parameters
        )
        return ModelEventPayload(
            event=ModelEventType(cast(str, value["event"])),
            model_revision=cast(int | None, value["model_revision"]),
            provenance=cast(str | None, value["provenance"]),
            detail=cast(str, value["detail"]),
            model_kind=cast(str | None, value["model_kind"]),
            model_schema=cast(str | None, value["model_schema"]),
            role_generation=cast(int | None, value["role_generation"]),
            snapshot_digest=cast(str | None, value["snapshot_digest"]),
            parameters=parameters,
        )
    except (KeyError, TypeError, ValueError):
        return None


class _ActivationConfidenceReceipt(Protocol):
    accepted: bool


class _HoldLearningRunner(Protocol):
    """Narrow runner surface used by observation/evidence reconciliation."""

    def observe_frame(
        self, observation: FrameObservation
    ) -> ObservationSubmission | None: ...

    def complete_frame(
        self,
        applied: AppliedOutput,
        observation: FrameObservation,
    ) -> ObservationSubmission | None: ...

    def drain_observation_outcomes(self) -> ObservationOutcomeDrain: ...

    def bind_evidence_context(
        self,
        generation: int,
        session_id: str,
        cook_id: str | None,
    ) -> None: ...

    def retire_evidence_context(self, generation: int) -> None: ...

    def submit_activation_confidence(
        self,
        record: ModelEvidenceRecord,
    ) -> _ActivationConfidenceReceipt | None: ...


class _EvidencePersistence(Protocol):
    """Nonblocking evidence-only persistence boundary."""

    @property
    def evidence_blocked(self) -> bool: ...

    def submit_evidence_batch(
        self,
        records: Sequence[ModelEvidenceRecord],
    ) -> EvidenceSubmission: ...


@dataclass(frozen=True, slots=True)
class _PendingObservation:
    frame_key: _FrameKey
    observation: FrameObservation
    trace_identity: TraceSessionIdentity | None
    configuration_generation: int
    records: tuple[_TraceRecord, ...] | None = None


class HoldLearningRuntime:
    """Own Hold's bounded observation table and learning evidence effects."""

    _PENDING_CAPACITY = 60

    def __init__(
        self,
        *,
        runner: _HoldLearningRunner | None,
        persistence: _EvidencePersistence | None,
        trace: ControlTraceSession | None,
        initial_generation: int,
    ) -> None:
        self._runner = runner
        self._persistence = persistence
        self._trace = trace
        self._generation = initial_generation
        self._pending: dict[int, _PendingObservation] = {}
        self._evidence_available = True

    @property
    def evidence_available(self) -> bool:
        return self._evidence_available

    def mark_evidence_unavailable(self) -> None:
        self._evidence_available = False

    def submit_completed_observation(
        self,
        frame_key: _FrameKey,
        observation: FrameObservation,
        feedback: AppliedOutput | None = None,
    ) -> None:
        """Submit one completed frame while retaining its exact immutable identity."""
        self._submit_calibration_frame_evidence(observation)
        if not observation.probe_valid:
            sequence = -1
            while sequence in self._pending:
                sequence -= 1
            self._pending[sequence] = _PendingObservation(
                frame_key=frame_key,
                observation=observation,
                trace_identity=self._trace_identity,
                configuration_generation=self._generation,
                records=(
                    (
                        TraceEventKind.MODEL_OBSERVATION,
                        self._rejected_model_observation(observation, "invalid-probe"),
                    ),
                ),
            )
            self._bound_pending()
            return

        persistence = self._persistence
        if not self._evidence_available or (
            persistence is not None and persistence.evidence_blocked
        ):
            self._evidence_available = False
            self.record_gap(observation, "model-persistence-unavailable")
            return

        runner = self._runner
        if runner is None:
            return
        submission = (
            runner.complete_frame(feedback, observation)
            if feedback is not None
            else runner.observe_frame(observation)
        )
        if submission is None:
            return
        sequence = submission.submission_sequence
        generation = submission.configuration_generation
        if (
            isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence < 0
            or isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation < 0
        ):
            return
        self._pending[sequence] = _PendingObservation(
            frame_key=frame_key,
            observation=observation,
            trace_identity=self._trace_identity,
            configuration_generation=generation,
        )
        evicted_sequence = submission.evicted_sequence
        if isinstance(evicted_sequence, int) and not isinstance(
            evicted_sequence, bool
        ):
            self._retire_pending(evicted_sequence, "runner-observation-evicted")
        self._bound_pending()

    def reconcile_outcomes(self, publication_time_s: float) -> None:
        """Consume each public runner outcome once and flush trace effects FIFO."""
        runner = self._runner
        if not self._pending or runner is None:
            return
        publication_ms = int(publication_time_s * 1_000)
        batch = runner.drain_observation_outcomes()
        for drop in batch.terminal_drops:
            self._retire_pending(drop.submission_sequence, drop.reason)

        for envelope in batch.envelopes:
            sequence = envelope.submission_sequence
            pending = self._pending.get(sequence)
            if pending is None:
                continue
            if (
                envelope.configuration_generation
                != pending.configuration_generation
                or pending.trace_identity != self._trace_identity
            ):
                self._queue_rejected(
                    sequence,
                    "observation-configuration-mismatch",
                )
                continue
            delivered = envelope.observation
            outcome_value = envelope.outcome
            if not isinstance(delivered, FrameObservation) or not isinstance(
                outcome_value, Mapping
            ):
                self._queue_rejected(sequence, "observation-outcome-malformed")
                continue
            self.persist_evidence(envelope.evidence)
            outcome = cast(_Outcome, outcome_value)
            evaluation_value = outcome.get("evaluation_payload")
            evaluation = (
                evaluation_value
                if isinstance(evaluation_value, ModelEvaluationPayload)
                else None
            )
            try:
                parsed = self._parse_outcome(outcome)
                rejection = self._outcome_rejection(delivered, parsed)
                if rejection is not None:
                    self._queue_rejected(sequence, rejection, evaluation)
                    continue
                observation_payload = self._observation_payload(delivered, parsed)
            except (KeyError, TypeError, ValueError):
                self._queue_rejected(
                    sequence,
                    "observation-outcome-malformed",
                    evaluation,
                )
                continue

            records: list[_TraceRecord] = [
                (TraceEventKind.MODEL_OBSERVATION, observation_payload)
            ]
            if evaluation is not None:
                records.append((TraceEventKind.MODEL_EVALUATION, evaluation))
            lifecycle_value = outcome.get("lifecycle")
            lifecycle = parse_model_lifecycle_payload(
                cast(Mapping[str, object], lifecycle_value)
                if isinstance(lifecycle_value, Mapping)
                else None
            )
            if lifecycle is not None:
                records.append((TraceEventKind.MODEL_EVENT, lifecycle))
            self._pending[sequence] = replace(pending, records=tuple(records))

        for sequence, pending in tuple(self._pending.items()):
            if pending.records is None or not self._flush_pending_trace(
                sequence,
                pending,
                publication_ms,
            ):
                break

    def record_gap(self, observation: FrameObservation, reason: str) -> None:
        publication_ms = int(observation.frame_end_s * 1_000)
        trace = self._trace
        if trace is not None:
            trace.record(
                TraceEventKind.RECORDER_GAP,
                RecorderGapPayload(
                    lost_record_count=1,
                    gap_start_ms=int(observation.frame_start_s * 1_000),
                    gap_end_ms=publication_ms,
                    reason=reason,
                    frame_start_ms=int(observation.frame_start_s * 1_000),
                    frame_end_ms=publication_ms,
                    result_revision=observation.result_revision,
                    observation_sequence=observation.observation_sequence,
                ),
                publication_ms,
            )
        identity = self._trace_identity
        persistence = self._persistence
        if persistence is None or identity is None:
            return
        gap = ModelEvidenceRecord(
            evidence_id=(
                f"{identity.session_id}:recorder-gap:{observation.role_generation}:"
                f"{observation.observation_sequence}:{publication_ms}"
            ),
            kind=EvidenceKind.RECORDER_GAP,
            session_id=identity.session_id,
            cook_id=identity.cook_id,
            timestamp_ms=publication_ms,
            role_generation=observation.role_generation,
            model_digest=None,
            provenance_digest=None,
            payload=RecorderGapEvidence(lost_record_count=1, reason=reason),
        )
        if not persistence.submit_evidence_batch((gap,)).accepted:
            self._evidence_available = False

    def bind_generation(self, generation: int) -> None:
        self._generation = generation
        runner = self._runner
        identity = self._trace_identity
        if runner is None or identity is None:
            return
        runner.bind_evidence_context(
            generation,
            identity.session_id,
            identity.cook_id,
        )
        for sequence, pending in tuple(self._pending.items()):
            if pending.configuration_generation == generation:
                self._pending[sequence] = replace(
                    pending,
                    trace_identity=identity,
                )

    def retire_generation(self, generation: int) -> tuple[int, ...]:
        """Retire one context and return other retained generations immutably."""
        runner = self._runner
        if runner is not None:
            runner.retire_evidence_context(generation)
        self._pending = {
            sequence: pending
            for sequence, pending in self._pending.items()
            if pending.configuration_generation != generation
        }
        return tuple(
            sorted(
                {
                    pending.configuration_generation
                    for pending in self._pending.values()
                }
            )
        )

    def persist_evidence(
        self,
        evidence: tuple[ModelEvidenceRecord, ...],
    ) -> None:
        """Preserve the established confidence/ordinary split-channel order."""
        confidence = tuple(
            record
            for record in evidence
            if record.kind is EvidenceKind.CONFIDENCE_DECISION
        )
        ordinary = tuple(
            record
            for record in evidence
            if record.kind is not EvidenceKind.CONFIDENCE_DECISION
        )
        runner = self._runner
        if runner is not None:
            for record in confidence:
                receipt = runner.submit_activation_confidence(record)
                if receipt is None or not receipt.accepted:
                    self._evidence_available = False
        persistence = self._persistence
        if (
            ordinary
            and persistence is not None
            and not persistence.submit_evidence_batch(ordinary).accepted
        ):
            self._evidence_available = False
        if persistence is not None and persistence.evidence_blocked:
            self._evidence_available = False

    @property
    def _trace_identity(self) -> TraceSessionIdentity | None:
        trace = self._trace
        return None if trace is None else trace.identity

    def _retire_pending(self, sequence: int, reason: str) -> None:
        pending = self._pending.pop(sequence, None)
        if pending is not None:
            self.record_gap(pending.observation, reason)

    def _bound_pending(self) -> None:
        while len(self._pending) > self._PENDING_CAPACITY:
            self._retire_pending(
                next(iter(self._pending)),
                "pending-observation-overflow",
            )

    def _submit_calibration_frame_evidence(
        self,
        observation: FrameObservation,
    ) -> None:
        persistence = self._persistence
        identity = self._trace_identity
        if persistence is None or identity is None:
            return
        compact = self._calibration_frame_evidence(
            observation,
            identity.session_id,
            identity.cook_id,
        )
        if compact is None:
            return
        if not persistence.submit_evidence_batch((compact,)).accepted:
            self._evidence_available = False

    def _queue_rejected(
        self,
        sequence: int,
        reason: str,
        evaluation: ModelEvaluationPayload | None = None,
    ) -> None:
        pending = self._pending.get(sequence)
        if pending is None:
            self._pending.pop(sequence, None)
            return
        try:
            rejected = self._rejected_model_observation(
                pending.observation,
                reason,
            )
        except ValueError:
            self._retire_pending(sequence, reason)
            return
        records: tuple[_TraceRecord, ...] = (
            (TraceEventKind.MODEL_OBSERVATION, rejected),
        )
        if evaluation is not None:
            records += ((TraceEventKind.MODEL_EVALUATION, evaluation),)
        self._pending[sequence] = replace(pending, records=records)

    def _flush_pending_trace(
        self,
        sequence: int,
        pending: _PendingObservation,
        publication_ms: int,
    ) -> bool:
        remaining = pending.records
        if remaining is None:
            return False
        trace = self._trace
        if trace is None:
            return False
        while remaining:
            event_kind, payload = remaining[0]
            if not trace.record(event_kind, payload, publication_ms):
                self._pending[sequence] = replace(pending, records=remaining)
                return False
            remaining = remaining[1:]
        self._pending.pop(sequence, None)
        return True

    @staticmethod
    def _allocation_evidence(
        allocation: AllocationResult | None,
    ) -> AllocationEvidence | None:
        if allocation is None:
            return None
        return AllocationEvidence(
            normalized_combustion_load=allocation.normalized_combustion_load,
            auger_duty=allocation.auger_duty,
            fan_duty=allocation.fan_duty,
            u_max=allocation.u_max,
            fan_min_pct=allocation.fan_min_pct,
            fan_max_pct=allocation.fan_max_pct,
            fan_enabled=allocation.fan_enabled,
            auger_clamp_reason=allocation.auger_clamp_reason,
            fan_clamp_reason=allocation.fan_clamp_reason,
            allocator_revision=allocation.allocator_revision,
        )

    @staticmethod
    def _trace_allocation_payload(
        allocation: AllocationResult | None,
        result_revision: int,
    ) -> AllocationPayload | None:
        if allocation is None:
            return None
        return AllocationPayload(
            result_revision=result_revision,
            normalized_combustion_load=allocation.normalized_combustion_load,
            requested_auger_duty=allocation.auger_duty,
            requested_fan_duty=allocation.fan_duty,
            u_max=allocation.u_max,
            fan_min_pct=allocation.fan_min_pct,
            fan_max_pct=allocation.fan_max_pct,
            fan_enabled=allocation.fan_enabled,
            mpc_has_fan_authority=allocation.fan_enabled,
            auger_clamp_reason=allocation.auger_clamp_reason,
            fan_clamp_reason=allocation.fan_clamp_reason,
            allocator_revision=allocation.allocator_revision,
        )

    @classmethod
    def _calibration_frame_evidence(
        cls,
        observation: FrameObservation,
        session_id: str | None,
        cook_id: str | None,
    ) -> ModelEvidenceRecord | None:
        if (
            session_id is None
            or observation.calibration_command_revision == 0
            or observation.baseline_allocation is None
            or observation.combined_allocation is None
            or (
                observation.calibration_status == "active"
                and observation.probe_q == 0.0
                and observation.calibration_stage != "coast"
            )
        ):
            return None
        payload = CalibrationSummaryEvidence(
            accepted=observation.calibration_status in {"accepted", "active"},
            probe_count=int(
                observation.calibration_status == "active"
                and observation.probe_q != 0.0
            ),
            reason=observation.calibration_cancellation_reason,
            result_revision=observation.result_revision,
            command_revision=observation.calibration_command_revision,
            command_action=cast(
                _CalibrationCommandAction,
                observation.calibration_command_action,
            ),
            baseline_q=observation.baseline_q,
            probe_q=observation.probe_q,
            combined_q=observation.requested_q,
            baseline_allocation=cls._allocation_evidence(
                observation.baseline_allocation
            ),
            combined_allocation=cls._allocation_evidence(
                observation.combined_allocation
            ),
            scheduled_on_seconds=observation.scheduled_on_s,
            cancellation_command_revision=observation.cancellation_command_revision,
            cancellation_command_action=cast(
                _CancellationCommandAction,
                observation.cancellation_command_action,
            ),
            delivered_on_seconds=observation.delivered_on_s,
            status=cast(_CalibrationStatus, observation.calibration_status),
            requested_fan_duty=observation.requested_fan_duty,
            actual_fan_duty=observation.actual_fan_duty,
            cancellation_reason=observation.calibration_cancellation_reason,
            stage=cast(_CalibrationStage | None, observation.calibration_stage),
            completed_stages=cast(
                tuple[_CalibrationStage, ...],
                observation.completed_calibration_stages,
            ),
            continuous=observation.continuous,
        )
        return ModelEvidenceRecord(
            evidence_id=(
                f"{session_id}:calibration-frame:{observation.result_revision}:"
                f"{int(observation.frame_start_s * 1_000)}"
            ),
            kind=EvidenceKind.CALIBRATION_SUMMARY,
            session_id=session_id,
            cook_id=cook_id,
            timestamp_ms=int(observation.frame_end_s * 1_000),
            role_generation=observation.role_generation,
            model_digest=None,
            provenance_digest=None,
            payload=payload,
        )

    @classmethod
    def _rejected_model_observation(
        cls,
        observation: FrameObservation,
        reason: str,
    ) -> ModelObservationPayload:
        return cls._observation_payload(
            observation,
            _ParsedOutcome(
                eligible=False,
                rejection_reasons=(reason,),
                input_variance=0.0,
                input_levels=0,
                incumbent_innovation_c=None,
                challenger_innovation_c=None,
                effective_updates=0,
                role_generation=observation.role_generation,
                model_digest=None,
            ),
        )

    @staticmethod
    def _outcome_rejection(
        observation: FrameObservation,
        outcome: _ParsedOutcome,
    ) -> str | None:
        if observation.allocation_join_reason is not None:
            return observation.allocation_join_reason
        if not observation.probe_valid:
            return "invalid-probe"
        if outcome.role_generation != observation.role_generation:
            return "observation-role-generation-mismatch"
        if outcome.eligible and (
            observation.output_source != OutputSource.CONTROLLER.value
            or observation.lid_open
            or observation.safety_inhibited
            or observation.manual_override
            or observation.stale
            or observation.skipped
            or observation.reset
            or not observation.continuous
        ):
            return "observation-gate-mismatch"
        return None

    @classmethod
    def _observation_payload(
        cls,
        observation: FrameObservation,
        outcome: _ParsedOutcome,
    ) -> ModelObservationPayload:
        output_source = (
            OutputSource(observation.output_source)
            if observation.output_source != "unknown"
            else None
        )
        return ModelObservationPayload(
            frame_start_ms=int(observation.frame_start_s * 1_000),
            frame_end_ms=int(observation.frame_end_s * 1_000),
            cancellation_command_revision=observation.cancellation_command_revision,
            cancellation_command_action=cast(
                _CancellationCommandAction,
                observation.cancellation_command_action,
            ),
            calibration_command_revision=observation.calibration_command_revision,
            calibration_command_action=cast(
                _CalibrationCommandAction,
                observation.calibration_command_action,
            ),
            calibration_cancellation_reason=observation.calibration_cancellation_reason,
            calibration_status=cast(
                _CalibrationStatus,
                observation.calibration_status,
            ),
            baseline_allocation=cls._trace_allocation_payload(
                observation.baseline_allocation,
                observation.result_revision,
            ),
            combined_allocation=cls._trace_allocation_payload(
                observation.combined_allocation,
                observation.result_revision,
            ),
            temp_c=observation.temp_c,
            setpoint_c=observation.setpoint_c,
            ambient_c=observation.ambient_c,
            observation_sequence=observation.observation_sequence,
            probe_valid=observation.probe_valid,
            probe_source=observation.probe_source,
            ambient_source=observation.ambient_source,
            ambient_uncertainty=observation.ambient_uncertainty,
            baseline_combustion_load=cast(float, observation.baseline_q),
            calibration_probe_load=observation.probe_q,
            requested_combustion_load=observation.requested_q,
            allocated_combustion_load=cast(float, observation.allocated_q),
            realized_combustion_load=observation.realized_q,
            requested_auger_duty=observation.requested_auger_duty,
            scheduled_on_seconds=cast(float, observation.scheduled_on_s),
            delivered_on_seconds=observation.delivered_on_s,
            realized_auger_duty=cast(float, observation.realized_auger_duty),
            allocator_revision=cast(int, observation.allocator_revision),
            allocation_clamp_reasons=observation.allocation_clamp_reasons,
            calibration_stage=observation.calibration_stage,
            calibration_fit=observation.calibration_fit,
            result_revision=observation.result_revision,
            eligible=outcome.eligible,
            rejection_reasons=outcome.rejection_reasons,
            input_variance=outcome.input_variance,
            input_levels=outcome.input_levels,
            incumbent_innovation_c=outcome.incumbent_innovation_c,
            challenger_innovation_c=outcome.challenger_innovation_c,
            effective_updates=outcome.effective_updates,
            role_generation=outcome.role_generation,
            model_digest=outcome.model_digest,
            requested_fan_duty=observation.requested_fan_duty,
            actual_fan_duty=observation.actual_fan_duty,
            output_source=output_source,
            lid_open=observation.lid_open,
            safety_inhibited=observation.safety_inhibited,
            manual_override=observation.manual_override,
            stale=observation.stale,
            skipped=observation.skipped,
            reset=observation.reset,
            continuous=observation.continuous,
        )

    @staticmethod
    def _parse_outcome(outcome: _Outcome) -> _ParsedOutcome:
        return _ParsedOutcome(
            eligible=cast(bool, outcome["eligible"]),
            rejection_reasons=tuple(
                cast(Sequence[str], outcome["rejection_reasons"])
            ),
            input_variance=cast(float, outcome["input_variance"]),
            input_levels=cast(int, outcome["input_levels"]),
            incumbent_innovation_c=cast(
                float | None,
                outcome["incumbent_innovation_c"],
            ),
            challenger_innovation_c=cast(
                float | None,
                outcome["challenger_innovation_c"],
            ),
            effective_updates=cast(int, outcome["effective_updates"]),
            role_generation=cast(int, outcome["role_generation"]),
            model_digest=cast(str | None, outcome["model_digest"]),
        )


