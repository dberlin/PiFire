"""The shape version decides which migrations run, and the release version
does not get a vote.

A real grill sat at 1.11.0 build 71 -- the code's own current release, so the
version gate was closed -- while still holding the pre-71 i2c settings shape.
Every case here is about what the STAMP says, with the release version held
current throughout so it cannot be the thing doing the work.
"""

import copy
import json

import pytest

from common import datastore, settings_migration
from common.persistence.runtime import read_settings_store, write_settings_store
from common.defaults import default_settings
from common.settings_schema import SETTINGS_SCHEMA_VERSION


def _unstamped_legacy_tree():
    """A settings tree as an install predating the stamp holds it: current
    release, legacy i2c shape, no schema_version at all."""
    settings = copy.deepcopy(default_settings())
    settings.pop("schema_version", None)
    distance = settings["platform"]["devices"]["distance"]
    distance.pop("i2c_bus", None)
    distance["i2c_bus_kind"] = "extended"
    distance["i2c_bus_num"] = "CP2112"
    write_settings_store(settings)
    return settings


def test_an_unstamped_tree_runs_every_step_and_ends_stamped_current(ds):
    _unstamped_legacy_tree()

    datastore._upgrade_settings_in_store()

    stored = read_settings_store()
    assert stored["schema_version"] == SETTINGS_SCHEMA_VERSION
    assert stored["platform"]["devices"]["distance"]["i2c_bus"] == {"kind": "kernel", "adapter": "CP2112"}


def test_a_current_tree_runs_no_step(ds, monkeypatch):
    """Spy on the callables rather than compare trees: every step here is
    idempotent, so an output comparison would pass while the step still ran."""
    ran = []
    monkeypatch.setattr(
        settings_migration,
        "_SHAPE_MIGRATIONS",
        [(1, lambda tree: ran.append(1) or False)],
    )
    write_settings_store(copy.deepcopy(default_settings()))

    datastore._upgrade_settings_in_store()

    assert ran == []

def test_a_sparse_pre_setting_tree_backfills_observe_without_a_schema_bump(ds, tmp_path):
    settings = copy.deepcopy(default_settings())
    settings.pop("thermocouple_health", None)
    original_schema_version = settings["schema_version"]
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(settings))

    normalized = settings_migration.read_settings_file(filename=str(path), init=True)

    assert normalized["thermocouple_health"] == {"inference_policy": "observe"}
    assert normalized["schema_version"] == original_schema_version
    assert SETTINGS_SCHEMA_VERSION == original_schema_version == 11


def test_only_the_steps_above_the_stamp_run(ds, monkeypatch):
    ran = []
    monkeypatch.setattr(
        settings_migration,
        "_SHAPE_MIGRATIONS",
        [
            (1, lambda tree: ran.append(1) or False),
            (2, lambda tree: ran.append(2) or False),
            (3, lambda tree: ran.append(3) or False),
        ],
    )
    # Patch the SOURCE module: _upgrade_settings_in_store imports the constant
    # inside the function body, so it reads common.settings_schema at call time.
    monkeypatch.setattr("common.settings_schema.SETTINGS_SCHEMA_VERSION", 3)
    settings = copy.deepcopy(default_settings())
    settings["schema_version"] = 1
    write_settings_store(settings)

    datastore._upgrade_settings_in_store()

    assert ran == [2, 3]


def test_a_stamp_from_the_future_runs_nothing_and_is_not_rewound(ds, monkeypatch):
    """An operator downgraded PiFire. This code cannot know what the newer
    keys meant, so it must not migrate backwards, and must not crash the boot
    path either."""
    ran = []
    monkeypatch.setattr(settings_migration, "_SHAPE_MIGRATIONS", [(1, lambda tree: ran.append(1) or True)])
    settings = copy.deepcopy(default_settings())
    settings["schema_version"] = SETTINGS_SCHEMA_VERSION + 5
    write_settings_store(settings)

    datastore._upgrade_settings_in_store()

    assert ran == []
    assert read_settings_store()["schema_version"] == SETTINGS_SCHEMA_VERSION + 5


def test_the_stamp_is_not_written_when_a_step_raises(ds, monkeypatch):
    """A version stamped ahead of the data is the one failure that cannot
    self-heal on the next boot."""

    def _explode(tree):
        raise RuntimeError("migration blew up")

    monkeypatch.setattr(settings_migration, "_SHAPE_MIGRATIONS", [(1, _explode)])
    _unstamped_legacy_tree()

    with pytest.raises(RuntimeError):
        datastore._upgrade_settings_in_store()

    stored = read_settings_store()
    assert "schema_version" not in stored
    assert stored["platform"]["devices"]["distance"]["i2c_bus_kind"] == "extended"


def test_running_the_chain_twice_is_identical_to_running_it_once(ds):
    _unstamped_legacy_tree()

    datastore._upgrade_settings_in_store()
    once = copy.deepcopy(read_settings_store())
    datastore._upgrade_settings_in_store()

    assert read_settings_store() == once


def test_a_migrated_tree_survives_a_validating_write(ds):
    """The stamp is a modeled field, so write_settings must preserve it rather
    than strip it as an unmodeled key -- which is what happens to anything the
    schema does not know about."""
    from common.persistence.runtime import write_settings

    _unstamped_legacy_tree()
    datastore._upgrade_settings_in_store()

    write_settings(read_settings_store())

    assert read_settings_store()["schema_version"] == SETTINGS_SCHEMA_VERSION


def test_init_stamps_a_pre_stamp_store(ds):
    """init() is what every entry point calls; the stamp has to arrive there
    and not only in the function under test."""
    _unstamped_legacy_tree()

    datastore.init()

    assert read_settings_store()["schema_version"] == SETTINGS_SCHEMA_VERSION
