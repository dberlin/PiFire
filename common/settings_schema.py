"""Pydantic shadow models for the PiFire settings tree.

common/defaults.py remains the defaults AUTHORITY — these models mirror it,
and tests/unit/common/test_settings_schema.py fails on any divergence.
Do not change a default here without changing defaults.py (parity will fail).

Unknown keys are REJECTED (`extra="forbid"` on `_Section`), not silently
allowed through. This is real typo-catching, but a stray/legacy/future key
must never be able to permanently block a settings save -- so the write-time
gate, `validate_settings_tree()`, wraps strict validation with a self-healing
repair pass: if every failure a validation attempt produces is an unmodeled
key (`extra_forbidden`), those keys are stripped from a copy of the input,
each stripped dotted path is logged via `common.common.write_log`, and
validation is retried once. Any OTHER error (bad value, missing required
field, cross-field model_validator failure) still raises
`SettingsValidationError` exactly as before -- repair never masks a real bug.
See `validate_settings_tree()` below for the mechanism, and
`tests/unit/common/test_settings_schema.py`'s "extra=\"forbid\" self-healing
repair behavior" section for the full behavior matrix.

Every clamp/invariant enforced by blueprints/settings/routes.py and the
web-react settings tabs is also migrated into schema constraints:
`Field(ge=..., le=...)` for scalar bounds, and `model_validator(mode="after")`
for cross-field/cross-section rules (see PwmSettings, SmartStart, and
SettingsSchema._check_startup_pwm_duty_cycle below). Only constraints traced
to an actual source-code clamp were added -- no new limits were invented. Any
NEW constraint must trace the same way.
"""

import copy
import json
from types import UnionType
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_serializer,
    model_validator,
)
from pydantic_core import ErrorDetails
from pydantic_partial import create_partial_model

from common.common import deep_update, write_log


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SafetySettings(_Section):
    # Mirrors defaults.py settings["safety"].
    minstartuptemp: int = 75
    maxstartuptemp: int = 100
    maxtemp: int = 550
    reigniteretries: int = 1
    startup_check: bool = True
    allow_manual_changes: bool = False
    manual_override_time: int = 30


class PelletLevel(_Section):
    # Mirrors defaults.py settings["pelletlevel"].
    warning_enabled: bool = True
    warning_level: int = 25
    warning_time: int = 20
    empty: int = 22
    full: int = 4


class KeepWarm(_Section):
    # Mirrors defaults.py settings["keep_warm"].
    temp: int = 165
    s_plus: bool = False


class SmokePlus(_Section):
    # Mirrors defaults.py settings["smoke_plus"].
    enabled: bool = False
    min_temp: int = 160
    max_temp: int = 220
    on_time: int = 5
    off_time: int = 5
    duty_cycle: int = 75
    fan_ramp: bool = False


class CycleData(_Section):
    # Mirrors defaults.py settings["cycle_data"].
    HoldCycleTime: int = 25
    SmokeOnCycleTime: int = 15
    SmokeOffCycleTime: int = 45
    PMode: int = 2
    u_min: float = 0.1
    u_max: float = 0.9
    LidOpenDetectEnabled: bool = False
    LidOpenThreshold: int = 15
    LidOpenPauseTime: int = 60
    FanPidEnabled: bool = False


class ShutdownSettings(_Section):
    # Mirrors defaults.py settings["shutdown"].
    shutdown_duration: int = 240
    auto_power_off: bool = False


class Modules(_Section):
    # Mirrors defaults.py settings["modules"].
    grillplat: str = "prototype"
    display: str = "none"
    dist: str = "none"


class _DisplayDeviceConfig(_Section):
    dc: int = 24
    led: int = 5
    rst: int = 25


# The four I2C bus kinds, mirroring common/i2c_bus_config.py's dataclass
# hierarchy. `kind` alone cannot discriminate -- three kernel variants share it
# -- so this is a left-to-right union whose members each forbid the other
# variants' fields. That is what makes {"kind":"kernel","adapter":"X"} match
# exactly one member, and what makes a stale field from a previous kind a
# validation error rather than a silently carried value.
#
# Matches what common/i2c_bus_config.py's parse_i2c_bus accepts for a kernel
# selector: bus_num non-negative, adapter/serial non-blank once stripped.
_NonBlankStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class _BasicBus(_Section):
    kind: Literal["basic"] = "basic"


class _KernelBusNumber(_Section):
    kind: Literal["kernel"] = "kernel"
    bus_num: int = Field(ge=0)


class _KernelAdapterName(_Section):
    kind: Literal["kernel"] = "kernel"
    adapter: _NonBlankStr


class _KernelSerialMatch(_Section):
    kind: Literal["kernel"] = "kernel"
    serial: _NonBlankStr


class _FT232hBus(_Section):
    kind: Literal["ft232h"] = "ft232h"
    url: str = ""


class _MCP2221Bus(_Section):
    kind: Literal["mcp2221"] = "mcp2221"
    serial: str = ""


I2CBusConfig = Annotated[
    Union[_BasicBus, _KernelBusNumber, _KernelAdapterName, _KernelSerialMatch, _FT232hBus, _MCP2221Bus],
    Field(union_mode="left_to_right"),
]


