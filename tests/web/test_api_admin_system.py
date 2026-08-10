"""POST /api/admin/system -- reboot, shutdown, restart.

READ THIS BEFORE ADDING A TEST HERE.

This module drives the three calls that can power the machine off. PiFire has
suffered three real unintended reboots from test and verification code, so the
neutralization is deliberately belt-and-braces and the first test in the file
proves the fixture actually intercepts before any endpoint test runs.

Three facts make the naive approaches insufficient:

  1. `blueprints/api_admin/routes.py` does `from common.system import
     reboot_system, ...` at import, binding those names into ITS OWN globals.
     `mock.patch("common.system.reboot_system")` would leave the call site
     pointing at the real function. Every patch here targets the IMPORTING
     module. (This is the "moving code out from under a mock" trap; it has cost
     this repo three times.)
  2. reboot_system/shutdown_system dispatch PRIMARILY through
     `subprocess.run(["sudo","systemctl","reboot"])` and only fall back to
     `os.system`, so patching `os.system` alone catches the fallback and misses
     the primary path.
  3. `is_real_hardware()` reads settings["platform"]["real_hw"], which
     default_settings() ships as True. A fresh test datastore does NOT disable
     the dangerous branch, and relying on it is how two of the three real
     reboots happened.

The endpoint's own `mode == Stop` guard is a product requirement, NOT a test
safeguard. Never lean on it to keep a test safe.
"""

from unittest import mock

import pytest

import blueprints.api_admin.routes as admin_routes

#: Any argv or command string containing one of these must never be executed.
_HAZARD_TOKENS = ("reboot", "poweroff", "shutdown", "supervisor", "systemctl", "halt")


@pytest.fixture
def hazard(ds):
    """Neutralizes every lifecycle call reachable from /api/admin/*, and records.

    Yields the recorder plus the subprocess mock, so a test can assert BOTH
    that the intended stub fired AND that nothing hazardous reached the real
    dispatch.
    """
    from app import app as flask_app

    calls = []

    def _record(name):
        def _inner(*args, **kwargs):
            calls.append((name, args, kwargs))

        return _inner

    flask_app.config["TESTING"] = True
    with (
        mock.patch("os.system", side_effect=lambda cmd: calls.append(("os.system", cmd)) or 0),
        mock.patch("subprocess.run") as m_run,
        mock.patch.object(admin_routes, "reboot_system", side_effect=_record("reboot_system")),
        mock.patch.object(admin_routes, "shutdown_system", side_effect=_record("shutdown_system")),
        mock.patch.object(admin_routes, "restart_scripts", side_effect=_record("restart_scripts")),
        flask_app.test_client() as client,
    ):
        yield {"client": client, "calls": calls, "subprocess_run": m_run}


def assert_nothing_hazardous_ran(hazard):
    """Proof the real dispatch body never executed -- not merely that a stub was
    reached first. Without this, a fixture that silently failed to patch would
    still look green."""
    for call in hazard["subprocess_run"].call_args_list:
        argv = " ".join(str(a) for a in call.args[0]) if call.args else str(call)
        assert not any(t in argv for t in _HAZARD_TOKENS), f"subprocess.run({argv})"
    for entry in hazard["calls"]:
        if entry[0] == "os.system":
            assert not any(t in str(entry[1]) for t in _HAZARD_TOKENS), entry


# ---------------------------------------------------------------------------
# The fixture proves itself before anything relies on it.
# ---------------------------------------------------------------------------


def test_the_hazard_fixture_actually_intercepts(hazard):
    """Calls the bound names directly. If the patches were pointed at the wrong
    module this fails loudly here, instead of every later test passing while a
    real reboot is dispatched three seconds after the response."""
    admin_routes.reboot_system()
    admin_routes.shutdown_system()
    admin_routes.restart_scripts()
    assert [c[0] for c in hazard["calls"]] == [
        "reboot_system",
        "shutdown_system",
        "restart_scripts",
    ]
    assert_nothing_hazardous_ran(hazard)


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "action,expected",
    [("reboot", "reboot_system"), ("shutdown", "shutdown_system"), ("restart", "restart_scripts")],
)
def test_each_action_dispatches_its_own_call(hazard, action, expected):
    resp = hazard["client"].post("/api/admin/system", json={"action": action})
    assert resp.status_code == 200
    assert resp.get_json()["result"] == "OK"
    assert [c[0] for c in hazard["calls"]] == [expected]
    assert_nothing_hazardous_ran(hazard)


