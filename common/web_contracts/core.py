from __future__ import annotations

from collections.abc import Mapping
from typing import Generic, Literal, TypeVar

from pydantic import (
    Field,
    SerializationInfo,
    SerializerFunctionWrapHandler,
    TypeAdapter,
    field_validator,
    model_serializer,
)

from .base import ExtensibleWireModel, FiniteFloat, WireModel
from .control import JsonValue, PelletDbSchema

T = TypeVar("T")
FiniteNumber = int | FiniteFloat
_JSON_VALUE_ADAPTER = TypeAdapter(JsonValue)


class ApiEnvelope(WireModel, Generic[T]):
    result: Literal["OK", "ERROR"]
    message: str = ""
    data: T | None = None


class _SparseExtensibleWireModel(ExtensibleWireModel):
    """Retain the wire distinction between an absent key and an explicit null."""

    @model_serializer(mode="wrap")
    def _omit_unset_fields(
        self,
        handler: SerializerFunctionWrapHandler,
        info: SerializationInfo,
    ):
        serialized = handler(self)
        for name, field in type(self).model_fields.items():
            if name not in self.model_fields_set:
                key = field.serialization_alias if info.by_alias and field.serialization_alias else name
                serialized.pop(key, None)
        return serialized


class ProbeStatusPayload(_SparseExtensibleWireModel):
    battery_charging: bool | None = Field(None, alias="batteryCharging")
    battery_percentage: FiniteNumber | None = Field(None, alias="batteryPercentage")
    battery_voltage: FiniteNumber | None = Field(None, alias="batteryVoltage")
    connected: bool | None = None
    error: bool | str | None = None
    last_temp: FiniteNumber | None = Field(None, alias="lastTemp")
    last_reading_age: int | None = Field(None, alias="lastReadingAge")


class ProbeDataPayload(_SparseExtensibleWireModel):
    title: str
    label: str
    eta: FiniteNumber | str | None
    temp: FiniteNumber | None
    set_temp: FiniteNumber = Field(alias="setTemp")
    max_temp: FiniteNumber = Field(alias="maxTemp")
    target: FiniteNumber
    low_limit_temp: FiniteNumber = Field(alias="lowLimitTemp")
    high_limit_temp: FiniteNumber = Field(alias="highLimitTemp")
    target_req: bool = Field(alias="targetReq")
    has_notifications: bool = Field(alias="hasNotifications")
    low_limit_req: bool = Field(alias="lowLimitReq")
    high_limit_req: bool = Field(alias="highLimitReq")
    high_limit_shutdown: bool = Field(alias="highLimitShutdown")
    high_limit_triggered: bool = Field(alias="highLimitTriggered")
    low_limit_shutdown: bool = Field(alias="lowLimitShutdown")
    low_limit_reignite: bool = Field(alias="lowLimitReignite")
    low_limit_triggered: bool = Field(alias="lowLimitTriggered")
    target_shutdown: bool = Field(alias="targetShutdown")
    target_keep_warm: bool = Field(alias="targetKeepWarm")
    device: str | None = None
    status: ProbeStatusPayload


class TimerPayload(WireModel):
    start: int
    paused: int
    end: int
    keep_warm: bool = Field(alias="keepWarm")
    shutdown: bool


class OutputPayload(WireModel):
    fan: bool
    auger: bool
    igniter: bool
    power: bool


class RecipeStatusPayload(WireModel):
    recipe_mode: bool = Field(alias="recipeMode")
    filename: str
    mode: str
    paused: bool
    step: int


class ThermocoupleHealthReportView(WireModel):
    state: Literal["unmonitored", "healthy", "suspected", "confirmed"]
    faults: list[Literal["open", "short", "malfunction"]]
    evidence: list[
        Literal[
            "hardware",
            "junction-collapse",
            "stuck-response",
            "excitation-response",
            "implausible-step",
        ]
    ]
    temperature_valid: bool = Field(alias="temperatureValid")
    detail: dict[str, object]

    @field_validator("detail")
    @classmethod
    def _detail_is_json(cls, detail: dict[str, object]) -> dict[str, object]:
        for value in detail.values():
            _JSON_VALUE_ADAPTER.validate_python(value, strict=True)
        return detail


class ThermocoupleHealthDetectorView(WireModel):
    source: Literal["hardware", "software", "mixed"]
    policy: Literal["off", "observe", "enforce"]


class ThermocoupleHealthFreshnessView(WireModel):
    current: bool
    last_reported_age_s: FiniteNumber = Field(alias="lastReportedAgeS")


