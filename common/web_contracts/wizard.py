from __future__ import annotations

from typing import Generic, Literal, TypeVar

from pydantic import Field

from common.settings_schema import I2CBusConfig

from .base import FiniteFloat, WireModel


type WireScalar = FiniteFloat | int | bool | str
type WireValue = WireScalar | None | list[WireValue] | dict[str, WireValue]
type WizardSection = Literal["grillplatform", "display", "distance", "probes"]
type ProbeType = Literal["Primary", "Food", "Aux"]
type ProbeFieldType = Literal[
    "list",
    "int",
    "float",
    "string",
    "i2c_bus",
    "probes_list",
    "bt_address",
    "usb_serial_device",
]


class _IncompleteKernelBusNumber(WireModel):
    """The one intentionally incomplete bus value a saved wizard draft carries."""

    kind: Literal["kernel"]
    bus_num: None


type I2cBusValue = I2CBusConfig | _IncompleteKernelBusNumber
type ModuleSettingValue = I2cBusValue | str | None


class SettingsDependency(WireModel):
    friendly_name: str
    description: str = ""
    type: Literal["usb_serial_device", "mcp2221_serial", "i2c_bus"] = "i2c_bus"
    options: dict[str, str] = Field(default_factory=dict)
    default: I2cBusValue | str = ""
    vid: str | int | None = None
    pid: str | int | None = None
    hidden: bool = False
    settings: list[str]


class ConfigOption(WireModel):
    option_name: str
    option_friendly_name: str
    option_description: str = ""
    option_type: Literal["list", "string"]
    list_values: list[WireValue] = Field(default_factory=list)
    list_labels: list[str] = Field(default_factory=list)
    default: WireValue = None
    hidden: bool = False


class WizardModuleData(WireModel):
    friendly_name: str
    description: str = ""
    notes: str = ""
    image: str = ""
    filename: str = ""
    default: bool = False
    py_dependencies: list[str] = Field(default_factory=list)
    apt_dependencies: list[str] = Field(default_factory=list)
    command_list: list[list[str]] = Field(default_factory=list)
    settings_dependencies: dict[str, SettingsDependency]
    config: list[ConfigOption] = Field(default_factory=list)


class ProbeProfile(WireModel):
    A: FiniteFloat
    B: FiniteFloat
    C: FiniteFloat
    id: str
    name: str


class _EmptyProbeProfile(WireModel):
    pass


class ProbeDevice(WireModel):
    device: str
    module: str
    module_filename: str
    ports: list[str]
    config: dict[str, WireValue]


class Probe(WireModel):
    name: str
    label: str
    type: ProbeType
    enabled: bool
    device: str
    port: str
    profile: ProbeProfile | _EmptyProbeProfile


class ProbeMap(WireModel):
    probe_devices: list[ProbeDevice]
    probe_info: list[Probe]


class ProbeConfigField(WireModel):
    label: str
    friendly_name: str
    description: str = ""
    type: ProbeFieldType
    default: WireValue = None
    hidden: bool = False
    list_values: list[WireValue] = Field(default_factory=list)
    list_labels: list[str] = Field(default_factory=list)
    min: FiniteFloat | int = 0
    max: FiniteFloat | int | Literal[""] = ""
    step: FiniteFloat | int = 1


class _ProbeDeviceMetadata(WireModel):
    ports: list[str]
    type: str
    config: list[ProbeConfigField]


class ProbeModuleData(WireModel):
    friendly_name: str
    filename: str
    description: str = ""
    notes: str = ""
    image: str = ""
    default: bool = False
    py_dependencies: list[str] = Field(default_factory=list)
    apt_dependencies: list[str] = Field(default_factory=list)
    command_list: list[list[str]] = Field(default_factory=list)
    settings_dependencies: dict[str, SettingsDependency] = Field(default_factory=dict)
    device_specific: _ProbeDeviceMetadata