class _DistanceDeviceConfig(_Section):
    echo: int = 27
    trig: int = 23
    i2c_bus: I2CBusConfig = _BasicBus()
    # Optional I2C address override. distance/_tof_base.py accepts a hex
    # string ("0x29"), a plain int, or None (fall back to the driver's
    # default_address), and the wizard writes the hex-string form -- so an
    # int-only annotation here rejects every real configured value, and with
    # it every subsequent settings write (the gate below only repairs
    # unmodeled keys, never a wrong type). Matches _FanControllerConfig.address.
    address: str | int | None = None
    device: str = "/dev/ttyACM0"


class _InputDeviceConfig(_Section):
    down_dt: int = 20
    enter_sw: int = 21
    up_clk: int = 16


class _DevicesConfig(_Section):
    display: _DisplayDeviceConfig = _DisplayDeviceConfig()
    distance: _DistanceDeviceConfig = _DistanceDeviceConfig()
    input: _InputDeviceConfig = _InputDeviceConfig()


# A pin is an identifier or nothing, and the identifier is not always a number.
# The Raspberry Pi platforms address pins by BCM number, but ft232h_relay
# addresses them by NAME (C0-C7 / D4-D7 -- grillplat/ft232h.py:136,158) and
# every board offers "None" for the pins it does not wire (wizard_manifest.json:
# pcb_2.00a output_dc_fan/output_pwm/input_shutdown, pcb_4.x.x input_selector,
# and custom's inputs). Typing these as plain int meant a legitimate ft232h or
# unwired-pin selection could not be written at all: write_settings raised
# SettingsValidationError inside the DETACHED installer, which has no handler,
# so the install died with the browser still polling a status frozen at
# "Installing Dependencies...".
class _InputsConfig(_Section):
    selector: int | None = 17
    shutdown: int | None = 17


class _OutputsConfig(_Section):
    auger: int | str | None = 14
    dc_fan: int | str | None = 26
    fan: int | str | None = 15
    igniter: int | str | None = 18
    power: int | str | None = 4
    pwm: int | str | None = 13


class _SPI0Config(_Section):
    CE0: int = 8
    CE1: int = 7


class _SystemConfig(_Section):
    # populate_by_name=True must be restated (assigning model_config here
    # replaces, rather than merges with, _Section's) -- extra now inherits
    # _Section's "forbid" implicitly via the same override, since "1WIRE" is
    # a real aliased field (below), not an extra pass-through.
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    SPI0: _SPI0Config = _SPI0Config()
    # "1WIRE" (defaults.py:99) isn't a valid Python identifier, hence the
    # alias; populate_by_name=True also accepts "one_wire" on input.
    # validate_settings_tree()/assert_parity() dump with by_alias=True so the
    # normalized output still round-trips the "1WIRE" key exactly.
    one_wire: int | None = Field(default=None, alias="1WIRE")


class _NumatoConfig(_Section):
    device: str = "/dev/ttyACM0"
    baudrate: int = 921600


class _FanControllerConfig(_Section):
    chip: str = "emc2101"
    i2c_bus: I2CBusConfig = _BasicBus()
    address: str = "0x4c"


class _FT232hConfig(_Section):
    url: str = "1"


class Platform(_Section):
    # Mirrors defaults.py settings["platform"].
    devices: _DevicesConfig = _DevicesConfig()
    inputs: _InputsConfig = _InputsConfig()
    outputs: _OutputsConfig = _OutputsConfig()
    system: _SystemConfig = _SystemConfig()
    numato: _NumatoConfig = _NumatoConfig()
    fan_controller: _FanControllerConfig = _FanControllerConfig()
    ft232h: _FT232hConfig = _FT232hConfig()
    current: str = "custom"
    dc_fan: bool = False
    triggerlevel: str = "HIGH"
    buttonslevel: str = "HIGH"
    standalone: bool = True
    real_hw: bool = True
    system_type: str = "prototype"


class Versions(_Section):
    # Mirrors defaults.py settings["versions"] (read from
    # updater/updater_manifest.json on every default_settings() call -- not a
    # static per-model default, it tracks the installed manifest). Required,
    # no defaults; parity holds because validate-then-dump preserves the
    # input values.
    server: str
    cookfile: str
    recipe: str
    build: int


class ServerInfo(_Section):
    # Mirrors defaults.py settings["server_info"].
    # uuid is generated fresh by generate_uuid() per-install; required, no
    # default (parity holds because validate-then-dump preserves the input).
    uuid: str


class LastUpdated(_Section):
    # Mirrors defaults.py settings["lastupdated"].
    # time is a live timestamp (math.trunc(time.time())) set at build time;
    # required, no default, same reasoning as ServerInfo.uuid.
    time: int


class ProbeMap(_Section):
    # Outer shape of probe_settings["probe_map"]; the device/probe dicts
    # inside are driver- and profile-specific and stay loose.
    probe_devices: list[dict] = []
    probe_info: list[dict] = []


