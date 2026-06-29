import sys
import types
import pytest

import probes.base as base
import mcp2210


# --- _gp_index ---


def test_gp_index_parses_all_forms():
	assert base._gp_index(3) == 3
	assert base._gp_index('3') == 3
	assert base._gp_index('GP3') == 3
	assert base._gp_index('GPIO3') == 3


def test_gp_index_rejects_out_of_range():
	with pytest.raises(ValueError):
		base._gp_index(9)


def test_gp_index_rejects_non_numeric():
	with pytest.raises(ValueError):
		base._gp_index('nope')


# --- resolve_mcp2210 caching ---


def test_resolve_mcp2210_caches_per_serial(monkeypatch):
	base._MCP2210_CACHE.clear()
	created = []

	class FakeMCP:
		def __init__(self, serial=None):
			created.append(serial)
			self.serial = serial

	monkeypatch.setattr(mcp2210, 'MCP2210', FakeMCP)
	a = base.resolve_mcp2210(None)
	b = base.resolve_mcp2210('')  # same canonical key as None
	c = base.resolve_mcp2210(None)
	assert a is b is c  # one shared instance
	assert created == [None]  # constructed exactly once
	d = base.resolve_mcp2210('ABC')
	assert d is not a
	assert created == [None, 'ABC']
	base._MCP2210_CACHE.clear()


# --- resolve_spi_bus: mcp2210 path ---


def test_resolve_spi_bus_mcp2210(monkeypatch):
	class FakeMCP:
		spi = 'SPIBUS'

		def digital_inout(self, n):
			return ('CS', n)

	monkeypatch.setattr(base, 'resolve_mcp2210', lambda serial=None: FakeMCP())
	spi, cs = base.resolve_spi_bus({'spi_bus_kind': 'mcp2210', 'cs': '5'}, default_cs='D6')
	assert spi == 'SPIBUS'
	assert cs == ('CS', 5)


# --- resolve_spi_bus: basic path (regression for the GPIOn KeyError bug) ---


def _install_fake_board(monkeypatch):
	fake_board = types.ModuleType('board')
	fake_board.D6 = 'BOARD_D6'
	fake_board.SPI = lambda: 'BOARD_SPI'
	fake_digitalio = types.ModuleType('digitalio')

	class DigitalInOut:
		def __init__(self, pin):
			self.pin = pin

	fake_digitalio.DigitalInOut = DigitalInOut
	monkeypatch.setitem(sys.modules, 'board', fake_board)
	monkeypatch.setitem(sys.modules, 'digitalio', fake_digitalio)
	return DigitalInOut


def test_resolve_spi_bus_basic_stored_gpio_value(monkeypatch):
	dio = _install_fake_board(monkeypatch)
	spi, cs = base.resolve_spi_bus({'spi_bus_kind': 'basic', 'cs': 'GPIO6'}, default_cs='D6')
	assert spi == 'BOARD_SPI'
	assert isinstance(cs, dio) and cs.pin == 'BOARD_D6'


def test_resolve_spi_bus_defaults_to_basic_and_accepts_d_name(monkeypatch):
	dio = _install_fake_board(monkeypatch)
	spi, cs = base.resolve_spi_bus({'cs': 'D6'}, default_cs='D6')  # no kind key
	assert spi == 'BOARD_SPI'
	assert isinstance(cs, dio) and cs.pin == 'BOARD_D6'


def test_resolve_spi_bus_unknown_kind_raises():
	with pytest.raises(ValueError):
		base.resolve_spi_bus({'spi_bus_kind': 'frobnicate'}, default_cs='D6')


def test_max31865_init_device_uses_resolver(monkeypatch):
	# Fake the adafruit lib so the probe module imports without hardware.
	fake_ada = types.ModuleType('adafruit_max31865')

	class FakeSensor:
		def __init__(self, spi, cs, rtd_nominal=None, ref_resistor=None, wires=None):
			self.spi = spi
			self.cs = cs
			self.rtd_nominal = rtd_nominal
			self.ref_resistor = ref_resistor
			self.wires = wires

	fake_ada.MAX31865 = FakeSensor
	monkeypatch.setitem(sys.modules, 'adafruit_max31865', fake_ada)

	import importlib
	import probes.max31865_adafruit as probe

	importlib.reload(probe)  # bind the fake adafruit_max31865

	captured = {}

	def fake_resolve(config, default_cs):
		captured['config'] = config
		captured['default_cs'] = default_cs
		return ('SPI', 'CS')

	monkeypatch.setattr(probe, 'resolve_spi_bus', fake_resolve)

	obj = probe.ReadProbes.__new__(probe.ReadProbes)  # bypass heavy base __init__
	obj.device_info = {
		'config': {'spi_bus_kind': 'mcp2210', 'cs': '5', 'rtd_nominal': '1000', 'ref_resistor': '430', 'wires': '3'}
	}
	obj._init_device()

	assert captured['default_cs'] == 'D6'
	assert obj.device.sensor.spi == 'SPI'
	assert obj.device.sensor.cs == 'CS'
	assert obj.device.sensor.rtd_nominal == 1000
	assert obj.device.sensor.ref_resistor == 430
	assert obj.device.sensor.wires == 3


import json
import os


def test_manifest_max31865_has_spi_bus_fields():
	repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
	manifest = json.load(open(os.path.join(repo_root, 'wizard', 'wizard_manifest.json')))
	entry = manifest['modules']['probes']['max31865_adafruit']

	labels = [item['label'] for item in entry['device_specific']['config']]
	assert 'spi_bus_kind' in labels
	assert 'mcp2210_serial' in labels

	kind = next(i for i in entry['device_specific']['config'] if i['label'] == 'spi_bus_kind')
	assert kind['list_values'] == ['basic', 'mcp2210']
	assert kind['default'] == 'basic'

	cs = next(i for i in entry['device_specific']['config'] if i['label'] == 'cs')
	# GP0-GP8 stored values are appended after the board pins.
	assert all(str(n) in cs['list_values'] for n in range(0, 9))

	deps = ' '.join(entry['py_dependencies'])
	assert 'mcp2210' in deps
	assert 'hid' in deps


def test_manifest_list_defaults_are_valid_values():
	# The wizard stores the list_values entry (the <option value>), so every
	# list-type config field's `default` must be one of its list_values --
	# otherwise nothing is preselected. Guards against the cs `default: "D2"`
	# (a label, not a value) regression.
	repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
	manifest = json.load(open(os.path.join(repo_root, 'wizard', 'wizard_manifest.json')))
	offenders = []
	for name, entry in manifest['modules']['probes'].items():
		for item in entry.get('device_specific', {}).get('config', []):
			if item.get('type') == 'list' and item.get('default') not in item.get('list_values', []):
				offenders.append(f'{name}.{item["label"]} default={item.get("default")!r}')
	assert offenders == [], f'list defaults not in list_values: {offenders}'
