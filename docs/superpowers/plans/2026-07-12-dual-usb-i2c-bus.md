# Dual USB I2C Bus Support (FT232H + MCP2221) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let PiFire drive an FT232H I2C bus and an MCP2221 I2C bus simultaneously in one process, with any I2C device (probe, distance sensor, EMC fan controller) assignable to either, selected in the wizard via new `ft232h`/`mcp2221` bus kinds.

**Architecture:** A new shared factory `common/i2c_bus.py` opens every I2C bus. It adds two USB-HID bus kinds that instantiate their Blinka backend I2C class directly (bypassing the process-global `board` singleton), wraps them so Adafruit drivers can lock them, caches one handle per physical bus, validates the one unworkable combination (`basic` + a USB-HID kind), and guards against board-forcing `BLINKA_*` env vars at startup. All existing call sites migrate to it; `ft232h_relay` routes its FT232H access through the factory so relays + EMC + probes share one MPSSE controller.

**Tech Stack:** Python 3.14, Adafruit Blinka 9.1.0 (`adafruit_blinka.microcontroller.ftdi_mpsse.mpsse.i2c`, `…mcp2221.i2c`), `pyftdi`, `hid`, `busio`, `adafruit_extended_bus`, pytest, ruff-format.

## Global Constraints

- **Indentation: TABS** (match every existing file). ruff-format (v0.15.20) runs on commit; changed `.py` files must pass it.
- **Test command:** `python3 -m pytest <path> -v` from the repo root (`tests/conftest.py` sets import roots).
- **Blinka backends are never opened on CI/dev.** Every test fakes them at the module boundary via `unittest.mock` (patterns already in `tests/ft232h_helpers.py` and `tests/test_tof_base.py`).
- **`ft232h`/`mcp2221` apply only to `busio`-based devices:** `mcp9600_adafruit`, `ads1115_adafruit`, `ads1015_adafruit`, the distance sensors, and the EMC fan controller. The smbus2 `ads1115` and the `prototype` probe keep `basic`/`extended` only.
- **The only unworkable combination is `basic` + (`ft232h` or `mcp2221`)** in one process. Everything else (including `basic` + `extended`, and `ft232h` + `mcp2221` + `extended`) is allowed.
- **Selector reuses the existing `i2c_bus_num` config field:** for `ft232h` a pyftdi URL (`ftdi://…`), for `mcp2221` a device serial; blank = first of that kind. For `ft232h`, blank and `'1'` are the same adapter.
- Spec: `docs/superpowers/specs/2026-07-12-dual-usb-i2c-bus-design.md`.

---

## File Structure

- **Create** `common/i2c_bus.py` — the whole bus subsystem: `find_i2c_bus`, `resolve_i2c_bus` (moved from `probes/base.py`), `I2CBusConfigError`, `validate_bus_kinds`, `assert_clean_blinka_env`, `_LockedI2C`, `open_i2c_bus` (+ cache and opened-kind registry), `reset_bus_state` (test helper).
- **Create** `tests/test_i2c_bus.py` — unit tests for the factory and guards.
- **Modify** `probes/base.py` — drop the local `find_i2c_bus`/`resolve_i2c_bus`, re-export them from `common.i2c_bus`.
- **Modify** `probes/mcp9600_adafruit.py`, `probes/ads1115_adafruit.py`, `probes/ads1015_adafruit.py` — open the bus via `open_i2c_bus`.
- **Modify** `distance/_tof_base.py` — open the bus via `open_i2c_bus`; **Modify** `tests/test_tof_base.py` mocks.
- **Modify** `grillplat/x86_numato.py` — open the EMC bus via `open_i2c_bus`; drop its local `find_i2c_bus`/`resolve_i2c_bus`.
- **Modify** `grillplat/ft232h_relay.py` — open the FT232H bus via the factory first, reuse it for relay GPIO and the EMC; **Modify** `tests/ft232h_helpers.py`.
- **Modify** `controller/runtime/devices.py` — call `assert_clean_blinka_env()` at the top of `build_devices`.
- **Modify** `wizard/wizard_manifest.json` — add `ft232h`/`mcp2221` to the eligible bus-kind selectors and update the `i2c_bus_num` help; **Modify** manifest tests.
- **Modify** `blueprints/probeconfig/routes.py` — validate the assembled bus kinds on device add/edit; **Create** `tests/test_i2c_bus_wizard_validation.py`.

---

### Task 1: `common/i2c_bus.py` — pure helpers, validation, and startup guard

**Files:**
- Create: `common/i2c_bus.py`
- Test: `tests/test_i2c_bus.py`

**Interfaces:**
- Produces:
  - `find_i2c_bus(match, devices_path='/sys/bus/i2c/devices') -> int`
  - `resolve_i2c_bus(bus) -> int`
  - `class I2CBusConfigError(ValueError)`
  - `validate_bus_kinds(kinds) -> None` (raises `I2CBusConfigError`)
  - `assert_clean_blinka_env(environ=None) -> None` (raises `I2CBusConfigError`)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_i2c_bus.py`:

```python
import pytest

from common.i2c_bus import (
	I2CBusConfigError,
	assert_clean_blinka_env,
	resolve_i2c_bus,
	validate_bus_kinds,
)


def test_resolve_i2c_bus_numeric_returns_int():
	assert resolve_i2c_bus('3') == 3
	assert resolve_i2c_bus(3) == 3


def test_validate_bus_kinds_allows_workable_combos():
	# None of these raise.
	validate_bus_kinds({'ft232h', 'mcp2221'})
	validate_bus_kinds({'ft232h', 'extended'})
	validate_bus_kinds({'mcp2221', 'extended'})
	validate_bus_kinds({'basic', 'extended'})
	validate_bus_kinds({'ft232h', 'mcp2221', 'extended'})
	validate_bus_kinds({'', None, 'basic'})  # blanks ignored


def test_validate_bus_kinds_rejects_basic_plus_usb():
	with pytest.raises(I2CBusConfigError):
		validate_bus_kinds({'basic', 'ft232h'})
	with pytest.raises(I2CBusConfigError):
		validate_bus_kinds({'basic', 'mcp2221'})


def test_assert_clean_blinka_env_rejects_board_forcing_vars():
	for var in ('BLINKA_FT232H', 'BLINKA_MCP2221', 'BLINKA_FORCEBOARD', 'BLINKA_FTX232H_0'):
		with pytest.raises(I2CBusConfigError):
			assert_clean_blinka_env({var: '1'})


def test_assert_clean_blinka_env_allows_tuning_and_empty():
	assert_clean_blinka_env({})
	assert_clean_blinka_env({'BLINKA_MCP2221_HID_DELAY': '0.1', 'BLINKA_MCP2221_RESET_DELAY': '0.5'})
	assert_clean_blinka_env({'PATH': '/usr/bin'})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_i2c_bus.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'common.i2c_bus'`.

- [ ] **Step 3: Write the module**

Create `common/i2c_bus.py`:

```python
#!/usr/bin/env python3

