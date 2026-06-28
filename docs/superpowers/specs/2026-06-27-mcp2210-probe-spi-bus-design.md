# MCP2210 as a selectable SPI bus for probes — Design

**Date:** 2026-06-27
**Status:** Approved (design); implementation pending
**Author:** PiFire

## Goal

Let PiFire's SPI temperature probes use the MCP2210 USB-to-SPI bridge (driver
in `mcp2210/`) as their SPI bus, selected per-probe in the setup wizard —
mirroring the existing "extended i2c bus" mechanism. This makes SPI probes work
on hosts with no native SPI (e.g. the x86 platforms), and the per-probe
selection coexists with native `board.SPI()` probes.

The first consumer is the existing `max31865_adafruit` RTD probe. The
`spi_bus_kind` handling is factored into a **shared helper** so adding further
SPI probes (MAX31855, MCP3008, …) is a one-line call plus three manifest fields.

## Background and the pattern being mirrored

The i2c extended-bus work (commits `2531981`, `ce8ec41`) gives each i2c probe a
`i2c_bus_kind` (`basic`/`extended`) + `i2c_bus_num` config, resolved by
`resolve_i2c_bus()` in `probes/base.py`, with the choice surfaced in
`wizard/wizard_manifest.json`. SPI mirrors this with two deliberate
differences:

1. **Naming.** The i2c value `extended` reflects the Linux kernel `i2cdev` /
   `adafruit-extended-bus` (`ExtendedI2C` opens `/dev/i2c-N`). The MCP2210 is a
   pure-userspace USB-HID driver with **no kernel device**, so "extended" would
   be misleading. The SPI value is `mcp2210` (vs. `basic` for native
   `board.SPI()`), leaving room for other named SPI bridges later.

2. **Shared HID handle.** A `/dev/i2c-N` node can be opened by many probes
   independently, so i2c needs no sharing. A USB MCP2210 HID handle can be
   **opened only once** — so multiple SPI probes on one bridge must share a
   single `MCP2210` instance. The resolver therefore **caches** the bridge.

Chip-select also differs: native SPI probes drive CS from a board GPIO; on the
MCP2210 path CS comes from the bridge's own GPIO (`GP0`–`GP8`) via the driver's
`digital_inout()`, which is essential on x86 hosts that have no board GPIO.

## Architecture

Two new helpers in `probes/base.py`, an SPI-bus helper section alongside the
existing I2C-bus helpers. All SPI probes consume them; no probe touches the
`mcp2210` package or `board` SPI directly through its own branch.

### `resolve_mcp2210(serial=None)` — cached bridge factory

```python
_MCP2210_CACHE = {}   # module-level: serial key -> MCP2210 instance

def resolve_mcp2210(serial=None):
    '''
    Open (and cache) a single MCP2210 per serial and return the shared
    instance. The MCP2210 USB-HID handle can be opened only once, so every
    probe on the same bridge must share one instance; the cache guarantees
    that. serial=None/'' selects the first MCP2210 by VID/PID (0x04D8/0x00DE)
    and is cached under a fixed key.
    '''
```

- Key normalisation: `None` and `""` map to one canonical key (the
  first-device bridge); a non-empty serial is its own key.
- On a cache miss it constructs `MCP2210(serial=...)` (or `MCP2210()` for the
  default), stores it, and returns it. Subsequent calls with the same key
  return the same instance.
- Import of `mcp2210` is lazy (inside the function), matching how the i2c
  helpers avoid importing hardware libs at module load.

### `resolve_spi_bus(config, default_cs)` — (spi, chip_select) for a probe

```python
def resolve_spi_bus(config, default_cs):
    '''
    Build the (spi, chip_select) pair for an SPI probe from its config dict.
      spi_bus_kind 'basic'  -> board.SPI() + digitalio.DigitalInOut(board pin)
      spi_bus_kind 'mcp2210'-> shared MCP2210.spi + mcp.digital_inout(GP index)
    Reads standardized keys: spi_bus_kind (default 'basic'), cs (default
    `default_cs`), mcp2210_serial (default ''). Raises ValueError on an
    unknown spi_bus_kind. Returns objects ready to hand to an
    adafruit_bus_device / SPIDevice-based sensor constructor.
    '''
```

This helper owns:
- the `spi_bus_kind` branch (the single place it lives),
- the board-pin `LOOKUP_TABLE` (moved here from `max31865_adafruit.py` so every
  SPI probe reuses one table; imports of `board`/`digitalio` are lazy, inside
  the `basic` branch),
- CS resolution: `basic` → `digitalio.DigitalInOut(LOOKUP_TABLE[cs])`;
  `mcp2210` → `mcp.digital_inout(_gp_index(cs))`, where `_gp_index` parses
  `0`–`8`, `"GP3"`, or `"GPIO3"` to an int 0–8.

**CS value/label convention (board-pin path).** The wizard renders a `list`
config field as `<option value="{list_values[i]}">{list_labels[i]}</option>`,
so the value **stored** in `config['cs']` is the `list_values` entry and the
`list_labels` entry is only the on-screen text. The existing `max31865_adafruit`
`cs` field uses `list_values` = `GPIO2`…`GPIO27` (BCM names, the stored values)
and `list_labels` = `D2`…`D27` (the Adafruit board names shown to the user) —
`GPIO6` *is* `D6`. The relocated `LOOKUP_TABLE` is therefore keyed by the stored
`GPIOn` names mapping to the `board.Dn` pin objects (e.g. `'GPIO6': board.D6`).
For robustness it also accepts the `Dn` form as a key (so a legacy stored value
or the in-code default still resolves), and `default_cs` stays `'D6'`.

### Probe modules become thin

