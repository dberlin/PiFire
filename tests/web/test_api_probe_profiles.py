"""Profile library mutations preserve assignments and never change live calibration."""

from copy import deepcopy

import pytest

from common.defaults import default_control, default_settings
from common.persistence.control import (
    execute_control_writes,
    read_control,
    read_pending_control_writes,
    write_control_snapshot,
)
from common.persistence.runtime import read_settings, write_settings
from tests.fakes.clock import clock_stamp

PROFILE = {"name": "Precision profile", "a": 0.0007343140544, "b": 0.0002157437229, "c": 9.51568577e-8}


@pytest.fixture(autouse=True)
def profile_state(ds):
    settings = default_settings()
    settings["platform"]["real_hw"] = False
    write_settings(settings)
    write_control_snapshot(default_control(), origin="test-profile")


def test_create_preserves_precision_without_changing_probe_assignments(client):
    before = deepcopy(read_settings()["probe_settings"]["probe_map"])
    response = client.post("/api/probe_profiles", json=PROFILE)
    assert response.status_code == 200
    profile_id = response.get_json()["data"]["id"]
    settings = read_settings()["probe_settings"]
    assert settings["probe_profiles"][profile_id] == {
        "id": profile_id,
        "name": PROFILE["name"],
        "A": PROFILE["a"],
        "B": PROFILE["b"],
        "C": PROFILE["c"],
    }
    assert settings["probe_map"] == before


def test_edit_updates_all_assignments_and_requests_runtime_reload(client):
    settings = read_settings()
    probes = settings["probe_settings"]["probe_map"]["probe_info"]
    for probe in probes[:2]:
        probe["profile"] = deepcopy(settings["probe_settings"]["probe_profiles"]["TWPS00"])
    probes[1]["enabled"] = False
    assigned_labels = [probe["label"] for probe in probes if probe["profile"].get("id") == "TWPS00"]
    write_settings(settings)
    response = client.put("/api/probe_profiles/TWPS00", json=PROFILE)
    assert response.status_code == 200
    assert response.get_json()["data"] == {"id": "TWPS00", "updated_probes": assigned_labels}
    updated = read_settings()["probe_settings"]
    expected = {
        "id": "TWPS00",
        "name": PROFILE["name"],
        "A": PROFILE["a"],
        "B": PROFILE["b"],
        "C": PROFILE["c"],
    }
    assert updated["probe_profiles"]["TWPS00"] == expected
    for probe in updated["probe_map"]["probe_info"]:
        if probe["profile"].get("id") == "TWPS00":
            assert probe["profile"] == expected
    for probe in probes:
        if probe["label"] in assigned_labels:
            probe["profile"] = expected.copy()
    settings["probe_settings"]["probe_profiles"]["TWPS00"] = expected
    after = read_settings()
    after["lastupdated"] = settings["lastupdated"]
    assert after == settings
    execute_control_writes(timer_now=clock_stamp())
    assert read_control()["probe_profile_update"] is True
    assert read_control()["mode"] == "Stop"


def test_assigned_profile_edit_during_hold_refuses_without_writes(client):
    control = default_control()
    control["mode"] = "Hold"
    write_control_snapshot(control, origin="test-profile")
    before = read_settings()
    response = client.put("/api/probe_profiles/TWPS00", json=PROFILE)
    assert response.status_code == 409
    assert response.get_json()["message"] == "system_active"
    assert read_settings() == before
    execute_control_writes(timer_now=clock_stamp())
    assert read_control()["probe_profile_update"] is False


def test_delete_in_use_profile_refuses_without_writes(client):
    before = read_settings()
    response = client.delete("/api/probe_profiles/TWPS00")
    assert response.status_code == 409
    assert response.get_json()["message"] == "profile_in_use"
    assert read_settings() == before


