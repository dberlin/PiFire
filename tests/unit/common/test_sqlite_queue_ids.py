from common.sqlite_queue import SqliteQueue


def _q():
    return SqliteQueue("list_warnings", raw=True)


def test_list_with_ids_returns_ids_in_insertion_order(ds):
    q = _q()
    q.push("first")
    q.push("second")
    rows = q.list_with_ids()
    assert [v for _, v in rows] == ["first", "second"]
    ids = [i for i, _ in rows]
    assert ids == sorted(ids)
    assert all(isinstance(i, int) for i in ids)


def test_list_with_ids_is_empty_for_empty_queue(ds):
    assert _q().list_with_ids() == []


def test_clear_through_deletes_only_up_to_the_id(ds):
    q = _q()
    q.push("first")
    q.push("second")
    first_id = q.list_with_ids()[0][0]
    q.clear_through(first_id)
    assert [v for _, v in q.list_with_ids()] == ["second"]


def test_clear_through_preserves_a_warning_written_after_the_snapshot(ds):
    # THE lossless property: a warning pushed after the client's snapshot has a
    # higher id, so dismissing the snapshot must not delete it unseen.
    q = _q()
    q.push("seen")
    snapshot_max_id = q.list_with_ids()[-1][0]
    q.push("written after the snapshot")
    q.clear_through(snapshot_max_id)
    assert [v for _, v in q.list_with_ids()] == ["written after the snapshot"]


def test_clear_through_is_idempotent(ds):
    q = _q()
    q.push("first")
    max_id = q.list_with_ids()[-1][0]
    q.clear_through(max_id)
    q.clear_through(max_id)
    assert q.list_with_ids() == []
