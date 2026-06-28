# MAX31856 Thermocouple Probe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a MAX31856 thermocouple probe (`max31856_adafruit`) that reports thermocouple temperature with configurable thermocouple type, averaging, and 50/60 Hz noise rejection, reusing the shared `resolve_spi_bus` helper so it works on native SPI and the MCP2210 bridge.

**Architecture:** A new `probes/max31856_adafruit.py` mirroring `probes/max31865_adafruit.py` (the RTD probe), temperature-only, calling `resolve_spi_bus(config, default_cs='D6')` for bus + CS. A wizard manifest entry exposes the config. Tests are hardware-free via a faked `adafruit_max31856`.

**Tech Stack:** Python ≥3.14, `adafruit_max31856` (only on real hardware), the in-repo `resolve_spi_bus` helper, `pytest`.

## Global Constraints

- **Indentation: TABS** in `probes/max31856_adafruit.py` (all `probes/*.py` use tabs). The test file `tests/test_max31856_probe.py` uses spaces (existing `tests/` convention).
- **Hardware libs absent in CI:** `adafruit_max31856`, `board`, `digitalio`, `hid` are not installed (only `mcp2210` imports). Tests must inject a fake `adafruit_max31856` into `sys.modules`; never import the real lib. The probe imports `adafruit_max31856` at module top (consistent with `max31865_adafruit`), but does NOT import `board`/`digitalio` (those are lazy inside `resolve_spi_bus`).
- **Reused helper:** `from probes.base import ProbeInterface, resolve_spi_bus`; `resolve_spi_bus(config, default_cs) -> (spi, chip_select)`. `default_cs='D6'`.
- **adafruit_max31856 API (verified):** `ThermocoupleType.{B,E,J,K,N,R,S,T}`; `MAX31856(spi, cs, thermocouple_type=ThermocoupleType.K)`; `sensor.averaging` accepts `1,2,4,8,16`; `sensor.noise_rejection` accepts `50,60`; `sensor.temperature` (°C). Cold junction (`reference_temperature`) is ignored.
- **Config defaults:** `tc_type='K'`, `averaging=1`, `noise_rejection=60`, `cs` default `'D6'`.
- **Probe shape:** single port `['TC0']`, `type` `"thermocouple"`; resistance (`tr`) slot written as `0` (no resistance for thermocouples), like `mcp9600_adafruit`.
- **Tests:** in `tests/`, run with `python -m pytest`. Run ONLY the new file `tests/test_max31856_probe.py` — never the whole `tests/` dir (unrelated MPC tests fail to collect; numpy absent).
- **Commit messages** end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## File Structure

- `probes/max31856_adafruit.py` (new) — `TCDevice` (wraps `adafruit_max31856.MAX31856`, temperature-only) + `ReadProbes` (`_init_device` resolves bus/CS and config; `read_all_ports` reports temp on `TC0`). One clear responsibility: the MAX31856 probe.
- `wizard/wizard_manifest.json` — add the `max31856_adafruit` entry under `modules.probes`.
- `tests/test_max31856_probe.py` (new) — probe-wiring test + manifest sanity test.

---

### Task 1: The `max31856_adafruit` probe module

**Files:**
- Create: `probes/max31856_adafruit.py`
- Test: `tests/test_max31856_probe.py` (new)

**Interfaces:**
- Consumes: `resolve_spi_bus(config, default_cs) -> (spi, chip_select)` and `ProbeInterface` from `probes.base`; `adafruit_max31856.MAX31856` / `.ThermocoupleType`.
- Produces: `TCDevice(spi, cs, tc_type='K', averaging=1, noise_rejection=60)` with `.temperature`/`.get_status()`; `ReadProbes` with `_init_device` (sets `ports=['TC0']`, builds `TCDevice`) and `read_all_ports`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_max31856_probe.py` (SPACES indentation):

```python
import sys
import types
import importlib

import pytest


def _install_fake_adafruit(monkeypatch):
    """Install a fake adafruit_max31856 so the probe imports without hardware."""
    fake = types.ModuleType("adafruit_max31856")

    class ThermocoupleType:
        B = "TC_B"
        E = "TC_E"
        J = "TC_J"
        K = "TC_K"
        N = "TC_N"
        R = "TC_R"
        S = "TC_S"
        T = "TC_T"

    class FakeMAX31856:
        def __init__(self, spi, cs, thermocouple_type=None):
            self.spi = spi
            self.cs = cs
            self.thermocouple_type = thermocouple_type
            self.averaging = None
            self.noise_rejection = None

    fake.ThermocoupleType = ThermocoupleType
    fake.MAX31856 = FakeMAX31856
    monkeypatch.setitem(sys.modules, "adafruit_max31856", fake)
    return fake


