# MCP2210 Probe SPI Bus Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let PiFire SPI probes select the MCP2210 USB-to-SPI bridge as their bus, per-probe in the wizard, via a shared helper — with `max31865_adafruit` as the first consumer.

**Architecture:** Add two helpers to `probes/base.py` — `resolve_mcp2210(serial)` (caches one shared `MCP2210` per serial, since a USB-HID handle opens only once) and `resolve_spi_bus(config, default_cs)` (returns `(spi, chip_select)`, owning the `spi_bus_kind` branch, the board-pin lookup, and CS resolution). Refactor `max31865_adafruit` to call the helper (also fixing a pre-existing CS `KeyError`), and surface the new config fields in the wizard manifest.

**Tech Stack:** Python ≥3.14, the in-repo `mcp2210` package, Adafruit Blinka (`board`/`digitalio`) + `adafruit_max31865` (only on real hardware), `pytest`.

## Global Constraints

- **Indentation: TABS** in `probes/*.py` — both `probes/base.py` and `probes/max31865_adafruit.py` use tab indentation. Match it exactly (mixing spaces will break Python).
- **Hardware libs are not importable in CI** (`board`, `digitalio`, `adafruit_max31865`, `hid` are absent; only `mcp2210` imports). Therefore: every hardware import must be **lazy** (inside the function/branch that needs it), and tests must never import a real hardware lib — they inject fakes into `sys.modules` and/or monkeypatch.
- **Bus kind naming:** `spi_bus_kind` is `"basic"` (native `board.SPI()`) or `"mcp2210"`. Not "extended" (no kernel device, unlike i2c).
- **CS source:** `basic` → `digitalio.DigitalInOut(getattr(board, <Dn>))`; `mcp2210` → `mcp.digital_inout(<GP index 0-8>)`.
- **CS value/label:** the wizard stores the `list_values` entry (BCM `GPIOn`) and shows the `list_labels` entry (`Dn`). The board-pin lookup is keyed by the stored `GPIOn` names mapping to `board.Dn`, and also accepts `Dn`. `default_cs` is `'D6'`.
- **MCP2210 IDs:** VID `0x04D8`, PID `0x00DE`. `MCP2210(serial=None)` opens the first device; `MCP2210(serial="ABC")` opens a specific one. (Driver signature: `MCP2210(vid=…, pid=…, serial=None, hid_device=None)`.)
- **Backward compatibility:** missing `spi_bus_kind` defaults to `basic`; no `settings.json` migration.
- **Tests:** in `tests/`, run with `python -m pytest`; `tests/conftest.py` puts the repo root on `sys.path`. Run the focused file `tests/test_mcp2210_probe_bus.py`, NOT the whole `tests/` dir (unrelated MPC tests fail to collect — numpy absent).
- **Commit messages** end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## File Structure

- `probes/base.py` — add an "SPI Bus Helpers" section (after the I2C helpers): `_SPI_CS_BOARD_PINS` map, `_MCP2210_CACHE`, `resolve_mcp2210`, `_gp_index`, `resolve_spi_bus`. No module-level hardware imports.
- `probes/max31865_adafruit.py` — drop `board`/`digitalio`/`LOOKUP_TABLE`; `RTDDevice` takes a ready `(spi, cs)`; `_init_device` calls `resolve_spi_bus(config, default_cs='D6')`.
- `wizard/wizard_manifest.json` — add `spi_bus_kind` + `mcp2210_serial` config items and GP0–GP8 CS options to the `max31865_adafruit` entry; add `mcp2210`/`hid` to its `py_dependencies`.
- `tests/test_mcp2210_probe_bus.py` (new) — helper tests, probe-wiring test, manifest sanity test.

---

### Task 1: SPI bus helpers in `probes/base.py`

**Files:**
- Modify: `probes/base.py` (add SPI helpers after the existing I2C helpers, before `## Class Definitions`)
- Test: `tests/test_mcp2210_probe_bus.py` (new)

**Interfaces:**
- Consumes: `mcp2210.MCP2210` (lazy import); `board`/`digitalio` (lazy import in the basic branch).
- Produces:
  - `resolve_mcp2210(serial=None) -> MCP2210` — cached per serial (`None`/`""` share one key).
  - `_gp_index(cs) -> int` — parses `0`–`8`, `"GP3"`, `"GPIO3"` to int 0–8; raises `ValueError` otherwise.
  - `resolve_spi_bus(config, default_cs) -> (spi, chip_select)` — reads `config['spi_bus_kind']` (default `'basic'`), `config['cs']` (default `default_cs`), `config['mcp2210_serial']` (default `''`); raises `ValueError` on unknown kind or unknown board pin.
  - Module globals `_SPI_CS_BOARD_PINS` (dict `GPIOn`/`Dn` → `Dn`) and `_MCP2210_CACHE` (dict).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mcp2210_probe_bus.py` (use spaces here — this is a `tests/` file, not `probes/`; the existing test files use spaces):

```python
import sys
import types
import pytest

