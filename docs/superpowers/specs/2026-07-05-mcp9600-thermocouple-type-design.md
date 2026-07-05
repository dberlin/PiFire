# Configurable Thermocouple Type for MCP9600 — Design

**Date:** 2026-07-05
**Status:** Approved

## Problem

The MCP9600 thermocouple-amplifier probe module always reads its thermocouple as
Type K. The Adafruit MCP9600 hardware and library support eight thermocouple
types (K, J, T, N, S, E, B, R), but PiFire never passes a type to the library, so
it silently uses the library default (K). Users with a non-K thermocouple on an
MCP9600 have no way to configure it.

The sibling MAX31856 probe already supports configurable thermocouple type end to
end (backend reads `tc_type` from config and maps it to the Adafruit enum; the
wizard exposes a "Thermocouple Type" dropdown). This work brings the MCP9600 to
parity. No MAX31856 changes are required — it was verified complete, including a
passing test asserting both the backend wiring and the manifest entry.

## Scope

- **In scope:** MCP9600 backend, MCP9600 wizard manifest entry, MCP9600 unit test.
- **Out of scope:** Any MAX31856 change (already complete). Migrating existing
  saved device configs (default of `K` preserves current behavior).

## Design

### 1. Backend — `probes/mcp9600_adafruit.py`

Unlike MAX31856, the Adafruit MCP9600 library accepts `tctype` as a plain string
whose valid values are exactly the eight type letters (`"K", "J", "T", "N", "S",
"E", "B", "R"`). No enum-mapping table is needed.

Changes:

- `KTTDevice.__init__` gains a `tc_type='K'` keyword parameter and passes it to
  the library as `MCP9600(self.i2c, address=i2c_bus_addr, tctype=tc_type)`.
- `ReadProbes._init_device` reads
  `tc_type = self.device_info['config'].get('tc_type', 'K')` and passes it to
  `KTTDevice`.
- Update the module docstring's example device definition to include
  `'tc_type' : 'K'` in the `config` block.

The default of `'K'` keeps existing configurations behaving identically.

### 2. Wizard — `wizard/wizard_manifest.json`

Add a `tc_type` entry to `modules.probes.mcp9600_adafruit.device_specific.config`,
identical in shape to the MAX31856 entry, placed immediately after
`i2c_bus_addr`:

- `label`: `tc_type`
- `friendly_name`: `Thermocouple Type`
- `description`: `Thermocouple type. Type K is the most common for cooking.`
- `type`: `list`
- `list_values`: `["B", "E", "J", "K", "N", "R", "S", "T"]`
- `list_labels`: `["Type B", "Type E", "Type J", "Type K", "Type N", "Type R", "Type S", "Type T"]`
- `default`: `K`
- `hidden`: `false`

### 3. Test — `tests/test_mcp9600_probe.py` (new)

There is currently no MCP9600 test. Add one mirroring
`tests/test_max31856_probe.py`:

- Install a fake `adafruit_mcp9600` module (a `MCP9600` class capturing the
  `tctype`/`address` it was constructed with) plus fakes for the `board`,
  `busio`, `adafruit_extended_bus`, and `adafruit_bus_device.i2c_device` imports
  the module pulls in at import time, so the probe imports without hardware.
- `test_init_device_wires_tc_type`: config `{'tc_type': 'J', ...}` →
  constructed sensor's captured `tctype == 'J'`, ports == `['KTT0']`.
- `test_init_device_defaults`: empty config → captured `tctype == 'K'`.
- `test_manifest_mcp9600_entry`: the manifest's `tc_type` config option exists
  with `list_values == ['B', 'E', 'J', 'K', 'N', 'R', 'S', 'T']` and
  `default == 'K'`.

## Error Handling

Invalid `tc_type` values can only originate from the wizard's fixed dropdown. If
an invalid value is supplied anyway, the Adafruit library raises `ValueError` at
construction — consistent with how the MAX31856 backend fails on a bad type. No
additional validation is added.

## Verification

- `pytest tests/test_mcp9600_probe.py tests/test_max31856_probe.py` passes.
- Manual/manifest sanity: the MCP9600 wizard entry shows the new "Thermocouple
  Type" dropdown defaulting to Type K.
