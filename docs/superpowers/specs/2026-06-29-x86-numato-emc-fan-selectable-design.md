# Generic x86 Grill Platform — selectable EMC2101 / EMC2301 fan controller

## Overview

The generic x86 grill platform currently drives fan PWM through an EMC2101 via
the Adafruit `adafruit_emc2101` library. This change makes the fan controller
selectable between the **EMC2101** and the **EMC2301**, and renames the
platform module to a generic name so it is no longer tied to one chip.

There is no Adafruit library for the EMC2301, so this work also adds a small
PiFire-owned `EMC2301` driver that mimics the slice of the `EMC2101` interface
the platform actually uses (`manual_fan_speed`, best-effort `pwm_frequency`).
The platform code stays chip-agnostic: it sets `self.emc.manual_fan_speed`
regardless of which chip is installed.

## Goals

- Rename `grillplat/x86_numato_emc2101.py` → `grillplat/x86_numato.py`.
- Add `grillplat/emc2301.py`: an `EMC2301` driver class with the same
  constructor and `manual_fan_speed` semantics as the Adafruit `EMC2101`.
- Let the platform choose `emc2101` or `emc2301` via configuration and the
  configuration wizard.
- Disable the EMC2301 watchdog (continuous mode) and SMBus timeout at init.
- Actually apply a 4-wire-fan-appropriate PWM frequency (~25–26 kHz) to the
  chip. PiFire's global `settings['pwm']['frequency']` already defaults to
  **25000** Hz (Intel 4-wire spec) and is passed to the platform as
  `config['frequency']`; this change makes the platform *use* it instead of (a)
  silently no-op'ing on the EMC2101 and (b) carrying a misleading in-module
  `100` Hz fallback. The EMC2301 runs at its native **26 kHz**; the EMC2101 is
  explicitly configured to **~25 kHz**.

## Non-Goals

- No backward-compatibility shim or settings migration. Existing
  `x86_numato_emc2101` installs reconfigure through the wizard (hard rename).
- No RPM-based / lookup-table fan curve on either chip; fan speed is driven
  manually by PiFire's control logic (unchanged from today).
- No change to relay handling, ramp logic, inputs, or the system/platform
  commands beyond what the rename requires.

## Architecture

### Module layout

- **`grillplat/x86_numato.py`** — the renamed generic platform module. Same
  `GrillPlatform` class and contract as before; the only behavioral addition is
  a factory that selects the fan-controller driver. Friendly name becomes
  *"Generic x86 (Numato USB Relay + EMC fan controller)"*.
- **`grillplat/emc2301.py`** — new local driver for the EMC2301, written
  against `adafruit_bus_device.i2c_device.I2CDevice` so it works over the same
  `busio.I2C` / `ExtendedI2C` bus objects the platform already builds (basic or
  extended / CP2112).

### Driver interface (chip-agnostic platform code)

Both drivers are constructed from an I2C bus object and expose:

- `manual_fan_speed` — read/write property, fan speed percent `0–100`.
  Out-of-range writes raise `ValueError` (matching the Adafruit EMC2101
  semantics the platform relies on; the platform clamps before writing).
- `pwm_frequency` — settable PWM frequency in Hz. Both drivers now implement it
  (EMC2101 via `EMC2101_LUT`, EMC2301 via its base-frequency/divide registers);
  the platform's `set_pwm_frequency` keeps its `hasattr` + try/except guard for
  safety.

The platform's `fan_on/off`, `set_duty_cycle`, ramp, and `get_output_status`
are unchanged — they only touch `self.emc.manual_fan_speed`. No inversion,
same as today.

## Components / Behavior

### EMC2301 driver (`grillplat/emc2301.py`)

Default SMBus address **`0x2F`**. Relevant registers (Microchip
EMC2301/2/3/5 DS20006532A):

- **Configuration register `0x20`** — written at init via read-modify-write so
  unrelated bits are preserved:
  - **`DIS_TO` (bit 6) = 1** — disable the SMBus timeout (full I²C compliance).
  - **`WD_EN` (bit 5) = 0** — do **not** run the watchdog in continuous mode,
    so the fan driver is never force-ramped to full speed during quiet periods
    between PiFire's duty writes.
  - *To verify during implementation:* the EMC230x also runs a one-shot
    watchdog once after power-on independent of `WD_EN`. If the datasheet
    offers a clean way to suppress it, the driver does so; otherwise the
    continuous-mode disable plus PiFire's periodic duty writes cover the
    practical case.
