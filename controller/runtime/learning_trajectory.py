"""Process-owned capture of exact Smoke pre-roll and reconciled Hold evidence."""

from __future__ import annotations

import math
import threading
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field, replace
from functools import wraps
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, Protocol
from uuid import uuid4

from common.learning_trajectory import (
    TRAJECTORY_OBSERVATION_SCHEMA_VERSION,
    FrameDeliveryCertainty,
    HoldEntrySample,
    LearningTrajectoryFrame,
    LearningTrajectorySegment,
    TrajectoryBreakReason,
    canonical_trajectory_digest,
)
from common.persistence.learning_trajectory import SegmentCursor
from controller.runtime.actuation_delivery import DeliveredActuationIntegral

if TYPE_CHECKING:
    from controller.model_learning.contracts import FrameObservation
    from controller.mpc_model import EstimatorSeed
    from controller.runtime.model_persistence import TrajectoryAppendBatch


_FRAME_MS = 20_000
_MAX_PRE_ROLL_PER_SEGMENT = 180
_MAX_REPLAY_INTERVALS = _MAX_PRE_ROLL_PER_SEGMENT + 1
_DEFAULT_SAMPLE_AGE_LIMIT_MS = 51


def _replay_synchronized(method):
    @wraps(method)
    def locked(runtime, *args, **kwargs):
        with runtime._replay_lock:
            return method(runtime, *args, **kwargs)

    return locked
_DEFAULT_TIMEOUT = 2.0

def _append_batch(**values: object) -> TrajectoryAppendBatch:
    # The persistence worker also owns activation/evidence machinery. Import it
    # only when real recorder work is queued so importing/running Smoke with a
    # hook spy does not acquire an MPC-side dependency.
    from controller.runtime.model_persistence import TrajectoryAppendBatch

    return TrajectoryAppendBatch(**values)


def _owned_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    def own(item: object) -> object:
        if isinstance(item, Mapping):
            return MappingProxyType({str(key): own(child) for key, child in item.items()})
        if isinstance(item, (list, tuple)):
            return tuple(own(child) for child in item)
        return deepcopy(item)

    return MappingProxyType({str(key): own(item) for key, item in value.items()})
def _plain_mapping(value: Mapping[str, object]) -> dict[str, object]:
    def plain(item: object) -> object:
        if isinstance(item, Mapping):
            return {str(key): plain(child) for key, child in item.items()}
        if isinstance(item, tuple):
            return [plain(child) for child in item]
        return deepcopy(item)

    return {str(key): plain(item) for key, item in value.items()}




@dataclass(frozen=True, slots=True)
class ModeEntered:
    """Immutable effective-mode context captured before its first thermal sample."""

    effective_mode: str
    persisted_mode: str
    monotonic_ms: int
    wall_ms: int
    cook_id: str
    trajectory_session_id: str
    trace_session_id: str
    recipe_step_id: str | None
    units: str
    settings_revision: int
    collection_provenance: Mapping[str, object]
    configuration_provenance: Mapping[str, object]
    cadence_digest: str
    model_structure_digest: str
    held_physics_digest: str
    delay_input_mapping_digest: str
    actuation_mapping_digest: str
    scored_fan_regime_digest: str
    ambient_semantics_digest: str
    source_trace_digest: str
    source_schema_version: int
    source_row_digest: str
    build_provenance: Mapping[str, object]

    def __post_init__(self) -> None:
        if self.units not in {"C", "F"}:
            raise ValueError("trajectory temperature units must be C or F")
        for name in ("collection_provenance", "configuration_provenance", "build_provenance"):
            object.__setattr__(self, name, _owned_mapping(getattr(self, name)))


@dataclass(frozen=True, slots=True)
class ModeExited:
    """Effective handler exit, distinct from its persisted Recipe label."""

    effective_mode: str
    next_effective_mode: str
    monotonic_ms: int
    wall_ms: int
    reason: TrajectoryBreakReason | None = None


@dataclass(frozen=True, slots=True)
class ThermalSample:
    """One real probe read with both clocks and source-unit provenance."""

    monotonic_ms: int
    wall_ms: int
    chamber_temperature: float | None
    units: str
    probe_valid: bool
    probe_source: str | None
    ambient_temperature: float
    ambient_source: str
    ambient_uncertainty: float
    settings_revision: int
    recipe_step_id: str | None = None

    def __post_init__(self) -> None:
        if self.units not in {"C", "F"}:
            raise ValueError("thermal sample units must be C or F")


@dataclass(frozen=True, slots=True)
class TrajectoryBoundary:
    """A typed physical/configuration boundary and optional replacement context."""

    reason: TrajectoryBreakReason
    monotonic_ms: int
    wall_ms: int
    detail: str
    replacement_mode: ModeEntered | None = None


@dataclass(frozen=True, slots=True)
class TrajectoryStatus:
    """Logical recorder status; counts advance only after durable receipts."""

    enabled: bool
    segment_id: str | None
    pre_roll_count: int
    scored_count: int
    last_break_reason: TrajectoryBreakReason | None
    last_error: str | None
    gap: bool


class _DeliveryJournal(Protocol):
    def integrate(self, start_ms: int, end_ms: int) -> DeliveredActuationIntegral: ...


class _PersistenceReceipt(Protocol):
    accepted: bool
    completed: bool
    durable: bool
    cursor: SegmentCursor | None
    error: str | None


class _Persistence(Protocol):
    def submit_trajectory_batch(self, batch: TrajectoryAppendBatch) -> _PersistenceReceipt: ...

    def barrier(self, timeout: float = _DEFAULT_TIMEOUT) -> bool: ...

    def close(self, timeout: float = _DEFAULT_TIMEOUT) -> bool: ...


@dataclass(slots=True)
class _PendingReceipt:
    receipt: _PersistenceReceipt
    lineage_token: int
    segment_id: str
    pre_roll_count: int = 0
    scored_count: int = 0
    closes_segment: bool = False


