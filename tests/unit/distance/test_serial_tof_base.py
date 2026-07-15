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

    def __init__(self, *args, reading_mm=100, read_delay=0, fail_open_calls=(), **kwargs):
        self.open_calls = 0
        self.opened_with = None
        self._reading_mm = reading_mm
        self._read_delay = read_delay
        self._fail_open_calls = set(fail_open_calls)
        super().__init__(*args, **kwargs)

    def _open_sensor(self, ser):
        self.open_calls += 1
        if self.open_calls in self._fail_open_calls:
            raise RuntimeError("simulated sensor re-init failure")
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


def _make_hopper(serial_tof_mod, dev_pins=None, reading_mm=100, empty=22, full=4, read_delay=0, fail_open_calls=()):
    class TestHopperLevel(FakeSensorMixin, serial_tof_mod.SerialToFHopperLevel):
        _serial_tof_mod = serial_tof_mod

    return TestHopperLevel(
        dev_pins or {},
        empty=empty,
        full=full,
        reading_mm=reading_mm,
        read_delay=read_delay,
        fail_open_calls=fail_open_calls,
    )


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


def test_slow_read_cycle_survives_failed_reinit(serial_tof_mod):
    """A RuntimeError from a failed re-init attempt (e.g. HopperLevel._open_sensor
    exhausting its setmode retries) must not escape _sensing_loop and kill the
    background thread -- it should be caught, logged, and the loop should keep
    running so it can try again on the next slow cycle."""
    # open call #1 is the initial __init__ open; open call #2 is the first
    # re-init attempt triggered by the slow read cycle below -- make that one fail.
    hopper = _make_hopper(serial_tof_mod, reading_mm=100, read_delay=0.2, fail_open_calls={2})
    try:
        # First slow cycle: the re-init attempt (open call #2) raises, but the
        # loop must survive it.
        hopper.get_level(override=True)
        assert hopper.open_calls == 2
        assert hopper.sensor_thread.is_alive()
        assert hopper.sensor_thread_active is True

        # Second slow cycle: the thread is still polling and retries the
        # re-init -- this time it succeeds, proving genuine recovery.
        hopper.get_level(override=True)
        assert hopper.open_calls == 3
        assert hopper.sensor_thread.is_alive()
        assert hopper.sensor_thread_active is True
    finally:
        _stop(hopper)


def test_reinit_closes_previous_serial_port(monkeypatch):
    """__start_sensor must close the previously-opened serial port before
    opening a new one on re-init, instead of leaking the file descriptor."""
    import distance._serial_tof_base as mod

    opened_ports = []

    def _fake_serial(*args, **kwargs):
        port = mock.MagicMock(name=f"serial_port_{len(opened_ports)}")
        opened_ports.append(port)
        return port

    with (
        mock.patch.object(mod.serial, "Serial", side_effect=_fake_serial),
        mock.patch.object(mod, "time", _FakeClock()),
    ):
        hopper = _make_hopper(mod, reading_mm=100, read_delay=0.2)  # forces a re-init every cycle
        try:
            hopper.get_level(override=True)  # triggers the slow-cycle re-init
            assert len(opened_ports) >= 2
            first_port, second_port = opened_ports[0], opened_ports[1]
            first_port.close.assert_called_once()
            second_port.close.assert_not_called()
        finally:
            _stop(hopper)
