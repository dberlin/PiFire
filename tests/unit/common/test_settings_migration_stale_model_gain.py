"""Schema v10 returns MPC thermal parameters that are not a fit to the defaults.

An install predating the single-lump model carried `K_Q = 3.5`, correct for the
retired two-lump model and 100x too small for this one. The v9 cutover dropped
the keys the new model has no state for (`C_f`, `h_fc`) but left the gain
behind, so the model concluded the auger could not heat the grill: it asked for
a steady-state load of 48.7, clipped to 1.0, and held the auger at saturation
through a 76F overshoot of a 425F setpoint.

Which parameters are a fit is `model_is_identified`'s question, so the reset
asks it rather than restating it -- see controller/mpc_config.py.
"""

from copy import deepcopy

import pytest

from common.defaults import default_settings
from common.settings_migration import _apply_shape_migrations, _reset_uncalibrated_mpc_parameters
from common.settings_schema import SETTINGS_SCHEMA_VERSION
from controller.mpc_config import DEFAULT_MPC_CONFIG, FITTED_PARAMETER_KEYS

#: A pasted result of controller/update_mpc.py: its free set moved together.
FITTED_PASTE = {"K_Q": 412.0, "C_c": 268.0, "theta": 63.5}


def _v9_settings(**overrides):
    """A stamped-v9 install -- the shape the stale gain survived into."""
    settings = default_settings()
    settings["schema_version"] = 9
    settings["controller"]["selected"] = "mpc"
    settings["controller"]["config"]["mpc"].update(overrides)
    return settings


def _mpc(settings):
    return settings["controller"]["config"]["mpc"]


def test_the_two_lump_heat_gain_is_returned_to_the_shipped_default():
    """The reported install: one stale parameter, and only it moves."""
    settings = _v9_settings(K_Q=3.5)
    expected_mpc = deepcopy(_mpc(default_settings()))
    expected_pid = deepcopy(settings["controller"]["config"]["pid"])

    assert _apply_shape_migrations(settings, SETTINGS_SCHEMA_VERSION) is True

    assert SETTINGS_SCHEMA_VERSION == 11
    assert settings["schema_version"] == 11
    assert _mpc(settings)["K_Q"] == 350.0
    assert _mpc(settings) == expected_mpc
    assert settings["controller"]["config"]["pid"] == expected_pid


def test_a_completed_fit_survives_byte_identical():
    """Three parameters moved together is somebody's grill, not a leftover."""
    settings = _v9_settings(**FITTED_PASTE)
    untouched = deepcopy(settings)

    assert _reset_uncalibrated_mpc_parameters(settings) is False
    assert settings == untouched

    assert _apply_shape_migrations(settings, SETTINGS_SCHEMA_VERSION) is True
    untouched["schema_version"] = SETTINGS_SCHEMA_VERSION
    assert settings == untouched
    assert {key: _mpc(settings)[key] for key in FITTED_PARAMETER_KEYS} == FITTED_PASTE


def test_a_configuration_already_on_the_defaults_is_unchanged():
    settings = _v9_settings()
    untouched = deepcopy(settings)

    assert _reset_uncalibrated_mpc_parameters(settings) is False
    assert settings == untouched


@pytest.mark.parametrize("stale", FITTED_PARAMETER_KEYS)
def test_two_of_the_three_parameters_are_not_a_fit_either(stale):
    """A subset diff is a stale or hand-edited value, whichever subset it is."""
    settings = _v9_settings(**{key: value for key, value in FITTED_PASTE.items() if key != stale})

    assert _reset_uncalibrated_mpc_parameters(settings) is True
    assert {key: _mpc(settings)[key] for key in FITTED_PARAMETER_KEYS} == {
        key: DEFAULT_MPC_CONFIG[key] for key in FITTED_PARAMETER_KEYS
    }


def test_a_parameter_the_install_never_stored_is_not_written_in():
    """Absent already means the shipped default; the reset adds no keys."""
    settings = _v9_settings(K_Q=3.5)
    _mpc(settings).pop("theta")

    assert _reset_uncalibrated_mpc_parameters(settings) is True
    assert "theta" not in _mpc(settings)
    assert _mpc(settings)["K_Q"] == DEFAULT_MPC_CONFIG["K_Q"]


def test_the_reset_is_idempotent_and_preserves_other_settings():
    settings = _v9_settings(K_Q=3.5)

    assert _reset_uncalibrated_mpc_parameters(settings) is True
    once = deepcopy(settings)

    assert _reset_uncalibrated_mpc_parameters(settings) is False
    assert settings == once


def test_the_reset_leaves_malformed_trees_to_normal_repair():
    malformed_trees = (
        {},
        {"controller": None},
        {"controller": {"config": None}},
        {"controller": {"config": {"mpc": None}}},
        {"controller": {"config": {"mpc": []}}},
    )

    for settings in malformed_trees:
        original = deepcopy(settings)

        assert _reset_uncalibrated_mpc_parameters(settings) is False
        assert settings == original