def project_thermocouple_health_outcome(
    role: object,
    state: object,
    evidence: object,
    detail: object,
) -> Literal["none", "notify_only", "unavailable", "stopped"]:
    """Project control impact from the report's own safety authority."""
    if state != "confirmed":
        return "none"
    if role != "Primary":
        return "unavailable"
    if isinstance(evidence, list) and "hardware" in evidence:
        return "stopped"
    authority = detail.get("authority") if isinstance(detail, Mapping) else None
    if authority == "stop":
        return "stopped"
    if authority == "notify_only":
        return "notify_only"
    return "unavailable"


class ThermocoupleHealthView(WireModel):
    device: str
    port: str
    label: str
    display_name: str = Field(alias="displayName")
    role: Literal["Primary", "Food", "Aux"]
    report: ThermocoupleHealthReportView
    detector: ThermocoupleHealthDetectorView
    outcome: Literal["none", "notify_only", "unavailable", "stopped"]
    freshness: ThermocoupleHealthFreshnessView


class DashSocketPayload(WireModel):
    uuid: str
    errors: list[str]
    warnings: list[str]
    warnings_max_id: int | None = Field(alias="warningsMaxId")
    status: str
    ui_hash: int = Field(alias="uiHash")
    critical_error: bool = Field(alias="criticalError")
    grill_name: str = Field(alias="grillName")
    current_mode: str = Field(alias="currentMode")
    next_mode: str = Field(alias="nextMode")
    display_mode: str = Field(alias="displayMode")
    smoke_plus: bool = Field(alias="smokePlus")
    pwm_control: bool = Field(alias="pwmControl")
    manual_pwm: int = Field(alias="manualPwm")
    p_mode: int = Field(alias="pMode")
    hopper_level: FiniteNumber = Field(alias="hopperLevel")
    startup_timestamp: int = Field(alias="startupTimestamp")
    mode_start_time: int = Field(alias="modeStartTime")
    lid_open_detect_enabled: bool = Field(alias="lidOpenDetectEnabled")
    lid_open_detected: bool = Field(alias="lidOpenDetected")
    lid_open_end_time: int = Field(alias="lidOpenEndTime")
    start_duration: int = Field(alias="startDuration")
    shutdown_duration: int = Field(alias="shutdownDuration")
    prime_duration: int = Field(alias="primeDuration")
    prime_amount: FiniteNumber = Field(alias="primeAmount")
    temp_units: Literal["F", "C"] = Field(alias="tempUnits")
    has_dc_fan: bool = Field(alias="hasDcFan")
    has_distance_sensor: bool = Field(alias="hasDistanceSensor")
    startup_check: bool = Field(alias="startupCheck")
    start_to_hold_prompt: bool = Field(alias="startToHoldPrompt")
    startup_goto_temp: FiniteNumber = Field(alias="startupGotoTemp")
    startup_goto_mode: str = Field(alias="startupGotoMode")
    allow_manual_outputs: bool = Field(alias="allowManualOutputs")
    safety_max_temp: FiniteNumber = Field(alias="safetyMaxTemp")
    cycle_ratio: FiniteNumber = Field(alias="cycleRatio")
    fan_duty: FiniteNumber = Field(alias="fanDuty")
    timer: TimerPayload
    outputs: OutputPayload
    recipe_status: RecipeStatusPayload = Field(alias="recipeStatus")
    food_probes: list[ProbeDataPayload] = Field(alias="foodProbes")
    primary_probe: ProbeDataPayload = Field(alias="primaryProbe")
    model_learning_revision: str | None = Field(alias="modelLearningRevision")
    thermocouple_health: list[ThermocoupleHealthView] = Field(
        default_factory=list,
        alias="thermocoupleHealth",
    )


class PelletSocketPayload(WireModel):
    uuid: str
    pellets: PelletDbSchema


class WebUiBuildResponse(WireModel):
    build: str | None


class EmptyResponseData(WireModel):
    pass


class ControlHealthTimeoutData(WireModel):
    response_was: Literal["To_Fast"] = Field(alias="Response_Was")


class ControlHealthResponse(WireModel):
    command: list[str | None] = Field(min_length=4, max_length=4)
    result: Literal["OK", "ERROR"]
    message: str | None
    data: EmptyResponseData | ControlHealthTimeoutData


class DismissWarningsRequest(WireModel):
    through_id: int


class DismissWarningsResponse(ApiEnvelope[None]):
    pass


class CommandResponseData(WireModel):
    pass


class CommandResponse(ApiEnvelope[CommandResponseData]):
    pass
