"""Pure, fail-closed confidence scoring over validated compact ledger records."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite, sqrt

import numpy as np

from common.model_evidence import (
    MODEL_EVIDENCE_SCHEMA_VERSION,
    CalibrationSummaryEvidence,
    CandidateAssessmentEvidence,
    EvidenceKind,
    ForecastOriginEvidence,
    ModelEvidenceRecord,
    RecorderGapEvidence,
    TimingDistributionEvidence,
)
from .contracts import CandidateOrigin, LearningStatus

_REQUIRED_HORIZONS = (3, 15, 45, 90, 180)
_RMSE_LIMITS = {3: 2.8, 15: 2.8, 45: 2.8, 90: 5.0, 180: 5.0}
_REQUIRED_STAGES = frozenset(("low", "middle", "high", "coast"))




@dataclass(frozen=True, slots=True)
class ConfidenceConfig:
    bootstrap_seed: int = 0
    maximum_signed_bias_c: float = 0.25
    maximum_band_bias_c: float = 0.5
    maximum_refresh_p99_ms: float = 250.0
    required_sequential_wins: int = 2
    bootstrap_replicates: int = 10_000

    def __post_init__(self) -> None:
        if self.bootstrap_replicates != 10_000:
            raise ValueError("confidence bootstrap uses exactly 10,000 replicates")
        if isinstance(self.bootstrap_seed, bool) or not isinstance(self.bootstrap_seed, int):
            raise ValueError("bootstrap_seed must be an integer")
        values = (
            self.maximum_signed_bias_c,
            self.maximum_band_bias_c,
            self.maximum_refresh_p99_ms,
        )
        if self.required_sequential_wins < 1 or not all(
            isfinite(value) and value >= 0.0 for value in values
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
    status: LearningStatus
    active_kind: str | None
    candidate_digest: str | None
    generation: int | None
    gates: tuple[GateResult, ...]
    bootstrap_intervals: tuple[BootstrapInterval, ...]
    blockers: tuple[str, ...]
    bootstrap_seed: int
    bootstrap_replicates: int




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
    """Evaluate typed persisted grey-candidate evidence without control effects."""
    del target_timing  # Timing authority is a persisted TimingDistributionEvidence record.
    if not isinstance(config, ConfidenceConfig):
        raise TypeError("config must be ConfidenceConfig")
    records, ledger_valid = _records(evidence)
    state = activation_state if isinstance(activation_state, Mapping) else {}
    active_kind = _text(state.get("active_kind"))
    authoritative = _authoritative_status(state)
    digest = _text(state.get("candidate_digest"))
    role_generation = _nonnegative_int(state.get("role_generation"))
    candidate_generation = _nonnegative_int(state.get("candidate_generation"))
    origin = _candidate_origin(state.get("origin"))
    selected = tuple(
        record
        for record in records
        if digest is not None
        and role_generation is not None
        and record.model_digest == digest
        and record.role_generation == role_generation
    )
    origins, duplicate_conflict = _origins(selected)
    assessment = _newest_payload(selected, CandidateAssessmentEvidence)
    timing = _newest_payload(selected, TimingDistributionEvidence)
    schema_invalidated = _text(state.get("status")) == LearningStatus.SCHEMA_INVALIDATED.value

    gates: list[GateResult] = []
    _gate(gates, "ledger-integrity", ledger_valid and not duplicate_conflict, "ledger-integrity")
    _gate(
        gates,
        "candidate-lineage",
        digest is not None
        and role_generation is not None
        and candidate_generation is not None
        and bool(selected),
        "candidate-lineage",
    )
    _gate(gates, "candidate-origin", origin is not None, "candidate-origin")
    if origin is CandidateOrigin.OPERATOR_CALIBRATION:
        _gate(
            gates,
            "calibration-completeness",
            _calibration_complete(selected),
            "calibration-completeness",
        )
    _gate(
        gates,
        "fit-accepted",
        assessment is not None and assessment.fit_accepted,
        "fit-accepted",
    )
    _gate(
        gates,
        "identifiability",
        assessment is not None and assessment.identifiability_accepted,
        "identifiability",
    )
    _gate(
        gates,
        "native-build",
        assessment is not None and assessment.native_build == "passed",
        "native-build",
    )
    _gate(
        gates,
        "native-dry-solve",
        assessment is not None and assessment.native_dry_solve == "passed",
        "native-dry-solve",
    )
    _gate(
        gates,
        "target-timing",
        assessment is not None
        and assessment.target_timing == "passed"
        and isinstance(timing, TimingDistributionEvidence)
        and timing.hardware_provenance == "target-hardware"
        and timing.p99_ms is not None
        and timing.p99_ms <= config.maximum_refresh_p99_ms,
        "target-timing",
    )
    _gate(
        gates,
        "model-integrity",
        bool(selected) and _candidate_model_integrity(records, digest, role_generation),
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
    continuity_records = (
        record
        for record in records
        if isinstance(record.payload, RecorderGapEvidence)
        and role_generation is not None
        and record.role_generation == role_generation
        and record.model_digest in (None, digest)
    )
    for reason in dict.fromkeys(record.payload.reason for record in continuity_records):
        _gate(gates, "evidence-continuity", False, reason)
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
    status = _status(authoritative, schema_invalidated, selected, assessment, blockers)
    return ConfidenceReport(
        status,
        active_kind,
        digest,
        candidate_generation,
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
    payload_type: type[CandidateAssessmentEvidence] | type[TimingDistributionEvidence],
) -> CandidateAssessmentEvidence | TimingDistributionEvidence | None:
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




def _one_provenance(records: Sequence[ModelEvidenceRecord]) -> bool:
    values = {record.provenance_digest for record in records}
    return len(values) == 1 and None not in values


def _gate(gates: list[GateResult], name: str, passed: bool, reason: str) -> None:
    gates.append(GateResult(name, passed, None if passed else reason))


def _status(
    authoritative: LearningStatus | None,
    invalidated: bool,
    records: Sequence[ModelEvidenceRecord],
    assessment: CandidateAssessmentEvidence | None,
    blockers: Sequence[str],
) -> LearningStatus:
    if authoritative is not None:
        return authoritative
    if invalidated:
        return LearningStatus.SCHEMA_INVALIDATED
    if not any(record.kind is not EvidenceKind.RECORDER_GAP for record in records):
        return LearningStatus.COLLECTING
    if not isinstance(assessment, CandidateAssessmentEvidence):
        return LearningStatus.FITTING
    if "calibration-completeness" in blockers or "identifiability" in blockers:
        return LearningStatus.INSUFFICIENT_EXCITATION
    return LearningStatus.READY_FOR_REVIEW if not blockers else LearningStatus.EVALUATING


def _authoritative_status(state: Mapping[object, object]) -> LearningStatus | None:
    value = _text(state.get("status"))
    if value in {
        LearningStatus.ACTIVATING.value,
        LearningStatus.ACTIVE.value,
        LearningStatus.FALLBACK.value,
        LearningStatus.ERROR.value,
        LearningStatus.SCHEMA_INVALIDATED.value,
    }:
        return LearningStatus(value)
    return None


def _candidate_origin(value: object) -> CandidateOrigin | None:
    if isinstance(value, CandidateOrigin):
        return value
    return CandidateOrigin(value) if isinstance(value, str) and value in {item.value for item in CandidateOrigin} else None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _nonnegative_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _label(interval: BootstrapInterval) -> str:
    return f"{interval.horizon_steps}/{interval.temperature_band}/{interval.phase}/{interval.ambient_source}/{interval.generation}"
