"""Bounded, prequential online adaptation for production scheduled ARX models."""

from __future__ import annotations
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum
import hashlib
import json
from math import isfinite, sqrt
from types import MappingProxyType
from typing import Protocol, cast
import numpy as np
from .arx import ScheduledARX
from .contracts import AffinePrediction, FrameObservation, ModelUpdate

_SCHEMA = "online-adaptation/v2"
_LEGACY_SCHEMA = "online-adaptation/v1"
_HORIZONS = (3, 15)
_SNAPSHOT_FIELDS = frozenset(
    (
        "schema",
        "policy",
        "parameter_learning",
        "accepted_sources",
        "incumbent",
        "challenger",
        "previous_incumbent",
        "previous_incumbent_digest",
        "last_rollback_digest",
        "role_generation",
        "effective_updates",
        "consecutive_wins",
        "lag_warmup_remaining",
        "excitation",
        "last_duty",
        "last_evaluation_s",
        "score_aggregate",
        "partial_origins",
    )
)


class AdaptiveModel(Protocol):
    def observe(self, observation: FrameObservation) -> ModelUpdate: ...

    def track(self, observation: FrameObservation) -> ModelUpdate: ...

    def affine_prediction(
        self, horizon_steps: int, q_previous: float, ambient_future: np.ndarray
    ) -> AffinePrediction: ...

    def snapshot(self) -> Mapping[str, object]: ...


class UpdateRejectionReason(StrEnum):
    LID_OPEN = "lid-open"
    SAFETY = "safety"
    MANUAL = "manual"
    STALE = "stale"
    SKIPPED_OR_RESET = "skipped-or-reset"
    NON_CONTROLLER_SOURCE = "non-controller-source"
    DISCONTINUITY = "discontinuity"
    UNKNOWN_ACTUATION = "unknown-actuation"
    INSUFFICIENT_EXCITATION = "insufficient-excitation"
    LAG_WARMUP = "lag-warmup"


class EvaluationRejectionReason(StrEnum):
    PREDICTION = "prediction"
    BRAKING = "braking"
    STABILITY = "stability"
    GAIN = "gain"
    DELAY = "delay"
    SAMPLES = "samples"
    CONTINUITY = "continuity"
    STALE_GENERATION = "stale-generation"
    PROSPECTIVE = "prospective"


@dataclass(frozen=True, slots=True)
class AdaptationPolicy:
    excitation_window: int = 12
    min_input_variance: float = 0.001
    min_input_levels: int = 2
    min_effective_updates: int = 20
    evaluation_interval_s: float = 300.0
    required_consecutive_wins: int = 2
    max_delay_steps: int = 15
    max_pole_magnitude: float = 0.999
    braking_tolerance_c: float = 0.0

    def __post_init__(self) -> None:
        if self.excitation_window < 1 or self.min_input_levels < 1:
            raise ValueError("excitation history limits must be positive")
        if self.min_input_variance < 0.0 or self.min_effective_updates < 0:
            raise ValueError("adaptation thresholds must be non-negative")
        if self.evaluation_interval_s <= 0.0 or self.required_consecutive_wins < 1:
            raise ValueError("evaluation policy must have positive interval and win count")
        if self.max_delay_steps < 1 or not 0.0 < self.max_pole_magnitude < 1.0:
            raise ValueError("delay and stability policy is invalid")
        if self.braking_tolerance_c < 0.0:
            raise ValueError("braking_tolerance_c must be non-negative")


@dataclass(frozen=True, slots=True)
class UpdateGate:
    permitted: bool
    reasons: tuple[UpdateRejectionReason, ...]
    input_variance: float
    input_levels: int


@dataclass(frozen=True, slots=True)
class ObservationOutcome:
    gate: UpdateGate
    incumbent: ModelUpdate | None
    challenger: ModelUpdate | None
    effective_updates: int

    @property
    def updated(self) -> bool:
        return self.challenger is not None and self.challenger.updated


@dataclass(frozen=True, slots=True)
class HorizonScore:
    """Immutable incumbent/challenger RMSE evidence for one required horizon."""

    horizon_steps: int
    incumbent_rmse_c: float | None
    challenger_rmse_c: float | None
    sample_count: int


@dataclass(frozen=True, slots=True)
class EvaluationDecision:
    decision_id: str
    evaluated_at_s: float
    generation: int
    promoted: bool
    committed: bool
    consecutive_wins: int
    reasons: tuple[EvaluationRejectionReason, ...]
    incumbent_prediction_score: float | None
    candidate_prediction_score: float | None
    incumbent_braking_score: float | None
    candidate_braking_score: float | None
    sample_count: int
    prospective_digest: str | None
    window_start_s: float
    window_end_s: float
    incumbent_digest: str
    challenger_digest: str
    completed_origins: tuple[CompletedOrigin, ...]
    horizon_scores: tuple[HorizonScore, ...]
    evaluation_duration_ms: float = 0.0

    @property
    def role_generation(self) -> int:
        return self.generation


