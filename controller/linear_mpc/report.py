"""Deterministic, read-only projections of the compact model-evidence ledger."""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Mapping, Sequence, cast

from pydantic import ValidationError

from common.model_evidence import (
    MODEL_EVIDENCE_SCHEMA_VERSION,
    ActivationEvidence,
    CalibrationSummaryEvidence,
    ConfidenceDecisionEvidence,
    FallbackEvidence,
    ForecastOriginEvidence,
    ModelEvidenceRecord,
    RecorderGapEvidence,
    RefreshDiagnosticsEvidence,
    RollbackEvidence,
    SchemaInvalidationEvidence,
    SessionSummaryEvidence,
    TimingDistributionEvidence,
)
from controller.linear_mpc.confidence import ConfidenceConfig, ConfidenceReport, ConfidenceStatus, evaluate_confidence

REPORT_SCHEMA_VERSION = 1
ARTIFACT_SCHEMA = "pifire-model-evidence/v1"
_DEFAULT_MODEL_KIND = "grey-box"
_CANDIDATE_MODEL_KIND = "innovation-state-space"
_STAGE_ORDER = ("low", "middle", "high", "coast")
_HISTORY_PAYLOADS = (ActivationEvidence, RollbackEvidence, FallbackEvidence)
_GLOBAL_EVIDENCE_PAYLOADS = (
    CalibrationSummaryEvidence,
    SessionSummaryEvidence,
    RecorderGapEvidence,
    SchemaInvalidationEvidence,
)

_REPORT_CACHE_MAX_ENTRIES = 8
_REPORT_CACHE: OrderedDict[str, EvidenceReport] = OrderedDict()
_REPORT_CACHE_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class ArtifactMetadata:
    schema_version: int
    provenance_digest: str | None
    bootstrap_seed: int
    bootstrap_replicates: int
    decision_id: str | None
    evidence_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "provenance_digest": self.provenance_digest,
            "bootstrap_seed": self.bootstrap_seed,
            "bootstrap_replicates": self.bootstrap_replicates,
            "decision_id": self.decision_id,
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True, slots=True)
class EvidenceReport:
    """An immutable canonical report; decoding it creates data, never model state."""

    artifact_metadata: ArtifactMetadata
    _canonical_json: bytes

    def to_dict(self) -> dict[str, object]:
        decoded = json.loads(self._canonical_json)
        if not isinstance(decoded, dict):  # Construction below makes this unreachable.
            raise ValueError("evidence report must decode to an object")
        return cast(dict[str, object], decoded)


def build_evidence_report(
    report: ConfidenceReport,
    records: Sequence[ModelEvidenceRecord],
    *,
    activation_state: object = None,
) -> EvidenceReport:
    """Project a confidence decision and validated ledger into stable operator data."""
    if not isinstance(report, ConfidenceReport):
        raise TypeError("report must be a ConfidenceReport")
    validated = _validated_records(records)
    selected = _candidate_records(report, validated)
    if report.candidate_digest is not None and report.generation is not None:
        decision_source = tuple(
            record
            for record in validated
            if record.model_digest == report.candidate_digest and record.role_generation == report.generation
        )
    else:
        decision_source = validated
    decision_record = _latest_payload(decision_source, ConfidenceDecisionEvidence)
    decision_id = (
        cast(ConfidenceDecisionEvidence, decision_record.payload).decision_id if decision_record is not None else None
    )
    references = _referenced_records(selected, validated, decision_record)
    provenance_digest = _one_text(record.provenance_digest for record in selected)
    activation_record = _activation_identity_record(report, validated, activation_state)
    default_digest = activation_record.provenance_digest if activation_record is not None else _default_digest(selected)
    if provenance_digest is None:
        provenance_digest = default_digest
    metadata = ArtifactMetadata(
        schema_version=REPORT_SCHEMA_VERSION,
        provenance_digest=provenance_digest,
        bootstrap_seed=report.bootstrap_seed,
        bootstrap_replicates=report.bootstrap_replicates,
        decision_id=decision_id,
        evidence_ids=tuple(sorted(record.evidence_id for record in references)),
    )
    active_kind = report.active_kind or _DEFAULT_MODEL_KIND
    active_digest = default_digest
    if active_kind != _DEFAULT_MODEL_KIND:
        active_digest = activation_record.model_digest if activation_record is not None else report.candidate_digest
    payload: dict[str, object] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": report.status.value,
        "decision_id": decision_id,
        "active_model": {
            "kind": active_kind,
            "digest": active_digest,
        },
        "default_model": {"kind": _DEFAULT_MODEL_KIND, "digest": default_digest},
        "candidate": {
            "kind": _CANDIDATE_MODEL_KIND,
            "generation": report.generation,
            "digest": report.candidate_digest,
        },
        "calibration": _calibration(validated),
        "identifiability": _identifiability(selected),
        "scores": _scores(report, selected),
        "gates": [{"name": gate.name, "passed": gate.passed, "reason": gate.reason} for gate in report.gates],
        "missing_gates": [gate.name for gate in report.gates if not gate.passed],
        "blockers": list(report.blockers),
        "target_timing": _target_timing(report, selected),
        "history": _history(validated),
        "ambient_provenance_limitation": _ambient_limitation(selected),
        "artifact_metadata": metadata.to_dict(),
    }
    return EvidenceReport(metadata, _canonical_bytes(payload))


