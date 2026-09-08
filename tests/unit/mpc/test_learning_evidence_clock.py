"""Wall provenance never selects learning authority or advances physical time."""

import json
from dataclasses import replace

import pytest

from common.model_evidence import (
    ActivationEvidence,
    CandidateAssessmentEvidence,
    ConfidenceDecisionEvidence,
    EvidenceKind,
    ModelEvidenceRecord,
)
from common.persistence.model_challenger import (
    ModelChallengerConflictError,
    compare_and_swap_model_challenger,
    create_model_challenger,
    read_model_challenger,
)
from common.persistence.model_evidence import append_model_evidence, commit_model_activation, read_model_evidence
from controller.model_learning.confidence import ConfidenceConfig, evaluate_confidence
from controller.model_learning.contracts import FrameObservation
from controller.model_learning.report import build_learning_artifact, build_learning_report
from controller.runtime.runner import _freeze_evidence
from tests.unit.common._model_challenger_helpers import _state
from tests.unit.mpc._grey_learning_runtime_helpers import (
    _automatic_candidate,
    _close_prepared_candidate,
    _harness,
)


def _confidence(identity, wall_ms, *, blocked):
    return ModelEvidenceRecord(
        evidence_id=identity,
        kind=EvidenceKind.CONFIDENCE_DECISION,
        session_id="session-clock",
        cook_id="cook-clock",
        timestamp_ms=wall_ms,
        role_generation=2,
        model_digest="a" * 64,
        provenance_digest="b" * 64,
        payload=ConfidenceDecisionEvidence(
            decision_id=identity, blocked=blocked, reason="blocked" if blocked else None
        ),
    )


def test_append_newer_blocked_authority_survives_wall_rollback_and_export(tmp_path):
    database = tmp_path / "evidence.db"
    accepted = _confidence("z-accepted", 4_000_000, blocked=False)
    blocked = _confidence("a-blocked", 400_000, blocked=True)
    append_model_evidence((accepted, blocked), database_path=database)
    records = read_model_evidence(database_path=database)
    report = build_learning_report(
        records, activation_state=None, live_status={"status": "collecting"}, calibration_command_high_water=0
    )
    exported = json.loads(build_learning_artifact(report, records))
    reloaded = tuple(ModelEvidenceRecord.model_validate_json(json.dumps(row)) for row in exported["records"])
    assert reloaded == (accepted, blocked)
    assert exported["report"]["evidence"]["high_water"] == [400_000, "a-blocked"]
    imported = tmp_path / "imported.db"
    append_model_evidence(reloaded, database_path=imported)
    activation = ModelEvidenceRecord(
        evidence_id="activation",
        kind=EvidenceKind.ACTIVATION,
        session_id="session-clock",
        cook_id="cook-clock",
        timestamp_ms=400_001,
        role_generation=3,
        model_digest="a" * 64,
        provenance_digest="b" * 64,
        payload=ActivationEvidence(
            decision_id="z-accepted",
            active_snapshot_json='{"revision": 3}',
            rollback_snapshot_json='{"revision": 2}',
            controller_configuration_digest="b" * 64,
        ),
    )
    for path in (database, imported):
        with pytest.raises(ValueError, match="activation-authority-changed"):
            commit_model_activation(activation, database_path=path)


def test_challenger_revision_accepts_wall_rollback_but_rejects_stale_revision(tmp_path):
    database = tmp_path / "challenger.db"
    initial = create_model_challenger(_state(), database_path=database)
    updated = replace(initial, revision=initial.revision + 1, updated_ms=100)
    compare_and_swap_model_challenger(expected_revision=initial.revision, replacement=updated, database_path=database)
    assert read_model_challenger(database_path=database) == updated
    with pytest.raises(ModelChallengerConflictError):
        compare_and_swap_model_challenger(
            expected_revision=initial.revision,
            replacement=replace(updated, updated_ms=99),
            database_path=database,
        )


