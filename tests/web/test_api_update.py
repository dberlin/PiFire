import json

import pytest

from app import app as flask_app


@pytest.fixture
def client(ds):
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def _stub_reads(monkeypatch):
    import blueprints.api_update.routes as ur

    monkeypatch.setattr(
        ur,
        "get_update_data",
        lambda settings: {
            "version": "v1.8.0 (v1.8.0)",
            "branch_target": "main",
            "branches": ["main", "dev", "prototype"],
            "remote_url": "https://github.com/nebhead/PiFire",
            "remote_version": "v1.8.1",
        },
    )
    return ur


def test_state_returns_the_update_data_shape(ds, client, monkeypatch):
    _stub_reads(monkeypatch)
    body = client.get("/api/update/state").get_json()
    assert body["result"] == "OK"
    assert body["data"] == {
        "version": "v1.8.0 (v1.8.0)",
        "branch": "main",
        "branches": ["main", "dev", "prototype"],
        "remote_url": "https://github.com/nebhead/PiFire",
        "remote_version": "v1.8.1",
    }


def test_check_reports_commits_behind(ds, client, monkeypatch):
    import blueprints.api_update.routes as ur

    monkeypatch.setattr(ur, "get_available_updates", lambda: {"success": True, "commits_behind": 3})
    body = client.get("/api/update/check").get_json()
    assert body["result"] == "OK"
    assert body["data"]["behind"] == 3
    assert isinstance(body["data"]["current"], str)


def test_check_maps_a_failed_fetch_to_an_error_envelope(ds, client, monkeypatch):
    import blueprints.api_update.routes as ur

    monkeypatch.setattr(ur, "get_available_updates", lambda: {"success": False, "message": "ERROR Getting Remote"})
    resp = client.get("/api/update/check")
    assert resp.status_code == 502
    assert resp.get_json()["result"] == "Error"
    assert "ERROR" in resp.get_json()["message"]


def test_log_defaults_to_ten_and_returns_output(ds, client, monkeypatch):
    import blueprints.api_update.routes as ur

    seen = {}
    monkeypatch.setattr(ur, "get_log", lambda num_commits: (seen.setdefault("n", num_commits), ("abc123 msg", ""))[1])
    body = client.get("/api/update/log").get_json()
    assert seen["n"] == 10
    assert body["data"]["output"] == "abc123 msg"


def test_log_rejects_a_non_numeric_commit_count(ds, client, monkeypatch):
    import blueprints.api_update.routes as ur

    monkeypatch.setattr(ur, "get_log", lambda num_commits: ("", ""))
    resp = client.get("/api/update/log?commits=abc")
    assert resp.status_code == 400


def test_status_passes_through_the_install_status_triplet(ds, client, monkeypatch):
    import blueprints.api_update.routes as ur

    monkeypatch.setattr(ur, "get_updater_install_status", lambda: (42, "Working...", "line"))
    body = client.get("/api/update/status").get_json()
    assert body["data"] == {"percent": 42, "status": "Working...", "output": "line"}
