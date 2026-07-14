# MCP2221 Backend: Switch to EasyMCP2221 — Design

**Date:** 2026-07-14
**Status:** Approved design, pending implementation plan

## Goal

Let PiFire drive **multiple MCP2221 adapters at once** (e.g. one carrying
probes, a second carrying the fan controller) by replacing the `mcp2221` bus
kind's backend in `common/i2c_bus.py` with the `EasyMCP2221` library. No
config or wizard changes: `i2c_bus_kind: mcp2221` and its `i2c_bus_num`
(an MCP2221 serial, or blank for "first") keep the same meaning.

## Background: why Blinka's MCP2221 backend can't do this

`adafruit_blinka.microcontroller.mcp2221.mcp2221` ends with:

```python
mcp2221 = MCP2221()
```

a **module-level singleton**, constructed once at import time by opening the
first MCP2221 by USB VID/PID (`0x04D8`/`0x00DD`). Every
`adafruit_blinka...mcp2221.i2c.I2C()` instance wraps this *same* object.

`common/i2c_bus.py`'s current `_construct_mcp2221` works around this for a
single non-default serial by reaching into the singleton and re-pointing its
HID handle:

```python
handle = _mcp_mod.mcp2221._hid
handle.close()
handle.open_path(path)
```

This "works" for one MCP2221 at a time, but it is destructive: opening a
*second* serial closes and re-opens the *same* handle the first bus object
already holds a reference to. The first cached `_LockedI2C` doesn't get a
`RuntimeError` — its next I/O call silently goes to the second adapter's
hardware, because both buses share the one Blinka `I2C()` object underneath.
There is no way to have two live MCP2221 buses with Blinka's backend; the
"Out of scope" section of
[2026-07-12-dual-usb-i2c-bus-design.md](2026-07-12-dual-usb-i2c-bus-design.md)
explicitly called out "selecting among multiple adapters of the same kind"
as unsupported for exactly this reason.

## Why EasyMCP2221 fixes it

`EasyMCP2221.Device(usbserial=..., devnum=...)` constructs **one object per
physical adapter**. Internally it keeps a class-level catalog keyed by USB
path so that *requesting the same physical device twice* returns the same
object (no accidental double-open, no HID handle contention) — but two
*different* serials produce two fully independent `Device` instances, each
with its own `hid.device()` handle and I2C engine. No process-global
singleton, so no re-pointing hack is needed, and two MCP2221 buses can be
live in the same process at the same time.

## API shape (relevant subset)

- `EasyMCP2221.Device(usbserial=None, devnum=0, scan_serial=False)` —
  `usbserial=None` opens the first device by enumeration order (`devnum`,
  default `0`). A non-`None` `usbserial` matches by USB serial, first via
  `hid.enumerate`'s `serial_number` field (same discovery path
  `discover_mcp2221_devices` already uses), then — if `scan_serial=True` —
  by opening every not-yet-claimed MCP2221 and reading its serial from
  flash. The flash-scan step skips any device already in `Device`'s
  catalog (i.e. already opened by this process), so it cannot disturb a
  bus another part of PiFire already has open; it only touches devices
  this process hasn't claimed yet.
- `device.I2C_write(addr, data, kind='regular'|'restart'|'nonstop')` —
  `data` must be **at least 1 byte** (`ValueError` for empty). Raises
  `EasyMCP2221.exceptions.NotAckError` on a NACK, `TimeoutError`,
  `LowSCLError`, `LowSDAError` on bus faults. None of these are `OSError`
  subclasses.
- `device.I2C_read(addr, size=1, kind='regular'|'restart')` -> `bytes`. Same
  exceptions as `I2C_write`.
- No native combined write-then-read; the library's own docs pattern it as
  `I2C_write(addr, data, 'nonstop')` followed by `I2C_read(addr, n,
  'restart')`.

## Architecture

### `_EasyMCP2221Backend` (new, in `common/i2c_bus.py`)

A thin adapter that presents the `scan`/`writeto`/`readfrom_into`/
`writeto_then_readfrom` surface `_LockedI2C` already expects (the same
surface the Blinka ft232h/mcp2221 backends provide), wrapping an
`EasyMCP2221.Device`:

- `writeto(address, buffer, *, start=0, end=None)` — if the requested slice
  is **empty**, `adafruit_bus_device.I2CDevice.__probe_for_device` uses a
  zero-length `writeto` purely as a presence check (`except OSError:` on
  failure). `EasyMCP2221.I2C_write` rejects empty data with `ValueError`, so
  the empty case is special-cased into an `I2C_read(address, 1)` presence
  check instead (mirrors the probe helper's own read-fallback). A non-empty
  slice calls `I2C_write` directly.
- `readfrom_into(address, buffer, *, start=0, end=None)` — `I2C_read`, copied
  into `buffer[start:end]`.
- `writeto_then_readfrom(address, out_buffer, in_buffer, ...)` —
  `I2C_write(address, out, 'nonstop')` then `I2C_read(address, len(in),
  'restart')`, copied into `in_buffer`.
- `scan()` — probes `0x08..0x77` with `I2C_read(addr, 1)`, collecting
  addresses that don't raise.
- All four translate `NotAckError` / `TimeoutError` / `LowSCLError` /
  `LowSDAError` into `OSError` — the exception type `adafruit_bus_device`
  and PiFire's own probe code already handle for "no device at this
  address" / "bus fault". Without this translation every adafruit driver's
  device probe (`I2CDevice.__probe_for_device`, used by the MCP9600,
  EMC2101, VL53Lx, ADS1x15 drivers) would raise instead of cleanly failing.

Wrapped in the existing `_LockedI2C` exactly like the ft232h/mcp2221 Blinka
backends are today — `_LockedI2C` only needs that four-method surface plus
`deinit` (mapped to `EasyMCP2221.Device` having no explicit `close()`;
`deinit` is a no-op, since the object's `__del__` closes its own HID handle
when garbage collected).

### `_construct_mcp2221(selector)` (rewritten)

```python
def _construct_mcp2221(selector):
    from EasyMCP2221 import Device as _MCP2221Device

    if selector:
        device = _MCP2221Device(usbserial=str(selector), scan_serial=True)
    else:
        device = _MCP2221Device()
    return _LockedI2C(_EasyMCP2221Backend(device))
```

No more reaching into a shared singleton's HID handle. Two calls with two
different serials now construct two independent `EasyMCP2221.Device`
objects — both live, both cached under their own `(mcp2221, selector)` key
in `open_i2c_bus`'s bus cache, neither stealing the other's handle.
`scan_serial=True` is passed unconditionally for a non-blank selector: it's
a pure fallback (only reached if plain `hid.enumerate` doesn't turn up the
serial) and, per the catalog-skip behavior above, cannot interfere with a
bus this process already opened.

### `discover_mcp2221_devices()` (rewritten)

Currently imports Blinka's MCP2221 module purely to read its `VID`/`PID`
class attributes for `hid.enumerate`. Those constants
(`0x04D8`/`0x00DD`, the chip's fixed values) are inlined as module constants
in `common/i2c_bus.py` instead, so this function (and the backend
construction above) no longer imports anything from `adafruit_blinka` —
only `hid` and `EasyMCP2221`. Behavior (return shape, best-effort/never-raise
contract) is unchanged.

## Call-site impact

None. Every caller goes through `open_i2c_bus('mcp2221', selector)`, which
still returns a `_LockedI2C`-wrapped, `busio`-compatible bus object. No
changes to `probes/*.py`, `distance/_tof_base.py`,
`grillplat/x86_numato.py`, `grillplat/ft232h_relay.py`, or the wizard.

## Testing

`tests/test_i2c_bus.py` fakes the backend-import boundary the same way it
already fakes Blinka's ft232h/mcp2221 modules: a fake `EasyMCP2221` module
with a `Device` class recording constructor args (`usbserial`) and exposing
fake `I2C_write`/`I2C_read`, injected via `sys.modules`.

- Blank selector -> `Device()` with no `usbserial`.
- Non-blank selector -> `Device(usbserial=selector, scan_serial=True)`.
- **Two different selectors construct two distinct `Device` instances that
  stay independently live** (i.e. constructing the second does not affect
  the first's recorded calls) — the regression test for the bug this change
  fixes; the old Blinka-backed test suite couldn't express this because the
  fake was a single shared singleton by construction.
- `_EasyMCP2221Backend`: zero-length `writeto` performs a presence read, not
  an `I2C_write` call; non-empty `writeto` calls `I2C_write`;
  `readfrom_into` calls `I2C_read` and fills the buffer;
  `writeto_then_readfrom` calls `I2C_write(..., 'nonstop')` then
  `I2C_read(..., 'restart')`; `NotAckError`/`TimeoutError`/`LowSCLError`/
  `LowSDAError` from the fake all surface as `OSError`.
- `discover_mcp2221_devices`: unchanged behavior (empty list when `hid` or
  `EasyMCP2221` aren't importable or nothing is enumerated; lists serial +
  path otherwise) against the new import boundary.

## Backward compatibility

- No config key or wizard changes; `mcp2221` still takes a blank/serial
  `i2c_bus_num`.
- `open_i2c_bus`'s public signature, caching, and validation
  (`validate_bus_kinds`, `assert_clean_blinka_env`) are unchanged.
- `EasyMCP2221` is a new direct dependency (`pyproject.toml`); it depends
  only on `hidapi`, already a PiFire dependency. `adafruit-blinka` stays a
  dependency (still used by `basic`, `extended`'s underlying `busio`
  interface expectations, and `ft232h`).

## Out of scope

- Changing the `ft232h` backend (unaffected; already supports multiple
  adapters via distinct `pyftdi` controllers).
- GPIO over MCP2221 (PiFire's `mcp2221` kind is I2C-only, same as before).
- Changing `discover_mcp2221_devices` (the wizard's Discover button) to use
  the flash-scan fallback — it stays on plain `hid.enumerate`, matching
  today's behavior; `scan_serial` only affects resolving an already-entered
  serial at bus-construction time.
