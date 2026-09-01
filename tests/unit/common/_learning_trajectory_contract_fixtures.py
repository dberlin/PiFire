"""Shared strict learning-trajectory contract fixtures."""

from __future__ import annotations
from dataclasses import replace

from hashlib import sha256
from typing import Any

from common.control_trace import TRACE_SCHEMA_VERSION
from common.learning_trajectory import (
    TRAJECTORY_OBSERVATION_SCHEMA_VERSION,
    FitCorpusIdentity,
    FitCorpusSlice,
    HoldEntrySample,
    LearningTrajectoryFrame,
    LearningTrajectorySegment,
    TrajectoryBreakReason,
    canonical_fit_corpus_digest,
)
from tests.fakes.current_contracts import current_trajectory_frame

_UNSET_BOUNDARY = object()


def _digest(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


def _frame(
    sequence: int,
    *,
    role_generation: int = 4,
    start_ms: int | None = None,
    end_ms: int | None = None,
    partial: bool = False,
    boundary_reason: object = _UNSET_BOUNDARY,
    **overrides: Any,
) -> LearningTrajectoryFrame:
    frame = current_trajectory_frame(
        sequence,
        role_generation=role_generation,
        start_ms=start_ms,
        end_ms=end_ms,
        partial=partial,
    )
    if boundary_reason is not _UNSET_BOUNDARY:
        overrides["boundary_reason"] = boundary_reason
    return replace(frame, **overrides)


def _hold_entry(at_ms: int = 40_000) -> HoldEntrySample:
    return HoldEntrySample(
        monotonic_ms=at_ms,
        wall_ms=1_000_000 + at_ms,
        chamber_temperature_c=112.0,
        probe_valid=True,
        probe_source="grill-probe-1",
    )


def _segment(**overrides: Any) -> LearningTrajectorySegment:
    pre_roll_frames = overrides.pop("pre_roll_frames", (_frame(0), _frame(1)))
    scored_hold_frames = overrides.pop("scored_hold_frames", (_frame(2), _frame(3)))
    all_frames = (*pre_roll_frames, *scored_hold_frames)
    if not all_frames:
        raise AssertionError("the test segment requires at least one frame")
    hold_entry = overrides.pop(
        "hold_entry",
        _hold_entry(
            scored_hold_frames[0].monotonic_start_ms if scored_hold_frames else pre_roll_frames[-1].monotonic_end_ms
        ),
    )
    values: dict[str, Any] = {
        "schema_version": 1,
        "observation_schema_version": TRAJECTORY_OBSERVATION_SCHEMA_VERSION,
        "segment_id": "segment-1",
        "cook_id": "cook-1",
        "trajectory_session_id": "trajectory-session-1",
        "trace_session_ids": ("trace-session-1",),
        "collection_provenance": {
            "origin": "passive-online",
            "incumbent_generation": 2,
            "candidate_generation": 3,
            "role_generation": 4,
            "C_c": 8_500.0,
            "K_Q": 0.18,
            "theta": 45.0,
        },
        "configuration_provenance": {"controller": "MPC", "revision": 7},
        "cadence_digest": _digest("cadence-20-seconds-v1"),
        "model_structure_digest": _digest("grey-one-zone-erlang-v1"),
        "held_physics_digest": _digest("held-grey-physics-v1"),
        "delay_input_mapping_digest": _digest("normalized-combustion-load-v1"),
        "actuation_mapping_digest": _digest("framed-pulse-v1"),
        "scored_fan_regime_digest": _digest("fan-regime-v1"),
        "ambient_semantics_digest": _digest("ambient-configured-celsius-v1"),
        "pre_roll_frames": pre_roll_frames,
        "hold_entry": hold_entry,
        "scored_hold_frames": scored_hold_frames,
        "generation_audit_ranges": (
            {
                "start_sequence": all_frames[0].sequence,
                "end_sequence": all_frames[-1].sequence,
                "role_generation": 4,
            },
        ),
        "start_monotonic_ms": all_frames[0].monotonic_start_ms,
        "end_monotonic_ms": all_frames[-1].monotonic_end_ms,
        "start_wall_ms": all_frames[0].wall_start_ms,
        "end_wall_ms": all_frames[-1].wall_end_ms,
        "start_sequence": all_frames[0].sequence,
        "end_sequence": all_frames[-1].sequence,
        "pre_roll_end_reason": None,
        "terminal_break_reason": TrajectoryBreakReason.STOP,
        "state": "finalized",
        "source_trace_digest": _digest("source-trace"),
        "source_schema_version": TRACE_SCHEMA_VERSION,
        "source_row_digest": _digest("source-rows"),
        "build_provenance": {"builder": "trajectory-runtime", "revision": 1},
    }
    values.update(overrides)
    return LearningTrajectorySegment(**values)


def _slice(
    segment_id: str = "segment-1",
    *,
    through_ordinal: int = 3,
    pre_roll_count: int = 2,
    scored_count: int = 2,
    prefix_digest: str | None = None,
    segment_content_digest: str | None = None,
) -> FitCorpusSlice:
    return FitCorpusSlice(
        segment_id=segment_id,
        through_ordinal=through_ordinal,
        prefix_digest=prefix_digest or _digest(f"{segment_id}:{through_ordinal}"),
        segment_content_digest=segment_content_digest or _digest(f"{segment_id}:content"),
        pre_roll_count=pre_roll_count,
        scored_count=scored_count,
    )


def _corpus_identity(
    *,
    slices: tuple[FitCorpusSlice, ...] = (_slice(),),
    schema_version: int = 2,
    corpus_revision: int = 4,
    fit_partition_digest: str | None = None,
) -> FitCorpusIdentity:
    partition = fit_partition_digest or _segment().fit_partition_digest
    return FitCorpusIdentity(
        schema_version=schema_version,
        corpus_revision=corpus_revision,
        fit_partition_digest=partition,
        slices=slices,
        corpus_digest=canonical_fit_corpus_digest(
            schema_version=schema_version,
            corpus_revision=corpus_revision,
            fit_partition_digest=partition,
            slices=slices,
        ),
    )