"""
*****************************************
PiFire Shared I2C Bus Factory
*****************************************

Description:
  Single entry point for opening any I2C bus used by PiFire (probes, distance
  sensor, fan controller). Supports four bus kinds:

    basic      -- Blinka's board singleton: busio.I2C(board.SCL, board.SDA)
    extended   -- a kernel i2c-dev bus (/dev/i2c-N or an adapter-name match)
    ft232h     -- an FT232H USB adapter, via its Blinka MPSSE backend
    mcp2221   -- an MCP2221 USB adapter, via its Blinka backend

  ft232h/mcp2221 bypass the process-global `board` singleton so two USB
  adapters can run at once; they cannot be combined with `basic` (which owns
  `board`). See docs/superpowers/specs/2026-07-12-dual-usb-i2c-bus-design.md.
"""

import glob
import os
import threading

# USB-HID bus kinds that bypass Blinka's `board` singleton.
USB_HID_KINDS = frozenset({'ft232h', 'mcp2221'})

# Board/chip-forcing Blinka env vars. If any is set, `import board` is pinned to
# that backend process-wide, which silently breaks `basic` and any later
# `import board`. The MCP2221 entry is EXACT so the _HID_DELAY/_RESET_DELAY
# tuning vars stay allowed.
_FORBIDDEN_BLINKA_EXACT = frozenset({
	'BLINKA_FT232H',
	'BLINKA_FT2232H',
	'BLINKA_FT4232H',
	'BLINKA_MCP2221',
	'BLINKA_U2IF',
	'BLINKA_GREATFET',
	'BLINKA_NOVA',
	'BLINKA_SPIDRIVER',
	'BLINKA_FORCECHIP',
	'BLINKA_FORCEBOARD',
})
_FORBIDDEN_BLINKA_PREFIXES = ('BLINKA_FTX232H_',)

_UNSET = object()


class I2CBusConfigError(ValueError):
	"""Raised for an I2C bus configuration that cannot work on this host."""


def find_i2c_bus(match, devices_path='/sys/bus/i2c/devices'):
	"""
	Return the integer i2c bus number whose adapter name contains `match`
	(case-insensitive), e.g. 'CP2112' for a USB-to-I2C bridge. Scans
	`<devices_path>/i2c-*/name`. Raises RuntimeError if zero or more than one
	adapter matches, so the caller fails clearly rather than guessing.
	"""
	match_lower = str(match).lower()
	adapters = []  # (bus_num, name) for every i2c adapter present
	for bus_dir in glob.glob(os.path.join(devices_path, 'i2c-*')):
		try:
			with open(os.path.join(bus_dir, 'name')) as handle:
				name = handle.read().strip()
		except OSError:
			continue
		try:
			bus_num = int(os.path.basename(bus_dir).split('-')[-1])
		except ValueError:
			continue
		adapters.append((bus_num, name))

	found = [num for num, name in adapters if match_lower in name.lower()]
	if len(found) == 1:
		return found[0]
	# Include what IS present so a misconfigured match string is easy to fix.
	available = ', '.join(f'i2c-{n} ({name!r})' for n, name in sorted(adapters)) or '(none)'
	if not found:
		raise RuntimeError(
			f'No i2c adapter found matching {match!r} under {devices_path}. Available adapters: {available}'
		)
	raise RuntimeError(f'Multiple i2c adapters match {match!r}: {sorted(found)}. Available adapters: {available}')


def resolve_i2c_bus(bus):
	"""
	Resolve an extended-i2c-bus spec to a bus number. Accepts an int or numeric
	string (e.g. 3 / '3' -> /dev/i2c-3, used directly) or an adapter-name match
	string (e.g. 'CP2112' -> discovered via find_i2c_bus, robust against the
	dynamic bus numbers USB-to-I2C bridges get).
	"""
	spec = str(bus).strip()
	if spec.isdigit():
		return int(spec)
	return find_i2c_bus(spec)


def validate_bus_kinds(kinds):
	"""Raise I2CBusConfigError if the set of bus kinds cannot coexist in one
	process. The only unworkable case is `basic` alongside a USB-HID kind:
	Blinka's board backend is process-global."""
	kinds = {str(k).lower() for k in kinds if k}
	if 'basic' in kinds and (kinds & USB_HID_KINDS):
		raise I2CBusConfigError(
			"'basic' I2C can't share a process with a USB-HID bus (ft232h/mcp2221): "
			"Blinka's board backend is process-global. Use 'extended' for the onboard "
			'bus (a Pi onboard I2C is reachable as extended bus 1).'
		)


def assert_clean_blinka_env(environ=None):
	"""Raise I2CBusConfigError if any board/chip-forcing BLINKA_* var is set.
	Called once at control-process startup so nobody can force `basic`/`import
	board` onto a USB adapter via the environment."""
	environ = os.environ if environ is None else environ
	offenders = sorted(
		key
		for key in environ
		if key in _FORBIDDEN_BLINKA_EXACT or any(key.startswith(p) for p in _FORBIDDEN_BLINKA_PREFIXES)
	)
	if offenders:
		raise I2CBusConfigError(
			f'Board-forcing Blinka environment variable(s) set: {", ".join(offenders)}. '
			'Remove them and select the ft232h/mcp2221 bus kinds in the wizard instead; '
			'forcing the Blinka board via the environment breaks `basic` and any import board.'
		)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_i2c_bus.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Format and commit**

```bash
uvx ruff format common/i2c_bus.py tests/test_i2c_bus.py
git add common/i2c_bus.py tests/test_i2c_bus.py
git commit -F - <<'EOF'
feat(i2c): shared bus helpers, kind validation, and startup env guard

New common/i2c_bus.py with find_i2c_bus/resolve_i2c_bus (moved next), the
basic+USB-HID validator, and a startup guard that rejects board-forcing
BLINKA_* env vars.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

---

### Task 2: `open_i2c_bus` factory, `_LockedI2C` wrapper, cache, runtime validation

**Files:**
- Modify: `common/i2c_bus.py`
- Test: `tests/test_i2c_bus.py`

**Interfaces:**
- Consumes: Task 1's `validate_bus_kinds`, `I2CBusConfigError`, `resolve_i2c_bus`, `_UNSET`.
- Produces:
  - `open_i2c_bus(bus_kind='basic', bus_selector=None) -> busio.I2C-compatible`
  - `reset_bus_state() -> None` (clears the cache + opened-kind registry; tests only)
  - `class _LockedI2C` with `try_lock() -> bool`, `unlock()`, `scan()`, `writeto(...)`, `readfrom_into(...)`, `writeto_then_readfrom(...)`, `deinit()`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_i2c_bus.py` (the `import os` is needed by the env tests below):