class ProbeSettings(_Section):
    # Mirrors defaults.py settings["probe_settings"].
    # probe_profiles is keyed by profile id (from probes/probes.json plus any
    # user-added profiles); profile contents are profile-specific and loose.
    probe_profiles: dict[str, dict] = {}
    probe_map: ProbeMap = ProbeMap()


class GlobalSettings(_Section):
    # Mirrors defaults.py settings["globals"].
    grill_name: str = ""
    debug_mode: bool = False
    disp_rotation: int = 0
    units: Literal["F", "C"] = "F"
    augerrate: float = 0.3
    first_time_setup: bool = True
    ext_data: bool = False
    boot_to_monitor: bool = False
    prime_ignition: bool = False
    updated_message: bool = False
    venv: bool = True
    python_exec: str = ".venv/bin/python"
    uv: bool = True


class ControllerSettings(_Section):
    # Mirrors defaults.py settings["controller"].
    # config is keyed by controller name (from controller/controllers.json);
    # each controller's option set/types are controller-specific and loose.
    selected: str = "pid"
    config: dict[str, dict[str, float | int | bool | str]] = {}


class DisplaySettings(_Section):
    # Mirrors defaults.py settings["display"].
    # config is keyed by display module name (from
    # wizard/wizard_manifest.json); each display's option set is
    # driver-specific and stays loose.
    selected: str = "none"
    # Clamp source: blueprints/settings/routes.py:15 -- max(0, int(raw)).
    sleep_timeout: int = Field(default=300, ge=0)
    config: dict[str, dict] = {}


class PwmProfile(_Section):
    duty_cycle: int


class PwmSettings(_Section):
    # Mirrors defaults.py settings["pwm"].
    pwm_control: bool = False
    update_time: int = 10
    frequency: int = 25000
    min_duty_cycle: int = 20
    max_duty_cycle: int = 100
    temp_range_list: list[int] = [3, 7, 10, 15]
    profiles: list[PwmProfile] = [
        PwmProfile(duty_cycle=20),
        PwmProfile(duty_cycle=35),
        PwmProfile(duty_cycle=50),
        PwmProfile(duty_cycle=75),
        PwmProfile(duty_cycle=100),
    ]

    @model_validator(mode="after")
    def _check_profiles(self) -> "PwmSettings":
        # Clamp source: React RangeProfileTable's construction invariant
        # (web-react/src/components/settings/RangeProfileTable.tsx handleAdd/
        # handleRemove keep profiles == boundaries + 1); PwmTab.tsx wires
        # temp_range_list as the boundaries.
        if len(self.profiles) != len(self.temp_range_list) + 1:
            raise ValueError("profiles must have exactly one more entry than temp_range_list")
        # Clamp source: web-react/src/components/settings/tabs/PwmTab.tsx:72-73
        # (RangeProfileTable column min/max = pwm.min_duty_cycle/max_duty_cycle).
        for i, profile in enumerate(self.profiles):
            if not (self.min_duty_cycle <= profile.duty_cycle <= self.max_duty_cycle):
                raise ValueError(f"profiles[{i}].duty_cycle must be within [min_duty_cycle, max_duty_cycle]")
        return self


class SmartStartProfile(_Section):
    # Clamp source: blueprints/settings/templates/settings/index.html (the
    # smartstart profile add/edit forms, which are what a Flask user is held to).
    startuptime: int = Field(ge=30, le=1200)
    augerontime: int = Field(ge=1, le=1000)
    p_mode: int = Field(ge=0, le=9)


class SmartStart(_Section):
    # Mirrors defaults.py settings["startup"]["smartstart"].
    enabled: bool = False
    exit_temp: int = 120
    temp_range_list: list[int] = [60, 80, 90]
    profiles: list[SmartStartProfile] = [
        SmartStartProfile(startuptime=360, augerontime=15, p_mode=0),
        SmartStartProfile(startuptime=360, augerontime=15, p_mode=1),
        SmartStartProfile(startuptime=240, augerontime=15, p_mode=3),
        SmartStartProfile(startuptime=240, augerontime=15, p_mode=5),
    ]

    @model_validator(mode="after")
    def _check_profile_count(self) -> "SmartStart":
        # Clamp source: RangeProfileTable's construction invariant (see
        # PwmSettings._check_profiles above) as wired by StartupTab.tsx.
        if len(self.profiles) != len(self.temp_range_list) + 1:
            raise ValueError("profiles must have exactly one more entry than temp_range_list")
        return self


class StartToMode(_Section):
    # Mirrors defaults.py settings["startup"]["start_to_mode"].
    after_startup_mode: Literal["Smoke", "Hold"] = "Smoke"
    primary_setpoint: int = 165
    start_to_hold_prompt: bool = False


