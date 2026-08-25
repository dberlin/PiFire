"""Shared evidence-building helpers for the model-confidence test suites.

Extracted from test_model_confidence.py so other test modules (e.g.
test_confidence_bootstrap.py) can reuse this fixture-construction machinery
without importing a test module directly (importing a test module runs its
module-level code and collection side effects, and couples the two files).
"""

from __future__ import annotations

from hashlib import sha256

from common.control_trace import AmbientSource
from common.model_evidence import (
    CalibrationSummaryEvidence,
    CandidateAssessmentEvidence,
    EvidenceKind,
    ForecastOriginEvidence,
    ModelEvidencePayload,
    ModelEvidenceRecord,
    TimingDistributionEvidence,
)
from controller.model_learning.contracts import CandidateOrigin, LearningStatus

_CANDIDATE = sha256(b"candidate").hexdigest()
_INCUMBENT = sha256(b"incumbent").hexdigest()


def _rebuild(
    record: ModelEvidenceRecord,
    *,
    payload: ModelEvidencePayload | None = None,
    **changes: object,
) -> ModelEvidenceRecord:
    values = {
        "evidence_id": record.evidence_id,
        "kind": record.kind,
        "session_id": record.session_id,
        "cook_id": record.cook_id,
        "timestamp_ms": record.timestamp_ms,
        "role_generation": record.role_generation,
        "model_digest": record.model_digest,
        "provenance_digest": record.provenance_digest,
        "schema_version": record.schema_version,
        "payload": record.payload if payload is None else payload,
    }
    values.update(changes)
    return ModelEvidenceRecord.model_validate(values)


def _record(
    kind: EvidenceKind,
    payload: ModelEvidencePayload,
    *,
    cook: str = "cook-a",
    timestamp: int = 1,
) -> ModelEvidenceRecord:
    return ModelEvidenceRecord(
        evidence_id=f"{kind.value}:{cook}:{timestamp}",
        kind=kind,
        session_id=f"session-{cook}",
        cook_id=cook,
        timestamp_ms=timestamp,
        role_generation=4,
        model_digest=_CANDIDATE,
        provenance_digest=_INCUMBENT,
        payload=payload,
    )


def _calibration_records() -> tuple[ModelEvidenceRecord, ...]:
    return tuple(
        _record(
            EvidenceKind.CALIBRATION_SUMMARY,
            CalibrationSummaryEvidence(
                accepted=True,
                probe_count=0,
                stage=stage,  # type: ignore[arg-type]
                completed_stages=("low", "middle", "high") if stage == "coast" else (),
                continuous=True,
            ),
            timestamp=index,
        )
        for index, stage in enumerate(("low", "middle", "high", "coast"), 1)
    )


def _qualifying(*, include_calibration: bool = False) -> tuple[ModelEvidenceRecord, ...]:
    records: list[ModelEvidenceRecord] = list(_calibration_records() if include_calibration else ())
    records.extend(
        (
            _record(
                EvidenceKind.CANDIDATE_ASSESSMENT,
                CandidateAssessmentEvidence(
                    decision_id="candidate-assessment-1",
                    origin="passive-online",
                    policy="passive-auto",
                    fit_accepted=True,
                    identifiability_accepted=True,
                    native_build="passed",
                    native_dry_solve="passed",
                    target_timing="passed",
                    confidence_accepted=True,
                ),
            ),
            _record(
                EvidenceKind.TIMING_DISTRIBUTION,
                TimingDistributionEvidence(
                    sample_count=50,
                    p50_ms=10.0,
                    p95_ms=20.0,
                    p99_ms=200.0,
                    hardware_provenance="target-hardware",
                ),
                timestamp=6,
            ),
        )
    )
    timestamp = 7
    for cook in ("cook-a", "cook-b"):
        for horizon in (3, 15, 45, 90, 180):
            for sequence in range(horizon):
                error = (-0.5, 0.5, 0.0)[sequence % 3]
                records.append(
                    _record(
                        EvidenceKind.FORECAST_ORIGIN,
                        ForecastOriginEvidence(
                            origin_sequence=sequence,
                            origin_time_ms=sequence * 25,
                            completion_time_ms=(sequence + horizon) * 25,
                            horizon_steps=horizon,
                            incumbent_digest=_INCUMBENT,
                            challenger_digest=_CANDIDATE,
                            incumbent_prediction_c=100.0,
                            challenger_prediction_c=100.0,
                            observed_temperature_c=100.0 + error,
                            incumbent_error_c=2.0 * error,
                            challenger_error_c=error,
                            temperature_band="middle",
                            phase="heating",
                            ambient_source=AmbientSource.CONFIGURED,
                            calibration_fit=False,
                        ),
                        cook=cook,
                        timestamp=timestamp,
                    )
                )
                timestamp += 1
    return tuple(records)


def _state(
    *,
    origin: CandidateOrigin = CandidateOrigin.PASSIVE_ONLINE,
    status: LearningStatus = LearningStatus.COLLECTING,
) -> dict[str, object]:
    return {
        "status": status.value,
        "active_kind": "grey-box",
        "candidate_digest": _CANDIDATE,
        "role_generation": 4,
        "candidate_generation": 9,
        "origin": origin.value,
    }
