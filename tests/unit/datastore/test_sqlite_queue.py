import sqlite3

import pytest

from common import datastore
from common.sqlite_queue import SqliteQueue, SqliteMembershipList


def test_fifo_roundtrip(ds):
    q = SqliteQueue("queue_systemq")
    assert q.length() == 0
    assert q.pop() is None
    q.push(["a", 1])
    q.push({"b": 2})
    assert q.length() == 2
    assert q.list() == [["a", 1], {"b": 2}]  # non-destructive peek, FIFO
    assert q.pop() == ["a", 1]  # head first
    assert q.pop() == {"b": 2}
    assert q.length() == 0


def test_flush(ds):
    q = SqliteQueue("queue_displayq")
    q.push(["text", "ERROR"])
    q.flush()
    assert q.length() == 0


def test_json_queue_rejects_via_check(ds):
    # raw (non-JSON) insert into a JSON queue table must be rejected by the CHECK
    with pytest.raises(sqlite3.IntegrityError):
        datastore.execute_write("INSERT INTO queue_control_write(value) VALUES('raw')")


def test_raw_tables_accept_non_json_without_check(ds):
    # Raw-list/log tables have no json_valid CHECK -- a plain non-JSON string
    # must insert cleanly, unlike the JSON queue tables above.
    datastore.execute_write("INSERT INTO list_warnings(value) VALUES('not json')")
    datastore.execute_write("INSERT INTO list_users_connected(value) VALUES('not json')")
    datastore.execute_write("INSERT INTO logs(name, ts, message) VALUES('t', 1, 'not json')")
    assert datastore.connection().execute("SELECT value FROM list_warnings").fetchone()[0] == "not json"
    assert datastore.connection().execute("SELECT value FROM list_users_connected").fetchone()[0] == "not json"
    assert datastore.connection().execute("SELECT message FROM logs").fetchone()[0] == "not json"


def test_raw_queue_roundtrips_plain_string_without_json_codec(ds):
    # SqliteQueue(..., raw=True) must store/return strings verbatim -- no
    # json.dumps/json.loads in the encode/decode path.
    q = SqliteQueue("list_warnings", raw=True)
    q.push("plain string, not json")
    assert q.list() == ["plain string, not json"]
    assert q.pop() == "plain string, not json"
    assert q.length() == 0


def test_membership_add_remove(ds):
    m = SqliteMembershipList("list_users_connected")
    m.add("sidA")
    m.add("sidB")
    m.add("sidA")  # duplicate allowed (matches rpush)
    assert sorted(m.list()) == ["sidA", "sidA", "sidB"]
    m.remove("sidA")  # removes ALL "sidA" (lrem count=0)
    assert m.list() == ["sidB"]
    m.flush()
    assert m.list() == []
