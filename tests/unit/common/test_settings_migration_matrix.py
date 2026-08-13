"""Regression matrix for the ungated i2c bus shape repair.

A real x86_numato database supplied by the owner was stamped
{"server": "1.11.0", "build": 71} -- the code's own current version -- while
still holding the legacy (i2c_bus_kind, i2c_bus_num) shape. The version gate
in datastore._upgrade_settings_in_store() therefore never fired, and the
first write_settings() of any unrelated setting silently replaced two bus
configs and a probe device's bus with the {"kind": "basic"} default, moving
three devices onto the wrong bus with no error.

Every shape below is exercised at both the old version (where the pre-fix
gate already migrated correctly) and at the code's own current version
(where the gate used to skip the repair entirely, and the bug shipped). Both
must come out identical: the repair does not depend on which side of the
gate the stored version lands on.

Expected values are read directly off _legacy_bus_to_config's string-parsing
rules (common/settings_migration.py), not invented:
  * "basic" ignores whatever selector sits beside it.
  * "extended" + a bare digit -> kernel bus_num; + "serial:X" -> kernel
    serial; + anything else -> kernel adapter.
  * "ft232h" normalizes a bare "1" selector (the historical default) to "",
    the same as blank; a real ftdi:// url survives.
  * "mcp2221" carries its selector through as-is when it names a real
    device serial.
  * A bare i2c_bus_match (no i2c_bus_kind key at all) is treated as
    "extended" with that value as the selector.
"""

import copy

import pytest

from common import datastore
from common.common import read_updater_manifest
from common.persistence.runtime import read_settings_store, write_settings, write_settings_store
from common.defaults import default_settings

OLD_VERSION = {"server": "1.10.10", "cookfile": "1.5.0", "recipe": "1.0.0", "build": 70}

# label -> (legacy i2c_bus_kind/i2c_bus_num/i2c_bus_match pair, expected i2c_bus)
SHAPES = {
    "pi-basic": ({"i2c_bus_kind": "basic", "i2c_bus_num": ""}, {"kind": "basic"}),
    "pi-ext-num": ({"i2c_bus_kind": "extended", "i2c_bus_num": "3"}, {"kind": "kernel", "bus_num": 3}),
    "x86-ext-adapter": (
        {"i2c_bus_kind": "extended", "i2c_bus_num": "CP2112"},
        {"kind": "kernel", "adapter": "CP2112"},
    ),
    "x86-ext-serial": (
        {"i2c_bus_kind": "extended", "i2c_bus_num": "serial:0006634723"},
        {"kind": "kernel", "serial": "0006634723"},
    ),
    "ft232h-blank": ({"i2c_bus_kind": "ft232h", "i2c_bus_num": ""}, {"kind": "ft232h", "url": ""}),
    "ft232h-one": ({"i2c_bus_kind": "ft232h", "i2c_bus_num": "1"}, {"kind": "ft232h", "url": ""}),
    "ft232h-url": (
        {"i2c_bus_kind": "ft232h", "i2c_bus_num": "ftdi://ftdi:232h:FT9ABC/1"},
        {"kind": "ft232h", "url": "ftdi://ftdi:232h:FT9ABC/1"},
    ),
    "mcp2221-blank": ({"i2c_bus_kind": "mcp2221", "i2c_bus_num": ""}, {"kind": "mcp2221", "serial": ""}),
    "mcp2221-serial": (
        {"i2c_bus_kind": "mcp2221", "i2c_bus_num": "01234567"},
        {"kind": "mcp2221", "serial": "01234567"},
    ),
    "legacy-match": ({"i2c_bus_match": "CP2112"}, {"kind": "kernel", "adapter": "CP2112"}),
}


def _current_version():
    """The code's own current version, read live from updater_manifest.json
    -- not hardcoded -- so this test keeps exercising the exact case that
    was broken (stored version == running code's version) after any future
    build bump."""
    return read_updater_manifest()["metadata"]["versions"]


