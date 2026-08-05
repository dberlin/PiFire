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
class ArmFailure:
    """A structured failure retained as evidence instead of being silently dropped."""

    arm: str
    scenario: str
    category: str
    detail: str

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value for value in (self.arm, self.scenario, self.category, self.detail)):
            raise ValueError("failure fields must be non-empty strings")

    def to_document(self) -> dict[str, str]:
        return {"arm": self.arm, "category": self.category, "detail": self.detail, "scenario": self.scenario}


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

    def __post_init__(self) -> None:
        if self.name not in _COMPLEXITY:
            raise ValueError(f"unknown arm {self.name!r}")
        scores = {str(domain): float(score) for domain, score in self.domain_median_scores.items()}
        if not scores or not all(isfinite(score) and score >= 0.0 for score in scores.values()):
            raise ValueError("domain scores must be finite and non-negative")
        values = (self.prediction_error, self.wrong_model_recovery, self.raw_solve_p99_ms)
        if not all(isfinite(float(value)) and float(value) >= 0.0 for value in values):
            raise ValueError("arm evidence values must be finite and non-negative")
        projected = self.raw_solve_p99_ms * 5.0 if self.projected_solve_p99_ms < 0.0 else self.projected_solve_p99_ms
        if not isfinite(float(projected)) or float(projected) < 0.0:
            raise ValueError("projected solve timing must be finite and non-negative")
        object.__setattr__(self, "domain_median_scores", MappingProxyType(dict(sorted(scores.items()))))
        object.__setattr__(self, "prediction_error", float(self.prediction_error))
        object.__setattr__(self, "wrong_model_recovery", float(self.wrong_model_recovery))
        object.__setattr__(self, "raw_solve_p99_ms", float(self.raw_solve_p99_ms))
        object.__setattr__(self, "projected_solve_p99_ms", float(projected))

    @property
    def worst_domain_score(self) -> float:
        return max(self.domain_median_scores.values())

    def to_document(self) -> dict[str, Any]:
        return {
            "domain_median_control_scores": _document(self.domain_median_scores),
            "prediction_error": self.prediction_error,
            "projected_solve_p99_ms": self.projected_solve_p99_ms,
            "raw_solve_p99_ms": self.raw_solve_p99_ms,
            "wrong_model_recovery": self.wrong_model_recovery,
            "raw_learner_p99_ms": self.raw_learner_p99_ms,
            "raw_refresh_p99_ms": self.raw_refresh_p99_ms,
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
        if evidence.projected_solve_p99_ms > budget * 5.0:
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
        lines.append(f"{evidence.name:<17}{str(result.valid):<7}{evidence.worst_domain_score:<13.3f}{evidence.projected_solve_p99_ms:<18.3f}{reasons}")
    lines.append(f"selected: {recommendation.selected_arm or 'none'}")
    lines.append(f"pareto: {', '.join(recommendation.pareto_frontier) or 'none'}")
    return "\n".join(lines)


def _pareto_frontier(evidence: Sequence[ArmEvidence]) -> tuple[ArmEvidence, ...]:
    return tuple(item for item in sorted(evidence, key=lambda value: value.name) if not any(_dominates(other, item) for other in evidence if other is not item))


def _dominates(left: ArmEvidence, right: ArmEvidence) -> bool:
    left_values = (left.worst_domain_score, left.prediction_error, left.wrong_model_recovery, left.projected_solve_p99_ms)
    right_values = (right.worst_domain_score, right.prediction_error, right.wrong_model_recovery, right.projected_solve_p99_ms)
    return all(a <= b for a, b in zip(left_values, right_values, strict=True)) and any(a < b * 0.95 for a, b in zip(left_values, right_values, strict=True) if b > 0.0)


def _material_pareto_conflict(frontier: Sequence[ArmEvidence]) -> bool:
    if len(frontier) < 2:
        return False
    scores = [item.worst_domain_score for item in frontier]
    return max(scores) > min(scores) * 1.05


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
