import copy

from common.defaults import default_settings
from common.settings_migration import _apply_shape_migrations
from common.settings_schema import SETTINGS_SCHEMA_VERSION


def test_schema_six_fan_pid_setting_is_removed_without_disturbing_cycle_values():
    settings = default_settings()
    settings["schema_version"] = 6
    settings["cycle_data"]["FanPidEnabled"] = True
    settings["cycle_data"]["u_min"] = 0.23
    settings["cycle_data"]["u_max"] = 0.81

    assert _apply_shape_migrations(settings, SETTINGS_SCHEMA_VERSION) is True
    assert settings["schema_version"] == 8
    assert settings["cycle_data"] == {
        "SmokeOnCycleTime": 15,
        "SmokeOffCycleTime": 45,
        "PMode": 2,
        "u_min": 0.23,
        "u_max": 0.81,
        "LidOpenDetectEnabled": False,
        "LidOpenThreshold": 15,
        "LidOpenPauseTime": 60,
    }

    migrated = copy.deepcopy(settings)
    assert _apply_shape_migrations(settings, SETTINGS_SCHEMA_VERSION) is False
    assert settings == migrated


def test_future_schema_tree_is_left_untouched():
    settings = default_settings()
    settings["schema_version"] = SETTINGS_SCHEMA_VERSION + 1
    settings["cycle_data"]["FanPidEnabled"] = True
    original = copy.deepcopy(settings)

    assert _apply_shape_migrations(settings, SETTINGS_SCHEMA_VERSION) is False
    assert settings == original
