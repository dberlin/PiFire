#!/usr/bin/env python3

"""Grey-box MPC control through the repository-published acados runtime.

The active controller owns one EKF/KF estimator and one native solver.  The
configured control period is estimator/runner cadence; the prediction model is
the fixed 25-second, eight-delay generated map.
"""

from __future__ import annotations


import collections
import copy
import hashlib
import json
import logging
import math
from dataclasses import asdict, dataclass, replace
import time
import threading
from typing import Literal

import numpy as np

from common.control_trace import (
    AmbientSource,
    ControlTraceRecord,
    ControllerType,
    GreyActivationLifecyclePayload,
    GreyCandidateAssessmentPayload,
    GreyFitLifecyclePayload,
    GreyLearningFailurePayload,
    TraceEventKind,
    CompletedOriginPayload,
    HorizonScorePayload,
    ModelEvaluationPayload,
)
from controller.base import ControllerBase, MpcTraceDiagnostics
from controller.applied_output import FrameFeedbackDisposition
from controller.model_promotion import Verdict as _Verdict
from controller.mpc_model import MODEL_SCHEMA
from controller import mpc_snapshot as _snapshot
from controller.mpc_allocator import AllocationResult
from controller.mpc_config import (
    DEFAULT_MPC_CONFIG,
    finite_float,
    normalize_config,
    optional_float,
    sanitized_copy,
    warn_about_model,
)
from controller.mpc_factory import MpcPairFactory, OwnedMpcPair
from common.controller_model_state import MAX_SNAPSHOT_BYTES
from common.model_evidence import (
    EvidenceKind,
    ActivationLifecycleEvidence,
    CandidateAssessmentEvidence,
    ConfidenceDecisionEvidence,
    FallbackEvidence,
    ForecastOriginEvidence,
    FitLifecycleEvidence,
    LearningFailureEvidence,
    ModelEvidenceRecord,
    RollbackEvidence,
)
from controller.acados import GreyBoxMPCConfig, SolverDiagnostics
from controller.runtime.model_fitting import (
    CandidatePair,
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
from common.web_contracts.learning import ModelActivationRequest
from controller.model_learning.contracts import (
    ActivationPolicy,
    CandidateOrigin,
    FitRequest,
    FitStatus,
    FitWindowIdentity,
    FrameObservation,
    LearningStatus,
)
from controller.model_learning.activation import (
    ActivationManager,
    ActivationPhase,
    GreyControlPairDescriptor,
    PreparedActivationRecord,
    recover_startup_activation,
)
from controller.grey_box import GreyBoxPredictionAdapter
from controller.model_learning.calibration import (
    CalibrationCommand as _CoordinatorCalibrationCommand,
    CalibrationCoordinator,
    CalibrationDecision,
    CalibrationProgress,
    CalibrationRuntimeContext,
)


# One row per control period. At the 5 s default that is ~12 hours, which is
# longer than any single cook; a longer one loses its beginning rather than
# its end, and the end is what describes the grill's current state. This is

# also what bounds a refit: the longest cook the fit can ever be handed off
# the teardown path is one full history.
_HISTORY_MAX = 8640

# Below this a record is an interrupted cook rather than a description of a
# grill, and fitting it would produce a confident answer from nothing. There
# is deliberately no upper limit to match: a refit fits the whole cook.
#
# Thinning the rows first looks like it should be cheaper and is not.
# mpc_model.simulate_grey_box sub-steps every interval to max_dt, so one
# residual evaluation costs (cook duration / max_dt) integration steps no
# matter how many rows were sampled from it -- the fit's price is set by how
# long the cook was and how many iterations the solver takes, and row count
# enters neither. Dropping rows therefore discards evidence for nothing, and
# the worse-conditioned problem left behind takes MORE iterations, so it is
# not even faster in practice. It costs an order of magnitude of accuracy in
# the chamber parameters, and the braking distance model_promotion sizes the
# horizon from is read off the same fit.
_REFIT_MIN_SAMPLES = 120

# Parameters the least-squares solve starts from, and the magnitudes it scales
# by. A fixed reference, so that a cook's fit describes the grill and nothing
# else: it cannot inherit whatever the previous fit happened to land on, and
# two identical cooks a season apart give the same answer. Seeding it from the
# running model instead would make each result a function of every result
# before it, through a solver path -- which basin the solve lands in depends on
# where it starts -- that no measurement of the finished model can unwind.
#
# Every parameter the fit reads a starting value for -- the ones update_mpc's
# `_FREE` moves and the ones it holds at the value they came in with. T_amb,
# sigma and n_delay are not here: the fit passes them through unchanged, so
# they come from the running config where an operator's own calibration of them
# lives.
_REFIT_INIT = {key: float(DEFAULT_MPC_CONFIG[key]) for key in ("C_c", "h_amb", "K_Q", "theta")}



@dataclass(frozen=True, slots=True)
class CalibrationCommand:
    """One revisioned operator calibration request at the runtime boundary."""

    action: str
    command_revision: int
    ambient_c: float
    ambient_source: str
    empty_grill_confirmed: bool
    pellets_confirmed: bool
    seed: int = 0

    def __post_init__(self) -> None:
        if self.action not in {"start", "pause", "resume", "stop", "reset-progress"}:
            raise ValueError("invalid calibration action")
        if (
            isinstance(self.command_revision, bool)
            or not isinstance(self.command_revision, int)
            or self.command_revision < 1
        ):
            raise ValueError("calibration command revision must be positive")
        if not all(
            isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            for value in (self.ambient_c,)
        ):
            raise ValueError("calibration temperatures must be finite")
        if self.ambient_source not in {"measured", "manual", "weather", "configured"}:
            raise ValueError("invalid calibration ambient source")
        if self.empty_grill_confirmed is not True or self.pellets_confirmed is not True:
            raise ValueError("calibration confirmations are required")


class Controller(ControllerBase):
    def __init__(self, config, units, cycle_data):
        super().__init__(config, units, cycle_data)

        self._activation_configuration = {
            "controller": "mpc",
            "config": copy.deepcopy(config or {}),
            "cycle_data": copy.deepcopy(cycle_data),
            "units": units,
        }
        cfg = normalize_config(config)
        self.cfg = cfg
        self.u_max = float(cycle_data.get("u_max", 0.9))
        self._learning_enabled = cfg.get("enable_online_adaptation") is True
        self._learning_eligible_updates = 0
        self._learning_rejected_updates = 0
        self._model_revision = 0
        self._model_meta = None  # provenance of an adopted model, or None
        self._active_activation_record: PreparedActivationRecord | None = None
        self._trace_diagnostics = None
        self._calibration = CalibrationCoordinator(predict_max_c=self._predict_calibration_max)
        self._calibration_operations: collections.deque[tuple[str, object]] = collections.deque()
        self._calibration_last_revision = 0
        self._calibration_ambient_c = float(cfg["T_amb"])
        self._calibration_safety_ceiling_c = 0.0
        self._calibration_frame_results: dict[int, tuple[float, CalibrationDecision]] = {}
        self._calibration_feedback: collections.deque[
            tuple[float, float, bool, bool, FrameFeedbackDisposition, int, int, str, int]
        ] = collections.deque()
        self._calibration_generation = 0
        self._calibration_last_feedback_timestamp: float | None = None
        self._trace_calibration = CalibrationDecision(False, 0.0, None, CalibrationProgress())
        self._trace_baseline_allocation: AllocationResult | None = None
        self._trace_allocation: AllocationResult | None = None
        self._activation_events: collections.deque[ModelEvidenceRecord] = collections.deque()
        self._pair_factory = MpcPairFactory(
            cfg,
            units,
            cycle_data,
            adjust_load=self._adjust_core_load,
            model_authority=self._core_model_authority,
            on_policy_failure=self._handle_core_policy_failure,
        )
        self._active_control_pair = self._pair_factory.build(
            self._pair_factory.configured(
                cfg,
                candidate_generation=self._model_revision,
                role_generation=self._model_revision,
            ),
            authorized=True,
        )
        self._core = self._active_control_pair.core
        self.cfg = self._core.config
        self.u_max = self._core.u_max
        warn_about_model(self.cfg)
        self._rollback_control_pair: OwnedMpcPair | None = None
        self._inert_activation: PreparedActivationRecord | None = None
        self._activation_terminated_reason: str | None = None
        self._failed_role_generations: set[int] = set()
        self._activation_persistence_worker = None
        self._activation_persistence_lock = threading.Lock()
        self._persisted_activation_confidence_ids: set[str] = set()
        self._prepared_pair_transitions = collections.deque()
        self._closed = False
        self._learning_lock = threading.RLock()
        self._learning_evaluation_lock = threading.Lock()
        self._learning_preparing = False
        self._learning_lifecycle_lock = threading.Lock()
        self._learning_pending_origin: CandidateOrigin | None = None
        self._learning_candidate_pair: CandidatePair | None = None
        self._learning_pending_evaluation = None
        self._learning_pending_confidence_accepted: bool | None = None
        self._learning_session_id = "runtime"
        self._learning_cook_id = None
        self._learning_role_generation = self._model_revision
        self._teardown_history = TeardownGreyHistory(
            role_generation=self._learning_role_generation,
            max_observations=_HISTORY_MAX,
        )
        self._cook_refit_outcome: TeardownRefitOutcome | None = None
        self._cook_refit_finalized = False
        self._teardown_candidate = None
        self._teardown_candidate_descriptor: GreyControlPairDescriptor | None = None
        self._teardown_fit_window: FitWindowIdentity | None = None
        self._checkpoint_origin: CandidateOrigin | None = None
        self._checkpoint_policy: ActivationPolicy | None = None
        self._checkpoint_rollback_identity: tuple[str, int] | None = None
        self._teardown_decision_id: str | None = None
        try:
            self._learning = self._build_learning() if self._learning_enabled else None
        except BaseException:
            self._core.close()
            raise



    @property
    def estimator(self):
        return self._core.estimator

    @property
    def mpc(self):
        return self._core.solver

    @property
    def _set_point_c(self):
        return self._core.set_point_c

    @property
    def _last_combustion_load(self):
        return self._core.last_combustion_load

    @property
    def _applied_combustion_load(self):
        return self._core.applied_combustion_load

    @property
    def _x_hat(self):
        return self._core.estimate

    @property
    def _last_raw_combustion_load(self):
        return self._core.last_raw_combustion_load

    @property
    def _last_equilibrium_load(self):
        return self._core.last_equilibrium_load

    @property
    def _last_residual_load(self):
        return self._core.last_residual_load

    @property
    def _last_feasibility(self):
        return self._core.last_feasibility

    @property
    def _consecutive_policy_failures(self):
        return self._core.consecutive_policy_failures

    @property
    def _history(self):
        return self._core.history

    @property
    def _native_failure_diagnostics(self):
        return self._core.native_failure_diagnostics
    def _core_model_authority(self):
        return self._model_revision, self._model_meta

    def _adjust_core_load(self, baseline_load, temperature_c):
        calibration = self._advance_calibration(baseline_load, temperature_c)
        return baseline_load + calibration.probe_q

    def _handle_core_policy_failure(self, _error):
        if isinstance(self._active_activation_record, PreparedActivationRecord):
            if not self._restore_activation_rollback(
                "native-solve-failure",
                emit_fallback=True,
            ):
                self.terminate_mpc_activation("native-failure-compensation-failed")


    def _learning_identity(self):
        document = {
            "config": self.cfg,
            "cycle_data": self.cycle_data,
            "units": self.units,
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
            incumbent_digest=grey_config_digest(self.mpc.config),
            role_generation=self._learning_role_generation,
            candidate_generation=self._learning_role_generation + 1,
        )

    def _build_learning(self):
        learning = GreyLearningOrchestrator(
            identity=self._learning_identity(),
            config=self.mpc.config,
            incumbent_pair=CandidatePair(self.estimator, self.mpc),
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

    def set_target(self, set_point):
        self.set_point = set_point
        self._core.set_target(set_point)

    def get_control_period(self):
        return float(self.cfg["control_period"])

    def commands_fan(self):
        return bool(self.cfg.get("enable_fan_input", False))

    def wants_async(self):
        return True

    @staticmethod
    def _normalized_forecast_failure(error):
        """Normalize model-library finite-value errors to the lifecycle reason."""
        if isinstance(error, (ValueError, FloatingPointError, RuntimeError)) and "finite" in str(error).lower():
            return ValueError("non-finite-forecast")
        return error

    @property
    def active_control_pair(self) -> OwnedMpcPair:
        return self._active_control_pair

    @property
    def rollback_control_pair(self) -> OwnedMpcPair | None:
        return self._rollback_control_pair

    @property
    def activation_output_authorized(self) -> bool:
        return self._active_control_pair.authorized

    @property
    def failed_role_generations(self) -> frozenset[int]:
        return frozenset(self._failed_role_generations)

    def install_candidate_pair_inert(
        self,
        pair: OwnedMpcPair,
        record: PreparedActivationRecord,
    ) -> bool:
        """Install both handles while making every solve/output path illegal."""
        if (
            not isinstance(pair, OwnedMpcPair)
            or not isinstance(record, PreparedActivationRecord)
            or record.phase is not ActivationPhase.PREPARED
            or pair.descriptor != record.candidate
            or self._active_control_pair.descriptor != record.incumbent
        ):
            return False
        if self._inert_activation is not None:
            return self._inert_activation.transaction_id == record.transaction_id
        pair.revoke_output()
        self._rollback_control_pair = self._active_control_pair
        self._active_control_pair = pair
        self._core = pair.core
        self._inert_activation = record
        return True

    def authorize_candidate_pair(self, record: PreparedActivationRecord) -> bool:
        """Authorize the installed pair only for its exact durable active record."""
        prepared = self._inert_activation
        if (
            prepared is None
            or not isinstance(record, PreparedActivationRecord)
            or record.phase is not ActivationPhase.ACTIVE
            or record.transaction_id != prepared.transaction_id
            or record.candidate != self._active_control_pair.descriptor
        ):
            return False
        self._active_control_pair.authorize_output()
        self._rotate_teardown_role_generation(record.candidate.role_generation)
        self._active_activation_record = record
        self._inert_activation = None
        self._model_revision = max(self._model_revision, record.candidate.role_generation)
        timestamp_ms = time.time_ns() // 1_000_000
        lifecycle = ActivationLifecycleEvidence(
            decision_id=record.decision_id,
            phase="active",
            origin=record.origin.value,
            policy=record.policy.value,
        )
        self._persist_grey_lifecycle(
            lifecycle,
            GreyActivationLifecyclePayload(
                decision_id=lifecycle.decision_id,
                phase=lifecycle.phase,
                origin=lifecycle.origin,
                policy=lifecycle.policy,
            ),
            timestamp_ms=timestamp_ms,
            role_generation=record.candidate.role_generation,
            model_digest=record.candidate.model_digest,
            provenance_digest=record.incumbent.model_digest,
        )
        return True

    def compensate_candidate_pair(
        self,
        pair: OwnedMpcPair,
        record: PreparedActivationRecord,
        _reason: str,
    ) -> bool:
        """Restore the exact retained incumbent after an uncommitted install."""
        rollback = self._rollback_control_pair
        if (
            rollback is None
            or pair is not self._active_control_pair
            or pair.descriptor != record.candidate
            or rollback.descriptor != record.incumbent
        ):
            return False
        rollback.authorize_output()
        self._active_control_pair = rollback
        self._core = rollback.core
        self._rollback_control_pair = None
        self._inert_activation = None
        self._active_activation_record = None
        self._model_revision = max(self._model_revision, record.candidate.role_generation + 1)
        self._rotate_teardown_role_generation(self._model_revision)
        timestamp_ms = time.time_ns() // 1_000_000
        lifecycle = ActivationLifecycleEvidence(
            decision_id=record.decision_id,
            phase="aborted",
            origin=record.origin.value,
            policy=record.policy.value,
            reason=_reason,
        )
        self._persist_grey_lifecycle(
            lifecycle,
            GreyActivationLifecyclePayload(
                decision_id=lifecycle.decision_id,
                phase=lifecycle.phase,
                origin=lifecycle.origin,
                policy=lifecycle.policy,
                reason=lifecycle.reason,
            ),
            timestamp_ms=timestamp_ms,
            role_generation=record.candidate.role_generation,
            model_digest=record.candidate.model_digest,
            provenance_digest=record.incumbent.model_digest,
        )
        try:
            pair.close()
        except Exception:
            return False
        return True

    def terminate_mpc_activation(self, reason: str) -> None:
        self._active_control_pair.revoke_output()
        self._activation_terminated_reason = reason

    def _restore_activation_rollback(self, reason: str, *, emit_fallback: bool) -> bool:
        rollback = self._rollback_control_pair
        if rollback is None or not self._active_control_pair.authorized:
            return False
        failed = self._active_control_pair
        activation_record = self._active_activation_record
        rollback.authorize_output()
        self._active_control_pair = rollback
        self._core = rollback.core
        self._rollback_control_pair = None
        self._failed_role_generations.add(failed.descriptor.role_generation)
        self._model_revision = max(self._model_revision, failed.descriptor.role_generation + 1)
        self._rotate_teardown_role_generation(self._model_revision)
        try:
            failed.close()
        except Exception:
            self.terminate_mpc_activation("rollback-close-failed")
            return False
        if emit_fallback:
            timestamp_ms = time.time_ns() // 1_000_000
            self._activation_events.append(
                ModelEvidenceRecord(
                    evidence_id=(
                        f"fallback:{failed.descriptor.role_generation}:{timestamp_ms}:{failed.descriptor.model_digest}"
                    ),
                    kind=EvidenceKind.FALLBACK,
                    session_id="mpc-runtime-activation",
                    cook_id=None,
                    timestamp_ms=timestamp_ms,
                    role_generation=self._model_revision,
                    model_digest=failed.descriptor.model_digest,
                    provenance_digest=rollback.descriptor.model_digest,
                    payload=FallbackEvidence(
                        decision_id=(
                            activation_record.decision_id
                            if isinstance(activation_record, PreparedActivationRecord)
                            else "runtime-confidence-window"
                        ),
                        reason=reason,
                        failed_digest=failed.descriptor.model_digest,
                        failed_generation=failed.descriptor.role_generation,
                        last_safe_command=self._last_combustion_load,
                        fallback_kind="grey-box",
                    ),
                )
            )
            failure = LearningFailureEvidence(
                code="activation-terminal",
                detail=reason,
                terminal=True,
            )
            self._persist_grey_lifecycle(
                failure,
                GreyLearningFailurePayload(
                    code=failure.code,
                    detail=failure.detail,
                    terminal=failure.terminal,
                ),
                timestamp_ms=timestamp_ms,
                role_generation=failed.descriptor.role_generation,
                model_digest=failed.descriptor.model_digest,
                provenance_digest=rollback.descriptor.model_digest,
            )
        self._active_activation_record = None
        self._inert_activation = None
        return True

    def _activation_persistence_channel(self):
        from common.controller_model_state import ControllerModelStore
        from controller.runtime.model_persistence import ModelPersistenceWorker

        with self._activation_persistence_lock:
            worker = getattr(self, "_activation_persistence_worker", None)
            if worker is None:
                worker = ModelPersistenceWorker(
                    ControllerModelStore(),
                    logging.getLogger("control"),
                )
                self._activation_persistence_worker = worker
            return worker

    def submit_activation_confidence(self, record: ModelEvidenceRecord):
        """Queue confidence on the same FIFO that owns activation phases."""
        if not isinstance(record, ModelEvidenceRecord) or record.kind is not EvidenceKind.CONFIDENCE_DECISION:
            raise TypeError("activation confidence must be confidence-decision evidence")
        return self._activation_persistence_channel().submit_activation_confidence(record)


    def restore_activation(self, persisted, records):
        """Converge startup before authorizing the pair selected by durable authority."""
        records = tuple(records)
        worker = self._activation_persistence_channel()
        restored: OwnedMpcPair | None = None
        rollback: OwnedMpcPair | None = None
        try:
            recovery = recover_startup_activation(
                persisted,
                persist_aborted=lambda record: worker.submit_activation_phase(
                    record,
                    expected_phase=ActivationPhase.PREPARED,
                ),
                receipt_timeout=2.0,
            )
            lifecycle = max(
                (
                    record
                    for record in records
                    if (
                        isinstance(record.payload, RollbackEvidence)
                        and record.payload.decision_id == recovery.record.decision_id
                        and record.model_digest == recovery.record.candidate.model_digest
                    )
                    or (
                        isinstance(record.payload, FallbackEvidence)
                        and record.payload.failed_digest == recovery.record.candidate.model_digest
                        and record.payload.failed_generation == recovery.record.candidate.role_generation
                    )
                ),
                key=lambda record: (record.timestamp_ms, record.evidence_id),
                default=None,
            )
            restore_descriptor = recovery.rollback if lifecycle is not None else recovery.restore
            restored = self._pair_factory.restore(restore_descriptor)
            rollback = (
                self._pair_factory.restore(recovery.rollback)
                if recovery.phase is ActivationPhase.ACTIVE and lifecycle is None
                else None
            )
        except Exception:
            if restored is not None:
                restored.close()
            return False
        if restored is None:
            return False
        retired_active = self._active_control_pair
        retired_rollback = self._rollback_control_pair
        restored.authorize_output()
        self._active_control_pair = restored
        self._rollback_control_pair = rollback
        self._core = restored.core
        self._inert_activation = None
        self._active_activation_record = (
            recovery.record if recovery.phase is ActivationPhase.ACTIVE and lifecycle is None else None
        )
        restored_generation = recovery.restore.role_generation
        if lifecycle is not None:
            restored_generation = lifecycle.role_generation
            self._failed_role_generations.add(recovery.record.candidate.role_generation)
        self._model_revision = restored_generation
        self._rotate_teardown_role_generation(self._model_revision)
        retired_active.close()
        if retired_rollback is not None:
            retired_rollback.close()
        return True

    def activation_runtime_failure(self, reason):
        """Restore the exact rollback pair and persist a fenced failure off-path."""
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("activation fallback reason must be non-blank")
        return self._restore_activation_rollback(reason.strip(), emit_fallback=True)

    def rollback_activation(self, reason):
        """Restore only the retained owner named by the durable activation."""
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("activation rollback reason must be non-blank")
        return self._restore_activation_rollback(reason.strip(), emit_fallback=False)

    def drain_activation_events(self):
        events = tuple(self._activation_events)
        self._activation_events.clear()
        return events

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

    @staticmethod
    def _grey_evaluation_payload(decision, *, evaluation_duration_ms):
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
        evaluated_at_s = max(origin.completion_time_s for origin in completed) if completed else time.monotonic()
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
        incumbent = GreyBoxPredictionAdapter.from_controller(self)
        candidate_config = dict(self.cfg)
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
            if learning is self._learning and request is not None:
                self._learning_pending_origin = request.origin
            evaluation = self._learning_pending_evaluation
            self._learning_pending_evaluation = None
            confidence_accepted = self._learning_pending_confidence_accepted
            self._learning_pending_confidence_accepted = None
            evaluation_decision_id = getattr(evaluation, "decision_id", None)
            confidence_already_persisted = (
                isinstance(evaluation_decision_id, str)
                and evaluation_decision_id in self._persisted_activation_confidence_ids
            )
            if confidence_already_persisted:
                self._persisted_activation_confidence_ids.discard(evaluation_decision_id)
        if request is not None:
            self._persist_fit_transition(
                request,
                status="queued",
                model_digest=request.window.incumbent_digest,
            )
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
            "model_digest": grey_config_digest(self.mpc.config),
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
            "model_digest": grey_config_digest(self.mpc.config),
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
        """Fence Task 7 work to the runner's current cook/configuration identity."""
        with self._learning_lifecycle_lock:
            self._learning_session_id = session_id
            self._learning_cook_id = cook_id
            self._rotate_teardown_role_generation(role_generation)
            with self._learning_lock:
                learning = self._learning
            if learning is not None:
                with self._learning_evaluation_lock:
                    learning.update_identity(
                        self._learning_identity(),
                        config=self.mpc.config,
                        incumbent_pair=CandidatePair(self.estimator, self.mpc),
                    )
                with self._learning_lock:
                    if learning is self._learning:
                        self._learning_pending_origin = None
                        self._learning_candidate_pair = None

    def poll_learning_off_path(self, *, live_origin=None):
        """Drain and prepare fits only from the runner's lifecycle dispatcher."""
        with self._learning_lifecycle_lock:
            return self._poll_learning_off_path_locked(live_origin=live_origin)

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
        session_id = getattr(self, "_learning_session_id", None) or "mpc-learning"
        cook_id = getattr(self, "_learning_cook_id", None)
        evidence = ModelEvidenceRecord(
            evidence_id=(f"{session_id}:{evidence_payload.payload_type}:{timestamp_ms}:{role_generation}"),
            kind=EvidenceKind(evidence_payload.payload_type),
            session_id=session_id,
            cook_id=cook_id,
            timestamp_ms=timestamp_ms,
            role_generation=role_generation,
            model_digest=model_digest,
            provenance_digest=provenance_digest,
            payload=evidence_payload,
        )
        worker = self._activation_persistence_channel()
        submission = worker.submit_evidence(evidence)
        if not submission.accepted:
            raise RuntimeError("learning-lifecycle-evidence-not-accepted")
        try:
            from common.persistence.control_trace import append_control_trace

            event_kind = {
                "fit_lifecycle": TraceEventKind.FIT_LIFECYCLE,
                "candidate_assessment": TraceEventKind.CANDIDATE_ASSESSMENT,
                "activation_lifecycle": TraceEventKind.ACTIVATION_LIFECYCLE,
                "learning_failure": TraceEventKind.LEARNING_FAILURE,
            }[trace_payload.payload_type]
            append_control_trace(
                (
                    ControlTraceRecord(
                        ts_ms=timestamp_ms,
                        session_id=session_id,
                        cook_id=cook_id,
                        controller=ControllerType.MPC,
                        event_kind=event_kind,
                        payload=trace_payload,
                    ),
                )
            )
        except Exception as error:
            self._activation_terminated_reason = f"learning lifecycle trace failed: {error}"
        return evidence

    @staticmethod
    def _policy_for_learning_origin(origin):
        return {
            CandidateOrigin.PASSIVE_ONLINE: ActivationPolicy.PASSIVE_AUTO,
            CandidateOrigin.OPERATOR_CALIBRATION: ActivationPolicy.OPERATOR_REVIEWED,
            CandidateOrigin.COOK_REFIT: ActivationPolicy.COOK_REFIT,
        }[origin]

    def _persist_reviewed_candidate_checkpoint(self, evaluation, preparation):
        request = getattr(getattr(preparation, "candidate", None), "request", None)
        if (
            getattr(request, "origin", None) is not CandidateOrigin.OPERATOR_CALIBRATION
            or not bool(getattr(evaluation, "accepted", False))
            or tuple(getattr(evaluation, "blockers", ()))
        ):
            return
        candidate_pair = getattr(preparation, "candidate_pair", None)
        candidate_descriptor = getattr(candidate_pair, "descriptor", None)
        active_descriptor = self._active_control_pair.descriptor
        if not isinstance(candidate_descriptor, GreyControlPairDescriptor):
            raise RuntimeError("reviewed-candidate-descriptor-missing")
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
        outcome = ControllerModelStore().save_outcome("mpc", checkpoint)
        if outcome is not CheckpointSaveOutcome.SAVED:
            self._model_revision = previous_revision
            raise RuntimeError("reviewed-candidate-checkpoint-not-durable")
        persisted.add(evaluation.decision_id)

    def _persist_candidate_evaluation(self, evaluation, preparation):
        persisted_ids = getattr(
            self,
            "_persisted_activation_confidence_ids",
            (),
        )
        if evaluation.decision_id in persisted_ids:
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
            default=time.time_ns() // 1_000_000,
        )
        persisted = self._persist_grey_lifecycle(
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
        receipt = self._activation_persistence_channel().submit_activation_confidence(confidence)
        if not receipt.accepted or receipt.wait(2.0) is not True or receipt.durable is not True:
            raise RuntimeError("activation-confidence-not-durable")
        persisted_ids = getattr(
            self,
            "_persisted_activation_confidence_ids",
            None,
        )
        if persisted_ids is None:
            persisted_ids = set()
            self._persisted_activation_confidence_ids = persisted_ids
        persisted_ids.add(evaluation.decision_id)
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
            timestamp_ms=time.time_ns() // 1_000_000,
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
            timestamp_ms=time.time_ns() // 1_000_000,
            role_generation=request.window.role_generation,
            model_digest=model_digest,
            provenance_digest=request.window.incumbent_digest,
        )

    def _prepare_automatic_pair_activation(self, preparation, policy):
        """Persist one passive candidate and expose it to the runner only after receipt."""
        from controller.runtime.runner import PreparedPairTransition

        if policy is not ActivationPolicy.PASSIVE_AUTO:
            raise ValueError("manual candidate requires the reviewed activation endpoint")
        candidate_pair = getattr(preparation, "candidate_pair", None)
        candidate_result = getattr(preparation, "candidate", None)
        request = getattr(candidate_result, "request", None)
        native_config = getattr(candidate_result, "config", None)
        raw_estimator_kind = self.cfg.get("estimator")
        if raw_estimator_kind == "ekf":
            estimator_kind: Literal["ekf", "kf"] = "ekf"
        elif raw_estimator_kind == "kf":
            estimator_kind = "kf"
        else:
            raise ValueError("candidate preparation is incomplete")
        if (
            candidate_pair is None
            or request is None
            or not isinstance(native_config, GreyBoxMPCConfig)
        ):
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
        worker = self._activation_persistence_channel()
        receipts = []

        def persist_prepared(record):
            receipt = worker.submit_activation_phase(record, expected_phase=None)
            receipts.append(receipt)
            return receipt
        def build_candidate(descriptor: GreyControlPairDescriptor) -> OwnedMpcPair:
            if descriptor != candidate_descriptor:
                raise ValueError("candidate-digest-changed")
            return owned_candidate


        manager = ActivationManager(
            incumbent_pair=self._active_control_pair,
            build_candidate=build_candidate,
            validate_candidate=lambda pair: (
                pair is owned_candidate and self._pair_factory.validate(pair)
            ),
            native_dry_solve=lambda _pair: bool(preparation.dry_solve_finite),
            persist_prepared=persist_prepared,
            receipt_timeout=2.0,
        )
        evaluation = getattr(self._learning, "_last_evaluation", None)
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
            or evaluation_role_generation != self._active_control_pair.descriptor.role_generation
        ):
            owned_candidate.close()
            raise RuntimeError("activation-confidence-changed")
        evaluated_at_ms = max(
            (int(origin.completion_time_s * 1_000) for origin in tuple(getattr(evaluation, "completed_origins", ()))),
            default=time.time_ns() // 1_000_000,
        )
        # Evaluation completion persists confidence for normal handoff. Direct
        # preparation tests and recovery callers still close the same durability gap.
        persisted_confidence = getattr(
            self,
            "_persisted_activation_confidence_ids",
            None,
        )
        if persisted_confidence is None:
            persisted_confidence = set()
            self._persisted_activation_confidence_ids = persisted_confidence
        if decision_id not in persisted_confidence:
            confidence = ModelEvidenceRecord(
                evidence_id=(f"activation-confidence:{decision_id}:{evaluation_role_generation}"),
                kind=EvidenceKind.CONFIDENCE_DECISION,
                session_id=getattr(self, "_learning_session_id", None) or "mpc-learning",
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
            confidence_receipt = worker.submit_activation_confidence(confidence)
            if (
                not confidence_receipt.accepted
                or confidence_receipt.wait(2.0) is not True
                or confidence_receipt.durable is not True
            ):
                owned_candidate.close()
                raise RuntimeError("activation-confidence-not-durable")
            persisted_confidence.add(decision_id)
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
        activation_lifecycle = ActivationLifecycleEvidence(
            decision_id=decision.record.decision_id,
            phase="prepared",
            origin=decision.record.origin.value,
            policy=decision.record.policy.value,
        )
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
        transition = PreparedPairTransition(
            decision.record,
            decision.candidate_pair,
            receipts[0],
            lambda record, expected: worker.submit_activation_phase(
                record,
                expected_phase=expected,
            ),
        )
        self._prepared_pair_transitions.append(transition)
        return decision.record.transaction_id

    def drain_prepared_pair_transitions(self):
        transitions = tuple(self._prepared_pair_transitions)
        self._prepared_pair_transitions.clear()
        return transitions

    def _poll_learning_off_path_locked(self, *, live_origin=None):
        """Run one lifecycle poll while identity mutation is fenced."""
        with self._learning_lock:
            learning = self._learning
            if learning is None:
                return None, None
            origin = self._learning_pending_origin if live_origin is None else live_origin
            if origin is not None:
                origin = origin if isinstance(origin, CandidateOrigin) else CandidateOrigin(origin)
                self._learning_preparing = True

        delivery = None
        try:
            if origin is not None:
                delivery = learning.poll_fit_off_path(
                    live_identity=self._learning_identity(),
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
        evaluation_started = time.monotonic()
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
        evaluation_duration_ms = (time.monotonic() - evaluation_started) * 1_000
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
                prepare=self._prepare_automatic_pair_activation,
            )
        with self._learning_lock:
            if learning is self._learning and payload is not None:
                self._learning_pending_evaluation = payload
                self._learning_pending_confidence_accepted = bool(getattr(evaluation, "accepted", False))
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
        if self._activation_terminated_reason is not None:
            status = LearningStatus.ERROR
        elif self._active_activation_record is not None:
            status = LearningStatus.ACTIVE
        elif self._inert_activation is not None or self._prepared_pair_transitions:
            status = LearningStatus.ACTIVATING
        elif learning is not None:
            request = getattr(learning, "_pending_request", None)
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
        active_descriptor = self._active_control_pair.descriptor
        candidate_descriptor = self._inert_activation.candidate if self._inert_activation is not None else None
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
                self._inert_activation.phase.value
                if self._inert_activation is not None
                else self._active_activation_record.phase.value
                if self._active_activation_record is not None
                else "aborted"
            ),
            "pending_persistence": bool(self._prepared_pair_transitions),
            "pending_swap": self._inert_activation is not None,
            "failure": (
                None
                if self._activation_terminated_reason is None
                else {
                    "code": "activation-terminal",
                    "detail": self._activation_terminated_reason,
                    "terminal": True,
                }
            ),
        }

    def get_status(self):
        return {
            "set_point": finite_float(getattr(self, "set_point", self._set_point_c)),
            "set_point_c": finite_float(self._set_point_c),
            "last_combustion_load": finite_float(self._last_combustion_load),
            "last_raw_combustion_load": optional_float(self._last_raw_combustion_load),
            "last_equilibrium_load": optional_float(self._last_equilibrium_load),
            "last_residual_load": optional_float(self._last_residual_load),
            "applied_combustion_load": finite_float(self._applied_combustion_load),
            "policy": "acados-grey",
            "policy_kind": "acados-grey",
            "n_horizon": int(self.cfg["n_horizon"]),
            # Non-zero means update() is returning a held command rather than a
            # computed one, so the number this reports is how many control
            # periods the grill has been running open-loop.
            "policy_failures": int(self._consecutive_policy_failures),
            "u_max": finite_float(self.u_max),
            "x_hat": None
            if self._x_hat is None
            else tuple(finite_float(v) for v in np.asarray(self._x_hat).reshape(-1)),
            # The __dict__ fallback this replaces reached the pid_cycle_data mqtt
            # topic only through notify()'s nested-dict recursion over this same
            # attribute; publish it explicitly so that topic keeps working.
            # cycle_data is core.__dict__'s live reference to settings["cycle_data"]
            # (see _build_core) -- sanitized_copy hands back a copy, not that
            # live settings mapping.
            "cycle_data": sanitized_copy(self.cycle_data),
            # None until a model has been adopted this process (fresh install,
            # or before the first fit completes): a model identified at one
            # temperature does not describe another, so the band it was fit
            # over travels with the fit error rather than being assumed global.
            "model": None
            if self._model_meta is None
            else {
                "band_c": [finite_float(v) for v in self._model_meta["band_c"]],
                "rmse": optional_float(self._model_meta["rmse"]),
            },
            "feasibility": None if self._last_feasibility is None else self._last_feasibility.as_status(),
            "learning": self._learning_live_status(),
            "activation": {
                "active_kind": _snapshot.GREY_BOX_KIND,
                "active_digest": self._active_control_pair.descriptor.model_digest,
                "decision_id": (
                    None if self._active_activation_record is None else self._active_activation_record.decision_id
                ),
                "role_generation": self._active_control_pair.descriptor.role_generation,
                "failed_digest": None,
                "failed_generation": None,
                "last_safe_command": finite_float(self._last_combustion_load),
                "fallback_kind": (_snapshot.GREY_BOX_KIND if self._activation_terminated_reason is not None else None),
                "fallback_reason": self._activation_terminated_reason,
            },
        }

    #: The model structure a snapshot describes, shared with every other thing
    #: that outlives the process and claims to describe this model (see
    #: mpc_model.MODEL_SCHEMA) rather than counted separately here -- two
    #: numbers meaning the same thing is how they drift.
    #:
    #: A version 1 record describes the two-lump model this controller no
    #: longer has: its C_f and h_fc name nothing, and the C_c, h_amb and K_Q
    #: beside them were fitted against a chamber that was fed through a
    #: firepot, so they are not this model's parameters under a shorter name.
    #: Applying the subset that still has matching keys would put a stranger's
    #: numbers on a live grill, so `restore_model` refuses the record and says
    #: so; the next cook refits from scratch, which is what a fresh install
    #: does anyway.
    _MODEL_SCHEMA = MODEL_SCHEMA
    _MODEL_PARAM_KEYS = _snapshot.MODEL_PARAM_KEYS

    def _adopt_model(
        self,
        params,
        *,
        rmse,
        samples,
        band_c,
        nfev=None,
        descriptor: GreyControlPairDescriptor | None = None,
        origin: CandidateOrigin = CandidateOrigin.COOK_REFIT,
        policy: ActivationPolicy = ActivationPolicy.COOK_REFIT,
    ):
        """Adopt fitted parameters and atomically advance the active owner identity.

        Only `_MODEL_PARAM_KEYS` cross into the numerical configuration. The
        pair descriptor advances in the same operation, so persistence never
        publishes a model identity different from the one owned by the pair.
        """

        previous = self._active_control_pair.descriptor
        self.cfg.update({k: params[k] for k in self._MODEL_PARAM_KEYS if k in params})
        active = descriptor or self._pair_factory.descriptor(
            self._pair_factory.configured(
                self.cfg,
                candidate_generation=previous.candidate_generation,
                role_generation=previous.role_generation,
            )
        )
        self._active_control_pair.descriptor = active
        self._checkpoint_origin = origin
        self._checkpoint_policy = policy
        self._checkpoint_rollback_identity = (
            previous.model_digest,
            previous.role_generation,
        )
        self._model_meta = {
            # Provenance, not a comparison input: what this model achieved on
            # the cook it was fit from. model_promotion.evaluate() always
            # RECOMPUTES the incumbent's error on the candidate's own data
            # before adopting anything new, so this stored value is never fed
            # into that comparison -- it is a record, not a gate.
            "rmse": float(rmse),
            "samples": int(samples),
            "band_c": [float(band_c[0]), float(band_c[1])],
            # How hard the solve worked for these numbers. A converged solve
            # that took one evaluation moved nowhere, and reads identically to
            # a hard-won one in every other field. None -- never 0 -- stands in
            # for "not recorded", so an unknown effort cannot be misread as a
            # measured absence of effort.
            "nfev": None if nfev is None else int(nfev),
        }

    def get_model_snapshot(self):
        """Return the complete grey-only v4 checkpoint; process jobs stay live-only."""
        metadata = (
            {"rmse": None, "samples": 0, "band_c": [0.0, 0.0], "nfev": None}
            if self._model_meta is None
            else self._model_meta
        )
        try:
            snapshot = _snapshot._new_grey_learning_snapshot(
                revision=int(self._model_revision),
                parameters=self._core.snapshot_parameters(),
                metadata=metadata,
            )
            live = self._learning_live_status()
            active = self._active_control_pair.descriptor
            candidate = self._inert_activation.candidate if self._inert_activation is not None else None
            if self._rollback_control_pair is not None:
                rollback_digest = self._rollback_control_pair.descriptor.model_digest
                rollback_generation = self._rollback_control_pair.descriptor.role_generation
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
                    key: (
                        getattr(candidate_config, "delay_states")
                        if key == "n_delay"
                        else getattr(candidate_config, key)
                    )
                    for key in self._MODEL_PARAM_KEYS
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
                candidate_descriptor = getattr(
                    getattr(prepared, "candidate_pair", None),
                    "descriptor",
                    None,
                )
            elif candidate is None and self._teardown_candidate is not None:
                candidate_config = self._teardown_candidate.config
                candidate_parameters = {
                    key: (candidate_config.delay_states if key == "n_delay" else getattr(candidate_config, key))
                    for key in self._MODEL_PARAM_KEYS
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
            else:
                candidate_descriptor = None
            snapshot["evidence"] = {
                "eligible": int(self._learning_eligible_updates),
                "rejected": int(self._learning_rejected_updates),
                "confidence_decision_id": (
                    self._teardown_decision_id
                    if self._teardown_decision_id is not None
                    else None
                    if self._active_activation_record is None
                    else self._active_activation_record.decision_id
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
                else self._inert_activation.policy.value
                if self._inert_activation is not None
                else self._active_activation_record.policy.value
                if self._active_activation_record is not None
                else None
            )
            snapshot["identification"] = {
                "status": "identified" if self._model_meta is not None else "unidentified",
            }
            refit_status = (
                "succeeded"
                if self._cook_refit_outcome
                in {
                    TeardownRefitOutcome.READY_FOR_REVIEW,
                    TeardownRefitOutcome.ACCEPTED_NEXT_COOK,
                }
                else "failed"
                if self._cook_refit_outcome
                in {
                    TeardownRefitOutcome.REJECTED,
                    TeardownRefitOutcome.FAILED,
                    TeardownRefitOutcome.CHECKPOINT_FAILURE,
                }
                else "idle"
            )
            snapshot["cook_refit"] = {
                "status": refit_status,
                "latest": (None if self._cook_refit_outcome is None else self._cook_refit_outcome.value),
            }
            snapshot["identities"] = {
                "active_digest": active.model_digest,
                "active_generation": active.role_generation,
                "candidate_digest": (
                    candidate.model_digest
                    if candidate is not None
                    else candidate_descriptor.model_digest
                    if isinstance(candidate_descriptor, GreyControlPairDescriptor)
                    else live["candidate_digest"]
                ),
                "candidate_generation": (
                    candidate.candidate_generation
                    if candidate is not None
                    else candidate_descriptor.candidate_generation
                    if isinstance(candidate_descriptor, GreyControlPairDescriptor)
                    else live["candidate_generation"]
                ),
                "rollback_digest": rollback_digest,
                "rollback_generation": rollback_generation,
            }
            snapshot["activation"] = {
                "phase": live["activation_phase"],
                "pending_persistence": live["pending_persistence"],
                "pending_swap": live["pending_swap"],
            }
            snapshot["failure"] = (
                None
                if live["failure"] is None
                else {
                    "code": live["failure"]["code"],
                    "detail": live["failure"]["detail"],
                }
            )
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

    def restore_model(self, snapshot):
        if not isinstance(snapshot, dict) or snapshot.get("version") != self._MODEL_SCHEMA:
            version = snapshot.get("version") if isinstance(snapshot, dict) else None
            print(
                f"[mpc] discarding a version {version!r} model snapshot: runtime restore "
                f"accepts only grey schema {self._MODEL_SCHEMA}; version 3 is migration input only."
            )
            return False
        try:
            owned = _snapshot.migrate_grey_learning_snapshot(snapshot)
        except _snapshot.GreySnapshotInvalid as error:
            print(f"[mpc] discarding an incompatible grey snapshot ({error.reason}).")
            return False
        active = owned["active"]
        params = active["parameters"]
        metadata = active["metadata"]
        revision = owned["revision"]
        configured_n_delay = int(self.cfg["n_delay"])
        snapshot_n_delay = int(params["n_delay"])
        if configured_n_delay != 8 or snapshot_n_delay != 8:
            print("[mpc] discarding an incompatible grey snapshot (incompatible-delay).")
            return False
        merged = dict(self.cfg)
        merged.update({key: params[key] for key in self._MODEL_PARAM_KEYS})
        merged["n_delay"] = 8
        # The restored parameters have to reach the estimator, the horizon and
        # the policy, not just the config those three were sized from -- a
        # config-only restore leaves the season's learning inert and, where the
        # restored model coasts further than the shipped one, plans over a
        # horizon that stops short of the brake. Built before anything is
        # committed so a build that fails leaves the controller solving the
        # model it already had.
        try:
            restored_descriptor = GreyControlPairDescriptor.from_dict(owned["active_pair"])
            restored_configuration = self._pair_factory.configured(
                merged,
                candidate_generation=restored_descriptor.candidate_generation,
                role_generation=restored_descriptor.role_generation,
            )
            if self._pair_factory.descriptor(restored_configuration) != restored_descriptor:
                raise ValueError("restored active pair does not match active model")
            restored_pair = self._pair_factory.restore(restored_descriptor)
            restored_pair.authorize_output()
        except Exception as exc:
            print(f"[mpc] a stored model could not be built ({exc}); keeping the model this controller started with.")
            return False
        old_pair = self._active_control_pair
        old_learning = self._learning
        self.cfg = restored_pair.core.config
        self._active_control_pair = restored_pair
        self._core = restored_pair.core
        self._learning = None
        if old_learning is not None:
            old_learning.close()
        old_pair.close()
        # Said for the model that will actually solve. __init__'s own call saw
        # only the configured parameters, which for a grill that has been
        # learning are not the ones about to steer it.
        warn_about_model(self.cfg)
        # Continue the persisted counter rather than starting a new one: the
        # store rejects a revision that does not advance, permanently.
        self._model_revision = revision
        self._model_meta = {
            "rmse": metadata["rmse"],
            "samples": metadata["samples"],
            "band_c": list(metadata["band_c"]),
            "nfev": metadata["nfev"],
        }
        restored_origin = owned["origin"]
        self._checkpoint_origin = (
            None if restored_origin is None else CandidateOrigin(restored_origin)
        )
        restored_policy = owned["policy"]
        self._checkpoint_policy = (
            None if restored_policy is None else ActivationPolicy(restored_policy)
        )
        rollback_digest = owned["identities"]["rollback_digest"]
        rollback_generation = owned["identities"]["rollback_generation"]
        self._checkpoint_rollback_identity = (
            None
            if rollback_digest is None or rollback_generation is None
            else (rollback_digest, rollback_generation)
        )
        if owned["identification"]["status"] != "identified":
            self._model_meta = None
        self._learning_eligible_updates = owned["evidence"]["eligible"]
        self._learning_rejected_updates = owned["evidence"]["rejected"]
        self._rotate_teardown_role_generation(restored_descriptor.role_generation)
        if self._learning_enabled:
            self._learning = self._build_learning()
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
        timestamp_ms = time.time_ns() // 1_000_000
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
        receipt = self._activation_persistence_channel().submit_activation_confidence(confidence)
        if not receipt.accepted or receipt.wait(2.0) is not True or receipt.durable is not True:
            raise RuntimeError("operator-review-confidence-not-durable")
        self._persisted_activation_confidence_ids.add(decision_id)
        self._teardown_decision_id = decision_id
        return decision_id

    def _refit_completed_frames(self) -> TeardownRefitResult:
        from controller.model_promotion import evaluate
        from controller.update_mpc import fit_quality

        frames = self._teardown_history.observations
        origin = self._teardown_history.origin
        if len(frames) < _REFIT_MIN_SAMPLES:
            return TeardownRefitResult.insufficient(f"only {len(frames)} samples; need {_REFIT_MIN_SAMPLES}")
        identity = self._learning_identity()
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

        worker = GreyFitWorker()
        try:
            worker.start()
            if worker.submit(GreyFitJob(request, frames, self.mpc.config)) is not FitSubmission.ACCEPTED:
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
            for key in self._MODEL_PARAM_KEYS
        }
        incumbent = {key: float(self.cfg[key]) for key in self._MODEL_PARAM_KEYS}
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
            T_amb=float(self.cfg["T_amb"]),
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
            incumbent_pair=CandidatePair(self.estimator, self.mpc),
            estimator_factory=self._pair_factory.build_estimator,
            controller_factory=self._pair_factory.build_solver,
            timing_probe=self._pair_factory.probe_solver,
        )
        if not preparation.accepted:
            reason = ",".join(preparation.blockers) or "candidate-preparation-rejected"
            return TeardownRefitResult.rejected(reason, origin=origin)
        descriptor = GreyControlPairDescriptor(
            model_digest=preparation.candidate_digest,
            configuration={name: getattr(success.config, name) for name in success.config.__dataclass_fields__},
            estimator_kind=str(self.cfg["estimator"]),
            solver_kind="acados-grey",
            candidate_generation=identity.candidate_generation,
            role_generation=identity.role_generation + 1,
        )
        self._teardown_fit_window = window
        try:
            if origin is CandidateOrigin.OPERATOR_CALIBRATION:
                self._persist_operator_teardown_authority(window, descriptor)
                self._teardown_candidate = success
                self._teardown_candidate_descriptor = descriptor
                return TeardownRefitResult.ready_for_review(
                    verdict.reason,
                    candidate_digest=descriptor.model_digest,
                )
            self._adopt_model(
                candidate_parameters,
                rmse=success.rmse_c,
                samples=success.sample_count,
                band_c=success.temperature_band_c,
                nfev=success.nfev,
                descriptor=descriptor,
                origin=origin,
                policy=(
                    ActivationPolicy.PASSIVE_AUTO
                    if origin is CandidateOrigin.PASSIVE_ONLINE
                    else ActivationPolicy.COOK_REFIT
                ),
            )
            return TeardownRefitResult.accepted_next_cook(
                verdict.reason,
                candidate_digest=descriptor.model_digest,
            )
        finally:
            self._close_prepared_candidate(preparation)

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
        return list(self._history)

    def refit_from_cook(self, history=None):
        """Refit the thermal model from a finished cook and judge the result.

        Between cooks only: a refit re-simulates the whole history once per
        least-squares evaluation, so it belongs nowhere near the control path.
        It runs synchronously on its caller's thread and takes seconds, bounded
        by `_HISTORY_MAX` -- see HoldMode._refit_model for why spending them at
        teardown is safe. An accepted model changes `cfg` but rebuilds nothing:
        it reaches the grill through the next cook's restore.
        """
        from controller.model_promotion import evaluate
        from controller.update_mpc import fit_params, fit_quality, identifiability

        if history is None:
            return self._refit_completed_frames()

        rows = list(history if history is not None else self._history)
        if len(rows) < _REFIT_MIN_SAMPLES:
            return _Verdict(False, f"only {len(rows)} samples; need {_REFIT_MIN_SAMPLES}")

        started = time.perf_counter()
        t = np.array([r[0] for r in rows], dtype=float)
        temp = np.array([r[1] for r in rows], dtype=float)
        Q = np.array([r[2] for r in rows], dtype=float)
        t = t - t[0]

        T_amb = float(self.cfg["T_amb"])
        try:
            fitted = fit_params(
                t,
                temp,
                Q,
                T_amb=T_amb,
                init=dict(_REFIT_INIT),
                sigma=float(self.cfg["sigma"]),
                n_delay=int(self.cfg["n_delay"]),
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
                print(
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
            incumbent = {k: float(self.cfg[k]) for k in self._MODEL_PARAM_KEYS}
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
        print(
            f"[mpc] refit: {verdict.reason} (candidate RMSE {cand_rmse:.2f} C, "
            f"incumbent {inc_rmse:.2f} C, {fitted['nfev']} evaluations over "
            f"{len(rows)} samples in {time.perf_counter() - started:.1f} s)"
        )
        if verdict.accepted:
            self._adopt_model(
                fitted,
                rmse=cand_rmse,
                samples=len(rows),
                band_c=(float(temp.min()), float(temp.max())),
                nfev=fitted["nfev"],
            )
        return verdict

    def set_safety_ceiling_c(self, ceiling_c) -> None:
        """Track the grill's configured maximum, which probing must stay under.

        Read from settings on every tick rather than carried on a calibration
        command, so lowering the grill maximum mid-cook binds the next probe
        instead of the next operator action.
        """
        value = float(ceiling_c)
        if not math.isfinite(value):
            raise ValueError("safety ceiling must be finite Celsius")
        self._calibration_safety_ceiling_c = value

    def request_calibration(self, command: CalibrationCommand) -> None:
        """Queue each strictly newer operator command for ordered consumption."""
        if not isinstance(command, CalibrationCommand):
            raise TypeError("command must be CalibrationCommand")
        if command.command_revision < self._calibration_last_revision:
            raise ValueError("calibration command revision must be monotonic")
        if command.command_revision == self._calibration_last_revision:
            return
        self._calibration_operations.append(("command", command))
        self._calibration_last_revision = command.command_revision

    def cancel_calibration(self, reason: str) -> None:
        """Queue a safety abort without consuming an operator revision."""
        if not isinstance(reason, str) or not reason:
            raise ValueError("calibration cancellation reason must be a non-empty string")
        self._calibration_operations.append(("cancel", reason))

    def _predict_calibration_max(self, baseline_q: float, probe_q: float, runtime: CalibrationRuntimeContext) -> float:
        """Forecast the incumbent grey-box horizon for one requested probe."""
        horizon = max(1, int(self.cfg["n_horizon"]))
        requested_q = float(np.clip(baseline_q + probe_q, 0.0, 1.0))
        adapter = GreyBoxPredictionAdapter.from_controller(self)
        forecast = adapter.forecast(
            np.full(horizon, requested_q, dtype=np.float64),
            np.full(horizon, self._calibration_ambient_c, dtype=np.float64),
        )
        if forecast.size != horizon or not np.isfinite(forecast).all():
            raise FloatingPointError("grey-box calibration forecast is non-finite")
        return float(np.max(forecast))

    def _calibration_runtime(
        self,
        baseline_q: float,
        temperature_c: float,
        *,
        realized_q: float | None = None,
        continuous: bool = True,
        actuation_known: bool = True,
    ) -> CalibrationRuntimeContext:
        return CalibrationRuntimeContext(
            now_s=time.monotonic(),
            temp_c=temperature_c,
            target_c=self._set_point_c,
            baseline_q=baseline_q,
            realized_q=self._applied_combustion_load if realized_q is None else realized_q,
            safety_ceiling_c=self._calibration_safety_ceiling_c,
            allocator_headroom=1.0,
            error_rate_headroom=1.0,
            capability_headroom=1.0,
            saturation_headroom=1.0,
            rank_progress=1.0,
            coverage_progress=1.0,
            continuous=continuous,
            actuation_known=actuation_known,
        )

    @staticmethod
    def _command_decision(
        decision: CalibrationDecision, command: CalibrationCommand, command_generation: int
    ) -> CalibrationDecision:
        return replace(
            decision,
            command_revision=command.command_revision,
            command_action=command.action,
            command_generation=command_generation,
        )

    def _advance_calibration(self, baseline_q: float, temperature_c: float) -> CalibrationDecision:
        decision: CalibrationDecision | None = None
        while self._calibration_feedback:
            (
                feedback_baseline_q,
                realized_q,
                continuous,
                actuation_known,
                disposition,
                _revision,
                command_revision,
                command_action,
                command_generation,
            ) = self._calibration_feedback.popleft()
            provenance = self._trace_calibration
            if not provenance.active or (
                provenance.command_revision,
                provenance.command_action,
                provenance.command_generation,
            ) != (command_revision, command_action, command_generation):
                continue
            if disposition is FrameFeedbackDisposition.DISCARDED:
                decision = self._calibration.cancel_probe("discarded_frame")
            elif disposition is FrameFeedbackDisposition.COMPLETE:
                decision = replace(
                    self._calibration.advance(
                        self._calibration_runtime(
                            feedback_baseline_q,
                            temperature_c,
                            realized_q=realized_q,
                            continuous=continuous,
                            actuation_known=actuation_known,
                        )
                    ),
                    command_revision=provenance.command_revision,
                    command_action=provenance.command_action,
                    command_generation=provenance.command_generation,
                )
            else:
                continue
            self._trace_calibration = decision
        while self._calibration_operations:
            operation, payload = self._calibration_operations.popleft()
            if operation == "cancel":
                decision = self._calibration.cancel_probe(payload)
            else:
                command = payload
                assert isinstance(command, CalibrationCommand)
                self._calibration_ambient_c = command.ambient_c
                runtime = self._calibration_runtime(baseline_q, temperature_c)
                if command.action == "start":
                    self._calibration_generation += 1
                    command_generation = self._calibration_generation
                else:
                    command_generation = self._trace_calibration.command_generation
                if command.action == "start":
                    decision = self._calibration.start(
                        _CoordinatorCalibrationCommand(command.command_revision, command.seed),
                        runtime,
                    )
                elif command.action == "pause":
                    decision = self._calibration.pause()
                elif command.action == "resume":
                    decision = self._calibration.resume(runtime)
                elif command.action == "stop":
                    decision = self._calibration.stop(runtime)
                else:
                    decision = self._calibration.reset_progress(runtime)
                decision = self._command_decision(decision, command, command_generation)
            self._trace_calibration = decision
        if decision is None:
            decision = replace(
                self._trace_calibration,
                events=(),
            )
        self._trace_calibration = decision
        return decision

    def register_calibration_result(self, result) -> None:
        """Associate a completed runner result with the frame that may latch it."""
        calibration = result.calibration
        baseline = result.baseline_allocation
        if calibration is None or baseline is None or result.revision <= 0:
            return
        self._calibration_frame_results[result.revision] = (
            baseline.normalized_combustion_load,
            calibration,
        )

    def set_output(self, applied):
        """Record physical output and terminalize only explicit frame feedback."""
        self._core.set_output(applied)
        realized_q = self._applied_combustion_load
        if applied.feedback_disposition is FrameFeedbackDisposition.PROGRESS:
            return
        revision = applied.producing_result_revision
        produced = self._calibration_frame_results.pop(revision, None) if revision > 0 else None
        if produced is None:
            return
        for stale_revision in tuple(self._calibration_frame_results):
            if stale_revision < revision:
                del self._calibration_frame_results[stale_revision]
        baseline_q, decision = produced
        if not decision.active or (
            decision.command_revision,
            decision.command_action,
            decision.command_generation,
        ) != (
            applied.producing_calibration_revision,
            applied.producing_calibration_action,
            applied.producing_calibration_generation,
        ):
            return
        previous = self._calibration_last_feedback_timestamp
        continuous = previous is None or applied.timestamp > previous
        self._calibration_last_feedback_timestamp = applied.timestamp
        disposition = applied.feedback_disposition
        if disposition is FrameFeedbackDisposition.COMPLETE and not applied.sample_complete:
            disposition = FrameFeedbackDisposition.DISCARDED
        self._calibration_feedback.append(
            (
                baseline_q,
                realized_q,
                continuous,
                applied.controller_commanded,
                disposition,
                revision,
                decision.command_revision,
                decision.command_action,
                decision.command_generation,
            )
        )

    def update(self, current):
        step = self._core.update(current)
        self._trace_diagnostics = step.diagnostics
        self._trace_baseline_allocation = step.baseline_allocation
        self._trace_allocation = step.allocation
        return {"cycle_ratio": step.cycle_ratio, "fan": dict(step.fan)}

    def trace_diagnostics(self) -> MpcTraceDiagnostics | None:
        return self._trace_diagnostics

    def trace_allocation(self) -> AllocationResult | None:
        return self._trace_allocation

    def trace_baseline_allocation(self) -> AllocationResult | None:
        return self._trace_baseline_allocation

    def trace_calibration(self) -> CalibrationDecision:
        return self._trace_calibration

    def native_failure_diagnostics(self) -> SolverDiagnostics | None:
        return self._core.native_failure_diagnostics

    def close(self):
        """Release learning, native solver, and estimator exactly once."""
        if self._closed:
            return
        self._closed = True
        errors: list[BaseException] = []
        learning = self._learning
        self._learning = None
        try:
            if learning is not None:
                learning.close()
        except BaseException as error:
            errors.append(error)
        with self._activation_persistence_lock:
            persistence_worker = self._activation_persistence_worker
            self._activation_persistence_worker = None
        if persistence_worker is not None:
            try:
                persistence_worker.flush_and_stop(timeout=0.1)
            except BaseException as error:
                errors.append(error)
        try:
            self._core.close()
        except BaseException as error:
            errors.append(error)
        rollback = self._rollback_control_pair
        self._rollback_control_pair = None
        if rollback is not None:
            try:
                rollback.close()
            except BaseException as error:
                errors.append(error)
        if errors:
            raise RuntimeError("could not close complete MPC controller ownership") from errors[0]
