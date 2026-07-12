# FT232H IO-Triggered Relay Platform — Design

**Date:** 2026-07-12
**Status:** Approved for planning
**Author:** Daniel Berlin (with Claude)

## Summary

Add a new host-agnostic PiFire grill platform, `grillplat/ft232h_relay.py`, that
drives an IO-triggered relay board (power / auger / igniter / fan) from **FT232H
GPIO pins** over USB via Adafruit Blinka `digitalio`. It is the
`raspberry_pi_all` output model with the GPIO backend swapped from Pi-native
`gpiozero` to the FT232H, offered as an alternative to a directly-wired relay
board on any host (Pi, x86, etc.).

The fan output is **selectable** between two modes:

- **Relay on/off** — the fan is a plain relay like the other outputs. Smoke-Plus
  fan ramping degrades to simply turning the fan relay on.
- **EMC PWM** — an EMC2101 or EMC2301 fan controller on the FT232H's own I2C bus
  provides variable fan speed; the fan relay gates fan power. This reuses the
  fan logic already proven in `x86_numato`.

Scope is **outputs only**: no selector/shutdown input.

## Background

PiFire platforms are single modules under `grillplat/` exposing a
`GrillPlatform(config)` class with a fixed method surface. The controller loads
the configured one dynamically:

- `controller/runtime/devices.py:132` — `importlib.import_module(f'grillplat.{grill_platform}')`
  where `grill_platform = settings['modules']['grillplat']`, then constructs
  `GrillPlatModule.GrillPlatform(settings['platform'])` (with `frequency`
  injected from `settings['pwm']['frequency']`).
- `wizard/wizard_manifest.json` → `modules.grillplatform.<key>` registers each
  platform for the setup wizard (friendly name, `py_dependencies`, and per-field
  config options mapped to settings paths).

Two existing platforms bracket this design:

- `grillplat/raspberry_pi_all.py` — outputs via `gpiozero.OutputDevice`,
  `active_high` derived from `triggerlevel`, optional PWM/DC fan.
- `grillplat/x86_numato.py` — outputs routed to an off-SoC controller over USB
  (Numato serial relay), fan speed via a selectable EMC2101/EMC2301 on a
  basic/extended I2C bus. This is the closest structural template.

Relevant existing facts:

- `settings['platform']['triggerlevel']` defaults to `'LOW'` — matching cheap
  active-low relay boards.
- The control loop gates **all** PWM behavior on `settings['platform']['dc_fan']`
  (`controller/runtime/modes/base.py:197`, `controller/runtime/logic/fan.py:44`,
  `controller/runtime/modes/hold.py:169`). `set_duty_cycle` / `pwm_fan_ramp` are
  only called when `dc_fan` is True.
- Blinka is already an indirect dependency (probes import it lazily;
  `x86_numato` imports `board`/`busio`). The FT232H backend additionally needs
  `pyftdi` and `BLINKA_FT232H=1` set **before** `import board`.
- `adafruit-circuitpython-emc2101` is already a dependency; a local `EMC2301`
  driver exists at `grillplat/emc2301.py`.

## Goals / Non-Goals

**Goals**

- Drive power/auger/igniter/fan relays from FT232H GPIO pins, honoring
  `triggerlevel` (active-low default).
- Selectable fan mode: relay on/off, or EMC2101/EMC2301 PWM on the FT232H I2C bus.
- Wizard-configurable: fan mode, EMC address, per-output pin mapping, optional
  FT232H device URL.
- Importable and fully unit-testable without FT232H hardware or Blinka's
  hardware backend.

**Non-Goals**

- Selector / shutdown digital inputs (`get_input_status()` always returns False).
- Multi-FT232H orchestration (single device; a pyftdi URL selects which one).
- Distance-sensor I2C bus configuration (that belongs to the `dist` module).
- Software-PWM fan speed (FT232H USB latency makes it unreliable).

## Architecture

### New module: `grillplat/ft232h_relay.py`

Implements the full `GrillPlatform` surface, matching the other platforms:
`auger_on/off`, `igniter_on/off`, `power_on/off`, `fan_on/off`, `fan_toggle`,
`set_duty_cycle`, `pwm_fan_ramp`, `set_pwm_frequency`, `get_input_status`,
`get_output_status`, `cleanup`, plus the system-info commands
(`supported_commands`, `check_throttled`, `check_cpu_temp`, `check_wifi_quality`,
`check_alive`, `scan_bluetooth`, `os_info`, `network_info`, `hardware_info`)
carried over from `x86_numato` (the generic-host, non-`vcgencmd` variants).

#### Import safety and testability

