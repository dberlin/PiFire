# VL53L4CD Support + VL53L0X CircuitPython Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the VL53L0X hopper-level driver off the unmaintained pimoroni GitHub library onto Adafruit's CircuitPython library, and add a new VL53L4CD hopper-level driver, both sharing a common base class and both configurable via basic/extended I2C bus + address in the wizard.

**Architecture:** A new `distance/_tof_base.py` module holds a `ToFHopperLevel` base class with the polling thread, hopper-percentage calculation, and I2C bus/address resolution (mirroring the `grillplat/x86_numato.py` basic/extended scheme). `distance/vl53l0x.py` and `distance/vl53l4cd.py` become thin subclasses implementing `_open_sensor`/`_read_distance_mm`/`_close_sensor`. The wizard manifest gains a new `vl53l4cd` distance module entry, an updated `vl53l0x` entry (new pip package), and new I2C bus/address fields on every platform block.

**Tech Stack:** Python 3, pytest, Adafruit CircuitPython libraries (`adafruit-circuitpython-vl53l0x`, `adafruit-circuitpython-vl53l4cd`, `adafruit-extended-bus`), `busio`/`board` (Blinka).

**Spec:** `docs/superpowers/specs/2026-07-01-vl53l4cd-vl53l0x-circuitpython-design.md`

## Global Constraints

- **Python files use TAB indentation** (repo convention) — every repo source/test code block below is tab-indented; preserve that exactly when creating the files.
- **Both chips default to I2C address `0x29`** (`41` decimal). The Adafruit drivers' own constructors also default to this, but PiFire always passes an explicit resolved address.
- **No settings migration.** Missing `i2c_bus_kind`/`i2c_bus_num`/`address` keys under `settings['platform']['devices']['distance']` on existing installs must resolve to `'basic'` / `'CP2112'` / chip default, reproducing today's Pi-only, bus-1 behavior exactly.
- **`HopperLevel` class name and constructor signature (`dev_pins, empty=22, full=4, debug=False`) do not change** — `control.py` loads modules via `importlib.import_module(f'distance.{dist_name}')` + `DistanceModule.HopperLevel(...)` and must keep working unmodified.
- Run tests with the project venv: `.venv/bin/python3 -m pytest`.
- No multi-sensor XSHUT/address-reassignment logic — PiFire only *consumes* an already-reassigned address via config, never reassigns one itself.

---

### Task 1: Shared ToF base class + default settings

**Files:**
- Create: `distance/_tof_base.py`
- Test: `tests/test_tof_base.py`
- Modify: `common/common.py:144-147` (add `i2c_bus_kind`/`i2c_bus_num`/`address` to the `distance` devices dict)