- **Fan Setting register `0x30`** — direct PWM control (8-bit, `0x00–0xFF`),
  active because the RPM-based algorithm is disabled by default (Fan Config 1
  register `0x32` power-on default `0x2B`, `EN_ALGO`/algorithm bit clear). The
  `manual_fan_speed` setter maps percent `0–100 → 0x00–0xFF`; the getter maps
  back. Init also sets the PWM output configuration (push-pull, correct
  polarity) so duty maps directly to fan speed — *exact output-config/polarity
  register bits to confirm against the datasheet during implementation.*
- **PWM frequency** — the EMC2301 produces **26 kHz by default** (base
  frequency 26 kHz ÷ PWM Divide register `0x31`, default `0x01`). At init the
  driver explicitly selects the 26 kHz base frequency and sets `0x31 = 1` so the
  output is a known 26 kHz rather than relying on power-on defaults. The
  `pwm_frequency` property works in **Hz**: the getter returns `base ÷ divide`;
  the setter picks the nearest of the chip's four selectable base frequencies
  (26000 / 19531 / 4882 / 2441 Hz) and the divide value. *Exact base-frequency
  selection bits/register to confirm against the datasheet during
  implementation.*

`__init__(i2c_bus, address=0x2F)` opens the device, applies the config above,
and leaves the fan stopped (`manual_fan_speed = 0`).

### EMC2101 fan controller (Adafruit library)

Today the platform imports the **base `EMC2101`** class, which has **no
`pwm_frequency` attribute** — so the existing `set_pwm_frequency` is a no-op and
the chip runs at its hardware-default PWM frequency (≈5.8 kHz) while the
platform misleadingly stores/reports `100`. This change fixes that:

- Import and instantiate **`adafruit_emc2101.emc2101_lut.EMC2101_LUT`** instead
  of the base class. It is an `EMC2101` subclass and `manual_fan_speed` works
  exactly as before; it additionally exposes `pwm_frequency`,
  `pwm_frequency_divisor`, and `set_pwm_clock`. The factory configures it for
  **manual (non-LUT) operation** so PiFire's control logic drives the duty.
- At init, configure **~25 kHz**: select the 360 kHz base clock via
  `set_pwm_clock(use_preset=False, use_slow=False)` and set the PWM_F register
  (`pwm_frequency`) to **7** with `pwm_frequency_divisor = 1`, giving
  `360 kHz / (2 × 7) ≈ 25.7 kHz` (the closest the chip reaches to 25 kHz).
- **Resolution tradeoff (EMC2101 only):** on the EMC2101, duty resolution and
  frequency are coupled — duty steps `= 2 × PWM_F`. At PWM_F = 7 that is **14
  duty steps (~7% granularity)**, accepted in exchange for the quiet ~25 kHz
  operation. (The EMC2301 has no such tradeoff: its 8-bit Fan Setting register
  keeps 256 duty steps at 26 kHz.) `manual_fan_speed` continues to take a
  `0–100` percentage; the library rounds it onto the available steps.
- The platform sets the frequency through the same chip-agnostic path
  (`emc.pwm_frequency = …` is now real, not a no-op). A small per-chip mapping
  converts the configured Hz to the EMC2101's PWM_F/divisor register values.

### Platform factory & configuration

New generic settings group under `settings['platform']`:

```jsonc
"fan_controller": {
  "chip":         "emc2101",   // "emc2101" | "emc2301"  (default "emc2101")
  "address":      "0x4c",      // optional; default per chip when unset
  "i2c_bus_kind": "basic",     // "basic" | "extended"   (unchanged semantics)
  "i2c_bus_num":  "CP2112"     // numbered bus or adapter-name match
}
```

- `address` is optional and defaults **per chip** when unset: `0x4C` for
  emc2101, `0x2F` for emc2301. Accepts an int or a hex string (existing
  parsing reused).
- `i2c_bus_kind` / `i2c_bus_num` keep today's basic/extended (integrated bus
  vs numbered `/dev/i2c-N` or CP2112-by-name) behavior.

**PWM frequency uses the existing global, not a new per-chip key.**
`settings['pwm']['frequency']` already defaults to **25000** Hz and `control.py`
passes it to the platform as `config['frequency']`, then re-applies it at
runtime via `set_pwm_frequency()` whenever it differs from
`get_output_status()['frequency']` (when `dc_fan` is set). So:

- The in-module fallback changes from `config.get('frequency', 100)` to
  `config.get('frequency', 25000)`.
