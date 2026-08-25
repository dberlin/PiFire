import pytest

from common.i2c_bus_config import (
    BasicBus,
    FT232HBus,
    I2CBusConfigError,
    KernelAdapterName,
    KernelBusNumber,
    KernelSerialMatch,
    MCP2221Bus,
    parse_i2c_bus,
)

ROUND_TRIP = [
    ({"kind": "basic"}, BasicBus()),
    ({"kind": "kernel", "bus_num": 3}, KernelBusNumber(bus_num=3)),
    ({"kind": "kernel", "adapter": "CP2112"}, KernelAdapterName(adapter="CP2112")),
    ({"kind": "kernel", "serial": "0012AB34"}, KernelSerialMatch(serial="0012AB34")),
    ({"kind": "ft232h", "url": "ftdi://ftdi:232h:FT9ABC/1"}, FT232HBus(url="ftdi://ftdi:232h:FT9ABC/1")),
    ({"kind": "ft232h", "url": ""}, FT232HBus()),
    ({"kind": "mcp2221", "serial": "01234567"}, MCP2221Bus(serial="01234567")),
    ({"kind": "mcp2221", "serial": ""}, MCP2221Bus()),
]


@pytest.mark.parametrize("config,bus", ROUND_TRIP)
def test_parse_produces_the_bus_the_config_names(config, bus):
    assert parse_i2c_bus(config) == bus


@pytest.mark.parametrize("config,bus", ROUND_TRIP)
def test_to_config_round_trips(config, bus):
    assert bus.to_config() == config
    assert parse_i2c_bus(bus.to_config()) == bus


def test_parse_passes_an_already_parsed_bus_through():
    bus = KernelBusNumber(bus_num=1)
    assert parse_i2c_bus(bus) is bus


def test_every_kind_reports_its_own_kind_string():
    assert BasicBus().kind == "basic"
    assert KernelBusNumber(bus_num=1).kind == "kernel"
    assert KernelAdapterName(adapter="CP2112").kind == "kernel"
    assert KernelSerialMatch(serial="AB").kind == "kernel"
    assert FT232HBus().kind == "ft232h"
    assert MCP2221Bus().kind == "mcp2221"


def test_buses_are_hashable_so_they_can_key_the_bus_cache():
    cache = {BasicBus(): "a", KernelBusNumber(bus_num=3): "b"}
    assert cache[BasicBus()] == "a"
    assert cache[KernelBusNumber(bus_num=3)] == "b"


def test_ft232h_blank_and_one_are_the_same_device():
    """'' and '1' both mean the first FT232H, so they must be equal -- an
    unequal pair opens one physical adapter twice."""
    assert FT232HBus(url="1") == FT232HBus(url="")
    assert hash(FT232HBus(url="1")) == hash(FT232HBus(url=""))


def test_kernel_bus_num_accepts_a_numeric_string():
    assert parse_i2c_bus({"kind": "kernel", "bus_num": "3"}) == KernelBusNumber(bus_num=3)


def test_kernel_needs_exactly_one_selector():
    with pytest.raises(I2CBusConfigError, match="exactly one"):
        parse_i2c_bus({"kind": "kernel"})
    with pytest.raises(I2CBusConfigError, match="exactly one"):
        parse_i2c_bus({"kind": "kernel", "bus_num": 3, "adapter": "CP2112"})


def test_kernel_rejects_a_blank_selector():
    with pytest.raises(I2CBusConfigError):
        parse_i2c_bus({"kind": "kernel", "adapter": "   "})


def test_kernel_rejects_a_non_numeric_bus_num():
    with pytest.raises(I2CBusConfigError, match="number"):
        parse_i2c_bus({"kind": "kernel", "bus_num": "CP2112"})


def test_kernel_rejects_a_negative_bus_num():
    """No /dev/i2c-N exists for a negative N; the wizard's i2cBusError rejects
    it too, so a settings import or restored backup must match that rule
    rather than accepting a bus_num the wizard would never have let through."""
    with pytest.raises(I2CBusConfigError, match="zero or greater"):
        parse_i2c_bus({"kind": "kernel", "bus_num": -1})


def test_a_kind_rejects_a_field_belonging_to_another_kind():
    """The whole point of the hierarchy: an ft232h bus has nowhere to keep a
    kernel adapter name, so a stale one cannot survive a change of kind."""
    with pytest.raises(I2CBusConfigError, match="adapter"):
        parse_i2c_bus({"kind": "ft232h", "adapter": "CP2112"})
    with pytest.raises(I2CBusConfigError, match="bus_num"):
        parse_i2c_bus({"kind": "mcp2221", "bus_num": 3})
    with pytest.raises(I2CBusConfigError):
        parse_i2c_bus({"kind": "basic", "bus_num": 3})


def test_parse_rejects_an_unknown_kind():
    with pytest.raises(I2CBusConfigError, match="extended"):
        parse_i2c_bus({"kind": "extended", "bus_num": 3})


def test_parse_rejects_a_non_mapping():
    with pytest.raises(I2CBusConfigError):
        parse_i2c_bus("CP2112")


def test_describe_names_the_hardware():
    assert "i2c-3" in KernelBusNumber(bus_num=3).describe()
    assert "CP2112" in KernelAdapterName(adapter="CP2112").describe()
    assert "first" in FT232HBus().describe()


def test_kernel_bus_number_resolves_to_itself():
    assert KernelBusNumber(bus_num=7).resolve_bus_num() == 7


def test_kernel_adapter_name_resolves_via_find_i2c_bus(monkeypatch):
    from common import i2c_bus

    monkeypatch.setattr(i2c_bus, "find_i2c_bus", lambda match: 5 if match == "CP2112" else None)
    assert KernelAdapterName(adapter="CP2112").resolve_bus_num() == 5


def test_kernel_serial_match_resolves_via_find_i2c_bus_by_serial(monkeypatch):
    from common import i2c_bus

    monkeypatch.setattr(i2c_bus, "find_i2c_bus_by_serial", lambda serial: 42 if serial == "AB12" else None)
    assert KernelSerialMatch(serial="AB12").resolve_bus_num() == 42