`max31865_adafruit.py` `_init_device()` (and every future SPI probe) reduces to:

```python
from probes.base import ProbeInterface, resolve_spi_bus
...
spi, cs = resolve_spi_bus(self.device_info['config'], default_cs='D6')
self.sensor = adafruit_max31865.MAX31865(
    spi, cs, rtd_nominal=rtd_nominal, ref_resistor=ref_resistor, wires=wires)
```

The probe no longer imports `board`/`digitalio` or owns `LOOKUP_TABLE`; it keeps
its sensor-specific config parsing (`rtd_nominal`, `ref_resistor`, `wires`).

## Config shape

A `max31865_adafruit` device `config` after the wizard (MCP2210 case):

```json
{
  "cs": "3",
  "spi_bus_kind": "mcp2210",
  "mcp2210_serial": "",
  "rtd_nominal": "1000",
  "ref_resistor": "4300",
  "wires": "2"
}
```

Native case is unchanged from today (`"spi_bus_kind"` absent or `"basic"`,
`"cs": "D6"`).

## Wizard manifest changes (`max31865_adafruit` entry)

- Add `spi_bus_kind` (list: `basic`/`mcp2210`; labels "Basic (native SPI)" /
  "MCP2210 (USB-to-SPI bridge)"; default `basic`).
- Add `mcp2210_serial` (text; default `""`; description: leave blank for the
  first/only MCP2210, or enter a USB serial to pick a specific bridge).
- Extend the existing `cs` field's `list_values`/`list_labels` to include the
  MCP2210 GPIO options: `list_values` `"0"`–`"8"` (the stored values that
  `_gp_index` parses) with `list_labels` "MCP2210 GP0"–"GP8", appended after the
  existing board-pin entries (whose `list_values` stay `GPIO2`…`GPIO27`). The
  field description notes board pins apply to Basic and GP0–GP8 to MCP2210.
- Add `mcp2210` and `hid` to `py_dependencies`.

No changes are needed in the wizard/probeconfig blueprints: they already parse
arbitrary `probes_devspec_<label>` form fields into `config`, and render the
manifest config list generically.

## Backward compatibility

- `spi_bus_kind` defaults to `basic` via `config.get('spi_bus_kind', 'basic')`,
  so existing `max31865_adafruit` setups (which have no such key) behave exactly
  as before — `board.SPI()` + board-pin CS.
- No `settings.json` migration. The board-pin `basic` path preserves the
  current `LOOKUP_TABLE` and `cs` default (`D6`) byte-for-byte.

## Error handling

- Unknown `spi_bus_kind` → `ValueError` with the offending value (fail clearly,
  consistent with `resolve_i2c_bus`'s clear-error philosophy).
- MCP2210 open failure surfaces the driver's own exception (e.g. device not
  found / busy) — not swallowed.
- An out-of-range or unparseable MCP2210 CS (`_gp_index` not 0–8) → `ValueError`.

## Testing strategy

All tests run without hardware, consistent with the existing `mcp2210` suite
(`FakeHID`) and the repo's pytest setup (`tests/conftest.py` puts the repo root
on `sys.path`).

- **`resolve_mcp2210` caching:** monkeypatch the `mcp2210.MCP2210` constructor
  (or inject via the existing `hid_device` seam with `FakeHID`) and assert that
  two calls with the same serial return the *same* instance, and different
  serials return different instances. Reset the module cache between tests.
- **`resolve_spi_bus` branch:** with `spi_bus_kind='mcp2210'`, assert it returns
  `mcp.spi` and a CS from `mcp.digital_inout(n)` for the parsed GP index, using
  a fake/cached MCP2210; with `'basic'`, monkeypatch `board`/`digitalio` so the
  test asserts `board.SPI()` and a board-pin `DigitalInOut` are used; unknown
  kind raises `ValueError`; `_gp_index` parses `3`/`"GP3"`/`"GPIO3"` → 3 and
  rejects out-of-range.
- **`max31865_adafruit` wiring:** monkeypatch `resolve_spi_bus` and
  `adafruit_max31865.MAX31865` to assert `_init_device` calls the helper with
  `default_cs='D6'` and the parsed sensor params, without importing real
  hardware libs.
- **Manifest sanity:** assert the `max31865_adafruit` manifest entry contains
  the `spi_bus_kind` and `mcp2210_serial` config items and that `mcp2210` is in
  its `py_dependencies` (cheap regression like the existing manifest tests).

## Files changed

| File | Change |
|------|--------|
| `probes/base.py` | Add SPI-bus helpers: `resolve_mcp2210`, `resolve_spi_bus`, `_gp_index`, module cache, and the relocated `LOOKUP_TABLE`. |
| `probes/max31865_adafruit.py` | Drop `board`/`digitalio`/`LOOKUP_TABLE`; call `resolve_spi_bus(config, default_cs='D6')`; keep sensor-specific parsing. |
| `wizard/wizard_manifest.json` | Add `spi_bus_kind` + `mcp2210_serial` config fields and GP0–GP8 CS options to `max31865_adafruit`; add `mcp2210`/`hid` deps. |
| `tests/test_mcp2210_probe_bus.py` (new) | Helper + probe-wiring + manifest tests above. |

## Out of scope / future

- **More SPI probes** (MAX31855 thermocouple, MCP3008 ADC): each just calls
  `resolve_spi_bus` and adds the three manifest fields — explicitly enabled by
  this design, implemented separately.
- **No platform-config changes.** Bridge selection is per-probe (mirrors i2c);
  the cached resolver handles sharing. (A platform-level default serial was
  considered and declined.)
- **The raw-spidev `max31865.py`** stays as-is (it uses `spidev` directly, not
  busio, so it can't take a busio bus without a larger rewrite).