def _load_probe(monkeypatch):
    _install_fake_adafruit(monkeypatch)
    import probes.max31856_adafruit as probe
    importlib.reload(probe)  # bind the fake adafruit_max31856
    return probe


def test_init_device_wires_bus_type_and_settings(monkeypatch):
    probe = _load_probe(monkeypatch)

    captured = {}

    def fake_resolve(config, default_cs):
        captured["config"] = config
        captured["default_cs"] = default_cs
        return ("SPI", "CS")

    monkeypatch.setattr(probe, "resolve_spi_bus", fake_resolve)

    obj = probe.ReadProbes.__new__(probe.ReadProbes)  # bypass heavy base __init__
    obj.device_info = {"config": {
        "spi_bus_kind": "mcp2210", "cs": "5",
        "tc_type": "J", "averaging": "8", "noise_rejection": "50"}}
    obj._init_device()

    assert captured["default_cs"] == "D6"
    assert obj.device_info["ports"] == ["TC0"]
    sensor = obj.device.sensor
    assert sensor.spi == "SPI" and sensor.cs == "CS"
    assert sensor.thermocouple_type == "TC_J"   # 'J' mapped via ThermocoupleType
    assert sensor.averaging == 8                 # int-parsed
    assert sensor.noise_rejection == 50          # int-parsed


def test_init_device_defaults(monkeypatch):
    probe = _load_probe(monkeypatch)
    monkeypatch.setattr(probe, "resolve_spi_bus", lambda config, default_cs: ("SPI", "CS"))

    obj = probe.ReadProbes.__new__(probe.ReadProbes)
    obj.device_info = {"config": {}}  # no keys -> all defaults
    obj._init_device()

    sensor = obj.device.sensor
    assert sensor.thermocouple_type == "TC_K"   # default K
    assert sensor.averaging == 1                 # default 1
    assert sensor.noise_rejection == 60          # default 60


def test_temperature_property(monkeypatch):
    probe = _load_probe(monkeypatch)
    dev = probe.TCDevice.__new__(probe.TCDevice)

    class S:
        temperature = 123.4
    dev.sensor = S()
    assert dev.temperature == 123.4
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_max31856_probe.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'probes.max31856_adafruit'`.

- [ ] **Step 3: Create `probes/max31856_adafruit.py`**

Write the file below. **Use TAB indentation** (match `probes/max31865_adafruit.py`).

```python
#!/usr/bin/env python3

'''
*****************************************
PiFire Probes MAX31856 Adafruit Module
*****************************************

Description:
  This module utilizes the MAX31856 thermocouple hardware and returns
  temperature data. Depends on: pip3 install adafruit-circuitpython-max31856

	Ex Device Definition:

	device = {
			'device' : 'your_device_name',	# Unique name for the device
			'module' : 'max31856_adafruit',	# Must be populated for this module to load properly
			'ports' : ['TC0'],				# Defined in the module
			'config' : {
				'cs' : 'D6',				# SPI Chip Select (board pin or MCP2210 GP index)
				'spi_bus_kind' : 'basic',	# 'basic' (native SPI) or 'mcp2210'
				'mcp2210_serial' : '',		# Optional MCP2210 USB serial
				'tc_type' : 'K',			# Thermocouple type B/E/J/K/N/R/S/T (default K)
				'averaging' : 1,			# Averaging samples 1/2/4/8/16 (default 1)
				'noise_rejection' : 60		# Mains noise rejection 50/60 Hz (default 60)
			}
		}

'''

'''
*****************************************
 Imported Libraries
*****************************************
'''
import logging
import adafruit_max31856
from probes.base import ProbeInterface, resolve_spi_bus

# Config string -> adafruit_max31856.ThermocoupleType.* enum value
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

'''
*****************************************
 Class Definitions
*****************************************
'''

class TCDevice():
	''' MAX31856 Thermocouple Device Based on the Adafruit Module '''
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
		''' Read temperature from device '''
		tempC = round(self.device.temperature, 1)
		tempF = int(tempC * (9/5) + 32) # Celsius to Fahrenheit
		port = self.device_info['ports'][0]

		''' Thermocouples have no resistance reading '''
		self.output_data['tr'][self.port_map[port]] = 0

		''' Store the temperature in the output data structure '''
		if port == self.primary_port:
			self.output_data['primary'][self.port_map[port]] = tempF if self.units == 'F' else tempC
		elif port in self.food_ports:
			self.output_data['food'][self.port_map[port]] = tempF if self.units == 'F' else tempC
		elif port in self.aux_ports:
			self.output_data['aux'][self.port_map[port]] = tempF if self.units == 'F' else tempC

		return self.output_data
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_max31856_probe.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add probes/max31856_adafruit.py tests/test_max31856_probe.py
git commit -m "feat(probes): MAX31856 thermocouple probe (adafruit, SPI/MCP2210)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Wizard manifest entry for `max31856_adafruit`

