import json

import common.common as c


def test_missing_file_returns_default(tmp_path):
    assert c._load_json_file(str(tmp_path / "absent.json"), {"d": 1}) == {"d": 1}


def test_valid_file_parses(tmp_path):
    p = tmp_path / "ok.json"
    p.write_text(json.dumps({"x": 9}))
    assert c._load_json_file(str(p), {}) == {"x": 9}


def test_corrupt_file_retries_then_succeeds(tmp_path, monkeypatch):
    """Simulate a reader/writer collision: the first read hits invalid JSON
    (writer still mid-write), the retry sees the fully-written, valid file."""
    p = tmp_path / "racy.json"
    p.write_text("{not valid json")

    orig_loads = json.loads
    calls = {"n": 0}

    def fake_loads(s):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("simulated collision")
        return orig_loads(s)

    monkeypatch.setattr(json, "loads", fake_loads)
    # Rewrite valid content so the (unmocked) file read on retry succeeds.
    p.write_text(json.dumps({"ok": True}))

    assert c._load_json_file(str(p), {}) == {"ok": True}
    assert calls["n"] == 2


def test_persistent_corruption_returns_default_once_retries_exhausted(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not valid json")

    assert c._load_json_file(str(p), {"fallback": True}, max_retries=2) == {"fallback": True}


def test_max_retries_zero_disables_retry(tmp_path, monkeypatch):
    p = tmp_path / "bad2.json"
    p.write_text(json.dumps({"a": 1}))

    calls = {"n": 0}

    def fake_loads(s):
        calls["n"] += 1
        raise ValueError("always fails")

    monkeypatch.setattr(json, "loads", fake_loads)

    assert c._load_json_file(str(p), {"d": 0}, max_retries=0) == {"d": 0}
    assert calls["n"] == 1
