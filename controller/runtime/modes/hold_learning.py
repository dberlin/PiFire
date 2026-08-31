"""Typed Hold observation reconciliation and learning-evidence ownership."""

from __future__ import annotations

import secrets
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Literal, Protocol, cast

from common.control_trace import (
    AllocationPayload,
    CalibrationEventType,
    CalibrationTracePayload,
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
    FallbackEvidence,
    ModelEvidenceRecord,
    RecorderGapEvidence,
    RollbackEvidence,
)
from common.persistence.learning_trajectory import LearningTrajectoryRepository
from common.persistence.model_evidence import (
    ModelActivationState,
    read_model_activation,
    read_model_evidence,
)
from common.persistence.protocols import JsonValue
from controller.applied_output import (
    AppliedOutput,
    FrameFeedbackDisposition,
    OutputSource,
)
from controller.model_learning.calibration import CalibrationDecision
from controller.model_learning.contracts import CandidateOrigin, FrameObservation
from controller.mpc_allocator import AllocationResult
from controller.runtime.control_trace_session import (
    ControlTraceSession,
    TraceModelAuthority,
    TraceModelContext,
    TraceSessionIdentity,
)
from controller.runtime.model_lifecycle import ModelLifecycleRunner
from controller.runtime.model_persistence import EvidenceSubmission
from controller.runtime.runner import (
    ModelRestoreOutcome,
    ObservationOutcomeDrain,
    ObservationSubmission,
)

type _FrameKey = tuple[int, int]
type _OutcomeScalar = None | bool | int | float | str | ModelEvaluationPayload
type _OutcomeValue = (
    _OutcomeScalar | Mapping[str, "_OutcomeValue"] | tuple["_OutcomeValue", ...] | list["_OutcomeValue"]
)
type _Outcome = Mapping[str, _OutcomeValue]
type _TraceRecord = tuple[TraceEventKind, ControlTracePayload]
type _StatusScalar = None | bool | int | float | str
type _StatusValue = _StatusScalar | Mapping[str, "_StatusValue"] | tuple["_StatusValue", ...]
type _ActivationIdentity = tuple[
    str,
    str,
    int,
    str | None,
    str | None,
    str | None,
    str | None,
]

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
    except KeyError, TypeError, ValueError:
        return None


class _LearningTrajectoryObserver(Protocol):
    def observe_hold_frame(
        self,
        observation: FrameObservation,
        *,
        replay_only: bool = False,
    ) -> None: ...

    def estimator_seed_anchor(self) -> tuple[int, float] | None: ...

    def barrier(self, timeout: float = 2.0) -> bool: ...


class _HoldLearningRunner(ModelLifecycleRunner, Protocol):
    """Typed runner surface owned by Hold's complete learning lifecycle."""

    def set_output(self, applied: AppliedOutput) -> None: ...

    def observe_frame(self, observation: FrameObservation) -> ObservationSubmission | None: ...

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

    def restore_model(
        self,
        snapshot: dict[str, object],
        *,
        restore_token: str | None = None,
    ) -> ModelRestoreOutcome: ...

    def drain_restore_outcome(self) -> ModelRestoreOutcome | None: ...

    def runs_async(self) -> bool: ...

    def controller_state(self) -> object: ...

    def _schedule_corpus_fit_after_barrier(
        self,
        origin: CandidateOrigin,
        before_schedule: Callable[[], bool],
    ) -> bool: ...
    def record_corpus_fit_disabled(
        self,
        origin: CandidateOrigin,
        reason: str,
    ) -> bool: ...

    def get_model_snapshot(self) -> object: ...


class _EvidencePersistence(Protocol):
    """Nonblocking evidence-only persistence boundary."""

    @property
    def evidence_blocked(self) -> bool: ...

    def submit_evidence_batch(
        self,
        records: Sequence[ModelEvidenceRecord],
    ) -> EvidenceSubmission: ...

    @property
    def failed(self) -> bool: ...

    def submit_checkpoint(
        self,
        name: str,
        snapshot: dict[str, object],
    ) -> bool: ...

    def barrier(self, timeout: float = 2.0) -> bool: ...


class _ModelStore(Protocol):
    def load(self, name: str) -> dict[str, object] | None: ...


