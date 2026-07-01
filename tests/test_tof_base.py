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
