"""Deterministic short-cook MPC admission characterization helpers."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import numpy as np

from common.control_trace import (
    AllocationClampReason,
    AmbientSource,
    AmbientUncertainty,
)
from common.learning_trajectory import (
    TRAJECTORY_OBSERVATION_SCHEMA_VERSION,
    FrameDeliveryCertainty,
    HoldEntrySample,
    LearningTrajectoryFrame,
    LearningTrajectorySegment,
    TrajectoryBreakReason,
    canonical_generation_audit_ranges,
)
from common.mpc_learning import MPC_FORECAST_HORIZON_SECONDS, MPC_FORECAST_HORIZONS
from common.persistence.learning_trajectory import (
    FitCorpusEmptyError,
    FitCorpusSnapshot,
    LearningTrajectoryRepository,
)
from controller.acados import GreyBoxMPCConfig
from controller.applied_output import AppliedOutput, OutputSource
from controller.grey_box import GreyBoxPredictionAdapter
from controller.grill_sim import GrillSim
from controller.model_learning.contracts import CandidateOrigin, FitRequest, FrameObservation
from controller.model_learning.evaluation import (
    CausalForecastEvaluator,
    CompletedForecastOrigin,
    EvaluationConfig,
    evaluate_forecasts,
)
from controller.mpc import Controller
from controller.mpc_config import DEFAULT_MPC_CONFIG
from controller.runtime.model_fitting import (
    FIT_CADENCE_S,
    CausalForecastInput,
    GreyFitError,
    GreyFitSuccess,
    TriggerConfig,
    fit_segmented_grey,
    paired_forecast_origin,
    segmented_corpus_fit_job,
)
from tests.e2e._mpc_online_learning_helpers import _CYCLE, _U_MAX
from tests.fakes.grill import FakeGrillPlatform

TRAINING_SEEDS = (0, 1, 2)
HELD_OUT_SEEDS = (10, 11, 12, 13, 14)
SCORED_FRAMES = 59
PRE_ROLL_FRAMES = 8
PRE_ROLL_DUTY = 0.15
FRAME_SECONDS = int(FIT_CADENCE_S)
TARGET_C = (225.0 - 32.0) * 5.0 / 9.0
HORIZONS = MPC_FORECAST_HORIZON_SECONDS
MIN_EFFECTIVE_DURATION_S = TriggerConfig().min_effective_duration_s

_FRAME_MS = FRAME_SECONDS * 1_000
_WALL_OFFSET_MS = 1_800_000_000_000
_FORECAST_ON_SECONDS = 10
_FORECAST_PRIMER_FRAMES = 30
_CLOSED_LOOP_FRAMES = SCORED_FRAMES


@dataclass(frozen=True, slots=True)
class CookDwell:
    seed: int
    entry_frame: int
    frames_after_entry: int
    frames_within_15f_after_entry: int


@dataclass(frozen=True, slots=True)
class CandidateScore:
    raw_counts: tuple[int, ...]
    effective_duration_s: float
    warmup_excluded_segment_ids: tuple[str, ...]
    horizon_ratios: tuple[tuple[int, float], ...]
    evaluation_blockers: tuple[str, ...]
    whole_cook_ratio: float
    closed_loop_iae_ratio: float
    candidate_overshoot_c: float
    incumbent_overshoot_c: float
    horizon_wins: tuple[tuple[int, int], ...]
    whole_cook_wins: int
    closed_loop_iae_wins: int
    overshoot_wins: int


@dataclass(frozen=True, slots=True)
class ShortCookCampaignResult:
    dwell: tuple[CookDwell, ...]
    first_600s: CandidateScore
    full_177: CandidateScore


@dataclass(frozen=True, slots=True)
class _CollectedCook:
    seed: int
    pre_roll: tuple[LearningTrajectoryFrame, ...]
    hold_entry: HoldEntrySample
    scored: tuple[LearningTrajectoryFrame, ...]
    dwell: CookDwell


@dataclass(frozen=True, slots=True)
class _FitBoundary:
    snapshot: FitCorpusSnapshot
    fit: GreyFitSuccess
    effective_duration_s: float
    warmup_excluded_segment_ids: tuple[str, ...]


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _controller_config(fit: GreyFitSuccess | None = None) -> dict[str, Any]:
    config: dict[str, Any] = dict(DEFAULT_MPC_CONFIG)
    config["control_period"] = float(FRAME_SECONDS)
    if fit is not None:
        config.update({"C_c": fit.config.C_c, "K_Q": fit.config.K_Q, "theta": fit.config.theta})
    return config


def _apply_frame(
    plant: GrillSim,
    grill: FakeGrillPlatform,
    *,
    on_seconds: int,
    fan_frac: float = 1.0,
) -> int:
    if not 0 <= on_seconds <= FRAME_SECONDS:
        raise ValueError("delivered auger time must fit the control frame")
    delivered = 0
    for second in range(FRAME_SECONDS):
        auger_on = second < on_seconds
        (grill.auger_on if auger_on else grill.auger_off)()
        plant.step(auger_on=auger_on, fan_frac=fan_frac)
        delivered += int(auger_on and grill.get_output_status()["auger"])
    grill.auger_off()
    assert delivered == on_seconds
    return delivered


def _frame(
    *,
    sequence: int,
    segment_start_ms: int,
    temperature_c: float,
    ambient_c: float,
    on_seconds: int,
    mode: str,
    family: str,
) -> LearningTrajectoryFrame:
    start_ms = segment_start_ms + sequence * _FRAME_MS
    end_ms = start_ms + _FRAME_MS
    wall_start_ms = _WALL_OFFSET_MS + start_ms
    duty = on_seconds / FRAME_SECONDS
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
        settings_revision=1,
        probe_valid=True,
        probe_source=f"{family}-short-cook-simulator-probe",
        ambient_temperature_c=ambient_c,
        ambient_source="configured",
        ambient_uncertainty_c=0.0,
        delivered_auger_on_seconds=float(on_seconds),
        realized_auger_duty=duty,
        normalized_combustion_load=duty / _U_MAX,
        delivered_fan_on_seconds=float(FRAME_SECONDS),
        fan_duty_integral_seconds=float(FRAME_SECONDS),
        mean_actual_fan_duty=1.0,
        auger_delivery_certainty=FrameDeliveryCertainty.EXACT,
        fan_delivery_certainty=FrameDeliveryCertainty.EXACT,
        effective_mode=mode,
        recipe_step_id=None,
        complete=True,
        continuous=True,
        partial=False,
        boundary_reason=None,
        role_generation=0,
    )


def _collect_cook(plant_type: type[GrillSim], family: str, seed: int) -> _CollectedCook:
    plant = plant_type(seed=seed)
    grill = FakeGrillPlatform(dc_fan=True)
    controller = Controller(_controller_config(), "C", dict(_CYCLE))
    controller.set_target(TARGET_C)
    segment_start_ms = (seed + 1) * 10_000_000
    pre_roll: list[LearningTrajectoryFrame] = []
    scored: list[LearningTrajectoryFrame] = []
    current_temperature = plant.measured()
    try:
        pre_roll_on_seconds = round(PRE_ROLL_DUTY * FRAME_SECONDS)
        for sequence in range(PRE_ROLL_FRAMES):
            controller.update(current_temperature)
            delivered = _apply_frame(plant, grill, on_seconds=pre_roll_on_seconds)
            current_temperature = plant.measured()
            controller.set_output(
                AppliedOutput(
                    ratio=delivered / FRAME_SECONDS,
                    requested=PRE_ROLL_DUTY,
                    source=OutputSource.CONTROLLER,
                    timestamp=float((sequence + 1) * FRAME_SECONDS),
                )
            )
            pre_roll.append(
                _frame(
                    sequence=sequence,
                    segment_start_ms=segment_start_ms,
                    temperature_c=current_temperature,
                    ambient_c=plant.T_amb,
                    on_seconds=delivered,
                    mode="Smoke",
                    family=family,
                )
            )

        hold_anchor_c = current_temperature
        hold_entry = HoldEntrySample(
            monotonic_ms=segment_start_ms + PRE_ROLL_FRAMES * _FRAME_MS,
            wall_ms=_WALL_OFFSET_MS + segment_start_ms + PRE_ROLL_FRAMES * _FRAME_MS,
            chamber_temperature_c=hold_anchor_c,
            probe_valid=True,
            probe_source=f"{family}-short-cook-simulator-probe",
        )
        for frame_index in range(SCORED_FRAMES):
            result = controller.update(current_temperature)
            requested_raw = result["cycle_ratio"]
            assert isinstance(requested_raw, float)
            requested = requested_raw
            on_seconds = round(min(max(requested, 0.0), _U_MAX) * FRAME_SECONDS)
            delivered = _apply_frame(plant, grill, on_seconds=on_seconds)
            current_temperature = plant.measured()
            controller.set_output(
                AppliedOutput(
                    ratio=delivered / FRAME_SECONDS,
                    requested=requested,
                    source=OutputSource.CONTROLLER,
                    timestamp=float((PRE_ROLL_FRAMES + frame_index + 1) * FRAME_SECONDS),
                )
            )
            scored.append(
                _frame(
                    sequence=PRE_ROLL_FRAMES + frame_index,
                    segment_start_ms=segment_start_ms,
                    temperature_c=current_temperature,
                    ambient_c=plant.T_amb,
                    on_seconds=delivered,
                    mode="Hold",
                    family=family,
                )
            )
    finally:
        controller.close()

    entry_band_c = 5.0 * 5.0 / 9.0
    dwell_band_c = 15.0 * 5.0 / 9.0
    entry_frame = next(
        index for index, item in enumerate(scored) if abs(item.chamber_temperature_c - TARGET_C) <= entry_band_c
    )
    after_entry = scored[entry_frame:]
    dwell = CookDwell(
        seed=seed,
        entry_frame=entry_frame,
        frames_after_entry=len(after_entry),
        frames_within_15f_after_entry=sum(
            abs(item.chamber_temperature_c - TARGET_C) <= dwell_band_c for item in after_entry
        ),
    )
    return _CollectedCook(seed, tuple(pre_roll), hold_entry, tuple(scored), dwell)


def _segment(cook: _CollectedCook, family: str, scored_count: int) -> LearningTrajectorySegment:
    scored = cook.scored[:scored_count]
    frames = (*cook.pre_roll, *scored)
    segment_id = f"{family}-short-cook-seed-{cook.seed}"
    return LearningTrajectorySegment(
        schema_version=1,
        observation_schema_version=TRAJECTORY_OBSERVATION_SCHEMA_VERSION,
        segment_id=segment_id,
        cook_id=segment_id,
        trajectory_session_id=f"{segment_id}-trajectory",
        trace_session_ids=(f"{segment_id}-trace",),
        collection_provenance={"origin": CandidateOrigin.PASSIVE_ONLINE.value, "seed": cook.seed},
        configuration_provenance={"controller": "mpc", "configuration": "shipped-uncalibrated"},
        cadence_digest=_digest("short-cook-twenty-second-cadence"),
        model_structure_digest=_digest("grey-one-zone-erlang-production-v1"),
        held_physics_digest=_digest("grey-one-zone-erlang-production-v1"),
        delay_input_mapping_digest=_digest("normalized-combustion-load"),
        actuation_mapping_digest=_digest("one-second-boolean-pulses-u-max-0.9"),
        scored_fan_regime_digest=_digest("fan-fixed-one"),
        ambient_semantics_digest=_digest("simulator-configured-ambient"),
        pre_roll_frames=cook.pre_roll,
        hold_entry=cook.hold_entry,
        scored_hold_frames=scored,
        generation_audit_ranges=canonical_generation_audit_ranges(frames),
        start_monotonic_ms=frames[0].monotonic_start_ms,
        end_monotonic_ms=frames[-1].monotonic_end_ms,
        start_wall_ms=frames[0].wall_start_ms,
        end_wall_ms=frames[-1].wall_end_ms,
        start_sequence=frames[0].sequence,
        end_sequence=frames[-1].sequence,
        pre_roll_end_reason=TrajectoryBreakReason.MODE_TRANSITION,
        terminal_break_reason=None,
        state="open",
        source_trace_digest=_digest(f"{segment_id}:trace"),
        source_schema_version=TRAJECTORY_OBSERVATION_SCHEMA_VERSION,
        source_row_digest=_digest(f"{segment_id}:rows"),
        build_provenance={"suite": "short-cook-mpc-admission", "revision": 1},
    )


def _persist_cooks(
    repository: LearningTrajectoryRepository,
    cooks: tuple[_CollectedCook, ...],
    family: str,
) -> tuple[str, int]:
    partition_digest: str | None = None
    for cook in cooks:
        initial = _segment(cook, family, 1)
        if partition_digest is None:
            partition_digest = initial.fit_partition_digest
        else:
            assert initial.fit_partition_digest == partition_digest
        cursor = repository.begin_segment(initial)
        for frame in cook.scored[1:]:
            receipt = repository.append(cursor, scored=(frame,))
            cursor = receipt.cursor
        repository.finalize(cursor, TrajectoryBreakReason.STOP)
    assert partition_digest is not None
    return partition_digest, repository.corpus_report().corpus_revision


def _fit_result(
    snapshot: FitCorpusSnapshot,
    family: str,
    label: str,
) -> GreyFitSuccess | GreyFitError:
    incumbent = GreyBoxMPCConfig()
    request = FitRequest(
        request_id=f"{family}-short-cook-{label}-fit",
        origin=CandidateOrigin.PASSIVE_ONLINE,
        fit_corpus=snapshot.identity,
        configuration_digest=_digest(f"{family}:short-cook:configuration"),
        parent_incumbent_digest=_digest(f"{family}:shipped-uncalibrated"),
        parent_incumbent_generation=0,
        candidate_generation=1,
    )
    return fit_segmented_grey(segmented_corpus_fit_job(snapshot, request, incumbent))


def _fit_snapshot(snapshot: FitCorpusSnapshot, family: str, label: str) -> GreyFitSuccess:
    result = _fit_result(snapshot, family, label)
    assert isinstance(result, GreyFitSuccess), result
    return result


def _fit_boundary(snapshot: FitCorpusSnapshot, fit: GreyFitSuccess) -> _FitBoundary:
    durations = tuple(
        float(np.sum(segment.scored_duration_s[mask]))
        for segment, mask in zip(
            segmented_corpus_fit_job(snapshot, fit.request, GreyBoxMPCConfig()).segments,
            fit.effective_masks,
            strict=True,
        )
    )
    excluded = tuple(
        corpus_slice.segment_id
        for corpus_slice, mask in zip(snapshot.identity.slices, fit.effective_masks, strict=True)
        if not bool(np.any(mask))
    )
    production_exclusions = getattr(fit, "warmup_excluded_segment_ids", excluded)
    assert tuple(production_exclusions) == excluded
    return _FitBoundary(snapshot, fit, sum(durations), excluded)


def _first_600_second_boundary(
    repository: LearningTrajectoryRepository,
    partition_digest: str,
    full_revision: int,
    family: str,
) -> _FitBoundary:
    incumbent_theta = GreyBoxMPCConfig().theta
    for revision in range(1, full_revision + 1):
        try:
            snapshot = repository.snapshot_fit_corpus(partition_digest, through_revision=revision)
        except FitCorpusEmptyError:
            continue
        job = segmented_corpus_fit_job(
            snapshot,
            FitRequest(
                request_id=f"{family}-short-cook-prefix-probe-{revision}",
                origin=CandidateOrigin.PASSIVE_ONLINE,
                fit_corpus=snapshot.identity,
                configuration_digest=_digest(f"{family}:short-cook:configuration"),
                parent_incumbent_digest=_digest(f"{family}:shipped-uncalibrated"),
                parent_incumbent_generation=0,
                candidate_generation=1,
            ),
            GreyBoxMPCConfig(),
        )
        incumbent_duration = sum(
            float(np.sum(segment.scored_duration_s[available_before >= 3.0 * incumbent_theta]))
            for segment in job.segments
            for available_before in (
                np.concatenate(
                    (
                        np.array([float(np.sum(segment.pre_roll_duration_s))]),
                        float(np.sum(segment.pre_roll_duration_s)) + np.cumsum(segment.scored_duration_s[:-1]),
                    )
                ),
            )
        )
        if incumbent_duration < MIN_EFFECTIVE_DURATION_S:
            continue
        fit = _fit_result(snapshot, family, f"first-600s-r{revision}")
        if not isinstance(fit, GreyFitSuccess):
            continue
        boundary = _fit_boundary(snapshot, fit)
        if boundary.effective_duration_s >= MIN_EFFECTIVE_DURATION_S:
            return boundary
    raise AssertionError(f"no immutable corpus prefix reached {MIN_EFFECTIVE_DURATION_S:g} effective seconds")


def _forecast_observation(
    *,
    plant: GrillSim,
    sequence: int,
    on_seconds: int,
) -> FrameObservation:
    normalized_load = (on_seconds / FRAME_SECONDS) / _U_MAX
    return FrameObservation(
        frame_start_s=float(sequence * FRAME_SECONDS),
        frame_end_s=float((sequence + 1) * FRAME_SECONDS),
        temp_c=plant.measured(),
        setpoint_c=TARGET_C,
        ambient_c=plant.T_amb,
        requested_q=normalized_load,
        realized_q=normalized_load,
        baseline_q=normalized_load,
        allocated_q=normalized_load,
        requested_auger_duty=on_seconds / FRAME_SECONDS,
        scheduled_on_s=float(on_seconds),
        delivered_on_s=float(on_seconds),
        realized_auger_duty=on_seconds / FRAME_SECONDS,
        requested_fan_duty=None,
        actual_fan_duty=1.0,
        allocator_revision=2,
        allocation_clamp_reasons=(AllocationClampReason.NONE,),
        result_revision=sequence + 1,
        output_source=OutputSource.CONTROLLER.value,
        lid_open=False,
        safety_inhibited=False,
        manual_override=False,
        stale=False,
        skipped=False,
        reset=False,
        continuous=True,
        role_generation=0,
        observation_sequence=sequence,
        probe_source="short-cook-held-out-simulator-probe",
        ambient_source=AmbientSource.CONFIGURED,
        ambient_uncertainty=AmbientUncertainty.UNMEASURED,
        temperature_band="held-out",
    )


def _forecast_scores(
    plant_type: type[GrillSim],
    fit: GreyFitSuccess,
) -> tuple[
    tuple[tuple[int, float], ...],
    tuple[str, ...],
    float,
    tuple[tuple[int, int], ...],
    int,
]:
    completed_windows: list[CompletedForecastOrigin] = []
    horizon_wins = {horizon_seconds: 0 for horizon_seconds in MPC_FORECAST_HORIZON_SECONDS}
    whole_wins = 0
    evaluation_blockers: list[str] = []
    for seed in HELD_OUT_SEEDS:
        plant = plant_type(seed=seed)
        grill = FakeGrillPlatform(dc_fan=True)
        candidate = Controller(_controller_config(fit), "C", dict(_CYCLE))
        incumbent = Controller(_controller_config(), "C", dict(_CYCLE))
        candidate.set_target(TARGET_C)
        incumbent.set_target(TARGET_C)
        candidate_digest = candidate.active_control_pair.descriptor.model_digest
        incumbent_digest = incumbent.active_control_pair.descriptor.model_digest
        evaluator = CausalForecastEvaluator(role_generation=0, candidate_generation=1)
        completed: list[CompletedForecastOrigin] = []
        decision = None
        try:
            for index in range(_FORECAST_PRIMER_FRAMES):
                shared_temperature = plant.measured()
                candidate.update(shared_temperature)
                incumbent.update(shared_temperature)
                on_seconds = (4, 10, 16)[(index // 8) % 3]
                _apply_frame(plant, grill, on_seconds=on_seconds)
                applied = AppliedOutput(
                    ratio=on_seconds / FRAME_SECONDS,
                    requested=on_seconds / FRAME_SECONDS,
                    source=OutputSource.CONTROLLER,
                    timestamp=float((index + 1) * FRAME_SECONDS),
                )
                candidate.set_output(applied)
                incumbent.set_output(applied)
            post_primer_temperature = plant.measured()
            candidate.update(post_primer_temperature)
            incumbent.update(post_primer_temperature)

            first_sequence = seed * 1_000
            for offset in range(max(horizon.observation_frames for horizon in MPC_FORECAST_HORIZONS) + 1):
                _apply_frame(plant, grill, on_seconds=_FORECAST_ON_SECONDS)
                observation = _forecast_observation(
                    plant=plant,
                    sequence=first_sequence + offset,
                    on_seconds=_FORECAST_ON_SECONDS,
                )
                completed.extend(evaluator.observe(observation))

                applied = AppliedOutput(
                    ratio=_FORECAST_ON_SECONDS / FRAME_SECONDS,
                    requested=_FORECAST_ON_SECONDS / FRAME_SECONDS,
                    source=OutputSource.CONTROLLER,
                    timestamp=observation.frame_end_s,
                )
                candidate.set_output(applied)
                incumbent.set_output(applied)
                candidate.update(observation.temp_c)
                incumbent.update(observation.temp_c)
                candidate_adapter = GreyBoxPredictionAdapter.from_estimator(
                    candidate.active_control_pair.core.estimator,
                    config=candidate.active_control_pair.core.config,
                )
                incumbent_adapter = GreyBoxPredictionAdapter.from_estimator(
                    incumbent.active_control_pair.core.estimator,
                    config=incumbent.active_control_pair.core.config,
                )

                def predict(
                    adapter: GreyBoxPredictionAdapter,
                    origin: CausalForecastInput,
                ) -> float:
                    prediction_steps = origin.prediction_steps
                    frame = origin.frame
                    forecast = adapter.forecast(
                        np.full(prediction_steps, frame.realized_q, dtype=np.float64),
                        np.full(prediction_steps, frame.ambient_c, dtype=np.float64),
                    )
                    return float(forecast[-1])

                for horizon in MPC_FORECAST_HORIZONS:
                    origin = paired_forecast_origin(
                        observation,
                        horizon=horizon,
                        candidate_generation=1,
                        incumbent_digest=incumbent_digest,
                        challenger_digest=candidate_digest,
                        incumbent_predict=lambda value, adapter=incumbent_adapter: predict(adapter, value),
                        challenger_predict=lambda value, adapter=candidate_adapter: predict(adapter, value),
                    )
                    assert origin is not None
                    evaluator.register(origin)

                if set(MPC_FORECAST_HORIZON_SECONDS) <= {row.horizon_seconds for row in completed}:
                    decision = evaluate_forecasts(
                        tuple(completed),
                        role_generation=0,
                        candidate_generation=1,
                        prior_consecutive_wins=0,
                        config=EvaluationConfig(),
                    )
                    break
        finally:
            candidate.close()
            incumbent.close()

        assert decision is not None
        completed_windows.extend(decision.completed_origins)
        for blocker in decision.blockers:
            if blocker not in evaluation_blockers:
                evaluation_blockers.append(blocker)
        for score in decision.scores:
            horizon_wins[score.horizon_seconds] += int(score.challenger_rmse_c < score.incumbent_rmse_c)
        candidate_whole_rmse = float(
            np.sqrt(np.mean([row.challenger_error_c**2 for row in decision.completed_origins]))
        )
        incumbent_whole_rmse = float(np.sqrt(np.mean([row.incumbent_error_c**2 for row in decision.completed_origins])))
        whole_wins += int(candidate_whole_rmse < incumbent_whole_rmse)

    horizon_ratios = []
    for horizon_seconds in MPC_FORECAST_HORIZON_SECONDS:
        rows = [row for row in completed_windows if row.horizon_seconds == horizon_seconds]
        candidate_rmse = float(np.sqrt(np.mean([row.challenger_error_c**2 for row in rows])))
        incumbent_rmse = float(np.sqrt(np.mean([row.incumbent_error_c**2 for row in rows])))
        horizon_ratios.append((horizon_seconds, candidate_rmse / incumbent_rmse))
    candidate_whole_rmse = float(np.sqrt(np.mean([row.challenger_error_c**2 for row in completed_windows])))
    incumbent_whole_rmse = float(np.sqrt(np.mean([row.incumbent_error_c**2 for row in completed_windows])))
    return (
        tuple(horizon_ratios),
        tuple(evaluation_blockers),
        candidate_whole_rmse / incumbent_whole_rmse,
        tuple(horizon_wins.items()),
        whole_wins,
    )


def _closed_loop_metrics(
    plant_type: type[GrillSim],
    seed: int,
    fit: GreyFitSuccess | None,
) -> tuple[float, float]:
    plant = plant_type(seed=seed)
    grill = FakeGrillPlatform(dc_fan=True)
    controller = Controller(_controller_config(fit), "C", dict(_CYCLE))
    controller.set_target(TARGET_C)
    iae = 0.0
    overshoot = 0.0
    try:
        for index in range(PRE_ROLL_FRAMES):
            controller.update(plant.measured())
            on_seconds = round(PRE_ROLL_DUTY * FRAME_SECONDS)
            _apply_frame(plant, grill, on_seconds=on_seconds)
            controller.set_output(
                AppliedOutput(
                    ratio=on_seconds / FRAME_SECONDS,
                    requested=PRE_ROLL_DUTY,
                    source=OutputSource.CONTROLLER,
                    timestamp=float((index + 1) * FRAME_SECONDS),
                )
            )
        for frame_index in range(_CLOSED_LOOP_FRAMES):
            result = controller.update(plant.measured())
            requested = float(result["cycle_ratio"])
            on_seconds = round(min(max(requested, 0.0), _U_MAX) * FRAME_SECONDS)
            for second in range(FRAME_SECONDS):
                auger_on = second < on_seconds
                (grill.auger_on if auger_on else grill.auger_off)()
                plant.step(auger_on=auger_on, fan_frac=1.0)
                temperature = plant.measured()
                iae += abs(temperature - TARGET_C)
                overshoot = max(overshoot, temperature - TARGET_C)
            grill.auger_off()
            controller.set_output(
                AppliedOutput(
                    ratio=on_seconds / FRAME_SECONDS,
                    requested=requested,
                    source=OutputSource.CONTROLLER,
                    timestamp=float((PRE_ROLL_FRAMES + frame_index + 1) * FRAME_SECONDS),
                )
            )
    finally:
        controller.close()
    return iae, max(overshoot, 0.0)


def _score_candidate(plant_type: type[GrillSim], boundary: _FitBoundary) -> CandidateScore:
    (
        horizon_ratios,
        evaluation_blockers,
        whole_cook_ratio,
        horizon_wins,
        whole_cook_wins,
    ) = _forecast_scores(plant_type, boundary.fit)
    candidate_iae = 0.0
    incumbent_iae = 0.0
    candidate_overshoot = 0.0
    incumbent_overshoot = 0.0
    iae_wins = 0
    overshoot_wins = 0
    for seed in HELD_OUT_SEEDS:
        learned_iae, learned_overshoot = _closed_loop_metrics(plant_type, seed, boundary.fit)
        fallback_iae, fallback_overshoot = _closed_loop_metrics(plant_type, seed, None)
        candidate_iae += learned_iae
        incumbent_iae += fallback_iae
        candidate_overshoot += learned_overshoot
        incumbent_overshoot += fallback_overshoot
        iae_wins += int(learned_iae < fallback_iae)
        overshoot_wins += int(learned_overshoot < fallback_overshoot)
    return CandidateScore(
        raw_counts=tuple(item.scored_count for item in boundary.snapshot.identity.slices),
        effective_duration_s=boundary.effective_duration_s,
        warmup_excluded_segment_ids=boundary.warmup_excluded_segment_ids,
        horizon_ratios=horizon_ratios,
        evaluation_blockers=evaluation_blockers,
        whole_cook_ratio=whole_cook_ratio,
        closed_loop_iae_ratio=candidate_iae / incumbent_iae,
        candidate_overshoot_c=candidate_overshoot,
        incumbent_overshoot_c=incumbent_overshoot,
        horizon_wins=horizon_wins,
        whole_cook_wins=whole_cook_wins,
        closed_loop_iae_wins=iae_wins,
        overshoot_wins=overshoot_wins,
    )


def seed_short_cook_corpus(
    repository: LearningTrajectoryRepository,
    plant_type: type[GrillSim],
    family: str,
) -> str:
    """Persist the deterministic qualifying campaign into an existing repository."""

    cooks = tuple(_collect_cook(plant_type, family, seed) for seed in TRAINING_SEEDS)
    partition_digest, _ = _persist_cooks(repository, cooks, family)
    return partition_digest


def run_short_cook_campaign(plant_type: type[GrillSim], family: str) -> ShortCookCampaignResult:
    cooks = tuple(_collect_cook(plant_type, family, seed) for seed in TRAINING_SEEDS)
    repository = LearningTrajectoryRepository()
    partition_digest, full_revision = _persist_cooks(repository, cooks, family)
    first = _first_600_second_boundary(repository, partition_digest, full_revision, family)
    full_snapshot = repository.snapshot_fit_corpus(partition_digest, through_revision=full_revision)
    full = _fit_boundary(full_snapshot, _fit_snapshot(full_snapshot, family, "full-177"))
    return ShortCookCampaignResult(
        dwell=tuple(cook.dwell for cook in cooks),
        first_600s=_score_candidate(plant_type, first),
        full_177=_score_candidate(plant_type, full),
    )
