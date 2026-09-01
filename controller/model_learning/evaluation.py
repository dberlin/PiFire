"""Pure causal evaluation of immutable incumbent and challenger grey forecasts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from math import isfinite, sqrt

from common.control_trace import AmbientSource

from .contracts import FrameObservation

_REQUIRED_HORIZONS = (3, 15, 45, 90, 180)


def _generation(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value)):
        raise ValueError(f"{name} must be finite")
    return float(value)


def _digest(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


@dataclass(frozen=True, slots=True)
class ForecastOrigin:
    """One pair of grey forecasts frozen before any future observation exists."""

    origin_sequence: int
    origin_time_s: float
    horizon_steps: int
    role_generation: int
    candidate_generation: int
    incumbent_digest: str
    challenger_digest: str
    incumbent_prediction_c: float
    challenger_prediction_c: float
    temperature_band: str
    phase: str
    ambient_source: AmbientSource
    calibration_fit: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "origin_sequence", _generation(self.origin_sequence, "origin_sequence"))
        object.__setattr__(self, "origin_time_s", _finite(self.origin_time_s, "origin_time_s"))
        if isinstance(self.horizon_steps, bool) or self.horizon_steps not in _REQUIRED_HORIZONS:
            raise ValueError(f"horizon_steps must be one of {_REQUIRED_HORIZONS}")
        object.__setattr__(self, "role_generation", _generation(self.role_generation, "role_generation"))
        object.__setattr__(
            self,
            "candidate_generation",
            _generation(self.candidate_generation, "candidate_generation"),
        )
        _digest(self.incumbent_digest, "incumbent_digest")
        _digest(self.challenger_digest, "challenger_digest")
        object.__setattr__(
            self,
            "incumbent_prediction_c",
            _finite(self.incumbent_prediction_c, "incumbent_prediction_c"),
        )
        object.__setattr__(
            self,
            "challenger_prediction_c",
            _finite(self.challenger_prediction_c, "challenger_prediction_c"),
        )
        for name in ("temperature_band", "phase"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-blank")
        if not isinstance(self.ambient_source, AmbientSource):
            raise TypeError("ambient_source must be an AmbientSource")
        if not isinstance(self.calibration_fit, bool):
            raise TypeError("calibration_fit must be a bool")


@dataclass(frozen=True, slots=True)
class CompletedForecastOrigin:
    """A forecast origin joined to its exact completed future observation."""

    forecast: ForecastOrigin
    completion_time_s: float
    observed_temperature_c: float

    def __post_init__(self) -> None:
        if not isinstance(self.forecast, ForecastOrigin):
            raise TypeError("forecast must be a ForecastOrigin")
        object.__setattr__(self, "completion_time_s", _finite(self.completion_time_s, "completion_time_s"))
        if self.completion_time_s <= self.forecast.origin_time_s:
            raise ValueError("completion_time_s must follow the forecast origin")
        object.__setattr__(
            self,
            "observed_temperature_c",
            _finite(self.observed_temperature_c, "observed_temperature_c"),
        )

    @property
    def origin_sequence(self) -> int:
        return self.forecast.origin_sequence

    @property
    def horizon_steps(self) -> int:
        return self.forecast.horizon_steps

    @property
    def role_generation(self) -> int:
        return self.forecast.role_generation

    @property
    def candidate_generation(self) -> int:
        return self.forecast.candidate_generation

    @property
    def incumbent_digest(self) -> str:
        return self.forecast.incumbent_digest

    @property
    def challenger_digest(self) -> str:
        return self.forecast.challenger_digest

    @property
    def temperature_band(self) -> str:
        return self.forecast.temperature_band

    @property
    def phase(self) -> str:
        return self.forecast.phase

    @property
    def ambient_source(self) -> AmbientSource:
        return self.forecast.ambient_source

    @property
    def calibration_fit(self) -> bool:
        return self.forecast.calibration_fit

    @property
    def incumbent_error_c(self) -> float:
        return self.observed_temperature_c - self.forecast.incumbent_prediction_c

    @property
    def challenger_error_c(self) -> float:
        return self.observed_temperature_c - self.forecast.challenger_prediction_c


@dataclass(frozen=True, slots=True)
class EvaluationConfig:
    required_horizons: tuple[int, ...] = _REQUIRED_HORIZONS
    required_consecutive_wins: int = 2

    def __post_init__(self) -> None:
        horizons = tuple(self.required_horizons)
        if horizons != _REQUIRED_HORIZONS:
            raise ValueError(f"required_horizons must be {_REQUIRED_HORIZONS}")
        if (
            isinstance(self.required_consecutive_wins, bool)
            or not isinstance(self.required_consecutive_wins, int)
            or self.required_consecutive_wins < 1
        ):
            raise ValueError("required_consecutive_wins must be a positive integer")
        object.__setattr__(self, "required_horizons", horizons)


@dataclass(frozen=True, slots=True)
class HorizonScore:
    horizon_steps: int
    incumbent_rmse_c: float
    challenger_rmse_c: float
    sample_count: int


@dataclass(frozen=True, slots=True)
class EvaluationDecision:
    decision_id: str
    accepted: bool
    role_generation: int
    candidate_generation: int
    incumbent_digest: str
    challenger_digest: str
    scores: tuple[HorizonScore, ...]
    consecutive_wins: int
    blockers: tuple[str, ...]
    completed_origins: tuple[CompletedForecastOrigin, ...] = ()

    @property
    def completed_horizons(self) -> tuple[int, ...]:
        """Horizons backed by observations in this complete causal round."""

        return tuple(score.horizon_steps for score in self.scores if score.sample_count > 0)


class CausalForecastEvaluator:
    """Join frozen forecasts to exact future frames without owning either model."""

    def __init__(self, *, role_generation: int, candidate_generation: int) -> None:
        self._role_generation = _generation(role_generation, "role_generation")
        self._candidate_generation = _generation(candidate_generation, "candidate_generation")
        self._pending: list[ForecastOrigin] = []
        self._completed: list[CompletedForecastOrigin] = []
        self._next_sequence: dict[ForecastOrigin, int] = {}

    @property
    def pending_origins(self) -> tuple[ForecastOrigin, ...]:
        return tuple(self._pending)

    @property
    def completed_origins(self) -> tuple[CompletedForecastOrigin, ...]:
        return tuple(self._completed)

    def set_generations(self, *, role_generation: int, candidate_generation: int) -> None:
        role = _generation(role_generation, "role_generation")
        candidate = _generation(candidate_generation, "candidate_generation")
        if (role, candidate) != (self._role_generation, self._candidate_generation):
            self._pending.clear()
            self._completed.clear()
            self._next_sequence.clear()
        self._role_generation = role
        self._candidate_generation = candidate

    def register(self, origin: ForecastOrigin) -> None:
        if not isinstance(origin, ForecastOrigin):
            raise TypeError("origin must be a ForecastOrigin")
        if (origin.role_generation, origin.candidate_generation) != (
            self._role_generation,
            self._candidate_generation,
        ):
            raise ValueError("forecast generation does not match evaluator generation")
        if origin.calibration_fit:
            raise ValueError("probe frames are forbidden as causal forecast origins")
        key = (origin.origin_sequence, origin.horizon_steps)
        if any((item.origin_sequence, item.horizon_steps) == key for item in self._pending):
            raise ValueError("duplicate causal forecast origin")
        self._pending.append(origin)
        self._next_sequence[origin] = origin.origin_sequence + 1

    def observe(self, observation: FrameObservation) -> tuple[CompletedForecastOrigin, ...]:
        if not isinstance(observation, FrameObservation):
            raise TypeError("observation must be a FrameObservation")
        newly_completed: list[CompletedForecastOrigin] = []
        survivors: list[ForecastOrigin] = []
        for origin in self._pending:
            sequence = observation.observation_sequence
            target = origin.origin_sequence + origin.horizon_steps
            if sequence <= origin.origin_sequence:
                survivors.append(origin)
                continue
            expected = self._next_sequence[origin]
            if sequence < expected:
                survivors.append(origin)
                continue
            valid = (
                sequence == expected
                and observation.role_generation == origin.role_generation
                and observation.continuous
                and not observation.calibration_fit
            )
            if not valid:
                self._next_sequence.pop(origin, None)
                continue
            if sequence < target:
                self._next_sequence[origin] = sequence + 1
                survivors.append(origin)
                continue
            if sequence == target:
                completed = CompletedForecastOrigin(
                    forecast=origin,
                    completion_time_s=observation.frame_end_s,
                    observed_temperature_c=observation.temp_c,
                )
                self._completed.append(completed)
                newly_completed.append(completed)
            self._next_sequence.pop(origin, None)
            # An observation after the target or a missed sequence invalidates
            # the exact causal join rather than relabeling a later frame.
        self._pending = survivors
        return tuple(newly_completed)


def evaluate_forecasts(
    completed: tuple[CompletedForecastOrigin, ...],
    *,
    role_generation: int,
    candidate_generation: int,
    prior_consecutive_wins: int,
    config: EvaluationConfig,
) -> EvaluationDecision:
    """Score one complete causal window without changing runtime ownership."""

    role = _generation(role_generation, "role_generation")
    candidate = _generation(candidate_generation, "candidate_generation")
    prior = _generation(prior_consecutive_wins, "prior_consecutive_wins")
    if not isinstance(config, EvaluationConfig):
        raise TypeError("config must be an EvaluationConfig")
    rows = tuple(completed)
    if not rows:
        raise ValueError("completed forecast window must not be empty")
    if not all(isinstance(row, CompletedForecastOrigin) for row in rows):
        raise TypeError("completed forecast window must contain CompletedForecastOrigin values")
    if any((row.role_generation, row.candidate_generation) != (role, candidate) for row in rows):
        raise ValueError("forecast generation does not match requested generation")
    incumbent_digests = {row.incumbent_digest for row in rows}
    challenger_digests = {row.challenger_digest for row in rows}
    if len(incumbent_digests) != 1 or len(challenger_digests) != 1:
        raise ValueError("forecast digest lineage is not unique")

    scores: list[HorizonScore] = []
    blockers: list[str] = []
    for horizon in config.required_horizons:
        horizon_rows = tuple(row for row in rows if row.horizon_steps == horizon)
        if not horizon_rows:
            scores.append(HorizonScore(horizon, 0.0, 0.0, 0))
            blockers.append(f"missing-horizon-{horizon}")
            continue
        incumbent_rmse = sqrt(sum(row.incumbent_error_c**2 for row in horizon_rows) / len(horizon_rows))
        challenger_rmse = sqrt(sum(row.challenger_error_c**2 for row in horizon_rows) / len(horizon_rows))
        scores.append(HorizonScore(horizon, incumbent_rmse, challenger_rmse, len(horizon_rows)))
        if challenger_rmse >= incumbent_rmse:
            blockers.append(f"challenger-horizon-{horizon}")

    wins = prior + 1 if not blockers else 0
    accepted = not blockers and wins >= config.required_consecutive_wins
    incumbent_digest = next(iter(incumbent_digests))
    challenger_digest = next(iter(challenger_digests))
    identity = {
        "role_generation": role,
        "candidate_generation": candidate,
        "incumbent_digest": incumbent_digest,
        "challenger_digest": challenger_digest,
        "scores": [
            score.__dict__
            if hasattr(score, "__dict__")
            else [score.horizon_steps, score.incumbent_rmse_c, score.challenger_rmse_c, score.sample_count]
            for score in scores
        ],
        "consecutive_wins": wins,
    }
    decision_id = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return EvaluationDecision(
        decision_id=decision_id,
        accepted=accepted,
        role_generation=role,
        candidate_generation=candidate,
        incumbent_digest=incumbent_digest,
        challenger_digest=challenger_digest,
        scores=tuple(scores),
        consecutive_wins=wins,
        blockers=tuple(blockers),
        completed_origins=rows,
    )
