import common.datastore_accessors as c


def test_read_json_blob_returns_default_when_absent(monkeypatch):
    monkeypatch.setattr(c.datastore, "get_blob", lambda key: None)
    assert c._read_json_blob("nope", lambda: {"d": 1}) == {"d": 1}


def test_read_json_blob_parses_present(monkeypatch):
    monkeypatch.setattr(c.datastore, "get_blob", lambda key: '{"x": 5}')
    assert c._read_json_blob("k", dict) == {"x": 5}


def test_write_json_blob_roundtrip(monkeypatch):
    seen = {}
    monkeypatch.setattr(c.datastore, "set_blob", lambda key, raw: seen.update({key: raw}))
    c._write_json_blob("k", {"x": 5})
    assert seen == {"k": '{"x": 5}'}
