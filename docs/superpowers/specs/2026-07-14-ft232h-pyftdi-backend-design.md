# FT232H via pyftdi (drop Blinka); extract USB-HID backends — Design

**Date:** 2026-07-14
**Status:** Proposed design, pending implementation plan

## Goal

Drive the FT232H's I2C bus **and** its relay GPIO pins through **pyftdi directly**,
removing Adafruit Blinka's `board`/`digitalio` from the FT232H path entirely. As
part of this, extract the two USB-HID adapter backends out of the increasingly
large `common/i2c_bus.py` into their own focused modules: `common/ft232h.py` and
`common/mcp2221.py`.

## Problem (root cause)

`grillplat/ft232h_relay.py` drives its relays through Blinka:

```python
os.environ['BLINKA_FT232H'] = url
import board
import digitalio
pin = getattr(board, 'C0')          # AttributeError on a Pi host
```

Blinka's `board` module is a **process-global singleton**: the first `import
board` anywhere in the process selects exactly one backend (chosen by the
`BLINKA_*` env vars present *at that moment*) and caches it in `sys.modules`.
Every later `import board` — including `_load_ft232h`'s — gets that same cached
module back, regardless of what `BLINKA_FT232H` is set to afterward.

On the failing device, `board` had already been imported as the **Raspberry Pi**
board before `_load_ft232h` ran, so `getattr(board, 'C0')` failed with
`module 'board' has no attribute 'C0'. Did you mean: 'CE0'?` — `CE0` being a Pi
SPI pin. The FT232H pin names (`C0`–`C7`) are correct; `board` was simply the
wrong board. The `BLINKA_FT232H` env-var save/restore dance in
`common/i2c_bus.py::_construct_ft232h` is an attempt to work around this
singleton, and it does not hold across import ordering.

The project already solved this exact singleton problem for the **MCP2221** by
using `EasyMCP2221.Device` instead of Blinka's MCP2221 backend (see
`docs/superpowers/specs/2026-07-14-mcp2221-easymcp2221-backend-design.md`). This
design gives the FT232H the same treatment using **pyftdi**, which is already a
direct dependency (`pyproject.toml`) and is what Blinka's mpsse backend wraps
internally anyway.

## Non-goals

- **Blinka is not removed as a dependency.** `basic` I2C
  (`busio.I2C(board.SCL, board.SDA)`), `basic`/native SPI probes
  (`probes/base.py`), and native GPIO on a directly-wired Pi still use it. Only
  the FT232H path stops using `board`.
- **No wizard/UI change and no config-format change.** Relay pin names stay
  `C0`–`C7` / `D4`–`D7`; existing saved configs keep working. Names translate to
  pyftdi bit positions internally.
- No functional change to the MCP2221 path — it is only relocated to its own
  module, behavior identical.

## Background: how pyftdi exposes one FT232H as I2C + GPIO

`pyftdi.i2c.I2cController` opens the FT232H once and hands out both interfaces
off that single MPSSE engine:

- `configure(url, frequency=...)` — opens the device; reserves `AD0` (SCL),
  `AD1`/`AD2` (SDA out/in) for I2C.
- `get_port(address)` → `I2cPort` with `read`/`write`/`exchange` — used by the
  EMC fan controller.
- `get_gpio()` → `I2cGpioPort` over the **free** pins: `AD3`–`AD7` (bits 3–7) and
  `AC0`–`AC7` (bits 8–15) — used by the relays.
- `poll(address)` → device-presence probe, for `scan()`.
- `terminate()` → close.

FT232H is a 16-bit "wide port": `AD`_n_ = bit _n_, `AC`_n_ = bit _(8+n)_. The
relay defaults `C0`–`C3` map to bits 8–11. One controller, one internal lock —
this is exactly the single-MPSSE coordination that `Pin.mpsse_gpio` was doing in
Blinka, but explicit and self-contained.

**Usable relay pins:** `C0`–`C7` (bits 8–15) and `D4`–`D7` (bits 4–7). This is
exactly the set Blinka's `ftdi_ft232h` board exposed, so existing configs are
unaffected. `D0`/`D1`/`D2` are the I2C pins (reserved); `D3` is electrically free
in I2C mode but was never exposed by Blinka, so we keep it out of the map to
match historical behavior.

## Architecture

Three files change plus tests.

### New: `common/mcp2221.py` (pure relocation, no behavior change)

Move verbatim out of `common/i2c_bus.py`:

- `_MCP2221_VID` / `_MCP2221_PID`
- `discover_mcp2221_devices()`
- `_EasyMCP2221Backend` (the scan/writeto/readfrom_into/writeto_then_readfrom
  adapter that translates EasyMCP2221 errors to `OSError`)
- `_open_mcp2221_device(selector)`
- the per-Device dedup registry `_mcp2221_bus_by_device` and `_construct_mcp2221`

Public surface used by the factory:

- `construct_i2c_bus(selector) -> _LockedI2C`
- `discover_mcp2221_devices() -> list[dict]`

`_LockedI2C` is imported from `common.i2c_bus`. `construct_i2c_bus` continues to
be called while the factory holds its construction lock (see "Locking"), so the
dedup registry stays atomic with the open exactly as today.

### New: `common/ft232h.py` (the substantive change)

- `discover_ft232h_devices()` — moved from `i2c_bus.py` (pyftdi `Ftdi.list_devices`).
- **Controller cache** `{canonical_url: I2cController}` keyed by a canonical url
  (`''` and `'1'` both mean "first FT232H" → one entry; an explicit pyftdi url is
  its own entry). `_get_controller(selector)` configures once and caches, so the
  fan's I2C bus and the relays' GPIO always share **one** controller no matter
  which is opened first. Configured at 100 kHz (matching Blinka's mpsse default).
- `_PyFtdiI2CBackend(controller)` — the busio-compatible surface `_LockedI2C`
  expects, a direct analogue of `_EasyMCP2221Backend`:
  - `scan()` → iterate `0x08..0x77`, `controller.poll(addr)`
  - `writeto` / `readfrom_into` / `writeto_then_readfrom` → `port.write` /
    `port.read` / `port.exchange`, translating pyftdi `I2cNackError`/`I2cIOError`
    to `OSError` (what adafruit_bus_device and PiFire probe code treat as
    "no device"/"bus fault").
- `construct_i2c_bus(selector) -> _LockedI2C(_PyFtdiI2CBackend(controller))`.
- `Ft232hGpio(controller)` — relay-facing GPIO helper:
  - name→bit map: `C0..C7 -> 1<<(8+n)`, `D4..D7 -> 1<<n`.
  - shadow `direction` and `output` integers + a `threading.Lock` so a
    single-relay change is an atomic read-modify-write of the output word (pyftdi
    `write()` sets the whole word; without a shadow, toggling one relay would
    clobber the others).
  - `setup_output(pin_name)` — validate the name, reject I2C-reserved bits
    (`AD0/AD1/AD2`, bits 0–2) and unknown names with a clear `ValueError`, add to
    the direction mask, push `set_direction`.
  - `set(pin_name, high)` — set/clear the bit under the lock, `port.write`.
- `open_gpio(selector) -> Ft232hGpio`, cached per controller so all relays on one
  FT232H share a single `Ft232hGpio` (and its lock).

### `common/i2c_bus.py` (slims down to the factory)

- Keeps: `_LockedI2C` (shared base, now imported by both backend modules),
  `open_i2c_bus`, `_construct_bus` (dispatches `ft232h`/`mcp2221` to the new
  modules via lazy import), `basic`/`extended` construction, `_canonical_selector`,
  `validate_bus_kinds`, `assert_clean_blinka_env`, `resolve_i2c_bus`,
  `find_i2c_bus*`, `discover_extended_i2c_buses`, `configured_bus_kinds`.
- Deletes: `_construct_ft232h` (and its `BLINKA_FT232H` env dance),
  `_construct_mcp2221`, `_EasyMCP2221Backend`, `_open_mcp2221_device`,
  `_mcp2221_bus_by_device`, `_MCP2221_VID/PID`, both `discover_*` bodies.
- Re-exports `discover_ft232h_devices` and `discover_mcp2221_devices` (from the
  new modules) so `from common.i2c_bus import discover_*` keeps working for
  `blueprints/wizard/routes.py` and existing tests.

**No import cycle:** `i2c_bus` imports the backend modules only *lazily* inside
`_construct_bus` and the re-export lines; the backend modules import `_LockedI2C`
from `i2c_bus` at module top. Loading `i2c_bus` does not pull in the backends;
loading a backend pulls in a fully-formed `i2c_bus`.

### `grillplat/ft232h_relay.py` (drop Blinka)

- Delete `_load_ft232h`; remove `board`/`digitalio`.
- `__init__` keeps opening the fan I2C bus via `open_i2c_bus('ft232h', url)`
  (unchanged — still returns a `_LockedI2C`, consumed by the EMC driver). Then it
  gets `gpio = ft232h.open_gpio(url)` (same shared controller), calls
  `gpio.setup_output(pin_name)` per output, and builds `_Relay(gpio, pin_name,
  active_high)`.
- `_Relay.on()/off()` call `gpio.set(pin_name, high)` applying trigger polarity;
  `is_active` unchanged; `close()` becomes a no-op (the shared controller lives
  for the process lifetime, as the MPSSE controller did before).
- Fan-controller setup and everything below it is unchanged.

## Locking

`pyftdi.I2cController` serializes I2C transactions and GPIO writes on its own
internal lock, so I2C and GPIO never interleave on the wire. The extra
`Ft232hGpio` lock guards only the shadow-register read-modify-write (so
concurrent relay threads don't lose updates). The MCP2221 dedup registry keeps
its current contract: `construct_i2c_bus` runs under the factory's construction
lock in `open_i2c_bus`.

## Error handling

- Unknown or I2C-reserved relay pin name → `ValueError` in `setup_output`,
  surfaced by `build_devices`' existing try/except as the standard "configuring
  … failed, prototype loaded instead" path (same as today).
- pyftdi I2C NACK/IO errors → `OSError`, matching the MCP2221 backend and what
  the adafruit/probe layers expect.
- `discover_*` stay best-effort: return `[]` if the library is missing or
  enumeration fails; never raise.

## Testing

- `tests/ft232h_helpers.py`: today patches `_load_ft232h` to inject a fake
  board/digitalio. Repoint to the new seam — patch `ft232h.open_gpio` to return a
  fake `Ft232hGpio` (records `setup_output`/`set` calls) and `open_i2c_bus` for
  the `ft232h` kind to a fake bus.
- `tests/test_i2c_bus.py`: `_EasyMCP2221Backend` references → `common.mcp2221`;
  the mpsse-module patching for ft232h → a fake pyftdi `I2cController`;
  `discover_*` tests keep working via the `i2c_bus` re-exports (or move to the new
  modules).
- New unit tests for `common/ft232h.py`: name→bit mapping, reserved-pin
  rejection, shadow read-modify-write (toggling one relay leaves the others),
  `scan()` over `poll`, and I2C-error → `OSError` translation.
- The existing `test_ft232h_outputs / _fan / _system / _wizard / _settings`
  suites must pass against the new seam.
- Full run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/`.
