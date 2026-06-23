# x86 Numato/EMC2101 Grill Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a PiFire grill platform for generic x86 hardware that controls outputs via the Numato USB relay board and drives fan PWM via an EMC2101 on the CP2112 I2C bridge.

**Architecture:** A new `grillplat/x86_numato_emc2101.py` exposes the standard `GrillPlatform(config)` class. It composes the existing `NumatoUSBRelay` driver (relays for power/igniter/auger/fan) with the Adafruit `adafruit_emc2101` library (fan PWM) reached over `adafruit_extended_bus.ExtendedI2C`. The CP2112 bus number is discovered by name from sysfs. The fan is a DC-fan-style output: a relay gates power, the EMC2101 sets speed.

**Tech Stack:** Python 3, `pyserial` (via existing `NumatoUSBRelay`), `adafruit-circuitpython-emc2101`, `adafruit-extended-bus`, `psutil`, `pytest` (dev), managed with `uv`.

## Global Constraints

- Module file: `grillplat/x86_numato_emc2101.py`; class name `GrillPlatform`.
- Platform contract (methods PiFire calls): `auger_on/off`, `fan_on/off/toggle`, `set_duty_cycle`, `pwm_fan_ramp`, `set_pwm_frequency`, `igniter_on/off`, `power_on/off`, `get_input_status`, `get_output_status`, `cleanup`, and system commands `supported_commands`, `check_throttled`, `check_wifi_quality`, `check_cpu_temp`, `check_alive`, `scan_bluetooth`, `os_info`, `network_info`, `hardware_info`.
- `GrillPlatform.__init__(self, config)` receives `settings['platform']` augmented with `frequency`.
- Relay index defaults: `power=0, igniter=1, auger=2, fan=3`. Numato device default `/dev/ttyACM0`, baud `921600`.
- EMC2101 defaults: I2C bus match string `"CP2112"` (case-insensitive), address `0x4c`.
- Fan duty is **not inverted** (EMC2101 `manual_fan_speed` maps directly to fan speed percent).
- The fan ramp uses `threading.Thread` + `threading.Event` (NOT `gpiozero.GPIOThread` — gpiozero is a Pi-only dependency and is not available on x86).
- All hardware (serial port, I2C bus, EMC2101) is mocked in tests; no hardware in CI. Run tests with `uv run pytest`.
- New file header comment style follows the existing `grillplat/*.py` modules.

---

### Task 1: Dependencies and test scaffolding

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/test_smoke.py`

**Interfaces:**
- Consumes: nothing.
- Produces: a working `uv run pytest` invocation; `adafruit_emc2101` importable; `tests/` on the import path with repo root importable.

- [ ] **Step 1: Add the runtime dependency**

In `pyproject.toml`, add `adafruit-circuitpython-emc2101` to the `dependencies` array (keep alphabetical-ish grouping with the other `adafruit-*` entries):

```toml
    "adafruit-extended-bus>=1.0.2",
    "adafruit-circuitpython-emc2101>=1.0.0",
    "adafruit-circuitpython-mcp9600>=2.0.10",
```

- [ ] **Step 2: Add pytest as a dev dependency**

In `pyproject.toml`, under `[dependency-groups]` `dev`, add pytest:

```toml
[dependency-groups]
dev = [
    "ipython>=9.14.1",
    "pytest>=8.0.0",
]
```

- [ ] **Step 3: Sync the environment**

Run: `uv sync`
Expected: completes successfully; `adafruit_emc2101` and `pytest` are installed.

- [ ] **Step 4: Create the test package and conftest**

Create `tests/__init__.py` (empty file).

Create `tests/conftest.py` so the repo root is importable (the platform module does `from common import ...`):

```python
import os
import sys

# Ensure the repository root is importable so `grillplat`, `common`, etc. resolve.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
```

- [ ] **Step 5: Write a smoke test**

Create `tests/test_smoke.py`:

```python
def test_emc2101_library_importable():
    import adafruit_emc2101  # noqa: F401


def test_numato_driver_importable():
    from grillplat.numato_usbrelay import NumatoUSBRelay  # noqa: F401
```

- [ ] **Step 6: Run the smoke test**

Run: `uv run pytest tests/test_smoke.py -v`
Expected: 2 passed.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock tests/__init__.py tests/conftest.py tests/test_smoke.py
git commit -m "test: add pytest scaffolding and emc2101 dependency for x86 platform"
```

---

### Task 2: CP2112 I2C bus discovery helper

**Files:**
- Create: `grillplat/x86_numato_emc2101.py`
- Create: `tests/test_x86_bus_discovery.py`

**Interfaces:**
- Consumes: nothing.
- Produces: module-level function `find_i2c_bus(match='CP2112', devices_path='/sys/bus/i2c/devices')` returning `int` (bus number); raises `RuntimeError` on zero or multiple matches. Module-level exception class is not needed — use `RuntimeError`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_x86_bus_discovery.py`:

```python
import pytest


