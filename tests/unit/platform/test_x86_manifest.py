import json
import os


def _manifest():
    path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "wizard", "wizard_manifest.json")
    with open(path) as handle:
        return json.load(handle)


def test_x86_platform_entry_present():
    manifest = _manifest()
    entry = manifest["modules"]["grillplatform"]["x86_numato"]
    assert entry["filename"] == "x86_numato"
    assert "adafruit-circuitpython-emc2101" in entry["py_dependencies"]


def test_x86_platform_settings_dependencies():
    manifest = _manifest()
    deps = manifest["modules"]["grillplatform"]["x86_numato"]["settings_dependencies"]
    # Chip selector plus the selectable basic/extended I2C bus and address.
    assert set(deps["fan_controller_chip"]["options"]) == {"emc2101", "emc2301"}
    assert deps["fan_controller_chip"]["settings"] == ["platform", "fan_controller", "chip"]
    assert deps["i2c_bus_kind"]["settings"] == ["platform", "fan_controller", "i2c_bus_kind"]
    assert deps["i2c_bus_num"]["settings"] == ["platform", "fan_controller", "i2c_bus_num"]
    assert deps["fan_controller_address"]["settings"] == ["platform", "fan_controller", "address"]
    assert "0x2f" in deps["fan_controller_address"]["options"]
    assert set(deps["i2c_bus_kind"]["options"]) == {"basic", "extended", "ft232h", "mcp2221"}


def test_x86_fan_bus_kind_includes_usb_hid():
    import json
    import os

    manifest = json.load(
        open(os.path.join(os.path.dirname(__file__), "..", "..", "..", "wizard", "wizard_manifest.json"))
    )
    # Locate the x86_numato fan_controller i2c_bus_kind options.
    numato = manifest["modules"]["grillplatform"]["x86_numato"]
    deps = numato["settings_dependencies"]
    options = set(deps["i2c_bus_kind"]["options"])
    assert {"basic", "extended", "ft232h", "mcp2221"} <= options


def test_numato_device_offers_discovery_scoped_to_the_relay_board():
    """The Numato serial path is discoverable, and the scan is narrowed to the
    board's own USB IDs.

    /dev/ttyACM* is assigned in enumeration order, so the right path moves when
    devices are replugged -- a hardcoded two-item dropdown cannot tell the user
    which one is the relay, and picking the wrong one makes every relay
    operation time out silently (see grillplat/numato_usbrelay.py's identity
    probe, which now refuses that case outright).

    The IDs are the Numato Lab 4 Channel USB Solid State Relay Module's, and
    they are written the way USB IDs are written; common/usb_serial.py coerces
    the hex string to the int pyserial reports.
    """
    deps = _manifest()["modules"]["grillplatform"]["x86_numato"]["settings_dependencies"]
    dep = deps["numato_device"]
    assert dep["type"] == "usb_serial_device"
    assert dep["vid"] == "0x2a19"
    assert dep["pid"] == "0x0c0c"
    assert dep["settings"] == ["platform", "numato", "device"]
    # The picker falls back to this when nothing is configured yet.
    assert dep["default"] == "/dev/ttyACM0"


def test_numato_device_vid_pid_match_the_driver_the_wizard_configures():
    """Guard against the manifest and the driver drifting apart: these IDs are
    the whole basis for claiming a discovered port IS the relay board."""
    from common.usb_serial import _as_usb_id

    dep = _manifest()["modules"]["grillplatform"]["x86_numato"]["settings_dependencies"]["numato_device"]
    assert _as_usb_id(dep["vid"]) == 0x2A19
    assert _as_usb_id(dep["pid"]) == 0x0C0C
