from dataclasses import replace
import hashlib

import json

import pytest

from common.control_trace import AllocationClampReason, AmbientSource
from common.model_evidence import (
    ActivationEvidence,
    AllocationEvidence,
    CalibrationSummaryEvidence,
    ConfidenceDecisionEvidence,
    EvidenceKind,
    ForecastOriginEvidence,
    ModelEvidenceRecord,
    RefreshDiagnosticsEvidence,
    RollbackEvidence,
    SessionSummaryEvidence,
    TimingDistributionEvidence,
)
from controller.linear_mpc.arx import ScheduledARX
from controller.linear_mpc.confidence import ConfidenceConfig, evaluate_confidence
from common.datastore_accessors import ModelActivationState
from controller.linear_mpc import report as report_module
from controller.linear_mpc.confidence import ConfidenceStatus
from controller.linear_mpc.report import build_evidence_artifact, build_evidence_report
from controller.linear_mpc.activation import canonical_snapshot_digest


_ACTIVE_SNAPSHOT = {
    "schema": "innovation-state-space/v2",
    "config": {},
    "model": {"generation": "b"},
    "bounds": {},
    "plausibility_bounds": {},
    "state": {"temperature_c": 123.0},
}
_ACTIVE_JSON = json.dumps(_ACTIVE_SNAPSHOT, sort_keys=True, separators=(",", ":"))
_ROLLBACK_JSON = '{"schema":"grey-box-adapter/v1"}'
_CANDIDATE = canonical_snapshot_digest(_ACTIVE_SNAPSHOT)
_INCUMBENT = hashlib.sha256(_ROLLBACK_JSON.encode()).hexdigest()
_CONTROLLER_CONFIG = "d" * 64


def _record(
    evidence_id,
    payload,
    *,
    timestamp_ms,
    generation=4,
    model_digest=None,
    provenance_digest=None,
    cook_id="cook-1",
):
    return ModelEvidenceRecord(
        evidence_id=evidence_id,
        kind=EvidenceKind(payload.payload_type),
        session_id="session-1",
        cook_id=cook_id,
        timestamp_ms=timestamp_ms,
        role_generation=generation,
        model_digest=model_digest,
        provenance_digest=provenance_digest,
        payload=payload,
    )


def _calibration(stage, timestamp_ms, completed_stages=()):
    allocation = AllocationEvidence(
        normalized_combustion_load=0.3,
        auger_duty=0.15,
        fan_duty=0.5,
        u_max=0.5,
        fan_min_pct=0.0,
        fan_max_pct=100.0,
        fan_enabled=True,
        auger_clamp_reason=AllocationClampReason.NONE,
        fan_clamp_reason=AllocationClampReason.NONE,
        allocator_revision=1,
    )
    return _record(
        f"calibration-{stage}",
        CalibrationSummaryEvidence(
            accepted=True,
            probe_count=0,
            result_revision=timestamp_ms,
            command_revision=7,
            command_action="start",
            baseline_q=0.3,
            probe_q=0.0,
            combined_q=0.3,
            baseline_allocation=allocation,
            combined_allocation=allocation,
            scheduled_on_seconds=6.0,
            delivered_on_seconds=6.0,
            status="accepted",
            stage=stage,
            completed_stages=completed_stages,
            eligible_observations=timestamp_ms,
        ),
        timestamp_ms=timestamp_ms,
        model_digest=None,
        provenance_digest=None,
    )


