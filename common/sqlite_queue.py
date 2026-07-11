"""List-backed queues on SQLite, one table per queue. API-compatible with the
old Redis/Valkey-style queue helper (push/pop/length/list/flush). Plus
SqliteMembershipList for the users:connected remove-by-value case."""

import json

from common import datastore

_ALLOWED_TABLES = {
	'queue_control_write',
	'queue_systemq',
	'queue_systemo',
	'queue_displayq',
	'queue_autotune',
	'list_warnings',
	'list_users_connected',
}


def _check_table(table):
	if table not in _ALLOWED_TABLES:
		raise ValueError(f'unknown queue table: {table!r}')


class SqliteQueue:
	def __init__(self, table, raw=False):
		_check_table(table)
		self.table = table
		self.raw = raw  # raw=True stores strings verbatim (list_warnings)

	def _encode(self, data):
		return data if self.raw else json.dumps(data)

	def _decode(self, value):
		return value if self.raw else json.loads(value)

	def push(self, data):
		datastore.execute_write(f'INSERT INTO {self.table}(value) VALUES(?)', (self._encode(data),))

	def pop(self):
		with datastore.transaction() as conn:
			row = conn.execute(f'SELECT id, value FROM {self.table} ORDER BY id LIMIT 1').fetchone()
			if row is None:
				return None
			conn.execute(f'DELETE FROM {self.table} WHERE id=?', (row[0],))
			return self._decode(row[1])

	def length(self):
		return datastore.connection().execute(f'SELECT COUNT(*) FROM {self.table}').fetchone()[0]

	def list(self, start=0, end=-1):
		rows = datastore.connection().execute(f'SELECT value FROM {self.table} ORDER BY id').fetchall()
		values = [self._decode(r[0]) for r in rows]
		if end == -1:
			return values[start:]
		return values[start : end + 1]

	def flush(self):
		datastore.execute_write(f'DELETE FROM {self.table}')


class SqliteMembershipList:
	"""Raw-string membership list with remove-by-value (Valkey lrem count=0)."""

	def __init__(self, table):
		_check_table(table)
		self.table = table

	def add(self, value):
		datastore.execute_write(f'INSERT INTO {self.table}(value) VALUES(?)', (value,))

	def remove(self, value):
		datastore.execute_write(f'DELETE FROM {self.table} WHERE value=?', (value,))

	def list(self):
		rows = datastore.connection().execute(f'SELECT value FROM {self.table} ORDER BY id').fetchall()
		return [r[0] for r in rows]

	def flush(self):
		datastore.execute_write(f'DELETE FROM {self.table}')
