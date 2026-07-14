import logging
import os
from unittest import mock

import pytest
from EasyMCP2221.exceptions import LowSCLError, LowSDAError, NotAckError, TimeoutError

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


class _FakeI2CDevice:
	"""Stand-in for an EasyMCP2221.Device -- records every I2C_write/I2C_read
	call, returns a canned read result, and can be told to raise a canned
	exception (simulating NotAckError etc.) instead."""

	def __init__(self, read_result=b'', raise_exc=None):
		self.read_result = read_result
		self.raise_exc = raise_exc
		self.calls = []

	def I2C_write(self, addr, data, kind='regular', timeout_ms=20):
		self.calls.append(('write', addr, bytes(data), kind))
		if self.raise_exc:
			raise self.raise_exc

	def I2C_read(self, addr, size=1, kind='regular', timeout_ms=20):
		self.calls.append(('read', addr, size, kind))
		if self.raise_exc:
			raise self.raise_exc
		return self.read_result


def test_easymcp2221_backend_writeto_nonempty_calls_i2c_write():
	device = _FakeI2CDevice()
	backend = i2c_bus._EasyMCP2221Backend(device)
	backend.writeto(0x40, b'\x01\x02')
	assert device.calls == [('write', 0x40, b'\x01\x02', 'regular')]


def test_easymcp2221_backend_writeto_empty_does_presence_read():
	device = _FakeI2CDevice()
	backend = i2c_bus._EasyMCP2221Backend(device)
	backend.writeto(0x40, b'')
	assert device.calls == [('read', 0x40, 1, 'regular')]


def test_easymcp2221_backend_readfrom_into_fills_buffer():
	device = _FakeI2CDevice(read_result=b'\x0a\x0b\x0c')
	backend = i2c_bus._EasyMCP2221Backend(device)
	buf = bytearray(3)
	backend.readfrom_into(0x40, buf)
	assert bytes(buf) == b'\x0a\x0b\x0c'
	assert device.calls == [('read', 0x40, 3, 'regular')]


def test_easymcp2221_backend_writeto_then_readfrom_uses_nonstop_restart():
	device = _FakeI2CDevice(read_result=b'\xaa\xbb')
	backend = i2c_bus._EasyMCP2221Backend(device)
	out = bytearray(2)
	backend.writeto_then_readfrom(0x40, b'\x00', out)
	assert bytes(out) == b'\xaa\xbb'
	assert device.calls == [('write', 0x40, b'\x00', 'nonstop'), ('read', 0x40, 2, 'restart')]


def test_easymcp2221_backend_scan_collects_acking_addresses():
	device = _FakeI2CDevice()
	backend = i2c_bus._EasyMCP2221Backend(device)
	assert backend.scan() == list(range(0x08, 0x78))


@pytest.mark.parametrize('exc_cls', [NotAckError, TimeoutError, LowSCLError, LowSDAError])
def test_easymcp2221_backend_translates_i2c_errors_to_oserror(exc_cls):
	device = _FakeI2CDevice(raise_exc=exc_cls('boom'))
	backend = i2c_bus._EasyMCP2221Backend(device)
	with pytest.raises(OSError):
		backend.writeto(0x40, b'\x01')
	with pytest.raises(OSError):
		backend.readfrom_into(0x40, bytearray(1))


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


def _make_usb_i2c_adapter(root, usb_name, serial, bus_num, adapter_name, devices_dir):
	usb_dev = root / usb_name
	usb_dev.mkdir(parents=True)
	(usb_dev / 'serial').write_text(serial)
	(usb_dev / 'idVendor').write_text('04d8')
	iface = usb_dev / f'{usb_name}:1.0'
	iface.mkdir()
	bus_dir = iface / f'i2c-{bus_num}'
	bus_dir.mkdir()
	(bus_dir / 'name').write_text(adapter_name)
	(devices_dir / f'i2c-{bus_num}').symlink_to(bus_dir)


def test_find_i2c_bus_by_serial_matches(tmp_path):
	devices_dir = tmp_path / 'devices_path'
	devices_dir.mkdir()
	_make_usb_i2c_adapter(tmp_path, 'usb1', 'AB12', 7, 'MCP2221 usb-i2c bridge', devices_dir)

	assert i2c_bus.find_i2c_bus_by_serial('AB12', devices_path=str(devices_dir)) == 7


