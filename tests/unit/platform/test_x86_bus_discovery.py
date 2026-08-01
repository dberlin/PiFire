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
        mock.patch.object(mod, "NumatoUSBRelay"),
        mock.patch.object(mod, "EMC2101_LUT"),
        mock.patch.object(mod, "EMC2301"),
        mock.patch.object(mod, "open_i2c_bus") as open_bus,
    ):
        config = {} if fan_cfg is None else {"fan_controller": fan_cfg}
        platform = mod.GrillPlatform(config)
        return platform, open_bus


def test_basic_bus_is_default_and_uses_integrated_i2c():
    # No fan_controller config at all -> basic (integrated) bus.
    from common.i2c_bus_config import BasicBus

    _, open_bus = _build_platform(None)
    open_bus.assert_called_once_with(BasicBus())


def test_basic_bus_kind_uses_integrated_i2c():
    from common.i2c_bus_config import BasicBus

    _, open_bus = _build_platform({"i2c_bus": {"kind": "basic"}})
    open_bus.assert_called_once_with(BasicBus())


def test_kernel_bus_by_number_is_used_directly():
    from common.i2c_bus_config import KernelBusNumber

    _, open_bus = _build_platform({"i2c_bus": {"kind": "kernel", "bus_num": 3}})
    open_bus.assert_called_once_with(KernelBusNumber(bus_num=3))


def test_fan_controller_opens_the_configured_bus():
    from common.i2c_bus_config import KernelAdapterName

    _, open_bus = _build_platform({"i2c_bus": {"kind": "kernel", "adapter": "CP2112"}})
    open_bus.assert_called_once_with(KernelAdapterName(adapter="CP2112"))


def test_fan_controller_defaults_to_the_integrated_bus():
    from common.i2c_bus_config import BasicBus

    _, open_bus = _build_platform({})
    open_bus.assert_called_once_with(BasicBus())


def _make_bus(tmp_path, index, name):
    bus_dir = tmp_path / f"i2c-{index}"
    bus_dir.mkdir()
    (bus_dir / "name").write_text(name + "\n")
    return bus_dir


def test_find_i2c_bus_single_match(tmp_path):
    from common.i2c_bus import find_i2c_bus

    _make_bus(tmp_path, 0, "Synopsys DesignWare I2C adapter")
    _make_bus(tmp_path, 7, "CP2112 SMBus Bridge on hidraw0")
    assert find_i2c_bus(match="CP2112", devices_path=str(tmp_path)) == 7


def test_find_i2c_bus_case_insensitive(tmp_path):
    from common.i2c_bus import find_i2c_bus

    _make_bus(tmp_path, 3, "cp2112 smbus bridge")
    assert find_i2c_bus(match="CP2112", devices_path=str(tmp_path)) == 3


def test_find_i2c_bus_no_match_raises(tmp_path):
    from common.i2c_bus import find_i2c_bus

    _make_bus(tmp_path, 0, "Synopsys DesignWare I2C adapter")
    with pytest.raises(RuntimeError):
        find_i2c_bus(match="CP2112", devices_path=str(tmp_path))


def test_find_i2c_bus_multiple_matches_raises(tmp_path):
    from common.i2c_bus import find_i2c_bus

    _make_bus(tmp_path, 4, "CP2112 SMBus Bridge on hidraw0")
    _make_bus(tmp_path, 5, "CP2112 SMBus Bridge on hidraw1")
    with pytest.raises(RuntimeError):
        find_i2c_bus(match="CP2112", devices_path=str(tmp_path))
