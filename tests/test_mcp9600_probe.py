import json
import os
import sys
import types
import importlib

import pytest


def _install_fakes(monkeypatch):
	"""Install fake hardware modules so the probe imports without hardware."""
	# adafruit_mcp9600 with an MCP9600 that captures its constructor args
	mcp_mod = types.ModuleType('adafruit_mcp9600')

	class FakeMCP9600:
		def __init__(self, i2c, address=0x67, tctype='K'):
			self.i2c = i2c
			self.address = address
			self.tctype = tctype
			self.temperature = 0.0

	mcp_mod.MCP9600 = FakeMCP9600
	monkeypatch.setitem(sys.modules, 'adafruit_mcp9600', mcp_mod)

	# board / busio
	board_mod = types.ModuleType('board')
	board_mod.SCL = 'SCL'
	board_mod.SDA = 'SDA'
	monkeypatch.setitem(sys.modules, 'board', board_mod)

	busio_mod = types.ModuleType('busio')
	busio_mod.I2C = lambda scl, sda: ('I2C', scl, sda)
	monkeypatch.setitem(sys.modules, 'busio', busio_mod)

	# adafruit_extended_bus.ExtendedI2C
	ext_mod = types.ModuleType('adafruit_extended_bus')
	ext_mod.ExtendedI2C = lambda bus: ('ExtI2C', bus)
	monkeypatch.setitem(sys.modules, 'adafruit_extended_bus', ext_mod)

	# adafruit_bus_device.i2c_device.I2CDevice
	busdev_pkg = types.ModuleType('adafruit_bus_device')
	i2cdev_mod = types.ModuleType('adafruit_bus_device.i2c_device')
	i2cdev_mod.I2CDevice = object
	busdev_pkg.i2c_device = i2cdev_mod
	monkeypatch.setitem(sys.modules, 'adafruit_bus_device', busdev_pkg)
	monkeypatch.setitem(sys.modules, 'adafruit_bus_device.i2c_device', i2cdev_mod)

	return mcp_mod


def _load_probe(monkeypatch):
	_install_fakes(monkeypatch)
	import probes.mcp9600_adafruit as probe

	importlib.reload(probe)  # bind the fake adafruit_mcp9600
	return probe


def test_init_device_wires_tc_type(monkeypatch):
	probe = _load_probe(monkeypatch)

	obj = probe.ReadProbes.__new__(probe.ReadProbes)  # bypass heavy base __init__
	obj.device_info = {'config': {'i2c_bus_addr': '0x66', 'tc_type': 'J'}}
	obj._init_device()

	assert obj.device_info['ports'] == ['KTT0']
	sensor = obj.device.sensor
	assert sensor.tctype == 'J'  # configured type passed through
	assert sensor.address == 0x66  # parsed from hex string


def test_init_device_defaults(monkeypatch):
	probe = _load_probe(monkeypatch)

	obj = probe.ReadProbes.__new__(probe.ReadProbes)
	obj.device_info = {'config': {}}  # no keys -> all defaults
	obj._init_device()

	sensor = obj.device.sensor
	assert sensor.tctype == 'K'  # default K
	assert sensor.address == 0x67  # default address


def test_manifest_mcp9600_entry():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    manifest = json.load(open(os.path.join(repo_root, 'wizard', 'wizard_manifest.json')))
    probes = manifest['modules']['probes']
    assert 'mcp9600_adafruit' in probes
    entry = probes['mcp9600_adafruit']

    ds = entry['device_specific']
    assert ds['type'] == 'thermocouple'
    assert ds['ports'] == ['KTT0']

    labels = [item['label'] for item in ds['config']]
    assert 'tc_type' in labels

    tc = next(i for i in ds['config'] if i['label'] == 'tc_type')
    assert tc['list_values'] == ['B', 'E', 'J', 'K', 'N', 'R', 'S', 'T']
    assert tc['default'] == 'K'
