"""Cook-scoped learning diagnostic collector contracts."""

from hashlib import sha256
from typing import Literal, TypedDict, cast

import pytest
from pydantic import JsonValue

from common.control_trace import (
    ActuationMode,
    ControllerBranch,
    ControllerType,
    ControlTraceRecord,
    InhibitReason,
    MpcFailureState,
    MpcUpdatePayload,
    OutputSource,
    PidSpUpdatePayload,
    ResultStaleState,
    SessionPayload,
    TraceEventKind,
    TraceSetting,
)
from common.cook_diagnostics import (
    ControllerLearningReport,
    CookTrajectoryBreakReasonCount,
    CookTrajectoryCorpusReport,
    CookTrajectorySegmentReference,
    collect_cook_learning_diagnostics,
)
from common.learning_trajectory import (
    FrameDeliveryCertainty,
    HoldEntrySample,
    LearningTrajectoryFrame,
    LearningTrajectorySegment,
    TrajectoryBreakReason,
)
from common.model_evidence import ConfidenceDecisionEvidence, EvidenceKind, ModelEvidenceRecord

type _TraceSchemaVersion = Literal[2, 3, 4, 5, 6, 7, 8]
type _EvidenceSchemaVersion = Literal[1, 2, 3, 4]


class _CommonUpdate(TypedDict):
    monotonic_ms: int
    wall_ms: int
    result_revision: int
    result_age_ms: int
    control_period_seconds: float
    observed_dt_seconds: float
    setpoint: float
    measured_temperature: float
    raw_output: float
    requested_output: float
    actuation_mode: ActuationMode
    prior_requested_auger_duty: float
    prior_realized_auger_duty: float
    requested_fan_duty: float | None
    applied_fan_duty: float | None
    output_source: OutputSource
    inhibit_reason: InhibitReason


_CAPTURED_AT_MS = 1_787_490_000_000


def _session(
    controller: ControllerType,
    *,
    schema_version: _TraceSchemaVersion,
    session_id: str,
) -> ControlTraceRecord:
    return ControlTraceRecord(
        ts_ms=100,
        session_id=session_id,
        cook_id="cook-7",
        controller=controller,
        event_kind=TraceEventKind.SESSION,
        schema_version=schema_version,  # pyright: ignore[reportArgumentType]
        payload=SessionPayload(
            controller=controller,
            controller_config=(TraceSetting(key="policy", value=controller.value),),
            temperature_unit="F",
            control_period_seconds=2.0,
            model_revision=None,
            model_provenance=None,
            pulse_slot_seconds=2.0,
            pulse_frame_seconds=20.0,
            fan_authority=controller is ControllerType.MPC,
            fan_pwm_capable=True,
            fan_min_duty=0.0,
            fan_max_duty=1.0,
            setpoint=225.0,
            ambient_temperature=70.0,
            software_version="1.2.3",
            build_version="test",
        ),
    )


def _common_update() -> _CommonUpdate:
    return {
        "monotonic_ms": 200,
        "wall_ms": 300,
        "result_revision": 1,
        "result_age_ms": 0,
        "control_period_seconds": 2.0,
        "observed_dt_seconds": 2.0,
        "setpoint": 225.0,
        "measured_temperature": 220.0,
        "raw_output": 0.45,
        "requested_output": 0.45,
        "actuation_mode": ActuationMode.FRAMED_PULSE,
        "prior_requested_auger_duty": 0.4,
        "prior_realized_auger_duty": 0.35,
        "requested_fan_duty": None,
        "applied_fan_duty": None,
        "output_source": OutputSource.CONTROLLER,
        "inhibit_reason": InhibitReason.NONE,
    }