class StartupSettings(_Section):
    # Mirrors defaults.py settings["startup"].
    duration: int = 240
    # Clamp source: blueprints/settings/routes.py:479-483 -- out of [0, 200]
    # is silently zeroed by the legacy route; this schema rejects instead
    # (see test_prime_on_startup_range_rejects).
    prime_on_startup: int = Field(default=0, ge=0, le=200)
    startup_exit_temp: int = 0
    start_to_mode: StartToMode = StartToMode()
    smartstart: SmartStart = SmartStart()
    # pwm_duty_cycle's [pwm.min_duty_cycle, pwm.max_duty_cycle] bound (clamp
    # source: blueprints/settings/routes.py:493-497) is cross-SECTION (against
    # a sibling of `startup`, not a field on this model) -- enforced by
    # SettingsSchema._check_startup_pwm_duty_cycle below.
    pwm_duty_cycle: int = 100


class Dashboard(_Section):
    # Mirrors defaults.py settings["dashboard"] (built by _default_dashboard()
    # from dashboard/*.json metadata). Per-dashboard entries
    # (name/friendly_name/html_name/metadata/custom/screenshot/config)
    # are dashboard-file-driven and stay loose.
    current: str = "Default"
    dashboards: dict[str, dict] = {}


class AppriseService(_Section):
    enabled: bool = False
    locations: list[str] = []


class IftttService(_Section):
    enabled: bool = False
    APIKey: str = ""


class PushbulletService(_Section):
    enabled: bool = False
    APIKey: str = ""
    PublicURL: str = ""


class PushoverService(_Section):
    enabled: bool = False
    APIKey: str = ""
    UserKeys: str = ""
    PublicURL: str = ""


class OneSignalService(_Section):
    enabled: bool = False
    # Generated fresh by generate_uuid() on every default_notify_services()
    # call (not a static default) -- required, no default, same reasoning as
    # ServerInfo.uuid above.
    uuid: str
    app_id: str = ""
    devices: dict[str, Any] = {}


class InfluxdbService(_Section):
    enabled: bool = False
    url: str = ""
    token: str = ""
    org: str = ""
    bucket: str = ""


class MqttService(_Section):
    broker: str = "homeassistant.local"
    enabled: bool = False
    homeassistant_autodiscovery_topic: str = "homeassistant"
    id: str = "PiFire"
    password: str = ""
    port: str = "1883"
    update_sec: str = "30"
    username: str = ""


class WledSuggestedConfig(_Section):
    cooking_color: str = "blue"
    # Clamp source: blueprints/settings/routes.py:191-194 -- max(1, min(100, ...)).
    idle_brightness: int = Field(default=20, ge=1, le=100)
    night_mode: bool = False
    # Clamp source: blueprints/settings/routes.py:195-198 -- max(1, min(1000, ...)).
    led_count: int = Field(default=6, ge=1, le=1000)


class WledProfileNumbers(_Section):
    # Mirrors defaults.py default_notify_services()["wled"]["profile_numbers"]
    # (defaults.py:385-399) -- a stable, named key set (one PiFire state per
    # key). Clamp source: blueprints/settings/routes.py:212-216 -- the POST
    # loop applies max(1, min(250, ...)) to every key uniformly.
    idle: int = Field(default=200, ge=1, le=250)
    booting: int = Field(default=201, ge=1, le=250)
    preheat: int = Field(default=202, ge=1, le=250)
    cooking: int = Field(default=203, ge=1, le=250)
    cooldown: int = Field(default=204, ge=1, le=250)
    target_reached: int = Field(default=205, ge=1, le=250)
    overshoot_alarm: int = Field(default=206, ge=1, le=250)
    probe_alarm: int = Field(default=207, ge=1, le=250)
    low_pellets: int = Field(default=208, ge=1, le=250)
    timer_done: int = Field(default=209, ge=1, le=250)
    error_fault: int = Field(default=210, ge=1, le=250)
    night_mode: int = Field(default=211, ge=1, le=250)


class WledModePresets(_Section):
    # Mirrors defaults.py default_notify_services()["wled"]["mode_presets"]
    # (defaults.py:400-409) -- legacy per-Mode preset numbers, read by
    # notify/wled_handler.py:494-498. No numeric clamp found anywhere these
    # are written (not settable via blueprints/settings/routes.py), so no
    # ge/le -- shape only.
    Stop: int = 1
    Startup: int = 1
    Reignite: int = 1
    Smoke: int = 1
    Hold: int = 1
    Shutdown: int = 1
    Prime: int = 1


class WledEventPresets(_Section):
    # Mirrors defaults.py default_notify_services()["wled"]["event_presets"]
    # (defaults.py:410-417) -- legacy per-event preset numbers, read by
    # notify/wled_handler.py:502-520. No numeric clamp found (not settable via
    # blueprints/settings/routes.py) -- shape only.
    Temp_Achieved: int = 1
    Recipe_Next: int = 1
    Grill_Error: int = 1
    Pellet_Level_Low: int = 1
    Timer_Expired: int = 1


class WledService(_Section):
    enabled: bool = False
    device_address: str = "wled.local"
    use_profiles: bool = True
    use_suggested_presets: bool = False
    profile_numbers: WledProfileNumbers = WledProfileNumbers()
    mode_presets: WledModePresets = WledModePresets()
    event_presets: WledEventPresets = WledEventPresets()
    suggested_config: WledSuggestedConfig = WledSuggestedConfig()
    # Clamp source: blueprints/settings/routes.py:179-180 -- max(int(...), 0).
    notify_duration: int = Field(default=120, ge=0)


