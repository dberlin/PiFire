"""End-to-end archive contract for one cook spanning PID-SP and MPC."""

import json
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import JsonValue

import file_mgmt.cookfile as cookfile_mod
from common.control_trace import (
    ActuationMode,
    AllocationClampReason,
    AllocationPayload,
    AppliedOutputPayload,
    ControllerBranch,
    ControllerType,
    ControlTraceRecord,
    FramedPulseFramePayload,
    InhibitReason,
    LearningSnapshotPayload,
    MpcFailureState,
    MpcUpdatePayload,
    OutputSource,
    PidSpUpdatePayload,
    ResultStaleState,
    SessionPayload,
    TraceEventKind,
    TraceSetting,
)
from common.cook_diagnostics import ControllerLearningReport
from common.defaults import default_metrics
from common.model_evidence import ConfidenceDecisionEvidence, EvidenceKind, ModelEvidenceRecord
from common.persistence.control_trace import append_control_trace
from common.persistence.history import append_metric, write_history
from common.persistence.model_evidence import append_model_evidence
from controller.model_learning.report import build_learning_report
from controller.pid_sp_learning import current_pid_sp_learning_report
from file_mgmt.cookfile import create_cookfile, read_cookfile

_COOK_ID = "cook-mixed-controller-7"
_PID_SESSION_ID = "session-pid-sp-7"
_MPC_SESSION_ID = "session-mpc-7"
_EXPECTED_TRACE_CONTROLLERS = ["pid_sp", "pid_sp", "pid_sp", "pid_sp", "mpc", "mpc", "mpc", "mpc", "mpc"]
_EXPECTED_TRACE_EVENTS = [
    "session",
    "control_update",
    "applied_output",
    "actuation_frame",
    "session",
    "control_update",
    "allocation",
    "actuation_frame",
    "applied_output",
]
_EXPECTED_TRACE_SESSIONS = [
    _PID_SESSION_ID,
    _PID_SESSION_ID,
    _PID_SESSION_ID,
    _PID_SESSION_ID,
    _MPC_SESSION_ID,
    _MPC_SESSION_ID,
    _MPC_SESSION_ID,
    _MPC_SESSION_ID,
    _MPC_SESSION_ID,
]
_EXPECTED_EVIDENCE_IDS = ["mpc-confidence-v2", "mpc-confidence-v3"]


def _session(controller: ControllerType, session_id: str, timestamp_ms: int) -> ControlTraceRecord:
    return ControlTraceRecord(
        ts_ms=timestamp_ms,
        session_id=session_id,
        cook_id=_COOK_ID,
        controller=controller,
        event_kind=TraceEventKind.SESSION,
        payload=SessionPayload(
            controller=controller,
            controller_config=(TraceSetting(key="policy", value=controller.value),),
            temperature_unit="F",
            control_period_seconds=2.0,
            model_revision=7 if controller is ControllerType.MPC else None,
            model_provenance="learned" if controller is ControllerType.MPC else None,
            pulse_slot_seconds=2.0,
            pulse_frame_seconds=20.0,
            fan_authority=controller is ControllerType.MPC,
            fan_pwm_capable=True,
            fan_min_duty=0.0,
            fan_max_duty=1.0,
            setpoint=225.0,
            ambient_temperature=70.0,
            software_version="1.2.3",
            build_version="task-7",
        ),
    )


