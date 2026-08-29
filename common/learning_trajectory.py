"""Immutable contracts for durable cumulative-learning trajectories and fit lineage."""

from __future__ import annotations

import json
import math
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import asdict
from dataclasses import dataclass as std_dataclass
from dataclasses import field as std_field
from enum import StrEnum
from hashlib import sha256
from itertools import pairwise
from typing import Annotated, Literal, cast

from pydantic import ConfigDict, Field, StringConstraints, field_validator, model_validator
from pydantic.dataclasses import dataclass

_FRAME_MILLISECONDS = 20_000
TRAJECTORY_OBSERVATION_SCHEMA_VERSION = 2
_MAX_METADATA_BYTES = 65_536
_MAX_CORPUS_SLICES = 256
_DATACLASS_CONFIG = ConfigDict(
    extra="forbid",
    strict=True,
    validate_default=True,
    arbitrary_types_allowed=True,
)

type FiniteFloat = Annotated[float, Field(allow_inf_nan=False, strict=True)]
type NonNegativeFloat = Annotated[FiniteFloat, Field(ge=0)]
type BoundedLoad = Annotated[FiniteFloat, Field(ge=0, le=1)]
type NonNegativeInt = Annotated[int, Field(ge=0, strict=True)]
type StrictInt = Annotated[int, Field(strict=True)]
type PositiveInt = Annotated[int, Field(gt=0, strict=True)]
type NonBlankString = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1),
]
type Digest = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$"),
]
type JsonValue = str | int | float | bool | None | dict[str, JsonValue] | list[JsonValue]


type FrozenJsonValue = str | int | float | bool | None | FrozenJsonArray | FrozenJsonObject


@std_dataclass(frozen=True, slots=True, eq=False)
class FrozenJsonArray(Sequence[FrozenJsonValue]):
    """Recursively immutable JSON array with stable iteration order."""

    _items: tuple[FrozenJsonValue, ...]

    def __post_init__(self) -> None:
        raw_items = cast(object, self._items)
        if type(raw_items) not in (tuple, list):
            raise ValueError("frozen JSON array items must be a tuple or list")
        items = cast(tuple[object, ...] | list[object], raw_items)
        frozen = tuple(_validated_frozen_json_value(item, context="frozen JSON array") for item in items)
        object.__setattr__(self, "_items", frozen)

    def __getitem__(self, index: int | slice) -> FrozenJsonValue | tuple[FrozenJsonValue, ...]:
        return self._items[index]

    def __len__(self) -> int:
        return len(self._items)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Sequence) or isinstance(other, (str, bytes, bytearray)):
            return False
        return tuple(self) == tuple(other)

    def __repr__(self) -> str:
        return repr(list(self))


@std_dataclass(frozen=True, slots=True, eq=False)
class FrozenJsonObject(Mapping[str, FrozenJsonValue]):
    """Recursively immutable JSON object with canonical key order."""

    _items: tuple[tuple[str, FrozenJsonValue], ...]

    def __post_init__(self) -> None:
        raw_items = cast(object, self._items)
        if isinstance(raw_items, Mapping):
            entries: tuple[object, ...] = tuple(raw_items.items())
        elif type(raw_items) in (tuple, list):
            entries = tuple(cast(tuple[object, ...] | list[object], raw_items))
        else:
            raise ValueError("frozen JSON object items must be key/value pairs")
        frozen_items: list[tuple[str, FrozenJsonValue]] = []
        seen: set[str] = set()
        for entry in entries:
            if type(entry) not in (tuple, list) or len(entry) != 2:
                raise ValueError("frozen JSON object entries must be key/value pairs")
            key, value = entry
            if type(key) is not str:
                raise ValueError("frozen JSON object keys must be strings")
            if key in seen:
                raise ValueError("frozen JSON object keys must be unique")
            seen.add(key)
            frozen_items.append((key, _validated_frozen_json_value(value, context="frozen JSON object")))
        frozen_items.sort(key=lambda item: item[0])
        object.__setattr__(self, "_items", tuple(frozen_items))

    def __getitem__(self, key: str) -> FrozenJsonValue:
        for candidate, value in self._items:
            if candidate == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Mapping):
            return False
        return dict(self.items()) == dict(other.items())

    def __repr__(self) -> str:
        return repr(dict(self.items()))


