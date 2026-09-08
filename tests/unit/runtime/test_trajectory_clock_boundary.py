from __future__ import annotations

import logging
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import NoReturn

import pytest
from pydantic import ValidationError

from common.learning_trajectory import (
    FrameDeliveryCertainty,
    HoldEntrySample,
    LearningTrajectoryFrame,
    LearningTrajectorySegment,
    TrajectoryBreakReason,
)
from common.persistence.learning_trajectory import LearningTrajectoryRepository
from controller.model_learning.contracts import FrameObservation
from controller.mpc_model import replay_delay_chain
from controller.runtime.actuation_delivery import DeliveredActuationIntegral
from controller.runtime.learning_trajectory import LearningTrajectoryRuntime, ModeEntered, ModeExited, ThermalSample
from controller.runtime.model_persistence import ModelPersistenceWorker
from tests.unit.common._learning_trajectory_helpers import _segment


@pytest.mark.parametrize("jump_ms", [-3_600_000, 3_600_000])
def test_current_trajectory_wall_jump_round_trip_preserves_physical_replay(tmp_path: Path, jump_ms: int) -> None:
    original = _segment("clock-boundary", pre_roll_count=0, scored_count=2)
    first, second = original.scored_hold_frames
    first = replace(
        first,
        wall_end_ms=first.wall_end_ms + jump_ms,
        temperature_sample_wall_ms=first.temperature_sample_wall_ms + jump_ms,
    )
    second = replace(
        second,
        wall_start_ms=second.wall_start_ms + jump_ms,
        wall_end_ms=second.wall_end_ms + jump_ms,
        temperature_sample_wall_ms=second.temperature_sample_wall_ms + jump_ms,
    )
    current = replace(
        original,
        observation_schema_version=4,
        scored_hold_frames=(first, second),
        end_wall_ms=second.wall_end_ms,
    )
    path = str(tmp_path / "trajectory.db")
    repository = LearningTrajectoryRepository(path)
    repository.begin_segment(current)
    reopened = LearningTrajectoryRepository(path).read_segment(current.segment_id)
    assert reopened is not None
    assert reopened == current
    assert reopened.content_digest == current.content_digest
    assert replay_delay_chain(
        reopened.scored_hold_frames, theta=20.0, n_delay=2, initial_load=0.0
    ) == replay_delay_chain(original.scored_hold_frames, theta=20.0, n_delay=2, initial_load=0.0)


