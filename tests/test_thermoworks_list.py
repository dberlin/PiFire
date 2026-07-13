import types

from tools.thermoworks_list import channel_label, format_temp, resolve_credentials


def _channel(value, units, label=None):
	return types.SimpleNamespace(value=value, units=units, label=label)


def test_format_temp_as_reported_uses_cloud_units():
	assert format_temp(_channel(212.5, 'F'), None) == '212.5 \N{DEGREE SIGN}F'
	assert format_temp(_channel(21.0, 'C'), None) == '21 \N{DEGREE SIGN}C'


def test_format_temp_missing_channel_and_no_reading():
	assert format_temp(None, 'F') == '(not found)'
	assert format_temp(_channel(None, 'F'), 'F') == '(no reading)'


def test_format_temp_normalizes_units():
	# 100 C -> 212 F, and F source shown in C.
	assert format_temp(_channel(100.0, 'C'), 'F') == '212.0 \N{DEGREE SIGN}F'
	assert format_temp(_channel(212.0, 'F'), 'C') == '100.0 \N{DEGREE SIGN}C'


def test_channel_label_falls_back_to_number():
	assert channel_label(_channel(1, 'F', label='Brisket'), 3) == 'Brisket'
	assert channel_label(_channel(1, 'F', label=''), 3) == 'Channel 3'
	assert channel_label(None, 5) == 'Channel 5'


def test_resolve_credentials_prefers_cli_then_env(monkeypatch):
	monkeypatch.delenv('THERMOWORKS_EMAIL', raising=False)
	monkeypatch.delenv('THERMOWORKS_PASSWORD', raising=False)
	assert resolve_credentials('a@b.com', 'pw') == ('a@b.com', 'pw')

	monkeypatch.setenv('THERMOWORKS_EMAIL', 'env@b.com')
	monkeypatch.setenv('THERMOWORKS_PASSWORD', 'envpw')
	assert resolve_credentials(None, None) == ('env@b.com', 'envpw')