class TrajectoryBreakReason(StrEnum):
    """A typed reason why trajectory continuity ended or was intentionally split."""

    MANUAL = "manual"
    LID_OPEN = "lid-open"
    SAFETY = "safety"
    RESET = "reset"
    STOP = "stop"
    ERROR = "error"
    PROCESS_RESTART = "process-restart"
    COOK_ROTATED = "cook-rotated"
    HISTORY_CLEARED = "history-cleared"
    PROBE_GAP = "probe-gap"
    ACTUATION_UNKNOWN = "actuation-unknown"
    RECORDER_GAP = "recorder-gap"
    CLOCK_DISCONTINUITY = "clock-discontinuity"
    UNITS_CHANGED = "units-changed"
    STRUCTURE_CHANGED = "structure-changed"
    ACTUATION_MAPPING_CHANGED = "actuation-mapping-changed"
    FAN_MAPPING_CHANGED = "fan-mapping-changed"
    AMBIENT_SEMANTICS_CHANGED = "ambient-semantics-changed"
    MODE_TRANSITION = "mode-transition"
    LEFT_HOLD = "left-hold"
    UNCLEAN_RESTART = "unclean-restart"
    RETENTION_ROLLOVER = "retention-rollover"


class FrameDeliveryCertainty(StrEnum):
    """Whether an interval's delivered actuator integral is defensible."""

    EXACT = "exact"
    UNKNOWN = "unknown"


def _owned_json_value(value: object, *, context: str) -> JsonValue:
    if value is None:
        return None
    if type(value) is str:
        return value
    if type(value) is bool:
        return value
    if type(value) is int:
        return value
    if type(value) is float:
        number = value
        if not math.isfinite(number):
            raise ValueError(f"{context} numbers must be finite")
        return number
    if type(value) is list:
        items = cast(list[object], value)
        return [_owned_json_value(item, context=context) for item in items]
    if type(value) is dict:
        mapping = cast(dict[object, object], value)
        if any(type(key) is not str for key in mapping):
            raise ValueError(f"{context} object keys must be strings")
        return {cast(str, key): _owned_json_value(item, context=context) for key, item in mapping.items()}
    raise ValueError(f"{context} must contain only JSON values")


