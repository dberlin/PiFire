"""Frozen, versioned compact evidence used outside the control-path trace sink."""

from __future__ import annotations

import json
from dataclasses import dataclass as std_dataclass
from enum import StrEnum
from typing import Annotated, ClassVar, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, ValidationError, field_validator, model_validator
from pydantic.dataclasses import dataclass

from common.control_trace import AllocationClampReason, AmbientSource

MODEL_EVIDENCE_SCHEMA_VERSION = 1

FiniteFloat: TypeAlias = Annotated[float, Field(allow_inf_nan=False, strict=True)]
NonNegativeFloat: TypeAlias = Annotated[FiniteFloat, Field(ge=0)]
NonNegativeInt: TypeAlias = Annotated[int, Field(ge=0, strict=True)]
NonBlankString: TypeAlias = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]
Digest: TypeAlias = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")]
_DATACLASS_CONFIG = ConfigDict(extra="forbid", strict=True, validate_default=True)


class EvidenceKind(StrEnum):
    SESSION_SUMMARY = "session_summary"
    CALIBRATION_SUMMARY = "calibration_summary"
    FORECAST_ORIGIN = "forecast_origin"
    REFRESH_DIAGNOSTICS = "refresh_diagnostics"
    TIMING_DISTRIBUTION = "timing_distribution"
    CONFIDENCE_DECISION = "confidence_decision"
    ACTIVATION = "activation"
    ROLLBACK = "rollback"
    FALLBACK = "fallback"
    RECORDER_GAP = "recorder_gap"
    SCHEMA_INVALIDATION = "schema_invalidation"


@dataclass(frozen=True, slots=True, config=_DATACLASS_CONFIG)
class SessionSummaryEvidence:
    completed_origins: NonNegativeInt
    accepted_observations: NonNegativeInt
    rejected_observations: NonNegativeInt
    payload_type: Literal["session_summary"] = "session_summary"

@dataclass(frozen=True, slots=True, config=_DATACLASS_CONFIG)
class AllocationEvidence:
    normalized_combustion_load: NonNegativeFloat
    auger_duty: NonNegativeFloat
    fan_duty: NonNegativeFloat | None
    u_max: NonNegativeFloat
    fan_min_pct: NonNegativeFloat
    fan_max_pct: NonNegativeFloat
    fan_enabled: bool
    auger_clamp_reason: AllocationClampReason
    fan_clamp_reason: AllocationClampReason
    allocator_revision: NonNegativeInt

    @model_validator(mode="after")
    def validate_allocator_inputs(self) -> AllocationEvidence:
        if self.normalized_combustion_load > 1.0:
            raise ValueError("allocation normalized combustion load must not exceed one")
        if self.auger_duty > self.u_max:
            raise ValueError("allocation auger duty must not exceed u_max")
        if self.fan_min_pct > self.fan_max_pct:
            raise ValueError("allocation fan bounds must be ordered")
        if self.fan_enabled != (self.fan_duty is not None):
            raise ValueError("allocation fan output must match enabled input")
        return self