class NotifyServices(_Section):
    # Mirrors defaults.py default_notify_services().
    # Every current service has a static, stable shape, so each gets its own
    # model. _Section is extra="forbid", so a brand-new/unmodeled top-level
    # service key is NOT silently passed through: the write-gate repair wrapper
    # strips it and logs a warning (rather than hard-blocking the save). Adding
    # a real new service means adding its model here.
    apprise: AppriseService = AppriseService()
    ifttt: IftttService = IftttService()
    pushbullet: PushbulletService = PushbulletService()
    pushover: PushoverService = PushoverService()
    onesignal: OneSignalService = OneSignalService(uuid="")
    influxdb: InfluxdbService = InfluxdbService()
    mqtt: MqttService = MqttService()
    wled: WledService = WledService()


class ProbeChartConfig(_Section):
    # Mirrors the always-present shape default_probe_config() (defaults.py
    # :307-336) writes for EVERY probe (Primary and Food). Primary-only
    # setpoint-color fields (bg_color_setpoint/line_color_setpoint,
    # defaults.py:329-331) are real optional fields (promoted off
    # extra="allow") -- absent on Food-probe entries, always plain color
    # strings on Primary entries.
    name: str
    type: str
    enabled: bool
    line_color: str
    line_color_target: str
    dash_setpoint: bool
    bg_color: str
    bg_color_target: str
    fill: bool
    bg_color_setpoint: str | None = None
    line_color_setpoint: str | None = None

    @model_serializer(mode="wrap")
    def _omit_unset_setpoint_colors(self, handler):
        # Food-probe entries never carry these keys at all (defaults.py only
        # sets them for Primary, :329-331) -- dump must omit rather than emit
        # `null`, or parity (assert_parity/validate_settings_tree round-trip)
        # breaks against the original dict, which simply lacks the key. No
        # other field on this model is ever legitimately None, so a blanket
        # None-filter is safe here.
        return {k: v for k, v in handler(self).items() if v is not None}


class HistoryPage(_Section):
    # Mirrors defaults.py settings["history_page"].
    minutes: int = 15
    clearhistoryonstart: bool = True
    autorefresh: Literal["on", "off"] = "on"
    datapoints: int = 10000
    fidelity_degrees: float = 2.0
    probe_config: dict[str, ProbeChartConfig] = {}


class RecipeProbeMap(_Section):
    primary: str = ""
    food: list[str] = []


class Recipe(_Section):
    # Mirrors defaults.py settings["recipe"].
    probe_map: RecipeProbeMap = RecipeProbeMap()


#: The shape of the settings tree, independent of the release version. Bumped
#: by any change to the persisted shape that an existing install cannot simply
#: be validated into: a rename, a retype, a restructure. NOT bumped for an
#: added or deleted field -- the write-time repair handles both losslessly
#: (added takes its default, deleted is stripped).
#:
#: It lives at the top level of the tree rather than beside versions.server /
#: versions.build, so that nothing suggests the three move together. They do
#: not: versions is the RELEASE, read from updater/updater_manifest.json.
SETTINGS_SCHEMA_VERSION = 2


class SettingsSchema(_Section):
    schema_version: int = SETTINGS_SCHEMA_VERSION
    versions: Versions
    server_info: ServerInfo
    probe_settings: ProbeSettings = ProbeSettings()
    globals: GlobalSettings = GlobalSettings()
    platform: Platform = Platform()
    cycle_data: CycleData = CycleData()
    controller: ControllerSettings = ControllerSettings()
    display: DisplaySettings = DisplaySettings()
    keep_warm: KeepWarm = KeepWarm()
    smoke_plus: SmokePlus = SmokePlus()
    pwm: PwmSettings = PwmSettings()
    safety: SafetySettings = SafetySettings()
    pelletlevel: PelletLevel = PelletLevel()
    modules: Modules = Modules()
    lastupdated: LastUpdated
    startup: StartupSettings = StartupSettings()
    shutdown: ShutdownSettings = ShutdownSettings()
    dashboard: Dashboard = Dashboard()
    notify_services: NotifyServices = NotifyServices()
    history_page: HistoryPage = HistoryPage()
    recipe: Recipe = Recipe()

    @model_validator(mode="after")
    def _check_startup_pwm_duty_cycle(self) -> "SettingsSchema":
        # Clamp source: blueprints/settings/routes.py:493-497 -- chained
        # min()/max() against the sibling `pwm` section's min_duty_cycle/
        # max_duty_cycle. Cross-SECTION (startup vs. pwm are siblings here),
        # so it lives on the top-level model rather than on StartupSettings.
        if not (self.pwm.min_duty_cycle <= self.startup.pwm_duty_cycle <= self.pwm.max_duty_cycle):
            raise ValueError("startup.pwm_duty_cycle must be within [pwm.min_duty_cycle, pwm.max_duty_cycle]")
        return self