def test_evaluation_evidence_envelope_uses_injected_wall_not_completion():
    harness = _harness()
    preparation, evaluation, _ = _automatic_candidate(harness)
    harness.runtime._clock_ms = lambda: 1_700_000_000_123
    try:
        record = harness.runtime._persist_candidate_evaluation(evaluation, preparation)
        assert record is not None
        assert record.timestamp_ms == 1_700_000_000_123
        assert harness.persistence.confidence[-1].timestamp_ms == record.timestamp_ms
        forecast = harness.runtime._completed_forecast_evidence(evaluation.completed_origins[0])
        completion_s = evaluation.completed_origins[0].completion_time_s
        observation = FrameObservation(
            frame_start_s=completion_s - 20.0,
            frame_end_s=completion_s,
            wall_start_ms=1_700_003_600_000,
            wall_end_ms=1_700_000_000_000,
            temp_c=100.0,
            setpoint_c=120.0,
            ambient_c=20.0,
            requested_q=0.4,
            realized_q=0.4,
            requested_auger_duty=0.4,
            delivered_on_s=8.0,
            requested_fan_duty=None,
            actual_fan_duty=None,
            result_revision=1,
            output_source="controller",
            lid_open=False,
            safety_inhibited=False,
            manual_override=False,
            stale=False,
            skipped=False,
            reset=False,
            continuous=True,
            role_generation=evaluation.role_generation,
        )
        records = _freeze_evidence(
            {
                "eligible": True,
                "forecast_origin_evidence": (forecast,),
                "evaluation_payload": harness.runtime._grey_evaluation_payload(evaluation, evaluation_duration_ms=0.0),
                "confidence_accepted": False,
            },
            "session-clock",
            "cook-clock",
            observation,
        )
        assert {value.kind for value in records} == {
            EvidenceKind.SESSION_SUMMARY,
            EvidenceKind.FORECAST_ORIGIN,
            EvidenceKind.CONFIDENCE_DECISION,
        }
        assert all(value.timestamp_ms == observation.wall_end_ms for value in records)
        assert forecast.origin_time_ms == 100_000
        assert forecast.completion_time_ms == int(evaluation.completed_origins[0].completion_time_s * 1_000)
    finally:
        _close_prepared_candidate(preparation)
        harness.runtime.close()
        harness.activation.close()


def test_confidence_uses_last_appended_assessment_after_wall_rollback():
    accepted = ModelEvidenceRecord(
        evidence_id="z-accepted-assessment",
        kind=EvidenceKind.CANDIDATE_ASSESSMENT,
        session_id="session-clock",
        cook_id="cook-clock",
        timestamp_ms=4_000_000,
        role_generation=2,
        model_digest="a" * 64,
        provenance_digest="b" * 64,
        payload=CandidateAssessmentEvidence(
            decision_id="accepted",
            origin="passive-online",
            policy="causal-auto",
            fit_accepted=True,
            identifiability_accepted=True,
            native_build="passed",
            native_dry_solve="passed",
            target_timing="passed",
            confidence_accepted=True,
        ),
    )
    assert isinstance(accepted.payload, CandidateAssessmentEvidence)
    rejected = accepted.model_copy(
        update={
            "evidence_id": "a-rejected-assessment",
            "timestamp_ms": 400_000,
            "payload": replace(
                accepted.payload,
                fit_accepted=False,
                confidence_accepted=False,
                rejection_reasons=("fit-rejected",),
            ),
        }
    )
    report = evaluate_confidence(
        (accepted, rejected),
        activation_state={
            "candidate_digest": "a" * 64,
            "role_generation": 2,
            "candidate_generation": 3,
            "origin": "passive-online",
        },
        target_timing=None,
        config=ConfidenceConfig(),
    )
    assert "fit-accepted" in report.blockers


def test_schema_five_seconds_forecasts_remain_historical_on_reload(tmp_path):
    harness = _harness()
    preparation, evaluation, _ = _automatic_candidate(harness)
    try:
        payload = harness.runtime._completed_forecast_evidence(evaluation.completed_origins[0])
        record = ModelEvidenceRecord(
            evidence_id="historical-five",
            kind=EvidenceKind.FORECAST_ORIGIN,
            session_id="historical-session",
            cook_id="historical-cook",
            timestamp_ms=1_700_000_000_000,
            role_generation=evaluation.role_generation,
            model_digest=payload.challenger_digest,
            provenance_digest=payload.incumbent_digest,
            schema_version=5,
            payload=payload,
        )
        database = tmp_path / "historical.db"
        append_model_evidence((record,), database_path=database)
        assert read_model_evidence(database_path=database) == [record]
        assert payload.horizon_seconds == evaluation.completed_origins[0].horizon_seconds
    finally:
        _close_prepared_candidate(preparation)
        harness.runtime.close()
        harness.activation.close()
