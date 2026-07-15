# FT232H via pyftdi (drop Blinka) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Drive the FT232H's I2C bus and relay GPIO through pyftdi directly, removing Adafruit Blinka's process-global `board`/`digitalio` singleton from the FT232H path, and split the two USB-HID backends into `common/ft232h.py` and `common/mcp2221.py`.

**Architecture:** One `pyftdi.i2c.I2cController` per FT232H (cached by url) hands out both `get_port()` (I2C, for the EMC fan) and `get_gpio()` (relays). A `_PyFtdiI2CBackend` presents the busio-compatible surface `_LockedI2C` expects; an `Ft232hGpio` helper maps `C0`/`D4`-style names to bits and does atomic shadow-register writes. `common/i2c_bus.py` slims to a factory that lazily dispatches `ft232h`/`mcp2221` to the new modules.

**Tech Stack:** Python 3.14, pyftdi 0.57.2 (already a dep), pytest, ruff.

## Global Constraints

- **Blinka stays a dependency.** Only the FT232H path stops using `board`/`digitalio`. `basic` I2C, native SPI probes, and native GPIO keep using Blinka.
- **No config-format or wizard change.** Relay pin names stay `C0`–`C7` / `D4`–`D7`; existing saved configs must keep working. Names translate to bits internally.
- **Usable relay pins:** `C0`–`C7` → `1 << (8 + n)`; `D4`–`D7` → `1 << n`. Any other name (including `D0`–`D3`) → `ValueError`. This matches Blinka's historically-exposed set.
- **Error mapping:** pyftdi `I2cNackError`/`I2cIOError`/`I2cTimeoutError` → `OSError` in the I2C backend (what adafruit_bus_device / probe code treat as "no device"/"bus fault").
- **I2C frequency:** configure the controller at `100_000` Hz (Blinka mpsse default).
- **No import cycle:** backend modules import `_LockedI2C` from `common.i2c_bus` at top level; `i2c_bus` imports the backend modules only lazily (inside functions).
- **Run tests with:** `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/` — bare `python`/`pytest` gives false failures. Format with `uvx ruff format <files>` before each commit.
- **zsh commit gotcha:** commit messages containing backticks must be passed via `git commit -F <file>`, not `-m`.

---

### Task 1: Extract the MCP2221 backend into `common/mcp2221.py`

Pure relocation — no behavior change. This unblocks the same pattern for ft232h and shrinks `i2c_bus.py`.

**Files:**
- Create: `common/mcp2221.py`
- Modify: `common/i2c_bus.py` (remove moved code; lazy-dispatch; re-export)
- Modify: `tests/test_i2c_bus.py` (repoint `_EasyMCP2221Backend` references)

**Interfaces:**
- Produces (in `common/mcp2221.py`):
  - `MCP2221_VID: int = 0x04D8`, `MCP2221_PID: int = 0x00DD`
  - `discover_mcp2221_devices() -> list[dict]`
  - `_EasyMCP2221Backend` (class; ctor `(device)`)
  - `construct_i2c_bus(selector) -> i2c_bus._LockedI2C`
  - `reset_state() -> None` (clears the per-Device dedup registry)
- Consumes: `from common.i2c_bus import _LockedI2C`.

- [ ] **Step 1: Create `common/mcp2221.py` with the moved code**

Move the following out of `common/i2c_bus.py` verbatim (adjusting names as noted) into a new `common/mcp2221.py`:

