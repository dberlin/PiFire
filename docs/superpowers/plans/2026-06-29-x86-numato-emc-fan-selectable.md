# Selectable EMC2101 / EMC2301 Fan Controller (x86 Platform) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the generic x86 grill platform drive fan PWM with either an EMC2101 or an EMC2301, selectable in configuration, with a correct ~25–26 kHz PWM frequency.

**Architecture:** Add a small PiFire-owned `EMC2301` driver that mimics the slice of the Adafruit `EMC2101` interface the platform uses (`manual_fan_speed`, `pwm_frequency`). Rename the chip-specific platform module `grillplat/x86_numato_emc2101.py` to the generic `grillplat/x86_numato.py`, and add a factory that picks the driver from a new `platform.fan_controller` settings group. Fix PWM frequency so it is actually applied to the chip at init.

**Tech Stack:** Python 3, pytest, Adafruit CircuitPython libraries (`adafruit_emc2101`, `adafruit_bus_device`, `adafruit_extended_bus`), Numato USB relay driver.

**Spec:** `docs/superpowers/specs/2026-06-29-x86-numato-emc-fan-selectable-design.md`

## Global Constraints

- **Python files use TAB indentation** (repo convention) — every code block below must be tab-indented, not spaces.
- **Hard rename, no migration:** the old module name `x86_numato_emc2101` and the old `platform.emc2101` settings group are removed outright; existing installs reconfigure via the wizard.
- **PWM frequency source is the existing global** `settings['pwm']['frequency']` (default `25000`), passed to the platform as `config['frequency']`. Do **not** add a new per-platform frequency setting.
- **Chip-agnostic platform code:** the platform sets `self.emc.manual_fan_speed`; only the factory and `set_pwm_frequency` know which chip is present.
- Run tests with the project venv: `.venv/bin/python -m pytest`.
- Default I2C addresses: EMC2101 = `0x4C`, EMC2301 = `0x2F`.

---

### Task 1: EMC2301 driver

**Files:**
- Create: `grillplat/emc2301.py`
- Test: `tests/test_emc2301.py`
- Modify: `pyproject.toml` (add direct `adafruit-circuitpython-busdevice` dependency)

**Interfaces:**
- Consumes: nothing (leaf module).
- Produces: `class EMC2301(i2c_bus, address=0x2F)` with read/write property `manual_fan_speed` (float percent 0–100, raises `ValueError` out of range) and read/write property `pwm_frequency` (Hz). Imported later as `from grillplat.emc2301 import EMC2301`.

