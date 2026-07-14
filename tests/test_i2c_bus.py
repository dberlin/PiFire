import logging
import os
from unittest import mock

import pytest

import common.i2c_bus as i2c_bus
from common.i2c_bus import I2CBusConfigError, assert_clean_blinka_env, resolve_i2c_bus, validate_bus_kinds


def test_resolve_i2c_bus_numeric_returns_int():
	assert resolve_i2c_bus('3') == 3
	assert resolve_i2c_bus(3) == 3


def test_validate_bus_kinds_allows_workable_combos():
	# None of these raise.
	validate_bus_kinds({'ft232h', 'mcp2221'})
	validate_bus_kinds({'ft232h', 'extended'})
	validate_bus_kinds({'mcp2221', 'extended'})
	validate_bus_kinds({'basic', 'extended'})
	validate_bus_kinds({'ft232h', 'mcp2221', 'extended'})
	validate_bus_kinds({'', None, 'basic'})  # blanks ignored


def test_validate_bus_kinds_rejects_basic_plus_usb():
	with pytest.raises(I2CBusConfigError):
		validate_bus_kinds({'basic', 'ft232h'})
	with pytest.raises(I2CBusConfigError):
		validate_bus_kinds({'basic', 'mcp2221'})


def test_assert_clean_blinka_env_rejects_board_forcing_vars():
	for var in ('BLINKA_FT232H', 'BLINKA_MCP2221', 'BLINKA_FORCEBOARD', 'BLINKA_FTX232H_0'):
		with pytest.raises(I2CBusConfigError):
			assert_clean_blinka_env({var: '1'})


def test_assert_clean_blinka_env_allows_tuning_and_empty():
	assert_clean_blinka_env({})
	assert_clean_blinka_env({'BLINKA_MCP2221_HID_DELAY': '0.1', 'BLINKA_MCP2221_RESET_DELAY': '0.5'})
	assert_clean_blinka_env({'PATH': '/usr/bin'})


@pytest.fixture(autouse=True)
def _clean_bus_state():
	i2c_bus.reset_bus_state()
	yield
	i2c_bus.reset_bus_state()


def test_locked_i2c_lock_and_delegate():
	backend = mock.Mock()
	wrapped = i2c_bus._LockedI2C(backend)
	assert wrapped.try_lock() is True
	wrapped.unlock()
	wrapped.unlock()  # double unlock is safe
	wrapped.writeto(0x10, b'\x01')
	backend.writeto.assert_called_once_with(0x10, b'\x01')
	wrapped.scan()
	backend.scan.assert_called_once()


def test_open_ft232h_sets_env_transiently_and_restores(monkeypatch):
	monkeypatch.delenv('BLINKA_FT232H', raising=False)
	created = []

	class FakeBackendI2C:
		def __init__(self):
			created.append(os.environ.get('BLINKA_FT232H'))

	fake_mod = types_module_with(I2C=FakeBackendI2C)
	with mock.patch.dict('sys.modules', {'adafruit_blinka.microcontroller.ftdi_mpsse.mpsse.i2c': fake_mod}):
		bus = i2c_bus.open_i2c_bus('ft232h', 'ftdi://ftdi:232h:FT9/1')
	assert isinstance(bus, i2c_bus._LockedI2C)
	# Env was set to the selector during construction, restored (unset) after.
	assert created == ['ftdi://ftdi:232h:FT9/1']
	assert 'BLINKA_FT232H' not in os.environ


def test_open_i2c_bus_caches_per_kind_and_selector():
	class FakeBackendI2C:
		def __init__(self):
			pass

	fake_mod = types_module_with(I2C=FakeBackendI2C)
	with mock.patch.dict('sys.modules', {'adafruit_blinka.microcontroller.ftdi_mpsse.mpsse.i2c': fake_mod}):
		a = i2c_bus.open_i2c_bus('ft232h', '')
		b = i2c_bus.open_i2c_bus('ft232h', '1')  # '' and '1' are the same adapter
		c = i2c_bus.open_i2c_bus('ft232h', '')
	assert a is b is c


