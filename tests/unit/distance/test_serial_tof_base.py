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
