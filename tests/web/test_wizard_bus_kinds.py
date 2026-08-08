import pytest

from blueprints.wizard.wizard import wizard_bus_kinds
from common.i2c_bus import I2CBusConfigError, validate_bus_kinds

# Minimal wizardData: the only thing that matters for bus-kind collection is a
# settings-dependency of type 'i2c_bus' (fan controller / distance sensor);
# other deps must be ignored.
_WIZARD_DATA = {
    "modules": {
        "grillplatform": {
            "x86": {
                "settings_dependencies": {
                    "fan_chip": {"settings": ["platform", "fan_controller", "chip"]},
                    "i2c_bus": {"type": "i2c_bus", "settings": ["platform", "fan_controller", "i2c_bus"]},
                    "device_distance_i2c_bus": {
                        "type": "i2c_bus",
                        "settings": ["platform", "devices", "distance", "i2c_bus"],
                    },
                }
            }
        },
        "distance": {
            "vl53l0x": {"settings_dependencies": {}},
            "none": {"settings_dependencies": {}},
        },
    }
}


def _install_info(probe_kinds=(), fan_kind=None, distance_kind=None, distance_module="vl53l0x", fan_chip="emc2101"):
    info = {
        "probe_map": {"probe_devices": [{"config": {"i2c_bus": k}} for k in probe_kinds]},
        "modules": {
            "grillplatform": {"profile_selected": ["x86"], "settings": {}},
            "distance": {"profile_selected": [distance_module], "settings": {}},
        },
    }
    if fan_kind is not None:
        info["modules"]["grillplatform"]["settings"]["i2c_bus"] = fan_kind
        info["modules"]["grillplatform"]["settings"]["fan_chip"] = fan_chip
    if distance_kind is not None:
        info["modules"]["grillplatform"]["settings"]["device_distance_i2c_bus"] = distance_kind
    return info


def test_collects_probe_fan_and_distance_kinds():
    info = _install_info(
        probe_kinds=[{"kind": "ft232h", "url": ""}, {"kind": "kernel", "adapter": "CP2112"}],
        fan_kind={"kind": "mcp2221", "serial": ""},
        distance_kind={"kind": "ft232h", "url": ""},
    )
    assert wizard_bus_kinds(info, _WIZARD_DATA) == {"ft232h", "kernel", "mcp2221"}


def test_flags_basic_plus_usb_hid_across_subsystems():
    # Probes on ft232h but the fan left on the onboard 'basic' bus -> the one
    # unworkable combo, which the finish step must catch.
    info = _install_info(probe_kinds=[{"kind": "ft232h", "url": ""}], fan_kind={"kind": "basic"})
    with pytest.raises(I2CBusConfigError):
        validate_bus_kinds(wizard_bus_kinds(info, _WIZARD_DATA))


def test_ignores_non_bus_deps_and_absent_selectors():
    # The fan_chip dep (not type 'i2c_bus') is ignored, and a distance module
    # with no i2c dep contributes nothing.
    info = _install_info(
        probe_kinds=[{"kind": "ft232h", "url": ""}], fan_kind={"kind": "mcp2221", "serial": ""}, distance_module="none"
    )
    info["modules"]["grillplatform"]["settings"]["fan_chip"] = "emc2101"
    assert wizard_bus_kinds(info, _WIZARD_DATA) == {"ft232h", "mcp2221"}
    validate_bus_kinds(wizard_bus_kinds(info, _WIZARD_DATA))  # workable -> no raise


def test_relay_only_fan_with_no_distance_ignores_both_unused_i2c_buses():
    info = _install_info(
        fan_kind={"kind": "mcp2221", "serial": ""},
        distance_kind={"kind": "basic"},
        distance_module="none",
        fan_chip="none",
    )

    kinds = wizard_bus_kinds(info, _WIZARD_DATA)
    assert kinds == set()
    validate_bus_kinds(kinds)


def test_mcp2221_pwm_with_no_distance_counts_only_the_active_fan_bus():
    info = _install_info(
        fan_kind={"kind": "mcp2221", "serial": ""},
        distance_kind={"kind": "basic"},
        distance_module="none",
        fan_chip="EMC2101",
    )

    kinds = wizard_bus_kinds(info, _WIZARD_DATA)
    assert kinds == {"mcp2221"}
    validate_bus_kinds(kinds)
