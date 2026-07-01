# VL53L4CD support + VL53L0X CircuitPython migration

## Overview

PiFire's hopper-level distance sensing currently supports the VL53L0X
time-of-flight sensor through `distance/vl53l0x.py`, built on the unmaintained
`git+https://github.com/pimoroni/VL53L0X-python.git` library and hardcoded to
Raspberry Pi I2C bus 1. This change:

1. Migrates the VL53L0X driver to Adafruit's `adafruit-circuitpython-vl53l0x`
   library, adding selectable basic/extended I2C bus support (matching the
   convention established for the EMC2101/EMC2301 fan controller).
2. Adds a new `distance/vl53l4cd.py` module supporting the VL53L4CD sensor via
   `adafruit-circuitpython-vl53l4cd`, exposed as an independent, separately
   selectable wizard module (not merged with VL53L0X).
3. Extracts the logic shared by both drivers (polling thread, hopper-percent
   calculation, I2C bus resolution) into a common base class so the two
   drivers don't duplicate ~100 lines of boilerplate.

## Goals

- Replace the VL53L0X driver's dependency on the pimoroni GitHub library with
  `adafruit-circuitpython-vl53l0x`.
- Add VL53L4CD as a new, independently selectable distance-sensor module.
- Both drivers support **basic** (integrated `board.SCL`/`board.SDA`) and
  **extended** (numbered `/dev/i2c-N` bus or adapter-name match, e.g. a CP2112
  bridge) I2C buses, reusing `probes.base.resolve_i2c_bus`.
- Add wizard UI (dropdown + bus-number field) for the distance sensor's I2C
  bus selection on every platform block, mirroring the fan_controller pattern.
- Factor the shared thread/percent-calc/bus-resolution logic into a
  `distance/_tof_base.py` base class; both drivers become thin subclasses.

## Non-Goals

- No settings migration script. Missing `i2c_bus_kind`/`i2c_bus_num` keys on
  existing installs default to `'basic'`, preserving today's Pi-only,
  bus-1 behavior exactly.
- No I2C address configuration in the wizard. Both chips use their factory
  default address `0x29`; changing it (e.g. to run two ToF sensors on one bus)
  is out of scope.
- No changes to `hcsr04.py`, `none.py`, or `prototype.py`.
- No updater_manifest.json version-bump entry — that's part of the maintainer's
  release process, not this feature change.
- No real product photography for the wizard; the new `vl53l4cd.png` wizard
  image is a placeholder, flagged for follow-up.

## Architecture

### Module layout

- **`distance/_tof_base.py`** (new) — `ToFHopperLevel` base class containing
  everything currently in `distance/vl53l0x.py`'s `HopperLevel` that isn't
  chip-specific:
  - `__init__(dev_pins, empty=22, full=4, debug=False)`: validates
    `empty > full` (forcing safe defaults and logging an error otherwise),
    resolves the I2C bus, calls `self._open_sensor(i2c)`, and starts the
    polling thread.
  - I2C bus resolution: reads `dev_pins['distance'].get('i2c_bus_kind',
    'basic')` and `dev_pins['distance'].get('i2c_bus_num', 'CP2112')`.
    `'basic'` → `busio.I2C(board.SCL, board.SDA)`; `'extended'` →
    `ExtendedI2C(resolve_i2c_bus(bus_num))`, importing `resolve_i2c_bus` from
    `probes.base` (no third copy of the bus-matching helper).
  - `_sensing_loop`: same behavior as today's VL53L0X thread — takes 3
    readings via `self._read_distance_mm()`, averages them, converts mm→cm,
    computes the hopper-level percentage from `empty`/`full`, and re-runs
    `self._open_sensor()` if a read cycle takes over 0.5s (stuck-sensor
    recovery).
  - Public API unchanged from today's `HopperLevel`: `set_level`,
    `update_distances`, `get_distances`, `get_level(override=False)`.
  - Subclass hooks (each driver implements these three):
    - `_open_sensor(self, i2c)` — construct the Adafruit driver instance and
      start ranging.
    - `_read_distance_mm(self)` — return one raw distance reading in mm.
    - `_close_sensor(self)` — stop ranging / release the sensor (optional,
      no-op default).

- **`distance/vl53l0x.py`** — `HopperLevel(ToFHopperLevel)`:
  - `_open_sensor`: `self.tof = adafruit_vl53l0x.VL53L0X(i2c, address=0x29)`.
  - `_read_distance_mm`: `self.tof.range` (already mm).

- **`distance/vl53l4cd.py`** (new) — `HopperLevel(ToFHopperLevel)`:
  - `_open_sensor`: `self.tof = adafruit_vl53l4cd.VL53L4CD(i2c, address=0x29)`;
    call `self.tof.start_ranging()`.
  - `_read_distance_mm`: wait for `self.tof.data_ready`, read
    `self.tof.distance` (returned in **cm** by this driver) and convert to mm
    (`* 10`), then `self.tof.clear_interrupt()` per the Adafruit VL53L4CD
    ranging protocol.
  - `_close_sensor`: `self.tof.stop_ranging()`.

Both `distance/vl53l0x.py` and `distance/vl53l4cd.py` keep the `HopperLevel`
class name so `control.py`'s `importlib.import_module(f'distance.{dist_name}')`
+ `DistanceModule.HopperLevel(...)` loading is unchanged.

### Settings