def _canonical_json_bytes(value: object, *, context: str, enforce_size: bool) -> bytes:
    owned = trajectory_json_value(value)
    encoded = json.dumps(
        owned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    if enforce_size and len(encoded) > _MAX_METADATA_BYTES:
        raise ValueError(f"{context} size must not exceed {_MAX_METADATA_BYTES} bytes")
    return encoded


def canonical_trajectory_digest(value: object) -> str:
    """Hash strict canonical JSON using sorted, compact, UTF-8 bytes."""

    encoded = _canonical_json_bytes(value, context="trajectory metadata", enforce_size=True)
    return sha256(encoded).hexdigest()


def _validated_frozen_json_value(value: object, *, context: str) -> FrozenJsonValue:
    if value is None or type(value) in (str, bool, int):
        return cast(str | bool | int | None, value)
    if type(value) is float:
        number = cast(float, value)
        if not math.isfinite(number):
            raise ValueError(f"{context} numbers must be finite")
        return number
    if isinstance(value, FrozenJsonObject):
        return FrozenJsonObject(value._items)
    if isinstance(value, FrozenJsonArray):
        return FrozenJsonArray(value._items)
    if type(value) is dict:
        mapping = cast(dict[object, object], value)
        return FrozenJsonObject(tuple(mapping.items()))
    if type(value) in (list, tuple):
        items = cast(list[object] | tuple[object, ...], value)
        return FrozenJsonArray(tuple(items))
    raise ValueError(f"{context} must contain only JSON values")


def _freeze_owned_json_value(value: JsonValue) -> FrozenJsonValue:
    return _validated_frozen_json_value(value, context="frozen JSON value")


def trajectory_json_value(value: object) -> JsonValue:
    """Return an owned plain-JSON value suitable for persistence and canonical encoding."""

    if isinstance(value, FrozenJsonObject):
        validated = FrozenJsonObject(value._items)
        return {key: trajectory_json_value(item) for key, item in validated.items()}
    if isinstance(value, FrozenJsonArray):
        validated = FrozenJsonArray(value._items)
        return [trajectory_json_value(item) for item in validated]
    return _owned_json_value(value, context="trajectory JSON value")


def _owned_json_object(value: object, *, context: str) -> FrozenJsonObject:
    if isinstance(value, FrozenJsonObject):
        frozen = FrozenJsonObject(value._items)
    elif type(value) is dict:
        owned = _owned_json_value(cast(dict[object, object], value), context=context)
        assert isinstance(owned, dict)
        candidate = _freeze_owned_json_value(owned)
        assert isinstance(candidate, FrozenJsonObject)
        frozen = candidate
    else:
        raise ValueError(f"{context} must be a JSON object")
    _ = _canonical_json_bytes(frozen, context=context, enforce_size=True)
    return frozen


def _owned_json_object_tuple(value: object, *, context: str) -> tuple[FrozenJsonObject, ...]:
    if type(value) is not tuple:
        raise ValueError(f"{context} must be an immutable tuple")
    items = cast(tuple[object, ...], value)
    result = tuple(_owned_json_object(item, context=context) for item in items)
    aggregate = [trajectory_json_value(item) for item in result]
    _ = _canonical_json_bytes(aggregate, context=context, enforce_size=True)
    return result


@dataclass(frozen=True, slots=True, config=_DATACLASS_CONFIG)
class LearningTrajectoryFrame:
    """One delivered cross-mode interval in canonical Celsius units."""

    sequence: NonNegativeInt
    monotonic_start_ms: NonNegativeInt
    monotonic_end_ms: NonNegativeInt
    wall_start_ms: NonNegativeInt
    wall_end_ms: NonNegativeInt
    chamber_temperature_c: FiniteFloat
    temperature_sample_monotonic_ms: NonNegativeInt
    temperature_sample_wall_ms: NonNegativeInt
    temperature_sample_age_ms: NonNegativeInt
    temperature_sample_wall_age_ms: StrictInt
    temperature_sample_clock_skew_ms: StrictInt
    source_temperature_units: Literal["C", "F"]
    settings_revision: NonNegativeInt
    probe_valid: bool
    probe_source: NonBlankString | None
    ambient_temperature_c: FiniteFloat
    ambient_source: NonBlankString
    ambient_uncertainty_c: NonNegativeFloat
    delivered_auger_on_seconds: NonNegativeFloat
    realized_auger_duty: BoundedLoad
    normalized_combustion_load: BoundedLoad
    delivered_fan_on_seconds: NonNegativeFloat
    fan_duty_integral_seconds: NonNegativeFloat
    mean_actual_fan_duty: BoundedLoad
    auger_delivery_certainty: FrameDeliveryCertainty
    fan_delivery_certainty: FrameDeliveryCertainty
    effective_mode: NonBlankString
    recipe_step_id: NonBlankString | None
    complete: bool
    continuous: bool
    partial: bool
    boundary_reason: TrajectoryBreakReason | None
    calibration_origin: bool = False

    @model_validator(mode="after")
    def validate_frame(self) -> LearningTrajectoryFrame:
        monotonic_duration_ms = self.monotonic_end_ms - self.monotonic_start_ms
        wall_duration_ms = self.wall_end_ms - self.wall_start_ms
        if monotonic_duration_ms <= 0:
            raise ValueError("trajectory frame interval must be positive")
        if wall_duration_ms != monotonic_duration_ms:
            raise ValueError("wall and monotonic frame durations must agree")
        if self.temperature_sample_monotonic_ms > self.monotonic_end_ms:
            raise ValueError("temperature sample must not follow its frame end")
        monotonic_sample_age_ms = self.monotonic_end_ms - self.temperature_sample_monotonic_ms
        wall_sample_age_ms = self.wall_end_ms - self.temperature_sample_wall_ms
        if self.temperature_sample_age_ms != monotonic_sample_age_ms:
            raise ValueError("temperature sample monotonic age must match the monotonic frame-end difference")
        if self.temperature_sample_wall_age_ms != wall_sample_age_ms:
            raise ValueError("temperature sample wall age must match the wall frame-end difference")
        if self.temperature_sample_clock_skew_ms != wall_sample_age_ms - monotonic_sample_age_ms:
            raise ValueError("temperature sample clock skew must equal wall age minus monotonic age")
        if self.partial:
            if monotonic_duration_ms >= _FRAME_MILLISECONDS:
                raise ValueError("partial trajectory frame must be shorter than twenty seconds")
            if self.complete:
                raise ValueError("partial trajectory frame cannot be complete")
            if self.boundary_reason is None:
                raise ValueError("partial trajectory frame requires a typed boundary reason")
        else:
            if monotonic_duration_ms != _FRAME_MILLISECONDS:
                raise ValueError("full trajectory frame must be exactly twenty seconds")
            if not self.complete:
                raise ValueError("full trajectory frame must be complete")
            if self.boundary_reason is not None:
                raise ValueError("only a partial trajectory frame may carry a boundary reason")
        duration_seconds = monotonic_duration_ms / 1_000
        if self.delivered_auger_on_seconds > duration_seconds:
            raise ValueError("delivered auger time must not exceed frame duration")
        if self.delivered_fan_on_seconds > duration_seconds:
            raise ValueError("delivered fan time must not exceed frame duration")
        if self.fan_duty_integral_seconds > duration_seconds:
            raise ValueError("fan duty integral must not exceed frame duration")
        realized_auger_duty = self.delivered_auger_on_seconds / duration_seconds
        if not math.isclose(self.realized_auger_duty, realized_auger_duty, rel_tol=0, abs_tol=1e-12):
            raise ValueError("realized auger duty must match delivered auger time")
        mean_fan_duty = self.fan_duty_integral_seconds / duration_seconds
        if not math.isclose(self.mean_actual_fan_duty, mean_fan_duty, rel_tol=0, abs_tol=1e-12):
            raise ValueError("mean actual fan duty must match its delivered integral")
        if self.probe_valid != (self.probe_source is not None):
            raise ValueError("probe validity must agree with probe source provenance")
        return self


@dataclass(frozen=True, slots=True, config=_DATACLASS_CONFIG)
class HoldEntrySample:
    """Measured chamber-state anchor at entry to Hold; never a residual."""

    monotonic_ms: NonNegativeInt
    wall_ms: NonNegativeInt
    chamber_temperature_c: FiniteFloat
    probe_valid: bool
    probe_source: NonBlankString | None

    @model_validator(mode="after")
    def validate_sample(self) -> HoldEntrySample:
        if self.probe_valid != (self.probe_source is not None):
            raise ValueError("Hold-entry probe validity must agree with source provenance")
        return self


def _frame_json(frame: LearningTrajectoryFrame) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "sequence": frame.sequence,
        "monotonic_start_ms": frame.monotonic_start_ms,
        "monotonic_end_ms": frame.monotonic_end_ms,
        "wall_start_ms": frame.wall_start_ms,
        "wall_end_ms": frame.wall_end_ms,
        "chamber_temperature_c": frame.chamber_temperature_c,
        "temperature_sample_monotonic_ms": frame.temperature_sample_monotonic_ms,
        "temperature_sample_wall_ms": frame.temperature_sample_wall_ms,
        "temperature_sample_age_ms": frame.temperature_sample_age_ms,
        "temperature_sample_wall_age_ms": frame.temperature_sample_wall_age_ms,
        "temperature_sample_clock_skew_ms": frame.temperature_sample_clock_skew_ms,
        "source_temperature_units": frame.source_temperature_units,
        "settings_revision": frame.settings_revision,
        "probe_valid": frame.probe_valid,
        "probe_source": frame.probe_source,
        "ambient_temperature_c": frame.ambient_temperature_c,
        "ambient_source": frame.ambient_source,
        "ambient_uncertainty_c": frame.ambient_uncertainty_c,
        "delivered_auger_on_seconds": frame.delivered_auger_on_seconds,
        "realized_auger_duty": frame.realized_auger_duty,
        "normalized_combustion_load": frame.normalized_combustion_load,
        "delivered_fan_on_seconds": frame.delivered_fan_on_seconds,
        "fan_duty_integral_seconds": frame.fan_duty_integral_seconds,
        "mean_actual_fan_duty": frame.mean_actual_fan_duty,
        "auger_delivery_certainty": frame.auger_delivery_certainty.value,
        "fan_delivery_certainty": frame.fan_delivery_certainty.value,
        "effective_mode": frame.effective_mode,
        "recipe_step_id": frame.recipe_step_id,
        "complete": frame.complete,
        "continuous": frame.continuous,
        "partial": frame.partial,
        "boundary_reason": (frame.boundary_reason.value if frame.boundary_reason is not None else None),
    }
    if frame.calibration_origin:
        payload["calibration_origin"] = True
    return payload