def _make_bus(tmp_path, index, name):
    bus_dir = tmp_path / f"i2c-{index}"
    bus_dir.mkdir()
    (bus_dir / "name").write_text(name + "\n")
    return bus_dir


def test_find_i2c_bus_single_match(tmp_path):
    from grillplat.x86_numato_emc2101 import find_i2c_bus
    _make_bus(tmp_path, 0, "Synopsys DesignWare I2C adapter")
    _make_bus(tmp_path, 7, "CP2112 SMBus Bridge on hidraw0")
    assert find_i2c_bus(match="CP2112", devices_path=str(tmp_path)) == 7


def test_find_i2c_bus_case_insensitive(tmp_path):
    from grillplat.x86_numato_emc2101 import find_i2c_bus
    _make_bus(tmp_path, 3, "cp2112 smbus bridge")
    assert find_i2c_bus(match="CP2112", devices_path=str(tmp_path)) == 3


def test_find_i2c_bus_no_match_raises(tmp_path):
    from grillplat.x86_numato_emc2101 import find_i2c_bus
    _make_bus(tmp_path, 0, "Synopsys DesignWare I2C adapter")
    with pytest.raises(RuntimeError):
        find_i2c_bus(match="CP2112", devices_path=str(tmp_path))


def test_find_i2c_bus_multiple_matches_raises(tmp_path):
    from grillplat.x86_numato_emc2101 import find_i2c_bus
    _make_bus(tmp_path, 4, "CP2112 SMBus Bridge on hidraw0")
    _make_bus(tmp_path, 5, "CP2112 SMBus Bridge on hidraw1")
    with pytest.raises(RuntimeError):
        find_i2c_bus(match="CP2112", devices_path=str(tmp_path))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_x86_bus_discovery.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'grillplat.x86_numato_emc2101'`.

- [ ] **Step 3: Create the module with the helper**

Create `grillplat/x86_numato_emc2101.py`:

```python
#!/usr/bin/env python3

# *****************************************
# PiFire Generic x86 Platform Interface Library
# *****************************************
#
# Description: This library controls PiFire outputs on generic x86 hardware.
#   Relays (power, igniter, auger, fan) are driven by a Numato USB relay board
#   over a serial (tty) device.  Fan PWM is generated by an EMC2101 fan
#   controller reached over the I2C bus exposed by a CP2112 USB-to-I2C bridge.
#
#   The fan is wired DC-fan-style: a relay gates fan power and the EMC2101
#   sets the fan speed.
#
# *****************************************

"""
	==============================
	  Imported Libraries
	==============================
"""

import glob
import os
import threading

from common import is_float, create_logger, get_os_info

from adafruit_extended_bus import ExtendedI2C
from adafruit_emc2101 import EMC2101

from grillplat.numato_usbrelay import NumatoUSBRelay


"""
	==============================
	  Module Helpers
	==============================
"""

def find_i2c_bus(match='CP2112', devices_path='/sys/bus/i2c/devices'):
	"""
	Find the integer i2c bus number whose adapter name contains `match`.

	Scans `<devices_path>/i2c-*/name`.  Raises RuntimeError if zero or more
	than one adapter matches, so the caller can fail clearly.
	"""
	match_lower = match.lower()
	found = []
	for bus_dir in glob.glob(os.path.join(devices_path, 'i2c-*')):
		name_file = os.path.join(bus_dir, 'name')
		try:
			with open(name_file) as handle:
				name = handle.read().strip()
		except OSError:
			continue
		if match_lower in name.lower():
			# Bus number is the trailing integer of the i2c-N directory name.
			try:
				bus_num = int(os.path.basename(bus_dir).split('-')[-1])
			except ValueError:
				continue
			found.append(bus_num)

	if len(found) == 1:
		return found[0]
	if not found:
		raise RuntimeError(f'No i2c adapter found matching {match!r} under {devices_path}')
	raise RuntimeError(f'Multiple i2c adapters match {match!r}: {sorted(found)}')
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_x86_bus_discovery.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add grillplat/x86_numato_emc2101.py tests/test_x86_bus_discovery.py
git commit -m "feat: add CP2112 i2c bus discovery for x86 platform"
```

---

### Task 3: GrillPlatform construction and relay output control

**Files:**
- Modify: `grillplat/x86_numato_emc2101.py`
- Create: `tests/test_x86_outputs.py`

**Interfaces:**
- Consumes: `find_i2c_bus`, `NumatoUSBRelay`, `EMC2101`, `ExtendedI2C` (all referenced by module-level name so tests can patch them).
- Produces: class `GrillPlatform(config)` with `auger_on/off()`, `igniter_on/off()`, `power_on/off()`, `get_input_status()`, and a partial `get_output_status()` returning `auger/igniter/power/fan` booleans. Relay indices come from `config['outputs']` with defaults `{power:0, igniter:1, auger:2, fan:3}`. Commanded output state is cached on the instance in `self._output_state`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_x86_outputs.py`:

```python
from unittest import mock

import pytest


@pytest.fixture
def platform():
    """A GrillPlatform with all hardware mocked out."""
    import grillplat.x86_numato_emc2101 as mod
    with mock.patch.object(mod, 'NumatoUSBRelay') as relay_cls, \
         mock.patch.object(mod, 'EMC2101') as emc_cls, \
         mock.patch.object(mod, 'ExtendedI2C') as i2c_cls, \
         mock.patch.object(mod, 'find_i2c_bus', return_value=7):
        config = {
            'outputs': {'power': 0, 'igniter': 1, 'auger': 2, 'fan': 3},
            'frequency': 100,
        }
        plat = mod.GrillPlatform(config)
        plat._relay_cls = relay_cls
        plat._emc_cls = emc_cls
        plat._i2c_cls = i2c_cls
        yield plat


def test_init_opens_relay_and_emc(platform):
    # Relay opened on the default device; EMC2101 constructed on discovered bus.
    platform._relay_cls.assert_called_once()
    assert platform._relay_cls.call_args.args[0] == '/dev/ttyACM0'
    platform._i2c_cls.assert_called_once_with(7)
    platform._emc_cls.assert_called_once()


def test_auger_on_off_uses_mapped_relay(platform):
    platform.auger_on()
    platform.relay.relay_on.assert_called_with(2)
    platform.auger_off()
    platform.relay.relay_off.assert_called_with(2)


def test_power_and_igniter_use_mapped_relays(platform):
    platform.power_on()
    platform.relay.relay_on.assert_called_with(0)
    platform.igniter_on()
    platform.relay.relay_on.assert_called_with(1)


def test_get_output_status_reflects_cached_state(platform):
    platform.auger_on()
    platform.igniter_on()
    status = platform.get_output_status()
    assert status['auger'] is True
    assert status['igniter'] is True
    assert status['power'] is False
    assert status['fan'] is False


def test_get_input_status_is_false_when_standalone(platform):
    assert platform.get_input_status() is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_x86_outputs.py -v`
Expected: FAIL — `AttributeError: module 'grillplat.x86_numato_emc2101' has no attribute 'GrillPlatform'`.

- [ ] **Step 3: Add the GrillPlatform class with construction and relay control**

Append to `grillplat/x86_numato_emc2101.py`:

```python
"""
	==============================
	  Class Definition
	==============================
"""

# Default Numato relay index for each PiFire output.
_DEFAULT_OUTPUTS = {'power': 0, 'igniter': 1, 'auger': 2, 'fan': 3}


class GrillPlatform:

	def __init__(self, config):
		self.logger = create_logger('control')
		self.config = config

		outputs = config.get('outputs', {}) or {}
		self.relay_map = {
			name: int(outputs.get(name, default))
			for name, default in _DEFAULT_OUTPUTS.items()
		}

		numato_cfg = config.get('numato', {}) or {}
		self.device = numato_cfg.get('device', '/dev/ttyACM0')
		self.baudrate = int(numato_cfg.get('baudrate', 921600))

		emc_cfg = config.get('emc2101', {}) or {}
		self.i2c_bus_match = emc_cfg.get('i2c_bus_match', 'CP2112')
		address = emc_cfg.get('address', 0x4c)
		if isinstance(address, str):
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

		# Open the EMC2101 on the CP2112 bridge bus.
		bus_num = find_i2c_bus(match=self.i2c_bus_match)
		self.emc = EMC2101(ExtendedI2C(bus_num))

		# Start in a known state: all relays off, fan stopped.
		self.relay.reset()
		self.emc.manual_fan_speed = 0

	# MARK: Output control
	def _set_output(self, name, state):
		# Call relay_on/relay_off directly (not relay_set) so the action is
		# explicit and observable when the relay driver is mocked in tests.
		index = self.relay_map[name]
		if state:
			self.relay.relay_on(index)
		else:
			self.relay.relay_off(index)
		self._output_state[name] = state

	def auger_on(self):
		self.logger.debug('auger_on: Turning on auger')
		self._set_output('auger', True)

	def auger_off(self):
		self.logger.debug('auger_off: Turning off auger')
		self._set_output('auger', False)

	def igniter_on(self):
		self.logger.debug('igniter_on: Turning on igniter')
		self._set_output('igniter', True)

	def igniter_off(self):
		self.logger.debug('igniter_off: Turning off igniter')
		self._set_output('igniter', False)

	def power_on(self):
		self.logger.debug('power_on: Powering on grill platform')
		self._set_output('power', True)

	def power_off(self):
		self.logger.debug('power_off: Powering off grill platform')
		self._set_output('power', False)

	def get_input_status(self):
		# No selector/shutdown inputs on this platform.
		return False

	def get_output_status(self):
		self.current = {
			'auger': self._output_state['auger'],
			'igniter': self._output_state['igniter'],
			'power': self._output_state['power'],
			'fan': self._output_state['fan'],
		}
		return self.current
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_x86_outputs.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add grillplat/x86_numato_emc2101.py tests/test_x86_outputs.py
git commit -m "feat: add GrillPlatform construction and relay output control for x86 platform"
```

