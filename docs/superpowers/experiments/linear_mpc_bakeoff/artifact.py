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
    mpc_horizon_s: int = 600

    def to_document(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "initialization": self.initialization,
            "mode": self.mode,
            "plant": self.plant,
            "scenario": self.scenario,
            "seed": self.seed,
            "mpc_horizon_s": self.mpc_horizon_s,
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
    before_mae: float
    after_mae: float
    recovery_improvement_ratio: float
    recovery_improvement_delta: float
    raw_solve_p99_ms: float
    recovery_available: bool = False
    projected_solve_p99_ms: float = -1.0
    raw_learner_p99_ms: float = 0.0
    raw_refresh_p99_ms: float = 0.0
    raw_learner_ms: Sequence[float] = ()
    raw_refresh_ms: Sequence[float] = ()
    raw_solve_ms: Sequence[float] = ()
    ranking_domain_scores: Mapping[str, float] = field(default_factory=dict)
    correct_baseline_no_degradation: bool = True
    runtime_validity: str = "measured"
    simulator_diagnostics: Mapping[str, Any] = field(default_factory=dict)
    simulator_diagnostics_available: bool = False
    simulator_diagnostics_valid: bool = True
    simulator_gain_error_c_per_q: float = 0.0
    simulator_delay_error_s: float = 0.0
    simulator_coast_braking_error_c: float = 0.0
    target_missed: bool = False
    operational_consequence: str | None = None

    def __post_init__(self) -> None:
        if self.name not in _COMPLEXITY:
            raise ValueError(f"unknown arm {self.name!r}")
        scores = {str(domain): float(score) for domain, score in self.domain_median_scores.items()}
        ranking_scores = {
            str(domain): float(score) for domain, score in self.ranking_domain_scores.items()
        }
        if not ranking_scores:
            ranking_scores = scores
        if not scores or not all(isfinite(score) and score >= 0.0 for score in scores.values()):
            raise ValueError("domain scores must be finite and non-negative")
        if not all(isfinite(score) and score >= 0.0 for score in ranking_scores.values()):
            raise ValueError("ranking domain scores must be finite and non-negative")
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
        values = (
            self.prediction_error,
            self.before_mae,
            self.after_mae,
            self.recovery_improvement_ratio,
            learner_p99,
            refresh_p99,
            solve_p99,
        )
        diagnostic_values = (
            self.simulator_gain_error_c_per_q,
            self.simulator_delay_error_s,
            self.simulator_coast_braking_error_c,
        )
        if not all(isfinite(float(value)) and float(value) >= 0.0 for value in diagnostic_values):
            raise ValueError("simulator diagnostic values must be finite and non-negative")
        if not all(isfinite(float(value)) and float(value) >= 0.0 for value in values):
            raise ValueError("arm evidence values must be finite and non-negative")
        if not isfinite(float(self.recovery_improvement_delta)):
            raise ValueError("recovery improvement delta must be finite")
        projected = solve_p99 * 5.0 if self.projected_solve_p99_ms < 0.0 else self.projected_solve_p99_ms
        if not isfinite(float(projected)) or float(projected) < 0.0:
            raise ValueError("projected solve timing must be finite and non-negative")
        if self.runtime_validity not in {"measured", "not_measured"}:
            raise ValueError("runtime_validity must be measured or not_measured")
        object.__setattr__(self, "domain_median_scores", MappingProxyType(dict(sorted(scores.items()))))
        object.__setattr__(self, "ranking_domain_scores", MappingProxyType(dict(sorted(ranking_scores.items()))))
        object.__setattr__(self, "prediction_error", float(self.prediction_error))
        object.__setattr__(self, "before_mae", float(self.before_mae))
        object.__setattr__(self, "after_mae", float(self.after_mae))
        object.__setattr__(self, "recovery_improvement_ratio", float(self.recovery_improvement_ratio))
        object.__setattr__(self, "recovery_improvement_delta", float(self.recovery_improvement_delta))
        object.__setattr__(self, "raw_learner_p99_ms", learner_p99)
        object.__setattr__(self, "raw_refresh_p99_ms", refresh_p99)
        object.__setattr__(self, "raw_solve_p99_ms", solve_p99)
        object.__setattr__(self, "projected_solve_p99_ms", float(projected))
        object.__setattr__(self, "runtime_validity", self.runtime_validity)
        object.__setattr__(self, "simulator_diagnostics", _freeze(self.simulator_diagnostics))
        object.__setattr__(
            self, "simulator_diagnostics_available", bool(self.simulator_diagnostics_available)
        )
        object.__setattr__(
            self, "simulator_diagnostics_valid", bool(self.simulator_diagnostics_valid)
        )
        object.__setattr__(
            self,
            "simulator_gain_error_c_per_q",
            float(self.simulator_gain_error_c_per_q),
        )
        object.__setattr__(self, "simulator_delay_error_s", float(self.simulator_delay_error_s))
        object.__setattr__(
            self,
            "simulator_coast_braking_error_c",
            float(self.simulator_coast_braking_error_c),
        )
        if self.target_missed and not self.operational_consequence:
            raise ValueError("target-missed evidence requires an operational consequence")
        object.__setattr__(self, "target_missed", bool(self.target_missed))
        object.__setattr__(self, "operational_consequence", self.operational_consequence)
        object.__setattr__(self, "recovery_available", bool(self.recovery_available))

    @property
    def worst_domain_score(self) -> float:
        return max(self.ranking_domain_scores.values())

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
            "ranking_domain_control_scores": _document(self.ranking_domain_scores),
            "correct_baseline_no_degradation": self.correct_baseline_no_degradation,
            "runtime_validity": self.runtime_validity,
            "prediction_error": self.prediction_error,
            "simulator_diagnostics": _document(self.simulator_diagnostics),
            "simulator_diagnostics_available": self.simulator_diagnostics_available,
            "simulator_diagnostics_valid": self.simulator_diagnostics_valid,
            "simulator_gain_error_c_per_q": self.simulator_gain_error_c_per_q,
            "simulator_delay_error_s": self.simulator_delay_error_s,
            "simulator_coast_braking_error_c": self.simulator_coast_braking_error_c,
            "target_missed": self.target_missed,
            "operational_consequence": self.operational_consequence,
            "recovery_available": self.recovery_available,
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
            "before_mae": self.before_mae,
            "after_mae": self.after_mae,
            "recovery_improvement_ratio": self.recovery_improvement_ratio,
            "recovery_improvement_delta": self.recovery_improvement_delta,
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
        object.__setattr__(self, "failures", tuple(sorted(self.failures, key=_failure_sort_key)))
        object.__setattr__(self, "horizon_evidence", _freeze(self.horizon_evidence))

    def with_failures(self, failures: Sequence[ArmFailure]) -> "ExperimentArtifact":
        return replace(self, failures=tuple(failures))

    def with_horizon_evidence(self, horizon_evidence: Mapping[str, Any]) -> "ExperimentArtifact":
        return replace(self, horizon_evidence=horizon_evidence)

    def to_document(self) -> dict[str, Any]:
        """Serialize rows once while storing repeated fitted evidence by identity."""
        bundles: dict[str, Any] = {}
        scenarios: list[dict[str, Any]] = []
        for scenario in self.scenarios:
            row = _scenario_document(scenario)
            evidence_id = row.get("evidence_id")
            model_evidence = row.pop("model_evidence", None)
            if evidence_id is not None and model_evidence is not None:
                shared_keys = (
                    "calibration_provenance",
                    "calibration_metadata",
                    "batch_fit_snapshot",
                    "simulator_prediction_diagnostics",
                    "adaptation",
                )
                shared = {
                    key: model_evidence[key]
                    for key in shared_keys
                    if key in model_evidence
                }
                runtime = {
                    key: value
                    for key, value in model_evidence.items()
                    if key not in shared_keys
                }
                previous = bundles.setdefault(str(evidence_id), shared)
                if previous != shared:
                    raise ValueError(f"evidence_id {evidence_id!r} has conflicting raw origins")
                row["runtime_model_evidence"] = runtime
            scenarios.append(row)
        return {
            "arms": {arm.name: arm.to_document() for arm in self.arms},
            "config": _document(self.config),
            "environment": _document(self.environment),
            "evidence_bundles": dict(sorted(bundles.items())),
            "failures": [failure.to_document() for failure in self.failures],
            "horizon_evidence": _normalize_horizon_document(self.horizon_evidence),
            "model_snapshots": _document(self.model_snapshots),
            "scenarios": scenarios,
            "schema_version": SCHEMA_VERSION,
            "seeds": list(self.seeds),
            "source_revision": self.source_revision,
            "splits": _document(self.splits),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_document(), allow_nan=False, indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, payload: str) -> "ExperimentArtifact":
        """Losslessly reconstruct a normalized artifact, including raw row origins."""
        document = json.loads(payload)
        bundles = document.get("evidence_bundles", {})
        if not isinstance(bundles, Mapping):
            raise ValueError("evidence_bundles must be a mapping")
        from .runner import _scenario_from_document

        rows = []
        for stored in document.get("scenarios", ()):
            row = dict(stored)
            evidence_id = row.get("evidence_id")
            if "model_evidence" not in row and evidence_id is not None:
                runtime = row.pop("runtime_model_evidence", {})
                try:
                    row["model_evidence"] = {**bundles[str(evidence_id)], **runtime}
                except KeyError as exc:
                    raise ValueError(f"missing evidence bundle {evidence_id!r}") from exc
            rows.append(_scenario_from_document(row))
        arms = []
        for name, evidence in document["arms"].items():
            raw = evidence.get("raw_timing_ms", {})
            arms.append(
                ArmEvidence(
                    name=name,
                    domain_median_scores=evidence["domain_median_control_scores"],
                    ranking_domain_scores=evidence.get("ranking_domain_control_scores", {}),
                    correct_baseline_no_degradation=evidence.get("correct_baseline_no_degradation", True),
                    runtime_validity=evidence.get("runtime_validity", "measured"),
                    prediction_error=evidence["prediction_error"],
                    before_mae=evidence["before_mae"],
                    after_mae=evidence["after_mae"],
                    recovery_improvement_ratio=evidence["recovery_improvement_ratio"],
                    recovery_improvement_delta=evidence["recovery_improvement_delta"],
                    recovery_available=evidence.get("recovery_available", False),
                    raw_solve_p99_ms=evidence["raw_solve_p99_ms"],
                    projected_solve_p99_ms=evidence.get("projected_solve_p99_ms", -1.0),
                    raw_learner_ms=raw.get("learner", ()),
                    raw_refresh_ms=raw.get("refresh", ()),
                    raw_solve_ms=raw.get("solve", ()),
                    simulator_diagnostics=evidence.get("simulator_diagnostics", {}),
                    simulator_diagnostics_available=evidence.get("simulator_diagnostics_available", False),
                    simulator_diagnostics_valid=evidence.get("simulator_diagnostics_valid", True),
                    simulator_gain_error_c_per_q=evidence.get("simulator_gain_error_c_per_q", 0.0),
                    simulator_delay_error_s=evidence.get("simulator_delay_error_s", 0.0),
                    simulator_coast_braking_error_c=evidence.get("simulator_coast_braking_error_c", 0.0),
                    target_missed=evidence.get("target_missed", False),
                    operational_consequence=evidence.get("operational_consequence"),
                )
            )
        failures = tuple(
            ArmFailure(
                item["arm"],
                item["scenario"],
                item["category"],
                item["detail"],
                MatrixKey(**item["matrix_key"]) if "matrix_key" in item else None,
            )
            for item in document.get("failures", ())
        )
        return cls(
            config=document["config"],
            seeds=document["seeds"],
            splits=document["splits"],
            model_snapshots=document["model_snapshots"],
            scenarios=tuple(rows),
            arms=tuple(arms),
            source_revision=document["source_revision"],
            environment=document["environment"],
            failures=failures,
            horizon_evidence=document.get("horizon_evidence", {}),
        )

    def canonical_document(self) -> dict[str, Any]:
        """Return deterministic scientific evidence while retaining timing evidence in ``to_document``."""
        document = self.to_document()
        document["config"].pop("execution_metadata", None)
        fitted_by_domain = document["model_snapshots"].get("fitted_by_domain", {})
        if isinstance(fitted_by_domain, dict):
            for evidence in fitted_by_domain.values():
                if isinstance(evidence, dict):
                    evidence.pop("initial_batch_fit_ms", None)
        for row in document["scenarios"]:
            row.pop("raw_timing_ms", None)
            for name in (
                "deadline_misses",
                "initial_batch_fit_ms",
                "raw_learner_p99_ms",
                "raw_refresh_p99_ms",
                "raw_solve_p99_ms",
            ):
                row["metrics"].pop(name, None)
            model_evidence = row.get("model_evidence")
            if isinstance(model_evidence, dict):
                model_evidence.pop("initial_batch_fit_ms", None)
            runtime_evidence = row.get("runtime_model_evidence")
            if isinstance(runtime_evidence, dict):
                runtime_evidence.pop("initial_batch_fit_ms", None)
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
    diagnostics_present = any(
        evidence.simulator_diagnostics_available for evidence in artifact.arms
    )
    for evidence in artifact.arms:
        reasons = sorted(set(failures_by_arm.get(evidence.name, ())))
        if evidence.runtime_validity == "measured" and (
            evidence.raw_learner_p99_ms * 5.0 > 25.0
            or evidence.raw_refresh_p99_ms * 5.0 > 1_250.0
            or evidence.projected_solve_p99_ms > min(budget * 5.0, 250.0)
        ):
            reasons.append("runtime beyond hard limits")
        if diagnostics_present and not evidence.simulator_diagnostics_available:
            reasons.append("simulator diagnostics unavailable")
        elif evidence.simulator_diagnostics_available and not evidence.simulator_diagnostics_valid:
            reasons.append("invalid simulator gain/delay/coast diagnostics")
        valid = not reasons
        recommendations[evidence.name] = ArmRecommendation(
            valid, tuple(reasons), evidence.worst_domain_score
        )
        if valid:
            valid_evidence.append(evidence)
    include_runtime = all(
        item.runtime_validity == "measured" for item in valid_evidence
    )
    include_diagnostics = bool(valid_evidence) and all(
        item.simulator_diagnostics_available and item.simulator_diagnostics_valid
        for item in valid_evidence
    )
    include_recovery = bool(valid_evidence) and all(
        item.recovery_available for item in valid_evidence
    )
    frontier = _pareto_frontier(
        valid_evidence, include_runtime, include_diagnostics, include_recovery
    )
    ranking_pool = list(frontier) if include_diagnostics else valid_evidence
    selected: str | None = None
    if ranking_pool and not _material_pareto_conflict(
        frontier, include_runtime, include_diagnostics, include_recovery
    ):
        best_score = min(item.worst_domain_score for item in ranking_pool)
        contenders = [
            item for item in ranking_pool if item.worst_domain_score <= best_score * 1.05
        ]
        selected = min(
            contenders,
            key=lambda item: _selection_key(
                item, include_runtime, include_diagnostics, include_recovery
            ),
        ).name
    return Recommendation(
        MappingProxyType(dict(sorted(recommendations.items()))),
        selected,
        tuple(item.name for item in frontier),
    )


def render_table(artifact: ExperimentArtifact, recommendation: Recommendation | None = None) -> str:
    """Render concise evidence without presenting raw timing as a measurement."""
    recommendation = recommend(artifact) if recommendation is None else recommendation
    lines = ["arm              valid  worst-score  timing                   target_missed  reasons"]
    for evidence in artifact.arms:
        result = recommendation.arms[evidence.name]
        reasons = ", ".join(result.reasons) or "-"
        timing = (
            "not_measured/raw-only"
            if evidence.runtime_validity == "not_measured"
            else f"projected-p99={evidence.projected_solve_p99_ms:.3f}ms"
        )
        miss = (
            f"true ({evidence.operational_consequence})"
            if evidence.target_missed
            else "false"
        )
        lines.append(
            f"{evidence.name:<17}{str(result.valid):<7}{evidence.worst_domain_score:<13.3f}"
            f"{timing:<25}{miss:<15}{reasons}"
        )
    lines.append(f"selected: {recommendation.selected_arm or 'none'}")
    lines.append(f"pareto: {', '.join(recommendation.pareto_frontier) or 'none'}")
    return "\n".join(lines)


def _pareto_frontier(
    evidence: Sequence[ArmEvidence],
    include_runtime: bool,
    include_diagnostics: bool,
    include_recovery: bool,
) -> tuple[ArmEvidence, ...]:
    if not evidence:
        return ()
    scales = tuple(
        max(min(values), 1e-12)
        for values in zip(
            *(
                _pareto_values(item, include_runtime, include_diagnostics, include_recovery)
                for item in evidence
            )
        )
    )
    return tuple(
        item
        for item in sorted(evidence, key=lambda value: value.name)
        if not any(
            _dominates(other, item, scales, include_runtime, include_diagnostics, include_recovery)
            for other in evidence
            if other is not item
        )
    )


def _pareto_values(
    evidence: ArmEvidence, include_runtime: bool, include_diagnostics: bool, include_recovery: bool
) -> tuple[float, ...]:
    values = [
        evidence.worst_domain_score,
        evidence.prediction_error,
    ]
    if include_recovery:
        values.append(evidence.recovery_improvement_ratio)
    if include_runtime:
        values.append(evidence.projected_solve_p99_ms)
    if include_diagnostics:
        values.extend(
            (
                evidence.simulator_gain_error_c_per_q,
                evidence.simulator_delay_error_s,
                evidence.simulator_coast_braking_error_c,
            )
        )
    return tuple(values)


def _selection_key(
    evidence: ArmEvidence, include_runtime: bool, include_diagnostics: bool, include_recovery: bool
) -> tuple[float | int | str, ...]:
    values: list[float | int | str] = [evidence.prediction_error]
    if include_recovery:
        values.append(evidence.recovery_improvement_ratio)
    if include_runtime:
        values.append(evidence.projected_solve_p99_ms)
    if include_diagnostics:
        values.extend(
            (
                evidence.simulator_gain_error_c_per_q,
                evidence.simulator_delay_error_s,
                evidence.simulator_coast_braking_error_c,
            )
        )
    return (*values, _COMPLEXITY[evidence.name], evidence.name)


def _dominates(
    left: ArmEvidence,
    right: ArmEvidence,
    scales: Sequence[float],
    include_runtime: bool,
    include_diagnostics: bool,
    include_recovery: bool,
) -> bool:
    normalized_left = tuple(
        value / scale
        for value, scale in zip(
            _pareto_values(left, include_runtime, include_diagnostics, include_recovery), scales
        )
    )
    normalized_right = tuple(
        value / scale
        for value, scale in zip(
            _pareto_values(right, include_runtime, include_diagnostics, include_recovery), scales
        )
    )
    return all(a <= b for a, b in zip(normalized_left, normalized_right)) and any(
        a < b * 0.95 for a, b in zip(normalized_left, normalized_right) if b > 0.0
    )


def _material_pareto_conflict(
    frontier: Sequence[ArmEvidence],
    include_runtime: bool,
    include_diagnostics: bool,
    include_recovery: bool,
) -> bool:
    if len(frontier) < 2:
        return False
    for values in zip(
        *(
            _pareto_values(item, include_runtime, include_diagnostics, include_recovery)
            for item in frontier
        )
    ):
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


def _scenario_sort_key(value: Any) -> tuple[str, str, str, str, str, int, int]:
    return (
        str(getattr(value, "arm", "")),
        str(getattr(value, "initialization", "")),
        str(getattr(value, "plant", "")),
        str(getattr(value, "mode", "")),
        str(getattr(value, "scenario", "")),
        int(getattr(value, "seed", 0)),
        int(getattr(value, "mpc_horizon_s", 0)),
    )


def _failure_sort_key(value: ArmFailure) -> tuple[str, str, str, str, str, int, int, str, str]:
    key = value.matrix_key
    if key is None:
        return (value.arm, "", "", "", value.scenario, -1, -1, value.category, value.detail)
    return (
        key.arm,
        key.initialization,
        key.plant,
        key.mode,
        key.scenario,
        key.seed,
        key.mpc_horizon_s,
        value.category,
        value.detail,
    )


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
