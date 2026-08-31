"""End-to-end archive contract for one cook spanning PID-SP and MPC."""

import json
import zipfile
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import JsonValue

import file_mgmt.cookfile as cookfile_mod
from common.control_trace import (
    TRACE_SCHEMA_VERSION,
    ActuationMode,
    AllocationClampReason,
    AllocationPayload,
    AmbientSource,
    AmbientUncertainty,
    AppliedOutputPayload,
    ControllerType,
    ControlTraceRecord,
    FramedPulseFramePayload,
    InhibitReason,
    LearningSnapshotPayload,
    ModelEventPayload,
    ModelEventType,
    ModelObservationPayload,
    MpcFailureState,
    MpcUpdatePayload,
    OutputSource,
    PidSpUpdatePayload,
    ResultStaleState,
    SessionPayload,
    TraceEventKind,
    TraceSetting,
    TrajectorySegmentTracePayload,
)
from common.cook_diagnostics import ControllerLearningReport
from common.defaults import default_metrics
from common.learning_trajectory import (
    FrameDeliveryCertainty,
    HoldEntrySample,
    LearningTrajectoryFrame,
    LearningTrajectorySegment,
    TrajectoryBreakReason,
    canonical_trajectory_digest,
)
from common.model_evidence import ConfidenceDecisionEvidence, EvidenceKind, ModelEvidenceRecord
from common.persistence.control_trace import append_control_trace
from common.persistence.history import append_metric, write_history
from common.persistence.learning_trajectory import LearningTrajectoryRepository
from common.persistence.model_evidence import append_model_evidence
from controller.model_learning.report import build_learning_report
from controller.pid_sp_learning import current_pid_sp_learning_report
from file_mgmt.cookfile import (
    CookLearningImportResult,
    create_cookfile,
    import_cookfile_learning_trajectory,
    read_cookfile,
)
from tests.unit.controller._control_trace_fixtures import current_pid_sp_records

_COOK_ID = "cook-mixed-controller-7"
_PID_SESSION_ID = "session-pid-sp-7"
_MPC_SESSION_ID = "session-mpc-7"
_EXPECTED_TRACE_CONTROLLERS = [
    "pid_sp",
    "pid_sp",
    "pid_sp",
    "pid_sp",
    "pid_sp",
    "mpc",
    "mpc",
    "mpc",
    "mpc",
    "mpc",
]
_EXPECTED_TRACE_EVENTS = [
    "session",
    "control_update",
    "allocation",
    "actuation_frame",
    "applied_output",
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
    _PID_SESSION_ID,
    _MPC_SESSION_ID,
    _MPC_SESSION_ID,
    _MPC_SESSION_ID,
    _MPC_SESSION_ID,
    _MPC_SESSION_ID,
]
_EXPECTED_EVIDENCE_IDS = ["mpc-confidence-v2", "mpc-confidence-v3"]
type _TraceSchemaVersion = Literal[6, 7, 8]


