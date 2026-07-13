import pytest

from common.i2c_bus import I2CBusConfigError, assert_clean_blinka_env, resolve_i2c_bus, validate_bus_kinds


def test_resolve_i2c_bus_numeric_returns_int():
	assert resolve_i2c_bus('3') == 3
	assert resolve_i2c_bus(3) == 3


def test_validate_bus_kinds_allows_workable_combos():
	# None of these raise.
	validate_bus_kinds({'ft232h', 'mcp2221a'})
	validate_bus_kinds({'ft232h', 'extended'})
	validate_bus_kinds({'mcp2221a', 'extended'})
	validate_bus_kinds({'basic', 'extended'})
	validate_bus_kinds({'ft232h', 'mcp2221a', 'extended'})
	validate_bus_kinds({'', None, 'basic'})  # blanks ignored


def test_validate_bus_kinds_rejects_basic_plus_usb():
	with pytest.raises(I2CBusConfigError):
		validate_bus_kinds({'basic', 'ft232h'})
	with pytest.raises(I2CBusConfigError):
		validate_bus_kinds({'basic', 'mcp2221a'})


def test_assert_clean_blinka_env_rejects_board_forcing_vars():
	for var in ('BLINKA_FT232H', 'BLINKA_MCP2221', 'BLINKA_FORCEBOARD', 'BLINKA_FTX232H_0'):
		with pytest.raises(I2CBusConfigError):
			assert_clean_blinka_env({var: '1'})


def test_assert_clean_blinka_env_allows_tuning_and_empty():
	assert_clean_blinka_env({})
	assert_clean_blinka_env({'BLINKA_MCP2221_HID_DELAY': '0.1', 'BLINKA_MCP2221_RESET_DELAY': '0.5'})
	assert_clean_blinka_env({'PATH': '/usr/bin'})


import os
from unittest import mock

import common.i2c_bus as i2c_bus


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