class SettingsValidationError(ValueError):
    """A settings tree (or delta) failed strict schema validation."""

    def __init__(self, errors: list[str], pairs: list[dict] | None = None):
        self.errors = errors
        self.pairs = pairs or []
        super().__init__("; ".join(errors))


def _format_errors(errs: list[ErrorDetails]) -> list[str]:
    return [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in errs]


def _error_pairs(errs: list[ErrorDetails]) -> list[dict]:
    return [{"path": ".".join(str(p) for p in err["loc"]), "message": err["msg"]} for err in errs]


def format_validation_errors(exc: ValidationError) -> list[str]:
    """Dotted-path `"section.field: reason"` strings for a pydantic ValidationError."""
    return _format_errors(exc.errors())


def format_validation_pairs(exc: ValidationError) -> list[dict]:
    """`{"path": "section.field", "message": reason}` for a pydantic ValidationError.

    The structured twin of format_validation_errors: same paths, same order, not
    yet joined, so a caller can route each one to the field that produced it.
    """
    return _error_pairs(exc.errors())


def _declared_types(annotation: Any) -> set[type]:
    """The concrete scalar types an annotation admits, unwrapping unions and
    Literals (`Literal["F", "C"]` admits str)."""
    origin = get_origin(annotation)
    if origin is Literal:
        return {type(arg) for arg in get_args(annotation)}
    if origin is Union or origin is UnionType:
        types: set[type] = set()
        for arg in get_args(annotation):
            types |= _declared_types(arg)
        return types
    return {annotation}


def declared_types_for_path(path) -> set[type]:
    """The types the schema declares at a dotted settings path, or an empty set
    if nothing is modelled there."""
    model: Any = SettingsSchema
    annotation: Any = None
    for key in path:
        fields = getattr(model, "model_fields", None)
        if not fields:
            return set()
        info = fields.get(key) or next((f for f in fields.values() if f.alias == key), None)
        if info is None:
            return set()
        annotation = info.annotation
        model = annotation
    return _declared_types(annotation) if annotation is not None else set()


def atomic_settings_paths() -> set[tuple[str, ...]]:
    """Settings paths whose value is a tagged union -- one value, not a
    namespace to merge into.

    Walks SettingsSchema's fields recursively and yields the dotted path (as
    a tuple) of every field whose annotation is a union of `_Section` models
    -- exactly what `I2CBusConfig` is, and what any future tagged union here
    would be. A delta that names such a path is replacing the whole value: a
    bus that becomes `mcp2221` has no `adapter`, and merging the delta into
    what was there would leave the previous kind's selector behind.
    """

    def is_tagged_union(annotation: Any) -> bool:
        origin = get_origin(annotation)
        if origin is not Union and origin is not UnionType:
            return False
        members = get_args(annotation)
        return bool(members) and all(isinstance(member, type) and issubclass(member, _Section) for member in members)

    def walk(model: Any, prefix: tuple[str, ...]) -> set[tuple[str, ...]]:
        paths: set[tuple[str, ...]] = set()
        for key, info in model.model_fields.items():
            path = prefix + (info.alias or key,)
            annotation = info.annotation
            if is_tagged_union(annotation):
                paths.add(path)
            elif isinstance(annotation, type) and issubclass(annotation, _Section):
                paths |= walk(annotation, path)
        return paths

    return walk(SettingsSchema, ())


def apply_settings_delta(settings, delta):
    """Merge `delta` into `settings`, replacing tagged-union values wholesale.

    `deep_update` stays a generic, unconditional mapping merge -- the
    knowledge that a path like `platform.devices.distance.i2c_bus` is a
    single value rather than a namespace belongs to the settings domain, not
    to a dict utility. The atomic subtrees named in `delta` are lifted out
    before the merge and assigned back directly afterward, so `deep_update`
    never even sees them as something to merge into.
    """
    delta = copy.deepcopy(delta)
    overrides: dict[tuple[str, ...], Any] = {}
    for path in atomic_settings_paths():
        node = delta
        for key in path[:-1]:
            if not isinstance(node, dict) or key not in node:
                node = None
                break
            node = node[key]
        if not isinstance(node, dict) or path[-1] not in node:
            continue
        overrides[path] = node.pop(path[-1])

    deep_update(settings, delta)

    for path, value in overrides.items():
        node = settings
        for key in path[:-1]:
            node = node.setdefault(key, {})
        node[path[-1]] = value
    return settings


def coerce_setting_value(path, value, fallback):
    """Convert a wizard manifest option string to the type the schema declares
    for `path`, deferring to `fallback` where the schema says nothing useful.

    The manifest can only carry strings -- its options are JSON object KEYS --
    so something has to turn "14" into an int. Guessing from the string's shape
    alone (the wizard's own _convert_value) gets that right only when the target
    happens to be numeric: it turned the ft232h url option "1" into int 1 for a
    field the schema declares `str`, and the write failed inside the detached
    installer. Asking the schema what the field IS makes the answer depend on
    the destination rather than on how the value happens to look.

    Order matters: a value is converted to the most specific declared type it
    can satisfy, so a pin field of `int | str | None` still yields int 14 for
    the Pi's "14" and keeps "C0" a string for the FT232H.
    """
    declared = declared_types_for_path(path)
    if not declared or not isinstance(value, str):
        return fallback(value)
    if type(None) in declared and value == "None":
        return None
    if bool in declared and value in ("True", "False"):
        return value == "True"
    if int in declared and (value.isdigit() or (value.startswith("-") and value[1:].isdigit())):
        return int(value)
    if float in declared:
        try:
            return float(value)
        except ValueError:
            pass
    if str in declared:
        return value
    # Nothing declared here can hold a string (a list or nested model). Let the
    # caller's shape-based guess have it -- and validation catch it if wrong.
    return fallback(value)


