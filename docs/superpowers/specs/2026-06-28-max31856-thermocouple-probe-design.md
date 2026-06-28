# MAX31856 thermocouple probe (`max31856_adafruit`) — Design

**Date:** 2026-06-28
**Status:** Approved (design); implementation pending
**Author:** PiFire

## Goal

Add a MAX31856 thermocouple probe to PiFire using the Adafruit
`adafruit_max31856` CircuitPython library. The probe reports thermocouple
temperature and lets the user configure the thermocouple type, the averaging
sample count, and the 50/60 Hz noise-rejection filter. It reuses the shared
`resolve_spi_bus` helper, so it works on both native `board.SPI()` and the
MCP2210 USB-to-SPI bridge (with GP0–GP8 chip-select) with no bus-specific code.

## Background

This mirrors `probes/max31865_adafruit.py` (the RTD probe), which was recently
refactored onto `resolve_spi_bus(config, default_cs) -> (spi, chip_select)` in
`probes/base.py`. That helper owns the `spi_bus_kind` branch (`basic` /
`mcp2210`), the board-pin lookup, and CS resolution. A new SPI probe is
therefore: one module that calls the helper, plus a wizard manifest entry with
the three standardized bus/CS fields and the sensor-specific fields.

The `adafruit_max31856` API (confirmed against the library source) is:
- `adafruit_max31856.ThermocoupleType.{B,E,J,K,N,R,S,T}` (plus voltage modes
  `G8`/`G32`, which are out of scope).
- `MAX31856(spi, cs, thermocouple_type=ThermocoupleType.K, baudrate=500000)` —
  thermocouple type is a constructor argument.
- `sensor.averaging` — settable property; accepts `1, 2, 4, 8, 16`.
- `sensor.noise_rejection` — settable property; accepts `50` or `60`.
- `sensor.temperature` — thermocouple temperature (°C).
- `sensor.reference_temperature` — cold-junction temperature (°C); **ignored**
  by this probe (single-reading design, per decision).

PiFire treats a probe device as producing one reading per port. The MCP9600
thermocouple probe (`probes/mcp9600_adafruit.py`) is the precedent for a
temperature-only probe: it reports temperature on its port and writes `0` to the
resistance (`tr`) slot. This probe follows that pattern with a single `TC0`
port.

## Architecture

### New file `probes/max31856_adafruit.py`

Same structure as `max31865_adafruit.py`. Hardware import (`adafruit_max31856`)
at module top is acceptable and consistent with the sibling probe; the
bus/board imports live inside `resolve_spi_bus` (lazy), so the probe never
imports `board`/`digitalio` directly.

```python
import logging
import adafruit_max31856
from probes.base import ProbeInterface, resolve_spi_bus

# config string -> adafruit_max31856.ThermocoupleType.* enum value
_TC_TYPES = {
	'B': adafruit_max31856.ThermocoupleType.B,
	'E': adafruit_max31856.ThermocoupleType.E,
	'J': adafruit_max31856.ThermocoupleType.J,
	'K': adafruit_max31856.ThermocoupleType.K,
	'N': adafruit_max31856.ThermocoupleType.N,
	'R': adafruit_max31856.ThermocoupleType.R,
	'S': adafruit_max31856.ThermocoupleType.S,
	'T': adafruit_max31856.ThermocoupleType.T,
}


class TCDevice():
	''' MAX31856 thermocouple device (Adafruit module) '''
	def __init__(self, spi, cs, tc_type='K', averaging=1, noise_rejection=60):
		self.status = {}
		self.sensor = adafruit_max31856.MAX31856(
			spi, cs, thermocouple_type=_TC_TYPES[tc_type])
		self.sensor.averaging = averaging
		self.sensor.noise_rejection = noise_rejection

	@property
	def temperature(self):
		return self.sensor.temperature

	def get_status(self):
		return self.status


class ReadProbes(ProbeInterface):
	def __init__(self, probe_info, device_info, units):
		super().__init__(probe_info, device_info, units)

	def _init_device(self):
		self.time_delay = 0
		self.device_info['ports'] = ['TC0']
		config = self.device_info['config']
		spi, cs = resolve_spi_bus(config, default_cs='D6')
		tc_type = config.get('tc_type', 'K')
		averaging = int(config.get('averaging', 1))
		noise_rejection = int(config.get('noise_rejection', 60))
		self.device = TCDevice(spi, cs, tc_type, averaging, noise_rejection)

	def read_all_ports(self, output_data):
		# thermocouple temperature -> the single TC0 port; resistance NA (0)
		...mirror max31865_adafruit.read_all_ports, but set 'tr' slot to 0...
```

`read_all_ports` mirrors `max31865_adafruit.read_all_ports` exactly, except the
resistance line writes `0` (thermocouples have no resistance reading), matching
`mcp9600_adafruit`.

