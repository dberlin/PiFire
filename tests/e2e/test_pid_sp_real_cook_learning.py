"""Permanent production-boundary replay of the sanitized August 28 PID-SP cook."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from itertools import count
from uuid import NAMESPACE_URL, uuid5

from common.control_trace import (
    ControllerType,
    ModelObservationPayload,
    RecorderGapPayload,
    TraceEventKind,
)
from common.controller_model_state import ControllerModelStore
from common.learning_trajectory import TrajectoryBreakReason, canonical_trajectory_digest
from common.model_evidence import (
    EvidenceKind,
    ModelEvidenceRecord,
    PidSpFitDecisionEvidence,
    RecorderGapEvidence,
)
from common.persistence.control_trace import read_control_trace_cook
from common.persistence.learning_trajectory import LearningTrajectoryRepository
from common.persistence.model_evidence import read_model_evidence
from controller.model_learning.contracts import CandidateOrigin, FitRequest
from controller.model_learning.pid_sp_fitting import fit_pid_sp_corpus
from controller.pid_sp import Controller as PidSpController
from controller.pid_sp_delay_evidence import DelayBlocker
from controller.pid_sp_observation import canonical_pid_sp_observation_model_digest
from controller.runtime.actuation_delivery import (
    ActuationDeliveryJournal,
    DeliveredGrillPlatform,
)
from controller.runtime.control_trace_recorder import ControlTraceRecorder
from controller.runtime.control_trace_session import (
    ControlTraceSession,
    TraceSessionContext,
)
from controller.runtime.learning_trajectory import (
    LearningTrajectoryRuntime,
    ModeEntered,
    ModeExited,
    ThermalSample,
    TrajectoryBoundary,
)
from controller.runtime.model_persistence import ModelPersistenceWorker
from controller.runtime.modes.hold_learning import HoldLearningRuntime
from controller.runtime.runner import SyncControllerRunner
from tests.e2e.real_cook_replay import (
    PID_SP_AUGUST_28_SHA256,
    PidSpAugust28Replay,
    ReplayGap,
    load_pid_sp_august_28_replay,
)
from tests.fakes.grill import FakeGrillPlatform

_COOK_ID = "pid-sp-august-28-sanitized"
_TRAJECTORY_ID = "pid-sp-august-28-trajectory"
_TIME_OFFSET_S = 10_000.0
_TIME_OFFSET_MS = round(_TIME_OFFSET_S * 1_000)
_WALL_OFFSET_MS = 1_700_000_000_000
_GAP_REASON = "missing-synchronized-thermal-update"
_TRACE_SESSION_ID = str(uuid5(NAMESPACE_URL, f"pifire:pid-sp-real-cook:{PID_SP_AUGUST_28_SHA256}"))


class _Logger:
    def debug(self, message: str) -> None:
        pass

    def info(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        pass

    def error(self, message: str) -> None:
        pass


_LOGGER = _Logger()


@dataclass(slots=True)
class _ReplayResult:
    replay: PidSpAugust28Replay
    trace_records: tuple[object, ...]
    segments: tuple[object, ...]
    fit_result: object | None
    evidence: tuple[ModelEvidenceRecord, ...]
    cold_segments: tuple[object, ...]
    cold_evidence: tuple[ModelEvidenceRecord, ...]
    cold_checkpoint: dict[str, object] | None


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _shifted_ms(seconds: float) -> int:
    return _TIME_OFFSET_MS + round(seconds * 1_000)


def _wall_shifted_ms(seconds: float) -> int:
    return _WALL_OFFSET_MS + _shifted_ms(seconds)


def _mode_entered(replay: PidSpAugust28Replay) -> ModeEntered:
    config = dict(replay.controller_config)
    return ModeEntered(
        effective_mode="Hold",
        persisted_mode="Hold",
        monotonic_ms=_shifted_ms(replay.actuation_frames[0].start_s),
        wall_ms=_WALL_OFFSET_MS + _shifted_ms(replay.actuation_frames[0].start_s),
        cook_id=_COOK_ID,
        trajectory_session_id=_TRAJECTORY_ID,
        trace_session_id="",
        recipe_step_id=None,
        units="F",
        settings_revision=1,
        collection_provenance={"source_kind": "sanitized-real-cook-replay"},
        configuration_provenance=config,
        cadence_digest=_digest("august-28-cadence"),
        model_structure_digest=_digest("pid-sp-structure"),
        held_physics_digest=_digest("august-28-held-physics"),
        delay_input_mapping_digest=_digest("realized-auger-duty"),
        actuation_mapping_digest=_digest("exact-framed-pulse"),
        scored_fan_regime_digest=_digest("no-fan-authority"),
        ambient_semantics_digest=_digest("configured-32-f"),
        source_trace_digest=PID_SP_AUGUST_28_SHA256,
        source_schema_version=2,
        source_row_digest=PID_SP_AUGUST_28_SHA256,
        build_provenance={"kind": "sanitized-fixture"},
    )


def _trace_context(replay: PidSpAugust28Replay, config: dict[str, object]) -> TraceSessionContext:
    return TraceSessionContext(
        controller=ControllerType.PID_SP,
        controller_config=config,
        temperature_unit="F",
        control_period_seconds=20.0,
        fallback_model=None,
        runner_snapshot_fallback_safe=True,
        pulse_slot_seconds=2.0,
        pulse_frame_seconds=20.0,
        fan_authority=False,
        fan_pwm_capable=False,
        fan_min_duty=0.0,
        fan_max_duty=100.0,
        setpoint=replay.intervals[0].temperature_f,
        ambient_temperature=replay.ambient_temperature_f,
        software_version="sanitized-fixture",
        build_version="sanitized-fixture",
        cook_id=_COOK_ID,
        runner_generation=0,
    )


def _thermal_sample(at_s: float, temperature_f: float, ambient_f: float) -> ThermalSample:
    monotonic_ms = _shifted_ms(at_s)
    return ThermalSample(
        monotonic_ms=monotonic_ms,
        wall_ms=_WALL_OFFSET_MS + monotonic_ms,
        chamber_temperature=temperature_f,
        units="F",
        probe_valid=True,
        probe_source="sanitized-august-28-chamber",
        ambient_temperature=ambient_f,
        ambient_source="configured",
        ambient_uncertainty=0.0,
        settings_revision=1,
    )


def _record_frame_gap(
    gap: ReplayGap,
    *,
    trace: ControlTraceSession,
    persistence: ModelPersistenceWorker,
    trajectory: LearningTrajectoryRuntime,
) -> None:
    identity = trace.identity
    assert identity is not None
    monotonic_end_ms = _shifted_ms(gap.frame.end_s)
    wall_start_ms = _wall_shifted_ms(gap.frame.start_s)
    wall_end_ms = _wall_shifted_ms(gap.frame.end_s)
    payload = RecorderGapPayload(
        lost_record_count=1,
        gap_start_ms=wall_start_ms,
        gap_end_ms=wall_end_ms,
        reason=gap.reason,
        frame_start_ms=wall_start_ms,
        frame_end_ms=wall_end_ms,
        result_revision=gap.frame.result_revision,
        observation_sequence=gap.frame.result_revision,
    )
    assert trace.record(TraceEventKind.RECORDER_GAP, payload, wall_end_ms)
    evidence = ModelEvidenceRecord(
        evidence_id=(f"{identity.session_id}:recorder-gap:0:{gap.frame.result_revision}:{wall_end_ms}"),
        kind=EvidenceKind.RECORDER_GAP,
        session_id=identity.session_id,
        cook_id=identity.cook_id,
        timestamp_ms=wall_end_ms,
        role_generation=0,
        model_digest=None,
        provenance_digest=None,
        payload=RecorderGapEvidence(lost_record_count=1, reason=gap.reason),
    )
    assert persistence.submit_evidence_batch((evidence,)).accepted
    trajectory.intervention(
        TrajectoryBoundary(
            reason=TrajectoryBreakReason.RECORDER_GAP,
            monotonic_ms=monotonic_end_ms,
            wall_ms=wall_end_ms,
            detail=gap.reason,
        )
    )


def _run_replay() -> _ReplayResult:
    replay = load_pid_sp_august_28_replay()
    repository = LearningTrajectoryRepository()
    model_store = ControllerModelStore()
    persistence = ModelPersistenceWorker(
        model_store,
        _LOGGER,
        trajectory_repository=repository,
    )
    segment_ids = count(1)
    journal_clock_ms = [_shifted_ms(replay.actuation_frames[0].start_s)]
    journal = ActuationDeliveryJournal(
        monotonic_clock=lambda: journal_clock_ms[0],
        wall_clock=lambda: _WALL_OFFSET_MS + journal_clock_ms[0],
    )
    delivered_grill = DeliveredGrillPlatform(
        FakeGrillPlatform(dc_fan=True),
        journal=journal,
        readback_authoritative=True,
    )
    delivered_grill.fan_on()
    trajectory = LearningTrajectoryRuntime(
        journal=journal,
        persistence=persistence,
        segment_id_factory=lambda: f"pid-sp-august-28-segment-{next(segment_ids)}",
        trajectory_session_id_factory=lambda: _TRAJECTORY_ID,
    )
    partition_digest: list[str | None] = [None]
    config: dict[str, object] = dict(replay.controller_config)
    config["enable_identification"] = True
    core = PidSpController(
        config,
        "F",
        {},
        model_persistence=persistence,
        trajectory_repository=repository,
        fit_partition_digest=lambda: partition_digest[0],
        clock_ms=lambda: _TIME_OFFSET_MS,
    )
    runner = SyncControllerRunner(core, controller_type=ControllerType.PID_SP)
    recorder = ControlTraceRecorder(
        monotonic_clock=lambda: _TIME_OFFSET_MS,
        wall_clock=lambda: _WALL_OFFSET_MS + _TIME_OFFSET_MS,
    )
    trace = ControlTraceSession(
        recorder,
        warning=_LOGGER.warning,
        session_id_factory=lambda: _TRACE_SESSION_ID,
    )
    identity = trace.ensure_open(
        _trace_context(replay, config),
        timestamp_ms=_wall_shifted_ms(replay.actuation_frames[0].start_s),
    )
    assert identity is not None
    assert trajectory.bind_trace_session(
        identity.session_id,
        identity.cook_id,
        trace.trajectory_segment_publisher(identity),
    )
    trajectory.mode_entered(_mode_entered(replay))
    trajectory.observe_temperature(_thermal_sample(0.0, replay.anchor.temperature_f, replay.ambient_temperature_f))
    learning = HoldLearningRuntime(
        runner=runner,
        model_store=model_store,
        persistence=persistence,
        trajectory_repository=repository,
        trace=trace,
        controller_name="pid_sp",
        logger=_LOGGER,
        initial_generation=0,
        learning_trajectory=trajectory,
    )
    learning.bind_generation(0)

    by_frame = {
        (observation.frame_start_s, observation.frame_end_s): (observation, interval)
        for observation, interval in zip(replay.observations, replay.intervals[1:], strict=True)
    }
    gaps = {(gap.frame.start_s, gap.frame.end_s): gap for gap in replay.gaps}
    for frame in replay.actuation_frames:
        key = (frame.start_s, frame.end_s)
        gap = gaps.get(key)
        if gap is not None:
            assert trajectory.barrier(timeout=5.0), trajectory.status()
            assert persistence.barrier(timeout=5.0)
            _record_frame_gap(
                gap,
                trace=trace,
                persistence=persistence,
                trajectory=trajectory,
            )
            continue
        observation, interval = by_frame[key]
        trajectory.observe_temperature(
            _thermal_sample(
                interval.end_s,
                interval.temperature_f,
                replay.ambient_temperature_f,
            )
        )
        shifted = replace(
            observation,
            frame_start_s=_wall_shifted_ms(observation.frame_start_s) / 1_000,
            frame_end_s=_wall_shifted_ms(observation.frame_end_s) / 1_000,
        )
        frame_key = (
            round(shifted.frame_start_s * 1_000),
            round(shifted.frame_end_s * 1_000),
        )
        learning.submit_completed_observation(frame_key, shifted)
        learning.reconcile_outcomes(_wall_shifted_ms(interval.end_s) / 1_000)
        assert trajectory.barrier(timeout=5.0), trajectory.status()
        assert persistence.barrier(timeout=5.0)

    terminal_s = replay.actuation_frames[-1].end_s
    assert runner.stop_and_retain_for_teardown() is True
    trajectory.mode_exited(
        ModeExited(
            effective_mode="Hold",
            next_effective_mode="Stop",
            monotonic_ms=_shifted_ms(terminal_s),
            wall_ms=_WALL_OFFSET_MS + _shifted_ms(terminal_s),
            reason=TrajectoryBreakReason.STOP,
        )
    )
    assert trajectory.barrier(timeout=5.0)
    prefit_segments = repository.read_cook_segments(_COOK_ID)
    if prefit_segments:
        partition_digest[0] = prefit_segments[-1].fit_partition_digest
    assert learning.barrier_for_teardown(generation=0)
    learning.schedule_stop_fit({"controller": {"config": {"pid_sp": {"enable_identification": True}}}})
    learning.finish_teardown(generation=0)
    assert persistence.barrier(timeout=5.0)

    segments = repository.read_cook_segments(_COOK_ID)
    fit_result = None
    if segments:
        partition = segments[-1].fit_partition_digest
        snapshot = repository.snapshot_fit_corpus(partition)
        request = FitRequest(
            request_id=_digest("pid-sp-august-28-e2e-fit"),
            origin=CandidateOrigin.PASSIVE_ONLINE,
            fit_corpus=snapshot.identity,
            configuration_digest=canonical_trajectory_digest(config),
            parent_incumbent_digest=canonical_pid_sp_observation_model_digest(None),
            parent_incumbent_generation=0,
            candidate_generation=1,
        )
        fit_result = fit_pid_sp_corpus(request, snapshot.segments, config)

    evidence = tuple(read_model_evidence(cook_id=_COOK_ID))
    cold_repository = LearningTrajectoryRepository()
    cold_segments = cold_repository.read_cook_segments(_COOK_ID)
    cold_store = ControllerModelStore()
    cold_persistence = ModelPersistenceWorker(
        cold_store,
        _LOGGER,
        trajectory_repository=cold_repository,
    )
    cold_core = PidSpController(
        config,
        "F",
        {},
        model_persistence=cold_persistence,
        trajectory_repository=cold_repository,
        fit_partition_digest=lambda: partition_digest[0],
        clock_ms=lambda: _TIME_OFFSET_MS + 1,
    )
    cold_runner = SyncControllerRunner(
        cold_core,
        controller_type=ControllerType.PID_SP,
    )
    cold_learning = HoldLearningRuntime(
        runner=cold_runner,
        model_store=cold_store,
        persistence=cold_persistence,
        trajectory_repository=cold_repository,
        trace=None,
        controller_name="pid_sp",
        logger=_LOGGER,
        initial_generation=0,
    )
    cold_learning.restore_model(
        timestamp_ms=_TIME_OFFSET_MS + 1,
        controller_name="pid_sp",
    )
    cold_checkpoint = cold_store.load("pid_sp")
    cold_learning.finish_teardown(generation=0)
    cold_evidence = tuple(read_model_evidence(cook_id=_COOK_ID))
    return _ReplayResult(
        replay=replay,
        trace_records=tuple(read_control_trace_cook(_COOK_ID)),
        segments=segments,
        fit_result=fit_result,
        evidence=evidence,
        cold_segments=cold_segments,
        cold_evidence=cold_evidence,
        cold_checkpoint=cold_checkpoint,
    )


def _assert_no_duplicate_replay_rows(result: _ReplayResult) -> None:
    trace_rows = tuple(record.model_dump_json() for record in result.trace_records)
    assert len(trace_rows) == len(set(trace_rows))
    assert len(result.segments) == len({segment.segment_id for segment in result.segments})
    assert len(result.evidence) == len({record.evidence_id for record in result.evidence})


def test_august_28_fixture_has_one_accounted_terminal_result_per_exact_frame() -> None:
    replay = load_pid_sp_august_28_replay()

    assert len(replay.intervals) == 408
    assert len(replay.actuation_frames) == 409
    assert len(replay.observations) == 407
    assert tuple(gap.reason for gap in replay.gaps) == (_GAP_REASON, _GAP_REASON)
    accounted = {(observation.frame_start_s, observation.frame_end_s) for observation in replay.observations} | {
        (gap.frame.start_s, gap.frame.end_s) for gap in replay.gaps
    }
    assert accounted == {(frame.start_s, frame.end_s) for frame in replay.actuation_frames}


def test_august_28_raw_evidence_survives_pid_sp_stop_fit_and_cold_restart(
    ds,
    tmp_path,
) -> None:
    ds._reset_for_tests(str(tmp_path / "pid-sp-replay-first.db"))
    ds.init()
    result = _run_replay()
    ds._reset_for_tests(str(tmp_path / "pid-sp-replay-second.db"))
    ds.init()
    duplicate = _run_replay()

    _assert_no_duplicate_replay_rows(result)
    _assert_no_duplicate_replay_rows(duplicate)
    assert {record.session_id for record in result.trace_records} == {_TRACE_SESSION_ID}
    assert tuple(record.session_id for record in result.trace_records) == tuple(
        record.session_id for record in duplicate.trace_records
    )
    assert tuple(record.evidence_id for record in result.evidence) == tuple(
        record.evidence_id for record in duplicate.evidence
    )
    assert tuple((segment.source_trace_digest, segment.content_digest) for segment in result.segments) == tuple(
        (segment.source_trace_digest, segment.content_digest) for segment in duplicate.segments
    )
    assert result.fit_result is not None
    assert duplicate.fit_result is not None
    assert result.fit_result.request.request_id == duplicate.fit_result.request.request_id
    assert tuple(
        record.evidence_id for record in result.evidence if record.kind is EvidenceKind.PID_SP_FIT_DECISION
    ) == tuple(record.evidence_id for record in duplicate.evidence if record.kind is EvidenceKind.PID_SP_FIT_DECISION)

    observation_records = tuple(
        record for record in result.trace_records if record.event_kind is TraceEventKind.MODEL_OBSERVATION
    )
    observation_payloads = tuple(record.payload for record in observation_records)
    assert len(observation_payloads) == 407
    assert all(
        isinstance(payload, ModelObservationPayload) and payload.eligible and payload.rejection_reasons == ()
        for payload in observation_payloads
    )
    expected_observation_wall_bounds = tuple(
        (
            _WALL_OFFSET_MS + _shifted_ms(observation.frame_start_s),
            _WALL_OFFSET_MS + _shifted_ms(observation.frame_end_s),
        )
        for observation in result.replay.observations
    )
    assert (
        tuple(
            (payload.frame_start_ms, payload.frame_end_ms)
            for payload in observation_payloads
            if isinstance(payload, ModelObservationPayload)
        )
        == expected_observation_wall_bounds
    )
    assert tuple(record.ts_ms for record in observation_records) == tuple(
        _wall_shifted_ms(interval.end_s) for interval in result.replay.intervals[1:]
    )
    gap_records = tuple(record for record in result.trace_records if record.event_kind is TraceEventKind.RECORDER_GAP)
    gap_payloads = tuple(record.payload for record in gap_records)
    assert len(gap_payloads) == 2
    assert all(isinstance(payload, RecorderGapPayload) and payload.reason == _GAP_REASON for payload in gap_payloads)
    assert "runner-no-observation-outcome" not in {
        payload.reason for payload in gap_payloads if isinstance(payload, RecorderGapPayload)
    }
    expected_gap_wall_bounds = tuple(
        (
            _WALL_OFFSET_MS + _shifted_ms(gap.frame.start_s),
            _WALL_OFFSET_MS + _shifted_ms(gap.frame.end_s),
        )
        for gap in result.replay.gaps
    )
    assert (
        tuple(
            (payload.frame_start_ms, payload.frame_end_ms)
            for payload in gap_payloads
            if isinstance(payload, RecorderGapPayload)
        )
        == expected_gap_wall_bounds
    )
    assert tuple(record.ts_ms for record in gap_records) == tuple(end_ms for _, end_ms in expected_gap_wall_bounds)

    assert result.segments, "exact synchronized frames produced no finalized durable corpus"
    assert all(segment.state == "finalized" for segment in result.segments)
    durable_frame_bounds = tuple(
        (frame.monotonic_start_ms, frame.monotonic_end_ms)
        for segment in result.segments
        for frame in segment.scored_hold_frames
    )
    expected_frame_bounds = tuple(
        (_shifted_ms(observation.frame_start_s), _shifted_ms(observation.frame_end_s))
        for observation in result.replay.observations
    )
    assert durable_frame_bounds == expected_frame_bounds
    assert result.cold_segments == result.segments

    fit_result = result.fit_result
    assert fit_result.reason == "insufficient-delay-identifiability"
    assert len({episode.episode_id for episode in fit_result.episodes}) >= 2
    assert len(fit_result.delay_profiles) == 3
    unavailable_profiles = tuple(
        profile
        for profile in fit_result.delay_profiles
        if DelayBlocker.NO_PHYSICALLY_VALID_CANDIDATE in profile.blockers
    )
    assert unavailable_profiles
    assert all(profile.basin is None for profile in unavailable_profiles)
    assert fit_result.comparison is not None
    assert fit_result.comparison.selected is None

    terminal = tuple(record.payload for record in result.evidence if record.kind is EvidenceKind.PID_SP_FIT_DECISION)
    assert len(terminal) == 1
    assert isinstance(terminal[0], PidSpFitDecisionEvidence)
    assert terminal[0].outcome == "rejected"
    assert terminal[0].reason == "insufficient-delay-identifiability"
    assert terminal[0].episode_ids == tuple(episode.episode_id for episode in fit_result.episodes)
    assert result.cold_evidence == result.evidence
    assert result.cold_checkpoint is None