def build_evidence_artifact(report: EvidenceReport | ConfidenceReport, records: Sequence[ModelEvidenceRecord]) -> bytes:
    """Return canonical evidence bytes containing exactly the report's references.

    This is deliberately an evidence-only envelope.  No loader or model-state
    constructor accepts its schema, and producing it has no persistence or
    controller side effects.
    """
    validated = _validated_records(records)
    projected = build_evidence_report(report, validated) if isinstance(report, ConfidenceReport) else report
    if not isinstance(projected, EvidenceReport):
        raise TypeError("report must be an EvidenceReport or ConfidenceReport")
    by_id = {record.evidence_id: record for record in validated}
    missing = [evidence_id for evidence_id in projected.artifact_metadata.evidence_ids if evidence_id not in by_id]
    if missing:
        raise ValueError(f"missing referenced evidence_id {missing[0]!r}")
    artifact = {
        "artifact_schema": ARTIFACT_SCHEMA,
        "authority": "read-only-evidence",
        "schema_version": projected.artifact_metadata.schema_version,
        "provenance_digest": projected.artifact_metadata.provenance_digest,
        "bootstrap_seed": projected.artifact_metadata.bootstrap_seed,
        "bootstrap_replicates": projected.artifact_metadata.bootstrap_replicates,
        "decision_id": projected.artifact_metadata.decision_id,
        "evidence_ids": list(projected.artifact_metadata.evidence_ids),
        "report": projected.to_dict(),
        "records": [
            by_id[evidence_id].model_dump(mode="json") for evidence_id in projected.artifact_metadata.evidence_ids
        ],
    }
    return _canonical_bytes(artifact)


def current_evidence_report(
    records: Sequence[ModelEvidenceRecord], *, activation_state: object = None
) -> EvidenceReport:
    """Evaluate the current ledger once for each immutable ledger/activation state."""
    validated = _validated_records(records)
    cache_key = _report_cache_key(validated, activation_state)
    with _REPORT_CACHE_LOCK:
        cached = _REPORT_CACHE.get(cache_key)
        if cached is not None:
            _REPORT_CACHE.move_to_end(cache_key)
            return cached
        state = _confidence_state(validated, activation_state)
        confidence = evaluate_confidence(
            validated,
            activation_state=state,
            target_timing=None,
            config=ConfidenceConfig(),
        )
        projected = build_evidence_report(
            confidence,
            validated,
            activation_state=activation_state,
        )
        _REPORT_CACHE[cache_key] = projected
        _REPORT_CACHE.move_to_end(cache_key)
        while len(_REPORT_CACHE) > _REPORT_CACHE_MAX_ENTRIES:
            _REPORT_CACHE.popitem(last=False)
        return projected