def validate_partial_settings(delta: dict) -> list[str]:
    """FIELD-level strict-validate a sparse settings delta; dotted-path error
    strings, empty if the delta type-checks (this is Layer 1).

    PartialSettingsSchema (below) recursively makes every field optional so a
    sparse delta -- e.g. a single settings-tab PATCH -- validates without
    requiring the rest of the tree. But its inherited
    `model_validator(mode="after")` cross-field/cross-section rules
    (PwmSettings._check_profiles, SmartStart._check_profile_count,
    SettingsSchema._check_startup_pwm_duty_cycle) still run on every section
    absent from the delta -- against that section's STATIC DEFAULT, not the
    store's actual current value. A field the store has legitimately moved
    away from its default (e.g. pwm.min_duty_cycle lowered via PwmTab) can
    then make an unrelated, otherwise-valid delta (e.g. StartupTab's sparse
    `{"startup": {"pwm_duty_cycle": ...}}`) fail here even though it's fine
    against the real merged tree.

    Cross-field validation is Layer 2's job alone
    (validate_settings_tree() on the merged tree, which has real values
    everywhere, called by write_settings() -- see
    blueprints/api/routes.py:_api_post_settings_update). This filters those
    errors out of Layer 1's report and returns only genuine per-field
    type/shape violations, each traceable to a field actually present in the
    delta.

    Discriminator (empirically verified against a live ValidationError; see
    tests/unit/common/test_settings_schema.py): every `model_validator` here
    raises a bare `ValueError`, which pydantic reports with
    `err["type"] == "value_error"`. No field-level failure in this schema --
    strict-mode type mismatches ("int_type", "string_type", ...), Field
    ge/le/pattern violations ("less_than_equal", "greater_than_equal", ...),
    Literal mismatches ("literal_error"), etc. -- ever produces that generic
    type; those are all pydantic's own specific error codes. So dropping
    "value_error" entries leaves exactly the field-level errors.
    """
    try:
        PartialSettingsSchema.model_validate(delta, strict=True)
    except ValidationError as exc:
        field_errors = [err for err in exc.errors() if err["type"] != "value_error"]
        return _format_errors(field_errors)
    return []


def validate_partial_settings_pairs(delta: dict) -> list[dict]:
    """`{"path": ..., "message": ...}` for a sparse delta; empty if it type-checks.

    The structured twin of validate_partial_settings, over the same
    PartialSettingsSchema and therefore the same Layer-1 rules: field types
    only, no cross-field validators, which on a sparse delta would run against
    static defaults rather than the store's real values.
    """
    try:
        PartialSettingsSchema.model_validate(delta, strict=True)
    except ValidationError as exc:
        field_errors = [err for err in exc.errors() if err["type"] != "value_error"]
        return _error_pairs(field_errors)
    return []


#: Settings that were RENAMED rather than removed, as
#: {old dotted path: new dotted path}. The repair gate below strips every
#: unmodeled key, which for a rename would silently discard the user's
#: configured value; carrying it across first makes repair lossless.
#: `upgrade_settings()` cannot do this job -- it is gated on `prev_ver`, so it
#: never fires for an install already sitting on the current version, which is
#: exactly where an un-migrated rename strands the old key.
#: Empty is the correct resting state: it means no rename is currently
#: mid-flight. The mechanism below is kept armed rather than deleted with its
#: last entry, because a rename that ships without one strands the user's value
#: silently, and that is not a failure anyone sees. Its tests install a
#: synthetic entry, so they prove the carry works for the NEXT rename rather
#: than for whichever one happened to be listed here.
_RENAMED_SETTINGS: dict[str, str] = {}


def _carry_renamed_keys(tree: dict) -> list[tuple[str, str]]:
    """Move each `_RENAMED_SETTINGS` value to its new path, in place.

    Only fills a destination that is absent or None -- a value already written
    under the new name wins over the stale one. Returns the (old, new) pairs
    actually carried, for logging. Caller passes a COPY (see
    validate_settings_tree); this mutates `tree` directly.
    """
    carried = []
    for old_path, new_path in _RENAMED_SETTINGS.items():
        *old_parents, old_key = old_path.split(".")
        *new_parents, new_key = new_path.split(".")
        src = tree
        for part in old_parents:
            src = src.get(part) if isinstance(src, dict) else None
            if not isinstance(src, dict):
                break
        if not isinstance(src, dict) or old_key not in src:
            continue
        value = src.pop(old_key)
        dst = tree
        for part in new_parents:
            dst = dst.get(part) if isinstance(dst, dict) else None
            if not isinstance(dst, dict):
                break
        if not isinstance(dst, dict):
            continue
        if dst.get(new_key) is None:
            dst[new_key] = value
            carried.append((old_path, new_path))
    return carried


