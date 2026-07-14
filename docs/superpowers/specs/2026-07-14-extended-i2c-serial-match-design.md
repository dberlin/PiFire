# Extended I2C Bus Matching by USB Serial — Design

**Date:** 2026-07-14
**Status:** Approved design, pending implementation plan

## Goal

Let the `extended` I2C bus kind select an adapter by its USB iSerial, not just by
adapter name or a hardcoded `/dev/i2c-N`. This is needed to run two identical
USB-to-I2C bridges (e.g. two MCP2221A units bound to the kernel `hid-mcp2221`
driver, each showing up as its own `/dev/i2c-N`) at once: their kernel-reported
adapter *names* are identical, so today's name-match selector (`find_i2c_bus`)
cannot tell them apart, and `/dev/i2c-N` numbers are not stable across reboots
or USB re-enumeration.

While implementing this, the underlying UI bug that would otherwise block it
gets fixed too: the `i2c_bus_num` field is a fixed-option `<select>` in every
wizard surface today, even though its help text already claims free-text
values (a pyftdi URL, an MCP2221 serial) work — they never did. This design
also turns that field into free text with a discovery-backed "Discover" button,
across all three USB-capable kinds (`extended`, `ft232h`, `mcp2221`) and all
three surfaces it appears on (probe devices, distance sensor, fan controller).

## Background

Why the userspace `mcp2221` bus kind (added in
`docs/superpowers/specs/2026-07-12-dual-usb-i2c-bus-design.md`) doesn't solve
this: `_construct_mcp2221` selects a physical device by serial by re-pointing
Adafruit Blinka's **single process-global HID handle**
(`_mcp_mod.mcp2221._hid`) at a different USB device path. It can *choose* which
MCP2221 to talk to, but only one at a time — opening a second serial steals the
handle from the first. Two physical MCP2221 units used concurrently in one
process need two independent handles, which the kernel `hid-mcp2221` driver
already provides via two separate `/dev/i2c-N` character devices. The `extended`
kind already opens `/dev/i2c-N` independently per adapter (`ExtendedI2C`), so it
has no such conflict — it only needs a way to identify *which* `i2c-N` belongs to
which physical device.

Current `extended` selector resolution (`common/i2c_bus.py`):

- `resolve_i2c_bus(spec)`: a numeric string selects `/dev/i2c-N` directly;
  otherwise `find_i2c_bus(spec)` substring-matches the adapter's `name` file
  under `/sys/bus/i2c/devices/i2c-*/name` (e.g. `'CP2112'`).
- Adapter *name* is a driver-level string (`"MCP2221 usb-i2c bridge"` or
  similar) and is **identical** across two units of the same chip — it cannot
  disambiguate them. The USB device's iSerial can.

Not every `extended` adapter is USB-backed (e.g. a Pi's onboard I2C, reachable
as `extended` bus `1`, has no USB ancestor and no serial). Serial-based
selection is only ever available for adapters that resolve to one; others keep
working exactly as today via numeric or name-based selection.

## Architecture

### Serial resolution (`common/i2c_bus.py`)