def _historical_segment(observation_version: int, *, count: int = 1) -> LearningTrajectorySegment:
    """Literal v2/v3 input owned by these historical admission/migration tests."""
    frames = tuple(
        {
            "sequence": index,
            "monotonic_start_ms": index * 20_000,
            "monotonic_end_ms": (index + 1) * 20_000,
            "wall_start_ms": 1_700_000_000_000 + index * 20_000,
            "wall_end_ms": 1_700_000_000_000 + (index + 1) * 20_000,
            "chamber_temperature_c": 110.0,
            "temperature_sample_monotonic_ms": (index + 1) * 20_000,
            "temperature_sample_wall_ms": 1_700_000_000_000 + (index + 1) * 20_000,
            "temperature_sample_age_ms": 0,
            "temperature_sample_wall_age_ms": 0,
            "temperature_sample_clock_skew_ms": 0,
            "source_temperature_units": "C",
            "settings_revision": 7,
            "probe_valid": True,
            "probe_source": "grill-probe-1",
            "ambient_temperature_c": 24.0,
            "ambient_source": "configured",
            "ambient_uncertainty_c": 1.5,
            "delivered_auger_on_seconds": 8.0,
            "realized_auger_duty": 0.4,
            "normalized_combustion_load": 0.4,
            "delivered_fan_on_seconds": 20.0,
            "fan_duty_integral_seconds": 10.0,
            "mean_actual_fan_duty": 0.5,
            "auger_delivery_certainty": FrameDeliveryCertainty.EXACT,
            "fan_delivery_certainty": FrameDeliveryCertainty.EXACT,
            "effective_mode": "Hold",
            "recipe_step_id": None,
            "complete": True,
            "continuous": True,
            "partial": False,
            "boundary_reason": None,
            "role_generation": 4,
        }
        for index in range(count)
    )
    return LearningTrajectorySegment(
        schema_version=1,
        observation_schema_version=observation_version,
        segment_id="historical",
        cook_id="historical-cook",
        trajectory_session_id="historical-trajectory",
        trace_session_ids=("historical-trace",),
        collection_provenance={"origin": "passive-online", "role_generation": 4},
        configuration_provenance={"controller": "MPC", "revision": 7},
        cadence_digest="a" * 64,
        model_structure_digest="b" * 64,
        held_physics_digest="c" * 64,
        delay_input_mapping_digest="d" * 64,
        actuation_mapping_digest="e" * 64,
        scored_fan_regime_digest="f" * 64,
        ambient_semantics_digest="1" * 64,
        pre_roll_frames=(),
        hold_entry=HoldEntrySample(
            monotonic_ms=0,
            wall_ms=1_700_000_000_000,
            chamber_temperature_c=110.0,
            probe_valid=True,
            probe_source="grill-probe-1",
        ),
        scored_hold_frames=tuple(LearningTrajectoryFrame(**frame) for frame in frames),
        generation_audit_ranges=({"start_sequence": 0, "end_sequence": count - 1, "role_generation": 4},),
        start_monotonic_ms=0,
        end_monotonic_ms=count * 20_000,
        start_wall_ms=1_700_000_000_000,
        end_wall_ms=1_700_000_000_000 + count * 20_000,
        start_sequence=0,
        end_sequence=count - 1,
        pre_roll_end_reason=None,
        terminal_break_reason=None,
        state="open",
        source_trace_digest="2" * 64,
        source_schema_version=9,
        source_row_digest="3" * 64,
        build_provenance={"builder": "historical-runtime"},
    )


@pytest.mark.parametrize("historical_version", [2, 3])
def test_historical_segment_still_rejects_independent_wall_duration(historical_version: int) -> None:
    original = _historical_segment(historical_version)
    frame = original.scored_hold_frames[0]
    changed = replace(
        frame,
        wall_end_ms=frame.wall_end_ms - 3_600_000,
        temperature_sample_wall_ms=frame.temperature_sample_wall_ms - 3_600_000,
    )
    with pytest.raises(ValidationError, match="wall and monotonic frame durations must agree"):
        replace(original, scored_hold_frames=(changed,), end_wall_ms=changed.wall_end_ms)


def test_current_trajectory_keeps_monotonic_contiguity_requirement() -> None:
    original = replace(_segment("gap", pre_roll_count=0, scored_count=2), observation_schema_version=4)
    first, second = original.scored_hold_frames
    shifted = replace(
        second,
        monotonic_start_ms=second.monotonic_start_ms + 1,
        monotonic_end_ms=second.monotonic_end_ms + 1,
        temperature_sample_monotonic_ms=second.temperature_sample_monotonic_ms + 1,
    )
    with pytest.raises(ValidationError, match="contiguous"):
        replace(original, scored_hold_frames=(first, shifted), end_monotonic_ms=shifted.monotonic_end_ms)


_WALL_MS = 1_700_000_000_000


