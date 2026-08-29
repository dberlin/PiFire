"""Production lifecycle proof for bounded cumulative grey-model learning."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

import file_mgmt.cookfile as cookfile_mod
from common.controller_model_state import ControllerModelStore
from common.cook_diagnostics import ControllerLearningReport
from common.defaults import default_metrics
from common.learning_trajectory import (
    TRAJECTORY_OBSERVATION_SCHEMA_VERSION,
    FitCorpusIdentity,
    FrameDeliveryCertainty,
    HoldEntrySample,
    LearningTrajectoryFrame,
    LearningTrajectorySegment,
    ModelFitLineage,
    TrajectoryBreakReason,
    canonical_trajectory_digest,
)
from common.model_evidence import (
    MODEL_EVIDENCE_SCHEMA_VERSION,
    ChallengerRoundEvidence,
    EvidenceKind,
    ModelEvidenceRecord,
)
from common.persistence.history import append_metric, write_history
from common.persistence.learning_trajectory import LearningTrajectoryRepository
from common.persistence.model_challenger import (
    ModelChallengerState,
    complete_model_challenger_round,
    create_model_challenger,
    prepare_model_challenger_activation,
    qualify_model_challenger,
    recover_model_challenger,
)
from common.persistence.model_evidence import read_model_activation
from controller.acados.contracts import GreyBoxMPCConfig
from controller.model_learning.activation import (
    ActivationPhase,
    GreyControlPairDescriptor,
    PreparedActivationRecord,
    canonical_snapshot_digest,
)
from controller.model_learning.confidence import qualification_gates
from controller.model_learning.contracts import (
    ActivationPolicy,
    CandidateOrigin,
    FitRequest,
    activation_policy_for_origin,
)
from controller.model_learning.report import build_learning_report
from controller.mpc_model import replay_delay_chain_arrays, simulate_grey_box_intervals
from controller.pid_sp_learning import current_pid_sp_learning_report
from controller.runtime.actuation_delivery import DeliveredActuationIntegral
from controller.runtime.learning_trajectory import (
    LearningTrajectoryRuntime,
    ModeEntered,
    ThermalSample,
    TrajectoryBoundary,
)
from controller.runtime.model_fitting import (
    GreyFitSuccess,
    fit_segmented_grey,
    grey_config_digest,
    segmented_corpus_fit_job,
)
from controller.runtime.model_persistence import ModelPersistenceWorker
from file_mgmt.cookfile import create_cookfile, read_cookfile

_FRAME_MS = 20_000
_FRAME_SECONDS = 20.0
_WALL_EPOCH_MS = 1_800_000_000_000
_REQUIRED_HORIZONS = (3, 15, 45, 90, 180)
_LOAD_LEVELS = (0.20, 0.55, 0.90)
_TRUTH = GreyBoxMPCConfig(
    C_c=1767.5013593870272,
    K_Q=288.2098500448781,
    h_amb=0.5,
    T_amb=20.0,
    theta=52.241101540886156,
    sigma=1.4e-9,
)


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _loads(count: int) -> tuple[float, ...]:
    return tuple(_LOAD_LEVELS[(index // 8) % len(_LOAD_LEVELS)] for index in range(count))


def _frame(
    sequence: int,
    *,
    segment_start_ms: int,
    load: float,
    temperature_c: float,
    effective_mode: str,
    calibration_origin: bool = False,
) -> LearningTrajectoryFrame:
    start_ms = segment_start_ms + sequence * _FRAME_MS
    end_ms = start_ms + _FRAME_MS
    wall_start_ms = _WALL_EPOCH_MS + start_ms
    return LearningTrajectoryFrame(
        sequence=sequence,
        monotonic_start_ms=start_ms,
        monotonic_end_ms=end_ms,
        wall_start_ms=wall_start_ms,
        wall_end_ms=wall_start_ms + _FRAME_MS,
        chamber_temperature_c=temperature_c,
        temperature_sample_monotonic_ms=end_ms,
        temperature_sample_wall_ms=wall_start_ms + _FRAME_MS,
        temperature_sample_age_ms=0,
        temperature_sample_wall_age_ms=0,
        temperature_sample_clock_skew_ms=0,
        source_temperature_units="C",
        settings_revision=9,
        probe_valid=True,
        probe_source="cumulative-e2e-probe",
        ambient_temperature_c=_TRUTH.T_amb,
        ambient_source="configured",
        ambient_uncertainty_c=0.0,
        delivered_auger_on_seconds=load * _FRAME_SECONDS,
        realized_auger_duty=load,
        normalized_combustion_load=load,
        delivered_fan_on_seconds=_FRAME_SECONDS,
        fan_duty_integral_seconds=_FRAME_SECONDS,
        mean_actual_fan_duty=1.0,
        auger_delivery_certainty=FrameDeliveryCertainty.EXACT,
        fan_delivery_certainty=FrameDeliveryCertainty.EXACT,
        effective_mode=effective_mode,
        recipe_step_id=None,
        complete=True,
        continuous=True,
        partial=False,
        boundary_reason=None,
        calibration_origin=calibration_origin,
    )


def _segment(
    segment_id: str,
    *,
    cook_id: str,
    start_ms: int,
    pre_roll_count: int,
    scored_count: int,
    initial_temperature_c: float,
    calibration_origin: bool = False,
) -> LearningTrajectorySegment:
    if pre_roll_count + scored_count == 0:
        raise AssertionError("a cumulative E2E segment must own at least one frame")
    pre_roll_loads = (0.20,) * pre_roll_count
    delay_states = replay_delay_chain_arrays(
        (_FRAME_SECONDS,) * pre_roll_count,
        pre_roll_loads,
        theta=_TRUTH.theta,
        n_delay=_TRUTH.delay_states,
        initial_load=pre_roll_loads[0] if pre_roll_loads else 0.20,
    )
    scored_loads = _loads(scored_count)
    scored_temperatures = (
        simulate_grey_box_intervals(
            (_FRAME_SECONDS,) * scored_count,
            scored_loads,
            (_TRUTH.T_amb,) * scored_count,
            C_c=_TRUTH.C_c,
            h_amb=_TRUTH.h_amb,
            T0=initial_temperature_c,
            K_Q=_TRUTH.K_Q,
            sigma=_TRUTH.sigma,
            theta=_TRUTH.theta,
            n_delay=_TRUTH.delay_states,
            initial_delay_states=delay_states,
        )
        if scored_count
        else ()
    )
    pre_roll = tuple(
        _frame(
            sequence,
            segment_start_ms=start_ms,
            load=load,
            temperature_c=initial_temperature_c,
            effective_mode="Smoke",
        )
        for sequence, load in enumerate(pre_roll_loads)
    )
    scored = tuple(
        _frame(
            pre_roll_count + index,
            segment_start_ms=start_ms,
            load=load,
            temperature_c=float(scored_temperatures[index]),
            effective_mode="Hold",
            calibration_origin=calibration_origin,
        )
        for index, load in enumerate(scored_loads)
    )
    frames = (*pre_roll, *scored)
    return LearningTrajectorySegment(
        schema_version=1,
        observation_schema_version=TRAJECTORY_OBSERVATION_SCHEMA_VERSION,
        segment_id=segment_id,
        cook_id=cook_id,
        trajectory_session_id=f"trajectory-{segment_id}",
        trace_session_ids=(f"trace-{segment_id}",),
        collection_provenance={"origin": CandidateOrigin.PASSIVE_ONLINE.value, "role_generation": 4},
        configuration_provenance={"controller": "mpc", "revision": 9},
        cadence_digest=_digest("cadence-20-seconds-v1"),
        model_structure_digest=_digest("grey-one-zone-erlang-v1"),
        held_physics_digest=_digest("held-grey-physics-v1"),
        delay_input_mapping_digest=_digest("normalized-combustion-load-v1"),
        actuation_mapping_digest=_digest("framed-pulse-v1"),
        scored_fan_regime_digest=_digest("fixed-fan-v1"),
        ambient_semantics_digest=_digest("configured-celsius-v1"),
        pre_roll_frames=pre_roll,
        hold_entry=(
            HoldEntrySample(
                monotonic_ms=scored[0].monotonic_start_ms,
                wall_ms=scored[0].wall_start_ms,
                chamber_temperature_c=initial_temperature_c,
                probe_valid=True,
                probe_source="cumulative-e2e-probe",
            )
            if scored
            else None
        ),
        scored_hold_frames=scored,
        generation_audit_ranges=(
            {
                "start_sequence": frames[0].sequence,
                "end_sequence": frames[-1].sequence,
                "role_generation": 4,
            },
        ),
        start_monotonic_ms=frames[0].monotonic_start_ms,
        end_monotonic_ms=frames[-1].monotonic_end_ms,
        start_wall_ms=frames[0].wall_start_ms,
        end_wall_ms=frames[-1].wall_end_ms,
        start_sequence=frames[0].sequence,
        end_sequence=frames[-1].sequence,
        pre_roll_end_reason=None,
        terminal_break_reason=None,
        state="open",
        source_trace_digest=_digest(f"source-trace:{segment_id}"),
        source_schema_version=TRAJECTORY_OBSERVATION_SCHEMA_VERSION,
        source_row_digest=_digest(f"source-rows:{segment_id}"),
        build_provenance={"builder": "cumulative-e2e", "revision": 1},
    )


def _persist_finalized(
    repository: LearningTrajectoryRepository,
    segment: LearningTrajectorySegment,
) -> LearningTrajectorySegment:
    cursor = repository.begin_segment(segment)
    receipt = repository.finalize(cursor, TrajectoryBreakReason.STOP)
    assert receipt.segment_id == segment.segment_id
    stored = repository.read_segment(segment.segment_id)
    assert stored is not None
    return stored


def _fit_request(
    fit_corpus: FitCorpusIdentity,
    configuration_digest: str,
) -> FitRequest:
    return FitRequest(
        request_id="cumulative-two-cook-fit",
        origin=CandidateOrigin.PASSIVE_ONLINE,
        fit_corpus=fit_corpus,
        configuration_digest=configuration_digest,
        parent_incumbent_digest=grey_config_digest(_TRUTH),
        parent_incumbent_generation=4,
        candidate_generation=5,
    )


def test_two_cooks_produce_one_exact_manifest_fit_and_replay_through_cookfile_and_database(
    ds,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = LearningTrajectoryRepository()
    first = _persist_finalized(
        repository,
        _segment(
            "cumulative-fit-segment-a",
            cook_id="cumulative-fit-cook-a",
            start_ms=10_000_000,
            pre_roll_count=21,
            scored_count=140,
            initial_temperature_c=80.0,
        ),
    )
    second = _persist_finalized(
        repository,
        _segment(
            "cumulative-fit-segment-b",
            cook_id="cumulative-fit-cook-b",
            start_ms=20_000_000,
            pre_roll_count=21,
            scored_count=140,
            initial_temperature_c=115.0,
        ),
    )
    assert first.fit_partition_digest == second.fit_partition_digest

    snapshot = repository.snapshot_fit_corpus(first.fit_partition_digest)
    assert tuple(segment.segment_id for segment in snapshot.segments) == (
        first.segment_id,
        second.segment_id,
    )
    assert tuple((item.pre_roll_count, item.scored_count) for item in snapshot.identity.slices) == (
        (21, 140),
        (21, 140),
    )
    assert snapshot.identity.corpus_digest == canonical_trajectory_digest(
        {
            "schema_version": snapshot.identity.schema_version,
            "corpus_revision": snapshot.identity.corpus_revision,
            "fit_partition_digest": snapshot.identity.fit_partition_digest,
            "slices": [
                {
                    "segment_id": item.segment_id,
                    "through_ordinal": item.through_ordinal,
                    "prefix_digest": item.prefix_digest,
                    "pre_roll_count": item.pre_roll_count,
                    "scored_count": item.scored_count,
                }
                for item in snapshot.identity.slices
            ],
        }
    )

    request = _fit_request(
        snapshot.identity,
        _digest("cumulative-fit-controller-configuration"),
    )
    job = segmented_corpus_fit_job(snapshot, request, _TRUTH)
    fitted = fit_segmented_grey(job)
    assert isinstance(fitted, GreyFitSuccess)
    assert fitted.optimizer_residual_count == 280
    assert fitted.rejection_reasons == ()
    assert fitted.metrics is not None
    assert tuple(metric.cook_id for metric in fitted.metrics.by_cook) == (
        "cumulative-fit-cook-a",
        "cumulative-fit-cook-b",
    )

    queued_lineage = ModelFitLineage(
        request_id=request.request_id,
        parent_incumbent_digest=request.parent_incumbent_digest,
        parent_incumbent_generation=request.parent_incumbent_generation,
        candidate_generation=request.candidate_generation,
        fit_corpus=snapshot.identity,
        fit_corpus_digest=snapshot.identity.corpus_digest,
        trigger_origin=request.origin.value,
        result_status="queued",
        candidate_digest=None,
    )
    queued = repository.record_fit_request(snapshot, queued_lineage)
    running = repository.record_fit_request(
        snapshot,
        replace(queued_lineage, result_status="running"),
    )
    completed = repository.complete_fit(
        request.request_id,
        candidate_digest=grey_config_digest(fitted.config),
        error=None,
    )
    assert (queued.status, running.status, completed.status) == (
        "queued",
        "running",
        "succeeded",
    )

    replayed = repository.replay_fit(request.request_id)
    replay_result = fit_segmented_grey(segmented_corpus_fit_job(replayed, request, _TRUTH))
    assert isinstance(replay_result, GreyFitSuccess)
    assert replayed.identity == snapshot.identity
    assert replay_result.result_digest == fitted.result_digest
    assert replay_result.config == fitted.config
    assert tuple(segment.content_digest for segment in replayed.segments) == (
        first.content_digest,
        second.content_digest,
    )

    reopened = LearningTrajectoryRepository()
    assert reopened.status() == repository.status()
    assert reopened.replay_fit(request.request_id) == replayed

    history_dir = tmp_path / "history"
    monkeypatch.setattr(cookfile_mod, "HISTORY_FOLDER", f"{history_dir}/")
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
    pid_report = current_pid_sp_learning_report(status={}, checkpoint=None)
    mpc_report = build_learning_report(
        (),
        activation_state={},
        live_status={},
        checkpoint_required=True,
        calibration_command_high_water=0,
    )
    reports = {
        "pid_sp": ControllerLearningReport(
            controller="pid_sp",
            schema_version=1,
            revision=pid_report.revision,
            report=cast(Mapping[str, JsonValue], pid_report.as_dict()),
        ),
        "mpc": ControllerLearningReport(
            controller="mpc",
            schema_version=1,
            revision=mpc_report.revision,
            report=cast(Mapping[str, JsonValue], mpc_report.as_dict()),
        ),
    }
    create_cookfile(
        cook_id=second.cook_id,
        learning_report_provider=reports.__getitem__,
    )
    archives = list(history_dir.glob("*.pifire"))
    assert len(archives) == 1
    with zipfile.ZipFile(archives[0]) as archive:
        archived_diagnostics = json.loads(archive.read("learning_diagnostics.json"))
    reread, status = read_cookfile(archives[0])
    assert status == "OK"
    assert reread["learning_diagnostics"] == archived_diagnostics
    assert archived_diagnostics["corpus"]["segment_count"] == 2
    assert archived_diagnostics["corpus"]["distinct_cook_count"] == 2
    assert archived_diagnostics["trajectory_segments"] == [
        {
            "segment_id": second.segment_id,
            "trajectory_session_id": second.trajectory_session_id,
            "trace_session_ids": list(second.trace_session_ids),
            "cook_id": second.cook_id,
            "segment_schema_version": second.schema_version,
            "observation_schema_version": second.observation_schema_version,
            "state": second.state,
            "source_trace_digest": second.source_trace_digest,
            "source_schema_version": second.source_schema_version,
            "content_digest": second.content_digest,
            "fit_partition_digest": second.fit_partition_digest,
            "source_row_digest": second.source_row_digest,
            "pre_roll_frame_count": len(second.pre_roll_frames),
            "scored_hold_frame_count": len(second.scored_hold_frames),
            "terminal_break_reason": TrajectoryBreakReason.STOP.value,
        }
    ]
    archived_reference = archived_diagnostics["trajectory_segments"][0]
    replayed_second = replayed.segments[1]
    assert (
        archived_reference["content_digest"],
        archived_reference["fit_partition_digest"],
        archived_reference["source_row_digest"],
    ) == (
        replayed_second.content_digest,
        replayed_second.fit_partition_digest,
        replayed_second.source_row_digest,
    )


def test_pre_roll_retention_evicts_the_oldest_whole_segment_beyond_8640_frames(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "retention.db"
    repository = LearningTrajectoryRepository(str(database_path))
    for index in range(49):
        _persist_finalized(
            repository,
            _segment(
                f"retention-{index:03d}",
                cook_id=f"retention-cook-{index:03d}",
                start_ms=index * 4_000_000,
                pre_roll_count=180,
                scored_count=0,
                initial_temperature_c=80.0 + index,
            ),
        )

    status = repository.status()
    assert status.pre_roll_count == 8_640
    assert status.segment_count == 48
    assert status.evicted_segment_count == 1
    assert repository.read_segment("retention-000") is None
    assert repository.read_segment("retention-001") is not None
    assert repository.read_segment("retention-048") is not None
    report = repository.corpus_report()
    assert report.evicted_pre_roll_count == 180
    assert report.evicted_scored_count == 0
    assert report.earliest_wall_ms == _WALL_EPOCH_MS + 4_000_000

    reopened = LearningTrajectoryRepository(str(database_path))
    assert reopened.status() == status
    assert reopened.corpus_report() == report


def test_recovery_quarantines_one_corrupt_segment_without_poisoning_the_fit_corpus(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "corruption.db"
    repository = LearningTrajectoryRepository(str(database_path))
    corrupt = _persist_finalized(
        repository,
        _segment(
            "corrupt-segment",
            cook_id="corrupt-cook",
            start_ms=1_000_000,
            pre_roll_count=1,
            scored_count=2,
            initial_temperature_c=90.0,
        ),
    )
    healthy = _persist_finalized(
        repository,
        _segment(
            "healthy-segment",
            cook_id="healthy-cook",
            start_ms=2_000_000,
            pre_roll_count=1,
            scored_count=2,
            initial_temperature_c=100.0,
        ),
    )
    assert corrupt.fit_partition_digest == healthy.fit_partition_digest
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE learning_trajectory_segment SET rolling_digest=? WHERE segment_id=?",
            ("0" * 64, corrupt.segment_id),
        )
        connection.commit()

    reopened = LearningTrajectoryRepository(str(database_path))
    recovery = reopened.recover_open_segments(now_ms=_WALL_EPOCH_MS + 100_000_000)
    assert recovery.quarantined_segment_ids == (corrupt.segment_id,)
    assert recovery.finalized_segment_ids == ()
    assert reopened.read_segment(corrupt.segment_id) is None
    snapshot = reopened.snapshot_fit_corpus(healthy.fit_partition_digest)
    assert tuple(item.segment_id for item in snapshot.identity.slices) == (healthy.segment_id,)
    assert tuple(segment.content_digest for segment in snapshot.segments) == (healthy.content_digest,)
    assert reopened.status().quarantined_segment_count == 1
    assert reopened.status().scored_count == 2


class _Logger:
    def info(self, _message: str) -> None:
        return None

    def warning(self, _message: str) -> None:
        return None

    def error(self, _message: str) -> None:
        return None


class _DeliveryJournal:
    def __init__(self, *, unknown: bool) -> None:
        self.unknown = unknown

    def integrate(self, start_ms: int, end_ms: int) -> DeliveredActuationIntegral:
        duration_s = (end_ms - start_ms) / 1_000.0
        return DeliveredActuationIntegral(
            monotonic_start_ms=start_ms,
            monotonic_end_ms=end_ms,
            auger_on_seconds=0.4 * duration_s,
            fan_on_seconds=duration_s,
            fan_duty_integral_seconds=duration_s,
            auger_start_active=False,
            auger_end_active=False,
            fan_start_active=True,
            fan_end_active=True,
            pwm_start=1.0,
            pwm_end=1.0,
            auger_certainty=(FrameDeliveryCertainty.UNKNOWN if self.unknown else FrameDeliveryCertainty.EXACT),
            fan_certainty=FrameDeliveryCertainty.EXACT,
            unknown_reasons=("auger-readback-unavailable",) if self.unknown else (),
        )


class _SegmentIds:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.next_value = 0

    def __call__(self) -> str:
        segment_id = f"{self.prefix}-{self.next_value:02d}"
        self.next_value += 1
        return segment_id


def _mode_entered(cook_id: str) -> ModeEntered:
    return ModeEntered(
        effective_mode="Smoke",
        persisted_mode="Smoke",
        monotonic_ms=0,
        wall_ms=_WALL_EPOCH_MS,
        cook_id=cook_id,
        trajectory_session_id=f"trajectory-{cook_id}",
        trace_session_id="00000000-0000-4000-8000-000000000013",
        recipe_step_id=None,
        units="C",
        settings_revision=9,
        collection_provenance={"origin": CandidateOrigin.PASSIVE_ONLINE.value, "role_generation": 4},
        configuration_provenance={"controller": "mpc", "revision": 9},
        cadence_digest=_digest("cadence-20-seconds-v1"),
        model_structure_digest=_digest("grey-one-zone-erlang-v1"),
        held_physics_digest=_digest("held-grey-physics-v1"),
        delay_input_mapping_digest=_digest("normalized-combustion-load-v1"),
        actuation_mapping_digest=_digest("framed-pulse-v1"),
        scored_fan_regime_digest=_digest("fixed-fan-v1"),
        ambient_semantics_digest=_digest("configured-celsius-v1"),
        source_trace_digest=_digest(f"source:{cook_id}"),
        source_schema_version=TRAJECTORY_OBSERVATION_SCHEMA_VERSION,
        source_row_digest=_digest(f"rows:{cook_id}"),
        build_provenance={"builder": "cumulative-e2e", "revision": 1},
    )


def _temperature_sample(at_ms: int, temperature_c: float) -> ThermalSample:
    return ThermalSample(
        monotonic_ms=at_ms,
        wall_ms=_WALL_EPOCH_MS + at_ms,
        chamber_temperature=temperature_c,
        units="C",
        probe_valid=True,
        probe_source="cumulative-e2e-probe",
        ambient_temperature=_TRUTH.T_amb,
        ambient_source="configured",
        ambient_uncertainty=0.0,
        settings_revision=9,
        recipe_step_id=None,
    )


@pytest.mark.parametrize(
    ("reason", "unknown_delivery"),
    (
        (TrajectoryBreakReason.MANUAL, False),
        (TrajectoryBreakReason.LID_OPEN, False),
        (TrajectoryBreakReason.SAFETY, False),
        (TrajectoryBreakReason.ACTUATION_UNKNOWN, True),
    ),
    ids=("manual", "lid-open", "safety", "unknown-actuation"),
)
def test_manual_lid_safety_and_unknown_delivery_create_exact_durable_gaps(
    ds,
    reason: TrajectoryBreakReason,
    unknown_delivery: bool,
) -> None:
    repository = LearningTrajectoryRepository()
    persistence = ModelPersistenceWorker(
        ControllerModelStore(),
        _Logger(),
        trajectory_repository=repository,
    )
    cook_id = f"gap-{reason.value}"
    runtime = LearningTrajectoryRuntime(
        journal=_DeliveryJournal(unknown=unknown_delivery),
        persistence=persistence,
        segment_id_factory=_SegmentIds(cook_id),
        trajectory_session_id_factory=lambda: f"trajectory-runtime-{reason.value}",
    )
    closed = False
    try:
        runtime.mode_entered(_mode_entered(cook_id))
        assert runtime.bind_trace_session(
            "00000000-0000-4000-8000-000000000013",
            cook_id,
            lambda _segment: True,
        )
        runtime.observe_temperature(_temperature_sample(_FRAME_MS, 100.0))
        if not unknown_delivery:
            assert runtime.barrier(timeout=5.0)
            runtime.observe_temperature(_temperature_sample(_FRAME_MS + 975, 100.5))
            runtime.intervention(
                TrajectoryBoundary(
                    reason=reason,
                    monotonic_ms=_FRAME_MS + 1_000,
                    wall_ms=_WALL_EPOCH_MS + _FRAME_MS + 1_000,
                    detail=reason.value,
                    replacement_mode=None,
                )
            )
            runtime.observe_temperature(_temperature_sample(2 * _FRAME_MS + 1_000, 101.0))
        assert runtime.barrier(timeout=5.0)
        status = runtime.status()
        assert status.last_break_reason is reason
        segments = repository.read_cook_segments(cook_id)
        if unknown_delivery:
            assert segments == ()
            assert repository.status().pre_roll_count == 0
        else:
            assert len(segments) == 2
            segment = next(item for item in segments if item.terminal_break_reason is reason)
            assert segment.state == "finalized"
            assert segment.terminal_break_reason is reason
            assert segment.scored_hold_frames == ()
            assert len(segment.pre_roll_frames) == 2
            assert segment.pre_roll_frames[0].complete is True
            assert segment.pre_roll_frames[-1].partial is True
            assert segment.pre_roll_frames[-1].boundary_reason is reason
            replacement = next(item for item in segments if item.segment_id != segment.segment_id)
            assert replacement.state == "open"
            assert len(replacement.pre_roll_frames) == 1
        closed = runtime.close()
        assert closed
    finally:
        if not closed:
            runtime.close()


def _descriptor(
    label: str,
    *,
    theta: float,
    candidate_generation: int,
    role_generation: int,
) -> GreyControlPairDescriptor:
    configuration = {
        "schema": "pifire-cumulative-e2e-pair/v1",
        "parameters": {"theta": theta, "label": label},
    }
    return GreyControlPairDescriptor(
        model_digest=canonical_snapshot_digest(configuration),
        configuration=configuration,
        estimator_kind="ekf",
        solver_kind="acados-grey",
        candidate_generation=candidate_generation,
        role_generation=role_generation,
    )


@pytest.mark.parametrize(
    "origin",
    (CandidateOrigin.PASSIVE_ONLINE, CandidateOrigin.OPERATOR_CALIBRATION),
    ids=("passive-online", "operator-calibration"),
)
def test_supplied_candidate_resumes_then_uses_the_same_two_win_durable_activation_gate(
    tmp_path: Path,
    origin: CandidateOrigin,
) -> None:
    database_path = tmp_path / f"candidate-{origin.value}.db"
    repository = LearningTrajectoryRepository(str(database_path))
    segment = _persist_finalized(
        repository,
        _segment(
            f"candidate-corpus-{origin.value}",
            cook_id=f"candidate-cook-{origin.value}",
            start_ms=3_000_000,
            pre_roll_count=1,
            scored_count=1,
            initial_temperature_c=95.0,
            calibration_origin=origin is CandidateOrigin.OPERATOR_CALIBRATION,
        ),
    )
    corpus = repository.snapshot_fit_corpus(segment.fit_partition_digest).identity
    incumbent = _descriptor(
        "incumbent",
        theta=50.0,
        candidate_generation=4,
        role_generation=4,
    )
    candidate = _descriptor(
        "supplied-candidate",
        theta=65.0,
        candidate_generation=5,
        role_generation=5,
    )
    request_id = f"supplied-candidate-{origin.value}"
    lineage = ModelFitLineage(
        request_id=request_id,
        parent_incumbent_digest=incumbent.model_digest,
        parent_incumbent_generation=incumbent.role_generation,
        candidate_generation=candidate.candidate_generation,
        fit_corpus=corpus,
        fit_corpus_digest=corpus.corpus_digest,
        trigger_origin=origin.value,
        result_status="succeeded",
        candidate_digest=candidate.model_digest,
    )
    manifest = (
        {
            "command_revision": 7,
            "session_id": f"calibration-session-{origin.value}",
            "completed_stages": ["low", "middle", "high", "coast"],
            "stage_evidence_ids": [
                f"calibration-{stage}-{origin.value}" for stage in ("low", "middle", "high", "coast")
            ],
        }
        if origin is CandidateOrigin.OPERATOR_CALIBRATION
        else None
    )
    supplied = ModelChallengerState(
        schema_version=1,
        challenger_id=f"challenger-{origin.value}",
        revision=0,
        phase="evaluating",
        origin=origin,
        policy=activation_policy_for_origin(origin),
        fit_corpus=corpus,
        fit_lineage=lineage,
        fit_preparation={
            "request_id": request_id,
            "accepted": True,
            "candidate_digest": candidate.model_digest,
            "result_digest": _digest(f"fit-result:{origin.value}"),
        },
        controller_configuration_digest=_digest("shared-controller-configuration"),
        incumbent=incumbent,
        candidate=candidate,
        calibration_manifest=manifest,
        evaluation_epoch=0,
        evaluation_round=0,
        consecutive_wins=0,
        required_wins=2,
        last_decision_id=None,
        last_evidence_id=None,
        activation_transaction_id=None,
        retirement_reason=None,
        created_ms=10_000,
        updated_ms=10_000,
        retired_ms=None,
    )
    create_model_challenger(supplied, database_path=database_path)

    resumed = recover_model_challenger(
        incumbent=incumbent,
        candidate=candidate,
        controller_configuration_digest=supplied.controller_configuration_digest,
        fit_corpus=corpus,
        calibration_manifest=manifest,
        recovered_ms=11_000,
        database_path=database_path,
    )
    assert resumed is not None
    assert (
        resumed.phase,
        resumed.revision,
        resumed.evaluation_epoch,
        resumed.evaluation_round,
        resumed.consecutive_wins,
        resumed.last_decision_id,
    ) == ("evaluating", 1, 1, 0, 0, None)
    assert resumed.fit_corpus == supplied.fit_corpus
    assert resumed.fit_lineage == supplied.fit_lineage
    assert resumed.candidate == supplied.candidate
    assert qualification_gates(resumed).blockers == ("consecutive-wins",)

    progressed = resumed
    for round_number in (1, 2):
        decision_id = f"decision-{origin.value}-{round_number}"
        evidence = ModelEvidenceRecord(
            evidence_id=f"challenger-round-{origin.value}-{round_number}",
            kind=EvidenceKind.CHALLENGER_ROUND,
            session_id=f"evaluation-session-{origin.value}",
            cook_id=f"evaluation-cook-{origin.value}",
            timestamp_ms=11_000 + round_number,
            role_generation=incumbent.role_generation,
            model_digest=candidate.model_digest,
            provenance_digest=incumbent.model_digest,
            schema_version=MODEL_EVIDENCE_SCHEMA_VERSION,
            payload=ChallengerRoundEvidence(
                challenger_id=progressed.challenger_id,
                evaluation_epoch=progressed.evaluation_epoch,
                evaluation_round=round_number,
                decision_id=decision_id,
                accepted=True,
                required_horizons=_REQUIRED_HORIZONS,
                completed_horizons=_REQUIRED_HORIZONS,
                incumbent_digest=incumbent.model_digest,
                candidate_digest=candidate.model_digest,
            ),
        )
        progressed = complete_model_challenger_round(
            expected_revision=progressed.revision,
            evidence=evidence,
            database_path=database_path,
        )
        assert progressed.evaluation_round == round_number
        assert progressed.consecutive_wins == round_number
        if round_number == 1:
            assert qualification_gates(progressed).blockers == ("consecutive-wins",)

    gate = qualification_gates(progressed)
    assert gate.accepted
    assert gate.blockers == ()
    qualified = qualify_model_challenger(
        expected_revision=progressed.revision,
        qualified_ms=11_003,
        database_path=database_path,
    )
    assert qualified.phase == "qualified"
    assert qualified.consecutive_wins == qualified.required_wins == 2

    activation = PreparedActivationRecord(
        phase=ActivationPhase.PREPARED,
        transaction_id=_digest(f"activation:{origin.value}"),
        timestamp_ms=11_004,
        incumbent=incumbent,
        candidate=candidate,
        rollback=incumbent,
        origin=origin,
        policy=ActivationPolicy.CAUSAL_AUTO,
        decision_id=cast(str, qualified.last_decision_id),
    )
    activating = prepare_model_challenger_activation(
        expected_revision=qualified.revision,
        activation=activation,
        database_path=database_path,
    )
    durable_activation = read_model_activation(database_path=database_path)
    assert durable_activation is not None
    assert activating.phase == "activating"
    assert activating.activation_transaction_id == activation.transaction_id
    assert durable_activation.phase == ActivationPhase.PREPARED.value
    assert durable_activation.origin == origin.value
    assert durable_activation.policy == ActivationPolicy.CAUSAL_AUTO.value
    assert durable_activation.active_pair == incumbent
    assert durable_activation.rollback_pair == incumbent
    assert durable_activation.candidate_pair == candidate
    assert durable_activation.candidate_digest == candidate.model_digest