@dataclass(frozen=True, slots=True, config=_DATACLASS_CONFIG)
class CalibrationSummaryEvidence:
    accepted: bool
    probe_count: NonNegativeInt
    reason: NonBlankString | None = None
    result_revision: NonNegativeInt | None = None
    command_revision: NonNegativeInt | None = None
    command_action: Literal["none", "start", "pause", "resume", "stop", "reset-progress", "safety-cancel"] = "none"
    baseline_q: NonNegativeFloat | None = None
    probe_q: FiniteFloat | None = None
    combined_q: NonNegativeFloat | None = None
    baseline_allocation: AllocationEvidence | None = None
    combined_allocation: AllocationEvidence | None = None
    scheduled_on_seconds: NonNegativeFloat | None = None
    delivered_on_seconds: NonNegativeFloat | None = None
    requested_fan_duty: NonNegativeFloat | None = None
    actual_fan_duty: NonNegativeFloat | None = None
    cancellation_reason: NonBlankString | None = None
    status: Literal["inactive", "accepted", "rejected", "active", "cancelled"] = "inactive"
    cancellation_command_revision: NonNegativeInt = 0
    cancellation_command_action: Literal["none", "pause", "stop", "reset-progress", "safety-cancel"] = "none"
    payload_type: Literal["calibration_summary"] = "calibration_summary"

    @model_validator(mode="after")
    def validate_completed_frame(self) -> CalibrationSummaryEvidence:
        values = (
            self.result_revision,
            self.command_revision,
            self.baseline_q,
            self.probe_q,
            self.combined_q,
            self.baseline_allocation,
            self.combined_allocation,
            self.scheduled_on_seconds,
            self.delivered_on_seconds,
        )
        if any(value is not None for value in values):
            if any(value is None for value in values):
                raise ValueError("completed calibration frame evidence must be complete")
            assert self.baseline_q is not None and self.probe_q is not None and self.combined_q is not None
            assert self.baseline_allocation is not None and self.combined_allocation is not None
            if not -1.0 <= self.probe_q <= 1.0 or self.combined_q > 1.0:
                raise ValueError("calibration loads must be bounded")
            if abs(self.combined_q - min(1.0, max(0.0, self.baseline_q + self.probe_q))) > 1e-12:
                raise ValueError("combined calibration load must match baseline plus probe")
            if self.result_revision < 1:
                raise ValueError("completed calibration frame requires result revision")
            if self.command_revision != 0 and self.command_action == "none":
                raise ValueError("producing calibration command action is required")
            if self.baseline_allocation.normalized_combustion_load != self.baseline_q:
                raise ValueError("baseline allocation must match baseline load")
            if self.combined_allocation.normalized_combustion_load != self.combined_q:
                raise ValueError("combined allocation must match combined load")
            if self.delivered_on_seconds > self.scheduled_on_seconds:
                raise ValueError("delivered calibration on-time must not exceed scheduled on-time")
        if self.status == "active":
            if not self.accepted or self.probe_count != 1 or self.probe_q in (None, 0.0):
                raise ValueError("active calibration evidence requires one accepted completed probe")
        elif self.status == "accepted":
            if not self.accepted or self.probe_count != 0 or self.probe_q != 0.0:
                raise ValueError("accepted calibration evidence must not claim a probe")
        elif self.status == "rejected":
            if self.accepted or self.probe_count != 0 or self.probe_q != 0.0:
                raise ValueError("rejected calibration evidence must not claim a probe")
        elif self.status == "cancelled":
            if (
                self.accepted
                or self.probe_count != 0
                or self.cancellation_reason is None
                or self.cancellation_command_action == "none"
            ):
                raise ValueError("cancelled calibration evidence must retain cancellation attribution")
        return self


@dataclass(frozen=True, slots=True, config=_DATACLASS_CONFIG)
class ForecastOriginEvidence:
    origin_sequence: NonNegativeInt
    origin_time_ms: NonNegativeInt
    completion_time_ms: NonNegativeInt
    horizon_steps: Literal[3, 15, 45, 90, 180]
    incumbent_digest: Digest
    challenger_digest: Digest
    incumbent_prediction_c: FiniteFloat
    challenger_prediction_c: FiniteFloat
    observed_temperature_c: FiniteFloat
    incumbent_error_c: FiniteFloat
    challenger_error_c: FiniteFloat
    temperature_band: NonBlankString
    phase: Literal["heating", "coasting"]
    ambient_source: AmbientSource
    calibration_fit: bool
    payload_type: Literal["forecast_origin"] = "forecast_origin"

    @model_validator(mode="after")
    def validate_completed_validation_origin(self) -> ForecastOriginEvidence:
        if self.origin_time_ms >= self.completion_time_ms:
            raise ValueError("forecast origin must precede completion")
        if self.calibration_fit:
            raise ValueError("calibration-fit forecasts are not validation evidence")
        return self


@dataclass(frozen=True, slots=True, config=_DATACLASS_CONFIG)
class RefreshDiagnosticsEvidence:
    accepted: bool
    reason: NonBlankString | None = None
    payload_type: Literal["refresh_diagnostics"] = "refresh_diagnostics"


@dataclass(frozen=True, slots=True, config=_DATACLASS_CONFIG)
class TimingDistributionEvidence:
    sample_count: NonNegativeInt
    p50_ms: NonNegativeFloat
    p95_ms: NonNegativeFloat
    payload_type: Literal["timing_distribution"] = "timing_distribution"

    @model_validator(mode="after")
    def validate_percentiles(self) -> TimingDistributionEvidence:
        if self.p50_ms > self.p95_ms:
            raise ValueError("timing p50 must not exceed p95")
        return self


@dataclass(frozen=True, slots=True, config=_DATACLASS_CONFIG)
class ConfidenceDecisionEvidence:
    decision_id: NonBlankString
    blocked: bool
    reason: NonBlankString | None = None
    payload_type: Literal["confidence_decision"] = "confidence_decision"


