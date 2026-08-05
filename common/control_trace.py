"""Typed, versioned records for controller control-quality traces.

The payload classes are immutable Pydantic dataclasses.  ``ControlTraceRecord``
is the sole JSON/database boundary: runtime code passes typed payloads and
storage receives a small, explicit row object rather than an untyped payload.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass as std_dataclass
from enum import StrEnum
from typing import Annotated, ClassVar, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, ValidationError, model_validator
from pydantic.dataclasses import dataclass

from controller.applied_output import OutputSource

TRACE_SCHEMA_VERSION = 1

FiniteFloat: TypeAlias = Annotated[float, Field(allow_inf_nan=False, strict=True)]
NonNegativeFloat: TypeAlias = Annotated[FiniteFloat, Field(ge=0)]
PositiveFloat: TypeAlias = Annotated[FiniteFloat, Field(gt=0)]
NonNegativeInt: TypeAlias = Annotated[int, Field(ge=0, strict=True)]
NonBlankString: TypeAlias = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]


class ControllerType(StrEnum):
    PID = "pid"
    PID_SP = "pid_sp"
    MPC = "mpc"


class TraceEventKind(StrEnum):
    SESSION = "session"
    CONTROL_UPDATE = "control_update"
    ALLOCATION = "allocation"
    ACTUATION_FRAME = "actuation_frame"
    APPLIED_OUTPUT = "applied_output"
    SAFETY_EVENT = "safety_event"
    MODEL_EVENT = "model_event"
    RECORDER_GAP = "recorder_gap"


class ActuationMode(StrEnum):
    FIXED_CYCLE = "fixed_cycle"
    FRAMED_PULSE = "framed_pulse"


class InhibitReason(StrEnum):
    NONE = "none"
    LID_OPEN = "lid_open"
    MANUAL_OVERRIDE = "manual_override"
    SAFETY = "safety"
    STALE_COMMAND = "stale_command"


class ModelEventType(StrEnum):
    RESTORE = "restore"
    ADOPT = "adopt"
    REJECT = "reject"
    REFIT = "refit"
    SCHEMA_INVALIDATED = "schema_invalidated"


class ControllerBranch(StrEnum):
    NONE = "none"
    INITIALIZATION = "initialization"
    FULL_HEAT = "full_heat"
    TARGET_REACHED = "target_reached"
    RESET = "reset"
    OVERSHOOT = "overshoot"


class MpcFailureState(StrEnum):
    SUCCESS = "success"
    POLICY_EXCEPTION = "policy_exception"


class SafetyEventType(StrEnum):
    LID_DETECTED = "lid_detected"
    LID_CLEARED = "lid_cleared"
    MANUAL_TAKEOVER = "manual_takeover"
    MANUAL_RELEASE = "manual_release"
    STOP = "stop"
    ERROR = "error"
    TEMPERATURE_GUARD = "temperature_guard"
    CONTROLLER_FALLBACK = "controller_fallback"
    CONTROLLER_RECONFIGURE = "controller_reconfigure"
    SCHEDULER_RESET = "scheduler_reset"


class AllocationClampReason(StrEnum):
    NONE = "none"
    AUGER_MAX = "auger_max"
    FAN_MIN = "fan_min"
    FAN_MAX = "fan_max"


_DATACLASS_CONFIG = ConfigDict(extra="forbid", strict=True, validate_default=True)


@dataclass(frozen=True, slots=True, config=_DATACLASS_CONFIG)
class TraceSetting:
    """One sanitized, scalar controller setting captured at session open."""

    key: NonBlankString
    value: str | int | FiniteFloat | bool


@dataclass(frozen=True, slots=True, config=_DATACLASS_CONFIG)
class SessionPayload:
    controller: ControllerType
    controller_config: tuple[TraceSetting, ...]
    temperature_unit: NonBlankString
    control_period_seconds: PositiveFloat
    model_revision: NonNegativeInt | None
    model_provenance: NonBlankString | None
    u_min: FiniteFloat | None
    u_max: FiniteFloat | None
    hold_cycle_seconds: PositiveFloat | None
    pulse_slot_seconds: PositiveFloat | None
    pulse_frame_seconds: PositiveFloat | None
    fan_authority: bool
    fan_pwm_capable: bool
    fan_min_duty: FiniteFloat
    fan_max_duty: FiniteFloat
    setpoint: FiniteFloat
    ambient_temperature: FiniteFloat
    software_version: NonBlankString
    build_version: NonBlankString
    payload_type: Literal["session"] = "session"

    @model_validator(mode="after")
    def validate_authority(self) -> SessionPayload:
        if self.fan_min_duty > self.fan_max_duty:
            raise ValueError("fan_min_duty must not exceed fan_max_duty")
        if (self.u_min is None) != (self.u_max is None):
            raise ValueError("u_min and u_max must be present together")
        if self.u_min is not None and self.u_max is not None and self.u_min > self.u_max:
            raise ValueError("u_min must not exceed u_max")
        if self.controller is ControllerType.MPC:
            if self.pulse_slot_seconds is None or self.pulse_frame_seconds is None:
                raise ValueError("MPC session requires pulse timing authority")
            slots = self.pulse_frame_seconds / self.pulse_slot_seconds
            if not math.isclose(slots, round(slots), rel_tol=0, abs_tol=1e-9):
                raise ValueError("MPC pulse frame must be divisible by pulse slot")
        elif self.hold_cycle_seconds is None or self.u_min is None or self.u_max is None:
            raise ValueError("fixed-cycle session requires hold_cycle_seconds, u_min, and u_max")
        return self


@dataclass(frozen=True, slots=True, config=_DATACLASS_CONFIG)
class _ControlUpdatePayload:
    monotonic_ms: NonNegativeInt
    wall_ms: NonNegativeInt
    result_revision: NonNegativeInt
    result_age_ms: NonNegativeInt
    control_period_seconds: PositiveFloat
    observed_dt_seconds: NonNegativeFloat
    setpoint: FiniteFloat
    measured_temperature: FiniteFloat
    raw_output: FiniteFloat
    requested_output: FiniteFloat
    actuation_mode: ActuationMode
    prior_requested_auger_duty: FiniteFloat
    prior_realized_auger_duty: FiniteFloat
    requested_fan_duty: FiniteFloat | None
    applied_fan_duty: FiniteFloat | None
    output_source: OutputSource
    inhibit_reason: InhibitReason


@dataclass(frozen=True, slots=True, config=_DATACLASS_CONFIG)
class PidUpdatePayload(_ControlUpdatePayload):
    error: FiniteFloat
    proportional_term: FiniteFloat
    integral_term: FiniteFloat
    derivative_term: FiniteFloat
    integral_accumulator: FiniteFloat
    integral_clamped: bool
    derivative_input: FiniteFloat
    derivative_state: FiniteFloat
    proportional_band: FiniteFloat
    kp: FiniteFloat
    ki: FiniteFloat
    kd: FiniteFloat
    center: FiniteFloat
    previous_temperature: FiniteFloat
    previous_update_ms: NonNegativeInt
    payload_type: Literal["pid_update"] = "pid_update"

    @model_validator(mode="after")
    def validate_actuation_mode(self) -> PidUpdatePayload:
        if self.actuation_mode is not ActuationMode.FIXED_CYCLE:
            raise ValueError("PID diagnostics require FIXED_CYCLE actuation")
        return self


@dataclass(frozen=True, slots=True, config=_DATACLASS_CONFIG)
class PidSpUpdatePayload(_ControlUpdatePayload):
    error: FiniteFloat
    proportional_term: FiniteFloat
    integral_term: FiniteFloat
    derivative_term: FiniteFloat
    integral_accumulator: FiniteFloat
    integral_clamped: bool
    derivative_input: FiniteFloat
    derivative_state: FiniteFloat
    proportional_band: FiniteFloat
    kp: FiniteFloat
    ki: FiniteFloat
    kd: FiniteFloat
    center: FiniteFloat
    previous_temperature: FiniteFloat
    previous_update_ms: NonNegativeInt
    measured_rate: FiniteFloat
    predicted_temperature: FiniteFloat
    predicted_error: FiniteFloat
    tau_seconds: PositiveFloat
    theta_seconds: NonNegativeFloat
    stable_window_seconds: PositiveFloat
    center_factor: FiniteFloat
    new_target_before: bool
    new_target_after: bool
    target_change_temperature: FiniteFloat
    target_change_ms: NonNegativeInt
    branch: ControllerBranch
    payload_type: Literal["pid_sp_update"] = "pid_sp_update"

    @model_validator(mode="after")
    def validate_actuation_mode(self) -> PidSpUpdatePayload:
        if self.actuation_mode is not ActuationMode.FIXED_CYCLE:
            raise ValueError("PID-SP diagnostics require FIXED_CYCLE actuation")
        return self


@dataclass(frozen=True, slots=True, config=_DATACLASS_CONFIG)
class MpcUpdatePayload(_ControlUpdatePayload):
    state_names: tuple[NonBlankString, ...]
    state_values: tuple[FiniteFloat, ...]
    disturbance_estimate: FiniteFloat
    model_revision: NonNegativeInt
    model_provenance: NonBlankString
    raw_policy_firing_load: FiniteFloat | None
    equilibrium_feed_forward: FiniteFloat | None
    residual_move: FiniteFloat | None
    bounded_firing_load: FiniteFloat
    policy_kind: NonBlankString
    failure_state: MpcFailureState
    solve_start_ms: NonNegativeInt
    solve_end_ms: NonNegativeInt
    deadline_miss_count: NonNegativeInt
    stale: bool
    recovered: bool
    predicted_feasible: bool | None
    predicted_steady_load: FiniteFloat | None
    payload_type: Literal["mpc_update"] = "mpc_update"

    @model_validator(mode="after")
    def validate_state_solve_interval_and_actuation_mode(self) -> MpcUpdatePayload:
        # Hold currently applies MPC's allocation through its fixed-cycle
        # producer. The later framed scheduler switches only that producer, so
        # both modes are durable trace contract values.
        if self.actuation_mode not in (ActuationMode.FIXED_CYCLE, ActuationMode.FRAMED_PULSE):
            raise ValueError("MPC diagnostics require a supported actuation mode")
        if len(self.state_names) != len(self.state_values):
            raise ValueError("state_names and state_values must have equal length")
        if self.solve_start_ms > self.solve_end_ms:
            raise ValueError("solve_start_ms must not exceed solve_end_ms")
        raw_components = (self.raw_policy_firing_load, self.equilibrium_feed_forward, self.residual_move)
        if self.failure_state is MpcFailureState.SUCCESS and any(value is None for value in raw_components):
            raise ValueError("successful MPC diagnostics require raw policy components")
        if self.failure_state is not MpcFailureState.SUCCESS and any(value is not None for value in raw_components):
            raise ValueError("failed MPC diagnostics must omit unknown raw policy components")
        return self


@dataclass(frozen=True, slots=True, config=_DATACLASS_CONFIG)
class AllocationPayload:
    result_revision: NonNegativeInt
    normalized_combustion_load: FiniteFloat
    requested_auger_duty: FiniteFloat
    requested_fan_duty: FiniteFloat | None
    q_min: FiniteFloat
    q_max: FiniteFloat
    u_min: FiniteFloat
    u_max: FiniteFloat
    fan_min_pct: FiniteFloat
    fan_max_pct: FiniteFloat
    fan_enabled: bool
    mpc_has_fan_authority: bool
    auger_clamp_reason: AllocationClampReason
    fan_clamp_reason: AllocationClampReason
    allocator_revision: NonNegativeInt
    payload_type: Literal["allocation"] = "allocation"

    @model_validator(mode="after")
    def validate_allocator_inputs(self) -> AllocationPayload:
        if self.q_min > self.q_max:
            raise ValueError("q_min must not exceed q_max")
        if self.u_min > self.u_max:
            raise ValueError("u_min must not exceed u_max")
        if self.fan_min_pct > self.fan_max_pct:
            raise ValueError("fan_min_pct must not exceed fan_max_pct")
        if not self.fan_enabled and self.requested_fan_duty is not None:
            raise ValueError("disabled fan allocation must not request a fan duty")
        if not self.fan_enabled and self.fan_clamp_reason is not AllocationClampReason.NONE:
            raise ValueError("disabled fan allocation must not carry a fan clamp")
        return self


@dataclass(frozen=True, slots=True, config=_DATACLASS_CONFIG)
class FixedCycleFramePayload:
    result_revision: NonNegativeInt
    raw_requested_duty: FiniteFloat
    bounded_duty: FiniteFloat
    u_min: FiniteFloat
    u_max: FiniteFloat
    cycle_start_ms: NonNegativeInt
    cycle_end_ms: NonNegativeInt
    scheduled_on_seconds: NonNegativeFloat
    scheduled_off_seconds: NonNegativeFloat
    actual_on_seconds: NonNegativeFloat
    actual_start_active: bool
    transition_count: NonNegativeInt
    fan_assist_active: bool
    inhibit_reason: InhibitReason
    output_active: bool
    payload_type: Literal["fixed_cycle_frame"] = "fixed_cycle_frame"

    @model_validator(mode="after")
    def validate_cycle(self) -> FixedCycleFramePayload:
        if self.u_min > self.u_max:
            raise ValueError("u_min must not exceed u_max")
        if self.cycle_start_ms > self.cycle_end_ms:
            raise ValueError("cycle_start_ms must not exceed cycle_end_ms")
        duration_seconds = (self.cycle_end_ms - self.cycle_start_ms) / 1000
        if self.actual_on_seconds > duration_seconds:
            raise ValueError("actual_on_seconds must not exceed cycle duration")
        if self.output_active is not (self.actual_start_active ^ bool(self.transition_count % 2)):
            raise ValueError("fixed-cycle transition parity must match start and end state")
        if self.transition_count == 0 and not math.isclose(
            self.actual_on_seconds,
            duration_seconds if self.actual_start_active else 0.0,
            rel_tol=0,
            abs_tol=1e-9,
        ):
            raise ValueError("zero-transition fixed-cycle delivery must match start state")
        return self


@dataclass(frozen=True, slots=True, config=_DATACLASS_CONFIG)
class FramedPulseFramePayload:
    result_revision: NonNegativeInt
    pulse_slot_seconds: PositiveFloat
    frame_seconds: PositiveFloat
    requested_combustion_load: FiniteFloat
    requested_auger_duty: FiniteFloat
    credit_before_seconds: FiniteFloat
    credit_after_seconds: FiniteFloat
    scheduled_on_seconds: NonNegativeFloat
    delivered_on_seconds: NonNegativeFloat
    transition_count: NonNegativeInt
    actual_start_active: bool
    actual_end_active: bool
    requested_fan_duty: FiniteFloat | None
    applied_fan_duty: FiniteFloat | None
    skipped: bool
    stale_command: bool
    inhibit_reason: InhibitReason
    reset_reason: NonBlankString | None
    payload_type: Literal["framed_pulse_frame"] = "framed_pulse_frame"

    @model_validator(mode="after")
    def validate_pulse_timing(self) -> FramedPulseFramePayload:
        slots = self.frame_seconds / self.pulse_slot_seconds
        if not math.isclose(slots, round(slots), rel_tol=0, abs_tol=1e-9):
            raise ValueError("frame_seconds must be divisible by pulse_slot_seconds")
        if self.scheduled_on_seconds > self.frame_seconds:
            raise ValueError("scheduled_on_seconds must not exceed frame_seconds")
        if self.delivered_on_seconds > self.frame_seconds:
            raise ValueError("delivered_on_seconds must not exceed frame_seconds")
        if self.actual_end_active is not (self.actual_start_active ^ bool(self.transition_count % 2)):
            raise ValueError("framed-pulse transition parity must match start and end state")
        if self.transition_count == 0 and not math.isclose(
            self.delivered_on_seconds,
            self.frame_seconds if self.actual_start_active else 0.0,
            rel_tol=0,
            abs_tol=1e-9,
        ):
            raise ValueError("zero-transition framed-pulse delivery must match start state")
        return self


@dataclass(frozen=True, slots=True, config=_DATACLASS_CONFIG)
class AppliedOutputPayload:
    result_revision: NonNegativeInt
    interval_start_ms: NonNegativeInt
    interval_end_ms: NonNegativeInt
    realized_auger_duty: FiniteFloat
    realized_combustion_load: FiniteFloat | None
    actual_fan_duty: FiniteFloat | None
    sample_complete: bool
    output_source: OutputSource
    payload_type: Literal["applied_output"] = "applied_output"

    @model_validator(mode="after")
    def validate_interval(self) -> AppliedOutputPayload:
        if self.interval_start_ms > self.interval_end_ms:
            raise ValueError("interval_start_ms must not exceed interval_end_ms")
        return self


@dataclass(frozen=True, slots=True, config=_DATACLASS_CONFIG)
class SafetyEventPayload:
    event: SafetyEventType
    inhibit_reason: InhibitReason
    result_revision: NonNegativeInt | None
    detail: NonBlankString
    payload_type: Literal["safety_event"] = "safety_event"


@dataclass(frozen=True, slots=True, config=_DATACLASS_CONFIG)
class ModelEventPayload:
    event: ModelEventType
    model_revision: NonNegativeInt | None
    provenance: NonBlankString | None
    detail: NonBlankString
    payload_type: Literal["model_event"] = "model_event"


@dataclass(frozen=True, slots=True, config=_DATACLASS_CONFIG)
class RecorderGapPayload:
    lost_record_count: NonNegativeInt
    gap_start_ms: NonNegativeInt
    gap_end_ms: NonNegativeInt
    payload_type: Literal["recorder_gap"] = "recorder_gap"

    @model_validator(mode="after")
    def validate_gap_interval(self) -> RecorderGapPayload:
        if self.gap_start_ms > self.gap_end_ms:
            raise ValueError("gap_start_ms must not exceed gap_end_ms")
        return self


ControlTracePayload: TypeAlias = Annotated[
    SessionPayload
    | PidUpdatePayload
    | PidSpUpdatePayload
    | MpcUpdatePayload
    | AllocationPayload
    | FixedCycleFramePayload
    | FramedPulseFramePayload
    | AppliedOutputPayload
    | SafetyEventPayload
    | ModelEventPayload
    | RecorderGapPayload,
    Field(discriminator="payload_type"),
]
_PAYLOAD_ADAPTER: TypeAdapter[ControlTracePayload] = TypeAdapter(ControlTracePayload)
_JSON_VALUE_ADAPTER: TypeAdapter[object] = TypeAdapter(object)


@std_dataclass(frozen=True, slots=True)
class ControlTraceDbRow:
    """The seven persisted columns, excluding SQLite's auto-generated ``id``."""

    ts_ms: int
    session_id: str
    cook_id: str | None
    controller: str
    event_kind: str
    schema_version: int
    payload: str


