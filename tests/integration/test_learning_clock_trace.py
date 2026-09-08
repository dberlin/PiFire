"""Trace clock domains must survive lossless persistence and exact imports."""

import json
from dataclasses import replace

import pytest
from pydantic import ValidationError

import file_mgmt.cookfile as cookfile_module
from common.control_trace import (
    ActuationMode,
    AllocationPayload,
    AmbientSource,
    AmbientUncertainty,
    AppliedOutputPayload,
    ChallengerProgressTracePayload,
    ControllerType,
    ControlTraceRecord,
    FramedPulseFramePayload,
    InhibitReason,
    ModelEventPayload,
    ModelEventType,
    ModelObservationPayload,
    MpcFailureState,
    MpcUpdatePayload,
    ResultStaleState,
    SafetyEventPayload,
    SafetyEventType,
    SessionPayload,
    TraceEventKind,
)
from common.defaults import default_metrics
from common.mpc_learning import MPC_FORECAST_HORIZON_SECONDS
from common.persistence.control_trace import append_control_trace
from common.persistence.history import append_metric, write_history
from common.persistence.learning_trajectory import LearningTrajectoryRepository
from controller.applied_output import OutputSource
from controller.control_trace_replay import validate_records
from controller.model_learning.trace import TraceSelectionError, learning_observations
from file_mgmt.cookfile import _exact_import_segment, _NonReplayableCookLearning
from tests.unit.controller._control_trace_fixtures import current_pid_sp_records

_WALL = 1_789_000_000_000
_DIGEST = "a" * 64


def _records(*, rollback_ms: int = 0) -> tuple[ControlTraceRecord, ...]:
    session, pid, allocation, pulse, applied = current_pid_sp_records(include_frame=True)
    assert isinstance(session.payload, SessionPayload)
    assert isinstance(allocation.payload, AllocationPayload)
    assert isinstance(pulse.payload, FramedPulseFramePayload)
    assert isinstance(applied.payload, AppliedOutputPayload)
    allocation_payload = replace(
        allocation.payload, requested_fan_duty=0.5, fan_max_pct=1.0, fan_enabled=True, mpc_has_fan_authority=True
    )
    pulse_payload = replace(pulse.payload, requested_fan_duty=0.5, applied_fan_duty=0.5)
    applied_payload = replace(applied.payload, actual_fan_duty=0.5)
    update = MpcUpdatePayload(
        monotonic_ms=2_000,
        wall_ms=_WALL + 2_000,
        result_revision=1,
        result_age_ms=0,
        control_period_seconds=2.0,
        observed_dt_seconds=2.0,
        setpoint=225.0,
        measured_temperature=220.0,
        raw_output=0.5,
        requested_output=0.5,
        actuation_mode=ActuationMode.FRAMED_PULSE,
        prior_requested_auger_duty=0.5,
        prior_realized_auger_duty=0.5,
        requested_fan_duty=0.5,
        applied_fan_duty=0.5,
        output_source=OutputSource.CONTROLLER,
        inhibit_reason=InhibitReason.NONE,
        state_names=("temperature",),
        state_values=(220.0,),
        disturbance_estimate=0.0,
        model_revision=7,
        model_provenance="learned",
        raw_policy_firing_load=0.5,
        equilibrium_feed_forward=0.5,
        residual_move=0.0,
        bounded_firing_load=0.5,
        policy_kind="acados-grey",
        failure_state=MpcFailureState.SUCCESS,
        solve_start_ms=1_995,
        solve_end_ms=2_000,
        deadline_miss_count=0,
        stale=False,
        recovered=False,
        predicted_feasible=True,
        predicted_steady_load=0.5,
        solve_duration_ms=5,
        consecutive_deadline_miss_count=0,
        stale_state=ResultStaleState.FRESH,
    )
    wall_start = _WALL + 2_000
    wall_end = _WALL + 22_000 - rollback_ms
    observation = ModelObservationPayload(
        frame_start_ms=2_000,
        frame_end_ms=22_000,
        wall_start_ms=wall_start,
        wall_end_ms=wall_end,
        temp_c=105.0,
        setpoint_c=107.22222222222223,
        ambient_c=21.11111111111111,
        observation_sequence=1,
        probe_valid=True,
        probe_source="grill",
        ambient_source=AmbientSource.CONFIGURED,
        ambient_uncertainty=AmbientUncertainty.ESTIMATED,
        baseline_combustion_load=0.5,
        calibration_probe_load=0.0,
        requested_combustion_load=0.5,
        allocated_combustion_load=0.5,
        realized_combustion_load=0.5,
        requested_auger_duty=0.5,
        scheduled_on_seconds=pulse.payload.scheduled_on_seconds,
        delivered_on_seconds=pulse.payload.delivered_on_seconds,
        realized_auger_duty=applied.payload.realized_auger_duty,
        allocator_revision=allocation.payload.allocator_revision,
        allocation_clamp_reasons=(),
        calibration_stage=None,
        calibration_fit=False,
        result_revision=1,
        eligible=True,
        rejection_reasons=(),
        input_variance=0.0,
        input_levels=1,
        effective_updates=1,
        role_generation=3,
        model_digest=_DIGEST,
        requested_fan_duty=0.5,
        actual_fan_duty=0.5,
        output_source=OutputSource.CONTROLLER,
        lid_open=False,
        safety_inhibited=False,
        manual_override=False,
        stale=False,
        skipped=False,
        reset=False,
        continuous=True,
    )
    payloads = (
        (
            session.event_kind,
            replace(
                session.payload,
                controller=ControllerType.MPC,
                model_revision=7,
                model_provenance="learned",
                fan_authority=True,
                fan_pwm_capable=True,
                fan_max_duty=1.0,
            ),
        ),
        (
            TraceEventKind.MODEL_EVENT,
            ModelEventPayload(
                event=ModelEventType.RESTORE,
                model_revision=7,
                provenance="learned",
                detail="restored",
                model_kind="grey-box",
                model_schema="grey-v4",
                role_generation=3,
                snapshot_digest=_DIGEST,
            ),
        ),
        (pid.event_kind, update),
        (allocation.event_kind, allocation_payload),
        (pulse.event_kind, replace(pulse_payload, wall_start_ms=wall_start, wall_end_ms=wall_end)),
        (applied.event_kind, applied_payload),
        (TraceEventKind.MODEL_OBSERVATION, observation),
    )
    # Publication is delayed and may move backwards; tuple order is durable order.
    publication = (_WALL, _WALL + 100, _WALL + 2_100, _WALL + 2_200, wall_end + 500, wall_end + 600, wall_end + 900)
    return tuple(
        ControlTraceRecord(
            ts_ms=stamp,
            session_id="clock-session",
            cook_id="clock-cook",
            controller=ControllerType.MPC,
            event_kind=kind,
            payload=payload,
        )
        for (kind, payload), stamp in zip(payloads, publication, strict=True)
    )