---

### Task 4: Fan and PWM control

**Files:**
- Modify: `grillplat/x86_numato_emc2101.py`
- Create: `tests/test_x86_fan.py`

**Interfaces:**
- Consumes: `GrillPlatform` from Task 3; `self.relay`, `self.emc`, `self.relay_map['fan']`, `self._output_state`, `self._fan_speed_percent`, `self.frequency`.
- Produces: `fan_on(fan_speed_percent=100)`, `fan_off()`, `fan_toggle()`, `set_duty_cycle(fan_speed_percent, override_ramping=True)`, `set_pwm_frequency(frequency=100)`, and an extended `get_output_status()` that also returns `pwm` (current fan %) and `frequency`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_x86_fan.py`:

```python
from unittest import mock

import pytest


@pytest.fixture
def platform():
    import grillplat.x86_numato_emc2101 as mod
    with mock.patch.object(mod, 'NumatoUSBRelay'), \
         mock.patch.object(mod, 'EMC2101'), \
         mock.patch.object(mod, 'ExtendedI2C'), \
         mock.patch.object(mod, 'find_i2c_bus', return_value=7):
        config = {'outputs': {'power': 0, 'igniter': 1, 'auger': 2, 'fan': 3}, 'frequency': 100}
        yield mod.GrillPlatform(config)


def test_fan_on_closes_relay_and_sets_speed(platform):
    platform.fan_on(60)
    platform.relay.relay_on.assert_called_with(3)
    assert platform.emc.manual_fan_speed == 60
    assert platform.get_output_status()['fan'] is True


def test_fan_off_zeroes_speed_and_opens_relay(platform):
    platform.fan_on(60)
    platform.fan_off()
    platform.relay.relay_off.assert_called_with(3)
    assert platform.emc.manual_fan_speed == 0
    assert platform.get_output_status()['fan'] is False


def test_set_duty_cycle_sets_manual_fan_speed_directly(platform):
    platform.set_duty_cycle(42)
    # No inversion: requested percent maps directly to EMC2101 duty.
    assert platform.emc.manual_fan_speed == 42
    assert platform.get_output_status()['pwm'] == 42


def test_fan_toggle_flips_state(platform):
    assert platform.get_output_status()['fan'] is False
    platform.fan_toggle()
    assert platform.get_output_status()['fan'] is True
    platform.fan_toggle()
    assert platform.get_output_status()['fan'] is False


def test_set_pwm_frequency_stored_and_reported(platform):
    platform.set_pwm_frequency(30)
    assert platform.frequency == 30
    assert platform.get_output_status()['frequency'] == 30


def test_get_output_status_includes_pwm_and_frequency(platform):
    platform.fan_on(75)
    status = platform.get_output_status()
    assert status['pwm'] == 75
    assert status['frequency'] == 100
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_x86_fan.py -v`
Expected: FAIL — `AttributeError: 'GrillPlatform' object has no attribute 'fan_on'`.

- [ ] **Step 3: Add fan/PWM methods and extend get_output_status**

Add these methods to `GrillPlatform` (place after the output-control methods, before `get_output_status`):

```python
	# MARK: Fan / PWM control
	def fan_on(self, fan_speed_percent=100):
		self.logger.debug('fan_on: Enabling fan power and setting speed to ' + str(fan_speed_percent))
		self.relay.relay_on(self.relay_map['fan'])
		self._output_state['fan'] = True
		self._stop_ramp()
		self.set_duty_cycle(fan_speed_percent)

	def fan_off(self):
		self.logger.debug('fan_off: Stopping fan and removing power')
		self._stop_ramp()
		self.emc.manual_fan_speed = 0
		self._fan_speed_percent = 0
		self.relay.relay_off(self.relay_map['fan'])
		self._output_state['fan'] = False

	def fan_toggle(self):
		if self._output_state['fan']:
			self.fan_off()
		else:
			self.fan_on()

	def set_duty_cycle(self, fan_speed_percent, override_ramping=True):
		# Called by control.py (override_ramping=True) and by the ramp thread
		# (override_ramping=False so it does not stop the thread it runs in).
		if override_ramping:
			self._stop_ramp()
		# EMC2101 duty maps directly to fan speed percent (no inversion).
		self.emc.manual_fan_speed = fan_speed_percent
		self._fan_speed_percent = fan_speed_percent

	def set_pwm_frequency(self, frequency=100):
		self.logger.debug('set_pwm_frequency: Setting PWM frequency to ' + str(frequency))
		self.frequency = frequency
		# Best-effort: apply to the EMC2101 if the library exposes the property.
		if hasattr(self.emc, 'pwm_frequency'):
			try:
				self.emc.pwm_frequency = frequency
			except (ValueError, OSError) as exc:
				self.logger.warning('set_pwm_frequency: EMC2101 rejected frequency: ' + str(exc))