def _records():
    forecasts = (
        _record(
            "forecast-2",
            ForecastOriginEvidence(
                origin_sequence=2,
                origin_time_ms=40,
                completion_time_ms=100,
                horizon_steps=3,
                incumbent_digest=_INCUMBENT,
                challenger_digest=_CANDIDATE,
                incumbent_prediction_c=101.0,
                challenger_prediction_c=100.5,
                observed_temperature_c=100.0,
                incumbent_error_c=1.0,
                challenger_error_c=0.5,
                temperature_band="low",
                phase="heating",
                ambient_source=AmbientSource.MANUAL,
                calibration_fit=False,
            ),
            timestamp_ms=100,
            model_digest=_CANDIDATE,
            provenance_digest=_INCUMBENT,
        ),
        _record(
            "forecast-1",
            ForecastOriginEvidence(
                origin_sequence=1,
                origin_time_ms=20,
                completion_time_ms=80,
                horizon_steps=3,
                incumbent_digest=_INCUMBENT,
                challenger_digest=_CANDIDATE,
                incumbent_prediction_c=98.0,
                challenger_prediction_c=99.5,
                observed_temperature_c=100.0,
                incumbent_error_c=-2.0,
                challenger_error_c=-0.5,
                temperature_band="low",
                phase="heating",
                ambient_source=AmbientSource.MANUAL,
                calibration_fit=False,
            ),
            timestamp_ms=80,
            model_digest=_CANDIDATE,
            provenance_digest=_INCUMBENT,
        ),
    )
    refresh = _record(
        "refresh",
        RefreshDiagnosticsEvidence(
            accepted=True,
            full_rank=True,
            finite_diagnostics=True,
            pole_magnitude=0.9,
            gain=1.2,
            delay_steps=2,
            covariance_finite=True,
            alignment_error_c=0.2,
            snapshot_round_trip=True,
            sequential_wins=2,
            generation_continuity=True,
            atomic_persistence=True,
            production_prospective=True,
            braking_error_c=0.4,
            incumbent_braking_error_c=0.5,
        ),
        timestamp_ms=110,
        model_digest=_CANDIDATE,
        provenance_digest=_INCUMBENT,
    )
    timing = _record(
        "timing",
        TimingDistributionEvidence(
            sample_count=50,
            p50_ms=10.0,
            p95_ms=20.0,
            p99_ms=25.0,
            hardware_provenance="target-hardware",
        ),
        timestamp_ms=120,
        model_digest=_CANDIDATE,
        provenance_digest=_INCUMBENT,
    )
    calibration = (
        _calibration("low", 1),
        _calibration("middle", 2, ("low",)),
        _calibration("high", 3, ("low", "middle")),
        _calibration("coast", 4, ("low", "middle", "high")),
    )
    summary = _record(
        "summary",
        SessionSummaryEvidence(
            completed_origins=2,
            accepted_observations=12,
            rejected_observations=3,
            rejection_reasons=("lid-open", "stale"),
        ),
        timestamp_ms=125,
        model_digest=_CANDIDATE,
        provenance_digest=_INCUMBENT,
    )
    decision = _record(
        "decision",
        ConfidenceDecisionEvidence(decision_id="decision-7", blocked=True, reason="missing-horizon-15"),
        timestamp_ms=130,
        model_digest=_CANDIDATE,
        provenance_digest=_INCUMBENT,
    )
    activation = _record(
        "activation",
        ActivationEvidence(
            decision_id="decision-6",
            active_snapshot_json=_ACTIVE_JSON,
            rollback_snapshot_json=_ROLLBACK_JSON,
            controller_configuration_digest=_CONTROLLER_CONFIG,
        ),
        timestamp_ms=70,
        model_digest=_CANDIDATE,
        provenance_digest=_INCUMBENT,
    )
    rollback = _record(
        "rollback",
        RollbackEvidence(decision_id="decision-6", reason="operator-request"),
        timestamp_ms=75,
        model_digest=_CANDIDATE,
        provenance_digest=_INCUMBENT,
    )
    return calibration + forecasts + (refresh, timing, summary, activation, rollback, decision)


def _confidence(records):
    return evaluate_confidence(
        records,
        activation_state={
            "active_kind": "grey-box",
            "candidate_digest": _CANDIDATE,
            "candidate_generation": 4,
        },
        target_timing=None,
        config=ConfidenceConfig(bootstrap_seed=91),
    )


def test_empty_report_is_collecting_and_names_every_missing_gate():
    confidence = evaluate_confidence(
        (), activation_state={}, target_timing=None, config=ConfidenceConfig(bootstrap_seed=17)
    )

    report = build_evidence_report(confidence, ())
    payload = report.to_dict()

    assert payload["status"] == "collecting"
    assert payload["decision_id"] is None
    assert payload["active_model"] == {"kind": "grey-box", "digest": None}
    assert payload["default_model"] == {"kind": "grey-box", "digest": None}
    assert payload["candidate"] == {"kind": "innovation-state-space", "generation": None, "digest": None}
    assert payload["calibration"]["completed_stages"] == []
    assert payload["calibration"]["missing_stages"] == ["low", "middle", "high", "coast"]
    assert payload["missing_gates"] == [gate["name"] for gate in payload["gates"] if not gate["passed"]]
    assert "candidate-lineage" in payload["blockers"]
    assert "calibration-completeness" in payload["blockers"]
    assert "identifiability" in payload["blockers"]
    assert "target-timing" in payload["blockers"]
    assert payload["artifact_metadata"] == {
        "schema_version": 1,
        "provenance_digest": None,
        "bootstrap_seed": 17,
        "bootstrap_replicates": 10_000,
        "decision_id": None,
        "evidence_ids": [],
    }


