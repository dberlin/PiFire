"""Pure, fail-closed confidence scoring over validated compact ledger records."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite, sqrt

import numpy as np

from common.model_evidence import (
    MODEL_EVIDENCE_SCHEMA_VERSION,
    CalibrationSummaryEvidence,
    ForecastOriginEvidence,
    ModelEvidenceRecord,
    RecorderGapEvidence,
    RefreshDiagnosticsEvidence,
    TimingDistributionEvidence,
)

_REQUIRED_HORIZONS = (3, 15, 45, 90, 180)
_RMSE_LIMITS = {3: 2.8, 15: 2.8, 45: 2.8, 90: 5.0, 180: 5.0}
_REQUIRED_STAGES = frozenset(("low", "middle", "high", "coast"))


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
    maximum_pole_magnitude: float = 0.999
    maximum_delay_steps: int = 15
    maximum_alignment_error_c: float = 2.0
    maximum_signed_bias_c: float = 0.25
    maximum_band_bias_c: float = 0.5
    braking_tolerance_c: float = 0.0
    maximum_refresh_p99_ms: float = 250.0
    required_sequential_wins: int = 2
    bootstrap_replicates: int = 10_000

    def __post_init__(self) -> None:
        if self.bootstrap_replicates != 10_000:
            raise ValueError("confidence bootstrap uses exactly 10,000 replicates")
        if not 0.0 < self.maximum_pole_magnitude < 1.0:
            raise ValueError("maximum_pole_magnitude must be below one")
        values = (
            self.maximum_alignment_error_c,
            self.maximum_signed_bias_c,
            self.maximum_band_bias_c,
            self.braking_tolerance_c,
            self.maximum_refresh_p99_ms,
        )
        if (
            self.maximum_delay_steps < 1
            or self.required_sequential_wins < 1
            or not all(isfinite(value) and value >= 0.0 for value in values)
        ):
            raise ValueError("confidence thresholds must be finite and non-negative")


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
    bootstrap_seed: int
    bootstrap_replicates: int


def parameter_promotion_blockers(
    report: ConfidenceReport,
    *,
    candidate_digest: str,
    candidate_generation: int,
    failed_generations: Sequence[int] = (),
) -> tuple[str, ...]:
    """Return fail-closed blockers for an already-active parameter challenger.

    ``status`` is deliberately not an authority here: an active controller is
    projected as ``active`` even while its next challenger is incomplete.
    Automatic promotion therefore requires every individual gate from the
    exact challenger generation to pass.
    """
    if not isinstance(report, ConfidenceReport):
        raise TypeError("report must be ConfidenceReport")
    blockers: list[str] = []
    if report.active_kind != "innovation-state-space":
        blockers.append("state-space-not-active")
    if report.candidate_digest != candidate_digest:
        blockers.append("candidate-digest-changed")
    if report.generation != candidate_generation:
        blockers.append("stale-candidate-generation")
    if candidate_generation in failed_generations:
        blockers.append("failed-generation-cannot-be-reenabled")
    blockers.extend(report.blockers)
    blockers.extend(
        gate.reason or gate.name
        for gate in report.gates
        if not gate.passed and (gate.reason or gate.name) not in blockers
    )
    return tuple(dict.fromkeys(blockers))


@dataclass(frozen=True, slots=True)
class _Origin:
    record: ModelEvidenceRecord
    payload: ForecastOriginEvidence

    @property
    def cook(self) -> str:
        assert self.record.cook_id is not None
        return self.record.cook_id

    @property
    def stratum(self) -> tuple[int, str, str, str, int]:
        return (
            self.payload.horizon_steps,
            self.payload.temperature_band,
            self.payload.phase,
            self.payload.ambient_source.value,
            self.record.role_generation,
        )


def evaluate_confidence(
    evidence: Sequence[object], *, activation_state: object, target_timing: object, config: ConfidenceConfig
) -> ConfidenceReport:
    """Evaluate typed persisted evidence only; this function has no control effects."""
    del target_timing  # Timing authority is a persisted TimingDistributionEvidence record.
    records, ledger_valid = _records(evidence)
    state = activation_state if isinstance(activation_state, Mapping) else {}
    active_kind = _text(state.get("active_kind"))
    authoritative = _authoritative_status(state)
    digest = _text(state.get("candidate_digest"))
    generation = _nonnegative_int(state.get("candidate_generation"))
    selected = tuple(
        record
        for record in records
        if digest is not None
        and generation is not None
        and record.model_digest == digest
        and record.role_generation == generation
    )
    origins, duplicate_conflict = _origins(selected)
    refresh = _newest_payload(selected, RefreshDiagnosticsEvidence)
    timing = _newest_payload(selected, TimingDistributionEvidence)
    schema_invalidated = _text(state.get("status")) == ConfidenceStatus.SCHEMA_INVALIDATED.value

    gates: list[GateResult] = []
    _gate(gates, "ledger-integrity", ledger_valid and not duplicate_conflict, "ledger-integrity")
    _gate(
        gates,
        "candidate-lineage",
        digest is not None and generation is not None and bool(selected),
        "candidate-lineage",
    )
    _gate(gates, "calibration-completeness", _calibration_complete(records), "calibration-completeness")
    _gate(
        gates,
        "identifiability",
        refresh is not None and refresh.accepted and refresh.full_rank and refresh.finite_diagnostics,
        "identifiability",
    )
    _gate(
        gates,
        "pole-magnitude",
        refresh is not None
        and refresh.pole_magnitude is not None
        and refresh.pole_magnitude < config.maximum_pole_magnitude,
        "pole-magnitude",
    )
    _gate(
        gates,
        "positive-gain",
        refresh is not None and refresh.gain is not None and refresh.gain > 0.0 and isfinite(refresh.gain),
        "positive-gain",
    )
    _gate(
        gates,
        "delay-limit",
        refresh is not None and refresh.delay_steps is not None and refresh.delay_steps <= config.maximum_delay_steps,
        "delay-limit",
    )
    _gate(gates, "finite-covariance", refresh is not None and refresh.covariance_finite, "finite-covariance")
    _gate(
        gates,
        "state-alignment",
        refresh is not None
        and refresh.alignment_error_c is not None
        and refresh.alignment_error_c <= config.maximum_alignment_error_c,
        "state-alignment",
    )
    _gate(gates, "snapshot-round-trip", refresh is not None and refresh.snapshot_round_trip, "snapshot-round-trip")
    _gate(
        gates,
        "sequential-wins",
        refresh is not None and refresh.sequential_wins >= config.required_sequential_wins,
        "sequential-wins",
    )
    _gate(
        gates, "generation-continuity", refresh is not None and refresh.generation_continuity, "generation-continuity"
    )
    _gate(gates, "atomic-persistence", refresh is not None and refresh.atomic_persistence, "atomic-persistence")
    _gate(
        gates,
        "production-prospective-construction",
        refresh is not None and refresh.production_prospective,
        "production-prospective-construction",
    )
    _gate(gates, "braking-error", _braking_ok(refresh, config.braking_tolerance_c), "braking-error")
    _gate(
        gates,
        "target-timing",
        isinstance(timing, TimingDistributionEvidence)
        and timing.hardware_provenance == "target-hardware"
        and timing.p99_ms is not None
        and timing.p99_ms <= config.maximum_refresh_p99_ms,
        "target-timing",
    )
    _gate(
        gates,
        "model-integrity",
        bool(selected) and _candidate_model_integrity(records, digest, generation),
        "model-integrity",
    )
    _gate(gates, "provenance-integrity", _one_provenance(selected), "provenance-integrity")
    _gate(
        gates,
        "schema-integrity",
        bool(selected)
        and all(record.schema_version == MODEL_EVIDENCE_SCHEMA_VERSION for record in selected)
        and not schema_invalidated,
        "schema-integrity",
    )
    for reason in dict.fromkeys(
        record.payload.reason for record in selected if isinstance(record.payload, RecorderGapEvidence)
    ):
        _gate(gates, f"evidence-continuity:{reason}", False, reason)
    _gate(gates, "untouched-future-rows", bool(origins), "untouched-future-rows")

    intervals = _bootstrap_intervals(origins, config)
    present = {interval.horizon_steps for interval in intervals}
    for horizon in _REQUIRED_HORIZONS:
        if horizon not in present:
            _gate(gates, f"missing-horizon-{horizon}", False, f"missing-horizon-{horizon}")
    for interval in intervals:
        if interval.horizon_steps not in _RMSE_LIMITS:
            _gate(
                gates,
                f"unsupported-horizon-{interval.horizon_steps}",
                False,
                f"unsupported-horizon-{interval.horizon_steps}",
            )
            continue
        label = _label(interval)
        _gate(
            gates,
            f"absolute-rmse:{label}",
            interval.challenger_rmse_c is not None
            and interval.challenger_rmse_c <= _RMSE_LIMITS[interval.horizon_steps],
            f"absolute-rmse:{label}",
        )
        _gate(
            gates,
            f"signed-bias:{label}",
            _signed_bias_ok(origins, interval, config.maximum_signed_bias_c),
            f"signed-bias:{label}",
        )
        _gate(
            gates,
            f"band-error:{label}",
            _band_error_ok(origins, interval, config.maximum_band_bias_c),
            f"band-error:{label}",
        )
        _gate(gates, f"bootstrap:{label}", interval.available, "bootstrap-unavailable")
        _gate(gates, f"relative-rmse:{label}", _relative_rmse_ok(interval), f"relative-rmse:{label}")
        _gate(
            gates,
            f"relative-bootstrap:{label}",
            interval.upper_bound is not None and interval.upper_bound < 1.0,
            "relative-bootstrap",
        )
        _gate(gates, f"cook-weight:{label}", _cook_weight_ok(origins, interval), "cook-effective-weight")

    blockers = tuple(gate.reason for gate in gates if not gate.passed and gate.reason is not None)
    status = _status(authoritative, schema_invalidated, records, refresh, blockers)
    return ConfidenceReport(
        status,
        active_kind,
        digest,
        generation,
        tuple(gates),
        intervals,
        blockers,
        config.bootstrap_seed,
        config.bootstrap_replicates,
    )


def _records(evidence: Sequence[object]) -> tuple[tuple[ModelEvidenceRecord, ...], bool]:
    if not all(isinstance(record, ModelEvidenceRecord) for record in evidence):
        return (), False
    records = tuple(record for record in evidence if isinstance(record, ModelEvidenceRecord))
    return tuple(sorted(records, key=lambda record: (record.timestamp_ms, record.evidence_id))), True


def _origins(records: Sequence[ModelEvidenceRecord]) -> tuple[tuple[_Origin, ...], bool]:
    unique: dict[tuple[str, int, int, int, int], _Origin] = {}
    conflict = False
    for record in records:
        if not isinstance(record.payload, ForecastOriginEvidence) or record.cook_id is None:
            continue
        payload = record.payload
        if record.model_digest != payload.challenger_digest or record.provenance_digest != payload.incumbent_digest:
            conflict = True
            continue
        identity = (
            record.cook_id,
            record.role_generation,
            payload.horizon_steps,
            payload.origin_sequence,
            payload.completion_time_ms,
        )
        origin = _Origin(record, payload)
        prior = unique.get(identity)
        if prior is not None:
            if (
                prior.payload != payload
                or prior.record.model_digest != record.model_digest
                or prior.record.provenance_digest != record.provenance_digest
            ):
                conflict = True
            continue
        unique[identity] = origin
    return tuple(unique[key] for key in sorted(unique)), conflict


def _newest_payload(
    records: Sequence[ModelEvidenceRecord],
    payload_type: type[RefreshDiagnosticsEvidence] | type[TimingDistributionEvidence],
) -> RefreshDiagnosticsEvidence | TimingDistributionEvidence | None:
    matches = [record for record in records if isinstance(record.payload, payload_type)]
    return max(matches, key=lambda record: (record.timestamp_ms, record.evidence_id)).payload if matches else None


def _calibration_complete(records: Sequence[ModelEvidenceRecord]) -> bool:
    stages: set[str] = set()
    for record in records:
        if (
            record.schema_version == MODEL_EVIDENCE_SCHEMA_VERSION
            and isinstance(record.payload, CalibrationSummaryEvidence)
            and record.payload.accepted
            and record.payload.continuous
        ):
            if record.payload.stage is not None:
                stages.add(record.payload.stage)
            stages.update(record.payload.completed_stages)
    return _REQUIRED_STAGES <= stages


def _candidate_model_integrity(
    records: Sequence[ModelEvidenceRecord],
    digest: str | None,
    generation: int | None,
) -> bool:
    return (
        digest is not None
        and generation is not None
        and all(
            record.model_digest == digest
            for record in records
            if record.role_generation == generation
            and not isinstance(record.payload, CalibrationSummaryEvidence)
            and record.model_digest is not None
        )
    )


def _bootstrap_intervals(origins: Sequence[_Origin], config: ConfidenceConfig) -> tuple[BootstrapInterval, ...]:
    grouped: dict[tuple[int, str, str, str, int], list[_Origin]] = defaultdict(list)
    for origin in origins:
        grouped[origin.stratum].append(origin)
    intervals: list[BootstrapInterval] = []
    for stratum in sorted(grouped):
        horizon, band, phase, ambient, generation = stratum
        group = grouped[stratum]
        by_cook: dict[str, list[_Origin]] = defaultdict(list)
        for origin in group:
            by_cook[origin.cook].append(origin)
        for values in by_cook.values():
            values.sort(
                key=lambda origin: (
                    origin.record.session_id,
                    origin.payload.origin_sequence,
                    origin.payload.origin_time_ms,
                    origin.record.evidence_id,
                )
            )
        ratios = _hierarchical_ratios(by_cook, horizon, config.bootstrap_seed)
        challenger = _rmse(origin.payload.challenger_error_c for origin in group)
        incumbent = _rmse(origin.payload.incumbent_error_c for origin in group)
        upper = None if len(ratios) != 10_000 else float(np.quantile(np.asarray(ratios), 0.95, method="higher"))
        intervals.append(
            BootstrapInterval(
                horizon,
                band,
                phase,
                ambient,
                generation,
                challenger,
                incumbent,
                upper,
                upper is not None,
                len(ratios),
            )
        )
    return tuple(intervals)


def _hierarchical_ratios(by_cook: Mapping[str, Sequence[_Origin]], horizon: int, seed: int) -> tuple[float, ...]:
    cooks = tuple(sorted(by_cook))
    starts = {cook: _block_starts(by_cook[cook], horizon) for cook in cooks}
    if len(cooks) < 2 or any(not starts[cook] for cook in cooks):
        return ()
    rng = np.random.default_rng(seed)
    ratios: list[float] = []
    for selected in rng.integers(0, len(cooks), size=(10_000, len(cooks))):
        sample: list[_Origin] = []
        for index in selected:
            cook = cooks[int(index)]
            sample.extend(_sample_blocks(by_cook[cook], horizon, starts[cook], rng))
        challenger = _rmse(origin.payload.challenger_error_c for origin in sample)
        incumbent = _rmse(origin.payload.incumbent_error_c for origin in sample)
        if challenger is None or incumbent is None or incumbent == 0.0:
            return ()
        ratio = challenger / incumbent
        if not isfinite(ratio):
            return ()
        ratios.append(ratio)
    return tuple(ratios)


def _block_starts(rows: Sequence[_Origin], horizon: int) -> tuple[int, ...]:
    return tuple(
        start
        for start in range(len(rows) - horizon + 1)
        if all(
            rows[index].record.session_id == rows[index + 1].record.session_id
            and rows[index].payload.origin_sequence + 1 == rows[index + 1].payload.origin_sequence
            for index in range(start, start + horizon - 1)
        )
    )


def _sample_blocks(
    rows: Sequence[_Origin], horizon: int, starts: Sequence[int], rng: np.random.Generator
) -> tuple[_Origin, ...]:
    sampled: list[_Origin] = []
    while len(sampled) < len(rows):
        start = starts[int(rng.integers(0, len(starts)))]
        sampled.extend(rows[start : start + horizon])
    return tuple(sampled[: len(rows)])


def _rmse(values: Sequence[float] | object) -> float | None:
    errors = tuple(values)  # type: ignore[arg-type]
    return (
        sqrt(sum(value * value for value in errors) / len(errors))
        if errors and all(isfinite(value) for value in errors)
        else None
    )


def _stratum_rows(origins: Sequence[_Origin], interval: BootstrapInterval) -> tuple[_Origin, ...]:
    key = (
        interval.horizon_steps,
        interval.temperature_band,
        interval.phase,
        interval.ambient_source,
        interval.generation,
    )
    return tuple(origin for origin in origins if origin.stratum == key)


def _signed_bias_ok(origins: Sequence[_Origin], interval: BootstrapInterval, maximum: float) -> bool:
    rows = _stratum_rows(origins, interval)
    return bool(rows) and abs(sum(row.payload.challenger_error_c for row in rows) / len(rows)) <= maximum


def _band_error_ok(origins: Sequence[_Origin], interval: BootstrapInterval, maximum: float) -> bool:
    rows = _stratum_rows(origins, interval)
    return bool(rows) and sum(abs(row.payload.challenger_error_c) for row in rows) / len(rows) <= maximum


def _cook_weight_ok(origins: Sequence[_Origin], interval: BootstrapInterval) -> bool:
    return len({row.cook for row in _stratum_rows(origins, interval)}) >= 2


def _relative_rmse_ok(interval: BootstrapInterval) -> bool:
    return (
        interval.challenger_rmse_c is not None
        and interval.incumbent_rmse_c is not None
        and interval.challenger_rmse_c < interval.incumbent_rmse_c
    )


def _braking_ok(refresh: RefreshDiagnosticsEvidence | TimingDistributionEvidence | None, tolerance: float) -> bool:
    return (
        isinstance(refresh, RefreshDiagnosticsEvidence)
        and refresh.braking_error_c is not None
        and refresh.incumbent_braking_error_c is not None
        and refresh.braking_error_c <= refresh.incumbent_braking_error_c + tolerance
    )


def _one_provenance(records: Sequence[ModelEvidenceRecord]) -> bool:
    values = {record.provenance_digest for record in records}
    return len(values) == 1 and None not in values


def _gate(gates: list[GateResult], name: str, passed: bool, reason: str) -> None:
    gates.append(GateResult(name, passed, None if passed else reason))


def _status(
    authoritative: ConfidenceStatus | None,
    invalidated: bool,
    records: Sequence[ModelEvidenceRecord],
    refresh: RefreshDiagnosticsEvidence | TimingDistributionEvidence | None,
    blockers: Sequence[str],
) -> ConfidenceStatus:
    if authoritative is not None:
        return authoritative
    if invalidated:
        return ConfidenceStatus.SCHEMA_INVALIDATED
    if not records:
        return ConfidenceStatus.COLLECTING
    if not isinstance(refresh, RefreshDiagnosticsEvidence):
        return ConfidenceStatus.FITTING
    if "calibration-completeness" in blockers or "identifiability" in blockers:
        return ConfidenceStatus.INSUFFICIENT_EXCITATION
    return ConfidenceStatus.READY_FOR_REVIEW if not blockers else ConfidenceStatus.EVALUATING


def _authoritative_status(state: Mapping[object, object]) -> ConfidenceStatus | None:
    value = _text(state.get("status"))
    return ConfidenceStatus(value) if value in {"active", "fallback", "schema-invalidated"} else None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _nonnegative_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _label(interval: BootstrapInterval) -> str:
    return f"{interval.horizon_steps}/{interval.temperature_band}/{interval.phase}/{interval.ambient_source}/{interval.generation}"
