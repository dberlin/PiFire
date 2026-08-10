from __future__ import annotations

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, RootModel, StringConstraints, TypeAdapter, model_validator

from .base import ExtensibleWireModel, FiniteFloat, WireModel


type FiniteNumber = int | FiniteFloat
type JsonValue = None | bool | int | FiniteFloat | str | list[JsonValue] | dict[str, JsonValue]
type GrillMode = Literal["startup", "smoke", "shutdown", "stop", "monitor", "reignite", "manual"]
type SystemCommand = Literal["reboot", "shutdown", "restart"]
type ManualOutput = Literal["power", "igniter", "auger", "fan"]
_JSON_VALUE_ADAPTER = TypeAdapter(JsonValue)



class _JsonExtensibleWireModel(ExtensibleWireModel):
    """Strict extensible model; declared open members remain JSON values."""
    @model_validator(mode="after")
    def _extras_are_json_values(self) -> _JsonExtensibleWireModel:
        for value in (self.model_extra or {}).values():
            _JSON_VALUE_ADAPTER.validate_python(value, strict=True)
        return self


class TimerOptionsPayload(WireModel):
    shutdown: bool
    keep_warm: bool = Field(alias="keepWarm")


class SetModeCommandRequest(WireModel):
    operation: Literal["set_mode"]
    mode: GrillMode


class SetPrimarySetpointCommandRequest(WireModel):
    operation: Literal["set_primary_setpoint"]
    temperature: FiniteNumber


class SetSmokePlusCommandRequest(WireModel):
    operation: Literal["set_smoke_plus"]
    enabled: bool


class SetPModeCommandRequest(WireModel):
    operation: Literal["set_p_mode"]
    value: int = Field(ge=0, le=9)


class PrimeCommandRequest(WireModel):
    operation: Literal["prime"]
    grams: int = Field(ge=0)
    next_mode: GrillMode | None = None


class TimerStartCommandRequest(WireModel):
    operation: Literal["timer_start"]
    seconds: FiniteNumber


class TimerStartWithOptionsCommandRequest(WireModel):
    operation: Literal["timer_start_with_options"]
    seconds: FiniteNumber
    options: TimerOptionsPayload


class TimerPauseCommandRequest(WireModel):
    operation: Literal["timer_pause"]


class TimerStopCommandRequest(WireModel):
    operation: Literal["timer_stop"]


class TimerShutdownCommandRequest(WireModel):
    operation: Literal["timer_shutdown"]
    enabled: bool


class TimerKeepWarmCommandRequest(WireModel):
    operation: Literal["timer_keep_warm"]
    enabled: bool


class SystemCommandRequest(WireModel):
    operation: Literal["system"]
    command: SystemCommand


class SetUnitsCommandRequest(WireModel):
    operation: Literal["set_units"]
    units: Literal["F", "C"]


class ManualOutputCommandRequest(WireModel):
    operation: Literal["manual_output"]
    output: ManualOutput
    action: Literal["toggle", "true", "false"] = "toggle"


class ManualPwmCommandRequest(WireModel):
    operation: Literal["manual_pwm"]
    duty: int = Field(ge=0, le=100)


type _CommandRequestUnion = Annotated[
    SetModeCommandRequest
    | SetPrimarySetpointCommandRequest
    | SetSmokePlusCommandRequest
    | SetPModeCommandRequest
    | PrimeCommandRequest
    | TimerStartCommandRequest
    | TimerStartWithOptionsCommandRequest
    | TimerPauseCommandRequest
    | TimerStopCommandRequest
    | TimerShutdownCommandRequest
    | TimerKeepWarmCommandRequest
    | SystemCommandRequest
    | SetUnitsCommandRequest
    | ManualOutputCommandRequest
    | ManualPwmCommandRequest,
    Field(discriminator="operation"),
]


class CommandRequest(RootModel[_CommandRequestUnion]):
    model_config = ConfigDict(frozen=True, strict=True)


class NotifyEntry(_JsonExtensibleWireModel):
    label: str
    type: str
    req: bool
    shutdown: bool
    keep_warm: bool | None = None
    reignite: bool | None = None
    target: FiniteNumber | None = None
    eta: FiniteNumber | None = None
    condition: str | None = None
    triggered: bool | None = None


class NotifyUpdate(WireModel):
    label: str
    type: str
    fields: dict[str, JsonValue]


class NotifyListResponse(WireModel):
    result: Literal["OK", "ERROR"]
    message: str
    data: list[NotifyEntry]


class ControlPatchRequest(_JsonExtensibleWireModel):
    notify_updates: list[NotifyUpdate] | None = None
    notify_data: list[NotifyEntry] | None = None


class ControlPatchResponse(WireModel):
    control: Literal["success", "error"]
    result: Literal["success", "error"]
    message: str


#: The shape of the pellet database, independent of both the release version
#: and the settings tree's shape version. Different shapes have independent
#: migration histories.
PELLETDB_SCHEMA_VERSION = 2
_EpochMsKey = Annotated[str, StringConstraints(pattern=r"^\d+$")]