@pytest.mark.parametrize("rollback_ms", [0, 3_600_000, -3_600_000])
def test_epoch_publication_and_wall_jump_preserve_exact_import(rollback_ms):
    records = tuple(ControlTraceRecord.from_db_row(record.to_db_row()) for record in _records(rollback_ms=rollback_ms))
    assert validate_records(records).valid
    replayed = learning_observations(records)[0]
    assert (replayed.frame_start_s, replayed.frame_end_s) == (2.0, 22.0)
    assert (replayed.wall_start_ms, replayed.wall_end_ms) == (_WALL + 2_000, _WALL + 22_000 - rollback_ms)
    segment = _exact_import_segment(records, cook_id="clock-cook", diagnostics_schema_version=2)
    frame = segment.scored_hold_frames[0]
    assert (frame.monotonic_start_ms, frame.monotonic_end_ms) == (2_000, 22_000)
    assert (frame.wall_start_ms, frame.wall_end_ms) == (replayed.wall_start_ms, replayed.wall_end_ms)
    assert frame.temperature_sample_monotonic_ms == 22_000
    assert frame.temperature_sample_age_ms == 0
    assert segment.source_schema_version == 10


@pytest.mark.parametrize(
    "corruption", ["duplicate-allocation", "late-allocation", "late-model", "pulse-wall", "future-update"]
)
def test_wall_independence_does_not_relax_immutable_import_joins(corruption):
    records = list(_records(rollback_ms=3_600_000))
    if corruption == "duplicate-allocation":
        records.insert(4, records[3])
    elif corruption == "late-allocation":
        records.append(records.pop(3))
    elif corruption == "late-model":
        records.append(records.pop(1))
    elif corruption == "pulse-wall":
        assert isinstance(records[4].payload, FramedPulseFramePayload)
        records[4] = records[4].model_copy(update={"payload": replace(records[4].payload, wall_end_ms=_WALL)})
    else:
        assert isinstance(records[2].payload, MpcUpdatePayload)
        records[2] = records[2].model_copy(update={"payload": replace(records[2].payload, monotonic_ms=22_001)})
    with pytest.raises(_NonReplayableCookLearning):
        _exact_import_segment(tuple(records), cook_id="clock-cook", diagnostics_schema_version=2)


def test_historical_trace_is_readable_but_cannot_supply_current_clock_precision():
    records = _records()
    historical = []
    for record in records:
        raw = record.model_dump(mode="json")
        raw["schema_version"] = 9
        raw["payload"].pop("wall_start_ms", None)
        raw["payload"].pop("wall_end_ms", None)
        historical.append(ControlTraceRecord.model_validate_json(json.dumps(raw)))
    assert validate_records(historical).valid
    with pytest.raises(TraceSelectionError):
        learning_observations(historical, required_schema_version=9)
    raw = records[-1].model_dump(mode="json")
    raw["payload"].pop("wall_start_ms")
    with pytest.raises(ValidationError):
        ControlTraceRecord.model_validate_json(json.dumps(raw))


