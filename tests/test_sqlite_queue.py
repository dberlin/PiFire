import sqlite3

import pytest

from common import datastore
from common.sqlite_queue import SqliteQueue


@pytest.fixture
def ds(tmp_path):
	datastore._reset_for_tests(str(tmp_path / 't.db'))
	datastore.init()
	yield datastore
	datastore._reset_for_tests(None)


def test_fifo_roundtrip(ds):
	q = SqliteQueue('queue_systemq')
	assert q.length() == 0
	assert q.pop() is None
	q.push(['a', 1])
	q.push({'b': 2})
	assert q.length() == 2
	assert q.list() == [['a', 1], {'b': 2}]  # non-destructive peek, FIFO
	assert q.pop() == ['a', 1]  # head first
	assert q.pop() == {'b': 2}
	assert q.length() == 0


def test_flush(ds):
	q = SqliteQueue('queue_displayq')
	q.push(['text', 'ERROR'])
	q.flush()
	assert q.length() == 0


def test_json_queue_rejects_via_check(ds):
	# raw (non-JSON) insert into a JSON queue table must be rejected by the CHECK
	with pytest.raises(sqlite3.IntegrityError):
		datastore.execute_write("INSERT INTO queue_control_write(value) VALUES('raw')")