```python
#!/usr/bin/env python3

"""FT232H's sibling: the MCP2221 USB-I2C adapter backend.

Uses EasyMCP2221.Device rather than Blinka's MCP2221 backend, which is a
process-wide singleton (selecting a second serial silently steals the first
bus's HID handle). EasyMCP2221.Device is per-adapter, so multiple MCP2221s can
be open at once. See docs/superpowers/specs/2026-07-14-mcp2221-easymcp2221-backend-design.md.
"""

import logging
import threading

from common.i2c_bus import _LockedI2C

logger = logging.getLogger('control')

# MCP2221(A) chip's fixed USB VID/PID.
MCP2221_VID = 0x04D8
MCP2221_PID = 0x00DD


def discover_mcp2221_devices():
    """Best-effort list of connected MCP2221 USB devices ({'serial', 'path'}),
    for the wizard's Discover button. Returns [] if the `hid` module isn't
    importable, or no devices are present -- never raises."""
    try:
        import hid
    except ImportError:
        return []
    try:
        return sorted(
            (
                {'serial': info.get('serial_number'), 'path': info.get('path')}
                for info in hid.enumerate(MCP2221_VID, MCP2221_PID)
                if info.get('serial_number')
            ),
            key=lambda d: d['serial'].lower(),
        )
    except Exception:
        logger.debug('discover_mcp2221_devices: hid.enumerate failed', exc_info=True)
        return []


class _EasyMCP2221Backend:
    """Adapt an EasyMCP2221.Device to the scan/writeto/readfrom_into/
    writeto_then_readfrom surface _LockedI2C expects. Translates EasyMCP2221's
    NotAckError/TimeoutError/LowSCLError/LowSDAError into OSError."""

    def __init__(self, device):
        from EasyMCP2221.exceptions import LowSCLError, LowSDAError, NotAckError, TimeoutError

        self._device = device
        self._errors = (NotAckError, TimeoutError, LowSCLError, LowSDAError)

    def scan(self):
        found = []
        for address in range(0x08, 0x78):
            try:
                self._device.I2C_read(address, 1)
            except self._errors:
                continue
            found.append(address)
        return found

    def writeto(self, address, buffer, *, start=0, end=None, **kwargs):
        end = len(buffer) if end is None else end
        data = bytes(buffer[start:end])
        try:
            if data:
                self._device.I2C_write(address, data)
            else:
                self._device.I2C_read(address, 1)
        except self._errors as exc:
            raise OSError(str(exc)) from exc

    def readfrom_into(self, address, buffer, *, start=0, end=None, **kwargs):
        end = len(buffer) if end is None else end
        try:
            data = self._device.I2C_read(address, end - start)
        except self._errors as exc:
            raise OSError(str(exc)) from exc
        buffer[start:end] = data

    def writeto_then_readfrom(
        self, address, out_buffer, in_buffer, *, out_start=0, out_end=None, in_start=0, in_end=None, **kwargs
    ):
        out_end = len(out_buffer) if out_end is None else out_end
        in_end = len(in_buffer) if in_end is None else in_end
        try:
            self._device.I2C_write(address, bytes(out_buffer[out_start:out_end]), kind='nonstop')
            data = self._device.I2C_read(address, in_end - in_start, kind='restart')
        except self._errors as exc:
            raise OSError(str(exc)) from exc
        in_buffer[in_start:in_end] = data


# EasyMCP2221.Device -> _LockedI2C. Keyed by the Device object itself (identity),
# since EasyMCP2221.Device.__new__ returns the SAME object for the SAME physical
# adapter regardless of selector spelling; this makes an aliasing selector reuse
# the existing bus/lock instead of double-wrapping under an independent lock.
_mcp2221_bus_by_device = {}
_lock = threading.RLock()


def reset_state():
    """Clear the per-Device dedup registry. Tests only."""
    with _lock:
        _mcp2221_bus_by_device.clear()


def _open_mcp2221_device(selector):
    from common.i2c_bus import I2CBusConfigError
    from EasyMCP2221 import Device as _MCP2221Device

    try:
        if selector:
            logger.debug('open_i2c_bus[mcp2221]: opening MCP2221 with serial=%r', selector)
            return _MCP2221Device(usbserial=str(selector), scan_serial=True)
        logger.debug(
            'open_i2c_bus[mcp2221]: opening first MCP2221 (VID 0x%04X / PID 0x%04X)', MCP2221_VID, MCP2221_PID
        )
        return _MCP2221Device()
    except RuntimeError as exc:
        raise I2CBusConfigError(str(exc)) from exc


def construct_i2c_bus(selector):
    """Open (or reuse) the MCP2221 for `selector` and return a _LockedI2C bus.
    Called while common.i2c_bus holds its construction lock, so the dedup
    registry stays atomic with the open."""
    device = _open_mcp2221_device(selector)
    bus = _mcp2221_bus_by_device.get(device)
    if bus is None:
        bus = _LockedI2C(_EasyMCP2221Backend(device))
        _mcp2221_bus_by_device[device] = bus
    else:
        logger.debug(
            'open_i2c_bus[mcp2221]: selector=%r aliases an already-open MCP2221; reusing its shared bus/lock', selector
        )
    return bus
```

- [ ] **Step 2: Update `common/i2c_bus.py` to delegate to the new module**

Delete from `common/i2c_bus.py`: `_MCP2221_VID`, `_MCP2221_PID`, `discover_mcp2221_devices`, `_EasyMCP2221Backend`, `_open_mcp2221_device`, `_construct_mcp2221`, and the `_mcp2221_bus_by_device` global.

In `reset_bus_state()`, replace the `_mcp2221_bus_by_device.clear()` line with a call to the module:

```python
def reset_bus_state():
    """Clear the bus cache and opened-kind registry. Tests only."""
    from common import mcp2221

    with _cache_lock:
        _bus_cache.clear()
        _opened_kinds.clear()
        mcp2221.reset_state()
```

In `_construct_bus`, replace the `mcp2221` branch:

```python
    if kind == 'mcp2221':
        from common import mcp2221

        return mcp2221.construct_i2c_bus(selector)
```

Add a re-export near the top-level definitions so existing importers keep working (`blueprints/wizard/routes.py`, tests):

```python
def discover_mcp2221_devices(*args, **kwargs):
    from common import mcp2221

    return mcp2221.discover_mcp2221_devices(*args, **kwargs)
```

(Keep it a thin wrapper rather than a top-level `from common import mcp2221` to preserve lazy import / avoid the cycle.)

- [ ] **Step 3: Repoint the MCP2221 tests**

In `tests/test_i2c_bus.py`, add `from common import mcp2221` and change every `i2c_bus._EasyMCP2221Backend(...)` to `mcp2221._EasyMCP2221Backend(...)` (lines ~89–195). The factory tests that call `i2c_bus.open_i2c_bus('mcp2221', ...)` and assert `isinstance(bus, i2c_bus._LockedI2C)` are unchanged. `test_webapp_sqlite.py` patches `wizard_routes.discover_mcp2221_devices` — unaffected (the wrapper keeps that name importable).

