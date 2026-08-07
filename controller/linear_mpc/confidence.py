"""Fail-closed, off-path confidence evaluation for compact model evidence."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
import json
from math import isfinite, sqrt
from typing import Any

import numpy as np

_REQUIRED_HORIZONS = (3, 15, 45, 90, 180)
_RMSE_LIMITS = {3: 2.8, 15: 2.8, 45: 2.8, 90: 5.0, 180: 5.0}
_REQUIRED_CALIBRATION_STAGES = frozenset(("low", "middle", "high", "coast"))


class ConfidenceStatus(StrEnum):
    COLLECTING = "collecting"
    INSUFFICIENT_EXCITATION = "insufficient-excitation"
    FITTING = "fitting"
    EVALUATING = "evaluating"
    READY_FOR_REVIEW = "ready-for-review"
    ACTIVE = "active"
    FALLBACK = "fallback"
    SCHEMA_INVALIDATED = "schema-invalidated"


@dataclass(frozen=True, slots=True)
class ConfidenceConfig:
    bootstrap_seed: int = 0
    bootstrap_replicates: int = 10_000
    maximum_pole_magnitude: float = 0.999
    maximum_delay_steps: int = 15
    maximum_alignment_error_c: float = 2.0
    maximum_signed_bias_c: float = 0.25
    maximum_band_bias_c: float = 0.5
    braking_tolerance_c: float = 0.0
    maximum_refresh_p99_ms: float = 250.0
    required_sequential_wins: int = 2
    required_horizons: tuple[int, ...] = _REQUIRED_HORIZONS

    def __post_init__(self) -> None:
        if self.bootstrap_replicates < 1:
            raise ValueError("bootstrap_replicates must be positive")
        if self.maximum_delay_steps < 1 or self.required_sequential_wins < 1:
            raise ValueError("confidence count limits must be positive")
        if tuple(self.required_horizons) != _REQUIRED_HORIZONS:
            raise ValueError("confidence requires horizons 3, 15, 45, 90, and 180")
        finite_positive = (
            self.maximum_pole_magnitude,
            self.maximum_alignment_error_c,
            self.maximum_signed_bias_c,
            self.maximum_band_bias_c,
            self.braking_tolerance_c,
            self.maximum_refresh_p99_ms,
        )
        if not all(isfinite(value) and value >= 0.0 for value in finite_positive):
            raise ValueError("confidence thresholds must be finite and non-negative")
        if not 0.0 < self.maximum_pole_magnitude < 1.0:
            raise ValueError("maximum_pole_magnitude must be below one")


@dataclass(frozen=True, slots=True)
class GateResult:
    name: str
    passed: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class BootstrapInterval:
    horizon_steps: int
    temperature_band: str
    phase: str
    ambient_source: str
    generation: int
    challenger_rmse_c: float | None
    incumbent_rmse_c: float | None
    upper_bound: float | None
    available: bool
    replicate_count: int
    method: str = "hierarchical-cook-block"


@dataclass(frozen=True, slots=True)
class ConfidenceReport:
    status: ConfidenceStatus
    active_kind: str | None
    candidate_digest: str | None
    generation: int | None
    gates: tuple[GateResult, ...]
    bootstrap_intervals: tuple[BootstrapInterval, ...]
    blockers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Origin:
    cook_key: tuple[str, str]
    horizon_steps: int
    band: str
    phase: str
    ambient: str
    generation: int
    sequence: int
    canonical: str
    incumbent_error: float
    challenger_error: float
    calibration_fit: bool
    untouched_future: bool


def evaluate_confidence(
    evidence: Sequence[object],
    *,
    activation_state: object,
    target_timing: object,
    config: ConfidenceConfig,
) -> ConfidenceReport:
    """Evaluate only immutable compact ledger entries; never alter controller ownership."""
    rows, malformed = _canonical_rows(evidence)
    state = _mapping(activation_state)
    candidate_digest = _candidate_digest(rows)
    active_kind = _string(state.get("active_kind")) if state is not None else None
    authoritative = _authoritative_status(state)
    schema_invalidated = any(_enum_string(row.get("kind")) == "schema_invalidation" for row in rows)
    origins, origin_errors = _origins(rows)
    diagnostics = _latest_payload(rows, "refresh_diagnostics")

    gates: list[GateResult] = []
    _add_gate(gates, "ledger-integrity", not malformed and not origin_errors, "ledger-integrity")
    _add_gate(gates, "calibration-completeness", _calibration_complete(rows), "calibration-completeness")
    _add_gate(
        gates,
        "identifiability",
        _truth(diagnostics, "full_rank") and _truth(diagnostics, "finite_diagnostics"),
        "identifiability",
    )
    _add_gate(gates, "pole-magnitude", _finite_at_most(diagnostics, "pole_magnitude", config.maximum_pole_magnitude), "pole-magnitude")
    _add_gate(gates, "positive-gain", _finite_positive(diagnostics, "gain"), "positive-gain")
    _add_gate(gates, "delay-limit", _integer_at_most(diagnostics, "delay_steps", config.maximum_delay_steps), "delay-limit")
    _add_gate(gates, "finite-covariance", _truth(diagnostics, "covariance_finite"), "finite-covariance")
    _add_gate(gates, "state-alignment", _finite_at_most(diagnostics, "alignment_error_c", config.maximum_alignment_error_c), "state-alignment")
    _add_gate(gates, "snapshot-round-trip", _truth(diagnostics, "snapshot_round_trip"), "snapshot-round-trip")
    _add_gate(gates, "atomic-persistence", _truth(diagnostics, "atomic_persistence"), "atomic-persistence")
    _add_gate(gates, "model-integrity", _truth(diagnostics, "model_integrity") and candidate_digest is not None, "model-integrity")
    _add_gate(gates, "provenance-integrity", _truth(diagnostics, "provenance_integrity") and _one_provenance(rows), "provenance-integrity")
    _add_gate(gates, "schema-integrity", _truth(diagnostics, "schema_integrity") and _schemas_valid(rows) and not schema_invalidated, "schema-integrity")
    _add_gate(gates, "untouched-future-rows", bool(origins) and all(origin.untouched_future and not origin.calibration_fit for origin in origins), "untouched-future-rows")
    _add_gate(gates, "production-prospective-construction", _truth(diagnostics, "production_prospective"), "production-prospective-construction")
    _add_gate(gates, "sequential-wins", _integer_at_least(diagnostics, "sequential_wins", config.required_sequential_wins), "sequential-wins")
    _add_gate(gates, "generation-continuity", _truth(diagnostics, "generation_continuity") and _one_generation(origins), "generation-continuity")
    _add_gate(gates, "target-timing", _target_timing_ok(target_timing, config.maximum_refresh_p99_ms), "target-timing")

    horizon_scores = _horizon_scores(origins)
    present_horizons = frozenset(score[0] for score in horizon_scores)
    for horizon in config.required_horizons:
        score = next((item for item in horizon_scores if item[0] == horizon), None)
        if score is None:
            _add_gate(gates, f"absolute-rmse-{horizon}", False, f"absolute-rmse-{horizon}")
            _add_gate(gates, f"unsupported-horizon-{horizon}", False, "unsupported-horizon")
        else:
            _add_gate(gates, f"absolute-rmse-{horizon}", score[2] <= _RMSE_LIMITS[horizon], f"absolute-rmse-{horizon}")
    for horizon in sorted(present_horizons - frozenset(config.required_horizons)):
        _add_gate(gates, f"unsupported-horizon-{horizon}", False, "unsupported-horizon")

    signed_bias = _mean(origin.challenger_error for origin in origins)
    _add_gate(gates, "signed-bias", signed_bias is not None and abs(signed_bias) <= config.maximum_signed_bias_c, "signed-bias")
    _add_gate(gates, "temperature-band-error", _band_errors_ok(origins, config.maximum_band_bias_c), "temperature-band-error")
    _add_gate(gates, "braking-error", _braking_ok(origins, config.braking_tolerance_c), "braking-error")

    intervals = _bootstrap_intervals(origins, config)
    _add_gate(gates, "bootstrap-unavailable", bool(intervals) and all(interval.available for interval in intervals), "bootstrap-unavailable")
    _add_gate(gates, "relative-rmse", bool(intervals) and all(_relative_rmse_ok(interval) for interval in intervals), "relative-rmse")
    _add_gate(gates, "relative-bootstrap", bool(intervals) and all(_relative_bootstrap_ok(interval) for interval in intervals), "relative-bootstrap")
    _add_gate(gates, "cook-effective-weight", _independent_cook_weight(origins), "cook-effective-weight")

    blockers = tuple(gate.reason for gate in gates if not gate.passed and gate.reason is not None)
    status = _status(authoritative, schema_invalidated, origins, diagnostics, blockers)
    return ConfidenceReport(status, active_kind, candidate_digest, _generation(origins), tuple(gates), intervals, blockers)


def _canonical_rows(evidence: Sequence[object]) -> tuple[tuple[Mapping[str, object], ...], bool]:
    rows: list[tuple[str, Mapping[str, object]]] = []
    malformed = False
    for entry in evidence:
        row = _record_mapping(entry)
        if row is None:
            malformed = True
            continue
        try:
            canonical = json.dumps(_json_owned(row), sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError):
            malformed = True
            continue
        rows.append((canonical, row))
    rows.sort(key=lambda value: value[0])
    return tuple(row for _, row in rows), malformed


def _record_mapping(entry: object) -> Mapping[str, object] | None:
    if isinstance(entry, Mapping):
        return _mapping(entry)
    dump = getattr(entry, "model_dump", None)
    if callable(dump):
        value = dump(mode="python")
        return _mapping(value)
    return None


def _origins(rows: Sequence[Mapping[str, object]]) -> tuple[tuple[_Origin, ...], bool]:
    origins: list[_Origin] = []
    invalid = False
    identities: set[tuple[str, str, int, int, str, str, str, int]] = set()
    for row in rows:
        if _string(row.get("kind")) != "forecast_origin":
            continue
        payload = _mapping(row.get("payload"))
        cook_id = _string(row.get("cook_id"))
        session_id = _string(row.get("session_id"))
        generation = _integer(row.get("role_generation"))
        if payload is None or cook_id is None or session_id is None or generation is None:
            invalid = True
            continue
        horizon = _integer(payload.get("horizon_steps"))
        sequence = _integer(payload.get("origin_sequence"))
        band = _string(payload.get("temperature_band"))
        phase = _string(payload.get("phase"))
        ambient = _enum_string(payload.get("ambient_source"))
        incumbent = _finite(payload.get("incumbent_error_c"))
        challenger = _finite(payload.get("challenger_error_c"))
        if None in (horizon, sequence, band, phase, ambient, incumbent, challenger):
            invalid = True
            continue
        identity = (cook_id, session_id, generation, horizon, band, phase, ambient, sequence)
        if identity in identities:
            invalid = True
            continue
        identities.add(identity)
        canonical = json.dumps(_json_owned(row), sort_keys=True, separators=(",", ":"), allow_nan=False)
        origins.append(
            _Origin(
                (cook_id, session_id), horizon, band, phase, ambient, generation, sequence, canonical,
                incumbent, challenger, payload.get("calibration_fit") is True,
                payload.get("untouched_future", payload.get("calibration_fit") is False) is True,
            )
        )
    return tuple(origins), invalid


def _bootstrap_intervals(origins: Sequence[_Origin], config: ConfidenceConfig) -> tuple[BootstrapInterval, ...]:
    grouped: dict[tuple[int, str, str, str, int], list[_Origin]] = defaultdict(list)
    for origin in origins:
        grouped[(origin.horizon_steps, origin.band, origin.phase, origin.ambient, origin.generation)].append(origin)
    intervals: list[BootstrapInterval] = []
    for key in sorted(grouped):
        horizon, band, phase, ambient, generation = key
        group = grouped[key]
        by_cook: dict[tuple[str, str], list[_Origin]] = defaultdict(list)
        for origin in group:
            by_cook[origin.cook_key].append(origin)
        for cook in by_cook:
            by_cook[cook].sort(key=lambda origin: (origin.sequence, origin.canonical))
        challenger_rmse = _rmse(origin.challenger_error for origin in group)
        incumbent_rmse = _rmse(origin.incumbent_error for origin in group)
        values = _hierarchical_ratios(by_cook, horizon, config)
        upper = _upper_p95(values)
        intervals.append(
            BootstrapInterval(
                horizon, band, phase, ambient, generation, challenger_rmse, incumbent_rmse,
                upper, upper is not None, len(values),
            )
        )
    return tuple(intervals)


def _hierarchical_ratios(
    by_cook: Mapping[tuple[str, str], Sequence[_Origin]], horizon: int, config: ConfidenceConfig
) -> tuple[float, ...]:
    cook_keys = tuple(sorted(by_cook))
    block_starts = {
        cook: _contiguous_block_starts(by_cook[cook], horizon)
        for cook in cook_keys
    }
    if len(cook_keys) < 2 or any(not block_starts[cook] for cook in cook_keys):
        return ()
    rng = np.random.default_rng(config.bootstrap_seed)
    ratios: list[float] = []
    for selected in rng.integers(0, len(cook_keys), size=(config.bootstrap_replicates, len(cook_keys))):
        sample: list[_Origin] = []
        for selected_index in selected:
            cook = cook_keys[int(selected_index)]
            sample.extend(_resample_blocks(by_cook[cook], horizon, block_starts[cook], rng))
        challenger = _rmse(origin.challenger_error for origin in sample)
        incumbent = _rmse(origin.incumbent_error for origin in sample)
        if challenger is not None and incumbent is not None and incumbent > 0.0:
            ratio = challenger / incumbent
            if isfinite(ratio):
                ratios.append(ratio)
    return tuple(ratios)


def _contiguous_block_starts(rows: Sequence[_Origin], horizon: int) -> tuple[int, ...]:
    return tuple(
        start
        for start in range(len(rows) - horizon + 1)
        if all(rows[index].sequence + 1 == rows[index + 1].sequence for index in range(start, start + horizon - 1))
    )


def _resample_blocks(
    rows: Sequence[_Origin], horizon: int, starts: Sequence[int], rng: np.random.Generator
) -> tuple[_Origin, ...]:
    sampled: list[_Origin] = []
    while len(sampled) < len(rows):
        start = starts[int(rng.integers(0, len(starts)))]
        sampled.extend(rows[start : start + horizon])
    return tuple(sampled[: len(rows)])


def _upper_p95(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return float(np.quantile(np.asarray(values, dtype=np.float64), 0.95, method="higher"))


def _horizon_scores(origins: Sequence[_Origin]) -> tuple[tuple[int, float, float], ...]:
    scores: list[tuple[int, float, float]] = []
    for horizon in sorted({origin.horizon_steps for origin in origins}):
        rows = tuple(origin for origin in origins if origin.horizon_steps == horizon)
        challenger = _rmse(origin.challenger_error for origin in rows)
        incumbent = _rmse(origin.incumbent_error for origin in rows)
        if challenger is not None and incumbent is not None:
            scores.append((horizon, incumbent, challenger))
    return tuple(scores)


def _calibration_complete(rows: Sequence[Mapping[str, object]]) -> bool:
    stages = {
        _string(payload.get("stage"))
        for row in rows
        if _string(row.get("kind")) == "calibration_summary"
        for payload in (_mapping(row.get("payload")),)
        if payload is not None and payload.get("accepted") is True
    }
    return _REQUIRED_CALIBRATION_STAGES <= stages


def _latest_payload(rows: Sequence[Mapping[str, object]], kind: str) -> Mapping[str, object]:
    matches = [
        payload
        for row in rows
        if _string(row.get("kind")) == kind
        for payload in (_mapping(row.get("payload")),)
        if payload is not None
    ]
    return matches[-1] if matches else {}


def _independent_cook_weight(origins: Sequence[_Origin]) -> bool:
    groups: dict[tuple[int, str, str, str, int], set[tuple[str, str]]] = defaultdict(set)
    for origin in origins:
        groups[(origin.horizon_steps, origin.band, origin.phase, origin.ambient, origin.generation)].add(origin.cook_key)
    return bool(groups) and all(len(cooks) >= 2 for cooks in groups.values())


def _one_generation(origins: Sequence[_Origin]) -> bool:
    return bool(origins) and len({origin.generation for origin in origins}) == 1


def _generation(origins: Sequence[_Origin]) -> int | None:
    return next(iter({origin.generation for origin in origins})) if _one_generation(origins) else None


def _candidate_digest(rows: Sequence[Mapping[str, object]]) -> str | None:
    values = {_string(row.get("model_digest")) for row in rows if _string(row.get("model_digest")) is not None}
    return next(iter(values)) if len(values) == 1 else None

def _one_provenance(rows: Sequence[Mapping[str, object]]) -> bool:
    values = {
        _string(row.get("provenance_digest"))
        for row in rows
        if _string(row.get("provenance_digest")) is not None
    }
    return bool(values) and len(values) == 1 and all(
        _string(row.get("provenance_digest")) is not None for row in rows
    )


def _schemas_valid(rows: Sequence[Mapping[str, object]]) -> bool:
    return bool(rows) and all(_integer(row.get("schema_version")) == 1 for row in rows)


def _band_errors_ok(origins: Sequence[_Origin], maximum: float) -> bool:
    grouped: dict[tuple[int, str], list[float]] = defaultdict(list)
    for origin in origins:
        if not origin.band:
            return False
        grouped[(origin.horizon_steps, origin.band)].append(origin.challenger_error)
    return bool(grouped) and all(
        (mean := _mean(values)) is not None and abs(mean) <= maximum for values in grouped.values()
    )


def _braking_ok(origins: Sequence[_Origin], tolerance: float) -> bool:
    rows = tuple(origin for origin in origins if origin.phase == "coasting")
    if not rows:
        return False
    challenger = _rmse(origin.challenger_error for origin in rows)
    incumbent = _rmse(origin.incumbent_error for origin in rows)
    return challenger is not None and incumbent is not None and challenger <= incumbent + tolerance


def _target_timing_ok(target_timing: object, maximum_p99: float) -> bool:
    timing = _mapping(target_timing)
    if timing is None or _string(timing.get("hardware_provenance")) != "target-hardware":
        return False
    value = _finite(timing.get("p99_ms"))
    return value is not None and value <= maximum_p99


def _relative_rmse_ok(interval: BootstrapInterval) -> bool:
    return (
        interval.challenger_rmse_c is not None
        and interval.incumbent_rmse_c is not None
        and interval.challenger_rmse_c < interval.incumbent_rmse_c
    )


def _relative_bootstrap_ok(interval: BootstrapInterval) -> bool:
    return interval.upper_bound is not None and interval.upper_bound < 1.0


def _status(
    authoritative: ConfidenceStatus | None,
    schema_invalidated: bool,
    origins: Sequence[_Origin],
    diagnostics: Mapping[str, object],
    blockers: Sequence[str],
) -> ConfidenceStatus:
    if authoritative is not None:
        return authoritative
    if schema_invalidated:
        return ConfidenceStatus.SCHEMA_INVALIDATED
    if not origins:
        return ConfidenceStatus.COLLECTING
    if not diagnostics:
        return ConfidenceStatus.FITTING
    if "calibration-completeness" in blockers or "identifiability" in blockers:
        return ConfidenceStatus.INSUFFICIENT_EXCITATION
    return ConfidenceStatus.READY_FOR_REVIEW if not blockers else ConfidenceStatus.EVALUATING


def _authoritative_status(state: Mapping[str, object] | None) -> ConfidenceStatus | None:
    if state is None:
        return None
    value = _enum_string(state.get("status"))
    if value in (ConfidenceStatus.ACTIVE.value, ConfidenceStatus.FALLBACK.value, ConfidenceStatus.SCHEMA_INVALIDATED.value):
        return ConfidenceStatus(value)
    return None


def _add_gate(gates: list[GateResult], name: str, passed: bool, reason: str) -> None:
    gates.append(GateResult(name, passed, None if passed else reason))


def _truth(values: Mapping[str, object], key: str) -> bool:
    return values.get(key) is True


def _finite_positive(values: Mapping[str, object], key: str) -> bool:
    value = _finite(values.get(key))
    return value is not None and value > 0.0


def _finite_at_most(values: Mapping[str, object], key: str, maximum: float) -> bool:
    value = _finite(values.get(key))
    return value is not None and value <= maximum


def _integer_at_most(values: Mapping[str, object], key: str, maximum: int) -> bool:
    value = _integer(values.get(key))
    return value is not None and value <= maximum


def _integer_at_least(values: Mapping[str, object], key: str, minimum: int) -> bool:
    value = _integer(values.get(key))
    return value is not None and value >= minimum


def _mapping(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        return None
    return value


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _enum_string(value: object) -> str | None:
    raw = getattr(value, "value", value)
    return _string(raw)


def _integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    result = float(value)
    return result if isfinite(result) else None


def _rmse(values: Any) -> float | None:
    errors = tuple(values)
    if not errors or not all(isfinite(error) for error in errors):
        return None
    return sqrt(sum(error * error for error in errors) / len(errors))


def _mean(values: Any) -> float | None:
    numbers = tuple(values)
    if not numbers or not all(isfinite(value) for value in numbers):
        return None
    return sum(numbers) / len(numbers)


def _json_owned(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_owned(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_json_owned(item) for item in value]
    raw = getattr(value, "value", value)
    return raw