def _pid_sp_update() -> ControlTraceRecord:
    return ControlTraceRecord(
        ts_ms=2_000,
        session_id=_PID_SESSION_ID,
        cook_id=_COOK_ID,
        controller=ControllerType.PID_SP,
        event_kind=TraceEventKind.CONTROL_UPDATE,
        payload=PidSpUpdatePayload(
            monotonic_ms=2_000,
            wall_ms=2_000,
            result_revision=1,
            result_age_ms=0,
            control_period_seconds=2.0,
            observed_dt_seconds=2.0,
            setpoint=225.0,
            measured_temperature=220.0,
            raw_output=0.45,
            requested_output=0.45,
            actuation_mode=ActuationMode.FRAMED_PULSE,
            prior_requested_auger_duty=0.4,
            prior_realized_auger_duty=0.35,
            requested_fan_duty=None,
            applied_fan_duty=None,
            output_source=OutputSource.CONTROLLER,
            inhibit_reason=InhibitReason.NONE,
            learning=LearningSnapshotPayload(
                schema_version=1,
                state={"status": "collecting", "accepted_samples": 12},
            ),
            error=5.0,
            proportional_term=0.3,
            integral_term=0.1,
            derivative_term=0.05,
            integral_accumulator=2.0,
            integral_clamped=False,
            derivative_input=-0.5,
            derivative_state=-0.25,
            proportional_band=30.0,
            kp=1.0,
            ki=0.1,
            kd=0.01,
            center=225.0,
            previous_temperature=219.0,
            previous_update_ms=0,
            measured_rate=0.5,
            predicted_temperature=221.0,
            predicted_error=4.0,
            tau_seconds=60.0,
            theta_seconds=5.0,
            stable_window_seconds=20.0,
            center_factor=1.0,
            new_target_before=False,
            new_target_after=False,
            target_change_temperature=225.0,
            target_change_ms=0,
            branch=ControllerBranch.NONE,
        ),
    )


def _pid_sp_applied() -> ControlTraceRecord:
    return ControlTraceRecord(
        ts_ms=22_000,
        session_id=_PID_SESSION_ID,
        cook_id=_COOK_ID,
        controller=ControllerType.PID_SP,
        event_kind=TraceEventKind.APPLIED_OUTPUT,
        payload=AppliedOutputPayload(
            result_revision=1,
            interval_start_ms=2_000,
            interval_end_ms=22_000,
            realized_auger_duty=0.45,
            realized_combustion_load=None,
            actual_fan_duty=None,
            sample_complete=True,
            output_source=OutputSource.CONTROLLER,
        ),
    )


def _frame(controller: ControllerType, session_id: str, revision: int, start_ms: int) -> ControlTraceRecord:
    mpc = controller is ControllerType.MPC
    return ControlTraceRecord(
        ts_ms=start_ms + 20_000,
        session_id=session_id,
        cook_id=_COOK_ID,
        controller=controller,
        event_kind=TraceEventKind.ACTUATION_FRAME,
        payload=FramedPulseFramePayload(
            result_revision=revision,
            pulse_slot_seconds=2.0,
            frame_seconds=20.0,
            frame_start_ms=start_ms,
            frame_end_ms=start_ms + 20_000,
            requested_combustion_load=0.6 if mpc else 0.45,
            requested_auger_duty=0.54 if mpc else 0.45,
            credit_before_seconds=0.0,
            credit_after_seconds=0.0,
            scheduled_on_seconds=10.8 if mpc else 9.0,
            delivered_on_seconds=10.8 if mpc else 9.0,
            actual_start_active=False,
            transition_count=2,
            actual_end_active=False,
            requested_fan_duty=0.55 if mpc else None,
            applied_fan_duty=0.55 if mpc else None,
            skipped=False,
            stale_command=False,
            inhibit_reason=InhibitReason.NONE,
            reset_reason=None,
        ),
    )


def _mpc_update() -> ControlTraceRecord:
    return ControlTraceRecord(
        ts_ms=31_000,
        session_id=_MPC_SESSION_ID,
        cook_id=_COOK_ID,
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.CONTROL_UPDATE,
        payload=MpcUpdatePayload(
            monotonic_ms=31_000,
            wall_ms=31_000,
            result_revision=2,
            result_age_ms=0,
            control_period_seconds=2.0,
            observed_dt_seconds=2.0,
            setpoint=225.0,
            measured_temperature=221.0,
            raw_output=0.6,
            requested_output=0.6,
            actuation_mode=ActuationMode.FRAMED_PULSE,
            prior_requested_auger_duty=0.45,
            prior_realized_auger_duty=0.45,
            requested_fan_duty=0.55,
            applied_fan_duty=0.55,
            output_source=OutputSource.CONTROLLER,
            inhibit_reason=InhibitReason.NONE,
            learning=LearningSnapshotPayload(
                schema_version=1,
                state={"status": "evaluating", "candidate_generation": 3},
            ),
            state_names=("temperature", "delay_1"),
            state_values=(221.0, 220.5),
            disturbance_estimate=0.1,
            model_revision=7,
            model_provenance="learned",
            raw_policy_firing_load=0.6,
            equilibrium_feed_forward=0.5,
            residual_move=0.1,
            bounded_firing_load=0.6,
            policy_kind="acados-grey",
            failure_state=MpcFailureState.SUCCESS,
            solve_start_ms=30_990,
            solve_end_ms=30_995,
            deadline_miss_count=0,
            stale=False,
            recovered=False,
            predicted_feasible=True,
            predicted_steady_load=0.55,
            solve_duration_ms=5,
            consecutive_deadline_miss_count=0,
            stale_state=ResultStaleState.FRESH,
        ),
    )