**Interfaces:**
- Consumes: `probes.base.resolve_i2c_bus(bus)` (existing, imported as-is — resolves an int/numeric-string bus index or an adapter-name match like `'CP2112'` to an integer bus number).
- Produces: `class ToFHopperLevel` with:
  - `default_address = 0x29` (class attribute, subclasses may override).
  - `__init__(self, dev_pins, empty=22, full=4, debug=False)`.
  - Abstract hooks subclasses must/may implement: `_open_sensor(self, i2c, address)` (must set `self.tof`), `_read_distance_mm(self)` (returns a `float`/`int` distance in mm), `_close_sensor(self)` (no-op default).
  - Public API (unchanged from today's `distance/vl53l0x.py`): `set_level(self, level=100)`, `update_distances(self, empty=22, full=4)`, `get_distances(self)`, `get_level(self, override=False)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tof_base.py`:

```python
import time
from unittest import mock

import pytest


class FakeSensorMixin:
	"""Mixed in ahead of ToFHopperLevel in test subclasses so the shared
	thread/percent-calc/bus-resolution logic can be exercised without a real
	sensor. `reading_mm` is the fixed distance every read returns; `read_delay`
	simulates a slow sensor to exercise the stuck-sensor re-init path."""

	def __init__(self, *args, reading_mm=100, read_delay=0, **kwargs):
		self.open_calls = 0
		self.opened_with = None
		self._reading_mm = reading_mm
		self._read_delay = read_delay
		super().__init__(*args, **kwargs)

	def _open_sensor(self, i2c, address):
		self.open_calls += 1
		self.opened_with = (i2c, address)

	def _read_distance_mm(self):
		if self._read_delay:
			time.sleep(self._read_delay)
		return self._reading_mm


@pytest.fixture
def tof_mod():
	import distance._tof_base as mod

	with (
		mock.patch.object(mod, 'busio'),
		mock.patch.object(mod, 'board'),
		mock.patch.object(mod, 'ExtendedI2C'),
		mock.patch.object(mod, 'resolve_i2c_bus', return_value=7),
	):
		yield mod


def _make_hopper(tof_mod, dev_pins=None, reading_mm=100, empty=22, full=4, read_delay=0):
	class TestHopperLevel(FakeSensorMixin, tof_mod.ToFHopperLevel):
		pass

	return TestHopperLevel(
		dev_pins or {}, empty=empty, full=full, reading_mm=reading_mm, read_delay=read_delay
	)


def _stop(hopper):
	hopper.sensor_thread_active = False
	hopper.sensor_thread.join(timeout=2)


def test_invalid_empty_full_forces_defaults(tof_mod):
	hopper = _make_hopper(tof_mod, empty=4, full=22)
	try:
		assert hopper.empty == 22
		assert hopper.full == 4
	finally:
		_stop(hopper)


def test_reading_at_or_below_full_is_100_percent(tof_mod):
	hopper = _make_hopper(tof_mod, reading_mm=40, empty=22, full=4)  # 4.0cm == full
	try:
		assert hopper.get_level(override=True) == 100
	finally:
		_stop(hopper)


def test_reading_at_empty_is_0_percent(tof_mod):
	hopper = _make_hopper(tof_mod, reading_mm=220, empty=22, full=4)  # 22.0cm == empty
	try:
		assert hopper.get_level(override=True) == 0
	finally:
		_stop(hopper)


def test_reading_above_empty_is_0_percent(tof_mod):
	hopper = _make_hopper(tof_mod, reading_mm=300, empty=22, full=4)  # 30.0cm > empty
	try:
		assert hopper.get_level(override=True) == 0
	finally:
		_stop(hopper)


def test_reading_between_full_and_empty_is_interpolated(tof_mod):
	hopper = _make_hopper(tof_mod, reading_mm=50, empty=22, full=4)  # 5.0cm
	try:
		assert hopper.get_level(override=True) == 94
	finally:
		_stop(hopper)


def test_slow_read_cycle_reinitializes_sensor(tof_mod):
	hopper = _make_hopper(tof_mod, reading_mm=100, read_delay=0.2)  # 3 * 0.2s > 0.5s threshold
	try:
		hopper.get_level(override=True)
		assert hopper.open_calls == 2
	finally:
		_stop(hopper)


def test_basic_bus_uses_board_scl_sda(tof_mod):
	hopper = _make_hopper(tof_mod, dev_pins={})
	try:
		tof_mod.busio.I2C.assert_called_with(tof_mod.board.SCL, tof_mod.board.SDA)
		assert hopper.opened_with[0] is tof_mod.busio.I2C.return_value
	finally:
		_stop(hopper)


def test_extended_bus_resolves_and_uses_extended_i2c(tof_mod):
	hopper = _make_hopper(tof_mod, dev_pins={'distance': {'i2c_bus_kind': 'extended', 'i2c_bus_num': '3'}})
	try:
		tof_mod.resolve_i2c_bus.assert_called_with('3')
		tof_mod.ExtendedI2C.assert_called_with(7)
		assert hopper.opened_with[0] is tof_mod.ExtendedI2C.return_value
	finally:
		_stop(hopper)


def test_address_defaults_to_chip_default(tof_mod):
	hopper = _make_hopper(tof_mod, dev_pins={})
	try:
		assert hopper.opened_with[1] == 0x29
	finally:
		_stop(hopper)


def test_address_override_parses_hex_string(tof_mod):
	hopper = _make_hopper(tof_mod, dev_pins={'distance': {'address': '0x2a'}})
	try:
		assert hopper.opened_with[1] == 0x2a
	finally:
		_stop(hopper)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python3 -m pytest tests/test_tof_base.py -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'distance._tof_base'`

- [ ] **Step 3: Create `distance/_tof_base.py`**

```python
#!/usr/bin/env python3

# *****************************************
# PiFire ToF (Time-of-Flight) Hopper Level Base
# *****************************************
#
# Description: Shared threading / hopper-percentage-calculation / I2C-bus
#   resolution logic for the VL53L0X and VL53L4CD time-of-flight distance
#   sensors. Each sensor module subclasses ToFHopperLevel and implements
#   _open_sensor, _read_distance_mm, and (optionally) _close_sensor.
#
# *****************************************

import threading
import time

import board
import busio
from adafruit_extended_bus import ExtendedI2C

from common import create_logger
from probes.base import resolve_i2c_bus


class ToFHopperLevel:
	default_address = 0x29

	def __init__(self, dev_pins, empty=22, full=4, debug=False):
		self.logger = create_logger('events')
		self.empty = empty  # Empty is greater than distance measured for empty
		self.full = full  # Full is less than or equal to the minimum full distance.
		self.debug = debug
		self.distance_read = 100

		self.event = threading.Event()

		if self.empty <= self.full:
			event = 'ERROR: Invalid Hopper Level Configuration Empty Level <= Full Level (forcing defaults)'
			self.logger.error(event)
			# Set defaults that are valid
			self.empty = 22
			self.full = 4

		distance_pins = (dev_pins or {}).get('distance', {}) or {}
		self.i2c_bus_kind = distance_pins.get('i2c_bus_kind', 'basic')
		self.i2c_bus_num = distance_pins.get('i2c_bus_num', 'CP2112')
		address = distance_pins.get('address')
		if address is None:
			self.address = self.default_address
		elif isinstance(address, str):
			self.address = int(address, 16)
		else:
			self.address = address

		self.__start_sensor()
		# Setup & Start Sensor Loop Thread
		self.sensor_thread_active = True
		self.sensor_thread_read_interval = 60  # Read sensor every 60 seconds
		self.sensor_thread_override = True  # Allow override to do direct reads
		self.sensor_thread = threading.Thread(target=self._sensing_loop)
		self.sensor_thread.start()

	def _open_i2c_bus(self):
		if self.i2c_bus_kind == 'extended':
			return ExtendedI2C(resolve_i2c_bus(self.i2c_bus_num))
		return busio.I2C(board.SCL, board.SDA)

	def __start_sensor(self):
		i2c = self._open_i2c_bus()
		self._open_sensor(i2c, self.address)

	def _open_sensor(self, i2c, address):
		"""Construct the Adafruit driver instance at `address` on `i2c`, start
		ranging if the chip requires it, and set self.tof. Subclasses must
		implement this."""
		raise NotImplementedError

	def _read_distance_mm(self):
		"""Return a single distance reading in millimeters. Subclasses must
		implement this."""
		raise NotImplementedError

	def _close_sensor(self):
		"""Stop ranging / release the sensor. Optional; no-op by default."""
		pass

	def _sensing_loop(self):
		"""This loop should run in a thread so that it does not stall the main control process"""
		sample_time = time.time()
		while self.sensor_thread_active:
			now = time.time()
			if self.sensor_thread_override or (now > sample_time + self.sensor_thread_read_interval):
				# Read the sensor multiple times and average the result
				avg_dist = 0
				start_time = time.time()

				for reading in range(3):
					distance = self._read_distance_mm()
					if distance > 0:
						if avg_dist > 0:
							avg_dist = (avg_dist + distance) / 2
						else:
							avg_dist = distance

				# Convert mm to cm
				avg_dist = avg_dist / 10

				if self.debug:
					event = '* Average Distance Measured: ' + str(avg_dist) + 'cm'
					self.logger.debug(event)

				# If Average Distance is less than the full distance, we are at 100%
				if avg_dist <= self.full:
					level = 100
				# If Average Distance is less than the empty distance, calculate percentage
				elif avg_dist <= self.empty:
					capacity = self.empty - self.full
					adjusted_ratio = (self.empty / capacity) * 100
					level = adjusted_ratio * (1 - (avg_dist / self.empty))
				# If Average Distance is higher than empty distance, report 0 level
				else:
					level = 0

				self.distance_read = int(level)

				# If it took a long time to get sensor data, then the sensor might be having issues
				if (time.time() - start_time) > 0.5:
					self.__start_sensor()  # Attempt re-init of sensor
					event = (
						'Warning: The TOF sensor took longer than normal to get a reading.  Re-initializing the sensor.'
					)
					self.logger.info(event)
				if self.sensor_thread_override:
					self.event.set()
					self.sensor_thread_override = False
				sample_time = time.time()
			time.sleep(1)

	def set_level(self, level=100):
		# Do nothing
		return ()

	def update_distances(self, empty=22, full=4):
		self.empty = empty
		self.full = full

	def get_distances(self):
		levels = {}
		levels['empty'] = self.empty
		levels['full'] = self.full
		return levels

	def get_level(self, override=False):
		"""If override selected, force the sensor thread to update"""
		if override:
			self.sensor_thread_override = True
			self.event.wait(3)  # Wait 3 seconds for sensor to update
			self.event.clear()  # Clear event flag
		return self.distance_read
```

Note: unlike the original pimoroni-based `vl53l0x.py`, there is no manual `time.sleep(timing / 1000000)` between the 3 reads in `_sensing_loop`. The pimoroni library exposed an explicit `get_timing()` value the caller had to sleep for; the Adafruit drivers block internally until each reading completes, so no extra sleep is needed.

- [ ] **Step 4: Add the new default settings keys**

Read `common/common.py:130-160` first to confirm current content, then modify the `distance` dict at line 144-147:

```python
			"distance": {
				"echo": 27,  # HCSR04 Distance Sensor
				"trig": 23,  # HCSR04 Distance Sensor
				"i2c_bus_kind": "basic",  # VL53L0X/VL53L4CD: "basic" | "extended"
				"i2c_bus_num": "CP2112",  # VL53L0X/VL53L4CD: numbered bus or adapter-name match
				"address": None,  # VL53L0X/VL53L4CD: optional I2C address override (hex string or int)
			},
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python3 -m pytest tests/test_tof_base.py -v`
Expected: PASS (10 passed)

- [ ] **Step 6: Commit**

```bash
git add distance/_tof_base.py tests/test_tof_base.py common/common.py
git commit -m "feat(distance): add shared ToF hopper-level base class"
```

---

### Task 2: Migrate VL53L0X to Adafruit CircuitPython library

**Files:**
- Modify (full rewrite): `distance/vl53l0x.py`
- Test: `tests/test_vl53l0x.py`

**Interfaces:**
- Consumes: `distance._tof_base.ToFHopperLevel` (Task 1).
- Produces: `distance.vl53l0x.HopperLevel(ToFHopperLevel)` — same public contract as before (`HopperLevel(dev_pins, empty=22, full=4, debug=False)`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_vl53l0x.py`:

```python
from unittest import mock


def _make_hopper(tof_mod, vl_mod, dev_pins=None, range_value=100):
	with mock.patch.object(vl_mod, 'VL53L0X') as VL53L0X:
		VL53L0X.return_value.range = range_value
		hopper = vl_mod.HopperLevel(dev_pins or {}, empty=22, full=4)
	return hopper, VL53L0X


def _stop(hopper):
	hopper.sensor_thread_active = False
	hopper.sensor_thread.join(timeout=2)


def test_open_sensor_constructs_vl53l0x_at_resolved_address():
	import distance._tof_base as tof_mod
	import distance.vl53l0x as vl_mod

	with (
		mock.patch.object(tof_mod, 'busio'),
		mock.patch.object(tof_mod, 'board'),
	):
		hopper, VL53L0X = _make_hopper(tof_mod, vl_mod)
		try:
			VL53L0X.assert_called_once_with(tof_mod.busio.I2C.return_value, address=0x29)
		finally:
			_stop(hopper)


def test_open_sensor_uses_configured_address():
	import distance._tof_base as tof_mod
	import distance.vl53l0x as vl_mod

	with (
		mock.patch.object(tof_mod, 'busio'),
		mock.patch.object(tof_mod, 'board'),
	):
		hopper, VL53L0X = _make_hopper(tof_mod, vl_mod, dev_pins={'distance': {'address': '0x2a'}})
		try:
			VL53L0X.assert_called_once_with(tof_mod.busio.I2C.return_value, address=0x2a)
		finally:
			_stop(hopper)


def test_read_distance_mm_returns_range_directly():
	import distance._tof_base as tof_mod
	import distance.vl53l0x as vl_mod

	with (
		mock.patch.object(tof_mod, 'busio'),
		mock.patch.object(tof_mod, 'board'),
	):
		hopper, VL53L0X = _make_hopper(tof_mod, vl_mod, range_value=123)
		try:
			assert hopper._read_distance_mm() == 123
		finally:
			_stop(hopper)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python3 -m pytest tests/test_vl53l0x.py -v`
Expected: FAIL with `AttributeError: <module 'distance.vl53l0x' ...> does not have the attribute 'VL53L0X'` (the old file has no `VL53L0X` name to patch)

- [ ] **Step 3: Rewrite `distance/vl53l0x.py`**

Replace the entire file content:

```python
#!/usr/bin/env python3

# *****************************************
# PiFire vl53l0x Interface Library
# *****************************************
#
# Description: This library supports getting the hopper level from the
#   VL53L0X distance sensor, via Adafruit's CircuitPython library.
#
# *****************************************

from adafruit_vl53l0x import VL53L0X

from distance._tof_base import ToFHopperLevel


class HopperLevel(ToFHopperLevel):
	default_address = 0x29

	def _open_sensor(self, i2c, address):
		self.tof = VL53L0X(i2c, address=address)

	def _read_distance_mm(self):
		return self.tof.range
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python3 -m pytest tests/test_vl53l0x.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add distance/vl53l0x.py tests/test_vl53l0x.py
git commit -m "feat(distance): migrate VL53L0X driver to adafruit-circuitpython-vl53l0x"
```

---

### Task 3: Add VL53L4CD driver

**Files:**
- Create: `distance/vl53l4cd.py`
- Test: `tests/test_vl53l4cd.py`

**Interfaces:**
- Consumes: `distance._tof_base.ToFHopperLevel` (Task 1).
- Produces: `distance.vl53l4cd.HopperLevel(ToFHopperLevel)`, loaded by `control.py` when `settings['modules']['dist'] == 'vl53l4cd'`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_vl53l4cd.py`:

```python
from unittest import mock


def _make_hopper(tof_mod, vl_mod, dev_pins=None, distance_cm=10.0):
	with mock.patch.object(vl_mod, 'VL53L4CD') as VL53L4CD:
		VL53L4CD.return_value.data_ready = True
		VL53L4CD.return_value.distance = distance_cm
		hopper = vl_mod.HopperLevel(dev_pins or {}, empty=22, full=4)
	return hopper, VL53L4CD


def _stop(hopper):
	hopper.sensor_thread_active = False
	hopper.sensor_thread.join(timeout=2)


def test_open_sensor_constructs_vl53l4cd_at_resolved_address_and_starts_ranging():
	import distance._tof_base as tof_mod
	import distance.vl53l4cd as vl_mod

	with (
		mock.patch.object(tof_mod, 'busio'),
		mock.patch.object(tof_mod, 'board'),
	):
		hopper, VL53L4CD = _make_hopper(tof_mod, vl_mod)
		try:
			VL53L4CD.assert_called_once_with(tof_mod.busio.I2C.return_value, address=0x29)
			VL53L4CD.return_value.start_ranging.assert_called_once()
		finally:
			_stop(hopper)


def test_open_sensor_uses_configured_address():
	import distance._tof_base as tof_mod
	import distance.vl53l4cd as vl_mod

	with (
		mock.patch.object(tof_mod, 'busio'),
		mock.patch.object(tof_mod, 'board'),
	):
		hopper, VL53L4CD = _make_hopper(tof_mod, vl_mod, dev_pins={'distance': {'address': '0x2a'}})
		try:
			VL53L4CD.assert_called_once_with(tof_mod.busio.I2C.return_value, address=0x2a)
		finally:
			_stop(hopper)


def test_read_distance_mm_converts_cm_to_mm_and_clears_interrupt():
	import distance._tof_base as tof_mod
	import distance.vl53l4cd as vl_mod

	with (
		mock.patch.object(tof_mod, 'busio'),
		mock.patch.object(tof_mod, 'board'),
	):
		hopper, VL53L4CD = _make_hopper(tof_mod, vl_mod, distance_cm=12.5)
		try:
			assert hopper._read_distance_mm() == 125.0
			assert VL53L4CD.return_value.clear_interrupt.call_count >= 1
		finally:
			_stop(hopper)


def test_close_sensor_stops_ranging():
	import distance._tof_base as tof_mod
	import distance.vl53l4cd as vl_mod

	with (
		mock.patch.object(tof_mod, 'busio'),
		mock.patch.object(tof_mod, 'board'),
	):
		hopper, VL53L4CD = _make_hopper(tof_mod, vl_mod)
		try:
			hopper._close_sensor()
			VL53L4CD.return_value.stop_ranging.assert_called_once()
		finally:
			_stop(hopper)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python3 -m pytest tests/test_vl53l4cd.py -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'distance.vl53l4cd'`

- [ ] **Step 3: Create `distance/vl53l4cd.py`**

```python
#!/usr/bin/env python3

# *****************************************
# PiFire vl53l4cd Interface Library
# *****************************************
#
# Description: This library supports getting the hopper level from the
#   VL53L4CD distance sensor, via Adafruit's CircuitPython library.
#
# *****************************************

import time

from adafruit_vl53l4cd import VL53L4CD

from distance._tof_base import ToFHopperLevel


class HopperLevel(ToFHopperLevel):
	default_address = 0x29

	def _open_sensor(self, i2c, address):
		self.tof = VL53L4CD(i2c, address=address)
		self.tof.start_ranging()

	def _read_distance_mm(self):
		while not self.tof.data_ready:
			time.sleep(0.001)
		distance_cm = self.tof.distance
		self.tof.clear_interrupt()
		return distance_cm * 10

	def _close_sensor(self):
		self.tof.stop_ranging()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python3 -m pytest tests/test_vl53l4cd.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add distance/vl53l4cd.py tests/test_vl53l4cd.py
git commit -m "feat(distance): add VL53L4CD hopper-level driver"
```

---

### Task 4: Add Adafruit dependencies

**Files:**
- Modify: `pyproject.toml`
- Modify (generated): `uv.lock`

**Interfaces:**
- Consumes: nothing.
- Produces: `adafruit_vl53l0x` and `adafruit_vl53l4cd` importable in the project venv (needed by Tasks 2/3's tests, which mock these names at the `distance.vl53l0x`/`distance.vl53l4cd` module level but still require the real package to be importable so `from adafruit_vl53l0x import VL53L0X` succeeds at module load time).

- [ ] **Step 1: Add the dependencies**

Read `pyproject.toml:1-34` first to confirm current content, then add two lines to the `dependencies` list (after the existing `adafruit-circuitpython-mcp9600` line, before `gpiozero`):

```toml
    "adafruit-circuitpython-mcp9600>=2.0.10",
    "adafruit-circuitpython-vl53l0x>=3.6.19",
    "adafruit-circuitpython-vl53l4cd>=1.3.6",
    "gpiozero>=2.0.1",
```

- [ ] **Step 2: Update the lockfile and sync the venv**

Run: `uv lock`
Expected: `uv.lock` updates to include `adafruit-circuitpython-vl53l0x`, `adafruit-circuitpython-vl53l4cd`, and no new transitive dependencies beyond what `adafruit-blinka`/`adafruit-circuitpython-busdevice` already provide.

Run: `uv sync`
Expected: the two new packages install into `.venv`.

- [ ] **Step 3: Verify Task 2/3 tests still pass with the real packages installed**

Run: `.venv/bin/python3 -m pytest tests/test_tof_base.py tests/test_vl53l0x.py tests/test_vl53l4cd.py -v`
Expected: PASS (all tests from Tasks 1-3)

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add adafruit-circuitpython-vl53l0x/vl53l4cd dependencies"
```

---

### Task 5: Wizard manifest — distance modules and platform I2C fields

**Files:**
- Modify: `wizard/wizard_manifest.json`
- Create: `static/img/wizard/vl53l4cd.png` (placeholder, copied from the existing `vl53l0x.png`)
- Test: `tests/test_distance_manifest.py`

**Interfaces:**
- Consumes: nothing (data-only JSON change).
- Produces: `manifest['modules']['distance']['vl53l0x']` and `['vl53l4cd']` entries; `manifest['modules']['grillplatform'][<platform>]['settings_dependencies']['device_distance_i2c_bus_kind'/'device_distance_i2c_bus_num'/'device_distance_address']` on all 6 platform blocks (`custom`, `pcb_2.00a`, `pcb_3.01a`, `pcb_pwm`, `pcb_4.x.x`, `x86_numato`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_distance_manifest.py`:

```python
import json
import os


def _manifest():
	path = os.path.join(os.path.dirname(__file__), '..', 'wizard', 'wizard_manifest.json')
	with open(path) as handle:
		return json.load(handle)


def test_vl53l0x_entry_uses_adafruit_circuitpython():
	manifest = _manifest()
	entry = manifest['modules']['distance']['vl53l0x']
	assert entry['py_dependencies'] == ['adafruit-circuitpython-vl53l0x']
	assert entry['apt_dependencies'] == []


def test_vl53l4cd_entry_present():
	manifest = _manifest()
	entry = manifest['modules']['distance']['vl53l4cd']
	assert entry['filename'] == 'vl53l4cd'
	assert entry['py_dependencies'] == ['adafruit-circuitpython-vl53l4cd']
	assert entry['apt_dependencies'] == []
	assert entry['image'] == 'vl53l4cd.png'


def test_all_platforms_have_distance_i2c_fields():
	manifest = _manifest()
	platforms = manifest['modules']['grillplatform']
	for name, entry in platforms.items():
		deps = entry.get('settings_dependencies', {})

		assert 'device_distance_i2c_bus_kind' in deps, name
		assert deps['device_distance_i2c_bus_kind']['settings'] == [
			'platform',
			'devices',
			'distance',
			'i2c_bus_kind',
		]
		assert set(deps['device_distance_i2c_bus_kind']['options']) == {'basic', 'extended'}

		assert 'device_distance_i2c_bus_num' in deps, name
		assert deps['device_distance_i2c_bus_num']['settings'] == [
			'platform',
			'devices',
			'distance',
			'i2c_bus_num',
		]

		assert 'device_distance_address' in deps, name
		assert deps['device_distance_address']['settings'] == [
			'platform',
			'devices',
			'distance',
			'address',
		]
		assert '0x29' in deps['device_distance_address']['options']
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python3 -m pytest tests/test_distance_manifest.py -v`
Expected: FAIL — `KeyError: 'vl53l4cd'` (and the platform-fields test fails too, since none of the new fields exist yet)

- [ ] **Step 3: Insert the platform I2C fields via a one-off script**

This edits 6 near-duplicate blocks scattered through a 4000+ line JSON file; a small script targeting exact, pre-verified line numbers is more reliable than manual text edits (several of the target blocks are byte-identical to each other, so a content-based find-and-replace can't disambiguate them). Run this from the repo root:

```bash
.venv/bin/python3 - <<'PYEOF'
import json
import pathlib

path = pathlib.Path("wizard/wizard_manifest.json")
lines = path.read_text().splitlines(keepends=True)

BLOCK = '''          "device_distance_i2c_bus_kind": {
            "friendly_name": "Distance Sensor I2C Bus Type",
            "description": "Use the board's integrated I2C bus (Basic) or an extended bus such as a USB-to-I2C bridge (Extended). Only applies to I2C-based distance sensors (VL53L0X/VL53L4CD); ignored by the HCSR04.",
            "options": {
              "basic": "Basic (integrated I2C bus)",
              "extended": "Extended (numbered / bridge bus)"
            },
            "settings": ["platform", "devices", "distance", "i2c_bus_kind"]
          },
          "device_distance_i2c_bus_num": {
            "friendly_name": "Distance Sensor Extended I2C Bus",
            "description": "Which bus to use when Distance Sensor I2C Bus Type is Extended. 'CP2112' auto-discovers the CP2112 USB-to-I2C bridge by adapter name (robust to changing bus numbers); a number selects /dev/i2c-N explicitly. Ignored when Basic.",
            "options": {
              "CP2112": "CP2112 (bridge name match)",
              "0": "i2c-0",
              "1": "i2c-1",
              "2": "i2c-2",
              "3": "i2c-3",
              "4": "i2c-4",
              "5": "i2c-5",
              "6": "i2c-6",
              "7": "i2c-7",
              "8": "i2c-8",
              "9": "i2c-9",
              "10": "i2c-10",
              "11": "i2c-11",
              "12": "i2c-12",
              "13": "i2c-13",
              "14": "i2c-14",
              "15": "i2c-15"
            },
            "settings": ["platform", "devices", "distance", "i2c_bus_num"]
          },
          "device_distance_address": {
            "friendly_name": "Distance Sensor I2C Address",
            "description": "I2C address of the VL53L0X/VL53L4CD distance sensor. Both default to 0x29; only change this if the sensor's address has been reassigned externally (e.g. via XSHUT-based remapping for multi-sensor setups).",
            "options": {
              "0x29": "0x29 (default)",
              "0x2a": "0x2A",
              "0x2b": "0x2B",
              "0x2c": "0x2C",
              "0x2d": "0x2D",
              "0x2e": "0x2E",
              "0x2f": "0x2F"
            },
            "settings": ["platform", "devices", "distance", "address"]
          },
'''

# 1-indexed line number AFTER which to insert, one per platform block:
#   1561 = x86_numato   (after "fan_controller_address" field closes)
#   1421 = pcb_4.x.x    (after "device_distance_trig" field closes)
#   1222 = pcb_pwm      (after "device_distance_trig" field closes)
#   1021 = pcb_3.01a    (after "device_distance_trig" field closes)
#    784 = pcb_2.00a    (after "device_distance_trig" field closes)
#    493 = custom       (after "device_distance_trig" field closes)
# Processed highest-to-lowest so earlier insertions don't shift not-yet-processed line numbers.
INSERT_AFTER_LINE = [1561, 1421, 1222, 1021, 784, 493]

for line_no in INSERT_AFTER_LINE:
	assert lines[line_no - 1].strip() == '},', f"line {line_no} was {lines[line_no - 1]!r}, expected '},'"
	lines.insert(line_no, BLOCK)

path.write_text("".join(lines))

# Verify the result is still valid JSON.
with path.open() as handle:
	json.load(handle)
print("OK: wizard_manifest.json is valid JSON after insertion")
PYEOF
```

Expected output: `OK: wizard_manifest.json is valid JSON after insertion`. If the `assert` on any line fails, STOP — it means the file has changed since this plan was written and the line numbers above are stale; re-locate the 6 insertion points (`grep -n '"settings": \["platform", "devices", "distance", "trig"\]' wizard/wizard_manifest.json` for the 5 GPIO platforms, `grep -n '"settings": \["platform", "fan_controller", "address"\]' wizard/wizard_manifest.json` for `x86_numato`) before re-running.

- [ ] **Step 4: Update the `vl53l0x` entry and add the `vl53l4cd` entry**

Read `wizard/wizard_manifest.json` around the `"distance":` module section (search for `"vl53l0x": {` — after Step 3's insertions above, this is still further down the file and unaffected by them) to confirm current content, then use Edit to replace:

Old:
```json
      "vl53l0x": {
        "friendly_name": "VL53L0X Time of Flight Distance Sensor",
        "filename": "vl53l0x",
        "description": "The VL53L0X is a new generation Time-of-Flight (ToF) laser-ranging module and is the hopper sensor of choice for the PiFire project.",
        "default": false,
        "image": "vl53l0x.png",
        "reboot_required": false,
        "py_dependencies": [
          "git+https://github.com/pimoroni/VL53L0X-python.git"
        ],
        "apt_dependencies": ["python3-smbus"],
        "command_list": [],
        "settings_dependencies": {}
      },
```

New:
```json
      "vl53l0x": {
        "friendly_name": "VL53L0X Time of Flight Distance Sensor",
        "filename": "vl53l0x",
        "description": "The VL53L0X is a new generation Time-of-Flight (ToF) laser-ranging module and is the hopper sensor of choice for the PiFire project.",
        "default": false,
        "image": "vl53l0x.png",
        "reboot_required": false,
        "py_dependencies": ["adafruit-circuitpython-vl53l0x"],
        "apt_dependencies": [],
        "command_list": [],
        "settings_dependencies": {}
      },
      "vl53l4cd": {
        "friendly_name": "VL53L4CD Time of Flight Distance Sensor",
        "filename": "vl53l4cd",
        "description": "The VL53L4CD is a newer-generation Time-of-Flight (ToF) laser-ranging module with improved accuracy at short range, a good alternative to the VL53L0X for hopper level sensing.",
        "default": false,
        "image": "vl53l4cd.png",
        "reboot_required": false,
        "py_dependencies": ["adafruit-circuitpython-vl53l4cd"],
        "apt_dependencies": [],
        "command_list": [],
        "settings_dependencies": {}
      },
```

- [ ] **Step 5: Add the placeholder wizard image**

```bash
cp static/img/wizard/vl53l0x.png static/img/wizard/vl53l4cd.png
```

This is a placeholder (identical image to the VL53L0X entry) so the wizard doesn't 404 on a missing image; it should be replaced with real VL53L4CD product photography in a follow-up.

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python3 -m pytest tests/test_distance_manifest.py -v`
Expected: PASS (3 passed)

Also run the full existing wizard-manifest-related test suite to confirm nothing else broke:

Run: `.venv/bin/python3 -m pytest tests/test_x86_manifest.py tests/test_distance_manifest.py -v`
Expected: PASS (all passed)

- [ ] **Step 7: Commit**

```bash
git add wizard/wizard_manifest.json static/img/wizard/vl53l4cd.png tests/test_distance_manifest.py
git commit -m "feat(wizard): add VL53L4CD module and distance-sensor I2C bus/address fields"
```

---

### Task 6: Update README

**Files:**
- Modify: `README.md:49-51`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing (documentation only).

- [ ] **Step 1: Update the pellet-level sensor bullet**

Read `README.md:40-54` first to confirm current content, then replace:

Old:
```markdown
* Pellet Level Sensor Support
	* VL53L0X Time of Flight Sensor (recommended)
	* HCSR04 Ultrasonic Sensor
```

New:
```markdown
* Pellet Level Sensor Support
	* VL53L0X or VL53L4CD Time of Flight Sensor (recommended) - installed via Adafruit's CircuitPython libraries
	* HCSR04 Ultrasonic Sensor
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: mention VL53L4CD support and CircuitPython-based ToF install"
```

---

## Self-Review Notes

- **Spec coverage:** shared base class (Task 1), VL53L0X migration (Task 2), VL53L4CD driver (Task 3), dependencies (Task 4), wizard module entries + platform I2C/address fields + placeholder image (Task 5), README (Task 6). All spec sections have a corresponding task.
- **Type consistency:** `_open_sensor(self, i2c, address)` / `_read_distance_mm(self)` / `_close_sensor(self)` signatures are identical across Task 1 (base), Task 2 (`vl53l0x.py`), and Task 3 (`vl53l4cd.py`). `default_address = 0x29` is set on the base and re-declared on both subclasses per the spec.
- **No placeholders:** every step has complete, runnable code; the one intentional placeholder (`vl53l4cd.png` reusing the VL53L0X image) is explicitly called out as such, matching the spec's Non-Goals.
