"""Behavioral contracts for the durable compact model-evidence ledger."""

from dataclasses import replace

import sqlite3

import pytest
from pydantic import ValidationError

from common.control_trace import AllocationClampReason, AmbientSource
from common.datastore_accessors import (
    append_model_evidence,
    commit_model_activation,
    invalidate_model_evidence_schema,
    read_model_activation,
    read_model_evidence,
    reset_model_evidence,
)
from common.model_evidence import (
    ActivationEvidence,
    AllocationEvidence,
    CalibrationSummaryEvidence,
    EvidenceKind,
    ForecastOriginEvidence,
    ModelEvidenceRecord,
    TimingDistributionEvidence,
)
from common.model_evidence import MODEL_EVIDENCE_SCHEMA_VERSION

_DIGEST = "a" * 64
_OTHER_DIGEST = "b" * 64


def _forecast(evidence_id: str, timestamp_ms: int, *, sequence: int = 1) -> ModelEvidenceRecord:
    return ModelEvidenceRecord(
        evidence_id=evidence_id,
        kind=EvidenceKind.FORECAST_ORIGIN,
        session_id="session-a",
        cook_id="cook-a",
        timestamp_ms=timestamp_ms,
        role_generation=2,
        model_digest=_OTHER_DIGEST,
        provenance_digest=_DIGEST,
        payload=ForecastOriginEvidence(
            origin_sequence=sequence,
            origin_time_ms=timestamp_ms - 1,
            completion_time_ms=timestamp_ms,
            horizon_steps=3,
            incumbent_digest=_DIGEST,
            challenger_digest=_OTHER_DIGEST,
            incumbent_prediction_c=100.0,
            challenger_prediction_c=101.0,
            observed_temperature_c=102.0,
            incumbent_error_c=2.0,
            challenger_error_c=1.0,
            temperature_band="near-target",
            phase="coasting",
            ambient_source=AmbientSource.CONFIGURED,
            calibration_fit=False,
        ),
    )


def _activation(evidence_id: str = "activation-a") -> ModelEvidenceRecord:
    return ModelEvidenceRecord(
        evidence_id=evidence_id,
        kind=EvidenceKind.ACTIVATION,
        session_id="session-a",
        cook_id="cook-a",
        timestamp_ms=500,
        role_generation=3,
        model_digest=_DIGEST,
        provenance_digest=_OTHER_DIGEST,
        payload=ActivationEvidence(
            decision_id="decision-a",
            active_snapshot_json='{"revision": 3, "model": "new"}',
            rollback_snapshot_json='{"revision": 2, "model": "old"}',
            controller_configuration_digest=_OTHER_DIGEST,
        ),
    )


def test_forecast_envelope_must_match_precommitted_payload_digests() -> None:
    record = _forecast("mismatched", 100)
    with pytest.raises(ValidationError, match="forecast envelope digests"):
        ModelEvidenceRecord(
            evidence_id=record.evidence_id,
            kind=record.kind,
            session_id=record.session_id,
            cook_id=record.cook_id,
            timestamp_ms=record.timestamp_ms,
            role_generation=record.role_generation,
            model_digest=_DIGEST,
            provenance_digest=record.provenance_digest,
            payload=record.payload,
        )

def test_calibration_summary_requires_matching_completed_frame_allocations() -> None:
    baseline = AllocationEvidence(0.3, 0.27, None, 0.9, 0.0, 100.0, False, AllocationClampReason.NONE, AllocationClampReason.NONE, 2)
    combined = AllocationEvidence(0.4, 0.36, None, 0.9, 0.0, 100.0, False, AllocationClampReason.NONE, AllocationClampReason.NONE, 2)

    payload = CalibrationSummaryEvidence(
        accepted=True, probe_count=1, result_revision=3, command_revision=2, command_action="start",
        baseline_q=0.3, probe_q=0.1, combined_q=0.4, baseline_allocation=baseline,
        combined_allocation=combined, scheduled_on_seconds=8.0, delivered_on_seconds=7.0,
    )

    assert payload.combined_allocation == combined
    with pytest.raises(ValidationError):
        replace(payload, result_revision=0)



@pytest.mark.parametrize(
    "replacement",
    (
        {"status": "rejected"},
        {"status": "rejected", "accepted": False, "probe_count": 1},
        {"status": "active", "probe_count": 0},
        {"status": "cancelled"},
    ),
)
def test_calibration_summary_rejects_status_that_contradicts_completed_frame(replacement) -> None:
    baseline = AllocationEvidence(0.3, 0.27, None, 0.9, 0.0, 100.0, False, AllocationClampReason.NONE, AllocationClampReason.NONE, 2)
    combined = AllocationEvidence(0.4, 0.36, None, 0.9, 0.0, 100.0, False, AllocationClampReason.NONE, AllocationClampReason.NONE, 2)
    payload = CalibrationSummaryEvidence(
        accepted=True, probe_count=1, result_revision=3, command_revision=2, command_action="start",
        baseline_q=0.3, probe_q=0.1, combined_q=0.4, baseline_allocation=baseline,
        combined_allocation=combined, scheduled_on_seconds=8.0, delivered_on_seconds=7.0, status="active",
    )

    with pytest.raises(ValidationError):
        replace(payload, **replacement)