class _LifecycleLogger(Protocol):
    def info(self, message: str, /) -> None: ...

    def warning(self, message: str, /) -> None: ...

    def error(self, message: str, /) -> None: ...


@dataclass(frozen=True, slots=True)
class CalibrationHandoff:
    status: _CalibrationStatus
    reason: str | None
    probe_load: float
    stage: _CalibrationStage | None
    command_revision: int
    command_action: _CalibrationCommandAction
    command_generation: int
    completed_stages: tuple[_CalibrationStage, ...]


@dataclass(frozen=True, slots=True)
class _PendingObservation:
    frame_key: _FrameKey
    observation: FrameObservation
    trace_identity: TraceSessionIdentity | None
    configuration_generation: int
    records: tuple[_TraceRecord, ...] | None = None


class HoldLearningRuntime:
    """Own Hold's observation, model, persistence, and teardown lifecycle."""

    _PENDING_CAPACITY = 60

    _CALIBRATION_OUTCOME_STATUS: Mapping[str, _CalibrationStatus] = {
        "start_rejected": "rejected",
        "safety_aborted": "cancelled",
        "stage_timeout": "cancelled",
        "stopped": "cancelled",
        "completed": "accepted",
    }

    def __init__(
        self,
        *,
        runner: _HoldLearningRunner | None,
        model_store: _ModelStore | None,
        persistence: _EvidencePersistence | None,
        trajectory_repository: LearningTrajectoryRepository | None = None,
        trace: ControlTraceSession | None,
        controller_name: str,
        logger: _LifecycleLogger,
        initial_generation: int,
        learning_trajectory: _LearningTrajectoryObserver | None = None,
    ) -> None:
        self._runner = runner
        self._model_store = model_store
        self._persistence = persistence
        self._trajectory_repository = trajectory_repository
        self._learning_trajectory = learning_trajectory
        self._trace = trace
        self._controller_name = controller_name
        self._logger = logger
        self._generation = initial_generation
        self._pending: dict[int, _PendingObservation] = {}
        self._evidence_available = True
        self._checkpoint_evidence_available = True
        self._seed_warmup_remaining = 0
        self._activation_state_identity: _ActivationIdentity | None = None
        self._retired_generations: set[int] = set()
        self._activation_lifecycle_evidence_id: str | None = None
        self._submitted_restore: tuple[str, dict[str, object]] | None = None
        self._restored_authority_blocked = False
        self._last_checkpoint_succeeded = False
        self._last_checkpoint_snapshot: dict[str, object] | None = None
        self._teardown_checkpoint_retried = False
        self._teardown_started = False
        self._generation_retired = False
        self._trace_flushed = False
        self._persistence_finished = False
        self._persistence_drained = False
        self._trace_finished = False
        self._runner_finished = False
        self._stop_fit_submitted = False
        self._postfit_persistence_finished = False
        self._teardown_finalized = False
        self._calibration_fit_authorizations: set[int] = set()

    @property
    def evidence_available(self) -> bool:
        return self._evidence_available and self._checkpoint_evidence_available and self._seed_warmup_remaining == 0

    @property
    def seed_warmup_remaining(self) -> int:
        return self._seed_warmup_remaining
    @property
    def submitted_restore_authority(self) -> TraceModelAuthority | None:
        submitted = self._submitted_restore
        if submitted is None:
            return None
        return TraceModelAuthority(
            cast(Mapping[str, JsonValue], submitted[1]),
            "restore_submitted",
        )


    def set_seed_warmup_remaining(self, frame_count: int) -> None:
        if isinstance(frame_count, bool) or not isinstance(frame_count, int) or frame_count < 0:
            raise ValueError("seed warm-up count must be a nonnegative integer")
        self._seed_warmup_remaining = frame_count

    def mark_evidence_unavailable(self) -> None:
        self._evidence_available = False

    def _deliver_feedback_without_observation(self, feedback: AppliedOutput | None) -> None:
        runner = self._runner
        if (
            runner is not None
            and feedback is not None
            and feedback.feedback_disposition is not FrameFeedbackDisposition.PROGRESS
        ):
            runner.set_output(feedback)

    def _trajectory_fit_barrier(self) -> bool:
        trajectory = self._learning_trajectory
        if trajectory is None:
            return True
        try:
            durable = trajectory.barrier(timeout=2.0)
        except Exception as error:
            self.mark_evidence_unavailable()
            self._logger.warning(f"Learning trajectory barrier failed: {error}")
            return False
        if not durable:
            self.mark_evidence_unavailable()
        return durable

    def _calibration_fit_barrier(self) -> bool:
        trajectory_durable = self._trajectory_fit_barrier()
        persistence = self._persistence
        if persistence is None:
            self.mark_evidence_unavailable()
            return False
        try:
            persistence_durable = persistence.barrier(timeout=2.0) and not persistence.failed
        except Exception as error:
            self.mark_evidence_unavailable()
            self._logger.warning(f"Calibration fit persistence barrier failed: {error}")
            return False
        durable = trajectory_durable and persistence_durable
        if not durable:
            self.mark_evidence_unavailable()
        return durable

    def _schedule_completed_calibration_fit(
        self,
        decision: CalibrationDecision,
    ) -> None:
        generation = decision.command_generation
        if any(event.kind == "start_accepted" for event in decision.events):
            self._calibration_fit_authorizations.add(generation)
        outcome = decision.outcome
        if outcome is None:
            return
        authorized = generation in self._calibration_fit_authorizations
        self._calibration_fit_authorizations.discard(generation)
        if outcome != "completed" or not authorized:
            return
        runner = self._runner
        if runner is None or not runner._schedule_corpus_fit_after_barrier(
            CandidateOrigin.OPERATOR_CALIBRATION,
            self._calibration_fit_barrier,
        ):
            self.mark_evidence_unavailable()

    def handoff_calibration(
        self,
        decision: CalibrationDecision | None,
        *,
        result_revision: int,
        timestamp_ms: int,
    ) -> CalibrationHandoff:
        if decision is not None:
            self._schedule_completed_calibration_fit(decision)
        trace = self._trace
        if decision is not None and trace is not None:
            command_action = cast(
                _CalibrationCommandAction,
                decision.command_action,
            )
            for event in decision.events:
                try:
                    event_type = CalibrationEventType(event.kind)
                except ValueError:
                    continue
                trace.record(
                    TraceEventKind.CALIBRATION,
                    CalibrationTracePayload(
                        event=event_type,
                        command_revision=decision.command_revision,
                        command_action=command_action,
                        result_revision=result_revision,
                        stage=event.stage,
                        intended_probe_load=event.intended_probe_q,
                        bounded_probe_load=event.bounded_probe_q,
                        cumulative_probe_load=event.realized_probe_sum,
                        eligible_observations=(decision.progress.eligible_observations),
                        positive_observations=(decision.progress.positive_observations),
                        negative_observations=(decision.progress.negative_observations),
                        reasons=event.reasons,
                    ),
                    timestamp_ms,
                )
        if decision is None:
            return CalibrationHandoff(
                "inactive",
                None,
                0.0,
                None,
                0,
                "none",
                0,
                (),
            )
        status = (
            "active"
            if decision.active
            else self._CALIBRATION_OUTCOME_STATUS.get(
                decision.outcome or "",
                ("accepted" if any(event.kind == "start_accepted" for event in decision.events) else "inactive"),
            )
        )
        return CalibrationHandoff(
            status=status,
            reason=(", ".join(decision.outcome_reasons) if decision.outcome_reasons else None),
            probe_load=decision.probe_q,
            stage=cast(
                _CalibrationStage | None,
                decision.stage if decision.active else None,
            ),
            command_revision=decision.command_revision,
            command_action=cast(_CalibrationCommandAction, decision.command_action),
            command_generation=decision.command_generation,
            completed_stages=cast(
                tuple[_CalibrationStage, ...],
                tuple(decision.completed_stages),
            ),
        )

    def _apply_restore_authority(self, outcome: ModelRestoreOutcome) -> None:
        trace = self._trace
        if trace is None:
            return
        authority = outcome.effective_authority
        if authority is None:
            trace.clear_model_authority()
            return
        trace.set_model_authority(
            cast(Mapping[str, JsonValue], authority),
            "configured_fallback" if outcome.staged_for_revalidation else "restored",
        )

    def restore_model(
        self,
        *,
        timestamp_ms: int,
        controller_name: str | None = None,
    ) -> None:
        if controller_name is not None and controller_name != self._controller_name:
            self._controller_name = controller_name
            self._activation_state_identity = None
            self._activation_lifecycle_evidence_id = None
            self._last_checkpoint_snapshot = None
            self._last_checkpoint_succeeded = False
        trace = self._trace
        if trace is not None:
            trace.clear_model_authority()
        store = self._model_store
        runner = self._runner
        if store is None or runner is None:
            return
        snapshot = store.load(self._controller_name)
        if snapshot is None:
            return
        restore_token = secrets.token_hex(16)
        self._submitted_restore = None
        self._restored_authority_blocked = False
        outcome = runner.restore_model(
            snapshot,
            restore_token=(restore_token if runner.runs_async() else None),
        )
        if outcome.pending:
            self._submitted_restore = (restore_token, snapshot)
            self._restored_authority_blocked = True
        elif outcome.accepted:
            self._restored_authority_blocked = outcome.staged_for_revalidation
            self._apply_restore_authority(outcome)
        else:
            self._restored_authority_blocked = True
            self._logger.warning(f"Stored {self._controller_name} model was rejected; starting fresh")
            if trace is not None:
                trace.record_model(
                    TraceModelContext(
                        event=ModelEventType.REJECT,
                        detail="stored model rejected for restore",
                        snapshot=cast(Mapping[str, JsonValue], snapshot),
                        provenance="persisted",
                        timestamp_ms=timestamp_ms,
                    )
                )
            return
        self._logger.info(f"Submitted the stored {self._controller_name} model for restore")
        if trace is not None:
            trace.record_model(
                TraceModelContext(
                    event=ModelEventType.RESTORE,
                    detail="stored model submitted for restore",
                    snapshot=cast(Mapping[str, JsonValue], snapshot),
                    provenance="persisted",
                    timestamp_ms=timestamp_ms,
                )
            )

    @staticmethod
    def _activation_identity(
        state: ModelActivationState | None,
    ) -> _ActivationIdentity | None:
        if state is None or state.transaction_id is None:
            return None
        return (
            state.phase,
            state.transaction_id,
            state.role_generation,
            state.incumbent_pair_json,
            state.candidate_pair_json,
            state.rollback_pair_json,
            state.reason,
        )

    @staticmethod
    def _pair_activation_lifecycle(
        state: ModelActivationState,
        records: Sequence[ModelEvidenceRecord],
    ) -> ModelEvidenceRecord | None:
        candidate = state.candidate_pair
        if candidate is None:
            return None
        matches = [
            record
            for record in records
            if (
                isinstance(record.payload, RollbackEvidence)
                and record.payload.decision_id == state.evidence_decision_id
                and record.model_digest == candidate.model_digest
            )
            or (
                isinstance(record.payload, FallbackEvidence)
                and record.payload.failed_digest == candidate.model_digest
                and record.payload.failed_generation == candidate.role_generation
            )
        ]
        return max(
            matches,
            key=lambda record: (record.timestamp_ms, record.evidence_id),
            default=None,
        )

    def reconcile_activation(self) -> None:
        runner = self._runner
        if runner is None or self._controller_name != "mpc":
            return
        if self._restored_authority_blocked:
            return
        try:
            state = read_model_activation()
            records = tuple(read_model_evidence())
        except Exception as error:
            self.mark_evidence_unavailable()
            self._logger.warning(f"Model activation state unavailable: {error}")
            return
        identity = self._activation_identity(state)
        if state is not None and identity is None:
            self.mark_evidence_unavailable()
            self._logger.warning("Model activation authority uses a retired schema")
            return
        lifecycle = None if state is None else self._pair_activation_lifecycle(state, records)
        if state is not None and identity != self._activation_state_identity:
            runner.restore_activation(state, records)
            self._activation_state_identity = identity
            self._activation_lifecycle_evidence_id = None if lifecycle is None else lifecycle.evidence_id
            return
        if lifecycle is None or lifecycle.evidence_id == self._activation_lifecycle_evidence_id:
            return
        self._activation_lifecycle_evidence_id = lifecycle.evidence_id
        if isinstance(lifecycle.payload, RollbackEvidence):
            runner.rollback_activation(lifecycle.payload.reason)
        elif isinstance(lifecycle.payload, FallbackEvidence):
            runner.activation_runtime_failure(lifecycle.payload.reason)

    def drain_activation_events(self) -> None:
        runner = self._runner
        if runner is None:
            return
        records = tuple(runner.drain_activation_events())
        if not records:
            return
        persistence = self._persistence
        if persistence is None or not persistence.submit_evidence_batch(records).accepted:
            self.mark_evidence_unavailable()
            self._logger.warning("Model activation fallback evidence was not persisted")

    def status_fragment(self) -> dict[str, dict[str, _StatusValue]]:
        runner = self._runner
        if runner is None:
            return {}
        try:
            status = runner.controller_state()
        except Exception:
            return {}
        if not isinstance(status, Mapping):
            return {}
        learning = status.get("learning")
        if not isinstance(learning, Mapping):
            return {}
        return {"learning": deepcopy(dict(learning))}

    def submit_online_checkpoint(self, snapshot: dict[str, object]) -> bool:
        persistence = self._persistence
        accepted = persistence is not None and persistence.submit_checkpoint(
            self._controller_name,
            snapshot,
        )
        self._last_checkpoint_snapshot = deepcopy(snapshot)
        self._last_checkpoint_succeeded = bool(accepted)
        if not accepted:
            self._checkpoint_evidence_available = False
        return bool(accepted)

    @staticmethod
    def _identification_enabled(
        settings: Mapping[str, JsonValue],
        controller_name: str,
    ) -> bool:
        controller = settings["controller"]
        if not isinstance(controller, Mapping):
            raise TypeError("controller settings are not a mapping")
        config = controller.get("config", {})
        if not isinstance(config, Mapping):
            raise TypeError("controller config is not a mapping")
        selected = config.get(controller_name, {})
        if not isinstance(selected, Mapping):
            raise TypeError("selected controller config is not a mapping")
        return selected.get("enable_identification") is True

    def schedule_stop_fit(self, settings: Mapping[str, JsonValue]) -> bool:
        """Schedule an identification-authorized cumulative fit after the Stop barrier."""
        try:
            enabled = self._identification_enabled(
                settings,
                self._controller_name,
            )
        except Exception as error:
            self.mark_evidence_unavailable()
            self._logger.error(f"Model fit scheduling failed at cook end: {error}")
            return False
        runner = self._runner
        if not enabled:
            if self._controller_name != "pid_sp" or not self._evidence_available or runner is None:
                return False
            try:
                recorded = bool(
                    runner.record_corpus_fit_disabled(
                        CandidateOrigin.PASSIVE_ONLINE,
                        "identification-disabled",
                    )
                )
                if recorded:
                    self._stop_fit_submitted = True
                return recorded
            except Exception as error:
                self._logger.error(f"Disabled PID-SP fit outcome recording failed at cook end: {error}")
                return False
        if not self._evidence_available:
            return False
        if runner is None:
            self.mark_evidence_unavailable()
            return False
        try:
            scheduled = runner.schedule_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)
        except Exception as error:
            self.mark_evidence_unavailable()
            self._logger.error(f"Model fit scheduling failed at cook end: {error}")
            return False
        if not scheduled:
            self.mark_evidence_unavailable()
            return False
        self._stop_fit_submitted = True
        return True

    def record_stop_fit_failure(
        self,
        settings: Mapping[str, JsonValue],
        reason: str,
    ) -> bool:
        try:
            enabled = self._identification_enabled(
                settings,
                self._controller_name,
            )
        except Exception as error:
            self._logger.error(f"Model fit failure recording failed at cook end: {error}")
            return False
        runner = self._runner
        if self._controller_name != "pid_sp" or not enabled or runner is None:
            return False
        try:
            recorded = bool(
                runner.record_corpus_fit_failed(
                    CandidateOrigin.PASSIVE_ONLINE,
                    reason,
                )
            )
            if recorded:
                self._stop_fit_submitted = True
            return recorded
        except Exception as error:
            self._logger.error(f"PID-SP fit failure recording failed at cook end: {error}")
            return False

    def barrier_for_teardown(self, *, generation: int) -> bool:
        """Fence causal origins and drain process-owned persistence once."""
        if not self._generation_retired:
            self._generation_retired = True
            try:
                self.retire_generation(generation)
            except Exception as error:
                self._logger.warning(f"Controller evidence retirement failed: {error}")
        if self._persistence_finished:
            return self._persistence_drained
        if (
            self._persistence is not None
            and self._last_checkpoint_snapshot is not None
            and not self._last_checkpoint_succeeded
            and not self._teardown_checkpoint_retried
        ):
            self._teardown_checkpoint_retried = True
            try:
                self.submit_online_checkpoint(self._last_checkpoint_snapshot)
            except Exception as error:
                self.mark_evidence_unavailable()
                self._logger.warning(f"Final online checkpoint retry failed: {error}")
        self._persistence_finished = True
        trajectory_drained = self._trajectory_fit_barrier()
        persistence = self._persistence
        persistence_drained = persistence is None
        if persistence is not None:
            try:
                persistence_drained = persistence.barrier(timeout=2.0) and not persistence.failed
            except Exception as error:
                persistence_drained = False
                self._logger.warning(f"Model persistence barrier failed: {error}")
        drained = trajectory_drained and persistence_drained
        self._persistence_drained = drained
        if not drained:
            self.mark_evidence_unavailable()
        return drained

    def _finish_teardown_after_fit(self) -> None:
        if self._teardown_finalized:
            return
        self._teardown_finalized = True
        persistence = self._persistence
        if self._stop_fit_submitted and not self._postfit_persistence_finished:
            self._postfit_persistence_finished = True
            postfit_drained = persistence is None
            if persistence is not None:
                try:
                    postfit_drained = persistence.barrier(timeout=2.0) and not persistence.failed
                except Exception as error:
                    postfit_drained = False
                    self._logger.warning(
                        f"Post-fit model persistence barrier failed: {error}",
                    )
            if not postfit_drained:
                self.mark_evidence_unavailable()
        trace = self._trace
        if not self._trace_flushed:
            self._trace_flushed = True
            if trace is not None:
                try:
                    trace.flush_pending()
                except Exception as error:
                    self._logger.warning(f"Control trace flush failed: {error}")
        if not self._trace_finished:
            self._trace_finished = True
            if trace is not None:
                try:
                    trace.close()
                except Exception as error:
                    self._logger.warning(f"Control trace close failed: {error}")

    def finish_teardown(self, *, generation: int) -> None:
        if self._teardown_started:
            return
        self._teardown_started = True
        runner = self._runner
        self.barrier_for_teardown(generation=generation)
        if runner is None:
            self._finish_teardown_after_fit()
            return
        self._runner_finished = True
        try:
            runner.finish_teardown(self._finish_teardown_after_fit)
        except Exception as error:
            self._logger.warning(f"Controller teardown close failed: {error}")
            self._finish_teardown_after_fit()

    def submit_completed_observation(
        self,
        frame_key: _FrameKey,
        observation: FrameObservation,
        feedback: AppliedOutput | None = None,
    ) -> None:
        """Submit one completed frame while retaining its exact immutable identity."""
        if self._seed_warmup_remaining > 0:
            trajectory = self._learning_trajectory
            replayed_exactly = False
            if trajectory is not None:
                trajectory.observe_hold_frame(observation, replay_only=True)
                anchor = trajectory.estimator_seed_anchor()
                replayed_exactly = isinstance(anchor, tuple) and anchor[0] == round(observation.frame_end_s * 1_000)
            if replayed_exactly and observation.probe_valid and observation.continuous:
                self._seed_warmup_remaining -= 1
            self._deliver_feedback_without_observation(feedback)
            return
        self._submit_calibration_frame_evidence(observation)
        if not observation.probe_valid:
            self._deliver_feedback_without_observation(feedback)
            # The observation trace is telemetry about the frame, never part of
            # actuating it, so a payload the trace model refuses costs the record
            # and leaves a gap in its place.
            try:
                rejected = self._rejected_model_observation(observation, "invalid-probe")
            except Exception as error:
                self._logger.warning(f"Rejected model observation failed: {error}")
                self.record_gap(observation, "invalid-probe")
                return
            sequence = -1
            while sequence in self._pending:
                sequence -= 1
            self._pending[sequence] = _PendingObservation(
                frame_key=frame_key,
                observation=observation,
                trace_identity=self._trace_identity,
                configuration_generation=self._generation,
                records=((TraceEventKind.MODEL_OBSERVATION, rejected),),
            )
            self._bound_pending()
            return

        runner = self._runner
        persistence = self._persistence
        if not self._evidence_available or (persistence is not None and persistence.evidence_blocked):
            self._evidence_available = False
            self.record_gap(observation, "model-persistence-unavailable")
            self._deliver_feedback_without_observation(feedback)
            return

        if runner is None:
            return
        submission = (
            runner.complete_frame(feedback, observation) if feedback is not None else runner.observe_frame(observation)
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
        if isinstance(evicted_sequence, int) and not isinstance(evicted_sequence, bool):
            self._retire_pending(evicted_sequence, "runner-observation-evicted")
        self._bound_pending()

    def _reconcile_submitted_restore(self, timestamp_ms: int) -> None:
        """Publish only the effective authority after the worker rules."""
        runner = self._runner
        submitted = self._submitted_restore
        if runner is None or submitted is None:
            return
        restore_token, snapshot = submitted
        outcome = runner.drain_restore_outcome()
        if outcome is None or outcome.pending or outcome.restore_token != restore_token:
            return
        self._submitted_restore = None
        if outcome.accepted:
            self._restored_authority_blocked = outcome.staged_for_revalidation
            self._apply_restore_authority(outcome)
            return
        self._restored_authority_blocked = True
        trace = self._trace
        if trace is not None:
            trace.clear_model_authority()
        self._logger.warning(f"Stored {self._controller_name} model was rejected; starting fresh")
        if trace is not None:
            trace.record_model(
                TraceModelContext(
                    event=ModelEventType.REJECT,
                    detail="stored model rejected for restore",
                    snapshot=cast(Mapping[str, JsonValue], snapshot),
                    provenance="persisted",
                    timestamp_ms=timestamp_ms,
                )
            )

    def reconcile_outcomes(self, publication_time_s: float) -> None:
        """Consume each public runner outcome once and flush trace effects FIFO."""
        self._reconcile_submitted_restore(int(publication_time_s * 1_000))
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
                envelope.configuration_generation != pending.configuration_generation
                or pending.trace_identity != self._trace_identity
            ):
                self._queue_rejected(
                    sequence,
                    "observation-configuration-mismatch",
                )
                continue
            delivered = envelope.observation
            outcome_value = envelope.outcome
            if not isinstance(delivered, FrameObservation) or not isinstance(outcome_value, Mapping):
                self._queue_rejected(sequence, "observation-outcome-malformed")
                continue
            self.persist_evidence(envelope.evidence)
            outcome = cast(_Outcome, outcome_value)
            evaluation_value = outcome.get("evaluation_payload")
            evaluation = evaluation_value if isinstance(evaluation_value, ModelEvaluationPayload) else None
            try:
                parsed = self._parse_outcome(outcome)
                if parsed.rejection_reasons == ("discontinuous",):
                    self._retire_pending(sequence, "discontinuous")
                    continue
                rejection = self._outcome_rejection(delivered, parsed)
                if rejection is not None:
                    self._queue_rejected(sequence, rejection, evaluation)
                    continue
                observation_payload = self._observation_payload(delivered, parsed)
            except KeyError, TypeError, ValueError:
                self._queue_rejected(
                    sequence,
                    "observation-outcome-malformed",
                    evaluation,
                )
                continue

            trajectory = self._learning_trajectory
            if trajectory is not None:
                try:
                    trajectory.observe_hold_frame(delivered)
                except Exception as error:
                    self._logger.warning(f"Learning trajectory observation failed: {error}")
            records: list[_TraceRecord] = [(TraceEventKind.MODEL_OBSERVATION, observation_payload)]
            if evaluation is not None:
                records.append((TraceEventKind.MODEL_EVALUATION, evaluation))
            lifecycle_value = outcome.get("lifecycle")
            lifecycle = parse_model_lifecycle_payload(
                cast(Mapping[str, object], lifecycle_value) if isinstance(lifecycle_value, Mapping) else None
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
        # This is where every refused observation lands, so a frame the gap models
        # cannot describe either costs the gap and nothing more.
        publication_ms = int(observation.frame_end_s * 1_000)
        trace = self._trace
        if trace is not None:
            try:
                gap_payload = RecorderGapPayload(
                    lost_record_count=1,
                    gap_start_ms=int(observation.frame_start_s * 1_000),
                    gap_end_ms=publication_ms,
                    reason=reason,
                    frame_start_ms=int(observation.frame_start_s * 1_000),
                    frame_end_ms=publication_ms,
                    result_revision=observation.result_revision,
                    observation_sequence=observation.observation_sequence,
                )
            except Exception as error:
                self._logger.warning(f"Recorder gap trace failed: {error}")
            else:
                trace.record(TraceEventKind.RECORDER_GAP, gap_payload, publication_ms)
        identity = self._trace_identity
        persistence = self._persistence
        if persistence is None or identity is None:
            return
        try:
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
            accepted = persistence.submit_evidence_batch((gap,)).accepted
        except Exception as error:
            self._logger.warning(f"Recorder gap evidence failed: {error}")
            return
        if not accepted:
            self._evidence_available = False

    def bind_generation(self, generation: int) -> None:
        self._generation = generation
        self._retired_generations.discard(generation)
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
        if generation not in self._retired_generations:
            self._retired_generations.add(generation)
            if runner is not None:
                runner.retire_evidence_context(generation)
        self._pending = {
            sequence: pending
            for sequence, pending in self._pending.items()
            if pending.configuration_generation != generation
        }
        return tuple(sorted({pending.configuration_generation for pending in self._pending.values()}))

    def persist_evidence(
        self,
        evidence: tuple[ModelEvidenceRecord, ...],
    ) -> None:
        """Preserve the established confidence/ordinary split-channel order."""
        confidence = tuple(record for record in evidence if record.kind is EvidenceKind.CONFIDENCE_DECISION)
        ordinary = tuple(record for record in evidence if record.kind is not EvidenceKind.CONFIDENCE_DECISION)
        runner = self._runner
        if runner is not None:
            for record in confidence:
                receipt = runner.submit_activation_confidence(record)
                if receipt is None or not receipt.accepted:
                    self._evidence_available = False
        persistence = self._persistence
        if ordinary and persistence is not None and not persistence.submit_evidence_batch(ordinary).accepted:
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
        # Calibration evidence is telemetry about the frame, never part of actuating
        # it, so a payload the evidence model refuses costs the record and nothing
        # more.
        try:
            compact = self._calibration_frame_evidence(
                observation,
                identity.session_id,
                identity.cook_id,
            )
            if compact is None:
                return
            accepted = persistence.submit_evidence_batch((compact,)).accepted
        except Exception as error:
            self._logger.warning(f"Calibration frame evidence failed: {error}")
            return
        if not accepted:
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
        # The observation trace is telemetry about the frame, never part of
        # actuating it, so a payload the trace model refuses costs the record and
        # leaves a gap in its place.
        try:
            rejected = self._rejected_model_observation(
                pending.observation,
                reason,
            )
        except Exception as error:
            self._logger.warning(f"Rejected model observation failed: {error}")
            self._retire_pending(sequence, reason)
            return
        records: tuple[_TraceRecord, ...] = ((TraceEventKind.MODEL_OBSERVATION, rejected),)
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
            probe_count=int(observation.calibration_status == "active" and observation.probe_q != 0.0),
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
            baseline_allocation=cls._allocation_evidence(observation.baseline_allocation),
            combined_allocation=cls._allocation_evidence(observation.combined_allocation),
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
                f"{session_id}:calibration-frame:{observation.result_revision}:{int(observation.frame_start_s * 1_000)}"
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
        output_source = OutputSource(observation.output_source) if observation.output_source != "unknown" else None
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
            rejection_reasons=tuple(cast(Sequence[str], outcome["rejection_reasons"])),
            input_variance=cast(float, outcome["input_variance"]),
            input_levels=cast(int, outcome["input_levels"]),
            effective_updates=cast(int, outcome["effective_updates"]),
            role_generation=cast(int, outcome["role_generation"]),
            model_digest=cast(str | None, outcome["model_digest"]),
        )
