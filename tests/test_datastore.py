import json
import logging
import os
import sqlite3
import subprocess

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


def test_schema_lazy_without_init(tmp_path):
	"""Regression test: accessors must work on a fresh DB even without calling
	init() first, matching Valkey's always-available semantics."""
	datastore._reset_for_tests(str(tmp_path / 'fresh.db'))
	try:
		datastore.set_blob('k', '{"a":1}')
		assert datastore.get_blob('k') == '{"a":1}'

		names = {r[0] for r in datastore.connection().execute("SELECT name FROM sqlite_master WHERE type='table'")}
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
	finally:
		datastore._reset_for_tests(None)


def test_reset_for_tests_restores_db_path_on_none(tmp_path):
	"""Regression test: _reset_for_tests(None) restores original DB_PATH."""
	original_db_path = datastore.DB_PATH
	temp_db_path = str(tmp_path / 'temp.db')

	# Set to temp path
	datastore._reset_for_tests(temp_db_path)
	assert datastore.DB_PATH == temp_db_path

	# Reset to None should restore original
	datastore._reset_for_tests(None)
	assert datastore.DB_PATH == original_db_path
	assert datastore.DB_PATH.endswith('pifire.db')


def test_blob_roundtrip_and_missing(ds):
	assert ds.get_blob('k') is None  # missing -> None (matches Valkey)
	ds.set_blob('k', '{"a": 1}')
	assert ds.get_blob('k') == '{"a": 1}'
	assert ds.exists_blob('k') is True
	ds.set_blob('k', '{"a": 2}')  # overwrite
	assert ds.get_blob('k') == '{"a": 2}'
	ds.delete_blob('k')
	assert ds.get_blob('k') is None
	assert ds.exists_blob('k') is False


def test_log_handler_and_read(ds):
	from common.sqlite_log_handler import SqliteLogHandler

	logger = logging.getLogger('t_events')
	logger.setLevel(logging.INFO)
	logger.addHandler(SqliteLogHandler('events'))
	logger.info('first')
	logger.info('second')
	assert ds.read_log('events', num=1) == ['second']  # newest-first, limited
	assert ds.read_log('events') == ['second', 'first']
	ds.clear_log('events')
	assert ds.read_log('events') == []


def test_retry_bounded_by_wall_clock_deadline(monkeypatch):
	"""FIX 4: _retry must not burn through all `attempts` when each attempt
	itself is slow (e.g. blocked inside SQLite's busy_timeout) -- it must
	give up once the wall-clock deadline is exceeded, well short of the
	worst-case attempts*busy_timeout (50 * 5s ~= 4min)."""
	calls = []
	fake_time = [0.0]

	def fake_monotonic():
		return fake_time[0]

	def fake_sleep(secs):
		fake_time[0] += secs

	def always_busy():
		calls.append(1)
		fake_time[0] += 1.0  # simulate ~1s blocked inside this attempt
		raise sqlite3.OperationalError('database is locked')

	monkeypatch.setattr(datastore.time, 'monotonic', fake_monotonic)
	monkeypatch.setattr(datastore.time, 'sleep', fake_sleep)

	with pytest.raises(sqlite3.OperationalError, match='deadline'):
		datastore._retry(always_busy, attempts=50, deadline_s=10.0)

	# Each simulated attempt costs >=1s; hitting the 10s deadline must stop
	# retries well short of all 50 attempts.
	assert len(calls) < 50


def test_retry_uncontended_case_unchanged():
	"""Common case: no contention, first attempt succeeds immediately --
	the deadline machinery must not add overhead or change behavior."""
	assert datastore._retry(lambda: 42) == 42


def test_no_valkey_references_in_source():
	hits = subprocess.run(
		[
			'grep',
			'-rIl',
			'-e',
			'import valkey',
			'-e',
			'cmdsts',
			'-e',
			'ValkeyQueue',
			'-e',
			'ValkeyHandler',
			'--include=*.py',
			'common',
			'controller',
			'blueprints',
			'control.py',
		],
		capture_output=True,
		text=True,
	).stdout.strip()
	assert hits == '', f'stale Valkey references in: {hits}'