def test_append_only_identity_and_insertion_order(ds):
    later = _forecast("forecast-later", 200, sequence=2)
    first = _forecast("forecast-first", 100)

    append_model_evidence([later, first])

    assert read_model_evidence(session_id="session-a") == [later, first]
    with pytest.raises(sqlite3.IntegrityError):
        append_model_evidence([later])


def test_raw_trace_pruning_cannot_delete_durable_evidence(ds):
    from common.datastore_accessors import append_control_trace, prune_control_trace
    from common.control_trace import ControlTraceRecord, ControllerType, SessionPayload, TraceEventKind, TraceSetting

    evidence = _forecast("forecast-a", 100)
    trace = ControlTraceRecord(
        ts_ms=100,
        session_id="session-a",
        cook_id="cook-a",
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.SESSION,
        payload=SessionPayload(
            controller=ControllerType.MPC,
            controller_config=(TraceSetting(key="horizon", value=3),),
            temperature_unit="F",
            control_period_seconds=1.0,
            model_revision=1,
            model_provenance="persisted",
            pulse_slot_seconds=1.0,
            pulse_frame_seconds=1.0,
            fan_authority=False,
            fan_pwm_capable=True,
            fan_min_duty=0.0,
            fan_max_duty=1.0,
            setpoint=225.0,
            ambient_temperature=70.0,
            software_version="test",
            build_version="test",
        ),
    )
    append_model_evidence([evidence])
    append_control_trace([trace])

    assert prune_control_trace(101, limit=1) == 1
    assert read_model_evidence(session_id="session-a") == [evidence]


def test_schema_invalidation_and_explicit_reset_are_the_only_ledger_deletions(ds):
    append_model_evidence([_forecast("first", 100)])
    invalidate_model_evidence_schema()
    assert read_model_evidence(session_id="session-a") == []

    append_model_evidence([_forecast("second", 101)])
    reset_model_evidence()
    assert read_model_evidence(session_id="session-a") == []


def test_corrupt_payload_and_calibration_fit_forecast_are_rejected(ds):
    with pytest.raises(ValidationError, match="calibration"):
        replace(_forecast("calibration", 100).payload, calibration_fit=True)

    conn = ds.connection()
    conn.execute(
        """
        INSERT INTO model_evidence(
            evidence_id, session_id, cook_id, timestamp_ms, kind, role_generation,
            model_digest, provenance_digest, schema_version, payload
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("corrupt", "session-a", "cook-a", 100, "forecast_origin", 2, _DIGEST, _OTHER_DIGEST, 1, "[]"),
    )
    with pytest.raises(ValueError, match="invalid payload"):
        read_model_evidence(session_id="session-a")


def test_v1_timing_row_reads_with_unavailable_new_measurements(ds) -> None:
    conn = ds.connection()
    conn.execute(
        """
        INSERT INTO model_evidence(
            evidence_id, session_id, cook_id, timestamp_ms, kind, role_generation,
            model_digest, provenance_digest, schema_version, payload
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "v1-timing",
            "session-a",
            "cook-a",
            100,
            "timing_distribution",
            2,
            _OTHER_DIGEST,
            _DIGEST,
            1,
            '{"sample_count": 50, "p50_ms": 10.0, "p95_ms": 20.0, "payload_type": "timing_distribution"}',
        ),
    )

    record = read_model_evidence(session_id="session-a")[0]

    assert MODEL_EVIDENCE_SCHEMA_VERSION == 2
    assert record.schema_version == 1
    assert isinstance(record.payload, TimingDistributionEvidence)
    assert record.payload.p99_ms is None
    assert record.payload.hardware_provenance is None

def test_activation_commit_replaces_singleton_and_rolls_back_with_evidence(ds, tmp_path):
    db_path = tmp_path / "activation.db"
    from common import datastore

    datastore._reset_for_tests(str(db_path))
    try:
        decision = _activation()
        commit_model_activation(decision)
        active = read_model_activation()
        assert active is not None
        assert active.evidence_decision_id == "decision-a"
        assert active.active_snapshot_json == '{"revision": 3, "model": "new"}'
        assert read_model_evidence(kind="activation") == [decision]

        reset_model_evidence()
        invalid_decision = decision.model_copy(update={"evidence_id": "activation-invalid"})
        conn = datastore.connection()
        conn.execute("DROP TABLE model_activation_state")
        with pytest.raises(sqlite3.OperationalError):
            commit_model_activation(invalid_decision)
        assert read_model_activation() is None
        assert read_model_evidence(kind="activation") == []
    finally:
        datastore._reset_for_tests(None)



@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_activation_snapshot_rejects_nonstandard_json_constants(constant):
    with pytest.raises(ValidationError, match="valid JSON"):
        ActivationEvidence(
            decision_id="decision-a",
            active_snapshot_json=f'{{"gain": {constant}}}',
            rollback_snapshot_json='{"revision": 1}',
            controller_configuration_digest=_OTHER_DIGEST,
        )