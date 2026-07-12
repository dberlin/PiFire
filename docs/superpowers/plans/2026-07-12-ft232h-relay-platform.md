# FT232H IO-Triggered Relay Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a host-agnostic PiFire grill platform that drives an IO-triggered relay board (power/auger/igniter/fan) from FT232H GPIO pins, with a selectable relay-only or EMC2101/EMC2301-PWM fan.

**Architecture:** A new `grillplat/ft232h_relay.py` implements the standard `GrillPlatform` surface. Outputs are FT232H GPIO pins via Adafruit Blinka `digitalio`; the fan is either a plain relay (`fan_controller.chip == 'none'`) or an EMC controller on the FT232H I2C bus (reusing `x86_numato`'s fan logic). All Blinka hardware access is isolated behind a patchable module-level `_load_ft232h()` so the module imports and unit-tests without hardware. The setup wizard registers the platform and sets `platform.dc_fan` from the chosen fan mode.

**Tech Stack:** Python 3, Adafruit Blinka (`board`/`digitalio`/`busio`), `pyftdi` (FT232H backend), `adafruit-circuitpython-emc2101`, local `grillplat/emc2301.py`, pytest + `unittest.mock`.

## Global Constraints

- Platform module path/name: `grillplat/ft232h_relay.py`; `settings['modules']['grillplat']` value is `ft232h_relay`.
- Wizard friendly name (verbatim): `FT232H IO-Triggered Relay`.
- Indentation is **tabs**, matching every file under `grillplat/` and `tests/`.
- Trigger polarity comes from `config['triggerlevel']` (`'LOW'` = active-low, the default); `digitalio` has no `active_high`, so invert explicitly.
- Fan modes: `fan_controller.chip` in `{'none', 'emc2101', 'emc2301'}`; `'none'` = relay-only fan.
- Default output pins on the C-bank: `power=C0, igniter=C1, auger=C2, fan=C3` (keeps I2C pins D0/D1/D2 free).
- Wizard output-pin dropdown options: `C0`–`C7` and `D4`–`D7` only (D0–D3 reserved).
- The control loop gates all PWM on `settings['platform']['dc_fan']`; the wizard sets it `True` for EMC modes, `False` for `none`.
- Run `uvx ruff format <changed files>` before every commit (repo standing rule).
- Fan/PWM logic is ported from `grillplat/x86_numato.py`. Generic-host system-info commands are **not** duplicated: they live in a shared `grillplat/system_commands.py` `SystemCommandsMixin`, inherited by both `x86_numato` and `ft232h_relay`.

---

### Task 1: FT232H relay outputs + relay-only fan

Creates the platform module driving the four relays via FT232H GPIO, with the fan as a plain relay. No EMC/I2C yet. Also creates the shared test helper used by later tasks.

**Files:**
- Create: `grillplat/ft232h_relay.py`
- Create: `tests/ft232h_helpers.py`
- Test: `tests/test_ft232h_outputs.py`

**Interfaces:**
- Produces:
  - `grillplat.ft232h_relay._load_ft232h(url='1') -> (board, digitalio)` — module-level, patchable.
  - `grillplat.ft232h_relay._Relay(dio, active_high)` with `.on()`, `.off()`, `.is_active` (bool), `.close()`.
  - `grillplat.ft232h_relay.GrillPlatform(config)` implementing `auger_on/off`, `igniter_on/off`, `power_on/off`, `fan_on(percent=100)`, `fan_off`, `fan_toggle`, `set_duty_cycle(percent, override_ramping=True)`, `set_pwm_frequency(frequency=25000)`, `pwm_fan_ramp(on_time=5, min_duty_cycle=20, max_duty_cycle=100)`, `get_input_status()`, `get_output_status()`, `cleanup()`. Instance attrs used by tests: `.relays` (dict name→`_Relay`), `.pin_map` (dict name→pin string), `._output_state`, `.pwm_fan` (bool), `.emc` (None in relay mode), `.chip`. (Task 3 makes `GrillPlatform` inherit `SystemCommandsMixin` for the system-info commands; Task 1 defines it as a plain `class GrillPlatform:`.)
  - `tests.ft232h_helpers.make_ft232h_platform(config)` — context manager yielding `(platform, harness)`; `harness` has `.board`, `.dio`, `.emc2101_cls`, `.emc2301_cls`, `.busio`.

- [ ] **Step 1: Write the shared test helper**

Create `tests/ft232h_helpers.py`:

```python
import contextlib
import types
from unittest import mock


class FakePin:
	"""Records the last value/direction written by digitalio."""

	def __init__(self):
		self.value = None
		self.direction = None
		self.deinit_called = False

	def deinit(self):
		self.deinit_called = True


class FakeDirection:
	OUTPUT = 'OUTPUT'
	INPUT = 'INPUT'


class FakeDigitalIO:
	"""Stand-in for Blinka's digitalio module."""

	Direction = FakeDirection

	def __init__(self):
		self.pins = {}

	def DigitalInOut(self, pin):
		created = FakePin()
		self.pins[pin] = created
		return created


class FakeBoard:
	"""Stand-in for Blinka's board module: C0-C7, D0-D7, SCL, SDA as sentinels."""

	def __init__(self):
		for bank in ('C', 'D'):
			for index in range(8):
				setattr(self, f'{bank}{index}', f'{bank}{index}')
		self.SCL = 'SCL'
		self.SDA = 'SDA'


@contextlib.contextmanager
def make_ft232h_platform(config):
	"""Build a GrillPlatform with FT232H/EMC/I2C hardware faked.

	Yields (platform, harness); harness carries the fakes/mocks for assertions.
	"""
	import grillplat.ft232h_relay as mod

	fake_board = FakeBoard()
	fake_dio = FakeDigitalIO()
	with (
		mock.patch.object(mod, '_load_ft232h', return_value=(fake_board, fake_dio)),
		mock.patch.object(mod, 'EMC2101_LUT') as emc2101_cls,
		mock.patch.object(mod, 'EMC2301') as emc2301_cls,
		mock.patch.object(mod, 'busio') as busio_mod,
	):
		platform = mod.GrillPlatform(config)
		harness = types.SimpleNamespace(
			board=fake_board,
			dio=fake_dio,
			emc2101_cls=emc2101_cls,
			emc2301_cls=emc2301_cls,
			busio=busio_mod,
		)
		yield platform, harness
```

- [ ] **Step 2: Write the failing outputs test**

Create `tests/test_ft232h_outputs.py`:

```python
from tests.ft232h_helpers import make_ft232h_platform


def _relay_config(**overrides):
	config = {
		'outputs': {'power': 'C0', 'igniter': 'C1', 'auger': 'C2', 'fan': 'C3'},
		'fan_controller': {'chip': 'none'},
		'triggerlevel': 'LOW',
		'frequency': 25000,
	}
	config.update(overrides)
	return config


def test_relay_only_init_opens_no_i2c_or_emc():
	with make_ft232h_platform(_relay_config()) as (plat, harness):
		assert plat.pwm_fan is False
		assert plat.emc is None
		harness.busio.I2C.assert_not_called()
		harness.emc2101_cls.assert_not_called()
		harness.emc2301_cls.assert_not_called()
		# Four output pins created and de-asserted (active-low -> value True).
		assert set(plat.relays) == {'power', 'igniter', 'auger', 'fan'}
		assert plat.relays['power']._dio.value is True


def test_output_methods_toggle_mapped_active_low_pins():
	with make_ft232h_platform(_relay_config()) as (plat, harness):
		plat.auger_on()
		# 'auger' maps to C2; active-low asserted -> value False.
		assert harness.dio.pins['C2'].value is False
		assert plat._output_state['auger'] is True
		plat.auger_off()
		assert harness.dio.pins['C2'].value is True
		assert plat._output_state['auger'] is False


def test_power_and_igniter_use_mapped_pins():
	with make_ft232h_platform(_relay_config()) as (plat, harness):
		plat.power_on()
		plat.igniter_on()
		assert harness.dio.pins['C0'].value is False  # power -> C0
		assert harness.dio.pins['C1'].value is False  # igniter -> C1


def test_active_high_trigger_level_not_inverted():
	with make_ft232h_platform(_relay_config(triggerlevel='HIGH')) as (plat, harness):
		# De-asserted at init -> value False for active-high.
		assert harness.dio.pins['C0'].value is False
		plat.power_on()
		assert harness.dio.pins['C0'].value is True


def test_custom_pin_mapping_is_honored():
	with make_ft232h_platform(
		_relay_config(outputs={'power': 'D4', 'igniter': 'D5', 'auger': 'D6', 'fan': 'D7'})
	) as (plat, harness):
		plat.auger_on()
		assert harness.dio.pins['D6'].value is False


def test_unknown_pin_name_raises_value_error():
	import pytest

	with pytest.raises(ValueError):
		with make_ft232h_platform(_relay_config(outputs={'power': 'Z9', 'igniter': 'C1', 'auger': 'C2', 'fan': 'C3'})):
			pass


def test_relay_only_fan_on_off_and_toggle():
	with make_ft232h_platform(_relay_config()) as (plat, harness):
		plat.fan_on()
		assert harness.dio.pins['C3'].value is False  # fan -> C3 asserted
		assert plat._output_state['fan'] is True
		plat.fan_toggle()
		assert plat._output_state['fan'] is False
		assert harness.dio.pins['C3'].value is True


def test_relay_only_set_duty_cycle_and_frequency_are_noops():
	with make_ft232h_platform(_relay_config()) as (plat, harness):
		# Must not raise and must not create an EMC.
		plat.set_duty_cycle(50)
		plat.set_pwm_frequency(20000)
		assert plat.emc is None


def test_get_output_status_relay_mode_has_no_pwm_keys():
	with make_ft232h_platform(_relay_config()) as (plat, harness):
		plat.auger_on()
		status = plat.get_output_status()
		assert status == {'auger': True, 'igniter': False, 'power': False, 'fan': False}


def test_get_input_status_is_false():
	with make_ft232h_platform(_relay_config()) as (plat, harness):
		assert plat.get_input_status() is False


def test_cleanup_deasserts_and_closes_pins():
	with make_ft232h_platform(_relay_config()) as (plat, harness):
		plat.power_on()
		plat.cleanup()
		# All relays de-asserted (active-low -> True) and closed.
		for pin in ('C0', 'C1', 'C2', 'C3'):
			assert harness.dio.pins[pin].value is True
			assert harness.dio.pins[pin].deinit_called is True
```

Note: `plat.relays['power']._dio` and `harness.dio.pins['C0']` are the same `FakePin`; tests use whichever reads clearest.

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_ft232h_outputs.py -q`
Expected: FAIL — collection/import error `ModuleNotFoundError: No module named 'grillplat.ft232h_relay'`.

- [ ] **Step 4: Write the module (relay outputs + relay-only fan)**

Create `grillplat/ft232h_relay.py`:

```python
#!/usr/bin/env python3

# *****************************************
# PiFire FT232H IO-Triggered Relay Platform Interface Library
# *****************************************
#
# Description: Controls PiFire outputs on any host using an FT232H USB breakout
#   as a GPIO expander.  Each output (power, igniter, auger, fan) drives one
#   input of an IO-triggered relay board via an FT232H GPIO pin (Adafruit Blinka
#   digitalio).  An alternative to a directly-wired relay board.
#
#   The fan is selectable via fan_controller.chip:
#     'none'                 -> the fan is a plain relay (on/off).
#     'emc2101' / 'emc2301'  -> the fan relay gates power and an EMC controller
#                               on the FT232H I2C bus sets fan speed.
#
# *****************************************

import os
import threading

from common import create_logger

import busio
from adafruit_emc2101.emc2101_lut import EMC2101_LUT

from grillplat.emc2301 import EMC2301


# Default FT232H pin name per PiFire output.  The C-bank keeps the I2C pins
# (D0=SCL, D1/D2=SDA) free for the EMC fan controller.
_DEFAULT_OUTPUTS = {'power': 'C0', 'igniter': 'C1', 'auger': 'C2', 'fan': 'C3'}


def _load_ft232h(url='1'):
	"""Enable Blinka's FT232H backend and import board + digitalio.

	Isolated so importing this module never opens USB hardware, and so tests can
	patch it to inject fakes.  `url` is assigned to BLINKA_FT232H before importing
	board: '1' selects the first FT232H; a pyftdi URL selects a specific device.
	"""
	os.environ['BLINKA_FT232H'] = str(url)
	import board
	import digitalio

	return board, digitalio


class _Relay:
	"""One relay-board input driven by an FT232H GPIO pin.

	digitalio has no active_high parameter, so trigger polarity is applied
	explicitly: an active-LOW board asserts the relay by driving the pin low.
	"""

	def __init__(self, dio, active_high):
		self._dio = dio
		self._active_high = active_high
		self._state = False
		self.off()

	def on(self):
		self._dio.value = self._active_high
		self._state = True

	def off(self):
		self._dio.value = not self._active_high
		self._state = False

	@property
	def is_active(self):
		return self._state

	def close(self):
		self._dio.deinit()


class GrillPlatform:
	def __init__(self, config):
		self.logger = create_logger('control')
		self.config = config

		outputs = config.get('outputs', {}) or {}
		self.pin_map = {name: str(outputs.get(name, default)) for name, default in _DEFAULT_OUTPUTS.items()}

		ft232h_cfg = config.get('ft232h', {}) or {}
		self.url = ft232h_cfg.get('url', '1')

		fan_cfg = config.get('fan_controller', {}) or {}
		self.chip = str(fan_cfg.get('chip', 'none')).lower()
		self.pwm_fan = self.chip in ('emc2101', 'emc2301')

		address = fan_cfg.get('address')
		if address is None:
			address = 0x2F if self.chip == 'emc2301' else 0x4C
		elif isinstance(address, str):
			address = int(address, 16)
		self.emc_address = address

		self.frequency = config.get('frequency', 25000)
		self.standalone = config.get('standalone', True)

		active_high = config.get('triggerlevel', 'LOW') == 'HIGH'

		# Cached commanded output state (avoids reading hardware per poll).
		self._output_state = {'auger': False, 'fan': False, 'igniter': False, 'power': False}
		self._fan_speed_percent = 0

		# Fan ramp control (EMC mode).
		self._ramp_thread = None
		self._ramp_stop = threading.Event()

		# Open the FT232H and create one output pin per PiFire output.
		board, digitalio = _load_ft232h(self.url)
		self.relays = {}
		for name, pin_name in self.pin_map.items():
			try:
				pin = getattr(board, pin_name)
			except AttributeError:
				raise ValueError(f'Unknown FT232H pin {pin_name!r} for output {name!r}')
			dio = digitalio.DigitalInOut(pin)
			dio.direction = digitalio.Direction.OUTPUT
			self.relays[name] = _Relay(dio, active_high)

		# Open the fan controller if PWM fan mode is selected (Task 2).
		self.emc = None
		if self.pwm_fan:
			self._init_fan_controller(board)

	def _init_fan_controller(self, board):
		# Implemented in Task 2.
		pass

	# MARK: Output control
	def _set_output(self, name, state):
		relay = self.relays[name]
		if state:
			relay.on()
		else:
			relay.off()
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

	# MARK: Fan / PWM control
	def fan_on(self, fan_speed_percent=100):
		self.logger.debug('fan_on: Enabling fan power, speed ' + str(fan_speed_percent))
		self._set_output('fan', True)
		if self.pwm_fan:
			self._stop_ramp()
			self.set_duty_cycle(fan_speed_percent)

	def fan_off(self):
		self.logger.debug('fan_off: Stopping fan and removing power')
		if self.pwm_fan:
			self._stop_ramp()
			self.emc.manual_fan_speed = 0
			self._fan_speed_percent = 0
		self._set_output('fan', False)

	def fan_toggle(self):
		if self._output_state['fan']:
			self.fan_off()
		else:
			self.fan_on()

	def set_duty_cycle(self, fan_speed_percent, override_ramping=True):
		if not self.pwm_fan:
			return
		if override_ramping:
			self._stop_ramp()
		fan_speed_percent = max(0, min(100, fan_speed_percent))
		self.emc.manual_fan_speed = fan_speed_percent
		self._fan_speed_percent = fan_speed_percent

	def set_pwm_frequency(self, frequency=25000):
		# Record the requested value so control.py's "re-apply if changed"
		# comparison settles even in relay-only mode.
		self.frequency = frequency
		if not self.pwm_fan:
			return
		try:
			if self.chip == 'emc2301':
				self.emc.pwm_frequency = frequency
			else:
				pwm_f = max(1, min(31, round(360000 / (2 * frequency))))
				self.emc.set_pwm_clock(use_preset=False, use_slow=False)
				self.emc.pwm_frequency_divisor = 1
				self.emc.pwm_frequency = pwm_f
		except (ValueError, OSError, AttributeError) as exc:
			self.logger.warning('set_pwm_frequency: controller rejected frequency: ' + str(exc))

	def _stop_ramp(self):
		if self._ramp_thread is not None:
			self._ramp_stop.set()
			if self._ramp_thread is not threading.current_thread():
				self._ramp_thread.join(timeout=5)
			self._ramp_thread = None

	def pwm_fan_ramp(self, on_time=5, min_duty_cycle=20, max_duty_cycle=100):
		self._set_output('fan', True)
		if not self.pwm_fan:
			return
		self._start_ramp(on_time, min_duty_cycle, max_duty_cycle)

	def _start_ramp(self, on_time, min_duty_cycle, max_duty_cycle):
		self._stop_ramp()
		self._ramp_stop = threading.Event()
		self._ramp_thread = threading.Thread(
			target=self._ramp_device, args=(on_time, min_duty_cycle, max_duty_cycle), daemon=True
		)
		self._ramp_thread.start()

	def _ramp_device(self, on_time, min_duty_cycle, max_duty_cycle, fps=25):
		steps = max(int(fps * on_time), 1)
		for i in range(steps):
			fraction = i / steps
			percent = min_duty_cycle + (max_duty_cycle - min_duty_cycle) * fraction
			self.set_duty_cycle(round(percent, 2), override_ramping=False)
			if self._ramp_stop.wait(1.0 / fps):
				break
		self.set_duty_cycle(max_duty_cycle, override_ramping=False)

	# MARK: Lifecycle
	def cleanup(self):
		self.logger.debug('cleanup: Shutting down outputs')
		self._stop_ramp()
		if self.pwm_fan and self.emc is not None:
			try:
				self.emc.manual_fan_speed = 0
			except Exception:
				pass
		for relay in self.relays.values():
			try:
				relay.off()
			finally:
				relay.close()

	def get_output_status(self):
		self.current = {
			'auger': self._output_state['auger'],
			'igniter': self._output_state['igniter'],
			'power': self._output_state['power'],
			'fan': self._output_state['fan'],
		}
		if self.pwm_fan:
			self.current['pwm'] = self._fan_speed_percent
			self.current['frequency'] = self.frequency
		return self.current
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_ft232h_outputs.py -q`
Expected: PASS (11 passed).

- [ ] **Step 6: Format and commit**

```bash
uvx ruff format grillplat/ft232h_relay.py tests/ft232h_helpers.py tests/test_ft232h_outputs.py
git add grillplat/ft232h_relay.py tests/ft232h_helpers.py tests/test_ft232h_outputs.py
git commit -m "feat(ft232h): relay outputs + relay-only fan platform"
```

---

### Task 2: EMC2101/EMC2301 PWM fan mode

Fills in `_init_fan_controller` so `chip` in `{'emc2101','emc2301'}` opens the FT232H I2C bus and drives fan speed. Fan on/off/duty/ramp logic already lives in the module (Task 1) and activates via `self.pwm_fan`.

**Files:**
- Modify: `grillplat/ft232h_relay.py` (`_init_fan_controller`)
- Test: `tests/test_ft232h_fan.py`

**Interfaces:**
- Consumes: everything Task 1 produced.
- Produces: after init in EMC mode, `plat.emc` is the EMC instance, `plat.emc.manual_fan_speed` reflects the last commanded speed, and `get_output_status()` includes `pwm` and `frequency`.

- [ ] **Step 1: Write the failing fan test**

Create `tests/test_ft232h_fan.py`:

```python
from tests.ft232h_helpers import make_ft232h_platform


def _emc_config(chip='emc2101', **overrides):
	config = {
		'outputs': {'power': 'C0', 'igniter': 'C1', 'auger': 'C2', 'fan': 'C3'},
		'fan_controller': {'chip': chip},
		'triggerlevel': 'LOW',
		'frequency': 25000,
	}
	config.update(overrides)
	return config


def test_emc2101_init_opens_i2c_and_controller():
	with make_ft232h_platform(_emc_config('emc2101')) as (plat, harness):
		assert plat.pwm_fan is True
		harness.busio.I2C.assert_called_once_with(harness.board.SCL, harness.board.SDA)
		harness.emc2101_cls.assert_called_once()
		harness.emc2301_cls.assert_not_called()
		assert plat.emc is harness.emc2101_cls.return_value
		# Fan curve disabled so PiFire drives speed directly, and speed starts 0.
		assert plat.emc.lut_enabled is False
		assert plat.emc.manual_fan_speed == 0


def test_emc2301_init_uses_emc2301_at_default_address():
	with make_ft232h_platform(_emc_config('emc2301')) as (plat, harness):
		harness.emc2301_cls.assert_called_once()
		# Default EMC2301 address is 0x2F.
		assert harness.emc2301_cls.call_args.kwargs.get('address') == 0x2F


def test_fan_on_sets_relay_and_speed():
	with make_ft232h_platform(_emc_config('emc2101')) as (plat, harness):
		plat.fan_on(80)
		assert harness.dio.pins['C3'].value is False  # fan relay asserted (active-low)
		assert plat._output_state['fan'] is True
		assert plat.emc.manual_fan_speed == 80
		assert plat._fan_speed_percent == 80


def test_fan_off_zeroes_speed_and_deasserts_relay():
	with make_ft232h_platform(_emc_config('emc2101')) as (plat, harness):
		plat.fan_on(80)
		plat.fan_off()
		assert plat.emc.manual_fan_speed == 0
		assert plat._output_state['fan'] is False
		assert harness.dio.pins['C3'].value is True


def test_set_duty_cycle_clamps_to_0_100():
	with make_ft232h_platform(_emc_config('emc2101')) as (plat, harness):
		plat.set_duty_cycle(150)
		assert plat.emc.manual_fan_speed == 100
		plat.set_duty_cycle(-20)
		assert plat.emc.manual_fan_speed == 0


def test_ramp_device_ends_at_max_duty_cycle():
	with make_ft232h_platform(_emc_config('emc2101')) as (plat, harness):
		# Pre-set the stop event so the loop body runs once then exits without sleeping.
		plat._ramp_stop.set()
		plat._ramp_device(on_time=1, min_duty_cycle=20, max_duty_cycle=90, fps=25)
		assert plat.emc.manual_fan_speed == 90


def test_get_output_status_emc_mode_reports_pwm_and_frequency():
	with make_ft232h_platform(_emc_config('emc2101')) as (plat, harness):
		plat.fan_on(60)
		status = plat.get_output_status()
		assert status['fan'] is True
		assert status['pwm'] == 60
		assert status['frequency'] == plat.frequency


def test_cleanup_zeroes_emc_speed():
	with make_ft232h_platform(_emc_config('emc2101')) as (plat, harness):
		plat.fan_on(50)
		plat.cleanup()
		assert plat.emc.manual_fan_speed == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_ft232h_fan.py -q`
Expected: FAIL — e.g. `test_emc2101_init_opens_i2c_and_controller` fails because `_init_fan_controller` is a no-op (`plat.emc is None`, `busio.I2C` not called).

- [ ] **Step 3: Implement `_init_fan_controller`**

In `grillplat/ft232h_relay.py`, replace the placeholder `_init_fan_controller` with:

```python
	def _init_fan_controller(self, board):
		# EMC fan controller on the FT232H's own I2C bus (D0=SCL, D1/D2=SDA).
		i2c = busio.I2C(board.SCL, board.SDA)
		if self.chip == 'emc2301':
			self.emc = EMC2301(i2c, address=self.emc_address)
		else:
			self.emc = EMC2101_LUT(i2c)
			# Drive the fan from PiFire's control logic, not the chip's LUT curve.
			self.emc.lut_enabled = False
		self.emc.manual_fan_speed = 0
		# Apply the PWM frequency now so the chip is correct immediately.
		self.set_pwm_frequency(self.frequency)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_ft232h_fan.py tests/test_ft232h_outputs.py -q`
Expected: PASS (both files green).

- [ ] **Step 5: Format and commit**

```bash
uvx ruff format grillplat/ft232h_relay.py tests/test_ft232h_fan.py
git add grillplat/ft232h_relay.py tests/test_ft232h_fan.py
git commit -m "feat(ft232h): EMC2101/EMC2301 PWM fan mode"
```

---

### Task 3: Shared generic-host system/platform info mixin

Extracts the generic-host system-info commands into a shared `SystemCommandsMixin` inherited by both `x86_numato` and the new `ft232h_relay`, replacing `x86_numato`'s inline copies. This is a behavior-preserving refactor: `tests/test_x86_system.py` calls these methods on the instance, so they keep resolving through inheritance and that file stays green **unmodified**.

**Files:**
- Create: `grillplat/system_commands.py`
- Modify: `grillplat/x86_numato.py` (inherit the mixin; delete the 9 inline methods; drop the now-unused `is_float` / `get_os_info` / `get_wifi_quality` imports)
- Modify: `grillplat/ft232h_relay.py` (inherit the mixin)
- Test: `tests/test_ft232h_system.py`
- Must stay green unmodified: `tests/test_x86_system.py`

**Interfaces:**
- Consumes: Task 1's `GrillPlatform`; Task 2's completed module.
- Produces: `grillplat.system_commands.SystemCommandsMixin` providing `supported_commands(arglist)`, `check_throttled(arglist)`, `check_cpu_temp(arglist)`, `check_wifi_quality(arglist)`, `check_alive(arglist)`, `scan_bluetooth(arglist)`, `os_info(arglist)`, `network_info(arglist)`, `hardware_info(arglist)` — each returns `{'result', 'message', 'data'}` and relies on `self.logger`. Both `x86_numato.GrillPlatform` and `ft232h_relay.GrillPlatform` inherit it.

- [ ] **Step 1: Write the failing system-commands test**

Create `tests/test_ft232h_system.py`:

```python
from tests.ft232h_helpers import make_ft232h_platform


def _config():
	return {
		'outputs': {'power': 'C0', 'igniter': 'C1', 'auger': 'C2', 'fan': 'C3'},
		'fan_controller': {'chip': 'none'},
		'triggerlevel': 'LOW',
	}


def test_supported_commands_lists_expected():
	with make_ft232h_platform(_config()) as (plat, harness):
		result = plat.supported_commands([])
		assert result['result'] == 'OK'
		cmds = result['data']['supported_cmds']
		assert 'check_alive' in cmds
		assert 'hardware_info' in cmds


def test_check_alive_ok():
	with make_ft232h_platform(_config()) as (plat, harness):
		assert plat.check_alive([])['result'] == 'OK'


def test_check_throttled_reports_not_throttled():
	with make_ft232h_platform(_config()) as (plat, harness):
		data = plat.check_throttled([])['data']
		assert data['cpu_under_voltage'] is False
		assert data['cpu_throttled'] is False


def test_check_cpu_temp_returns_float():
	with make_ft232h_platform(_config()) as (plat, harness):
		result = plat.check_cpu_temp([])
		assert isinstance(result['data']['cpu_temp'], float)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_ft232h_system.py -q`
Expected: FAIL — `AttributeError: 'GrillPlatform' object has no attribute 'supported_commands'`.

- [ ] **Step 3: Create the shared mixin**

Create `grillplat/system_commands.py` by **moving** the nine methods currently under `# MARK: System / Platform Commands` in `grillplat/x86_numato.py` (`supported_commands`, `check_throttled`, `check_cpu_temp`, `check_wifi_quality`, `check_alive`, `scan_bluetooth`, `os_info`, `network_info`, `hardware_info`) verbatim into a mixin class. These are the generic-host variants (psutil-based CPU temp, no `vcgencmd`), which suit any non-Raspberry-Pi host. Add the imports the moved methods need at module top:

```python
#!/usr/bin/env python3

# *****************************************
# PiFire Generic-Host System / Platform Commands
# *****************************************
#
# Description: System/platform info commands (supported_commands, CPU temp via
#   psutil, wifi quality, bluetooth scan, os/network/hardware info) shared by
#   non-Raspberry-Pi platforms (x86_numato, ft232h_relay).  Raspberry-Pi
#   platforms keep their own vcgencmd-based variants.
#
#   Consuming classes must provide self.logger.
# *****************************************

from common import is_float, get_os_info, get_wifi_quality


class SystemCommandsMixin:
	# <the nine methods, pasted verbatim from x86_numato.py, tabs preserved>
```

Paste the nine methods exactly as they appear in `x86_numato.py` (do not rewrite them). `check_cpu_temp`, `scan_bluetooth`, `network_info`, and `hardware_info` keep their existing method-local `import psutil` / `import asyncio` / `from bleak import BleakScanner` / `import netifaces` lines.

- [ ] **Step 4: Make `x86_numato` inherit the mixin and delete its copies**

In `grillplat/x86_numato.py`:
1. Add the import near the other `grillplat.*` imports:

```python
from grillplat.system_commands import SystemCommandsMixin
```

2. Change the class declaration:

```python
class GrillPlatform(SystemCommandsMixin):
```

3. Delete the nine methods that were moved (the whole `# MARK: System / Platform Commands` block down to but **not** including `get_output_status`, which stays on the class).
4. Fix the top-of-file import — the moved methods were the only users of `is_float`, `get_os_info`, and `get_wifi_quality`, so narrow the line to what `x86_numato` still uses:

```python
from common import create_logger
```

(Verify with `grep -nE 'is_float|get_os_info|get_wifi_quality' grillplat/x86_numato.py` — expected: no matches after the deletion.)

- [ ] **Step 5: Make `ft232h_relay` inherit the mixin**

In `grillplat/ft232h_relay.py`:
1. Add the import near the other `grillplat.*` import:

```python
from grillplat.system_commands import SystemCommandsMixin
```

2. Change the class declaration:

```python
class GrillPlatform(SystemCommandsMixin):
```

- [ ] **Step 6: Run both suites to verify green**

Run: `python -m pytest tests/test_ft232h_system.py tests/test_x86_system.py tests/test_x86_outputs.py tests/test_ft232h_outputs.py tests/test_ft232h_fan.py -q`
Expected: PASS (all). `test_x86_system.py` passes unmodified, proving the refactor preserved behavior.

- [ ] **Step 7: Format and commit**

```bash
uvx ruff format grillplat/system_commands.py grillplat/x86_numato.py grillplat/ft232h_relay.py tests/test_ft232h_system.py
git add grillplat/system_commands.py grillplat/x86_numato.py grillplat/ft232h_relay.py tests/test_ft232h_system.py
git commit -m "refactor(grillplat): shared generic-host system-info mixin"
```

---

### Task 4: Settings defaults + pyftdi dependency

Adds the `ft232h` config block to the platform settings defaults and the `pyftdi` dependency.

**Files:**
- Modify: `common/common.py` (inside `default_settings()`, the `settings['platform']` dict near line 149-193)
- Modify: `pyproject.toml` (dependencies list)
- Modify: `auto-install/requirements.txt`
- Test: `tests/test_ft232h_settings.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `default_settings()['platform']['ft232h'] == {'url': '1'}`.

- [ ] **Step 1: Write the failing settings test**

Create `tests/test_ft232h_settings.py`:

```python
from common.common import default_settings


def test_platform_defaults_include_ft232h_block():
	platform = default_settings()['platform']
	assert platform['ft232h'] == {'url': '1'}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ft232h_settings.py -q`
Expected: FAIL — `KeyError: 'ft232h'`.

- [ ] **Step 3: Add the `ft232h` block to platform defaults**

In `common/common.py`, inside `default_settings()`'s `settings['platform']` dict, add an `ft232h` entry next to the existing `fan_controller` block:

```python
		'ft232h': {  # ft232h_relay platform: FT232H USB GPIO expander selection
			'url': '1',  # '1' = first FT232H; or a pyftdi URL to pick a specific device
		},
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_ft232h_settings.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Add the pyftdi dependency**

In `pyproject.toml`, add to the `dependencies` list (alphabetically near the other libs):

```
    "pyftdi>=0.55.0",
```

In `auto-install/requirements.txt`, add a line:

```
pyftdi==0.56.0
```

- [ ] **Step 6: Verify dependency resolves**

Run: `uv lock`
Expected: resolves without error and `pyftdi` appears in `uv.lock`. Confirm with:
Run: `grep -c 'name = "pyftdi"' uv.lock`
Expected: `1`.

- [ ] **Step 7: Format and commit**

```bash
uvx ruff format common/common.py tests/test_ft232h_settings.py
git add common/common.py tests/test_ft232h_settings.py pyproject.toml auto-install/requirements.txt uv.lock
git commit -m "feat(ft232h): platform settings defaults + pyftdi dependency"
```

---

### Task 5: Wizard manifest entry + dc_fan coupling

Registers the platform in the setup wizard and wires the `dc_fan`-from-fan-mode coupling through a small, testable helper.

**Files:**
- Modify: `wizard/wizard_manifest.json` (add `modules.grillplatform.ft232h_relay`)
- Modify: `wizard.py` (extract `select_grillplat_module(settings)` helper; call it where the inline `system_type` mapping currently lives around line 183-190)
- Test: `tests/test_ft232h_wizard.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (config-only).
- Produces: `wizard.select_grillplat_module(settings)` — mutates `settings['modules']['grillplat']` and `settings['platform']['dc_fan']` based on `settings['platform']['system_type']` and, for `ft232h_relay`, `settings['platform']['fan_controller']['chip']`.

- [ ] **Step 1: Write the failing wizard test**

Create `tests/test_ft232h_wizard.py`:

```python
import json

from wizard import select_grillplat_module


def _settings(system_type, chip='none'):
	return {
		'modules': {'grillplat': 'prototype'},
		'platform': {
			'system_type': system_type,
			'dc_fan': False,
			'fan_controller': {'chip': chip},
		},
	}


def test_manifest_registers_ft232h_relay():
	with open('wizard/wizard_manifest.json') as handle:
		manifest = json.load(handle)
	entry = manifest['modules']['grillplatform']['ft232h_relay']
	assert entry['friendly_name'] == 'FT232H IO-Triggered Relay'
	assert entry['filename'] == 'ft232h_relay'
	assert 'pyftdi' in entry['py_dependencies']
	# Output pin dropdowns expose C0-C7 and D4-D7 only.
	pin_options = set(entry['settings_dependencies']['output_power']['options'])
	assert pin_options == {f'C{i}' for i in range(8)} | {f'D{i}' for i in range(4, 8)}
	# Fan mode option maps to fan_controller.chip and includes 'none'.
	fan_mode = entry['settings_dependencies']['fan_mode']
	assert fan_mode['settings'] == ['platform', 'fan_controller', 'chip']
	assert set(fan_mode['options']) == {'none', 'emc2101', 'emc2301'}


def test_ft232h_relay_selection_relay_mode_leaves_dc_fan_false():
	settings = _settings('ft232h_relay', chip='none')
	select_grillplat_module(settings)
	assert settings['modules']['grillplat'] == 'ft232h_relay'
	assert settings['platform']['dc_fan'] is False


def test_ft232h_relay_selection_emc_mode_sets_dc_fan_true():
	settings = _settings('ft232h_relay', chip='emc2101')
	select_grillplat_module(settings)
	assert settings['modules']['grillplat'] == 'ft232h_relay'
	assert settings['platform']['dc_fan'] is True


def test_existing_platforms_still_map():
	settings = _settings('x86_numato')
	select_grillplat_module(settings)
	assert settings['modules']['grillplat'] == 'x86_numato'
	assert settings['platform']['dc_fan'] is True

	settings = _settings('raspberry_pi_all')
	select_grillplat_module(settings)
	assert settings['modules']['grillplat'] == 'raspberry_pi_all'

	settings = _settings('something_unknown')
	select_grillplat_module(settings)
	assert settings['modules']['grillplat'] == 'prototype'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_ft232h_wizard.py -q`
Expected: FAIL — `ImportError: cannot import name 'select_grillplat_module' from 'wizard'` and the manifest KeyError.

- [ ] **Step 3: Add the manifest entry**

In `wizard/wizard_manifest.json`, add under `modules.grillplatform` (sibling of `x86_numato`):

```json
"ft232h_relay": {
  "friendly_name": "FT232H IO-Triggered Relay",
  "filename": "ft232h_relay",
  "description": "Host-agnostic build. PiFire outputs (power/igniter/auger/fan) drive an IO-triggered relay board via FT232H GPIO pins over USB. The fan is either a plain relay (on/off) or variable-speed via an EMC2101/EMC2301 fan controller on the FT232H's I2C bus. Requires USB access to the FT232H (libusb; on Linux add a udev rule so it is reachable without root, and ensure the ftdi_sio kernel driver does not claim the device).",
  "default": false,
  "image": "custom.png",
  "reboot_required": false,
  "py_dependencies": [
    "pyftdi",
    "adafruit-circuitpython-emc2101"
  ],
  "apt_dependencies": [],
  "command_list": [],
  "settings_dependencies": {
    "current": {
      "friendly_name": "Platform Selected",
      "description": "Selects the current platform.",
      "options": { "ft232h_relay": "FT232H IO-Triggered Relay" },
      "settings": ["platform", "current"],
      "hidden": true
    },
    "system_type": {
      "friendly_name": "System Type",
      "description": "System core for this platform. Set automatically for the FT232H relay build.",
      "options": { "ft232h_relay": "FT232H IO-Triggered Relay" },
      "settings": ["platform", "system_type"],
      "hidden": true
    },
    "fan_mode": {
      "friendly_name": "Fan Mode",
      "description": "How the fan is driven: 'Relay (on/off)' treats the fan as a plain relay; EMC2101/EMC2301 add variable fan speed via a controller on the FT232H I2C bus.",
      "options": {
        "none": "Relay (on/off)",
        "emc2101": "EMC2101 PWM fan controller",
        "emc2301": "EMC2301 PWM fan controller"
      },
      "settings": ["platform", "fan_controller", "chip"]
    },
    "fan_controller_address": {
      "friendly_name": "Fan Controller I2C Address",
      "description": "I2C address of the fan controller when a PWM fan mode is selected (EMC2101: 0x4C/0x4D, EMC2301: 0x2F). Ignored in Relay mode.",
      "options": {
        "0x4c": "0x4C",
        "0x4d": "0x4D",
        "0x2f": "0x2F"
      },
      "settings": ["platform", "fan_controller", "address"]
    },
    "ft232h_url": {
      "friendly_name": "FT232H Device",
      "description": "Which FT232H to use. '1' selects the first FT232H found; a pyftdi URL (e.g. ftdi://ftdi:232h:SERIAL/1) selects a specific device by serial.",
      "options": {
        "1": "First FT232H (default)"
      },
      "settings": ["platform", "ft232h", "url"]
    },
    "output_power": {
      "friendly_name": "Power Output Pin",
      "description": "FT232H GPIO pin driving the power relay.",
      "options": {
        "C0": "C0", "C1": "C1", "C2": "C2", "C3": "C3",
        "C4": "C4", "C5": "C5", "C6": "C6", "C7": "C7",
        "D4": "D4", "D5": "D5", "D6": "D6", "D7": "D7"
      },
      "settings": ["platform", "outputs", "power"]
    },
    "output_igniter": {
      "friendly_name": "Igniter Output Pin",
      "description": "FT232H GPIO pin driving the igniter relay.",
      "options": {
        "C0": "C0", "C1": "C1", "C2": "C2", "C3": "C3",
        "C4": "C4", "C5": "C5", "C6": "C6", "C7": "C7",
        "D4": "D4", "D5": "D5", "D6": "D6", "D7": "D7"
      },
      "settings": ["platform", "outputs", "igniter"]
    },
    "output_auger": {
      "friendly_name": "Auger Output Pin",
      "description": "FT232H GPIO pin driving the auger relay.",
      "options": {
        "C0": "C0", "C1": "C1", "C2": "C2", "C3": "C3",
        "C4": "C4", "C5": "C5", "C6": "C6", "C7": "C7",
        "D4": "D4", "D5": "D5", "D6": "D6", "D7": "D7"
      },
      "settings": ["platform", "outputs", "auger"]
    },
    "output_fan": {
      "friendly_name": "Fan Output Pin",
      "description": "FT232H GPIO pin driving the fan relay (gates fan power in both modes).",
      "options": {
        "C0": "C0", "C1": "C1", "C2": "C2", "C3": "C3",
        "C4": "C4", "C5": "C5", "C6": "C6", "C7": "C7",
        "D4": "D4", "D5": "D5", "D6": "D6", "D7": "D7"
      },
      "settings": ["platform", "outputs", "fan"]
    }
  }
}
```

Add a comma after the preceding `x86_numato` object so the JSON stays valid.

- [ ] **Step 4: Extract and wire the `select_grillplat_module` helper**

In `wizard.py`, add a module-level function (place it above the install function that currently contains the inline mapping):

```python
def select_grillplat_module(settings):
	"""Map platform.system_type to the grillplat module and set dc_fan.

	dc_fan gates all PWM behavior in the control loop, so it is set per platform:
	always True for x86_numato, and for ft232h_relay only when a PWM fan
	controller (EMC2101/EMC2301) is selected.
	"""
	system_type = settings['platform']['system_type']
	settings['modules']['grillplat'] = 'prototype'
	if system_type == 'raspberry_pi_all':
		settings['modules']['grillplat'] = 'raspberry_pi_all'
	elif system_type == 'x86_numato':
		settings['modules']['grillplat'] = 'x86_numato'
		settings['platform']['dc_fan'] = True
	elif system_type == 'ft232h_relay':
		settings['modules']['grillplat'] = 'ft232h_relay'
		settings['platform']['dc_fan'] = settings['platform']['fan_controller']['chip'] in ('emc2101', 'emc2301')
```

Then replace the existing inline block (currently `wizard.py:183-190`):

```python
	""" Set the grillplatform module per the system_type """
	settings['modules']['grillplat'] = 'prototype'
	if settings['platform']['system_type'] == 'raspberry_pi_all':
		settings['modules']['grillplat'] = 'raspberry_pi_all'
	elif settings['platform']['system_type'] == 'x86_numato':
		settings['modules']['grillplat'] = 'x86_numato'
		settings['platform']['dc_fan'] = True
```

with a call to the helper:

```python
	""" Set the grillplatform module per the system_type """
	select_grillplat_module(settings)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_ft232h_wizard.py -q`
Expected: PASS (4 passed).

- [ ] **Step 6: Run the full FT232H suite + a JSON validity check**

Run: `python -m pytest tests/test_ft232h_outputs.py tests/test_ft232h_fan.py tests/test_ft232h_system.py tests/test_ft232h_settings.py tests/test_ft232h_wizard.py -q`
Expected: PASS (all).
Run: `python -c "import json; json.load(open('wizard/wizard_manifest.json')); print('manifest OK')"`
Expected: prints `manifest OK`.

- [ ] **Step 7: Format and commit**

```bash
uvx ruff format wizard.py tests/test_ft232h_wizard.py
git add wizard/wizard_manifest.json wizard.py tests/test_ft232h_wizard.py
git commit -m "feat(ft232h): wizard manifest entry + dc_fan coupling"
```

---

## Self-Review

**Spec coverage:**
- Module `grillplat/ft232h_relay.py` with full `GrillPlatform` surface → Tasks 1–3.
- Import safety via patchable `_load_ft232h` → Task 1 (and every test relies on it).
- `_Relay` polarity from `triggerlevel` → Task 1 (`test_active_high_trigger_level_not_inverted`).
- Selectable fan: relay-only → Task 1; EMC2101/EMC2301 PWM on FT232H I2C → Task 2.
- `get_output_status` shape per mode → Tasks 1 & 2.
- `get_input_status()` False (outputs only) → Task 1.
- System-info commands via shared `SystemCommandsMixin` (no duplication) → Task 3.
- Config: `outputs` pin names, `ft232h.url`, `fan_controller.chip` incl. `none` → Tasks 1 & 4.
- Settings defaults `ft232h` block → Task 4.
- Wizard manifest entry (friendly name, pin dropdowns C0–C7/D4–D7, py_dependencies) → Task 5.
- `dc_fan`-from-fan-mode coupling → Task 5.
- `pyftdi` dependency in pyproject + requirements → Task 4.
- Tests mirroring x86 suite → Tasks 1–5.
- Host-agnostic naming `ft232h_relay` → Global Constraints + Task 5.

**Placeholder scan:** No `TBD`/`TODO`/"add error handling"/"similar to". The Task 1 `_init_fan_controller` stub is an intentional, named seam filled by Task 2 (its Task-1 tests assert `plat.emc is None`, its Task-2 tests assert the filled behavior). Task 3 avoids duplication entirely by extracting the shared `SystemCommandsMixin` (moving, not copying, `x86_numato`'s methods) — verified behavior-preserving by `test_x86_system.py` staying green unmodified.

**Type consistency:** `_load_ft232h(url)` signature, `_Relay(dio, active_high)` with `.on/.off/.is_active/.close`, `pin_map`/`relays`/`_output_state`/`pwm_fan`/`emc`/`chip` attribute names, `select_grillplat_module(settings)`, and the harness attribute names (`board/dio/emc2101_cls/emc2301_cls/busio`) are used identically across all tasks and tests. Fan-mode option key `none`/`emc2101`/`emc2301` matches `fan_controller.chip` reads in the module and the wizard helper.