- [ ] **Step 4: Run the tests**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/test_i2c_bus.py tests/test_webapp_sqlite.py -q`
Expected: PASS (same set of tests as before the move).

- [ ] **Step 5: Format and commit**

```bash
uvx ruff format common/mcp2221.py common/i2c_bus.py tests/test_i2c_bus.py
git add common/mcp2221.py common/i2c_bus.py tests/test_i2c_bus.py
git commit -F <msgfile>   # "refactor: extract MCP2221 backend into common/mcp2221.py"
```

---

### Task 2: Create `common/ft232h.py` I2C backend (pyftdi); drop Blinka mpsse

Replace the Blinka-mpsse FT232H I2C construction (and its `BLINKA_FT232H` env dance) with a pyftdi `I2cController`.

**Files:**
- Create: `common/ft232h.py`
- Modify: `common/i2c_bus.py` (remove `_construct_ft232h`; dispatch; re-export `discover_ft232h_devices`)
- Modify: `tests/test_i2c_bus.py` (replace mpsse-seam ft232h tests with a pyftdi seam)

**Interfaces:**
- Produces (in `common/ft232h.py`):
  - `discover_ft232h_devices() -> list[dict]`
  - `canonical_url(selector) -> str` (`''`/`'1'`/`None` → `'1'`; else `str(selector)`)
  - `_new_controller(url, frequency) -> I2cController` (test seam; does the pyftdi import + `configure`)
  - `_get_controller(selector) -> I2cController` (cached per `canonical_url`)
  - `_PyFtdiI2CBackend` (class; ctor `(controller)`)
  - `construct_i2c_bus(selector) -> i2c_bus._LockedI2C`
  - `reset_state() -> None` (clears controller + gpio caches)
- Consumes: `from common.i2c_bus import _LockedI2C`.

- [ ] **Step 1: Write the failing tests for the I2C backend**

Create `tests/test_ft232h_bus.py`:

```python
from unittest import mock

import pytest

from common import ft232h, i2c_bus


class FakePort:
    def __init__(self, controller, address):
        self.controller = controller
        self.address = address

    def write(self, data, **kwargs):
        self.controller.writes.append((self.address, bytes(data)))

    def read(self, length, **kwargs):
        return bytes(self.controller.read_data[:length])

    def exchange(self, out, readlen=0, **kwargs):
        self.controller.writes.append((self.address, bytes(out)))
        return bytes(self.controller.read_data[:readlen])


class FakeController:
    def __init__(self):
        self.configured_url = None
        self.frequency = None
        self.writes = []
        self.read_data = b'\x11\x22\x33'
        self.present = {0x10, 0x50}
        self.terminated = False

    def get_port(self, address):
        return FakePort(self, address)

    def poll(self, address, write=False, relax=True):
        return address in self.present

    def terminate(self):
        self.terminated = True


@pytest.fixture(autouse=True)
def _clean():
    ft232h.reset_state()
    i2c_bus.reset_bus_state()
    yield
    ft232h.reset_state()
    i2c_bus.reset_bus_state()


def _patch_controller():
    controller = FakeController()
    return controller, mock.patch.object(ft232h, '_new_controller', return_value=controller)


def test_construct_i2c_bus_returns_locked_i2c():
    controller, patch = _patch_controller()
    with patch:
        bus = i2c_bus.open_i2c_bus('ft232h', '')
    assert isinstance(bus, i2c_bus._LockedI2C)


def test_scan_uses_poll():
    controller, patch = _patch_controller()
    with patch:
        backend = ft232h._PyFtdiI2CBackend(controller)
    assert backend.scan() == [0x10, 0x50]


def test_blank_and_one_selector_share_one_controller():
    controller, patch = _patch_controller()
    with patch as new_controller:
        a = i2c_bus.open_i2c_bus('ft232h', '')
        b = i2c_bus.open_i2c_bus('ft232h', '1')
    assert a is b
    assert new_controller.call_count == 1  # one physical controller


def test_i2c_nack_becomes_oserror():
    from pyftdi.i2c import I2cNackError

    controller = FakeController()

    def boom(length, **kwargs):
        raise I2cNackError('nack')

    with mock.patch.object(ft232h, '_new_controller', return_value=controller):
        backend = ft232h._PyFtdiI2CBackend(controller)
    with mock.patch.object(FakePort, 'read', boom):
        buf = bytearray(1)
        with pytest.raises(OSError):
            backend.readfrom_into(0x10, buf)


def test_runtime_rejects_basic_after_ft232h():
    controller, patch = _patch_controller()
    with patch:
        i2c_bus.open_i2c_bus('ft232h', '')
        with pytest.raises(i2c_bus.I2CBusConfigError):
            i2c_bus.open_i2c_bus('basic')
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/test_ft232h_bus.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'common.ft232h'`.

- [ ] **Step 3: Create `common/ft232h.py`**

```python
#!/usr/bin/env python3

