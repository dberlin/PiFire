from unittest import mock


def test_check_throttled_reports_ok_and_false(x86_platform):
	data = x86_platform.check_throttled([])
	assert data['result'] == 'OK'
	assert data['data']['cpu_under_voltage'] is False
	assert data['data']['cpu_throttled'] is False


def test_check_cpu_temp_uses_psutil(x86_platform):
	import grillplat.x86_numato as mod

	fake_reading = mock.Mock(current=47.0)
	with mock.patch('psutil.sensors_temperatures', return_value={'coretemp': [fake_reading]}):
		data = x86_platform.check_cpu_temp([])
	assert data['result'] == 'OK'
	assert data['data']['cpu_temp'] == 47.0


def test_check_cpu_temp_handles_no_sensors(x86_platform):
	with mock.patch('psutil.sensors_temperatures', return_value={}):
		data = x86_platform.check_cpu_temp([])
	assert data['data']['cpu_temp'] == 0.0


def test_supported_commands_lists_commands(x86_platform):
	data = x86_platform.supported_commands([])
	assert 'check_cpu_temp' in data['data']['supported_cmds']


def test_check_alive_ok(x86_platform):
	assert x86_platform.check_alive([])['result'] == 'OK'


def test_cleanup_stops_fan_and_closes_relay(x86_platform):
	x86_platform.cleanup()
	x86_platform.relay.reset.assert_called()
	x86_platform.relay.close.assert_called()
	assert x86_platform.emc.manual_fan_speed == 0