@pytest.mark.parametrize("endpoint", ["/api/probe_profiles", "/api/tuner/profile"])
@pytest.mark.parametrize("mode", ["Stop", "Hold"])
def test_save_only_does_not_queue_reload(client, endpoint, mode):
    control = read_control()
    control["mode"] = mode
    write_control_snapshot(control, origin="test-profile")
    before = read_settings()
    response = client.post(endpoint, json={**PROFILE, "name": "  Precise  ", "a": -0.001, "b": 0})
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["applied"] is None
    settings = read_settings()
    profile = settings["probe_settings"]["probe_profiles"].pop(data["id"])
    assert profile == {"id": data["id"], "name": "Precise", "A": -0.001, "B": 0, "C": PROFILE["c"]}
    settings["lastupdated"] = before["lastupdated"]
    assert settings == before
    assert read_pending_control_writes() == ()


def test_unused_profile_can_be_edited_and_deleted_during_hold(client):
    profile_id = client.post("/api/probe_profiles", json=PROFILE).get_json()["data"]["id"]
    control = read_control()
    control["mode"] = "Hold"
    write_control_snapshot(control, origin="test-profile")
    response = client.put(f"/api/probe_profiles/{profile_id}", json={**PROFILE, "name": "Edited"})
    assert response.status_code == 200
    assert response.get_json()["data"] == {"id": profile_id, "updated_probes": []}
    assert read_settings()["probe_settings"]["probe_profiles"][profile_id]["name"] == "Edited"
    before = read_settings()
    response = client.delete(f"/api/probe_profiles/{profile_id}")
    assert response.status_code == 200
    assert response.get_json()["data"] == {"id": profile_id, "updated_probes": []}
    del before["probe_settings"]["probe_profiles"][profile_id]
    after = read_settings()
    after["lastupdated"] = before["lastupdated"]
    assert after == before
    assert read_pending_control_writes() == ()


@pytest.mark.parametrize("method", ["put", "delete"])
def test_missing_profile_is_refused_without_writes(client, method):
    before = read_settings()
    kwargs = {"json": PROFILE} if method == "put" else {}
    response = getattr(client, method)("/api/probe_profiles/missing", **kwargs)
    assert response.status_code == 404
    assert response.get_json()["message"] == "not_found"
    assert response.get_json()["data"]["field"] == "id"
    assert read_settings() == before
    assert read_pending_control_writes() == ()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", " \t "),
        ("name", 12),
        ("a", True),
        ("a", 10**400),
        ("b", False),
        ("c", "9.51568577e-8"),
        ("a", None),
        ("a", float("nan")),
        ("b", float("inf")),
        ("c", float("-inf")),
        ("extra", 1),
    ],
)
@pytest.mark.parametrize(
    ("method", "endpoint"),
    [
        ("post", "/api/probe_profiles"),
        ("post", "/api/tuner/profile"),
        ("put", "/api/probe_profiles/TWPS00"),
    ],
)
def test_invalid_profile_values_refuse_without_writes(client, method, endpoint, field, value):
    before = read_settings()
    response = getattr(client, method)(endpoint, json={**PROFILE, field: value})
    assert response.status_code == 400
    assert response.get_json()["message"] == "bad_request"
    assert response.get_json()["data"]["field"] == field
    assert read_settings() == before
    assert read_pending_control_writes() == ()


@pytest.mark.parametrize("field", ["name", "a", "b", "c"])
@pytest.mark.parametrize("method", ["post", "put"])
def test_missing_profile_values_refuse_without_writes(client, method, field):
    payload = dict(PROFILE)
    del payload[field]
    endpoint = "/api/probe_profiles" + ("/TWPS00" if method == "put" else "")
    before = read_settings()
    response = getattr(client, method)(endpoint, json=payload)
    assert response.status_code == 400
    assert response.get_json()["data"]["field"] == field
    assert read_settings() == before
    assert read_pending_control_writes() == ()


@pytest.mark.parametrize("endpoint", ["/api/probe_profiles", "/api/tuner/profile"])
def test_apply_queues_reload_without_changing_other_probes(client, endpoint):
    before = read_settings()
    probes = before["probe_settings"]["probe_map"]["probe_info"]
    label = probes[0]["label"]
    response = client.post(endpoint, json={**PROFILE, "apply_to": label})
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["applied"] == label
    settings = read_settings()["probe_settings"]
    assert settings["probe_map"]["probe_info"][0]["profile"] == settings["probe_profiles"][data["id"]]
    assert settings["probe_map"]["probe_info"][1:] == probes[1:]
    execute_control_writes(timer_now=clock_stamp())
    assert read_control()["probe_profile_update"] is True
    assert read_control()["mode"] == "Stop"