"""FT232H USB adapter backend, via pyftdi directly (not Adafruit Blinka).

One pyftdi.i2c.I2cController per FT232H (cached by url) exposes BOTH the I2C bus
(get_port, for an EMC fan controller) and the free GPIO pins (get_gpio, for the
IO-triggered relays). This bypasses Blinka's process-global `board` singleton,
which resolves to the wrong board when `import board` runs before BLINKA_FT232H
is set (the ft232h_relay `board has no attribute 'C0'` failure). See
docs/superpowers/specs/2026-07-14-ft232h-pyftdi-backend-design.md.
"""

import logging
import threading

from common.i2c_bus import _LockedI2C

logger = logging.getLogger('control')

_I2C_FREQUENCY = 100_000  # Hz; matches Blinka's mpsse default.


def discover_ft232h_devices():
    """Best-effort list of connected FT232H USB devices ({'url', 'serial',
    'description'}), for the wizard's Discover button. Returns [] if pyftdi
    isn't importable or no devices are present -- never raises."""
    try:
        from pyftdi.ftdi import Ftdi
    except ImportError:
        return []
    try:
        devices = []
        for descriptor, _interface_count in Ftdi.list_devices('ftdi://ftdi:232h/'):
            url = f'ftdi://ftdi:232h:{descriptor.sn}/1' if descriptor.sn else 'ftdi://ftdi:232h/1'
            devices.append({'url': url, 'serial': descriptor.sn, 'description': descriptor.description})
        return sorted(devices, key=lambda d: (d['serial'] or '').lower())
    except Exception:
        logger.debug('discover_ft232h_devices: Ftdi.list_devices failed', exc_info=True)
        return []


def canonical_url(selector):
    """Canonical pyftdi url for an FT232H selector. Blank/'1'/None all mean
    'the first FT232H' -> one shared controller."""
    sel = '' if selector in (None, '') else str(selector)
    if sel in ('', '1'):
        return '1'
    return sel


_controllers = {}   # canonical_url -> I2cController
_gpios = {}         # canonical_url -> Ft232hGpio
_lock = threading.RLock()


def reset_state():
    """Clear the controller and GPIO caches. Tests only."""
    with _lock:
        _controllers.clear()
        _gpios.clear()


def _new_controller(url, frequency):
    """Open and configure a pyftdi I2cController. Isolated as a test seam."""
    from pyftdi.i2c import I2cController

    controller = I2cController()
    controller.configure(url, frequency=frequency)
    return controller


def _get_controller(selector):
    url = canonical_url(selector)
    with _lock:
        controller = _controllers.get(url)
        if controller is None:
            logger.debug('ft232h: opening pyftdi I2cController url=%r @ %d Hz', url, _I2C_FREQUENCY)
            controller = _new_controller(url, _I2C_FREQUENCY)
            _controllers[url] = controller
        return controller


class _PyFtdiI2CBackend:
    """Adapt a pyftdi I2cController to the scan/writeto/readfrom_into/
    writeto_then_readfrom surface _LockedI2C expects. Translates pyftdi I2C
    errors into OSError (what adafruit_bus_device / probe code treat as
    'no device' / 'bus fault')."""

    def __init__(self, controller):
        from pyftdi.i2c import I2cIOError, I2cNackError, I2cTimeoutError

        self._controller = controller
        self._errors = (I2cNackError, I2cIOError, I2cTimeoutError)

    def scan(self):
        return [addr for addr in range(0x08, 0x78) if self._controller.poll(addr)]

    def writeto(self, address, buffer, *, start=0, end=None, **kwargs):
        end = len(buffer) if end is None else end
        data = bytes(buffer[start:end])
        try:
            self._controller.get_port(address).write(data)
        except self._errors as exc:
            raise OSError(str(exc)) from exc

    def readfrom_into(self, address, buffer, *, start=0, end=None, **kwargs):
        end = len(buffer) if end is None else end
        try:
            data = self._controller.get_port(address).read(end - start)
        except self._errors as exc:
            raise OSError(str(exc)) from exc
        buffer[start:end] = data

    def writeto_then_readfrom(
        self, address, out_buffer, in_buffer, *, out_start=0, out_end=None, in_start=0, in_end=None, **kwargs
    ):
        out_end = len(out_buffer) if out_end is None else out_end
        in_end = len(in_buffer) if in_end is None else in_end
        try:
            data = self._controller.get_port(address).exchange(
                bytes(out_buffer[out_start:out_end]), in_end - in_start
            )
        except self._errors as exc:
            raise OSError(str(exc)) from exc
        in_buffer[in_start:in_end] = data


def construct_i2c_bus(selector):
    """Open (or reuse) the FT232H for `selector` and return a _LockedI2C bus
    over its pyftdi I2C port."""
    controller = _get_controller(selector)
    return _LockedI2C(_PyFtdiI2CBackend(controller))
```

- [ ] **Step 4: Update `common/i2c_bus.py`**

Delete `_construct_ft232h`, its `from adafruit_blinka...mpsse.i2c import I2C` import, the `_UNSET` sentinel if now unused, and the old `discover_ft232h_devices` body.

In `_construct_bus`, replace the `ft232h` branch:

```python
    if kind == 'ft232h':
        from common import ft232h

        return ft232h.construct_i2c_bus(selector)
