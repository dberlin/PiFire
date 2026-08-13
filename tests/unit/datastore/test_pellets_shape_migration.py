"""The pellet database's own stamp, on the same terms as the settings tree's.

One version and digest per persisted blob, because each shape moves on its own
schedule rather than coupling unrelated migrations.
"""

import copy
import json
from pathlib import Path

import pytest

from common import datastore, pellets_schema
from common.persistence.runtime import read_pellets_store, write_pellets_store
from common.defaults import default_pellets
from common.web_contracts.control import PELLETDB_SCHEMA_VERSION

LIVE_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "pelletdb_live.json"


def _unstamped_live_db():
    db = json.loads(LIVE_FIXTURE.read_text())
    db.pop("schema_version", None)
    write_pellets_store(db)
    return db


def test_a_fresh_database_is_stamped_current():
    assert default_pellets()["schema_version"] == PELLETDB_SCHEMA_VERSION


def test_an_unstamped_database_ends_stamped_current(ds):
    _unstamped_live_db()

    datastore._upgrade_pellets_in_store()

    assert read_pellets_store()["schema_version"] == PELLETDB_SCHEMA_VERSION


def test_v3_shape_step_requires_no_data_rewrite():
    db = copy.deepcopy(default_pellets())
    before = copy.deepcopy(db)
    migrate = dict(pellets_schema._PELLET_MIGRATIONS).get(3)

    assert migrate is not None
    assert migrate(db) is False
    assert db == before


def test_a_current_database_runs_no_step(ds, monkeypatch):
    ran = []
    monkeypatch.setattr(pellets_schema, "_PELLET_MIGRATIONS", [(1, lambda db: ran.append(1) or False)])
    write_pellets_store(copy.deepcopy(default_pellets()))

    datastore._upgrade_pellets_in_store()

    assert ran == []


def test_a_stamp_from_the_future_runs_nothing_and_is_not_rewound(ds, monkeypatch):
    ran = []
    monkeypatch.setattr(pellets_schema, "_PELLET_MIGRATIONS", [(1, lambda db: ran.append(1) or True)])
    db = copy.deepcopy(default_pellets())
    db["schema_version"] = PELLETDB_SCHEMA_VERSION + 5
    write_pellets_store(db)

    datastore._upgrade_pellets_in_store()

    assert ran == []
    assert read_pellets_store()["schema_version"] == PELLETDB_SCHEMA_VERSION + 5


def test_the_stamp_is_not_written_when_a_step_raises(ds, monkeypatch):
    def _explode(db):
        raise RuntimeError("migration blew up")

    monkeypatch.setattr(pellets_schema, "_PELLET_MIGRATIONS", [(1, _explode)])
    _unstamped_live_db()

    with pytest.raises(RuntimeError):
        datastore._upgrade_pellets_in_store()

    assert "schema_version" not in read_pellets_store()


def test_running_the_chain_twice_is_identical_to_running_it_once(ds):
    _unstamped_live_db()

    datastore._upgrade_pellets_in_store()
    once = copy.deepcopy(read_pellets_store())
    datastore._upgrade_pellets_in_store()

    assert read_pellets_store() == once


def test_init_stamps_a_pre_stamp_store(ds):
    _unstamped_live_db()

    datastore.init()

    assert read_pellets_store()["schema_version"] == PELLETDB_SCHEMA_VERSION