```

Then replace `get_output_status` to also report `pwm` and `frequency`:

```python
	def get_output_status(self):
		self.current = {
			'auger': self._output_state['auger'],
			'igniter': self._output_state['igniter'],
			'power': self._output_state['power'],
			'fan': self._output_state['fan'],
			'pwm': self._fan_speed_percent,
			'frequency': self.frequency,
		}
		return self.current
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_x86_fan.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add grillplat/x86_numato_emc2101.py tests/test_x86_fan.py
git commit -m "feat: add EMC2101 fan and PWM control for x86 platform"
```

---

### Task 5: Fan ramp (Smoke Plus)

**Files:**
- Modify: `grillplat/x86_numato_emc2101.py`
- Create: `tests/test_x86_ramp.py`

**Interfaces:**
- Consumes: `set_duty_cycle`, `self.relay`, `self._ramp_thread`, `self._ramp_stop` from earlier tasks.
- Produces: `pwm_fan_ramp(on_time=5, min_duty_cycle=20, max_duty_cycle=100)`, plus private `_start_ramp`, `_stop_ramp`, `_ramp_device`. Ramp uses `threading.Thread` + `threading.Event` (no gpiozero). When a ramp finishes or is stopped, the fan ends at `max_duty_cycle`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_x86_ramp.py`:

```python
from unittest import mock

import pytest


@pytest.fixture
def platform():
    import grillplat.x86_numato_emc2101 as mod
    with mock.patch.object(mod, 'NumatoUSBRelay'), \
         mock.patch.object(mod, 'EMC2101'), \
         mock.patch.object(mod, 'ExtendedI2C'), \
         mock.patch.object(mod, 'find_i2c_bus', return_value=7):
        config = {'outputs': {'power': 0, 'igniter': 1, 'auger': 2, 'fan': 3}, 'frequency': 100}
        yield mod.GrillPlatform(config)


def test_pwm_fan_ramp_runs_to_completion(platform):
    # Use a very short ramp so the test is fast; join the thread before asserting.
    platform.pwm_fan_ramp(on_time=0.1, min_duty_cycle=20, max_duty_cycle=100)
    platform._ramp_thread.join(timeout=5)
    assert platform._ramp_thread.is_alive() is False
    # Fan power relay enabled and final speed is the max duty cycle.
    platform.relay.relay_on.assert_any_call(3)
    assert platform._fan_speed_percent == 100


def test_stop_ramp_halts_thread(platform):
    platform.pwm_fan_ramp(on_time=10, min_duty_cycle=20, max_duty_cycle=100)
    platform._stop_ramp()
    assert platform._ramp_thread is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_x86_ramp.py -v`
Expected: FAIL — `AttributeError: 'GrillPlatform' object has no attribute 'pwm_fan_ramp'`.

- [ ] **Step 3: Add ramp methods**

Add to `GrillPlatform` (after the fan/PWM methods):

```python
	# MARK: Fan ramp (Smoke Plus)
	def pwm_fan_ramp(self, on_time=5, min_duty_cycle=20, max_duty_cycle=100):
		self.logger.debug('pwm_fan_ramp: Starting fan ramp on_time=' + str(on_time) +
			' min=' + str(min_duty_cycle) + ' max=' + str(max_duty_cycle))
		self.relay.relay_on(self.relay_map['fan'])
		self._output_state['fan'] = True
		self._start_ramp(on_time, min_duty_cycle, max_duty_cycle)

	def _start_ramp(self, on_time, min_duty_cycle, max_duty_cycle):
		self._stop_ramp()
		self._ramp_stop = threading.Event()
		self._ramp_thread = threading.Thread(
			target=self._ramp_device,
			args=(on_time, min_duty_cycle, max_duty_cycle),
			daemon=True,
		)
		self._ramp_thread.start()

	def _stop_ramp(self):
		if self._ramp_thread is not None:
			self._ramp_stop.set()
			if self._ramp_thread is not threading.current_thread():
				self._ramp_thread.join(timeout=5)
			self._ramp_thread = None

	def _ramp_device(self, on_time, min_duty_cycle, max_duty_cycle, fps=25):
		# Linearly ramp the fan speed from min to max over on_time seconds.
		# No inversion: values are fan-speed percent applied directly.
		steps = max(int(fps * on_time), 1)
		for i in range(steps):
			fraction = i / steps
			percent = min_duty_cycle + (max_duty_cycle - min_duty_cycle) * fraction
			self.set_duty_cycle(round(percent, 2), override_ramping=False)
			if self._ramp_stop.wait(1.0 / fps):
				break
		self.set_duty_cycle(max_duty_cycle, override_ramping=False)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_x86_ramp.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add grillplat/x86_numato_emc2101.py tests/test_x86_ramp.py
git commit -m "feat: add threaded fan ramp for x86 platform Smoke Plus"
```