```

Add a re-export wrapper (mirrors the mcp2221 one from Task 1):

```python
def discover_ft232h_devices(*args, **kwargs):
    from common import ft232h

    return ft232h.discover_ft232h_devices(*args, **kwargs)
```

Keep `_canonical_selector`'s ft232h `''`/`'1'` merge as-is (the factory cache key stays consistent with `ft232h.canonical_url`).

Also extend `reset_bus_state()` to clear the ft232h caches too, so a single reset in a test clears every USB-HID backend:

```python
def reset_bus_state():
    """Clear the bus cache and opened-kind registry. Tests only."""
    from common import ft232h, mcp2221

    with _cache_lock:
        _bus_cache.clear()
        _opened_kinds.clear()
        ft232h.reset_state()
        mcp2221.reset_state()
```

- [ ] **Step 5: Replace the mpsse-seam ft232h tests in `tests/test_i2c_bus.py`**

Delete `test_open_ft232h_sets_env_transiently_and_restores` (its premise — the `BLINKA_FT232H` env dance — no longer exists). Delete `test_open_i2c_bus_caches_per_kind_and_selector` and `test_open_i2c_bus_runtime_rejects_basic_after_ft232h` from `test_i2c_bus.py` — equivalent coverage now lives in `tests/test_ft232h_bus.py` (`test_blank_and_one_selector_share_one_controller`, `test_runtime_rejects_basic_after_ft232h`). Leave the `discover_ft232h_devices` tests (lines ~598–630); they still pass through the `i2c_bus` re-export.

- [ ] **Step 6: Run the tests**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/test_ft232h_bus.py tests/test_i2c_bus.py -q`
Expected: PASS.

- [ ] **Step 7: Format and commit**

```bash
uvx ruff format common/ft232h.py common/i2c_bus.py tests/test_ft232h_bus.py tests/test_i2c_bus.py
git add common/ft232h.py common/i2c_bus.py tests/test_ft232h_bus.py tests/test_i2c_bus.py
git commit -F <msgfile>   # "refactor: FT232H I2C via pyftdi, drop Blinka mpsse backend"
```

---

### Task 3: Add `Ft232hGpio` + `open_gpio` to `common/ft232h.py`

The relay-facing GPIO helper: name→bit mapping, reserved/unknown rejection, atomic shadow-register writes, and per-controller sharing.

**Files:**
- Modify: `common/ft232h.py`
- Test: `tests/test_ft232h_bus.py` (add GPIO tests)

**Interfaces:**
- Consumes: `_get_controller`, `canonical_url`, `_lock`, `_gpios` from Task 2.
- Produces (in `common/ft232h.py`):
  - `Ft232hGpio` with:
    - `PIN_BITS: dict[str, int]` (class attr) — `C0..C7 -> 1<<(8+n)`, `D4..D7 -> 1<<n`
    - `setup_output(pin_name: str) -> None` — raises `ValueError` for unknown/reserved names
    - `set(pin_name: str, high: bool) -> None`
  - `open_gpio(selector) -> Ft232hGpio` (cached per `canonical_url`)

- [ ] **Step 1: Write the failing GPIO tests**

Append to `tests/test_ft232h_bus.py`:

```python
class FakeGpioPort:
    def __init__(self):
        self.direction = 0
        self.value = 0

    def set_direction(self, pins, direction):
        # pyftdi semantics: 1 bits in `pins` are (re)configured to `direction`.
        self.direction = (self.direction & ~pins) | (direction & pins)

    def write(self, value):
        self.value = value

    def read(self, with_output=False):
        return self.value


def _controller_with_gpio():
    controller = FakeController()
    port = FakeGpioPort()
    controller.get_gpio = lambda: port
    return controller, port


def test_setup_output_sets_direction_bits():
    controller, port = _controller_with_gpio()
    with mock.patch.object(ft232h, '_new_controller', return_value=controller):
        gpio = ft232h.open_gpio('')
    gpio.setup_output('C0')  # bit 8
    gpio.setup_output('D4')  # bit 4
    assert port.direction == (1 << 8) | (1 << 4)


def test_set_toggles_only_its_own_bit():
    controller, port = _controller_with_gpio()
    with mock.patch.object(ft232h, '_new_controller', return_value=controller):
        gpio = ft232h.open_gpio('')
    for name in ('C0', 'C1', 'C2', 'C3'):
        gpio.setup_output(name)
    gpio.set('C1', True)   # bit 9
    gpio.set('C3', True)   # bit 11
    assert port.value == (1 << 9) | (1 << 11)
    gpio.set('C1', False)
    assert port.value == (1 << 11)  # C3 untouched


def test_unknown_pin_name_raises():
    controller, port = _controller_with_gpio()
    with mock.patch.object(ft232h, '_new_controller', return_value=controller):
        gpio = ft232h.open_gpio('')
    with pytest.raises(ValueError):
        gpio.setup_output('Z9')


def test_reserved_i2c_pin_raises():
    controller, port = _controller_with_gpio()
    with mock.patch.object(ft232h, '_new_controller', return_value=controller):
        gpio = ft232h.open_gpio('')
    for reserved in ('D0', 'D1', 'D2', 'D3'):
        with pytest.raises(ValueError):
            gpio.setup_output(reserved)


def test_gpio_and_i2c_share_one_controller():
    controller, port = _controller_with_gpio()
    with mock.patch.object(ft232h, '_new_controller', return_value=controller) as new_controller:
        bus = i2c_bus.open_i2c_bus('ft232h', '')
        gpio = ft232h.open_gpio('1')  # '' and '1' alias
    assert new_controller.call_count == 1
    assert isinstance(bus, i2c_bus._LockedI2C)
    assert gpio.set  # smoke


def test_open_gpio_is_cached_per_controller():
    controller, port = _controller_with_gpio()
    with mock.patch.object(ft232h, '_new_controller', return_value=controller):
        a = ft232h.open_gpio('')
        b = ft232h.open_gpio('1')
    assert a is b
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/test_ft232h_bus.py -q -k "gpio or pin or setup or set_toggles or reserved"`
Expected: FAIL with `AttributeError: module 'common.ft232h' has no attribute 'open_gpio'`.

