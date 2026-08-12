"""What the writers must produce now that the shape is enforced at the door.

The same-millisecond test is the regression test for a bug the modeling
surfaced: the log dict was keyed at second resolution, so two loads inside one
second collided on the key and one entry was silently lost.
"""

import pytest

from common import pellets_actions
from common.datastore_accessors import read_pellets_store, write_pellets_store
from common.defaults import default_pellets


@pytest.fixture
def db(ds, monkeypatch):
    monkeypatch.setattr(pellets_actions, "enqueue_control_delta", lambda *a, **k: None)
    monkeypatch.setattr(pellets_actions, "backup_pellet_db", lambda *a, **k: None)
    pelletdb = default_pellets()
    write_pellets_store(pelletdb)
    return pelletdb


def _frozen_clock(monkeypatch, millis):
    """Pin the clock so both writes land in the same millisecond -- the
    strongest form of the collision, and one that does not depend on how fast
    this machine happens to be. monkeypatch restores the stdlib attribute when
    the test ends."""
    monkeypatch.setattr(pellets_actions.time, "time", lambda: millis / 1000)


def test_two_loads_in_the_same_millisecond_are_both_recorded(db, monkeypatch):
    profile_id = next(iter(db["archive"]))
    # Frozen only after the fixture has seeded its own entry at the real clock.
    _frozen_clock(monkeypatch, 1783775006000)
    before = set(read_pellets_store()["log"])

    pellets_actions.pellets_load_profile(read_pellets_store(), {"profile": profile_id})
    pellets_actions.pellets_load_profile(read_pellets_store(), {"profile": profile_id})

    log = read_pellets_store()["log"]
    # COUNT first: a key-format assertion would fail for the wrong reason if
    # the entry were dropped.
    assert len(log) == len(before) + 2
    assert sorted(set(log) - before) == ["1783775006000", "1783775006001"]


def test_a_load_writes_a_tombstone_shaped_entry(db, monkeypatch):
    _frozen_clock(monkeypatch, 1783775006000)
    profile_id = next(iter(db["archive"]))

    pellets_actions.pellets_load_profile(read_pellets_store(), {"profile": profile_id})

    assert read_pellets_store()["log"]["1783775006000"] == {"pelletid": profile_id, "deleted": False}


def test_deleting_a_profile_writes_a_tombstone(db):
    profile_id = next(iter(db["archive"]))
    pelletdb = read_pellets_store()
    pelletdb["archive"]["other"] = {"brand": "Custom", "wood": "Oak", "rating": 3, "comments": ""}
    pelletdb["log"]["1783775009000"] = {"pelletid": "other", "deleted": False}
    write_pellets_store(pelletdb)

    pellets_actions.pellets_delete_profile(read_pellets_store(), {"profile": "other"})

    assert read_pellets_store()["log"]["1783775009000"] == {"pelletid": None, "deleted": True}
    assert profile_id in read_pellets_store()["archive"]


def test_adding_a_profile_stores_no_redundant_id(db):
    pellets_actions.pellets_add_profile(
        read_pellets_store(),
        {"brand_name": "Acme", "wood_type": "Oak", "rating": 4, "comments": "", "add_and_load": False},
    )

    assert all("id" not in profile for profile in read_pellets_store()["archive"].values())


def test_an_unseen_brand_and_wood_join_the_vocabularies(db):
    """The vocabularies are what the lists are visibly for; a bag they have not
    heard of is the normal case."""
    pellets_actions.pellets_add_profile(
        read_pellets_store(),
        {"brand_name": "Acme", "wood_type": "Ironbark", "rating": 4, "comments": "", "add_and_load": False},
    )

    stored = read_pellets_store()
    assert "Acme" in stored["brands"]
    assert "Ironbark" in stored["woods"]


@pytest.mark.parametrize("bad", ["nope", 0, 99, None])
def test_a_rating_outside_one_to_five_is_refused_at_the_door(db, bad):
    """New writes are validated strictly; the store must be untouched."""
    before = read_pellets_store()

    resp = pellets_actions.pellets_add_profile(
        read_pellets_store(),
        {"brand_name": "Acme", "wood_type": "Oak", "rating": bad, "comments": "", "add_and_load": False},
    )

    assert resp["result"] == "Error"
    assert "rating" in resp["message"]
    assert read_pellets_store() == before


def test_editing_a_profile_refuses_a_bad_rating(db):
    profile_id = next(iter(db["archive"]))

    resp = pellets_actions.pellets_edit_profile(
        read_pellets_store(),
        {"profile": profile_id, "brand_name": "Acme", "wood_type": "Oak", "rating": 12, "comments": ""},
    )

    assert resp["result"] == "Error"
    assert read_pellets_store()["archive"][profile_id]["rating"] != 12