def test_find_i2c_bus_by_serial_no_match_raises(tmp_path):
	devices_dir = tmp_path / 'devices_path'
	devices_dir.mkdir()
	_make_usb_i2c_adapter(tmp_path, 'usb1', 'AB12', 7, 'MCP2221 usb-i2c bridge', devices_dir)

	with pytest.raises(RuntimeError, match='No i2c adapter found with serial'):
		i2c_bus.find_i2c_bus_by_serial('DEADBEEF', devices_path=str(devices_dir))


def test_find_i2c_bus_by_serial_ambiguous_raises(tmp_path):
	devices_dir = tmp_path / 'devices_path'
	devices_dir.mkdir()
	_make_usb_i2c_adapter(tmp_path, 'usb1', 'AB12', 1, 'MCP2221 usb-i2c bridge', devices_dir)
	_make_usb_i2c_adapter(tmp_path, 'usb2', 'AB12', 2, 'MCP2221 usb-i2c bridge', devices_dir)

	with pytest.raises(RuntimeError, match='Multiple i2c adapters have serial'):
		i2c_bus.find_i2c_bus_by_serial('AB12', devices_path=str(devices_dir))


def test_find_i2c_bus_by_serial_is_exact_not_substring(tmp_path):
	devices_dir = tmp_path / 'devices_path'
	devices_dir.mkdir()
	_make_usb_i2c_adapter(tmp_path, 'usb1', 'AB1234', 7, 'MCP2221 usb-i2c bridge', devices_dir)

	with pytest.raises(RuntimeError, match='No i2c adapter found with serial'):
		i2c_bus.find_i2c_bus_by_serial('AB12', devices_path=str(devices_dir))


def test_resolve_i2c_bus_serial_prefix_dispatches(monkeypatch):
	monkeypatch.setattr(i2c_bus, 'find_i2c_bus_by_serial', lambda serial: 42 if serial == 'AB12' else None)
	assert resolve_i2c_bus('serial:AB12') == 42
	assert resolve_i2c_bus('SERIAL:AB12') == 42  # prefix keyword is case-insensitive


def test_discover_extended_i2c_buses_wraps_enumeration(tmp_path):
	usb_device = tmp_path / 'devices' / 'usb1' / '1-1'
	usb_device.mkdir(parents=True)
	(usb_device / 'serial').write_text('AB12')
	(usb_device / 'idVendor').write_text('04d8')
	iface = usb_device / '1-1:1.0'
	iface.mkdir()
	bus_dir = iface / 'i2c-7'
	bus_dir.mkdir()
	(bus_dir / 'name').write_text('MCP2221 usb-i2c bridge')

	assert i2c_bus.discover_extended_i2c_buses(devices_path=str(iface)) == [
		{'bus_num': 7, 'name': 'MCP2221 usb-i2c bridge', 'serial': 'AB12'}
	]


def test_discover_extended_i2c_buses_empty_when_missing_path():
	assert i2c_bus.discover_extended_i2c_buses(devices_path='/no/such/path') == []


def test_discover_mcp2221_devices_lists_serials():
	hid_mod = types_module_with(
		enumerate=lambda vid, pid: [
			{'serial_number': 'AAAA', 'path': b'/dev/hidraw0'},
			{'serial_number': 'BBBB', 'path': b'/dev/hidraw1'},
		]
	)
	with mock.patch.dict('sys.modules', {'hid': hid_mod}):
		devices = i2c_bus.discover_mcp2221_devices()
	assert devices == [{'serial': 'AAAA', 'path': b'/dev/hidraw0'}, {'serial': 'BBBB', 'path': b'/dev/hidraw1'}]


def test_discover_mcp2221_devices_empty_without_hid_module():
	with mock.patch.dict('sys.modules', {'hid': None}):
		assert i2c_bus.discover_mcp2221_devices() == []


def test_discover_ft232h_devices_lists_urls():
	descriptor = types_module_with(sn='FT9', description='Single RS232-HS')

	class FakeFtdi:
		@staticmethod
		def list_devices(url):
			return [(descriptor, 1)]

	fake_mod = types_module_with(Ftdi=FakeFtdi)
	with mock.patch.dict('sys.modules', {'pyftdi.ftdi': fake_mod}):
		devices = i2c_bus.discover_ft232h_devices()
	assert devices == [{'url': 'ftdi://ftdi:232h:FT9/1', 'serial': 'FT9', 'description': 'Single RS232-HS'}]


def test_discover_ft232h_devices_empty_without_pyftdi():
	with mock.patch.dict('sys.modules', {'pyftdi.ftdi': None}):
		assert i2c_bus.discover_ft232h_devices() == []
