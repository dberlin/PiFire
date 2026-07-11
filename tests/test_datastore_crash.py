# tests/test_datastore_crash.py
import multiprocessing as mp
import os

import pytest

from common import datastore


def _write_then_kill(db):
	os.environ['PIFIRE_DB_PATH'] = db
	datastore._reset_for_tests(db)
	datastore.init()
	datastore.set_blob('settings:general', '{"committed": true}')
	os._exit(9)  # hard kill AFTER commit, before clean close


@pytest.fixture
def db(tmp_path):
	p = str(tmp_path / 't.db')
	os.environ['PIFIRE_DB_PATH'] = p
	yield p
	datastore._reset_for_tests(None)


def test_committed_survives_hard_kill(db):
	ctx = mp.get_context('spawn')
	p = ctx.Process(target=_write_then_kill, args=(db,))
	p.start()
	p.join()
	assert p.exitcode == 9
	datastore._reset_for_tests(db)
	datastore.init()
	assert datastore.get_blob('settings:general') == '{"committed": true}'  # WAL recovered
	assert datastore.connection().execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
