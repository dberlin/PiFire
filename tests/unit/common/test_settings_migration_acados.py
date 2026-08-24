"""Schema v9 normalizes persisted MPC settings for the acados grey-box runtime."""

from copy import deepcopy

import pytest

from common.settings_migration import _apply_shape_migrations
from common.settings_schema import SETTINGS_SCHEMA_VERSION


RETIRED_MPC_KEYS = {
    "policy",
    "policy_net_path",
    "t_step",
    "n_delay",
    "C_f",
    "h_fc",
    "feed_forward",
    "enable_grey_box",
    "mhe_horizon",
    "pw_state",
    "pw_dist",
    "px_state",
    "px_dist",
    "r_meas",
    "Q_min",
    "Q_max",
    "log_data",
    "log_path",
}

#: C_c, theta and K_Q all differ from their shipped values, so this is a
#: completed fit and the v10 reset leaves it alone -- what survives v9 here is
#: therefore the whole of it, not the part v10 would have written back.
RETAINED_MPC = {
    "n_horizon": 17,
    "control_period": 7.5,
    "Q_w": 2.25,
    "R_dQ": 0.35,
    "C_c": 411.5,
    "h_amb": 0.73,
    "T_amb": 14.0,
    "theta": 61.0,
    "K_Q": 387.0,
    "sigma": 2.1e-9,
    "estimator": "ekf",
    "fan_min_pct": 33.0,
    "fan_max_pct": 88.0,
    "enable_fan_input": True,
    "est_q_temp": 0.025,
    "est_q_dist": 0.075,
    "est_r_meas": 0.06,
    "enable_identification": False,
    "enable_online_adaptation": True,
}


def _legacy_mpc(*, estimator="mhe", horizon=17):
    retired = {
        "policy": "net",
        "policy_net_path": "/data/custom-policy.npz",
        "t_step": 12.5,
        "n_delay": 3,
        "C_f": 19.0,
        "h_fc": 1.7,
        "feed_forward": True,
        "enable_grey_box": False,
        "mhe_horizon": 12,
        "pw_state": 9.0,
        "pw_dist": 0.4,
        "px_state": 1.2,
        "px_dist": 0.3,
        "r_meas": 0.09,
        "Q_min": 4.0,
        "Q_max": 91.0,
        "log_data": True,
        "log_path": "/mnt/usb/mpc.csv",
    }
    return {
        "schema_version": 8,
        "controller": {
            "selected": "mpc",
            "config": {
                "mpc": {**RETAINED_MPC, **retired, "estimator": estimator, "n_horizon": horizon},
                "pid": {"PB": 57.0, "cycle": 19},
            },
        },
    }


def _migrate(settings):
    assert _apply_shape_migrations(settings, SETTINGS_SCHEMA_VERSION) is True
    return settings["controller"]["config"]["mpc"]


def test_schema_nine_migration_is_idempotent_and_preserves_non_mpc_settings():
    settings = _legacy_mpc()
    expected_pid = deepcopy(settings["controller"]["config"]["pid"])

    migrated = _migrate(settings)
    once = deepcopy(settings)

    assert SETTINGS_SCHEMA_VERSION == 11
    assert settings["schema_version"] == 11
    assert settings["controller"]["selected"] == "mpc"
    assert settings["controller"]["config"]["pid"] == expected_pid
    assert _apply_shape_migrations(settings, SETTINGS_SCHEMA_VERSION) is False
    assert settings == once
    assert migrated == RETAINED_MPC


@pytest.mark.parametrize(
    ("stored", "expected"),
    (("mhe", "ekf"), ("ekf", "ekf"), ("kf", "kf")),
)
def test_estimator_selection_normalizes_only_mhe(stored, expected):
    settings = _legacy_mpc(estimator=stored)

    assert _migrate(settings)["estimator"] == expected


@pytest.mark.parametrize(
    ("stored", "expected"),
    ((-10, 5), (4, 5), (5, 5), (17, 17), (24, 24), (25, 24), (99, 24)),
)
def test_prediction_horizon_is_clamped_to_the_supported_native_range(stored, expected):
    settings = _legacy_mpc(estimator="ekf", horizon=stored)

    assert _migrate(settings)["n_horizon"] == expected


def test_integral_float_horizon_is_normalized_before_clamping_and_migration_is_idempotent():
    settings = _legacy_mpc(estimator="ekf", horizon=25.0)

    migrated = _migrate(settings)
    once = deepcopy(settings)

    assert migrated["n_horizon"] == 24
    assert type(migrated["n_horizon"]) is int
    assert _apply_shape_migrations(settings, SETTINGS_SCHEMA_VERSION) is False
    assert settings == once


@pytest.mark.parametrize("stored", (True, 25.5, "25", None))
def test_non_integral_or_non_numeric_horizon_is_preserved_deterministically(stored):
    settings = _legacy_mpc(estimator="ekf", horizon=stored)

    migrated = _migrate(settings)
    once = deepcopy(settings)

    assert migrated["n_horizon"] == stored
    assert type(migrated["n_horizon"]) is type(stored)
    assert _apply_shape_migrations(settings, SETTINGS_SCHEMA_VERSION) is False
    assert settings == once


def test_acados_cutover_removes_every_policy_structural_linear_and_mhe_setting():
    settings = _legacy_mpc(estimator="kf")

    migrated = _migrate(settings)

    assert RETIRED_MPC_KEYS.isdisjoint(migrated)
    assert migrated == {**RETAINED_MPC, "estimator": "kf"}