Config keys read by `_init_device`:
- `tc_type` (default `'K'`) — mapped via `_TC_TYPES`.
- `averaging` (default `1`) — int, passed to `sensor.averaging`.
- `noise_rejection` (default `60`) — int, passed to `sensor.noise_rejection`.
- `cs`, `spi_bus_kind`, `mcp2210_serial` — read by `resolve_spi_bus`; `cs`
  default `'D6'`.

### Wizard manifest entry (`modules.probes.max31856_adafruit`)

`friendly_name` "MAX31856 Thermocouple Adafruit", `filename`
`max31856_adafruit`, `type` `"thermocouple"`, `ports` `["TC0"]`,
`default` `false`, `settings_dependencies` = units (copied from
`max31865_adafruit`). `py_dependencies`:
`["adafruit-circuitpython-max31856", "mcp2210", "hid>=1.0.4"]`.

`device_specific.config` items, in order:
1. `cs` — copied verbatim from the current `max31865_adafruit` entry (board
   pins `GPIO2`–`GPIO27` plus MCP2210 `0`–`8`, same `list_values`/`list_labels`,
   default `"D2"`).
2. `spi_bus_kind` — copied verbatim (`basic`/`mcp2210`, default `basic`).
3. `mcp2210_serial` — copied verbatim (string, default `""`).
4. `tc_type` — list, `list_values` `["B","E","J","K","N","R","S","T"]`,
   `list_labels` `["Type B","Type E","Type J","Type K","Type N","Type R","Type
   S","Type T"]`, default `"K"`.
5. `averaging` — list, `list_values` `["1","2","4","8","16"]`, matching labels,
   default `"1"`.
6. `noise_rejection` — list, `list_values` `["60","50"]`, `list_labels`
   `["60 Hz (US)","50 Hz (EU)"]`, default `"60"`.
7. `transient` — hidden, copied from `max31865_adafruit`.

No wizard/blueprint code changes — the manifest config list renders generically
and is stored by the existing `probes_devspec_*` handling.

## Config shape (example stored device)

```json
{
  "device": "GrillTC",
  "module": "max31856_adafruit",
  "module_filename": "max31856_adafruit",
  "ports": ["TC0"],
  "config": {
    "cs": "5",
    "spi_bus_kind": "mcp2210",
    "mcp2210_serial": "",
    "tc_type": "K",
    "averaging": "1",
    "noise_rejection": "60"
  }
}
```

## Error handling

- An unknown `tc_type` (not in `_TC_TYPES`) raises `KeyError` at init; the
  default `'K'` and the wizard's fixed list make this a misconfiguration-only
  path. (Mirrors how `max31865_adafruit` indexes its config.)
- Bus/CS errors surface from `resolve_spi_bus` (clear `ValueError` for unknown
  kind/pin) and the MCP2210 driver — not swallowed.
- `averaging`/`noise_rejection` are passed to the library setters; out-of-range
  values surface the library's own error.

## Testing (hardware-free)

`adafruit_max31856` is not installed in CI, so tests inject a fake module,
mirroring the `max31865` probe test approach:

- **Probe wiring** (new `tests/test_max31856_probe.py`): inject a fake
  `adafruit_max31856` into `sys.modules` (with a `ThermocoupleType` carrying
  `B…T` attributes and a `MAX31856` class capturing constructor args and
  `averaging`/`noise_rejection` assignments), `importlib.reload` the probe,
  monkeypatch `resolve_spi_bus` to return sentinels, build a bare `ReadProbes`
  (`__new__`), call `_init_device`, and assert: `resolve_spi_bus` called with
  `default_cs='D6'`; the sensor got the sentinel `(spi, cs)` and the mapped
  `thermocouple_type` for a non-default type (e.g. `tc_type='J'`); `averaging`
  and `noise_rejection` set from int-parsed config; `ports == ['TC0']`.
- **Manifest sanity** (same test file): assert the `max31856_adafruit` entry
  exists with `type == 'thermocouple'`, `ports == ['TC0']`, config labels
  include `tc_type`/`averaging`/`noise_rejection` plus the three bus/CS fields,
  `tc_type.list_values == ["B","E","J","K","N","R","S","T"]`, and
  `py_dependencies` includes `adafruit-circuitpython-max31856`, `mcp2210`, `hid`.

Run only the new test file (the broader `tests/` dir has unrelated
numpy-dependent collection failures).

## Files changed

| File | Change |
|------|--------|
| `probes/max31856_adafruit.py` (new) | The probe module above. |
| `wizard/wizard_manifest.json` | Add the `max31856_adafruit` entry. |
| `tests/test_max31856_probe.py` (new) | Probe-wiring + manifest sanity tests. |

## Out of scope

- **Cold-junction / reference temperature** — ignored by decision (single `TC0`
  reading).
- **Voltage modes** `ThermocoupleType.G8`/`G32` — not exposed (not thermocouple
  types).
- **Fault detection / thresholds** — the library defaults are used; no fault
  surfacing in this first cut.
- **A raw-spidev variant** — only the Adafruit/busio path (which the MCP2210
  bus supports) is added.
