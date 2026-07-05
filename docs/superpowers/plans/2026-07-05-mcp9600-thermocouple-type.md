# MCP9600 Configurable Thermocouple Type Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users configure the thermocouple type (K/J/T/N/S/E/B/R) for the MCP9600 probe, matching the MAX31856 which already supports it.

**Architecture:** The Adafruit MCP9600 library accepts a `tctype` string argument whose valid values are exactly the eight type letters, so the backend passes the configured string straight through (no enum-mapping table, unlike MAX31856). A new `tc_type` config option is added to the MCP9600 wizard manifest entry so the type is selectable in the setup UI. A new unit test locks in both the backend wiring and the manifest entry.

**Tech Stack:** Python 3, pytest, Adafruit CircuitPython MCP9600 library, JSON wizard manifest.

## Global Constraints

- Default thermocouple type is `'K'` — existing saved configs must behave identically (always Type K today).
- Valid `tc_type` values: `B, E, J, K, N, R, S, T` (the wizard-exposed order); the library also accepts these eight letters.
- Follow the existing MAX31856 pattern exactly (`probes/max31856_adafruit.py`, `tests/test_max31856_probe.py`, and the MAX31856 wizard entry) for shape and style.
- Tests must import the probe module without real hardware by installing fake Adafruit/board modules into `sys.modules`.

---

### Task 1: Backend — pass `tc_type` into the MCP9600 sensor

**Files:**
- Create: `tests/test_mcp9600_probe.py`
- Modify: `probes/mcp9600_adafruit.py`

