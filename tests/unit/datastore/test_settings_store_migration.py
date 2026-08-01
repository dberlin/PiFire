"""The settings migration has to reach the tree that actually runs the grill.

upgrade_settings used to reach only settings imported from a JSON file, so a
tree that had lived in SQLite since first boot never saw a migration. A shape
change then left every existing install holding keys the schema no longer
models -- which the write-time repair strips, silently, on the next save.
"""

import copy

from common import datastore
from common.datastore_accessors import read_settings_store, write_settings_store
from common.defaults import default_settings


def _legacy_stored_settings(ds):
    """A settings tree as an install predating the i2c_bus composite holds it."""
    settings = copy.deepcopy(default_settings())
    settings["versions"] = {"server": "1.10.10", "cookfile": "1.5.0", "recipe": "1.0.0", "build": 70}
    distance = settings["platform"]["devices"]["distance"]
    distance.pop("i2c_bus", None)
    distance["i2c_bus_kind"] = "extended"
    distance["i2c_bus_num"] = "CP2112"
    fan = settings["platform"]["fan_controller"]
    fan.pop("i2c_bus", None)
    fan["i2c_bus_kind"] = "basic"
    fan["i2c_bus_num"] = "1"
    write_settings_store(settings)
    return settings


def test_a_legacy_tree_in_sqlite_is_migrated(ds):
    _legacy_stored_settings(ds)

    datastore._upgrade_settings_in_store()

    stored = read_settings_store()
    assert stored["platform"]["devices"]["distance"]["i2c_bus"] == {"kind": "kernel", "adapter": "CP2112"}
    assert stored["platform"]["fan_controller"]["i2c_bus"] == {"kind": "basic"}
    assert "i2c_bus_kind" not in stored["platform"]["devices"]["distance"]
    assert "i2c_bus_num" not in stored["platform"]["devices"]["distance"]


def test_the_version_is_stamped_so_it_does_not_run_twice(ds):
    _legacy_stored_settings(ds)

    datastore._upgrade_settings_in_store()
    once = copy.deepcopy(read_settings_store())
    datastore._upgrade_settings_in_store()

    assert read_settings_store()["versions"] == default_settings()["versions"]
    assert read_settings_store() == once


def test_an_already_current_tree_is_left_alone(ds):
    settings = copy.deepcopy(default_settings())
    write_settings_store(settings)

    datastore._upgrade_settings_in_store()

    assert read_settings_store() == settings


def test_the_migrated_tree_survives_a_validating_write(ds):
    """The whole point: after migration, write_settings must not strip the bus.

    Before this task, validate_settings_tree stripped the unmodeled legacy keys
    and substituted the i2c_bus default -- a configured CP2112 adapter silently
    became the board's own pins.
    """
    from common.datastore_accessors import write_settings

    _legacy_stored_settings(ds)
    datastore._upgrade_settings_in_store()

    settings = read_settings_store()
    write_settings(settings)

    assert read_settings_store()["platform"]["devices"]["distance"]["i2c_bus"] == {
        "kind": "kernel",
        "adapter": "CP2112",
    }


def test_init_runs_the_upgrade(monkeypatch):
    """init() is the startup hook; the migration must be wired into it."""
    calls = []
    monkeypatch.setattr(datastore, "_first_boot_import", lambda: calls.append("import"))
    monkeypatch.setattr(datastore, "_upgrade_settings_in_store", lambda: calls.append("upgrade"))
    monkeypatch.setattr(datastore, "connection", lambda: calls.append("connect"))

    datastore.init()

    assert calls == ["connect", "import", "upgrade"]