def test_open_i2c_bus_runtime_rejects_basic_after_ft232h():
	class FakeBackendI2C:
		def __init__(self):
			pass

	fake_mod = types_module_with(I2C=FakeBackendI2C)
	with mock.patch.dict('sys.modules', {'adafruit_blinka.microcontroller.ftdi_mpsse.mpsse.i2c': fake_mod}):
		i2c_bus.open_i2c_bus('ft232h', '')
		with pytest.raises(i2c_bus.I2CBusConfigError):
			i2c_bus.open_i2c_bus('basic')


def types_module_with(**attrs):
	import types

	mod = types.ModuleType('fake')
	for name, value in attrs.items():
		setattr(mod, name, value)
	return mod


def _fake_mcp2221_modules(enumerate_result):
	"""Build fake adafruit_blinka mcp2221 backend modules + a fake hid module.

	Returns (modules_dict_for_sys_modules, handle_mock, i2c_ctor_calls)."""
	import types

	handle = mock.Mock()  # stands in for mcp2221._hid (the open HID handle)

	singleton = types.SimpleNamespace(_hid=handle)

	class _MCP2221:
		VID = 0x04D8
		PID = 0x00DD

	mcp2221_mod = types.ModuleType('adafruit_blinka.microcontroller.mcp2221.mcp2221')
	mcp2221_mod.mcp2221 = singleton
	mcp2221_mod.MCP2221 = _MCP2221

	pkg = types.ModuleType('adafruit_blinka.microcontroller.mcp2221')
	pkg.mcp2221 = singleton  # so `from ...mcp2221 import mcp2221 as _mcp_mod` yields the module below
	# NOTE: `from adafruit_blinka.microcontroller.mcp2221 import mcp2221` resolves the
	# submodule `mcp2221` -> must be the module object, not the singleton:
	pkg.mcp2221 = mcp2221_mod

	i2c_ctor_calls = []

	class _I2C:
		def __init__(self):
			i2c_ctor_calls.append(True)

	i2c_mod = types.ModuleType('adafruit_blinka.microcontroller.mcp2221.i2c')
	i2c_mod.I2C = _I2C

	hid_mod = types.ModuleType('hid')
	hid_mod.enumerate = lambda vid, pid: enumerate_result

	modules = {
		'adafruit_blinka.microcontroller.mcp2221': pkg,
		'adafruit_blinka.microcontroller.mcp2221.mcp2221': mcp2221_mod,
		'adafruit_blinka.microcontroller.mcp2221.i2c': i2c_mod,
		'hid': hid_mod,
	}
	return modules, handle, i2c_ctor_calls


def test_open_mcp2221_no_selector_constructs_backend():
	modules, handle, ctor = _fake_mcp2221_modules(enumerate_result=[])
	with mock.patch.dict('sys.modules', modules):
		bus = i2c_bus.open_i2c_bus('mcp2221', '')
	assert isinstance(bus, i2c_bus._LockedI2C)
	assert ctor == [True]
	handle.open_path.assert_not_called()  # no selector -> first device, no reopen


def test_open_mcp2221_selector_opens_matching_serial():
	enumerate_result = [
		{'serial_number': 'AAAA', 'path': b'/dev/hidraw0'},
		{'serial_number': 'BBBB', 'path': b'/dev/hidraw1'},
	]
	modules, handle, ctor = _fake_mcp2221_modules(enumerate_result)
	with mock.patch.dict('sys.modules', modules):
		bus = i2c_bus.open_i2c_bus('mcp2221', 'BBBB')
	assert isinstance(bus, i2c_bus._LockedI2C)
	handle.close.assert_called_once()
	handle.open_path.assert_called_once_with(b'/dev/hidraw1')


def test_open_mcp2221_selector_not_found_raises():
	modules, handle, ctor = _fake_mcp2221_modules(enumerate_result=[{'serial_number': 'AAAA', 'path': b'/dev/hidraw0'}])
	with mock.patch.dict('sys.modules', modules):
		with pytest.raises(i2c_bus.I2CBusConfigError):
			i2c_bus.open_i2c_bus('mcp2221', 'ZZZZ')