def _hold_entry_json(sample: HoldEntrySample) -> dict[str, JsonValue]:
    return {
        "monotonic_ms": sample.monotonic_ms,
        "wall_ms": sample.wall_ms,
        "chamber_temperature_c": sample.chamber_temperature_c,
        "probe_valid": sample.probe_valid,
        "probe_source": sample.probe_source,
    }


def _validate_frame_tuple_chronology(
    frames: tuple[LearningTrajectoryFrame, ...],
    *,
    label: str,
    allow_boundary_gap: bool,
) -> None:
    for previous, current in pairwise(frames):
        if current.sequence <= previous.sequence:
            raise ValueError(f"{label} frames must be chronological")
        if current.sequence != previous.sequence + 1:
            raise ValueError(f"{label} frames must be contiguous")
        if current.monotonic_start_ms < previous.monotonic_end_ms:
            raise ValueError(f"{label} frames overlap")
        if current.wall_start_ms < previous.wall_end_ms:
            raise ValueError(f"{label} wall intervals overlap")
        monotonic_gap = current.monotonic_start_ms != previous.monotonic_end_ms
        wall_gap = current.wall_start_ms != previous.wall_end_ms
        represented_boundary = previous.partial and previous.boundary_reason is not None
        if (monotonic_gap or wall_gap) and not (allow_boundary_gap and represented_boundary):
            raise ValueError(f"{label} frames must be contiguous")