Register facts (Microchip EMC2301/2/3/5 DS20006532A): Configuration `0x20` (bit6 `DIS_TO` 1=SMBus timeout disabled, bit5 `WD_EN` 1=watchdog continuous), PWM Base Frequency select `0x2D` (00=26 kHz, 01=19.531 kHz, 10=4.882 kHz, 11=2.441 kHz), Fan Setting `0x30` (8-bit direct PWM duty, default direct mode since the RPM algorithm is off at power-on), PWM Divide `0x31` (default `0x01`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_emc2301.py`:

```python
from unittest import mock

import pytest


class FakeI2C:
	"""In-memory stand-in for an Adafruit I2CDevice: stores a register map and
	honors the context-manager + write / write_then_readinto protocol the driver
	uses."""

	def __init__(self):
		self.registers = {}

	def __enter__(self):
		return self

	def __exit__(self, *exc):
		return False

	def write(self, data):
		# Register writes are two bytes: [register, value].
		if len(data) == 2:
			self.registers[data[0]] = data[1]

	def write_then_readinto(self, out_buf, in_buf):
		in_buf[0] = self.registers.get(out_buf[0], 0)


def _build_emc(seed=None):
	"""Construct an EMC2301 with a FakeI2C, optionally pre-seeding registers
	before __init__ runs. Returns (emc, fake)."""
	import grillplat.emc2301 as mod

	fake = FakeI2C()
	if seed:
		fake.registers.update(seed)
	with mock.patch.object(mod, 'I2CDevice', return_value=fake):
		emc = mod.EMC2301(object(), address=0x2F)
	return emc, fake


def test_init_disables_timeout_and_continuous_watchdog():
	_, fake = _build_emc()
	# DIS_TO (bit6) set, WD_EN (bit5) clear.
	assert fake.registers[0x20] & 0x40 == 0x40
	assert fake.registers[0x20] & 0x20 == 0x00


def test_init_preserves_other_config_bits():
	# 0xAA has unrelated bits set; init must keep them, set DIS_TO, clear WD_EN.
	_, fake = _build_emc(seed={0x20: 0xAA})
	assert fake.registers[0x20] == 0xCA


def test_init_sets_26khz_base_divide_one_and_fan_off():
	_, fake = _build_emc()
	assert fake.registers[0x2D] == 0x00  # 26 kHz base
	assert fake.registers[0x31] == 0x01  # divide by 1
	assert fake.registers[0x30] == 0x00  # fan stopped


def test_manual_fan_speed_sets_fan_register():
	emc, fake = _build_emc()
	emc.manual_fan_speed = 100
	assert fake.registers[0x30] == 255
	emc.manual_fan_speed = 20
	assert fake.registers[0x30] == 51
	emc.manual_fan_speed = 0
	assert fake.registers[0x30] == 0


def test_manual_fan_speed_reads_back_percent():
	emc, fake = _build_emc()
	fake.registers[0x30] = 255
	assert emc.manual_fan_speed == 100.0
	fake.registers[0x30] = 51
	assert emc.manual_fan_speed == 20.0


def test_manual_fan_speed_out_of_range_raises():
	emc, _ = _build_emc()
	with pytest.raises(ValueError):
		emc.manual_fan_speed = 150
	with pytest.raises(ValueError):
		emc.manual_fan_speed = -1


def test_pwm_frequency_maps_to_nearest_base():
	emc, fake = _build_emc()
	emc.pwm_frequency = 25000  # nearest selectable base is 26 kHz
	assert fake.registers[0x2D] == 0x00
	assert fake.registers[0x31] == 0x01
	assert emc.pwm_frequency == 26000.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_emc2301.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'grillplat.emc2301'`.

- [ ] **Step 3: Write the driver**

Create `grillplat/emc2301.py`:

```python
#!/usr/bin/env python3

# *****************************************
# PiFire EMC2301 Fan Controller Driver
# *****************************************
#
# Description: Minimal driver for the Microchip EMC2301 SMBus PWM fan
#   controller. There is no Adafruit library for the EMC2301, so this class
#   mimics the slice of the Adafruit EMC2101 interface the x86 platform uses
#   (`manual_fan_speed`, `pwm_frequency`) over the same I2C bus objects.
#
# *****************************************

from adafruit_bus_device.i2c_device import I2CDevice

# Register addresses (Microchip EMC2301/2/3/5 DS20006532A).
_REG_CONFIG = 0x20  # Configuration
_REG_PWM_BASE_FREQ = 0x2D  # PWM base frequency select
_REG_FAN_SETTING = 0x30  # Direct PWM duty (0x00-0xFF)
_REG_PWM_DIVIDE = 0x31  # PWM divide ratio

# Configuration register bits.
_CONFIG_DIS_TO = 0x40  # bit 6: 1 = SMBus timeout disabled
_CONFIG_WD_EN = 0x20  # bit 5: 1 = watchdog runs continuously

# PWM base frequency: Hz -> 0x2D register value.
_BASE_FREQS = {26000: 0x00, 19531: 0x01, 4882: 0x02, 2441: 0x03}
_BASE_VALUE_TO_HZ = {value: hz for hz, value in _BASE_FREQS.items()}

_DEFAULT_ADDRESS = 0x2F
_MAX_DUTY = 0xFF


class EMC2301:
	def __init__(self, i2c_bus, address=_DEFAULT_ADDRESS):
		self.i2c_device = I2CDevice(i2c_bus, address)
		# Disable the SMBus timeout (DIS_TO=1) and keep the watchdog out of
		# continuous mode (WD_EN=0) so the fan is never force-ramped to full
		# speed during quiet periods; preserve the other config bits.
		config = self._read_register(_REG_CONFIG)
		config |= _CONFIG_DIS_TO
		config &= ~_CONFIG_WD_EN
		self._write_register(_REG_CONFIG, config)
		# Known 26 kHz output: 26 kHz base, divide by 1. Fan stopped.
		self._write_register(_REG_PWM_BASE_FREQ, _BASE_FREQS[26000])
		self._write_register(_REG_PWM_DIVIDE, 0x01)
		self._write_register(_REG_FAN_SETTING, 0x00)

	def _read_register(self, register):
		result = bytearray(1)
		with self.i2c_device as i2c:
			i2c.write_then_readinto(bytes([register]), result)
		return result[0]

	def _write_register(self, register, value):
		with self.i2c_device as i2c:
			i2c.write(bytes([register, value & 0xFF]))

	@property
	def manual_fan_speed(self):
		raw = self._read_register(_REG_FAN_SETTING)
		return (raw / _MAX_DUTY) * 100.0

	@manual_fan_speed.setter
	def manual_fan_speed(self, percent):
		if not 0 <= percent <= 100:
			raise ValueError('manual_fan_speed must be from 0-100')
		self._write_register(_REG_FAN_SETTING, round((percent / 100.0) * _MAX_DUTY))

	@property
	def pwm_frequency(self):
		base_value = self._read_register(_REG_PWM_BASE_FREQ) & 0x03
		divide = self._read_register(_REG_PWM_DIVIDE) or 1
		base_hz = _BASE_VALUE_TO_HZ.get(base_value, 26000)
		return base_hz / divide

	@pwm_frequency.setter
	def pwm_frequency(self, hz):
		nearest = min(_BASE_FREQS, key=lambda base: abs(base - hz))
		self._write_register(_REG_PWM_BASE_FREQ, _BASE_FREQS[nearest])
		self._write_register(_REG_PWM_DIVIDE, 0x01)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_emc2301.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Add the busdevice dependency**

In `pyproject.toml`, add the direct dependency right after the `adafruit-circuitpython-emc2101` line (the EMC2301 driver imports `adafruit_bus_device` directly; today it is only transitive):

```toml
    "adafruit-circuitpython-busdevice>=5.0.0",
```

- [ ] **Step 6: Commit**

```bash
git add grillplat/emc2301.py tests/test_emc2301.py pyproject.toml
git commit -m "feat(grillplat): add EMC2301 fan controller driver"
```

---

### Task 2: Rename platform module to x86_numato

This is a behavior-preserving rename. The module still reads the `emc2101` config group and uses the base `EMC2101` class; only names and import paths change. After this task the full existing x86 suite still passes.

**Files:**
- Rename: `grillplat/x86_numato_emc2101.py` → `grillplat/x86_numato.py`
- Modify: `wizard.py:187-188`
- Modify: `wizard/wizard_manifest.json` (entry key, `filename`, `current`/`system_type` option keys)
- Modify: `tests/test_x86_outputs.py`, `tests/test_x86_system.py`, `tests/test_x86_fan.py`, `tests/test_x86_ramp.py`, `tests/test_x86_bus_discovery.py`, `tests/test_x86_manifest.py`
- Modify: `common/common.py` (comment text only)

**Interfaces:**
- Produces: importable module `grillplat.x86_numato` with the same `GrillPlatform` class and `find_i2c_bus` / `resolve_i2c_bus` helpers as before.

- [ ] **Step 1: Rename the module file**

```bash
git mv grillplat/x86_numato_emc2101.py grillplat/x86_numato.py
```

- [ ] **Step 2: Update test imports and references**

In each of the six test files, replace every occurrence of `x86_numato_emc2101` with `x86_numato`:
- `import grillplat.x86_numato_emc2101 as mod` → `import grillplat.x86_numato as mod`
- `from grillplat.x86_numato_emc2101 import find_i2c_bus` → `from grillplat.x86_numato import find_i2c_bus`

In `tests/test_x86_manifest.py`, also update the entry key and filename assertion:

```python
def test_x86_platform_entry_present():
	manifest = _manifest()
	entry = manifest['modules']['grillplatform']['x86_numato']
	assert entry['filename'] == 'x86_numato'
	assert 'adafruit-circuitpython-emc2101' in entry['py_dependencies']
```

And in `test_x86_platform_settings_dependencies`, change the lookup key:

```python
	deps = manifest['modules']['grillplatform']['x86_numato']['settings_dependencies']
```

- [ ] **Step 3: Update wizard.py**

In `wizard.py`, replace lines 187-188:

```python
	elif settings['platform']['system_type'] == 'x86_numato':
		settings['modules']['grillplat'] = 'x86_numato'
```

- [ ] **Step 4: Update the manifest entry key and labels**

In `wizard/wizard_manifest.json`, edit the x86 entry (starts at the `"x86_numato_emc2101": {` line):
- Rename the entry key `"x86_numato_emc2101"` → `"x86_numato"`.
- `"filename": "x86_numato_emc2101"` → `"filename": "x86_numato"`.
- In the `current` block options: `"x86_numato_emc2101": "Generic x86 (Numato + EMC2101)"` → `"x86_numato": "Generic x86 (Numato + EMC fan)"`.
- In the `system_type` block options: `"x86_numato_emc2101": "Generic x86 (Numato + EMC2101)"` → `"x86_numato": "Generic x86 (Numato + EMC fan)"`.

(Leave `friendly_name`, `description`, and the `emc2101_*` settings_dependencies as-is for now — Task 5 restructures those.)

- [ ] **Step 5: Update the stale comments in common.py**

In `common/common.py`, the two comments referencing the old module name (around lines 166 and 170) read `# x86_numato_emc2101 platform: ...`. Replace `x86_numato_emc2101` with `x86_numato` in both comment lines. (Leave the `'emc2101'` settings key itself — Task 5 changes that.)

- [ ] **Step 6: Run the full x86 suite + manifest validity**

Run: `.venv/bin/python -m pytest tests/test_x86_outputs.py tests/test_x86_system.py tests/test_x86_fan.py tests/test_x86_ramp.py tests/test_x86_bus_discovery.py tests/test_x86_manifest.py tests/test_emc2301.py -q`
Expected: PASS (all previously-passing tests still pass).

Also confirm the manifest is still valid JSON:
Run: `.venv/bin/python -c "import json; json.load(open('wizard/wizard_manifest.json')); print('ok')"`
Expected: `ok`

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor(grillplat): rename x86_numato_emc2101 module to x86_numato"
```

---

### Task 3: Chip-selection factory + fan_controller config group

Switch the platform to read the new `platform.fan_controller` settings group, select the driver by `chip`, and use `EMC2101_LUT` (so frequency is controllable in Task 4) for the EMC2101.

**Files:**
- Modify: `grillplat/x86_numato.py` (imports + `__init__` config/factory)
- Modify: `tests/test_x86_outputs.py`, `tests/test_x86_system.py`, `tests/test_x86_fan.py`, `tests/test_x86_ramp.py`, `tests/test_x86_bus_discovery.py`

**Interfaces:**
- Consumes: `grillplat.emc2301.EMC2301` (Task 1); `adafruit_emc2101.emc2101_lut.EMC2101_LUT`.
- Produces: `GrillPlatform(config)` where `config['fan_controller'] = {chip, address, i2c_bus_kind, i2c_bus_num}`; instance attribute `self.chip` (`'emc2101'`/`'emc2301'`), `self.emc` (the driver), `self.emc_address` (int).

- [ ] **Step 1: Write/adjust the failing tests**

Add a factory-selection test file `tests/test_x86_factory.py`:

```python
from unittest import mock

import pytest


def _build(chip):
	"""Build the platform with hardware mocked. chip=None means no
	fan_controller config at all, exercising the default."""
	import grillplat.x86_numato as mod

	with (
		mock.patch.object(mod, 'NumatoUSBRelay'),
		mock.patch.object(mod, 'EMC2101_LUT') as emc2101_lut,
		mock.patch.object(mod, 'EMC2301') as emc2301,
		mock.patch.object(mod, 'ExtendedI2C'),
		mock.patch.object(mod, 'busio'),
		mock.patch.object(mod, 'board'),
		mock.patch.object(mod, 'find_i2c_bus', return_value=7),
	):
		config = {} if chip is None else {'fan_controller': {'chip': chip}}
		platform = mod.GrillPlatform(config)
		return platform, emc2101_lut, emc2301


def test_emc2101_is_default_chip():
	# No fan_controller config -> EMC2101 by default.
	platform, emc2101_lut, emc2301 = _build(None)
	emc2101_lut.assert_called_once()
	emc2301.assert_not_called()
	assert platform.chip == 'emc2101'


def test_emc2301_selected_with_default_address():
	platform, emc2101_lut, emc2301 = _build('emc2301')
	emc2301.assert_called_once()
	# Default EMC2301 address is 0x2F when none configured.
	assert emc2301.call_args.kwargs['address'] == 0x2F
	emc2101_lut.assert_not_called()
	assert platform.chip == 'emc2301'


def test_emc2101_default_address_is_0x4c():
	platform, _, _ = _build('emc2101')
	assert platform.emc_address == 0x4C
```

Update the six existing x86 test files: change every `mock.patch.object(mod, 'EMC2101')` to `mock.patch.object(mod, 'EMC2101_LUT')`, and add `mock.patch.object(mod, 'EMC2301')` to each `with` block so the symbol exists. In `tests/test_x86_outputs.py` rename the captured `emc_cls = ...` accordingly:

```python
		mock.patch.object(mod, 'NumatoUSBRelay') as relay_cls,
		mock.patch.object(mod, 'EMC2101_LUT') as emc_cls,
		mock.patch.object(mod, 'EMC2301'),
		mock.patch.object(mod, 'ExtendedI2C') as i2c_cls,
```

In `tests/test_x86_bus_discovery.py`, the `_build_platform` helper must wrap config under `fan_controller` and patch the new symbols:

```python
def _build_platform(fan_cfg):
	import grillplat.x86_numato as mod

	with (
		mock.patch.object(mod, 'NumatoUSBRelay'),
		mock.patch.object(mod, 'EMC2101_LUT'),
		mock.patch.object(mod, 'EMC2301'),
		mock.patch.object(mod, 'ExtendedI2C') as extended_i2c,
		mock.patch.object(mod, 'busio') as busio,
		mock.patch.object(mod, 'board') as board,
		mock.patch.object(mod, 'find_i2c_bus', return_value=7) as find_bus,
	):
		config = {} if fan_cfg is None else {'fan_controller': fan_cfg}
		platform = mod.GrillPlatform(config)
		return platform, extended_i2c, busio, board, find_bus
```

(The five bus-selection test bodies in that file already pass their dicts through `_build_platform`; they now describe the `fan_controller` group. The `find_i2c_bus` unit tests at the bottom are unaffected.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_x86_factory.py -q`
Expected: FAIL — `AttributeError: <module 'grillplat.x86_numato'> does not have the attribute 'EMC2101_LUT'`.

- [ ] **Step 3: Update the module imports**

In `grillplat/x86_numato.py`, replace the EMC2101 import:

```python
from adafruit_emc2101.emc2101_lut import EMC2101_LUT
```

And add, below the existing `from grillplat.numato_usbrelay import NumatoUSBRelay` line:

```python
from grillplat.emc2301 import EMC2301
```

- [ ] **Step 4: Rewrite the config-reading + factory block in `__init__`**

Replace the block that currently reads the `emc2101` config and opens the chip (from `emc_cfg = config.get('emc2101', {}) or {}` through `self.emc = EMC2101(i2c)`) with:

```python
		fan_cfg = config.get('fan_controller', {}) or {}
		self.chip = str(fan_cfg.get('chip', 'emc2101')).lower()

		# I2C bus selection, matching the probe drivers' basic/extended scheme:
		#   'basic'    -> the board's integrated I2C bus (board.SCL/SDA)
		#   'extended' -> a numbered /dev/i2c-N bus, or a USB-to-I2C bridge
		#                 (e.g. a CP2112) discovered by adapter-name match.
		if 'i2c_bus_kind' in fan_cfg:
			self.i2c_bus_kind = fan_cfg['i2c_bus_kind']
		elif 'i2c_bus_match' in fan_cfg:
			# Legacy config (pre basic/extended): the controller lived on a
			# CP2112 bridge, so honor it as an extended bus.
			self.i2c_bus_kind = 'extended'
		else:
			self.i2c_bus_kind = 'basic'
		self.i2c_bus_num = fan_cfg.get('i2c_bus_num', fan_cfg.get('i2c_bus_match', 'CP2112'))

		# Address defaults per chip when unset.
		address = fan_cfg.get('address')
		if address is None:
			address = 0x2F if self.chip == 'emc2301' else 0x4C
		elif isinstance(address, str):
			address = int(address, 16)
		self.emc_address = address

		self.frequency = config.get('frequency', 100)
		self.standalone = config.get('standalone', True)

		# Cached commanded output state (avoids a serial round-trip per poll).
		self._output_state = {'auger': False, 'fan': False, 'igniter': False, 'power': False}
		self._fan_speed_percent = 0

		# Fan ramp control.
		self._ramp_thread = None
		self._ramp_stop = threading.Event()

		# Open the relay board.
		self.relay = NumatoUSBRelay(self.device, baudrate=self.baudrate)

		# Open the fan controller on the configured I2C bus.
		if self.i2c_bus_kind == 'extended':
			i2c = ExtendedI2C(resolve_i2c_bus(self.i2c_bus_num))
		else:
			i2c = busio.I2C(board.SCL, board.SDA)

		if self.chip == 'emc2301':
			self.emc = EMC2301(i2c, address=self.emc_address)
		else:
			self.emc = EMC2101_LUT(i2c)
			# Drive the fan directly from PiFire's control logic, not the
			# chip's internal lookup-table fan curve.
			self.emc.lut_enabled = False

		# Start in a known state: all relays off, fan stopped.
		self.relay.reset()
		self.emc.manual_fan_speed = 0
```

(Note: `self.device` / `self.baudrate` are still set by the unchanged `numato` block above this; leave that block as-is. `self.frequency` keeps the `100` fallback here — Task 4 changes it to `25000` and applies it to the chip.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_x86_factory.py tests/test_x86_outputs.py tests/test_x86_system.py tests/test_x86_fan.py tests/test_x86_ramp.py tests/test_x86_bus_discovery.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add grillplat/x86_numato.py tests/test_x86_factory.py tests/test_x86_outputs.py tests/test_x86_system.py tests/test_x86_fan.py tests/test_x86_ramp.py tests/test_x86_bus_discovery.py
git commit -m "feat(grillplat): select EMC2101/EMC2301 via fan_controller config group"
```

---

### Task 4: Apply the PWM frequency to the chip (~25 kHz / 26 kHz)

Make `set_pwm_frequency` actually configure the chip, apply it at init, default to 25000, and report the requested frequency so `control.py`'s re-apply loop settles.

**Files:**
- Modify: `grillplat/x86_numato.py` (`__init__` fallback + apply call, `set_pwm_frequency`)
- Modify: `tests/test_x86_fan.py` (frequency tests)

**Interfaces:**
- Consumes: `self.chip`, `self.emc` from Task 3.
- Produces: `set_pwm_frequency(frequency=25000)` stores `self.frequency` and configures the chip; `get_output_status()['frequency']` returns the requested value (already wired).

EMC2101 frequency math: with the 360 kHz base clock, `f = 360000 / (2 × PWM_F)` and duty steps `= 2 × PWM_F`. `PWM_F = round(360000 / (2 × hz))`, clamped to 1–31. For 25000 Hz → `PWM_F = 7` (≈25.7 kHz, 14 duty steps).

- [ ] **Step 1: Write the failing tests**

In `tests/test_x86_fan.py`, replace `test_set_pwm_frequency_stored_and_reported` and add coverage. The fixture already mocks `EMC2101_LUT` (from Task 3) and constructs with `{'frequency': 100}` — change that fixture's config to omit `frequency` so the default path is exercised, and add an explicit emc2301 fixture. Concretely, update the fixture and tests:

```python
@pytest.fixture
def platform():
	import grillplat.x86_numato as mod

	with (
		mock.patch.object(mod, 'NumatoUSBRelay'),
		mock.patch.object(mod, 'EMC2101_LUT'),
		mock.patch.object(mod, 'EMC2301'),
		mock.patch.object(mod, 'ExtendedI2C'),
		mock.patch.object(mod, 'busio'),
		mock.patch.object(mod, 'board'),
		mock.patch.object(mod, 'find_i2c_bus', return_value=7),
	):
		config = {'outputs': {'power': 0, 'igniter': 1, 'auger': 2, 'fan': 3}}
		yield mod.GrillPlatform(config)


def test_frequency_defaults_to_25000(platform):
	assert platform.frequency == 25000
	assert platform.get_output_status()['frequency'] == 25000


def test_init_configures_emc2101_for_25khz(platform):
	# EMC2101_LUT is configured for ~25 kHz at init: 360 kHz preset clock,
	# PWM_F = 7, divisor 1.
	platform.emc.set_pwm_clock.assert_called_with(use_preset=False, use_slow=False)
	assert platform.emc.pwm_frequency == 7
	assert platform.emc.pwm_frequency_divisor == 1


def test_set_pwm_frequency_reports_requested_value(platform):
	platform.set_pwm_frequency(26000)
	assert platform.frequency == 26000
	assert platform.get_output_status()['frequency'] == 26000
	# 26 kHz still maps to PWM_F = 7 on the EMC2101.
	assert platform.emc.pwm_frequency == 7


def test_set_pwm_frequency_on_emc2301_passes_hz():
	import grillplat.x86_numato as mod

	with (
		mock.patch.object(mod, 'NumatoUSBRelay'),
		mock.patch.object(mod, 'EMC2101_LUT'),
		mock.patch.object(mod, 'EMC2301'),
		mock.patch.object(mod, 'ExtendedI2C'),
		mock.patch.object(mod, 'busio'),
		mock.patch.object(mod, 'board'),
		mock.patch.object(mod, 'find_i2c_bus', return_value=7),
	):
		platform = mod.GrillPlatform({'fan_controller': {'chip': 'emc2301'}})
	# EMC2301 takes a frequency in Hz directly.
	assert platform.emc.pwm_frequency == 25000
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_x86_fan.py -q`
Expected: FAIL — `test_frequency_defaults_to_25000` (still 100) and the init-config assertions fail.

- [ ] **Step 3: Change the frequency fallback and apply it at init**

In `grillplat/x86_numato.py`, change the fallback:

```python
		self.frequency = config.get('frequency', 25000)
```

And immediately after the `self.emc.manual_fan_speed = 0` line at the end of `__init__`, add:

```python
		# Apply the fan PWM frequency now so the chip is correct immediately,
		# independent of whether control.py later calls set_pwm_frequency.
		self.set_pwm_frequency(self.frequency)
```

- [ ] **Step 4: Rewrite `set_pwm_frequency`**

Replace the existing `set_pwm_frequency` method with:

```python
	def set_pwm_frequency(self, frequency=25000):
		self.logger.debug('set_pwm_frequency: Setting PWM frequency to ' + str(frequency))
		# Report the requested value so control.py's "re-apply if changed"
		# comparison settles even though each chip rounds to its own grid.
		self.frequency = frequency
		try:
			if self.chip == 'emc2301':
				# The EMC2301 driver takes a frequency in Hz.
				self.emc.pwm_frequency = frequency
			else:
				# EMC2101: f = 360 kHz / (2 * PWM_F); PWM_F also sets duty
				# resolution (2 * PWM_F steps). Use the 360 kHz preset clock.
				pwm_f = max(1, min(31, round(360000 / (2 * frequency))))
				self.emc.set_pwm_clock(use_preset=False, use_slow=False)
				self.emc.pwm_frequency_divisor = 1
				self.emc.pwm_frequency = pwm_f
		except (ValueError, OSError, AttributeError) as exc:
			self.logger.warning('set_pwm_frequency: controller rejected frequency: ' + str(exc))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_x86_fan.py tests/test_x86_factory.py tests/test_x86_ramp.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add grillplat/x86_numato.py tests/test_x86_fan.py
git commit -m "fix(grillplat): drive EMC2101/EMC2301 PWM at ~25-26 kHz, apply at init"
```

---

### Task 5: Wizard manifest + default settings

Finish the user-facing wiring: rename/rebind the manifest entry to the `fan_controller` group, add the chip dropdown, update `common.py` defaults, and force `dc_fan` on for this platform (so `control.py` actually exercises PWM duty/frequency control).

**Files:**
- Modify: `common/common.py` (default `platform` settings: `emc2101` group → `fan_controller`)
- Modify: `wizard/wizard_manifest.json` (friendly_name/description, `fan_controller_chip`, rebind I2C/address settings paths)
- Modify: `wizard.py` (set `dc_fan` True for x86)
- Modify: `tests/test_x86_manifest.py`

**Interfaces:**
- Consumes: the `fan_controller` config shape from Task 3.

- [ ] **Step 1: Write the failing manifest tests**

Replace the body of `tests/test_x86_manifest.py` with:

```python
import json
import os


def _manifest():
	path = os.path.join(os.path.dirname(__file__), '..', 'wizard', 'wizard_manifest.json')
	with open(path) as handle:
		return json.load(handle)


def test_x86_platform_entry_present():
	manifest = _manifest()
	entry = manifest['modules']['grillplatform']['x86_numato']
	assert entry['filename'] == 'x86_numato'
	assert 'adafruit-circuitpython-emc2101' in entry['py_dependencies']


def test_x86_platform_settings_dependencies():
	manifest = _manifest()
	deps = manifest['modules']['grillplatform']['x86_numato']['settings_dependencies']
	# Chip selector plus the selectable basic/extended I2C bus and address.
	assert set(deps['fan_controller_chip']['options']) == {'emc2101', 'emc2301'}
	assert deps['fan_controller_chip']['settings'] == ['platform', 'fan_controller', 'chip']
	assert deps['i2c_bus_kind']['settings'] == ['platform', 'fan_controller', 'i2c_bus_kind']
	assert deps['i2c_bus_num']['settings'] == ['platform', 'fan_controller', 'i2c_bus_num']
	assert deps['fan_controller_address']['settings'] == ['platform', 'fan_controller', 'address']
	assert '0x2f' in deps['fan_controller_address']['options']
	assert set(deps['i2c_bus_kind']['options']) == {'basic', 'extended'}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_x86_manifest.py -q`
Expected: FAIL — `KeyError: 'fan_controller_chip'`.

- [ ] **Step 3: Update the default settings in common.py**

In `common/common.py`, replace the `'emc2101': { ... }` default group (the block with `i2c_bus_kind` / `i2c_bus_num` / `address`) with:

```python
		'fan_controller': {  # x86_numato platform: selectable EMC2101/EMC2301 fan PWM controller
			'chip': 'emc2101',  # 'emc2101' or 'emc2301'
			'i2c_bus_kind': 'basic',  # 'basic' = integrated I2C bus (board.SCL/SDA); 'extended' = numbered/bridge bus
			'i2c_bus_num': '1',  # extended only: /dev/i2c-N number or adapter-name match (e.g. 'CP2112')
			'address': '0x4c',  # fan controller I2C address (EMC2101 0x4C / EMC2301 0x2F)
		},
```

- [ ] **Step 4: Update the manifest entry**

In `wizard/wizard_manifest.json`, in the `x86_numato` entry:

Update the friendly name and description:

```json
        "friendly_name": "Generic x86 (Numato USB Relay + EMC fan controller)",
        "filename": "x86_numato",
        "description": "Generic x86 build. Outputs are driven by a Numato USB relay board and fan PWM is generated by a selectable EMC2101 or EMC2301 fan controller on the board's integrated I2C bus (or an extended bus such as a CP2112 USB-to-I2C bridge).",
```

Add a `fan_controller_chip` block immediately before the `numato_device` block:

```json
          "fan_controller_chip": {
            "friendly_name": "Fan Controller Chip",
            "description": "Which fan PWM controller is fitted: EMC2101 (default address 0x4C) or EMC2301 (default address 0x2F).",
            "options": {
              "emc2101": "EMC2101",
              "emc2301": "EMC2301"
            },
            "settings": ["platform", "fan_controller", "chip"]
          },
```

Repoint the `i2c_bus_kind` and `i2c_bus_num` settings arrays from `["platform", "emc2101", ...]` to `["platform", "fan_controller", ...]`:

```json
            "settings": ["platform", "fan_controller", "i2c_bus_kind"]
```
```json
            "settings": ["platform", "fan_controller", "i2c_bus_num"]
```

Replace the `emc2101_address` block with a renamed `fan_controller_address` block that includes the EMC2301 address:

```json
          "fan_controller_address": {
            "friendly_name": "Fan Controller I2C Address",
            "description": "I2C address of the fan controller (EMC2101: 0x4C/0x4D, EMC2301: 0x2F).",
            "options": {
              "0x4c": "0x4C",
              "0x4d": "0x4D",
              "0x2f": "0x2F"
            },
            "settings": ["platform", "fan_controller", "address"]
          },
```

(Optional polish: the `i2c_bus_kind` / `i2c_bus_num` `friendly_name`/`description` still say "EMC2101"; you may generalize them to "fan controller", but it is not required for tests.)

- [ ] **Step 5: Force dc_fan on for the x86 platform**

`control.py` only drives PWM duty and frequency when `settings['platform']['dc_fan']` is True. In `wizard.py`, in the x86 branch added in Task 2, set it:

```python
	elif settings['platform']['system_type'] == 'x86_numato':
		settings['modules']['grillplat'] = 'x86_numato'
		settings['platform']['dc_fan'] = True
```

- [ ] **Step 6: Run tests + validate manifest**

Run: `.venv/bin/python -m pytest tests/test_x86_manifest.py -q`
Expected: PASS.

Run: `.venv/bin/python -c "import json; json.load(open('wizard/wizard_manifest.json')); print('ok')"`
Expected: `ok`

- [ ] **Step 7: Run the full test suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS (no regressions).

- [ ] **Step 8: Commit**

```bash
git add common/common.py wizard/wizard_manifest.json wizard.py tests/test_x86_manifest.py
git commit -m "feat(wizard): expose EMC2101/EMC2301 selection for x86 platform"
```

---

## Notes / Out of Scope

- **EMC2301 PWM output polarity / drive type:** the driver relies on the chip's power-on defaults (direct PWM mode, non-inverted, `0x00`=off). If a specific board needs push-pull vs open-drain or inverted polarity, that is a follow-up — confirm the exact `0x2D`/output-config bits against the datasheet before adding.
- **EMC2301 one-shot watchdog:** Task 1 disables only the *continuous* watchdog (`WD_EN`). The EMC230x one-shot POR watchdog is covered in practice by PiFire's periodic duty writes; suppressing it explicitly (if the datasheet allows) is a follow-up.
- **EMC2101 duty resolution:** at ~25 kHz the EMC2101 has ~14 duty steps (~7% granularity) — an inherent frequency/resolution tradeoff on that chip. The EMC2301 keeps 256 steps at 26 kHz.
```
