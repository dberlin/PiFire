"""Schema v6 hands MPC's identification switch back to the shipped default."""

from copy import deepcopy

from common.defaults import default_settings
from common.settings_migration import _apply_shape_migrations, _clear_mpc_identification_choice
from common.settings_schema import SETTINGS_SCHEMA_VERSION


def _v5_settings(stored):
    return {
        "schema_version": 5,
        "controller": {
            "selected": "mpc",
            "config": {
                "mpc": {"enable_identification": stored, "C_c": 432.1, "policy": "nlp"},
                "pid": {"PB": 60.0, "cycle": 17},
                "pid_sp": {"PB": 51.0, "cycle": 23},
            },
        },
        "cycle_data": {"HoldCycleTime": 25, "u_min": 0.1, "u_max": 0.9},
        "globals": {"grill_name": "Learning Grill"},
    }


def test_the_switch_ships_on():
    """The default this migration exists to deliver.

    Asserted here rather than only where identification is used, because the
    migration below is pointless if the value it defers to is still off.
    """
    assert default_settings()["controller"]["config"]["mpc"]["enable_identification"] is True


def test_schema_version_six_drops_a_stored_identification_choice():
    settings = _v5_settings(False)

    assert _apply_shape_migrations(settings, SETTINGS_SCHEMA_VERSION) is True

    assert SETTINGS_SCHEMA_VERSION == 6
    assert settings == {
        "schema_version": 6,
        "controller": {
            "selected": "mpc",
            "config": {
                "mpc": {"C_c": 432.1, "policy": "nlp"},
                "pid": {"PB": 60.0, "cycle": 17},
                "pid_sp": {"PB": 51.0, "cycle": 23},
            },
        },
        "cycle_data": {"HoldCycleTime": 25, "u_min": 0.1, "u_max": 0.9},
        "globals": {"grill_name": "Learning Grill"},
    }


def test_a_stored_choice_goes_whichever_way_it_was_set():
    """Removal is not conditional on the stored value.

    An install that was already learning and one that was not both end up on
    the shipped default, which is the only way this migration can be described
    in one sentence -- and the two cases are otherwise indistinguishable to the
    operator, since both were reading the same word off the same switch.
    """
    for stored in (False, True):
        settings = _v5_settings(stored)
        assert _clear_mpc_identification_choice(settings) is True
        assert "enable_identification" not in settings["controller"]["config"]["mpc"]


def test_identification_removal_is_idempotent_and_preserves_other_settings():
    settings = _v5_settings(False)

    assert _clear_mpc_identification_choice(settings) is True
    once = deepcopy(settings)
    assert _clear_mpc_identification_choice(settings) is False
    assert settings == once