def test_pause_command_projects_paused_even_while_the_coordinator_remains_active():
    base = _calibration("middle", 8, ("low",))
    combined = AllocationEvidence(
        normalized_combustion_load=0.35,
        auger_duty=0.175,
        fan_duty=0.5,
        u_max=0.5,
        fan_min_pct=0.0,
        fan_max_pct=100.0,
        fan_enabled=True,
        auger_clamp_reason=AllocationClampReason.NONE,
        fan_clamp_reason=AllocationClampReason.NONE,
        allocator_revision=1,
    )
    paused_payload = replace(
        base.payload,
        status="active",
        command_action="pause",
        probe_count=1,
        probe_q=0.05,
        combined_q=0.35,
        combined_allocation=combined,
    )
    paused = base.model_copy(update={"payload": paused_payload})
    confidence = evaluate_confidence((paused,), activation_state={}, target_timing=None, config=ConfidenceConfig())

    calibration = build_evidence_report(confidence, (paused,)).to_dict()["calibration"]

    assert calibration["status"] == "paused"
    assert calibration["current_probe"] == pytest.approx(0.05)


def test_projection_reports_typed_progress_scores_diagnostics_timing_and_history():
    records = _records()

    payload = build_evidence_report(_confidence(records), tuple(reversed(records))).to_dict()
    assert payload == build_evidence_report(_confidence(records), records).to_dict()

    assert payload["decision_id"] == "decision-7"
    assert payload["active_model"] == {"kind": "grey-box", "digest": _INCUMBENT}
    assert payload["default_model"] == {"kind": "grey-box", "digest": _INCUMBENT}
    assert payload["candidate"] == {
        "kind": "innovation-state-space",
        "generation": 4,
        "digest": _CANDIDATE,
    }
    assert payload["calibration"] == {
        "status": "accepted",
        "stage": "coast",
        "current_probe": None,
        "completed_stages": ["low", "middle", "high", "coast"],
        "missing_stages": [],
        "eligible_count": 12,
        "ineligible_count": 3,
        "ineligible_reasons": ["lid-open", "stale"],
        "timed_out": False,
        "incomplete": False,
        "revision": 7,
    }
    assert payload["identifiability"]["full_rank"] is True
    assert payload["identifiability"]["pole_magnitude"] == 0.9
    assert payload["target_timing"] == {
        "available": True,
        "sample_count": 50,
        "p50_ms": 10.0,
        "p95_ms": 20.0,
        "p99_ms": 25.0,
        "hardware_provenance": "target-hardware",
        "gate_passed": True,
    }
    score = payload["scores"][0]
    assert score["horizon_steps"] == 3
    assert score["temperature_band"] == "low"
    assert score["challenger_rmse_c"] == pytest.approx(0.5)
    assert score["incumbent_rmse_c"] == pytest.approx(2.5**0.5)
    assert score["challenger_bias_c"] == pytest.approx(0.0)
    assert score["incumbent_bias_c"] == pytest.approx(-0.5)
    assert score["challenger_band_error_c"] == pytest.approx(0.5)
    assert score["incumbent_band_error_c"] == pytest.approx(1.5)
    assert score["bootstrap"]["available"] is False
    assert score["bootstrap"]["replicate_count"] == 0
    assert payload["history"] == [
        {
            "evidence_id": "activation",
            "timestamp_ms": 70,
            "event": "activation",
            "decision_id": "decision-6",
            "reason": None,
        },
        {
            "evidence_id": "rollback",
            "timestamp_ms": 75,
            "event": "rollback",
            "decision_id": "decision-6",
            "reason": "operator-request",
        },
    ]
    assert payload["ambient_provenance_limitation"] == (
        "forecast evidence uses manual ambient provenance; no measured ambient evidence is present"
    )
    assert payload["artifact_metadata"]["bootstrap_seed"] == 91
    assert payload["artifact_metadata"]["provenance_digest"] == _INCUMBENT


