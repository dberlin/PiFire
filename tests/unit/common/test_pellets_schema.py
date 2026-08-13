"""Version 2 of the pellet database: the sane shape.

The writers are in scope, so the shape is allowed to be sane. The split is by
TIME, not by strictness -- new writes are validated strictly here, and data an
install already holds is migrated rather than rejected (see
test_pellets_migration_v2.py, which is where the live v1 fixture went).
"""

import copy

import pytest

from common.pellets_schema import PelletDbValidationError, validate_pellet_db
from common.web_contracts.control import PelletDbSchema


def _v2_db():
    """A v2-shaped database. The live fixture is v1; the migration is what
    carries one to the other, and these tests are about the SHAPE only."""
    return {
        "schema_version": 2,
        "current": {
            "pelletid": "p1",
            "hopper_level": 100,
            "date_loaded": "2026-07-11 09:03:26",
            "est_usage": 171.19809679985045,
        },
        "archive": {"p1": {"brand": "Generic", "wood": "Alder", "rating": 4, "comments": "c"}},
        "log": {"1783775006000": {"pelletid": "p1", "deleted": False}},
        "brands": ["Generic", "Custom"],
        "woods": ["Alder"],
        "lastupdated": {"time": 1783775006},
    }


def test_the_v2_shape_validates_and_round_trips():
    db = _v2_db()
    assert validate_pellet_db(copy.deepcopy(db)) == db


def test_a_rating_out_of_range_is_rejected():
    db = _v2_db()
    db["archive"]["p1"]["rating"] = 99
    with pytest.raises(PelletDbValidationError) as exc:
        validate_pellet_db(db)
    assert "rating" in str(exc.value)


def test_a_redundant_id_is_not_a_modeled_field():
    """It repeated its own dict key and nothing enforced the agreement. An
    unmodeled key is stripped by the repair pass, not rejected."""
    db = _v2_db()
    db["archive"]["p1"]["id"] = "p1"
    assert "id" not in validate_pellet_db(db)["archive"]["p1"]


def test_a_tombstone_log_entry_validates():
    db = _v2_db()
    db["log"]["1783775007000"] = {"pelletid": None, "deleted": True}
    assert validate_pellet_db(db)["log"]["1783775007000"]["deleted"] is True


def test_a_non_numeric_log_key_is_rejected():
    """The key format is enforced rather than hoped: a second-resolution
    timestamp string is exactly what v2 exists to stop storing."""
    db = _v2_db()
    db["log"]["2026-07-11 09:03:26"] = {"pelletid": "p1", "deleted": False}
    with pytest.raises(PelletDbValidationError):
        validate_pellet_db(db)


def test_the_in_band_deleted_sentinel_is_rejected():
    db = _v2_db()
    db["log"]["1783775008000"] = "deleted"
    with pytest.raises(PelletDbValidationError):
        validate_pellet_db(db)


def test_a_brand_outside_the_vocabulary_is_still_accepted():
    """The one legacy looseness that is a feature: an operator naming a bag the
    vocabulary has not heard of is the normal case, not an error."""
    db = _v2_db()
    db["archive"]["p1"]["brand"] = "A Brand Nobody Listed"
    assert validate_pellet_db(db)


def test_est_usage_is_still_a_float():
    db = _v2_db()
    db["current"]["est_usage"] = 0
    assert validate_pellet_db(db)["current"]["est_usage"] == 0


def test_a_loaded_profile_missing_from_the_archive_is_rejected():
    """Rejected at BOTH versions. pellets_delete_profile refuses to delete the
    loaded profile, so this is the one invariant the code already enforces."""
    db = _v2_db()
    db["current"]["pelletid"] = "not-in-archive"
    with pytest.raises(PelletDbValidationError) as exc:
        validate_pellet_db(db)
    assert "archive" in str(exc.value)


def test_an_unmodeled_key_is_stripped_and_the_write_proceeds():
    """Self-healing repair, on the same terms as validate_settings_tree: an
    unmodeled key must never permanently block a save."""
    db = _v2_db()
    db["current"]["future_knob"] = 42
    db["totally_new_section"] = {"a": 1}

    repaired = validate_pellet_db(db)

    assert "future_knob" not in repaired["current"]
    assert "totally_new_section" not in repaired


def test_a_real_error_still_raises_even_beside_an_unmodeled_key():
    """Repair must never mask a genuine failure."""
    db = _v2_db()
    db["future_knob"] = 42
    db["current"]["hopper_level"] = "not a number"
    with pytest.raises(PelletDbValidationError):
        validate_pellet_db(db)


def test_raw_model_validate_rejects_an_unmodeled_key():
    """Self-healing lives ONLY in validate_pellet_db, exactly as it lives only
    in validate_settings_tree."""
    from pydantic import ValidationError

    db = _v2_db()
    db["current"]["future_knob"] = 42
    with pytest.raises(ValidationError):
        PelletDbSchema.model_validate(db, strict=True)


def test_the_control_process_write_path_accepts_a_stored_database(ds):
    """controller/runtime/store.py calls write_pellet_db every 60s and at each
    mode end, updating exactly these two fields. The gate raises, so a shape it
    rejected would take down the control loop rather than a request."""
    from common.persistence.runtime import read_pellets_store, write_pellet_db

    write_pellet_db(_v2_db())

    stored = read_pellets_store()
    stored["current"]["est_usage"] = stored["current"]["est_usage"] + 12.5
    stored["current"]["hopper_level"] = 87
    write_pellet_db(stored)

    assert read_pellets_store()["current"]["hopper_level"] == 87
