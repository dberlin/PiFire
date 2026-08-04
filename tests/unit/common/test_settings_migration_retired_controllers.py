import copy

import pytest

from common import datastore
from common.datastore_accessors import read_settings_store, write_settings_store
from common.defaults import default_settings
from common.settings_migration import _migrate_retired_controllers

RETIRED = (
    "pid_clamping",
    "pid_clamping_percent_pb",
    "pid_ac",
    "pid_parallel",
    "fuzzy",
    "ml",
)
RETAINED = ("pid", "pid_sp", "mpc")


def _settings(selected="pid"):
    settings = copy.deepcopy(default_settings())
    settings["controller"]["selected"] = selected
    settings["controller"]["config"].update({name: {"stale": 1} for name in RETIRED})
    return settings


@pytest.mark.parametrize("selected", RETIRED)
def test_retired_selection_moves_to_pid_and_preserves_pid_config(selected):
    settings = _settings(selected)
    expected_pid = copy.deepcopy(settings["controller"]["config"]["pid"])

    assert _migrate_retired_controllers(settings) is True

    assert settings["controller"]["selected"] == "pid"
    assert settings["controller"]["config"]["pid"] == expected_pid
    assert not (set(settings["controller"]["config"]) & set(RETIRED))


@pytest.mark.parametrize("selected", RETAINED)
def test_retained_selection_is_unchanged_while_stale_blocks_are_removed(selected):
    settings = _settings(selected)

    assert _migrate_retired_controllers(settings) is True

    assert settings["controller"]["selected"] == selected
    assert not (set(settings["controller"]["config"]) & set(RETIRED))


def test_migration_is_idempotent():
    settings = _settings("ml")
    assert _migrate_retired_controllers(settings) is True
    assert _migrate_retired_controllers(settings) is False


@pytest.mark.parametrize(
    "settings",
    [{}, {"controller": None}, {"controller": {}}, {"controller": {"config": None}}],
)
def test_malformed_or_missing_controller_tree_is_left_for_normal_repair(settings):
    before = copy.deepcopy(settings)
    assert _migrate_retired_controllers(settings) is False
    assert settings == before


def test_store_upgrade_migrates_a_schema_v2_retired_controller_selection(ds):
    settings = _settings("ml")
    settings["schema_version"] = 2
    expected_pid = copy.deepcopy(settings["controller"]["config"]["pid"])
    write_settings_store(settings)

    datastore._upgrade_settings_in_store()

    stored = read_settings_store()
    assert stored["schema_version"] == 3
    assert stored["controller"]["selected"] == "pid"
    assert stored["controller"]["config"]["pid"] == expected_pid
    assert not (set(stored["controller"]["config"]) & set(RETIRED))
