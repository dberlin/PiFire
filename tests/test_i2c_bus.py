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