def _pid_sp_update(*, schema_version: _TraceSchemaVersion, session_id: str) -> ControlTraceRecord:
    return ControlTraceRecord(
        ts_ms=200,
        session_id=session_id,
        cook_id="cook-7",
        controller=ControllerType.PID_SP,
        event_kind=TraceEventKind.CONTROL_UPDATE,
        schema_version=schema_version,  # pyright: ignore[reportArgumentType]
        payload=PidSpUpdatePayload(
            **_common_update(),
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
            previous_update_ms=198,
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


def _mpc_update(*, schema_version: _TraceSchemaVersion, session_id: str) -> ControlTraceRecord:
    return ControlTraceRecord(
        ts_ms=200,
        session_id=session_id,
        cook_id="cook-7",
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.CONTROL_UPDATE,
        schema_version=schema_version,  # pyright: ignore[reportArgumentType]
        payload=MpcUpdatePayload(
            **_common_update(),
            state_names=("temperature",),
            state_values=(220.0,),
            disturbance_estimate=0.0,
            model_revision=1,
            model_provenance="learned",
            raw_policy_firing_load=0.45,
            equilibrium_feed_forward=0.4,
            residual_move=0.05,
            bounded_firing_load=0.45,
            policy_kind="linear-mpc",
            failure_state=MpcFailureState.SUCCESS,
            solve_start_ms=190,
            solve_end_ms=195,
            deadline_miss_count=0,
            stale=False,
            recovered=False,
            predicted_feasible=True,
            predicted_steady_load=0.4,
            solve_duration_ms=5,
            consecutive_deadline_miss_count=0,
            stale_state=ResultStaleState.FRESH,
        ),
    )


def _evidence(
    evidence_id: str,
    schema_version: _EvidenceSchemaVersion,
    *,
    cook_id: str = "cook-7",
) -> ModelEvidenceRecord:
    return ModelEvidenceRecord(
        evidence_id=evidence_id,
        kind=EvidenceKind.CONFIDENCE_DECISION,
        session_id="session-mpc",
        cook_id=cook_id,
        timestamp_ms=400,
        role_generation=1,
        model_digest=None,
        provenance_digest=None,
        schema_version=schema_version,
        payload=ConfidenceDecisionEvidence(decision_id=f"decision-{evidence_id}", blocked=False),
    )


def _provider(controller: str) -> ControllerLearningReport:
    return ControllerLearningReport(
        controller=controller,
        schema_version=1,
        revision=f"{controller}-revision",
        report={"controller": controller},
    )


def _digest(label: str) -> str:
    return sha256(label.encode()).hexdigest()


def _trajectory_frame(sequence: int, *, effective_mode: str) -> LearningTrajectoryFrame:
    start_ms = sequence * 20_000
    end_ms = start_ms + 20_000
    return LearningTrajectoryFrame(
        sequence=sequence,
        monotonic_start_ms=start_ms,
        monotonic_end_ms=end_ms,
        wall_start_ms=1_000_000 + start_ms,
        wall_end_ms=1_000_000 + end_ms,
        chamber_temperature_c=110.0 + sequence,
        temperature_sample_monotonic_ms=end_ms,
        temperature_sample_wall_ms=1_000_000 + end_ms,
        temperature_sample_age_ms=0,
        temperature_sample_wall_age_ms=0,
        temperature_sample_clock_skew_ms=0,
        source_temperature_units="C",
        settings_revision=7,
        probe_valid=True,
        probe_source="grill-probe-1",
        ambient_temperature_c=24.0,
        ambient_source="configured",
        ambient_uncertainty_c=1.5,
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
        role_generation=4,
    )


def _trajectory_segment(
    segment_id: str,
    *,
    cook_id: str = "cook-7",
    schema_version: int = 1,
    sequence: int = 0,
    state: Literal["open", "finalized", "quarantined"] = "finalized",
    terminal_break_reason: TrajectoryBreakReason | None = TrajectoryBreakReason.STOP,
) -> LearningTrajectorySegment:
    pre_roll = _trajectory_frame(sequence, effective_mode="Smoke")
    scored = _trajectory_frame(sequence + 1, effective_mode="Hold")
    return LearningTrajectorySegment(
        schema_version=schema_version,
        observation_schema_version=3,
        segment_id=segment_id,
        cook_id=cook_id,
        trajectory_session_id=f"trajectory-{segment_id}",
        trace_session_ids=(f"trace-{segment_id}",),
        collection_provenance={"origin": "passive-online"},
        configuration_provenance={"controller": "MPC", "revision": 7},
        cadence_digest=_digest(f"cadence-{segment_id}"),
        model_structure_digest=_digest(f"structure-{segment_id}"),
        held_physics_digest=_digest(f"physics-{segment_id}"),
        delay_input_mapping_digest=_digest(f"delay-{segment_id}"),
        actuation_mapping_digest=_digest(f"actuation-{segment_id}"),
        scored_fan_regime_digest=_digest(f"fan-{segment_id}"),
        ambient_semantics_digest=_digest(f"ambient-{segment_id}"),
        pre_roll_frames=(pre_roll,),
        hold_entry=HoldEntrySample(
            monotonic_ms=scored.monotonic_start_ms,
            wall_ms=scored.wall_start_ms,
            chamber_temperature_c=scored.chamber_temperature_c,
            probe_valid=True,
            probe_source="grill-probe-1",
        ),
        scored_hold_frames=(scored,),
        generation_audit_ranges=(
            {
                "start_sequence": pre_roll.sequence,
                "end_sequence": scored.sequence,
                "role_generation": 4,
            },
        ),
        start_monotonic_ms=pre_roll.monotonic_start_ms,
        end_monotonic_ms=scored.monotonic_end_ms,
        start_wall_ms=pre_roll.wall_start_ms,
        end_wall_ms=scored.wall_end_ms,
        start_sequence=pre_roll.sequence,
        end_sequence=scored.sequence,
        pre_roll_end_reason=None,
        terminal_break_reason=terminal_break_reason,
        state=state,
        source_trace_digest=_digest(f"trace-source-{segment_id}"),
        source_schema_version=7,
        source_row_digest=_digest(f"rows-{segment_id}"),
        build_provenance={"builder": "trajectory-runtime", "revision": 1},
    )


def _trajectory_reference(segment: LearningTrajectorySegment) -> CookTrajectorySegmentReference:
    return CookTrajectorySegmentReference(
        cook_id=segment.cook_id,
        segment_schema_version=segment.schema_version,
        observation_schema_version=segment.observation_schema_version,
        segment_id=segment.segment_id,
        trajectory_session_id=segment.trajectory_session_id,
        trace_session_ids=segment.trace_session_ids,
        state=segment.state,
        source_trace_digest=segment.source_trace_digest,
        content_digest=segment.content_digest,
        fit_partition_digest=segment.fit_partition_digest,
        source_row_digest=segment.source_row_digest,
        source_schema_version=segment.source_schema_version,
        pre_roll_frame_count=len(segment.pre_roll_frames),
        scored_hold_frame_count=len(segment.scored_hold_frames),
        terminal_break_reason=segment.terminal_break_reason,
    )


def _corpus_report(
    *,
    break_reason_counts: tuple[CookTrajectoryBreakReasonCount, ...] | None = None,
) -> CookTrajectoryCorpusReport:
    return CookTrajectoryCorpusReport(
        schema_version=1,
        corpus_revision=19,
        segment_count=4,
        pre_roll_count=5,
        pre_roll_capacity=8_640,
        scored_count=6,
        scored_capacity=8_640,
        evicted_segment_count=2,
        evicted_pre_roll_count=3,
        evicted_scored_count=4,
        open_segment_count=1,
        finalized_segment_count=2,
        quarantined_segment_count=1,
        distinct_cook_count=3,
        distinct_session_count=4,
        earliest_wall_ms=1_000_000,
        latest_wall_ms=2_000_000,
        break_reason_counts=break_reason_counts
        if break_reason_counts is not None
        else (
            CookTrajectoryBreakReasonCount(reason=TrajectoryBreakReason.LEFT_HOLD, count=1),
            CookTrajectoryBreakReasonCount(reason=TrajectoryBreakReason.STOP, count=2),
        ),
        last_persistence_error=None,
        last_recovery_error="recovered segment after unclean restart",
    )


def _empty_trajectory_segments(cook_id: str) -> tuple[LearningTrajectorySegment, ...]:
    return ()


def test_collects_complete_mixed_controller_cook_in_order() -> None:
    warnings: list[str] = []
    pid_session = _session(ControllerType.PID_SP, schema_version=5, session_id="session-pid-sp")
    pid_update = _pid_sp_update(schema_version=5, session_id="session-pid-sp")
    mpc_session = _session(ControllerType.MPC, schema_version=7, session_id="session-mpc")
    mpc_update = _mpc_update(schema_version=7, session_id="session-mpc")
    evidence_v2 = _evidence("evidence-v2", 2)
    evidence_v4 = _evidence("evidence-v4", 4)
    segment_v2_a = _trajectory_segment("segment-v2-a", schema_version=2, sequence=20)
    segment_v1 = _trajectory_segment("segment-v1", schema_version=1, sequence=30)
    segment_v2_b = _trajectory_segment("segment-v2-b", schema_version=2, sequence=40)
    corpus = _corpus_report()

    bundle = collect_cook_learning_diagnostics(
        "cook-7",
        _provider,
        read_trace=lambda cook_id: [pid_session, pid_update, mpc_session, mpc_update],
        read_evidence=lambda *, cook_id: [evidence_v2, evidence_v4],
        read_trajectory_segments=lambda cook_id: [segment_v2_a, segment_v1, segment_v2_b],
        read_corpus_report=lambda: corpus,
        clock_ms=lambda: _CAPTURED_AT_MS,
        warn=warnings.append,
    )

    assert bundle.cook_id == "cook-7"
    assert bundle.captured_at_ms == _CAPTURED_AT_MS
    assert bundle.controllers == ("pid_sp", "mpc")
    assert [item.controller for item in bundle.reports] == ["pid_sp", "mpc"]
    assert [item.schema_version for item in bundle.control_trace.records] == [5, 5, 7, 7]
    assert bundle.control_trace.record_schema_versions == (5, 7)
    assert bundle.model_evidence.record_schema_versions == (2, 4)
    assert bundle.trajectory_segments == (
        _trajectory_reference(segment_v2_a),
        _trajectory_reference(segment_v1),
        _trajectory_reference(segment_v2_b),
    )
    assert bundle.trajectory_schema_versions == (1, 2)
    assert bundle.corpus == corpus
    assert bundle.capture_errors == ()
    assert warnings == []
    dumped = bundle.model_dump(mode="json")
    assert tuple(dumped) == (
        "schema_version",
        "cook_id",
        "captured_at_ms",
        "controllers",
        "reports",
        "control_trace",
        "model_evidence",
        "trajectory_segments",
        "trajectory_schema_versions",
        "corpus",
        "capture_errors",
    )
    assert dumped["schema_version"] == 2
    assert dumped["controllers"] == ["pid_sp", "mpc"]
    assert dumped["control_trace"]["record_schema_versions"] == [5, 7]
    assert dumped["model_evidence"]["record_schema_versions"] == [2, 4]
    assert dumped["trajectory_schema_versions"] == [1, 2]
    assert tuple(dumped["trajectory_segments"][0]) == (
        "cook_id",
        "segment_schema_version",
        "observation_schema_version",
        "segment_id",
        "trajectory_session_id",
        "trace_session_ids",
        "state",
        "source_trace_digest",
        "content_digest",
        "fit_partition_digest",
        "source_row_digest",
        "source_schema_version",
        "pre_roll_frame_count",
        "scored_hold_frame_count",
        "terminal_break_reason",
    )
    assert dumped["trajectory_segments"][0] == {
        "cook_id": "cook-7",
        "segment_schema_version": 2,
        "observation_schema_version": 3,
        "segment_id": "segment-v2-a",
        "trajectory_session_id": "trajectory-segment-v2-a",
        "trace_session_ids": ["trace-segment-v2-a"],
        "state": "finalized",
        "source_trace_digest": segment_v2_a.source_trace_digest,
        "content_digest": segment_v2_a.content_digest,
        "fit_partition_digest": segment_v2_a.fit_partition_digest,
        "source_row_digest": segment_v2_a.source_row_digest,
        "source_schema_version": 7,
        "pre_roll_frame_count": 1,
        "scored_hold_frame_count": 1,
        "terminal_break_reason": "stop",
    }
    assert "pre_roll_frames" not in dumped["trajectory_segments"][0]
    assert "scored_hold_frames" not in dumped["trajectory_segments"][0]
    assert dumped["corpus"] == {
        "schema_version": 1,
        "corpus_revision": 19,
        "segment_count": 4,
        "pre_roll_count": 5,
        "pre_roll_capacity": 8_640,
        "scored_count": 6,
        "scored_capacity": 8_640,
        "evicted_segment_count": 2,
        "evicted_pre_roll_count": 3,
        "evicted_scored_count": 4,
        "open_segment_count": 1,
        "finalized_segment_count": 2,
        "quarantined_segment_count": 1,
        "distinct_cook_count": 3,
        "distinct_session_count": 4,
        "earliest_wall_ms": 1_000_000,
        "latest_wall_ms": 2_000_000,
        "break_reason_counts": [
            {"reason": "left-hold", "count": 1},
            {"reason": "stop", "count": 2},
        ],
        "last_persistence_error": None,
        "last_recovery_error": "recovered segment after unclean restart",
    }
    assert dumped["capture_errors"] == []


def test_corpus_report_normalizes_typed_break_reason_counts() -> None:
    report = _corpus_report(
        break_reason_counts=(
            CookTrajectoryBreakReasonCount(reason=TrajectoryBreakReason.STOP, count=2),
            CookTrajectoryBreakReasonCount(reason=TrajectoryBreakReason.LEFT_HOLD, count=1),
        )
    )

    assert [(item.reason, item.count) for item in report.break_reason_counts] == [
        (TrajectoryBreakReason.LEFT_HOLD, 1),
        (TrajectoryBreakReason.STOP, 2),
    ]
    assert report.model_dump(mode="json")["break_reason_counts"] == [
        {"reason": "left-hold", "count": 1},
        {"reason": "stop", "count": 2},
    ]

    payload = report.model_dump()
    payload["unowned"] = True
    with pytest.raises(ValueError):
        CookTrajectoryCorpusReport.model_validate(payload)


def test_trace_read_failure_keeps_evidence_and_names_error() -> None:
    warnings: list[str] = []
    evidence_v3 = _evidence("evidence-v3", 3)
    segment = _trajectory_segment("segment-trace-failure")
    corpus = _corpus_report()

    def failed_trace(cook_id: str) -> list[ControlTraceRecord]:
        raise RuntimeError("trace unavailable")

    bundle = collect_cook_learning_diagnostics(
        "cook-7",
        _provider,
        read_trace=failed_trace,
        read_evidence=lambda *, cook_id: [evidence_v3],
        read_trajectory_segments=lambda cook_id: [segment],
        read_corpus_report=lambda: corpus,
        clock_ms=lambda: _CAPTURED_AT_MS,
        warn=warnings.append,
    )

    assert bundle.control_trace.records == ()
    assert [record.evidence_id for record in bundle.model_evidence.records] == ["evidence-v3"]
    assert bundle.trajectory_segments == (_trajectory_reference(segment),)
    assert bundle.corpus == corpus
    assert [(error.source, error.code) for error in bundle.capture_errors] == [
        ("control_trace", "control-trace-read-failed")
    ]
    assert warnings == ["control_trace: trace unavailable"]


def test_evidence_read_failure_keeps_trace_and_reports() -> None:
    warnings: list[str] = []
    session = _session(ControllerType.MPC, schema_version=6, session_id="session-mpc")

    def failed_evidence(*, cook_id: str) -> list[ModelEvidenceRecord]:
        raise RuntimeError("evidence unavailable")

    bundle = collect_cook_learning_diagnostics(
        "cook-7",
        _provider,
        read_trace=lambda cook_id: [session],
        read_evidence=failed_evidence,
        read_trajectory_segments=_empty_trajectory_segments,
        read_corpus_report=_corpus_report,
        clock_ms=lambda: _CAPTURED_AT_MS,
        warn=warnings.append,
    )

    assert bundle.control_trace.records == (session,)
    assert bundle.model_evidence.records == ()
    assert [report.controller for report in bundle.reports] == ["mpc"]
    assert [(error.source, error.code) for error in bundle.capture_errors] == [
        ("model_evidence", "model-evidence-read-failed")
    ]
    assert warnings == ["model_evidence: evidence unavailable"]


def test_trajectory_read_failure_keeps_trace_evidence_reports_and_corpus() -> None:
    warnings: list[str] = []
    session = _session(ControllerType.MPC, schema_version=7, session_id="session-mpc")
    evidence = _evidence("evidence-v4", 4)
    corpus = _corpus_report()

    def failed_trajectory(cook_id: str) -> list[LearningTrajectorySegment]:
        raise RuntimeError("trajectory unavailable")

    bundle = collect_cook_learning_diagnostics(
        "cook-7",
        _provider,
        read_trace=lambda cook_id: [session],
        read_evidence=lambda *, cook_id: [evidence],
        read_trajectory_segments=failed_trajectory,
        read_corpus_report=lambda: corpus,
        clock_ms=lambda: _CAPTURED_AT_MS,
        warn=warnings.append,
    )

    assert bundle.control_trace.records == (session,)
    assert bundle.model_evidence.records == (evidence,)
    assert [report.controller for report in bundle.reports] == ["mpc"]
    assert bundle.trajectory_segments == ()
    assert bundle.trajectory_schema_versions == ()
    assert bundle.corpus == corpus
    assert [(error.source, error.code) for error in bundle.capture_errors] == [
        ("learning_trajectory", "learning-trajectory-read-failed")
    ]
    assert warnings == ["learning_trajectory: trajectory unavailable"]


def test_corpus_read_failure_keeps_every_cook_scoped_source() -> None:
    warnings: list[str] = []
    session = _session(ControllerType.MPC, schema_version=7, session_id="session-mpc")
    evidence = _evidence("evidence-v4", 4)
    segment = _trajectory_segment("segment-corpus-failure", schema_version=2)

    def failed_corpus() -> CookTrajectoryCorpusReport:
        raise RuntimeError("corpus unavailable")

    bundle = collect_cook_learning_diagnostics(
        "cook-7",
        _provider,
        read_trace=lambda cook_id: [session],
        read_evidence=lambda *, cook_id: [evidence],
        read_trajectory_segments=lambda cook_id: [segment],
        read_corpus_report=failed_corpus,
        clock_ms=lambda: _CAPTURED_AT_MS,
        warn=warnings.append,
    )

    assert bundle.control_trace.records == (session,)
    assert bundle.model_evidence.records == (evidence,)
    assert [report.controller for report in bundle.reports] == ["mpc"]
    assert bundle.trajectory_segments == (_trajectory_reference(segment),)
    assert bundle.trajectory_schema_versions == (2,)
    assert bundle.corpus is None
    assert [(error.source, error.code) for error in bundle.capture_errors] == [
        ("trajectory_corpus", "trajectory-corpus-read-failed")
    ]
    assert warnings == ["trajectory_corpus: corpus unavailable"]


def test_report_failure_isolated_from_other_controller_report() -> None:
    warnings: list[str] = []
    pid_session = _session(ControllerType.PID_SP, schema_version=5, session_id="session-pid-sp")
    mpc_session = _session(ControllerType.MPC, schema_version=6, session_id="session-mpc")

    def provider(controller: str) -> ControllerLearningReport:
        if controller == "mpc":
            raise RuntimeError("report unavailable")
        return _provider(controller)

    bundle = collect_cook_learning_diagnostics(
        "cook-7",
        provider,
        read_trace=lambda cook_id: [pid_session, mpc_session],
        read_evidence=lambda *, cook_id: [],
        read_trajectory_segments=_empty_trajectory_segments,
        read_corpus_report=_corpus_report,
        clock_ms=lambda: _CAPTURED_AT_MS,
        warn=warnings.append,
    )

    assert [report.controller for report in bundle.reports] == ["pid_sp"]
    assert [(error.source, error.code) for error in bundle.capture_errors] == [("report:mpc", "report-read-failed")]
    assert warnings == ["report:mpc: report unavailable"]


def test_null_cook_identity_avoids_all_source_calls() -> None:
    calls: list[str] = []

    def trace(cook_id: str) -> list[ControlTraceRecord]:
        calls.append("trace")
        return []

    def evidence(*, cook_id: str) -> list[ModelEvidenceRecord]:
        calls.append("evidence")
        return []

    def trajectory(cook_id: str) -> list[LearningTrajectorySegment]:
        calls.append("trajectory")
        return []

    def corpus() -> CookTrajectoryCorpusReport:
        calls.append("corpus")
        return _corpus_report()

    def provider(controller: str) -> None:
        calls.append("provider")

    bundle = collect_cook_learning_diagnostics(
        None,
        provider,
        read_trace=trace,
        read_evidence=evidence,
        read_trajectory_segments=trajectory,
        read_corpus_report=corpus,
        clock_ms=lambda: _CAPTURED_AT_MS,
        warn=lambda message: None,
    )

    assert calls == []
    assert bundle.cook_id is None
    assert bundle.controllers == ()
    assert bundle.control_trace.records == ()
    assert bundle.model_evidence.records == ()
    assert bundle.trajectory_segments == ()
    assert bundle.trajectory_schema_versions == ()
    assert bundle.corpus is None
    assert [(error.source, error.code) for error in bundle.capture_errors] == [("collector", "cook-identity-invalid")]


def test_missing_session_context_retains_trace_without_requesting_reports() -> None:
    warnings: list[str] = []
    calls: list[str] = []
    update = _mpc_update(schema_version=6, session_id="session-mpc")

    def provider(controller: str) -> ControllerLearningReport:
        calls.append(controller)
        return _provider(controller)

    bundle = collect_cook_learning_diagnostics(
        "cook-7",
        provider,
        read_trace=lambda cook_id: [update],
        read_evidence=lambda *, cook_id: [],
        read_trajectory_segments=_empty_trajectory_segments,
        read_corpus_report=_corpus_report,
        clock_ms=lambda: _CAPTURED_AT_MS,
        warn=warnings.append,
    )

    assert bundle.control_trace.records == (update,)
    assert bundle.controllers == ()
    assert bundle.reports == ()
    assert calls == []
    assert [(error.source, error.code) for error in bundle.capture_errors] == [
        ("control_trace", "trace-session-missing")
    ]
    assert warnings == ["control_trace: no session records found for requested cook"]


def test_cross_cook_records_are_excluded_without_dropping_compatible_records() -> None:
    warnings: list[str] = []
    session = _session(ControllerType.MPC, schema_version=6, session_id="session-mpc")
    foreign_session = session.model_copy(update={"cook_id": "cook-other"})
    evidence = _evidence("evidence-v3", 3)
    foreign_evidence = _evidence("foreign-evidence", 3, cook_id="cook-other")
    segment = _trajectory_segment("segment-current", schema_version=2)
    foreign_segment = _trajectory_segment(
        "segment-foreign",
        cook_id="cook-other",
        schema_version=3,
        sequence=10,
    )

    bundle = collect_cook_learning_diagnostics(
        "cook-7",
        _provider,
        read_trace=lambda cook_id: [foreign_session, session],
        read_evidence=lambda *, cook_id: [foreign_evidence, evidence],
        read_trajectory_segments=lambda cook_id: [foreign_segment, segment],
        read_corpus_report=_corpus_report,
        clock_ms=lambda: _CAPTURED_AT_MS,
        warn=warnings.append,
    )

    assert bundle.control_trace.records == (session,)
    assert bundle.model_evidence.records == (evidence,)
    assert bundle.trajectory_segments == (_trajectory_reference(segment),)
    assert bundle.trajectory_schema_versions == (2,)
    assert bundle.controllers == ("mpc",)
    assert [(error.source, error.code) for error in bundle.capture_errors] == [
        ("control_trace", "control-trace-cook-mismatch"),
        ("model_evidence", "model-evidence-cook-mismatch"),
        ("learning_trajectory", "learning-trajectory-cook-mismatch"),
    ]
    assert warnings == [
        "control_trace: record cook_id does not match requested cook_id",
        "model_evidence: record cook_id does not match requested cook_id",
        "learning_trajectory: segment cook_id does not match requested cook_id",
    ]


def test_duplicate_session_controllers_request_one_ordered_report() -> None:
    calls: list[str] = []
    first = _session(ControllerType.MPC, schema_version=5, session_id="session-mpc-a")
    duplicate = _session(ControllerType.MPC, schema_version=6, session_id="session-mpc-b")

    def provider(controller: str) -> ControllerLearningReport:
        calls.append(controller)
        return _provider(controller)

    bundle = collect_cook_learning_diagnostics(
        "cook-7",
        provider,
        read_trace=lambda cook_id: [first, duplicate],
        read_evidence=lambda *, cook_id: [],
        read_trajectory_segments=_empty_trajectory_segments,
        read_corpus_report=_corpus_report,
        clock_ms=lambda: _CAPTURED_AT_MS,
        warn=lambda message: None,
    )

    assert bundle.controllers == ("mpc",)
    assert [report.controller for report in bundle.reports] == ["mpc"]
    assert calls == ["mpc"]


def test_failing_warning_sink_cannot_replace_source_error() -> None:
    def failed_trace(cook_id: str) -> list[ControlTraceRecord]:
        raise RuntimeError("trace unavailable")

    def failed_warning(message: str) -> None:
        raise RuntimeError("warning unavailable")

    bundle = collect_cook_learning_diagnostics(
        "cook-7",
        _provider,
        read_trace=failed_trace,
        read_evidence=lambda *, cook_id: [],
        read_trajectory_segments=_empty_trajectory_segments,
        read_corpus_report=_corpus_report,
        clock_ms=lambda: _CAPTURED_AT_MS,
        warn=failed_warning,
    )

    assert [(error.source, error.code, error.detail) for error in bundle.capture_errors] == [
        ("control_trace", "control-trace-read-failed", "trace unavailable")
    ]


def test_top_level_failure_returns_minimal_valid_collector_fallback() -> None:
    warnings: list[str] = []

    def failed_clock() -> int:
        raise RuntimeError("clock unavailable")

    bundle = collect_cook_learning_diagnostics(
        "cook-7",
        _provider,
        read_trace=lambda cook_id: [],
        read_evidence=lambda *, cook_id: [],
        read_trajectory_segments=_empty_trajectory_segments,
        read_corpus_report=_corpus_report,
        clock_ms=failed_clock,
        warn=warnings.append,
    )

    assert bundle.cook_id == "cook-7"
    assert bundle.captured_at_ms >= 0
    assert bundle.controllers == ()
    assert bundle.reports == ()
    assert bundle.control_trace.records == ()
    assert bundle.model_evidence.records == ()
    assert bundle.trajectory_segments == ()
    assert bundle.trajectory_schema_versions == ()
    assert bundle.corpus is None
    assert [(error.source, error.code, error.detail) for error in bundle.capture_errors] == [
        ("collector", "collector-failed", "clock unavailable")
    ]
    assert warnings == ["collector: clock unavailable"]


def test_report_envelope_owns_and_serializes_nested_json() -> None:
    values: list[JsonValue] = [1.5]
    source: dict[str, JsonValue] = {"values": values}
    report = ControllerLearningReport(
        controller="mpc",
        schema_version=1,
        revision="revision-1",
        report=source,
    )
    values.append(2.5)
    session = _session(ControllerType.MPC, schema_version=6, session_id="session-mpc")

    bundle = collect_cook_learning_diagnostics(
        "cook-7",
        lambda controller: report,
        read_trace=lambda cook_id: [session],
        read_evidence=lambda *, cook_id: [],
        read_trajectory_segments=_empty_trajectory_segments,
        read_corpus_report=_corpus_report,
        clock_ms=lambda: _CAPTURED_AT_MS,
        warn=lambda message: None,
    )

    assert bundle.model_dump(mode="json")["reports"] == [
        {
            "controller": "mpc",
            "schema_version": 1,
            "revision": "revision-1",
            "report": {"values": [1.5]},
        }
    ]


@pytest.mark.parametrize(
    ("overrides", "error_type"),
    [
        ({"controller": ""}, ValueError),
        ({"schema_version": 0}, ValueError),
        ({"revision": ""}, ValueError),
        ({"report": []}, TypeError),
        ({"report": {"number": float("inf")}}, ValueError),
        ({"report": {1: "value"}}, TypeError),
        ({"report": {"unsupported": {1}}}, TypeError),
    ],
)
def test_report_envelope_rejects_invalid_contract_values(
    overrides: dict[str, object],
    error_type: type[Exception],
) -> None:
    fields = {
        "controller": "mpc",
        "schema_version": 1,
        "revision": "revision-1",
        "report": {"status": "ready"},
    }
    fields.update(overrides)

    with pytest.raises(error_type):
        ControllerLearningReport(**fields)


def test_none_report_is_supported_without_fabricating_a_report() -> None:
    session = _session(ControllerType.MPC, schema_version=6, session_id="session-mpc")

    bundle = collect_cook_learning_diagnostics(
        "cook-7",
        lambda controller: None,
        read_trace=lambda cook_id: [session],
        read_evidence=lambda *, cook_id: [],
        read_trajectory_segments=_empty_trajectory_segments,
        read_corpus_report=_corpus_report,
        clock_ms=lambda: _CAPTURED_AT_MS,
        warn=lambda message: None,
    )

    assert bundle.controllers == ("mpc",)
    assert bundle.reports == ()
    assert bundle.capture_errors == ()


def test_source_exception_without_a_usable_message_still_returns_valid_error_detail() -> None:
    class BrokenMessageError(RuntimeError):
        def __str__(self) -> str:
            raise RuntimeError("message unavailable")

    def failed_trace(cook_id: str) -> list[ControlTraceRecord]:
        raise BrokenMessageError

    bundle = collect_cook_learning_diagnostics(
        "cook-7",
        _provider,
        read_trace=failed_trace,
        read_evidence=lambda *, cook_id: [],
        read_trajectory_segments=_empty_trajectory_segments,
        read_corpus_report=_corpus_report,
        clock_ms=lambda: _CAPTURED_AT_MS,
        warn=lambda message: None,
    )

    assert [(error.code, error.detail) for error in bundle.capture_errors] == [
        ("control-trace-read-failed", "BrokenMessageError")
    ]


def test_wrong_report_type_isolated_without_losing_sources_or_later_reports() -> None:
    warnings: list[str] = []
    pid_session = _session(ControllerType.PID_SP, schema_version=5, session_id="session-pid-sp")
    mpc_session = _session(ControllerType.MPC, schema_version=6, session_id="session-mpc")
    evidence = _evidence("evidence-v3", 3)

    def provider(controller: str) -> ControllerLearningReport:
        if controller == "pid_sp":
            return cast(ControllerLearningReport, object())
        return _provider(controller)

    bundle = collect_cook_learning_diagnostics(
        "cook-7",
        provider,
        read_trace=lambda cook_id: [pid_session, mpc_session],
        read_evidence=lambda *, cook_id: [evidence],
        read_trajectory_segments=_empty_trajectory_segments,
        read_corpus_report=_corpus_report,
        clock_ms=lambda: _CAPTURED_AT_MS,
        warn=warnings.append,
    )

    assert bundle.control_trace.records == (pid_session, mpc_session)
    assert bundle.model_evidence.records == (evidence,)
    assert bundle.controllers == ("pid_sp", "mpc")
    assert [report.controller for report in bundle.reports] == ["mpc"]
    assert [(error.source, error.code) for error in bundle.capture_errors] == [("report:pid_sp", "report-type-invalid")]
    assert warnings == ["report:pid_sp: provider returned unsupported report type: object"]


def test_mismatched_report_controller_isolated_without_losing_later_report() -> None:
    warnings: list[str] = []
    pid_session = _session(ControllerType.PID_SP, schema_version=5, session_id="session-pid-sp")
    mpc_session = _session(ControllerType.MPC, schema_version=6, session_id="session-mpc")

    def provider(controller: str) -> ControllerLearningReport:
        if controller == "pid_sp":
            return _provider("mpc")
        return _provider(controller)

    bundle = collect_cook_learning_diagnostics(
        "cook-7",
        provider,
        read_trace=lambda cook_id: [pid_session, mpc_session],
        read_evidence=lambda *, cook_id: [],
        read_trajectory_segments=_empty_trajectory_segments,
        read_corpus_report=_corpus_report,
        clock_ms=lambda: _CAPTURED_AT_MS,
        warn=warnings.append,
    )

    assert bundle.control_trace.records == (pid_session, mpc_session)
    assert bundle.controllers == ("pid_sp", "mpc")
    assert [report.controller for report in bundle.reports] == ["mpc"]
    assert [(error.source, error.code) for error in bundle.capture_errors] == [
        ("report:pid_sp", "report-controller-mismatch")
    ]
    assert warnings == ["report:pid_sp: provider returned report for 'mpc'"]
