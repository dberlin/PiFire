"""An extended-bus selector on a USB-HID bus names a bus that kind cannot open.

`extended` addresses a kernel i2c-dev adapter -- by /dev/i2c-N number, by
adapter name ("CP2112"), or by USB iSerial ("serial:<ISERIAL>"). `ft232h` takes
a pyftdi URL and `mcp2221` a device serial; neither goes anywhere near
/sys/bus/i2c/devices. Leaving an extended selector in place while switching the
bus type to FT232H or MCP2221 used to be accepted and then failed at run time,
inside the control process, with whatever pyftdi or EasyMCP2221 made of it.
"""

import pytest

from common.i2c_bus import (
    I2CBusConfigError,
    configured_bus_selectors,
    validate_bus_selector,
    validate_bus_selectors,
)


@pytest.mark.parametrize("kind", ["ft232h", "mcp2221"])
@pytest.mark.parametrize("selector", ["CP2112", "cp2112", "MCP2221", "serial:0012AB34"])
def test_usb_hid_kinds_reject_an_extended_selector(kind, selector):
    with pytest.raises(I2CBusConfigError):
        validate_bus_selector(kind, selector)


@pytest.mark.parametrize("selector", ["", None, "1", "ftdi://ftdi:232h:FT1ABCDE/1"])
def test_ft232h_accepts_blank_first_and_a_pyftdi_url(selector):
    validate_bus_selector("ft232h", selector)


@pytest.mark.parametrize("selector", ["", None, "0012AB34", "FT1ABCDE"])
def test_mcp2221_accepts_blank_and_a_device_serial(selector):
    validate_bus_selector("mcp2221", selector)


@pytest.mark.parametrize("selector", ["3", "CP2112", "MCP2221", "serial:0012AB34"])
def test_extended_accepts_its_whole_namespace(selector):
    validate_bus_selector("extended", selector)


def test_basic_ignores_its_selector():
    # `basic` is board.SCL/SDA; nothing reads the selector, so a leftover value
    # is not a configuration error.
    validate_bus_selector("basic", "CP2112")


def test_ft232h_rejects_a_bare_number_that_is_not_the_first_device():
    # '1' is the manifest's "first FT232H"; '3' is a /dev/i2c-N number that only
    # means something to `extended`, and pyftdi rejects it as a URL.
    with pytest.raises(I2CBusConfigError):
        validate_bus_selector("ft232h", "3")


def test_the_error_names_the_device_and_what_is_accepted():
    with pytest.raises(I2CBusConfigError) as excinfo:
        validate_bus_selector("ft232h", "CP2112", where="ADS1115_0")
    message = str(excinfo.value)
    assert "ADS1115_0" in message
    assert "CP2112" in message
    assert "ftdi://" in message


def _settings(distance=None, fan=None):
    platform = {}
    if distance:
        platform["devices"] = {"distance": distance}
    if fan:
        platform["fan_controller"] = fan
    return {"platform": platform}


def test_configured_bus_selectors_collects_all_surfaces():
    selectors = configured_bus_selectors(
        _settings(
            distance={"i2c_bus_kind": "ft232h", "i2c_bus_num": "1"},
            fan={"i2c_bus_kind": "extended", "i2c_bus_num": "CP2112"},
        ),
        {"probe_devices": [{"device": "ADS1115_0", "config": {"i2c_bus_kind": "mcp2221", "i2c_bus_num": "0012AB34"}}]},
    )
    assert sorted(selectors) == sorted(
        [
            ("ADS1115_0", "mcp2221", "0012AB34"),
            ("distance sensor", "ft232h", "1"),
            ("fan controller", "extended", "CP2112"),
        ]
    )
    validate_bus_selectors(selectors)  # all workable -> no raise


def test_configured_bus_selectors_catches_the_leftover_bridge_name():
    selectors = configured_bus_selectors(
        _settings(distance={"i2c_bus_kind": "ft232h", "i2c_bus_num": "CP2112"}),
        None,
    )
    with pytest.raises(I2CBusConfigError, match="distance sensor"):
        validate_bus_selectors(selectors)


def test_configured_bus_selectors_skips_a_surface_with_no_kind():
    assert configured_bus_selectors({}, None) == []
    assert configured_bus_selectors(None, {"probe_devices": [{"device": "SPI_0", "config": {}}]}) == []
