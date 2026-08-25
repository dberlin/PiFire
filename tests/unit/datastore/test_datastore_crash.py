# tests/unit/datastore/test_datastore_crash.py
import multiprocessing as mp
import os

import pytest

from common import datastore

#: A key no migration owns. The claim under test is that a committed write
#: survives a hard kill, which holds for any key -- and `settings:general` is
#: brought forward and restamped by init(), so a sentinel stored there would
#: be measuring the upgrade path instead of the WAL.
SENTINEL_KEY = "test:crash_sentinel"


def _write_then_kill(db):
    os.environ["PIFIRE_DB_PATH"] = db
    datastore._reset_for_tests(db)
    datastore.init()
    datastore.set_blob(SENTINEL_KEY, '{"committed": true}')
    os._exit(9)  # hard kill AFTER commit, before clean close


@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / "t.db")
    os.environ["PIFIRE_DB_PATH"] = p
    yield p
    datastore._reset_for_tests(None)


def test_committed_survives_hard_kill(db):
    ctx = mp.get_context("spawn")
    p = ctx.Process(target=_write_then_kill, args=(db,))
    p.start()
    p.join()
    assert p.exitcode == 9
    datastore._reset_for_tests(db)
    datastore.init()
    assert datastore.get_blob(SENTINEL_KEY) == '{"committed": true}'  # WAL recovered
    assert datastore.connection().execute("PRAGMA integrity_check").fetchone()[0] == "ok"