def _legacy_settings(legacy, version):
    """A settings tree carrying `legacy` at all three i2c-bus sites: the
    distance sensor, the fan controller, and one probe device's config."""
    settings = copy.deepcopy(default_settings())
    # An install still holding the legacy i2c shape predates the shape stamp.
    settings.pop("schema_version", None)
    settings["versions"] = dict(version)

    distance = settings["platform"]["devices"]["distance"]
    distance.pop("i2c_bus", None)
    distance.update(legacy)

    fan = settings["platform"]["fan_controller"]
    fan.pop("i2c_bus", None)
    fan.update(legacy)

    probe_config = {"cs": 0}
    probe_config.pop("i2c_bus", None)
    probe_config.update(legacy)
    settings["probe_settings"]["probe_map"]["probe_devices"] = [
        {
            "device": "ADS1115_0",
            "module": "ads1115_adafruit",
            "module_filename": "ads1115_adafruit",
            "config": probe_config,
        }
    ]
    return settings


@pytest.mark.parametrize("version_label,version", [("old 1.10.10/70", OLD_VERSION), ("cur", None)])
@pytest.mark.parametrize("shape_label", list(SHAPES))
def test_every_shape_survives_repair_and_a_validating_write(ds, shape_label, version_label, version):
    """The matrix: 10 legacy shapes x 2 version states (old, and the code's
    own current version -- the case the real x86_numato database hit)."""
    legacy, expected = SHAPES[shape_label]
    stamped_version = version if version is not None else _current_version()

    settings = _legacy_settings(legacy, stamped_version)
    write_settings_store(settings)

    datastore._upgrade_settings_in_store()

    stored = read_settings_store()
    write_settings(stored)
    stored = read_settings_store()

    assert stored["platform"]["devices"]["distance"]["i2c_bus"] == expected, (
        f"{shape_label} @ {version_label}: distance"
    )
    assert stored["platform"]["fan_controller"]["i2c_bus"] == expected, f"{shape_label} @ {version_label}: fan"
    assert stored["probe_settings"]["probe_map"]["probe_devices"][0]["config"]["i2c_bus"] == expected, (
        f"{shape_label} @ {version_label}: probe"
    )
    for section in (
        stored["platform"]["devices"]["distance"],
        stored["platform"]["fan_controller"],
        stored["probe_settings"]["probe_map"]["probe_devices"][0]["config"],
    ):
        assert "i2c_bus_kind" not in section
        assert "i2c_bus_num" not in section
        assert "i2c_bus_match" not in section


class _WriteSpy:
    """Wraps the real sqlite3.Connection (which is an immutable C type and
    cannot be monkeypatched directly or subclassed via composition-free
    attribute assignment) to record every `INSERT INTO kv` this connection
    issues, while every other call passes straight through."""

    def __init__(self, real, writes):
        self._real = real
        self._writes = writes

    def execute(self, sql, *args, **kwargs):
        if "INSERT INTO kv" in sql:
            self._writes.append(sql)
        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_a_tree_already_in_the_new_shape_is_left_alone_and_not_written(ds, monkeypatch):
    """Pins the no-op side of idempotency: a tree with nothing left to repair
    must not be rewritten, whatever the stored version is. A no-op write on
    every process start would itself be a regression."""
    settings = copy.deepcopy(default_settings())
    settings["versions"] = _current_version()
    write_settings_store(settings)
    before = copy.deepcopy(read_settings_store())

    writes = []
    real_conn = datastore.connection()
    monkeypatch.setattr(datastore, "connection", lambda: _WriteSpy(real_conn, writes))

    datastore._upgrade_settings_in_store()

    assert read_settings_store() == before
    assert writes == [], "settings:general was rewritten even though nothing changed"


def test_the_repair_is_not_version_gated(ds):
    """This is the test that would have caught the bug: a settings tree
    stamped at the code's own current version -- read live from
    updater/updater_manifest.json, not hardcoded -- still has its legacy i2c
    shape repaired, because the shape repair does not depend on the version
    cascade ever running."""
    current = _current_version()
    legacy, expected = SHAPES["x86-ext-adapter"]
    settings = _legacy_settings(legacy, current)
    write_settings_store(settings)

    datastore._upgrade_settings_in_store()

    stored = read_settings_store()
    assert stored["versions"]["build"] == current["build"]
    assert stored["platform"]["devices"]["distance"]["i2c_bus"] == expected
    assert "i2c_bus_kind" not in stored["platform"]["devices"]["distance"]
