# Generic x86 Grill Platform (Numato relays + EMC2101 PWM)

## Overview

Add a new PiFire grill platform that runs on generic x86 hardware (no
Raspberry Pi GPIO). Output control uses the Numato 4-channel USB relay module
(already implemented in `grillplat/numato_usbrelay.py`), and fan PWM uses an
EMC2101 fan controller reached over the I2C bus exposed by a CP2112
USB-to-I2C bridge.

This platform is selectable from the configuration wizard.

## Goals

- A new `GrillPlatform` implementation that satisfies the existing platform
  contract used by `control.py`.
- Relay control for power, igniter, auger, and fan via the Numato board.
- Real PWM fan speed control via an EMC2101 at I2C address `0x4c`.
- Selectable and configurable through `wizard/wizard_manifest.json`.

## Non-Goals

- No Raspberry Pi GPIO, hardware PWM, or `vcgencmd`-based throttling checks.
- No selector/shutdown button inputs (x86 builds are assumed standalone).
- No use of the EMC2101's automatic lookup-table fan curve; fan speed is
  driven manually by PiFire's control logic.

## Architecture

### Platform contract

PiFire loads a platform module by name:
`settings['modules']['grillplat']` → `importlib.import_module('grillplat.<name>')`,
then instantiates `GrillPlatform(platform_config)` where
`platform_config = settings['platform']` augmented with
`frequency = settings['pwm']['frequency']`.

The new module is **`grillplat/x86_numato_emc2101.py`**, exposing the standard
`GrillPlatform` class with the methods the rest of PiFire expects:
`auger_on/off`, `fan_on/off/toggle`, `set_duty_cycle`, `pwm_fan_ramp`,
`set_pwm_frequency`, `igniter_on/off`, `power_on/off`, `get_input_status`,
`get_output_status`, `cleanup`, plus the system/platform commands
(`supported_commands`, `check_throttled`, `check_wifi_quality`,
`check_cpu_temp`, `check_alive`, `scan_bluetooth`, `os_info`, `network_info`,
`hardware_info`).

### Composed drivers

- **Relays** — reuses the existing `grillplat/numato_usbrelay.py`
  (`NumatoUSBRelay`) over a serial `/dev/ttyACM*` device. Four relays map to
  power / igniter / auger / fan.
- **PWM fan** — the Adafruit `adafruit_emc2101` library, talking to the
  EMC2101 over `adafruit_extended_bus.ExtendedI2C(bus_num)`
  (`adafruit-extended-bus` is already a project dependency).

### CP2112 I2C bus discovery

A helper `_find_i2c_bus(match)`:

- Scans `/sys/bus/i2c/devices/i2c-*/name`.
- Returns the integer bus number of the adapter whose name contains the match
  string (default `"CP2112"`, case-insensitive).
- Raises a clear error if zero or more than one adapter matches, so
  `control.py`'s existing "log and fall back to prototype" path reports it.

The returned bus number is passed to `ExtendedI2C(bus_num)`.

## Components / Behavior

### Output control (relays)

- Relay indices read from `config['outputs']` with defaults
  `{power: 0, igniter: 1, auger: 2, fan: 3}`.
- Commanded relay state is **cached in the module** so `get_output_status()`
  is fast and does not incur a serial round-trip per poll (mirroring how the
  RPi/prototype modules report `is_active`).
- `auger_on/off`, `igniter_on/off`, `power_on/off` →
  `NumatoUSBRelay.relay_on/off(index)`.

### Fan / PWM (relay gates power + EMC2101 PWM)

Behaves as a DC-fan-style platform (`dc_fan` semantics True) so PWM control
and Smoke Plus operate:

- `fan_on(percent=100)` → close the fan power relay **and**
  `emc.manual_fan_speed = percent`.
- `fan_off()` → `emc.manual_fan_speed = 0` and open the fan relay.
- `fan_toggle()` → toggle the fan relay state.
- `set_duty_cycle(percent, override_ramping=True)` →
  `emc.manual_fan_speed = percent`. **No inversion** — unlike the RPi
  amplifier circuit, EMC2101 duty maps directly to fan speed.
- `pwm_fan_ramp` / `_start_ramp` / `_ramp_device` / `_stop_ramp` → reuse the
  proven ramp logic from `raspberry_pi_all.py`, driving `set_duty_cycle` via a
  `gpiozero.threads.GPIOThread`. Because there is no inversion, the ramp math
  is simplified to operate directly on fan-speed percent.
- `set_pwm_frequency(freq)` → applied through the EMC2101 library's
  PWM-frequency setting; frequency originates from `settings['pwm']['frequency']`.
- `get_output_status()` → reports `auger/igniter/power/fan` (cached) plus
  `pwm` (current fan %) and `frequency`.

### Inputs

- `standalone` defaults True; `get_input_status()` returns `False`.
- Selector/shutdown inputs are unused on this platform.

### System / platform commands

Copied and adapted into the module to match the existing per-platform
convention (prototype and raspberry_pi_all each carry their own copies):

- `check_cpu_temp` → via `psutil.sensors_temperatures()` (e.g. coretemp /
  k10temp), with a graceful `0.0` fallback.
- `check_throttled` → not applicable on x86; returns `result: OK` with
  `cpu_under_voltage` and `cpu_throttled` both `False`.
- `os_info`, `network_info`, `hardware_info`, `scan_bluetooth`,
  `check_wifi_quality`, `check_alive`, `supported_commands` → reuse the
  existing generic implementations (psutil / netifaces / bleak / iwconfig).

### Error handling & cleanup

- Constructor failures (no CP2112 bus found, relay serial device missing,
  EMC2101 absent) raise, triggering `control.py`'s existing log-and-fall-back-
  to-prototype path.
- `cleanup()` → all relays off, `emc.manual_fan_speed = 0`, stop the ramp
  thread, close the serial port.

## Configuration

New keys under `settings['platform']`, read with `.get` defaults so the
module is robust if a key is absent:

```jsonc
"outputs":  { "power": 0, "igniter": 1, "auger": 2, "fan": 3 }, // Numato relay indices
"numato":   { "device": "/dev/ttyACM0", "baudrate": 921600 },
"emc2101":  { "i2c_bus_match": "CP2112", "address": "0x4c" },
"dc_fan":   true
```

## Wizard integration

- New entry in `wizard/wizard_manifest.json` under `modules.grillplatform`,
  keyed `x86_numato_emc2101`, with:
  - `friendly_name` and `description`.
  - `filename`: `x86_numato_emc2101`.
  - `py_dependencies`: `["adafruit-circuitpython-emc2101"]`.
  - `apt_dependencies`: `[]`.
  - `command_list`: empty / no-op (no RPi `board-config.py` run).
  - `settings_dependencies` exposing: Numato device path, the four relay
    indices, the I2C bus match string, and the EMC2101 address.
- `pyproject.toml` gains the `adafruit-circuitpython-emc2101` dependency.

## Testing

Unit tests with the serial port and I2C/EMC2101 mocked (no hardware in CI):

- Relay-to-output mapping uses configured indices and falls back to defaults.
- Fan behavior: `fan_on` closes the relay and sets duty; `fan_off` zeroes duty
  and opens the relay; `set_duty_cycle` sets `manual_fan_speed`; ramp drives
  duty over time without inversion.
- `_find_i2c_bus`: matches a single CP2112 adapter; raises on zero matches and
  on multiple matches.
- `get_output_status` returns the expected shape including `pwm` and
  `frequency`.
- `cleanup` turns outputs off and closes resources.