class _Journal:
    """Explicit physical intervals; absent delivery cannot become exact."""

    def __init__(self) -> None:
        self.intervals: dict[tuple[int, int], DeliveredActuationIntegral] = {}

    def add(self, start_ms: int, end_ms: int, on_seconds: float) -> None:
        seconds = (end_ms - start_ms) / 1_000
        self.intervals[start_ms, end_ms] = DeliveredActuationIntegral(
            monotonic_start_ms=start_ms,
            monotonic_end_ms=end_ms,
            auger_on_seconds=on_seconds,
            fan_on_seconds=seconds,
            fan_duty_integral_seconds=seconds * 0.5,
            auger_start_active=False,
            auger_end_active=False,
            fan_start_active=True,
            fan_end_active=True,
            pwm_start=0.5,
            pwm_end=0.5,
            auger_certainty=FrameDeliveryCertainty.EXACT,
            fan_certainty=FrameDeliveryCertainty.EXACT,
            unknown_reasons=(),
        )

    def integrate(self, start_ms: int, end_ms: int) -> DeliveredActuationIntegral:
        return self.intervals[start_ms, end_ms]


class _NoModelWrites:
    def save_outcome(self, name: str, snapshot: dict[str, object]) -> NoReturn:
        raise AssertionError("trajectory capture must not write a model")


def _entered(mode: str, at_ms: int, wall_ms: int) -> ModeEntered:
    template = _segment("clock-runtime")
    return ModeEntered(
        effective_mode=mode,
        persisted_mode=mode,
        monotonic_ms=at_ms,
        wall_ms=wall_ms,
        cook_id=template.cook_id,
        trajectory_session_id=template.trajectory_session_id,
        trace_session_id="00000000-0000-4000-8000-000000000001",
        recipe_step_id=None,
        units="C",
        settings_revision=7,
        collection_provenance={"role_generation": 4},
        configuration_provenance={"controller": "MPC"},
        cadence_digest=template.cadence_digest,
        model_structure_digest=template.model_structure_digest,
        held_physics_digest=template.held_physics_digest,
        delay_input_mapping_digest=template.delay_input_mapping_digest,
        actuation_mapping_digest=template.actuation_mapping_digest,
        scored_fan_regime_digest=template.scored_fan_regime_digest,
        ambient_semantics_digest=template.ambient_semantics_digest,
        source_trace_digest=template.source_trace_digest,
        source_schema_version=10,
        source_row_digest=template.source_row_digest,
        build_provenance={"builder": "trajectory-clock-regression"},
    )


def _sample(at_ms: int, wall_ms: int) -> ThermalSample:
    return ThermalSample(
        monotonic_ms=at_ms,
        wall_ms=wall_ms,
        chamber_temperature=110.0,
        units="C",
        probe_valid=True,
        probe_source="grill-probe-1",
        ambient_temperature=25.0,
        ambient_source="configured",
        ambient_uncertainty=1.5,
        settings_revision=7,
        recipe_step_id=None,
    )


def _observation(start_ms: int, wall_start_ms: int, wall_end_ms: int) -> FrameObservation:
    return FrameObservation(
        frame_start_s=start_ms / 1_000,
        frame_end_s=(start_ms + 20_000) / 1_000,
        wall_start_ms=wall_start_ms,
        wall_end_ms=wall_end_ms,
        temp_c=111.0,
        setpoint_c=120.0,
        ambient_c=25.0,
        requested_q=0.25,
        realized_q=0.25,
        requested_auger_duty=0.25,
        delivered_on_s=5.0,
        requested_fan_duty=0.5,
        actual_fan_duty=0.5,
        result_revision=1,
        output_source="mpc",
        lid_open=False,
        safety_inhibited=False,
        manual_override=False,
        stale=False,
        skipped=False,
        reset=False,
        continuous=True,
        role_generation=4,
        observation_sequence=1,
        probe_valid=True,
        probe_source="grill-probe-1",
    )


