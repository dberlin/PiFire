from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass, replace
from hashlib import sha256
from math import inf, nan
from typing import Any

import pytest

from common.control_trace import AmbientSource, AmbientUncertainty
from common.learning_trajectory import (
    FrameDeliveryCertainty,
    LearningTrajectoryFrame,
    TrajectoryBreakReason,
)
from common.persistence.learning_trajectory import SegmentCursor
from controller.model_learning.contracts import FrameObservation
from controller.runtime.actuation_delivery import DeliveredActuationIntegral
from controller.runtime.learning_trajectory import (
    LearningTrajectoryRuntime,
    ModeEntered,
    ModeExited,
    ThermalSample,
    TrajectoryBoundary,
    TrajectoryStatus,
)
from controller.runtime.model_persistence import (
    TrajectoryAppendBatch,
    TrajectoryPersistenceGap,
)

_FRAME_MS = 20_000
# ControlMode sleeps for 50 ms between normal samples; timestamps are persisted at
# millisecond precision, so one interval plus one quantization millisecond is valid.
_SAMPLE_AGE_LIMIT_MS = 51
_WALL_OFFSET_MS = 1_700_000_000_000


def _digest(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


class _SegmentIds:
    def __init__(self, start: int = 1) -> None:
        self.next_value = start

    def __call__(self) -> str:
        value = f"segment-{self.next_value}"
        self.next_value += 1
        return value


class _Journal:
    """Deterministic observation-only journal double; it has no actuator API."""

    def __init__(self) -> None:
        self.integrals: dict[tuple[int, int], DeliveredActuationIntegral] = {}
        self.calls: list[tuple[int, int]] = []

    def set_exact(
        self,
        start_ms: int,
        end_ms: int,
        *,
        auger_on_s: float = 0.0,
        fan_on_s: float | None = None,
        fan_duty_integral_s: float | None = None,
    ) -> None:
        duration_s = (end_ms - start_ms) / 1_000
        fan_on = duration_s if fan_on_s is None else fan_on_s
        fan_integral = fan_on if fan_duty_integral_s is None else fan_duty_integral_s
        self.integrals[(start_ms, end_ms)] = DeliveredActuationIntegral(
            monotonic_start_ms=start_ms,
            monotonic_end_ms=end_ms,
            auger_on_seconds=auger_on_s,
            fan_on_seconds=fan_on,
            fan_duty_integral_seconds=fan_integral,
            auger_start_active=False,
            auger_end_active=False,
            fan_start_active=fan_on > 0.0,
            fan_end_active=fan_on > 0.0,
            pwm_start=0.0 if fan_on == 0.0 else fan_integral / fan_on,
            pwm_end=0.0 if fan_on == 0.0 else fan_integral / fan_on,
            auger_certainty=FrameDeliveryCertainty.EXACT,
            fan_certainty=FrameDeliveryCertainty.EXACT,
            unknown_reasons=(),
        )

    def set_unknown(self, start_ms: int, end_ms: int, reason: str) -> None:
        self.set_exact(start_ms, end_ms)
        exact = self.integrals[(start_ms, end_ms)]
        self.integrals[(start_ms, end_ms)] = DeliveredActuationIntegral(
            monotonic_start_ms=exact.monotonic_start_ms,
            monotonic_end_ms=exact.monotonic_end_ms,
            auger_on_seconds=exact.auger_on_seconds,
            fan_on_seconds=exact.fan_on_seconds,
            fan_duty_integral_seconds=exact.fan_duty_integral_seconds,
            auger_start_active=exact.auger_start_active,
            auger_end_active=exact.auger_end_active,
            fan_start_active=exact.fan_start_active,
            fan_end_active=exact.fan_end_active,
            pwm_start=exact.pwm_start,
            pwm_end=exact.pwm_end,
            auger_certainty=FrameDeliveryCertainty.UNKNOWN,
            fan_certainty=exact.fan_certainty,
            unknown_reasons=(reason,),
        )

    def integrate(self, start_ms: int, end_ms: int) -> DeliveredActuationIntegral:
        self.calls.append((start_ms, end_ms))
        integral = self.integrals.get((start_ms, end_ms))
        if integral is None:
            self.set_exact(start_ms, end_ms)
            integral = self.integrals[(start_ms, end_ms)]
        return integral


@dataclass(slots=True)
class _Receipt:
    accepted: bool
    durable: bool
    cursor: SegmentCursor | None
    error: str | None = None
    gap: TrajectoryPersistenceGap | None = None
    def complete(
        self,
        *,
        durable: bool,
        error: str | None = None,
    ) -> None:
        self.completed = True
        self.durable = durable
        self.error = error

    completed: bool = True

    def wait(self, timeout: float | None = None) -> bool:
        del timeout
        return self.durable


class _Persistence:
    """Immediate deterministic worker double with real append-batch boundaries."""

    def __init__(self) -> None:
        self.batches: list[TrajectoryAppendBatch] = []
        self.reject_next: str | None = None
        self.barrier_calls: list[float] = []
        self.close_calls: list[float] = []
        self.delay_next = False
        self.pending_receipts: list[_Receipt] = []
        self.barrier_completion: tuple[bool, str | None] | None = None
        self.close_result = True
        self.close_error: BaseException | None = None
        self.revision = 0

    def submit_trajectory_batch(self, batch: TrajectoryAppendBatch) -> _Receipt:
        assert isinstance(batch, TrajectoryAppendBatch)
        self.batches.append(batch)
        if self.reject_next is not None:
            error = self.reject_next
            self.reject_next = None
            return _Receipt(
                accepted=False,
                durable=False,
                cursor=None,
                error=error,
                gap=TrajectoryPersistenceGap(reason=error),
            )

        self.revision += 1
        cursor = batch.cursor
        if batch.begin_segment is not None:
            segment = batch.begin_segment
            cursor = SegmentCursor(
                segment_id=segment.segment_id,
                next_ordinal=len(segment.pre_roll_frames) + len(segment.scored_hold_frames),
                chain_digest=_digest(f"{segment.segment_id}:{self.revision}"),
                corpus_revision=self.revision,
            )
        elif batch.next_segment is not None:
            segment = batch.next_segment
            cursor = SegmentCursor(
                segment_id=segment.segment_id,
                next_ordinal=len(segment.pre_roll_frames) + len(segment.scored_hold_frames),
                chain_digest=_digest(f"{segment.segment_id}:{self.revision}"),
                corpus_revision=self.revision,
            )
        elif cursor is not None and (batch.pre_roll or batch.scored):
            cursor = SegmentCursor(
                segment_id=cursor.segment_id,
                next_ordinal=cursor.next_ordinal + len(batch.pre_roll) + len(batch.scored),
                chain_digest=_digest(f"{cursor.segment_id}:{self.revision}"),
                corpus_revision=self.revision,
            )
        receipt = _Receipt(
            accepted=True,
            durable=not self.delay_next,
            cursor=cursor,
            completed=not self.delay_next,
        )
        if self.delay_next:
            self.delay_next = False
            self.pending_receipts.append(receipt)
        return receipt

    def barrier(self, timeout: float = 2.0) -> bool:
        self.barrier_calls.append(timeout)
        if self.pending_receipts and self.barrier_completion is not None:
            durable, error = self.barrier_completion
            self.barrier_completion = None
            self.complete_next(durable=durable, error=error)
        return True

    def close(self, timeout: float = 2.0) -> bool:
        self.close_calls.append(timeout)
        if self.close_error is not None:
            raise self.close_error
        return self.close_result
    def complete_next(
        self,
        *,
        durable: bool = True,
        error: str | None = None,
    ) -> None:
        self.pending_receipts.pop(0).complete(durable=durable, error=error)



def _entered(
    mode: str,
    *,
    at_ms: int = 0,
    cook_id: str = "cook-1",
    persisted_mode: str | None = None,
    recipe_step_id: str | None = None,
    units: str = "C",
    settings_revision: int = 7,
) -> ModeEntered:
    return ModeEntered(
        effective_mode=mode,
        persisted_mode=mode if persisted_mode is None else persisted_mode,
        monotonic_ms=at_ms,
        wall_ms=_WALL_OFFSET_MS + at_ms,
        cook_id=cook_id,
        trajectory_session_id="trajectory-session",
        trace_session_id=f"trace-{cook_id}",
        recipe_step_id=recipe_step_id,
        units=units,
        settings_revision=settings_revision,
        collection_provenance={"origin": "passive-online", "role_generation": 4},
        configuration_provenance={"controller": "MPC", "revision": settings_revision},
        cadence_digest=_digest("cadence-20-seconds-v1"),
        model_structure_digest=_digest("grey-one-zone-erlang-v1"),
        held_physics_digest=_digest("held-grey-physics-v1"),
        delay_input_mapping_digest=_digest("normalized-combustion-load-v1"),
        actuation_mapping_digest=_digest("framed-pulse-v1"),
        scored_fan_regime_digest=_digest("fan-regime-v1"),
        ambient_semantics_digest=_digest("ambient-configured-v1"),
        source_trace_digest=_digest(f"source-{cook_id}"),
        source_schema_version=7,
        source_row_digest=_digest(f"rows-{cook_id}"),
        build_provenance={"builder": "trajectory-runtime", "revision": 1},
    )


def _exited(
    mode: str,
    next_mode: str,
    at_ms: int,
    *,
    reason: TrajectoryBreakReason | None = None,
) -> ModeExited:
    return ModeExited(
        effective_mode=mode,
        next_effective_mode=next_mode,
        monotonic_ms=at_ms,
        wall_ms=_WALL_OFFSET_MS + at_ms,
        reason=reason,
    )


def _sample(
    at_ms: int,
    temperature: float | None = 110.0,
    *,
    units: str = "C",
    valid: bool = True,
    ambient: float = 25.0,
    settings_revision: int = 7,
    wall_skew_ms: int = 0,
    recipe_step_id: str | None = None,
) -> ThermalSample:
    return ThermalSample(
        monotonic_ms=at_ms,
        wall_ms=_WALL_OFFSET_MS + at_ms + wall_skew_ms,
        chamber_temperature=temperature,
        units=units,
        probe_valid=valid,
        probe_source="grill-probe-1" if valid else None,
        ambient_temperature=ambient,
        ambient_source="configured",
        ambient_uncertainty=1.5,
        settings_revision=settings_revision,
        recipe_step_id=recipe_step_id,
    )


def _boundary(
    reason: TrajectoryBreakReason,
    at_ms: int,
    detail: str | None = None,
    *,
    replacement_mode: ModeEntered | None = None,
) -> TrajectoryBoundary:
    return TrajectoryBoundary(
        reason=reason,
        monotonic_ms=at_ms,
        wall_ms=_WALL_OFFSET_MS + at_ms,
        detail=detail or reason.value,
        replacement_mode=replacement_mode,
    )


def _hold_frame(
    sequence: int,
    *,
    start_ms: int = 0,
    temp_c: float = 110.0,
    delivered_on_s: float = 5.0,
) -> FrameObservation:
    end_ms = start_ms + _FRAME_MS
    return FrameObservation(
        frame_start_s=start_ms / 1_000,
        frame_end_s=end_ms / 1_000,
        temp_c=temp_c,
        setpoint_c=120.0,
        ambient_c=25.0,
        requested_q=0.25,
        realized_q=delivered_on_s / 20.0,
        requested_auger_duty=0.25,
        delivered_on_s=delivered_on_s,
        requested_fan_duty=0.5,
        actual_fan_duty=0.5,
        result_revision=sequence,
        output_source="mpc",
        lid_open=False,
        safety_inhibited=False,
        manual_override=False,
        stale=False,
        skipped=False,
        reset=False,
        continuous=True,
        role_generation=4,
        observation_sequence=sequence,
        probe_valid=True,
        probe_source="grill-probe-1",
        ambient_source=AmbientSource.CONFIGURED,
        ambient_uncertainty=AmbientUncertainty.UNMEASURED,
    )


def _runtime(
    *,
    journal: _Journal | None = None,
    persistence: _Persistence | None = None,
    ids: _SegmentIds | None = None,
) -> tuple[LearningTrajectoryRuntime, _Journal, _Persistence]:
    owned_journal = journal or _Journal()
    owned_persistence = persistence or _Persistence()
    runtime = LearningTrajectoryRuntime(
        journal=owned_journal,
        persistence=owned_persistence,
        segment_id_factory=ids or _SegmentIds(),
        sample_age_limit_ms=_SAMPLE_AGE_LIMIT_MS,
    )
    return runtime, owned_journal, owned_persistence


def _segments(persistence: _Persistence) -> list[Any]:
    return [
        segment
        for batch in persistence.batches
        for segment in (batch.begin_segment, batch.next_segment)
        if segment is not None
    ]


def _pre_roll_frames(persistence: _Persistence) -> list[LearningTrajectoryFrame]:
    frames: list[LearningTrajectoryFrame] = []
    for batch in persistence.batches:
        if batch.begin_segment is not None:
            frames.extend(batch.begin_segment.pre_roll_frames)
        if batch.next_segment is not None:
            frames.extend(batch.next_segment.pre_roll_frames)
        frames.extend(batch.pre_roll)
    return frames


def _scored_frames(persistence: _Persistence) -> list[LearningTrajectoryFrame]:
    frames: list[LearningTrajectoryFrame] = []
    for batch in persistence.batches:
        if batch.begin_segment is not None:
            frames.extend(batch.begin_segment.scored_hold_frames)
        if batch.next_segment is not None:
            frames.extend(batch.next_segment.scored_hold_frames)
        frames.extend(batch.scored)
    return frames


def _finalize_reasons(persistence: _Persistence) -> list[TrajectoryBreakReason]:
    return [
        reason
        for batch in persistence.batches
        for reason in (batch.finalize_reason, batch.break_reason)
        if reason is not None
    ]


def test_smoke_closes_exact_twenty_second_delivered_auger_and_fan_integrals() -> None:
    runtime, journal, persistence = _runtime()
    journal.set_exact(
        0,
        _FRAME_MS,
        auger_on_s=7.5,
        fan_on_s=12.0,
        fan_duty_integral_s=6.0,
    )

    runtime.mode_entered(_entered("Smoke"))
    runtime.observe_temperature(_sample(_FRAME_MS, 111.0))

    (frame,) = _pre_roll_frames(persistence)
    assert journal.calls == [(0, _FRAME_MS)]
    assert frame.monotonic_start_ms == 0
    assert frame.monotonic_end_ms == _FRAME_MS
    assert frame.delivered_auger_on_seconds == pytest.approx(7.5)
    assert frame.realized_auger_duty == pytest.approx(0.375)
    assert frame.delivered_fan_on_seconds == pytest.approx(12.0)
    assert frame.fan_duty_integral_seconds == pytest.approx(6.0)
    assert frame.mean_actual_fan_duty == pytest.approx(0.3)
    assert frame.effective_mode == "Smoke"
    assert frame.complete is True
    assert frame.partial is False


def test_temperature_uses_actual_prior_sample_timestamp_age_and_never_interpolates() -> None:
    runtime, _journal, persistence = _runtime()
    runtime.mode_entered(_entered("Smoke"))
    runtime.observe_temperature(_sample(_FRAME_MS - _SAMPLE_AGE_LIMIT_MS, 123.25))
    runtime.observe_temperature(_sample(_FRAME_MS + 1, 999.0))

    (frame,) = _pre_roll_frames(persistence)
    assert frame.chamber_temperature_c == pytest.approx(123.25)
    assert frame.temperature_sample_monotonic_ms == _FRAME_MS - _SAMPLE_AGE_LIMIT_MS
    assert frame.temperature_sample_wall_ms == _WALL_OFFSET_MS + _FRAME_MS - _SAMPLE_AGE_LIMIT_MS
    assert frame.temperature_sample_age_ms == _SAMPLE_AGE_LIMIT_MS
    assert frame.source_temperature_units == "C"
    assert frame.settings_revision == 7

    stale_runtime, _journal, stale_persistence = _runtime()
    stale_runtime.mode_entered(_entered("Smoke"))
    stale_runtime.observe_temperature(_sample(_FRAME_MS - _SAMPLE_AGE_LIMIT_MS - 1, 88.0))
    stale_runtime.observe_temperature(_sample(_FRAME_MS + 1, 200.0))

    assert _pre_roll_frames(stale_persistence) == []
    assert stale_runtime.status().last_break_reason is TrajectoryBreakReason.PROBE_GAP


@pytest.mark.parametrize(
    ("temperature", "valid"),
    [
        pytest.param(None, False, id="missing"),
        pytest.param(nan, True, id="nan"),
        pytest.param(inf, True, id="infinite"),
        pytest.param(110.0, False, id="invalid-flag"),
    ],
)
def test_invalid_nonfinite_or_missing_probe_finalizes_without_last_value_fill(
    temperature: float | None,
    valid: bool,
) -> None:
    runtime, _journal, persistence = _runtime()
    runtime.mode_entered(_entered("Smoke"))
    runtime.observe_temperature(_sample(1_000, 100.0))
    runtime.observe_temperature(_sample(2_000, temperature, valid=valid))
    runtime.observe_temperature(_sample(_FRAME_MS, 140.0))

    assert _pre_roll_frames(persistence) == []
    assert runtime.status().last_break_reason is TrajectoryBreakReason.PROBE_GAP


def test_compatible_smoke_to_hold_keeps_one_segment_with_partial_tail_and_exact_anchor() -> None:
    runtime, journal, persistence = _runtime()
    journal.set_exact(0, _FRAME_MS, auger_on_s=5.0)
    journal.set_exact(_FRAME_MS, 40_000, auger_on_s=6.0)
    journal.set_exact(40_000, 55_000, auger_on_s=3.0)

    runtime.mode_entered(_entered("Smoke"))
    runtime.observe_temperature(_sample(_FRAME_MS, 100.0))
    runtime.observe_temperature(_sample(40_000, 105.0))
    runtime.observe_temperature(_sample(54_975, 106.0))
    runtime.mode_exited(_exited("Smoke", "Hold", 55_000))
    runtime.mode_entered(_entered("Hold", at_ms=55_000))
    runtime.observe_temperature(_sample(55_025, 106.5))
    runtime.observe_temperature(_sample(74_975, 108.0))
    runtime.observe_hold_frame(_hold_frame(1, start_ms=55_000, temp_c=108.0))

    segments = _segments(persistence)
    assert {segment.segment_id for segment in segments} == {"segment-1"}
    pre_roll = _pre_roll_frames(persistence)
    assert len(pre_roll) == 3
    assert pre_roll[-1].monotonic_start_ms == 40_000
    assert pre_roll[-1].monotonic_end_ms == 55_000
    assert pre_roll[-1].partial is True
    assert pre_roll[-1].complete is False
    assert pre_roll[-1].boundary_reason is TrajectoryBreakReason.MODE_TRANSITION
    assert _finalize_reasons(persistence) == []

    scored_batches = [batch for batch in persistence.batches if batch.scored]
    assert len(scored_batches) == 1
    anchor = scored_batches[0].hold_entry
    assert anchor is not None
    assert anchor.monotonic_ms == 55_025
    assert anchor.wall_ms == _WALL_OFFSET_MS + 55_025
    assert scored_batches[0].scored[0].temperature_sample_monotonic_ms == 74_975
    assert scored_batches[0].scored[0].temperature_sample_age_ms == 25
    assert anchor.chamber_temperature_c == pytest.approx(106.5)
    assert len(scored_batches[0].scored) == 1
    assert scored_batches[0].scored[0].sequence == 3


def test_pure_hold_begins_without_pre_roll_and_anchors_first_valid_measurement() -> None:
    runtime, _journal, persistence = _runtime()
    runtime.mode_entered(_entered("Hold"))
    runtime.observe_temperature(_sample(25, 109.5))
    runtime.observe_temperature(_sample(19_975, 111.0))
    runtime.observe_hold_frame(_hold_frame(1, temp_c=111.0))

    (segment,) = _segments(persistence)
    assert segment.pre_roll_frames == ()
    assert segment.hold_entry is not None
    assert segment.hold_entry.monotonic_ms == 25
    assert segment.hold_entry.chamber_temperature_c == pytest.approx(109.5)
    assert len(segment.scored_hold_frames) == 1
    assert segment.scored_hold_frames[0].sequence == 0


@pytest.mark.parametrize(
    ("exit_reason", "expected_reason"),
    [
        pytest.param(TrajectoryBreakReason.STOP, TrajectoryBreakReason.STOP, id="stop"),
        pytest.param(TrajectoryBreakReason.ERROR, TrajectoryBreakReason.ERROR, id="error"),
    ],
)
def test_smoke_only_terminal_exit_records_unscored_tail_without_hold_anchor(
    exit_reason: TrajectoryBreakReason,
    expected_reason: TrajectoryBreakReason,
) -> None:
    runtime, journal, persistence = _runtime()
    journal.set_exact(0, _FRAME_MS, auger_on_s=4.0)
    journal.set_exact(_FRAME_MS, 27_000, auger_on_s=1.0)
    runtime.mode_entered(_entered("Smoke"))
    runtime.observe_temperature(_sample(_FRAME_MS, 100.0))
    runtime.observe_temperature(_sample(26_975, 101.0))

    runtime.mode_exited(_exited("Smoke", exit_reason.value.title(), 27_000, reason=exit_reason))

    pre_roll = _pre_roll_frames(persistence)
    assert len(pre_roll) == 2
    assert pre_roll[-1].partial is True
    assert pre_roll[-1].boundary_reason is expected_reason
    assert _scored_frames(persistence) == []
    assert all(segment.hold_entry is None for segment in _segments(persistence))
    assert expected_reason in _finalize_reasons(persistence)


def test_hold_completed_frame_is_scored_once_and_generic_temperature_grid_is_not_duplicated() -> None:
    runtime, _journal, persistence = _runtime()
    runtime.mode_entered(_entered("Hold"))
    runtime.observe_temperature(_sample(10, 109.0))
    runtime.observe_temperature(_sample(_FRAME_MS, 110.0))
    before = len(persistence.batches)
    observation = _hold_frame(17, temp_c=110.0)

    runtime.observe_hold_frame(observation)
    runtime.observe_hold_frame(observation)

    scored = _scored_frames(persistence)
    assert len(persistence.batches) == before + 1
    assert len(scored) == 1
    assert scored[0].sequence == 0
    assert scored[0].effective_mode == "Hold"
    assert _pre_roll_frames(persistence) == []


_NONTERMINAL_BOUNDARIES = (
    TrajectoryBreakReason.MANUAL,
    TrajectoryBreakReason.LID_OPEN,
    TrajectoryBreakReason.SAFETY,
    TrajectoryBreakReason.RESET,
    TrajectoryBreakReason.COOK_ROTATED,
    TrajectoryBreakReason.HISTORY_CLEARED,
    TrajectoryBreakReason.RECORDER_GAP,
    TrajectoryBreakReason.CLOCK_DISCONTINUITY,
    TrajectoryBreakReason.STRUCTURE_CHANGED,
    TrajectoryBreakReason.ACTUATION_MAPPING_CHANGED,
    TrajectoryBreakReason.FAN_MAPPING_CHANGED,
    TrajectoryBreakReason.AMBIENT_SEMANTICS_CHANGED,
)


@pytest.mark.parametrize("reason", _NONTERMINAL_BOUNDARIES, ids=lambda reason: reason.value)
def test_every_explicit_boundary_reason_appends_only_exact_tail_before_split(
    reason: TrajectoryBreakReason,
) -> None:
    runtime, _journal, persistence = _runtime()
    runtime.mode_entered(_entered("Smoke"))
    runtime.observe_temperature(_sample(_FRAME_MS, 100.0))
    runtime.observe_temperature(_sample(24_975, 100.5))
    old_segment_id = runtime.status().segment_id
    before = len(persistence.batches)

    boundary = _boundary(
        reason,
        25_000,
        replacement_mode=_entered("Smoke", at_ms=25_000, settings_revision=8)
        if reason
        in {
            TrajectoryBreakReason.STRUCTURE_CHANGED,
            TrajectoryBreakReason.ACTUATION_MAPPING_CHANGED,
            TrajectoryBreakReason.FAN_MAPPING_CHANGED,
            TrajectoryBreakReason.AMBIENT_SEMANTICS_CHANGED,
        }
        else None,
    )
    if reason in {
        TrajectoryBreakReason.STRUCTURE_CHANGED,
        TrajectoryBreakReason.ACTUATION_MAPPING_CHANGED,
        TrajectoryBreakReason.FAN_MAPPING_CHANGED,
        TrajectoryBreakReason.AMBIENT_SEMANTICS_CHANGED,
    }:
        runtime.configuration_changed(boundary)
    else:
        runtime.intervention(boundary)
    runtime.observe_temperature(_sample(45_000, 101.0))

    assert reason in _finalize_reasons(persistence)
    assert runtime.status().segment_id != old_segment_id
    old_appends = [
        batch
        for batch in persistence.batches[before:]
        if batch.cursor is not None
        and batch.cursor.segment_id == old_segment_id
        and (batch.pre_roll or batch.scored)
    ]
    assert len(old_appends) == 1
    boundary_batch = old_appends[0]
    assert boundary_batch.scored == ()
    assert len(boundary_batch.pre_roll) == 1
    boundary_tail = boundary_batch.pre_roll[0]
    assert boundary_tail.partial is True
    assert boundary_tail.complete is False
    assert boundary_tail.boundary_reason is reason
    assert boundary_tail.monotonic_end_ms == 25_000
    assert all(
        not batch.scored
        and not any(not frame.partial for frame in batch.pre_roll)
        for batch in old_appends
    )
    next_segments = [
        batch.next_segment
        for batch in persistence.batches[before:]
        if batch.next_segment is not None
    ]
    assert len(next_segments) == 1
    assert next_segments[0].segment_id == runtime.status().segment_id
    assert next_segments[0].segment_id != old_segment_id
    assert next_segments[0].pre_roll_frames[0].partial is False


@pytest.mark.parametrize("detail", ["manual-auger", "manual-fan", "manual-pwm"])
def test_manual_channels_lid_safety_and_uncertain_delivery_have_exact_breaks(detail: str) -> None:
    runtime, _journal, persistence = _runtime()
    runtime.mode_entered(_entered("Smoke"))
    runtime.observe_temperature(_sample(_FRAME_MS, 100.0))
    runtime.observe_temperature(_sample(20_975, 100.5))
    runtime.intervention(_boundary(TrajectoryBreakReason.MANUAL, 21_000, detail))
    assert runtime.status().last_break_reason is TrajectoryBreakReason.MANUAL
    runtime.observe_temperature(_sample(41_000, 101.0))
    assert TrajectoryBreakReason.MANUAL in _finalize_reasons(persistence)

    for reason in (TrajectoryBreakReason.LID_OPEN, TrajectoryBreakReason.SAFETY):
        next_runtime, _journal, next_persistence = _runtime()
        next_runtime.mode_entered(_entered("Smoke"))
        next_runtime.observe_temperature(_sample(_FRAME_MS, 100.0))
        next_runtime.observe_temperature(_sample(20_975, 100.5))
        next_runtime.intervention(_boundary(reason, 21_000))
        assert next_runtime.status().last_break_reason is reason
        next_runtime.observe_temperature(_sample(41_000, 101.0))
        assert reason in _finalize_reasons(next_persistence)

    uncertain_runtime, uncertain_journal, uncertain_persistence = _runtime()
    uncertain_journal.set_unknown(0, _FRAME_MS, "auger-readback-unavailable")
    uncertain_runtime.mode_entered(_entered("Smoke"))
    uncertain_runtime.observe_temperature(_sample(_FRAME_MS, 100.0))
    assert uncertain_runtime.status().last_break_reason is TrajectoryBreakReason.ACTUATION_UNKNOWN
    assert _pre_roll_frames(uncertain_persistence) == []


def test_clock_regression_is_detected_without_accepting_a_cross_epoch_frame() -> None:
    runtime, _journal, persistence = _runtime()
    runtime.mode_entered(_entered("Smoke", at_ms=1_000))
    runtime.observe_temperature(_sample(10_000, 100.0))
    runtime.observe_temperature(_sample(9_999, 101.0))
    runtime.observe_temperature(_sample(21_000, 102.0))

    assert runtime.status().last_break_reason is TrajectoryBreakReason.CLOCK_DISCONTINUITY
    assert _pre_roll_frames(persistence) == []


def test_recipe_uses_effective_smoke_hold_modes_and_hold_to_smoke_breaks_left_hold() -> None:
    runtime, journal, persistence = _runtime()
    journal.set_exact(0, _FRAME_MS, auger_on_s=4.0)
    journal.set_exact(_FRAME_MS, 30_000, auger_on_s=2.0)
    runtime.mode_entered(
        _entered("Smoke", persisted_mode="Recipe", recipe_step_id="step-smoke")
    )
    runtime.observe_temperature(_sample(_FRAME_MS, 100.0, recipe_step_id="step-smoke"))
    runtime.observe_temperature(_sample(29_975, 101.0, recipe_step_id="step-smoke"))
    runtime.mode_exited(_exited("Smoke", "Hold", 30_000))
    runtime.mode_entered(
        _entered(
            "Hold",
            at_ms=30_000,
            persisted_mode="Recipe",
            recipe_step_id="step-hold",
        )
    )
    runtime.observe_temperature(_sample(30_025, 102.0, recipe_step_id="step-hold"))
    runtime.observe_temperature(_sample(49_975, 103.0, recipe_step_id="step-hold"))
    runtime.observe_hold_frame(_hold_frame(1, start_ms=30_000, temp_c=103.0))
    first_segment_id = runtime.status().segment_id
    runtime.mode_exited(_exited("Hold", "Smoke", 50_000))
    runtime.mode_entered(
        _entered(
            "Smoke",
            at_ms=50_000,
            persisted_mode="Recipe",
            recipe_step_id="step-smoke-2",
        )
    )
    runtime.observe_temperature(_sample(70_000, 104.0, recipe_step_id="step-smoke-2"))

    assert _pre_roll_frames(persistence)[0].effective_mode == "Smoke"
    assert _pre_roll_frames(persistence)[0].recipe_step_id == "step-smoke"
    assert _scored_frames(persistence)[0].effective_mode == "Hold"
    assert TrajectoryBreakReason.LEFT_HOLD in _finalize_reasons(persistence)
    assert runtime.status().segment_id != first_segment_id


def test_celsius_fahrenheit_capture_parity_and_unit_change_split() -> None:
    c_runtime, _journal, c_persistence = _runtime()
    c_runtime.mode_entered(_entered("Smoke", units="C"))
    c_runtime.observe_temperature(_sample(_FRAME_MS, 100.0, units="C", ambient=25.0))

    f_runtime, _journal, f_persistence = _runtime()
    f_runtime.mode_entered(_entered("Smoke", units="F"))
    f_runtime.observe_temperature(_sample(_FRAME_MS, 212.0, units="F", ambient=77.0))

    c_frame = _pre_roll_frames(c_persistence)[0]
    f_frame = _pre_roll_frames(f_persistence)[0]
    assert f_frame.chamber_temperature_c == pytest.approx(c_frame.chamber_temperature_c)
    assert f_frame.ambient_temperature_c == pytest.approx(c_frame.ambient_temperature_c)
    assert c_frame.source_temperature_units == "C"
    assert f_frame.source_temperature_units == "F"

    old_id = c_runtime.status().segment_id
    c_runtime.observe_temperature(_sample(24_975, 100.5, units="C", ambient=25.0))
    c_runtime.configuration_changed(
        _boundary(
            TrajectoryBreakReason.UNITS_CHANGED,
            25_000,
            replacement_mode=_entered(
                "Smoke",
                at_ms=25_000,
                units="F",
                settings_revision=8,
            ),
        )
    )
    c_runtime.observe_temperature(_sample(45_000, 212.0, units="F", ambient=77.0, settings_revision=8))
    assert TrajectoryBreakReason.UNITS_CHANGED in _finalize_reasons(c_persistence)
    assert c_runtime.status().segment_id != old_id


def test_persistence_rejection_disables_learning_records_gap_and_never_commands_hardware() -> None:
    persistence = _Persistence()
    persistence.reject_next = "trajectory-queue-overflow"
    runtime, journal, _persistence = _runtime(persistence=persistence)
    hardware = {"auger": True, "fan": True, "pwm": 0.4}
    runtime.mode_entered(_entered("Smoke"))

    runtime.observe_temperature(_sample(_FRAME_MS, 100.0))
    calls_after_rejection = list(journal.calls)
    runtime.observe_temperature(_sample(40_000, 101.0))

    status = runtime.status()
    assert hardware == {"auger": True, "fan": True, "pwm": 0.4}
    assert status.enabled is False
    assert status.gap is True
    assert status.last_break_reason is TrajectoryBreakReason.RECORDER_GAP
    assert status.last_error == "trajectory-queue-overflow"
    assert journal.calls == calls_after_rejection
    assert len(persistence.batches) == 1

    runtime.mode_exited(
        _exited("Smoke", "Stop", 40_000, reason=TrajectoryBreakReason.STOP)
    )
    runtime.mode_entered(_entered("Smoke", at_ms=40_000, cook_id="cook-2"))
    runtime.observe_temperature(_sample(60_000, 102.0))

    assert len(persistence.batches) == 2
    assert runtime.status().enabled is True
    assert runtime.status().segment_id == "segment-2"


def test_process_restart_finalizes_unclean_and_new_epoch_cannot_seed_across() -> None:
    persistence = _Persistence()
    ids = _SegmentIds()
    runtime, _journal, _persistence = _runtime(persistence=persistence, ids=ids)
    runtime.mode_entered(_entered("Smoke"))
    runtime.observe_temperature(_sample(_FRAME_MS, 100.0))
    runtime.observe_temperature(_sample(24_975, 101.0))
    old_segment_id = runtime.status().segment_id
    runtime.intervention(_boundary(TrajectoryBreakReason.PROCESS_RESTART, 25_000))

    restarted, _journal, _persistence = _runtime(persistence=persistence, ids=ids)
    restarted.mode_entered(_entered("Hold", at_ms=0))
    restarted.observe_temperature(_sample(10, 105.0))
    restarted.observe_temperature(_sample(19_975, 106.0))
    restarted.observe_hold_frame(_hold_frame(1, temp_c=106.0))

    assert TrajectoryBreakReason.UNCLEAN_RESTART in _finalize_reasons(persistence)
    assert restarted.status().segment_id != old_segment_id
    restarted_segment = _segments(persistence)[-1]
    assert restarted_segment.pre_roll_frames == ()
    assert restarted_segment.hold_entry is not None
    assert restarted_segment.hold_entry.monotonic_ms == 10


def test_status_reports_logical_counts_segment_break_and_persistence_error() -> None:
    runtime, _journal, persistence = _runtime()
    runtime.mode_entered(_entered("Smoke"))
    runtime.observe_temperature(_sample(_FRAME_MS, 100.0))
    runtime.observe_temperature(_sample(40_000, 101.0))
    runtime.mode_exited(_exited("Smoke", "Hold", 40_000))
    runtime.mode_entered(_entered("Hold", at_ms=40_000))
    runtime.observe_temperature(_sample(40_010, 102.0))
    runtime.observe_temperature(_sample(59_975, 103.0))
    runtime.observe_hold_frame(_hold_frame(2, start_ms=40_000, temp_c=103.0))

    status = runtime.status()
    assert isinstance(status, TrajectoryStatus)
    assert status.enabled is True
    assert status.segment_id == "segment-1"
    assert status.pre_roll_count == 2
    assert status.scored_count == 1
    assert status.last_break_reason is None
    assert status.last_error is None
    assert status.gap is False

    persistence.reject_next = "sqlite-unavailable"
    runtime.observe_temperature(_sample(79_975, 104.0))
    runtime.observe_hold_frame(_hold_frame(3, start_ms=60_000, temp_c=104.0))
    failed = runtime.status()
    assert failed.enabled is False
    assert failed.pre_roll_count == 2
    assert failed.scored_count == 1
    assert failed.last_break_reason is TrajectoryBreakReason.RECORDER_GAP
    assert failed.last_error == "sqlite-unavailable"


def test_one_process_runtime_spans_modes_and_cooks_barriers_teardown_and_closes_once() -> None:
    runtime, _journal, persistence = _runtime(ids=_SegmentIds())
    runtime.mode_entered(_entered("Smoke", cook_id="cook-1"))
    runtime.observe_temperature(_sample(_FRAME_MS, 100.0))
    runtime.mode_exited(_exited("Smoke", "Stop", _FRAME_MS, reason=TrajectoryBreakReason.STOP))

    runtime.mode_entered(_entered("Smoke", cook_id="cook-2", at_ms=40_000))
    runtime.observe_temperature(_sample(60_000, 101.0))
    runtime.mode_exited(_exited("Smoke", "Stop", 60_000, reason=TrajectoryBreakReason.STOP))

    assert [segment.cook_id for segment in _segments(persistence)] == ["cook-1", "cook-2"]
    assert len({segment.segment_id for segment in _segments(persistence)}) == 2
    assert len(persistence.barrier_calls) == 2
    assert persistence.close_calls == []
    assert runtime.barrier(timeout=0.25) is True
    assert persistence.barrier_calls[-1] == 0.25

    runtime.close()
    runtime.close()
    assert persistence.close_calls == [2.0]


def test_runtime_events_and_status_are_frozen_owned_values() -> None:
    event = _entered("Smoke")
    runtime, _journal, _persistence = _runtime()
    runtime.mode_entered(event)
    status = runtime.status()

    with pytest.raises(FrozenInstanceError):
        event.effective_mode = "Hold"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        status.enabled = False  # type: ignore[misc]


def test_temperature_persists_independent_wall_age_and_clock_skew() -> None:
    runtime, _journal, persistence = _runtime()
    runtime.mode_entered(_entered("Smoke"))
    runtime.observe_temperature(
        _sample(
            _FRAME_MS - _SAMPLE_AGE_LIMIT_MS,
            123.0,
            wall_skew_ms=2,
        )
    )
    runtime.observe_temperature(_sample(_FRAME_MS + 1, 124.0, wall_skew_ms=2))

    (frame,) = _pre_roll_frames(persistence)
    assert frame.temperature_sample_age_ms == 51
    assert frame.temperature_sample_wall_age_ms == 49
    assert frame.temperature_sample_clock_skew_ms == -2


def test_boundary_stages_until_delayed_begin_receipt_owns_cursor() -> None:
    persistence = _Persistence()
    persistence.delay_next = True
    runtime, _journal, _persistence = _runtime(persistence=persistence)
    runtime.mode_entered(_entered("Smoke"))
    runtime.observe_temperature(_sample(_FRAME_MS, 100.0))
    segment_id = runtime.status().segment_id
    runtime.observe_temperature(_sample(24_975, 101.0))

    runtime.intervention(_boundary(TrajectoryBreakReason.MANUAL, 25_000))

    staged = runtime.status()
    assert staged.segment_id == segment_id
    assert staged.enabled is True
    persistence.complete_next()
    runtime.status()
    runtime.observe_temperature(_sample(45_000, 102.0))

    partials = [frame for frame in _pre_roll_frames(persistence) if frame.partial]
    assert len(partials) == 1
    assert partials[0].boundary_reason is TrajectoryBreakReason.MANUAL
    assert TrajectoryBreakReason.MANUAL in _finalize_reasons(persistence)
    assert runtime.status().segment_id != segment_id


def test_short_first_partial_can_begin_and_finalize_smoke_segment() -> None:
    runtime, _journal, persistence = _runtime()
    runtime.mode_entered(_entered("Smoke"))
    runtime.observe_temperature(_sample(4_975, 100.0))

    runtime.mode_exited(
        _exited("Smoke", "Stop", 5_000, reason=TrajectoryBreakReason.STOP)
    )

    (segment,) = _segments(persistence)
    assert segment.pre_roll_frames[0].monotonic_start_ms == 0
    assert segment.pre_roll_frames[0].monotonic_end_ms == 5_000
    assert segment.pre_roll_frames[0].partial is True
    assert TrajectoryBreakReason.STOP in _finalize_reasons(persistence)


def test_unknown_fan_delivery_breaks_current_capture() -> None:
    runtime, journal, persistence = _runtime()
    journal.set_exact(0, _FRAME_MS, auger_on_s=5.0)
    journal.integrals[(0, _FRAME_MS)] = replace(
        journal.integrals[(0, _FRAME_MS)],
        fan_certainty=FrameDeliveryCertainty.UNKNOWN,
        unknown_reasons=("fan-readback-unavailable",),
    )
    runtime.mode_entered(_entered("Smoke"))

    runtime.observe_temperature(_sample(_FRAME_MS, 100.0))

    assert _pre_roll_frames(persistence) == []
    assert runtime.status().last_break_reason is TrajectoryBreakReason.ACTUATION_UNKNOWN


def test_close_returns_worker_result_and_records_timeout_or_error() -> None:
    persistence = _Persistence()
    persistence.close_result = False
    runtime, _journal, _persistence = _runtime(persistence=persistence)

    assert runtime.close() is False
    assert runtime.status().last_error == "persistence-close-timeout"
    assert runtime.close() is False

    failed_persistence = _Persistence()
    failed_persistence.close_error = RuntimeError("worker-close-failed")
    failed_runtime, _journal, _persistence = _runtime(
        persistence=failed_persistence
    )
    assert failed_runtime.close() is False
    assert failed_runtime.status().last_error == "worker-close-failed"


def test_boundary_without_fresh_partial_sample_records_probe_gap() -> None:
    runtime, _journal, persistence = _runtime()
    runtime.mode_entered(_entered("Smoke"))
    runtime.observe_temperature(_sample(_FRAME_MS, 100.0))

    runtime.intervention(_boundary(TrajectoryBreakReason.MANUAL, 25_000))

    assert runtime.status().last_break_reason is TrajectoryBreakReason.PROBE_GAP
    assert TrajectoryBreakReason.PROBE_GAP in _finalize_reasons(persistence)


def test_failed_lineage_ignores_delayed_receipt_and_cannot_resurrect_cursor_or_counts() -> None:
    persistence = _Persistence()
    runtime, _journal, _persistence = _runtime(persistence=persistence)
    runtime.mode_entered(_entered("Smoke"))
    runtime.observe_temperature(_sample(_FRAME_MS, 100.0))
    first_id = runtime.status().segment_id
    persistence.delay_next = True
    runtime.observe_temperature(_sample(40_000, 101.0))

    persistence.complete_next(durable=False, error="sqlite-failed")
    failed = runtime.status()
    assert failed.enabled is False
    assert failed.segment_id is None
    assert failed.pre_roll_count == 1

    runtime.mode_entered(_entered("Smoke", at_ms=40_000, cook_id="cook-2"))
    runtime.observe_temperature(_sample(60_000, 102.0))
    recovered = runtime.status()
    assert recovered.enabled is True
    assert recovered.segment_id != first_id
    assert recovered.pre_roll_count == 1


def test_reset_boundary_is_deduplicated_before_later_hold_frames() -> None:
    runtime, _journal, persistence = _runtime()
    runtime.mode_entered(_entered("Smoke"))
    runtime.observe_temperature(_sample(_FRAME_MS, 100.0))
    runtime.observe_temperature(_sample(20_975, 100.5))
    boundary = _boundary(TrajectoryBreakReason.RESET, 21_000, "controller-reset")

    runtime.intervention(boundary)
    runtime.intervention(boundary)
    runtime.observe_temperature(_sample(41_000, 101.0))

    assert _finalize_reasons(persistence).count(TrajectoryBreakReason.RESET) == 1


def test_retired_delayed_append_failure_disables_replacement_lineage() -> None:
    persistence = _Persistence()
    runtime, _journal, _persistence = _runtime(persistence=persistence)
    runtime.mode_entered(_entered("Smoke"))
    runtime.observe_temperature(_sample(_FRAME_MS, 100.0))
    persistence.delay_next = True
    runtime.observe_temperature(_sample(40_000, 101.0))
    runtime.observe_temperature(_sample(44_975, 101.5))
    runtime.intervention(_boundary(TrajectoryBreakReason.MANUAL, 45_000))
    runtime.observe_temperature(_sample(65_000, 102.0))
    replacement_id = runtime.status().segment_id

    persistence.complete_next(durable=False, error="retired-append-failed")
    failed = runtime.status()

    assert replacement_id is not None
    assert failed.enabled is False
    assert failed.segment_id is None
    assert failed.last_error == "retired-append-failed"
    assert failed.last_break_reason is TrajectoryBreakReason.RECORDER_GAP


def test_delayed_terminal_finalize_makes_close_fail_even_when_worker_closes() -> None:
    persistence = _Persistence()
    runtime, _journal, _persistence = _runtime(persistence=persistence)
    runtime.mode_entered(_entered("Smoke"))
    runtime.observe_temperature(_sample(_FRAME_MS, 100.0))
    persistence.delay_next = True

    assert runtime.close() is False
    assert persistence.close_calls == [2.0]
    assert runtime.status().last_break_reason is TrajectoryBreakReason.RECORDER_GAP
    assert runtime.status().last_error == "persistence-barrier-timeout"


def test_delayed_begin_crossing_full_frame_boundary_records_gap_without_loss() -> None:
    persistence = _Persistence()
    persistence.delay_next = True
    runtime, _journal, _persistence = _runtime(persistence=persistence)
    runtime.mode_entered(_entered("Smoke"))
    runtime.observe_temperature(_sample(_FRAME_MS, 100.0))
    runtime.observe_temperature(_sample(40_000, 101.0))
    runtime.observe_temperature(_sample(44_975, 101.5))

    runtime.intervention(_boundary(TrajectoryBreakReason.MANUAL, 45_000))

    failed = runtime.status()
    assert failed.enabled is False
    assert failed.last_break_reason is TrajectoryBreakReason.RECORDER_GAP
    assert failed.last_error == "pending-cursor-prevented-exact-boundary-closure"
    assert len(_pre_roll_frames(persistence)) == 1
    assert not any(frame.partial for frame in _pre_roll_frames(persistence))

    persistence.complete_next(durable=True)
    after_stale_success = runtime.status()
    assert after_stale_success.enabled is False
    assert after_stale_success.segment_id is None
    assert after_stale_success.pre_roll_count == 0


def test_nondurable_terminal_finalize_disables_runtime_after_stop_barrier() -> None:
    persistence = _Persistence()
    runtime, _journal, _persistence = _runtime(persistence=persistence)
    runtime.mode_entered(_entered("Smoke"))
    runtime.observe_temperature(_sample(_FRAME_MS, 100.0))
    persistence.delay_next = True
    persistence.barrier_completion = (False, "terminal-finalize-failed")

    runtime.mode_exited(
        _exited(
            "Smoke",
            "Stop",
            _FRAME_MS,
            reason=TrajectoryBreakReason.STOP,
        )
    )

    status = runtime.status()
    assert status.enabled is False
    assert status.last_error == "terminal-finalize-failed"
    assert status.last_break_reason is TrajectoryBreakReason.RECORDER_GAP