**Files:**
- Modify: `wizard/wizard_manifest.json` (add `modules.probes.max31856_adafruit`)
- Test: `tests/test_max31856_probe.py` (append the manifest sanity test)

**Interfaces:**
- Consumes: nothing in code; the wizard renders `device_specific.config` generically and stores the `list_values` entry into `device['config'][label]`.
- Produces: a probe module entry whose config keys match what Task 1's `_init_device` reads (`tc_type`, `averaging`, `noise_rejection`) and what `resolve_spi_bus` reads (`cs`, `spi_bus_kind`, `mcp2210_serial`).

- [ ] **Step 1: Write the failing test (append to `tests/test_max31856_probe.py`)**

```python
import json
import os


def test_manifest_max31856_entry():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    manifest = json.load(open(os.path.join(repo_root, "wizard", "wizard_manifest.json")))
    probes = manifest["modules"]["probes"]
    assert "max31856_adafruit" in probes
    entry = probes["max31856_adafruit"]

    ds = entry["device_specific"]
    assert ds["type"] == "thermocouple"
    assert ds["ports"] == ["TC0"]

    labels = [item["label"] for item in ds["config"]]
    for required in ("cs", "spi_bus_kind", "mcp2210_serial",
                     "tc_type", "averaging", "noise_rejection"):
        assert required in labels

    tc = next(i for i in ds["config"] if i["label"] == "tc_type")
    assert tc["list_values"] == ["B", "E", "J", "K", "N", "R", "S", "T"]
    assert tc["default"] == "K"

    avg = next(i for i in ds["config"] if i["label"] == "averaging")
    assert avg["list_values"] == ["1", "2", "4", "8", "16"]

    nr = next(i for i in ds["config"] if i["label"] == "noise_rejection")
    assert nr["list_values"] == ["60", "50"]

    deps = " ".join(entry["py_dependencies"])
    assert "adafruit-circuitpython-max31856" in deps
    assert "mcp2210" in deps
    assert "hid" in deps
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_max31856_probe.py::test_manifest_max31856_entry -v`
Expected: FAIL — `assert "max31856_adafruit" in probes` (entry not present yet).

- [ ] **Step 3: Add the entry to `wizard/wizard_manifest.json`**

Add a new key `"max31856_adafruit"` to the `modules.probes` object (place it immediately after the `max31865_adafruit` entry for a clean diff). The `cs`, `spi_bus_kind`, and `mcp2210_serial` config items are copied verbatim from the current `max31865_adafruit` entry. Use exactly this JSON:

```json
   "max31856_adafruit": {
    "friendly_name": "MAX31856 Thermocouple Adafruit",
    "filename": "max31856_adafruit",
    "description": "This SPI device reads thermocouple probes (types B, E, J, K, N, R, S, T) using the Adafruit CircuitPython MAX31856 module. Supports native SPI or an MCP2210 USB-to-SPI bridge. Use a GPIO other than CE0/CE1 for the chip select.",
    "default": false,
    "image": "max31865.png",
    "reboot_required": false,
    "py_dependencies": [
     "adafruit-circuitpython-max31856",
     "mcp2210",
     "hid>=1.0.4"
    ],
    "apt_dependencies": [],
    "command_list": [],
    "settings_dependencies": {
     "units": {
      "friendly_name": "Temp Units",
      "description": "Select the temperature units to use for PiFire globally.  (this can be modified in settings later)",
      "options": {
       "F": "Fahrenheit",
       "C": "Celsius"
      },
      "settings": [
       "globals",
       "units"
      ]
     }
    },
    "device_specific": {
     "ports": [
      "TC0"
     ],
     "type": "thermocouple",
     "config": [
      {
       "label": "cs",
       "friendly_name": "SPI Chip Select (CS)",
       "description": "SPI Chip Select pin. For Basic (native SPI) pick a board GPIO (D2-D27). For MCP2210 pick the bridge's GP0-GP8.",
       "type": "list",
       "list_values": [
        "GPIO2", "GPIO3", "GPIO4", "GPIO5", "GPIO6", "GPIO12", "GPIO13",
        "GPIO14", "GPIO15", "GPIO16", "GPIO17", "GPIO18", "GPIO19", "GPIO20",
        "GPIO21", "GPIO22", "GPIO23", "GPIO24", "GPIO25", "GPIO26", "GPIO27",
        "0", "1", "2", "3", "4", "5", "6", "7", "8"
       ],
       "list_labels": [
        "D2", "D3", "D4", "D5", "D6", "D12", "D13", "D14", "D15", "D16", "D17",
        "D18", "D19", "D20", "D21", "D22", "D23", "D24", "D25", "D26", "D27",
        "MCP2210 GP0", "MCP2210 GP1", "MCP2210 GP2", "MCP2210 GP3", "MCP2210 GP4",
        "MCP2210 GP5", "MCP2210 GP6", "MCP2210 GP7", "MCP2210 GP8"
       ],
       "default": "D2",
       "hidden": false
      },
      {
       "label": "spi_bus_kind",
       "friendly_name": "SPI Bus Type",
       "description": "Use the board's native SPI (Basic) or an MCP2210 USB-to-SPI bridge. MCP2210 is required on hosts without native SPI (e.g. x86).",
       "type": "list",
       "list_values": [
        "basic",
        "mcp2210"
       ],
       "list_labels": [
        "Basic (native SPI)",
        "MCP2210 (USB-to-SPI bridge)"
       ],
       "default": "basic",
       "hidden": false
      },
      {
       "label": "mcp2210_serial",
       "friendly_name": "MCP2210 Serial",
       "description": "Optional. Leave blank to use the first/only MCP2210. Enter a USB serial string to select a specific bridge when more than one is attached. Ignored when SPI Bus Type is Basic.",
       "type": "string",
       "default": "",
       "hidden": false
      },
      {
       "label": "tc_type",
       "friendly_name": "Thermocouple Type",
       "description": "Thermocouple type. Type K is the most common for cooking.",
       "type": "list",
       "list_values": [
        "B", "E", "J", "K", "N", "R", "S", "T"
       ],
       "list_labels": [
        "Type B", "Type E", "Type J", "Type K", "Type N", "Type R", "Type S", "Type T"
       ],
       "default": "K",
       "hidden": false
      },
      {
       "label": "averaging",
       "friendly_name": "Averaging Samples",
       "description": "Number of samples averaged per reading. More samples = smoother but slower.",
       "type": "list",
       "list_values": [
        "1", "2", "4", "8", "16"
       ],
       "list_labels": [
        "1", "2", "4", "8", "16"
       ],
       "default": "1",
       "hidden": false
      },
      {
       "label": "noise_rejection",
       "friendly_name": "Noise Rejection",
       "description": "Mains noise rejection filter frequency. Use 60 Hz in North America, 50 Hz in most of Europe/Asia.",
       "type": "list",
       "list_values": [
        "60", "50"
       ],
       "list_labels": [
        "60 Hz (US)", "50 Hz (EU)"
       ],
       "default": "60",
       "hidden": false
      },
      {
       "label": "transient",
       "friendly_name": "Transient",
       "description": "Select whether this device is fixed/always attached (default) or if it is transient/sometimes detached.",
       "type": "list",
       "list_values": [
        "False",
        "True"
       ],
       "list_labels": [
        "Fixed (always attached)",
        "Transient (sometimes detached)"
       ],
       "default": "False",
       "hidden": true
      }
     ]
    }
   },
```

After editing, confirm the JSON is still valid:
Run: `python -c "import json; json.load(open('wizard/wizard_manifest.json')); print('valid')"`
Expected: `valid`

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_max31856_probe.py -v`
Expected: PASS (4 passed — the 3 from Task 1 plus the manifest test).

- [ ] **Step 5: Commit**

```bash
git add wizard/wizard_manifest.json tests/test_max31856_probe.py
git commit -m "feat(wizard): add MAX31856 thermocouple probe manifest entry

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review notes (for the implementer)

- **Manifest ↔ code contract:** the manifest config labels (`tc_type`, `averaging`, `noise_rejection`, `cs`, `spi_bus_kind`, `mcp2210_serial`) are exactly the keys `_init_device` and `resolve_spi_bus` read. The `tc_type` `list_values` (`B`–`T`) are exactly the `_TC_TYPES` keys.
- **Reused bus helper:** the probe gets native-SPI and MCP2210 support (and GP0–GP8 CS) entirely from `resolve_spi_bus`; no bus/CS logic is duplicated here.
- **Temperature-only:** `read_all_ports` writes `0` to the `tr` (resistance) slot, matching `mcp9600_adafruit`; only the thermocouple temperature is reported (cold junction ignored).
- **Defaults:** `tc_type='K'`, `averaging=1`, `noise_rejection=60` in both `_init_device` and the manifest defaults.