@pytest.mark.parametrize("jump_ms", [-3_600_000, 3_600_000])
def test_smoke_partial_exit_and_hold_capture_use_independent_wall_samples(tmp_path: Path, jump_ms: int) -> None:
    path = str(tmp_path / "runtime.db")
    repository = LearningTrajectoryRepository(path)
    worker = ModelPersistenceWorker(_NoModelWrites(), logging.getLogger(__name__), trajectory_repository=repository)
    journal = _Journal()
    journal.add(0, 20_000, 5.0)
    journal.add(20_000, 30_000, 2.0)
    runtime = LearningTrajectoryRuntime(
        journal=journal,
        persistence=worker,
        segment_id_factory=lambda: "clock-runtime",
        trajectory_session_id_factory=lambda: "00000000-0000-4000-8000-000000000002",
    )
    published: list[LearningTrajectorySegment] = []

    def publish(segment: LearningTrajectorySegment) -> bool:
        published.append(segment)
        return True

    try:
        runtime.mode_entered(_entered("Smoke", 0, _WALL_MS))
        assert runtime.bind_trace_session("00000000-0000-4000-8000-000000000001", "cook-clock-runtime", publish)
        runtime.observe_temperature(_sample(20_000, _WALL_MS + 20_000 + jump_ms))
        assert runtime.barrier()
        # The probe sample precedes a second independent correction at the exit.
        runtime.observe_temperature(_sample(29_975, _WALL_MS + 29_975 + jump_ms))
        exit_wall_ms = _WALL_MS + 30_000 - jump_ms
        runtime.mode_exited(ModeExited("Smoke", "Hold", 30_000, exit_wall_ms))
        runtime.mode_entered(_entered("Hold", 30_000, exit_wall_ms))
        runtime.observe_temperature(_sample(30_025, exit_wall_ms + 25))
        seed = runtime.seed_for(5.0, 2, 30_025, 110.0)
        assert seed.status == "exact"
        runtime.observe_temperature(_sample(49_975, exit_wall_ms + 19_975))
        frame_wall_end_ms = _WALL_MS + 50_000 + jump_ms
        observation = _observation(30_000, exit_wall_ms, frame_wall_end_ms)
        assert runtime.observe_hold_frame(observation)
        assert not runtime.observe_hold_frame(observation)
        assert runtime.barrier()

        cold = LearningTrajectoryRepository(path).read_segment("clock-runtime")
        assert cold is not None
        full, partial = cold.pre_roll_frames
        scored = cold.scored_hold_frames[0]
        assert (full.monotonic_start_ms, full.monotonic_end_ms) == (0, 20_000)
        assert (full.wall_start_ms, full.wall_end_ms) == (_WALL_MS, _WALL_MS + 20_000 + jump_ms)
        assert (partial.monotonic_start_ms, partial.monotonic_end_ms) == (20_000, 30_000)
        assert (partial.wall_start_ms, partial.wall_end_ms) == (full.wall_end_ms, exit_wall_ms)
        assert partial.partial and partial.boundary_reason is TrajectoryBreakReason.MODE_TRANSITION
        assert partial.temperature_sample_age_ms == 25
        assert partial.temperature_sample_clock_skew_ms == -2 * jump_ms
        assert scored.temperature_sample_age_ms == 25
        assert scored.temperature_sample_wall_age_ms == 25 + 2 * jump_ms
        assert scored.wall_end_ms == frame_wall_end_ms
        assert [item.delivered_auger_on_seconds for item in (*cold.pre_roll_frames, scored)] == [5.0, 2.0, 5.0]
        assert seed.delay_states == replay_delay_chain(cold.pre_roll_frames, theta=5.0, n_delay=2, initial_load=0.25)
        assert published[-1].content_digest == cold.content_digest
        assert cold.trajectory_session_id == "00000000-0000-4000-8000-000000000002"
    finally:
        assert runtime.close()


