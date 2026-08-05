"""Immutable, deterministic evidence documents and bake-off recommendations."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from math import isfinite
from types import MappingProxyType
from typing import Any

SCHEMA_VERSION = 1
_HARD_FAILURE_REASONS = {
    "leakage": "leakage",
    "wrong-input-semantics": "wrong input semantics",
    "non-finite/unstable": "non-finite/unstable behavior",
    "irreproducible": "irreproducibility",
}
_COMPLEXITY = {"scheduled-arx": 0, "dmc": 1, "state-space": 2}


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(value[key]) for key in sorted(value, key=str)})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"artifact values must be JSON-compatible, not {type(value).__name__}")


def _document(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _document(value[key]) for key in sorted(value)}
    if isinstance(value, tuple):
        return [_document(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class MatrixKey:
    """The immutable identity of one matrix cell, including failed cells."""

    arm: str
    initialization: str
    plant: str
    mode: str
    scenario: str
    seed: int

    def to_document(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "initialization": self.initialization,
            "mode": self.mode,
            "plant": self.plant,
            "scenario": self.scenario,
            "seed": self.seed,
        }


@dataclass(frozen=True, slots=True)
class ArmFailure:
    """A structured failure retained as evidence instead of being silently dropped."""

    arm: str
    scenario: str
    category: str
    detail: str
    matrix_key: MatrixKey | None = None

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value for value in (self.arm, self.scenario, self.category, self.detail)):
            raise ValueError("failure fields must be non-empty strings")
        if self.matrix_key is not None and (self.matrix_key.arm != self.arm or self.matrix_key.scenario != self.scenario):
            raise ValueError("failure matrix key must match arm and scenario")

    def to_document(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "arm": self.arm,
            "category": self.category,
            "detail": self.detail,
            "scenario": self.scenario,
        }
        if self.matrix_key is not None:
            document["matrix_key"] = self.matrix_key.to_document()
        return document


@dataclass(frozen=True, slots=True)
class ArmEvidence:
    """Aggregated evidence needed to compare one model arm fairly."""

    name: str
    domain_median_scores: Mapping[str, float]
    prediction_error: float
    wrong_model_recovery: float
    raw_solve_p99_ms: float
    projected_solve_p99_ms: float = -1.0
    raw_learner_p99_ms: float = 0.0
    raw_refresh_p99_ms: float = 0.0
    raw_learner_ms: Sequence[float] = ()
    raw_refresh_ms: Sequence[float] = ()
    raw_solve_ms: Sequence[float] = ()

    def __post_init__(self) -> None:
        if self.name not in _COMPLEXITY:
            raise ValueError(f"unknown arm {self.name!r}")
        scores = {str(domain): float(score) for domain, score in self.domain_median_scores.items()}
        if not scores or not all(isfinite(score) and score >= 0.0 for score in scores.values()):
            raise ValueError("domain scores must be finite and non-negative")
        distributions = {
            "raw_learner_ms": tuple(float(value) for value in self.raw_learner_ms),
            "raw_refresh_ms": tuple(float(value) for value in self.raw_refresh_ms),
            "raw_solve_ms": tuple(float(value) for value in self.raw_solve_ms),
        }
        if any(not isfinite(value) or value < 0.0 for values in distributions.values() for value in values):
            raise ValueError("raw timing distributions must be finite and non-negative")
        for name, values in distributions.items():
            object.__setattr__(self, name, values)
        learner_p99 = _p99(distributions["raw_learner_ms"]) if distributions["raw_learner_ms"] else float(self.raw_learner_p99_ms)
        refresh_p99 = _p99(distributions["raw_refresh_ms"]) if distributions["raw_refresh_ms"] else float(self.raw_refresh_p99_ms)
        solve_p99 = _p99(distributions["raw_solve_ms"]) if distributions["raw_solve_ms"] else float(self.raw_solve_p99_ms)
        values = (self.prediction_error, self.wrong_model_recovery, learner_p99, refresh_p99, solve_p99)
        if not all(isfinite(float(value)) and float(value) >= 0.0 for value in values):
            raise ValueError("arm evidence values must be finite and non-negative")
        projected = solve_p99 * 5.0 if self.projected_solve_p99_ms < 0.0 else self.projected_solve_p99_ms
        if not isfinite(float(projected)) or float(projected) < 0.0:
            raise ValueError("projected solve timing must be finite and non-negative")
        object.__setattr__(self, "domain_median_scores", MappingProxyType(dict(sorted(scores.items()))))
        object.__setattr__(self, "prediction_error", float(self.prediction_error))
        object.__setattr__(self, "wrong_model_recovery", float(self.wrong_model_recovery))
        object.__setattr__(self, "raw_learner_p99_ms", learner_p99)
        object.__setattr__(self, "raw_refresh_p99_ms", refresh_p99)
        object.__setattr__(self, "raw_solve_p99_ms", solve_p99)
        object.__setattr__(self, "projected_solve_p99_ms", float(projected))

    @property
    def worst_domain_score(self) -> float:
        return max(self.domain_median_scores.values())

    def to_document(self) -> dict[str, Any]:
        raw_timing = {
            "learner": list(self.raw_learner_ms),
            "learner_p99": self.raw_learner_p99_ms,
            "refresh": list(self.raw_refresh_ms),
            "refresh_p99": self.raw_refresh_p99_ms,
            "solve": list(self.raw_solve_ms),
            "solve_p99": self.raw_solve_p99_ms,
        }
        return {
            "domain_median_control_scores": _document(self.domain_median_scores),
            "prediction_error": self.prediction_error,
            "projected_solve_p99_ms": self.projected_solve_p99_ms,
            "projected_timing_ms": {
                "learner": [value * 5.0 for value in self.raw_learner_ms],
                "learner_p99": self.raw_learner_p99_ms * 5.0,
                "refresh": [value * 5.0 for value in self.raw_refresh_ms],
                "refresh_p99": self.raw_refresh_p99_ms * 5.0,
                "solve": [value * 5.0 for value in self.raw_solve_ms],
                "solve_p99": self.projected_solve_p99_ms,
            },
            "raw_solve_p99_ms": self.raw_solve_p99_ms,
            "raw_timing_ms": raw_timing,
            "wrong_model_recovery": self.wrong_model_recovery,
        }


@dataclass(frozen=True, slots=True)
class ExperimentArtifact:
    """Schema-versioned experiment evidence with reproducible serialization."""

    config: Mapping[str, Any]
    seeds: Sequence[int]
    splits: Mapping[str, Any]
    model_snapshots: Mapping[str, Any]
    scenarios: Sequence[Any]
    arms: Sequence[ArmEvidence]
    source_revision: str
    environment: Mapping[str, str]
    failures: Sequence[ArmFailure] = ()
    horizon_evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source_revision:
            raise ValueError("source_revision is required")
        arms = tuple(sorted(self.arms, key=lambda arm: arm.name))
        if len({arm.name for arm in arms}) != len(arms):
            raise ValueError("each arm may occur only once")
        if not all(isinstance(seed, int) for seed in self.seeds):
            raise ValueError("seeds must be integers")
        object.__setattr__(self, "config", _freeze(self.config))
        object.__setattr__(self, "seeds", tuple(sorted(self.seeds)))
        object.__setattr__(self, "splits", _freeze(self.splits))
        object.__setattr__(self, "model_snapshots", _freeze(self.model_snapshots))
        object.__setattr__(self, "scenarios", tuple(sorted(self.scenarios, key=_scenario_sort_key)))
        object.__setattr__(self, "arms", arms)
        object.__setattr__(self, "environment", _freeze(self.environment))
        object.__setattr__(self, "failures", tuple(sorted(self.failures, key=lambda item: (item.arm, item.scenario, item.category, item.detail))))
        object.__setattr__(self, "horizon_evidence", _freeze(self.horizon_evidence))

    def with_failures(self, failures: Sequence[ArmFailure]) -> "ExperimentArtifact":
        return replace(self, failures=tuple(failures))

    def with_horizon_evidence(self, horizon_evidence: Mapping[str, Any]) -> "ExperimentArtifact":
        return replace(self, horizon_evidence=horizon_evidence)

    def to_document(self) -> dict[str, Any]:
        return {
            "arms": {arm.name: arm.to_document() for arm in self.arms},
            "config": _document(self.config),
            "environment": _document(self.environment),
            "failures": [failure.to_document() for failure in self.failures],
            "horizon_evidence": _normalize_horizon_document(self.horizon_evidence),
            "model_snapshots": _document(self.model_snapshots),
            "scenarios": [_scenario_document(scenario) for scenario in self.scenarios],
            "schema_version": SCHEMA_VERSION,
            "seeds": list(self.seeds),
            "source_revision": self.source_revision,
            "splits": _document(self.splits),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_document(), allow_nan=False, sort_keys=True, separators=(",", ":"))

    def canonical_document(self) -> dict[str, Any]:
        """Return deterministic scientific evidence while retaining timing evidence in ``to_document``."""
        document = self.to_document()
        for row in document["scenarios"]:
            row.pop("raw_timing_ms", None)
            for name in ("raw_learner_p99_ms", "raw_refresh_p99_ms", "raw_solve_p99_ms"):
                row["metrics"].pop(name, None)
        for arm in document["arms"].values():
            arm.pop("raw_timing_ms", None)
            arm.pop("projected_timing_ms", None)
            arm.pop("raw_solve_p99_ms", None)
            arm.pop("projected_solve_p99_ms", None)
        return document


@dataclass(frozen=True, slots=True)
class ArmRecommendation:
    valid: bool
    reasons: tuple[str, ...]
    worst_domain_score: float


@dataclass(frozen=True, slots=True)
class Recommendation:
    arms: Mapping[str, ArmRecommendation]
    selected_arm: str | None
    pareto_frontier: tuple[str, ...]


def recommend(artifact: ExperimentArtifact) -> Recommendation:
    """Apply the published validity, ranking, simplicity, and Pareto hierarchy."""
    budget = float(artifact.config.get("control_budget_ms", 50.0))
    if not isfinite(budget) or budget <= 0.0:
        raise ValueError("control_budget_ms must be finite and positive")
    failures_by_arm: dict[str, list[str]] = {}
    for failure in artifact.failures:
        reason = _HARD_FAILURE_REASONS.get(failure.category)
        if reason is not None:
            failures_by_arm.setdefault(failure.arm, []).append(reason)
    recommendations: dict[str, ArmRecommendation] = {}
    valid_evidence: list[ArmEvidence] = []
    for evidence in artifact.arms:
        reasons = sorted(set(failures_by_arm.get(evidence.name, ())))
        if (
            evidence.raw_learner_p99_ms * 5.0 > 25.0
            or evidence.raw_refresh_p99_ms * 5.0 > 1_250.0
            or evidence.projected_solve_p99_ms > min(budget * 5.0, 250.0)
        ):
            reasons.append("runtime beyond hard limits")
        reasons = sorted(set(reasons))
        valid = not reasons
        recommendations[evidence.name] = ArmRecommendation(valid, tuple(reasons), evidence.worst_domain_score)
        if valid:
            valid_evidence.append(evidence)
    frontier = _pareto_frontier(valid_evidence)
    selected: str | None = None
    if valid_evidence and not _material_pareto_conflict(frontier):
        best_score = min(item.worst_domain_score for item in valid_evidence)
        contenders = [item for item in valid_evidence if item.worst_domain_score <= best_score * 1.05]
        selected = min(
            contenders,
            key=lambda item: (_COMPLEXITY[item.name], item.prediction_error, item.wrong_model_recovery, item.projected_solve_p99_ms, item.name),
        ).name
    return Recommendation(MappingProxyType(dict(sorted(recommendations.items()))), selected, tuple(item.name for item in frontier))


def render_table(artifact: ExperimentArtifact, recommendation: Recommendation | None = None) -> str:
    """Render a concise deterministic comparison suitable for the command line."""
    recommendation = recommend(artifact) if recommendation is None else recommendation
    lines = ["arm              valid  worst-score  projected-p99-ms  reasons"]
    for evidence in artifact.arms:
        result = recommendation.arms[evidence.name]
        reasons = ", ".join(result.reasons) or "-"
        lines.append(
            f"{evidence.name:<17}{str(result.valid):<7}{evidence.worst_domain_score:<13.3f}"
            f"{evidence.projected_solve_p99_ms:<18.3f}{reasons}"
        )
    lines.append(f"selected: {recommendation.selected_arm or 'none'}")
    lines.append(f"pareto: {', '.join(recommendation.pareto_frontier) or 'none'}")
    return "\n".join(lines)


def _pareto_frontier(evidence: Sequence[ArmEvidence]) -> tuple[ArmEvidence, ...]:
    if not evidence:
        return ()
    scales = tuple(
        max(min(values), 1e-12)
        for values in zip(*(_pareto_values(item) for item in evidence))
    )
    return tuple(
        item
        for item in sorted(evidence, key=lambda value: value.name)
        if not any(_dominates(other, item, scales) for other in evidence if other is not item)
    )


def _pareto_values(evidence: ArmEvidence) -> tuple[float, float, float, float]:
    return (
        evidence.worst_domain_score,
        evidence.prediction_error,
        evidence.wrong_model_recovery,
        evidence.projected_solve_p99_ms,
    )


def _dominates(left: ArmEvidence, right: ArmEvidence, scales: Sequence[float]) -> bool:
    normalized_left = tuple(value / scale for value, scale in zip(_pareto_values(left), scales))
    normalized_right = tuple(value / scale for value, scale in zip(_pareto_values(right), scales))
    return all(a <= b for a, b in zip(normalized_left, normalized_right)) and any(
        a < b * 0.95 for a, b in zip(normalized_left, normalized_right) if b > 0.0
    )


def _material_pareto_conflict(frontier: Sequence[ArmEvidence]) -> bool:
    if len(frontier) < 2:
        return False
    for values in zip(*(_pareto_values(item) for item in frontier)):
        scale = max(1e-12, max(abs(value) for value in values))
        if (max(values) - min(values)) / scale > 0.05:
            return True
    return False




def _p99(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * 0.99
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _scenario_sort_key(value: Any) -> tuple[str, str, str, int]:
    return (str(getattr(value, "plant", "")), str(getattr(value, "scenario", "")), str(getattr(value, "mode", "")), int(getattr(value, "seed", 0)))


def _scenario_document(value: Any) -> Any:
    return value.to_document() if hasattr(value, "to_document") else _document(value)


def _normalize_horizon_document(value: Mapping[str, Any]) -> dict[str, Any]:
    document = _document(value)
    for arm_values in document.values():
        if isinstance(arm_values, dict):
            for horizon, evidence in list(arm_values.items()):
                if isinstance(evidence, list) and len(evidence) == 2:
                    arm_values[horizon] = {"bootstrap_ci": evidence}
    return document
