from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from hashlib import sha256

import pytest

from common.control_trace import AmbientSource
from common.model_evidence import (
    CalibrationSummaryEvidence,
    EvidenceKind,
    ForecastOriginEvidence,
    ModelEvidenceRecord,
    RefreshDiagnosticsEvidence,
    TimingDistributionEvidence,
)
from controller.linear_mpc.confidence import ConfidenceConfig, ConfidenceStatus, evaluate_confidence


_CANDIDATE = sha256(b"candidate").hexdigest()
_INCUMBENT = sha256(b"incumbent").hexdigest()


def _record(kind: EvidenceKind, payload: object, *, cook: str = "cook-a", timestamp: int = 1) -> ModelEvidenceRecord:
    return ModelEvidenceRecord(
        evidence_id=f"{kind.value}:{cook}:{timestamp}", kind=kind, session_id=f"session-{cook}", cook_id=cook,
        timestamp_ms=timestamp, role_generation=4, model_digest=_CANDIDATE, provenance_digest=_INCUMBENT, payload=payload
    )


def _qualifying() -> tuple[ModelEvidenceRecord, ...]:
    records: list[ModelEvidenceRecord] = [
        _record(EvidenceKind.CALIBRATION_SUMMARY, CalibrationSummaryEvidence(accepted=True, probe_count=0, stage="low", continuous=True), timestamp=1),
        _record(EvidenceKind.CALIBRATION_SUMMARY, CalibrationSummaryEvidence(accepted=True, probe_count=0, stage="middle", continuous=True), timestamp=2),
        _record(EvidenceKind.CALIBRATION_SUMMARY, CalibrationSummaryEvidence(accepted=True, probe_count=0, stage="high", continuous=True), timestamp=3),
        _record(EvidenceKind.CALIBRATION_SUMMARY, CalibrationSummaryEvidence(accepted=True, probe_count=0, stage="coast", completed_stages=("low", "middle", "high"), continuous=True), timestamp=4),
        _record(EvidenceKind.REFRESH_DIAGNOSTICS, RefreshDiagnosticsEvidence(accepted=True, full_rank=True, finite_diagnostics=True, pole_magnitude=0.9, gain=1.0, delay_steps=3, covariance_finite=True, alignment_error_c=1.0, snapshot_round_trip=True, sequential_wins=2, generation_continuity=True, atomic_persistence=True, production_prospective=True, braking_error_c=1.0, incumbent_braking_error_c=2.0), timestamp=5),
        _record(EvidenceKind.TIMING_DISTRIBUTION, TimingDistributionEvidence(sample_count=50, p50_ms=10.0, p95_ms=20.0, p99_ms=200.0, hardware_provenance="target-hardware"), timestamp=6),
    ]
    timestamp = 7
    for cook in ("cook-a", "cook-b"):
        for horizon in (3, 15, 45, 90, 180):
            for sequence in range(horizon):
                error = (-0.5, 0.5, 0.0)[sequence % 3]
                payload = ForecastOriginEvidence(
                    origin_sequence=sequence, origin_time_ms=sequence * 20, completion_time_ms=(sequence + horizon) * 20,
                    horizon_steps=horizon, incumbent_digest=_INCUMBENT, challenger_digest=_CANDIDATE,
                    incumbent_prediction_c=100.0, challenger_prediction_c=100.0, observed_temperature_c=100.0 + error,
                    incumbent_error_c=2.0 * error, challenger_error_c=error, temperature_band="middle",
                    phase="heating", ambient_source=AmbientSource.CONFIGURED, calibration_fit=False,
                )
                records.append(_record(EvidenceKind.FORECAST_ORIGIN, payload, cook=cook, timestamp=timestamp))
                timestamp += 1
    return tuple(records)


def _state() -> dict[str, object]:
    return {"status": "collecting", "active_kind": "grey_box", "candidate_digest": _CANDIDATE, "candidate_generation": 4}


def _report(records: tuple[ModelEvidenceRecord, ...]):
    return evaluate_confidence(records, activation_state=_state(), target_timing=None, config=ConfidenceConfig(bootstrap_seed=7))


def test_typed_qualifying_ledger_is_ready_without_ownership_change() -> None:
    report = _report(_qualifying())
    assert report.status is ConfidenceStatus.READY_FOR_REVIEW
    assert report.active_kind == "grey_box"
    assert report.blockers == ()
    assert all(interval.replicate_count == 10_000 for interval in report.bootstrap_intervals)