def _session(
    controller: ControllerType,
    session_id: str,
    timestamp_ms: int,
    *,
    schema_version: _TraceSchemaVersion = TRACE_SCHEMA_VERSION,
) -> ControlTraceRecord:
    return ControlTraceRecord(
        ts_ms=timestamp_ms,
        session_id=session_id,
        cook_id=_COOK_ID,
        controller=controller,
        event_kind=TraceEventKind.SESSION,
        schema_version=schema_version,
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


def _current_pid_sp_trace() -> tuple[ControlTraceRecord, ...]:
    records = current_pid_sp_records(
        session_id=_PID_SESSION_ID,
        cook_id=_COOK_ID,
        raw_demand=0.45,
        include_frame=True,
    )
    update = records[1]
    assert isinstance(update.payload, PidSpUpdatePayload)
    update = update.model_copy(
        update={
            "payload": replace(
                update.payload,
                learning=LearningSnapshotPayload(
                    schema_version=1,
                    state={"status": "collecting", "accepted_samples": 12},
                ),
            )
        }
    )
    return records[:1] + (update,) + records[2:]


def _frame(
    controller: ControllerType,
    session_id: str,
    revision: int,
    start_ms: int,
    *,
    schema_version: _TraceSchemaVersion = TRACE_SCHEMA_VERSION,
) -> ControlTraceRecord:
    mpc = controller is ControllerType.MPC
    return ControlTraceRecord(
        ts_ms=start_ms + 20_000,
        session_id=session_id,
        cook_id=_COOK_ID,
        controller=controller,
        event_kind=TraceEventKind.ACTUATION_FRAME,
        schema_version=schema_version,
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


def _mpc_update(*, schema_version: _TraceSchemaVersion = TRACE_SCHEMA_VERSION) -> ControlTraceRecord:
    return ControlTraceRecord(
        ts_ms=31_000,
        session_id=_MPC_SESSION_ID,
        cook_id=_COOK_ID,
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.CONTROL_UPDATE,
        schema_version=schema_version,
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


def _mpc_allocation(*, schema_version: _TraceSchemaVersion = TRACE_SCHEMA_VERSION) -> ControlTraceRecord:
    return ControlTraceRecord(
        ts_ms=31_000,
        session_id=_MPC_SESSION_ID,
        cook_id=_COOK_ID,
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.ALLOCATION,
        schema_version=schema_version,
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


def _mpc_applied(*, schema_version: _TraceSchemaVersion = TRACE_SCHEMA_VERSION) -> ControlTraceRecord:
    return ControlTraceRecord(
        ts_ms=52_000,
        session_id=_MPC_SESSION_ID,
        cook_id=_COOK_ID,
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.APPLIED_OUTPUT,
        schema_version=schema_version,
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


def _mpc_model_event(*, schema_version: _TraceSchemaVersion) -> ControlTraceRecord:
    return ControlTraceRecord(
        ts_ms=30_500,
        session_id=_MPC_SESSION_ID,
        cook_id=_COOK_ID,
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.MODEL_EVENT,
        schema_version=schema_version,
        payload=ModelEventPayload(
            event=ModelEventType.RESTORE,
            model_revision=7,
            provenance="learned",
            detail="restored exact v7 fixture",
            model_kind="grey-box",
            model_schema="grey-v4",
            role_generation=3,
            snapshot_digest=canonical_trajectory_digest({"fixture": "exact-v7-model", "generation": 3}),
        ),
    )


def _mpc_observation(*, schema_version: _TraceSchemaVersion) -> ControlTraceRecord:
    return ControlTraceRecord(
        ts_ms=52_000,
        session_id=_MPC_SESSION_ID,
        cook_id=_COOK_ID,
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.MODEL_OBSERVATION,
        schema_version=schema_version,
        payload=ModelObservationPayload(
            frame_start_ms=32_000,
            frame_end_ms=52_000,
            temp_c=105.0,
            setpoint_c=107.22222222222223,
            ambient_c=21.11111111111111,
            observation_sequence=1,
            probe_valid=True,
            probe_source="grill-probe-1",
            ambient_source=AmbientSource.CONFIGURED,
            ambient_uncertainty=AmbientUncertainty.ESTIMATED,
            baseline_combustion_load=0.6,
            calibration_probe_load=0.0,
            requested_combustion_load=0.6,
            allocated_combustion_load=0.6,
            realized_combustion_load=0.6,
            requested_auger_duty=0.54,
            scheduled_on_seconds=10.8,
            delivered_on_seconds=10.8,
            realized_auger_duty=0.54,
            allocator_revision=1,
            allocation_clamp_reasons=(),
            calibration_stage=None,
            calibration_fit=False,
            result_revision=2,
            eligible=True,
            rejection_reasons=(),
            input_variance=0.04,
            input_levels=2,
            effective_updates=1,
            role_generation=3,
            model_digest=canonical_trajectory_digest({"fixture": "exact-v7-model", "generation": 3}),
            requested_fan_duty=0.55,
            actual_fan_duty=0.55,
            output_source=OutputSource.CONTROLLER,
            lid_open=False,
            safety_inhibited=False,
            manual_override=False,
            stale=False,
            skipped=False,
            reset=False,
            continuous=True,
        ),
    )


def _exact_mpc_trace(
    schema_version: _TraceSchemaVersion,
) -> tuple[ControlTraceRecord, ...]:
    return (
        _session(
            ControllerType.MPC,
            _MPC_SESSION_ID,
            30_000,
            schema_version=schema_version,
        ),
        _mpc_model_event(schema_version=schema_version),
        _mpc_update(schema_version=schema_version),
        _mpc_allocation(schema_version=schema_version),
        _frame(
            ControllerType.MPC,
            _MPC_SESSION_ID,
            2,
            32_000,
            schema_version=schema_version,
        ),
        _mpc_applied(schema_version=schema_version),
        _mpc_observation(schema_version=schema_version),
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


def _digest(label: str) -> str:
    return canonical_trajectory_digest({"cookfile-integration-fixture": label})


def _trajectory_frame(
    sequence: int,
    *,
    monotonic_start_ms: int,
    wall_start_ms: int,
    effective_mode: str,
) -> LearningTrajectoryFrame:
    return LearningTrajectoryFrame(
        sequence=sequence,
        monotonic_start_ms=monotonic_start_ms,
        monotonic_end_ms=monotonic_start_ms + 20_000,
        wall_start_ms=wall_start_ms,
        wall_end_ms=wall_start_ms + 20_000,
        chamber_temperature_c=100.0 + sequence,
        temperature_sample_monotonic_ms=monotonic_start_ms + 20_000,
        temperature_sample_wall_ms=wall_start_ms + 20_000,
        temperature_sample_age_ms=0,
        temperature_sample_wall_age_ms=0,
        temperature_sample_clock_skew_ms=0,
        source_temperature_units="C",
        settings_revision=7,
        probe_valid=True,
        probe_source="grill-probe-1",
        ambient_temperature_c=21.0,
        ambient_source="configured",
        ambient_uncertainty_c=1.0,
        delivered_auger_on_seconds=8.0,
        realized_auger_duty=0.4,
        normalized_combustion_load=0.4,
        delivered_fan_on_seconds=20.0,
        fan_duty_integral_seconds=10.0,
        mean_actual_fan_duty=0.5,
        auger_delivery_certainty=FrameDeliveryCertainty.EXACT,
        fan_delivery_certainty=FrameDeliveryCertainty.EXACT,
        effective_mode=effective_mode,
        recipe_step_id=None,
        complete=True,
        continuous=True,
        partial=False,
        boundary_reason=None,
        role_generation=3,
    )


def _trajectory_segment(
    segment_id: str,
    *,
    cook_id: str,
    trace_session_id: str,
    start_ms: int,
) -> LearningTrajectorySegment:
    pre_roll = _trajectory_frame(
        0,
        monotonic_start_ms=start_ms,
        wall_start_ms=1_000_000 + start_ms,
        effective_mode="Smoke",
    )
    scored = _trajectory_frame(
        1,
        monotonic_start_ms=start_ms + 20_000,
        wall_start_ms=1_020_000 + start_ms,
        effective_mode="Hold",
    )
    return LearningTrajectorySegment(
        schema_version=1,
        observation_schema_version=3,
        segment_id=segment_id,
        cook_id=cook_id,
        trajectory_session_id=f"trajectory-{segment_id}",
        trace_session_ids=(trace_session_id,),
        collection_provenance={"origin": "passive-online", "role_generation": 3},
        configuration_provenance={"controller": "MPC", "revision": 7},
        cadence_digest=_digest("cadence-20-seconds-v1"),
        model_structure_digest=_digest("grey-one-zone-erlang-v1"),
        held_physics_digest=_digest("held-grey-physics-v1"),
        delay_input_mapping_digest=_digest("normalized-combustion-load-v1"),
        actuation_mapping_digest=_digest("framed-pulse-v1"),
        scored_fan_regime_digest=_digest("fan-regime-v1"),
        ambient_semantics_digest=_digest("ambient-configured-celsius-v1"),
        pre_roll_frames=(pre_roll,),
        hold_entry=HoldEntrySample(
            monotonic_ms=scored.monotonic_start_ms,
            wall_ms=scored.wall_start_ms,
            chamber_temperature_c=scored.chamber_temperature_c,
            probe_valid=True,
            probe_source="grill-probe-1",
        ),
        scored_hold_frames=(scored,),
        generation_audit_ranges=({"start_sequence": 0, "end_sequence": 1, "role_generation": 3},),
        start_monotonic_ms=pre_roll.monotonic_start_ms,
        end_monotonic_ms=scored.monotonic_end_ms,
        start_wall_ms=pre_roll.wall_start_ms,
        end_wall_ms=scored.wall_end_ms,
        start_sequence=0,
        end_sequence=1,
        pre_roll_end_reason=None,
        terminal_break_reason=TrajectoryBreakReason.STOP,
        state="finalized",
        source_trace_digest=_digest(f"{segment_id}:source-trace"),
        source_schema_version=7,
        source_row_digest=_digest(f"{segment_id}:source-rows"),
        build_provenance={"builder": "trajectory-runtime", "revision": 1},
    )


def _persist_segment(
    repository: LearningTrajectoryRepository,
    segment: LearningTrajectorySegment,
) -> LearningTrajectorySegment:
    cursor = repository.begin_segment(replace(segment, state="open", terminal_break_reason=None))
    repository.finalize(cursor, TrajectoryBreakReason.STOP)
    stored = repository.read_segment(segment.segment_id)
    assert stored is not None
    return stored


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


def _create_trace_archive(
    history_dir: Path,
    monkeypatch,
    records: tuple[ControlTraceRecord, ...],
) -> Path:
    monkeypatch.setattr(cookfile_mod, "HISTORY_FOLDER", f"{history_dir}/")
    _seed_archive_prerequisites()
    append_control_trace(records)
    reports = _reports()
    create_cookfile(cook_id=_COOK_ID, learning_report_provider=reports.__getitem__)
    archives = list(history_dir.glob("*.pifire"))
    assert len(archives) == 1
    return archives[0]


def _replace_learning_diagnostics(path: Path, payload: Mapping[str, Any]) -> None:
    with zipfile.ZipFile(path) as source:
        members = [(info, source.read(info.filename)) for info in source.infolist()]
    encoded = json.dumps(payload, indent=2, sort_keys=True).encode()
    with zipfile.ZipFile(path, "w") as target:
        for info, content in members:
            target.writestr(
                info,
                encoded if info.filename == "learning_diagnostics.json" else content,
            )


def _assert_mixed_controller_envelope(payload: Mapping[str, Any]) -> None:
    assert payload["schema_version"] == 2
    assert payload["cook_id"] == _COOK_ID
    assert payload["controllers"] == ["pid_sp", "mpc"]

    reports = payload["reports"]
    assert isinstance(reports, list)
    assert len(reports) == 2
    assert [report["controller"] for report in reports] == ["pid_sp", "mpc"]
    assert reports[0]["report"]["controller"] == "pid_sp"
    assert reports[0]["report"]["schema_version"] == 1
    assert reports[1]["report"]["schema_version"] == 3

    trace = payload["control_trace"]
    assert isinstance(trace, dict)
    records = trace["records"]
    assert len(records) == 10
    assert [record["controller"] for record in records] == _EXPECTED_TRACE_CONTROLLERS
    assert [record["event_kind"] for record in records] == _EXPECTED_TRACE_EVENTS
    assert [record["session_id"] for record in records] == _EXPECTED_TRACE_SESSIONS
    assert {record["cook_id"] for record in records} == {_COOK_ID}
    assert trace["record_schema_versions"] == [TRACE_SCHEMA_VERSION]
    assert records[1]["payload"]["learning"] == {
        "schema_version": 1,
        "state": {"status": "collecting", "accepted_samples": 12},
    }
    assert records[1]["payload"]["raw_output"] == 0.45
    assert records[1]["payload"]["requested_output"] == 0.45
    assert records[3]["payload"]["requested_auger_duty"] == 0.45
    assert records[3]["payload"]["scheduled_on_seconds"] == 8.0
    assert records[4]["payload"]["realized_auger_duty"] == 0.4
    assert records[4]["payload"]["realized_combustion_load"] == 0.4
    assert records[6]["payload"]["learning"] == {
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

    assert payload["trajectory_segments"] == []
    assert payload["trajectory_schema_versions"] == []
    corpus = payload["corpus"]
    assert corpus["schema_version"] == 1
    assert corpus["segment_count"] == 0
    assert corpus["pre_roll_count"] == 0
    assert corpus["scored_count"] == 0
    assert "segments" not in corpus
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
        *_current_pid_sp_trace(),
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


def test_cookfile_exports_current_cook_segment_references_and_one_global_corpus_report(
    ds,
    tmp_path,
    monkeypatch,
) -> None:
    repository = LearningTrajectoryRepository()
    current = _persist_segment(
        repository,
        _trajectory_segment(
            "segment-current-cook",
            cook_id=_COOK_ID,
            trace_session_id=_MPC_SESSION_ID,
            start_ms=100_000,
        ),
    )
    _persist_segment(
        repository,
        _trajectory_segment(
            "segment-foreign-cook",
            cook_id="cook-foreign",
            trace_session_id="session-foreign-mpc",
            start_ms=200_000,
        ),
    )
    archive = _create_trace_archive(
        tmp_path / "history",
        monkeypatch,
        (_session(ControllerType.MPC, _MPC_SESSION_ID, 30_000),),
    )

    with zipfile.ZipFile(archive) as source:
        payload = json.loads(source.read("learning_diagnostics.json"))

    assert payload["trajectory_schema_versions"] == [1]
    assert payload["trajectory_segments"] == [
        {
            "segment_id": current.segment_id,
            "trajectory_session_id": current.trajectory_session_id,
            "trace_session_ids": list(current.trace_session_ids),
            "cook_id": _COOK_ID,
            "segment_schema_version": current.schema_version,
            "observation_schema_version": current.observation_schema_version,
            "state": current.state,
            "source_trace_digest": current.source_trace_digest,
            "source_schema_version": current.source_schema_version,
            "content_digest": current.content_digest,
            "fit_partition_digest": current.fit_partition_digest,
            "source_row_digest": current.source_row_digest,
            "pre_roll_frame_count": len(current.pre_roll_frames),
            "scored_hold_frame_count": len(current.scored_hold_frames),
            "terminal_break_reason": current.terminal_break_reason.value,
        }
    ]
    reference = payload["trajectory_segments"][0]
    assert "pre_roll_frames" not in reference
    assert "scored_hold_frames" not in reference
    assert "corpus" not in reference

    status_before_read = repository.status()
    corpus = payload["corpus"]
    assert corpus["schema_version"] == 1
    assert corpus["corpus_revision"] == status_before_read.corpus_revision
    assert corpus["segment_count"] == 2
    assert corpus["pre_roll_count"] == 2
    assert corpus["scored_count"] == 2
    assert corpus["evicted_segment_count"] == 0
    assert corpus["quarantined_segment_count"] == 0
    assert "segments" not in corpus

    reread, status = read_cookfile(archive)
    assert status == "OK"
    assert reread["learning_diagnostics"] == payload
    assert repository.status() == status_before_read


def test_explicit_v7_import_is_exact_and_second_import_is_idempotent(
    ds,
    tmp_path,
    monkeypatch,
) -> None:
    archive = _create_trace_archive(
        tmp_path / "history",
        monkeypatch,
        _exact_mpc_trace(7),
    )
    repository = LearningTrajectoryRepository(str(tmp_path / "imported.db"))
    empty = repository.status()

    _payload, status = read_cookfile(archive)
    assert status == "OK"
    assert repository.status() == empty

    first = import_cookfile_learning_trajectory(archive, repository=repository)
    assert isinstance(first, CookLearningImportResult)
    assert first.outcome == "imported"
    assert first.source_schema_version == 7
    assert len(first.segment_ids) == 1

    imported = repository.read_segment(first.segment_ids[0])
    assert imported is not None
    assert imported.cook_id == _COOK_ID
    assert imported.trace_session_ids == (_MPC_SESSION_ID,)
    assert imported.source_schema_version == 7
    assert imported.pre_roll_frames == ()
    assert len(imported.scored_hold_frames) == 1
    frame = imported.scored_hold_frames[0]
    assert frame.chamber_temperature_c == 105.0
    assert frame.delivered_auger_on_seconds == 10.8
    assert frame.realized_auger_duty == 0.54
    assert frame.normalized_combustion_load == 0.6
    assert frame.role_generation == 3
    assert tuple(dict(item.items()) for item in imported.generation_audit_ranges) == (
        {"start_sequence": 1, "end_sequence": 1, "role_generation": 3},
    )

    imported_status = repository.status()
    second = import_cookfile_learning_trajectory(archive, repository=repository)
    assert isinstance(second, CookLearningImportResult)
    assert second.outcome == "idempotent"
    assert second.source_schema_version == 7
    assert second.segment_ids == first.segment_ids
    assert repository.status() == imported_status
    assert repository.read_segment(first.segment_ids[0]) == imported


def test_historical_v1_diagnostics_migrate_strictly_for_exact_v7_import(
    ds,
    tmp_path,
    monkeypatch,
) -> None:
    archive = _create_trace_archive(
        tmp_path / "history",
        monkeypatch,
        _exact_mpc_trace(7),
    )
    payload, status = read_cookfile(archive)
    assert status == "OK"
    diagnostics = payload["learning_diagnostics"]
    diagnostics["schema_version"] = 1
    diagnostics.pop("trajectory_segments")
    diagnostics.pop("trajectory_schema_versions")
    diagnostics.pop("corpus")
    _replace_learning_diagnostics(archive, diagnostics)
    repository = LearningTrajectoryRepository(str(tmp_path / "historical-v1.db"))

    result = import_cookfile_learning_trajectory(
        archive,
        repository=repository,
    )

    assert result.outcome == "imported"
    assert result.source_schema_version == 7
    assert len(result.segment_ids) == 1
    imported = repository.read_segment(result.segment_ids[0])
    assert imported is not None
    assert imported.source_schema_version == 7
    assert imported.pre_roll_frames == ()


def test_exact_v7_import_accepts_every_rotated_mpc_session_atomically(
    ds,
    tmp_path,
    monkeypatch,
) -> None:
    first = _exact_mpc_trace(7)
    second_session_id = "00000000-0000-4000-8000-000000000002"
    second = tuple(record.model_copy(update={"session_id": second_session_id}) for record in first)
    records = tuple(
        sorted(
            (*first, *second),
            key=lambda record: (record.ts_ms, record.session_id),
        )
    )
    archive = _create_trace_archive(
        tmp_path / "history",
        monkeypatch,
        records,
    )
    repository = LearningTrajectoryRepository(str(tmp_path / "rotated-sessions.db"))

    first_result = import_cookfile_learning_trajectory(
        archive,
        repository=repository,
    )
    second_result = import_cookfile_learning_trajectory(
        archive,
        repository=repository,
    )

    assert first_result.outcome == "imported"
    assert first_result.source_schema_version == 7
    assert len(first_result.segment_ids) == 2
    assert second_result.outcome == "idempotent"
    assert second_result.segment_ids == first_result.segment_ids
    imported = tuple(repository.read_segment(segment_id) for segment_id in first_result.segment_ids)
    assert all(segment is not None for segment in imported)
    assert {segment.trace_session_ids for segment in imported if segment is not None} == {
        (_MPC_SESSION_ID,),
        (second_session_id,),
    }


def test_v8_diagnostics_accept_delayed_segment_metadata_after_newer_rows(
    ds,
    tmp_path,
    monkeypatch,
) -> None:
    delayed_segment = _trajectory_segment(
        "delayed-old-session",
        cook_id=_COOK_ID,
        trace_session_id=_MPC_SESSION_ID,
        start_ms=0,
    )
    delayed_trace_metadata = ControlTraceRecord(
        ts_ms=1,
        session_id=_MPC_SESSION_ID,
        cook_id=_COOK_ID,
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.TRAJECTORY_SEGMENT,
        schema_version=TRACE_SCHEMA_VERSION,
        payload=TrajectorySegmentTracePayload(
            segment_id=delayed_segment.segment_id,
            trajectory_session_id=delayed_segment.trajectory_session_id,
            trace_session_ids=delayed_segment.trace_session_ids,
            cook_id=delayed_segment.cook_id,
            segment_schema_version=delayed_segment.schema_version,
            observation_schema_version=delayed_segment.observation_schema_version,
            state=delayed_segment.state,
            source_trace_digest=delayed_segment.source_trace_digest,
            content_digest=delayed_segment.content_digest,
            fit_partition_digest=delayed_segment.fit_partition_digest,
            source_row_digest=delayed_segment.source_row_digest,
            pre_roll_frame_count=len(delayed_segment.pre_roll_frames),
            scored_hold_frame_count=len(delayed_segment.scored_hold_frames),
            terminal_break_reason=delayed_segment.terminal_break_reason.value,
        ),
    )
    archive = _create_trace_archive(
        tmp_path / "history",
        monkeypatch,
        (*_exact_mpc_trace(TRACE_SCHEMA_VERSION), delayed_trace_metadata),
    )

    payload, status = read_cookfile(archive)
    diagnostics = cookfile_mod._validated_learning_diagnostics(payload["learning_diagnostics"])

    assert status == "OK"
    assert len(cookfile_mod._mpc_sessions(diagnostics)) == 1


def test_explicit_v6_import_is_audit_only_and_never_synthesizes_pre_roll(
    ds,
    tmp_path,
    monkeypatch,
) -> None:
    archive = _create_trace_archive(
        tmp_path / "history",
        monkeypatch,
        _exact_mpc_trace(6),
    )
    repository = LearningTrajectoryRepository(str(tmp_path / "audit-only.db"))
    before = repository.status()

    result = import_cookfile_learning_trajectory(archive, repository=repository)

    assert isinstance(result, CookLearningImportResult)
    assert result.outcome == "audit-only"
    assert result.source_schema_version == 6
    assert result.segment_ids == ()
    assert repository.status() == before
    assert repository.status().segment_count == 0
    assert repository.status().pre_roll_count == 0
    assert repository.status().scored_count == 0


def test_corrupt_v7_model_provenance_digest_is_non_replayable_without_partial_import(
    ds,
    tmp_path,
    monkeypatch,
) -> None:
    archive = _create_trace_archive(
        tmp_path / "history",
        monkeypatch,
        _exact_mpc_trace(7),
    )
    payload, status = read_cookfile(archive)
    assert status == "OK"
    diagnostics = payload["learning_diagnostics"]
    observation = next(
        record for record in diagnostics["control_trace"]["records"] if record["event_kind"] == "model_observation"
    )
    observation["payload"]["model_digest"] = canonical_trajectory_digest({"fixture": "corrupt-model-provenance"})
    _replace_learning_diagnostics(archive, diagnostics)

    repository = LearningTrajectoryRepository(str(tmp_path / "corrupt.db"))
    before = repository.status()
    result = import_cookfile_learning_trajectory(archive, repository=repository)

    assert isinstance(result, CookLearningImportResult)
    assert result.outcome == "non-replayable"
    assert result.source_schema_version == 7
    assert result.segment_ids == ()
    assert repository.status() == before
    assert repository.status().segment_count == 0
