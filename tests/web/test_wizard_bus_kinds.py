import pytest

from blueprints.wizard.wizard import wizard_bus_kinds
from common.i2c_bus import I2CBusConfigError, validate_bus_kinds

# Minimal wizardData: the only thing that matters for bus-kind collection is a
# settings-dependency whose `settings` path ends in 'i2c_bus_kind' (fan
# controller / distance sensor); other deps must be ignored.
_WIZARD_DATA = {
    "modules": {
        "grillplatform": {
            "x86": {
                "settings_dependencies": {
                    "fan_chip": {"settings": ["platform", "fan_controller", "chip"]},
                    "i2c_bus_kind": {"settings": ["platform", "fan_controller", "i2c_bus_kind"]},
                }
            }
        },
        "distance": {
            "vl53": {
                "settings_dependencies": {
                    "device_distance_i2c_bus_kind": {"settings": ["platform", "devices", "distance", "i2c_bus_kind"]}
                }
            },
            "none": {"settings_dependencies": {}},
        },
    }
}


def _install_info(probe_kinds=(), fan_kind=None, distance_kind=None, distance_module="vl53"):
    info = {
        "probe_map": {"probe_devices": [{"config": {"i2c_bus_kind": k}} for k in probe_kinds]},
        "modules": {
            "grillplatform": {"profile_selected": ["x86"], "settings": {}},
            "distance": {"profile_selected": [distance_module], "settings": {}},
        },
    }
    if fan_kind is not None:
        info["modules"]["grillplatform"]["settings"]["i2c_bus_kind"] = fan_kind
    if distance_kind is not None:
        info["modules"]["distance"]["settings"]["device_distance_i2c_bus_kind"] = distance_kind
    return info


def test_collects_probe_fan_and_distance_kinds():
    info = _install_info(probe_kinds=["ft232h", "extended"], fan_kind="mcp2221", distance_kind="ft232h")
    assert wizard_bus_kinds(info, _WIZARD_DATA) == {"ft232h", "extended", "mcp2221"}


def test_flags_basic_plus_usb_hid_across_subsystems():
    # Probes on ft232h but the fan left on the onboard 'basic' bus -> the one
    # unworkable combo, which the finish step must catch.
    info = _install_info(probe_kinds=["ft232h"], fan_kind="basic")
    with pytest.raises(I2CBusConfigError):
        validate_bus_kinds(wizard_bus_kinds(info, _WIZARD_DATA))


def test_ignores_non_bus_deps_and_absent_selectors():
    # The fan_chip dep (path not ending in i2c_bus_kind) is ignored, and a
    # distance module with no i2c dep contributes nothing.
    info = _install_info(probe_kinds=["ft232h"], fan_kind="mcp2221", distance_module="none")
    info["modules"]["grillplatform"]["settings"]["fan_chip"] = "emc2101"
    assert wizard_bus_kinds(info, _WIZARD_DATA) == {"ft232h", "mcp2221"}
    validate_bus_kinds(wizard_bus_kinds(info, _WIZARD_DATA))  # workable -> no raise