`_read_usb_serial(bus_dir)`: `os.path.realpath()` the adapter's sysfs directory
(`/sys/bus/i2c/devices/i2c-N`, itself a symlink), then walk up parent
directories (bounded, stops at the filesystem root) until one contains both a
`serial` file and an `idVendor` file — the USB *device* level in sysfs, as
opposed to an interface or subsystem level. Requiring both files co-located
avoids false positives from an unrelated `serial` file elsewhere in sysfs (e.g.
`power_supply` nodes also expose `serial`). Returns the stripped contents of
`serial`, or `None` if no such ancestor exists within the walk (non-USB
adapter, or sysfs layout PiFire doesn't recognize).

`_enumerate_i2c_adapters(devices_path='/sys/bus/i2c/devices')`: internal helper
factoring the existing `glob.glob('i2c-*')` + `name`-file-read loop out of
`find_i2c_bus` into one place, extended to also call `_read_usb_serial` per
adapter. Returns `[{bus_num, name, serial}, ...]`. Both `find_i2c_bus` (existing,
name match) and `find_i2c_bus_by_serial` (new) consume this list — no duplicated
sysfs-walking code, and `find_i2c_bus`'s existing behavior, logging, and error
messages are unchanged.

`find_i2c_bus_by_serial(serial, devices_path='/sys/bus/i2c/devices')`: **exact**
match (not substring — unlike name matching, a serial is meant to be an
unambiguous identifier, so partial matches only add risk) against the
enumerated `serial` field. Same error convention as `find_i2c_bus`: `RuntimeError`
for zero or multiple matches, listing every available adapter as
`i2c-N (serial=<repr or None>)` so the message is actionable without re-running
anything.

`resolve_i2c_bus(spec)` gains a selector syntax, checked before the existing
numeric/name-match branches:

```python
def resolve_i2c_bus(bus):
    spec = str(bus).strip()
    if spec.lower().startswith('serial:'):
        return find_i2c_bus_by_serial(spec.split(':', 1)[1].strip())
    if spec.isdigit():
        return int(spec)
    return find_i2c_bus(spec)
```

`i2c_bus_num = "serial:0012AB34"` now selects the `extended` adapter whose USB
iSerial is `0012AB34`, independent of bus number or adapter name. Fully
backward compatible: existing numeric and name-match values are untouched.

### Discovery helpers (`common/i2c_bus.py`)

Added to support the new UI (below). All are **best-effort**: a missing
optional dependency (`hid`, `pyftdi`), a permission error, or zero devices
present all resolve to `[]` (logged at DEBUG) rather than raising — these only
feed autocomplete suggestions, and must never break the page that calls them.

- `discover_extended_i2c_buses()` → `_enumerate_i2c_adapters()` as-is:
  `[{bus_num, name, serial}, ...]`.
- `discover_mcp2221_devices()` → `hid.enumerate(MCP2221.VID, MCP2221.PID)`,
  mirroring the matching logic already in `_construct_mcp2221`. Returns
  `[{serial, path}, ...]`.
- `discover_ft232h_devices()` → `pyftdi.ftdi.Ftdi.list_devices()`. Returns
  `[{url, serial, description}, ...]`.

## UI: discovery-backed free-text entry

### Why this is in scope

`i2c_bus_num` is rendered today as a strict `<select>` with a fixed
`list_values` enum (`CP2112`, `MCP2221`, `0`-`15`) on every surface it appears —
despite its `description` already promising free-text pyftdi URLs and MCP2221
serials work. They never did: nothing in the UI could ever submit a value
outside `list_values`. This was already a latent bug; adding a `serial:...`
selector that's impossible to type in would just be a second instance of it.
Fixed once, for all three affected kinds (`extended`, `ft232h`, `mcp2221`) and
all three surfaces (probe devices, distance sensor, fan controller), since
they all share the identical field/bug.

### Backend: new wizard route action

`blueprints/wizard/routes.py`, following the existing `bt_scan` /
`thermoworks_discover` dispatch convention inside `wizard_page()`: a new
`action == 'i2c_bus_scan'` branch reads `kind` (`extended` / `ft232h` /
`mcp2221`) and `itemID` from the POST body, calls the matching discovery
helper, and returns a `render_template_string` of a new
`render_i2c_scan_table` macro — an HTML fragment (not JSON, matching the
existing convention), one row per candidate with a "Select" button. Discovery
errors render the same `alert-danger` fragment style the existing actions use.

### Frontend: new input macro + JS

`render_input_i2c_bus_num(section, mode, label, default, kind_field_id)`,
added to `_macro_probes_config.html` alongside `render_input_bt_address`: a
free-text `<input>` + "Discover" button + modal, the same shape as the
Bluetooth-scan pattern. `scanI2CBus(itemID, kindFieldId)` (new, in
`probeconfig.js`, mirroring `scanBluetooth`) reads the paired `i2c_bus_kind`
`<select>`'s current value so the scan is kind-aware, `.load()`s the
`i2c_bus_scan` fragment into the modal; `selectI2CBus(value, itemID)` (mirroring
`selectBluetoothDevice`) writes the chosen value into the input and closes the
modal.

`_macro_wizard_card.html` (distance sensor, fan controller
`settings-dependency` fields) has no scan-button pattern today — this is net
new there, wired to the same `/wizard` `i2c_bus_scan` action and the same
`render_i2c_scan_table` fragment, so both surfaces share one backend action and
one result-rendering macro with two thin field-wrapper macros.

### Manifest changes (`wizard/wizard_manifest.json`)

All ~11 `i2c_bus_num` occurrences (probe `device_specific.config`, distance
sensor, fan controller): `"type": "list"` → `"type": "text"`, dropping
`list_values`/`list_labels`/`options` (no longer meaningful for free text).
`description` updated on each to document the `serial:<ISERIAL>` selector and
note that Discover suggestions are best-effort per kind. Template dispatch in
both macros routes `i2c_bus_num` fields to the new input macro instead of the
generic list-select path.

## Testing

- `tests/test_i2c_bus.py`: `_read_usb_serial` against a fixture sysfs tree
  (symlinked `i2c-N` → nested USB device directory with `serial`+`idVendor` →
  resolves; an adapter with no USB ancestor → `None`); `find_i2c_bus_by_serial`
  (match / no-match / ambiguous, mirroring the existing `find_i2c_bus` test
  shapes); `resolve_i2c_bus('serial:...')` dispatch; the three discovery
  helpers via mocked `hid.enumerate` / `pyftdi.ftdi.Ftdi.list_devices` / the
  sysfs fixture.
- New route test(s) for `/wizard` `action=i2c_bus_scan`, one per kind, asserting
  the rendered fragment for both the happy path and the empty/error path.
- `tests/test_wizard_bus_kinds.py` / `tests/test_i2c_bridge_match_manifest.py`:
  update assertions that currently check `list_values` on `i2c_bus_num` fields
  for the `type: "text"` change.

## Backward compatibility

Every existing stored value keeps working unchanged: plain bus numbers,
`CP2112`/`MCP2221` name matches, existing ft232h URLs, existing mcp2221
serials — the field becomes free-text-with-help rather than a dropdown that
happened to already list those exact strings as options. No settings migration.

## Out of scope

- Any UI/backend change to the `basic` or (already free-form, working) numeric
  `extended` selector paths.
- Persisting discovered devices' friendly names/history — Discover is a
  point-in-time scan, not a saved device registry.
- Non-USB serial-like identifiers (e.g. matching by a platform/onboard bus's
  own non-USB identifier) — out of scope; `serial:` is specifically a USB
  iSerial.