def test_an_unknown_action_is_refused(hazard):
    resp = hazard["client"].post("/api/admin/system", json={"action": "selfdestruct"})
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "action"
    assert hazard["calls"] == []


def test_system_action_rejects_extra_json_members(hazard):
    resp = hazard["client"].post("/api/admin/system", json={"action": "reboot", "extra": True})
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "extra"
    assert hazard["calls"] == []


def test_a_missing_action_is_refused(hazard):
    resp = hazard["client"].post("/api/admin/system", json={})
    assert resp.status_code == 400
    assert hazard["calls"] == []


def test_get_cannot_reach_it(hazard):
    """Same hazard /api/cmd/* had: a lifecycle action must not be reachable by
    a bare GET."""
    resp = hazard["client"].get("/api/admin/system")
    assert resp.status_code == 405
    assert hazard["calls"] == []


# ---------------------------------------------------------------------------
# Factory reset lives here rather than with the other maintenance actions:
# it calls restart_scripts(), so it belongs behind the proven hazard fixture.
# ---------------------------------------------------------------------------


def test_factory_reset_restores_defaults_and_restarts(hazard):
    from common.datastore_accessors import read_settings, write_settings

    settings = read_settings()
    settings["globals"]["grill_name"] = "Not A Default"
    write_settings(settings)

    resp = hazard["client"].post("/api/admin/factory-reset", json={})
    assert resp.status_code == 200
    assert read_settings()["globals"]["grill_name"] != "Not A Default"
    assert [c[0] for c in hazard["calls"]] == ["restart_scripts"]
    assert_nothing_hazardous_ran(hazard)


def test_factory_reset_clears_the_pellet_database(hazard):
    """Pre-SQLite this was `os.system("rm pelletdb.json")`; removing that dead
    line preserved a reset that kept every profile. Clearing is the ruling.

    The log does not end up EMPTY: clear_pellet_db() reseeds a default profile
    and records loading it, so a reset leaves exactly one fresh entry. What must
    be gone is the user's own history.
    """
    from common.datastore_accessors import read_pellet_db, write_pellet_db

    pelletdb = read_pellet_db()
    pelletdb["log"]["1767225600000"] = {"pelletid": "sentinel-profile-id", "deleted": False}
    write_pellet_db(pelletdb)
    assert "1767225600000" in read_pellet_db()["log"]

    hazard["client"].post("/api/admin/factory-reset", json={})
    log_after = read_pellet_db()["log"]
    assert "1767225600000" not in log_after
    assert "sentinel-profile-id" not in log_after.values()


def test_factory_reset_refused_unless_stopped(hazard):
    with mock.patch.object(admin_routes, "read_control", return_value={"mode": "Hold"}):
        resp = hazard["client"].post("/api/admin/factory-reset", json={})
    assert resp.status_code == 409
    assert hazard["calls"] == []
    assert_nothing_hazardous_ran(hazard)


def test_refused_unless_stopped(hazard):
    """G7. A deliberate divergence from Flask, which powers the machine off from
    any mode. Second line of defence behind stubbing, never a replacement."""
    with mock.patch.object(admin_routes, "read_control", return_value={"mode": "Hold"}):
        resp = hazard["client"].post("/api/admin/system", json={"action": "reboot"})
    assert resp.status_code == 409
    assert resp.get_json()["message"] == "not_stopped"
    assert resp.get_json()["data"]["mode"] == "Hold"
    assert hazard["calls"] == []
    assert_nothing_hazardous_ran(hazard)
