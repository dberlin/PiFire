from __future__ import annotations

from typing import Annotated, Any, Literal, Self, TypeAliasType

from pydantic import (
    Field,
    SerializationInfo,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)
from pydantic.json_schema import SkipJsonSchema

from .base import ExtensibleWireModel, FiniteFloat, WireModel

NonNegativeInt = Annotated[int, Field(ge=0, strict=True)]
type FiniteNumber = int | FiniteFloat
type Reading = str | int | FiniteFloat
type BackupKind = Literal["settings", "pelletdb"]
type SystemAction = Literal["reboot", "shutdown", "restart"]
type MaintenanceAction = Literal["clear_history", "clear_events", "clear_pelletdb", "clear_pelletdb_log"]
type Segment = Literal["High", "Medium", "Low"]


class _SparseWireModel(WireModel):
    """Serialize only members present on the JSON wire."""

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


class _OperationErrorData(_SparseWireModel):
    field: str | SkipJsonSchema[None] = None
    mode: str | None = None
    branches: list[str] | SkipJsonSchema[None] = None
    log: str | SkipJsonSchema[None] = None
    detail: str | SkipJsonSchema[None] = None


class NetworkInterface(WireModel):
    ip_address: str
    mac_address: str


class OsInfo(ExtensibleWireModel):
    """The guaranteed display fields plus intentionally open /etc/os-release data."""

    __pydantic_extra__: dict[str, str] = Field(init=False)
    PRETTY_NAME: str
    NAME: str
    VERSION: str
    VERSION_ID: str
    VERSION_CODENAME: str
    ARCHITECTURE: str
    BITS: str


class CpuInfo(WireModel):
    hardware: Reading
    model: Reading
    model_name: Reading
    cores: Reading
    frequency: Reading


class HardwareInfo(WireModel):
    total_ram: Reading
    available_ram: Reading
    cpu_info: CpuInfo


class SystemInfo(WireModel):
    uptime: str
    os_info: OsInfo
    network_info: dict[str, NetworkInterface]
    hardware_info: HardwareInfo


class AdminSettings(WireModel):
    debug_mode: bool
    boot_to_monitor: bool


class AdminSettingsUpdate(_SparseWireModel):
    debug_mode: bool | SkipJsonSchema[None] = None
    boot_to_monitor: bool | SkipJsonSchema[None] = None

    @field_validator("debug_mode", "boot_to_monitor")
    @classmethod
    def _reject_explicit_null(cls, value: bool | None) -> bool:
        if value is None:
            raise ValueError("must be a boolean")
        return value

    @model_validator(mode="after")
    def _require_a_toggle(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("at least one admin setting is required")
        return self


class BackupListing(WireModel):
    settings: list[str]
    pelletdb: list[str]


class AdminState(WireModel):
    system: SystemInfo
    settings: AdminSettings
    backups: BackupListing
    logs: list[str]
    mode: str


class SystemActionRequest(WireModel):
    action: SystemAction


class SystemActionResponse(WireModel):
    action: SystemAction


class EmptyOperationRequest(WireModel):
    pass


class FactoryResetResponse(WireModel):
    action: Literal["factory_reset"]


class MaintenanceActionRequest(WireModel):
    action: MaintenanceAction


class MaintenanceActionResponse(WireModel):
    action: MaintenanceAction


class BackupCreateRequest(WireModel):
    kind: BackupKind


class BackupCreated(WireModel):
    filename: str


class BackupRestoreRequest(WireModel):
    kind: BackupKind
    file: str


class BackupRestored(WireModel):
    kind: BackupKind
    file: str


class LogFamily(WireModel):
    stem: str
    members: list[str]
    bytes: NonNegativeInt


class LogsMetadata(WireModel):
    logs: list[str]
    families: list[LogFamily]


class LogsDeleted(WireModel):
    removed: list[str]


class UpdateState(WireModel):
    version: str
    branch: str
    detached: str | None
    branches: list[str]
    remote_url: str
    remote_version: str
    web_ui_stale: bool
    web_ui_build_failed: bool
    restart_pending: bool
    manual_dependency_actions: list[str]


class UpdateCheck(WireModel):
    current: str
    behind: int


class UpdateLog(WireModel):
    output: str


class BuildLog(WireModel):
    text: str
    offset: NonNegativeInt
    reset: bool


class UpdateStatus(WireModel):
    percent: int | None
    status: str | None
    output: str | None


class UpdateStarted(WireModel):
    started: bool


class UpdateBranchRequest(WireModel):
    target: str


class TunerPoint(WireModel):
    segment: Segment
    temp: FiniteNumber
    trohms: FiniteNumber


class TunerSessionRequest(WireModel):
    open: bool


class TunerSession(WireModel):
    open: bool
    mode: str
    restored: bool


class TrReading(WireModel):
    probe: str
    trohms: FiniteNumber | None
    tuning: bool


class CoefficientPoint(WireModel):
    x: FiniteNumber
    y: FiniteNumber


class CoefficientsRequest(WireModel):
    points: list[TunerPoint]

    @model_validator(mode="after")
    def _require_each_segment_once(self) -> Self:
        if len(self.points) != 3 or {point.segment for point in self.points} != {"High", "Medium", "Low"}:
            raise ValueError("points must contain High, Medium, and Low exactly once")
        return self


class Coefficients(WireModel):
    a: FiniteFloat
    b: FiniteFloat
    c: FiniteFloat
    chart: list[CoefficientPoint]
    chart_ok: bool


class ProfileInput(WireModel):
    name: str
    a: FiniteNumber
    b: FiniteNumber
    c: FiniteNumber
    apply_to: str | None = None

    @field_validator("name")
    @classmethod
    def _strip_non_blank_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class SavedProfile(WireModel):
    id: str
    applied: str | None


class AutoStatusRequest(WireModel):
    probe: Annotated[str, Field(min_length=1, strict=True)]
    reference: Annotated[str, Field(min_length=1, strict=True)]


class AutoStatus(WireModel):
    current_tr: FiniteNumber | None
    current_temp: FiniteNumber | None
    high_tr: FiniteNumber
    high_temp: FiniteNumber
    medium_tr: FiniteNumber
    medium_temp: FiniteNumber
    low_tr: FiniteNumber
    low_temp: FiniteNumber
    samples: NonNegativeInt
    ready: bool


def dump_wire(model: type[WireModel], payload: Any) -> dict[str, Any]:
    """Strictly validate and serialize one JSON response data object."""

    return model.model_validate(payload, strict=True).model_dump(
        mode="json",
        by_alias=True,
        exclude_none=False,
    )


def dump_error_data(payload: dict[str, Any]) -> dict[str, Any]:
    """Strictly serialize the finite set of operation refusal details."""

    return dump_wire(_OperationErrorData, payload)
