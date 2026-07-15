import sys
import types
import importlib

import pytest


def _install_fake_adafruit(monkeypatch):
	"""Install a fake adafruit_max31856 so the probe imports without hardware."""
	fake = types.ModuleType('adafruit_max31856')

	class ThermocoupleType:
		B = 'TC_B'
		E = 'TC_E'
		J = 'TC_J'
		K = 'TC_K'
		N = 'TC_N'
		R = 'TC_R'
		S = 'TC_S'
		T = 'TC_T'

	class FakeMAX31856:
		def __init__(self, spi, cs, thermocouple_type=None):
			self.spi = spi
			self.cs = cs
			self.thermocouple_type = thermocouple_type
			self.averaging = None
			self.noise_rejection = None

	fake.ThermocoupleType = ThermocoupleType
	fake.MAX31856 = FakeMAX31856
	monkeypatch.setitem(sys.modules, 'adafruit_max31856', fake)
	return fake


def _load_probe(monkeypatch):
	_install_fake_adafruit(monkeypatch)
	import probes.max31856_adafruit as probe

	importlib.reload(probe)  # bind the fake adafruit_max31856
	return probe


def test_init_device_wires_bus_type_and_settings(monkeypatch):
	probe = _load_probe(monkeypatch)

	captured = {}

	def fake_resolve(config, default_cs):
		captured['config'] = config
		captured['default_cs'] = default_cs
		return ('SPI', 'CS')

	monkeypatch.setattr(probe, 'resolve_spi_bus', fake_resolve)

	obj = probe.ReadProbes.__new__(probe.ReadProbes)  # bypass heavy base __init__
	obj.device_info = {
		'config': {'spi_bus_kind': 'mcp2210', 'cs': '5', 'tc_type': 'J', 'averaging': '8', 'noise_rejection': '50'}
	}
	obj._init_device()

	assert captured['default_cs'] == 'D6'
	assert obj.device_info['ports'] == ['TC0']
	sensor = obj.device.sensor
	assert sensor.spi == 'SPI' and sensor.cs == 'CS'
	assert sensor.thermocouple_type == 'TC_J'  # 'J' mapped via ThermocoupleType
	assert sensor.averaging == 8  # int-parsed
	assert sensor.noise_rejection == 50  # int-parsed


def test_init_device_defaults(monkeypatch):
	probe = _load_probe(monkeypatch)
	monkeypatch.setattr(probe, 'resolve_spi_bus', lambda config, default_cs: ('SPI', 'CS'))

	obj = probe.ReadProbes.__new__(probe.ReadProbes)
	obj.device_info = {'config': {}}  # no keys -> all defaults
	obj._init_device()

	sensor = obj.device.sensor
	assert sensor.thermocouple_type == 'TC_K'  # default K
	assert sensor.averaging == 1  # default 1
	assert sensor.noise_rejection == 60  # default 60


def test_temperature_property(monkeypatch):
	probe = _load_probe(monkeypatch)
	dev = probe.TCDevice.__new__(probe.TCDevice)

	class S:
		temperature = 123.4

	dev.sensor = S()
	assert dev.temperature == 123.4


import json
import os


def test_manifest_max31856_entry():
	repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
	manifest = json.load(open(os.path.join(repo_root, 'wizard', 'wizard_manifest.json')))
	probes = manifest['modules']['probes']
	assert 'max31856_adafruit' in probes
	entry = probes['max31856_adafruit']

	ds = entry['device_specific']
	assert ds['type'] == 'thermocouple'
	assert ds['ports'] == ['TC0']

	labels = [item['label'] for item in ds['config']]
	for required in ('cs', 'spi_bus_kind', 'mcp2210_serial', 'tc_type', 'averaging', 'noise_rejection'):
		assert required in labels

	tc = next(i for i in ds['config'] if i['label'] == 'tc_type')
	assert tc['list_values'] == ['B', 'E', 'J', 'K', 'N', 'R', 'S', 'T']
	assert tc['default'] == 'K'

	avg = next(i for i in ds['config'] if i['label'] == 'averaging')
	assert avg['list_values'] == ['1', '2', '4', '8', '16']

	nr = next(i for i in ds['config'] if i['label'] == 'noise_rejection')
	assert nr['list_values'] == ['60', '50']

	deps = ' '.join(entry['py_dependencies'])
	assert 'adafruit-circuitpython-max31856' in deps
	assert 'mcp2210' in deps
	assert 'hid' in deps
