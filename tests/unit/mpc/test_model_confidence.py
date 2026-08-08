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
    RecorderGapEvidence,
    RefreshDiagnosticsEvidence,
    TimingDistributionEvidence,
)
from controller.linear_mpc.confidence import ConfidenceConfig, ConfidenceStatus, evaluate_confidence


_CANDIDATE = sha256(b"candidate").hexdigest()
_OTHER = sha256(b"other").hexdigest()


def _rebuild(
    record: ModelEvidenceRecord,
    *,
    payload: object | None = None,
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
    return ModelEvidenceRecord(**values)


def _legacy_calibration(stage: str, *, timestamp: int) -> ModelEvidenceRecord:
    return ModelEvidenceRecord(
        evidence_id=f"legacy-calibration:{stage}:{timestamp}",
        kind=EvidenceKind.CALIBRATION_SUMMARY,
        session_id="legacy-session",
        cook_id=None,
        timestamp_ms=timestamp,
        role_generation=0,
        model_digest=None,
        provenance_digest=None,
        schema_version=1,
        payload=CalibrationSummaryEvidence(
            accepted=True,
            probe_count=0,
            stage=stage,  # type: ignore[arg-type]
            completed_stages=("low", "middle", "high") if stage == "coast" else (),
            continuous=True,
        ),
    )


_INCUMBENT = sha256(b"incumbent").hexdigest()


def _record(kind: EvidenceKind, payload: object, *, cook: str = "cook-a", timestamp: int = 1) -> ModelEvidenceRecord:
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


def _qualifying() -> tuple[ModelEvidenceRecord, ...]:
    records: list[ModelEvidenceRecord] = [
        _record(
            EvidenceKind.CALIBRATION_SUMMARY,
            CalibrationSummaryEvidence(accepted=True, probe_count=0, stage="low", continuous=True),
            timestamp=1,
        ),
        _record(
            EvidenceKind.CALIBRATION_SUMMARY,
            CalibrationSummaryEvidence(accepted=True, probe_count=0, stage="middle", continuous=True),
            timestamp=2,
        ),
        _record(
            EvidenceKind.CALIBRATION_SUMMARY,
            CalibrationSummaryEvidence(accepted=True, probe_count=0, stage="high", continuous=True),
            timestamp=3,
        ),
        _record(
            EvidenceKind.CALIBRATION_SUMMARY,
            CalibrationSummaryEvidence(
                accepted=True, probe_count=0, stage="coast", completed_stages=("low", "middle", "high"), continuous=True
            ),
            timestamp=4,
        ),
        _record(
            EvidenceKind.REFRESH_DIAGNOSTICS,
            RefreshDiagnosticsEvidence(
                accepted=True,
                full_rank=True,
                finite_diagnostics=True,
                pole_magnitude=0.9,
                gain=1.0,
                delay_steps=3,
                covariance_finite=True,
                alignment_error_c=1.0,
                snapshot_round_trip=True,
                sequential_wins=2,
                generation_continuity=True,
                atomic_persistence=True,
                production_prospective=True,
                braking_error_c=1.0,
                incumbent_braking_error_c=2.0,
            ),
            timestamp=5,
        ),
        _record(
            EvidenceKind.TIMING_DISTRIBUTION,
            TimingDistributionEvidence(
                sample_count=50, p50_ms=10.0, p95_ms=20.0, p99_ms=200.0, hardware_provenance="target-hardware"
            ),
            timestamp=6,
        ),
    ]
    timestamp = 7
    for cook in ("cook-a", "cook-b"):
        for horizon in (3, 15, 45, 90, 180):
            for sequence in range(horizon):
                error = (-0.5, 0.5, 0.0)[sequence % 3]
                payload = ForecastOriginEvidence(
                    origin_sequence=sequence,
                    origin_time_ms=sequence * 20,
                    completion_time_ms=(sequence + horizon) * 20,
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
                )
                records.append(_record(EvidenceKind.FORECAST_ORIGIN, payload, cook=cook, timestamp=timestamp))
                timestamp += 1
    return tuple(records)


def _state() -> dict[str, object]:
    return {
        "status": "collecting",
        "active_kind": "grey_box",
        "candidate_digest": _CANDIDATE,
        "candidate_generation": 4,
    }


def _report(records: tuple[ModelEvidenceRecord, ...], *, config: ConfidenceConfig | None = None):
    return evaluate_confidence(
        records,
        activation_state=_state(),
        target_timing=None,
        config=ConfidenceConfig(bootstrap_seed=7) if config is None else config,
    )


def _forecast_records(records: tuple[ModelEvidenceRecord, ...], horizon: int) -> tuple[ModelEvidenceRecord, ...]:
    return tuple(
        record
        for record in records
        if isinstance(record.payload, ForecastOriginEvidence) and record.payload.horizon_steps == horizon
    )


def _replace_forecasts(
    records: tuple[ModelEvidenceRecord, ...],
    horizon: int,
    replacement: callable,
) -> tuple[ModelEvidenceRecord, ...]:
    return tuple(
        _rebuild(record, payload=replacement(record.payload))
        if isinstance(record.payload, ForecastOriginEvidence) and record.payload.horizon_steps == horizon
        else record
        for record in records
    )


def test_typed_qualifying_ledger_is_ready_without_ownership_change() -> None:
    report = _report(_qualifying())
    assert report.status is ConfidenceStatus.READY_FOR_REVIEW
    assert report.active_kind == "grey_box"
    assert report.blockers == ()
    assert all(interval.replicate_count == 10_000 for interval in report.bootstrap_intervals)


@pytest.mark.parametrize(
    ("replacement", "expected"),
    [
        (lambda record: _rebuild(record, payload=replace(record.payload, full_rank=False)), ("identifiability",)),
        (lambda record: _rebuild(record, payload=replace(record.payload, pole_magnitude=0.999)), ("pole-magnitude",)),
        (lambda record: _rebuild(record, payload=replace(record.payload, gain=-1.0)), ("positive-gain",)),
        (lambda record: _rebuild(record, payload=replace(record.payload, delay_steps=16)), ("delay-limit",)),
        (
            lambda record: _rebuild(record, payload=replace(record.payload, covariance_finite=False)),
            ("finite-covariance",),
        ),
        (lambda record: _rebuild(record, payload=replace(record.payload, alignment_error_c=2.1)), ("state-alignment",)),
        (
            lambda record: _rebuild(record, payload=replace(record.payload, snapshot_round_trip=False)),
            ("snapshot-round-trip",),
        ),
        (lambda record: _rebuild(record, payload=replace(record.payload, sequential_wins=1)), ("sequential-wins",)),
        (
            lambda record: _rebuild(record, payload=replace(record.payload, generation_continuity=False)),
            ("generation-continuity",),
        ),
        (lambda record: _rebuild(record, payload=replace(record.payload, braking_error_c=3.0)), ("braking-error",)),
        (
            lambda record: _rebuild(record, payload=replace(record.payload, atomic_persistence=False)),
            ("atomic-persistence",),
        ),
        (
            lambda record: _rebuild(record, payload=replace(record.payload, production_prospective=False)),
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
        (lambda record: _rebuild(record, payload=replace(record.payload, p99_ms=251.0)), ("target-timing",)),
        (
            lambda record: _rebuild(record, payload=replace(record.payload, hardware_provenance="workstation")),
            ("target-timing",),
        ),
        (lambda record: _rebuild(record, schema_version=1), ("schema-integrity",)),
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
    records[3] = _rebuild(
        records[3],
        payload=replace(records[3].payload, continuous=False),
    )

    report = _report(tuple(records))

    assert report.blockers == ("calibration-completeness",)


def test_one_cook_and_duplicate_rows_cannot_create_cross_session_confidence() -> None:
    evidence = tuple(record for record in _qualifying() if record.cook_id != "cook-b")
    report = _report(evidence + evidence)
    assert report.status is ConfidenceStatus.EVALUATING
    assert "bootstrap-unavailable" in report.blockers
    assert "cook-effective-weight" in report.blockers


def test_one_cook_across_sessions_remains_one_bootstrap_unit() -> None:
    one_cook = tuple(record for record in _qualifying() if record.cook_id == "cook-a")
    restarted = tuple(
        _rebuild(
            record,
            evidence_id=f"restart:{record.evidence_id}",
            session_id="session-restarted",
        )
        for record in one_cook
        if record.kind is EvidenceKind.FORECAST_ORIGIN
    )

    report = _report(one_cook + restarted)

    assert "bootstrap-unavailable" in report.blockers
    assert "cook-effective-weight" in report.blockers


def test_only_typed_model_evidence_records_are_authority() -> None:
    report = evaluate_confidence(
        ({"kind": "forecast_origin"},), activation_state=_state(), target_timing=None, config=ConfidenceConfig()
    )
    assert report.status is ConfidenceStatus.COLLECTING
    assert report.blockers[0] == "ledger-integrity"


def test_active_fallback_and_schema_states_remain_authoritative() -> None:
    records = _qualifying()
    for status, expected in (
        ("active", ConfidenceStatus.ACTIVE),
        ("fallback", ConfidenceStatus.FALLBACK),
        ("schema-invalidated", ConfidenceStatus.SCHEMA_INVALIDATED),
    ):
        assert (
            evaluate_confidence(
                records, activation_state=_state() | {"status": status}, target_timing=None, config=ConfidenceConfig()
            ).status
            is expected
        )


def test_config_is_frozen_and_replicates_are_fixed() -> None:
    config = ConfidenceConfig()
    with pytest.raises(FrozenInstanceError):
        config.bootstrap_seed = 8  # type: ignore[misc]
    with pytest.raises(ValueError, match="exactly 10,000"):
        ConfidenceConfig(bootstrap_replicates=9)


def test_current_schema_calibration_ignores_legacy_reader_history() -> None:
    records = _qualifying() + tuple(
        _legacy_calibration(stage, timestamp=1_000 + index)
        for index, stage in enumerate(("low", "middle", "high", "coast"))
    )

    report = _report(records)

    assert report.status is ConfidenceStatus.READY_FOR_REVIEW
    assert report.blockers == ()


def test_legacy_only_calibration_is_incomplete_not_schema_poison() -> None:
    records = tuple(
        record for record in _qualifying() if not isinstance(record.payload, CalibrationSummaryEvidence)
    ) + tuple(
        _legacy_calibration(stage, timestamp=1_000 + index)
        for index, stage in enumerate(("low", "middle", "high", "coast"))
    )

    report = _report(records)

    assert report.blockers == ("calibration-completeness",)

    report = _report(
        _replace_forecasts(
            _qualifying(), 3, lambda payload: replace(payload, challenger_error_c=3.0, incumbent_error_c=6.0)
        ),
        config=ConfidenceConfig(
            bootstrap_seed=7,
            maximum_signed_bias_c=4.0,
            maximum_band_bias_c=4.0,
        ),
    )

    assert report.blockers == ("absolute-rmse:3/middle/heating/configured/4",)


def test_signed_bias_has_exact_blocker() -> None:
    report = _report(
        _replace_forecasts(
            _qualifying(), 3, lambda payload: replace(payload, challenger_error_c=0.3, incumbent_error_c=0.6)
        )
    )

    assert report.blockers == ("signed-bias:3/middle/heating/configured/4",)


def test_band_mae_has_exact_blocker_without_signed_bias() -> None:
    report = _report(
        _replace_forecasts(
            _qualifying(),
            3,
            lambda payload: replace(
                payload,
                challenger_error_c=0.6 if payload.origin_sequence % 2 else -0.6,
                incumbent_error_c=1.2 if payload.origin_sequence % 2 else -1.2,
            ),
        )
    )

    assert report.blockers == ("band-error:3/middle/heating/configured/4",)


def test_relative_rmse_and_upper_bound_are_the_exact_coupled_blockers() -> None:
    report = _report(
        _replace_forecasts(
            _qualifying(),
            3,
            lambda payload: replace(
                payload,
                challenger_error_c=0.4 if payload.origin_sequence % 2 else -0.4,
                incumbent_error_c=0.2 if payload.origin_sequence % 2 else -0.2,
            ),
        )
    )

    # A point estimate at or above the incumbent necessarily makes its one-sided
    # upper confidence bound fail closed as well.
    assert report.blockers == (
        "relative-rmse:3/middle/heating/configured/4",
        "relative-bootstrap",
    )


def test_relative_bootstrap_upper_has_exact_blocker_below_point_rmse_limit() -> None:
    records = _replace_forecasts(
        _qualifying(),
        3,
        lambda payload: replace(
            payload,
            challenger_error_c=(0.1 if payload.origin_sequence % 2 else -0.1),
            incumbent_error_c=1.0 if payload.origin_sequence % 2 else -1.0,
        ),
    )
    records = tuple(
        _rebuild(
            record,
            payload=replace(
                record.payload,
                challenger_error_c=1.1 if record.payload.origin_sequence % 2 else -1.1,
                incumbent_error_c=1.0 if record.payload.origin_sequence % 2 else -1.0,
            ),
        )
        if isinstance(record.payload, ForecastOriginEvidence)
        and record.payload.horizon_steps == 3
        and record.cook_id == "cook-b"
        else record
        for record in records
    )
    report = _report(
        records,
        config=ConfidenceConfig(
            bootstrap_seed=7,
            maximum_signed_bias_c=2.0,
            maximum_band_bias_c=2.0,
        ),
    )

    interval = next(interval for interval in report.bootstrap_intervals if interval.horizon_steps == 3)
    assert interval.challenger_rmse_c is not None and interval.challenger_rmse_c < 1.0
    assert interval.upper_bound is not None and interval.upper_bound >= 1.0
    assert report.blockers == ("relative-bootstrap",)


def test_model_digest_mismatch_has_exact_integrity_blocker() -> None:
    extra = ModelEvidenceRecord(
        evidence_id="timing-with-other-model",
        kind=EvidenceKind.TIMING_DISTRIBUTION,
        session_id="session-cook-a",
        cook_id="cook-a",
        timestamp_ms=1_000,
        role_generation=4,
        model_digest=_OTHER,
        provenance_digest=_INCUMBENT,
        payload=TimingDistributionEvidence(
            sample_count=50,
            p50_ms=10.0,
            p95_ms=20.0,
            p99_ms=200.0,
            hardware_provenance="target-hardware",
        ),
    )
    records = _qualifying() + (extra,)

    report = _report(records)

    assert report.blockers == ("model-integrity",)


def test_provenance_digest_mismatch_has_exact_integrity_blocker() -> None:
    extra = ModelEvidenceRecord(
        evidence_id="timing-with-other-provenance",
        kind=EvidenceKind.TIMING_DISTRIBUTION,
        session_id="session-cook-a",
        cook_id="cook-a",
        timestamp_ms=1_000,
        role_generation=4,
        model_digest=_CANDIDATE,
        provenance_digest=_OTHER,
        payload=TimingDistributionEvidence(
            sample_count=50,
            p50_ms=10.0,
            p95_ms=20.0,
            p99_ms=200.0,
            hardware_provenance="target-hardware",
        ),
    )
    report = _report(_qualifying() + (extra,))

    assert report.blockers == ("provenance-integrity",)


def test_schema_valid_unsupported_horizon_has_exact_blocker_without_missing_required_horizons() -> None:
    source = _forecast_records(_qualifying(), 3)[0]
    assert isinstance(source.payload, ForecastOriginEvidence)
    unsupported = _rebuild(
        source,
        evidence_id="unsupported-horizon",
        payload=replace(
            source.payload,
            origin_sequence=99,
            origin_time_ms=1_980,
            completion_time_ms=2_060,
            horizon_steps=4,
        ),
    )

    report = _report(_qualifying() + (unsupported,))

    assert report.blockers == ("unsupported-horizon-4",)


def test_missing_horizon_has_exact_blocker() -> None:
    report = _report(
        tuple(
            record
            for record in _qualifying()
            if not isinstance(record.payload, ForecastOriginEvidence) or record.payload.horizon_steps != 3
        )
    )

    assert report.blockers == ("missing-horizon-3",)


def test_no_untouched_future_rows_has_exact_complete_blockers() -> None:
    report = _report(
        tuple(record for record in _qualifying() if not isinstance(record.payload, ForecastOriginEvidence))
    )

    assert report.blockers == (
        "untouched-future-rows",
        "missing-horizon-3",
        "missing-horizon-15",
        "missing-horizon-45",
        "missing-horizon-90",
        "missing-horizon-180",
    )


def test_fewer_than_two_independent_cooks_has_exact_complete_blockers() -> None:
    report = _report(tuple(record for record in _qualifying() if record.cook_id != "cook-b"))

    assert report.blockers == (
        "bootstrap-unavailable",
        "relative-bootstrap",
        "cook-effective-weight",
        "bootstrap-unavailable",
        "relative-bootstrap",
        "cook-effective-weight",
        "bootstrap-unavailable",
        "relative-bootstrap",
        "cook-effective-weight",
        "bootstrap-unavailable",
        "relative-bootstrap",
        "cook-effective-weight",
        "bootstrap-unavailable",
        "relative-bootstrap",
        "cook-effective-weight",
    )


def test_cook_effective_weight_monopoly_has_the_same_exact_fail_closed_blockers() -> None:
    report = _report(tuple(record for record in _qualifying() if record.cook_id != "cook-b"))

    # Fewer than two cooks and one-cook effective-weight monopoly are the same
    # fail-closed condition in a cook-resampled bootstrap.
    assert report.blockers == (
        "bootstrap-unavailable",
        "relative-bootstrap",
        "cook-effective-weight",
        "bootstrap-unavailable",
        "relative-bootstrap",
        "cook-effective-weight",
        "bootstrap-unavailable",
        "relative-bootstrap",
        "cook-effective-weight",
        "bootstrap-unavailable",
        "relative-bootstrap",
        "cook-effective-weight",
        "bootstrap-unavailable",
        "relative-bootstrap",
        "cook-effective-weight",
    )


@pytest.mark.parametrize("reason", ("evidence-queue-overflow", "recorder-gap"))
def test_destructive_evidence_gap_is_the_exact_blocker_and_keeps_grey_box_owner(reason: str) -> None:
    gap = _record(
        EvidenceKind.RECORDER_GAP,
        RecorderGapEvidence(lost_record_count=2, reason=reason),
        timestamp=10_000,
    )

    report = _report(_qualifying() + (gap,))

    assert report.blockers == (reason,)
    assert report.active_kind == "grey_box"
    assert report.status is not ConfidenceStatus.READY_FOR_REVIEW


@pytest.mark.parametrize(
    ("failure", "expected_blocker"),
    (
        ("schema-invalidation", "schema-integrity"),
        ("bad-ambient-provenance", "provenance-integrity"),
        ("rank-deficiency", "identifiability"),
        ("unsupported-horizon", "unsupported-horizon-4"),
        ("covariance-failure", "finite-covariance"),
        ("refresh-timeout", "target-timing"),
    ),
)
def test_acceptance_failure_matrix_has_one_exact_blocker_and_never_changes_ownership(
    failure: str,
    expected_blocker: str,
) -> None:
    records = list(_qualifying())
    state = _state()
    if failure == "schema-invalidation":
        state["status"] = "schema-invalidated"
    elif failure == "bad-ambient-provenance":
        timing = next(record for record in records if isinstance(record.payload, TimingDistributionEvidence))
        records.append(
            _rebuild(
                timing,
                evidence_id="bad-ambient-provenance",
                provenance_digest=_OTHER,
                timestamp_ms=10_000,
            )
        )
    elif failure == "rank-deficiency":
        index = next(
            index for index, record in enumerate(records) if isinstance(record.payload, RefreshDiagnosticsEvidence)
        )
        refresh = records[index]
        assert isinstance(refresh.payload, RefreshDiagnosticsEvidence)
        records[index] = _rebuild(
            refresh,
            payload=replace(refresh.payload, accepted=False, reason="rank-deficient", full_rank=False),
        )
    elif failure == "unsupported-horizon":
        source = _forecast_records(tuple(records), 3)[0]
        assert isinstance(source.payload, ForecastOriginEvidence)
        records.append(
            _rebuild(
                source,
                evidence_id="failure-matrix-unsupported-horizon",
                payload=replace(
                    source.payload,
                    origin_sequence=999,
                    origin_time_ms=19_980,
                    completion_time_ms=20_060,
                    horizon_steps=4,
                ),
            )
        )
    elif failure == "covariance-failure":
        index = next(
            index for index, record in enumerate(records) if isinstance(record.payload, RefreshDiagnosticsEvidence)
        )
        refresh = records[index]
        assert isinstance(refresh.payload, RefreshDiagnosticsEvidence)
        records[index] = _rebuild(refresh, payload=replace(refresh.payload, covariance_finite=False))
    elif failure == "refresh-timeout":
        index = next(
            index for index, record in enumerate(records) if isinstance(record.payload, TimingDistributionEvidence)
        )
        timing = records[index]
        assert isinstance(timing.payload, TimingDistributionEvidence)
        records[index] = _rebuild(
            timing,
            payload=replace(timing.payload, p50_ms=100.0, p95_ms=200.0, p99_ms=251.0),
        )
    else:  # pragma: no cover - the parameter list is the closed failure vocabulary
        raise AssertionError(f"unknown failure injection: {failure}")

    report = evaluate_confidence(
        tuple(records),
        activation_state=state,
        target_timing=None,
        config=ConfidenceConfig(bootstrap_seed=7),
    )

    assert report.blockers == (expected_blocker,)
    assert report.active_kind == "grey_box"
    assert report.status is not ConfidenceStatus.READY_FOR_REVIEW


def test_recorder_gaps_alone_are_not_progress_and_stay_collecting() -> None:
    """A gap records that an observation was LOST. A store holding nothing else
    has learned nothing, and must not report a model being fitted -- which is
    what a real grill shows when online adaptation is off and every frame is
    dropped."""
    gaps = tuple(
        _record(
            EvidenceKind.RECORDER_GAP,
            RecorderGapEvidence(lost_record_count=1, reason="runner-no-observation-outcome"),
            timestamp=index + 1,
        )
        for index in range(3)
    )

    report = evaluate_confidence(gaps, activation_state=_state(), target_timing=None, config=ConfidenceConfig())

    assert report.status is ConfidenceStatus.COLLECTING