```python
import os
from unittest import mock

import common.i2c_bus as i2c_bus


@pytest.fixture(autouse=True)
def _clean_bus_state():
	i2c_bus.reset_bus_state()
	yield
	i2c_bus.reset_bus_state()


def test_locked_i2c_lock_and_delegate():
	backend = mock.Mock()
	wrapped = i2c_bus._LockedI2C(backend)
	assert wrapped.try_lock() is True
	wrapped.unlock()
	wrapped.unlock()  # double unlock is safe
	wrapped.writeto(0x10, b'\x01')
	backend.writeto.assert_called_once_with(0x10, b'\x01')
	wrapped.scan()
	backend.scan.assert_called_once()


def test_open_ft232h_sets_env_transiently_and_restores(monkeypatch):
	monkeypatch.delenv('BLINKA_FT232H', raising=False)
	created = []

	class FakeBackendI2C:
		def __init__(self):
			created.append(os.environ.get('BLINKA_FT232H'))

	fake_mod = types_module_with(I2C=FakeBackendI2C)
	with mock.patch.dict('sys.modules', {'adafruit_blinka.microcontroller.ftdi_mpsse.mpsse.i2c': fake_mod}):
		bus = i2c_bus.open_i2c_bus('ft232h', 'ftdi://ftdi:232h:FT9/1')
	assert isinstance(bus, i2c_bus._LockedI2C)
	# Env was set to the selector during construction, restored (unset) after.
	assert created == ['ftdi://ftdi:232h:FT9/1']
	assert 'BLINKA_FT232H' not in os.environ


def test_open_i2c_bus_caches_per_kind_and_selector():
	class FakeBackendI2C:
		def __init__(self):
			pass

	fake_mod = types_module_with(I2C=FakeBackendI2C)
	with mock.patch.dict('sys.modules', {'adafruit_blinka.microcontroller.ftdi_mpsse.mpsse.i2c': fake_mod}):
		a = i2c_bus.open_i2c_bus('ft232h', '')
		b = i2c_bus.open_i2c_bus('ft232h', '1')  # '' and '1' are the same adapter
		c = i2c_bus.open_i2c_bus('ft232h', '')
	assert a is b is c


def test_open_i2c_bus_runtime_rejects_basic_after_ft232h():
	class FakeBackendI2C:
		def __init__(self):
			pass

	fake_mod = types_module_with(I2C=FakeBackendI2C)
	with mock.patch.dict('sys.modules', {'adafruit_blinka.microcontroller.ftdi_mpsse.mpsse.i2c': fake_mod}):
		i2c_bus.open_i2c_bus('ft232h', '')
		with pytest.raises(i2c_bus.I2CBusConfigError):
			i2c_bus.open_i2c_bus('basic')


def types_module_with(**attrs):
	import types

	mod = types.ModuleType('fake')
	for name, value in attrs.items():
		setattr(mod, name, value)
	return mod
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_i2c_bus.py -v`
Expected: FAIL — `AttributeError: module 'common.i2c_bus' has no attribute 'reset_bus_state'` / `open_i2c_bus`.

- [ ] **Step 3: Implement the factory**

Append to `common/i2c_bus.py`:

```python
class _LockedI2C:
	"""Wrap a Blinka backend I2C (ft232h/mcp2221) so Adafruit drivers can use it.

	The backend classes expose scan/writeto/readfrom_into/writeto_then_readfrom
	but not try_lock/unlock, which adafruit_bus_device.I2CDevice requires. Add a
	reentrant lock and delegate I/O to the backend."""

	def __init__(self, backend):
		self._backend = backend
		self._lock = threading.RLock()

	def try_lock(self):
		return self._lock.acquire(blocking=False)

	def unlock(self):
		try:
			self._lock.release()
		except RuntimeError:
			pass

	def scan(self):
		return self._backend.scan()

	def writeto(self, address, buffer, **kwargs):
		return self._backend.writeto(address, buffer, **kwargs)

	def readfrom_into(self, address, buffer, **kwargs):
		return self._backend.readfrom_into(address, buffer, **kwargs)

	def writeto_then_readfrom(self, address, out_buffer, in_buffer, **kwargs):
		return self._backend.writeto_then_readfrom(address, out_buffer, in_buffer, **kwargs)

	def deinit(self):
		deinit = getattr(self._backend, 'deinit', None)
		if deinit is not None:
			deinit()


_bus_cache = {}  # (kind, selector) -> bus object
_opened_kinds = set()  # kinds actually opened this process
_cache_lock = threading.RLock()


def reset_bus_state():
	"""Clear the bus cache and opened-kind registry. Tests only."""
	with _cache_lock:
		_bus_cache.clear()
		_opened_kinds.clear()


def _canonical_selector(kind, selector):
	sel = '' if selector in (None, '') else str(selector)
	# For ft232h, blank and '1' both mean "first FT232H" -> one cache entry.
	if kind == 'ft232h' and sel in ('', '1'):
		sel = ''
	return sel


def _construct_ft232h(selector):
	from adafruit_blinka.microcontroller.ftdi_mpsse.mpsse.i2c import I2C as _FT232H_I2C

	# The backend reads BLINKA_FT232H only during __init__ (get_ft232h_url()).
	# Set it transiently and restore the prior value so the factory never leaves
	# a board-forcing var in the environment (keeps assert_clean_blinka_env true
	# process-wide). If a caller pre-set it (ft232h_relay), restore keeps it set.
	prev = os.environ.get('BLINKA_FT232H', _UNSET)
	os.environ['BLINKA_FT232H'] = str(selector) if selector else '1'
	try:
		backend = _FT232H_I2C()
	finally:
		if prev is _UNSET:
			os.environ.pop('BLINKA_FT232H', None)
		else:
			os.environ['BLINKA_FT232H'] = prev
	return _LockedI2C(backend)


def _construct_mcp2221(selector):
	from adafruit_blinka.microcontroller.mcp2221 import mcp2221 as _mcp_mod
	from adafruit_blinka.microcontroller.mcp2221.i2c import I2C as _MCP2221_I2C

	if selector:
		# Point the Blinka MCP2221 singleton at the adapter with this serial.
		import hid

		path = None
		for info in hid.enumerate(_mcp_mod.MCP2221.VID, _mcp_mod.MCP2221.PID):
			if info.get('serial_number') == str(selector):
				path = info['path']
				break
		if path is None:
			raise I2CBusConfigError(f'No MCP2221 found with serial {selector!r}.')
		handle = _mcp_mod.mcp2221._hid
		try:
			handle.close()
		except Exception:
			pass
		handle.open_path(path)
	return _LockedI2C(_MCP2221_I2C())


def _construct_bus(kind, selector):
	if kind == 'basic':
		import board
		import busio

		return busio.I2C(board.SCL, board.SDA)
	if kind == 'extended':
		from adafruit_extended_bus import ExtendedI2C

		return ExtendedI2C(resolve_i2c_bus(selector))
	if kind == 'ft232h':
		return _construct_ft232h(selector)
	if kind == 'mcp2221':
		return _construct_mcp2221(selector)
	raise I2CBusConfigError(f'Unknown i2c bus kind {kind!r}.')


def open_i2c_bus(bus_kind='basic', bus_selector=None):
	"""Return a busio.I2C-compatible bus for `bus_kind`, opening it if needed.

	bus_selector is the stored i2c_bus_num value: a /dev/i2c-N number or adapter
	match for `extended`, a pyftdi URL for `ft232h`, an MCP2221 serial for
	`mcp2221`; ignored for `basic`. Buses are cached per (kind, selector) for
	the process lifetime so every device on one physical bus shares one handle
	and lock. Raises I2CBusConfigError for an unworkable combination."""
	kind = (bus_kind or 'basic').strip().lower()
	selector = _canonical_selector(kind, bus_selector)
	with _cache_lock:
		validate_bus_kinds(_opened_kinds | {kind})
		key = (kind, selector)
		bus = _bus_cache.get(key)
		if bus is None:
			bus = _construct_bus(kind, selector)
			_bus_cache[key] = bus
		_opened_kinds.add(kind)
		return bus
```