@dataclass(frozen=True, slots=True, config=_DATACLASS_CONFIG)
class LearningTrajectorySegment:
    """One independently replayable, compatibility-bound trajectory segment."""

    schema_version: PositiveInt
    observation_schema_version: PositiveInt
    segment_id: NonBlankString
    cook_id: NonBlankString
    trajectory_session_id: NonBlankString
    trace_session_ids: tuple[NonBlankString, ...]
    collection_provenance: FrozenJsonObject
    configuration_provenance: FrozenJsonObject
    cadence_digest: Digest
    model_structure_digest: Digest
    held_physics_digest: Digest
    delay_input_mapping_digest: Digest
    actuation_mapping_digest: Digest
    scored_fan_regime_digest: Digest
    ambient_semantics_digest: Digest
    pre_roll_frames: tuple[LearningTrajectoryFrame, ...]
    hold_entry: HoldEntrySample | None
    scored_hold_frames: tuple[LearningTrajectoryFrame, ...]
    generation_audit_ranges: tuple[FrozenJsonObject, ...]
    start_monotonic_ms: NonNegativeInt
    end_monotonic_ms: NonNegativeInt
    start_wall_ms: NonNegativeInt
    end_wall_ms: NonNegativeInt
    start_sequence: NonNegativeInt
    end_sequence: NonNegativeInt
    pre_roll_end_reason: TrajectoryBreakReason | None
    terminal_break_reason: TrajectoryBreakReason | None
    state: Literal["open", "finalized", "quarantined"]
    source_trace_digest: Digest
    source_schema_version: PositiveInt
    source_row_digest: Digest
    build_provenance: FrozenJsonObject
    fit_partition_digest: Digest = std_field(init=False)
    content_digest: Digest = std_field(init=False)

    @field_validator("collection_provenance", "configuration_provenance", "build_provenance", mode="before")
    @classmethod
    def own_provenance(cls, value: object) -> object:
        return _owned_json_object(value, context="trajectory provenance")

    @field_validator("generation_audit_ranges", mode="before")
    @classmethod
    def own_generation_audit(cls, value: object) -> object:
        return _owned_json_object_tuple(value, context="generation audit provenance")

    @model_validator(mode="after")
    def validate_segment(self) -> LearningTrajectorySegment:
        if self.observation_schema_version != TRAJECTORY_OBSERVATION_SCHEMA_VERSION:
            raise ValueError("older trajectory observation schema is non-scoreable")
        if not self.pre_roll_frames and not self.scored_hold_frames:
            raise ValueError("trajectory segment requires at least one frame")
        if not self.trace_session_ids or len(set(self.trace_session_ids)) != len(self.trace_session_ids):
            raise ValueError("trace session identities must be non-empty and unique")
        _validate_frame_tuple_chronology(
            self.pre_roll_frames,
            label="pre-roll",
            allow_boundary_gap=False,
        )
        _validate_frame_tuple_chronology(
            self.scored_hold_frames,
            label="scored",
            allow_boundary_gap=False,
        )
        for frame in self.pre_roll_frames:
            if not frame.continuous:
                raise ValueError("pre-roll frames must be continuous")
            if frame.auger_delivery_certainty is not FrameDeliveryCertainty.EXACT:
                raise ValueError("pre-roll frames require exact auger delivery")
            if frame.fan_delivery_certainty is not FrameDeliveryCertainty.EXACT:
                raise ValueError("pre-roll frames require exact fan delivery")
        for frame in self.scored_hold_frames:
            if frame.partial:
                raise ValueError("partial frames are forbidden in scored observations")
            if not frame.complete or not frame.continuous or not frame.probe_valid:
                raise ValueError("scored observations must be complete, continuous, and probe-valid")
            if frame.effective_mode != "Hold":
                raise ValueError("scored observations require effective Hold mode")
            if frame.auger_delivery_certainty is not FrameDeliveryCertainty.EXACT:
                raise ValueError("scored observations require exact auger delivery")
            if frame.fan_delivery_certainty is not FrameDeliveryCertainty.EXACT:
                raise ValueError("scored observations require exact fan delivery")
        if self.pre_roll_frames and self.scored_hold_frames:
            previous = self.pre_roll_frames[-1]
            current = self.scored_hold_frames[0]
            if current.sequence <= previous.sequence:
                raise ValueError("pre-roll and scored frames overlap or are not chronological")
            if current.sequence != previous.sequence + 1:
                raise ValueError("pre-roll and scored frames must be contiguous")
            if current.monotonic_start_ms < previous.monotonic_end_ms:
                raise ValueError("pre-roll and scored frames overlap")
            if current.wall_start_ms < previous.wall_end_ms:
                raise ValueError("pre-roll and scored wall intervals overlap")
            has_gap = (
                current.monotonic_start_ms != previous.monotonic_end_ms or current.wall_start_ms != previous.wall_end_ms
            )
            if has_gap and not (previous.partial and previous.boundary_reason is not None):
                raise ValueError("pre-roll and scored frames must be contiguous")
        all_frames = (*self.pre_roll_frames, *self.scored_hold_frames)
        first = all_frames[0]
        last = all_frames[-1]
        if (
            self.start_monotonic_ms != first.monotonic_start_ms
            or self.end_monotonic_ms != last.monotonic_end_ms
            or self.start_wall_ms != first.wall_start_ms
            or self.end_wall_ms != last.wall_end_ms
            or self.start_sequence != first.sequence
            or self.end_sequence != last.sequence
        ):
            raise ValueError("segment time and sequence bounds must match its retained frames")
        if self.scored_hold_frames:
            if self.hold_entry is None:
                raise ValueError("scored observations require a Hold-entry anchor")
            first_scored = self.scored_hold_frames[0]
            if (
                self.hold_entry.monotonic_ms < first_scored.monotonic_start_ms
                or self.hold_entry.wall_ms < first_scored.wall_start_ms
            ):
                raise ValueError("Hold-entry anchor must fall inside the first scored interval")
            if (
                self.hold_entry.monotonic_ms - first_scored.monotonic_start_ms
                != self.hold_entry.wall_ms - first_scored.wall_start_ms
            ):
                raise ValueError("Hold-entry wall and monotonic offsets must agree inside the first scored interval")
            if (
                self.hold_entry.monotonic_ms > first_scored.temperature_sample_monotonic_ms
                or self.hold_entry.wall_ms > first_scored.temperature_sample_wall_ms
            ):
                raise ValueError("Hold-entry sample must not follow the first scored temperature sample")
            if not self.hold_entry.probe_valid:
                raise ValueError("Hold-entry anchor must be probe-valid")
        elif self.hold_entry is not None:
            pre_roll_end = self.pre_roll_frames[-1]
            if self.hold_entry.monotonic_ms != pre_roll_end.monotonic_end_ms:
                raise ValueError("Hold-entry monotonic boundary must match pre-roll end")
            if self.hold_entry.wall_ms != pre_roll_end.wall_end_ms:
                raise ValueError("Hold-entry wall boundary must match pre-roll end")
            if not self.hold_entry.probe_valid:
                raise ValueError("Hold-entry anchor must be probe-valid")
        if self.state == "open" and self.terminal_break_reason is not None:
            raise ValueError("open trajectory segment cannot have a terminal break reason")
        if self.state != "open" and self.terminal_break_reason is None:
            raise ValueError("closed trajectory segment requires a terminal break reason")

        partition_payload: dict[str, JsonValue] = {
            "segment_schema_version": self.schema_version,
            "observation_schema_version": self.observation_schema_version,
            "temperature_unit": "celsius",
            "cadence_digest": self.cadence_digest,
            "model_structure_digest": self.model_structure_digest,
            "held_physics_digest": self.held_physics_digest,
            "delay_input_mapping_digest": self.delay_input_mapping_digest,
            "actuation_mapping_digest": self.actuation_mapping_digest,
            "scored_fan_regime_digest": self.scored_fan_regime_digest,
            "ambient_semantics_digest": self.ambient_semantics_digest,
        }
        object.__setattr__(self, "fit_partition_digest", canonical_trajectory_digest(partition_payload))
        content_payload: dict[str, object] = {
            "schema_version": self.schema_version,
            "observation_schema_version": self.observation_schema_version,
            "segment_id": self.segment_id,
            "cook_id": self.cook_id,
            "trajectory_session_id": self.trajectory_session_id,
            "trace_session_ids": list(self.trace_session_ids),
            "collection_provenance": trajectory_json_value(self.collection_provenance),
            "configuration_provenance": trajectory_json_value(self.configuration_provenance),
            "fit_partition_digest": self.fit_partition_digest,
            "cadence_digest": self.cadence_digest,
            "model_structure_digest": self.model_structure_digest,
            "held_physics_digest": self.held_physics_digest,
            "delay_input_mapping_digest": self.delay_input_mapping_digest,
            "actuation_mapping_digest": self.actuation_mapping_digest,
            "scored_fan_regime_digest": self.scored_fan_regime_digest,
            "ambient_semantics_digest": self.ambient_semantics_digest,
            "pre_roll_frames": [_frame_json(frame) for frame in self.pre_roll_frames],
            "hold_entry": _hold_entry_json(self.hold_entry) if self.hold_entry is not None else None,
            "scored_hold_frames": [_frame_json(frame) for frame in self.scored_hold_frames],
            "generation_audit_ranges": [trajectory_json_value(item) for item in self.generation_audit_ranges],
            "start_monotonic_ms": self.start_monotonic_ms,
            "end_monotonic_ms": self.end_monotonic_ms,
            "start_wall_ms": self.start_wall_ms,
            "end_wall_ms": self.end_wall_ms,
            "start_sequence": self.start_sequence,
            "end_sequence": self.end_sequence,
            "pre_roll_end_reason": (self.pre_roll_end_reason.value if self.pre_roll_end_reason is not None else None),
            "terminal_break_reason": (
                self.terminal_break_reason.value if self.terminal_break_reason is not None else None
            ),
            "state": self.state,
            "source_trace_digest": self.source_trace_digest,
            "source_schema_version": self.source_schema_version,
            "source_row_digest": self.source_row_digest,
            "build_provenance": trajectory_json_value(self.build_provenance),
        }
        content_bytes = _canonical_json_bytes(
            content_payload,
            context="trajectory segment content",
            enforce_size=False,
        )
        object.__setattr__(self, "content_digest", sha256(content_bytes).hexdigest())
        return self