- [ ] **Step 3: Implement `Ft232hGpio` and `open_gpio`**

Append to `common/ft232h.py`:

```python
def _pin_bits():
    bits = {f'C{n}': 1 << (8 + n) for n in range(8)}
    bits.update({f'D{n}': 1 << n for n in range(4, 8)})  # D4-D7; D0-D3 are I2C/unexposed
    return bits


class Ft232hGpio:
    """Drive the FT232H's free GPIO pins (C0-C7, D4-D7) as relay outputs, over
    the same pyftdi controller the I2C bus uses. pyftdi's write() sets the whole
    output word, so a shadow register + lock make a single-relay change an atomic
    read-modify-write that leaves the other relays untouched."""

    PIN_BITS = _pin_bits()

    def __init__(self, controller):
        self._port = controller.get_gpio()
        self._direction = 0
        self._output = 0
        self._lock = threading.Lock()

    def _bit(self, pin_name):
        try:
            return self.PIN_BITS[str(pin_name)]
        except KeyError:
            raise ValueError(f'Unknown or reserved FT232H GPIO pin {pin_name!r} (use C0-C7 or D4-D7)')

    def setup_output(self, pin_name):
        bit = self._bit(pin_name)
        with self._lock:
            self._direction |= bit
            self._port.set_direction(bit, bit)  # 1 = output

    def set(self, pin_name, high):
        bit = self._bit(pin_name)
        with self._lock:
            if high:
                self._output |= bit
            else:
                self._output &= ~bit
            self._port.write(self._output)


def open_gpio(selector):
    """Return the Ft232hGpio for `selector`, sharing the same controller as the
    I2C bus and cached so all relays on one FT232H share one helper (and lock)."""
    controller = _get_controller(selector)
    url = canonical_url(selector)
    with _lock:
        gpio = _gpios.get(url)
        if gpio is None:
            gpio = Ft232hGpio(controller)
            _gpios[url] = gpio
        return gpio
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/test_ft232h_bus.py -q`
Expected: PASS.

- [ ] **Step 5: Format and commit**

```bash
uvx ruff format common/ft232h.py tests/test_ft232h_bus.py
git add common/ft232h.py tests/test_ft232h_bus.py
git commit -F <msgfile>   # "feat: FT232H relay GPIO via pyftdi (Ft232hGpio)"
```

---

### Task 4: Rewrite `grillplat/ft232h_relay.py` to use pyftdi GPIO

Remove Blinka `board`/`digitalio` from the relay platform; drive relays through `Ft232hGpio`.

**Files:**
- Modify: `grillplat/ft232h_relay.py`
- Modify: `tests/ft232h_helpers.py` (new fake seam)
- Modify: `tests/test_ft232h_outputs.py` (assert on the new GPIO fake)

**Interfaces:**
- Consumes: `open_i2c_bus('ft232h', url)` (unchanged) and `common.ft232h.open_gpio(url)` / `Ft232hGpio.setup_output` / `Ft232hGpio.set` from Task 3.

- [ ] **Step 1: Rewrite the fake seam in `tests/ft232h_helpers.py`**

Replace the whole file with a fake `Ft232hGpio` seam:

```python
import contextlib
import types
from unittest import mock


class FakeGpio:
    """Stand-in for common.ft232h.Ft232hGpio. Records per-pin direction/value
    so tests can assert what each relay pin was driven to."""

    def __init__(self):
        self.outputs = set()     # pins configured as outputs
        self.values = {}         # pin_name -> bool last written

    def setup_output(self, pin_name):
        # Mirror the real validation so bad-pin tests still exercise it.
        from common.ft232h import Ft232hGpio

        if str(pin_name) not in Ft232hGpio.PIN_BITS:
            raise ValueError(f'Unknown or reserved FT232H GPIO pin {pin_name!r}')
        self.outputs.add(pin_name)
        self.values.setdefault(pin_name, None)

    def set(self, pin_name, high):
        self.values[pin_name] = bool(high)


@contextlib.contextmanager
def make_ft232h_platform(config):
    """Build a GrillPlatform with FT232H/EMC/I2C hardware faked.

    Yields (platform, harness); harness.gpio.values[pin] is the last bool
    written to that relay pin.
    """
    import grillplat.ft232h_relay as mod

    fake_gpio = FakeGpio()
    with (
        mock.patch.object(mod, 'open_ft232h_gpio', return_value=fake_gpio),
        mock.patch.object(mod, 'open_i2c_bus', return_value=mock.sentinel.ft232h_bus) as open_bus,
        mock.patch.object(mod, 'EMC2101_LUT') as emc2101_cls,
        mock.patch.object(mod, 'EMC2301') as emc2301_cls,
    ):
        platform = mod.GrillPlatform(config)
        harness = types.SimpleNamespace(
            gpio=fake_gpio, open_bus=open_bus, emc2101_cls=emc2101_cls, emc2301_cls=emc2301_cls
        )
        yield platform, harness
```

