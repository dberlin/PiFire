"""`POST /api/settings_update` refuses a controller whose optional dependency is
missing, and leaves the store completely untouched when it does.

This is the route-level half of tests/unit/deps/test_settings_gate.py. It exists
because the refusal has to hold at the seam the React Controller tab actually
posts to -- the gate being right in isolation is not the same as it being wired
in, and a save that half-applies would leave settings naming a controller the
control loop cannot build.

NO REAL INSTALL RUNS. `install_extra_detached` is stubbed at its subprocess
`popen` seam; the assertions pin the argv that would have been spawned.
"""

import json

import pytest

from common import controller_deps as cd
from common.datastore_accessors import read_settings, write_settings


@pytest.fixture
def client(ds):
    from app import app as flask_app

    flask_app.config.update(TESTING=True)
    return flask_app.test_client()


@pytest.fixture
def no_do_mpc(monkeypatch):
    """Make do_mpc look absent without uninstalling it."""
    real_find_spec = cd.importlib.util.find_spec

    def fake(name, *args, **kwargs):
        if name == "do_mpc" or name.startswith("do_mpc."):
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(cd.importlib.util, "find_spec", fake)


@pytest.fixture
def spawned(monkeypatch):
    """Capture what would have been spawned, spawning nothing."""
    calls = []
    real = cd.install_extra_detached

    def record(command, **kwargs):
        calls.append((list(command), kwargs))
        return object()

    monkeypatch.setattr(cd, "install_extra_detached", lambda extra, **kw: real(extra, popen=record, **kw))
    return calls


def _select_mpc(client):
    body = {"settings": {"controller": {"selected": "mpc"}}, "flags": ["controller_update"]}
    return client.post("/api/settings_update", data=json.dumps(body), content_type="application/json")


def test_selecting_mpc_without_do_mpc_is_rejected(client, no_do_mpc, spawned):
    resp = _select_mpc(client)

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["result"] == "error"
    assert "do_mpc" in payload["message"]
    assert "controller is unchanged" in payload["message"]


def test_a_rejected_selection_leaves_the_store_untouched(client, no_do_mpc, spawned):
    before = read_settings()["controller"]["selected"]

    _select_mpc(client)

    # The whole save is refused, matching the two validation layers above it --
    # so the controller currently running the user's cook is not disturbed.
    assert read_settings()["controller"]["selected"] == before


def test_the_rejection_triggers_the_background_install(client, no_do_mpc, spawned):
    _select_mpc(client)

    assert len(spawned) == 1
    command, kwargs = spawned[0]
    assert command[1:] == ["-m", "common.extra_installer", "mpc"]
    assert kwargs["start_new_session"] is True


def test_an_unrelated_save_is_unaffected_by_the_gate(client, no_do_mpc, spawned):
    # do_mpc is missing, but this save does not touch the controller: it must
    # succeed and must not kick off a CasADi build.
    body = {"settings": {"pwm": {"update_time": 9}}, "flags": ["settings_update"]}
    resp = client.post("/api/settings_update", data=json.dumps(body), content_type="application/json")

    assert resp.get_json()["result"] == "success"
    assert read_settings()["pwm"]["update_time"] == 9
    assert spawned == []


def test_selecting_mpc_succeeds_when_do_mpc_is_present(client, spawned):
    # The dev venv has do-mpc. Guards against the gate becoming an unconditional
    # "you may never select MPC".
    resp = _select_mpc(client)

    assert resp.get_json()["result"] == "success"
    assert read_settings()["controller"]["selected"] == "mpc"
    assert spawned == []


def test_switching_away_from_a_broken_mpc_selection_is_allowed(client, no_do_mpc, spawned):
    # A user stuck on MPC (e.g. a restored backup) must always be able to get
    # back to a controller that works.
    settings = read_settings()
    settings["controller"]["selected"] = "mpc"
    write_settings(settings)

    body = {"settings": {"controller": {"selected": "pid"}}, "flags": ["controller_update"]}
    resp = client.post("/api/settings_update", data=json.dumps(body), content_type="application/json")

    assert resp.get_json()["result"] == "success"
    assert read_settings()["controller"]["selected"] == "pid"
    assert spawned == []
