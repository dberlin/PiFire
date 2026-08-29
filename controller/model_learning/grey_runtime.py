"""Grey learning, lifecycle evidence, snapshot, and refit ownership."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Literal, Protocol

import numpy as np

from common.control_trace import (
    ChallengerProgressTracePayload,
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
from common.learning_trajectory import (
    FitCorpusIdentity,
    ModelFitLineage,
    canonical_model_fit_lineage_digest,
    trajectory_json_value,
)
from common.model_evidence import (
    ActivationLifecycleEvidence,
    CalibrationSummaryEvidence,
    CandidateAssessmentEvidence,
    ChallengerRoundEvidence,
    ConfidenceDecisionEvidence,
    EvidenceKind,
    FitLifecycleEvidence,
    ForecastOriginEvidence,
    ModelEvidenceRecord,
)
from common.persistence.learning_trajectory import FitCorpusSnapshot
from common.persistence.model_challenger import (
    ModelChallengerConflictError,
    ModelChallengerState,
    abort_model_challenger_activation,
    complete_model_challenger_round,
    create_model_challenger,
    prepare_model_challenger_activation,
    qualify_model_challenger,
    read_model_challenger,
    recover_model_challenger,
    retire_model_challenger,
)
from common.persistence.model_evidence import read_model_evidence
from controller import mpc_snapshot as _snapshot
from controller.acados import GreyBoxMPCConfig
from controller.grey_box import GreyBoxPredictionAdapter
from controller.model_learning.activation import (
    ActivationManager,
    ActivationRequest,
    GreyControlPairDescriptor,
    PreparedActivationRecord,
)
from controller.model_learning.activation_runtime import ActivationRuntime
from controller.model_learning.confidence import qualification_gates
from controller.model_learning.contracts import (
    ActivationPolicy,
    CandidateOrigin,
    FitRequest,
    FitStatus,
    FrameObservation,
    LearningStatus,
    activation_policy_for_origin,
)
from controller.model_learning.evaluation import EvaluationConfig, EvaluationDecision
from controller.mpc_config import (
    DEFAULT_MPC_CONFIG,
    JsonValue,
    MpcConfig,
    warn_about_model,
)
from controller.mpc_factory import MpcPairFactory, OwnedMpcPair
from controller.mpc_model import MODEL_SCHEMA
from controller.runtime.context import EVENT_LOG_NAME
from controller.runtime.model_fitting import (
    CandidateOwnershipTransferredError,
    CandidatePair,
    CandidatePreparation,
    FitSubmission,
    GreyFitError,
    GreyFitSuccess,
    GreyFitWorker,
    GreyLearningOrchestrator,
    LiveLearningIdentity,
    TargetTimingEvidence,
    grey_config_digest,
    persistent_corpus_trigger,
    segmented_corpus_fit_job,
)
from controller.runtime.model_persistence import DurableActivationReceipt


class _FitCorpusRepository(Protocol):
    def snapshot_fit_corpus(
        self,
        fit_partition_digest: str,
        *,
        through_revision: int | None = None,
    ) -> FitCorpusSnapshot: ...

    def record_fit_request(
        self,
        snapshot: FitCorpusSnapshot,
        lineage: ModelFitLineage,
    ) -> object: ...

    def complete_fit(
        self,
        request_id: str,
        *,
        candidate_digest: str | None,
        error: str | None,
    ) -> object: ...

    def mark_fit_stale(self, request_id: str) -> object: ...


@dataclass(frozen=True, slots=True)
class _ProcessLearningBinding:
    learning: GreyLearningOrchestrator
    lease: int


@dataclass(frozen=True, slots=True)
class _CorpusFitIntent:
    ticket: str
    origin: CandidateOrigin
    replace_owned_prepared: bool = False


class GreyLearningProcessOwner:
    """Process-owned in-memory fit worker and prepared-candidate lifecycle."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._active_runs = 0
        self._transitioning = False
        self._learning: GreyLearningOrchestrator | None = None
        self._identity: LiveLearningIdentity | None = None
        self._lease = 0
        self._terminal_tickets: dict[str, CandidateOrigin] = {}
        self._closed = False

    @property
    def learning(self) -> GreyLearningOrchestrator | None:
        with self._lock:
            return self._learning

    def has_pending_fit(self) -> bool:
        with self._lock:
            learning = self._learning
            return learning is not None and (learning.pending_request is not None or learning.worker.busy)

    def bind(
        self,
        builder: Callable[[], GreyLearningOrchestrator],
        *,
        identity: LiveLearningIdentity,
        config: GreyBoxMPCConfig,
        incumbent_pair: CandidatePair,
        estimator_factory: Callable[..., object],
        controller_factory: Callable[..., object],
        timing_probe: Callable[..., object],
    ) -> _ProcessLearningBinding:
        with self._condition:
            if self._closed:
                raise RuntimeError("grey learning process owner is closed")
            self._transitioning = True
            try:
                self._condition.wait_for(lambda: self._active_runs == 0)
                learning = self._learning
                defer_rebind = False
                if learning is None:
                    learning = builder()
                    self._learning = learning
                elif learning.prepared is not None and learning.prepared.accepted:
                    # A replacement controller has not restored its active
                    # authority or bound the next cook yet. Keep the process-
                    # owned challenger inert until rebind() can compare the
                    # complete live identity.
                    defer_rebind = True
                else:
                    learning.rebind_process(
                        identity,
                        config=config,
                        incumbent_pair=incumbent_pair,
                        estimator_factory=estimator_factory,
                        controller_factory=controller_factory,
                        timing_probe=timing_probe,
                    )
                self._identity = None if defer_rebind else identity
                self._lease += 1
                return _ProcessLearningBinding(learning, self._lease)
            finally:
                self._transitioning = False
                self._condition.notify_all()

    def rebind(
        self,
        *,
        identity: LiveLearningIdentity,
        config: GreyBoxMPCConfig,
        incumbent_pair: CandidatePair,
    ) -> _ProcessLearningBinding:
        with self._condition:
            if self._closed or self._learning is None:
                raise RuntimeError("grey learning process owner is not bound")
            self._transitioning = True
            try:
                self._condition.wait_for(lambda: self._active_runs == 0)
                self._learning.rebind_process(
                    identity,
                    config=config,
                    incumbent_pair=incumbent_pair,
                )
                self._identity = identity
                self._lease += 1
                return _ProcessLearningBinding(self._learning, self._lease)
            finally:
                self._transitioning = False
                self._condition.notify_all()

    def run(
        self,
        lease: int,
        operation: Callable[
            [GreyLearningOrchestrator, LiveLearningIdentity],
            object,
        ],
    ):
        with self._condition:
            if (
                self._closed
                or self._transitioning
                or self._learning is None
                or self._identity is None
                or lease <= 0
                or lease > self._lease
            ):
                return None
            learning = self._learning
            identity = self._identity
            self._active_runs += 1
        try:
            return operation(learning, identity)
        finally:
            with self._condition:
                self._active_runs -= 1
                self._condition.notify_all()

    def record_terminal(
        self,
        lease: int,
        ticket: str,
        origin: CandidateOrigin,
    ) -> None:
        with self._lock:
            if 0 < lease <= self._lease:
                self._terminal_tickets[ticket] = origin

    def consume_terminal(
        self,
        ticket: str,
        origin: CandidateOrigin,
    ) -> bool:
        with self._lock:
            if self._terminal_tickets.get(ticket) is not origin:
                return False
            del self._terminal_tickets[ticket]
            return True

    def prepared(self, lease: int) -> CandidatePreparation | None:
        with self._lock:
            if (
                self._closed
                or self._transitioning
                or self._learning is None
                or self._identity is None
                or lease != self._lease
            ):
                return None
            return self._learning.prepared

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._transitioning = True
            self._condition.wait_for(lambda: self._active_runs == 0)
            learning = self._learning
            self._learning = None
        if learning is not None:
            learning.close()