- `__init__` **applies the frequency to the chip** (via the same code path as
  `set_pwm_frequency`) so the chip is at the right frequency immediately,
  independent of whether `control.py` later calls `set_pwm_frequency` (which is
  gated on `dc_fan`).
- `get_output_status()['frequency']` reports the **last requested** frequency
  (`self.frequency`), not the chip's exact achieved value, so `control.py`'s
  `requested == reported` comparison settles and it does not call
  `set_pwm_frequency` every control loop.

In `GrillPlatform.__init__`, after the I2C bus is opened, a small factory
selects the driver, then `set_pwm_frequency(self.frequency)` configures it:

- `chip == "emc2101"` → `adafruit_emc2101.emc2101_lut.EMC2101_LUT(i2c)`,
  configured for manual (non-LUT) operation (see EMC2101 section).
- `chip == "emc2301"` → `grillplat.emc2301.EMC2301(i2c, address=...)`.

`set_pwm_frequency(hz)` stores `self.frequency = hz` and maps `hz` onto the
selected chip's registers (EMC2101: PWM_F/divisor; EMC2301: base/divide),
keeping its `hasattr` + try/except guard so a driver without the property never
raises.

Constructor failures (bus not found, chip absent, relay device missing) raise,
triggering `control.py`'s existing log-and-fall-back-to-prototype path —
unchanged.

### Wizard integration

In `wizard/wizard_manifest.json`, under `modules.grillplatform`:

- Replace the `x86_numato_emc2101` entry with **`x86_numato`**: update
  `filename` to `x86_numato`, the `current` and `system_type` option keys/labels
  to `x86_numato`, and the friendly name/description.
- Add a **`fan_controller_chip`** dropdown (`emc2101` / `emc2301`) bound to
  `platform.fan_controller.chip`.
- Rebind the existing I2C bus-kind / bus-num / address settings to the new
  `platform.fan_controller.*` paths. The address option list includes both
  EMC2101 addresses (`0x4c` / `0x4d`) and the EMC2301 address (`0x2f`).
- No PWM-frequency control is added to the wizard: frequency stays the existing
  global `settings['pwm']['frequency']` (default 25000), unchanged.
- `py_dependencies` keeps `adafruit-circuitpython-emc2101`. The EMC2301 driver
  is local and uses `adafruit_bus_device` — confirm
  `adafruit-circuitpython-busdevice` is listed as a direct dependency in
  `pyproject.toml` (it is currently pulled in transitively by the EMC2101
  library).

## Configuration migration

None. Per the design decision, existing `x86_numato_emc2101` installs
reconfigure through the wizard. The old module name is removed outright.

## Testing

Unit tests, no hardware (I2C / `I2CDevice` mocked):

- **`tests/test_emc2301.py`** (new):
  - `manual_fan_speed` round-trips percent ↔ Fan Setting register `0x30`
    (`0–100 ↔ 0x00–0xFF`); out-of-range writes raise `ValueError`.
  - `__init__` performs a read-modify-write on Configuration register `0x20`
    leaving `DIS_TO = 1` and `WD_EN = 0`, and writes the expected PWM
    output-config and initial Fan Setting (`0`) registers.
  - `pwm_frequency` setter maps a requested Hz onto the base-frequency/divide
    registers (`0x31`), selecting the nearest of the four base frequencies; a
    25000 request lands on the 26 kHz base.
- **`tests/test_x86_*.py`** (updated):
  - Import the renamed module `grillplat.x86_numato`.
  - Use the new `platform.fan_controller` config group.
  - Fan/ramp tests parametrized over both chips, proving the factory selects
    the right driver and that fan/duty/ramp behavior is identical regardless of
    chip.
  - The emc2101 factory uses `EMC2101_LUT` and configures it at init:
    `set_pwm_clock(use_preset=False, use_slow=False)`, `pwm_frequency = 7`,
    `pwm_frequency_divisor = 1` (≈25.7 kHz), in manual (non-LUT) mode.
  - `__init__` applies the frequency to the chip; the in-module fallback is
    `25000` (never `100`); `get_output_status()['frequency']` reports the
    requested frequency so `control.py`'s re-apply comparison settles.
- **`tests/test_x86_manifest.py`** (updated): assert the renamed `x86_numato`
  wizard entry, the new `fan_controller_chip` and `fan_controller_pwm_frequency`
  options, and the rebound `fan_controller.*` settings paths.