class PelletLogEntry(WireModel):
    pelletid: str | None
    deleted: bool


class PelletProfile(WireModel):
    brand: str
    wood: str
    rating: int = Field(ge=1, le=5)
    comments: str


class PelletCurrent(WireModel):
    pelletid: str
    hopper_level: int
    date_loaded: str
    est_usage: FiniteFloat


class PelletLastUpdated(WireModel):
    time: int


class PelletDbSchema(WireModel):
    schema_version: int = PELLETDB_SCHEMA_VERSION
    current: PelletCurrent
    archive: dict[str, PelletProfile]
    log: dict[_EpochMsKey, PelletLogEntry]
    brands: list[str]
    woods: list[str]
    lastupdated: PelletLastUpdated

    @model_validator(mode="after")
    def _loaded_profile_is_archived(self) -> PelletDbSchema:
        if self.current.pelletid not in self.archive:
            raise ValueError("current.pelletid must be a key of archive")
        return self


class PelletProfileFields(WireModel):
    brand_name: str
    wood_type: str
    rating: int = Field(ge=1, le=5)
    comments: str


class PelletProfileReference(WireModel):
    profile: str


class PelletLogReference(WireModel):
    log_item: str


class PelletVocabularyEdit(WireModel):
    new_brand: str | None = None
    delete_brand: str | None = None
    new_wood: str | None = None
    delete_wood: str | None = None

    @model_validator(mode="after")
    def _has_exactly_one_edit(self) -> PelletVocabularyEdit:
        values = (self.new_brand, self.delete_brand, self.new_wood, self.delete_wood)
        if sum(value is not None for value in values) != 1:
            raise ValueError("pellet vocabulary action requires exactly one edit")
        return self


class AddPelletProfileData(PelletProfileFields):
    add_and_load: bool


class EditPelletProfileData(PelletProfileFields):
    profile: str


class EmptyPelletActionData(WireModel):
    pass


class LoadPelletProfileRequest(WireModel):
    action: Literal["load_profile"]
    data: PelletProfileReference


class HopperCheckRequest(WireModel):
    action: Literal["hopper_check"]
    data: EmptyPelletActionData


class EditPelletBrandsRequest(WireModel):
    action: Literal["edit_brands"]
    data: PelletVocabularyEdit

    @model_validator(mode="after")
    def _edits_brand(self) -> EditPelletBrandsRequest:
        if self.data.new_brand is None and self.data.delete_brand is None:
            raise ValueError("edit_brands requires a brand field")
        return self


class EditPelletWoodsRequest(WireModel):
    action: Literal["edit_woods"]
    data: PelletVocabularyEdit

    @model_validator(mode="after")
    def _edits_wood(self) -> EditPelletWoodsRequest:
        if self.data.new_wood is None and self.data.delete_wood is None:
            raise ValueError("edit_woods requires a wood field")
        return self


class AddPelletProfileRequest(WireModel):
    action: Literal["add_profile"]
    data: AddPelletProfileData


class EditPelletProfileRequest(WireModel):
    action: Literal["edit_profile"]
    data: EditPelletProfileData


class DeletePelletProfileRequest(WireModel):
    action: Literal["delete_profile"]
    data: PelletProfileReference


class DeletePelletLogRequest(WireModel):
    action: Literal["delete_log"]
    data: PelletLogReference


type _PelletActionUnion = Annotated[
    LoadPelletProfileRequest
    | HopperCheckRequest
    | EditPelletBrandsRequest
    | EditPelletWoodsRequest
    | AddPelletProfileRequest
    | EditPelletProfileRequest
    | DeletePelletProfileRequest
    | DeletePelletLogRequest,
    Field(discriminator="action"),
]


class PelletActionRequest(RootModel[_PelletActionUnion]):
    model_config = ConfigDict(frozen=True, strict=True)


class PelletActionResponse(WireModel):
    data: None = None
    result: Literal["OK", "Error"]
    message: str | None = None




class PelletRestData(WireModel):
    uuid: str
    pellets: PelletDbSchema


class PelletRestResponse(WireModel):
    data: PelletRestData
    result: Literal["OK", "Error"]
    message: str | None = None


class WledDevice(_JsonExtensibleWireModel):
    name: str
    ip: str
    port: int | None = None
    led_count: int | None = None
    version: str | None = None
    product: str | None = None
    mac: str | None = None
    online: bool | None = None


class WledDiscoverResponse(WireModel):
    result: Literal["success", "error"]
    message: str
    devices: list[WledDevice]


class WledPushProfilesRequest(WireModel):
    device_address: str
    profile_numbers: dict[str, int] = Field(default_factory=dict)


class WledTestProfileRequest(WireModel):
    device_address: str
    profile_number: int = 1


class WledActionResponse(WireModel):
    result: Literal["success", "error"]
    message: str
    profiles_pushed: int | None = None
    profiles: list[str] | None = None
