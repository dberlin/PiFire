"""Multi-process contention stress tests (T3): prove the SQLite datastore
behaves correctly under PiFire's real multi-process topology — several
producer processes hammering a SqliteQueue, and cross-process visibility of a
committed write via a fresh connection in another process."""

import multiprocessing as mp
import os

import pytest

from common import datastore


def _producer(db, table, n):
	os.environ['PIFIRE_DB_PATH'] = db
	datastore._reset_for_tests(db)
	from common.sqlite_queue import SqliteQueue

	q = SqliteQueue(table)
	for i in range(n):
		q.push({'i': i})


def _reader(db, out):
	os.environ['PIFIRE_DB_PATH'] = db
	datastore._reset_for_tests(db)
	out.put(datastore.get_blob('control:status'))


@pytest.fixture
def db(tmp_path):
	p = str(tmp_path / 't.db')
	os.environ['PIFIRE_DB_PATH'] = p
	datastore._reset_for_tests(p)
	datastore.init()
	yield p
	datastore._reset_for_tests(None)


def test_concurrent_producers_no_loss(db):
	from common.sqlite_queue import SqliteQueue

	ctx = mp.get_context('spawn')
	procs = [ctx.Process(target=_producer, args=(db, 'queue_systemq', 200)) for _ in range(4)]
	for p in procs:
		p.start()
	for p in procs:
		p.join(timeout=60)
	for p in procs:
		assert p.exitcode == 0, f'producer process failed with exitcode {p.exitcode}'
	assert SqliteQueue('queue_systemq').length() == 800  # no lost/dup under contention


def test_cross_process_visibility(db):
	datastore.set_blob('control:status', '{"mode":"Hold"}')
	ctx = mp.get_context('spawn')
	q = ctx.Queue()

	p = ctx.Process(target=_reader, args=(db, q))
	p.start()
	assert q.get(timeout=30) == '{"mode":"Hold"}'  # committed write visible in another process
	p.join(timeout=30)
	assert p.exitcode == 0
