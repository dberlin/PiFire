from unittest import mock


def _make_hopper(tof_mod, vl_mod, dev_pins=None, distance_cm=10.0):
	with mock.patch.object(vl_mod, 'VL53L1X') as VL53L1X:
		VL53L1X.return_value.data_ready = True
		VL53L1X.return_value.distance = distance_cm
		hopper = vl_mod.HopperLevel(dev_pins or {}, empty=22, full=4)
	return hopper, VL53L1X


def _stop(hopper):
	hopper.sensor_thread_active = False
	hopper.sensor_thread.join(timeout=2)


def test_open_sensor_constructs_vl53l1x_at_resolved_address_and_starts_ranging():
	import distance._tof_base as tof_mod
	import distance.vl53l1x as vl_mod

	with mock.patch.object(tof_mod, 'open_i2c_bus', return_value=mock.sentinel.bus):
		hopper, VL53L1X = _make_hopper(tof_mod, vl_mod)
		try:
			VL53L1X.assert_called_once_with(mock.sentinel.bus, address=0x29)
			VL53L1X.return_value.start_ranging.assert_called_once()
		finally:
			_stop(hopper)


def test_open_sensor_uses_configured_address():
	import distance._tof_base as tof_mod
	import distance.vl53l1x as vl_mod

	with mock.patch.object(tof_mod, 'open_i2c_bus', return_value=mock.sentinel.bus):
		hopper, VL53L1X = _make_hopper(tof_mod, vl_mod, dev_pins={'distance': {'address': '0x2a'}})
		try:
			VL53L1X.assert_called_once_with(mock.sentinel.bus, address=0x2A)
		finally:
			_stop(hopper)


def test_read_distance_mm_converts_cm_to_mm_and_clears_interrupt():
	import distance._tof_base as tof_mod
	import distance.vl53l1x as vl_mod

	with mock.patch.object(tof_mod, 'open_i2c_bus', return_value=mock.sentinel.bus):
		hopper, VL53L1X = _make_hopper(tof_mod, vl_mod, distance_cm=12.5)
		try:
			assert hopper._read_distance_mm() == 125.0
			assert VL53L1X.return_value.clear_interrupt.call_count >= 1
		finally:
			_stop(hopper)


def test_read_distance_mm_returns_zero_when_out_of_range():
	import distance._tof_base as tof_mod
	import distance.vl53l1x as vl_mod

	with mock.patch.object(tof_mod, 'open_i2c_bus', return_value=mock.sentinel.bus):
		# The VL53L1X reports None when the reading is invalid / out of range.
		hopper, VL53L1X = _make_hopper(tof_mod, vl_mod, distance_cm=None)
		try:
			assert hopper._read_distance_mm() == 0
			assert VL53L1X.return_value.clear_interrupt.call_count >= 1
		finally:
			_stop(hopper)


def test_close_sensor_stops_ranging():
	import distance._tof_base as tof_mod
	import distance.vl53l1x as vl_mod

	with mock.patch.object(tof_mod, 'open_i2c_bus', return_value=mock.sentinel.bus):
		hopper, VL53L1X = _make_hopper(tof_mod, vl_mod)
		try:
			hopper._close_sensor()
			VL53L1X.return_value.stop_ranging.assert_called_once()
		finally:
			_stop(hopper)