- [ ] **Step 4: Run to verify they pass**

Run: `python3 -m pytest tests/test_i2c_bus.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Format and commit**

```bash
uvx ruff format common/i2c_bus.py tests/test_i2c_bus.py
git add common/i2c_bus.py tests/test_i2c_bus.py
git commit -F - <<'EOF'
feat(i2c): open_i2c_bus factory with _LockedI2C wrapper, caching, guards

open_i2c_bus dispatches basic/extended/ft232h/mcp2221, wraps USB-HID backends
so Adafruit drivers can lock them, caches one handle per physical bus (ft232h
blank/'1' canonicalized), sets BLINKA_FT232H only transiently, and validates
the running combination on each open.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

---

### Task 3: Re-export bus helpers from `probes/base.py`

**Files:**
- Modify: `probes/base.py` (remove `find_i2c_bus`/`resolve_i2c_bus` defs at lines ~33-80; the `import glob` and `import os` become unused for this purpose — keep `os` only if still used elsewhere)
- Test: `tests/test_i2c_bus.py`

**Interfaces:**
- Consumes: `common.i2c_bus.find_i2c_bus`, `resolve_i2c_bus`.
- Produces: `probes.base.resolve_i2c_bus` and `probes.base.find_i2c_bus` remain importable (back-compat for probe/distance modules).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_i2c_bus.py`:

```python
def test_probes_base_reexports_bus_helpers():
	import common.i2c_bus as cib
	import probes.base as base

	assert base.resolve_i2c_bus is cib.resolve_i2c_bus
	assert base.find_i2c_bus is cib.find_i2c_bus
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_i2c_bus.py::test_probes_base_reexports_bus_helpers -v`
Expected: FAIL — `base.resolve_i2c_bus` is the local function, not `cib.resolve_i2c_bus` (identity mismatch).

- [ ] **Step 3: Replace the local defs with a re-export**

In `probes/base.py`, delete the entire `find_i2c_bus(...)` and `resolve_i2c_bus(...)` function definitions (the two functions under the "I2C Bus Helpers" banner). Immediately after the existing top-of-file imports (after `from probes.kalman import TempKalman`), add:

```python
# resolve_i2c_bus / find_i2c_bus now live in the shared factory; re-export so
# existing `from probes.base import resolve_i2c_bus` imports keep working.
from common.i2c_bus import find_i2c_bus, resolve_i2c_bus
```

Then remove the now-unused `import glob` line. Leave `import os` only if another function in `probes/base.py` still uses it; otherwise remove it too. (Check: `grep -n 'os\.' probes/base.py` after editing — if no hits remain, drop `import os`.)

- [ ] **Step 4: Run the affected tests**

Run: `python3 -m pytest tests/test_i2c_bus.py tests/test_mcp9600_probe.py tests/test_max31856_probe.py -v`
Expected: PASS.

- [ ] **Step 5: Format and commit**

```bash
uvx ruff format probes/base.py
git add probes/base.py tests/test_i2c_bus.py
git commit -F - <<'EOF'
refactor(probes): source resolve_i2c_bus/find_i2c_bus from common.i2c_bus

Remove the duplicated implementations in probes/base.py and re-export the
shared ones so existing imports keep working.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

---

### Task 4: Migrate the `busio` probes to `open_i2c_bus`

**Files:**
- Modify: `probes/mcp9600_adafruit.py`, `probes/ads1115_adafruit.py`, `probes/ads1015_adafruit.py`
- Test: `tests/test_mcp9600_probe.py`

**Interfaces:**
- Consumes: `common.i2c_bus.open_i2c_bus`.
- Produces: each device's `__init__` sets `self.i2c = open_i2c_bus(i2c_bus_kind, i2c_bus_num)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_mcp9600_probe.py`:

```python
from unittest import mock


def test_kttdevice_opens_bus_via_factory(monkeypatch):
	import probes.mcp9600_adafruit as mod

	fake_bus = object()
	opened = {}

	def fake_open(kind, selector):
		opened['args'] = (kind, selector)
		return fake_bus

	monkeypatch.setattr(mod, 'open_i2c_bus', fake_open)
	monkeypatch.setattr(mod, 'MCP9600', mock.Mock())

	dev = mod.KTTDevice(i2c_bus_addr=0x67, i2c_bus_kind='ft232h', i2c_bus_num='1', tc_type='K')
	assert dev.i2c is fake_bus
	assert opened['args'] == ('ft232h', '1')
	mod.MCP9600.assert_called_once()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_mcp9600_probe.py::test_kttdevice_opens_bus_via_factory -v`
Expected: FAIL — `module 'probes.mcp9600_adafruit' has no attribute 'open_i2c_bus'`.

- [ ] **Step 3: Migrate `mcp9600_adafruit.py`**

In `probes/mcp9600_adafruit.py`, change the import line
`from probes.base import ProbeInterface, resolve_i2c_bus`
to:

```python
from probes.base import ProbeInterface
from common.i2c_bus import open_i2c_bus
```

Replace the bus-opening branch in `KTTDevice.__init__`:

```python
		if i2c_bus_kind == 'basic':
			# Create the I2C bus
			self.i2c = busio.I2C(board.SCL, board.SDA)
		elif i2c_bus_kind == 'extended':
			self.i2c = ExtendedI2C(resolve_i2c_bus(i2c_bus_num))
```

with:

```python
		self.i2c = open_i2c_bus(i2c_bus_kind, i2c_bus_num)
```

Remove the now-unused `import board`, `import busio`, and `from adafruit_extended_bus import ExtendedI2C` from the top of the file.

- [ ] **Step 4: Migrate `ads1115_adafruit.py` and `ads1015_adafruit.py`**

In each of `probes/ads1115_adafruit.py` and `probes/ads1015_adafruit.py`, apply the same edit: import `open_i2c_bus` from `common.i2c_bus` (and drop `resolve_i2c_bus` from the `probes.base` import), replace the `if i2c_bus_kind == 'basic' … elif … extended …` block in `ADSDevice.__init__` with:

```python
		self.i2c = open_i2c_bus(i2c_bus_kind, i2c_bus_num)
```

and remove the now-unused `import board`, `import busio`, `from adafruit_extended_bus import ExtendedI2C`.

- [ ] **Step 5: Run tests, format, commit**

Run: `python3 -m pytest tests/test_mcp9600_probe.py -v`
Expected: PASS.