def _validated_records(records: Sequence[ModelEvidenceRecord]) -> tuple[ModelEvidenceRecord, ...]:
    if isinstance(records, (str, bytes)) or not isinstance(records, Sequence):
        raise TypeError("records must be a sequence of ModelEvidenceRecord values")
    validated: list[ModelEvidenceRecord] = []
    seen: set[str] = set()
    for index, record in enumerate(records):
        evidence_id = getattr(record, "evidence_id", None)
        label = evidence_id if isinstance(evidence_id, str) and evidence_id else f"at-index-{index}"
        if not isinstance(record, ModelEvidenceRecord):
            raise ValueError(f"invalid ledger record {label!r}")
        try:
            owned = ModelEvidenceRecord.model_validate_json(record.model_dump_json())
        except (ValidationError, ValueError, TypeError) as exc:
            raise ValueError(f"invalid ledger record {label!r}") from exc
        if owned.evidence_id in seen:
            raise ValueError(f"duplicate evidence_id {owned.evidence_id!r}")
        seen.add(owned.evidence_id)
        validated.append(owned)
    return tuple(sorted(validated, key=lambda value: (value.timestamp_ms, value.evidence_id)))


def _candidate_records(
    report: ConfidenceReport, records: Sequence[ModelEvidenceRecord]
) -> tuple[ModelEvidenceRecord, ...]:
    return tuple(
        record
        for record in records
        if record.model_digest == report.candidate_digest
        and record.role_generation == report.generation
        and isinstance(
            record.payload,
            (ForecastOriginEvidence, RefreshDiagnosticsEvidence, TimingDistributionEvidence),
        )
    )


def _referenced_records(
    selected: Sequence[ModelEvidenceRecord],
    records: Sequence[ModelEvidenceRecord],
    decision: ModelEvidenceRecord | None,
) -> tuple[ModelEvidenceRecord, ...]:
    referenced = {record.evidence_id: record for record in selected}
    for record in records:
        if isinstance(record.payload, _HISTORY_PAYLOADS) or (
            record.schema_version == MODEL_EVIDENCE_SCHEMA_VERSION
            and isinstance(record.payload, _GLOBAL_EVIDENCE_PAYLOADS)
        ):
            referenced[record.evidence_id] = record
    if decision is not None:
        referenced[decision.evidence_id] = decision
    return tuple(referenced[key] for key in sorted(referenced))


