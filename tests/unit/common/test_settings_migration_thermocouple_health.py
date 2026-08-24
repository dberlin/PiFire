"""Schema v11 records the thermocouple-health settings shape cutover."""

from copy import deepcopy

from common.settings_migration import (
    _SHAPE_MIGRATIONS,
    _apply_shape_migrations,
)
from common.settings_schema import SETTINGS_SCHEMA_VERSION


def test_v11_records_the_shape_change_without_moving_data():
    settings = {"schema_version": 10, "sentinel": {"preserved": True}}
    untouched = deepcopy(settings)

    assert SETTINGS_SCHEMA_VERSION == 11
    migration = dict(_SHAPE_MIGRATIONS)[11]
    assert migration.__name__ == "_acknowledge_thermocouple_health_settings"
    assert migration(settings) is False
    assert settings == untouched

    assert _apply_shape_migrations(settings, SETTINGS_SCHEMA_VERSION) is True
    untouched["schema_version"] = 11
    assert settings == untouched