FT232H requires `BLINKA_FT232H=1` set before `import board`, and that import
opens the USB device. To keep `import grillplat.ft232h_relay` hardware-free (so
the controller's import step and the unit tests never touch USB), the Blinka
board/digitalio import is isolated behind a module-level, patchable function:

```python
def _load_ft232h():
    """Enable Blinka's FT232H backend and import board + digitalio.
    Isolated so importing this module never opens USB, and so tests can
    patch it to inject fakes."""
    os.environ['BLINKA_FT232H'] = _ft232h_url  # '1' by default, or a pyftdi URL
    import board
    import digitalio
    return board, digitalio
```

`__init__` calls `_load_ft232h()`, resolves each output pin by name via
`getattr(board, name)`, and creates a `digitalio.DigitalInOut` per output. EMC
drivers (`EMC2101_LUT`, `EMC2301`) and `busio` are imported at module top —
those imports are hardware-free; only their construction (in EMC mode) touches
hardware. This matches the `x86_numato` test pattern, where module-level names
are `mock.patch.object`-ed.

#### Relay abstraction

`digitalio.DigitalInOut` has no `active_high` parameter, so trigger polarity is
applied explicitly. A small internal helper owns one output pin:

```python
class _Relay:
    def __init__(self, dio, active_high):
        self._dio = dio                 # DigitalInOut, direction = OUTPUT
        self._active_high = active_high
        self._state = False
        self.off()                      # start de-asserted

    def on(self):
        self._dio.value = self._active_high
        self._state = True

    def off(self):
        self._dio.value = not self._active_high
        self._state = False

    @property
    def is_active(self):
        return self._state
```

`active_high = (config.get('triggerlevel', 'LOW') == 'HIGH')`. Output on/off
methods delegate to the mapped `_Relay`; cached `_state` backs
`get_output_status()` without reading hardware.

#### Fan modes

`self.pwm_fan = chip in ('emc2101', 'emc2301')` where
`chip = settings['platform']['fan_controller']['chip']` (new option `'none'`
means relay-only).

- **Relay-only (`chip == 'none'`):** the fan is another `_Relay`. `fan_on()`
  asserts it; `fan_off()` de-asserts it; `set_duty_cycle()` and
  `set_pwm_frequency()` are no-ops; `pwm_fan_ramp()` just asserts the fan relay.
  No I2C bus or EMC object is opened.
- **EMC PWM (`chip in ('emc2101','emc2301')`):** identical fan behavior to
  `x86_numato` — the fan relay gates power and the EMC sets speed. I2C is the
  FT232H's own bus: `busio.I2C(board.SCL, board.SDA)` (no basic/extended
  selection — there is only the one FT232H bus). `set_duty_cycle`, `pwm_fan_ramp`
  / `_ramp_device`, and `set_pwm_frequency` are ported from `x86_numato`,
  including the EMC2101 vs EMC2301 frequency handling and the daemon-thread ramp.

`get_output_status()` always reports `auger`/`igniter`/`power`/`fan` booleans
from cached state; in EMC mode it additionally reports `pwm`
(`self._fan_speed_percent`) and `frequency`, matching the DC-fan status shape the
UI expects.

`cleanup()`: stop any ramp, set EMC speed to 0 (EMC mode), de-assert all relays,
and `close()` the `DigitalInOut` objects.

### Config schema — `settings['platform']`

- **`outputs`** — pin *names* per output, e.g.
  `{'power': 'C0', 'igniter': 'C1', 'auger': 'C2', 'fan': 'C3'}`. Defaults live
  on the C-bank so they never collide with the I2C pins (D0 = SCL, D1/D2 = SDA)
  used in EMC mode. The platform resolves each name with `getattr(board, name)`
  and raises a clear error if a name is unknown.
- **`fan_controller`** — reuse the existing block; `chip` gains a `'none'` option
  (relay-only). `address` (hex string or int) is used only in EMC mode, default
  `0x4c` for EMC2101 / `0x2f` for EMC2301.
- **`ft232h`** (new, minimal) — `{'url': '1'}`. `'1'` selects the first FT232H;
  a pyftdi URL (e.g. `ftdi://ftdi:232h:SERIAL/1`) selects a specific device. The
  value is assigned to `BLINKA_FT232H` in `_load_ft232h()`.
- **`triggerlevel`** — reused, default `'LOW'`.
- **`dc_fan`** — set by the wizard: `True` for EMC modes, `False` for relay-only,
  so the control loop and UI enable fan-speed control only when a real PWM fan
  exists. This is the single lever that couples fan-mode selection to controller
  behavior (see Background).