@dataclass(frozen=True, slots=True, config=_DATACLASS_CONFIG)
class FitCorpusSlice:
    """One immutable segment-prefix contribution to a fit corpus."""

    segment_id: NonBlankString
    through_ordinal: NonNegativeInt
    prefix_digest: Digest
    pre_roll_count: NonNegativeInt
    scored_count: NonNegativeInt

    @model_validator(mode="after")
    def validate_slice(self) -> FitCorpusSlice:
        total_count = self.pre_roll_count + self.scored_count
        if total_count == 0:
            raise ValueError("fit corpus slice cannot be empty")
        if self.through_ordinal != total_count - 1:
            raise ValueError("through ordinal must equal retained prefix count minus one")
        return self


def _corpus_payload(identity: FitCorpusIdentity) -> dict[str, JsonValue]:
    return {
        "schema_version": identity.schema_version,
        "corpus_revision": identity.corpus_revision,
        "fit_partition_digest": identity.fit_partition_digest,
        "slices": [
            {
                "segment_id": item.segment_id,
                "through_ordinal": item.through_ordinal,
                "prefix_digest": item.prefix_digest,
                "pre_roll_count": item.pre_roll_count,
                "scored_count": item.scored_count,
            }
            for item in identity.slices
        ],
    }


@dataclass(frozen=True, slots=True, config=_DATACLASS_CONFIG)
class FitCorpusIdentity:
    """Canonical identity of an ordered immutable compatible corpus prefix."""

    schema_version: PositiveInt
    corpus_revision: NonNegativeInt
    fit_partition_digest: Digest
    slices: Annotated[tuple[FitCorpusSlice, ...], Field(min_length=1, max_length=_MAX_CORPUS_SLICES)]
    corpus_digest: Digest

    @model_validator(mode="after")
    def validate_identity(self) -> FitCorpusIdentity:
        segment_ids = tuple(item.segment_id for item in self.slices)
        if len(set(segment_ids)) != len(segment_ids):
            raise ValueError("fit corpus slices must have unique segment identities")
        expected_digest = canonical_trajectory_digest(_corpus_payload(self))
        if self.corpus_digest != expected_digest:
            raise ValueError("corpus digest must match the canonical ordered slices")
        return self


