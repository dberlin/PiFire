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

	with mock.patch.object(tof_mod, 'open_i2c_bus', return_value=mock.sentinel.bus):
		hopper, VL53L0X = _make_hopper(tof_mod, vl_mod)
		try:
			VL53L0X.assert_called_once_with(mock.sentinel.bus, address=0x29)
		finally:
			_stop(hopper)


def test_open_sensor_uses_configured_address():
	import distance._tof_base as tof_mod
	import distance.vl53l0x as vl_mod

	with mock.patch.object(tof_mod, 'open_i2c_bus', return_value=mock.sentinel.bus):
		hopper, VL53L0X = _make_hopper(tof_mod, vl_mod, dev_pins={'distance': {'address': '0x2a'}})
		try:
			VL53L0X.assert_called_once_with(mock.sentinel.bus, address=0x2A)
		finally:
			_stop(hopper)


def test_read_distance_mm_returns_range_directly():
	import distance._tof_base as tof_mod
	import distance.vl53l0x as vl_mod

	with mock.patch.object(tof_mod, 'open_i2c_bus', return_value=mock.sentinel.bus):
		hopper, VL53L0X = _make_hopper(tof_mod, vl_mod, range_value=123)
		try:
			assert hopper._read_distance_mm() == 123
		finally:
			_stop(hopper)