---

### Task 6: System/platform commands and cleanup

**Files:**
- Modify: `grillplat/x86_numato_emc2101.py`
- Create: `tests/test_x86_system.py`

**Interfaces:**
- Consumes: `self.relay`, `self.emc`, `self._stop_ramp` from earlier tasks; `is_float`, `get_os_info` from `common`.
- Produces: `supported_commands(arglist)`, `check_throttled(arglist)`, `check_cpu_temp(arglist)`, `check_wifi_quality(arglist)`, `check_alive(arglist)`, `scan_bluetooth(arglist)`, `os_info(arglist)`, `network_info(arglist)`, `hardware_info(arglist)`, and `cleanup()`. Each command returns a dict shaped `{'result': ..., 'message': ..., 'data': {...}}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_x86_system.py`:

```python
from unittest import mock

import pytest


@pytest.fixture
def platform():
    import grillplat.x86_numato_emc2101 as mod
    with mock.patch.object(mod, 'NumatoUSBRelay'), \
         mock.patch.object(mod, 'EMC2101'), \
         mock.patch.object(mod, 'ExtendedI2C'), \
         mock.patch.object(mod, 'find_i2c_bus', return_value=7):
        config = {'outputs': {'power': 0, 'igniter': 1, 'auger': 2, 'fan': 3}, 'frequency': 100}
        yield mod.GrillPlatform(config)


def test_check_throttled_reports_ok_and_false(platform):
    data = platform.check_throttled([])
    assert data['result'] == 'OK'
    assert data['data']['cpu_under_voltage'] is False
    assert data['data']['cpu_throttled'] is False


def test_check_cpu_temp_uses_psutil(platform):
    import grillplat.x86_numato_emc2101 as mod
    fake_reading = mock.Mock(current=47.0)
    with mock.patch('psutil.sensors_temperatures', return_value={'coretemp': [fake_reading]}):
        data = platform.check_cpu_temp([])
    assert data['result'] == 'OK'
    assert data['data']['cpu_temp'] == 47.0


def test_check_cpu_temp_handles_no_sensors(platform):
    with mock.patch('psutil.sensors_temperatures', return_value={}):
        data = platform.check_cpu_temp([])
    assert data['data']['cpu_temp'] == 0.0


def test_supported_commands_lists_commands(platform):
    data = platform.supported_commands([])
    assert 'check_cpu_temp' in data['data']['supported_cmds']


def test_check_alive_ok(platform):
    assert platform.check_alive([])['result'] == 'OK'


def test_cleanup_stops_fan_and_closes_relay(platform):
    platform.cleanup()
    platform.relay.reset.assert_called()
    platform.relay.close.assert_called()
    assert platform.emc.manual_fan_speed == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_x86_system.py -v`
Expected: FAIL — `AttributeError: 'GrillPlatform' object has no attribute 'check_throttled'`.

- [ ] **Step 3: Add system commands and cleanup**

Add `cleanup` and the system commands to `GrillPlatform`. Place `cleanup` after the ramp methods:

```python
	# MARK: Lifecycle
	def cleanup(self):
		self.logger.debug('cleanup: Shutting down outputs')
		self._stop_ramp()
		try:
			self.emc.manual_fan_speed = 0
		except Exception:
			pass
		try:
			self.relay.reset()
		finally:
			self.relay.close()
```

Then add the system/platform commands:

```python
	# MARK: System / Platform Commands
	def supported_commands(self, arglist):
		supported_commands = [
			'check_throttled',
			'check_wifi_quality',
			'check_cpu_temp',
			'supported_commands',
			'check_alive',
			'scan_bluetooth',
			'os_info',
			'network_info',
			'hardware_info',
		]
		return {
			'result': 'OK',
			'message': 'Supported commands listed in "data".',
			'data': {'supported_cmds': supported_commands},
		}

	def check_throttled(self, arglist):
		# Not applicable on x86 hardware.
		return {
			'result': 'OK',
			'message': 'No under-voltage or throttling detected.',
			'data': {'cpu_under_voltage': False, 'cpu_throttled': False},
		}

	def check_cpu_temp(self, arglist):
		import psutil
		temp = 0.0
		result = 'OK'
		message = 'Successfully obtained CPU temperature.'
		try:
			sensors = psutil.sensors_temperatures()
			readings = []
			for label in ('coretemp', 'k10temp', 'cpu_thermal', 'acpitz'):
				if sensors.get(label):
					readings = sensors[label]
					break
			if not readings:
				for entries in sensors.values():
					if entries:
						readings = entries
						break
			if readings:
				temp = float(readings[0].current)
			else:
				message = 'No CPU temperature sensors available.'
		except Exception as exc:
			result = 'ERROR'
			message = 'Error obtaining CPU temperature: ' + str(exc)
		if not is_float(temp):
			temp = 0.0
		return {
			'result': result,
			'message': message,
			'data': {'cpu_temp': float(temp)},
		}

	def check_wifi_quality(self, arglist):
		import subprocess
		data = {'result': 'ERROR', 'message': 'Unable to obtain wifi quality data.', 'data': {}}
		try:
			output = subprocess.check_output(['iwconfig'])
			lines = output.decode('utf-8').splitlines()
			for line in lines:
				if 'Link Quality=' in line:
					quality_str = line.split('=')[1].strip()
					quality_parts = quality_str.split(' ')[0]
					try:
						quality_value, quality_max = quality_parts.split('/')
						percentage = (int(quality_value) / int(quality_max)) * 100
						data['result'] = 'OK'
						data['message'] = 'Successfully obtained wifi quality data.'
						data['data']['wifi_quality_value'] = int(quality_value)
						data['data']['wifi_quality_max'] = int(quality_max)
						data['data']['wifi_quality_percentage'] = round(percentage, 2)
					except ValueError:
						pass
		except Exception:
			pass
		return data

	def check_alive(self, arglist):
		return {
			'result': 'OK',
			'message': 'The control script is running.',
			'data': {},
		}

	def scan_bluetooth(self, arglist):
		import asyncio
		try:
			from bleak import BleakScanner
		except ImportError:
			return {
				'result': 'ERROR',
				'message': 'bleak is not installed. Run: pip install bleak',
				'data': {'bt_devices': []},
			}

		bt_devices = []
		result = 'OK'
		message = 'Bluetooth scan completed successfully.'

		async def _scan():
			discovered = await BleakScanner.discover(timeout=5.0)
			for dev in discovered:
				name = dev.name or 'Unknown'
				bt_devices.append({'name': name, 'hw_id': dev.address.lower(), 'info': ''})

		try:
			asyncio.run(_scan())
		except Exception as exc:
			result = 'ERROR'
			message = 'Bluetooth scan error: ' + str(exc)
			self.logger.error('scan_bluetooth: Error during scan - ' + str(exc))

		return {
			'result': result,
			'message': message,
			'data': {'bt_devices': bt_devices},
		}

	def os_info(self, arglist):
		return {
			'result': 'OK',
			'message': 'OS information retrieved successfully.',
			'data': get_os_info(),
		}

	def network_info(self, arglist):
		import netifaces
		net_info = {}
		for iface in netifaces.interfaces():
			addrs = netifaces.ifaddresses(iface)
			ip_addr = addrs.get(netifaces.AF_INET, [{}])[0].get('addr', 'N/A')
			mac_addr = addrs.get(netifaces.AF_LINK, [{}])[0].get('addr', 'N/A')
			net_info[iface] = {'ip_address': ip_addr, 'mac_address': mac_addr}
		return {
			'result': 'OK',
			'message': 'Network information retrieved successfully.',
			'data': net_info,
		}

	def hardware_info(self, arglist):
		import psutil
		cpu_info = {
			'hardware': 'Unknown',
			'model': 'Unknown',
			'model_name': 'Unknown',
			'cores': psutil.cpu_count(logical=True),
			'frequency': psutil.cpu_freq().current if psutil.cpu_freq() else 'Unknown',
		}
		try:
			with open('/proc/cpuinfo') as f:
				for line in f:
					if 'model name' in line.lower():
						cpu_info['model_name'] = line.strip().split(':')[1].strip()
		except OSError:
			pass
		mem_info = psutil.virtual_memory()
		return {
			'result': 'OK',
			'message': 'Hardware information retrieved successfully.',
			'data': {
				'cpu_info': cpu_info,
				'total_ram': mem_info.total,
				'available_ram': mem_info.available,
			},
		}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_x86_system.py -v`
Expected: 6 passed.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -v`
Expected: all tests from Tasks 1-6 pass.

- [ ] **Step 6: Commit**

```bash
git add grillplat/x86_numato_emc2101.py tests/test_x86_system.py
git commit -m "feat: add system commands and cleanup for x86 platform"
```

---

### Task 7: Wizard integration

**Files:**
- Modify: `wizard/wizard_manifest.json`
- Create: `tests/test_x86_manifest.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (this wires the platform into the config wizard).
- Produces: a new entry under `modules.grillplatform` keyed `x86_numato_emc2101` whose `filename` is `x86_numato_emc2101` and whose `py_dependencies` include `adafruit-circuitpython-emc2101`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_x86_manifest.py`:

```python
import json
import os