New keys under `settings['platform']['devices']['distance']` (alongside the
existing `echo`/`trig` used by `hcsr04.py`), added to `common/common.py`'s
default settings:

```jsonc
"distance": {
  "echo": 27,
  "trig": 23,
  "i2c_bus_kind": "basic",   // "basic" | "extended"
  "i2c_bus_num":  "CP2112"   // numbered bus or adapter-name match
}
```

Both VL53L0X and VL53L4CD drivers read these two new keys via
`dev_pins['distance'].get(...)`; `hcsr04.py`/`none.py`/`prototype.py` ignore
them, matching how those modules already ignore each other's keys.

## Wizard integration

In `wizard/wizard_manifest.json`, under `modules.distance`:

- **`vl53l0x`** entry updated: `py_dependencies` →
  `["adafruit-circuitpython-vl53l0x"]` (removing the pimoroni git URL),
  `apt_dependencies` → `[]` (matching the emc2101 entry — blinka's I2C access
  doesn't need `python3-smbus`).
- **`vl53l4cd`** entry added (new), mirroring `vl53l0x`'s shape:
  `friendly_name: "VL53L4CD Time of Flight Distance Sensor"`,
  `filename: "vl53l4cd"`, a description noting it as a newer-generation ToF
  sensor, `image: "vl53l4cd.png"` (placeholder), `py_dependencies:
  ["adafruit-circuitpython-vl53l4cd"]`, `apt_dependencies: []`.

Under each of the 6 platform blocks (`custom`, `pcb_2.00a`, `pcb_3.01a`,
`pcb_pwm`, `pcb_4.x.x`, `x86_numato`), two new fields added alongside the
existing `device_distance_echo`/`device_distance_trig` fields:

- **`device_distance_i2c_bus_kind`** — dropdown, `"basic"` / `"extended"`,
  bound to `["platform", "devices", "distance", "i2c_bus_kind"]`, default
  `"basic"` on the Pi PCB platforms.
- **`device_distance_i2c_bus_num`** — text field (shown when `i2c_bus_kind ==
  "extended"`), bound to `["platform", "devices", "distance", "i2c_bus_num"]`,
  default `"CP2112"`.

`x86_numato` defaults `i2c_bus_kind` to `"extended"` / `"CP2112"`, matching its
existing `fan_controller` default, since that platform has no integrated I2C
bus in the usual sense.

## Dependencies

- `pyproject.toml`: add `adafruit-circuitpython-vl53l0x` and
  `adafruit-circuitpython-vl53l4cd` to `dependencies` (unconditional, matching
  how `adafruit-circuitpython-emc2101` was added — not gated behind an extra).
  Run `uv lock` to update `uv.lock`.
- `wizard/wizard_manifest.json` `py_dependencies` updated as above so a wizard
  (re-)run on an existing install installs the correct package for whichever
  distance module is selected.

## Configuration migration

None. Existing installs with `modules.dist == 'vl53l0x'` keep working
unmodified: `common/common.py`'s new default settings mean
`i2c_bus_kind`/`i2c_bus_num` resolve to `'basic'` even for settings.json files
written before this change, reproducing today's `i2c_bus=1` behavior. The
underlying pip package only changes when the user re-runs the wizard or a
future updater release installs the new dependency.

## Testing

Unit tests, no hardware (I2C mocked, following `tests/test_emc2301.py`'s
`FakeI2C` pattern):

- **`tests/test_tof_base.py`** (new): exercise `ToFHopperLevel` via a minimal
  fake subclass (fixed/sequenced `_read_distance_mm` return values):
  - Empty/full validation forces safe defaults when `empty <= full`.
  - Percentage calculation at the boundaries (`<= full` → 100, `<= empty` →
    interpolated, `> empty` → 0) and for a mid-range reading.
  - `get_level(override=True)` blocks until the thread's next read completes.
  - A slow read cycle (>0.5s) triggers `_open_sensor` again (stuck-sensor
    recovery).
  - Basic vs. extended I2C bus resolution calls the right bus constructor
    (`busio.I2C` vs. `ExtendedI2C(resolve_i2c_bus(...))`), with `board`/
    `busio`/`ExtendedI2C`/`resolve_i2c_bus` mocked.
- **`tests/test_vl53l0x.py`** (new): `_open_sensor` constructs
  `adafruit_vl53l0x.VL53L0X` at address `0x29`; `_read_distance_mm` returns
  `tof.range` directly.
- **`tests/test_vl53l4cd.py`** (new): `_open_sensor` constructs
  `adafruit_vl53l4cd.VL53L4CD` at address `0x29` and calls `start_ranging()`;
  `_read_distance_mm` waits for `data_ready`, reads `distance` (cm→mm
  conversion), and calls `clear_interrupt()`.
- **`tests/test_distance_manifest.py`** (new, or added to an existing manifest
  test file): assert the `vl53l0x` entry's updated `py_dependencies` /
  `apt_dependencies`, the new `vl53l4cd` entry's presence and dependencies,
  and that all 6 platform blocks carry the new
  `device_distance_i2c_bus_kind`/`device_distance_i2c_bus_num` fields bound to
  the correct settings paths.

## Documentation

- `README.md:50` — update the VL53L0X bullet to mention VL53L4CD as an
  available alternative and note both now install via Adafruit's
  CircuitPython libraries rather than a third-party GitHub package.
