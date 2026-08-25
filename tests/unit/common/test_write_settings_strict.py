"""write_settings() is the hard-strict enforcement gate.

Before this gate existed, the writer matrix used validate_settings_tree() as
a test ORACLE on the side -- write_settings() itself never validated. This
module pins the enforcement: write_settings() calls validate_settings_tree()
as its first statement, raising SettingsValidationError (and leaving the
store untouched) on any invalid tree, both for a normal write and the
flush/factory-reset path.
"""

import copy

import pytest

from common.defaults import default_settings
from common.persistence.runtime import read_settings, write_settings, write_settings_store
from common.settings_schema import SettingsValidationError


@pytest.fixture
def store(ds):
    write_settings_store(default_settings())
    yield ds


def test_write_settings_rejects_invalid_tree_atomically(store):
    before = read_settings()
    bad = copy.deepcopy(before)
    bad["safety"]["maxtemp"] = "nope"
    with pytest.raises(SettingsValidationError):
        write_settings(bad)
    assert read_settings() == before  # untouched


def test_write_settings_rejects_invalid_tree_atomically_flush_path(store):
    """Same gate applies on the flush/factory-reset path (write_settings() has
    no separate branch for it -- both callers go through the identical
    validate-then-persist statement), not just the ordinary "edit one field"
    path exercised above."""
    before = read_settings()
    bad = copy.deepcopy(before)
    bad["pwm"]["min_duty_cycle"] = "also nope"
    with pytest.raises(SettingsValidationError):
        write_settings(bad)
    assert read_settings() == before


def test_write_settings_error_message_contains_offending_path(store):
    bad = copy.deepcopy(read_settings())
    bad["safety"]["maxtemp"] = "nope"
    with pytest.raises(SettingsValidationError) as ei:
        write_settings(bad)
    assert any("safety.maxtemp" in m for m in ei.value.errors)


def test_write_settings_persists_normalized_dump_on_success(store):
    """write_settings() persists validate_settings_tree()'s NORMALIZED return
    value, not the caller's raw dict -- so callers relying on type coercion
    get it (int-as-string form fields upstream should already be coerced by
    callers, but this confirms the store round-trips the normalized shape)."""
    settings = read_settings()
    settings["globals"]["grill_name"] = "Strict Write"
    write_settings(settings)
    assert read_settings()["globals"]["grill_name"] == "Strict Write"
