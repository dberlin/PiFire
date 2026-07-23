"""Pydantic shadow models for the PiFire settings tree (S1: shape only).

common/defaults.py remains the defaults AUTHORITY — these models mirror it,
and tests/unit/common/test_settings_schema.py fails on any divergence.
Do not add validation constraints here in S1 (that is S2's job); do not
change a default here without changing defaults.py (parity will fail).
Unknown keys are allowed everywhere: legacy stores and future upgrades
must always validate.
"""

from typing import Optional

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
    address: Optional[int] = None
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


class SettingsSchema(_Section):
    safety: SafetySettings = SafetySettings()
    pelletlevel: PelletLevel = PelletLevel()
    keep_warm: KeepWarm = KeepWarm()
    smoke_plus: SmokePlus = SmokePlus()
    cycle_data: CycleData = CycleData()
    shutdown: ShutdownSettings = ShutdownSettings()
    modules: Modules = Modules()
    platform: Platform = Platform()
    # Remaining sections arrive in Task 2; extra="allow" passes them through.
