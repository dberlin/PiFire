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
from common.learning_trajectory import ModelFitLineage
from common.model_evidence import (
    ActivationLifecycleEvidence,
    CandidateAssessmentEvidence,
    ConfidenceDecisionEvidence,
    EvidenceKind,
    FitLifecycleEvidence,
    ForecastOriginEvidence,
    ModelEvidenceRecord,
)
from common.persistence.learning_trajectory import FitCorpusSnapshot
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


class GreyLearningProcessOwner:
    """Process-owned in-memory fit worker and prepared-candidate lifecycle."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._learning: GreyLearningOrchestrator | None = None
        self._identity: LiveLearningIdentity | None = None
        self._lease = 0
        self._terminal_tickets: dict[str, CandidateOrigin] = {}
        self._closed = False

    @property
    def learning(self) -> GreyLearningOrchestrator | None:
        with self._lock:
            return self._learning

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
        with self._lock:
            if self._closed:
                raise RuntimeError("grey learning process owner is closed")
            learning = self._learning
            if learning is None:
                learning = builder()
                self._learning = learning
            else:
                learning.rebind_process(
                    identity,
                    config=config,
                    incumbent_pair=incumbent_pair,
                    estimator_factory=estimator_factory,
                    controller_factory=controller_factory,
                    timing_probe=timing_probe,
                )
            self._identity = identity
            self._lease += 1
            return _ProcessLearningBinding(learning, self._lease)

    def rebind(
        self,
        *,
        identity: LiveLearningIdentity,
        config: GreyBoxMPCConfig,
        incumbent_pair: CandidatePair,
    ) -> _ProcessLearningBinding:
        with self._lock:
            if self._closed or self._learning is None:
                raise RuntimeError("grey learning process owner is not bound")
            self._learning.rebind_process(
                identity,
                config=config,
                incumbent_pair=incumbent_pair,
            )
            self._identity = identity
            self._lease += 1
            return _ProcessLearningBinding(self._learning, self._lease)

    def run(
        self,
        lease: int,
        operation: Callable[
            [GreyLearningOrchestrator, LiveLearningIdentity],
            object,
        ],
    ):
        with self._lock:
            if (
                self._closed
                or self._learning is None
                or self._identity is None
                or lease <= 0
                or lease > self._lease
            ):
                return None
            return operation(self._learning, self._identity)

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
            if self._closed or self._learning is None or lease != self._lease:
                return None
            return self._learning.prepared


    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
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
        self._checkpoint_origin: CandidateOrigin | None = None
        self._checkpoint_policy: ActivationPolicy | None = None
        self._checkpoint_rollback_identity: tuple[str, int] | None = None
        self._checkpoint_decision_id: str | None = None
        self._checkpoint_challenger: dict[str, JsonValue] | None = None
        self._checkpoint_candidate_identity: tuple[str, int] | None = None
        self._checkpoint_candidate_descriptor: GreyControlPairDescriptor | None = None
        self._checkpoint_window: FitWindowIdentity | None = None
        self._checkpoint_preparation: CandidatePreparation | None = None
        self._checkpoint_preparation_key: tuple[FitRequest, str] | None = None
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
            observation.calibration_fit
            or observation.calibration_stage is not None
            or observation.probe_q != 0.0
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
            ) and self._activation_runtime.consume_confidence_persisted(
                evaluation_decision_id
            )
        if result.history.accepted and not operator_frame:
            self.request_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)
        forecasts = tuple(
            self._completed_forecast_evidence(value)
            for value in result.completed_forecasts
        )
        reasons = tuple(result.history.reasons)
        trigger = result.trigger
        return {
            "role_generation": observation.role_generation,
            "eligible": bool(result.history.accepted),
            "rejection_reasons": reasons,
            "input_variance": trigger.input_variance,
            "input_levels": trigger.input_levels,
            "effective_updates": self._learning_eligible_updates,
            "model_digest": grey_config_digest(
                self._active_components().controller.config
            ),
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
            "model_digest": grey_config_digest(
                self._active_components().controller.config
            ),
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
                                prepared.candidate_pair
                                if prepared is not None and prepared.accepted
                                else None
                            )

    def request_corpus_fit(self, origin: CandidateOrigin) -> bool:
        return self._request_corpus_fit_ticket(origin) is not None

    def _request_corpus_fit_ticket(self, origin: CandidateOrigin) -> str | None:
        """Queue an authorized persistent-corpus fit without touching storage."""
        if not isinstance(origin, CandidateOrigin):
            raise TypeError("origin must be a CandidateOrigin")
        if (
            self._closed
            or self._corpus_fit_failure is not None
            or self._trajectory_repository is None
            or self._fit_partition_digest is None
            or (
                origin is CandidateOrigin.PASSIVE_ONLINE
                and not self._learning_enabled
            )
        ):
            return None
        with self._learning_lock:
            for intent in self._corpus_fit_intents:
                if intent.origin is origin:
                    return intent.ticket
            intent = _CorpusFitIntent(secrets.token_hex(32), origin)
            self._corpus_fit_intents.append(intent)
            self._learning_pending_origin = origin
            return intent.ticket

    def _fail_corpus_learning(self, code: str, error: BaseException | str) -> None:
        detail = error if isinstance(error, str) else f"{type(error).__name__}: {error}"
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
                self._checkpoint_challenger = None
                self._checkpoint_candidate_identity = None
                self._checkpoint_candidate_descriptor = None
                self._checkpoint_window = None
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
                model_digest=request.window.incumbent_digest,
            )
            self._persist_rejected_candidate(
                request,
                model_digest=request.window.incumbent_digest,
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
        identity: LiveLearningIdentity,
    ) -> None:
        repository.record_fit_request(
            snapshot,
            ModelFitLineage(
                request_id=request.request_id,
                parent_incumbent_digest=identity.incumbent_digest,
                parent_incumbent_generation=identity.role_generation,
                candidate_generation=identity.candidate_generation,
                fit_corpus=snapshot.identity,
                fit_corpus_digest=snapshot.identity.corpus_digest,
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
            if (
                not self._corpus_fit_intents
                or learning.pending_request is not None
            ):
                return None
            intent = self._corpus_fit_intents[0]
            origin = intent.origin
        prepared_to_replace: CandidatePreparation | None = None
        terminal_rejection_reason: str | None = None
        prepared = learning.prepared
        if prepared is not None and prepared.accepted:
            prepared_is_owned_operator = (
                prepared.candidate.request.origin
                is CandidateOrigin.OPERATOR_CALIBRATION
                and learning._can_supersede_prepared_candidate(prepared)
            )
            if intent.origin is not CandidateOrigin.COOK_REFIT:
                terminal_rejection_reason = "superseded-by-prepared-candidate"
            elif prepared_is_owned_operator:
                terminal_rejection_reason = (
                    "superseded-by-prepared-operator-calibration-candidate"
                )
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

        if origin is CandidateOrigin.PASSIVE_ONLINE:
            trigger = persistent_corpus_trigger(
                snapshot,
                config=learning.trigger_config,
            )
            if not trigger.ready:
                with self._learning_lock:
                    if (
                        self._corpus_fit_intents
                        and self._corpus_fit_intents[0] is intent
                    ):
                        self._corpus_fit_intents.popleft()
                    if self._learning_pending_origin is origin:
                        self._learning_pending_origin = None
                self._record_terminal_fit_intent(intent)
                return None
        identity = self.learning_identity() if identity is None else identity
        scored_sequences = tuple(
            frame.sequence
            for segment in snapshot.segments
            for frame in segment.scored_hold_frames
        )
        if not scored_sequences:
            self._fail_corpus_learning(
                "corpus-snapshot-failed",
                "persistent corpus contains no scored observations",
            )
            return None
        window = FitWindowIdentity(
            session_id=identity.session_id,
            cook_id=identity.cook_id,
            first_observation_sequence=min(scored_sequences),
            last_observation_sequence=max(scored_sequences),
            configuration_digest=identity.configuration_digest,
            incumbent_digest=identity.incumbent_digest,
            role_generation=identity.role_generation,
        )
        request = FitRequest(
            request_id=intent.ticket,
            origin=origin,
            window=window,
            candidate_generation=identity.candidate_generation,
        )
        if terminal_rejection_reason is not None:
            try:
                self._record_corpus_fit_request(
                    repository,
                    snapshot,
                    request,
                    identity,
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
                identity,
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
                self._clear_superseded_prepared_candidate(
                    prepared_to_replace,
                )
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
        return True

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

    def _adopt_prepared_checkpoint_lineage(
        self,
        preparation: CandidatePreparation,
    ) -> None:
        """Bind one accepted fit's checkpoint lineage and advance it once."""

        with self._learning_lock:
            request = preparation.candidate.request
            descriptor = self._prepared_candidate_descriptor(preparation)
            identity = (descriptor.model_digest, descriptor.candidate_generation)
            preparation_key = (request, descriptor.model_digest)
            candidate_config = preparation.candidate.config
            candidate_parameters = {
                key: (
                    candidate_config.delay_states
                    if key == "n_delay"
                    else getattr(candidate_config, key)
                )
                for key in self.MODEL_PARAM_KEYS
            }
            if preparation_key != self._checkpoint_preparation_key:
                self._model_revision += 1
            self._checkpoint_preparation = preparation
            self._checkpoint_preparation_key = preparation_key
            self._checkpoint_candidate_identity = identity
            self._checkpoint_origin = request.origin
            self._checkpoint_policy = self._policy_for_learning_origin(
                request.origin
            )
            self._checkpoint_challenger = {
                "parameters": _snapshot.normalize_grey_parameters(
                    candidate_parameters
                ),
                "metadata": {
                    "rmse": preparation.candidate.rmse_c,
                    "samples": preparation.candidate.sample_count,
                    "band_c": list(preparation.candidate.temperature_band_c),
                    "nfev": preparation.candidate.nfev,
                },
            }
            self._checkpoint_window = request.window
            self._checkpoint_candidate_descriptor = descriptor

    def _clear_prepared_checkpoint_lineage(self) -> None:
        with self._learning_lock:
            self._checkpoint_preparation = None
            self._checkpoint_preparation_key = None

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
        self._adopt_prepared_checkpoint_lineage(preparation)
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

    def _poll_learning_off_path_locked(
        self,
        *,
        live_origin=None,
        authoritative_learning: GreyLearningOrchestrator | None = None,
        authoritative_identity: LiveLearningIdentity | None = None,
    ):
        """Run one lifecycle poll while identity mutation is fenced."""
        with self._learning_lock:
            learning = (
                self._learning
                if authoritative_learning is None
                else authoritative_learning
            )
            if learning is None:
                return None, None
            queued = self._learning_pending_fit_transition
            self._learning_pending_fit_transition = None
            origin = self._learning_pending_origin if live_origin is None else live_origin
            if origin is not None:
                origin = origin if isinstance(origin, CandidateOrigin) else CandidateOrigin(origin)

        if self._submit_requested_corpus_fit(
            learning,
            identity=authoritative_identity,
        ) is not None:
            return None, None
        with self._learning_lock:
            self._learning_preparing = origin is not None


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
                    live_identity=(
                        self.learning_identity()
                        if authoritative_identity is None
                        else authoritative_identity
                    ),
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
                if (
                    self._process_owner is None
                    and prepared is not None
                    and prepared.accepted
                ):
                    self._learning_candidate_pair = prepared.candidate_pair
                    self._adopt_prepared_checkpoint_lineage(prepared)

        if delivery is not None:
            if not self._complete_corpus_fit(delivery):
                return delivery, None
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
                if (
                    "candidate-supersession-persistence-failed"
                    in stale_reasons
                ):
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
        elif self._corpus_fit_failure is not None:
            status = LearningStatus.ERROR
            fit_status = FitStatus.FAILED
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
        with self._learning_lock:
            self._checkpoint_preparation = None
            self._checkpoint_preparation_key = None
            self._checkpoint_origin = origin
            self._checkpoint_policy = policy
            self._checkpoint_rollback_identity = (
                previous.descriptor.model_digest,
                previous.descriptor.role_generation,
            )
            self._checkpoint_challenger = None
            self._checkpoint_candidate_identity = None
            self._checkpoint_candidate_descriptor = None
            self._checkpoint_window = None
            self._checkpoint_decision_id = None
            self._checkpoint_activation = ("aborted", False, False)
            self._checkpoint_failure = None
            self._model_meta = adopted_metadata
            self._model_revision = max(
                self._model_revision + 1,
                pair.descriptor.role_generation,
            )
            self._rotate_learning_role_generation(self._model_revision)

    def get_model_snapshot(self):
        """Return the complete grey-only v4 checkpoint; process jobs stay live-only."""
        metadata = (
            {"rmse": None, "samples": 0, "band_c": [0.0, 0.0], "nfev": None}
            if self._model_meta is None
            else self._model_meta
        )
        with self._learning_lock:
            checkpoint_preparation = self._checkpoint_preparation
            model_revision = self._model_revision
        try:
            snapshot = _snapshot.new_grey_learning_snapshot(
                revision=int(model_revision),
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
            prepared = checkpoint_preparation
            candidate_descriptor = self._checkpoint_candidate_descriptor
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
            elif candidate is None and self._checkpoint_challenger is not None:
                snapshot["challenger"] = copy.deepcopy(self._checkpoint_challenger)
                snapshot["window"] = (
                    None
                    if self._checkpoint_window is None
                    else asdict(self._checkpoint_window)
                )
            snapshot["evidence"] = {
                "eligible": int(self._learning_eligible_updates),
                "rejected": int(self._learning_rejected_updates),
                "confidence_decision_id": (
                    self._checkpoint_decision_id
                    if self._checkpoint_decision_id is not None
                    else None
                    if active_record is None
                    else active_record.decision_id
                ),
            }
            prepared_request = (
                None
                if candidate is not None or prepared is None
                else prepared.candidate.request
            )
            checkpoint_origin = (
                prepared_request.origin
                if prepared_request is not None
                else self._checkpoint_origin
            )
            snapshot["origin"] = checkpoint_origin.value if checkpoint_origin is not None else live["origin"]
            snapshot["policy"] = (
                self._policy_for_learning_origin(prepared_request.origin).value
                if prepared_request is not None
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
            refit_status, refit_latest = self._checkpoint_cook_refit
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
        version = snapshot.get("version") if isinstance(snapshot, dict) else None
        if not isinstance(snapshot, dict) or version != self.MODEL_SCHEMA:
            if version == 3:
                try:
                    _snapshot.migrate_grey_learning_snapshot(snapshot)
                except _snapshot.GreySnapshotInvalid:
                    pass
                else:
                    self._adopt_persisted_revision(snapshot)
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
        self._adopt_persisted_revision(snapshot)
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
        if self._process_owner is None:
            self._learning = staged_learning
            if old_learning is not None:
                old_learning.close()
        elif (
            old_learning is not None
            and restored_components is not None
            and restored_identity is not None
        ):
            binding = self._process_owner.rebind(
                identity=restored_identity,
                config=restored_components.controller.config,
                incumbent_pair=restored_components,
            )
            self._learning = binding.learning
            self._process_lease = binding.lease
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
        self._clear_prepared_checkpoint_lineage()
        self._checkpoint_candidate_descriptor = restored_candidate_descriptor
        self._checkpoint_window = restored_window
        self._checkpoint_candidate_identity = restored_candidate_identity
        self._checkpoint_decision_id = owned["evidence"]["confidence_decision_id"]
        self._checkpoint_cook_refit = restored_cook_refit
        self._checkpoint_activation = restored_activation
        self._checkpoint_failure = restored_failure
        if owned["identification"]["status"] != "identified":
            self._model_meta = None
        self._learning_eligible_updates = owned["evidence"]["eligible"]
        self._learning_rejected_updates = owned["evidence"]["rejected"]
        self._rotate_learning_role_generation(restored_descriptor.role_generation)
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
