"""The wizard's own view of the same check: pair each i2c_bus_kind dependency
with the i2c_bus_num that sits beside it, using the user's in-progress
selections rather than the live settings tree."""

import pytest

from blueprints.wizard.wizard import wizard_bus_selectors
from common.i2c_bus import I2CBusConfigError, validate_bus_selectors

_WIZARD_DATA = {
    "modules": {
        "grillplatform": {
            "ft232h_relay": {
                "settings_dependencies": {
                    "device_distance_i2c_bus_kind": {"settings": ["platform", "devices", "distance", "i2c_bus_kind"]},
                    "device_distance_i2c_bus_num": {"settings": ["platform", "devices", "distance", "i2c_bus_num"]},
                    "fan_mode": {"settings": ["platform", "fan_controller", "chip"]},
                }
            }
        },
        "distance": {
            "vl53l4cd": {
                "settings_dependencies": {
                    "i2c_bus_kind": {"settings": ["platform", "devices", "distance", "i2c_bus_kind"]},
                    "i2c_bus_num": {"settings": ["platform", "devices", "distance", "i2c_bus_num"]},
                }
            },
            "none": {"settings_dependencies": {}},
        },
    }
}


def _info(platform_settings, probe_devices=()):
    return {
        "modules": {
            "grillplatform": {"profile_selected": ["ft232h_relay"], "settings": platform_settings},
            "distance": {"profile_selected": ["none"], "settings": {}},
        },
        "probe_map": {"probe_devices": list(probe_devices)},
    }


def test_wizard_bus_selectors_pairs_a_kind_with_the_num_beside_it():
    info = _info(
        {"device_distance_i2c_bus_kind": "ft232h", "device_distance_i2c_bus_num": "ftdi://ftdi:232h/1"},
        probe_devices=[{"device": "ADS1115_0", "config": {"i2c_bus_kind": "extended", "i2c_bus_num": "CP2112"}}],
    )
    assert sorted(wizard_bus_selectors(info, _WIZARD_DATA)) == sorted(
        [
            ("ADS1115_0", "extended", "CP2112"),
            ("grillplatform/device_distance_i2c_bus_kind", "ft232h", "ftdi://ftdi:232h/1"),
        ]
    )
    validate_bus_selectors(wizard_bus_selectors(info, _WIZARD_DATA))  # workable -> no raise


def test_wizard_bus_selectors_catches_a_leftover_bridge_name():
    info = _info({"device_distance_i2c_bus_kind": "mcp2221", "device_distance_i2c_bus_num": "CP2112"})
    with pytest.raises(I2CBusConfigError, match="CP2112"):
        validate_bus_selectors(wizard_bus_selectors(info, _WIZARD_DATA))


def test_wizard_bus_selectors_ignores_a_kind_with_no_num_beside_it():
    # A module may configure a bus kind without exposing a selector at all
    # (nothing to pair, nothing to validate) -- must not KeyError.
    wizard_data = {
        "modules": {
            "grillplatform": {
                "ft232h_relay": {
                    "settings_dependencies": {
                        "device_distance_i2c_bus_kind": {
                            "settings": ["platform", "devices", "distance", "i2c_bus_kind"]
                        }
                    }
                }
            }
        }
    }
    info = _info({"device_distance_i2c_bus_kind": "ft232h"})
    del info["modules"]["distance"]
    assert wizard_bus_selectors(info, wizard_data) == []