class GreyLearningRuntime:
    """Own persistent grey fitting, causal evaluation, and model lifecycle state."""

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
        trajectory_repository: _FitCorpusRepository | None = None,
        fit_partition_digest: Callable[[], str | None] | None = None,
        process_owner: GreyLearningProcessOwner | None = None,
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
        self._trajectory_repository = trajectory_repository
        self._fit_partition_digest = fit_partition_digest
        self._process_owner = process_owner
        self._process_lease: int | None = None
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
        self._corpus_fit_intents: deque[_CorpusFitIntent] = deque()
        self._corpus_fit_failure: tuple[str, str] | None = None
        self._terminal_fit_tickets: dict[str, CandidateOrigin] = {}
        self._challenger_state: ModelChallengerState | None = None
        self._checkpoint_origin: CandidateOrigin | None = None
        self._checkpoint_policy: ActivationPolicy | None = None
        self._checkpoint_rollback_identity: tuple[str, int] | None = None
        self._checkpoint_decision_id: str | None = None
        self._checkpoint_preparation: CandidatePreparation | None = None
        self._checkpoint_preparation_key: tuple[FitRequest, str] | None = None
        self._checkpoint_activation: tuple[Literal["prepared", "active", "aborted"], bool, bool] = (
            "aborted",
            False,
            False,
        )
        self._checkpoint_failure: tuple[str, str] | None = None
        self._learning_eligible_updates = 0
        self._learning_rejected_updates = 0
        self._model_revision = 0
        self._model_meta: dict[str, JsonValue] | None = None
        if learning_enabled or trajectory_repository is not None:
            if process_owner is None:
                self._learning = self._build_learning()
            else:
                components = self._active_components()
                identity = self.learning_identity()
                binding = process_owner.bind(
                    lambda: self._build_learning(
                        components=components,
                        identity=identity,
                    ),
                    identity=identity,
                    config=components.controller.config,
                    incumbent_pair=components,
                    estimator_factory=self._pair_factory.build_estimator,
                    controller_factory=self._pair_factory.build_solver,
                    timing_probe=self._pair_factory.probe_solver,
                )
                self._learning = binding.learning
                self._process_lease = binding.lease
        else:
            self._learning = None

    @property
    def model_metadata(self) -> dict[str, JsonValue] | None:
        return None if self._model_meta is None else copy.deepcopy(self._model_meta)

    @property
    def learning_role_generation(self) -> int:
        return self._learning_role_generation

    def model_authority(self) -> tuple[int, dict[str, JsonValue] | None]:
        return self._model_revision, self.model_metadata

    def sync_activation_generation(self, *, exact: bool = False) -> None:
        generation = self._activation_runtime.role_generation
        self._model_revision = generation if exact else max(self._model_revision, generation)
        self._rotate_learning_role_generation(self._model_revision)
        state = self._challenger_state
        if state is None:
            try:
                state = read_model_challenger()
            except ValueError:
                state = None
        if state is None or state.phase == "retired":
            return
        active = self._active_pair().descriptor
        if active == state.candidate:
            with self._learning_lock:
                self._challenger_state = state
            self._retire_durable_challenger("activated")
        elif active != state.incumbent:
            with self._learning_lock:
                self._challenger_state = state
            self._retire_durable_challenger("active-authority-changed")

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
            worker=self._fit_worker_factory(),
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

    def _current_learning_candidate_pair(self) -> CandidatePair | None:
        owner = self._process_owner
        lease = self._process_lease
        if owner is not None and lease is not None:
            prepared = owner.prepared(lease)
            if prepared is None or not prepared.accepted:
                return None
            with self._learning_lock:
                lineage_is_current = self._checkpoint_preparation is prepared
            if not lineage_is_current:
                self._adopt_prepared_checkpoint_lineage(prepared)
            return prepared.candidate_pair
        with self._learning_lock:
            return self._learning_candidate_pair

    def _register_learning_forecasts(self, observation):
        pair = self._current_learning_candidate_pair()
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
        """Dispatch completed frames without materializing fit data on this worker."""
        if not isinstance(observation, FrameObservation):
            raise TypeError("observation must be a FrameObservation")
        with self._learning_lock:
            learning = self._learning
            preparing = self._learning_preparing
        if learning is None or self._corpus_fit_failure is not None:
            return None
        operator_frame = (
            observation.calibration_fit or observation.calibration_stage is not None or observation.probe_q != 0.0
        )
        with self._learning_evaluation_lock:
            result = learning.observe_completed_frame(
                observation,
                identifiability=0.0 if preparing else 1.0,
            )
            if result.history.accepted and not operator_frame:
                self._register_learning_forecasts(observation)
        request = getattr(result, "request", None)
        with self._learning_lock:
            if learning is self._learning and isinstance(request, FitRequest):
                self._learning_pending_origin = request.origin
                self._learning_pending_fit_transition = request
            if result.history.accepted:
                self._learning_eligible_updates += 1
            else:
                self._learning_rejected_updates += 1
            evaluation = self._learning_pending_evaluation
            self._learning_pending_evaluation = None
            confidence_accepted = self._learning_pending_confidence_accepted
            self._learning_pending_confidence_accepted = None
            evaluation_decision_id = getattr(evaluation, "decision_id", None)
            confidence_already_persisted = isinstance(
                evaluation_decision_id,
                str,
            ) and self._activation_runtime.consume_confidence_persisted(evaluation_decision_id)
        if result.history.accepted and not operator_frame:
            self.request_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)
        forecasts = tuple(self._completed_forecast_evidence(value) for value in result.completed_forecasts)
        reasons = tuple(result.history.reasons)
        trigger = result.trigger
        return {
            "role_generation": observation.role_generation,
            "eligible": bool(result.history.accepted),
            "rejection_reasons": reasons,
            "input_variance": trigger.input_variance,
            "input_levels": trigger.input_levels,
            "effective_updates": self._learning_eligible_updates,
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
        self._learning_rejected_updates += 1
        return {
            "role_generation": observation.role_generation,
            "eligible": False,
            "rejection_reasons": ("learner-exception",),
            "input_variance": 0.0,
            "input_levels": 0,
            "effective_updates": self._learning_eligible_updates,
            "model_digest": grey_config_digest(self._active_components().controller.config),
            "learner_error": f"{type(error).__name__}: {error}",
            "forecast_origin_evidence": (),
        }

    def _rotate_learning_role_generation(self, role_generation: int) -> None:
        normalized = int(role_generation)
        if normalized == self._learning_role_generation:
            return
        self._learning_role_generation = normalized

    def bind_learning_identity(self, session_id, cook_id, role_generation):
        """Fence learning work to the runner's current cook/configuration identity."""
        with self._learning_lifecycle_lock:
            self._learning_session_id = session_id
            self._learning_cook_id = cook_id
            self._rotate_learning_role_generation(role_generation)
            with self._learning_lock:
                learning = self._learning
            if learning is not None:
                components = self._active_components()
                identity = self.learning_identity()
                with self._learning_evaluation_lock:
                    if self._process_owner is None:
                        learning.update_identity(
                            identity,
                            config=components.controller.config,
                            incumbent_pair=components,
                        )
                    else:
                        binding = self._process_owner.rebind(
                            identity=identity,
                            config=components.controller.config,
                            incumbent_pair=components,
                        )
                        self._learning = binding.learning
                        self._process_lease = binding.lease
                with self._learning_lock:
                    if learning is self._learning:
                        self._learning_pending_origin = None
                        self._learning_pending_fit_transition = None
                        if self._process_owner is None:
                            prepared = learning.prepared
                            self._learning_candidate_pair = (
                                prepared.candidate_pair if prepared is not None and prepared.accepted else None
                            )

    def request_corpus_fit(
        self,
        origin: CandidateOrigin,
        *,
        replace_owned_prepared: bool = False,
    ) -> bool:
        return (
            self._request_corpus_fit_ticket(
                origin,
                replace_owned_prepared=replace_owned_prepared,
            )
            is not None
        )

    def _request_corpus_fit_ticket(
        self,
        origin: CandidateOrigin,
        *,
        replace_owned_prepared: bool = False,
    ) -> str | None:
        """Queue an authorized persistent-corpus fit without touching storage."""
        if not isinstance(origin, CandidateOrigin):
            raise TypeError("origin must be a CandidateOrigin")
        if type(replace_owned_prepared) is not bool:
            raise TypeError("replace_owned_prepared must be a bool")
        if (
            self._closed
            or self._corpus_fit_failure is not None
            or self._trajectory_repository is None
            or self._fit_partition_digest is None
            or (origin is CandidateOrigin.PASSIVE_ONLINE and not replace_owned_prepared and not self._learning_enabled)
        ):
            return None
        process_fit_pending = (
            origin is CandidateOrigin.PASSIVE_ONLINE
            and self._process_owner is not None
            and self._process_owner.has_pending_fit()
        )
        with self._learning_lock:
            for index, intent in enumerate(self._corpus_fit_intents):
                if intent.origin is origin:
                    if replace_owned_prepared and not intent.replace_owned_prepared:
                        self._corpus_fit_intents[index] = _CorpusFitIntent(
                            intent.ticket,
                            intent.origin,
                            replace_owned_prepared=True,
                        )
                    return intent.ticket
            if origin is CandidateOrigin.PASSIVE_ONLINE:
                challenger = self._challenger_state
                if (
                    process_fit_pending
                    or not replace_owned_prepared
                    and (
                        self._learning_pending_origin is origin
                        or challenger is not None
                        and challenger.phase != "retired"
                    )
                ):
                    return None
            intent = _CorpusFitIntent(
                secrets.token_hex(32),
                origin,
                replace_owned_prepared=replace_owned_prepared,
            )
            self._corpus_fit_intents.append(intent)
            self._learning_pending_origin = origin
            return intent.ticket

    def _fail_corpus_learning(self, code: str, error: BaseException | str) -> None:
        detail = error if isinstance(error, str) else f"{type(error).__name__}: {error}"
        self._logger.warning(f"[mpc] cumulative learning failed closed ({code}): {detail}")
        with self._learning_lock:
            self._corpus_fit_failure = (code, detail)
            self._corpus_fit_intents.clear()
            self._learning_pending_origin = None

    def fail_corpus_fit(
        self,
        code: str,
        error: BaseException | str,
    ) -> None:
        """Fail queued corpus learning closed from an off-path lifecycle owner."""
        self._fail_corpus_learning(code, error)

    def _persist_prepared_cumulative_supersession(
        self,
        prepared: CandidatePreparation,
    ) -> None:
        request = prepared.candidate.request
        self._persist_rejected_candidate(
            request,
            model_digest=prepared.candidate_digest,
            reasons=("superseded-by-newer-cumulative-fit",),
            fit_accepted=True,
            identifiability_accepted=True,
            preparation=prepared,
        )
        self._retire_durable_challenger("superseded-by-newer-cumulative-fit")

    def _clear_superseded_prepared_candidate(
        self,
        prepared: CandidatePreparation,
    ) -> None:
        with self._learning_lock:
            if self._learning_candidate_pair is prepared.candidate_pair:
                self._learning_candidate_pair = None
            if self._checkpoint_preparation is prepared:
                self._model_revision += 1
                self._checkpoint_preparation = None
                self._checkpoint_preparation_key = None
                self._checkpoint_decision_id = None
                self._checkpoint_origin = None
                self._checkpoint_policy = None
                self._checkpoint_activation = ("aborted", False, False)

    def _terminalize_blocked_corpus_fit(
        self,
        repository: _FitCorpusRepository,
        request: FitRequest,
        intent: _CorpusFitIntent,
        *,
        reason: str,
    ) -> bool:
        try:
            repository.mark_fit_stale(request.request_id)
            self._persist_fit_transition(
                request,
                status="stale",
                model_digest=request.parent_incumbent_digest,
            )
            self._persist_rejected_candidate(
                request,
                model_digest=request.parent_incumbent_digest,
                reasons=(reason,),
                fit_accepted=False,
                identifiability_accepted=False,
            )
        except Exception as error:
            self._fail_corpus_learning("fit-run-persistence-failed", error)
            return False
        with self._learning_lock:
            if self._corpus_fit_intents and self._corpus_fit_intents[0] is intent:
                self._corpus_fit_intents.popleft()
            if self._learning_pending_origin is request.origin:
                self._learning_pending_origin = None
        self._record_terminal_fit_ticket(request)
        return True

    @staticmethod
    def _record_corpus_fit_request(
        repository: _FitCorpusRepository,
        snapshot: FitCorpusSnapshot,
        request: FitRequest,
    ) -> None:
        repository.record_fit_request(
            snapshot,
            ModelFitLineage(
                request_id=request.request_id,
                parent_incumbent_digest=request.parent_incumbent_digest,
                parent_incumbent_generation=request.parent_incumbent_generation,
                candidate_generation=request.candidate_generation,
                fit_corpus=request.fit_corpus,
                fit_corpus_digest=request.fit_corpus.corpus_digest,
                trigger_origin=request.origin.value,
                result_status="running",
                candidate_digest=None,
            ),
        )

    def _submit_requested_corpus_fit(
        self,
        learning: GreyLearningOrchestrator,
        *,
        identity: LiveLearningIdentity | None = None,
    ) -> FitRequest | None:
        repository = self._trajectory_repository
        partition_resolver = self._fit_partition_digest
        if repository is None or partition_resolver is None:
            return None
        with self._learning_lock:
            if not self._corpus_fit_intents or learning.pending_request is not None:
                return None
            intent = self._corpus_fit_intents[0]
            origin = intent.origin
        prepared_to_replace: CandidatePreparation | None = None
        terminal_rejection_reason: str | None = None
        prepared = learning.prepared
        if prepared is not None and prepared.accepted:
            if not intent.replace_owned_prepared:
                terminal_rejection_reason = "superseded-by-prepared-candidate"
            elif prepared.candidate.request.origin is CandidateOrigin.OPERATOR_CALIBRATION:
                terminal_rejection_reason = "superseded-by-prepared-operator-calibration-candidate"
            else:
                prepared_to_replace = prepared
        try:
            partition = partition_resolver()
            if partition is None:
                self._fail_corpus_learning(
                    "corpus-snapshot-failed",
                    "no compatible persistent corpus partition is available",
                )
                return None
            snapshot = repository.snapshot_fit_corpus(partition)
        except Exception as error:
            self._fail_corpus_learning("corpus-snapshot-failed", error)
            return None

        if origin is CandidateOrigin.PASSIVE_ONLINE and not intent.replace_owned_prepared:
            trigger = persistent_corpus_trigger(
                snapshot,
                config=learning.trigger_config,
            )
            if not trigger.ready:
                self._logger.info("[mpc] cumulative corpus fit is not ready: " + ", ".join(trigger.blockers))
                with self._learning_lock:
                    if self._corpus_fit_intents and self._corpus_fit_intents[0] is intent:
                        self._corpus_fit_intents.popleft()
                    if self._learning_pending_origin is origin:
                        self._learning_pending_origin = None
                self._record_terminal_fit_intent(intent)
                return None
        identity = self.learning_identity() if identity is None else identity
        request = FitRequest(
            request_id=intent.ticket,
            origin=origin,
            fit_corpus=snapshot.identity,
            configuration_digest=identity.configuration_digest,
            parent_incumbent_digest=identity.incumbent_digest,
            parent_incumbent_generation=identity.role_generation,
            candidate_generation=identity.candidate_generation,
        )
        if terminal_rejection_reason is not None:
            try:
                self._record_corpus_fit_request(
                    repository,
                    snapshot,
                    request,
                )
            except Exception as error:
                self._fail_corpus_learning("fit-run-persistence-failed", error)
                return None
            return (
                request
                if self._terminalize_blocked_corpus_fit(
                    repository,
                    request,
                    intent,
                    reason=terminal_rejection_reason,
                )
                else None
            )
        try:
            job = segmented_corpus_fit_job(
                snapshot,
                request,
                learning.config,
            )
        except Exception as error:
            self._fail_corpus_learning("corpus-snapshot-failed", error)
            return None
        try:
            self._record_corpus_fit_request(
                repository,
                snapshot,
                request,
            )
        except Exception as error:
            self._fail_corpus_learning("fit-run-persistence-failed", error)
            return None
        try:
            superseded = False
            if prepared_to_replace is None:
                submission = learning.submit_corpus_fit(job)
            else:
                submission, superseded = learning.submit_superseding_corpus_fit(
                    job,
                    prepared_to_replace,
                    persist=lambda: self._persist_prepared_cumulative_supersession(
                        prepared_to_replace,
                    ),
                )
            if submission is not FitSubmission.ACCEPTED:
                raise RuntimeError("fitting worker was busy")
            if superseded:
                self._clear_superseded_prepared_candidate(prepared_to_replace)
        except Exception as error:
            try:
                repository.complete_fit(
                    request.request_id,
                    candidate_digest=None,
                    error=f"{type(error).__name__}: {error}",
                )
            except Exception as completion_error:
                self._fail_corpus_learning(
                    "fit-run-persistence-failed",
                    completion_error,
                )
                return None
            self._fail_corpus_learning("fit-submission-failed", error)
            return None
        with self._learning_lock:
            if self._corpus_fit_intents and self._corpus_fit_intents[0] is intent:
                self._corpus_fit_intents.popleft()
            self._learning_pending_origin = origin
            self._learning_pending_fit_transition = request
        self._logger.info(f"[mpc] submitted cumulative corpus fit {request.request_id} for {request.origin.value}")
        return request

    def _complete_corpus_fit(self, delivery: object) -> bool:
        repository = self._trajectory_repository
        message = getattr(delivery, "message", None)
        if repository is None or message is None:
            return True
        stale_reasons = tuple(getattr(delivery, "stale_reasons", ()))
        if stale_reasons:
            try:
                repository.mark_fit_stale(message.request.request_id)
            except Exception as completion_error:
                self._fail_corpus_learning(
                    "fit-run-persistence-failed",
                    completion_error,
                )
                return False
            return True
        outcome = getattr(message, "outcome", None)
        if isinstance(outcome, GreyFitSuccess):
            candidate_digest = grey_config_digest(outcome.config)
            error = None
        elif isinstance(outcome, GreyFitError):
            candidate_digest = None
            error = outcome.detail
        else:
            candidate_digest = None
            error = "fit worker returned an invalid outcome"
        try:
            repository.complete_fit(
                message.request.request_id,
                candidate_digest=candidate_digest,
                error=error,
            )
        except Exception as completion_error:
            self._fail_corpus_learning(
                "fit-run-persistence-failed",
                completion_error,
            )
            return False
        self._logger.info(
            f"[mpc] completed cumulative corpus fit {message.request.request_id}: "
            + ("succeeded" if error is None else f"failed ({error})")
        )
        return True

    def _calibration_manifest_for_corpus(
        self,
        fit_corpus: FitCorpusIdentity,
    ) -> dict[str, object] | None:
        """Bind one complete calibration run to exact calibration frames in the fit corpus."""
        repository = self._trajectory_repository
        if repository is None:
            raise RuntimeError("calibration-manifest-unavailable")

        windows: list[tuple[frozenset[str], str | None, int, int]] = []
        for corpus_slice in fit_corpus.slices:
            segment = repository.read_segment(corpus_slice.segment_id)
            if segment is None:
                raise RuntimeError("calibration-manifest-corpus-missing")
            frames = (*segment.pre_roll_frames, *segment.scored_hold_frames)
            if corpus_slice.through_ordinal >= len(frames):
                raise RuntimeError("calibration-manifest-corpus-incomplete")
            session_ids = frozenset(
                (
                    segment.trajectory_session_id,
                    *segment.trace_session_ids,
                )
            )
            for frame in frames[: corpus_slice.through_ordinal + 1]:
                if frame.calibration_origin:
                    windows.append(
                        (
                            session_ids,
                            segment.cook_id,
                            frame.monotonic_start_ms,
                            frame.monotonic_end_ms,
                        )
                    )
        if not windows:
            return None

        database_path = getattr(repository, "_database_path", None)
        records = read_model_evidence(
            kind=EvidenceKind.CALIBRATION_SUMMARY,
            database_path=database_path,
        )
        grouped: dict[
            tuple[str, str | None, int],
            dict[str, ModelEvidenceRecord],
        ] = {}
        stage_order = ("low", "middle", "high", "coast")
        for record in records:
            payload = record.payload
            if (
                not isinstance(payload, CalibrationSummaryEvidence)
                or not payload.accepted
                or not payload.continuous
                or not isinstance(payload.command_revision, int)
                or isinstance(payload.command_revision, bool)
                or payload.command_revision <= 0
                or payload.command_action == "none"
                or payload.stage not in stage_order
            ):
                continue
            belongs_to_corpus = any(
                record.session_id in session_ids
                and record.cook_id == cook_id
                and start_ms <= record.timestamp_ms <= end_ms
                for session_ids, cook_id, start_ms, end_ms in windows
            )
            if not belongs_to_corpus:
                continue
            stage_index = stage_order.index(payload.stage)
            if tuple(payload.completed_stages) != stage_order[:stage_index]:
                continue
            key = (record.session_id, record.cook_id, payload.command_revision)
            by_stage = grouped.setdefault(key, {})
            existing = by_stage.get(payload.stage)
            if existing is None or (record.timestamp_ms, record.evidence_id) > (
                existing.timestamp_ms,
                existing.evidence_id,
            ):
                by_stage[payload.stage] = record

        complete_runs: list[tuple[tuple[str, str | None, int], tuple[ModelEvidenceRecord, ...]]] = []
        for key, by_stage in grouped.items():
            if set(by_stage) != set(stage_order):
                continue
            selected = tuple(by_stage[stage] for stage in stage_order)
            if len({record.evidence_id for record in selected}) == len(stage_order):
                complete_runs.append((key, selected))
        if not complete_runs:
            return None

        key, selected = max(
            complete_runs,
            key=lambda item: (
                item[0][2],
                item[1][-1].timestamp_ms,
                item[0][0],
                item[0][1] or "",
                tuple(record.evidence_id for record in item[1]),
            ),
        )
        return {
            "command_revision": key[2],
            "session_id": key[0],
            "completed_stages": list(stage_order),
            "stage_evidence_ids": [record.evidence_id for record in selected],
        }

    def _persist_durable_challenger(
        self,
        learning: GreyLearningOrchestrator,
        preparation: CandidatePreparation,
    ) -> ModelChallengerState:
        request = preparation.candidate.request
        fit_corpus = request.fit_corpus
        calibration_manifest = (
            self._calibration_manifest_for_corpus(fit_corpus)
            if request.origin is CandidateOrigin.OPERATOR_CALIBRATION
            else None
        )
        manifest_blocked = request.origin is CandidateOrigin.OPERATOR_CALIBRATION and calibration_manifest is None
        incumbent = self._active_pair().descriptor
        candidate = self._prepared_candidate_descriptor(preparation)
        lineage = ModelFitLineage(
            request_id=request.request_id,
            parent_incumbent_digest=incumbent.model_digest,
            parent_incumbent_generation=incumbent.role_generation,
            candidate_generation=candidate.candidate_generation,
            fit_corpus=fit_corpus,
            fit_corpus_digest=fit_corpus.corpus_digest,
            trigger_origin=request.origin.value,
            result_status="succeeded",
            candidate_digest=candidate.model_digest,
        )
        current = read_model_challenger()
        if (
            current is not None
            and current.phase != "retired"
            and current.fit_lineage == lineage
            and current.fit_corpus == fit_corpus
            and current.incumbent == incumbent
            and current.candidate == candidate
            and current.origin is request.origin
            and current.controller_configuration_digest == request.configuration_digest
            and trajectory_json_value(current.calibration_manifest) == calibration_manifest
            and not manifest_blocked
        ):
            with self._learning_lock:
                self._challenger_state = current
            self._adopt_prepared_checkpoint_lineage(preparation)
            return current
        timing = preparation.timing
        now_ms = self._clock_ms()
        state = ModelChallengerState(
            schema_version=1,
            challenger_id=f"challenger-{secrets.token_hex(16)}",
            revision=0,
            phase="retired" if manifest_blocked else "evaluating",
            origin=request.origin,
            policy=self._policy_for_learning_origin(request.origin),
            fit_corpus=fit_corpus,
            fit_lineage=lineage,
            fit_preparation={
                "request_id": request.request_id,
                "accepted": True,
                "candidate_digest": candidate.model_digest,
                "required_horizons": list(learning.evaluation_config.required_horizons),
                "native_build": "passed",
                "dry_solve": "passed" if preparation.dry_solve_finite else "failed",
                "target_timing": (None if timing is None else trajectory_json_value(asdict(timing))),
                "fit_corpus_digest": request.fit_corpus.corpus_digest,
                "fit_result": {
                    "rmse_c": preparation.candidate.rmse_c,
                    "max_error_c": preparation.candidate.max_error_c,
                    "identifiability": preparation.candidate.identifiability,
                    "sample_count": preparation.candidate.sample_count,
                    "temperature_band_c": list(preparation.candidate.temperature_band_c),
                    "nfev": preparation.candidate.nfev,
                    "result_digest": preparation.candidate.result_digest,
                },
            },
            controller_configuration_digest=request.configuration_digest,
            incumbent=incumbent,
            candidate=candidate,
            calibration_manifest=calibration_manifest,
            evaluation_epoch=0,
            evaluation_round=0,
            consecutive_wins=0,
            required_wins=learning.evaluation_config.required_consecutive_wins,
            last_decision_id=None,
            last_evidence_id=None,
            activation_transaction_id=None,
            retirement_reason="calibration-manifest" if manifest_blocked else None,
            created_ms=now_ms,
            updated_ms=now_ms,
            retired_ms=now_ms if manifest_blocked else None,
        )
        if current is not None and current.phase != "retired":
            retired_current = retire_model_challenger(
                expected_revision=current.revision,
                reason="superseded-by-new-cumulative-fit",
                retired_ms=now_ms,
            )
            self._trace_durable_challenger(retired_current)
        durable = create_model_challenger(state)
        with self._learning_lock:
            self._challenger_state = durable
        self._trace_durable_challenger(durable)
        if not manifest_blocked:
            self._adopt_prepared_checkpoint_lineage(preparation)
        return durable

    def _retire_durable_challenger(self, reason: str) -> None:
        state = self._challenger_state
        if state is None:
            try:
                state = read_model_challenger()
            except ValueError:
                return
        if state is None or state.phase == "retired":
            return
        try:
            retired = retire_model_challenger(
                expected_revision=state.revision,
                reason=reason,
                retired_ms=self._clock_ms(),
            )
        except ModelChallengerConflictError:
            return
        with self._learning_lock:
            self._challenger_state = retired
            self._model_revision += 1
        self._trace_durable_challenger(retired)

    def _abort_durable_challenger_activation(
        self,
        record: PreparedActivationRecord,
        reason: str,
    ) -> None:
        state = self._challenger_state
        if state is None or state.phase != "activating" or state.activation_transaction_id != record.transaction_id:
            return
        try:
            retired = abort_model_challenger_activation(
                expected_revision=state.revision,
                activation_transaction_id=record.transaction_id,
                reason=reason,
                retired_ms=self._clock_ms(),
            )
        except ModelChallengerConflictError:
            return
        with self._learning_lock:
            self._challenger_state = retired
            self._model_revision += 1
        self._trace_durable_challenger(retired)

    def _record_terminal_fit_intent(self, intent: _CorpusFitIntent) -> None:
        self._record_terminal_fit_identity(intent.ticket, intent.origin)

    def _record_terminal_fit_ticket(self, request: FitRequest) -> None:
        self._record_terminal_fit_identity(request.request_id, request.origin)

    def _record_terminal_fit_identity(
        self,
        ticket: str,
        origin: CandidateOrigin,
    ) -> None:
        owner = self._process_owner
        lease = self._process_lease
        if owner is not None and lease is not None:
            owner.record_terminal(lease, ticket, origin)
            return
        with self._learning_lock:
            self._terminal_fit_tickets[ticket] = origin

    def _consume_terminal_fit_ticket(
        self,
        ticket: str,
        origin: CandidateOrigin,
    ) -> bool:
        owner = self._process_owner
        if owner is not None:
            return owner.consume_terminal(ticket, origin)
        with self._learning_lock:
            if self._terminal_fit_tickets.get(ticket) is not origin:
                return False
            del self._terminal_fit_tickets[ticket]
            return True

    def poll_learning_off_path(self, *, live_origin=None):
        """Drain and prepare fits only from the runner's lifecycle dispatcher."""
        with self._learning_lifecycle_lock:
            owner = self._process_owner
            lease = self._process_lease
            if owner is None:
                return self._poll_learning_off_path_locked(live_origin=live_origin)
            if lease is None:
                return None, None
            result = owner.run(
                lease,
                lambda learning, identity: self._poll_learning_off_path_locked(
                    live_origin=live_origin,
                    authoritative_learning=learning,
                    authoritative_identity=identity,
                ),
            )
            return (None, None) if result is None else result

    def _durable_challenger_horizons(
        self,
        state: ModelChallengerState,
    ) -> tuple[tuple[int, ...], tuple[int, ...]]:
        fit_preparation = trajectory_json_value(state.fit_preparation)
        stored_required_horizons = (
            fit_preparation.get("required_horizons") if isinstance(fit_preparation, dict) else None
        )
        if stored_required_horizons is None:
            required_horizons = EvaluationConfig().required_horizons
        elif (
            not isinstance(stored_required_horizons, list)
            or not stored_required_horizons
            or any(type(horizon) is not int or horizon <= 0 for horizon in stored_required_horizons)
        ):
            raise RuntimeError("durable challenger required horizons are invalid")
        else:
            required_horizons = tuple(stored_required_horizons)
        if state.last_evidence_id is None:
            return (), required_horizons

        repository = self._trajectory_repository
        records = read_model_evidence(
            kind=EvidenceKind.CHALLENGER_ROUND,
            database_path=getattr(repository, "_database_path", None),
        )
        evidence = next(
            (record for record in reversed(records) if record.evidence_id == state.last_evidence_id),
            None,
        )
        if evidence is None or not isinstance(evidence.payload, ChallengerRoundEvidence):
            raise RuntimeError("durable challenger round evidence is absent")
        payload = evidence.payload
        if (
            payload.challenger_id != state.challenger_id
            or payload.incumbent_digest != state.incumbent.model_digest
            or payload.candidate_digest != state.candidate.model_digest
            or evidence.role_generation != state.incumbent.role_generation
            or payload.required_horizons != required_horizons
        ):
            raise RuntimeError("durable challenger round lineage changed")
        current_round = (
            payload.evaluation_epoch == state.evaluation_epoch and payload.evaluation_round == state.evaluation_round
        )
        resumed_round = state.evaluation_round == 0 and state.evaluation_epoch == payload.evaluation_epoch + 1
        if not current_round and not resumed_round:
            raise RuntimeError("durable challenger round progress changed")
        return payload.completed_horizons, payload.required_horizons

    def _trace_durable_challenger(self, state: ModelChallengerState) -> None:
        try:
            completed_horizons, required_horizons = self._durable_challenger_horizons(state)
            fit_preparation = trajectory_json_value(state.fit_preparation)
            fit_result = fit_preparation.get("fit_result") if isinstance(fit_preparation, dict) else None
            result_digest = fit_result.get("result_digest") if isinstance(fit_result, dict) else None
            if not isinstance(result_digest, str):
                raise TypeError("durable challenger fit result digest is absent")
            payload = ChallengerProgressTracePayload(
                challenger_id=state.challenger_id,
                challenger_revision=state.revision,
                phase=state.phase,
                origin=state.origin.value,
                policy=state.policy.value,
                incumbent_digest=state.incumbent.model_digest,
                incumbent_generation=state.incumbent.role_generation,
                candidate_digest=state.candidate.model_digest,
                candidate_generation=state.candidate.candidate_generation,
                corpus_digest=state.fit_corpus.corpus_digest,
                lineage_digest=canonical_model_fit_lineage_digest(state.fit_lineage),
                result_digest=result_digest,
                evaluation_epoch=state.evaluation_epoch,
                evaluation_round=state.evaluation_round,
                consecutive_wins=state.consecutive_wins,
                required_wins=state.required_wins,
                completed_horizons=completed_horizons,
                required_horizons=required_horizons,
                resumed_from_previous_cook=(
                    state.evaluation_epoch > 0 or bool(getattr(self._learning, "resumed_from_previous_cook", False))
                ),
                reset_reason=state.retirement_reason,
            )
            self._append_trace(
                (
                    ControlTraceRecord(
                        ts_ms=state.updated_ms,
                        session_id=(getattr(self, "_learning_session_id", None) or "mpc-learning"),
                        cook_id=getattr(self, "_learning_cook_id", None),
                        controller=ControllerType.MPC,
                        event_kind=TraceEventKind.CHALLENGER_PROGRESS,
                        payload=payload,
                    ),
                )
            )
        except Exception as error:
            self._activation_runtime.terminate(f"durable challenger trace failed: {error}")

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
        return activation_policy_for_origin(origin)

    def _prepared_candidate_descriptor(
        self,
        preparation: CandidatePreparation,
    ) -> GreyControlPairDescriptor:
        request = preparation.candidate.request
        configured = self._pair_factory.configured(
            self._configuration(),
            candidate_generation=request.candidate_generation,
            role_generation=request.parent_incumbent_generation + 1,
        )
        return self._pair_factory.descriptor(
            self._pair_factory.native(
                preparation.candidate.config,
                estimator_kind=configured.estimator_kind,
                candidate_generation=configured.candidate_generation,
                role_generation=configured.role_generation,
            )
        )

    def _adopt_prepared_checkpoint_lineage(
        self,
        preparation: CandidatePreparation,
    ) -> None:
        """Bind one accepted fit's checkpoint lineage and advance it once."""

        with self._learning_lock:
            request = preparation.candidate.request
            descriptor = self._prepared_candidate_descriptor(preparation)
            preparation_key = (request, descriptor.model_digest)
            if preparation_key != self._checkpoint_preparation_key:
                self._model_revision += 1
            self._checkpoint_preparation = preparation
            self._checkpoint_preparation_key = preparation_key
            self._checkpoint_origin = request.origin
            self._checkpoint_policy = self._policy_for_learning_origin(request.origin)

    def _clear_prepared_checkpoint_lineage(self) -> None:
        with self._learning_lock:
            self._checkpoint_preparation = None
            self._checkpoint_preparation_key = None

    def _persist_durable_evaluation_round(
        self,
        evaluation: EvaluationDecision,
        preparation: CandidatePreparation | None,
    ) -> ModelChallengerState:
        state = self._challenger_state
        if state is None:
            state = read_model_challenger()
        if state is None or preparation is None:
            raise RuntimeError("durable challenger authority is absent")
        timestamp_ms = max(
            state.updated_ms,
            max(
                (int(origin.completion_time_s * 1_000) for origin in evaluation.completed_origins),
                default=self._clock_ms(),
            ),
        )
        evaluation_config = getattr(self._learning, "evaluation_config", None)
        required_horizons = tuple(
            getattr(
                evaluation_config,
                "required_horizons",
                evaluation.completed_horizons,
            )
        )
        completed_horizons = evaluation.completed_horizons
        if completed_horizons != required_horizons:
            raise RuntimeError("partial causal evaluation round cannot persist")
        gates = qualification_gates(state)
        if "calibration-manifest" in gates.blockers:
            retired = retire_model_challenger(
                expected_revision=state.revision,
                reason="calibration-manifest",
                retired_ms=timestamp_ms,
            )
            with self._learning_lock:
                self._challenger_state = retired
                self._model_revision += 1
            self._trace_durable_challenger(retired)
            return retired
        if state.last_decision_id == evaluation.decision_id:
            if (
                evaluation.role_generation != state.incumbent.role_generation
                or evaluation.candidate_generation != state.candidate.candidate_generation
                or evaluation.incumbent_digest != state.incumbent.model_digest
                or evaluation.challenger_digest != state.candidate.model_digest
            ):
                raise RuntimeError("durable challenger evaluation lineage changed")
            gates = qualification_gates(state)
            if state.phase == "evaluating" and gates.accepted:
                state = qualify_model_challenger(
                    expected_revision=state.revision,
                    qualified_ms=timestamp_ms,
                )
                with self._learning_lock:
                    self._challenger_state = state
                    self._model_revision += 1
                self._trace_durable_challenger(state)
            return state
        round_number = state.evaluation_round + 1
        evidence = ModelEvidenceRecord(
            evidence_id=(
                f"challenger-round:{state.challenger_id}:"
                f"{state.evaluation_epoch}:{round_number}:{evaluation.decision_id}"
            ),
            kind=EvidenceKind.CHALLENGER_ROUND,
            session_id=self._learning_session_id or "mpc-learning",
            cook_id=self._learning_cook_id,
            timestamp_ms=timestamp_ms,
            role_generation=evaluation.role_generation,
            model_digest=evaluation.challenger_digest,
            provenance_digest=evaluation.incumbent_digest,
            payload=ChallengerRoundEvidence(
                challenger_id=state.challenger_id,
                evaluation_epoch=state.evaluation_epoch,
                evaluation_round=round_number,
                decision_id=evaluation.decision_id,
                accepted=not bool(evaluation.blockers),
                required_horizons=required_horizons,
                completed_horizons=completed_horizons,
                incumbent_digest=evaluation.incumbent_digest,
                candidate_digest=evaluation.challenger_digest,
            ),
        )
        progressed = complete_model_challenger_round(
            expected_revision=state.revision,
            evidence=evidence,
        )
        self._trace_durable_challenger(progressed)
        gates = qualification_gates(progressed)
        if progressed.phase == "evaluating" and gates.accepted:
            progressed = qualify_model_challenger(
                expected_revision=progressed.revision,
                qualified_ms=timestamp_ms,
            )
            self._trace_durable_challenger(progressed)
        with self._learning_lock:
            self._challenger_state = progressed
            self._model_revision += 1
        return progressed

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
        durable = self._challenger_state
        durable_causal_confidence = (
            durable is not None
            and durable.last_decision_id == evaluation.decision_id
            and durable.phase in {"qualified", "activating"}
            and qualification_gates(durable).accepted
        )
        causal_origin = origin in {
            CandidateOrigin.PASSIVE_ONLINE,
            CandidateOrigin.OPERATOR_CALIBRATION,
        }
        confidence_accepted = (
            bool(getattr(evaluation, "accepted", False))
            and not blockers
            and (not causal_origin or durable_causal_confidence)
        )
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
        payload = FitLifecycleEvidence(
            request_id=request.request_id,
            status=status,
            origin=request.origin.value,
            policy=policy.value,
            fit_corpus_digest=request.fit_corpus.corpus_digest,
            error=error,
        )
        return self._persist_grey_lifecycle(
            payload,
            GreyFitLifecyclePayload(
                request_id=payload.request_id,
                status=payload.status,
                origin=payload.origin,
                policy=payload.policy,
                fit_corpus_digest=payload.fit_corpus_digest,
                error=payload.error,
            ),
            timestamp_ms=self._clock_ms(),
            role_generation=request.parent_incumbent_generation,
            model_digest=model_digest,
            provenance_digest=request.parent_incumbent_digest,
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
            role_generation=request.parent_incumbent_generation,
            model_digest=model_digest,
            provenance_digest=request.parent_incumbent_digest,
        )

    def prepare_automatic_activation(
        self,
        preparation: CandidatePreparation,
        policy: ActivationPolicy,
        evaluation: EvaluationDecision | None = None,
    ) -> str:
        """Persist one causal-auto candidate and expose it only after durable PREPARED."""

        if policy is not ActivationPolicy.CAUSAL_AUTO:
            raise ValueError("automatic candidate requires causal-auto policy")
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
        if (
            request.origin
            not in {
                CandidateOrigin.PASSIVE_ONLINE,
                CandidateOrigin.OPERATOR_CALIBRATION,
            }
            or activation_policy_for_origin(request.origin) is not policy
        ):
            raise ValueError("candidate origin is not eligible for causal-auto activation")
        owned_candidate = self._pair_factory.adopt(
            self._pair_factory.native(
                native_config,
                estimator_kind=estimator_kind,
                candidate_generation=request.candidate_generation,
                role_generation=request.parent_incumbent_generation + 1,
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
            receipt = DurableActivationReceipt(accepted=True)
            try:
                state = self._challenger_state
                if state is None:
                    state = read_model_challenger()
                if state is None:
                    raise RuntimeError("durable challenger authority is absent")
                activating = prepare_model_challenger_activation(
                    expected_revision=state.revision,
                    activation=record,
                )
                with self._learning_lock:
                    self._challenger_state = activating
                self._trace_durable_challenger(activating)
            except BaseException as error:
                receipt._complete(durable=False, error=error)
            else:
                receipt._complete(durable=True)
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
            ActivationRequest(
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
            self._abort_durable_challenger_activation(
                decision.record,
                "activation-transition-rejected",
            )
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
            self._abort_durable_challenger_activation(
                decision.record,
                "learning-lifecycle-persistence-failed",
            )
            self._activation_runtime.abort_prepared_activation(
                decision.record,
                "learning-lifecycle-persistence-failed",
            )
            with self._learning_lock:
                if self._learning_candidate_pair is candidate_pair:
                    self._learning_candidate_pair = None
            raise CandidateOwnershipTransferredError(str(error)) from error
        return decision.record.transaction_id

    def _poll_learning_off_path_locked(
        self,
        *,
        live_origin=None,
        authoritative_learning: GreyLearningOrchestrator | None = None,
        authoritative_identity: LiveLearningIdentity | None = None,
    ):
        """Run one lifecycle poll while identity mutation is fenced."""
        with self._learning_lock:
            learning = self._learning if authoritative_learning is None else authoritative_learning
            if learning is None:
                return None, None
            queued = self._learning_pending_fit_transition
            self._learning_pending_fit_transition = None
            origin = self._learning_pending_origin if live_origin is None else live_origin
            if origin is not None:
                origin = origin if isinstance(origin, CandidateOrigin) else CandidateOrigin(origin)

        if (
            self._submit_requested_corpus_fit(
                learning,
                identity=authoritative_identity,
            )
            is not None
        ):
            return None, None
        with self._learning_lock:
            self._learning_preparing = origin is not None

        if queued is not None:
            self._persist_fit_transition(
                queued,
                status="queued",
                model_digest=queued.parent_incumbent_digest,
            )

        delivery = None
        try:
            if origin is not None:
                delivery = learning.poll_fit_off_path(
                    live_identity=(
                        self.learning_identity() if authoritative_identity is None else authoritative_identity
                    ),
                    live_origin=origin,
                )
        finally:
            with self._learning_lock:
                self._learning_preparing = False

        delivered_preparation = (
            None
            if delivery is None
            else getattr(
                delivery,
                "preparation",
                getattr(delivery, "prepared", None),
            )
        )
        with self._learning_lock:
            if learning is not self._learning:
                return delivery, None
            if delivery is not None:
                self._learning_pending_origin = None

        if delivery is not None:
            if not self._complete_corpus_fit(delivery):
                return delivery, None
            if delivered_preparation is not None and delivered_preparation.accepted:
                try:
                    durable = self._persist_durable_challenger(
                        learning,
                        delivered_preparation,
                    )
                except Exception as error:
                    learning._release_prepared()
                    learning._reset_prepared_evaluation()
                    self._fail_corpus_learning(
                        "challenger-persistence-failed",
                        error,
                    )
                    return delivery, None
                if durable.phase == "retired":
                    learning._release_prepared()
                    learning._reset_prepared_evaluation()
                elif self._process_owner is None:
                    with self._learning_lock:
                        self._learning_candidate_pair = delivered_preparation.candidate_pair
            terminal_request = getattr(
                getattr(delivery, "message", None),
                "request",
                None,
            )
            if isinstance(terminal_request, FitRequest):
                self._record_terminal_fit_ticket(terminal_request)

        if delivery is not None and getattr(delivery, "message", None) is not None:
            request = delivery.message.request
            outcome = delivery.message.outcome
            stale_reasons = tuple(getattr(delivery, "stale_reasons", ()))
            delivery_blockers = tuple(getattr(delivery, "blockers", ()))
            completed_config = getattr(outcome, "config", None)
            candidate_digest = (
                grey_config_digest(completed_config)
                if isinstance(completed_config, GreyBoxMPCConfig)
                else request.parent_incumbent_digest
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
                if "candidate-supersession-persistence-failed" in stale_reasons:
                    self._fail_corpus_learning(
                        "candidate-supersession-persistence-failed",
                        "durable rejection of the superseded candidate failed",
                    )
                    return delivery, None
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
            durable = self._challenger_state
            if (
                evaluation is not None
                and durable is not None
                and durable.phase != "evaluating"
                and (durable.phase == "retired" or durable.last_decision_id != evaluation.decision_id)
            ):
                evaluation = None
            blockers = () if evaluation is None else tuple(evaluation.blockers)
            preparation = getattr(learning, "prepared", None)
            if evaluation is not None:
                durable = self._persist_durable_evaluation_round(
                    evaluation,
                    preparation,
                )
                self._persist_candidate_evaluation(evaluation, preparation)
            if blockers:
                learning.retire_evaluated_candidate(evaluation)
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
            and preparation_origin
            in {
                CandidateOrigin.PASSIVE_ONLINE,
                CandidateOrigin.OPERATOR_CALIBRATION,
            }
            and durable is not None
            and durable.phase == "qualified"
            and durable.last_decision_id == evaluation.decision_id
            and qualification_gates(durable).accepted
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
        durable = self._challenger_state
        if durable is not None and durable.phase == "retired":
            durable = None
        fit_status = FitStatus.IDLE
        status = LearningStatus.COLLECTING
        origin = self._learning_pending_origin
        candidate_digest = None
        candidate_generation = None
        checks = {}
        required_horizons = tuple(
            getattr(
                getattr(learning, "evaluation_config", None),
                "required_horizons",
                (3, 15, 45, 90, 180),
            )
        )
        completed_horizons = tuple(getattr(learning, "completed_horizons", ()))
        resumed_from_previous_cook = bool(getattr(learning, "resumed_from_previous_cook", False))
        pending_origins = tuple(getattr(learning, "pending_origins", ()))
        inert_record = self._activation_runtime.inert_record
        active_record = self._activation_runtime.active_record
        terminated_reason = self._activation_runtime.terminated_reason
        if durable is not None:
            origin = durable.origin
            candidate_digest = durable.candidate.model_digest
            candidate_generation = durable.candidate.candidate_generation
        if terminated_reason is not None:
            status = LearningStatus.ERROR
        elif self._corpus_fit_failure is not None:
            status = LearningStatus.ERROR
            fit_status = FitStatus.FAILED
        elif active_record is not None:
            status = LearningStatus.ACTIVE
        elif (
            inert_record is not None
            or self._activation_runtime.activation_pending
            or durable is not None
            and durable.phase == "activating"
        ):
            status = LearningStatus.ACTIVATING
        elif durable is not None and durable.phase == "qualified":
            status = LearningStatus.QUALIFIED
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
                status = (
                    LearningStatus.INTERRUPTED
                    if resumed_from_previous_cook
                    and self._learning_cook_id is None
                    and not completed_horizons
                    and not pending_origins
                    else LearningStatus.WARMING
                    if resumed_from_previous_cook and not completed_horizons and not pending_origins
                    else LearningStatus.EVALUATING
                )
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
        elif durable is not None and durable.phase == "evaluating":
            status = LearningStatus.INTERRUPTED
            resumed_from_previous_cook = True
        if status in {
            LearningStatus.QUALIFIED,
            LearningStatus.ACTIVATING,
            LearningStatus.ACTIVE,
        }:
            completed_horizons = required_horizons

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
            "completed_horizons": completed_horizons,
            "required_horizons": required_horizons,
            "resumed_from_previous_cook": resumed_from_previous_cook,
            "pending_origins": [
                {
                    "origin_sequence": item.origin_sequence,
                    "horizon_steps": item.horizon_steps,
                    "role_generation": item.role_generation,
                    "candidate_generation": item.candidate_generation,
                    "incumbent_digest": item.incumbent_digest,
                    "candidate_digest": item.challenger_digest,
                }
                for item in pending_origins
            ],
            "failure": (
                {
                    "code": "activation-terminal",
                    "detail": terminated_reason,
                    "terminal": True,
                }
                if terminated_reason is not None
                else {
                    "code": self._corpus_fit_failure[0],
                    "detail": self._corpus_fit_failure[1],
                    "terminal": False,
                }
                if self._corpus_fit_failure is not None
                else None
            ),
        }

    def get_model_snapshot(self):
        """Return the grey-only v6 checkpoint with one challenger authority reference."""

        metadata = (
            {"rmse": None, "samples": 0, "band_c": [0.0, 0.0], "nfev": None}
            if self._model_meta is None
            else self._model_meta
        )
        with self._learning_lock:
            model_revision = self._model_revision
            checkpoint_decision_id = self._checkpoint_decision_id
            checkpoint_origin = self._checkpoint_origin
            checkpoint_policy = self._checkpoint_policy
            checkpoint_activation = self._checkpoint_activation
            checkpoint_failure = self._checkpoint_failure
            checkpoint_rollback_identity = self._checkpoint_rollback_identity
        try:
            durable_challenger = read_model_challenger()
        except ValueError:
            durable_challenger = None
        if durable_challenger is not None:
            with self._learning_lock:
                self._challenger_state = durable_challenger
        challenger_authority = (
            None
            if durable_challenger is None or durable_challenger.phase == "retired"
            else {
                "challenger_id": durable_challenger.challenger_id,
                "revision": durable_challenger.revision,
            }
        )
        try:
            snapshot = _snapshot.new_grey_learning_snapshot(
                revision=int(model_revision),
                parameters=self._snapshot_parameters(),
                metadata=metadata,
            )
            live = self._learning_live_status()
            active = self._active_pair().descriptor
            active_record = self._activation_runtime.active_record
            rollback_pair = self._activation_runtime.rollback_pair
            if rollback_pair is not None:
                rollback_digest = rollback_pair.descriptor.model_digest
                rollback_generation = rollback_pair.descriptor.role_generation
            elif checkpoint_rollback_identity is not None:
                rollback_digest, rollback_generation = checkpoint_rollback_identity
            else:
                rollback_digest = None
                rollback_generation = None
            snapshot["evidence"] = {
                "eligible": int(self._learning_eligible_updates),
                "rejected": int(self._learning_rejected_updates),
                "confidence_decision_id": (
                    durable_challenger.last_decision_id
                    if challenger_authority is not None
                    else checkpoint_decision_id
                    if checkpoint_decision_id is not None
                    else None
                    if active_record is None
                    else active_record.decision_id
                ),
            }
            snapshot["origin"] = (
                active_record.origin.value
                if active_record is not None
                else None
                if challenger_authority is not None
                else checkpoint_origin.value
                if checkpoint_origin is not None
                else live["origin"]
            )
            snapshot["policy"] = (
                active_record.policy.value
                if active_record is not None
                else None
                if challenger_authority is not None
                else checkpoint_policy.value
                if checkpoint_policy is not None
                else None
            )
            snapshot["identification"] = {
                "status": ("identified" if self._model_meta is not None else "unidentified"),
            }
            snapshot["identities"] = {
                "active_digest": active.model_digest,
                "active_generation": active.role_generation,
                "rollback_digest": rollback_digest,
                "rollback_generation": rollback_generation,
            }
            live_activation = (
                live["activation_phase"],
                live["pending_persistence"],
                live["pending_swap"],
            )
            activation = checkpoint_activation if live_activation == ("aborted", False, False) else live_activation
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
            elif checkpoint_failure is not None:
                snapshot["failure"] = {
                    "code": checkpoint_failure[0],
                    "detail": checkpoint_failure[1],
                }
            else:
                snapshot["failure"] = None
            snapshot["active_pair"] = active.to_dict()
            snapshot["challenger_authority"] = challenger_authority
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
        next fit starts from scratch, and a counter that restarted at zero
        alongside it could never persist that fit.
        """
        if not isinstance(snapshot, dict):
            return
        revision = snapshot.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            return
        self._model_revision = max(self._model_revision, revision)

    def _restore_challenger_preparation(
        self,
        state: ModelChallengerState,
        incumbent_pair: CandidatePair,
    ) -> CandidatePreparation:
        """Rebuild one exact candidate owner solely from durable challenger lineage."""

        preparation = state.fit_preparation
        fit_result = preparation.get("fit_result")
        timing_value = preparation.get("target_timing")
        if (
            not isinstance(fit_result, Mapping)
            or not isinstance(timing_value, Mapping)
            or preparation.get("fit_corpus_digest") != state.fit_corpus.corpus_digest
        ):
            raise TypeError("durable challenger preparation is incomplete")
        lineage = state.fit_lineage
        request = FitRequest(
            request_id=lineage.request_id,
            origin=state.origin,
            fit_corpus=state.fit_corpus,
            configuration_digest=state.controller_configuration_digest,
            parent_incumbent_digest=lineage.parent_incumbent_digest,
            parent_incumbent_generation=lineage.parent_incumbent_generation,
            candidate_generation=lineage.candidate_generation,
        )
        candidate_owner = self._pair_factory.restore(state.candidate)
        candidate = GreyFitSuccess(
            request=request,
            config=candidate_owner.solver.config,
            rmse_c=fit_result["rmse_c"],
            max_error_c=fit_result["max_error_c"],
            identifiability=fit_result["identifiability"],
            sample_count=fit_result["sample_count"],
            temperature_band_c=tuple(fit_result["temperature_band_c"]),
            nfev=fit_result["nfev"],
            result_digest=fit_result["result_digest"],
        )
        timing = TargetTimingEvidence(
            target=timing_value["target"],
            samples=timing_value["samples"],
            p99_ms=timing_value["p99_ms"],
            limit_ms=timing_value["limit_ms"],
        )
        restored = CandidatePreparation(
            candidate=candidate,
            incumbent_pair=incumbent_pair,
            accepted=True,
            blockers=(),
            candidate_pair=CandidatePair(
                candidate_owner.estimator,
                candidate_owner.solver,
            ),
            dry_solve_finite=True,
            timing=timing,
        )
        if restored.candidate_digest != state.candidate.model_digest:
            candidate_owner.close()
            raise ValueError(
                "durable challenger candidate digest changed "
                f"({restored.candidate_digest} != {state.candidate.model_digest})"
            )
        return restored

    def restore_model(self, snapshot):
        version = snapshot.get("version") if isinstance(snapshot, dict) else None
        if not isinstance(snapshot, dict) or version != self.MODEL_SCHEMA:
            try:
                _snapshot.migrate_grey_learning_snapshot(snapshot)
            except _snapshot.GreySnapshotInvalid:
                pass
            else:
                self._adopt_persisted_revision(snapshot)
            self._logger.warning(
                f"[mpc] discarding a version {version!r} model snapshot: runtime restore "
                f"accepts only grey schema {self.MODEL_SCHEMA}; versions 4 and 5 are migration input only."
            )
            return False
        try:
            owned = _snapshot.migrate_grey_learning_snapshot(snapshot)
        except _snapshot.GreySnapshotInvalid as error:
            self._logger.warning(f"[mpc] discarding an incompatible grey snapshot ({error.reason}).")
            return False
        self._adopt_persisted_revision(snapshot)
        active = owned["active"]
        params = active["parameters"]
        metadata = active["metadata"]
        configured_n_delay = int(self._configuration()["n_delay"])
        snapshot_n_delay = int(params["n_delay"])
        if configured_n_delay != 8 or snapshot_n_delay != 8:
            self._logger.warning("[mpc] discarding an incompatible grey snapshot (incompatible-delay).")
            return False
        if owned["active_pair"] is None:
            self._logger.warning(
                "[mpc] discarding a grey snapshot with no active pair: the record holds "
                "parameters but no model to restore. This cook starts from the configured model."
            )
            return False
        recovery_configuration_digest = self.learning_identity().configuration_digest
        restored_pair: OwnedMpcPair | None = None
        try:
            restored_descriptor = GreyControlPairDescriptor.from_dict(owned["active_pair"])
            active_identity = owned["identities"]
            if (
                active_identity["active_digest"] != restored_descriptor.model_digest
                or active_identity["active_generation"] != restored_descriptor.role_generation
            ):
                raise ValueError("restored active identity does not match its pair descriptor")
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
                raise ValueError("restored active pair does not match active model")
            restored_pair.core.adopt_operating_state(self._active_pair().core.capture_operating_state())
        except Exception as exc:
            if restored_pair is not None:
                restored_pair.close()
            self._logger.warning(
                f"[mpc] a stored model could not be built ({exc}); keeping the model this controller started with."
            )
            return False
        staged_learning = None
        restored_components = None
        restored_identity = None
        if self._learning_enabled or self._trajectory_repository is not None:
            restored_components = CandidatePair(
                restored_pair.estimator,
                restored_pair.solver,
            )
            restored_identity = self._learning_identity_for(
                restored_components,
                restored_descriptor.role_generation,
                configuration=restored_pair.core.config,
            )
            if self._process_owner is None:
                try:
                    staged_learning = self._build_learning(
                        components=restored_components,
                        identity=restored_identity,
                    )
                except BaseException as error:
                    restored_pair.close()
                    self._logger.warning(
                        f"[mpc] restored learning could not start ({error}); keeping "
                        "the model this controller started with."
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
        if self._process_owner is None:
            self._learning = staged_learning
            if old_learning is not None:
                old_learning.close()
        elif old_learning is not None and restored_components is not None and restored_identity is not None:
            binding = self._process_owner.rebind(
                identity=restored_identity,
                config=restored_components.controller.config,
                incumbent_pair=restored_components,
            )
            self._learning = binding.learning
            self._process_lease = binding.lease
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
        self._clear_prepared_checkpoint_lineage()
        self._checkpoint_decision_id = owned["evidence"]["confidence_decision_id"]
        self._checkpoint_activation = restored_activation
        self._checkpoint_failure = restored_failure
        if owned["identification"]["status"] != "identified":
            self._model_meta = None
        self._learning_eligible_updates = owned["evidence"]["eligible"]
        self._learning_rejected_updates = owned["evidence"]["rejected"]
        self._rotate_learning_role_generation(restored_descriptor.role_generation)

        authority = owned["challenger_authority"]
        try:
            durable = read_model_challenger()
        except ValueError:
            durable = None
        authority_matches = (
            durable is not None
            and authority is not None
            and durable.phase != "retired"
            and authority["challenger_id"] == durable.challenger_id
            and authority["revision"] == durable.revision
        )
        if durable is not None and durable.phase != "retired" and not authority_matches:
            with self._learning_lock:
                self._challenger_state = durable
            self._retire_durable_challenger("checkpoint-reference-mismatch")
        elif (
            authority_matches
            and durable is not None
            and restored_components is not None
            and restored_identity is not None
            and self._learning is not None
            and self._trajectory_repository is not None
        ):
            preparation = None
            try:
                live_corpus = self._trajectory_repository.snapshot_fit_corpus(
                    durable.fit_corpus.fit_partition_digest,
                    through_revision=durable.fit_corpus.corpus_revision,
                ).identity
                preparation = self._restore_challenger_preparation(
                    durable,
                    restored_components,
                )
                recovered = recover_model_challenger(
                    incumbent=restored_descriptor,
                    candidate=durable.candidate,
                    controller_configuration_digest=(recovery_configuration_digest),
                    fit_corpus=live_corpus,
                    calibration_manifest=durable.calibration_manifest,
                    recovered_ms=self._clock_ms(),
                )
                if recovered is None:
                    latest = read_model_challenger()
                    if (
                        latest is not None
                        and latest.challenger_id == durable.challenger_id
                        and latest.revision > durable.revision
                        and latest.phase == "retired"
                    ):
                        self._trace_durable_challenger(latest)
                    for component in (
                        preparation.candidate_pair.controller,
                        preparation.candidate_pair.estimator,
                    ):
                        close = getattr(component, "close", None)
                        if callable(close):
                            close()
                else:
                    self._trace_durable_challenger(recovered)
                    self._learning.restore_persisted_challenger(
                        preparation,
                        evaluation_epoch=recovered.evaluation_epoch,
                        consecutive_wins=recovered.consecutive_wins,
                    )
                    with self._learning_lock:
                        self._challenger_state = recovered
                        if self._process_owner is None:
                            self._learning_candidate_pair = preparation.candidate_pair
                    self._adopt_prepared_checkpoint_lineage(preparation)
            except Exception as error:
                if self._learning is not None:
                    self._learning._release_prepared()
                try:
                    latest = read_model_challenger()
                except ValueError:
                    latest = None
                if latest is not None:
                    with self._learning_lock:
                        self._challenger_state = latest
                self._retire_durable_challenger("challenger-reconstruction-failed")
                self._logger.warning(
                    f"[mpc] durable challenger recovery failed ({error}); keeping the restored active model."
                )
        elif authority_matches and durable is not None:
            with self._learning_lock:
                self._challenger_state = durable
            self._retire_durable_challenger("challenger-recovery-prerequisite-missing")
        self.get_model_snapshot()
        return True

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        learning = self._learning
        self._learning = None
        if learning is not None and self._process_owner is None:
            learning.close()