@dataclass(slots=True)
class LearningTrajectoryRuntime:
    """Own one cross-mode trajectory recorder for the control-process lifetime.

    The runtime has no actuator API. It observes the delivery journal and queues
    immutable Task 3 batches; any capture/persistence failure only disables
    learning for the current lineage.
    """

    journal: _DeliveryJournal
    persistence: _Persistence
    segment_id_factory: Callable[[], str] = field(default=lambda: uuid4().hex)
    sample_age_limit_ms: int = _DEFAULT_SAMPLE_AGE_LIMIT_MS
    _mode: ModeEntered | None = field(init=False, default=None)
    _segment_id: str | None = field(init=False, default=None)
    _cursor: SegmentCursor | None = field(init=False, default=None)
    _next_sequence: int = field(init=False, default=0)
    _smoke_frame_start_ms: int | None = field(init=False, default=None)
    _smoke_frame_start_wall_ms: int | None = field(init=False, default=None)
    _hold_entry: HoldEntrySample | None = field(init=False, default=None)
    _samples: list[ThermalSample] = field(init=False, default_factory=list)
    _seen_hold_frames: set[tuple[object, ...]] = field(init=False, default_factory=set)
    _pending_receipts: list[_PendingReceipt] = field(init=False, default_factory=list)
    _pending_break: tuple[SegmentCursor, TrajectoryBreakReason] | None = field(
        init=False, default=None
    )
    _counts: dict[str, list[int]] = field(init=False, default_factory=dict)
    _failed_counts: tuple[int, int] = field(init=False, default=(0, 0))
    _last_sample_ms: int | None = field(init=False, default=None)
    _enabled: bool = field(init=False, default=True)
    _gap: bool = field(init=False, default=False)
    _last_break_reason: TrajectoryBreakReason | None = field(init=False, default=None)
    _last_error: str | None = field(init=False, default=None)
    _closed: bool = field(init=False, default=False)
    _close_result: bool | None = field(init=False, default=None)
    _lineage_token: int = field(init=False, default=0)
    _staged_partial: LearningTrajectoryFrame | None = field(init=False, default=None)
    _pending_boundary: tuple[
        int,
        TrajectoryBreakReason,
        int,
        int,
        ModeEntered | None,
        bool,
    ] | None = field(init=False, default=None)
    _draining_boundary: bool = field(init=False, default=False)
    _last_boundary_signature: tuple[object, ...] | None = field(
        init=False,
        default=None,
    )
    _reset_boundary_open: bool = field(init=False, default=False)
    _replay_frames: list[LearningTrajectoryFrame] = field(
        init=False,
        default_factory=list,
    )
    _replay_mode: ModeEntered | None = field(init=False, default=None)
    _replay_segment_id: str | None = field(init=False, default=None)
    _replay_uncertain: bool = field(init=False, default=False)
    _replay_lock: object = field(
        init=False,
        default_factory=threading.RLock,
        repr=False,
    )

    def __post_init__(self) -> None:
        if isinstance(self.sample_age_limit_ms, bool) or self.sample_age_limit_ms < 0:
            raise ValueError("sample age limit must be a non-negative integer")

    def _discard_replay_suffix(self, *, uncertain: bool = False) -> None:
        with self._replay_lock:
            self._replay_frames.clear()
            self._replay_mode = None
            self._replay_segment_id = None
            self._replay_uncertain = uncertain

    def _retain_replay_frame(self, frame: LearningTrajectoryFrame) -> None:
        with self._replay_lock:
            if frame.effective_mode not in {"Smoke", "Hold"}:
                return
            if self._replay_mode is None:
                self._replay_mode = self._mode
                self._replay_segment_id = self._segment_id
            self._replay_frames.append(frame)
            if len(self._replay_frames) > _MAX_REPLAY_INTERVALS:
                del self._replay_frames[:-_MAX_REPLAY_INTERVALS]
            self._replay_uncertain = False

    def _seed_digest(
        self,
        *,
        theta: float,
        n_delay: int,
        selected_count: int,
        status: str,
    ) -> str:
        mode = self._replay_mode
        identity = (
            {}
            if mode is None
            else {
                "cadence": mode.cadence_digest,
                "model_structure": mode.model_structure_digest,
                "held_physics": mode.held_physics_digest,
                "delay_input_mapping": mode.delay_input_mapping_digest,
                "actuation_mapping": mode.actuation_mapping_digest,
            }
        )
        return canonical_trajectory_digest(
            {
                "schema": "mpc-estimator-seed-v1",
                "segment_id": self._replay_segment_id or "no-compatible-pre-roll",
                "candidate": {"theta": float(theta), "n_delay": n_delay},
                "status": status,
                "selected_suffix_count": selected_count,
                "identity": identity,
                "prefix": [
                    {
                        "sequence": frame.sequence,
                        "monotonic_start_ms": frame.monotonic_start_ms,
                        "monotonic_end_ms": frame.monotonic_end_ms,
                        "normalized_combustion_load": frame.normalized_combustion_load,
                    }
                    for frame in self._replay_frames
                ],
            }
        )

    @_replay_synchronized
    def estimator_seed_anchor(self) -> tuple[int, float] | None:
        if self._mode is None or self._mode.effective_mode != "Hold":
            return None
        if self._replay_frames and self._replay_frames[-1].effective_mode == "Hold":
            frame = self._replay_frames[-1]
            return frame.monotonic_end_ms, frame.chamber_temperature_c
        if self._hold_entry is None:
            return None
        return self._hold_entry.monotonic_ms, self._hold_entry.chamber_temperature_c

    @_replay_synchronized
    def seed_for(
        self,
        theta: float,
        n_delay: int,
        at_ms: int,
        measured_temp_c: float,
    ) -> EstimatorSeed:
        """Return the candidate-specific replay suffix anchored by the Hold sample."""

        from controller.mpc_model import EstimatorSeed, replay_delay_chain

        if isinstance(n_delay, bool) or not isinstance(n_delay, int):
            raise TypeError("delay-state count must be an integer")
        if n_delay < 0:
            raise ValueError("delay-state count must be nonnegative")
        if isinstance(theta, bool) or not isinstance(theta, (int, float)):
            raise TypeError("delay-chain theta must be numeric")
        theta_value = float(theta)
        if n_delay > 0 and (not math.isfinite(theta_value) or theta_value <= 0.0):
            raise ValueError("delay-chain theta must be positive and finite")
        if isinstance(at_ms, bool) or not isinstance(at_ms, int):
            raise TypeError("estimator seed anchor must be an integer")
        if at_ms < 0:
            raise ValueError("estimator seed anchor must be nonnegative")
        measured = float(measured_temp_c)
        if not math.isfinite(measured):
            raise ValueError("estimator seed chamber temperature must be finite")
        required = (
            0
            if n_delay == 0
            else min(
                _MAX_PRE_ROLL_PER_SEGMENT,
                math.ceil((3.0 * theta_value) / (_FRAME_MS / 1_000)),
            )
        )
        segment_id = self._replay_segment_id or "no-compatible-pre-roll"
        anchor = self.estimator_seed_anchor()
        anchor_exact = anchor is not None and anchor[0] == at_ms
        if anchor_exact:
            measured = anchor[1]
        replay_compatible = (
            anchor_exact
            and self._replay_mode is not None
            and self._compatible(self._replay_mode, self._mode)
        )

        if self._replay_uncertain or (
            self._replay_frames and not replay_compatible
        ):
            status: Literal["uncertain"] = "uncertain"
            return EstimatorSeed(
                delay_states=(),
                chamber_temperature_c=measured,
                disturbance=0.0,
                segment_id=segment_id,
                pre_roll_digest=self._seed_digest(
                    theta=theta_value,
                    n_delay=n_delay,
                    selected_count=0,
                    status=status,
                ),
                pre_roll_frame_count=0,
                required_frame_count=required,
                status=status,
            )
        if not anchor_exact or (n_delay > 0 and not self._replay_frames):
            status_absent: Literal["absent"] = "absent"
            return EstimatorSeed(
                delay_states=(),
                chamber_temperature_c=measured,
                disturbance=0.0,
                segment_id=segment_id,
                pre_roll_digest=self._seed_digest(
                    theta=theta_value,
                    n_delay=n_delay,
                    selected_count=0,
                    status=status_absent,
                ),
                pre_roll_frame_count=0,
                required_frame_count=required,
                status=status_absent,
            )

        if n_delay == 0:
            selected: tuple[LearningTrajectoryFrame, ...] = ()
            replayed_ms = 0
        else:
            target_ms = required * _FRAME_MS
            suffix: list[LearningTrajectoryFrame] = []
            replayed_ms = 0
            for frame in reversed(self._replay_frames):
                suffix.append(frame)
                replayed_ms += frame.monotonic_end_ms - frame.monotonic_start_ms
                if replayed_ms >= target_ms:
                    break
            selected = tuple(reversed(suffix))
            expected_end_ms = (
                at_ms
                if selected[-1].effective_mode == "Hold"
                else self._mode.monotonic_ms
            )
            if selected[-1].monotonic_end_ms != expected_end_ms:
                status_uncertain: Literal["uncertain"] = "uncertain"
                return EstimatorSeed(
                    delay_states=(),
                    chamber_temperature_c=measured,
                    disturbance=0.0,
                    segment_id=segment_id,
                    pre_roll_digest=self._seed_digest(
                        theta=theta_value,
                        n_delay=n_delay,
                        selected_count=0,
                        status=status_uncertain,
                    ),
                    pre_roll_frame_count=0,
                    required_frame_count=required,
                    status=status_uncertain,
                )
        selected_count = len(selected)
        remaining_frames = (
            0
            if required == 0
            else math.ceil(
                max(0, required * _FRAME_MS - replayed_ms) / _FRAME_MS
            )
        )
        reported_frame_count = required - remaining_frames
        seed_status: Literal["exact", "short"] = (
            "exact" if remaining_frames == 0 else "short"
        )
        delay_states = replay_delay_chain(
            selected,
            theta=theta_value,
            n_delay=n_delay,
            initial_load=(
                0.0
                if not selected
                else selected[0].normalized_combustion_load
            ),
        )
        return EstimatorSeed(
            delay_states=delay_states,
            chamber_temperature_c=measured,
            disturbance=0.0,
            segment_id=segment_id,
            pre_roll_digest=self._seed_digest(
                theta=theta_value,
                n_delay=n_delay,
                selected_count=selected_count,
                status=seed_status,
            ),
            pre_roll_frame_count=reported_frame_count,
            required_frame_count=required,
            status=seed_status,
        )

    @_replay_synchronized
    def mode_entered(self, event: ModeEntered) -> None:
        if self._closed:
            return
        self._reap_receipts()
        previous = self._mode
        if previous is not None and previous.effective_mode != event.effective_mode:
            if previous.effective_mode == "Hold" and event.effective_mode == "Smoke":
                self._split_at(
                    TrajectoryBreakReason.LEFT_HOLD,
                    event.monotonic_ms,
                    event.wall_ms,
                    replacement=event,
                )
                self._discard_replay_suffix()
            elif previous.effective_mode == "Smoke" and event.effective_mode == "Hold":
                if not self._compatible(previous, event):
                    physical_pre_roll = bool(self._replay_frames)
                    self._split_at(
                        TrajectoryBreakReason.STRUCTURE_CHANGED,
                        event.monotonic_ms,
                        event.wall_ms,
                        replacement=event,
                    )
                    self._discard_replay_suffix(uncertain=physical_pre_roll)
            else:
                self._split_at(
                    TrajectoryBreakReason.STRUCTURE_CHANGED,
                    event.monotonic_ms,
                    event.wall_ms,
                    replacement=event,
                )
                self._discard_replay_suffix()
        elif previous is None and event.effective_mode == "Smoke":
            self._discard_replay_suffix()
        self._mode = event
        self._enabled = True
        self._gap = False
        self._failed_counts = (0, 0)
        if event.effective_mode == "Smoke":
            self._smoke_frame_start_ms = event.monotonic_ms
            self._smoke_frame_start_wall_ms = event.wall_ms
            self._hold_entry = None
        elif event.effective_mode == "Hold":
            self._hold_entry = None
        self._samples.clear()
        self._last_sample_ms = None

    @_replay_synchronized
    def mode_exited(self, event: ModeExited) -> None:
        if self._closed:
            return
        self._reap_receipts()
        if (
            event.effective_mode == "Smoke"
            and not self._drain_due_smoke_boundary(event.monotonic_ms)
        ):
            self.barrier()
            return
        if event.effective_mode == "Smoke" and event.next_effective_mode == "Hold" and event.reason is None:
            if not self._close_smoke_partial(
                event.monotonic_ms,
                event.wall_ms,
                TrajectoryBreakReason.MODE_TRANSITION,
            ):
                failure_reason = (
                    TrajectoryBreakReason.ACTUATION_UNKNOWN
                    if self._last_break_reason
                    is TrajectoryBreakReason.ACTUATION_UNKNOWN
                    else TrajectoryBreakReason.PROBE_GAP
                )
                self._finalize(failure_reason)
                self._last_break_reason = failure_reason
        elif event.effective_mode == "Hold" and event.next_effective_mode == "Smoke":
            self._split_at(
                TrajectoryBreakReason.LEFT_HOLD,
                event.monotonic_ms,
                event.wall_ms,
            )
        elif event.reason is not None:
            reason = (
                TrajectoryBreakReason.UNCLEAN_RESTART
                if event.reason is TrajectoryBreakReason.PROCESS_RESTART
                else event.reason
            )
            if event.effective_mode == "Smoke":
                self._close_smoke_partial(event.monotonic_ms, event.wall_ms, reason)
            self._finalize(reason)
            self._last_break_reason = reason
        self.barrier()

    @_replay_synchronized
    def observe_temperature(self, sample: ThermalSample) -> None:
        if self._closed or not self._enabled or self._mode is None:
            return
        self._reap_receipts()
        if not self._enabled:
            return
        if self._last_sample_ms is not None and sample.monotonic_ms < self._last_sample_ms:
            self._finalize(TrajectoryBreakReason.CLOCK_DISCONTINUITY)
            self._last_break_reason = TrajectoryBreakReason.CLOCK_DISCONTINUITY
            self._reset_capture_at(sample.monotonic_ms, sample.wall_ms)
            return
        self._last_sample_ms = sample.monotonic_ms
        if not self._valid_sample(sample):
            self._finalize(TrajectoryBreakReason.PROBE_GAP)
            self._last_break_reason = TrajectoryBreakReason.PROBE_GAP
            self._reset_capture_at(sample.monotonic_ms, sample.wall_ms)
            return
        self._samples.append(sample)
        if len(self._samples) > 512:
            del self._samples[:-512]
        if self._mode.effective_mode == "Hold":
            if self._hold_entry is None and sample.monotonic_ms >= self._mode.monotonic_ms:
                self._hold_entry = HoldEntrySample(
                    monotonic_ms=sample.monotonic_ms,
                    wall_ms=sample.wall_ms,
                    chamber_temperature_c=self._to_celsius(sample.chamber_temperature, sample.units),
                    probe_valid=True,
                    probe_source=sample.probe_source,
                )
            return
        if self._mode.effective_mode == "Smoke":
            self._close_due_smoke_frames(sample.monotonic_ms)

    @_replay_synchronized
    def observe_hold_frame(
        self,
        observation: FrameObservation,
        *,
        replay_only: bool = False,
    ) -> None:
        if self._closed or not self._enabled or self._mode is None:
            return
        if self._mode.effective_mode != "Hold":
            return
        self._reap_receipts()
        identity = (
            observation.frame_start_s,
            observation.frame_end_s,
            observation.result_revision,
            observation.observation_sequence,
        )
        if identity in self._seen_hold_frames:
            return
        raw_start_ms = round(observation.frame_start_s * 1_000)
        raw_end_ms = round(observation.frame_end_s * 1_000)
        if raw_end_ms - raw_start_ms != _FRAME_MS:
            self._split_at(
                TrajectoryBreakReason.RECORDER_GAP,
                raw_end_ms,
                self._wall_for_monotonic(raw_end_ms),
            )
            return
        start_ms = raw_start_ms
        end_ms = raw_end_ms
        sample = self._sample_for_frame_end(end_ms)
        if sample is None:
            sample = self._sample_for_wall_end(raw_end_ms)
            if sample is not None:
                clock_offset_ms = sample.wall_ms - sample.monotonic_ms
                start_ms = raw_start_ms - clock_offset_ms
                end_ms = raw_end_ms - clock_offset_ms
                wall_start_ms = raw_start_ms
                wall_end_ms = raw_end_ms
            else:
                wall_start_ms = self._wall_for_monotonic(start_ms)
                wall_end_ms = self._wall_for_monotonic(end_ms)
        else:
            wall_start_ms = self._wall_for_monotonic(start_ms, sample)
            wall_end_ms = self._wall_for_monotonic(end_ms, sample)
        if sample is None or self._hold_entry is None:
            self._finalize(TrajectoryBreakReason.PROBE_GAP)
            self._last_break_reason = TrajectoryBreakReason.PROBE_GAP
            return
        actual_fan_duty = observation.actual_fan_duty
        fan_exact = (
            isinstance(actual_fan_duty, (int, float))
            and not isinstance(actual_fan_duty, bool)
            and math.isfinite(float(actual_fan_duty))
            and 0.0 <= float(actual_fan_duty) <= 1.0
        )
        if not fan_exact:
            self._finalize(TrajectoryBreakReason.ACTUATION_UNKNOWN)
            self._last_break_reason = TrajectoryBreakReason.ACTUATION_UNKNOWN
            return
        fan_duty = float(actual_fan_duty) if fan_exact else 0.0
        duration_seconds = (end_ms - start_ms) / 1_000
        integral = DeliveredActuationIntegral(
            monotonic_start_ms=start_ms,
            monotonic_end_ms=end_ms,
            auger_on_seconds=float(observation.delivered_on_s),
            fan_on_seconds=duration_seconds if fan_duty > 0.0 else 0.0,
            fan_duty_integral_seconds=duration_seconds * fan_duty,
            auger_start_active=False,
            auger_end_active=False,
            fan_start_active=fan_duty > 0.0,
            fan_end_active=fan_duty > 0.0,
            pwm_start=fan_duty,
            pwm_end=fan_duty,
            auger_certainty=FrameDeliveryCertainty.EXACT,
            fan_certainty=(
                FrameDeliveryCertainty.EXACT
                if fan_exact
                else FrameDeliveryCertainty.UNKNOWN
            ),
            unknown_reasons=() if fan_exact else ("hold-fan-delivery-unknown",),
        )
        frame = self._frame_from_integral(
            start_ms=start_ms,
            end_ms=end_ms,
            wall_start_ms=wall_start_ms,
            wall_end_ms=wall_end_ms,
            sample=sample,
            integral=integral,
            effective_mode="Hold",
            partial=False,
            boundary_reason=None,
            normalized_load=float(observation.realized_q),
        )
        if not observation.continuous or not observation.probe_valid:
            self._finalize(TrajectoryBreakReason.RECORDER_GAP)
            self._last_break_reason = TrajectoryBreakReason.RECORDER_GAP
            return
        if replay_only or self._submit_frame(
            frame,
            scored=True,
            hold_entry=self._hold_entry,
        ):
            self._retain_replay_frame(frame)
            self._seen_hold_frames.add(identity)

    @_replay_synchronized
    def intervention(self, boundary: TrajectoryBoundary) -> None:
        signature = (
            boundary.reason,
            boundary.monotonic_ms,
            boundary.wall_ms,
            boundary.detail,
        )
        if (
            boundary.reason is TrajectoryBreakReason.RESET
            and self._reset_boundary_open
        ):
            return
        if boundary.reason is TrajectoryBreakReason.RESET:
            self._reset_boundary_open = True
            self._last_boundary_signature = signature
        reason = (
            TrajectoryBreakReason.UNCLEAN_RESTART
            if boundary.reason is TrajectoryBreakReason.PROCESS_RESTART
            else boundary.reason
        )
        if boundary.reason is TrajectoryBreakReason.PROCESS_RESTART:
            if not self._drain_due_smoke_boundary(boundary.monotonic_ms):
                return
            if self._mode is not None and self._mode.effective_mode == "Smoke":
                self._close_smoke_partial(
                    boundary.monotonic_ms,
                    boundary.wall_ms,
                    reason,
                )
            self._finalize(reason)
            self._mode = boundary.replacement_mode
            self._last_break_reason = reason
            self._reset_capture_at(boundary.monotonic_ms, boundary.wall_ms)
            return
        self._split_at(
            reason,
            boundary.monotonic_ms,
            boundary.wall_ms,
            replacement=boundary.replacement_mode,
        )

    @_replay_synchronized
    def configuration_changed(self, boundary: TrajectoryBoundary) -> None:
        self._split_at(
            boundary.reason,
            boundary.monotonic_ms,
            boundary.wall_ms,
            replacement=boundary.replacement_mode,
        )

    def status(self) -> TrajectoryStatus:
        self._reap_receipts()
        counts = (
            list(self._failed_counts)
            if self._gap and self._segment_id is None
            else self._counts.get(self._segment_id or "", [0, 0])
        )
        return TrajectoryStatus(
            enabled=self._enabled and not self._closed,
            segment_id=self._segment_id,
            pre_roll_count=counts[0],
            scored_count=counts[1],
            last_break_reason=self._last_break_reason,
            last_error=self._last_error,
            gap=self._gap,
        )

    def barrier(self, timeout: float = _DEFAULT_TIMEOUT) -> bool:
        for _attempt in range(4):
            try:
                complete = self.persistence.barrier(timeout=timeout)
            except Exception as error:
                self._persistence_failed(str(error))
                return False
            if not complete:
                self._persistence_failed("persistence-barrier-timeout")
                return False
            self._reap_receipts()
            if not self._pending_receipts:
                return self._enabled
        self._persistence_failed("persistence-barrier-timeout")
        return False

    def close(self) -> bool:
        if self._closed:
            return bool(self._close_result)
        self._closed = True
        drained = (
            self.barrier(timeout=_DEFAULT_TIMEOUT)
            if self._pending_receipts
            else True
        )
        if self._segment_id is not None or self._pending_break is not None:
            self._finalize(TrajectoryBreakReason.UNCLEAN_RESTART)
        terminal_drained = (
            self.barrier(timeout=_DEFAULT_TIMEOUT)
            if self._pending_receipts
            else True
        )
        try:
            worker_closed = bool(
                self.persistence.close(timeout=_DEFAULT_TIMEOUT)
            )
        except Exception as error:
            self._last_error = str(error)
            worker_closed = False
        self._reap_receipts()
        receipts_settled = not self._pending_receipts
        closed = (
            drained
            and terminal_drained
            and worker_closed
            and receipts_settled
        )
        if not closed and self._last_error is None:
            self._last_error = "persistence-close-timeout"
        self._close_result = closed
        return closed

    @staticmethod
    def _compatible(left: ModeEntered, right: ModeEntered) -> bool:
        fields = (
            "units",
            "cadence_digest",
            "model_structure_digest",
            "held_physics_digest",
            "delay_input_mapping_digest",
            "actuation_mapping_digest",
            "scored_fan_regime_digest",
            "ambient_semantics_digest",
        )
        return all(getattr(left, name) == getattr(right, name) for name in fields)

    @staticmethod
    def _valid_sample(sample: ThermalSample) -> bool:
        return (
            sample.probe_valid
            and sample.probe_source is not None
            and isinstance(sample.chamber_temperature, (int, float))
            and not isinstance(sample.chamber_temperature, bool)
            and math.isfinite(float(sample.chamber_temperature))
            and math.isfinite(float(sample.ambient_temperature))
            and math.isfinite(float(sample.ambient_uncertainty))
        )

    def _sample_for_wall_end(self, wall_end_ms: int) -> ThermalSample | None:
        for sample in reversed(self._samples):
            if sample.wall_ms <= wall_end_ms:
                age = wall_end_ms - sample.wall_ms
                return sample if age <= self.sample_age_limit_ms else None
        return None

    @staticmethod
    def _to_celsius(value: float | None, units: str) -> float:
        if value is None:
            raise ValueError("missing temperature")
        numeric = float(value)
        return numeric if units == "C" else (numeric - 32.0) * 5.0 / 9.0

    def _sample_for_frame_end(self, end_ms: int) -> ThermalSample | None:
        for sample in reversed(self._samples):
            if sample.monotonic_ms <= end_ms:
                age = end_ms - sample.monotonic_ms
                return sample if age <= self.sample_age_limit_ms else None
        return None

    def _drain_due_smoke_boundary(self, boundary_ms: int) -> bool:
        if (
            self._mode is None
            or self._mode.effective_mode != "Smoke"
            or self._smoke_frame_start_ms is None
            or self._smoke_frame_start_ms + _FRAME_MS > boundary_ms
        ):
            return True
        if self._segment_id is not None and self._cursor is None:
            self._persistence_failed(
                "pending-cursor-prevented-exact-boundary-closure"
            )
            return False
        self._close_due_smoke_frames(boundary_ms)
        if not self._enabled:
            return False
        if (
            self._smoke_frame_start_ms is not None
            and self._smoke_frame_start_ms + _FRAME_MS <= boundary_ms
        ):
            self._persistence_failed(
                "pending-cursor-prevented-exact-boundary-closure"
            )
            return False
        return True

    def _close_due_smoke_frames(self, through_ms: int) -> None:
        while (
            self._enabled
            and self._smoke_frame_start_ms is not None
            and self._smoke_frame_start_wall_ms is not None
            and self._smoke_frame_start_ms + _FRAME_MS <= through_ms
        ):
            start_ms = self._smoke_frame_start_ms
            end_ms = start_ms + _FRAME_MS
            sample = self._sample_for_frame_end(end_ms)
            if sample is None:
                self._finalize(TrajectoryBreakReason.PROBE_GAP)
                self._last_break_reason = TrajectoryBreakReason.PROBE_GAP
                self._reset_capture_at(
                    end_ms,
                    self._smoke_frame_start_wall_ms + _FRAME_MS,
                )
                return
            if self._next_sequence >= _MAX_PRE_ROLL_PER_SEGMENT:
                self._split_at(
                    TrajectoryBreakReason.RETENTION_ROLLOVER,
                    start_ms,
                    self._smoke_frame_start_wall_ms,
                )
            integral = self.journal.integrate(start_ms, end_ms)
            if (
                integral.auger_certainty is not FrameDeliveryCertainty.EXACT
                or integral.fan_certainty is not FrameDeliveryCertainty.EXACT
            ):
                self._finalize(TrajectoryBreakReason.ACTUATION_UNKNOWN)
                self._discard_replay_suffix(uncertain=True)
                self._last_break_reason = TrajectoryBreakReason.ACTUATION_UNKNOWN
                self._reset_capture_at(
                    end_ms,
                    self._smoke_frame_start_wall_ms + _FRAME_MS,
                )
                return
            frame = self._frame_from_integral(
                start_ms=start_ms,
                end_ms=end_ms,
                wall_start_ms=self._smoke_frame_start_wall_ms,
                wall_end_ms=self._smoke_frame_start_wall_ms + _FRAME_MS,
                sample=sample,
                integral=integral,
                effective_mode="Smoke",
                partial=False,
                boundary_reason=None,
            )
            if not self._submit_frame(frame, scored=False):
                return
            self._smoke_frame_start_ms = end_ms
            self._smoke_frame_start_wall_ms += _FRAME_MS

    def _close_smoke_partial(
        self,
        end_ms: int,
        wall_end_ms: int,
        reason: TrajectoryBreakReason,
    ) -> bool:
        start_ms = self._smoke_frame_start_ms
        wall_start_ms = self._smoke_frame_start_wall_ms
        if (
            start_ms is None
            or wall_start_ms is None
            or end_ms <= start_ms
            or end_ms - start_ms >= _FRAME_MS
        ):
            return True
        sample = self._sample_for_frame_end(end_ms)
        if sample is None:
            return False
        integral = self.journal.integrate(start_ms, end_ms)
        if (
            integral.auger_certainty is not FrameDeliveryCertainty.EXACT
            or integral.fan_certainty is not FrameDeliveryCertainty.EXACT
        ):
            self._last_break_reason = TrajectoryBreakReason.ACTUATION_UNKNOWN
            self._discard_replay_suffix(uncertain=True)
            return False
        frame = self._frame_from_integral(
            start_ms=start_ms,
            end_ms=end_ms,
            wall_start_ms=wall_start_ms,
            wall_end_ms=wall_end_ms,
            sample=sample,
            integral=integral,
            effective_mode="Smoke",
            partial=True,
            boundary_reason=reason,
        )
        if self._segment_id is not None and self._cursor is None:
            self._staged_partial = frame
            self._smoke_frame_start_ms = end_ms
            self._smoke_frame_start_wall_ms = wall_end_ms
            return True
        submitted = self._submit_frame(frame, scored=False)
        if submitted:
            self._smoke_frame_start_ms = end_ms
            self._smoke_frame_start_wall_ms = wall_end_ms
        return submitted

    def _frame_from_integral(
        self,
        *,
        start_ms: int,
        end_ms: int,
        wall_start_ms: int,
        wall_end_ms: int,
        sample: ThermalSample,
        integral: DeliveredActuationIntegral,
        effective_mode: str,
        partial: bool,
        boundary_reason: TrajectoryBreakReason | None,
        normalized_load: float | None = None,
    ) -> LearningTrajectoryFrame:
        duration_seconds = (end_ms - start_ms) / 1_000
        realized = integral.auger_on_seconds / duration_seconds
        fan_mean = integral.fan_duty_integral_seconds / duration_seconds
        return LearningTrajectoryFrame(
            sequence=self._next_sequence,
            monotonic_start_ms=start_ms,
            monotonic_end_ms=end_ms,
            wall_start_ms=wall_start_ms,
            wall_end_ms=wall_end_ms,
            chamber_temperature_c=self._to_celsius(sample.chamber_temperature, sample.units),
            temperature_sample_monotonic_ms=sample.monotonic_ms,
            temperature_sample_wall_ms=sample.wall_ms,
            temperature_sample_age_ms=end_ms - sample.monotonic_ms,
            temperature_sample_wall_age_ms=wall_end_ms - sample.wall_ms,
            temperature_sample_clock_skew_ms=(
                (wall_end_ms - sample.wall_ms)
                - (end_ms - sample.monotonic_ms)
            ),
            source_temperature_units=sample.units,
            settings_revision=sample.settings_revision,
            probe_valid=True,
            probe_source=sample.probe_source,
            ambient_temperature_c=self._to_celsius(sample.ambient_temperature, sample.units),
            ambient_source=sample.ambient_source,
            ambient_uncertainty_c=(
                float(sample.ambient_uncertainty)
                if sample.units == "C"
                else float(sample.ambient_uncertainty) * 5.0 / 9.0
            ),
            delivered_auger_on_seconds=float(integral.auger_on_seconds),
            realized_auger_duty=realized,
            normalized_combustion_load=realized if normalized_load is None else normalized_load,
            delivered_fan_on_seconds=float(integral.fan_on_seconds),
            fan_duty_integral_seconds=float(integral.fan_duty_integral_seconds),
            mean_actual_fan_duty=fan_mean,
            auger_delivery_certainty=integral.auger_certainty,
            fan_delivery_certainty=integral.fan_certainty,
            effective_mode=effective_mode,
            recipe_step_id=sample.recipe_step_id,
            complete=not partial,
            continuous=True,
            partial=partial,
            boundary_reason=boundary_reason,
        )

    def _submit_frame(
        self,
        frame: LearningTrajectoryFrame,
        *,
        scored: bool,
        hold_entry: HoldEntrySample | None = None,
    ) -> bool:
        mode = self._mode
        if mode is None:
            return False
        if self._segment_id is None:
            self._lineage_token += 1
            segment_id = self.segment_id_factory()
            segment = self._segment_from_first_frame(
                segment_id,
                mode,
                frame,
                scored=scored,
                hold_entry=hold_entry,
            )
            pending_break = self._pending_break
            batch = (
                _append_batch(
                    cursor=pending_break[0],
                    break_reason=pending_break[1],
                    next_segment=segment,
                )
                if pending_break is not None
                else _append_batch(begin_segment=segment)
            )
            self._segment_id = segment_id
            self._counts.setdefault(segment_id, [0, 0])
            accepted = self._submit(
                batch,
                segment_id,
                pre_roll_count=0 if scored else 1,
                scored_count=1 if scored else 0,
            )
            if not accepted:
                self._segment_id = None
                return False
            self._pending_break = None
            self._reset_boundary_open = False
            self._next_sequence = 1
            if not scored:
                self._retain_replay_frame(frame)
            return True
        cursor = self._cursor
        if cursor is None:
            self._reap_receipts()
            cursor = self._cursor
        if cursor is None:
            return False
        batch = _append_batch(
            cursor=cursor,
            pre_roll=() if scored else (frame,),
            hold_entry=hold_entry if scored and self._counts[self._segment_id][1] == 0 else None,
            scored=(frame,) if scored else (),
        )
        if not self._submit(
            batch,
            self._segment_id,
            pre_roll_count=0 if scored else 1,
            scored_count=1 if scored else 0,
        ):
            return False
        self._reset_boundary_open = False
        self._next_sequence += 1
        if not scored:
            self._retain_replay_frame(frame)
        return True

    def _segment_from_first_frame(
        self,
        segment_id: str,
        mode: ModeEntered,
        frame: LearningTrajectoryFrame,
        *,
        scored: bool,
        hold_entry: HoldEntrySample | None,
    ) -> LearningTrajectorySegment:
        return LearningTrajectorySegment(
            schema_version=1,
            observation_schema_version=TRAJECTORY_OBSERVATION_SCHEMA_VERSION,
            segment_id=segment_id,
            cook_id=mode.cook_id,
            trajectory_session_id=mode.trajectory_session_id,
            trace_session_ids=(mode.trace_session_id,),
            collection_provenance=_plain_mapping(mode.collection_provenance),
            configuration_provenance=_plain_mapping(
                mode.configuration_provenance
            ),
            cadence_digest=mode.cadence_digest,
            model_structure_digest=mode.model_structure_digest,
            held_physics_digest=mode.held_physics_digest,
            delay_input_mapping_digest=mode.delay_input_mapping_digest,
            actuation_mapping_digest=mode.actuation_mapping_digest,
            scored_fan_regime_digest=mode.scored_fan_regime_digest,
            ambient_semantics_digest=mode.ambient_semantics_digest,
            pre_roll_frames=() if scored else (frame,),
            hold_entry=hold_entry if scored else None,
            scored_hold_frames=(frame,) if scored else (),
            generation_audit_ranges=(),
            start_monotonic_ms=frame.monotonic_start_ms,
            end_monotonic_ms=frame.monotonic_end_ms,
            start_wall_ms=frame.wall_start_ms,
            end_wall_ms=frame.wall_end_ms,
            start_sequence=frame.sequence,
            end_sequence=frame.sequence,
            pre_roll_end_reason=None,
            terminal_break_reason=None,
            state="open",
            source_trace_digest=mode.source_trace_digest,
            source_schema_version=mode.source_schema_version,
            source_row_digest=mode.source_row_digest,
            build_provenance=_plain_mapping(mode.build_provenance),
        )

    def _submit(
        self,
        batch: TrajectoryAppendBatch,
        segment_id: str,
        *,
        pre_roll_count: int = 0,
        scored_count: int = 0,
        closes_segment: bool = False,
    ) -> bool:
        try:
            receipt = self.persistence.submit_trajectory_batch(batch)
        except Exception as error:
            self._persistence_failed(str(error))
            return False
        if not receipt.accepted:
            self._persistence_failed(receipt.error or "trajectory-persistence-rejected")
            return False
        pending = _PendingReceipt(
            receipt=receipt,
            lineage_token=self._lineage_token,
            segment_id=segment_id,
            pre_roll_count=pre_roll_count,
            scored_count=scored_count,
            closes_segment=closes_segment,
        )
        self._pending_receipts.append(pending)
        self._reap_receipts()
        return self._enabled

    def _reap_receipts(self) -> None:
        remaining: list[_PendingReceipt] = []
        failure_error: str | None = None
        for pending in self._pending_receipts:
            receipt = pending.receipt
            if not receipt.completed:
                remaining.append(pending)
                continue
            if not receipt.durable:
                if failure_error is None:
                    failure_error = (
                        receipt.error or "trajectory-persistence-failed"
                    )
                continue
            if pending.lineage_token != self._lineage_token:
                continue
            counts = self._counts.setdefault(pending.segment_id, [0, 0])
            counts[0] += pending.pre_roll_count
            counts[1] += pending.scored_count
            if (
                receipt.cursor is not None
                and not pending.closes_segment
                and pending.segment_id == self._segment_id
            ):
                self._cursor = receipt.cursor
        self._pending_receipts = remaining
        if failure_error is not None:
            self._persistence_failed(failure_error)
            return
        self._drain_staged_boundary()

    def _drain_staged_boundary(self) -> None:
        if (
            self._draining_boundary
            or not self._enabled
            or self._cursor is None
        ):
            return
        self._draining_boundary = True
        try:
            staged_partial = self._staged_partial
            if staged_partial is not None:
                self._staged_partial = None
                if not self._submit_frame(staged_partial, scored=False):
                    return
                if any(
                    item.lineage_token == self._lineage_token
                    for item in self._pending_receipts
                ):
                    return
            pending = self._pending_boundary
            if pending is None:
                return
            token, reason, monotonic_ms, wall_ms, replacement, terminal = pending
            if token != self._lineage_token:
                self._pending_boundary = None
                return
            self._pending_boundary = None
            if terminal:
                self._finalize(reason)
            else:
                self._complete_split(
                    reason,
                    monotonic_ms,
                    wall_ms,
                    replacement=replacement,
                )
        finally:
            self._draining_boundary = False

    def _finalize(self, reason: TrajectoryBreakReason) -> None:
        self._discard_replay_suffix(
            uncertain=reason is TrajectoryBreakReason.ACTUATION_UNKNOWN
        )
        if self._segment_id is None:
            pending_break = self._pending_break
            if pending_break is not None:
                cursor, pending_reason = pending_break
                self._submit(
                    _append_batch(
                        cursor=cursor,
                        finalize_reason=pending_reason,
                    ),
                    cursor.segment_id,
                    closes_segment=True,
                )
                self._pending_break = None
            return
        if self._cursor is None:
            self._pending_boundary = (
                self._lineage_token,
                reason,
                self._smoke_frame_start_ms or 0,
                self._smoke_frame_start_wall_ms or 0,
                None,
                True,
            )
            self._last_break_reason = reason
            return
        cursor = self._cursor
        segment_id = self._segment_id
        self._submit(
            _append_batch(cursor=cursor, finalize_reason=reason),
            segment_id,
            closes_segment=True,
        )
        self._lineage_token += 1
        self._segment_id = None
        self._cursor = None
        self._next_sequence = 0
        self._hold_entry = None
        self._staged_partial = None

    def _complete_split(
        self,
        reason: TrajectoryBreakReason,
        monotonic_ms: int,
        wall_ms: int,
        *,
        replacement: ModeEntered | None,
    ) -> None:
        self._discard_replay_suffix()
        if self._segment_id is not None and self._cursor is not None:
            self._pending_break = (self._cursor, reason)
        self._lineage_token += 1
        self._segment_id = None
        self._cursor = None
        self._next_sequence = 0
        self._hold_entry = None
        self._seen_hold_frames.clear()
        self._last_break_reason = reason
        if replacement is not None:
            self._mode = replacement
        elif self._mode is not None:
            self._mode = replace(
                self._mode,
                monotonic_ms=monotonic_ms,
                wall_ms=wall_ms,
            )
        self._reset_capture_at(monotonic_ms, wall_ms)

    def _split_at(
        self,
        reason: TrajectoryBreakReason,
        monotonic_ms: int,
        wall_ms: int,
        *,
        replacement: ModeEntered | None = None,
    ) -> None:
        self._reap_receipts()
        if not self._drain_due_smoke_boundary(monotonic_ms):
            return
        if (
            self._mode is not None
            and self._mode.effective_mode == "Smoke"
            and not self._close_smoke_partial(
                monotonic_ms,
                wall_ms,
                reason,
            )
        ):
            failure_reason = (
                TrajectoryBreakReason.ACTUATION_UNKNOWN
                if self._last_break_reason
                is TrajectoryBreakReason.ACTUATION_UNKNOWN
                else TrajectoryBreakReason.PROBE_GAP
            )
            self._finalize(failure_reason)
            self._last_break_reason = failure_reason
            if replacement is not None:
                self._mode = replacement
            self._reset_capture_at(monotonic_ms, wall_ms)
            return
        if self._segment_id is not None and self._cursor is None:
            self._pending_boundary = (
                self._lineage_token,
                reason,
                monotonic_ms,
                wall_ms,
                replacement,
                False,
            )
            self._last_break_reason = reason
            if replacement is not None:
                self._mode = replacement
            self._reset_capture_at(monotonic_ms, wall_ms)
            return
        self._complete_split(
            reason,
            monotonic_ms,
            wall_ms,
            replacement=replacement,
        )

    def _reset_capture_at(self, monotonic_ms: int, wall_ms: int) -> None:
        self._samples.clear()
        self._last_sample_ms = None
        if self._mode is not None and self._mode.effective_mode == "Smoke":
            self._smoke_frame_start_ms = monotonic_ms
            self._smoke_frame_start_wall_ms = wall_ms
        else:
            self._smoke_frame_start_ms = None
            self._smoke_frame_start_wall_ms = None

    def _persistence_failed(self, error: str) -> None:
        self._discard_replay_suffix()
        self._enabled = False
        self._gap = True
        self._last_break_reason = TrajectoryBreakReason.RECORDER_GAP
        self._last_error = error
        if self._segment_id is not None:
            durable = self._counts.get(self._segment_id, [0, 0])
            self._failed_counts = (durable[0], durable[1])
        self._lineage_token += 1
        self._segment_id = None
        self._cursor = None
        self._pending_break = None
        self._pending_boundary = None
        self._staged_partial = None
        self._next_sequence = 0
        self._hold_entry = None

    def _wall_for_monotonic(
        self,
        monotonic_ms: int,
        sample: ThermalSample | None = None,
    ) -> int:
        authority = sample
        if authority is None and self._samples:
            authority = self._samples[-1]
        if authority is not None:
            return authority.wall_ms + monotonic_ms - authority.monotonic_ms
        if self._mode is not None:
            return self._mode.wall_ms + monotonic_ms - self._mode.monotonic_ms
        return monotonic_ms
