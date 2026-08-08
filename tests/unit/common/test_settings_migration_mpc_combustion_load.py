"""Schema v5 removes obsolete affine MPC firing-rate bounds."""

from copy import deepcopy

from common.settings_migration import _apply_shape_migrations, _remove_mpc_affine_load_bounds
from common.settings_schema import SETTINGS_SCHEMA_VERSION


def _v4_settings():
    return {
        "schema_version": 4,
        "controller": {
            "selected": "mpc",
            "config": {
                "mpc": {"Q_min": 5.0, "Q_max": 100.0, "C_c": 432.1, "policy": "net"},
                "pid": {"PB": 60.0, "cycle": 17},
                "pid_sp": {"PB": 51.0, "cycle": 23},
            },
        },
        "cycle_data": {"HoldCycleTime": 25, "u_min": 0.1, "u_max": 0.9},
        "globals": {"grill_name": "Normalized Grill"},
    }


def test_later_schema_versions_preserve_the_v5_affine_load_cutover():
    settings = _v4_settings()

    assert _apply_shape_migrations(settings, SETTINGS_SCHEMA_VERSION) is True

    assert SETTINGS_SCHEMA_VERSION == 8
    assert settings == {
        "schema_version": 8,
        "controller": {
            "selected": "mpc",
            "config": {
                "mpc": {"C_c": 432.1, "policy": "net"},
                "pid": {"PB": 60.0, "cycle": 17},
                "pid_sp": {"PB": 51.0, "cycle": 23},
            },
        },
        "cycle_data": {"HoldCycleTime": 25, "u_min": 0.1, "u_max": 0.9},
        "globals": {"grill_name": "Normalized Grill"},
    }


def test_affine_load_bound_removal_is_idempotent_and_preserves_other_settings():
    settings = _v4_settings()

    assert _remove_mpc_affine_load_bounds(settings) is True
    once = deepcopy(settings)
    assert _remove_mpc_affine_load_bounds(settings) is False
    assert settings == once
