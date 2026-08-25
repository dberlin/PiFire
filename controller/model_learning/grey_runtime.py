"""Grey learning, lifecycle evidence, snapshot, and refit ownership."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from typing import Literal

import numpy as np

from common.control_trace import (
    CompletedOriginPayload,
    ControllerType,
    ControlTraceRecord,
    GreyActivationLifecyclePayload,
    GreyCandidateAssessmentPayload,
    GreyFitLifecyclePayload,
    HorizonScorePayload,
    ModelEvaluationPayload,
    TraceEventKind,
)
from common.controller_model_state import MAX_SNAPSHOT_BYTES, ControllerModelStore
from common.model_evidence import (
    ActivationLifecycleEvidence,
    CandidateAssessmentEvidence,
    ConfidenceDecisionEvidence,
    EvidenceKind,
    FitLifecycleEvidence,
    ForecastOriginEvidence,
    ModelEvidenceRecord,
)
from common.web_contracts.learning import ModelActivationRequest
from controller import mpc_snapshot as _snapshot
from controller.acados import GreyBoxMPCConfig
from controller.grey_box import GreyBoxPredictionAdapter
from controller.model_learning.activation import (
    ActivationManager,
    GreyControlPairDescriptor,
    PreparedActivationRecord,
)
from controller.model_learning.activation_runtime import ActivationRuntime
from controller.model_learning.contracts import (
    ActivationPolicy,
    CandidateOrigin,
    FitRequest,
    FitStatus,
    FitWindowIdentity,
    FrameObservation,
    LearningStatus,
)
from controller.model_learning.evaluation import EvaluationDecision
from controller.model_promotion import Verdict as _Verdict
from controller.mpc_config import DEFAULT_MPC_CONFIG, JsonValue, MpcConfig, warn_about_model
from controller.mpc_factory import MpcPairFactory, OwnedMpcPair
from controller.mpc_model import MODEL_SCHEMA
from controller.runtime.context import EVENT_LOG_NAME
from controller.runtime.model_fitting import (
    CandidateOwnershipTransferredError,
    CandidatePair,
    CandidatePreparation,
    FitSubmission,
    GreyFitError,
    GreyFitJob,
    GreyFitWorker,
    GreyLearningOrchestrator,
    LiveLearningIdentity,
    TeardownGreyHistory,
    TeardownRefitOutcome,
    TeardownRefitResult,
    grey_config_digest,
    prepare_candidate_off_path,
)
from controller.runtime.model_persistence import DurableActivationReceipt

_HISTORY_MAX = 8640
_REFIT_MIN_SAMPLES = 120
_REFIT_INIT = {key: float(DEFAULT_MPC_CONFIG[key]) for key in ("C_c", "h_amb", "K_Q", "theta")}


class GreyLearningRuntime:
    """Sole mutable owner for grey learning, checkpoints, and cook refit."""

    MODEL_SCHEMA = MODEL_SCHEMA
    MODEL_PARAM_KEYS = _snapshot.MODEL_PARAM_KEYS

    def __init__(
        self,
        *,
        pair_factory: MpcPairFactory,
        activation_runtime: ActivationRuntime,
        learning_enabled: bool,
        units: str,
        cycle_data: dict[str, JsonValue],
        active_pair: Callable[[], OwnedMpcPair],
        active_components: Callable[[], CandidatePair],
        configuration: Callable[[], MpcConfig],
        snapshot_parameters: Callable[[], Mapping[str, float | int]],
        cook_history: Callable[[], Sequence[tuple[float, float, float]]],
        sync_configuration: Callable[[], None],
        append_trace: Callable[[Sequence[ControlTraceRecord]], None],
        checkpoint_store: ControllerModelStore | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        clock_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
        fit_worker_factory: Callable[[], GreyFitWorker] = GreyFitWorker,
        logger: logging.Logger | None = None,
    ) -> None:
        #: No ControllerContext reaches the core, so the context's loggers are
        #: injected here instead. The default is the name the context itself
        #: defaults to, which keeps an un-injected runtime out of stdout.
        self._logger = logging.getLogger(EVENT_LOG_NAME) if logger is None else logger
        self._pair_factory = pair_factory
        self._activation_runtime = activation_runtime
        self._learning_enabled = learning_enabled
        self._units = units
        self._cycle_data = copy.deepcopy(cycle_data)
        self._active_pair = active_pair
        self._active_components = active_components
        self._configuration = configuration
        self._snapshot_parameters = snapshot_parameters
        self._cook_history = cook_history
        self._sync_configuration = sync_configuration
        self._append_trace = append_trace
        self._checkpoint_store = ControllerModelStore() if checkpoint_store is None else checkpoint_store
        self._monotonic = monotonic
        self._clock_ms = clock_ms
        self._fit_worker_factory = fit_worker_factory
        self._closed = False
        self._learning_lock = threading.RLock()
        self._learning_evaluation_lock = threading.Lock()
        self._learning_lifecycle_lock = threading.Lock()
        self._learning_preparing = False
        self._learning_pending_origin: CandidateOrigin | None = None
        self._learning_pending_fit_transition: FitRequest | None = None
        self._learning_candidate_pair: CandidatePair | None = None
        self._learning_pending_evaluation: ModelEvaluationPayload | None = None
        self._learning_pending_confidence_accepted: bool | None = None
        self._learning_session_id = "runtime"
        self._learning_cook_id: str | None = None
        self._learning_role_generation = 0
        self._teardown_history = TeardownGreyHistory(role_generation=0, max_observations=_HISTORY_MAX)
        self._cook_refit_outcome: TeardownRefitOutcome | None = None
        self._cook_refit_finalized = False
        self._teardown_candidate = None
        self._teardown_candidate_descriptor: GreyControlPairDescriptor | None = None
        self._teardown_fit_window: FitWindowIdentity | None = None
        self._checkpoint_origin: CandidateOrigin | None = None
        self._checkpoint_policy: ActivationPolicy | None = None
        self._checkpoint_rollback_identity: tuple[str, int] | None = None
        self._teardown_decision_id: str | None = None
        self._checkpoint_challenger: dict[str, JsonValue] | None = None
        self._checkpoint_candidate_identity: tuple[str, int] | None = None
        self._checkpoint_cook_refit: tuple[Literal["idle", "succeeded", "failed"], str | None] = ("idle", None)
        self._checkpoint_activation: tuple[Literal["prepared", "active", "aborted"], bool, bool] = (
            "aborted",
            False,
            False,
        )
        self._checkpoint_failure: tuple[str, str] | None = None
        self._reviewed_checkpoint_decision_ids: set[str] = set()
        self._learning_eligible_updates = 0
        self._learning_rejected_updates = 0
        self._model_revision = 0
        self._model_meta: dict[str, JsonValue] | None = None
        self._learning = self._build_learning() if learning_enabled else None

    @property
    def teardown_observations(self) -> tuple[FrameObservation, ...]:
        return self._teardown_history.observations

    @property
    def teardown_role_generation(self) -> int:
        return self._teardown_history.role_generation

    @property
    def model_metadata(self) -> dict[str, JsonValue] | None:
        return None if self._model_meta is None else copy.deepcopy(self._model_meta)

    def model_authority(self) -> tuple[int, dict[str, JsonValue] | None]:
        return self._model_revision, self.model_metadata

    def sync_activation_generation(self, *, exact: bool = False) -> None:
        generation = self._activation_runtime.role_generation
        self._model_revision = generation if exact else max(self._model_revision, generation)
        self._rotate_teardown_role_generation(self._model_revision)

    def _learning_identity_for(
        self,
        components: CandidatePair,
        role_generation: int,
        *,
        configuration: Mapping[str, JsonValue] | None = None,
    ) -> LiveLearningIdentity:
        config = copy.deepcopy(self._configuration() if configuration is None else configuration)
        config.update(asdict(components.controller.config))
        document = {
            "config": config,
            "cycle_data": self._cycle_data,
            "units": self._units,
        }
        encoded = json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return LiveLearningIdentity(
            session_id=self._learning_session_id,
            cook_id=self._learning_cook_id,
            configuration_digest=hashlib.sha256(encoded).hexdigest(),
            incumbent_digest=grey_config_digest(components.controller.config),
            role_generation=role_generation,
            candidate_generation=role_generation + 1,
        )

    def learning_identity(self) -> LiveLearningIdentity:
        return self._learning_identity_for(
            self._active_components(),
            self._learning_role_generation,
        )

    def _build_learning(
        self,
        *,
        components: CandidatePair | None = None,
        identity: LiveLearningIdentity | None = None,
    ) -> GreyLearningOrchestrator:
        owned_components = self._active_components() if components is None else components
        owned_identity = self.learning_identity() if identity is None else identity
        learning = GreyLearningOrchestrator(
            identity=owned_identity,
            config=owned_components.controller.config,
            incumbent_pair=owned_components,
            estimator_factory=self._pair_factory.build_estimator,
            controller_factory=self._pair_factory.build_solver,
            timing_probe=self._pair_factory.probe_solver,
        )
        try:
            learning.start()
        except BaseException:
            learning.close()
            raise
        return learning

    def learning_status(self) -> dict[str, JsonValue]:
        return self._learning_live_status()

    @staticmethod
    def _completed_forecast_evidence(value):
        forecast = value.forecast
        phase = (
            forecast.phase
            if forecast.phase in {"heating", "coasting"}
            else ("coasting" if value.observed_temperature_c <= forecast.challenger_prediction_c else "heating")
        )
        return ForecastOriginEvidence(
            origin_sequence=value.origin_sequence,
            origin_time_ms=int(forecast.origin_time_s * 1_000),
            completion_time_ms=int(value.completion_time_s * 1_000),
            horizon_steps=value.horizon_steps,
            incumbent_digest=value.incumbent_digest,
            challenger_digest=value.challenger_digest,
            incumbent_prediction_c=forecast.incumbent_prediction_c,
            challenger_prediction_c=forecast.challenger_prediction_c,
            observed_temperature_c=value.observed_temperature_c,
            incumbent_error_c=value.incumbent_error_c,
            challenger_error_c=value.challenger_error_c,
            temperature_band=value.temperature_band,
            phase=phase,
            ambient_source=value.ambient_source,
            calibration_fit=value.calibration_fit,
        )

    def _grey_evaluation_payload(self, decision, *, evaluation_duration_ms):
        completed = tuple(decision.completed_origins)
        raw_origins = tuple(
            CompletedOriginPayload(
                origin_time_ms=int(origin.forecast.origin_time_s * 1_000),
                completion_time_ms=int(origin.completion_time_s * 1_000),
                horizon_steps=origin.horizon_steps,
                generation=origin.role_generation,
                observed_temperature_c=origin.observed_temperature_c,
                incumbent_error_c=origin.incumbent_error_c,
                challenger_error_c=origin.challenger_error_c,
                braking=origin.phase == "coasting",
                observation_sequence=origin.origin_sequence,
                incumbent_digest=origin.incumbent_digest,
                challenger_digest=origin.challenger_digest,
                incumbent_prediction_c=origin.forecast.incumbent_prediction_c,
                challenger_prediction_c=origin.forecast.challenger_prediction_c,
                temperature_band=origin.temperature_band,
                ambient_source=origin.ambient_source,
            )
            for origin in completed
        )
        evaluated_at_s = max(origin.completion_time_s for origin in completed) if completed else self._monotonic()
        incumbent_score = (
            math.sqrt(sum(origin.incumbent_error_c**2 for origin in completed) / len(completed)) if completed else None
        )
        challenger_score = (
            math.sqrt(sum(origin.challenger_error_c**2 for origin in completed) / len(completed)) if completed else None
        )
        return ModelEvaluationPayload(
            decision_id=decision.decision_id,
            evaluated_at_ms=int(evaluated_at_s * 1_000),
            role_generation=decision.role_generation,
            promoted=False,
            committed=False,
            consecutive_wins=decision.consecutive_wins,
            rejection_reasons=tuple(decision.blockers),
            incumbent_prediction_score=incumbent_score,
            challenger_prediction_score=challenger_score,
            incumbent_braking_score=None,
            challenger_braking_score=None,
            sample_count=len(raw_origins),
            prospective_digest=None,
            window_start_ms=(
                min(origin.origin_time_ms for origin in raw_origins) if raw_origins else int(evaluated_at_s * 1_000)
            ),
            window_end_ms=(
                max(origin.completion_time_ms for origin in raw_origins) if raw_origins else int(evaluated_at_s * 1_000)
            ),
            incumbent_digest=decision.incumbent_digest,
            challenger_digest=decision.challenger_digest,
            completed_origins=raw_origins,
            horizon_scores=tuple(
                HorizonScorePayload(
                    horizon_steps=score.horizon_steps,
                    incumbent_rmse_c=score.incumbent_rmse_c if score.sample_count else None,
                    challenger_rmse_c=score.challenger_rmse_c if score.sample_count else None,
                    sample_count=score.sample_count,
                )
                for score in decision.scores
            ),
            evaluation_duration_ms=evaluation_duration_ms,
            challenger_model_kind="grey-box",
        )

    @staticmethod
    def _forecast_from_adapter(adapter, origin):
        horizon = origin.horizon_steps
        frame = origin.frame
        predicted = adapter.forecast(
            np.full(horizon, frame.realized_q, dtype=np.float64),
            np.full(horizon, frame.ambient_c, dtype=np.float64),
        )
        return float(predicted[-1])

    def _register_learning_forecasts(self, observation):
        pair = self._learning_candidate_pair
        if pair is None or self._learning is None:
            return ()
        pair.estimator.update(observation.realized_q, observation.temp_c)
        incumbent = GreyBoxPredictionAdapter.from_estimator(
            self._active_components().estimator, config=self._configuration()
        )
        candidate_config = dict(self._configuration())
        for name in ("C_c", "h_amb", "T_amb", "theta", "K_Q", "sigma"):
            candidate_config[name] = getattr(pair.controller.config, name)
        challenger = GreyBoxPredictionAdapter.from_estimator(
            pair.estimator,
            config=candidate_config,
        )
        return self._learning.register_causal_forecasts(
            observation,
            incumbent_predict=lambda origin: self._forecast_from_adapter(incumbent, origin),
            challenger_predict=lambda origin: self._forecast_from_adapter(challenger, origin),
        )

    def observe_frame(self, observation):
        """Dispatch completed frames without running fit preparation on this worker."""
        if not isinstance(observation, FrameObservation):
            raise TypeError("observation must be a FrameObservation")
        self._teardown_history.observe(observation)
        with self._learning_lock:
            learning = self._learning
            preparing = self._learning_preparing
        if learning is None:
            return None
        with self._learning_evaluation_lock:
            result = learning.observe_completed_frame(
                observation,
                identifiability=0.0 if preparing else 1.0,
            )
            self._register_learning_forecasts(observation)
        request = getattr(result, "request", None)
        with self._learning_lock:
            if learning is self._learning and isinstance(request, FitRequest):
                self._learning_pending_origin = request.origin
                self._learning_pending_fit_transition = request
            evaluation = self._learning_pending_evaluation
            self._learning_pending_evaluation = None
            confidence_accepted = self._learning_pending_confidence_accepted
            self._learning_pending_confidence_accepted = None
            evaluation_decision_id = getattr(evaluation, "decision_id", None)
            confidence_already_persisted = isinstance(
                evaluation_decision_id, str
            ) and self._activation_runtime.consume_confidence_persisted(evaluation_decision_id)
        forecasts = tuple(self._completed_forecast_evidence(value) for value in result.completed_forecasts)
        reasons = tuple(result.history.reasons)
        return {
            "role_generation": observation.role_generation,
            "eligible": bool(result.history.accepted),
            "rejection_reasons": reasons,
            "input_variance": 0.0,
            "input_levels": 0,
            "incumbent_innovation_c": None,
            "challenger_innovation_c": None,
            "effective_updates": len(learning.passive_history.observations)
            if hasattr(learning, "passive_history")
            else 0,
            "model_digest": grey_config_digest(self._active_components().controller.config),
            "forecast_origin_evidence": forecasts,
            "learning_evaluation": evaluation,
            "evaluation_payload": evaluation,
            "confidence_accepted": confidence_accepted,
            "confidence_already_persisted": confidence_already_persisted,
        }

    def observation_failure(self, observation, error):
        """Turn an isolated learning-hook failure into explicit frame evidence."""
        if self._learning is None or not isinstance(observation, FrameObservation):
            return None
        return {
            "role_generation": observation.role_generation,
            "eligible": False,
            "rejection_reasons": ("learner-exception",),
            "input_variance": 0.0,
            "input_levels": 0,
            "incumbent_innovation_c": None,
            "challenger_innovation_c": None,
            "effective_updates": 0,
            "model_digest": grey_config_digest(self._active_components().controller.config),
            "learner_error": f"{type(error).__name__}: {error}",
            "forecast_origin_evidence": (),
        }

    def _rotate_teardown_role_generation(self, role_generation: int) -> None:
        normalized = int(role_generation)
        if normalized == self._learning_role_generation:
            return
        self._learning_role_generation = normalized
        self._teardown_history = TeardownGreyHistory(
            role_generation=normalized,
            max_observations=self._teardown_history.max_observations,
        )

    def bind_learning_identity(self, session_id, cook_id, role_generation):
        """Fence learning work to the runner's current cook/configuration identity."""
        with self._learning_lifecycle_lock:
            self._learning_session_id = session_id
            self._learning_cook_id = cook_id
            self._rotate_teardown_role_generation(role_generation)
            with self._learning_lock:
                learning = self._learning
            if learning is not None:
                with self._learning_evaluation_lock:
                    learning.update_identity(
                        self.learning_identity(),
                        config=self._active_components().controller.config,
                        incumbent_pair=CandidatePair(
                            self._active_components().estimator, self._active_components().controller
                        ),
                    )
                with self._learning_lock:
                    if learning is self._learning:
                        self._learning_pending_origin = None
                        self._learning_pending_fit_transition = None
                        self._learning_candidate_pair = None

    def poll_learning_off_path(self, *, live_origin=None):
        """Drain and prepare fits only from the runner's lifecycle dispatcher."""
        with self._learning_lifecycle_lock:
            return self._poll_learning_off_path_locked(live_origin=live_origin)

    def _grey_lifecycle_record(
        self,
        evidence_payload,
        *,
        timestamp_ms,
        role_generation,
        model_digest,
        provenance_digest,
    ):
        session_id = getattr(self, "_learning_session_id", None) or "mpc-learning"
        return ModelEvidenceRecord(
            evidence_id=(f"{session_id}:{evidence_payload.payload_type}:{timestamp_ms}:{role_generation}"),
            kind=EvidenceKind(evidence_payload.payload_type),
            session_id=session_id,
            cook_id=getattr(self, "_learning_cook_id", None),
            timestamp_ms=timestamp_ms,
            role_generation=role_generation,
            model_digest=model_digest,
            provenance_digest=provenance_digest,
            payload=evidence_payload,
        )

    def _trace_grey_lifecycle(self, evidence, trace_payload):
        try:
            event_kind = {
                "fit_lifecycle": TraceEventKind.FIT_LIFECYCLE,
                "candidate_assessment": TraceEventKind.CANDIDATE_ASSESSMENT,
                "activation_lifecycle": TraceEventKind.ACTIVATION_LIFECYCLE,
                "learning_failure": TraceEventKind.LEARNING_FAILURE,
            }[trace_payload.payload_type]
            self._append_trace(
                (
                    ControlTraceRecord(
                        ts_ms=evidence.timestamp_ms,
                        session_id=evidence.session_id,
                        cook_id=evidence.cook_id,
                        controller=ControllerType.MPC,
                        event_kind=event_kind,
                        payload=trace_payload,
                    ),
                )
            )
        except Exception as error:
            self._activation_runtime.terminate(f"learning lifecycle trace failed: {error}")

    def _persist_grey_lifecycle(
        self,
        evidence_payload,
        trace_payload,
        *,
        timestamp_ms,
        role_generation,
        model_digest,
        provenance_digest,
    ):
        evidence = self._grey_lifecycle_record(
            evidence_payload,
            timestamp_ms=timestamp_ms,
            role_generation=role_generation,
            model_digest=model_digest,
            provenance_digest=provenance_digest,
        )
        if not self._activation_runtime.submit_evidence(evidence):
            raise RuntimeError("learning-lifecycle-evidence-not-accepted")
        self._trace_grey_lifecycle(evidence, trace_payload)
        return evidence

    @staticmethod
    def _policy_for_learning_origin(origin):
        return {
            CandidateOrigin.PASSIVE_ONLINE: ActivationPolicy.PASSIVE_AUTO,
            CandidateOrigin.OPERATOR_CALIBRATION: ActivationPolicy.OPERATOR_REVIEWED,
            CandidateOrigin.COOK_REFIT: ActivationPolicy.COOK_REFIT,
        }[origin]

    def _prepared_candidate_descriptor(
        self,
        preparation: CandidatePreparation,
    ) -> GreyControlPairDescriptor:
        request = preparation.candidate.request
        configured = self._pair_factory.configured(
            self._configuration(),
            candidate_generation=request.candidate_generation,
            role_generation=request.window.role_generation + 1,
        )
        return self._pair_factory.descriptor(
            self._pair_factory.native(
                preparation.candidate.config,
                estimator_kind=configured.estimator_kind,
                candidate_generation=configured.candidate_generation,
                role_generation=configured.role_generation,
            )
        )

    def _persist_reviewed_candidate_checkpoint(self, evaluation, preparation):
        request = getattr(getattr(preparation, "candidate", None), "request", None)
        if (
            getattr(request, "origin", None) is not CandidateOrigin.OPERATOR_CALIBRATION
            or not bool(getattr(evaluation, "accepted", False))
            or tuple(getattr(evaluation, "blockers", ()))
        ):
            return
        if not isinstance(preparation, CandidatePreparation):
            raise RuntimeError("reviewed-candidate-preparation-invalid")  # noqa: TRY004  invariant on already-normalized input, not caller type validation
        candidate_descriptor = self._prepared_candidate_descriptor(preparation)
        active_descriptor = self._active_pair().descriptor
        if (
            evaluation.incumbent_digest != active_descriptor.model_digest
            or evaluation.challenger_digest != candidate_descriptor.model_digest
            or evaluation.candidate_generation != candidate_descriptor.candidate_generation
        ):
            raise RuntimeError("reviewed-candidate-identity-changed")
        persisted = getattr(self, "_reviewed_checkpoint_decision_ids", None)
        if persisted is None:
            persisted = set()
            self._reviewed_checkpoint_decision_ids = persisted
        if evaluation.decision_id in persisted:
            return

        from common.controller_model_state import (
            CheckpointSaveOutcome,
            ControllerModelStore,
        )

        previous_revision = self._model_revision
        self._model_revision = max(
            previous_revision + 1,
            candidate_descriptor.role_generation,
        )
        checkpoint = self.get_model_snapshot()
        if checkpoint is None:
            self._model_revision = previous_revision
            raise RuntimeError("reviewed-candidate-checkpoint-invalid")
        checkpoint["evidence"]["confidence_decision_id"] = evaluation.decision_id
        checkpoint["origin"] = CandidateOrigin.OPERATOR_CALIBRATION.value
        checkpoint["policy"] = ActivationPolicy.OPERATOR_REVIEWED.value
        outcome = self._checkpoint_store.save_outcome("mpc", checkpoint)
        if outcome is not CheckpointSaveOutcome.SAVED:
            self._model_revision = previous_revision
            raise RuntimeError("reviewed-candidate-checkpoint-not-durable")
        persisted.add(evaluation.decision_id)

    def _persist_candidate_evaluation(self, evaluation, preparation):
        if self._activation_runtime.confidence_persisted(evaluation.decision_id):
            return None
        request = getattr(getattr(preparation, "candidate", None), "request", None)
        origin = getattr(request, "origin", None)
        if not isinstance(origin, CandidateOrigin):
            return None
        policy = self._policy_for_learning_origin(origin)
        blockers = tuple(getattr(evaluation, "blockers", ()))
        candidate_pair = getattr(preparation, "candidate_pair", None)
        timing = getattr(preparation, "timing", None)
        fit_accepted = preparation is not None
        identifiability_accepted = "identifiability" not in blockers
        native_build = "passed" if candidate_pair is not None else "failed"
        native_dry_solve = "passed" if bool(getattr(preparation, "dry_solve_finite", False)) else "failed"
        target_timing = "passed" if bool(getattr(timing, "accepted", False)) else "failed"
        confidence_accepted = bool(getattr(evaluation, "accepted", False)) and not blockers
        reasons = list(blockers)
        if native_build == "failed":
            reasons.append("native-build-failed")
        if native_dry_solve == "failed":
            reasons.append("native-dry-solve-failed")
        if target_timing == "failed":
            reasons.append("target-timing-failed")
        if not confidence_accepted and not reasons:
            reasons.append("confidence-rejected")
        assessment = CandidateAssessmentEvidence(
            decision_id=evaluation.decision_id,
            origin=origin.value,
            policy=policy.value,
            fit_accepted=fit_accepted,
            identifiability_accepted=identifiability_accepted,
            native_build=native_build,
            native_dry_solve=native_dry_solve,
            target_timing=target_timing,
            confidence_accepted=confidence_accepted,
            rejection_reasons=tuple(dict.fromkeys(reasons)),
        )
        timestamp_ms = max(
            (
                int(origin_record.completion_time_s * 1_000)
                for origin_record in tuple(getattr(evaluation, "completed_origins", ()))
            ),
            default=self._clock_ms(),
        )
        assessment_trace = GreyCandidateAssessmentPayload(
            decision_id=assessment.decision_id,
            origin=assessment.origin,
            policy=assessment.policy,
            fit_accepted=assessment.fit_accepted,
            identifiability_accepted=assessment.identifiability_accepted,
            native_build=assessment.native_build,
            native_dry_solve=assessment.native_dry_solve,
            target_timing=assessment.target_timing,
            confidence_accepted=assessment.confidence_accepted,
            rejection_reasons=assessment.rejection_reasons,
        )
        persisted = self._grey_lifecycle_record(
            assessment,
            timestamp_ms=timestamp_ms,
            role_generation=evaluation.role_generation,
            model_digest=evaluation.challenger_digest,
            provenance_digest=evaluation.incumbent_digest,
        )
        confidence = ModelEvidenceRecord(
            evidence_id=(f"activation-confidence:{evaluation.decision_id}:{evaluation.role_generation}"),
            kind=EvidenceKind.CONFIDENCE_DECISION,
            session_id=getattr(self, "_learning_session_id", None) or "mpc-learning",
            cook_id=getattr(self, "_learning_cook_id", None),
            timestamp_ms=timestamp_ms,
            role_generation=evaluation.role_generation,
            model_digest=evaluation.challenger_digest,
            provenance_digest=evaluation.incumbent_digest,
            payload=ConfidenceDecisionEvidence(
                decision_id=evaluation.decision_id,
                blocked=not confidence_accepted,
                reason=None if confidence_accepted else reasons[0],
            ),
        )
        receipt = self._activation_runtime.submit_activation_confidence(
            confidence,
            preceding_evidence=(persisted,),
        )
        if not receipt.accepted:
            raise RuntimeError("activation-confidence-not-durable")
        self._trace_grey_lifecycle(persisted, assessment_trace)
        if receipt.wait(2.0) is not True or receipt.durable is not True:
            raise RuntimeError("activation-confidence-not-durable")
        self._activation_runtime.mark_confidence_persisted(evaluation.decision_id)
        return persisted

    def _persist_fit_transition(
        self,
        request,
        *,
        status,
        model_digest,
        error=None,
    ):
        policy = self._policy_for_learning_origin(request.origin)
        window_id = (
            f"{request.window.session_id}:"
            f"{request.window.first_observation_sequence}:"
            f"{request.window.last_observation_sequence}"
        )
        payload = FitLifecycleEvidence(
            request_id=request.request_id,
            status=status,
            origin=request.origin.value,
            policy=policy.value,
            window_id=window_id,
            error=error,
        )
        return self._persist_grey_lifecycle(
            payload,
            GreyFitLifecyclePayload(
                request_id=payload.request_id,
                status=payload.status,
                origin=payload.origin,
                policy=payload.policy,
                window_id=payload.window_id,
                error=payload.error,
            ),
            timestamp_ms=self._clock_ms(),
            role_generation=request.window.role_generation,
            model_digest=model_digest,
            provenance_digest=request.window.incumbent_digest,
        )

    def _persist_rejected_candidate(
        self,
        request,
        *,
        model_digest,
        reasons,
        fit_accepted,
        identifiability_accepted,
        preparation=None,
    ):
        policy = self._policy_for_learning_origin(request.origin)
        candidate_pair = getattr(preparation, "candidate_pair", None)
        timing = getattr(preparation, "timing", None)
        native_build = "passed" if candidate_pair is not None else "failed" if preparation is not None else "not-run"
        native_dry_solve = (
            "passed"
            if preparation is not None and bool(getattr(preparation, "dry_solve_finite", False))
            else "failed"
            if preparation is not None
            else "not-run"
        )
        target_timing = (
            "passed"
            if preparation is not None and bool(getattr(timing, "accepted", False))
            else "failed"
            if preparation is not None
            else "not-run"
        )
        assessment = CandidateAssessmentEvidence(
            decision_id=f"fit:{request.request_id}",
            origin=request.origin.value,
            policy=policy.value,
            fit_accepted=fit_accepted,
            identifiability_accepted=identifiability_accepted,
            native_build=native_build,
            native_dry_solve=native_dry_solve,
            target_timing=target_timing,
            confidence_accepted=False,
            rejection_reasons=tuple(dict.fromkeys(reasons)),
        )
        return self._persist_grey_lifecycle(
            assessment,
            GreyCandidateAssessmentPayload(
                decision_id=assessment.decision_id,
                origin=assessment.origin,
                policy=assessment.policy,
                fit_accepted=assessment.fit_accepted,
                identifiability_accepted=assessment.identifiability_accepted,
                native_build=assessment.native_build,
                native_dry_solve=assessment.native_dry_solve,
                target_timing=assessment.target_timing,
                confidence_accepted=assessment.confidence_accepted,
                rejection_reasons=assessment.rejection_reasons,
            ),
            timestamp_ms=self._clock_ms(),
            role_generation=request.window.role_generation,
            model_digest=model_digest,
            provenance_digest=request.window.incumbent_digest,
        )

    def prepare_automatic_activation(
        self,
        preparation: CandidatePreparation,
        policy: ActivationPolicy,
        evaluation: EvaluationDecision | None = None,
    ) -> str:
        """Persist one passive candidate and expose it to the runner only after receipt."""

        if policy is not ActivationPolicy.PASSIVE_AUTO:
            raise ValueError("manual candidate requires the reviewed activation endpoint")
        candidate_pair = getattr(preparation, "candidate_pair", None)
        candidate_result = getattr(preparation, "candidate", None)
        request = getattr(candidate_result, "request", None)
        native_config = getattr(candidate_result, "config", None)
        raw_estimator_kind = self._configuration().get("estimator")
        if raw_estimator_kind == "ekf":
            estimator_kind: Literal["ekf", "kf"] = "ekf"
        elif raw_estimator_kind == "kf":
            estimator_kind = "kf"
        else:
            raise ValueError("candidate preparation is incomplete")
        if candidate_pair is None or request is None or not isinstance(native_config, GreyBoxMPCConfig):
            raise ValueError("candidate preparation is incomplete")
        owned_candidate = self._pair_factory.adopt(
            self._pair_factory.native(
                native_config,
                estimator_kind=estimator_kind,
                candidate_generation=request.candidate_generation,
                role_generation=request.window.role_generation + 1,
            ),
            candidate_pair.estimator,
            candidate_pair.controller,
            authorized=False,
        )
        candidate_descriptor = owned_candidate.descriptor
        if candidate_descriptor.model_digest != preparation.candidate_digest:
            owned_candidate.close()
            raise ValueError("candidate-digest-changed")
        receipts: list[DurableActivationReceipt] = []

        def persist_prepared(
            record: PreparedActivationRecord,
        ) -> DurableActivationReceipt:
            receipt = self._activation_runtime.submit_prepared_phase(record)
            receipts.append(receipt)
            return receipt

        def build_candidate(descriptor: GreyControlPairDescriptor) -> OwnedMpcPair:
            if descriptor != candidate_descriptor:
                raise ValueError("candidate-digest-changed")
            return owned_candidate

        manager = ActivationManager(
            incumbent_pair=self._active_pair(),
            build_candidate=build_candidate,
            validate_candidate=lambda pair: pair is owned_candidate and self._pair_factory.validate(pair),
            native_dry_solve=lambda _pair: bool(preparation.dry_solve_finite),
            persist_prepared=persist_prepared,
            receipt_timeout=2.0,
        )
        if evaluation is None:
            evaluation = None if self._learning is None else self._learning.last_evaluation
        decision_id = getattr(evaluation, "decision_id", None)
        evaluation_role_generation = getattr(evaluation, "role_generation", None)
        evaluation_candidate_generation = getattr(evaluation, "candidate_generation", None)
        challenger_digest = getattr(evaluation, "challenger_digest", None)
        incumbent_digest = getattr(evaluation, "incumbent_digest", None)
        if (
            not isinstance(decision_id, str)
            or not isinstance(evaluation_role_generation, int)
            or not isinstance(evaluation_candidate_generation, int)
            or not isinstance(challenger_digest, str)
            or not isinstance(incumbent_digest, str)
            or not bool(getattr(evaluation, "accepted", False))
            or tuple(getattr(evaluation, "blockers", ()))
            or challenger_digest != candidate_descriptor.model_digest
            or evaluation_candidate_generation != candidate_descriptor.candidate_generation
            or evaluation_role_generation != self._active_pair().descriptor.role_generation
        ):
            owned_candidate.close()
            raise RuntimeError("activation-confidence-changed")
        evaluated_at_ms = max(
            (int(origin.completion_time_s * 1_000) for origin in tuple(getattr(evaluation, "completed_origins", ()))),
            default=self._clock_ms(),
        )
        # Evaluation completion persists confidence for normal handoff. Direct
        # preparation tests and recovery callers still close the same durability gap.
        if not self._activation_runtime.confidence_persisted(decision_id):
            confidence = ModelEvidenceRecord(
                evidence_id=(f"activation-confidence:{decision_id}:{evaluation_role_generation}"),
                kind=EvidenceKind.CONFIDENCE_DECISION,
                session_id=(getattr(self, "_learning_session_id", None) or "mpc-learning"),
                cook_id=getattr(self, "_learning_cook_id", None),
                timestamp_ms=evaluated_at_ms,
                role_generation=evaluation_role_generation,
                model_digest=challenger_digest,
                provenance_digest=incumbent_digest,
                payload=ConfidenceDecisionEvidence(
                    decision_id=decision_id,
                    blocked=False,
                    reason=None,
                ),
            )
            confidence_receipt = self._activation_runtime.submit_activation_confidence(confidence)
            if (
                not confidence_receipt.accepted
                or confidence_receipt.wait(2.0) is not True
                or confidence_receipt.durable is not True
            ):
                owned_candidate.close()
                raise RuntimeError("activation-confidence-not-durable")
            self._activation_runtime.mark_confidence_persisted(decision_id)
        decision = manager.prepare(
            ModelActivationRequest(
                candidate_digest=candidate_descriptor.model_digest,
                decision_id=decision_id,
            ),
            candidate_descriptor,
            origin=request.origin,
            policy=policy,
        )
        if not decision.accepted or decision.record is None or decision.candidate_pair is None:
            raise RuntimeError(decision.reason)
        if not self._activation_runtime.queue_prepared_activation(
            decision.record,
            decision.candidate_pair,
            receipts[0],
        ):
            decision.candidate_pair.close()
            raise RuntimeError("activation-transition-rejected")
        activation_lifecycle = ActivationLifecycleEvidence(
            decision_id=decision.record.decision_id,
            phase="prepared",
            origin=decision.record.origin.value,
            policy=decision.record.policy.value,
        )
        try:
            self._persist_grey_lifecycle(
                activation_lifecycle,
                GreyActivationLifecyclePayload(
                    decision_id=activation_lifecycle.decision_id,
                    phase=activation_lifecycle.phase,
                    origin=activation_lifecycle.origin,
                    policy=activation_lifecycle.policy,
                ),
                timestamp_ms=decision.record.timestamp_ms,
                role_generation=decision.record.candidate.role_generation,
                model_digest=decision.record.candidate.model_digest,
                provenance_digest=decision.record.incumbent.model_digest,
            )
        except BaseException as error:
            self._activation_runtime.abort_prepared_activation(
                decision.record,
                "learning-lifecycle-persistence-failed",
            )
            with self._learning_lock:
                if self._learning_candidate_pair is candidate_pair:
                    self._learning_candidate_pair = None
            raise CandidateOwnershipTransferredError(str(error)) from error
        return decision.record.transaction_id

    def _poll_learning_off_path_locked(self, *, live_origin=None):
        """Run one lifecycle poll while identity mutation is fenced."""
        with self._learning_lock:
            learning = self._learning
            if learning is None:
                return None, None
            queued = self._learning_pending_fit_transition
            self._learning_pending_fit_transition = None
            origin = self._learning_pending_origin if live_origin is None else live_origin
            if origin is not None:
                origin = origin if isinstance(origin, CandidateOrigin) else CandidateOrigin(origin)
                self._learning_preparing = True

        if queued is not None:
            self._persist_fit_transition(
                queued,
                status="queued",
                model_digest=queued.window.incumbent_digest,
            )

        delivery = None
        try:
            if origin is not None:
                delivery = learning.poll_fit_off_path(
                    live_identity=self.learning_identity(),
                    live_origin=origin,
                )
        finally:
            with self._learning_lock:
                self._learning_preparing = False

        with self._learning_lock:
            if learning is not self._learning:
                return delivery, None
            if delivery is not None:
                self._learning_pending_origin = None
                prepared = getattr(delivery, "preparation", getattr(delivery, "prepared", None))
                if prepared is not None and prepared.accepted:
                    self._learning_candidate_pair = prepared.candidate_pair

        if delivery is not None and getattr(delivery, "message", None) is not None:
            request = delivery.message.request
            outcome = delivery.message.outcome
            stale_reasons = tuple(getattr(delivery, "stale_reasons", ()))
            delivery_blockers = tuple(getattr(delivery, "blockers", ()))
            completed_config = getattr(outcome, "config", None)
            candidate_digest = (
                grey_config_digest(completed_config)
                if isinstance(completed_config, GreyBoxMPCConfig)
                else request.window.incumbent_digest
            )
            if "fit-error" in delivery_blockers:
                detail = getattr(outcome, "detail", "fit-error")
                self._persist_fit_transition(
                    request,
                    status="failed",
                    model_digest=candidate_digest,
                    error=detail,
                )
                self._persist_rejected_candidate(
                    request,
                    model_digest=candidate_digest,
                    reasons=("fit-error",),
                    fit_accepted=False,
                    identifiability_accepted=False,
                )
            elif stale_reasons:
                self._persist_fit_transition(
                    request,
                    status="stale",
                    model_digest=candidate_digest,
                )
            else:
                self._persist_fit_transition(
                    request,
                    status="succeeded",
                    model_digest=candidate_digest,
                )
                preparation = getattr(delivery, "preparation", None)
                if delivery_blockers or (preparation is not None and not bool(getattr(preparation, "accepted", False))):
                    reasons = (
                        delivery_blockers
                        or tuple(getattr(preparation, "blockers", ()))
                        or ("candidate-preparation-rejected",)
                    )
                    self._persist_rejected_candidate(
                        request,
                        model_digest=candidate_digest,
                        reasons=reasons,
                        fit_accepted=True,
                        identifiability_accepted="identifiability" not in reasons,
                        preparation=preparation,
                    )
        evaluation_started = self._monotonic()
        with self._learning_evaluation_lock:
            evaluation = learning.evaluate_ready_off_path()
            blockers = () if evaluation is None else tuple(evaluation.blockers)
            preparation = getattr(learning, "prepared", None)
            if evaluation is not None:
                self._persist_reviewed_candidate_checkpoint(evaluation, preparation)
            if evaluation is not None:
                self._persist_candidate_evaluation(evaluation, preparation)
            if blockers:
                retire = getattr(learning, "retire_evaluated_candidate", None)
                if callable(retire):
                    retire(evaluation)
        evaluation_duration_ms = (self._monotonic() - evaluation_started) * 1_000
        payload = (
            None
            if evaluation is None
            else self._grey_evaluation_payload(
                evaluation,
                evaluation_duration_ms=evaluation_duration_ms,
            )
        )
        preparation_origin = getattr(
            getattr(getattr(preparation, "candidate", None), "request", None),
            "origin",
            None,
        )
        if (
            evaluation is not None
            and not blockers
            and bool(getattr(evaluation, "accepted", False))
            and preparation_origin is CandidateOrigin.PASSIVE_ONLINE
        ):
            learning.handoff_if_ready(
                confidence_accepted=True,
                online_enabled=self._learning_enabled,
                prepare=self.prepare_automatic_activation,
            )
        with self._learning_lock:
            if learning is self._learning and payload is not None:
                self._learning_pending_evaluation = payload
                self._learning_pending_confidence_accepted = (
                    bool(getattr(evaluation, "accepted", False)) and not blockers
                )
                if blockers:
                    self._learning_candidate_pair = None
        return delivery, payload

    def _learning_live_status(self):
        learning = self._learning
        fit_status = FitStatus.IDLE
        status = LearningStatus.COLLECTING
        origin = self._learning_pending_origin
        candidate_digest = None
        candidate_generation = None
        checks = {}
        inert_record = self._activation_runtime.inert_record
        active_record = self._activation_runtime.active_record
        terminated_reason = self._activation_runtime.terminated_reason
        if terminated_reason is not None:
            status = LearningStatus.ERROR
        elif active_record is not None:
            status = LearningStatus.ACTIVE
        elif inert_record is not None or self._activation_runtime.activation_pending:
            status = LearningStatus.ACTIVATING
        elif learning is not None:
            request = learning.pending_request
            prepared = learning.prepared
            handoff = learning.handoff
            if request is not None:
                fit_status = FitStatus.RUNNING if getattr(learning.worker, "busy", False) else FitStatus.QUEUED
                status = LearningStatus.FITTING
                origin = request.origin
                candidate_generation = request.candidate_generation
            elif self._learning_preparing:
                fit_status = FitStatus.RUNNING
                status = LearningStatus.FITTING
            elif prepared is not None:
                fit_status = FitStatus.SUCCEEDED
                status = LearningStatus.EVALUATING
                candidate_digest = prepared.candidate_digest
                candidate_generation = prepared.candidate.request.candidate_generation
                origin = prepared.candidate.request.origin
                blockers = set(prepared.blockers)
                checks = {
                    "identifiability": "failed" if "identifiability" in blockers else "passed",
                    "native_build": "passed" if prepared.candidate_pair is not None else "failed",
                    "native_dry_solve": "passed" if prepared.dry_solve_finite else "failed",
                    "target_timing": (
                        "passed" if prepared.timing is not None and prepared.timing.accepted else "failed"
                    ),
                }
            if handoff is not None:
                status = handoff.status
        active_descriptor = self._active_pair().descriptor
        candidate_descriptor = inert_record.candidate if inert_record is not None else None
        return {
            "status": status.value,
            "fit_status": fit_status.value,
            "role_generation": active_descriptor.role_generation,
            "candidate_generation": (
                candidate_descriptor.candidate_generation if candidate_descriptor is not None else candidate_generation
            ),
            "checkpoint_digest": active_descriptor.model_digest,
            "candidate_digest": (
                candidate_descriptor.model_digest if candidate_descriptor is not None else candidate_digest
            ),
            "origin": None if origin is None else origin.value,
            "checks": checks,
            "activation_phase": (
                inert_record.phase.value
                if inert_record is not None
                else active_record.phase.value
                if active_record is not None
                else "aborted"
            ),
            "pending_persistence": self._activation_runtime.activation_pending,
            "pending_swap": inert_record is not None,
            "failure": (
                None
                if terminated_reason is None
                else {
                    "code": "activation-terminal",
                    "detail": terminated_reason,
                    "terminal": True,
                }
            ),
        }

    def adopt_model(
        self,
        pair: OwnedMpcPair,
        *,
        rmse,
        samples,
        band_c,
        nfev=None,
        origin: CandidateOrigin = CandidateOrigin.COOK_REFIT,
        policy: ActivationPolicy = ActivationPolicy.COOK_REFIT,
    ):
        """Install a complete fitted owner without relabeling incumbent resources."""

        if (
            pair is self._active_pair()
            or pair is self._activation_runtime.rollback_pair
            or not self._pair_factory.validate(pair)
        ):
            raise ValueError("adopted model pair must be a distinct validated owner")
        adopted_metadata = {
            "rmse": float(rmse),
            "samples": int(samples),
            "band_c": [float(band_c[0]), float(band_c[1])],
            "nfev": None if nfev is None else int(nfev),
        }
        previous = self._active_pair()
        self._activation_runtime.replace_active_pair(
            pair,
            retain_current=True,
        )
        self._sync_configuration()
        self._checkpoint_origin = origin
        self._checkpoint_policy = policy
        self._checkpoint_rollback_identity = (
            previous.descriptor.model_digest,
            previous.descriptor.role_generation,
        )
        self._checkpoint_challenger = None
        self._checkpoint_candidate_identity = None
        self._teardown_candidate = None
        self._teardown_candidate_descriptor = None
        self._teardown_fit_window = None
        self._teardown_decision_id = None
        self._checkpoint_activation = ("aborted", False, False)
        self._checkpoint_failure = None
        self._model_meta = adopted_metadata

    def get_model_snapshot(self):
        """Return the complete grey-only v4 checkpoint; process jobs stay live-only."""
        metadata = (
            {"rmse": None, "samples": 0, "band_c": [0.0, 0.0], "nfev": None}
            if self._model_meta is None
            else self._model_meta
        )
        try:
            snapshot = _snapshot.new_grey_learning_snapshot(
                revision=int(self._model_revision),
                parameters=self._snapshot_parameters(),
                metadata=metadata,
            )
            live = self._learning_live_status()
            active = self._active_pair().descriptor
            inert_record = self._activation_runtime.inert_record
            active_record = self._activation_runtime.active_record
            candidate = inert_record.candidate if inert_record is not None else None
            rollback_pair = self._activation_runtime.rollback_pair
            if rollback_pair is not None:
                rollback_digest = rollback_pair.descriptor.model_digest
                rollback_generation = rollback_pair.descriptor.role_generation
            elif self._checkpoint_rollback_identity is not None:
                rollback_digest, rollback_generation = self._checkpoint_rollback_identity
            else:
                rollback_digest = None
                rollback_generation = None
            learning = self._learning
            prepared = None if learning is None else learning.prepared
            if candidate is None and prepared is not None:
                candidate_config = prepared.candidate.config
                candidate_parameters = {
                    key: (candidate_config.delay_states if key == "n_delay" else getattr(candidate_config, key))
                    for key in self.MODEL_PARAM_KEYS
                }
                snapshot["challenger"] = {
                    "parameters": _snapshot.normalize_grey_parameters(candidate_parameters),
                    "metadata": {
                        "rmse": prepared.candidate.rmse_c,
                        "samples": prepared.candidate.sample_count,
                        "band_c": list(prepared.candidate.temperature_band_c),
                        "nfev": prepared.candidate.nfev,
                    },
                }
                snapshot["window"] = asdict(prepared.candidate.request.window)
                candidate_descriptor = self._prepared_candidate_descriptor(prepared)
            elif candidate is None and self._teardown_candidate is not None:
                candidate_config = self._teardown_candidate.config
                candidate_parameters = {
                    key: (candidate_config.delay_states if key == "n_delay" else getattr(candidate_config, key))
                    for key in self.MODEL_PARAM_KEYS
                }
                snapshot["challenger"] = {
                    "parameters": _snapshot.normalize_grey_parameters(candidate_parameters),
                    "metadata": {
                        "rmse": self._teardown_candidate.rmse_c,
                        "samples": self._teardown_candidate.sample_count,
                        "band_c": list(self._teardown_candidate.temperature_band_c),
                        "nfev": self._teardown_candidate.nfev,
                    },
                }
                snapshot["window"] = None if self._teardown_fit_window is None else asdict(self._teardown_fit_window)
                candidate_descriptor = self._teardown_candidate_descriptor
            elif candidate is None and self._checkpoint_challenger is not None:
                snapshot["challenger"] = copy.deepcopy(self._checkpoint_challenger)
                snapshot["window"] = None if self._teardown_fit_window is None else asdict(self._teardown_fit_window)
                candidate_descriptor = self._teardown_candidate_descriptor
            else:
                candidate_descriptor = self._teardown_candidate_descriptor
            snapshot["evidence"] = {
                "eligible": int(self._learning_eligible_updates),
                "rejected": int(self._learning_rejected_updates),
                "confidence_decision_id": (
                    self._teardown_decision_id
                    if self._teardown_decision_id is not None
                    else None
                    if active_record is None
                    else active_record.decision_id
                ),
            }
            checkpoint_origin = (
                CandidateOrigin.OPERATOR_CALIBRATION
                if self._teardown_candidate is not None
                else self._checkpoint_origin
            )
            snapshot["origin"] = checkpoint_origin.value if checkpoint_origin is not None else live["origin"]
            snapshot["policy"] = (
                ActivationPolicy.OPERATOR_REVIEWED.value
                if self._teardown_candidate is not None
                else self._checkpoint_policy.value
                if self._checkpoint_policy is not None
                else inert_record.policy.value
                if inert_record is not None
                else active_record.policy.value
                if active_record is not None
                else None
            )
            snapshot["identification"] = {
                "status": "identified" if self._model_meta is not None else "unidentified",
            }
            if self._cook_refit_outcome is None:
                refit_status, refit_latest = self._checkpoint_cook_refit
            else:
                refit_status = (
                    "succeeded"
                    if self._cook_refit_outcome
                    in {
                        TeardownRefitOutcome.READY_FOR_REVIEW,
                        TeardownRefitOutcome.ACCEPTED_NEXT_COOK,
                    }
                    else "failed"
                )
                refit_latest = self._cook_refit_outcome.value
            snapshot["cook_refit"] = {
                "status": refit_status,
                "latest": refit_latest,
            }
            snapshot["identities"] = {
                "active_digest": active.model_digest,
                "active_generation": active.role_generation,
                "candidate_digest": (
                    candidate.model_digest
                    if candidate is not None
                    else candidate_descriptor.model_digest
                    if isinstance(candidate_descriptor, GreyControlPairDescriptor)
                    else self._checkpoint_candidate_identity[0]
                    if self._checkpoint_candidate_identity is not None
                    else live["candidate_digest"]
                ),
                "candidate_generation": (
                    candidate.candidate_generation
                    if candidate is not None
                    else candidate_descriptor.candidate_generation
                    if isinstance(candidate_descriptor, GreyControlPairDescriptor)
                    else self._checkpoint_candidate_identity[1]
                    if self._checkpoint_candidate_identity is not None
                    else live["candidate_generation"]
                ),
                "rollback_digest": rollback_digest,
                "rollback_generation": rollback_generation,
            }
            live_activation = (
                live["activation_phase"],
                live["pending_persistence"],
                live["pending_swap"],
            )
            activation = (
                self._checkpoint_activation if live_activation == ("aborted", False, False) else live_activation
            )
            snapshot["activation"] = {
                "phase": activation[0],
                "pending_persistence": activation[1],
                "pending_swap": activation[2],
            }
            if live["failure"] is not None:
                snapshot["failure"] = {
                    "code": live["failure"]["code"],
                    "detail": live["failure"]["detail"],
                }
            elif self._checkpoint_failure is not None:
                snapshot["failure"] = {
                    "code": self._checkpoint_failure[0],
                    "detail": self._checkpoint_failure[1],
                }
            else:
                snapshot["failure"] = None
            snapshot["active_pair"] = active.to_dict()
            snapshot["candidate_pair"] = (
                candidate.to_dict()
                if candidate is not None
                else candidate_descriptor.to_dict()
                if isinstance(candidate_descriptor, GreyControlPairDescriptor)
                else None
            )
            encoded = json.dumps(snapshot, allow_nan=False).encode()
        except AttributeError, TypeError, ValueError, OverflowError:
            return None
        return snapshot if len(encoded) <= MAX_SNAPSHOT_BYTES else None

    def _adopt_persisted_revision(self, snapshot) -> None:
        """Continue a stored checkpoint's counter instead of starting a new one.

        The store keeps one revision per controller and rejects every save that
        does not advance past it, permanently -- so this counter has to survive
        the restart, not merely the process. That holds for a checkpoint this
        runtime refuses as much as for one it restores: refusing one means the
        next cook refits from scratch, and a counter that restarted at zero
        alongside it could never persist that refit.
        """
        if not isinstance(snapshot, dict):
            return
        revision = snapshot.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            return
        self._model_revision = max(self._model_revision, revision)

    def restore_model(self, snapshot):
        self._adopt_persisted_revision(snapshot)
        if not isinstance(snapshot, dict) or snapshot.get("version") != self.MODEL_SCHEMA:
            version = snapshot.get("version") if isinstance(snapshot, dict) else None
            self._logger.warning(
                f"[mpc] discarding a version {version!r} model snapshot: runtime restore "
                f"accepts only grey schema {self.MODEL_SCHEMA}; version 3 is migration input only."
            )
            return False
        try:
            owned = _snapshot.migrate_grey_learning_snapshot(snapshot)
        except _snapshot.GreySnapshotInvalid as error:
            self._logger.warning(f"[mpc] discarding an incompatible grey snapshot ({error.reason}).")
            return False
        active = owned["active"]
        params = active["parameters"]
        metadata = active["metadata"]
        configured_n_delay = int(self._configuration()["n_delay"])
        snapshot_n_delay = int(params["n_delay"])
        if configured_n_delay != 8 or snapshot_n_delay != 8:
            self._logger.warning("[mpc] discarding an incompatible grey snapshot (incompatible-delay).")
            return False
        # A null active pair is well-formed v4: it is what the grey migration
        # writes when no prior authority survived, carrying shipped defaults
        # that no fit ever produced. There is no owner to rebuild, so say that
        # rather than discovering it as an attribute error on the null below.
        if owned["active_pair"] is None:
            self._logger.warning(
                "[mpc] discarding a grey snapshot with no active pair: the record holds "
                "parameters but no model to restore. This cook starts from the configured model."
            )
            return False
        try:
            restored_descriptor = GreyControlPairDescriptor.from_dict(owned["active_pair"])
            active_identity = owned["identities"]
            if (
                active_identity["active_digest"] != restored_descriptor.model_digest
                or active_identity["active_generation"] != restored_descriptor.role_generation
            ):
                raise ValueError("restored active identity does not match its pair descriptor")
            candidate_pair_value = owned["candidate_pair"]
            restored_candidate_descriptor = (
                None if candidate_pair_value is None else GreyControlPairDescriptor.from_dict(candidate_pair_value)
            )
            candidate_digest = active_identity["candidate_digest"]
            candidate_generation = active_identity["candidate_generation"]
            if restored_candidate_descriptor is not None and (
                candidate_digest != restored_candidate_descriptor.model_digest
                or candidate_generation != restored_candidate_descriptor.candidate_generation
            ):
                raise ValueError("restored candidate identity does not match its pair descriptor")
            restored_candidate_identity = (
                None
                if candidate_digest is None or candidate_generation is None
                else (candidate_digest, candidate_generation)
            )
            challenger_value = owned["challenger"]
            restored_challenger: dict[str, JsonValue] | None = (
                None if challenger_value is None else copy.deepcopy(challenger_value)
            )
            window_value = owned["window"]
            restored_window = (
                None
                if window_value is None
                else FitWindowIdentity(
                    session_id=window_value["session_id"],
                    cook_id=window_value["cook_id"],
                    first_observation_sequence=window_value["first_observation_sequence"],
                    last_observation_sequence=window_value["last_observation_sequence"],
                    configuration_digest=window_value["configuration_digest"],
                    incumbent_digest=window_value["incumbent_digest"],
                    role_generation=window_value["role_generation"],
                )
            )
            cook_refit_value = owned["cook_refit"]
            raw_refit_status = cook_refit_value["status"]
            if raw_refit_status == "idle":
                restored_refit_status: Literal["idle", "succeeded", "failed"] = "idle"
            elif raw_refit_status == "succeeded":
                restored_refit_status = "succeeded"
            elif raw_refit_status == "failed":
                restored_refit_status = "failed"
            else:
                raise ValueError("unsupported restored cook-refit status")
            restored_cook_refit = (restored_refit_status, cook_refit_value["latest"])
            activation_value = owned["activation"]
            raw_activation_phase = activation_value["phase"]
            if raw_activation_phase == "prepared":
                restored_activation_phase: Literal["prepared", "active", "aborted"] = "prepared"
            elif raw_activation_phase == "active":
                restored_activation_phase = "active"
            elif raw_activation_phase == "aborted":
                restored_activation_phase = "aborted"
            else:
                raise ValueError("unsupported restored activation phase")
            restored_activation = (
                restored_activation_phase,
                activation_value["pending_persistence"],
                activation_value["pending_swap"],
            )
            failure_value = owned["failure"]
            restored_failure = None if failure_value is None else (failure_value["code"], failure_value["detail"])
            restored_pair = self._pair_factory.restore(restored_descriptor)
            restored_parameters = restored_pair.core.snapshot_parameters()
            if any(restored_parameters[key] != params[key] for key in self.MODEL_PARAM_KEYS):
                restored_pair.close()
                raise ValueError("restored active pair does not match active model")
        except Exception as exc:
            self._logger.warning(
                f"[mpc] a stored model could not be built ({exc}); keeping the model this controller started with."
            )
            return False
        staged_learning = None
        if self._learning_enabled:
            restored_components = CandidatePair(
                restored_pair.estimator,
                restored_pair.solver,
            )
            restored_identity = self._learning_identity_for(
                restored_components,
                restored_descriptor.role_generation,
                configuration=restored_pair.core.config,
            )
            try:
                staged_learning = self._build_learning(
                    components=restored_components,
                    identity=restored_identity,
                )
            except BaseException as error:
                restored_pair.close()
                self._logger.warning(
                    f"[mpc] restored learning could not start ({error}); "
                    "keeping the model this controller started with."
                )
                return False
        old_learning = self._learning
        try:
            self._activation_runtime.replace_active_pair(
                restored_pair,
                retain_current=False,
            )
        except BaseException as error:
            if staged_learning is not None:
                staged_learning.close()
            if self._activation_runtime.active_pair is not restored_pair and not restored_pair.closed:
                restored_pair.close()
            self._logger.warning(
                f"[mpc] a stored model could not replace the active owner ({error}); "
                "keeping the model this controller started with."
            )
            return False
        self._sync_configuration()
        self._learning = staged_learning
        if old_learning is not None:
            old_learning.close()
        # Said for the model that will actually solve. __init__'s own call saw
        # only the configured parameters, which for a grill that has been
        # learning are not the ones about to steer it. A restored model is
        # calibrated by its own fit record, so pass the metadata rather than
        # letting parameter distance stand in for it.
        warn_about_model(self._configuration(), metadata, logger=self._logger)
        self._model_meta = {
            "rmse": metadata["rmse"],
            "samples": metadata["samples"],
            "band_c": list(metadata["band_c"]),
            "nfev": metadata["nfev"],
        }
        restored_origin = owned["origin"]
        self._checkpoint_origin = None if restored_origin is None else CandidateOrigin(restored_origin)
        restored_policy = owned["policy"]
        self._checkpoint_policy = None if restored_policy is None else ActivationPolicy(restored_policy)
        rollback_digest = owned["identities"]["rollback_digest"]
        rollback_generation = owned["identities"]["rollback_generation"]
        self._checkpoint_rollback_identity = (
            None if rollback_digest is None or rollback_generation is None else (rollback_digest, rollback_generation)
        )
        self._checkpoint_challenger = restored_challenger
        self._teardown_candidate = None
        self._teardown_candidate_descriptor = restored_candidate_descriptor
        self._teardown_fit_window = restored_window
        self._checkpoint_candidate_identity = restored_candidate_identity
        self._teardown_decision_id = owned["evidence"]["confidence_decision_id"]
        self._checkpoint_cook_refit = restored_cook_refit
        self._checkpoint_activation = restored_activation
        self._checkpoint_failure = restored_failure
        self._cook_refit_outcome = None
        self._cook_refit_finalized = False
        if owned["identification"]["status"] != "identified":
            self._model_meta = None
        self._learning_eligible_updates = owned["evidence"]["eligible"]
        self._learning_rejected_updates = owned["evidence"]["rejected"]
        self._rotate_teardown_role_generation(restored_descriptor.role_generation)
        self.get_model_snapshot()
        return True

    def _close_prepared_candidate(self, preparation) -> None:
        pair = getattr(preparation, "candidate_pair", None)
        if pair is None:
            return
        self._pair_factory.close_components(pair.estimator, pair.controller)

    def _persist_operator_teardown_authority(self, window, descriptor) -> str:
        session_id = getattr(self, "_learning_session_id", None) or "mpc-learning"
        cook_id = getattr(self, "_learning_cook_id", None) or "none"
        decision_id = (
            f"teardown:{session_id}:{cook_id}:{window.first_observation_sequence}:"
            f"{window.last_observation_sequence}:{descriptor.model_digest}"
        )
        timestamp_ms = self._clock_ms()
        assessment = CandidateAssessmentEvidence(
            decision_id=decision_id,
            origin=CandidateOrigin.OPERATOR_CALIBRATION.value,
            policy=ActivationPolicy.OPERATOR_REVIEWED.value,
            fit_accepted=True,
            identifiability_accepted=True,
            native_build="passed",
            native_dry_solve="passed",
            target_timing="passed",
            confidence_accepted=True,
            rejection_reasons=(),
        )
        self._persist_grey_lifecycle(
            assessment,
            GreyCandidateAssessmentPayload(
                decision_id=decision_id,
                origin=assessment.origin,
                policy=assessment.policy,
                fit_accepted=True,
                identifiability_accepted=True,
                native_build="passed",
                native_dry_solve="passed",
                target_timing="passed",
                confidence_accepted=True,
                rejection_reasons=assessment.rejection_reasons,
            ),
            timestamp_ms=timestamp_ms,
            role_generation=window.role_generation,
            model_digest=descriptor.model_digest,
            provenance_digest=window.incumbent_digest,
        )
        confidence = ModelEvidenceRecord(
            evidence_id=f"activation-confidence:{decision_id}:{window.role_generation}",
            kind=EvidenceKind.CONFIDENCE_DECISION,
            session_id=session_id,
            cook_id=None if cook_id == "none" else cook_id,
            timestamp_ms=timestamp_ms,
            role_generation=window.role_generation,
            model_digest=descriptor.model_digest,
            provenance_digest=window.incumbent_digest,
            payload=ConfidenceDecisionEvidence(
                decision_id=decision_id,
                blocked=False,
                reason=None,
            ),
        )
        receipt = self._activation_runtime.submit_activation_confidence(confidence)
        if not receipt.accepted or receipt.wait(2.0) is not True or receipt.durable is not True:
            raise RuntimeError("operator-review-confidence-not-durable")
        self._activation_runtime.mark_confidence_persisted(decision_id)
        self._teardown_decision_id = decision_id
        return decision_id

    def _refit_completed_frames(self) -> TeardownRefitResult:
        from controller.model_promotion import evaluate
        from controller.update_mpc import fit_quality

        frames = self._teardown_history.observations
        origin = self._teardown_history.origin
        if len(frames) < _REFIT_MIN_SAMPLES:
            return TeardownRefitResult.insufficient(f"only {len(frames)} samples; need {_REFIT_MIN_SAMPLES}")
        identity = self.learning_identity()
        window = identity.window(
            frames[0].observation_sequence,
            frames[-1].observation_sequence,
        )
        request_identity = {
            "origin": origin.value,
            "window": asdict(window),
            "candidate_generation": identity.candidate_generation,
        }
        request = FitRequest(
            request_id=hashlib.sha256(
                json.dumps(request_identity, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            origin=origin,
            window=window,
            candidate_generation=identity.candidate_generation,
        )
        with self._learning_lock:
            learning = self._learning
            self._learning = None
            self._learning_pending_origin = None
            self._learning_candidate_pair = None
        if learning is not None:
            learning.close()

        worker = self._fit_worker_factory()
        try:
            worker.start()
            if (
                worker.submit(GreyFitJob(request, frames, self._active_components().controller.config))
                is not FitSubmission.ACCEPTED
            ):
                return TeardownRefitResult.failed("fitting worker was busy", origin=origin)
            message = worker.receive(timeout_s=120.0)
        except Exception as error:
            return TeardownRefitResult.failed(f"fit failed: {error}", origin=origin)
        finally:
            worker.close()
        if isinstance(message.outcome, GreyFitError):
            return TeardownRefitResult.failed(
                f"fit failed: {message.outcome.detail}",
                origin=origin,
            )
        success = message.outcome
        candidate_parameters = {
            key: (success.config.delay_states if key == "n_delay" else getattr(success.config, key))
            for key in self.MODEL_PARAM_KEYS
        }
        incumbent = {key: float(self._configuration()[key]) for key in self.MODEL_PARAM_KEYS}
        times = np.array(
            [frame.frame_end_s - frames[0].frame_end_s for frame in frames],
            dtype=float,
        )
        temperatures = np.array([frame.temp_c for frame in frames], dtype=float)
        realized = np.array(
            [frame.realized_q for frame in frames[1:]] + [frames[-1].realized_q],
            dtype=float,
        )
        incumbent_rmse, _ = fit_quality(
            times,
            temperatures,
            realized,
            incumbent,
            T_amb=float(self._configuration()["T_amb"]),
        )
        verdict = evaluate(
            candidate_parameters,
            incumbent,
            candidate_rmse=success.rmse_c,
            incumbent_rmse=incumbent_rmse,
            identifiability=success.identifiability,
        )
        if not verdict.accepted:
            return TeardownRefitResult.rejected(verdict.reason, origin=origin)
        preparation = prepare_candidate_off_path(
            success,
            incumbent_pair=CandidatePair(self._active_components().estimator, self._active_components().controller),
            estimator_factory=self._pair_factory.build_estimator,
            controller_factory=self._pair_factory.build_solver,
            timing_probe=self._pair_factory.probe_solver,
        )
        if not preparation.accepted:
            reason = ",".join(preparation.blockers) or "candidate-preparation-rejected"
            return TeardownRefitResult.rejected(reason, origin=origin)
        estimator_kind = self._active_pair().descriptor.estimator_kind
        if estimator_kind == "ekf":
            candidate_estimator_kind: Literal["ekf", "kf"] = "ekf"
        elif estimator_kind == "kf":
            candidate_estimator_kind = "kf"
        else:
            self._close_prepared_candidate(preparation)
            return TeardownRefitResult.rejected("unsupported-estimator-kind", origin=origin)
        candidate_configuration = self._pair_factory.native(
            success.config,
            estimator_kind=candidate_estimator_kind,
            candidate_generation=identity.candidate_generation,
            role_generation=identity.role_generation + 1,
        )
        descriptor = self._pair_factory.descriptor(candidate_configuration)
        self._teardown_fit_window = window
        if origin is CandidateOrigin.OPERATOR_CALIBRATION:
            try:
                self._persist_operator_teardown_authority(window, descriptor)
                self._teardown_candidate = success
                self._teardown_candidate_descriptor = descriptor
                return TeardownRefitResult.ready_for_review(
                    verdict.reason,
                    candidate_digest=descriptor.model_digest,
                )
            finally:
                self._close_prepared_candidate(preparation)
        prepared_pair = preparation.candidate_pair
        if prepared_pair is None:
            raise RuntimeError("accepted candidate preparation lost its owned resources")
        owned_candidate = self._pair_factory.adopt(
            candidate_configuration,
            prepared_pair.estimator,
            prepared_pair.controller,
            authorized=False,
        )
        try:
            self.adopt_model(
                owned_candidate,
                rmse=success.rmse_c,
                samples=success.sample_count,
                band_c=success.temperature_band_c,
                nfev=success.nfev,
                origin=origin,
                policy=(
                    ActivationPolicy.PASSIVE_AUTO
                    if origin is CandidateOrigin.PASSIVE_ONLINE
                    else ActivationPolicy.COOK_REFIT
                ),
            )
        except BaseException:
            owned_candidate.close()
            raise
        return TeardownRefitResult.accepted_next_cook(
            verdict.reason,
            candidate_digest=descriptor.model_digest,
        )

    def finalize_cook_refit(self, outcome) -> bool:
        normalized = outcome if isinstance(outcome, TeardownRefitOutcome) else TeardownRefitOutcome(outcome)
        if self._cook_refit_finalized:
            if normalized is not TeardownRefitOutcome.CHECKPOINT_FAILURE:
                return False
            self._cook_refit_outcome = normalized
            return True
        self._cook_refit_finalized = True
        self._cook_refit_outcome = normalized
        self._model_revision += 1
        return True

    def cook_history(self):
        """The cook's (time_s, temp_c, Q_applied) rows, oldest first."""
        return list(self._cook_history())

    def refit_from_cook(self, history=None):
        """Refit the thermal model from a finished cook and judge the result.

        Between cooks only: a refit re-simulates the whole history once per
        least-squares evaluation, so it belongs nowhere near the control path.
        It runs synchronously on its caller's thread and takes seconds, bounded
        by `_HISTORY_MAX` -- see HoldLearningRuntime.refit_once for why spending
        teardown is safe. An accepted model replaces the complete owned pair
        only after the cook has ended, so the resulting checkpoint—not a
        mid-cook numerical relabel—authorizes it for the next cook.
        """
        from controller.model_promotion import evaluate
        from controller.update_mpc import fit_params, fit_quality, identifiability

        if history is None:
            return self._refit_completed_frames()

        rows = list(history if history is not None else self._cook_history())
        if len(rows) < _REFIT_MIN_SAMPLES:
            return _Verdict(False, f"only {len(rows)} samples; need {_REFIT_MIN_SAMPLES}")

        started = time.perf_counter()
        t = np.array([r[0] for r in rows], dtype=float)
        temp = np.array([r[1] for r in rows], dtype=float)
        Q = np.array([r[2] for r in rows], dtype=float)
        t = t - t[0]

        T_amb = float(self._configuration()["T_amb"])
        try:
            fitted = fit_params(
                t,
                temp,
                Q,
                T_amb=T_amb,
                init=dict(_REFIT_INIT),
                sigma=float(self._configuration()["sigma"]),
                n_delay=int(self._configuration()["n_delay"]),
            )
            # A solve that ran out of evaluations reports its best point so
            # far, and that point has not been shown to be a minimum -- so it
            # is refused. The converse is not available: scipy calls a stalled
            # step and a stalled cost "converged" too, and a one-evaluation
            # solve that moved nowhere reports the same flag as a hard-won
            # fit. Convergence can only veto here; what earns a promotion is
            # the error comparison and the bounds below.
            #
            # It vetoes before anything is measured, so no statistic is ever
            # taken at a point that is already refused -- including at one the
            # model cannot be simulated at, which a diverging solve's best
            # point can be.
            if not fitted["converged"]:
                self._logger.info(
                    f"[mpc] refit: abandoned after {fitted['nfev']} evaluations over "
                    f"{len(rows)} samples in {time.perf_counter() - started:.1f} s"
                )
                return _Verdict(
                    False,
                    f"the solve did not converge within {fitted['nfev']} evaluations",
                )
            # The candidate starts from a fixed reference, but it is judged
            # against the model actually driving the grill: the question this
            # answers is whether to replace THAT, on this cook's own data.
            incumbent = {k: float(self._configuration()[k]) for k in self.MODEL_PARAM_KEYS}
            cand_rmse, _ = fit_quality(t, temp, Q, fitted, T_amb=T_amb)
            inc_rmse, _ = fit_quality(t, temp, Q, incumbent, T_amb=T_amb)
            # How much this cook actually determined, measured at the point the
            # solve landed on. Six more simulations against the solve's own
            # hundreds, and the only thing asked here that the fit residual
            # cannot say -- a flat cook fits itself perfectly and pins nothing.
            ident = identifiability(t, Q, fitted, T_amb=T_amb, T0=float(temp[0]))
        except (ValueError, FloatingPointError) as e:
            return _Verdict(False, f"fit failed: {e}")
        except Exception:
            _Verdict(False, "fit failed")
            raise

        verdict = evaluate(
            fitted,
            incumbent,
            candidate_rmse=cand_rmse,
            incumbent_rmse=inc_rmse,
            identifiability=ident,
        )
        self._logger.info(
            f"[mpc] refit: {verdict.reason} (candidate RMSE {cand_rmse:.2f} C, "
            f"incumbent {inc_rmse:.2f} C, {fitted['nfev']} evaluations over "
            f"{len(rows)} samples in {time.perf_counter() - started:.1f} s)"
        )
        if verdict.accepted:
            active_descriptor = self._active_pair().descriptor
            candidate_settings = dict(self._configuration())
            candidate_settings.update({key: fitted[key] for key in self.MODEL_PARAM_KEYS if key in fitted})
            candidate_pair: OwnedMpcPair | None = None
            try:
                candidate_pair = self._pair_factory.build(
                    self._pair_factory.configured(
                        candidate_settings,
                        candidate_generation=active_descriptor.candidate_generation + 1,
                        role_generation=active_descriptor.role_generation + 1,
                        model_identified=True,
                    ),
                    authorized=False,
                )
                self.adopt_model(
                    candidate_pair,
                    rmse=cand_rmse,
                    samples=len(rows),
                    band_c=(float(temp.min()), float(temp.max())),
                    nfev=fitted["nfev"],
                )
            except Exception as error:
                if candidate_pair is not None and candidate_pair is not self._active_pair():
                    candidate_pair.close()
                return _Verdict(False, f"candidate construction failed: {error}")
        return verdict

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        learning = self._learning
        self._learning = None
        if learning is not None:
            learning.close()
