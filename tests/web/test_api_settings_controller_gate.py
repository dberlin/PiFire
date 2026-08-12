"""The settings API gates MPC selection on the published acados native release."""

import json

import pytest

from common import controller_deps as cd
from common.datastore_accessors import read_settings, write_settings


REBUILD = "./rebuild-acados.sh --if-needed"


def _native_failure(monkeypatch, detail="native publication is missing"):
    def fail():
        raise RuntimeError(f"{detail}. Run {REBUILD}")

    monkeypatch.setattr(cd, "load_native", fail, raising=False)


def _select_mpc(client):
    body = {"settings": {"controller": {"selected": "mpc"}}, "flags": ["controller_update"]}
    return client.post("/api/settings_update", data=json.dumps(body), content_type="application/json")


def test_selecting_mpc_without_a_native_publication_is_rejected_with_rebuild_guidance(
    client, monkeypatch
):
    _native_failure(monkeypatch)

    response = _select_mpc(client)

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["result"] == "error"
    assert "native publication is missing" in payload["message"]
    assert REBUILD in payload["message"]
    assert "controller is unchanged" in payload["message"].lower()


def test_an_abi_mismatch_rejection_leaves_the_saved_selection_untouched(client, monkeypatch):
    before = read_settings()["controller"]["selected"]
    _native_failure(monkeypatch, "native ABI mismatch: expected 2, got 1")

    response = _select_mpc(client)

    assert response.get_json()["result"] == "error"
    assert read_settings()["controller"]["selected"] == before
    assert REBUILD in response.get_json()["message"]


def test_an_unrelated_save_does_not_probe_acados(client, monkeypatch):
    def unexpected():
        raise AssertionError("an unrelated settings save must not probe acados")

    monkeypatch.setattr(cd, "load_native", unexpected, raising=False)
    body = {"settings": {"pwm": {"update_time": 9}}, "flags": ["settings_update"]}

    response = client.post("/api/settings_update", data=json.dumps(body), content_type="application/json")

    assert response.get_json()["result"] == "success"
    assert read_settings()["pwm"]["update_time"] == 9


def test_selecting_mpc_succeeds_when_the_native_loader_is_ready(client, monkeypatch):
    calls = []
    monkeypatch.setattr(cd, "load_native", lambda: calls.append("loaded") or object(), raising=False)

    response = _select_mpc(client)

    assert response.get_json()["result"] == "success"
    assert read_settings()["controller"]["selected"] == "mpc"
    assert calls == ["loaded"]


def test_switching_away_from_a_broken_mpc_selection_is_always_allowed(client, monkeypatch):
    settings = read_settings()
    settings["controller"]["selected"] = "mpc"
    write_settings(settings)
    _native_failure(monkeypatch)
    body = {"settings": {"controller": {"selected": "pid"}}, "flags": ["controller_update"]}

    response = client.post("/api/settings_update", data=json.dumps(body), content_type="application/json")

    assert response.get_json()["result"] == "success"
    assert read_settings()["controller"]["selected"] == "pid"
