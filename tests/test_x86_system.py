from unittest import mock

import pytest


@pytest.fixture
def platform():
	import grillplat.x86_numato as mod

	with (
		mock.patch.object(mod, 'NumatoUSBRelay'),
		mock.patch.object(mod, 'EMC2101_LUT'),
		mock.patch.object(mod, 'EMC2301'),
		mock.patch.object(mod, 'ExtendedI2C'),
		mock.patch.object(mod, 'busio'),
		mock.patch.object(mod, 'board'),
		mock.patch.object(mod, 'find_i2c_bus', return_value=7),
	):
		config = {'outputs': {'power': 0, 'igniter': 1, 'auger': 2, 'fan': 3}, 'frequency': 100}
		yield mod.GrillPlatform(config)


def test_check_throttled_reports_ok_and_false(platform):
	data = platform.check_throttled([])
	assert data['result'] == 'OK'
	assert data['data']['cpu_under_voltage'] is False
	assert data['data']['cpu_throttled'] is False


def test_check_cpu_temp_uses_psutil(platform):
	import grillplat.x86_numato as mod

	fake_reading = mock.Mock(current=47.0)
	with mock.patch('psutil.sensors_temperatures', return_value={'coretemp': [fake_reading]}):
		data = platform.check_cpu_temp([])
	assert data['result'] == 'OK'
	assert data['data']['cpu_temp'] == 47.0


def test_check_cpu_temp_handles_no_sensors(platform):
	with mock.patch('psutil.sensors_temperatures', return_value={}):
		data = platform.check_cpu_temp([])
	assert data['data']['cpu_temp'] == 0.0


def test_supported_commands_lists_commands(platform):
	data = platform.supported_commands([])
	assert 'check_cpu_temp' in data['data']['supported_cmds']


def test_check_alive_ok(platform):
	assert platform.check_alive([])['result'] == 'OK'


def test_cleanup_stops_fan_and_closes_relay(platform):
	platform.cleanup()
	platform.relay.reset.assert_called()
	platform.relay.close.assert_called()
	assert platform.emc.manual_fan_speed == 0