import probes.base as base
import mcp2210


# --- _gp_index ---

def test_gp_index_parses_all_forms():
    assert base._gp_index(3) == 3
    assert base._gp_index("3") == 3
    assert base._gp_index("GP3") == 3
    assert base._gp_index("GPIO3") == 3


def test_gp_index_rejects_out_of_range():
    with pytest.raises(ValueError):
        base._gp_index(9)


def test_gp_index_rejects_non_numeric():
    with pytest.raises(ValueError):
        base._gp_index("nope")


# --- resolve_mcp2210 caching ---

def test_resolve_mcp2210_caches_per_serial(monkeypatch):
    base._MCP2210_CACHE.clear()
    created = []

    class FakeMCP:
        def __init__(self, serial=None):
            created.append(serial)
            self.serial = serial

    monkeypatch.setattr(mcp2210, "MCP2210", FakeMCP)
    a = base.resolve_mcp2210(None)
    b = base.resolve_mcp2210("")        # same canonical key as None
    c = base.resolve_mcp2210(None)
    assert a is b is c                  # one shared instance
    assert created == [None]            # constructed exactly once
    d = base.resolve_mcp2210("ABC")
    assert d is not a
    assert created == [None, "ABC"]
    base._MCP2210_CACHE.clear()


# --- resolve_spi_bus: mcp2210 path ---

def test_resolve_spi_bus_mcp2210(monkeypatch):
    class FakeMCP:
        spi = "SPIBUS"
        def digital_inout(self, n):
            return ("CS", n)

    monkeypatch.setattr(base, "resolve_mcp2210", lambda serial=None: FakeMCP())
    spi, cs = base.resolve_spi_bus(
        {"spi_bus_kind": "mcp2210", "cs": "5"}, default_cs="D6")
    assert spi == "SPIBUS"
    assert cs == ("CS", 5)


# --- resolve_spi_bus: basic path (regression for the GPIOn KeyError bug) ---

def _install_fake_board(monkeypatch):
    fake_board = types.ModuleType("board")
    fake_board.D6 = "BOARD_D6"
    fake_board.SPI = lambda: "BOARD_SPI"
    fake_digitalio = types.ModuleType("digitalio")

    class DigitalInOut:
        def __init__(self, pin):
            self.pin = pin

    fake_digitalio.DigitalInOut = DigitalInOut
    monkeypatch.setitem(sys.modules, "board", fake_board)
    monkeypatch.setitem(sys.modules, "digitalio", fake_digitalio)
    return DigitalInOut


def test_resolve_spi_bus_basic_stored_gpio_value(monkeypatch):
    dio = _install_fake_board(monkeypatch)
    spi, cs = base.resolve_spi_bus(
        {"spi_bus_kind": "basic", "cs": "GPIO6"}, default_cs="D6")
    assert spi == "BOARD_SPI"
    assert isinstance(cs, dio) and cs.pin == "BOARD_D6"


def test_resolve_spi_bus_defaults_to_basic_and_accepts_d_name(monkeypatch):
    dio = _install_fake_board(monkeypatch)
    spi, cs = base.resolve_spi_bus({"cs": "D6"}, default_cs="D6")  # no kind key
    assert spi == "BOARD_SPI"
    assert isinstance(cs, dio) and cs.pin == "BOARD_D6"


def test_resolve_spi_bus_unknown_kind_raises():
    with pytest.raises(ValueError):
        base.resolve_spi_bus({"spi_bus_kind": "frobnicate"}, default_cs="D6")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mcp2210_probe_bus.py -v`
Expected: FAIL — `AttributeError: module 'probes.base' has no attribute '_gp_index'` (helpers not defined yet).

- [ ] **Step 3: Add the helpers to `probes/base.py`**

Insert this block in `probes/base.py` **immediately after** the `resolve_i2c_bus` function and **before** the `## Class Definitions` banner comment. **Use TAB indentation** (the file uses tabs):