@pytest.mark.parametrize("jump_ms", [-3_600_000, 3_600_000])
@pytest.mark.parametrize("first_frame_start_s", [30.250, 100.0008])
def test_pidsp_setup_gap_splits_smoke_history_before_durable_hold_capture(
    tmp_path: Path, jump_ms: int, first_frame_start_s: float
) -> None:
    path = str(tmp_path / "setup-gap.db")
    worker = ModelPersistenceWorker(
        _NoModelWrites(),
        logging.getLogger(__name__),
        trajectory_repository=LearningTrajectoryRepository(path),
    )
    journal = _Journal()
    journal.add(0, 20_000, 5.0)
    journal.add(20_000, 30_000, 2.0)
    segment_ids = iter(("smoke-history", "hold-after-setup"))
    runtime = LearningTrajectoryRuntime(
        journal=journal,
        persistence=worker,
        segment_id_factory=lambda: next(segment_ids),
    )
    published: list[LearningTrajectorySegment] = []

    def publish(segment: LearningTrajectorySegment) -> bool:
        published.append(segment)
        return True

    try:
        runtime.mode_entered(replace(_entered("Smoke", 0, _WALL_MS), configuration_provenance={"controller": "PID-SP"}))
        runtime.observe_temperature(_sample(20_000, _WALL_MS + 20_000))
        runtime.observe_temperature(_sample(29_975, _WALL_MS + 29_975))
        exit_wall_ms = _WALL_MS + 30_000 + jump_ms
        runtime.mode_exited(ModeExited("Smoke", "Hold", 30_000, exit_wall_ms))
        entry_wall_ms = _WALL_MS + 30_050 - jump_ms
        runtime.mode_entered(
            replace(
                _entered("Hold", 30_050, entry_wall_ms),
                configuration_provenance={"controller": "PID-SP"},
            )
        )
        # The real setup sample and first pulse follow both mode boundaries.
        runtime.observe_temperature(_sample(30_125, entry_wall_ms + 75))
        assert runtime.bind_trace_session("00000000-0000-4000-8000-000000000001", "cook-clock-runtime", publish)
        assert runtime.barrier()
        seed = runtime.seed_for(20.0, 2, 30_125, 110.0)
        assert seed.status == "uncertain"
        assert seed.delay_states == ()

        start_ms = round(first_frame_start_s * 1_000)
        end_ms = round((first_frame_start_s + 20.0) * 1_000)
        first_wall_ms = _WALL_MS + start_ms - jump_ms
        first_end_wall_ms = _WALL_MS + end_ms + jump_ms
        sample_wall_ms = first_wall_ms + 19_975
        runtime.observe_temperature(_sample(end_ms - 25, sample_wall_ms))
        observation = replace(
            _observation(start_ms, first_wall_ms, first_end_wall_ms),
            frame_start_s=first_frame_start_s,
            frame_end_s=first_frame_start_s + 20.0,
            output_source="pidsp",
        )
        assert runtime.observe_hold_frame(observation)
        assert runtime.barrier()
        seed = runtime.seed_for(20.0, 2, end_ms, 111.0)
        assert seed.status == "short"
        assert seed.pre_roll_frame_count == 1

        second_end_wall_ms = _WALL_MS + end_ms + 20_000 - jump_ms
        assert runtime.observe_hold_frame(
            replace(
                _observation(end_ms, first_end_wall_ms, second_end_wall_ms),
                frame_start_s=observation.frame_end_s,
                frame_end_s=observation.frame_end_s + 20.0,
                output_source="pidsp",
                observation_sequence=2,
            )
        )
        assert runtime.barrier()
        status = runtime.status()
        assert status.enabled and not status.gap
        assert status.last_error is None
        assert status.scored_count == 2
        assert status.last_break_reason is TrajectoryBreakReason.RECORDER_GAP

        cold_repository = LearningTrajectoryRepository(path)
        smoke = cold_repository.read_segment("smoke-history")
        hold = cold_repository.read_segment("hold-after-setup")
        assert smoke is not None and hold is not None
        assert smoke.terminal_break_reason is TrajectoryBreakReason.RECORDER_GAP
        assert smoke.scored_hold_frames == ()
        assert [(frame.monotonic_start_ms, frame.monotonic_end_ms) for frame in smoke.pre_roll_frames] == [
            (0, 20_000),
            (20_000, 30_000),
        ]
        assert [frame.delivered_auger_on_seconds for frame in smoke.pre_roll_frames] == [5.0, 2.0]
        assert smoke.end_wall_ms == exit_wall_ms
        assert hold.pre_roll_frames == ()
        first, second = hold.scored_hold_frames
        assert (first.monotonic_start_ms, first.monotonic_end_ms) == (start_ms, end_ms)
        assert first.monotonic_start_ms > smoke.end_monotonic_ms
        assert second.monotonic_start_ms == first.monotonic_end_ms
        assert (first.wall_start_ms, first.wall_end_ms) == (first_wall_ms, first_end_wall_ms)
        assert first.temperature_sample_monotonic_ms == end_ms - 25
        assert first.temperature_sample_wall_ms == sample_wall_ms
        assert first.temperature_sample_age_ms == 25
        assert first.temperature_sample_clock_skew_ms == 2 * jump_ms
        assert hold.hold_entry is not None
        assert (hold.hold_entry.monotonic_ms, hold.hold_entry.wall_ms) == (end_ms - 25, sample_wall_ms)
        assert [frame.delivered_auger_on_seconds for frame in hold.scored_hold_frames] == [5.0, 5.0]
        assert published[-1].content_digest == hold.content_digest
    finally:
        assert runtime.close()