def _strip_error_locs(tree: dict, errors: list[ErrorDetails]) -> None:
    """Delete each error's `loc` path from `tree` in place.

    Every `extra_forbidden` error's `loc` is a tuple of dict keys ending at
    the offending unmodeled key -- pydantic's `extra` policy only ever fires
    on a dict-like (object) level, never inside a list, so the final segment
    is always a plain string key in a dict reachable by walking `loc[:-1]`
    through `tree`. Caller is responsible for passing a COPY of the real
    input (see validate_settings_tree) -- this mutates `tree` directly.

    Tolerant of an already-absent path: `_carry_renamed_keys()` runs first and
    pops the old name of any renamed setting, so that key's `extra_forbidden`
    error is still in `errors` but no longer in `tree`.
    """
    for err in errors:
        loc = err["loc"]
        cur = tree
        for part in loc[:-1]:
            cur = cur.get(part) if isinstance(cur, dict) else None
            if not isinstance(cur, dict):
                break
        if isinstance(cur, dict):
            cur.pop(loc[-1], None)


def validate_settings_tree(settings: dict, *, persisted: bool = True) -> dict:
    """Strict-validate a full settings tree; return the normalized dump.

    This is the single enforcement entry -- write_settings() calls it before
    persisting. Raises SettingsValidationError with dotted-path messages on
    failure.

    `persisted` only changes what the repair-success log lines below say; it
    never changes whether stripping/carrying/raising happens. Pass False for
    a read-only caller (e.g. a startup drift check) that discards the
    returned dump instead of writing it back, so the log doesn't claim a
    repair was written when nothing was.

    `_Section` is `extra="forbid"`, so an unmodeled key anywhere in the tree
    fails validation. Rather than let that permanently block every subsequent
    save (the tree-wide blast-radius risk of a naive forbid flip), this is a
    self-healing repair gate: if EVERY error the first validation attempt
    produces is
    "extra_forbidden" (i.e. nothing else is wrong), those keys are stripped
    from a deepcopy of `settings` -- the caller's dict is never mutated --
    and validation is retried exactly once. Only once that retry SUCCEEDS is
    each stripped dotted path logged via write_log() -- logging is deferred
    until repair is known to have actually rescued the write, so a tree that
    still fails after stripping (a real error was hiding behind/alongside
    the extra key, e.g. a cross-field model_validator that only runs once
    every field validates) never logs a misleading "stripped" line for a
    write that didn't happen. Any error that ISN'T "extra_forbidden" (bad
    value, missing required field, cross-field model_validator failure),
    whether alone or mixed in alongside extra keys on the first attempt, or
    surfacing only on the retry, still raises SettingsValidationError -- repair
    never masks a real bug.
    """
    try:
        model = SettingsSchema.model_validate(settings, strict=True)
    except ValidationError as exc:
        errors = exc.errors()
        if not errors or any(err["type"] != "extra_forbidden" for err in errors):
            raise SettingsValidationError(format_validation_errors(exc), format_validation_pairs(exc)) from exc

        repaired = copy.deepcopy(settings)
        carried = _carry_renamed_keys(repaired)
        _strip_error_locs(repaired, errors)

        try:
            model = SettingsSchema.model_validate(repaired, strict=True)
        except ValidationError as retry_exc:
            raise SettingsValidationError(
                format_validation_errors(retry_exc), format_validation_pairs(retry_exc)
            ) from retry_exc

        carried_olds = {old for old, _ in carried}
        verb = "during write-time repair" if persisted else "in a read-only check (not written back)"
        for old_path, new_path in carried:
            write_log(f"settings: carried renamed key '{old_path}' -> '{new_path}' {verb}")
        for err in errors:
            dotted = ".".join(str(part) for part in err["loc"])
            if dotted in carried_olds:
                continue
            write_log(f"settings: stripped unmodeled key '{dotted}' {verb} (was: {err['msg']})")
    # by_alias=True: platform.system.one_wire must dump back out as "1WIRE"
    # (its defaults.py/on-disk key) -- see the alias comment on _SystemConfig.
    # No other field in the tree carries an alias, so this is a no-op for the
    # rest of the dump (mechanism verified by the parity tests staying green).
    return model.model_dump(mode="json", by_alias=True)


# Recursive all-optional twin of SettingsSchema, for validating sparse deltas
# (e.g. a single-tab PATCH from the settings API) without requiring every
# required field (versions.*, server_info.uuid, lastupdated.time,
# notify_services.onesignal.uuid) to be present. ERROR-QUALITY nicety, not
# the enforcement mechanism -- validate_settings_tree() above is that.
PartialSettingsSchema = create_partial_model(SettingsSchema, recursive=True)


def export_schema() -> dict:
    return SettingsSchema.model_json_schema()


if __name__ == "__main__":
    print(json.dumps(export_schema(), indent=2, sort_keys=True))