def _mpc_allocation() -> ControlTraceRecord:
    return ControlTraceRecord(
        ts_ms=31_000,
        session_id=_MPC_SESSION_ID,
        cook_id=_COOK_ID,
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.ALLOCATION,
        payload=AllocationPayload(
            result_revision=2,
            normalized_combustion_load=0.6,
            requested_auger_duty=0.54,
            requested_fan_duty=0.55,
            u_max=0.9,
            fan_min_pct=0.2,
            fan_max_pct=0.8,
            fan_enabled=True,
            mpc_has_fan_authority=True,
            auger_clamp_reason=AllocationClampReason.NONE,
            fan_clamp_reason=AllocationClampReason.NONE,
            allocator_revision=1,
        ),
    )


def _mpc_applied() -> ControlTraceRecord:
    return ControlTraceRecord(
        ts_ms=52_000,
        session_id=_MPC_SESSION_ID,
        cook_id=_COOK_ID,
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.APPLIED_OUTPUT,
        payload=AppliedOutputPayload(
            result_revision=2,
            interval_start_ms=32_000,
            interval_end_ms=52_000,
            realized_auger_duty=0.54,
            realized_combustion_load=0.6,
            actual_fan_duty=0.55,
            sample_complete=True,
            output_source=OutputSource.CONTROLLER,
        ),
    )


def _evidence(
    evidence_id: str,
    schema_version: Literal[1, 2, 3],
    *,
    cook_id: str = _COOK_ID,
) -> ModelEvidenceRecord:
    return ModelEvidenceRecord(
        evidence_id=evidence_id,
        kind=EvidenceKind.CONFIDENCE_DECISION,
        session_id=_MPC_SESSION_ID if cook_id == _COOK_ID else "session-foreign-mpc",
        cook_id=cook_id,
        timestamp_ms=60_000 + schema_version,
        role_generation=3,
        model_digest=None,
        provenance_digest=None,
        schema_version=schema_version,
        payload=ConfidenceDecisionEvidence(decision_id=f"decision-{evidence_id}", blocked=False),
    )


def _reports() -> Mapping[str, ControllerLearningReport]:
    pid_sp = current_pid_sp_learning_report(status={}, checkpoint=None)
    mpc = build_learning_report(
        (),
        activation_state={},
        live_status={},
        checkpoint_required=True,
        calibration_command_high_water=0,
    )
    return {
        "pid_sp": ControllerLearningReport(
            controller="pid_sp",
            schema_version=1,
            revision=pid_sp.revision,
            report=cast(Mapping[str, JsonValue], pid_sp.as_dict()),
        ),
        "mpc": ControllerLearningReport(
            controller="mpc",
            schema_version=1,
            revision=mpc.revision,
            report=cast(Mapping[str, JsonValue], mpc.as_dict()),
        ),
    }


def _seed_archive_prerequisites() -> None:
    settings = cookfile_mod.read_settings()
    probe_info = settings["probe_settings"]["probe_map"]["probe_info"]
    primary_label = next(probe["label"] for probe in probe_info if probe["type"] == "Primary")
    food_labels = [probe["label"] for probe in probe_info if probe["type"] == "Food"]
    write_history(
        {
            "probe_history": {
                "primary": {primary_label: 221.0},
                "food": {label: 160.0 for label in food_labels},
                "aux": {},
            },
            "primary_setpoint": 225.0,
            "notify_targets": {primary_label: 225.0, **{label: 165.0 for label in food_labels}},
        }
    )
    append_metric(dict(default_metrics(), mode="Hold", augerontime=180))