def _calibration(records: Sequence[ModelEvidenceRecord]) -> dict[str, object]:
    calibration_records = tuple(
        record
        for record in records
        if record.schema_version == MODEL_EVIDENCE_SCHEMA_VERSION
        and isinstance(record.payload, CalibrationSummaryEvidence)
    )
    reset_records = tuple(
        record
        for record in calibration_records
        if cast(CalibrationSummaryEvidence, record.payload).command_action == "reset-progress"
        or cast(
            CalibrationSummaryEvidence,
            record.payload,
        ).cancellation_command_action
        == "reset-progress"
    )
    reset_record = max(
        reset_records,
        key=lambda record: (record.timestamp_ms, record.evidence_id),
        default=None,
    )
    reset_key = (reset_record.timestamp_ms, reset_record.evidence_id) if reset_record is not None else None
    calibration = tuple(
        record
        for record in calibration_records
        if reset_key is None or (record.timestamp_ms, record.evidence_id) > reset_key
    )
    summaries = tuple(
        cast(SessionSummaryEvidence, record.payload)
        for record in records
        if record.schema_version == MODEL_EVIDENCE_SCHEMA_VERSION
        and isinstance(record.payload, SessionSummaryEvidence)
        and (reset_key is None or (record.timestamp_ms, record.evidence_id) > reset_key)
    )
    latest = cast(CalibrationSummaryEvidence, calibration[-1].payload) if calibration else None
    completed: set[str] = set()
    reasons: set[str] = set()
    revisions: list[int] = []
    if reset_record is not None:
        reset = cast(CalibrationSummaryEvidence, reset_record.payload)
        revisions.extend(
            value
            for value in (
                reset.command_revision,
                reset.cancellation_command_revision,
            )
            if value is not None
        )
    for record in calibration:
        payload = cast(CalibrationSummaryEvidence, record.payload)
        if payload.continuous:
            completed.update(payload.completed_stages)
        if not payload.accepted and payload.reason is not None:
            reasons.add(payload.reason)
        if payload.cancellation_reason is not None:
            reasons.add(payload.cancellation_reason)
        revisions.extend(
            value
            for value in (
                payload.result_revision,
                payload.command_revision,
                payload.cancellation_command_revision,
            )
            if value is not None
        )
    if set(_STAGE_ORDER[:-1]).issubset(completed):
        completed.add(_STAGE_ORDER[-1])
    missing = [stage for stage in _STAGE_ORDER if stage not in completed]
    ordered_completed = [stage for stage in _STAGE_ORDER if stage in completed]
    if summaries:
        eligible_count = sum(summary.accepted_observations for summary in summaries)
        ineligible_count = sum(summary.rejected_observations for summary in summaries)
        for summary in summaries:
            reasons.update(summary.rejection_reasons)
    else:
        eligible_count = latest.eligible_observations if latest is not None else 0
        ineligible_count = 0
    timed_out = any("timeout" in reason.lower() for reason in reasons)
    if latest is None:
        status = "inactive"
    elif latest.status == "active" and latest.command_action == "pause":
        status = "paused"
    elif not missing and latest.status == "inactive":
        status = "accepted"
    else:
        status = latest.status
    return {
        "status": status,
        "stage": latest.stage if latest is not None else None,
        "current_probe": latest.probe_q if latest is not None and latest.status == "active" else None,
        "completed_stages": ordered_completed,
        "missing_stages": missing,
        "eligible_count": eligible_count,
        "ineligible_count": ineligible_count,
        "ineligible_reasons": sorted(reasons),
        "timed_out": timed_out,
        "incomplete": bool(missing)
        or status
        in {
            "inactive",
            "rejected",
            "active",
            "paused",
            "cancelled",
        },
        "revision": max(revisions, default=0),
    }


def _identifiability(records: Sequence[ModelEvidenceRecord]) -> dict[str, object]:
    record = _latest_payload(records, RefreshDiagnosticsEvidence)
    payload = cast(RefreshDiagnosticsEvidence, record.payload) if record is not None else None
    return {
        "available": payload is not None,
        "accepted": payload.accepted if payload is not None else False,
        "reason": payload.reason if payload is not None else None,
        "full_rank": payload.full_rank if payload is not None else False,
        "finite_diagnostics": payload.finite_diagnostics if payload is not None else False,
        "pole_magnitude": payload.pole_magnitude if payload is not None else None,
        "gain": payload.gain if payload is not None else None,
        "delay_steps": payload.delay_steps if payload is not None else None,
        "covariance_finite": payload.covariance_finite if payload is not None else False,
        "alignment_error_c": payload.alignment_error_c if payload is not None else None,
        "snapshot_round_trip": payload.snapshot_round_trip if payload is not None else False,
        "sequential_wins": payload.sequential_wins if payload is not None else 0,
        "generation_continuity": payload.generation_continuity if payload is not None else False,
        "atomic_persistence": payload.atomic_persistence if payload is not None else False,
        "production_prospective": payload.production_prospective if payload is not None else False,
        "braking_error_c": payload.braking_error_c if payload is not None else None,
        "incumbent_braking_error_c": payload.incumbent_braking_error_c if payload is not None else None,
    }