`settings['platform']['outputs']` in the shared defaults still holds integer Pi
GPIO numbers; the wizard writes FT232H pin-name strings when this platform is
selected. Each platform interprets its own `outputs` values, so mixed types
across platforms are fine.

### Wizard manifest entry — `wizard/wizard_manifest.json`

New `modules.grillplatform.ft232h_relay`, modeled on the `x86_numato` entry:

- `friendly_name`: "FT232H IO-Triggered Relay"
- `filename`: `ft232h_relay`
- `description`: notes that outputs are driven by FT232H GPIO pins on an
  IO-triggered relay board, fan is relay on/off or EMC2101/EMC2301 PWM, and that
  the FT232H needs USB access (libusb; on Linux a udev rule so it is reachable
  without root, and the `ftdi_sio` kernel driver must not claim the device).
- `default`: false; `reboot_required`: false; `image`: reuse `custom.png`.
- `py_dependencies`: `["pyftdi", "adafruit-circuitpython-emc2101"]`.
- `settings_dependencies`:
  - `current`, `system_type` — hidden, `ft232h_relay`.
  - `fan_mode` — options `none` / `emc2101` / `emc2301` → `platform.fan_controller.chip`.
  - `fan_controller_address` → `platform.fan_controller.address`.
  - `ft232h_url` → `platform.ft232h.url` (default `1`; a couple of common URL
    presets plus the default).
  - `output_power` / `output_igniter` / `output_auger` / `output_fan` — pin
    dropdowns with options **C0–C7 and D4–D7** (D0–D3 omitted to keep the I2C +
    MPSSE pins free) → `platform.outputs.<name>`.
- The wizard sets `platform.dc_fan` from the chosen `fan_mode` (True for EMC
  modes, False for `none`). If the manifest cannot express this coupling
  declaratively, it is applied in the wizard's platform-selection handler
  (`wizard.py`, where `system_type` is applied); the implementation plan will
  confirm the exact hook.

### Dependencies

- Add **`pyftdi`** to `pyproject.toml` (`>=0.55.0`) and
  `auto-install/requirements.txt` (a pinned compatible version). Blinka and the
  EMC libraries are already present.

## Testing

New `tests/test_ft232h_*.py`, mirroring the `x86_numato` suite's structure
(`mock.patch.object` on module-level names). A shared fixture patches
`_load_ft232h` to return a fake `board` (attributes `C0`…`D7`) and a fake
`digitalio` whose `DigitalInOut` yields a recording stub, plus `EMC2101_LUT`,
`EMC2301`, and `busio`.

Cases:

- **Relay-only init** — no I2C bus and no EMC object opened; four relays created;
  all start de-asserted.
- **EMC init** — I2C opened and the correct EMC chip constructed at the
  configured address; frequency applied.
- **Output mapping** — each `*_on`/`*_off` sets the mapped pin's `.value`.
- **Trigger polarity** — active-low (`triggerlevel='LOW'`) inverts `.value`
  vs active-high.
- **Fan relay-only** — `fan_on`/`fan_off` toggle the fan relay;
  `set_duty_cycle`/`set_pwm_frequency` are no-ops; `pwm_fan_ramp` asserts the
  fan relay without touching an EMC.
- **Fan EMC** — `fan_on(percent)` asserts the relay and sets EMC speed;
  `fan_off` zeroes speed and de-asserts; ramp thread drives `set_duty_cycle`.
- **`get_output_status`** — boolean outputs always present; `pwm`/`frequency`
  present only in EMC mode.
- **`get_input_status()`** — always False.
- **`cleanup()`** — stops ramp, zeroes EMC (EMC mode), de-asserts and closes all
  pins.

## Files

- **New:** `grillplat/ft232h_relay.py`
- **New:** `tests/test_ft232h_outputs.py`, `tests/test_ft232h_fan.py`,
  `tests/test_ft232h_init.py` (final split decided during planning)
- **Modified:** `wizard/wizard_manifest.json` (new platform entry)
- **Modified:** `common/common.py` (add `ft232h` block to `settings['platform']`
  defaults; add `'none'` as a valid `fan_controller.chip`)
- **Modified:** `pyproject.toml`, `auto-install/requirements.txt` (add `pyftdi`)
- **Possibly modified:** `wizard.py` (apply `dc_fan` from fan mode on platform
  selection, if not expressible in the manifest)

## Open questions for planning

- Exact hook for the `dc_fan`-from-`fan_mode` coupling (manifest vs `wizard.py`
  handler).
- Whether `set_pwm_frequency` in relay-only mode should still record
  `self.frequency` for status parity (leaning yes, as a harmless no-op that keeps
  `get_output_status` consistent).
