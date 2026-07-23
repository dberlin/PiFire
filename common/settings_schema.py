"""Pydantic shadow models for the PiFire settings tree.

common/defaults.py remains the defaults AUTHORITY — these models mirror it,
and tests/unit/common/test_settings_schema.py fails on any divergence.
Do not change a default here without changing defaults.py (parity will fail).
Unknown keys are allowed everywhere: legacy stores and future upgrades
must always validate.

S2 (Task 2) migrated every clamp/invariant enforced today by
blueprints/settings/routes.py and the web-react settings tabs into schema
constraints: `Field(ge=..., le=...)` for scalar bounds, and
`model_validator(mode="after")` for cross-field/cross-section rules (see
PwmSettings, SmartStart, and SettingsSchema._check_startup_pwm_duty_cycle
below). Only constraints traced to an actual source-code clamp were added --
no new limits were invented. Any NEW constraint must trace the same way.
"""

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_partial import create_partial_model


class _Section(BaseModel):
    model_config = ConfigDict(extra="allow")


class SafetySettings(_Section):
    # Mirrors defaults.py settings["safety"] — transcribed 2026-07-23.
    minstartuptemp: int = 75
    maxstartuptemp: int = 100
    maxtemp: int = 550
    reigniteretries: int = 1
    startup_check: bool = True
    allow_manual_changes: bool = False
    manual_override_time: int = 30


class PelletLevel(_Section):
    # Mirrors defaults.py settings["pelletlevel"] — transcribed 2026-07-23.
    warning_enabled: bool = True
    warning_level: int = 25
    warning_time: int = 20
    empty: int = 22
    full: int = 4


class KeepWarm(_Section):
    # Mirrors defaults.py settings["keep_warm"] — transcribed 2026-07-23.
    temp: int = 165
    s_plus: bool = False


class SmokePlus(_Section):
    # Mirrors defaults.py settings["smoke_plus"] — transcribed 2026-07-23.
    enabled: bool = False
    min_temp: int = 160
    max_temp: int = 220
    on_time: int = 5
    off_time: int = 5
    duty_cycle: int = 75
    fan_ramp: bool = False


class CycleData(_Section):
    # Mirrors defaults.py settings["cycle_data"] — transcribed 2026-07-23.
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
    # Mirrors defaults.py settings["shutdown"] — transcribed 2026-07-23.
    shutdown_duration: int = 240
    auto_power_off: bool = False


class Modules(_Section):
    # Mirrors defaults.py settings["modules"] — transcribed 2026-07-23.
    grillplat: str = "prototype"
    display: str = "none"
    dist: str = "none"


class _DisplayDeviceConfig(_Section):
    dc: int = 24
    led: int = 5
    rst: int = 25


class _DistanceDeviceConfig(_Section):
    echo: int = 27
    trig: int = 23
    i2c_bus_kind: str = "basic"
    i2c_bus_num: str = "CP2112"
    address: int | None = None
    device: str = "/dev/ttyACM0"


class _InputDeviceConfig(_Section):
    down_dt: int = 20
    enter_sw: int = 21
    up_clk: int = 16


class _DevicesConfig(_Section):
    display: _DisplayDeviceConfig = _DisplayDeviceConfig()
    distance: _DistanceDeviceConfig = _DistanceDeviceConfig()
    input: _InputDeviceConfig = _InputDeviceConfig()


class _InputsConfig(_Section):
    selector: int = 17
    shutdown: int = 17


class _OutputsConfig(_Section):
    auger: int = 14
    dc_fan: int = 26
    fan: int = 15
    igniter: int = 18
    power: int = 4
    pwm: int = 13


class _SPI0Config(_Section):
    CE0: int = 8
    CE1: int = 7


class _SystemConfig(_Section):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

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
    i2c_bus_kind: str = "basic"
    i2c_bus_num: str = "1"
    address: str = "0x4c"


class _FT232hConfig(_Section):
    url: str = "1"


class Platform(_Section):
    # Mirrors defaults.py settings["platform"] — transcribed 2026-07-23.
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
    # static per-model default, it tracks the installed manifest) --
    # transcribed 2026-07-22. Required, no defaults; parity holds because
    # validate-then-dump preserves the input values.
    server: str
    cookfile: str
    recipe: str
    build: int


class ServerInfo(_Section):
    # Mirrors defaults.py settings["server_info"] — transcribed 2026-07-22.
    # uuid is generated fresh by generate_uuid() per-install; required, no
    # default (parity holds because validate-then-dump preserves the input).
    uuid: str


class LastUpdated(_Section):
    # Mirrors defaults.py settings["lastupdated"] — transcribed 2026-07-22.
    # time is a live timestamp (math.trunc(time.time())) set at build time;
    # required, no default, same reasoning as ServerInfo.uuid.
    time: int


class ProbeMap(_Section):
    # Outer shape of probe_settings["probe_map"]; the device/probe dicts
    # inside are driver- and profile-specific and stay loose.
    probe_devices: list[dict] = []
    probe_info: list[dict] = []