```bash
uvx ruff format probes/mcp9600_adafruit.py probes/ads1115_adafruit.py probes/ads1015_adafruit.py tests/test_mcp9600_probe.py
git add probes/mcp9600_adafruit.py probes/ads1115_adafruit.py probes/ads1015_adafruit.py tests/test_mcp9600_probe.py
git commit -F - <<'EOF'
refactor(probes): open busio probe buses via the shared factory

mcp9600_adafruit, ads1115_adafruit, ads1015_adafruit now call open_i2c_bus,
which adds ft232h/mcp2221 support and shared bus caching.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

---

### Task 5: Migrate the distance base to `open_i2c_bus`

**Files:**
- Modify: `distance/_tof_base.py`
- Test: `tests/test_tof_base.py`

**Interfaces:**
- Consumes: `common.i2c_bus.open_i2c_bus`.
- Produces: `ToFHopperLevel._open_i2c_bus()` returns `open_i2c_bus(self.i2c_bus_kind, self.i2c_bus_num)`.

- [ ] **Step 1: Update the test fixture and add a test**

In `tests/test_tof_base.py`, change the `tof_mod` fixture to patch the factory instead of `busio`/`board`/`ExtendedI2C`/`resolve_i2c_bus`:

```python
@pytest.fixture
def tof_mod():
	import distance._tof_base as mod

	with mock.patch.object(mod, 'open_i2c_bus', return_value=mock.sentinel.bus):
		yield mod
```

Add a test:

```python
def test_open_i2c_bus_delegates_to_factory(tof_mod):
	hopper = _make_hopper(tof_mod, dev_pins={'distance': {'i2c_bus_kind': 'ft232h', 'i2c_bus_num': '1'}})
	try:
		assert hopper.opened_with[0] is mock.sentinel.bus
		tof_mod.open_i2c_bus.assert_called_with('ft232h', '1')
	finally:
		_stop(hopper)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_tof_base.py::test_open_i2c_bus_delegates_to_factory -v`
Expected: FAIL — `module 'distance._tof_base' has no attribute 'open_i2c_bus'`.

- [ ] **Step 3: Migrate `_tof_base.py`**

In `distance/_tof_base.py`, replace the imports
```python
import board
import busio
from adafruit_extended_bus import ExtendedI2C

from probes.base import resolve_i2c_bus
```
with:
```python
from common.i2c_bus import open_i2c_bus
```

Replace `_open_i2c_bus`:

```python
	def _open_i2c_bus(self):
		if self.i2c_bus_kind == 'extended':
			return ExtendedI2C(resolve_i2c_bus(self.i2c_bus_num))
		return busio.I2C(board.SCL, board.SDA)
```

with:

```python
	def _open_i2c_bus(self):
		return open_i2c_bus(self.i2c_bus_kind, self.i2c_bus_num)
```

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_tof_base.py -v`
Expected: PASS (existing tests + the new one).

- [ ] **Step 5: Format and commit**

```bash
uvx ruff format distance/_tof_base.py tests/test_tof_base.py
git add distance/_tof_base.py tests/test_tof_base.py
git commit -F - <<'EOF'
refactor(distance): open the ToF sensor bus via the shared factory

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

---

### Task 6: Migrate `grillplat/x86_numato.py` EMC bus to `open_i2c_bus`

**Files:**
- Modify: `grillplat/x86_numato.py` (drop local `find_i2c_bus`/`resolve_i2c_bus`; open EMC bus via factory)
- Test: `tests/test_x86_numato_fan.py` (create)

**Interfaces:**
- Consumes: `common.i2c_bus.open_i2c_bus`.
- Produces: EMC controller constructed on `open_i2c_bus(self.i2c_bus_kind, self.i2c_bus_num)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_x86_numato_fan.py`:

```python
from unittest import mock


def _base_config(**fan):
	return {
		'outputs': {'power': 0, 'igniter': 1, 'auger': 2, 'fan': 3},
		'numato': {'device': '/dev/ttyACM0'},
		'fan_controller': fan,
		'frequency': 25000,
	}


def _make(config):
	import grillplat.x86_numato as mod

	with (
		mock.patch.object(mod, 'open_i2c_bus', return_value=mock.sentinel.bus) as open_bus,
		mock.patch.object(mod, 'NumatoUSBRelay'),
		mock.patch.object(mod, 'EMC2101_LUT') as emc2101,
		mock.patch.object(mod, 'EMC2301') as emc2301,
	):
		platform = mod.GrillPlatform(config)
		return platform, open_bus, emc2101, emc2301


def test_emc_bus_opened_via_factory_mcp2221():
	platform, open_bus, emc2101, emc2301 = _make(_base_config(chip='emc2101', i2c_bus_kind='mcp2221', i2c_bus_num='SERIAL9'))
	open_bus.assert_called_once_with('mcp2221', 'SERIAL9')
	emc2101.assert_called_once_with(mock.sentinel.bus)
	emc2301.assert_not_called()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_x86_numato_fan.py -v`
Expected: FAIL — `module 'grillplat.x86_numato' has no attribute 'open_i2c_bus'`.

- [ ] **Step 3: Migrate `x86_numato.py`**

In `grillplat/x86_numato.py`:

1. Delete the module-level `find_i2c_bus(...)` and `resolve_i2c_bus(...)` function definitions (under "Module Helpers").
2. Replace the top imports
   ```python
   import board
   import busio
   from adafruit_extended_bus import ExtendedI2C
   from adafruit_emc2101.emc2101_lut import EMC2101_LUT
   ```
   with:
   ```python
   from adafruit_emc2101.emc2101_lut import EMC2101_LUT

   from common.i2c_bus import open_i2c_bus
   ```
   (Also drop `import glob` and `import os` if they are now unused — check with `grep -nE '\b(glob|os)\.' grillplat/x86_numato.py`.)
3. Replace the EMC bus construction
   ```python
		# Open the fan controller on the configured I2C bus.
		if self.i2c_bus_kind == 'extended':
			i2c = ExtendedI2C(resolve_i2c_bus(self.i2c_bus_num))
		else:
			i2c = busio.I2C(board.SCL, board.SDA)
   ```
   with:
   ```python
		# Open the fan controller on the configured I2C bus.
		i2c = open_i2c_bus(self.i2c_bus_kind, self.i2c_bus_num)
   ```

The existing `self.i2c_bus_kind`/`self.i2c_bus_num` resolution (including the legacy `i2c_bus_match` fallback) stays unchanged.

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_x86_numato_fan.py tests/test_x86_manifest.py -v`
Expected: PASS.

- [ ] **Step 5: Format and commit**