@pytest.mark.parametrize("endpoint", ["/api/probe_profiles", "/api/tuner/profile"])
@pytest.mark.parametrize("mode", ["Hold", "Monitor"])
def test_apply_requires_stop_and_refuses_without_writes(client, endpoint, mode):
    control = read_control()
    control["mode"] = mode
    write_control_snapshot(control, origin="test-profile")
    before = read_settings()
    label = before["probe_settings"]["probe_map"]["probe_info"][0]["label"]
    response = client.post(endpoint, json={**PROFILE, "apply_to": label})
    assert response.status_code == 409
    assert response.get_json()["message"] == "system_active"
    assert response.get_json()["data"]["mode"] == mode
    assert read_settings() == before
    assert read_pending_control_writes() == ()


@pytest.mark.parametrize("endpoint", ["/api/probe_profiles", "/api/tuner/profile"])
@pytest.mark.parametrize("label", ["Nonexistent", ""])
def test_unknown_apply_target_refuses_without_creating_profile(client, endpoint, label):
    before = read_settings()
    response = client.post(endpoint, json={**PROFILE, "apply_to": label})
    assert response.status_code == 404
    assert response.get_json()["data"]["field"] == "apply_to"
    assert read_settings() == before
    assert read_pending_control_writes() == ()


def test_disabled_probe_reference_prevents_deletion_with_readable_detail(client):
    settings = read_settings()
    profile_id = "disabled-only"
    profile = {"id": profile_id, "name": "Disabled", "A": 0, "B": 0, "C": 0}
    settings["probe_settings"]["probe_profiles"][profile_id] = profile
    probe = settings["probe_settings"]["probe_map"]["probe_info"][0]
    probe["profile"] = profile.copy()
    probe["enabled"] = False
    probe["name"] = "Disabled grill probe"
    write_settings(settings)
    before = read_settings()
    response = client.delete(f"/api/probe_profiles/{profile_id}")
    assert response.status_code == 409
    assert response.get_json()["message"] == "profile_in_use"
    detail = response.get_json()["data"]["detail"]
    assert "Disabled grill probe" in detail
    assert "unassign" in detail.lower()
    assert read_settings() == before
    assert read_pending_control_writes() == ()


def test_edit_rejects_apply_target_instead_of_silently_ignoring_it(client):
    before = read_settings()
    response = client.put("/api/probe_profiles/TWPS00", json={**PROFILE, "apply_to": "Grill"})
    assert response.status_code == 400
    assert response.get_json()["data"]["field"] == "apply_to"
    assert read_settings() == before
    assert read_pending_control_writes() == ()


@pytest.mark.parametrize("operation", ["create", "update"])
def test_profile_and_reload_enqueue_roll_back_together(monkeypatch, operation):
    from common.app import enqueue_control_delta
    from common.probe_profiles import create_probe_profile, update_probe_profile
    from common.web_contracts.operations import ProfileInput, ProfileValues

    def enqueue_then_fail(delta, *, origin):
        enqueue_control_delta(delta, origin=origin)
        raise RuntimeError("Simulated persistence interruption")

    monkeypatch.setattr("common.app.enqueue_control_delta", enqueue_then_fail)
    before = read_settings()
    with pytest.raises(RuntimeError, match="Simulated persistence interruption"):
        if operation == "create":
            label = before["probe_settings"]["probe_map"]["probe_info"][0]["label"]
            create_probe_profile(ProfileInput.model_validate({**PROFILE, "apply_to": label}, strict=True))
        else:
            update_probe_profile("TWPS00", ProfileValues.model_validate(PROFILE, strict=True))
    assert read_settings() == before
    assert read_pending_control_writes() == ()