def test_export_import_preserves_durable_order_across_wall_rollback(ds, tmp_path, monkeypatch):
    records = _records(rollback_ms=3_600_000)
    append_control_trace(records)
    settings = cookfile_module.read_settings()
    probes = settings["probe_settings"]["probe_map"]["probe_info"]
    primary = next(probe["label"] for probe in probes if probe["type"] == "Primary")
    food = {probe["label"]: 160.0 for probe in probes if probe["type"] == "Food"}
    write_history(
        {
            "probe_history": {"primary": {primary: 221.0}, "food": food, "aux": {}},
            "primary_setpoint": 225.0,
            "notify_targets": {primary: 225.0, **{label: 165.0 for label in food}},
        }
    )
    append_metric(dict(default_metrics(), mode="Hold", augerontime=10))
    history = tmp_path / "history"
    monkeypatch.setattr(cookfile_module, "HISTORY_FOLDER", f"{history}/")
    cookfile_module.create_cookfile(cook_id="clock-cook", learning_report_provider=lambda _controller: None)
    (archive,) = history.glob("*.pifire")
    exported, status = cookfile_module.read_cookfile(archive)
    assert status == "OK"
    stored_records = exported["learning_diagnostics"]["control_trace"]["records"]
    assert [record["ts_ms"] for record in stored_records] == [record.ts_ms for record in records]
    repository = LearningTrajectoryRepository(str(tmp_path / "imported.db"))
    first = cookfile_module.import_cookfile_learning_trajectory(archive, repository=repository)
    assert first.outcome == "imported"
    segment = repository.read_segment(first.segment_ids[0])
    assert segment is not None
    assert segment.scored_hold_frames[0].wall_end_ms < segment.scored_hold_frames[0].wall_start_ms
    second = cookfile_module.import_cookfile_learning_trajectory(archive, repository=repository)
    assert second.outcome == "idempotent"
    assert second.segment_ids == first.segment_ids


@pytest.mark.parametrize("boundary_ms,valid", [(4_000, True), (4_001, False)])
def test_replacement_safety_joins_physical_boundary_not_publication(boundary_ms, valid):
    session, update, allocation, applied = current_pid_sp_records()
    assert isinstance(applied.payload, AppliedOutputPayload)
    partial = applied.model_copy(
        update={
            "ts_ms": _WALL + 9_000,
            "payload": replace(applied.payload, sample_complete=False, realized_combustion_load=None),
        }
    )
    safety = ControlTraceRecord(
        ts_ms=_WALL - 3_600_000,
        session_id=session.session_id,
        cook_id=session.cook_id,
        controller=session.controller,
        event_kind=TraceEventKind.SAFETY_EVENT,
        payload=SafetyEventPayload(
            event=SafetyEventType.MANUAL_TAKEOVER,
            inhibit_reason=InhibitReason.MANUAL_OVERRIDE,
            result_revision=1,
            detail="manual takeover",
            monotonic_ms=boundary_ms,
        ),
    )
    later = current_pid_sp_records(revision=2)[1:]
    records = (session, update, allocation, partial, safety, *later)
    assert validate_records(records).valid is valid


def test_schema_nine_seconds_horizons_remain_historical_seconds():
    progress = ChallengerProgressTracePayload(
        challenger_id="candidate",
        challenger_revision=1,
        phase="evaluating",
        origin="passive-online",
        policy="causal-auto",
        incumbent_digest=_DIGEST,
        incumbent_generation=1,
        candidate_digest="b" * 64,
        candidate_generation=2,
        corpus_digest=_DIGEST,
        lineage_digest=_DIGEST,
        result_digest=_DIGEST,
        evaluation_epoch=0,
        evaluation_round=0,
        consecutive_wins=0,
        required_wins=2,
        resumed_from_previous_cook=False,
        reset_reason=None,
        completed_horizon_seconds=(),
        required_horizon_seconds=MPC_FORECAST_HORIZON_SECONDS,
    )
    record = ControlTraceRecord(
        schema_version=9,
        ts_ms=_WALL,
        session_id="historical",
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.CHALLENGER_PROGRESS,
        payload=progress,
    )
    restored = ControlTraceRecord.from_db_row(record.to_db_row())
    assert restored.schema_version == 9
    assert restored.payload == progress
    raw = record.model_dump(mode="json")
    raw["schema_version"] = 8
    with pytest.raises(ValidationError):
        ControlTraceRecord.model_validate_json(json.dumps(raw))
