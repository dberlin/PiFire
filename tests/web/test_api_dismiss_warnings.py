import pytest
from common.persistence.runtime import read_warnings_snapshot, write_warning


def test_dismiss_clears_the_warnings_through_the_given_id(client):
    write_warning("hopper low")
    snap = read_warnings_snapshot()
    r = client.post("/api/dismiss_warnings", json={"through_id": snap["max_id"]})
    assert r.status_code == 200
    assert read_warnings_snapshot()["warnings"] == []


def test_dismiss_keeps_a_warning_written_after_the_snapshot(client):
    write_warning("seen")
    snap = read_warnings_snapshot()
    write_warning("written after the snapshot")
    client.post("/api/dismiss_warnings", json={"through_id": snap["max_id"]})
    assert read_warnings_snapshot()["warnings"] == ["written after the snapshot"]


def test_dismiss_rejects_a_non_integer_through_id(client):
    write_warning("hopper low")
    r = client.post("/api/dismiss_warnings", json={"through_id": "not-an-int"})
    assert r.status_code == 400
    # The warning must survive a rejected request.
    assert read_warnings_snapshot()["warnings"] == ["hopper low"]


def test_dismiss_rejects_a_boolean_through_id(client):
    # bool is an int subclass in Python; True must not be accepted as id 1.
    r = client.post("/api/dismiss_warnings", json={"through_id": True})
    assert r.status_code == 400


def test_dismiss_rejects_a_missing_through_id(client):
    r = client.post("/api/dismiss_warnings", json={"nope": 1})
    assert r.status_code == 400


def test_dismiss_is_idempotent(client):
    write_warning("hopper low")
    max_id = read_warnings_snapshot()["max_id"]
    assert client.post("/api/dismiss_warnings", json={"through_id": max_id}).status_code == 200
    assert client.post("/api/dismiss_warnings", json={"through_id": max_id}).status_code == 200
