"""Schema v4 removes only retired MPC CSV logging settings."""

from copy import deepcopy

from common.settings_migration import _apply_shape_migrations, _remove_retired_mpc_logging_settings
from common.settings_schema import SETTINGS_SCHEMA_VERSION


#: A completed fit -- update_mpc's free set, all moved together. The surviving
#: neighbour has to be one, because v10 returns anything less to the defaults,
#: and a neighbour whose expected value IS the default could not tell a
#: preserved setting apart from a deleted one.
FITTED_PASTE = {"K_Q": 412.0, "C_c": 268.0, "theta": 63.5}


def _v3_settings():
    return {
        "schema_version": 3,
        "controller": {
            "selected": "mpc",
            "config": {
                "mpc": {
                    "log_data": True,
                    "log_path": "/mnt/usb/calibration.csv",
                    **FITTED_PASTE,
                    "policy": "net",
                },
                "pid": {"PB": 60.0},
            },
        },
        "globals": {"grill_name": "Trace Grill"},
    }


def test_later_schema_versions_preserve_the_v4_logging_cutover():
    settings = _v3_settings()

    assert _apply_shape_migrations(settings, SETTINGS_SCHEMA_VERSION) is True

    assert SETTINGS_SCHEMA_VERSION == 10
    assert settings == {
        "schema_version": 10,
        "controller": {
            "selected": "mpc",
            "config": {
                "mpc": dict(FITTED_PASTE),
                "pid": {"PB": 60.0},
            },
        },
        "globals": {"grill_name": "Trace Grill"},
    }


def test_retired_mpc_logging_removal_is_idempotent():
    settings = _v3_settings()

    assert _remove_retired_mpc_logging_settings(settings) is True
    once = deepcopy(settings)

    assert _remove_retired_mpc_logging_settings(settings) is False
    assert settings == once


def test_retired_mpc_logging_migration_leaves_malformed_trees_to_normal_repair():
    malformed_trees = (
        {},
        {"controller": None},
        {"controller": {"config": None}},
        {"controller": {"config": {"mpc": None}}},
        {"controller": {"config": {"mpc": []}}},
    )

    for settings in malformed_trees:
        original = deepcopy(settings)

        assert _remove_retired_mpc_logging_settings(settings) is False
        assert settings == original
