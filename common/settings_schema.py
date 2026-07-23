"""Pydantic shadow models for the PiFire settings tree (S1: shape only).

common/defaults.py remains the defaults AUTHORITY — these models mirror it,
and tests/unit/common/test_settings_schema.py fails on any divergence.
Do not add validation constraints here in S1 (that is S2's job); do not
change a default here without changing defaults.py (parity will fail).
Unknown keys are allowed everywhere: legacy stores and future upgrades
must always validate.
"""

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


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
    SPI0: _SPI0Config = _SPI0Config()
    # Note: 1WIRE is None in defaults, represented as Optional[None] = None
    # Will pass through via extra="allow" if present


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
    sleep_timeout: int = 300
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


class SmartStartProfile(_Section):
    startuptime: int
    augerontime: int
    p_mode: int


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


class StartToMode(_Section):
    # Mirrors defaults.py settings["startup"]["start_to_mode"] — transcribed 2026-07-22.
    after_startup_mode: Literal["Smoke", "Hold"] = "Smoke"
    primary_setpoint: int = 165
    start_to_hold_prompt: bool = False


class StartupSettings(_Section):
    # Mirrors defaults.py settings["startup"] — transcribed 2026-07-22.
    duration: int = 240
    prime_on_startup: int = 0
    startup_exit_temp: int = 0
    start_to_mode: StartToMode = StartToMode()
    smartstart: SmartStart = SmartStart()
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
    idle_brightness: int = 20
    night_mode: bool = False
    led_count: int = 6


class WledService(_Section):
    enabled: bool = False
    device_address: str = "wled.local"
    use_profiles: bool = True
    use_suggested_presets: bool = False
    profile_numbers: dict[str, int] = {
        "idle": 200,
        "booting": 201,
        "preheat": 202,
        "cooking": 203,
        "cooldown": 204,
        "target_reached": 205,
        "overshoot_alarm": 206,
        "probe_alarm": 207,
        "low_pellets": 208,
        "timer_done": 209,
        "error_fault": 210,
        "night_mode": 211,
    }
    mode_presets: dict[str, int] = {
        "Stop": 1,
        "Startup": 1,
        "Reignite": 1,
        "Smoke": 1,
        "Hold": 1,
        "Shutdown": 1,
        "Prime": 1,
    }
    event_presets: dict[str, int] = {
        "Temp_Achieved": 1,
        "Recipe_Next": 1,
        "Grill_Error": 1,
        "Pellet_Level_Low": 1,
        "Timer_Expired": 1,
    }
    suggested_config: WledSuggestedConfig = WledSuggestedConfig()
    notify_duration: int = 120


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


def export_schema() -> dict:
    return SettingsSchema.model_json_schema()


if __name__ == "__main__":
    print(json.dumps(export_schema(), indent=2, sort_keys=True))
