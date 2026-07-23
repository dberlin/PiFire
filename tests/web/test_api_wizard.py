import json

import pytest

from app import app as flask_app
from common.datastore_accessors import read_settings, write_settings_store


@pytest.fixture
def client(ds):
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def test_state_fresh_returns_metadata_and_selections(ds, client):
    resp = client.get("/api/wizard/state")
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body["modules_metadata"].keys()) >= {"grillplatform", "display", "distance"}
    assert "display" in body["selections"]
    assert body["has_draft"] is False
    # display_config is a dict keyed by module name (may be empty on fresh)
    assert isinstance(body["display_config"], dict)
    assert "control_mode" in body and "first_time_setup" in body


def test_draft_persists_and_state_resumes(ds, client):
    draft = {
        "selections": {"display": "ili9341b"},
        "settings_dep_values": {"display": {}},
        "display_config": {"ili9341b": {"rotation": 90}},
    }
    r1 = client.post("/api/wizard/draft", data=json.dumps(draft), content_type="application/json")
    assert r1.status_code == 200 and r1.get_json()["result"] == "success"

    r2 = client.get("/api/wizard/state")
    body = r2.get_json()
    assert body["has_draft"] is True
    assert body["selections"]["display"] == "ili9341b"
    assert body["display_config"]["ili9341b"]["rotation"] == 90


def test_state_existing_stale_module_returns_empty_selection(ds, client):
    """Characterizes the (now-fixed) stale-module recovery path in
    wizardInstallInfoExisting() (blueprints/wizard/wizard.py): when
    settings["modules"]["dist"] names a module that is no longer in the
    wizard manifest (e.g. a distance sensor that was removed/renamed), the
    "stale module" recovery branch sets:

        wizardInstallInfo["modules"]["distance"]["profile_selected"] = []

    profile_selected is ALWAYS a list -- an invalid/stale saved module now
    means "no selection" ([]), not a bare string echoing the recovered
    fallback name. We deliberately use the "distance" section (not
    "display") to isolate this from a *second*, unrelated bug: the display
    branch of the same function indexes
    settings["display"]["config"][settings["modules"]["display"]] using the
    ORIGINAL (still-stale) settings value rather than the recovered
    `selected`, which would KeyError before we ever reach the
    profile_selected shape issue.

    _build_state()'s clean always-list extraction (`pf[0] if pf else ""`)
    turns that empty list into an empty-string selection."""
    settings = read_settings()
    settings["globals"]["first_time_setup"] = False
    settings["modules"]["dist"] = "totally_bogus_distance_module"
    write_settings_store(settings)

    resp = client.get("/api/wizard/state")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["selections"]["distance"] == ""


def test_scan_extended_i2c_returns_groups(ds, client, monkeypatch):
    import blueprints.api_wizard.routes as wr

    monkeypatch.setattr(
        wr,
        "discover_extended_i2c_buses",
        lambda *a, **k: [{"bus_num": 1, "name": "i2c-1", "serial": "ABC"}],
    )
    resp = client.post(
        "/api/wizard/scan",
        data=json.dumps({"kind": "extended"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["error"] is None
    assert isinstance(body["groups"], list) and body["groups"]
    assert body["groups"][0]["items"][0]["value"]


def test_scan_no_results_returns_friendly_error(ds, client, monkeypatch):
    import blueprints.api_wizard.routes as wr

    monkeypatch.setattr(wr, "discover_extended_i2c_buses", lambda *a, **k: [])
    resp = client.post(
        "/api/wizard/scan",
        data=json.dumps({"kind": "extended"}),
        content_type="application/json",
    )
    body = resp.get_json()
    assert body["error"] == "No devices found."
