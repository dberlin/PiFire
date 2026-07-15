# SEN0628 USB Distance Sensor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the DFRobot SEN0628 (8x8 Matrix ToF distance sensor, connected via USB) as a new pluggable `distance` module in PiFire, plus a generic USB-serial "Discover" button in the setup wizard.

**Architecture:** A new `SerialToFHopperLevel` base class (`distance/_serial_tof_base.py`) mirrors the existing I2C `ToFHopperLevel` base's threaded-polling/percentage-math scaffold but opens a pyserial port instead of an I2C bus. `distance/sen0628.py` subclasses it and implements DFRobot's documented UART/USB packet protocol (reimplemented from the vendor's Python reference driver, fixed for Python 3). A new `common/usb_serial.py` provides VID/PID-filterable serial device discovery, wired into the wizard through one new Flask route action and one new Jinja macro that reuse the existing I2C-bridge Discover UI's result-table/selection JS wherever possible.

**Tech Stack:** Python 3.14+, Flask (Jinja templates + `render_template_string` partials), pyserial, pytest + `unittest.mock`.

**Spec:** `docs/superpowers/specs/2026-07-15-sen0628-distance-sensor-design.md`

## Global Constraints

- Python `>=3.14` (per `pyproject.toml`); `pyserial>=3.5` is already a core dependency — no new dependency declarations needed for pyserial itself.
- Run `uvx ruff format <changed files>` before every commit in this plan (standing repo rule).
- Run tests via `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest <path>` — bare `python`/`pytest` gives false failures in this repo's environment.
- When writing commit messages, use `git commit -F -` with a heredoc (or `-F <file>`) rather than `-m` with backticks — zsh mangles backticks inside `-m "..."`.
- Do not modify `distance/_tof_base.py`, `distance/vl53l0x.py`, `distance/vl53l1x.py`, `distance/vl53l4cd.py`, or their tests — this plan is purely additive with respect to the existing I2C ToF sensors.
- This integration is written from DFRobot's documented protocol and reference driver source; it has not been verified against physical hardware. Say so in the new module's docstring, matching the existing disclaimer style in `distance/hcsr04.py`.

---

### Task 1: `SerialToFHopperLevel` base class

**Files:**
- Create: `distance/_serial_tof_base.py`
- Test: `tests/unit/distance/test_serial_tof_base.py`

**Interfaces:**
- Produces: `distance._serial_tof_base.SerialToFHopperLevel` — constructor `__init__(self, dev_pins, empty=22, full=4, debug=False)`; abstract methods `_open_sensor(self, ser)` and `_read_distance_mm(self)` for subclasses to implement; optional override `_close_sensor(self)` (default no-op); public methods `set_level(level=100)`, `update_distances(empty, full)`, `get_distances()`, `get_level(override=False)` — identical contract to `distance._tof_base.ToFHopperLevel`. Reads `dev_pins["distance"]["device"]` (default `/dev/ttyACM0`) and `dev_pins["distance"]["baudrate"]` (default `115200`). Opens the port via `serial.Serial(self.device, self.baudrate, timeout=0.2)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/distance/test_serial_tof_base.py`:

```python
from unittest import mock

import pytest


class _FakeClock:
    """Deterministic stand-in for the `time` module as seen by
    distance._serial_tof_base. Mirrors tests/unit/distance/test_tof_base.py's
    _FakeClock -- see that file for the full rationale."""

    def __init__(self):
        self._now = 0.0

    def time(self):
        return self._now

    def sleep(self, seconds):
        pass

    def advance(self, seconds):
        self._now += seconds


class FakeSensorMixin:
    """Mixed in ahead of SerialToFHopperLevel in test subclasses so the
    shared thread/percent-calc/port-opening logic can be exercised without a
    real serial device."""

    def __init__(self, *args, reading_mm=100, read_delay=0, **kwargs):
        self.open_calls = 0
        self.opened_with = None
        self._reading_mm = reading_mm
        self._read_delay = read_delay
        super().__init__(*args, **kwargs)

    def _open_sensor(self, ser):
        self.open_calls += 1
        self.opened_with = ser

    def _read_distance_mm(self):
        if self._read_delay:
            self._serial_tof_mod.time.advance(self._read_delay)
        return self._reading_mm


@pytest.fixture
def serial_tof_mod():
    import distance._serial_tof_base as mod

    with (
        mock.patch.object(mod.serial, "Serial", return_value=mock.sentinel.ser),
        mock.patch.object(mod, "time", _FakeClock()),
    ):
        yield mod


def _make_hopper(serial_tof_mod, dev_pins=None, reading_mm=100, empty=22, full=4, read_delay=0):
    class TestHopperLevel(FakeSensorMixin, serial_tof_mod.SerialToFHopperLevel):
        _serial_tof_mod = serial_tof_mod

    return TestHopperLevel(dev_pins or {}, empty=empty, full=full, reading_mm=reading_mm, read_delay=read_delay)


def _stop(hopper):
    hopper.sensor_thread_active = False
    hopper.sensor_thread.join(timeout=2)


def test_open_serial_port_delegates_to_pyserial(serial_tof_mod):
    hopper = _make_hopper(serial_tof_mod, dev_pins={"distance": {"device": "/dev/ttyACM3"}})
    try:
        assert hopper.opened_with is mock.sentinel.ser
        serial_tof_mod.serial.Serial.assert_called_with("/dev/ttyACM3", 115200, timeout=0.2)
    finally:
        _stop(hopper)


def test_device_defaults_to_ttyACM0(serial_tof_mod):
    hopper = _make_hopper(serial_tof_mod, dev_pins={})
    try:
        serial_tof_mod.serial.Serial.assert_called_with("/dev/ttyACM0", 115200, timeout=0.2)
    finally:
        _stop(hopper)


def test_baudrate_override_is_used(serial_tof_mod):
    hopper = _make_hopper(serial_tof_mod, dev_pins={"distance": {"baudrate": 9600}})
    try:
        serial_tof_mod.serial.Serial.assert_called_with("/dev/ttyACM0", 9600, timeout=0.2)
    finally:
        _stop(hopper)


def test_invalid_empty_full_forces_defaults(serial_tof_mod):
    hopper = _make_hopper(serial_tof_mod, empty=4, full=22)
    try:
        assert hopper.empty == 22
        assert hopper.full == 4
    finally:
        _stop(hopper)


def test_reading_at_or_below_full_is_100_percent(serial_tof_mod):
    hopper = _make_hopper(serial_tof_mod, reading_mm=40, empty=22, full=4)  # 4.0cm == full
    try:
        assert hopper.get_level(override=True) == 100
    finally:
        _stop(hopper)


def test_reading_at_empty_is_0_percent(serial_tof_mod):
    hopper = _make_hopper(serial_tof_mod, reading_mm=220, empty=22, full=4)  # 22.0cm == empty
    try:
        assert hopper.get_level(override=True) == 0
    finally:
        _stop(hopper)


def test_reading_between_full_and_empty_is_interpolated(serial_tof_mod):
    hopper = _make_hopper(serial_tof_mod, reading_mm=50, empty=22, full=4)  # 5.0cm
    try:
        assert hopper.get_level(override=True) == 94
    finally:
        _stop(hopper)


def test_slow_read_cycle_reinitializes_sensor(serial_tof_mod):
    hopper = _make_hopper(serial_tof_mod, reading_mm=100, read_delay=0.2)  # 3 * 0.2s > 0.5s threshold
    try:
        hopper.get_level(override=True)
        assert hopper.open_calls == 2
    finally:
        _stop(hopper)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/distance/test_serial_tof_base.py -v`
Expected: FAIL/ERROR — `ModuleNotFoundError: No module named 'distance._serial_tof_base'`

- [ ] **Step 3: Write the implementation**

Create `distance/_serial_tof_base.py`:

```python
#!/usr/bin/env python3

# *****************************************
# PiFire Serial ToF (Time-of-Flight) Hopper Level Base
# *****************************************
#
# Description: Shared threading / hopper-percentage-calculation logic for
#   USB-serial-connected Time-of-Flight distance sensors (e.g. the DFRobot
#   SEN0628). Each sensor module subclasses SerialToFHopperLevel and
#   implements _open_sensor, _read_distance_mm, and (optionally)
#   _close_sensor. Mirrors distance/_tof_base.py's threading/percentage-math
#   scaffold, but opens a pyserial port instead of an I2C bus -- kept as a
#   separate base rather than merged with _tof_base.py's I2C-specific one,
#   to avoid touching the tested, shipped I2C ToF sensors for a single new
#   serial consumer.
#
# *****************************************

import threading
import logging
import time

import serial


class SerialToFHopperLevel:
    default_device = "/dev/ttyACM0"
    default_baudrate = 115200

    def __init__(self, dev_pins, empty=22, full=4, debug=False):
        self.logger = logging.getLogger("events")
        self.empty = empty  # Empty is greater than distance measured for empty
        self.full = full  # Full is less than or equal to the minimum full distance.
        self.debug = debug
        self.distance_read = 100

        self.event = threading.Event()

        if self.empty <= self.full:
            event = "ERROR: Invalid Hopper Level Configuration Empty Level <= Full Level (forcing defaults)"
            self.logger.error(event)
            # Set defaults that are valid
            self.empty = 22
            self.full = 4

        distance_pins = (dev_pins or {}).get("distance", {}) or {}
        self.device = distance_pins.get("device", self.default_device)
        self.baudrate = distance_pins.get("baudrate", self.default_baudrate)

        self.__start_sensor()
        # Setup & Start Sensor Loop Thread
        self.sensor_thread_active = True
        self.sensor_thread_read_interval = 60  # Read sensor every 60 seconds
        self.sensor_thread_override = True  # Allow override to do direct reads
        self.sensor_thread = threading.Thread(target=self._sensing_loop)
        self.sensor_thread.start()

    def _open_serial_port(self):
        return serial.Serial(self.device, self.baudrate, timeout=0.2)

    def __start_sensor(self):
        ser = self._open_serial_port()
        self._open_sensor(ser)

    def _open_sensor(self, ser):
        """Initialize the sensor protocol on the already-open `ser` (a
        pyserial Serial instance) and set whatever state _read_distance_mm
        needs (e.g. self.ser). Subclasses must implement this."""
        raise NotImplementedError

    def _read_distance_mm(self):
        """Return a single distance reading in millimeters. Subclasses must
        implement this."""
        raise NotImplementedError

    def _close_sensor(self):
        """Close the serial port / release the sensor. Optional; no-op by default."""
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
                    event = "* Average Distance Measured: " + str(avg_dist) + "cm"
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
                        "Warning: The serial ToF sensor took longer than normal to get a reading.  "
                        "Re-initializing the sensor."
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
        levels["empty"] = self.empty
        levels["full"] = self.full
        return levels

    def get_level(self, override=False):
        """If override selected, force the sensor thread to update"""
        if override:
            self.sensor_thread_override = True
            self.event.wait(3)  # Wait 3 seconds for sensor to update
            self.event.clear()  # Clear event flag
        return self.distance_read
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/distance/test_serial_tof_base.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Format and commit**

```bash
uvx ruff format distance/_serial_tof_base.py tests/unit/distance/test_serial_tof_base.py
git add distance/_serial_tof_base.py tests/unit/distance/test_serial_tof_base.py
git commit -F - <<'EOF'
feat: add SerialToFHopperLevel base for USB-serial distance sensors

Mirrors distance/_tof_base.py's threaded-polling/percentage-math
scaffold for I2C ToF sensors, but opens a pyserial port instead of an
I2C bus. Kept separate from _tof_base.py to avoid touching the
tested, shipped I2C ToF sensors for this new transport.
EOF
```

---

### Task 2: SEN0628 protocol driver

**Files:**
- Create: `distance/sen0628.py`
- Test: `tests/unit/distance/test_sen0628.py`

**Interfaces:**
- Consumes: `distance._serial_tof_base.SerialToFHopperLevel` (Task 1) — subclasses it, implementing `_open_sensor(self, ser)` and `_read_distance_mm(self)`.
- Produces: `distance.sen0628.HopperLevel(SerialToFHopperLevel)` — the module's driver class, loaded dynamically by `controller/runtime/devices.py` as `distance.sen0628.HopperLevel(dev_pins=..., empty=..., full=..., debug=...)` (same convention as every other `distance/*.py` module). Also exposes module-level helpers `_build_packet(cmd, args=())`, `_recv_data(ser, length)`, `_recv_packet(ser, cmd, timeout=2.0)`, and constants `CMD_SETMODE`, `CMD_FIXED_POINT`, `STATUS_SUCCESS`, `STATUS_FAILED`, `_SYNC_BYTE`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/distance/test_sen0628.py`:

```python
from unittest import mock

import pytest

import distance.sen0628 as sen_mod


class _FakeSerial:
    """Minimal stand-in for pyserial's Serial: records written bytes and
    serves reads from a pre-loaded byte queue."""

    def __init__(self, rx_bytes=b""):
        self.written = bytearray()
        self._rx = bytearray(rx_bytes)

    def write(self, data):
        self.written += data

    def read(self, length):
        chunk = self._rx[:length]
        del self._rx[:length]
        return bytes(chunk)

    def reset_input_buffer(self):
        pass


def _success_packet(cmd, data=b""):
    """Build a raw response frame (status, cmd, len_lo, len_hi, *data) as
    the sensor would send it -- the mirror of sen0628._recv_packet's
    parsing."""
    length = len(data)
    return bytes([sen_mod.STATUS_SUCCESS, cmd, length & 0xFF, (length >> 8) & 0xFF]) + data


def test_build_packet_frames_fixed_point_request():
    pkt = sen_mod._build_packet(sen_mod.CMD_FIXED_POINT, args=[3, 4])
    assert pkt == bytes([0, 3, sen_mod.CMD_FIXED_POINT, 3, 4])


def test_build_packet_frames_setmode_request():
    pkt = sen_mod._build_packet(sen_mod.CMD_SETMODE, args=[0, 0, 0, 8])
    assert pkt == bytes([0, 5, sen_mod.CMD_SETMODE, 0, 0, 0, 8])


def test_recv_packet_parses_success_response():
    ser = _FakeSerial(rx_bytes=_success_packet(sen_mod.CMD_FIXED_POINT, data=bytes([0x2C, 0x01])))  # 300mm
    data = sen_mod._recv_packet(ser, sen_mod.CMD_FIXED_POINT)
    assert data == [0x2C, 0x01]


def test_recv_packet_returns_none_on_failure_status():
    frame = bytes([sen_mod.STATUS_FAILED, sen_mod.CMD_FIXED_POINT, 2, 0, 0x00, 0x00])
    ser = _FakeSerial(rx_bytes=frame)
    assert sen_mod._recv_packet(ser, sen_mod.CMD_FIXED_POINT) is None


def test_recv_packet_returns_none_on_timeout():
    ser = _FakeSerial(rx_bytes=b"")
    assert sen_mod._recv_packet(ser, sen_mod.CMD_FIXED_POINT, timeout=0.05) is None


def test_recv_packet_returns_none_on_command_mismatch():
    frame = bytes([sen_mod.STATUS_SUCCESS, sen_mod.CMD_SETMODE, 0, 0])
    ser = _FakeSerial(rx_bytes=frame)
    assert sen_mod._recv_packet(ser, sen_mod.CMD_FIXED_POINT) is None


def test_open_sensor_sends_setmode_and_succeeds_on_first_ack(monkeypatch):
    monkeypatch.setattr(sen_mod.time, "sleep", lambda seconds: None)
    ser = _FakeSerial(rx_bytes=_success_packet(sen_mod.CMD_SETMODE))
    hopper = sen_mod.HopperLevel.__new__(sen_mod.HopperLevel)
    hopper._open_sensor(ser)
    assert hopper.ser is ser
    assert ser.written == sen_mod._SYNC_BYTE + sen_mod._build_packet(sen_mod.CMD_SETMODE, args=[0, 0, 0, 8])


def test_open_sensor_raises_after_repeated_failure(monkeypatch):
    monkeypatch.setattr(sen_mod.time, "sleep", lambda seconds: None)
    ser = _FakeSerial(rx_bytes=b"")  # never responds
    hopper = sen_mod.HopperLevel.__new__(sen_mod.HopperLevel)
    hopper._setmode_recv_timeout = 0.02  # keep the 3 retries fast in this test
    with pytest.raises(RuntimeError):
        hopper._open_sensor(ser)


def test_read_distance_mm_averages_center_block(monkeypatch):
    hopper = sen_mod.HopperLevel.__new__(sen_mod.HopperLevel)
    readings = {(3, 3): 100, (3, 4): 200, (4, 3): 100, (4, 4): 200}
    monkeypatch.setattr(hopper, "_get_fixed_point_mm", lambda x, y: readings[(x, y)])
    assert hopper._read_distance_mm() == 150


def test_read_distance_mm_ignores_invalid_points(monkeypatch):
    hopper = sen_mod.HopperLevel.__new__(sen_mod.HopperLevel)
    readings = {(3, 3): 0, (3, 4): 200, (4, 3): 0, (4, 4): 200}
    monkeypatch.setattr(hopper, "_get_fixed_point_mm", lambda x, y: readings[(x, y)])
    assert hopper._read_distance_mm() == 200


def test_read_distance_mm_returns_zero_when_all_invalid(monkeypatch):
    hopper = sen_mod.HopperLevel.__new__(sen_mod.HopperLevel)
    monkeypatch.setattr(hopper, "_get_fixed_point_mm", lambda x, y: 0)
    assert hopper._read_distance_mm() == 0


def test_get_fixed_point_mm_sends_request_and_parses_response():
    ser = _FakeSerial(rx_bytes=_success_packet(sen_mod.CMD_FIXED_POINT, data=bytes([0x2C, 0x01])))  # 300mm
    hopper = sen_mod.HopperLevel.__new__(sen_mod.HopperLevel)
    hopper.ser = ser
    assert hopper._get_fixed_point_mm(3, 3) == 300
    assert ser.written == sen_mod._SYNC_BYTE + sen_mod._build_packet(sen_mod.CMD_FIXED_POINT, args=[3, 3])


def test_get_fixed_point_mm_returns_zero_on_no_response():
    ser = _FakeSerial(rx_bytes=b"")
    hopper = sen_mod.HopperLevel.__new__(sen_mod.HopperLevel)
    hopper.ser = ser
    hopper._setmode_recv_timeout = 0.02
    assert hopper._get_fixed_point_mm(3, 3) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/distance/test_sen0628.py -v`
Expected: FAIL/ERROR — `ModuleNotFoundError: No module named 'distance.sen0628'`

- [ ] **Step 3: Write the implementation**

Create `distance/sen0628.py`:

```python
#!/usr/bin/env python3

# *****************************************
# PiFire SEN0628 Interface Library
# *****************************************
#
# Description: This library supports getting the hopper level from the
#   DFRobot SEN0628 (Gravity 8x8 Matrix ToF 3D Distance Sensor), connected
#   via its onboard USB-C port. Per DFRobot's wiki, the USB port is a live
#   data interface (not just firmware update) -- it enumerates as a fixed
#   115200-baud serial device speaking the same command protocol documented
#   for the sensor's UART pins.
#
#   Protocol reference: DFRobot's own Python driver
#   (github.com/DFRobot/DFRobot_MatrixLidar, python/raspberry/
#   DFRobot_matrixLidar.py). That reference script has two Python-2-only
#   bugs that would crash verbatim under Python 3 (ord() on an
#   already-int byte from iterating `bytes`, and writing a raw `list`
#   instead of `bytes`/`bytearray` to pyserial); this module reimplements
#   the protocol fixed for Python 3, and additionally corrects what looks
#   like a typo in the vendor's response-length parsing (`<< 2` where
#   every other 16-bit combine in the same file uses `<< 8`) -- this makes
#   no difference for the short response lengths this driver ever sees
#   (always < 64, so the high length byte is always 0 either way), but is
#   implemented correctly here regardless.
#
#   Only a single distance number is needed for hopper level, not the full
#   64-point depth matrix, so this driver reads the 2x2 block of points
#   nearest the matrix center -- (3,3), (3,4), (4,3), (4,4) in the 0-indexed
#   8x8 grid -- via four CMD_FIXED_POINT queries and averages them. The
#   single-point response format is unambiguous in the vendor protocol; the
#   full-matrix (CMD_ALLData) byte ordering is not documented anywhere
#   verifiable, so it is deliberately not used here.
#
#   NOTE: This library hasn't been tested against real hardware yet and is
#   provided for testing (see distance/hcsr04.py for the same disclaimer
#   style used elsewhere in this project).
#
# *****************************************

import time

from distance._serial_tof_base import SerialToFHopperLevel

CMD_SETMODE = 1
CMD_FIXED_POINT = 3
STATUS_SUCCESS = 0x53
STATUS_FAILED = 0x63

_SYNC_BYTE = b"\x55"
_CENTER_BLOCK = ((3, 3), (3, 4), (4, 3), (4, 4))
_RANGING_MATRIX_8X8 = 8


def _build_packet(cmd, args=()):
    """Build a `[len_hi, len_lo, cmd, *args]` command packet payload (the
    caller prefixes the 0x55 sync byte separately)."""
    length = len(args) + 1  # +1 for the command byte, per the vendor protocol's length field
    return bytes([(length >> 8) & 0xFF, length & 0xFF, cmd, *args])


def _recv_data(ser, length):
    if length <= 0:
        return []
    return list(ser.read(length))


def _recv_packet(ser, cmd, timeout=2.0):
    """Read and validate a response packet for `cmd`. Returns the response
    payload (list of ints), or None on timeout / status failure / a
    malformed or mismatched response."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = _recv_data(ser, 1)
        if not status:
            continue
        status = status[0]
        if status not in (STATUS_SUCCESS, STATUS_FAILED):
            continue
        command = _recv_data(ser, 1)
        if not command or command[0] != cmd:
            return None
        len_bytes = _recv_data(ser, 2)
        if len(len_bytes) < 2:
            return None
        length = (len_bytes[1] << 8) | len_bytes[0]
        if length > 128:
            return None
        data = _recv_data(ser, length) if length else []
        if status != STATUS_SUCCESS:
            return None
        return data
    return None


class HopperLevel(SerialToFHopperLevel):
    _setmode_retries = 3
    _setmode_recv_timeout = 2.0

    def _open_sensor(self, ser):
        self.ser = ser
        for _attempt in range(self._setmode_retries):
            ser.reset_input_buffer()
            ser.write(_SYNC_BYTE)
            ser.write(_build_packet(CMD_SETMODE, args=[0, 0, 0, _RANGING_MATRIX_8X8]))
            response = _recv_packet(ser, CMD_SETMODE, timeout=self._setmode_recv_timeout)
            if response is not None:
                time.sleep(5)  # matches the vendor driver's post-configure settle time
                return
        raise RuntimeError("SEN0628: sensor did not acknowledge ranging-mode configuration")

    def _get_fixed_point_mm(self, x, y):
        self.ser.reset_input_buffer()
        self.ser.write(_SYNC_BYTE)
        self.ser.write(_build_packet(CMD_FIXED_POINT, args=[x, y]))
        data = _recv_packet(self.ser, CMD_FIXED_POINT, timeout=self._setmode_recv_timeout)
        if not data or len(data) < 2:
            return 0
        return (data[1] << 8) | data[0]

    def _read_distance_mm(self):
        readings = [self._get_fixed_point_mm(x, y) for (x, y) in _CENTER_BLOCK]
        valid = [r for r in readings if r > 0]
        if not valid:
            return 0
        return sum(valid) / len(valid)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/distance/test_sen0628.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Format and commit**

```bash
uvx ruff format distance/sen0628.py tests/unit/distance/test_sen0628.py
git add distance/sen0628.py tests/unit/distance/test_sen0628.py
git commit -F - <<'EOF'
feat: add DFRobot SEN0628 USB distance sensor driver

Implements the DFRobot UART/USB packet protocol (reimplemented from
the vendor's Python reference driver, fixed for two Python-2-only
bugs) on top of SerialToFHopperLevel, reading the center 2x2 block of
the 8x8 depth matrix and averaging it into a single hopper-level
distance reading. Not yet verified against physical hardware.
EOF
```

---

### Task 3: Wizard registration (settings default, manifest entry, placeholder image)

**Files:**
- Modify: `common/common.py` (the `"distance"` dict inside `default_settings()`, ~line 152)
- Modify: `wizard/wizard_manifest.json` (`modules.distance`, add `"sen0628"` entry)
- Create: `static/img/wizard/sen0628.png` (placeholder copy of `static/img/wizard/none.png` until a real product photo is available)
- Modify: `tests/unit/distance/test_distance_manifest.py`
- Create: `tests/unit/common/test_default_settings_devices.py`

**Interfaces:**
- Consumes: `distance.sen0628` (Task 2) via its module filename `"sen0628"`, matched against `wizard_manifest.json`'s `modules.distance.sen0628.filename` and loaded dynamically by `controller/runtime/devices.py:226` (`importlib.import_module(f"distance.{dist_name}")`).
- Produces: `settings["platform"]["devices"]["distance"]["device"]` (default `"/dev/ttyACM0"`), read by `SerialToFHopperLevel.__init__` (Task 1). `wizard_manifest.json`'s new `settings_dependencies.sen0628_device` entry (`"type": "usb_serial_device"`, `"vid": null`, `"pid": null`) is the contract Task 6's UI dispatch and Task 5's discovery route both key off of.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/distance/test_distance_manifest.py` (after the existing `test_vl53l1x_entry_present` function):

```python
def test_sen0628_entry_present():
    manifest = _manifest()
    entry = manifest["modules"]["distance"]["sen0628"]
    assert entry["filename"] == "sen0628"
    assert entry["py_dependencies"] == []
    assert entry["apt_dependencies"] == []
    assert entry["image"] == "sen0628.png"
    device_field = entry["settings_dependencies"]["sen0628_device"]
    assert device_field["type"] == "usb_serial_device"
    assert device_field["settings"] == ["platform", "devices", "distance", "device"]
    assert device_field["vid"] is None
    assert device_field["pid"] is None
```

Create `tests/unit/common/test_default_settings_devices.py`:

```python
from common.common import default_settings


def test_distance_defaults_include_sen0628_device_path():
    distance_defaults = default_settings()["platform"]["devices"]["distance"]
    assert distance_defaults["device"] == "/dev/ttyACM0"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/distance/test_distance_manifest.py tests/unit/common/test_default_settings_devices.py -v`
Expected: FAIL — `KeyError: 'sen0628'` and `KeyError: 'device'`

- [ ] **Step 3: Update `common/common.py`**

In `default_settings()`, find the existing `"distance"` dict (currently):

```python
            "distance": {
                "echo": 27,  # HCSR04 Distance Sensor
                "trig": 23,  # HCSR04 Distance Sensor
                "i2c_bus_kind": "basic",  # VL53L0X/VL53L4CD/VL53L1X: "basic" | "extended"
                "i2c_bus_num": "CP2112",  # VL53L0X/VL53L4CD/VL53L1X: numbered bus or adapter-name match
                "address": None,  # VL53L0X/VL53L4CD/VL53L1X: optional I2C address override (hex string or int)
            },
```

Replace it with:

```python
            "distance": {
                "echo": 27,  # HCSR04 Distance Sensor
                "trig": 23,  # HCSR04 Distance Sensor
                "i2c_bus_kind": "basic",  # VL53L0X/VL53L4CD/VL53L1X: "basic" | "extended"
                "i2c_bus_num": "CP2112",  # VL53L0X/VL53L4CD/VL53L1X: numbered bus or adapter-name match
                "address": None,  # VL53L0X/VL53L4CD/VL53L1X: optional I2C address override (hex string or int)
                "device": "/dev/ttyACM0",  # SEN0628: USB-serial device path
            },
```

- [ ] **Step 4: Add the manifest entry**

In `wizard/wizard_manifest.json`, inside `modules.distance`, add a `"sen0628"` key (alongside the existing `hcsr04`/`vl53l0x`/`vl53l4cd`/`vl53l1x`/`prototype`/`none` entries):

```json
    "sen0628": {
      "friendly_name": "DFRobot SEN0628 8x8 Matrix ToF Distance Sensor (USB)",
      "filename": "sen0628",
      "description": "An 8x8-point Time-of-Flight depth sensor (20mm-3.5m range) connected via its onboard USB-C port. A good option for taller hoppers.",
      "default": false,
      "image": "sen0628.png",
      "py_dependencies": [],
      "apt_dependencies": [],
      "command_list": [],
      "settings_dependencies": {
        "sen0628_device": {
          "friendly_name": "Serial Device (USB)",
          "description": "Path to the SEN0628's USB-C serial device (e.g. /dev/ttyACM0). Use Discover to scan for connected USB serial devices.",
          "type": "usb_serial_device",
          "vid": null,
          "pid": null,
          "default": "/dev/ttyACM0",
          "options": {
            "/dev/ttyACM0": "/dev/ttyACM0",
            "/dev/ttyACM1": "/dev/ttyACM1",
            "/dev/ttyUSB0": "/dev/ttyUSB0",
            "/dev/ttyUSB1": "/dev/ttyUSB1"
          },
          "settings": ["platform", "devices", "distance", "device"]
        }
      }
    },
```

- [ ] **Step 5: Add the placeholder image**

```bash
cp static/img/wizard/none.png static/img/wizard/sen0628.png
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/distance/test_distance_manifest.py tests/unit/common/test_default_settings_devices.py -v`
Expected: PASS

Also run the full manifest/settings suites to catch any JSON-shape assumption broken elsewhere:

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/distance/ tests/unit/wizard/ tests/web/test_wizard_install_info_defaults.py -v`
Expected: PASS

- [ ] **Step 7: Format and commit**

```bash
uvx ruff format common/common.py tests/unit/distance/test_distance_manifest.py tests/unit/common/test_default_settings_devices.py
git add common/common.py wizard/wizard_manifest.json static/img/wizard/sen0628.png tests/unit/distance/test_distance_manifest.py tests/unit/common/test_default_settings_devices.py
git commit -F - <<'EOF'
feat: register SEN0628 distance sensor in wizard manifest

Adds the settings default for the sensor's serial device path and a
wizard_manifest.json entry (type: usb_serial_device, vid/pid left
null pending real hardware) so the sensor is selectable in the setup
wizard. Image is a placeholder copy of none.png until a real product
photo is available.
EOF
```

---

### Task 4: Generic USB-serial device discovery

**Files:**
- Create: `common/usb_serial.py`
- Create: `tests/unit/usb_serial/__init__.py` (empty)
- Create: `tests/unit/usb_serial/test_usb_serial_discovery.py`

**Interfaces:**
- Produces: `common.usb_serial.discover_usb_serial_devices(vid=None, pid=None)` → `list[dict]`, each dict shaped `{"device": str, "description": str, "manufacturer": str, "serial_number": str, "vid": int | None, "pid": int | None}`. Never raises; returns `[]` on enumeration failure. This is the function Task 5's route imports and calls.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/usb_serial/__init__.py` (empty file).

Create `tests/unit/usb_serial/test_usb_serial_discovery.py`:

```python
from unittest import mock

from common.usb_serial import discover_usb_serial_devices


class _FakePort:
    def __init__(self, device, description="", manufacturer=None, serial_number=None, vid=None, pid=None):
        self.device = device
        self.description = description
        self.manufacturer = manufacturer
        self.serial_number = serial_number
        self.vid = vid
        self.pid = pid


def test_discover_returns_all_ports_when_unfiltered():
    ports = [
        _FakePort("/dev/ttyACM0", description="SEN0628", vid=0x2E8A, pid=0x000A),
        _FakePort("/dev/ttyUSB0", description="FTDI adapter", vid=0x0403, pid=0x6001),
    ]
    with mock.patch("common.usb_serial.list_ports.comports", return_value=ports):
        result = discover_usb_serial_devices()
    assert [d["device"] for d in result] == ["/dev/ttyACM0", "/dev/ttyUSB0"]


def test_discover_filters_by_vid_and_pid():
    ports = [
        _FakePort("/dev/ttyACM0", vid=0x2E8A, pid=0x000A),
        _FakePort("/dev/ttyUSB0", vid=0x0403, pid=0x6001),
    ]
    with mock.patch("common.usb_serial.list_ports.comports", return_value=ports):
        result = discover_usb_serial_devices(vid=0x2E8A, pid=0x000A)
    assert [d["device"] for d in result] == ["/dev/ttyACM0"]


def test_discover_filters_by_vid_only():
    ports = [
        _FakePort("/dev/ttyACM0", vid=0x2E8A, pid=0x000A),
        _FakePort("/dev/ttyACM1", vid=0x2E8A, pid=0x0009),
        _FakePort("/dev/ttyUSB0", vid=0x0403, pid=0x6001),
    ]
    with mock.patch("common.usb_serial.list_ports.comports", return_value=ports):
        result = discover_usb_serial_devices(vid=0x2E8A)
    assert [d["device"] for d in result] == ["/dev/ttyACM0", "/dev/ttyACM1"]


def test_discover_returns_empty_list_on_enumeration_failure():
    with mock.patch("common.usb_serial.list_ports.comports", side_effect=OSError("no such device")):
        assert discover_usb_serial_devices() == []


def test_discover_includes_serial_number_and_manufacturer():
    ports = [_FakePort("/dev/ttyACM0", description="SEN0628", manufacturer="DFRobot", serial_number="ABC123")]
    with mock.patch("common.usb_serial.list_ports.comports", return_value=ports):
        result = discover_usb_serial_devices()
    assert result[0]["manufacturer"] == "DFRobot"
    assert result[0]["serial_number"] == "ABC123"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/usb_serial/ -v`
Expected: FAIL/ERROR — `ModuleNotFoundError: No module named 'common.usb_serial'`

- [ ] **Step 3: Write the implementation**

Create `common/usb_serial.py`:

```python
#!/usr/bin/env python3

# *****************************************
# PiFire USB Serial Device Discovery
# *****************************************
#
# Description: Best-effort discovery of connected USB serial devices, for
#   the wizard's "Discover" button on serial-device-path settings fields
#   (e.g. distance/sen0628.py's device path). Optionally filtered by USB
#   vendor/product ID; when neither is given, every enumerable serial
#   device is returned, so a not-yet-configured vid/pid still yields a
#   usable (if unfiltered) device list rather than nothing.
#
# *****************************************

from serial.tools import list_ports


def discover_usb_serial_devices(vid=None, pid=None):
    """Best-effort list of connected USB serial devices, for the wizard's
    Discover button. Returns [] if pyserial can't enumerate ports; never
    raises. Each result is a dict with 'device', 'description',
    'manufacturer', 'serial_number', 'vid', 'pid'."""
    try:
        ports = list_ports.comports()
    except Exception:
        return []

    results = []
    for port in ports:
        if vid is not None and port.vid != vid:
            continue
        if pid is not None and port.pid != pid:
            continue
        results.append(
            {
                "device": port.device,
                "description": port.description or "",
                "manufacturer": getattr(port, "manufacturer", None) or "",
                "serial_number": port.serial_number or "",
                "vid": port.vid,
                "pid": port.pid,
            }
        )
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/usb_serial/ -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Format and commit**

```bash
uvx ruff format common/usb_serial.py tests/unit/usb_serial/test_usb_serial_discovery.py
git add common/usb_serial.py tests/unit/usb_serial/__init__.py tests/unit/usb_serial/test_usb_serial_discovery.py
git commit -F - <<'EOF'
feat: add generic USB-serial device discovery helper

discover_usb_serial_devices(vid, pid) wraps pyserial's list_ports,
optionally filtering by USB vendor/product ID. Reusable by any future
USB-serial module's wizard Discover button, not just SEN0628.
EOF
```

---

### Task 5: `usb_serial_scan` wizard route

**Files:**
- Modify: `blueprints/wizard/routes.py`
- Modify: `tests/web/test_webapp_sqlite.py`

**Interfaces:**
- Consumes: `common.usb_serial.discover_usb_serial_devices(vid=None, pid=None)` (Task 4); the existing `render_i2c_scan_table(itemID, groups, error)` Jinja macro in `blueprints/probeconfig/templates/probeconfig/_macro_probes_config.html` (unchanged — reused as-is).
- Produces: `POST /wizard/usb_serial_scan` (form fields `itemID`, `vid`, `pid` — `vid`/`pid` are optional hex strings like `"2E8A"`, blank/absent means unfiltered), returning the same `groups`/`error`-shaped HTML fragment as `/wizard/i2c_bus_scan`. This is the endpoint Task 6's `scanUsbSerial()` JS calls.

- [ ] **Step 1: Write the failing tests**

Add to `tests/web/test_webapp_sqlite.py`, after the existing `test_i2c_bus_scan_no_devices_shows_error` function:

```python
def test_usb_serial_scan_lists_all_devices_when_unfiltered(monkeypatch):
    flask_app.config.update(TESTING=True)
    client = flask_app.test_client()

    import blueprints.wizard.routes as wizard_routes

    monkeypatch.setattr(
        wizard_routes,
        "discover_usb_serial_devices",
        lambda vid=None, pid=None: [
            {
                "device": "/dev/ttyACM0",
                "description": "SEN0628",
                "manufacturer": "DFRobot",
                "serial_number": "AB12",
                "vid": 0x2E8A,
                "pid": 0x000A,
            }
        ],
    )

    resp = client.post("/wizard/usb_serial_scan", data={"itemID": "distance_sen0628_device", "vid": "", "pid": ""})
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "/dev/ttyACM0" in body
    assert "All Serial Devices" in body


def test_usb_serial_scan_filters_by_vid_pid(monkeypatch):
    flask_app.config.update(TESTING=True)
    client = flask_app.test_client()

    import blueprints.wizard.routes as wizard_routes

    captured = {}

    def _fake_discover(vid=None, pid=None):
        captured["vid"] = vid
        captured["pid"] = pid
        return [{"device": "/dev/ttyACM0", "description": "SEN0628", "manufacturer": "", "serial_number": "", "vid": vid, "pid": pid}]

    monkeypatch.setattr(wizard_routes, "discover_usb_serial_devices", _fake_discover)

    resp = client.post(
        "/wizard/usb_serial_scan", data={"itemID": "distance_sen0628_device", "vid": "2E8A", "pid": "000A"}
    )
    assert resp.status_code == 200
    assert captured["vid"] == 0x2E8A
    assert captured["pid"] == 0x000A
    assert "Matched Devices" in resp.get_data(as_text=True)


def test_usb_serial_scan_no_devices_shows_error(monkeypatch):
    flask_app.config.update(TESTING=True)
    client = flask_app.test_client()

    import blueprints.wizard.routes as wizard_routes

    monkeypatch.setattr(wizard_routes, "discover_usb_serial_devices", lambda vid=None, pid=None: [])

    resp = client.post("/wizard/usb_serial_scan", data={"itemID": "distance_sen0628_device", "vid": "", "pid": ""})
    assert resp.status_code == 200
    assert "No serial devices found." in resp.get_data(as_text=True)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_webapp_sqlite.py -k usb_serial_scan -v`
Expected: FAIL — 404 (no such route) or `AttributeError: module 'blueprints.wizard.routes' has no attribute 'discover_usb_serial_devices'`

- [ ] **Step 3: Add the import**

In `blueprints/wizard/routes.py`, the existing import block reads:

```python
from common.i2c_bus import (
    I2CBusConfigError,
    discover_extended_i2c_buses,
    discover_ft232h_devices,
    discover_mcp2221_devices,
    validate_bus_kinds,
)
```

Add a new import directly after it:

```python
from common.usb_serial import discover_usb_serial_devices
```

- [ ] **Step 4: Add the route branch**

In `wizard_page()`, immediately after the existing `if action == "i2c_bus_scan": ... return render_template_string(...)` block (and still inside the `elif request.method == "POST":` branch, before the function's closing `""" Create Temporary Probe Device/Port Structure for Setup..."""` section), add:

```python
        if action == "usb_serial_scan":
            itemID = r["itemID"]
            vid_raw = r.get("vid", "")
            pid_raw = r.get("pid", "")
            groups = []
            error = None

            try:
                vid = int(vid_raw, 16) if vid_raw else None
                pid = int(pid_raw, 16) if pid_raw else None
                devices = discover_usb_serial_devices(vid=vid, pid=pid)
                items = [
                    {
                        "value": d["device"],
                        "label": f"{d['device']} — {d['description'] or 'Unknown device'}"
                        + (f" (serial {d['serial_number']})" if d["serial_number"] else ""),
                    }
                    for d in devices
                ]
                if items:
                    title = "Matched Devices" if (vid is not None or pid is not None) else "All Serial Devices"
                    groups.append({"title": title, "items": items})
                if not groups:
                    error = (
                        "No matching USB serial devices found."
                        if (vid is not None or pid is not None)
                        else "No serial devices found."
                    )
            except Exception as e:
                error = f"Something bad happened: {e}"

            render_string = "{% from 'probeconfig/_macro_probes_config.html' import render_i2c_scan_table %}{{ render_i2c_scan_table(itemID, groups, error) }}"
            return render_template_string(render_string, itemID=itemID, groups=groups, error=error)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_webapp_sqlite.py -v`
Expected: PASS (all tests in the file, including the 3 new ones and the pre-existing `i2c_bus_scan` ones — confirms no regression)

- [ ] **Step 6: Format and commit**

```bash
uvx ruff format blueprints/wizard/routes.py tests/web/test_webapp_sqlite.py
git add blueprints/wizard/routes.py tests/web/test_webapp_sqlite.py
git commit -F - <<'EOF'
feat: add usb_serial_scan wizard route

Generic Discover-button backend for any usb_serial_device wizard
field, not just SEN0628 -- optionally filters by vid/pid, reuses the
existing render_i2c_scan_table macro for results.
EOF
```

---

### Task 6: Wizard UI wiring (Discover button + dispatch)

**Files:**
- Modify: `blueprints/probeconfig/templates/probeconfig/_macro_probes_config.html`
- Modify: `blueprints/probeconfig/static/probeconfig/js/probeconfig.js`
- Modify: `blueprints/wizard/templates/wizard/_macro_wizard_card.html`

**Interfaces:**
- Consumes: `POST /wizard/usb_serial_scan` (Task 5); the existing `selectI2CBus(value, itemID)` JS function and `i2c_{{dom_id}}_Modal` / `i2c_{{dom_id}}_Select` DOM ID convention (both unchanged, reused as-is — this is why the new modal must use that same ID scheme).
- Produces: Jinja macro `render_input_usb_serial_device(dom_id, css_class, default, vid, pid)`; JS function `scanUsbSerial(itemID, vid, pid)`. Wired into `_macro_wizard_card.html`'s per-setting dispatch via `settings_dependencies[setting].type == 'usb_serial_device'`.

There is no existing automated test coverage for the equivalent I2C-bridge Discover *UI* wiring (only for the backend route, covered in Task 5, and for an unrelated nested-modal-scroll interaction in `tests/web/test_wizard_nested_modal_scroll.py`) — this task is verified manually per Step 4 below, consistent with that existing coverage gap.

- [ ] **Step 1: Add the input macro**

In `blueprints/probeconfig/templates/probeconfig/_macro_probes_config.html`, insert a new macro immediately after `render_input_i2c_bus_num` ends (i.e. right after its `{% endmacro %}`, before `{% macro render_input_thermoworks_discover(...) %}`):

```html
{% macro render_input_usb_serial_device(dom_id, css_class, default, vid, pid) %}

<div class="input-group mb-3">
    <input type="text" class="form-control {{ css_class }}"
    value="{{ default }}" aria-label="usb_serial_device"
    id="{{ dom_id }}"
    name="{{ dom_id }}"/>
    <div class="input-group-append">
        <button type="button" class="btn btn-success" id="i2c_{{ dom_id }}_Scan" onclick="scanUsbSerial('{{ dom_id }}', '{{ vid or '' }}', '{{ pid or '' }}')">Discover</button>
    </div>
</div>

<!-- Discover USB Serial Device Modal -->
<div class="modal fade power-modal" id="i2c_{{ dom_id }}_Modal" data-backdrop="false" tabindex="-1" aria-labelledby="i2c_{{ dom_id }}_Label" aria-hidden="true" >
    <div class="modal-dialog modal-xl">
    <div class="modal-content">
        <div class="modal-header">
        <h5 class="modal-title" id="i2c_{{ dom_id }}_Label">Discovered USB Serial Devices</h5>
        <button type="button" class="close" data-dismiss="modal" aria-label="Close">
            <span aria-hidden="true">&times;</span>
        </button>
        </div>
        <div class="modal-body text-center">

            <div id="i2c_{{ dom_id }}_Select">
                <br>
                <h4>Scanning...</h4>
                <br>
                <div class="fa-3x">
                    <i class="fa-solid fa-magnifying-glass fa-bounce"></i>
                </div>
                <br>
            </div>

        </div>
        <div class="modal-footer">
        <button type="button" class="btn btn-secondary" data-dismiss="modal">Close</button>
        <button type="button" class="btn btn-primary" value="" onclick="scanUsbSerial('{{ dom_id }}', '{{ vid or '' }}', '{{ pid or '' }}')">Refresh</button>
        </div>
    </div>
    </div>
</div>

{% endmacro %}
```

- [ ] **Step 2: Add the JS trigger function**

In `blueprints/probeconfig/static/probeconfig/js/probeconfig.js`, insert immediately after the existing `selectI2CBus` function:

```javascript
//
// USB Serial Device Discovery Functions
//
function scanUsbSerial(itemID, vid, pid) {
	const modal = '#i2c_' + itemID + '_Modal';
	const modalContent = '#i2c_' + itemID + '_Select';
	$(modal).modal('show');
	// Show scanning text while scanning
	$(modalContent).html('<br> \
                <h4>Scanning...</h4> \
                <br> \
                <div class="fa-3x"> \
                    <i class="fa-solid fa-magnifying-glass fa-bounce"></i> \
                </div> \
                <br></br>');
	// Load the USB serial scan results
	$(modalContent).load("/wizard/usb_serial_scan", {"itemID" : itemID, "vid" : vid, "pid" : pid});
}
```

Selection reuses the existing `selectI2CBus(value, itemID)` function unmodified — no new selection JS is needed since the results table (rendered by `render_i2c_scan_table`) already calls it.

- [ ] **Step 3: Wire the dispatch branch**

In `blueprints/wizard/templates/wizard/_macro_wizard_card.html`, the file's import line currently reads:

```
{% from 'probeconfig/_macro_probes_config.html' import render_input_i2c_bus_num %}
```

Change it to:

```
{% from 'probeconfig/_macro_probes_config.html' import render_input_i2c_bus_num, render_input_usb_serial_device %}
```

The setting-rendering block currently reads:

```
						{% if moduleData['settings_dependencies'][setting].get('type') == 'i2c_bus_num' %}
						{{ render_input_i2c_bus_num(moduleSection ~ '_' ~ setting, '', moduleSettings['settings'][setting], moduleSection ~ '_' ~ (setting | replace('_num', '_kind'))) }}
						{% else %}
```

Change it to:

```
						{% if moduleData['settings_dependencies'][setting].get('type') == 'i2c_bus_num' %}
						{{ render_input_i2c_bus_num(moduleSection ~ '_' ~ setting, '', moduleSettings['settings'][setting], moduleSection ~ '_' ~ (setting | replace('_num', '_kind'))) }}
						{% elif moduleData['settings_dependencies'][setting].get('type') == 'usb_serial_device' %}
						{{ render_input_usb_serial_device(moduleSection ~ '_' ~ setting, '', moduleSettings['settings'][setting], moduleData['settings_dependencies'][setting].get('vid'), moduleData['settings_dependencies'][setting].get('pid')) }}
						{% else %}
```

- [ ] **Step 4: Manually verify in the running app**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run python app.py &
```

Navigate to the wizard, pick the SEN0628 as the distance module, confirm:
- The "Serial Device (USB)" field renders as a text input with a "Discover" button (not a plain dropdown).
- Clicking "Discover" opens a modal showing "Scanning...", then either a device list or "No serial devices found." (expected in a dev environment with no USB serial devices attached).
- Selecting a result (if any) populates the text field and closes the modal.

Stop the dev server afterward.

- [ ] **Step 5: Run the existing wizard test suites to check for regressions**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/ tests/unit/wizard/ -v`
Expected: PASS (no regressions in existing wizard/modal tests, including `test_wizard_nested_modal_scroll.py` and `test_wizard_modulecard_renders_i2c_bus_num_as_free_text`)

- [ ] **Step 6: Commit**

No Python files changed in this task (only `.html`/`.js`), so there's nothing for `ruff format` to do here.

```bash
git add blueprints/probeconfig/templates/probeconfig/_macro_probes_config.html blueprints/probeconfig/static/probeconfig/js/probeconfig.js blueprints/wizard/templates/wizard/_macro_wizard_card.html
git commit -F - <<'EOF'
feat: wire USB-serial Discover button into the wizard UI

Adds render_input_usb_serial_device (input + modal, reusing the
existing i2c_{dom_id}_Modal ID scheme and selectI2CBus/
render_i2c_scan_table so no new selection JS or results markup is
needed) and dispatches to it from _macro_wizard_card.html for any
settings_dependencies field with type: usb_serial_device.
EOF
```

---

## Final verification

After all 6 tasks are complete, run the full suite once to confirm nothing was missed:

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -v
```

Expected: PASS, no regressions anywhere in the suite.