def test_cold_corpus_order_follows_durable_capture_not_wall_sort(tmp_path: Path) -> None:
    path = str(tmp_path / "ordered.db")
    repository = LearningTrajectoryRepository(path)
    first = replace(
        _segment("first", epoch_ms=100_000, pre_roll_count=0, scored_count=1),
        observation_schema_version=4,
        cook_id="shared-cook",
    )
    second = replace(
        _segment("second", pre_roll_count=0, scored_count=1),
        observation_schema_version=4,
        cook_id="shared-cook",
    )
    for segment in (first, second):
        repository.finalize(repository.begin_segment(segment), TrajectoryBreakReason.STOP)
    cold = LearningTrajectoryRepository(path)
    assert [item.segment_id for item in cold.read_cook_segments("shared-cook")] == ["first", "second"]
    corpus = cold.snapshot_fit_corpus(first.fit_partition_digest)
    assert [item.segment_id for item in corpus.identity.slices] == ["first", "second"]


@pytest.mark.parametrize("historical_version", [2, 3])
def test_clock_schema_migration_preserves_historical_canonical_rows(tmp_path: Path, historical_version: int) -> None:
    path = str(tmp_path / "historical.db")
    original = _historical_segment(historical_version)
    repository = LearningTrajectoryRepository(path)
    repository.begin_segment(original)
    with sqlite3.connect(path) as connection:
        before = connection.execute("SELECT * FROM learning_trajectory_frame").fetchall()
        frame_ddl = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='learning_trajectory_frame'"
        ).fetchone()[0]
        # Restore the actual v12 constraint and registry boundary, not payload bytes.
        connection.execute("ALTER TABLE learning_trajectory_frame RENAME TO historical_frames")
        connection.execute(frame_ddl.replace("IN (2, 3, 4)", "IN (2, 3)"))
        connection.execute("INSERT INTO learning_trajectory_frame SELECT * FROM historical_frames")
        connection.execute("DROP TABLE historical_frames")
        connection.execute(
            "CREATE INDEX ix_learning_frame_revision ON learning_trajectory_frame"
            "(segment_id, created_corpus_revision, ordinal)"
        )
        connection.execute("DELETE FROM _sqlite_migrations WHERE name='v0013_trajectory_clock_domains'")
        connection.execute("PRAGMA user_version=12")
    cold = LearningTrajectoryRepository(path)
    assert cold.read_segment(original.segment_id) == original
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (13,)
        assert connection.execute("SELECT * FROM learning_trajectory_frame").fetchall() == before