```python
'''
*****************************************
 SPI Bus Helpers
*****************************************
'''

# Stored chip-select value -> board pin attribute name. The wizard stores the
# `list_values` entry, which for this field is the BCM name 'GPIOn'; the 'Dn'
# Adafruit name is accepted too so a legacy stored value or an in-code default
# still resolves. 'GPIO6' and 'D6' are the same physical pin (board.D6).
_SPI_CS_BOARD_PINS = {}
for _spi_cs_n in (2, 3, 4, 5, 6, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22,
                  23, 24, 25, 26, 27):
	_SPI_CS_BOARD_PINS[f'GPIO{_spi_cs_n}'] = f'D{_spi_cs_n}'
	_SPI_CS_BOARD_PINS[f'D{_spi_cs_n}'] = f'D{_spi_cs_n}'
del _spi_cs_n

# Cache of opened MCP2210 bridges, keyed by serial. A USB-HID handle can be
# opened only once, so every probe on the same bridge must share one instance.
_MCP2210_CACHE = {}


def resolve_mcp2210(serial=None):
	'''
	Open (and cache) a single MCP2210 USB-to-SPI bridge per serial and return
	the shared instance. The MCP2210 HID handle can be opened only once, so
	probes sharing a bridge must share one instance; the cache guarantees that.
	serial=None or '' selects the first MCP2210 by VID/PID (0x04D8/0x00DE) and is
	cached under one canonical key.
	'''
	key = serial or ''  # None and '' both mean "the first/only bridge"
	if key not in _MCP2210_CACHE:
		from mcp2210 import MCP2210
		_MCP2210_CACHE[key] = MCP2210(serial=serial or None)
	return _MCP2210_CACHE[key]


def _gp_index(cs):
	'''
	Parse an MCP2210 GPIO chip-select spec to an int 0-8. Accepts 0-8, 'GP3', or
	'GPIO3'. Raises ValueError for anything else, so a misconfigured CS fails
	clearly rather than driving the wrong pin.
	'''
	text = str(cs).strip().upper()
	if text.startswith('GPIO'):
		text = text[4:]
	elif text.startswith('GP'):
		text = text[2:]
	if not text.isdigit():
		raise ValueError(f'Invalid MCP2210 chip-select {cs!r}; expected GP0-GP8')
	index = int(text)
	if not 0 <= index <= 8:
		raise ValueError(f'MCP2210 chip-select out of range: {cs!r} (GP0-GP8)')
	return index


def resolve_spi_bus(config, default_cs):
	'''
	Build the (spi, chip_select) pair for an SPI probe from its config dict.
	  spi_bus_kind 'basic'   -> board.SPI() + digitalio.DigitalInOut(board pin)
	  spi_bus_kind 'mcp2210' -> shared MCP2210.spi + mcp.digital_inout(GP index)
	Reads standardized keys: spi_bus_kind (default 'basic'), cs (default
	`default_cs`), mcp2210_serial (default ''). Returns objects ready for an
	adafruit_bus_device / SPIDevice-based sensor constructor. Raises ValueError
	on an unknown spi_bus_kind or an unknown board chip-select. board/digitalio
	are imported lazily so this module imports without Blinka present.
	'''
	kind = config.get('spi_bus_kind', 'basic')
	cs = config.get('cs', default_cs)
	if kind == 'mcp2210':
		mcp = resolve_mcp2210(config.get('mcp2210_serial') or None)
		return mcp.spi, mcp.digital_inout(_gp_index(cs))
	if kind == 'basic':
		import board
		import digitalio
		try:
			pin_attr = _SPI_CS_BOARD_PINS[cs]
		except KeyError:
			raise ValueError(
				f'Unknown SPI chip-select {cs!r} for native board.SPI()')
		return board.SPI(), digitalio.DigitalInOut(getattr(board, pin_attr))
	raise ValueError(
		f'Unknown spi_bus_kind {kind!r}; expected "basic" or "mcp2210"')
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mcp2210_probe_bus.py -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add probes/base.py tests/test_mcp2210_probe_bus.py
git commit -m "feat(probes): shared resolve_spi_bus / resolve_mcp2210 helpers

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Refactor `max31865_adafruit` onto the shared helper

**Files:**
- Modify: `probes/max31865_adafruit.py` (drop `board`/`digitalio`/`LOOKUP_TABLE`; use `resolve_spi_bus`)
- Test: `tests/test_mcp2210_probe_bus.py` (append the probe-wiring test)

**Interfaces:**
- Consumes: `resolve_spi_bus(config, default_cs) -> (spi, chip_select)` from Task 1.
- Produces: `RTDDevice(spi, cs, rtd_nominal=1000, ref_resistor=4300, wires=2)` with `.temperature`/`.resistance`/`.get_status()`; `ReadProbes._init_device` building it from config.

- [ ] **Step 1: Write the failing test (append to `tests/test_mcp2210_probe_bus.py`)**

```python
def test_max31865_init_device_uses_resolver(monkeypatch):
    # Fake the adafruit lib so the probe module imports without hardware.
    fake_ada = types.ModuleType("adafruit_max31865")

    class FakeSensor:
        def __init__(self, spi, cs, rtd_nominal=None, ref_resistor=None, wires=None):
            self.spi = spi
            self.cs = cs
            self.rtd_nominal = rtd_nominal
            self.ref_resistor = ref_resistor
            self.wires = wires

    fake_ada.MAX31865 = FakeSensor
    monkeypatch.setitem(sys.modules, "adafruit_max31865", fake_ada)

    import importlib
    import probes.max31865_adafruit as probe
    importlib.reload(probe)  # bind the fake adafruit_max31865

    captured = {}

    def fake_resolve(config, default_cs):
        captured["config"] = config
        captured["default_cs"] = default_cs
        return ("SPI", "CS")

    monkeypatch.setattr(probe, "resolve_spi_bus", fake_resolve)

    obj = probe.ReadProbes.__new__(probe.ReadProbes)  # bypass heavy base __init__
    obj.device_info = {"config": {
        "spi_bus_kind": "mcp2210", "cs": "5",
        "rtd_nominal": "1000", "ref_resistor": "430", "wires": "3"}}
    obj._init_device()

    assert captured["default_cs"] == "D6"
    assert obj.device.sensor.spi == "SPI"
    assert obj.device.sensor.cs == "CS"
    assert obj.device.sensor.rtd_nominal == 1000
    assert obj.device.sensor.ref_resistor == 430
    assert obj.device.sensor.wires == 3
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_mcp2210_probe_bus.py::test_max31865_init_device_uses_resolver -v`
Expected: FAIL — the current probe imports `board`/`digitalio` at module top (absent), so `importlib.reload(probe)` raises `ModuleNotFoundError: No module named 'board'`.

- [ ] **Step 3: Rewrite `probes/max31865_adafruit.py`**

Replace the file's imports/`LOOKUP_TABLE`/`RTDDevice`/`_init_device` so it reads as below. **Use TAB indentation** (the file uses tabs). Keep the module docstring header and `read_all_ports` unchanged.

Replace lines 28–108 (the imports block through the end of `_init_device`) with:

```python
'''
*****************************************
 Imported Libraries
*****************************************
'''
import logging
import adafruit_max31865
from probes.base import ProbeInterface, resolve_spi_bus