@dataclass(slots=True)
class _ScoreAggregate:
    incumbent_prediction_error: float = 0.0
    candidate_prediction_error: float = 0.0
    prediction_count: int = 0
    incumbent_braking_error: float = 0.0
    candidate_braking_error: float = 0.0
    braking_count: int = 0
    continuous: bool = True

    def add(self, incumbent_error: float, candidate_error: float, braking: bool) -> None:
        self.incumbent_prediction_error += incumbent_error * incumbent_error
        self.candidate_prediction_error += candidate_error * candidate_error
        self.prediction_count += 1
        if braking:
            self.incumbent_braking_error += incumbent_error * incumbent_error
            self.candidate_braking_error += candidate_error * candidate_error
            self.braking_count += 1

    def clear(self) -> None:
        self.incumbent_prediction_error = self.candidate_prediction_error = 0.0
        self.prediction_count = 0
        self.incumbent_braking_error = self.candidate_braking_error = 0.0
        self.braking_count = 0
        self.continuous = True


@dataclass(slots=True)
class _Origin:
    origin_time_s: float
    generation: int
    interval_s: float
    horizon_steps: int
    incumbent: AffinePrediction
    challenger: AffinePrediction
    duty: np.ndarray
    braking: bool


@dataclass(frozen=True, slots=True)
class CompletedOrigin:
    origin_time_s: float
    completion_time_s: float
    horizon_steps: int
    generation: int
    observed_temperature_c: float
    incumbent_error_c: float
    challenger_error_c: float
    braking: bool


@dataclass(slots=True)
class _PendingDecision:
    decision: EvaluationDecision


