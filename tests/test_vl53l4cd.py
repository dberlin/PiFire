from unittest import mock


def _make_hopper(tof_mod, vl_mod, dev_pins=None, distance_cm=10.0):
	with mock.patch.object(vl_mod, 'VL53L4CD') as VL53L4CD:
		VL53L4CD.return_value.data_ready = True
		VL53L4CD.return_value.distance = distance_cm
		hopper = vl_mod.HopperLevel(dev_pins or {}, empty=22, full=4)
	return hopper, VL53L4CD


def _stop(hopper):
	hopper.sensor_thread_active = False
	hopper.sensor_thread.join(timeout=2)


def test_open_sensor_constructs_vl53l4cd_at_resolved_address_and_starts_ranging():
	import distance._tof_base as tof_mod
	import distance.vl53l4cd as vl_mod

	with mock.patch.object(tof_mod, 'busio'), mock.patch.object(tof_mod, 'board'):
		hopper, VL53L4CD = _make_hopper(tof_mod, vl_mod)
		try:
			VL53L4CD.assert_called_once_with(tof_mod.busio.I2C.return_value, address=0x29)
			VL53L4CD.return_value.start_ranging.assert_called_once()
		finally:
			_stop(hopper)


def test_open_sensor_uses_configured_address():
	import distance._tof_base as tof_mod
	import distance.vl53l4cd as vl_mod

	with mock.patch.object(tof_mod, 'busio'), mock.patch.object(tof_mod, 'board'):
		hopper, VL53L4CD = _make_hopper(tof_mod, vl_mod, dev_pins={'distance': {'address': '0x2a'}})
		try:
			VL53L4CD.assert_called_once_with(tof_mod.busio.I2C.return_value, address=0x2A)
		finally:
			_stop(hopper)


def test_read_distance_mm_converts_cm_to_mm_and_clears_interrupt():
	import distance._tof_base as tof_mod
	import distance.vl53l4cd as vl_mod

	with mock.patch.object(tof_mod, 'busio'), mock.patch.object(tof_mod, 'board'):
		hopper, VL53L4CD = _make_hopper(tof_mod, vl_mod, distance_cm=12.5)
		try:
			assert hopper._read_distance_mm() == 125.0
			assert VL53L4CD.return_value.clear_interrupt.call_count >= 1
		finally:
			_stop(hopper)


def test_close_sensor_stops_ranging():
	import distance._tof_base as tof_mod
	import distance.vl53l4cd as vl_mod

	with mock.patch.object(tof_mod, 'busio'), mock.patch.object(tof_mod, 'board'):
		hopper, VL53L4CD = _make_hopper(tof_mod, vl_mod)
		try:
			hopper._close_sensor()
			VL53L4CD.return_value.stop_ranging.assert_called_once()
		finally:
			_stop(hopper)