- [ ] **Step 2: Rewrite the output tests in `tests/test_ft232h_outputs.py`**

Rewrite the assertions to use `harness.gpio.values[...]`. Active-LOW means asserted → `False`, de-asserted → `True`. Replace the file body below the imports (keep `_relay_config`):

```python
def test_relay_only_init_opens_shared_bus_but_no_emc():
    with make_ft232h_platform(_relay_config()) as (plat, harness):
        assert plat.pwm_fan is False
        assert plat.emc is None
        harness.open_bus.assert_called_once_with('ft232h', '1')
        harness.emc2101_cls.assert_not_called()
        harness.emc2301_cls.assert_not_called()
        assert set(plat.relays) == {'power', 'igniter', 'auger', 'fan'}
        # Active-low, de-asserted at init -> True.
        assert harness.gpio.values['C0'] is True


def test_output_methods_toggle_mapped_active_low_pins():
    with make_ft232h_platform(_relay_config()) as (plat, harness):
        plat.auger_on()
        assert harness.gpio.values['C2'] is False  # auger -> C2 asserted (active-low)
        assert plat._output_state['auger'] is True
        plat.auger_off()
        assert harness.gpio.values['C2'] is True
        assert plat._output_state['auger'] is False


def test_power_and_igniter_use_mapped_pins():
    with make_ft232h_platform(_relay_config()) as (plat, harness):
        plat.power_on()
        plat.igniter_on()
        assert harness.gpio.values['C0'] is False  # power -> C0
        assert harness.gpio.values['C1'] is False  # igniter -> C1


def test_active_high_trigger_level_not_inverted():
    with make_ft232h_platform(_relay_config(triggerlevel='HIGH')) as (plat, harness):
        assert harness.gpio.values['C0'] is False  # de-asserted at init (active-high)
        plat.power_on()
        assert harness.gpio.values['C0'] is True


def test_custom_pin_mapping_is_honored():
    with make_ft232h_platform(_relay_config(outputs={'power': 'D4', 'igniter': 'D5', 'auger': 'D6', 'fan': 'D7'})) as (
        plat,
        harness,
    ):
        plat.auger_on()
        assert harness.gpio.values['D6'] is False


def test_unknown_pin_name_raises_value_error():
    import pytest

    with pytest.raises(ValueError):
        with make_ft232h_platform(_relay_config(outputs={'power': 'Z9', 'igniter': 'C1', 'auger': 'C2', 'fan': 'C3'})):
            pass


def test_relay_only_fan_on_off_and_toggle():
    with make_ft232h_platform(_relay_config()) as (plat, harness):
        plat.fan_on()
        assert harness.gpio.values['C3'] is False  # fan -> C3 asserted
        assert plat._output_state['fan'] is True
        plat.fan_toggle()
        assert plat._output_state['fan'] is False
        assert harness.gpio.values['C3'] is True


def test_relay_only_set_duty_cycle_and_frequency_are_noops():
    with make_ft232h_platform(_relay_config()) as (plat, harness):
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


def test_cleanup_deasserts_pins():
    with make_ft232h_platform(_relay_config()) as (plat, harness):
        plat.power_on()
        plat.cleanup()
        for pin in ('C0', 'C1', 'C2', 'C3'):
            assert harness.gpio.values[pin] is True  # all de-asserted


def test_import_does_not_enable_ft232h_backend():
    import subprocess
    import sys

    code = "import os, grillplat.ft232h_relay; assert 'BLINKA_FT232H' not in os.environ"
    subprocess.run([sys.executable, '-c', code], check=True, cwd='.')
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/test_ft232h_outputs.py -q`
Expected: FAIL (module still imports `_load_ft232h`; `open_ft232h_gpio` not defined in the module yet).

- [ ] **Step 4: Rewrite `grillplat/ft232h_relay.py`**

Replace the import block, the `_load_ft232h` function, the `_Relay` class, and the GPIO section of `__init__`.

Imports (top of file): remove nothing from the EMC imports; add the gpio import next to `open_i2c_bus`:

```python
from common.i2c_bus import open_i2c_bus
from common.ft232h import open_gpio as open_ft232h_gpio
```