@dataclass(frozen=True, slots=True, config=_DATACLASS_CONFIG)
class ActivationEvidence:
    decision_id: NonBlankString
    active_snapshot_json: NonBlankString
    rollback_snapshot_json: NonBlankString
    controller_configuration_digest: Digest
    payload_type: Literal["activation"] = "activation"

    @field_validator("active_snapshot_json", "rollback_snapshot_json")
    @classmethod
    def validate_snapshot_json(cls, value: str) -> str:
        try:
            decoded = json.loads(
                value,
                parse_constant=lambda constant: (_ for _ in ()).throw(ValueError(f"invalid JSON constant {constant}")),
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("activation snapshot must be valid JSON") from exc
        if not isinstance(decoded, dict):
            raise ValueError("activation snapshot must be a JSON object")
        return value


@dataclass(frozen=True, slots=True, config=_DATACLASS_CONFIG)
class RollbackEvidence:
    decision_id: NonBlankString
    reason: NonBlankString
    payload_type: Literal["rollback"] = "rollback"


@dataclass(frozen=True, slots=True, config=_DATACLASS_CONFIG)
class FallbackEvidence:
    reason: NonBlankString
    payload_type: Literal["fallback"] = "fallback"


@dataclass(frozen=True, slots=True, config=_DATACLASS_CONFIG)
class RecorderGapEvidence:
    lost_record_count: NonNegativeInt
    reason: NonBlankString
    payload_type: Literal["recorder_gap"] = "recorder_gap"


@dataclass(frozen=True, slots=True, config=_DATACLASS_CONFIG)
class SchemaInvalidationEvidence:
    previous_schema_version: NonNegativeInt
    reason: NonBlankString
    payload_type: Literal["schema_invalidation"] = "schema_invalidation"


ModelEvidencePayload: TypeAlias = Annotated[
    SessionSummaryEvidence
    | CalibrationSummaryEvidence
    | ForecastOriginEvidence
    | RefreshDiagnosticsEvidence
    | TimingDistributionEvidence
    | ConfidenceDecisionEvidence
    | ActivationEvidence
    | RollbackEvidence
    | FallbackEvidence
    | RecorderGapEvidence
    | SchemaInvalidationEvidence,
    Field(discriminator="payload_type"),
]
_PAYLOAD_ADAPTER: TypeAdapter[ModelEvidencePayload] = TypeAdapter(ModelEvidencePayload)
_JSON_VALUE_ADAPTER: TypeAdapter[object] = TypeAdapter(object)


@std_dataclass(frozen=True, slots=True)
class ModelEvidenceDbRow:
    evidence_id: str
    session_id: str
    cook_id: str | None
    timestamp_ms: int
    kind: str
    role_generation: int
    model_digest: str | None
    provenance_digest: str | None
    schema_version: int
    payload: str


class ModelEvidenceRecord(BaseModel):
    """Immutable, indexed envelope around exactly one compact evidence payload."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True, strict=True)

    evidence_id: NonBlankString
    kind: EvidenceKind
    session_id: NonBlankString
    cook_id: NonBlankString | None = None
    timestamp_ms: NonNegativeInt
    role_generation: NonNegativeInt
    model_digest: Digest | None
    provenance_digest: Digest | None
    schema_version: Literal[1] = MODEL_EVIDENCE_SCHEMA_VERSION
    payload: ModelEvidencePayload

    @model_validator(mode="after")
    def validate_kind(self) -> ModelEvidenceRecord:
        expected = EvidenceKind(self.payload.payload_type)
        if self.kind is not expected:
            raise ValueError("evidence kind does not match payload_type")
        return self

    def to_db_row(self) -> ModelEvidenceDbRow:
        return ModelEvidenceDbRow(
            evidence_id=self.evidence_id,
            session_id=self.session_id,
            cook_id=self.cook_id,
            timestamp_ms=self.timestamp_ms,
            kind=self.kind.value,
            role_generation=self.role_generation,
            model_digest=self.model_digest,
            provenance_digest=self.provenance_digest,
            schema_version=self.schema_version,
            payload=_PAYLOAD_ADAPTER.dump_json(self.payload).decode("utf-8"),
        )

    @classmethod
    def from_db_row(cls, row: ModelEvidenceDbRow) -> ModelEvidenceRecord:
        if not isinstance(row.payload, str):
            raise ValueError("model evidence payload column must be JSON text")
        try:
            payload = _JSON_VALUE_ADAPTER.validate_json(row.payload)
        except ValidationError as exc:
            raise ValueError("model evidence payload column is invalid JSON") from exc
        try:
            return cls.model_validate_json(
                json.dumps(
                    {
                        "evidence_id": row.evidence_id,
                        "session_id": row.session_id,
                        "cook_id": row.cook_id,
                        "timestamp_ms": row.timestamp_ms,
                        "kind": row.kind,
                        "role_generation": row.role_generation,
                        "model_digest": row.model_digest,
                        "provenance_digest": row.provenance_digest,
                        "schema_version": row.schema_version,
                        "payload": payload,
                    }
                )
            )
        except ValidationError as exc:
            raise ValueError("model evidence payload column is invalid payload") from exc