def _scores(report: ConfidenceReport, records: Sequence[ModelEvidenceRecord]) -> list[dict[str, object]]:
    origins = tuple(
        (record, cast(ForecastOriginEvidence, record.payload))
        for record in records
        if isinstance(record.payload, ForecastOriginEvidence)
    )
    scores: list[dict[str, object]] = []
    for interval in sorted(
        report.bootstrap_intervals,
        key=lambda value: (
            value.horizon_steps,
            value.temperature_band,
            value.phase,
            value.ambient_source,
            value.generation,
        ),
    ):
        matching = tuple(
            payload
            for record, payload in origins
            if record.role_generation == interval.generation
            and payload.horizon_steps == interval.horizon_steps
            and payload.temperature_band == interval.temperature_band
            and payload.phase == interval.phase
            and payload.ambient_source.value == interval.ambient_source
        )
        challenger = tuple(payload.challenger_error_c for payload in matching)
        incumbent = tuple(payload.incumbent_error_c for payload in matching)
        scores.append(
            {
                "horizon_steps": interval.horizon_steps,
                "temperature_band": interval.temperature_band,
                "phase": interval.phase,
                "ambient_source": interval.ambient_source,
                "generation": interval.generation,
                "challenger_rmse_c": interval.challenger_rmse_c,
                "incumbent_rmse_c": interval.incumbent_rmse_c,
                "challenger_bias_c": _mean(challenger),
                "incumbent_bias_c": _mean(incumbent),
                "challenger_band_error_c": _mean_absolute(challenger),
                "incumbent_band_error_c": _mean_absolute(incumbent),
                "bootstrap": {
                    "available": interval.available,
                    "method": interval.method,
                    "replicate_count": interval.replicate_count,
                    "rmse_ratio_upper_bound": interval.upper_bound,
                },
            }
        )
    return scores


def _target_timing(report: ConfidenceReport, records: Sequence[ModelEvidenceRecord]) -> dict[str, object]:
    record = _latest_payload(records, TimingDistributionEvidence)
    payload = cast(TimingDistributionEvidence, record.payload) if record is not None else None
    gate = next((gate for gate in report.gates if gate.name == "target-timing"), None)
    return {
        "available": payload is not None,
        "sample_count": payload.sample_count if payload is not None else 0,
        "p50_ms": payload.p50_ms if payload is not None else None,
        "p95_ms": payload.p95_ms if payload is not None else None,
        "p99_ms": payload.p99_ms if payload is not None else None,
        "hardware_provenance": payload.hardware_provenance if payload is not None else None,
        "gate_passed": gate.passed if gate is not None else False,
    }


def _history(records: Sequence[ModelEvidenceRecord]) -> list[dict[str, object]]:
    history: list[dict[str, object]] = []
    for record in records:
        payload = record.payload
        if isinstance(payload, ActivationEvidence):
            history.append(
                {
                    "evidence_id": record.evidence_id,
                    "timestamp_ms": record.timestamp_ms,
                    "event": "activation",
                    "decision_id": payload.decision_id,
                    "reason": None,
                }
            )
        elif isinstance(payload, RollbackEvidence):
            history.append(
                {
                    "evidence_id": record.evidence_id,
                    "timestamp_ms": record.timestamp_ms,
                    "event": "rollback",
                    "decision_id": payload.decision_id,
                    "reason": payload.reason,
                }
            )
        elif isinstance(payload, FallbackEvidence):
            history.append(
                {
                    "evidence_id": record.evidence_id,
                    "timestamp_ms": record.timestamp_ms,
                    "event": "fallback",
                    "decision_id": None,
                    "reason": payload.reason,
                }
            )
    return history


def _ambient_limitation(records: Sequence[ModelEvidenceRecord]) -> str | None:
    sources = sorted(
        {
            cast(ForecastOriginEvidence, record.payload).ambient_source.value
            for record in records
            if isinstance(record.payload, ForecastOriginEvidence)
        }
    )
    if not sources:
        return "no forecast ambient provenance is available"
    non_measured = [source for source in sources if source != "measured"]
    if not non_measured:
        return None
    joined = ", ".join(non_measured)
    if "measured" not in sources:
        return f"forecast evidence uses {joined} ambient provenance; no measured ambient evidence is present"
    return f"forecast evidence mixes measured and {joined} ambient provenance"


def _latest_payload(records: Sequence[ModelEvidenceRecord], payload_type: type) -> ModelEvidenceRecord | None:
    matches = [record for record in records if isinstance(record.payload, payload_type)]
    return max(matches, key=lambda record: (record.timestamp_ms, record.evidence_id)) if matches else None