class ControlTraceRecord(BaseModel):
    """Validated envelope for an indexed, discriminated trace payload."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True, strict=True)

    ts_ms: NonNegativeInt
    session_id: NonBlankString
    cook_id: NonBlankString | None = None
    controller: ControllerType
    event_kind: TraceEventKind
    schema_version: Literal[1] = TRACE_SCHEMA_VERSION
    payload: ControlTracePayload

    @model_validator(mode="after")
    def validate_payload_match(self) -> ControlTraceRecord:
        expected_event = _payload_event_kind(self.payload)
        if self.event_kind is not expected_event:
            raise ValueError("event_kind does not match payload_type")

        if isinstance(self.payload, SessionPayload) and self.controller is not self.payload.controller:
            raise ValueError("controller does not match session payload")
        if isinstance(self.payload, PidUpdatePayload) and self.controller is not ControllerType.PID:
            raise ValueError("controller does not match PID diagnostics")
        if isinstance(self.payload, PidSpUpdatePayload) and self.controller is not ControllerType.PID_SP:
            raise ValueError("controller does not match PID-SP diagnostics")
        if isinstance(self.payload, MpcUpdatePayload) and self.controller is not ControllerType.MPC:
            raise ValueError("controller does not match MPC diagnostics")
        if isinstance(self.payload, AllocationPayload) and self.controller is not ControllerType.MPC:
            raise ValueError("allocation records are MPC-only")
        if isinstance(self.payload, FramedPulseFramePayload) and self.controller is not ControllerType.MPC:
            raise ValueError("framed-pulse records are MPC-only")
        return self

    def to_db_row(self) -> ControlTraceDbRow:
        """Serialize one validated record into the exact SQLite table columns."""
        return ControlTraceDbRow(
            ts_ms=self.ts_ms,
            session_id=self.session_id,
            cook_id=self.cook_id,
            controller=self.controller.value,
            event_kind=self.event_kind.value,
            schema_version=self.schema_version,
            payload=_PAYLOAD_ADAPTER.dump_json(self.payload).decode("utf-8"),
        )

    @classmethod
    def from_db_row(
        cls,
        row: ControlTraceDbRow | Mapping[str, object] | Sequence[object],
    ) -> ControlTraceRecord:
        """Validate an explicit persisted row before exposing its typed payload."""
        if isinstance(row, ControlTraceDbRow):
            values = (
                row.ts_ms,
                row.session_id,
                row.cook_id,
                row.controller,
                row.event_kind,
                row.schema_version,
                row.payload,
            )
        elif isinstance(row, Mapping):
            try:
                values = tuple(
                    row[name]
                    for name in (
                        "ts_ms",
                        "session_id",
                        "cook_id",
                        "controller",
                        "event_kind",
                        "schema_version",
                        "payload",
                    )
                )
            except KeyError as exc:
                raise ValueError(f"control trace row is missing {exc.args[0]!r}") from exc
        else:
            if len(row) != 7:
                raise ValueError("control trace row must contain seven values")
            values = tuple(row)

        ts_ms, session_id, cook_id, controller, event_kind, schema_version, payload_json = values
        if not isinstance(payload_json, str):
            raise ValueError("control trace payload column must be JSON text")
        try:
            decoded_payload: object = _JSON_VALUE_ADAPTER.validate_json(payload_json)
        except ValidationError as exc:
            raise ValueError("control trace payload column is invalid JSON") from exc
        envelope_json = json.dumps(
            {
                "ts_ms": ts_ms,
                "session_id": session_id,
                "cook_id": cook_id,
                "controller": controller,
                "event_kind": event_kind,
                "schema_version": schema_version,
                "payload": decoded_payload,
            }
        )
        return cls.model_validate_json(envelope_json)


def _payload_event_kind(payload: ControlTracePayload) -> TraceEventKind:
    if isinstance(payload, SessionPayload):
        return TraceEventKind.SESSION
    if isinstance(payload, (PidUpdatePayload, PidSpUpdatePayload, MpcUpdatePayload)):
        return TraceEventKind.CONTROL_UPDATE
    if isinstance(payload, AllocationPayload):
        return TraceEventKind.ALLOCATION
    if isinstance(payload, (FixedCycleFramePayload, FramedPulseFramePayload)):
        return TraceEventKind.ACTUATION_FRAME
    if isinstance(payload, AppliedOutputPayload):
        return TraceEventKind.APPLIED_OUTPUT
    if isinstance(payload, SafetyEventPayload):
        return TraceEventKind.SAFETY_EVENT
    if isinstance(payload, ModelEventPayload):
        return TraceEventKind.MODEL_EVENT
    return TraceEventKind.RECORDER_GAP