'''
*****************************************
 Class Definitions
*****************************************
'''

class RTDDevice():
	''' MAX31865 Device Based on the Adafruit Module '''
	def __init__(self, spi, cs, rtd_nominal=1000, ref_resistor=4300, wires=2):
		self.wires = wires
		self.rtd_nominal = rtd_nominal
		self.ref_resistor = ref_resistor
		self.status = {}
		self.sensor = adafruit_max31865.MAX31865(
			spi, cs, rtd_nominal=self.rtd_nominal,
			ref_resistor=self.ref_resistor, wires=self.wires)

	@property
	def temperature(self):
		return self.sensor.temperature

	@property
	def resistance(self):
		return self.sensor.resistance

	def get_status(self):
		return self.status

class ReadProbes(ProbeInterface):

	def __init__(self, probe_info, device_info, units):
		super().__init__(probe_info, device_info, units)

	def _init_device(self):
		self.time_delay = 0
		self.device_info['ports'] = ['RTD0']
		config = self.device_info['config']
		spi, cs = resolve_spi_bus(config, default_cs='D6')
		rtd_nominal = int(config.get('rtd_nominal', 1000))
		ref_resistor = int(config.get('ref_resistor', 4300))
		wires = int(config.get('wires', 2))
		self.device = RTDDevice(spi, cs, rtd_nominal, ref_resistor, wires)
```

Leave `read_all_ports` (and everything after it) exactly as it is.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mcp2210_probe_bus.py -v`
Expected: PASS (9 passed — the 8 from Task 1 plus the new probe test).

- [ ] **Step 5: Commit**

