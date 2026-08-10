"""One test per v2 migration, each asserting the v1 input and the v2 output.

Existing data is never rejected for a legacy value -- that is the whole split
this version exists to express: strict for new writes, migrated for what is
already stored.
"""

import copy
import json
from pathlib import Path

from common.pellets_schema import _migrate_pellets_to_v2, validate_pellet_db
from common.web_contracts.control import PELLETDB_SCHEMA_VERSION

LIVE_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "pelletdb_live.json"


def _v1_db():
    """The live v1 database, with the traps of the version 1 shape present."""
    db = json.loads(LIVE_FIXTURE.read_text())
    db.pop("schema_version", None)
    profile_id = next(iter(db["archive"]))
    db["archive"]["gone"] = {"id": "gone", "brand": "Custom", "wood": "Oak", "rating": 4, "comments": ""}
    db["log"]["2026-07-12 10:00:00"] = "deleted"
    db["log"]["2026-07-13 11:30:45"] = profile_id
    return db


def test_a_migrated_database_validates_at_v2():
    db = _v1_db()
    assert _migrate_pellets_to_v2(db) is True
    assert validate_pellet_db(db)


def test_rating_is_coerced_and_clamped():
    db = _v1_db()
    db["archive"]["gone"]["rating"] = "4"
    profile_id = next(iter(db["archive"]))
    db["archive"][profile_id]["rating"] = 99

    _migrate_pellets_to_v2(db)

    assert db["archive"]["gone"]["rating"] == 4
    assert db["archive"][profile_id]["rating"] == 5


def test_a_rating_below_range_is_clamped_up():
    db = _v1_db()
    db["archive"]["gone"]["rating"] = 0

    _migrate_pellets_to_v2(db)

    assert db["archive"]["gone"]["rating"] == 1


def test_the_redundant_id_is_dropped():
    db = _v1_db()

    _migrate_pellets_to_v2(db)

    assert all("id" not in profile for profile in db["archive"].values())


def test_a_mismatched_id_is_logged_rather_than_silently_resolved(monkeypatch):
    logged = []
    monkeypatch.setattr("common.pellets_schema.write_log", logged.append)
    db = _v1_db()
    db["archive"]["gone"]["id"] = "something-else"

    _migrate_pellets_to_v2(db)

    assert any("something-else" in line for line in logged)


def test_log_keys_become_epoch_milliseconds():
    db = _v1_db()
    original = sorted(db["log"])

    _migrate_pellets_to_v2(db)

    assert len(db["log"]) == len(original)
    assert all(key.isdigit() for key in db["log"])


def test_log_keys_keep_their_order():
    db = _v1_db()
    expected = [db["log"][key] for key in sorted(db["log"])]

    _migrate_pellets_to_v2(db)

    migrated = [db["log"][key] for key in sorted(db["log"], key=int)]
    assert [entry["pelletid"] or "deleted" for entry in migrated] == expected


def test_the_deleted_sentinel_becomes_a_tombstone():
    db = _v1_db()

    _migrate_pellets_to_v2(db)

    entries = list(db["log"].values())
    assert {"pelletid": None, "deleted": True} in entries
    assert any(entry["deleted"] is False and entry["pelletid"] for entry in entries)


def test_running_the_migration_twice_is_identical_to_running_it_once():
    db = _v1_db()

    _migrate_pellets_to_v2(db)
    once = copy.deepcopy(db)
    _migrate_pellets_to_v2(db)

    assert db == once


def test_the_store_upgrade_carries_a_v1_database_to_v2(ds):
    from common import datastore
    from common.datastore_accessors import read_pellets_store, write_pellets_store

    write_pellets_store(_v1_db())

    datastore._upgrade_pellets_in_store()

    stored = read_pellets_store()
    assert stored["schema_version"] == PELLETDB_SCHEMA_VERSION
    assert all(key.isdigit() for key in stored["log"])
    assert validate_pellet_db(stored)
