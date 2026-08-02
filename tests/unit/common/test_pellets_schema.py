"""Version 1 of the pellet database: today's shape, modeled exactly.

Every case here is a shape a live grill can hold. A v1 that is stricter than
the data in the field turns a restore into a 400 with no migration to blame it
on, so the traps below are ACCEPTANCE tests, not rejections -- each one is
behaviour verified in common/pellets_actions.py, not a guess.
"""

import copy
import json
from pathlib import Path

import pytest

from common.pellets_schema import (
    PELLETDB_SCHEMA_VERSION,
    PelletDbSchema,
    PelletDbValidationError,
    validate_pellet_db,
)

LIVE_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "pelletdb_live.json"


@pytest.fixture
def live():
    return json.loads(LIVE_FIXTURE.read_text())


def test_the_live_database_validates_and_round_trips(live):
    """Captured from a running grill, not authored from the model -- a fixture
    written from the model only proves the model agrees with itself.

    The stamp is the one key the model adds rather than reads back: the capture
    predates it, and an install in that state is exactly what
    _upgrade_pellets_in_store brings forward."""
    assert validate_pellet_db(copy.deepcopy(live)) == {**live, "schema_version": PELLETDB_SCHEMA_VERSION}


def test_est_usage_is_a_float(live):
    """defaults.py seeds int 0 and the field holds 171.198...; an int
    annotation would reject every grill that has ever cooked."""
    live["current"]["est_usage"] = 171.19809679985045
    assert validate_pellet_db(live)["current"]["est_usage"] == 171.19809679985045


def test_an_int_est_usage_is_accepted(live):
    """pellets_load_profile writes the literal 0."""
    live["current"]["est_usage"] = 0
    assert validate_pellet_db(live)["current"]["est_usage"] == 0


def test_a_deleted_log_value_is_accepted(live):
    """pellets_delete_profile rewrites each of the profile's log entries to the
    literal string rather than removing them, so log values are not keys of
    archive and must not be modeled as such."""
    live["log"]["2020-01-01 00:00:00"] = "deleted"
    assert validate_pellet_db(live)["log"]["2020-01-01 00:00:00"] == "deleted"


def test_a_brand_outside_the_vocabulary_is_accepted(live):
    """pellets_add_profile copies brand_name verbatim; brands is autocomplete,
    not an enumeration."""
    profile = next(iter(live["archive"].values()))
    profile["brand"] = "A Brand Nobody Listed"
    assert "A Brand Nobody Listed" not in live["brands"]
    assert validate_pellet_db(live)


def test_an_unbounded_rating_is_accepted_at_v1(live):
    """rating is action_data["rating"] with no bounds check at either writer.
    v1 records that; v2 is where it becomes 1..5."""
    next(iter(live["archive"].values()))["rating"] = 99
    assert validate_pellet_db(live)


def test_the_redundant_id_is_accepted_at_v1(live):
    profile_id, profile = next(iter(live["archive"].items()))
    assert profile["id"] == profile_id
    assert validate_pellet_db(live)


def test_a_loaded_profile_missing_from_the_archive_is_rejected(live):
    """The one invariant the code already enforces: pellets_delete_profile
    refuses to delete the loaded profile."""
    live["current"]["pelletid"] = "not-in-archive"
    with pytest.raises(PelletDbValidationError) as exc:
        validate_pellet_db(live)
    assert "archive" in str(exc.value)


def test_an_unmodeled_key_is_stripped_and_the_write_proceeds(live):
    """Self-healing repair, on the same terms as validate_settings_tree: an
    unmodeled key must never permanently block a save."""
    live["current"]["future_knob"] = 42
    live["totally_new_section"] = {"a": 1}

    repaired = validate_pellet_db(live)

    assert "future_knob" not in repaired["current"]
    assert "totally_new_section" not in repaired


def test_a_real_error_still_raises_even_beside_an_unmodeled_key(live):
    """Repair must never mask a genuine failure."""
    live["future_knob"] = 42
    live["current"]["hopper_level"] = "not a number"
    with pytest.raises(PelletDbValidationError):
        validate_pellet_db(live)


def test_the_control_process_write_path_accepts_a_live_database(ds, live):
    """controller/runtime/store.py calls write_pellet_db every 60s and at each
    mode end, updating exactly these two fields. The gate raises, so a shape it
    rejected would take down the control loop rather than a request."""
    from common.datastore_accessors import read_pellets_store, write_pellet_db

    write_pellet_db(live)

    stored = read_pellets_store()
    stored["current"]["est_usage"] = stored["current"]["est_usage"] + 12.5
    stored["current"]["hopper_level"] = 87
    write_pellet_db(stored)

    assert read_pellets_store()["current"]["hopper_level"] == 87


def test_raw_model_validate_rejects_an_unmodeled_key(live):
    """Self-healing lives ONLY in validate_pellet_db, exactly as it lives only
    in validate_settings_tree."""
    from pydantic import ValidationError

    live["current"]["future_knob"] = 42
    with pytest.raises(ValidationError):
        PelletDbSchema.model_validate(live, strict=True)
