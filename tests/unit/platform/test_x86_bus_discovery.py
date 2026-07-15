from unittest import mock

import pytest


def _build_platform(fan_cfg):
	"""Build a GrillPlatform with the relay/EMC/I2C hardware mocked, so only
	the I2C-bus resolution logic in __init__ is exercised. Returns the platform
	plus the mock needed to assert which (kind, selector) was handed to the
	shared open_i2c_bus factory. Actual bus construction (basic/extended/etc.)
	is common.i2c_bus's job and is covered in tests/unit/i2c/test_i2c_bus.py."""
	import grillplat.x86_numato as mod

	with (
		mock.patch.object(mod, 'NumatoUSBRelay'),
		mock.patch.object(mod, 'EMC2101_LUT'),
		mock.patch.object(mod, 'EMC2301'),
		mock.patch.object(mod, 'open_i2c_bus') as open_bus,
	):
		config = {} if fan_cfg is None else {'fan_controller': fan_cfg}
		platform = mod.GrillPlatform(config)
		return platform, open_bus


def test_basic_bus_is_default_and_uses_integrated_i2c():
	# No emc2101 config at all -> basic (integrated) bus, default selector.
	_, open_bus = _build_platform(None)
	open_bus.assert_called_once_with('basic', 'CP2112')


def test_basic_bus_kind_uses_integrated_i2c():
	_, open_bus = _build_platform({'i2c_bus_kind': 'basic'})
	open_bus.assert_called_once_with('basic', 'CP2112')


def test_extended_bus_with_numeric_bus_used_directly():
	_, open_bus = _build_platform({'i2c_bus_kind': 'extended', 'i2c_bus_num': '3'})
	# A plain number is a /dev/i2c-N index; resolution/discovery now happens
	# inside common.i2c_bus, which is handed the raw selector.
	open_bus.assert_called_once_with('extended', '3')


def test_extended_bus_with_name_match_is_discovered():
	_, open_bus = _build_platform({'i2c_bus_kind': 'extended', 'i2c_bus_num': 'CP2112'})
	# A non-numeric spec is an adapter-name match; resolved by common.i2c_bus.
	open_bus.assert_called_once_with('extended', 'CP2112')


def test_legacy_i2c_bus_match_config_stays_extended():
	# Pre basic/extended installs only had i2c_bus_match (the CP2112 bridge name).
	# They must keep using the bridge, not silently switch to the integrated bus.
	_, open_bus = _build_platform({'i2c_bus_match': 'CP2112'})
	open_bus.assert_called_once_with('extended', 'CP2112')


def _make_bus(tmp_path, index, name):
	bus_dir = tmp_path / f'i2c-{index}'
	bus_dir.mkdir()
	(bus_dir / 'name').write_text(name + '\n')
	return bus_dir


def test_find_i2c_bus_single_match(tmp_path):
	from common.i2c_bus import find_i2c_bus

	_make_bus(tmp_path, 0, 'Synopsys DesignWare I2C adapter')
	_make_bus(tmp_path, 7, 'CP2112 SMBus Bridge on hidraw0')
	assert find_i2c_bus(match='CP2112', devices_path=str(tmp_path)) == 7


def test_find_i2c_bus_case_insensitive(tmp_path):
	from common.i2c_bus import find_i2c_bus

	_make_bus(tmp_path, 3, 'cp2112 smbus bridge')
	assert find_i2c_bus(match='CP2112', devices_path=str(tmp_path)) == 3


def test_find_i2c_bus_no_match_raises(tmp_path):
	from common.i2c_bus import find_i2c_bus

	_make_bus(tmp_path, 0, 'Synopsys DesignWare I2C adapter')
	with pytest.raises(RuntimeError):
		find_i2c_bus(match='CP2112', devices_path=str(tmp_path))


def test_find_i2c_bus_multiple_matches_raises(tmp_path):
	from common.i2c_bus import find_i2c_bus

	_make_bus(tmp_path, 4, 'CP2112 SMBus Bridge on hidraw0')
	_make_bus(tmp_path, 5, 'CP2112 SMBus Bridge on hidraw1')
	with pytest.raises(RuntimeError):
		find_i2c_bus(match='CP2112', devices_path=str(tmp_path))