```bash
uvx ruff format grillplat/x86_numato.py tests/test_x86_numato_fan.py
git add grillplat/x86_numato.py tests/test_x86_numato_fan.py
git commit -F - <<'EOF'
refactor(grillplat): open x86_numato EMC bus via the shared factory

Drop the duplicated resolve_i2c_bus/find_i2c_bus and add ft232h/mcp2221
support for the EMC fan controller through common.i2c_bus.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

---

### Task 7: Unify FT232H access in `grillplat/ft232h_relay.py`

**Files:**
- Modify: `grillplat/ft232h_relay.py`
- Modify: `tests/ft232h_helpers.py`
- Test: `tests/test_ft232h_fan.py`

**Interfaces:**
- Consumes: `common.i2c_bus.open_i2c_bus`.
- Produces: `GrillPlatform.__init__` opens the FT232H bus via the factory before creating relay pins; `self.emc` is built on that factory bus; relay pins are created after (reusing `Pin.mpsse_gpio`). No direct `busio.I2C(...)` call remains.

- [ ] **Step 1: Update the harness and fan test**

In `tests/ft232h_helpers.py`, add a factory patch to `make_ft232h_platform` and surface it on the harness. Replace the `with (...)` block and harness assembly:

```python
	fake_board = FakeBoard()
	fake_dio = FakeDigitalIO()
	with (
		mock.patch.object(mod, '_load_ft232h', return_value=(fake_board, fake_dio)),
		mock.patch.object(mod, 'open_i2c_bus', return_value=mock.sentinel.ft232h_bus) as open_bus,
		mock.patch.object(mod, 'EMC2101_LUT') as emc2101_cls,
		mock.patch.object(mod, 'EMC2301') as emc2301_cls,
	):
		platform = mod.GrillPlatform(config)
		harness = types.SimpleNamespace(
			board=fake_board,
			dio=fake_dio,
			open_bus=open_bus,
			emc2101_cls=emc2101_cls,
			emc2301_cls=emc2301_cls,
		)
		yield platform, harness
```

Add `from unittest import mock` is already imported at the top of the helper (`from unittest import mock`) — keep it. Remove the now-unused `mock.patch.object(mod, 'busio')` line and the `busio=busio_mod` field.

In `tests/test_ft232h_fan.py`, update `test_emc2101_init_opens_i2c_and_controller` to assert the factory path:

```python
def test_emc2101_init_opens_i2c_and_controller():
	with make_ft232h_platform(_emc_config('emc2101')) as (plat, harness):
		assert plat.pwm_fan is True
		harness.open_bus.assert_called_once_with('ft232h', '1')
		harness.emc2101_cls.assert_called_once_with(mock.sentinel.ft232h_bus)
		harness.emc2301_cls.assert_not_called()
		assert plat.emc is harness.emc2101_cls.return_value
		assert plat.emc.lut_enabled is False
		assert plat.emc.manual_fan_speed == 0
```

Add `from unittest import mock` to the top of `tests/test_ft232h_fan.py`.

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_ft232h_fan.py::test_emc2101_init_opens_i2c_and_controller -v`
Expected: FAIL — `mod` has no attribute `open_i2c_bus` (patch target missing) / `busio` assertions gone.

- [ ] **Step 3: Refactor `ft232h_relay.py`**

In `grillplat/ft232h_relay.py`:

1. Add the factory import near the top (after `import busio`):
   ```python
   from common.i2c_bus import open_i2c_bus
   ```
2. In `GrillPlatform.__init__`, open the FT232H bus through the factory **before** creating relay pins, and hold it for the EMC. Replace the block:
   ```python
		# Open the FT232H and create one output pin per PiFire output.
		board, digitalio = _load_ft232h(self.url)
   ```
   with:
   ```python
		# Open the FT232H I2C bus through the shared factory FIRST. This creates
		# the single MPSSE controller (and sets Blinka's Pin.mpsse_gpio), so the
		# relay GPIO pins below and any ft232h probe reuse one controller instead
		# of fighting over the FT232H's single MPSSE engine.
		self._ft232h_bus = open_i2c_bus('ft232h', self.url)

		# Now import the ft232h board/digitalio and create one pin per output;
		# these reuse the controller established above via Pin.mpsse_gpio.
		board, digitalio = _load_ft232h(self.url)
   ```
3. Replace `_init_fan_controller` so the EMC uses the factory bus instead of a fresh `busio.I2C`:
   ```python
	def _init_fan_controller(self, board):
		# EMC fan controller on the FT232H's own I2C bus (D0=SCL, D1/D2=SDA).
		i2c = busio.I2C(board.SCL, board.SDA)
		if self.chip == 'emc2301':
   ```
   becomes:
   ```python
	def _init_fan_controller(self, board):
		# EMC fan controller on the shared FT232H bus opened in __init__.
		i2c = self._ft232h_bus
		if self.chip == 'emc2301':
   ```
   (Leave the rest of `_init_fan_controller` unchanged.) The `import busio` line may now be unused — remove it if `grep -n 'busio' grillplat/ft232h_relay.py` shows no remaining use.

`self.url` is already computed above these lines from `ft232h_cfg.get('url', '1')`, so `open_i2c_bus('ft232h', self.url)` receives `'1'` by default (canonicalized to the first FT232H).

- [ ] **Step 4: Run the FT232H tests**

Run: `python3 -m pytest tests/test_ft232h_fan.py tests/test_ft232h_outputs.py tests/test_ft232h_system.py -v`
Expected: PASS. (The outputs/system tests exercise relays and still pass because `_load_ft232h` is faked and the new factory call is faked to a sentinel.)

- [ ] **Step 5: Format and commit**

```bash
uvx ruff format grillplat/ft232h_relay.py tests/ft232h_helpers.py tests/test_ft232h_fan.py
git add grillplat/ft232h_relay.py tests/ft232h_helpers.py tests/test_ft232h_fan.py
git commit -F - <<'EOF'
refactor(grillplat): ft232h_relay shares one MPSSE controller via the factory

Open the FT232H bus through common.i2c_bus before creating relay GPIO pins, so
relays, the EMC controller, and any ft232h probe share a single pyftdi
controller instead of opening the FT232H's single MPSSE engine multiple times.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

---

### Task 8: Call the startup guard in `build_devices`

**Files:**
- Modify: `controller/runtime/devices.py` (top of `build_devices`)
- Test: `tests/test_build_devices_env_guard.py` (create)

**Interfaces:**
- Consumes: `common.i2c_bus.assert_clean_blinka_env`, `I2CBusConfigError`.
- Produces: `build_devices` raises `I2CBusConfigError` before constructing any hardware if a board-forcing `BLINKA_*` var is set.

- [ ] **Step 1: Write the failing test**

Create `tests/test_build_devices_env_guard.py`:

```python
import pytest

from common.i2c_bus import I2CBusConfigError


def test_build_devices_rejects_board_forcing_env(monkeypatch):
	import controller.runtime.devices as devices

	monkeypatch.setenv('BLINKA_FT232H', '1')
	with pytest.raises(I2CBusConfigError):
		devices.build_devices({}, errors=[], event_log=None, control_log=None)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_build_devices_env_guard.py -v`
Expected: FAIL — no guard yet (it proceeds past the check and raises some other error, or returns).

- [ ] **Step 3: Add the guard**

In `controller/runtime/devices.py`, add the import near the top:

```python
from common.i2c_bus import assert_clean_blinka_env
```

As the very first statement inside `build_devices(...)` (before it reads `settings['platform']` or imports any hardware module), add:

```python
	# Refuse to start if a board-forcing BLINKA_* env var is set: it would pin
	# Blinka's `board` backend process-wide and silently break `basic` and any
	# import board. Devices must select ft232h/mcp2221 bus kinds instead.
	assert_clean_blinka_env()