@pytest.mark.parametrize("historical_version", [2, 3])
def test_historical_segment_keeps_wall_order_and_anchor_mapping(historical_version: int) -> None:
    original = _historical_segment(historical_version, count=2)
    first, second = original.scored_hold_frames
    rollback = replace(
        second,
        wall_start_ms=second.wall_start_ms - 3_600_000,
        wall_end_ms=second.wall_end_ms - 3_600_000,
        temperature_sample_wall_ms=second.temperature_sample_wall_ms - 3_600_000,
    )
    with pytest.raises(ValidationError, match="wall intervals overlap"):
        replace(original, scored_hold_frames=(first, rollback), end_wall_ms=rollback.wall_end_ms)
    assert original.hold_entry is not None
    with pytest.raises(ValidationError, match="wall and monotonic offsets must agree"):
        replace(original, hold_entry=replace(original.hold_entry, wall_ms=original.hold_entry.wall_ms + 1))


def test_current_partial_boundary_does_not_authorize_unobserved_physical_gap() -> None:
    original = replace(_segment("partial-gap", pre_roll_count=1, scored_count=1), observation_schema_version=4)
    pre_roll = original.pre_roll_frames[0]
    partial = replace(
        pre_roll,
        monotonic_end_ms=10_000,
        wall_end_ms=pre_roll.wall_start_ms + 10_000,
        temperature_sample_monotonic_ms=10_000,
        temperature_sample_wall_ms=pre_roll.wall_start_ms + 10_000,
        delivered_auger_on_seconds=4.0,
        delivered_fan_on_seconds=10.0,
        fan_duty_integral_seconds=5.0,
        complete=False,
        partial=True,
        boundary_reason=TrajectoryBreakReason.MODE_TRANSITION,
    )
    with pytest.raises(ValidationError, match="pre-roll and scored frames must be contiguous"):
        replace(original, pre_roll_frames=(partial,))


def test_current_wall_correction_between_frames_does_not_define_order() -> None:
    original = replace(_segment("inter-frame", pre_roll_count=0, scored_count=2), observation_schema_version=4)
    first, second = original.scored_hold_frames
    rollback = replace(
        second,
        wall_start_ms=second.wall_start_ms - 3_600_000,
        wall_end_ms=second.wall_end_ms - 3_600_000,
        temperature_sample_wall_ms=second.temperature_sample_wall_ms - 3_600_000,
    )
    admitted = replace(original, scored_hold_frames=(first, rollback), end_wall_ms=rollback.wall_end_ms)
    assert tuple(frame.sequence for frame in admitted.scored_hold_frames) == (0, 1)
    assert admitted.end_monotonic_ms - admitted.start_monotonic_ms == 40_000


def test_live_seed_refuses_gap_between_buffered_exact_intervals(tmp_path: Path) -> None:
    worker = ModelPersistenceWorker(
        _NoModelWrites(),
        logging.getLogger(__name__),
        trajectory_repository=LearningTrajectoryRepository(str(tmp_path / "gap.db")),
    )
    journal = _Journal()
    journal.add(0, 20_000, 5.0)
    journal.add(40_000, 60_000, 5.0)
    runtime = LearningTrajectoryRuntime(journal=journal, persistence=worker)
    try:
        runtime.mode_entered(_entered("Smoke", 0, _WALL_MS))
        runtime.observe_temperature(_sample(20_000, _WALL_MS + 20_000))
        # Re-entry does not observe the skipped physical interval [20s, 40s).
        runtime.mode_entered(_entered("Smoke", 40_000, _WALL_MS + 40_000))
        runtime.observe_temperature(_sample(60_000, _WALL_MS + 60_000))
        runtime.mode_entered(_entered("Hold", 60_000, _WALL_MS + 60_000))
        runtime.observe_temperature(_sample(60_025, _WALL_MS + 60_025))
        seed = runtime.seed_for(10.0, 2, 60_025, 110.0)
        assert seed.status == "uncertain"
        assert seed.delay_states == ()
    finally:
        assert runtime.close()