def _assert_mixed_controller_envelope(payload: Mapping[str, Any]) -> None:
    assert payload["schema_version"] == 1
    assert payload["cook_id"] == _COOK_ID
    assert payload["controllers"] == ["pid_sp", "mpc"]

    reports = payload["reports"]
    assert isinstance(reports, list)
    assert len(reports) == 2
    assert [report["controller"] for report in reports] == ["pid_sp", "mpc"]
    assert reports[0]["report"]["controller"] == "pid_sp"
    assert reports[0]["report"]["schema_version"] == 1
    assert reports[1]["report"]["schema_version"] == 2

    trace = payload["control_trace"]
    assert isinstance(trace, dict)
    records = trace["records"]
    assert len(records) == 9
    assert [record["controller"] for record in records] == _EXPECTED_TRACE_CONTROLLERS
    assert [record["event_kind"] for record in records] == _EXPECTED_TRACE_EVENTS
    assert [record["session_id"] for record in records] == _EXPECTED_TRACE_SESSIONS
    assert {record["cook_id"] for record in records} == {_COOK_ID}
    assert trace["record_schema_versions"] == [6]
    assert records[1]["payload"]["learning"] == {
        "schema_version": 1,
        "state": {"status": "collecting", "accepted_samples": 12},
    }
    assert records[5]["payload"]["learning"] == {
        "schema_version": 1,
        "state": {"status": "evaluating", "candidate_generation": 3},
    }

    evidence = payload["model_evidence"]
    assert isinstance(evidence, dict)
    evidence_records = evidence["records"]
    assert len(evidence_records) == 2
    assert [record["evidence_id"] for record in evidence_records] == _EXPECTED_EVIDENCE_IDS
    assert [record["session_id"] for record in evidence_records] == [_MPC_SESSION_ID, _MPC_SESSION_ID]
    assert {record["cook_id"] for record in evidence_records} == {_COOK_ID}
    assert evidence["record_schema_versions"] == [2, 3]
    assert payload["capture_errors"] == []


def test_mixed_controller_diagnostics_round_trip_through_real_sqlite_and_cookfile(
    ds,
    tmp_path,
    monkeypatch,
) -> None:
    """One durable cook keeps every typed diagnostic in first-seen order through ZIP readback."""
    history_dir = tmp_path / "history"
    monkeypatch.setattr(cookfile_mod, "HISTORY_FOLDER", f"{history_dir}/")
    _seed_archive_prerequisites()

    trace_records = [
        _session(ControllerType.PID_SP, _PID_SESSION_ID, 1_000),
        _pid_sp_update(),
        _pid_sp_applied(),
        _frame(ControllerType.PID_SP, _PID_SESSION_ID, 1, 2_000),
        _session(ControllerType.MPC, _MPC_SESSION_ID, 30_000),
        _mpc_update(),
        _mpc_allocation(),
        _frame(ControllerType.MPC, _MPC_SESSION_ID, 2, 32_000),
        _mpc_applied(),
    ]
    append_control_trace(trace_records)
    append_model_evidence(
        [
            _evidence("mpc-confidence-v2", 2),
            _evidence("foreign-confidence", 3, cook_id="cook-foreign"),
            _evidence("mpc-confidence-v3", 3),
        ]
    )
    reports = _reports()

    create_cookfile(cook_id=_COOK_ID, learning_report_provider=reports.__getitem__)

    archives = list(Path(history_dir).glob("*.pifire"))
    assert len(archives) == 1
    with zipfile.ZipFile(archives[0]) as archive:
        assert "learning_diagnostics.json" in archive.namelist()
        payload = json.loads(archive.read("learning_diagnostics.json"))
    _assert_mixed_controller_envelope(payload)

    reread, status = read_cookfile(archives[0])
    assert status == "OK"
    assert reread["learning_diagnostics"] == payload
    _assert_mixed_controller_envelope(reread["learning_diagnostics"])
