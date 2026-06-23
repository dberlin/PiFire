import pytest


def _make_bus(tmp_path, index, name):
	bus_dir = tmp_path / f"i2c-{index}"
	bus_dir.mkdir()
	(bus_dir / "name").write_text(name + "\n")
	return bus_dir


def test_find_i2c_bus_single_match(tmp_path):
	from grillplat.x86_numato_emc2101 import find_i2c_bus
	_make_bus(tmp_path, 0, "Synopsys DesignWare I2C adapter")
	_make_bus(tmp_path, 7, "CP2112 SMBus Bridge on hidraw0")
	assert find_i2c_bus(match="CP2112", devices_path=str(tmp_path)) == 7


def test_find_i2c_bus_case_insensitive(tmp_path):
	from grillplat.x86_numato_emc2101 import find_i2c_bus
	_make_bus(tmp_path, 3, "cp2112 smbus bridge")
	assert find_i2c_bus(match="CP2112", devices_path=str(tmp_path)) == 3


def test_find_i2c_bus_no_match_raises(tmp_path):
	from grillplat.x86_numato_emc2101 import find_i2c_bus
	_make_bus(tmp_path, 0, "Synopsys DesignWare I2C adapter")
	with pytest.raises(RuntimeError):
		find_i2c_bus(match="CP2112", devices_path=str(tmp_path))


def test_find_i2c_bus_multiple_matches_raises(tmp_path):
	from grillplat.x86_numato_emc2101 import find_i2c_bus
	_make_bus(tmp_path, 4, "CP2112 SMBus Bridge on hidraw0")
	_make_bus(tmp_path, 5, "CP2112 SMBus Bridge on hidraw1")
	with pytest.raises(RuntimeError):
		find_i2c_bus(match="CP2112", devices_path=str(tmp_path))
