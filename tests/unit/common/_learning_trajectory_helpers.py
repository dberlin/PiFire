"""Shared learning-trajectory fixtures for unit tests."""

from __future__ import annotations

from hashlib import sha256

from common.learning_trajectory import (
    FrameDeliveryCertainty,
    HoldEntrySample,
    LearningTrajectoryFrame,
    LearningTrajectorySegment,
    TrajectoryBreakReason,
)
from common.persistence.learning_trajectory import (
    FinalizeReceipt,
    LearningTrajectoryRepository,
)

_FRAME_MS = 20_000
_WALL_EPOCH_MS = 1_700_000_000_000


def _digest(label: str) -> str:
    return sha256(label.encode()).hexdigest()


def _frame(
    sequence: int,
    *,
    epoch_ms: int = 0,
    effective_mode: str = "Hold",
    temperature_offset: float = 0.0,
) -> LearningTrajectoryFrame:
    start_ms = epoch_ms + sequence * _FRAME_MS
    end_ms = start_ms + _FRAME_MS
    return LearningTrajectoryFrame(
        sequence=sequence,
        monotonic_start_ms=start_ms,
        monotonic_end_ms=end_ms,
        wall_start_ms=_WALL_EPOCH_MS + start_ms,
        wall_end_ms=_WALL_EPOCH_MS + end_ms,
        chamber_temperature_c=110.0 + temperature_offset + sequence / 100.0,
        temperature_sample_monotonic_ms=end_ms,
        temperature_sample_wall_ms=_WALL_EPOCH_MS + end_ms,
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
    )


def _hold_entry(frame: LearningTrajectoryFrame) -> HoldEntrySample:
    return HoldEntrySample(
        monotonic_ms=frame.monotonic_start_ms,
        wall_ms=frame.wall_start_ms,
        chamber_temperature_c=frame.chamber_temperature_c,
        probe_valid=True,
        probe_source="grill-probe-1",
    )


def _segment(
    segment_id: str,
    *,
    epoch_ms: int = 0,
    start_sequence: int = 0,
    pre_roll_count: int = 1,
    scored_count: int = 0,
    state: str = "open",
) -> LearningTrajectorySegment:
    if pre_roll_count + scored_count == 0:
        raise AssertionError("test segments must contain at least one frame")
    pre_roll = tuple(
        _frame(sequence, epoch_ms=epoch_ms, effective_mode="Smoke")
        for sequence in range(start_sequence, start_sequence + pre_roll_count)
    )
    scored_start = start_sequence + pre_roll_count
    scored = tuple(_frame(sequence, epoch_ms=epoch_ms) for sequence in range(scored_start, scored_start + scored_count))
    all_frames = (*pre_roll, *scored)
    hold_entry = _hold_entry(scored[0]) if scored else None
    return LearningTrajectorySegment(
        schema_version=1,
        observation_schema_version=2,
        segment_id=segment_id,
        cook_id=f"cook-{segment_id}",
        trajectory_session_id=f"trajectory-{segment_id}",
        trace_session_ids=(f"trace-{segment_id}",),
        collection_provenance={"origin": "passive-online", "role_generation": 4},
        configuration_provenance={"controller": "MPC", "revision": 7},
        cadence_digest=_digest("cadence-20-seconds-v1"),
        model_structure_digest=_digest("grey-one-zone-erlang-v1"),
        held_physics_digest=_digest("held-grey-physics-v1"),
        delay_input_mapping_digest=_digest("normalized-combustion-load-v1"),
        actuation_mapping_digest=_digest("framed-pulse-v1"),
        scored_fan_regime_digest=_digest("fan-regime-v1"),
        ambient_semantics_digest=_digest("ambient-configured-celsius-v1"),
        pre_roll_frames=pre_roll,
        hold_entry=hold_entry,
        scored_hold_frames=scored,
        generation_audit_ranges=(
            {
                "start_sequence": all_frames[0].sequence,
                "end_sequence": all_frames[-1].sequence,
                "role_generation": 4,
            },
        ),
        start_monotonic_ms=all_frames[0].monotonic_start_ms,
        end_monotonic_ms=all_frames[-1].monotonic_end_ms,
        start_wall_ms=all_frames[0].wall_start_ms,
        end_wall_ms=all_frames[-1].wall_end_ms,
        start_sequence=all_frames[0].sequence,
        end_sequence=all_frames[-1].sequence,
        pre_roll_end_reason=None,
        terminal_break_reason=(None if state == "open" else TrajectoryBreakReason.STOP),
        state=state,
        source_trace_digest=_digest(f"source-trace-{segment_id}"),
        source_schema_version=7,
        source_row_digest=_digest(f"source-rows-{segment_id}"),
        build_provenance={"builder": "trajectory-runtime", "revision": 1},
    )


def _finalize_segment(
    repository: LearningTrajectoryRepository,
    segment: LearningTrajectorySegment,
) -> FinalizeReceipt:
    cursor = repository.begin_segment(segment)
    return repository.finalize(cursor, TrajectoryBreakReason.STOP)