def _manifest():
    path = os.path.join(os.path.dirname(__file__), '..', 'wizard', 'wizard_manifest.json')
    with open(path) as handle:
        return json.load(handle)


def test_x86_platform_entry_present():
    manifest = _manifest()
    entry = manifest['modules']['grillplatform']['x86_numato_emc2101']
    assert entry['filename'] == 'x86_numato_emc2101'
    assert 'adafruit-circuitpython-emc2101' in entry['py_dependencies']


def test_x86_platform_settings_dependencies():
    manifest = _manifest()
    deps = manifest['modules']['grillplatform']['x86_numato_emc2101']['settings_dependencies']
    # Exposes the EMC2101 address and the i2c bus match string.
    assert 'emc2101_address' in deps
    assert 'i2c_bus_match' in deps
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_x86_manifest.py -v`
Expected: FAIL — `KeyError: 'x86_numato_emc2101'`.

- [ ] **Step 3: Add the manifest entry**

In `wizard/wizard_manifest.json`, under `modules.grillplatform`, add a new key `x86_numato_emc2101` alongside the existing platform entries (e.g. `custom`). Insert this object:

```json
"x86_numato_emc2101": {
    "friendly_name": "Generic x86 (Numato USB Relay + EMC2101 PWM)",
    "filename": "x86_numato_emc2101",
    "description": "Generic x86 build. Outputs are driven by a Numato USB relay board and fan PWM is generated by an EMC2101 on a CP2112 USB-to-I2C bridge.",
    "default": false,
    "image": "custom.png",
    "reboot_required": false,
    "py_dependencies": [
        "adafruit-circuitpython-emc2101"
    ],
    "apt_dependencies": [],
    "command_list": [],
    "settings_dependencies": {
        "current": {
            "friendly_name": "Platform Selected",
            "description": "Selects the current platform.",
            "options": {
                "x86_numato_emc2101": "Generic x86 (Numato + EMC2101)"
            },
            "settings": ["platform", "current"],
            "hidden": true
        },
        "numato_device": {
            "friendly_name": "Numato Serial Device",
            "description": "Path to the Numato USB relay serial device (e.g. /dev/ttyACM0).",
            "options": {
                "/dev/ttyACM0": "/dev/ttyACM0",
                "/dev/ttyACM1": "/dev/ttyACM1"
            },
            "settings": ["platform", "numato", "device"]
        },
        "i2c_bus_match": {
            "friendly_name": "I2C Bridge Name Match",
            "description": "Substring matched against I2C adapter names to locate the EMC2101 bus (default CP2112).",
            "options": {
                "CP2112": "CP2112 USB-to-I2C Bridge"
            },
            "settings": ["platform", "emc2101", "i2c_bus_match"]
        },
        "emc2101_address": {
            "friendly_name": "EMC2101 I2C Address",
            "description": "I2C address of the EMC2101 fan controller.",
            "options": {
                "0x4c": "0x4C"
            },
            "settings": ["platform", "emc2101", "address"]
        },
        "output_power": {
            "friendly_name": "Power Relay Index",
            "description": "Numato relay index for the power output.",
            "options": {"0": "Relay 0", "1": "Relay 1", "2": "Relay 2", "3": "Relay 3"},
            "settings": ["platform", "outputs", "power"]
        },
        "output_igniter": {
            "friendly_name": "Igniter Relay Index",
            "description": "Numato relay index for the igniter output.",
            "options": {"0": "Relay 0", "1": "Relay 1", "2": "Relay 2", "3": "Relay 3"},
            "settings": ["platform", "outputs", "igniter"]
        },
        "output_auger": {
            "friendly_name": "Auger Relay Index",
            "description": "Numato relay index for the auger output.",
            "options": {"0": "Relay 0", "1": "Relay 1", "2": "Relay 2", "3": "Relay 3"},
            "settings": ["platform", "outputs", "auger"]
        },
        "output_fan": {
            "friendly_name": "Fan Relay Index",
            "description": "Numato relay index for the fan power output.",
            "options": {"0": "Relay 0", "1": "Relay 1", "2": "Relay 2", "3": "Relay 3"},
            "settings": ["platform", "outputs", "fan"]
        }
    }
}
```

- [ ] **Step 4: Verify the manifest is valid JSON and the test passes**

Run: `python3 -c "import json; json.load(open('wizard/wizard_manifest.json'))" && uv run pytest tests/test_x86_manifest.py -v`
Expected: no JSON error; 2 passed.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -v`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add wizard/wizard_manifest.json tests/test_x86_manifest.py
git commit -m "feat: add x86 Numato/EMC2101 platform to the configuration wizard"
```

---

## Notes on the `address` setting

`config['emc2101']['address']` may arrive as the string `"0x4c"` (from the wizard) or an int (from a hand-edited settings file). Task 3's constructor already normalizes both via `int(address, 16)` for strings. No additional task needed.