@dataclass(frozen=True, slots=True, config=_DATACLASS_CONFIG)
class ModelFitLineage:
    """Immutable request/result lineage for one cumulative-corpus model fit."""

    request_id: NonBlankString
    parent_incumbent_digest: Digest
    parent_incumbent_generation: NonNegativeInt
    candidate_generation: NonNegativeInt
    fit_corpus: FitCorpusIdentity
    fit_corpus_digest: Digest
    trigger_origin: NonBlankString
    result_status: Literal["queued", "running", "succeeded", "failed", "stale"]
    candidate_digest: Digest | None

    @model_validator(mode="after")
    def validate_lineage(self) -> ModelFitLineage:
        if self.fit_corpus_digest != self.fit_corpus.corpus_digest:
            raise ValueError("fit corpus lineage digest must match its corpus identity")
        if self.result_status == "succeeded" and self.candidate_digest is None:
            raise ValueError("successful fit lineage requires a candidate digest")
        if self.result_status != "succeeded" and self.candidate_digest is not None:
            raise ValueError("only successful fit lineage may carry a candidate digest")
        return self


def canonical_model_fit_lineage_digest(lineage: ModelFitLineage) -> str:
    """Return the canonical digest of one complete immutable fit lineage."""

    if not isinstance(lineage, ModelFitLineage):
        raise TypeError("lineage must be a ModelFitLineage")
    encoded = json.dumps(
        asdict(lineage),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()