def test_probes_base_reexports_bus_helpers():
	import common.i2c_bus as cib
	import probes.base as base

	assert base.resolve_i2c_bus is cib.resolve_i2c_bus
	assert base.find_i2c_bus is cib.find_i2c_bus


def test_find_i2c_bus_debug_logs_match_and_result(tmp_path, caplog):
	bus = tmp_path / 'i2c-5'
	bus.mkdir()
	(bus / 'name').write_text('CP2112 SMBus Bridge\n')

	with caplog.at_level(logging.DEBUG, logger='control'):
		assert i2c_bus.find_i2c_bus('CP2112', devices_path=str(tmp_path)) == 5

	messages = [record.getMessage() for record in caplog.records]
	assert any('CP2112' in m for m in messages)
	assert any('i2c-5' in m for m in messages)


def test_open_i2c_bus_debug_logs_kind_and_selector(caplog):
	fake_mod = types_module_with(I2C=type('FakeBackendI2C', (), {'__init__': lambda self: None}))
	with mock.patch.dict('sys.modules', {'adafruit_blinka.microcontroller.ftdi_mpsse.mpsse.i2c': fake_mod}):
		with caplog.at_level(logging.DEBUG, logger='control'):
			i2c_bus.open_i2c_bus('ft232h', 'ftdi://ftdi:232h:FT9/1')

	text = caplog.text
	assert 'ft232h' in text  # the kind being opened
	assert 'ftdi://ftdi:232h:FT9/1' in text  # the exact selector/URL


def test_read_usb_serial_resolves_via_sysfs_walk(tmp_path):
	usb_device = tmp_path / 'devices' / 'usb1' / '1-1'
	usb_device.mkdir(parents=True)
	(usb_device / 'serial').write_text('AB12\n')
	(usb_device / 'idVendor').write_text('04d8\n')
	iface = usb_device / '1-1:1.0'
	iface.mkdir()
	bus_dir = iface / 'i2c-7'
	bus_dir.mkdir()
	(bus_dir / 'name').write_text('MCP2221 usb-i2c bridge\n')

	assert i2c_bus._read_usb_serial(str(bus_dir)) == 'AB12'


def test_read_usb_serial_returns_none_without_usb_ancestor(tmp_path):
	bus_dir = tmp_path / 'i2c-1'
	bus_dir.mkdir()
	(bus_dir / 'name').write_text('bcm2835 I2C adapter\n')

	assert i2c_bus._read_usb_serial(str(bus_dir)) is None


def test_read_usb_serial_ignores_serial_file_without_idvendor(tmp_path):
	# A directory with a 'serial' file but no 'idVendor' isn't a USB device
	# level (e.g. a power_supply sysfs node) -- must not be mistaken for one.
	not_usb = tmp_path / 'not_a_usb_device'
	not_usb.mkdir()
	(not_usb / 'serial').write_text('DECOY\n')
	bus_dir = not_usb / 'i2c-2'
	bus_dir.mkdir()
	(bus_dir / 'name').write_text('some adapter\n')

	assert i2c_bus._read_usb_serial(str(bus_dir)) is None


def test_enumerate_i2c_adapters_includes_serial(tmp_path):
	usb_device = tmp_path / 'devices' / 'usb1' / '1-1'
	usb_device.mkdir(parents=True)
	(usb_device / 'serial').write_text('AB12')
	(usb_device / 'idVendor').write_text('04d8')
	devices_dir = usb_device / '1-1:1.0'
	devices_dir.mkdir()
	bus_dir = devices_dir / 'i2c-7'
	bus_dir.mkdir()
	(bus_dir / 'name').write_text('MCP2221 usb-i2c bridge')

	adapters = i2c_bus._enumerate_i2c_adapters(devices_path=str(devices_dir))
	assert adapters == [{'bus_num': 7, 'name': 'MCP2221 usb-i2c bridge', 'serial': 'AB12'}]