**Interfaces:**
- Consumes: `probes.base.ProbeInterface`, `probes.base.resolve_i2c_bus` (unchanged).
- Produces: `KTTDevice.__init__(self, i2c_bus_addr=0x67, i2c_bus_kind='basic', i2c_bus_num=0, tc_type='K')` — constructs `MCP9600(self.i2c, address=i2c_bus_addr, tctype=tc_type)`. `ReadProbes._init_device` reads `tc_type` from `self.device_info['config']` (default `'K'`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mcp9600_probe.py`. The MCP9600 module imports `board`, `busio`, `adafruit_extended_bus`, `adafruit_bus_device.i2c_device`, and `adafruit_mcp9600` at import time, so all must be faked before importing the probe.

```python
import sys
import types
import importlib

import pytest


def _install_fakes(monkeypatch):
    """Install fake hardware modules so the probe imports without hardware."""
    # adafruit_mcp9600 with an MCP9600 that captures its constructor args
    mcp_mod = types.ModuleType('adafruit_mcp9600')

    class FakeMCP9600:
        def __init__(self, i2c, address=0x67, tctype='K'):
            self.i2c = i2c
            self.address = address
            self.tctype = tctype
            self.temperature = 0.0

    mcp_mod.MCP9600 = FakeMCP9600
    monkeypatch.setitem(sys.modules, 'adafruit_mcp9600', mcp_mod)

    # board / busio
    board_mod = types.ModuleType('board')
    board_mod.SCL = 'SCL'
    board_mod.SDA = 'SDA'
    monkeypatch.setitem(sys.modules, 'board', board_mod)

    busio_mod = types.ModuleType('busio')
    busio_mod.I2C = lambda scl, sda: ('I2C', scl, sda)
    monkeypatch.setitem(sys.modules, 'busio', busio_mod)

    # adafruit_extended_bus.ExtendedI2C
    ext_mod = types.ModuleType('adafruit_extended_bus')
    ext_mod.ExtendedI2C = lambda bus: ('ExtI2C', bus)
    monkeypatch.setitem(sys.modules, 'adafruit_extended_bus', ext_mod)

    # adafruit_bus_device.i2c_device.I2CDevice
    busdev_pkg = types.ModuleType('adafruit_bus_device')
    i2cdev_mod = types.ModuleType('adafruit_bus_device.i2c_device')
    i2cdev_mod.I2CDevice = object
    busdev_pkg.i2c_device = i2cdev_mod
    monkeypatch.setitem(sys.modules, 'adafruit_bus_device', busdev_pkg)
    monkeypatch.setitem(sys.modules, 'adafruit_bus_device.i2c_device', i2cdev_mod)

    return mcp_mod


def _load_probe(monkeypatch):
    _install_fakes(monkeypatch)
    import probes.mcp9600_adafruit as probe

    importlib.reload(probe)  # bind the fake adafruit_mcp9600
    return probe


def test_init_device_wires_tc_type(monkeypatch):
    probe = _load_probe(monkeypatch)

    obj = probe.ReadProbes.__new__(probe.ReadProbes)  # bypass heavy base __init__
    obj.device_info = {'config': {'i2c_bus_addr': '0x66', 'tc_type': 'J'}}
    obj._init_device()

    assert obj.device_info['ports'] == ['KTT0']
    sensor = obj.device.sensor
    assert sensor.tctype == 'J'  # configured type passed through
    assert sensor.address == 0x66  # parsed from hex string


def test_init_device_defaults(monkeypatch):
    probe = _load_probe(monkeypatch)

    obj = probe.ReadProbes.__new__(probe.ReadProbes)
    obj.device_info = {'config': {}}  # no keys -> all defaults
    obj._init_device()

    sensor = obj.device.sensor
    assert sensor.tctype == 'K'  # default K
    assert sensor.address == 0x67  # default address
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mcp9600_probe.py -v`
Expected: `test_init_device_wires_tc_type` FAILS on `assert sensor.tctype == 'J'` (currently the sensor is constructed without `tctype`, so it is `'K'`).

- [ ] **Step 3: Implement the backend change**

In `probes/mcp9600_adafruit.py`, change `KTTDevice.__init__` to accept and pass through `tc_type`:

```python
	def __init__(self, i2c_bus_addr=0x67, i2c_bus_kind='basic', i2c_bus_num=0, tc_type='K'):
		self.logger = logging.getLogger('control')
		self.status = {}

		if i2c_bus_kind == 'basic':
			# Create the I2C bus
			self.i2c = busio.I2C(board.SCL, board.SDA)
		elif i2c_bus_kind == 'extended':
			self.i2c = ExtendedI2C(resolve_i2c_bus(i2c_bus_num))

		self.sensor = MCP9600(self.i2c, address=i2c_bus_addr, tctype=tc_type)
```

In `ReadProbes._init_device`, read `tc_type` from config and pass it to `KTTDevice`:

```python
	def _init_device(self):
		self.time_delay = 0
		self.device_info['ports'] = ['KTT0']
		i2c_bus_addr = int(self.device_info['config'].get('i2c_bus_addr', '0x67'), 16)
		i2c_bus_kind = self.device_info['config'].get('i2c_bus_kind', 'basic')
		i2c_bus_num = self.device_info['config'].get('i2c_bus_num', 0)
		tc_type = self.device_info['config'].get('tc_type', 'K')
		try:
			self.device = KTTDevice(i2c_bus_addr=i2c_bus_addr, i2c_bus_kind=i2c_bus_kind, i2c_bus_num=i2c_bus_num, tc_type=tc_type)
		except:
			self.logger.error('Something went wrong when trying to initialize the MCP9600 device.')
			raise
```

Also update the module docstring's example device definition `config` block (near the top of the file) to include the new option, so the example matches reality:

```python
			'config' : {
				'i2c_bus_addr' : '0x67',	# I2C Bus Address
				'tc_type' : 'K'				# Thermocouple type K/J/T/N/S/E/B/R (default K)
			}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mcp9600_probe.py -v`
Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add probes/mcp9600_adafruit.py tests/test_mcp9600_probe.py
git commit -m "feat(probes): configurable thermocouple type for MCP9600"
```

---

### Task 2: Wizard manifest — add the Thermocouple Type dropdown

**Files:**
- Modify: `wizard/wizard_manifest.json` (`modules.probes.mcp9600_adafruit.device_specific.config`)
- Modify: `tests/test_mcp9600_probe.py`

**Interfaces:**
- Consumes: nothing from Task 1 at runtime; the manifest `tc_type` `label` must match the config key read in Task 1 (`tc_type`).
- Produces: a `tc_type` config entry in the MCP9600 wizard manifest with `list_values == ['B','E','J','K','N','R','S','T']` and `default == 'K'`.

- [ ] **Step 1: Write the failing manifest test**

Append to `tests/test_mcp9600_probe.py`:

```python
import json
import os


def test_manifest_mcp9600_entry():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    manifest = json.load(open(os.path.join(repo_root, 'wizard', 'wizard_manifest.json')))
    probes = manifest['modules']['probes']
    assert 'mcp9600_adafruit' in probes
    entry = probes['mcp9600_adafruit']

    ds = entry['device_specific']
    assert ds['type'] == 'thermocouple'
    assert ds['ports'] == ['KTT0']

    labels = [item['label'] for item in ds['config']]
    assert 'tc_type' in labels

    tc = next(i for i in ds['config'] if i['label'] == 'tc_type')
    assert tc['list_values'] == ['B', 'E', 'J', 'K', 'N', 'R', 'S', 'T']
    assert tc['default'] == 'K'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mcp9600_probe.py::test_manifest_mcp9600_entry -v`
Expected: FAIL — `'tc_type' in labels` is False (no such config entry yet).

- [ ] **Step 3: Add the manifest entry**

In `wizard/wizard_manifest.json`, inside
`modules.probes.mcp9600_adafruit.device_specific.config`, insert this object
immediately **after** the `i2c_bus_addr` entry (and before `i2c_bus_kind`):

```json
      {
        "label": "tc_type",
        "friendly_name": "Thermocouple Type",
        "description": "Thermocouple type. Type K is the most common for cooking.",
        "type": "list",
        "list_values": [
          "B",
          "E",
          "J",
          "K",
          "N",
          "R",
          "S",
          "T"
        ],
        "list_labels": [
          "Type B",
          "Type E",
          "Type J",
          "Type K",
          "Type N",
          "Type R",
          "Type S",
          "Type T"
        ],
        "default": "K",
        "hidden": false
      },
```

- [ ] **Step 4: Verify JSON validity and run tests**

Run: `python3 -c "import json; json.load(open('wizard/wizard_manifest.json')); print('valid')"`
Expected: `valid`

Run: `pytest tests/test_mcp9600_probe.py -v`
Expected: all three tests PASS.

- [ ] **Step 5: Commit**

```bash
git add wizard/wizard_manifest.json tests/test_mcp9600_probe.py
git commit -m "feat(wizard): add MCP9600 thermocouple type option"
```

---

### Task 3: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full probe test suite**

Run: `pytest tests/test_mcp9600_probe.py tests/test_max31856_probe.py -v`
Expected: all tests PASS (Task 1 + Task 2 MCP9600 tests, plus the existing MAX31856 tests still green).

- [ ] **Step 2: Confirm no other tests regressed**

Run: `pytest -q`
Expected: the suite passes (or only pre-existing, unrelated failures remain — compare against a clean `main` run if anything looks off).
