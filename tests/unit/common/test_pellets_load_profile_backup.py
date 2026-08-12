"""Loading a new pellet profile backs up the pellet DB first (#67).

Flask's legacy loader (blueprints/pellets/routes.py::_pellets_loadprofile) calls
backup_pellet_db(action="backup") after recording the new load. The shared
handler the React UI reaches (POST /api/pellets -> pellets_load_profile) dropped
that call, so a React "Load New Pellets" left no restore point. These tests pin
the backup and its ordering without touching the real datastore.
"""

import common.pellets_actions as pa


def _pelletdb():
    return {"current": {"pelletid": "old", "date_loaded": "", "est_usage": 5}, "log": {}}


def test_load_profile_backs_up_the_pellet_db(monkeypatch):
    calls = []
    monkeypatch.setattr(pa, "enqueue_control_delta", lambda *a, **k: calls.append("enqueue_control_delta"))
    monkeypatch.setattr(pa, "write_pellet_db", lambda *a, **k: calls.append("write_pellet_db"))
    monkeypatch.setattr(pa, "backup_pellet_db", lambda **k: calls.append(("backup", k)))

    result = pa.pellets_load_profile(_pelletdb(), {"profile": "Lumberjack Comp"})

    assert result["result"] == "OK"
    assert ("backup", {"action": "backup"}) in calls
    # The backup must capture the NEW state -- it runs after the write, mirroring
    # Flask's _pellets_loadprofile ordering.
    assert calls.index("write_pellet_db") < calls.index(("backup", {"action": "backup"}))


def test_load_profile_without_a_profile_does_not_back_up(monkeypatch):
    calls = []
    monkeypatch.setattr(pa, "enqueue_control_delta", lambda *a, **k: calls.append("enqueue_control_delta"))
    monkeypatch.setattr(pa, "write_pellet_db", lambda *a, **k: calls.append("write_pellet_db"))
    monkeypatch.setattr(pa, "backup_pellet_db", lambda **k: calls.append("backup"))

    result = pa.pellets_load_profile(_pelletdb(), {})

    assert result["result"] == "Error"
    assert "backup" not in calls
    assert "write_pellet_db" not in calls