```bash
git add probes/max31865_adafruit.py tests/test_mcp2210_probe_bus.py
git commit -m "refactor(probes): max31865_adafruit uses resolve_spi_bus; supports MCP2210

Also fixes a pre-existing CS KeyError (the wizard stores GPIOn but the old
LOOKUP_TABLE was keyed by Dn).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Wizard manifest — expose the SPI bus + CS options

**Files:**
- Modify: `wizard/wizard_manifest.json` (the `modules.probes.max31865_adafruit` entry)
- Test: `tests/test_mcp2210_probe_bus.py` (append a manifest sanity test)

**Interfaces:**
- Consumes: nothing in code; the wizard renders `device_specific.config` items generically and stores the `list_values` entry into `device['config'][label]`.
- Produces: config fields `spi_bus_kind` and `mcp2210_serial`, extended `cs` options, and `mcp2210`/`hid` dependencies that the probe (Task 2) relies on.

- [ ] **Step 1: Write the failing test (append to `tests/test_mcp2210_probe_bus.py`)**

```python
import json
import os


def test_manifest_max31865_has_spi_bus_fields():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    manifest = json.load(open(os.path.join(repo_root, "wizard", "wizard_manifest.json")))
    entry = manifest["modules"]["probes"]["max31865_adafruit"]

    labels = [item["label"] for item in entry["device_specific"]["config"]]
    assert "spi_bus_kind" in labels
    assert "mcp2210_serial" in labels

    kind = next(i for i in entry["device_specific"]["config"]
                if i["label"] == "spi_bus_kind")
    assert kind["list_values"] == ["basic", "mcp2210"]
    assert kind["default"] == "basic"

    cs = next(i for i in entry["device_specific"]["config"] if i["label"] == "cs")
    # GP0-GP8 stored values are appended after the board pins.
    assert all(str(n) in cs["list_values"] for n in range(0, 9))

    deps = " ".join(entry["py_dependencies"])
    assert "mcp2210" in deps
    assert "hid" in deps
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_mcp2210_probe_bus.py::test_manifest_max31865_has_spi_bus_fields -v`
Expected: FAIL — `assert "spi_bus_kind" in labels` (field not present yet).

- [ ] **Step 3: Edit `wizard/wizard_manifest.json`**

In `modules.probes.max31865_adafruit`:

**(a)** Replace its `py_dependencies` value with:
```json
   "py_dependencies": [
    "adafruit-circuitpython-max31865==2.2.24",
    "mcp2210",
    "hid>=1.0.4"
   ],
```

**(b)** In `device_specific.config`, replace the existing `cs` item's `list_values` and `list_labels` arrays so the MCP2210 GP options are appended after the board pins (keep the item's other keys — `label`, `friendly_name`, `type`, `default`, `hidden`):
```json
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
```
Also update that `cs` item's `description` to:
```json
    "description": "SPI Chip Select pin. For Basic (native SPI) pick a board GPIO (D2-D27). For MCP2210 pick the bridge's GP0-GP8.",
```

**(c)** Insert two new config items **immediately after** the `cs` item (and before `rtd_nominal`):
```json
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
```

After editing, confirm the JSON is still valid:
Run: `python -c "import json; json.load(open('wizard/wizard_manifest.json')); print('valid')"`
Expected: `valid`

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_mcp2210_probe_bus.py -v`
Expected: PASS (10 passed).

- [ ] **Step 5: Commit**

```bash
git add wizard/wizard_manifest.json tests/test_mcp2210_probe_bus.py
git commit -m "feat(wizard): MCP2210 SPI bus + CS options for max31865_adafruit

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review notes (for the implementer)

- **CS dispatch:** on the `mcp2210` path the `cs` field carries `0`–`8`; on `basic` it carries `GPIOn`. `resolve_spi_bus` dispatches by `spi_bus_kind` first, so each path only ever parses its own form. A board-pin value on the mcp2210 path (`_gp_index('GPIO6')`) still yields GP6 harmlessly; a GP value on the basic path raises a clear `ValueError`.
- **Shared bus:** two `max31865_adafruit` probes with `spi_bus_kind=mcp2210` and the same (blank) serial share one `MCP2210` via `_MCP2210_CACHE`, but each gets its own CS from a distinct `mcp.digital_inout(n)` — exactly the intended multi-probe-on-one-bridge story.
- **DRY:** all SPI bus/CS logic is in `resolve_spi_bus`; the probe is a thin consumer, so the next SPI probe (MAX31855, MCP3008) is `spi, cs = resolve_spi_bus(config, default_cs=...)` plus the three manifest fields.
- **No `settings.json` change:** `config.get('spi_bus_kind', 'basic')` preserves existing setups.