def test_artifact_is_canonical_insertion_order_independent_and_excludes_unreferenced_rows():
    records = _records()
    projected = build_evidence_report(_confidence(records), records)
    unrelated = _record(
        "old-generation",
        RefreshDiagnosticsEvidence(accepted=False, reason="rank-deficient"),
        timestamp_ms=10,
        generation=1,
        model_digest="b" * 64,
        provenance_digest="e" * 64,
    )

    first = build_evidence_artifact(projected, records + (unrelated,))
    second = build_evidence_artifact(projected, tuple(reversed(records + (unrelated,))))
    decoded = json.loads(first)

    assert first == second
    assert first == json.dumps(decoded, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    assert decoded["artifact_schema"] == "pifire-model-evidence/v1"
    assert decoded["schema_version"] == 1
    assert decoded["provenance_digest"] == _INCUMBENT
    assert decoded["bootstrap_seed"] == 91
    assert decoded["bootstrap_replicates"] == 10_000
    assert decoded["decision_id"] == "decision-7"
    assert decoded["evidence_ids"] == list(projected.artifact_metadata.evidence_ids)
    assert [record["evidence_id"] for record in decoded["records"]] == list(projected.artifact_metadata.evidence_ids)
    assert "old-generation" not in projected.artifact_metadata.evidence_ids
    with pytest.raises(ValueError, match="snapshot schema"):
        ScheduledARX.from_snapshot(decoded)


def test_artifact_rejects_duplicate_missing_and_invalid_referenced_records():
    records = _records()
    projected = build_evidence_report(_confidence(records), records)

    with pytest.raises(ValueError, match="duplicate evidence_id"):
        build_evidence_artifact(projected, records + (records[0],))
    with pytest.raises(ValueError, match="missing referenced evidence_id"):
        build_evidence_artifact(projected, records[1:])
    invalid = records[0].model_copy(update={"evidence_id": ""})
    with pytest.raises(ValueError, match="invalid ledger record"):
        build_evidence_artifact(projected, (invalid,) + records[1:])


def test_new_candidate_never_inherits_an_older_generation_decision():
    records = _records()
    confidence = replace(
        _confidence(records),
        candidate_digest="b" * 64,
        generation=5,
    )

    payload = build_evidence_report(confidence, records).to_dict()

    assert payload["candidate"]["digest"] == "b" * 64
    assert payload["decision_id"] is None
    assert payload["artifact_metadata"]["decision_id"] is None


def test_activation_identity_remains_bound_when_a_later_challenger_arrives():
    assert _CANDIDATE != hashlib.sha256(_ACTIVE_JSON.encode()).hexdigest()
    records = _records()
    confidence = replace(
        _confidence(records),
        status=ConfidenceStatus.ACTIVE,
        active_kind="innovation-state-space",
        candidate_digest="b" * 64,
        generation=5,
    )
    activation = ModelActivationState(
        active_snapshot_json=_ACTIVE_JSON,
        rollback_snapshot_json=_ROLLBACK_JSON,
        evidence_decision_id="decision-6",
        controller_configuration_digest=_CONTROLLER_CONFIG,
        role_generation=4,
    )

    payload = build_evidence_report(
        confidence,
        records,
        activation_state=activation,
    ).to_dict()

    assert payload["active_model"] == {
        "kind": "innovation-state-space",
        "digest": _CANDIDATE,
    }
    assert payload["default_model"] == {"kind": "grey-box", "digest": _INCUMBENT}
    assert payload["candidate"] == {
        "kind": "innovation-state-space",
        "generation": 5,
        "digest": "b" * 64,
    }
    assert payload["artifact_metadata"]["provenance_digest"] == _INCUMBENT


def test_fallback_report_projects_prior_state_space_as_the_exact_active_owner():
    rollback_snapshot = {**_ACTIVE_SNAPSHOT, "model": {"generation": "a"}}
    rollback_json = json.dumps(rollback_snapshot, sort_keys=True, separators=(",", ":"))
    rollback_digest = canonical_snapshot_digest(rollback_snapshot)
    records = tuple(
        record for record in _records() if not isinstance(record.payload, (ActivationEvidence, RollbackEvidence))
    )
    activation_record = _record(
        "activation-b",
        ActivationEvidence(
            decision_id="decision-b",
            active_snapshot_json=_ACTIVE_JSON,
            rollback_snapshot_json=rollback_json,
            controller_configuration_digest=_CONTROLLER_CONFIG,
        ),
        timestamp_ms=200,
        generation=8,
        model_digest=_CANDIDATE,
        provenance_digest=rollback_digest,
    )
    rollback_record = _record(
        "rollback-b",
        RollbackEvidence(decision_id="decision-b", reason="operator rollback b"),
        timestamp_ms=201,
        generation=9,
        model_digest=_CANDIDATE,
        provenance_digest=rollback_digest,
    )
    records += (activation_record, rollback_record)
    activation = ModelActivationState(
        active_snapshot_json=_ACTIVE_JSON,
        rollback_snapshot_json=rollback_json,
        evidence_decision_id="decision-b",
        controller_configuration_digest=_CONTROLLER_CONFIG,
        role_generation=8,
    )
    state = report_module._confidence_state(records, activation)
    confidence = replace(
        _confidence(records),
        status=ConfidenceStatus.FALLBACK,
        active_kind=state["active_kind"],
    )

    payload = build_evidence_report(confidence, records, activation_state=activation).to_dict()

    assert payload["status"] == "fallback"
    assert payload["active_model"] == {
        "kind": "innovation-state-space",
        "digest": rollback_digest,
    }


def test_current_report_caches_confidence_by_immutable_ledger_and_activation(monkeypatch):
    records = _records()
    calls = 0
    evaluate = report_module.evaluate_confidence

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return evaluate(*args, **kwargs)

    monkeypatch.setattr(report_module, "evaluate_confidence", counted)

    first = report_module.current_evidence_report(records)
    second = report_module.current_evidence_report(tuple(reversed(records)))

    assert first.to_dict() == second.to_dict()
    assert calls == 1


def test_active_calibration_stage_is_not_reported_as_completed():
    terminal = _calibration("low", 1)
    baseline = terminal.payload.baseline_allocation
    assert baseline is not None
    combined = replace(
        baseline,
        normalized_combustion_load=0.2,
        auger_duty=0.1,
    )
    active_payload = replace(
        terminal.payload,
        status="active",
        probe_count=1,
        probe_q=-0.1,
        combined_q=0.2,
        combined_allocation=combined,
    )
    active = terminal.model_copy(update={"evidence_id": "calibration-active-low", "payload": active_payload})

    payload = build_evidence_report(_confidence((active,)), (active,)).to_dict()

    assert payload["calibration"]["status"] == "active"
    assert payload["calibration"]["stage"] == "low"
    assert payload["calibration"]["completed_stages"] == []
    assert payload["calibration"]["missing_stages"] == [
        "low",
        "middle",
        "high",
        "coast",
    ]


def test_production_terminal_completion_infers_finished_coast_transition():
    frame = _calibration("high", 3, ("low", "middle", "high"))
    terminal_payload = replace(
        frame.payload,
        accepted=False,
        status="inactive",
        stage=None,
    )
    terminal = frame.model_copy(
        update={
            "evidence_id": "calibration-terminal-complete",
            "payload": terminal_payload,
        }
    )

    payload = build_evidence_report(
        _confidence((terminal,)),
        (terminal,),
    ).to_dict()["calibration"]

    assert payload["status"] == "accepted"
    assert payload["completed_stages"] == ["low", "middle", "high", "coast"]
    assert payload["missing_stages"] == []
    assert payload["incomplete"] is False


def test_reset_progress_excludes_prior_stages_reasons_and_counts():
    old_complete = _calibration(
        "coast",
        4,
        ("low", "middle", "high"),
    )
    old_timeout = _record(
        "old-timeout",
        CalibrationSummaryEvidence(
            accepted=False,
            probe_count=0,
            reason="stage timeout",
        ),
        timestamp_ms=5,
    )
    reset = _record(
        "reset-progress",
        CalibrationSummaryEvidence(
            accepted=False,
            probe_count=0,
            status="cancelled",
            cancellation_reason="operator_reset-progress",
            cancellation_command_revision=8,
            cancellation_command_action="reset-progress",
        ),
        timestamp_ms=6,
    )
    terminal = _calibration("low", 7)
    baseline = terminal.payload.baseline_allocation
    assert baseline is not None
    active_payload = replace(
        terminal.payload,
        status="active",
        probe_count=1,
        probe_q=-0.1,
        combined_q=0.2,
        combined_allocation=replace(
            baseline,
            normalized_combustion_load=0.2,
            auger_duty=0.1,
        ),
    )
    active = terminal.model_copy(update={"evidence_id": "new-active-low", "payload": active_payload})
    records = (old_complete, old_timeout, reset, active)

    payload = build_evidence_report(_confidence(records), records).to_dict()

    assert payload["calibration"]["status"] == "active"
    assert payload["calibration"]["completed_stages"] == []
    assert payload["calibration"]["ineligible_reasons"] == []
    assert payload["calibration"]["timed_out"] is False
    assert payload["calibration"]["eligible_count"] == 7
