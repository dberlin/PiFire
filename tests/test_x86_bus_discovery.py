from unittest import mock

import pytest


def _build_platform(emc2101_cfg):
	"""Build a GrillPlatform with the relay/EMC2101/I2C hardware mocked, so only
	the I2C-bus resolution logic in __init__ is exercised. Returns the platform
	plus the mocks needed to assert which bus was opened."""
	import grillplat.x86_numato as mod

	with (
		mock.patch.object(mod, 'NumatoUSBRelay'),
		mock.patch.object(mod, 'EMC2101'),
		mock.patch.object(mod, 'ExtendedI2C') as extended_i2c,
		mock.patch.object(mod, 'busio') as busio,
		mock.patch.object(mod, 'board') as board,
		mock.patch.object(mod, 'find_i2c_bus', return_value=7) as find_bus,
	):
		config = {} if emc2101_cfg is None else {'emc2101': emc2101_cfg}
		platform = mod.GrillPlatform(config)
		return platform, extended_i2c, busio, board, find_bus


def test_basic_bus_is_default_and_uses_integrated_i2c():
	# No emc2101 config at all -> the integrated I2C bus (board.SCL/SDA), no
	# adapter-name discovery and no extended bus.
	_, extended_i2c, busio, board, find_bus = _build_platform(None)
	busio.I2C.assert_called_once_with(board.SCL, board.SDA)
	extended_i2c.assert_not_called()
	find_bus.assert_not_called()


def test_basic_bus_kind_uses_integrated_i2c():
	_, extended_i2c, busio, board, find_bus = _build_platform({'i2c_bus_kind': 'basic'})
	busio.I2C.assert_called_once_with(board.SCL, board.SDA)
	extended_i2c.assert_not_called()
	find_bus.assert_not_called()


def test_extended_bus_with_numeric_bus_used_directly():
	_, extended_i2c, busio, board, find_bus = _build_platform({'i2c_bus_kind': 'extended', 'i2c_bus_num': '3'})
	# A plain number is a /dev/i2c-N index, used directly without discovery.
	extended_i2c.assert_called_once_with(3)
	find_bus.assert_not_called()
	busio.I2C.assert_not_called()


def test_extended_bus_with_name_match_is_discovered():
	_, extended_i2c, busio, board, find_bus = _build_platform({'i2c_bus_kind': 'extended', 'i2c_bus_num': 'CP2112'})
	# A non-numeric spec is an adapter-name match resolved via find_i2c_bus.
	find_bus.assert_called_once_with('CP2112')
	extended_i2c.assert_called_once_with(7)
	busio.I2C.assert_not_called()


def test_legacy_i2c_bus_match_config_stays_extended():
	# Pre basic/extended installs only had i2c_bus_match (the CP2112 bridge name).
	# They must keep using the bridge, not silently switch to the integrated bus.
	_, extended_i2c, busio, board, find_bus = _build_platform({'i2c_bus_match': 'CP2112'})
	find_bus.assert_called_once_with('CP2112')
	extended_i2c.assert_called_once_with(7)
	busio.I2C.assert_not_called()


def _make_bus(tmp_path, index, name):
	bus_dir = tmp_path / f'i2c-{index}'
	bus_dir.mkdir()
	(bus_dir / 'name').write_text(name + '\n')
	return bus_dir


def test_find_i2c_bus_single_match(tmp_path):
	from grillplat.x86_numato import find_i2c_bus

	_make_bus(tmp_path, 0, 'Synopsys DesignWare I2C adapter')
	_make_bus(tmp_path, 7, 'CP2112 SMBus Bridge on hidraw0')
	assert find_i2c_bus(match='CP2112', devices_path=str(tmp_path)) == 7


def test_find_i2c_bus_case_insensitive(tmp_path):
	from grillplat.x86_numato import find_i2c_bus

	_make_bus(tmp_path, 3, 'cp2112 smbus bridge')
	assert find_i2c_bus(match='CP2112', devices_path=str(tmp_path)) == 3


def test_find_i2c_bus_no_match_raises(tmp_path):
	from grillplat.x86_numato import find_i2c_bus

	_make_bus(tmp_path, 0, 'Synopsys DesignWare I2C adapter')
	with pytest.raises(RuntimeError):
		find_i2c_bus(match='CP2112', devices_path=str(tmp_path))


def test_find_i2c_bus_multiple_matches_raises(tmp_path):
	from grillplat.x86_numato import find_i2c_bus

	_make_bus(tmp_path, 4, 'CP2112 SMBus Bridge on hidraw0')
	_make_bus(tmp_path, 5, 'CP2112 SMBus Bridge on hidraw1')
	with pytest.raises(RuntimeError):
		find_i2c_bus(match='CP2112', devices_path=str(tmp_path))