class ProbeSettings(_Section):
    # Mirrors defaults.py settings["probe_settings"] — transcribed 2026-07-22.
    # probe_profiles is keyed by profile id (from probes/probes.json plus any
    # user-added profiles); profile contents are profile-specific and loose.
    probe_profiles: dict[str, dict] = {}
    probe_map: ProbeMap = ProbeMap()


class GlobalSettings(_Section):
    # Mirrors defaults.py settings["globals"] — transcribed 2026-07-22.
    grill_name: str = ""
    debug_mode: bool = False
    page_theme: str = "light"
    disp_rotation: int = 0
    units: Literal["F", "C"] = "F"
    augerrate: float = 0.3
    first_time_setup: bool = True
    ext_data: bool = False
    global_control_panel: bool = False
    boot_to_monitor: bool = False
    prime_ignition: bool = False
    updated_message: bool = False
    venv: bool = True
    python_exec: str = ".venv/bin/python"
    uv: bool = True


class ControllerSettings(_Section):
    # Mirrors defaults.py settings["controller"] — transcribed 2026-07-22.
    # config is keyed by controller name (from controller/controllers.json);
    # each controller's option set/types are controller-specific and loose.
    selected: str = "pid"
    config: dict[str, dict[str, float | int | bool | str]] = {}


class DisplaySettings(_Section):
    # Mirrors defaults.py settings["display"] — transcribed 2026-07-22.
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
    # Mirrors defaults.py settings["pwm"] — transcribed 2026-07-22.
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
    # Clamp source: web-react/src/components/settings/tabs/StartupTab.tsx:13-15
    # (RangeProfileTable column min/max for the smartstart profile table).
    startuptime: int = Field(ge=30, le=1200)
    augerontime: int = Field(ge=1, le=60)
    p_mode: int = Field(ge=0, le=9)


class SmartStart(_Section):
    # Mirrors defaults.py settings["startup"]["smartstart"] — transcribed 2026-07-22.
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
    # Mirrors defaults.py settings["startup"]["start_to_mode"] — transcribed 2026-07-22.
    after_startup_mode: Literal["Smoke", "Hold"] = "Smoke"
    primary_setpoint: int = 165
    start_to_hold_prompt: bool = False


class StartupSettings(_Section):
    # Mirrors defaults.py settings["startup"] — transcribed 2026-07-22.
    duration: int = 240
    # Clamp source: blueprints/settings/routes.py:479-483 -- out of [0, 200]
    # is silently zeroed by the legacy route; S2 rejects instead (see
    # test_prime_on_startup_range_rejects).
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
    # from dashboard/*.json metadata) — transcribed 2026-07-22. Per-dashboard
    # entries (name/friendly_name/html_name/metadata/custom/screenshot/config)
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
    # Mirrors defaults.py default_notify_services() — transcribed 2026-07-22.
    # Every current service has a static, stable shape, so each gets its own
    # model (see the per-service verdicts in the Task 2 report); extra="allow"
    # on _Section still lets a brand-new/unmodeled service key pass through.
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
    # :307-336) writes for EVERY probe (Primary and Food) — transcribed
    # 2026-07-22. Primary-only setpoint-color fields (bg_color_setpoint/
    # line_color_setpoint) are not modeled; they ride through extra="allow".
    name: str
    type: str
    enabled: bool
    line_color: str
    line_color_target: str
    dash_setpoint: bool
    bg_color: str
    bg_color_target: str
    fill: bool


class HistoryPage(_Section):
    # Mirrors defaults.py settings["history_page"] — transcribed 2026-07-22.
    minutes: int = 15
    clearhistoryonstart: bool = True
    autorefresh: Literal["on", "off"] = "on"
    datapoints: int = 60
    probe_config: dict[str, ProbeChartConfig] = {}


class RecipeProbeMap(_Section):
    primary: str = ""
    food: list[str] = []


class Recipe(_Section):
    # Mirrors defaults.py settings["recipe"] — transcribed 2026-07-22.
    probe_map: RecipeProbeMap = RecipeProbeMap()


class SettingsSchema(_Section):
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

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _format_errors(errs: list[dict]) -> list[str]:
    return [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in errs]


def format_validation_errors(exc: ValidationError) -> list[str]:
    """Dotted-path `"section.field: reason"` strings for a pydantic ValidationError."""
    return _format_errors(exc.errors())


def validate_partial_settings(delta: dict) -> list[str]:
    """FIELD-level strict-validate a sparse settings delta; dotted-path error
    strings, empty if the delta type-checks (Task 5's Layer 1).

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


def validate_settings_tree(settings: dict) -> dict:
    """Strict-validate a full settings tree; return the normalized dump.

    This is S2's single enforcement entry -- write_settings() calls it before
    persisting (Task 5). Raises SettingsValidationError with dotted-path
    messages on failure.
    """
    try:
        model = SettingsSchema.model_validate(settings, strict=True)
    except ValidationError as exc:
        raise SettingsValidationError(format_validation_errors(exc)) from exc
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