(The alias keeps the module-level name `open_ft232h_gpio` in `ft232h_relay`'s namespace — the name the test helper patches — while the `common.ft232h` module keeps the un-stuttering `open_gpio`.)

Delete the entire `_load_ft232h` function. Also remove `import os` from the top of the file — it was used only by `_load_ft232h`'s `os.environ` and is now dead (ruff would flag F401). Keep `import logging` and `import threading` (both still used).

Replace `_Relay` with a GPIO-bit driver:

```python
class _Relay:
    """One relay-board input driven by an FT232H GPIO pin (by name, via the
    shared Ft232hGpio). digitalio had no active_high parameter and neither does
    this: an active-LOW board asserts the relay by driving the pin low."""

    def __init__(self, gpio, pin_name, active_high):
        self._gpio = gpio
        self._pin_name = pin_name
        self._active_high = active_high
        self._state = False
        self.off()

    def on(self):
        self._gpio.set(self._pin_name, self._active_high)
        self._state = True

    def off(self):
        self._gpio.set(self._pin_name, not self._active_high)
        self._state = False

    @property
    def is_active(self):
        return self._state

    def close(self):
        # The shared pyftdi controller lives for the process lifetime; nothing
        # per-relay to release.
        pass
```

Replace the GPIO section of `__init__` (the block from the `open_i2c_bus` comment through the relay-build loop). Keep the `open_i2c_bus('ft232h', ...)` call first (it establishes/caches the shared controller), then build relays over `open_ft232h_gpio`:

```python
        # Open the FT232H I2C bus through the shared factory FIRST. This creates
        # (and caches) the single pyftdi I2cController, so the relay GPIO below
        # and any ft232h I2C probe reuse one controller and one MPSSE engine.
        self._ft232h_bus = open_i2c_bus('ft232h', self.url)

        # Relay GPIO comes off that same controller via pyftdi's get_gpio() --
        # no Adafruit Blinka `board`/`digitalio`, so no process-global board
        # singleton to resolve to the wrong board.
        gpio = open_ft232h_gpio(self.url)
        self.relays = {}
        try:
            for name, pin_name in self.pin_map.items():
                gpio.setup_output(pin_name)
                self.relays[name] = _Relay(gpio, pin_name, active_high)
        except Exception:
            for relay in self.relays.values():
                try:
                    relay.close()
                except Exception:
                    pass
            raise
```

Update the module docstring's "digitalio" mentions to reflect pyftdi (cosmetic). In `cleanup()`, the relay loop stays; `relay.close()` is now a no-op but the `relay.off()` still de-asserts — leave that loop as-is.

- [ ] **Step 5: Run tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/test_ft232h_outputs.py tests/test_ft232h_fan.py tests/test_ft232h_system.py tests/test_ft232h_settings.py tests/test_ft232h_wizard.py -q`
Expected: PASS.

- [ ] **Step 6: Format and commit**

```bash
uvx ruff format grillplat/ft232h_relay.py tests/ft232h_helpers.py tests/test_ft232h_outputs.py
git add grillplat/ft232h_relay.py tests/ft232h_helpers.py tests/test_ft232h_outputs.py
git commit -F <msgfile>   # "feat: drive FT232H relays via pyftdi GPIO, drop Blinka board/digitalio"
```

---

### Task 5: Full-suite verification and comment cleanup

**Files:**
- Modify: `common/i2c_bus.py` (docstring/comment touch-ups), `grillplat/ft232h_relay.py` (docstring)

- [ ] **Step 1: Grep for stale Blinka-on-FT232H references**

Run: `grep -rn "BLINKA_FT232H\|mpsse\|_load_ft232h\|Pin.mpsse_gpio" --include=*.py common grillplat tests | grep -v test_i2c_bus`
Expected: no functional references remain on the ft232h path (comments in `i2c_bus.py`'s module docstring describing history are fine, but the FT232H-specific "Each instance constructs its own pyftdi..." note should now read as pyftdi-direct). Fix any leftover code references; adjust the `common/i2c_bus.py` module docstring bullet for `ft232h` to say it now uses pyftdi directly (not Blinka's mpsse backend).

- [ ] **Step 2: Run the full test suite**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q`
Expected: all green.

- [ ] **Step 3: Verify no board singleton import remains reachable on the ft232h path**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run python -c "import sys, grillplat.ft232h_relay; assert 'board' not in sys.modules, 'board should not be imported by importing ft232h_relay'"`
Expected: exit 0 (importing the module must not import `board`).

- [ ] **Step 4: Format and commit**

```bash
uvx ruff format common/i2c_bus.py grillplat/ft232h_relay.py
git add -A
git commit -F <msgfile>   # "docs: update i2c_bus/ft232h_relay comments for pyftdi FT232H path"
```

---

## Notes for the implementer

- Hardware isn't available in CI; every test uses a fake controller/gpio via the `ft232h._new_controller` seam or the `make_ft232h_platform` fakes. Do not add tests that open a real FT232H.
- The `_LockedI2C` wrapper stays in `common/i2c_bus.py`; both backend modules import it from there. This is intentional and cycle-free (i2c_bus imports the backends only lazily).
- If `test_webapp_sqlite.py` or `blueprints/wizard/routes.py` fail on the `discover_*` names, confirm the thin re-export wrappers in `i2c_bus.py` are present (Task 1 Step 2, Task 2 Step 4).
