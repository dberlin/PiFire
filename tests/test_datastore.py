import json
import os
import sqlite3

import pytest

from common import datastore


@pytest.fixture
def ds(tmp_path):
	datastore._reset_for_tests(str(tmp_path / 't.db'))
	datastore.init()
	yield datastore
	datastore._reset_for_tests(None)


def test_pragmas_applied(ds):
	conn = ds.connection()
	assert conn.execute('PRAGMA journal_mode').fetchone()[0].lower() == 'wal'
	assert conn.execute('PRAGMA synchronous').fetchone()[0] == 1  # NORMAL
	assert conn.execute('PRAGMA foreign_keys').fetchone()[0] == 1


def test_schema_tables_exist(ds):
	names = {r[0] for r in ds.connection().execute("SELECT name FROM sqlite_master WHERE type='table'")}
	for t in [
		'kv',
		'history',
		'metrics',
		'logs',
		'queue_control_write',
		'queue_systemq',
		'queue_systemo',
		'queue_displayq',
		'queue_autotune',
		'list_warnings',
		'list_users_connected',
	]:
		assert t in names, t


def test_init_idempotent(ds):
	ds.init()  # second call must not raise
	assert ds.connection().execute('PRAGMA user_version').fetchone()[0] >= 1


def test_kv_check_rejects_non_json(ds):
	with pytest.raises(sqlite3.IntegrityError):
		ds.execute_write("INSERT INTO kv(key,value) VALUES('x','{not json')")


def test_transaction_rolls_back_on_error(ds):
	with pytest.raises(RuntimeError):
		with ds.transaction() as conn:
			conn.execute("INSERT INTO kv(key,value) VALUES('a','1')")
			raise RuntimeError('boom')
	assert ds.connection().execute("SELECT COUNT(*) FROM kv WHERE key='a'").fetchone()[0] == 0