class OnlineAdaptation:
    """Own challenger learning, prequential scoring, and two-phase role changes."""

    def __init__(
        self,
        incumbent: AdaptiveModel,
        challenger: AdaptiveModel,
        policy: AdaptationPolicy = AdaptationPolicy(),
        *,
        parameter_learning: bool = True,
        accepted_sources: Sequence[str] = ("controller",),
    ) -> None:
        self.incumbent = incumbent
        self.challenger = challenger
        self.policy = policy
        self._parameter_learning = parameter_learning
        self._accepted_sources = frozenset(accepted_sources)
        if not self._accepted_sources:
            raise ValueError("accepted_sources must not be empty")
        self._excitation: deque[float] = deque(maxlen=policy.excitation_window)
        self._origins: list[_Origin] = []
        self._completed: deque[CompletedOrigin] = deque(maxlen=2 * max(_HORIZONS))
        self._completed_window: tuple[CompletedOrigin, ...] = ()
        self._scores = _ScoreAggregate()
        self._role_generation = 0
        self._effective_updates = int(max(_values(challenger.snapshot(), "effective_samples"), default=0.0))
        self._consecutive_wins = 0
        self._lag_warmup_remaining = 0
        self._last_evaluation_s: float | None = None
        self._previous_incumbent: AdaptiveModel | None = None
        self._previous_incumbent_snapshot: Mapping[str, object] | None = None
        self._previous_incumbent_digest: str | None = None
        self._last_rollback_digest: str | None = None
        self._restored_lag_reset_pending = False
        self._pending: dict[str, _PendingDecision] = {}
        self._decision_sequence = 0
        self._last_duty: float | None = None

    @property
    def role_generation(self) -> int:
        return self._role_generation

    @property
    def consecutive_wins(self) -> int:
        return self._consecutive_wins

    @property
    def effective_updates(self) -> int:
        """Number of challenger parameter updates accepted by the coordinator."""
        return self._effective_updates

    @property
    def lag_warmup_remaining(self) -> int:
        return self._lag_warmup_remaining

    def reset_continuity(self) -> None:
        """Discard continuity after an in-session rejected frame."""
        self._mark_discontinuity()

    def begin_restored_session(self, *, preserve_persisted_models: bool = False) -> None:
        """Start fresh post-shutdown evidence without losing durable model state.

        Legacy snapshots predate the explicit ownership audit and must round-trip
        their active learner exactly. Keep its serialized lag state observable
        until the first new frame, while forcing the controller through warmup;
        that frame discards the old lags before ARX prediction can resume.
        """
        self._origins.clear()
        self._pending.clear()
        self._completed.clear()
        self._completed_window = ()
        self._scores.clear()
        self._last_evaluation_s = None
        if preserve_persisted_models:
            self._excitation.clear()
            self._last_duty = None
            self._lag_warmup_remaining = self.policy.max_delay_steps
            self._restored_lag_reset_pending = True
            return
        self._reset_role_lag_history()

    @property
    def completed_origins(self) -> tuple[CompletedOrigin, ...]:
        return self._completed_window or tuple(self._completed)

    @property
    def previous_incumbent_digest(self) -> str | None:
        return self._previous_incumbent_digest

    @property
    def last_rollback_digest(self) -> str | None:
        return self._last_rollback_digest

    def observe(
        self,
        observation: FrameObservation,
        *,
        actuation_known: bool = True,
        ambient_future: Sequence[float] | None = None,
        braking: bool = False,
    ) -> ObservationOutcome:
        destructive_gap = not actuation_known or observation.skipped or observation.reset or not observation.continuous
        hard_reason = self._hard_rejection(observation, actuation_known)
        if destructive_gap:
            self._mark_discontinuity()
        if hard_reason is not None:
            return ObservationOutcome(
                UpdateGate(False, (hard_reason,), 0.0, 0),
                None,
                None,
                self._effective_updates,
            )
        if self._restored_lag_reset_pending:
            self._reset_role_lag_history()
            self._restored_lag_reset_pending = False
        self._complete_origins(observation, braking)
        self._excitation.append(observation.realized_q)
        variance, levels = _excitation(self._excitation)
        if self._lag_warmup_remaining:
            self._lag_warmup_remaining -= 1
            return self._track_only(
                observation,
                variance,
                levels,
                UpdateRejectionReason.LAG_WARMUP,
                ambient_future,
                braking,
            )
        if variance < self.policy.min_input_variance or levels < self.policy.min_input_levels:
            return self._track_only(
                observation,
                variance,
                levels,
                UpdateRejectionReason.INSUFFICIENT_EXCITATION,
                ambient_future,
                braking,
            )
        incumbent_outcome = self.incumbent.track(observation)
        challenger_outcome = (
            self.challenger.observe(observation) if self._parameter_learning else self.challenger.track(observation)
        )
        if challenger_outcome.updated:
            self._effective_updates += 1
        self._capture_origins(observation, ambient_future, braking)
        self._last_duty = observation.realized_q
        return ObservationOutcome(
            UpdateGate(True, (), variance, levels),
            incumbent_outcome,
            challenger_outcome,
            self._effective_updates,
        )

    def evaluate_due(self, at_s: float) -> EvaluationDecision:
        at_s = _finite(at_s, "at_s")
        if self._last_evaluation_s is not None and at_s - self._last_evaluation_s < self.policy.evaluation_interval_s:
            raise ValueError("evaluation is not due")
        self._last_evaluation_s = at_s
        incumbent_prediction, candidate_prediction = _means(
            self._scores.incumbent_prediction_error,
            self._scores.candidate_prediction_error,
            self._scores.prediction_count,
        )
        incumbent_braking, candidate_braking = _means(
            self._scores.incumbent_braking_error, self._scores.candidate_braking_error, self._scores.braking_count
        )
        completed = tuple(self._completed)
        horizon_scores = _horizon_scores(completed)
        reasons = self._evaluation_reasons(
            incumbent_prediction,
            candidate_prediction,
            incumbent_braking,
            candidate_braking,
            horizon_scores,
        )
        win = not reasons
        has_complete_horizon_evidence = all(score.sample_count > 0 for score in horizon_scores)
        if win:
            self._consecutive_wins += 1
        elif has_complete_horizon_evidence:
            self._consecutive_wins = 0
        eligible = win and self._consecutive_wins >= self.policy.required_consecutive_wins
        self._decision_sequence += 1
        decision_id = f"generation-{self._role_generation}-evaluation-{self._decision_sequence}"
        if completed:
            window_start_s = min(origin.origin_time_s for origin in completed)
            window_end_s = max(origin.completion_time_s for origin in completed)
        else:
            window_start_s = window_end_s = at_s
        incumbent_digest = self.model_digest(self.incumbent)
        challenger_digest = self.model_digest(self.challenger)
        decision = EvaluationDecision(
            decision_id,
            at_s,
            self._role_generation,
            eligible,
            False,
            self._consecutive_wins,
            tuple(reasons),
            incumbent_prediction,
            candidate_prediction,
            incumbent_braking,
            candidate_braking,
            len(completed),
            challenger_digest if eligible else None,
            window_start_s,
            window_end_s,
            incumbent_digest,
            challenger_digest,
            completed,
            horizon_scores,
        )
        if eligible:
            self._pending.clear()
            self._pending[decision_id] = _PendingDecision(decision)
        self._completed_window = completed
        self._completed.clear()
        self._scores.clear()
        self._origins = [origin for origin in self._origins if origin.origin_time_s >= at_s]
        return decision

    def prospective_model(self, decision_id: str) -> AdaptiveModel:
        pending = self._pending.get(decision_id)
        if pending is None or pending.decision.generation != self._role_generation:
            raise ValueError("decision is not a current prospective promotion")
        return self.challenger

    def commit_promotion(self, decision_id: str, prospective_solve: object) -> bool:
        """Commit only a current challenger backed by a finite external solve."""
        pending = self._pending.pop(decision_id, None)
        if (
            pending is None
            or pending.decision.generation != self._role_generation
            or pending.decision.prospective_digest != self.model_digest(self.challenger)
            or not _valid_prospective_solve(prospective_solve)
        ):
            self._consecutive_wins = 0
            return False
        previous_incumbent = self.incumbent
        self._previous_incumbent = previous_incumbent
        self._previous_incumbent_snapshot = cast(Mapping[str, object], _owned_json(previous_incumbent.snapshot()))
        self._previous_incumbent_digest = self.model_digest(previous_incumbent)
        self.incumbent = self.challenger
        if isinstance(self.incumbent, ScheduledARX):
            self.challenger = ScheduledARX.from_snapshot(
                _mapping(_owned_json(self.incumbent.snapshot()), "promoted challenger")
            )
        else:
            self.challenger = previous_incumbent
        self._role_generation += 1
        self._consecutive_wins = 0
        self._begin_role_generation()
        return True

    def reject_prospective(self, decision_id: str, reason: str = "invalid-solve") -> bool:
        if not reason:
            raise ValueError("reason must not be empty")
        if self._pending.pop(decision_id, None) is None:
            return False
        self._consecutive_wins = 0
        return True

    def rollback(self) -> bool:
        """Restore the owned pre-promotion incumbent and advance generation."""
        if self._previous_incumbent is None:
            return False
        snapshot = self._previous_incumbent_snapshot
        current_incumbent = self.incumbent
        if isinstance(snapshot, Mapping) and snapshot.get("schema") == "scheduled-arx/v2":
            self.incumbent = ScheduledARX.from_snapshot(snapshot)
        else:
            self.incumbent = self._previous_incumbent
        self.challenger = current_incumbent
        self._previous_incumbent = None
        self._previous_incumbent_snapshot = None
        self._last_rollback_digest = self._previous_incumbent_digest
        self._previous_incumbent_digest = None
        self._role_generation += 1
        self._consecutive_wins = 0
        self._begin_role_generation()
        if callable(getattr(self.incumbent, "reset_lag_history", None)):
            self._reset_role_lag_history()
        else:
            # Grey-box authority has no causal ARX lag to rebuild.  The parked
            # challenger remains available for a later, independently earned
            # promotion without blocking the live grey-box controller.
            self._lag_warmup_remaining = 0
        return True

    def snapshot(self) -> dict[str, object]:
        return {
            "schema": _SCHEMA,
            "policy": asdict(self.policy),
            "parameter_learning": self._parameter_learning,
            "accepted_sources": sorted(self._accepted_sources),
            "incumbent": _owned_json(self.incumbent.snapshot()),
            "challenger": _owned_json(self.challenger.snapshot()),
            "previous_incumbent": self._previous_incumbent_snapshot,
            "previous_incumbent_digest": self._previous_incumbent_digest,
            "last_rollback_digest": self._last_rollback_digest,
            "role_generation": self._role_generation,
            "effective_updates": self._effective_updates,
            "consecutive_wins": self._consecutive_wins,
            "lag_warmup_remaining": self._lag_warmup_remaining,
            "excitation": list(self._excitation),
            "last_duty": self._last_duty,
            "last_evaluation_s": self._last_evaluation_s,
            "score_aggregate": asdict(self._scores),
            "partial_origins": [],
        }

    @classmethod
    def from_snapshot(
        cls,
        snapshot: Mapping[str, object],
        *,
        model_loader: Callable[[Mapping[str, object]], AdaptiveModel] | None = None,
    ) -> OnlineAdaptation:
        schema = snapshot.get("schema")
        if schema not in (_SCHEMA, _LEGACY_SCHEMA):
            raise ValueError(f"snapshot schema must be {_SCHEMA!r} or {_LEGACY_SCHEMA!r}")
        legacy = schema == _LEGACY_SCHEMA
        loader = model_loader or _scheduled_arx_loader
        if not legacy and not _SNAPSHOT_FIELDS.issubset(snapshot):
            raise ValueError("snapshot fields are invalid")
        policy_data = _mapping(snapshot.get("policy"), "policy")
        policy = AdaptationPolicy(**dict(policy_data))
        parameter_learning = snapshot.get("parameter_learning")
        if not isinstance(parameter_learning, bool):
            raise ValueError("parameter_learning must be a bool")
        sources = _strings(snapshot.get("accepted_sources"), "accepted_sources")
        if not sources or len(sources) != len(set(sources)):
            raise ValueError("accepted_sources must be a non-empty unique string list")
        excitation = [
            _finite(value, "excitation item") for value in _sequence(snapshot.get("excitation"), "excitation")
        ]
        if len(excitation) > policy.excitation_window:
            raise ValueError("excitation exceeds policy excitation_window")
        warmup = _nonnegative(
            snapshot.get("lag_warmup_remaining"),
            "lag_warmup_remaining",
        )
        if warmup > policy.max_delay_steps:
            raise ValueError("lag_warmup_remaining exceeds max_delay_steps")
        scores = _score_aggregate(_mapping(snapshot.get("score_aggregate"), "score_aggregate"))
        if snapshot.get("partial_origins") != []:
            raise ValueError("partial origins must never be restored")
        incumbent_payload = _mapping(snapshot.get("incumbent"), "incumbent")
        challenger_payload = _mapping(snapshot.get("challenger"), "challenger")
        incumbent = loader(incumbent_payload)
        challenger = loader(challenger_payload)
        previous_payload = snapshot.get("previous_incumbent")
        previous_digest = _optional_string(
            snapshot.get("previous_incumbent_digest"),
            "previous_incumbent_digest",
        )
        last_rollback_digest = _optional_string(
            snapshot.get("last_rollback_digest"),
            "last_rollback_digest",
        )
        if (previous_payload is None) != (previous_digest is None):
            raise ValueError("previous incumbent and digest must be jointly present")
        previous = None if previous_payload is None else loader(_mapping(previous_payload, "previous_incumbent"))
        if previous is not None and cls.model_digest(previous) != previous_digest:
            raise ValueError("previous incumbent digest does not match model")
        if not legacy and isinstance(incumbent, ScheduledARX) and isinstance(previous, ScheduledARX):
            raise ValueError("active ARX rollback owner must be grey-box")
        if legacy and isinstance(incumbent, ScheduledARX) and not isinstance(challenger, ScheduledARX):
            # Before role ownership was explicit, an active ARX kept its
            # grey-box fallback in the challenger slot. Give the active ARX
            # an independently learnable clone and retain that fallback as the
            # one-shot rollback owner. Some oldest records had no separate
            # previous_incumbent field, so their challenger is the owner.
            if previous is None or isinstance(previous, ScheduledARX):
                previous = challenger
                previous_payload = cast(Mapping[str, object], _owned_json(challenger.snapshot()))
                previous_digest = cls.model_digest(previous)
            challenger = ScheduledARX.from_snapshot(_mapping(_owned_json(incumbent_payload), "legacy incumbent"))
        manager = cls(
            incumbent,
            challenger,
            policy,
            parameter_learning=parameter_learning,
            accepted_sources=tuple(sources),
        )
        manager._previous_incumbent = previous
        manager._previous_incumbent_snapshot = (
            None if previous_payload is None else cast(Mapping[str, object], _owned_json(previous_payload))
        )
        manager._previous_incumbent_digest = previous_digest
        manager._last_rollback_digest = last_rollback_digest
        manager._role_generation = _nonnegative(
            snapshot.get("role_generation"),
            "role_generation",
        )
        manager._effective_updates = _nonnegative(
            snapshot.get("effective_updates"),
            "effective_updates",
        )
        manager._consecutive_wins = _nonnegative(
            snapshot.get("consecutive_wins"),
            "consecutive_wins",
        )
        manager._lag_warmup_remaining = warmup
        manager._excitation.extend(excitation)
        manager._last_duty = _optional_float(snapshot.get("last_duty"), "last_duty")
        manager._last_evaluation_s = _optional_float(
            snapshot.get("last_evaluation_s"),
            "last_evaluation_s",
        )
        manager._scores = scores
        return manager

    @staticmethod
    def model_digest(model: AdaptiveModel) -> str:
        encoded = json.dumps(
            _owned_json(model.snapshot()), sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    def _hard_rejection(self, observation: FrameObservation, actuation_known: bool) -> UpdateRejectionReason | None:
        if observation.lid_open:
            return UpdateRejectionReason.LID_OPEN
        if observation.safety_inhibited:
            return UpdateRejectionReason.SAFETY
        if observation.manual_override:
            return UpdateRejectionReason.MANUAL
        if observation.stale:
            return UpdateRejectionReason.STALE
        if observation.skipped or observation.reset:
            return UpdateRejectionReason.SKIPPED_OR_RESET
        if observation.output_source not in self._accepted_sources:
            return UpdateRejectionReason.NON_CONTROLLER_SOURCE
        if not observation.continuous:
            return UpdateRejectionReason.DISCONTINUITY
        if not actuation_known:
            return UpdateRejectionReason.UNKNOWN_ACTUATION
        return None

    def _track_only(
        self,
        observation: FrameObservation,
        variance: float,
        levels: int,
        reason: UpdateRejectionReason,
        ambient_future: Sequence[float] | None,
        braking: bool,
    ) -> ObservationOutcome:
        incumbent, challenger = (self.incumbent.track(observation), self.challenger.track(observation))
        self._capture_origins(observation, ambient_future, braking)
        self._last_duty = observation.realized_q
        return ObservationOutcome(
            UpdateGate(False, (reason,), variance, levels), incumbent, challenger, self._effective_updates
        )

    def _reset_role_lag_history(self) -> None:
        """Rebuild both role histories before ARX-derived evidence can resume."""
        self._excitation.clear()
        self._last_duty = None
        self._lag_warmup_remaining = self.policy.max_delay_steps
        for model in (self.incumbent, self.challenger):
            reset = getattr(model, "reset_lag_history", None)
            if callable(reset):
                reset()

    def _mark_discontinuity(self) -> None:
        self._invalidate_origins()
        self._excitation.clear()
        self._lag_warmup_remaining = self.policy.max_delay_steps
        self._last_duty = None
        for model in (self.incumbent, self.challenger):
            reset = getattr(model, "reset_lag_history", None)
            if callable(reset):
                reset()

    def _invalidate_origins(self) -> None:
        self._origins.clear()
        self._scores.continuous = False
        self._pending.clear()

    def _begin_role_generation(self) -> None:
        """Discard old-role evidence while preserving continuous new-role scoring."""
        self._origins.clear()
        self._pending.clear()
        self._completed.clear()
        self._completed_window = ()
        self._scores.clear()

    def _capture_origins(
        self,
        observation: FrameObservation,
        ambient_future: Sequence[float] | None,
        braking: bool,
    ) -> None:
        if self._lag_warmup_remaining:
            return
        ambient = np.asarray(
            ambient_future if ambient_future is not None else [observation.ambient_c] * max(_HORIZONS),
            dtype=np.float64,
        )
        if ambient.shape != (max(_HORIZONS),) or not np.isfinite(ambient).all():
            raise ValueError("ambient_future must contain exactly 15 finite values")
        for horizon in _HORIZONS:
            try:
                incumbent = self.incumbent.affine_prediction(
                    horizon,
                    observation.realized_q,
                    ambient[:horizon],
                )
                challenger = self.challenger.affine_prediction(
                    horizon,
                    observation.realized_q,
                    ambient[:horizon],
                )
            except RuntimeError:
                continue
            duty = np.empty(horizon, dtype=np.float64)
            duty.setflags(write=False)
            self._origins.append(
                _Origin(
                    observation.frame_end_s,
                    self._role_generation,
                    observation.frame_end_s - observation.frame_start_s,
                    horizon,
                    incumbent,
                    challenger,
                    duty,
                    braking,
                )
            )
            if len(self._origins) > 2 * max(_HORIZONS):
                self._scores.continuous = False
                del self._origins[: len(self._origins) - 2 * max(_HORIZONS)]

    def _complete_origins(
        self,
        observation: FrameObservation,
        braking: bool,
    ) -> None:
        live: list[_Origin] = []
        interval_s = observation.frame_end_s - observation.frame_start_s
        for origin in self._origins:
            if origin.generation != self._role_generation or abs(interval_s - origin.interval_s) > 1e-6:
                self._scores.continuous = False
                continue
            step = round((observation.frame_end_s - origin.origin_time_s) / origin.interval_s)
            if step <= 0:
                live.append(origin)
                continue
            if (
                step > origin.horizon_steps
                or abs(origin.origin_time_s + step * origin.interval_s - observation.frame_end_s) > 1e-6
            ):
                self._scores.continuous = False
                continue
            duty = np.array(origin.duty, copy=True)
            duty[step - 1] = observation.realized_q
            duty.setflags(write=False)
            origin.duty = duty
            origin.braking = origin.braking or braking
            if step < origin.horizon_steps:
                live.append(origin)
                continue
            incumbent_prediction = float(
                origin.incumbent.free_output_c[-1] + origin.incumbent.input_response_c[-1] @ duty
            )
            challenger_prediction = float(
                origin.challenger.free_output_c[-1] + origin.challenger.input_response_c[-1] @ duty
            )
            incumbent_error = observation.temp_c - incumbent_prediction
            challenger_error = observation.temp_c - challenger_prediction
            if not all(isfinite(value) for value in (incumbent_error, challenger_error)):
                self._scores.continuous = False
                continue
            if len(self._completed) == self._completed.maxlen:
                expired = self._completed.popleft()
                self._scores.incumbent_prediction_error -= expired.incumbent_error_c * expired.incumbent_error_c
                self._scores.candidate_prediction_error -= expired.challenger_error_c * expired.challenger_error_c
                self._scores.prediction_count -= 1
                if expired.braking:
                    self._scores.incumbent_braking_error -= expired.incumbent_error_c * expired.incumbent_error_c
                    self._scores.candidate_braking_error -= expired.challenger_error_c * expired.challenger_error_c
                    self._scores.braking_count -= 1
            self._scores.add(incumbent_error, challenger_error, origin.braking)
            self._completed.append(
                CompletedOrigin(
                    origin.origin_time_s,
                    observation.frame_end_s,
                    origin.horizon_steps,
                    origin.generation,
                    observation.temp_c,
                    incumbent_error,
                    challenger_error,
                    origin.braking,
                )
            )
        self._origins = live

    def _evaluation_reasons(
        self,
        incumbent_prediction: float | None,
        candidate_prediction: float | None,
        incumbent_braking: float | None,
        candidate_braking: float | None,
        horizon_scores: Sequence[HorizonScore],
    ) -> list[EvaluationRejectionReason]:
        reasons: list[EvaluationRejectionReason] = []
        if (
            candidate_prediction is None
            or incumbent_prediction is None
            or not candidate_prediction < incumbent_prediction
            or any(
                score.incumbent_rmse_c is None
                or score.challenger_rmse_c is None
                or not score.challenger_rmse_c < score.incumbent_rmse_c
                for score in horizon_scores
            )
        ):
            reasons.append(EvaluationRejectionReason.PREDICTION)
        if (
            candidate_braking is not None
            and incumbent_braking is not None
            and candidate_braking > incumbent_braking + self.policy.braking_tolerance_c
        ):
            reasons.append(EvaluationRejectionReason.BRAKING)
        snapshot = self.challenger.snapshot()
        if not _stable(snapshot, self.policy.max_pole_magnitude):
            reasons.append(EvaluationRejectionReason.STABILITY)
        if not _positive_gain(snapshot):
            reasons.append(EvaluationRejectionReason.GAIN)
        if not _valid_delay(snapshot, self.policy.max_delay_steps):
            reasons.append(EvaluationRejectionReason.DELAY)
        if self._effective_updates < self.policy.min_effective_updates:
            reasons.append(EvaluationRejectionReason.SAMPLES)
        if not self._scores.continuous:
            reasons.append(EvaluationRejectionReason.CONTINUITY)
        return reasons


def _scheduled_arx_loader(snapshot: Mapping[str, object]) -> AdaptiveModel:
    if snapshot.get("schema") != "scheduled-arx/v2":
        raise ValueError("model_loader is required for a non-ScheduledARX snapshot")
    return ScheduledARX.from_snapshot(snapshot)


def _valid_prospective_solve(value: object) -> bool:
    objective = getattr(value, "objective", None)
    residual = getattr(value, "kkt_residual", None)
    return (
        not isinstance(objective, bool)
        and (not isinstance(residual, bool))
        and isinstance(objective, (int, float))
        and isinstance(residual, (int, float))
        and isfinite(float(objective))
        and isfinite(float(residual))
        and (float(residual) >= 0.0)
    )


def _excitation(inputs: Sequence[float]) -> tuple[float, int]:
    values = np.asarray(inputs, dtype=np.float64)
    return (float(np.var(values)) if values.size else 0.0, len(set((float(value) for value in values))))


def _means(
    incumbent: float,
    challenger: float,
    count: int,
) -> tuple[float | None, float | None]:
    if count == 0:
        return None, None
    return sqrt(incumbent / count), sqrt(challenger / count)


def _horizon_scores(completed: Sequence[CompletedOrigin]) -> tuple[HorizonScore, ...]:
    scores: list[HorizonScore] = []
    for horizon_steps in _HORIZONS:
        origins = tuple(origin for origin in completed if origin.horizon_steps == horizon_steps)
        incumbent_rmse_c, challenger_rmse_c = _means(
            sum(origin.incumbent_error_c * origin.incumbent_error_c for origin in origins),
            sum(origin.challenger_error_c * origin.challenger_error_c for origin in origins),
            len(origins),
        )
        scores.append(
            HorizonScore(
                horizon_steps,
                incumbent_rmse_c,
                challenger_rmse_c,
                len(origins),
            )
        )
    return tuple(scores)


def _score_aggregate(payload: Mapping[str, object]) -> _ScoreAggregate:
    required = {
        "incumbent_prediction_error",
        "candidate_prediction_error",
        "prediction_count",
        "incumbent_braking_error",
        "candidate_braking_error",
        "braking_count",
        "continuous",
    }
    if set(payload) != required:
        raise ValueError("score_aggregate fields are invalid")
    prediction_count = _nonnegative(
        payload["prediction_count"],
        "score_aggregate.prediction_count",
    )
    braking_count = _nonnegative(
        payload["braking_count"],
        "score_aggregate.braking_count",
    )
    if braking_count > prediction_count:
        raise ValueError("braking_count cannot exceed prediction_count")
    continuous = payload["continuous"]
    if not isinstance(continuous, bool):
        raise ValueError("score_aggregate.continuous must be a bool")
    return _ScoreAggregate(
        incumbent_prediction_error=_nonnegative_finite(
            payload["incumbent_prediction_error"],
            "score_aggregate.incumbent_prediction_error",
        ),
        candidate_prediction_error=_nonnegative_finite(
            payload["candidate_prediction_error"],
            "score_aggregate.candidate_prediction_error",
        ),
        prediction_count=prediction_count,
        incumbent_braking_error=_nonnegative_finite(
            payload["incumbent_braking_error"],
            "score_aggregate.incumbent_braking_error",
        ),
        candidate_braking_error=_nonnegative_finite(
            payload["candidate_braking_error"],
            "score_aggregate.candidate_braking_error",
        ),
        braking_count=braking_count,
        continuous=continuous,
    )


def _nonnegative_finite(value: object, name: str) -> float:
    result = _finite(value, name)
    if result < 0.0:
        raise ValueError(f"{name} must be non-negative")
    return result


def _stable(snapshot: Mapping[str, object], maximum: float) -> bool:
    poles = _values(snapshot, "poles")
    return bool(poles) and all((abs(value) <= maximum for value in poles))


def _positive_gain(snapshot: Mapping[str, object]) -> bool:
    gains = _values(snapshot, "steady_gain")
    return bool(gains) and all((value > 0.0 for value in gains))


def _valid_delay(snapshot: Mapping[str, object], maximum: int) -> bool:
    delays = _values(snapshot, "active_delay") + _values(snapshot, "delay_steps")
    return bool(delays) and all((0.0 <= delay <= maximum and delay.is_integer() for delay in delays))


def _sufficient_model_samples(snapshot: Mapping[str, object], minimum: int) -> bool:
    samples = _values(snapshot, "effective_samples")
    return bool(samples) and max(samples) >= minimum


def _values(value: object, key: str) -> list[float]:
    if isinstance(value, Mapping):
        values = _numeric(value.get(key))
        for nested in value.values():
            values.extend(_values(nested, key))
        return values
    if isinstance(value, (list, tuple)):
        return [item for nested in value for item in _values(nested, key)]
    return []


def _numeric(value: object) -> list[float]:
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)) and isfinite(float(value)):
        return [float(value)]
    if isinstance(value, (list, tuple)):
        return [item for nested in value for item in _numeric(nested)]
    return []


def _owned_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _owned_json(nested) for key, nested in value.items()}
    if isinstance(value, np.ndarray):
        return [_owned_json(nested) for nested in value.tolist()]
    if isinstance(value, (list, tuple)):
        return [_owned_json(nested) for nested in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all((isinstance(key, str) for key in value)):
        raise ValueError(f"{name} must be an object")
    return MappingProxyType(dict(value))


def _sequence(value: object, name: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be an array")
    return value


def _strings(value: object, name: str) -> list[str]:
    values = list(_sequence(value, name))
    if any(not isinstance(item, str) or not item for item in values):
        raise ValueError(f"{name} must contain only non-empty strings")
    return values


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or (not isfinite(float(value))):
        raise ValueError(f"{name} must be finite")
    return float(value)


def _optional_float(value: object, name: str) -> float | None:
    return None if value is None else _finite(value, name)


def _optional_string(value: object, name: str) -> str | None:
    if value is not None and (not isinstance(value, str) or not value):
        raise ValueError(f"{name} must be a non-empty string or null")
    return value


def _nonnegative(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value
