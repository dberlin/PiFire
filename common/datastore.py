"""SQLite datastore: thread-local connection, schema, transactions, first-boot
import. The only module that opens the database; common.py talks to it."""

import os
import sqlite3
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get('PIFIRE_DB_PATH', os.path.join(_HERE, '..', 'pifire.db'))
_ORIGINAL_DB_PATH = DB_PATH

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL CHECK(json_valid(value))
);
CREATE TABLE IF NOT EXISTS history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             INTEGER NOT NULL,
    psp            REAL,
    primary_temps  TEXT NOT NULL CHECK(json_valid(primary_temps)),
    food_temps     TEXT NOT NULL CHECK(json_valid(food_temps)),
    aux_temps      TEXT NOT NULL CHECK(json_valid(aux_temps)),
    notify_targets TEXT NOT NULL CHECK(json_valid(notify_targets)),
    ext_data       TEXT CHECK(ext_data IS NULL OR json_valid(ext_data))
);
CREATE INDEX IF NOT EXISTS ix_history_ts ON history(ts);
CREATE TABLE IF NOT EXISTS metrics (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    data TEXT NOT NULL CHECK(json_valid(data))
);
CREATE TABLE IF NOT EXISTS logs (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL,
    ts      INTEGER NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_logs_name_id ON logs(name, id);
"""

# one table per queue; JSON queues carry a json_valid CHECK, raw lists do not
_JSON_QUEUE_TABLES = ['queue_control_write', 'queue_systemq', 'queue_systemo', 'queue_displayq', 'queue_autotune']
_RAW_LIST_TABLES = ['list_warnings', 'list_users_connected']


def _queue_ddl():
	ddl = []
	for t in _JSON_QUEUE_TABLES:
		ddl.append(
			f'CREATE TABLE IF NOT EXISTS {t} ('
			'id INTEGER PRIMARY KEY AUTOINCREMENT, '
			'value TEXT NOT NULL CHECK(json_valid(value)));'
		)
	for t in _RAW_LIST_TABLES:
		ddl.append(f'CREATE TABLE IF NOT EXISTS {t} (id INTEGER PRIMARY KEY AUTOINCREMENT, value TEXT NOT NULL);')
	return '\n'.join(ddl)


def connection():
	conn = getattr(_local, 'conn', None)
	if conn is None:
		conn = sqlite3.connect(DB_PATH, timeout=30)
		conn.execute('PRAGMA journal_mode=WAL')
		conn.execute('PRAGMA synchronous=NORMAL')
		conn.execute('PRAGMA busy_timeout=5000')
		conn.execute('PRAGMA foreign_keys=ON')
		conn.isolation_level = None  # autocommit; we manage txns explicitly
		_local.conn = conn
	return conn


def _retry(fn, attempts=50):
	for i in range(attempts):
		try:
			return fn()
		except sqlite3.OperationalError as e:
			if 'locked' in str(e).lower() or 'busy' in str(e).lower():
				time.sleep(0.005 * (i + 1))
				continue
			raise
	raise sqlite3.OperationalError('SQLITE_BUSY: retries exhausted')


def execute_write(sql, params=()):
	return _retry(lambda: connection().execute(sql, params))


class transaction:
	"""`with transaction() as conn:` — BEGIN IMMEDIATE / COMMIT / ROLLBACK,
	retrying only the BEGIN on BUSY."""

	def __enter__(self):
		self.conn = connection()
		_retry(lambda: self.conn.execute('BEGIN IMMEDIATE'))
		return self.conn

	def __exit__(self, exc_type, exc, tb):
		if exc_type is None:
			self.conn.execute('COMMIT')
		else:
			self.conn.execute('ROLLBACK')
		return False


def init():
	conn = connection()
	conn.executescript(SCHEMA + _queue_ddl())
	if conn.execute('PRAGMA user_version').fetchone()[0] == 0:
		conn.execute('PRAGMA user_version=1')
	_first_boot_import()  # filled in Task 13


def _first_boot_import():
	pass  # Task 13


def _reset_for_tests(path):
	"""Test hook: repoint DB_PATH and drop the cached thread-local connection."""
	global DB_PATH
	conn = getattr(_local, 'conn', None)
	if conn is not None:
		conn.close()
		_local.conn = None
	DB_PATH = path if path is not None else _ORIGINAL_DB_PATH


def get_blob(key):
	row = connection().execute('SELECT value FROM kv WHERE key=?', (key,)).fetchone()
	return None if row is None else row[0]


def set_blob(key, value_str):
	execute_write(
		'INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, value_str)
	)


def delete_blob(key):
	execute_write('DELETE FROM kv WHERE key=?', (key,))


def exists_blob(key):
	return connection().execute('SELECT 1 FROM kv WHERE key=?', (key,)).fetchone() is not None