@pytest.mark.parametrize(
    ("replacement", "expected"),
    [
        (
            lambda record: record.model_copy(
                update={"payload": replace(record.payload, full_rank=False)}
            ),
            ("identifiability",),
        ),
        (
            lambda record: record.model_copy(
                update={"payload": replace(record.payload, pole_magnitude=0.999)}
            ),
            ("pole-magnitude",),
        ),
        (
            lambda record: record.model_copy(
                update={"payload": replace(record.payload, gain=-1.0)}
            ),
            ("positive-gain",),
        ),
        (
            lambda record: record.model_copy(
                update={"payload": replace(record.payload, delay_steps=16)}
            ),
            ("delay-limit",),
        ),
        (
            lambda record: record.model_copy(
                update={"payload": replace(record.payload, covariance_finite=False)}
            ),
            ("finite-covariance",),
        ),
        (
            lambda record: record.model_copy(
                update={"payload": replace(record.payload, alignment_error_c=2.1)}
            ),
            ("state-alignment",),
        ),
        (
            lambda record: record.model_copy(
                update={"payload": replace(record.payload, snapshot_round_trip=False)}
            ),
            ("snapshot-round-trip",),
        ),
        (
            lambda record: record.model_copy(
                update={"payload": replace(record.payload, sequential_wins=1)}
            ),
            ("sequential-wins",),
        ),
        (
            lambda record: record.model_copy(
                update={"payload": replace(record.payload, generation_continuity=False)}
            ),
            ("generation-continuity",),
        ),
        (
            lambda record: record.model_copy(
                update={"payload": replace(record.payload, braking_error_c=3.0)}
            ),
            ("braking-error",),
        ),
        (
            lambda record: record.model_copy(
                update={"payload": replace(record.payload, atomic_persistence=False)}
            ),
            ("atomic-persistence",),
        ),
        (
            lambda record: record.model_copy(
                update={"payload": replace(record.payload, production_prospective=False)}
            ),
            ("production-prospective-construction",),
        ),
    ],
)
def test_each_refresh_gate_has_only_its_expected_blocker(replacement, expected: tuple[str, ...]) -> None:
    records = list(_qualifying())
    records[4] = replacement(records[4])
    report = _report(tuple(records))
    assert report.blockers == expected
    assert report.status is not ConfidenceStatus.READY_FOR_REVIEW


@pytest.mark.parametrize(
    ("replacement", "expected"),
    [
        (
            lambda record: record.model_copy(
                update={"payload": replace(record.payload, p99_ms=251.0)}
            ),
            ("target-timing",),
        ),
        (
            lambda record: record.model_copy(
                update={"payload": replace(record.payload, hardware_provenance="workstation")}
            ),
            ("target-timing",),
        ),
        (
            lambda record: record.model_copy(update={"schema_version": 1}),
            ("schema-integrity",),
        ),
    ],
)
def test_each_timing_and_schema_gate_has_only_its_expected_blocker(replacement, expected: tuple[str, ...]) -> None:
    records = list(_qualifying())
    records[5] = replacement(records[5])
    report = _report(tuple(records))
    assert report.blockers == expected
    assert report.status is not ConfidenceStatus.READY_FOR_REVIEW


def test_discontinuous_coast_is_the_only_calibration_blocker() -> None:
    records = list(_qualifying())
    records[3] = records[3].model_copy(
        update={"payload": replace(records[3].payload, continuous=False)}
    )

    report = _report(tuple(records))

    assert report.blockers == ("calibration-completeness",)


def test_one_cook_and_duplicate_rows_cannot_create_cross_session_confidence() -> None:
    evidence = tuple(record for record in _qualifying() if record.cook_id != "cook-b")
    report = _report(evidence + evidence)
    assert report.status is ConfidenceStatus.EVALUATING
    assert "bootstrap-unavailable" in report.blockers
    assert "cook-effective-weight" in report.blockers


def test_only_typed_model_evidence_records_are_authority() -> None:
    report = evaluate_confidence(({"kind": "forecast_origin"},), activation_state=_state(), target_timing=None, config=ConfidenceConfig())
    assert report.status is ConfidenceStatus.COLLECTING
    assert report.blockers[0] == "ledger-integrity"


def test_active_fallback_and_schema_states_remain_authoritative() -> None:
    records = _qualifying()
    for status, expected in (("active", ConfidenceStatus.ACTIVE), ("fallback", ConfidenceStatus.FALLBACK), ("schema-invalidated", ConfidenceStatus.SCHEMA_INVALIDATED)):
        assert evaluate_confidence(records, activation_state=_state() | {"status": status}, target_timing=None, config=ConfidenceConfig()).status is expected


def test_config_is_frozen_and_replicates_are_fixed() -> None:
    config = ConfidenceConfig()
    with pytest.raises(FrozenInstanceError):
        config.bootstrap_seed = 8  # type: ignore[misc]
    with pytest.raises(ValueError, match="exactly 10,000"):
        ConfidenceConfig(bootstrap_replicates=9)