class _WizardModulesMetadata(WireModel):
    grillplatform: dict[str, WizardModuleData]
    display: dict[str, WizardModuleData]
    distance: dict[str, WizardModuleData]
    probes: dict[str, ProbeModuleData]


class WizardState(WireModel):
    modules_metadata: _WizardModulesMetadata
    selections: dict[WizardSection, str | None]
    settings_dep_values: dict[WizardSection, dict[str, ModuleSettingValue]]
    display_config: dict[str, dict[str, WireValue]]
    probe_map: ProbeMap
    probe_profiles: list[ProbeProfile]
    probes_units: str
    board_probe_maps: dict[str, ProbeMap]
    control_mode: str
    first_time_setup: bool
    has_draft: bool


class WizardDraftRequest(WireModel):
    clear: bool = False
    selections: dict[WizardSection, str | None] = Field(default_factory=dict)
    settings_dep_values: dict[WizardSection, dict[str, ModuleSettingValue]] = Field(default_factory=dict)
    display_config: dict[str, dict[str, WireValue]] = Field(default_factory=dict)
    probe_map: ProbeMap = Field(default_factory=lambda: ProbeMap(probe_devices=[], probe_info=[]))
    probes_units: str = "F"


class WizardFinishRequest(WireModel):
    selections: dict[WizardSection, str | None] = Field(default_factory=dict)
    settings_dep_values: dict[WizardSection, dict[str, ModuleSettingValue]] = Field(default_factory=dict)
    display_config: dict[str, dict[str, WireValue]] = Field(default_factory=dict)
    probe_map: ProbeMap | None = None
    probes_units: str = ""


class ModuleValuesRequest(WireModel):
    section: str = ""
    module: str = ""


class ModuleValues(WireModel):
    settings: dict[str, ModuleSettingValue]
    config: dict[str, WireValue]


class ScanRequest(WireModel):
    kind: str = ""
    vid: str | int | None = None
    pid: str | int | None = None


class _ScanItem(WireModel):
    value: str
    label: str


class _ScanGroup(WireModel):
    title: str
    items: list[_ScanItem]


class ScanResult(WireModel):
    groups: list[_ScanGroup]
    error: str | None


class InstallStatus(WireModel):
    percent: FiniteFloat | int
    status: str
    output: str


class InstallLog(WireModel):
    text: str
    offset: int = Field(ge=0)
    reset: bool


class BtScanRow(WireModel):
    name: str
    hw_id: str
    info: str


class ThermoworksRow(WireModel):
    label: str
    type: str
    serial: str
    num_channels: int = Field(ge=0)


_RowT = TypeVar("_RowT")


class RowsResult(WireModel, Generic[_RowT]):
    rows: list[_RowT]
    error: str | None


class BtRowsResult(RowsResult[BtScanRow]):
    pass


class ThermoworksRowsResult(RowsResult[ThermoworksRow]):
    pass


class BusKindsValidationRequest(WireModel):
    probe_devices: list[ProbeDevice] = Field(default_factory=list)


class BusKindsValidationResponse(WireModel):
    ok: bool
    detail: str = ""


class ProbeModuleCatalog(WireModel):
    modules: dict[str, ProbeModuleData]
    requires_install: dict[str, bool]


class _EmptyRequest(WireModel):
    pass


class _ThermoworksRequest(WireModel):
    email: str = ""
    password: str = ""


class _ProbeMapRequest(WireModel):
    probe_map: ProbeMap


class _ActionResponse(WireModel):
    result: Literal["success", "error"]
    message: str = ""
    detail: str = ""
    sections: list[str] = Field(default_factory=list)


class _ProbeMapApplyData(WireModel):
    probe_map: ProbeMap


class _ProbeMapApplyResponse(WireModel):
    result: Literal["success"]
    message: str
    data: _ProbeMapApplyData


class _ProbeMapErrorResponse(WireModel):
    result: Literal["error"]
    message: Literal["bad_probe_map", "system_active", "modules_require_install", "bus_conflict"]
    detail: str = ""
    modules: list[str] = Field(default_factory=list)