def _default_digest(records: Sequence[ModelEvidenceRecord]) -> str | None:
    forecasts = tuple(record for record in records if isinstance(record.payload, ForecastOriginEvidence))
    source = forecasts if forecasts else records
    return _one_text(record.provenance_digest for record in source)


def _activation_identity_record(
    report: ConfidenceReport,
    records: Sequence[ModelEvidenceRecord],
    activation_state: object,
) -> ModelEvidenceRecord | None:
    decision_id = getattr(activation_state, "evidence_decision_id", None)
    matches = tuple(
        record
        for record in records
        if isinstance(record.payload, ActivationEvidence)
        and (decision_id is None or cast(ActivationEvidence, record.payload).decision_id == decision_id)
    )
    if decision_id is not None:
        return _latest_payload(matches, ActivationEvidence)
    if report.active_kind != _DEFAULT_MODEL_KIND:
        return _latest_payload(matches, ActivationEvidence)
    return None


def _one_text(values: Iterable[object]) -> str | None:
    unique = {value for value in values if isinstance(value, str)}
    return next(iter(unique)) if len(unique) == 1 else None


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _mean_absolute(values: Sequence[float]) -> float | None:
    return sum(abs(value) for value in values) / len(values) if values else None


def _canonical_bytes(value: Mapping[str, object]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _report_cache_key(
    records: Sequence[ModelEvidenceRecord],
    activation_state: object,
) -> str:
    activation = (
        None
        if activation_state is None
        else {
            "active_snapshot_json": getattr(activation_state, "active_snapshot_json", None),
            "rollback_snapshot_json": getattr(activation_state, "rollback_snapshot_json", None),
            "evidence_decision_id": getattr(activation_state, "evidence_decision_id", None),
            "controller_configuration_digest": getattr(
                activation_state,
                "controller_configuration_digest",
                None,
            ),
            "role_generation": getattr(activation_state, "role_generation", None),
        }
    )
    encoded = _canonical_bytes(
        {
            "records": [record.model_dump(mode="json") for record in records],
            "activation_state": activation,
        }
    )
    return hashlib.sha256(encoded).hexdigest()


def _confidence_state(records: Sequence[ModelEvidenceRecord], activation_state: object) -> dict[str, object]:
    candidate_records = tuple(
        record
        for record in records
        if record.model_digest is not None
        and isinstance(
            record.payload,
            (ForecastOriginEvidence, RefreshDiagnosticsEvidence, TimingDistributionEvidence),
        )
    )
    latest = max(
        candidate_records,
        key=lambda record: (record.role_generation, record.timestamp_ms, record.evidence_id),
        default=None,
    )
    state: dict[str, object] = {
        "active_kind": _CANDIDATE_MODEL_KIND if activation_state is not None else _DEFAULT_MODEL_KIND,
    }
    if activation_state is not None:
        state["status"] = ConfidenceStatus.ACTIVE.value
    if latest is not None:
        state["candidate_digest"] = latest.model_digest
        state["candidate_generation"] = latest.role_generation
    lifecycle = max(
        (
            record
            for record in records
            if isinstance(
                record.payload,
                (ActivationEvidence, RollbackEvidence, FallbackEvidence, SchemaInvalidationEvidence),
            )
        ),
        key=lambda record: (record.timestamp_ms, record.evidence_id),
        default=None,
    )
    if lifecycle is not None:
        if isinstance(lifecycle.payload, ActivationEvidence):
            state["status"] = ConfidenceStatus.ACTIVE.value
            state["active_kind"] = _CANDIDATE_MODEL_KIND
        elif isinstance(lifecycle.payload, (RollbackEvidence, FallbackEvidence)):
            state["status"] = ConfidenceStatus.FALLBACK.value
            state["active_kind"] = _DEFAULT_MODEL_KIND
        elif isinstance(lifecycle.payload, SchemaInvalidationEvidence):
            state["status"] = ConfidenceStatus.SCHEMA_INVALIDATED.value
    return state
