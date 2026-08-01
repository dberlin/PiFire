"""Every legacy (i2c_bus_kind, i2c_bus_num) shape, at every site that stores one.

The pair is unreadable to the new code, so a config that fails to migrate does
not degrade -- it stops the device from opening at all. Pure string parsing: the
migration runs before any hardware is touched, so it can never probe.
"""

import copy

import pytest

from common.defaults import default_settings
from common.settings_migration import _migrate_i2c_buses

CASES = [
    ({"i2c_bus_kind": "basic", "i2c_bus_num": "CP2112"}, {"kind": "basic"}),
    ({"i2c_bus_kind": "basic", "i2c_bus_num": ""}, {"kind": "basic"}),
    ({"i2c_bus_kind": "extended", "i2c_bus_num": "3"}, {"kind": "kernel", "bus_num": 3}),
    ({"i2c_bus_kind": "extended", "i2c_bus_num": 3}, {"kind": "kernel", "bus_num": 3}),
    ({"i2c_bus_kind": "extended", "i2c_bus_num": "serial:AB12"}, {"kind": "kernel", "serial": "AB12"}),
    ({"i2c_bus_kind": "extended", "i2c_bus_num": "SERIAL:AB12"}, {"kind": "kernel", "serial": "AB12"}),
    ({"i2c_bus_kind": "extended", "i2c_bus_num": "CP2112"}, {"kind": "kernel", "adapter": "CP2112"}),
    ({"i2c_bus_kind": "extended", "i2c_bus_num": ""}, {"kind": "basic"}),
    ({"i2c_bus_kind": "ft232h", "i2c_bus_num": ""}, {"kind": "ft232h", "url": ""}),
    ({"i2c_bus_kind": "ft232h", "i2c_bus_num": "1"}, {"kind": "ft232h", "url": ""}),
    (
        {"i2c_bus_kind": "ft232h", "i2c_bus_num": "ftdi://ftdi:232h:FT9/1"},
        {"kind": "ft232h", "url": "ftdi://ftdi:232h:FT9/1"},
    ),
    ({"i2c_bus_kind": "ft232h", "i2c_bus_num": "CP2112"}, {"kind": "ft232h", "url": ""}),
    ({"i2c_bus_kind": "ft232h", "i2c_bus_num": "serial:AB12"}, {"kind": "ft232h", "url": ""}),
    ({"i2c_bus_kind": "mcp2221", "i2c_bus_num": ""}, {"kind": "mcp2221", "serial": ""}),
    ({"i2c_bus_kind": "mcp2221", "i2c_bus_num": "0123"}, {"kind": "mcp2221", "serial": "0123"}),
    ({"i2c_bus_kind": "mcp2221", "i2c_bus_num": "CP2112"}, {"kind": "mcp2221", "serial": ""}),
    ({"i2c_bus_match": "CP2112"}, {"kind": "kernel", "adapter": "CP2112"}),
]


def _legacy_settings(section):
    settings = copy.deepcopy(default_settings())
    settings["platform"]["devices"]["distance"] = dict(section)
    settings["platform"]["fan_controller"] = {"chip": "emc2101", "address": "0x4c", **section}
    settings["probe_settings"]["probe_map"]["probe_devices"] = [
        {"device": "ADS1115_0", "module": "ads1115_adafruit", "config": dict(section)}
    ]
    return settings


@pytest.mark.parametrize("legacy,expected", CASES)
def test_every_legacy_shape_migrates_at_every_site(legacy, expected):
    settings = _legacy_settings(legacy)
    _migrate_i2c_buses(settings)

    assert settings["platform"]["devices"]["distance"]["i2c_bus"] == expected
    assert settings["platform"]["fan_controller"]["i2c_bus"] == expected
    assert settings["probe_settings"]["probe_map"]["probe_devices"][0]["config"]["i2c_bus"] == expected


@pytest.mark.parametrize("legacy,expected", CASES)
def test_the_legacy_keys_are_removed(legacy, expected):
    settings = _legacy_settings(legacy)
    _migrate_i2c_buses(settings)

    for section in (
        settings["platform"]["devices"]["distance"],
        settings["platform"]["fan_controller"],
        settings["probe_settings"]["probe_map"]["probe_devices"][0]["config"],
    ):
        assert "i2c_bus_kind" not in section
        assert "i2c_bus_num" not in section
        assert "i2c_bus_match" not in section


def test_migration_is_idempotent():
    """upgrade_settings can run twice across an upgrade/downgrade cycle."""
    settings = _legacy_settings({"i2c_bus_kind": "extended", "i2c_bus_num": "CP2112"})
    _migrate_i2c_buses(settings)
    once = copy.deepcopy(settings)
    _migrate_i2c_buses(settings)
    assert settings == once


def test_a_device_with_no_bus_at_all_is_left_alone():
    settings = copy.deepcopy(default_settings())
    settings["probe_settings"]["probe_map"]["probe_devices"] = [
        {"device": "SPI_0", "module": "max31865", "config": {"cs": 0}}
    ]
    _migrate_i2c_buses(settings)
    assert settings["probe_settings"]["probe_map"]["probe_devices"][0]["config"] == {"cs": 0}


def test_the_migrated_tree_validates():
    from common.settings_schema import validate_settings_tree

    settings = _legacy_settings({"i2c_bus_kind": "extended", "i2c_bus_num": "serial:AB12"})
    _migrate_i2c_buses(settings)
    validate_settings_tree(settings)


def test_the_historical_ft232h_default_is_not_reported_as_dropped(monkeypatch):
    """'1' means "the first FT232H", the same as blank -- an operator upgrading a
    working FT232H install must not be told their selector was discarded."""
    logged = []
    monkeypatch.setattr("common.settings_migration.write_log", lambda msg: logged.append(msg))
    settings = _legacy_settings({"i2c_bus_kind": "ft232h", "i2c_bus_num": "1"})
    _migrate_i2c_buses(settings)
    assert settings["platform"]["fan_controller"]["i2c_bus"] == {"kind": "ft232h", "url": ""}
    assert logged == []


def test_a_stranded_ft232h_selector_is_still_reported_with_its_value(monkeypatch):
    logged = []
    monkeypatch.setattr("common.settings_migration.write_log", lambda msg: logged.append(msg))
    settings = _legacy_settings({"i2c_bus_kind": "ft232h", "i2c_bus_num": "CP2112"})
    _migrate_i2c_buses(settings)
    assert settings["platform"]["fan_controller"]["i2c_bus"] == {"kind": "ft232h", "url": ""}
    assert any("CP2112" in msg for msg in logged)


def test_an_unrecognized_kind_reports_the_selector_it_discards(monkeypatch):
    logged = []
    monkeypatch.setattr("common.settings_migration.write_log", lambda msg: logged.append(msg))
    settings = _legacy_settings({"i2c_bus_kind": None, "i2c_bus_num": "3"})
    _migrate_i2c_buses(settings)
    assert settings["platform"]["fan_controller"]["i2c_bus"] == {"kind": "basic"}
    assert any("'3'" in msg for msg in logged)