```

- [ ] **Step 4: Run the test**

Run: `python3 -m pytest tests/test_build_devices_env_guard.py -v`
Expected: PASS (raises `I2CBusConfigError` before touching `settings`).

- [ ] **Step 5: Format and commit**

```bash
uvx ruff format controller/runtime/devices.py tests/test_build_devices_env_guard.py
git add controller/runtime/devices.py tests/test_build_devices_env_guard.py
git commit -F - <<'EOF'
feat(control): reject board-forcing BLINKA_* env at device build

build_devices now calls assert_clean_blinka_env() before constructing any
hardware, so an operator can't force basic/import board onto a USB adapter.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

---

### Task 9: Add `ft232h`/`mcp2221` to the wizard manifest selectors

**Files:**
- Modify: `wizard/wizard_manifest.json`
- Test: `tests/test_x86_manifest.py`, `tests/test_distance_manifest.py`, `tests/test_mcp9600_probe.py`

**Interfaces:**
- Produces: the `i2c_bus_kind` selectors for `mcp9600_adafruit`, `ads1115_adafruit`, `ads1015_adafruit` probe configs, the distance `device_distance_i2c_bus_kind` settings-dependency, and the platform `fan_controller` `i2c_bus_kind` settings-dependency all offer `ft232h` and `mcp2221`.

- [ ] **Step 1: Write the failing manifest tests**

Append to `tests/test_x86_manifest.py` (which already loads the manifest; reuse its existing `deps` accessor pattern):

```python
def test_x86_fan_bus_kind_includes_usb_hid():
	import json
	import os

	manifest = json.load(open(os.path.join(os.path.dirname(__file__), '..', 'wizard', 'wizard_manifest.json')))
	# Locate the x86_numato fan_controller i2c_bus_kind options.
	numato = manifest['modules']['grillplatform']['x86_numato']
	deps = numato['settings_dependencies']
	options = set(deps['i2c_bus_kind']['options'])
	assert {'basic', 'extended', 'ft232h', 'mcp2221'} <= options
```

Append to `tests/test_distance_manifest.py` a check that at least one `device_distance_i2c_bus_kind` selector includes the USB kinds:

```python
def test_distance_bus_kind_includes_usb_hid():
	import json
	import os

	manifest = json.load(open(os.path.join(os.path.dirname(__file__), '..', 'wizard', 'wizard_manifest.json')))
	found = []

	def walk(node):
		if isinstance(node, dict):
			opts = node.get('options')
			if isinstance(opts, dict) and 'basic' in opts and 'extended' in opts:
				found.append(set(opts))
			for value in node.values():
				walk(value)
		elif isinstance(node, list):
			for value in node:
				walk(value)

	walk(manifest['modules'])
	assert found, 'no bus-kind selectors found'
	assert all({'ft232h', 'mcp2221'} <= opts for opts in found)
```

Append to `tests/test_mcp9600_probe.py`:

```python
def test_mcp9600_manifest_bus_kind_includes_usb_hid():
	import json
	import os

	manifest = json.load(open(os.path.join(os.path.dirname(__file__), '..', 'wizard', 'wizard_manifest.json')))
	cfg = manifest['modules']['probes']['mcp9600_adafruit']['device_specific']['config']
	bus_kind = next(item for item in cfg if item['label'] == 'i2c_bus_kind')
	assert bus_kind['list_values'] == ['basic', 'extended', 'ft232h', 'mcp2221']
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_x86_manifest.py tests/test_distance_manifest.py tests/test_mcp9600_probe.py -v`
Expected: FAIL — options currently only `basic`/`extended`.

- [ ] **Step 3: Edit the manifest**

In `wizard/wizard_manifest.json`, for the **probe** configs `mcp9600_adafruit`, `ads1115_adafruit`, and `ads1015_adafruit` only (NOT `ads1115` or `prototype`), update each `i2c_bus_kind` entry:

```json
"list_values": ["basic", "extended", "ft232h", "mcp2221"],
"list_labels": ["Basic (native I2C)", "Extended (USB / other bus)", "FT232H (USB)", "MCP2221 (USB)"],
```

For every `device_distance_i2c_bus_kind` settings-dependency (each platform board that has one) and the platform `fan_controller` `i2c_bus_kind` settings-dependency, update the `options` object to:

```json
"options": {
  "basic": "Basic (integrated I2C bus)",
  "extended": "Extended (numbered / bridge bus)",
  "ft232h": "FT232H (USB)",
  "mcp2221": "MCP2221 (USB)"
}
```

Update every `i2c_bus_num` description (probe config `description`, and the distance/fan `device_*_i2c_bus_num` entries) to note the selector doubles as the USB device selector, e.g.:

```
"For extended: a /dev/i2c-N number or adapter-name match. For ft232h: a pyftdi URL (blank = first). For mcp2221: a device serial (blank = first)."
```

Use a JSON-aware edit — after editing, verify validity:
`python3 -c "import json; json.load(open('wizard/wizard_manifest.json')); print('OK')"`

- [ ] **Step 4: Run the manifest tests**

Run: `python3 -m pytest tests/test_x86_manifest.py tests/test_distance_manifest.py tests/test_mcp9600_probe.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

(JSON is not ruff-formatted; do not run ruff on it.)

```bash
git add wizard/wizard_manifest.json tests/test_x86_manifest.py tests/test_distance_manifest.py tests/test_mcp9600_probe.py
git commit -F - <<'EOF'
feat(wizard): offer ft232h/mcp2221 bus kinds for busio I2C devices

Add the two USB-HID bus kinds to the i2c_bus_kind selectors for the busio
probes, the distance sensor, and the platform fan controller, and document the
i2c_bus_num selector's per-kind meaning.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

---

### Task 10: Wizard save-time validation of the bus-kind combination

**Files:**
- Modify: `common/i2c_bus.py` (add `configured_bus_kinds`)
- Modify: `blueprints/probeconfig/routes.py` (validate on `add_device` / `edit_device`)
- Test: `tests/test_i2c_bus_wizard_validation.py` (create)

**Interfaces:**
- Consumes: `common.i2c_bus.validate_bus_kinds`, `I2CBusConfigError`.
- Produces: `configured_bus_kinds(settings, probe_map) -> set[str]` — every I2C bus kind across probe devices, the distance sensor, and the platform fan controller.

- [ ] **Step 1: Write the failing test**

Create `tests/test_i2c_bus_wizard_validation.py`:

```python
import pytest

from common.i2c_bus import I2CBusConfigError, configured_bus_kinds, validate_bus_kinds


def _settings(distance_kind=None, fan_kind=None):
	return {
		'platform': {
			'devices': {'distance': {'i2c_bus_kind': distance_kind} if distance_kind else {}},
			'fan_controller': {'i2c_bus_kind': fan_kind} if fan_kind else {},
		}
	}


def _probe_map(*kinds):
	return {'probe_devices': [{'config': {'i2c_bus_kind': k}} for k in kinds]}


def test_configured_bus_kinds_collects_all_surfaces():
	kinds = configured_bus_kinds(_settings(distance_kind='ft232h', fan_kind='mcp2221'), _probe_map('ft232h', 'extended'))
	assert kinds == {'ft232h', 'mcp2221', 'extended'}


def test_configured_bus_kinds_conflict_raises_when_validated():
	kinds = configured_bus_kinds(_settings(fan_kind='basic'), _probe_map('ft232h'))
	with pytest.raises(I2CBusConfigError):
		validate_bus_kinds(kinds)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_i2c_bus_wizard_validation.py -v`
Expected: FAIL — `cannot import name 'configured_bus_kinds'`.

- [ ] **Step 3: Add `configured_bus_kinds`**

Append to `common/i2c_bus.py`:

```python
def configured_bus_kinds(settings, probe_map):
	"""Collect every I2C bus kind across probe devices, the distance sensor, and
	the platform fan controller. Used to validate a whole wizard config."""
	kinds = set()
	for device in (probe_map or {}).get('probe_devices', []):
		kind = (device.get('config') or {}).get('i2c_bus_kind')
		if kind:
			kinds.add(kind)
	platform = (settings or {}).get('platform', {})
	distance = (platform.get('devices', {}) or {}).get('distance', {}) or {}
	if distance.get('i2c_bus_kind'):
		kinds.add(distance['i2c_bus_kind'])
	fan = platform.get('fan_controller', {}) or {}
	if fan.get('i2c_bus_kind'):
		kinds.add(fan['i2c_bus_kind'])
	return kinds
```

- [ ] **Step 4: Run to verify the helper tests pass**

Run: `python3 -m pytest tests/test_i2c_bus_wizard_validation.py -v`
Expected: PASS.

- [ ] **Step 5: Wire validation into the probeconfig save path**

In `blueprints/probeconfig/routes.py`, add the import:

```python
from common.i2c_bus import I2CBusConfigError, configured_bus_kinds, validate_bus_kinds
```

In the `add_device` branch, after `new_device` is fully assembled and **before** `wizardInstallInfo['probe_map']['probe_devices'].append(new_device)` / `store_wizard_install_info(...)`, insert a validation guard that includes the candidate device:

```python
					candidate = {
						'probe_devices': wizardInstallInfo['probe_map']['probe_devices'] + [new_device]
					}
					try:
						validate_bus_kinds(configured_bus_kinds(settings, candidate))
					except I2CBusConfigError as exc:
						alerts.append({'message': str(exc), 'type': 'error'})
						errors += 1
```

Guard the append/store so a conflicting device is not persisted:

```python
					if errors == 0:
						wizardInstallInfo['probe_map']['probe_devices'].append(new_device)
						store_wizard_install_info(wizardInstallInfo)
```

Apply the equivalent guard in the `edit_device` branch: build the candidate probe_devices list with the edited device substituted at its index, validate, and only `store_wizard_install_info` when `errors == 0`.

- [ ] **Step 6: Add a route-level test**

Append to `tests/test_i2c_bus_wizard_validation.py` a test that drives the add-device conflict through the validator seam (unit-level, no Flask app needed):

```python
def test_add_conflicting_probe_is_rejected():
	# basic fan + ft232h probe is the one unworkable combination.
	kinds = configured_bus_kinds(_settings(fan_kind='basic'), _probe_map('ft232h'))
	with pytest.raises(I2CBusConfigError):
		validate_bus_kinds(kinds)
	# a workable combination validates cleanly
	validate_bus_kinds(configured_bus_kinds(_settings(fan_kind='mcp2221'), _probe_map('ft232h')))
```

- [ ] **Step 7: Run tests, format, commit**

Run: `python3 -m pytest tests/test_i2c_bus_wizard_validation.py -v`
Expected: PASS.

```bash
uvx ruff format common/i2c_bus.py blueprints/probeconfig/routes.py tests/test_i2c_bus_wizard_validation.py
git add common/i2c_bus.py blueprints/probeconfig/routes.py tests/test_i2c_bus_wizard_validation.py
git commit -F - <<'EOF'
feat(wizard): block saving an unworkable I2C bus combination

configured_bus_kinds() gathers every bus kind across probes, distance, and the
fan controller; the probeconfig add/edit paths validate it and surface an alert
instead of persisting a basic + USB-HID config.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

---

### Task 11: Full-suite regression pass

**Files:** none (verification only)

- [ ] **Step 1: Run the whole suite**

Run: `python3 -m pytest -q`
Expected: PASS (no regressions across probes, distance, platform, wizard, manifest tests).

- [ ] **Step 2: Confirm formatting is clean**

Run: `uvx ruff format --check common/i2c_bus.py probes/base.py probes/mcp9600_adafruit.py probes/ads1115_adafruit.py probes/ads1015_adafruit.py distance/_tof_base.py grillplat/x86_numato.py grillplat/ft232h_relay.py controller/runtime/devices.py blueprints/probeconfig/routes.py`
Expected: "… files already formatted".

- [ ] **Step 3: Import smoke check**

Run: `python3 -c "import common.i2c_bus; import probes.base; import distance._tof_base; print('imports OK')"`
Expected: `imports OK` (no Blinka hardware opened — all backend imports are lazy).

---

## Self-Review

**Spec coverage:**
- Shared factory `common/i2c_bus.py` — Tasks 1, 2. ✅
- `_LockedI2C` wrapper — Task 2. ✅
- Cache per (kind, selector) incl. ft232h blank/'1' canonicalization — Task 2. ✅
- `basic`/`extended`/`ft232h`/`mcp2221` construction incl. transient env — Task 2. ✅
- `validate_bus_kinds` + runtime backstop — Tasks 1, 2. ✅
- `assert_clean_blinka_env` + wiring — Tasks 1, 8. ✅
- De-duplicate `resolve_i2c_bus` (probes.base re-export; x86_numato drops copy) — Tasks 3, 6. ✅
- Migrate busio probes / distance / x86_numato / ft232h_relay — Tasks 4, 5, 6, 7. ✅
- FT232H single-controller sharing — Task 7. ✅
- Wizard manifest (busio devices only) — Task 9. ✅
- Wizard save-time validation — Task 10. ✅
- Testing via faked backends — every task. ✅
- Scope excludes smbus `ads1115` and `prototype` — Task 9 explicitly. ✅

**Placeholder scan:** No TBD/TODO; every code step shows complete code.

**Type consistency:** `open_i2c_bus(bus_kind, bus_selector)` signature is consistent across Tasks 2/4/5/6/7. `validate_bus_kinds`, `configured_bus_kinds`, `assert_clean_blinka_env`, `_LockedI2C`, `I2CBusConfigError` names match across all tasks and tests.
