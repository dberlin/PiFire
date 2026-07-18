"""Characterization net for the Socket.IO god-functions in
``blueprints/mobile/socket_io.py``: ``_get_app_data`` (8 actions) and
``_post_app_data`` (8 action-groups x nested ``type``).

These are Socket.IO event handlers, NOT HTTP routes, so the Playwright
suite (tests/web/test_page_*.py) does not touch them. This module drives
the two plain functions directly against a fresh temp-SQLite datastore
(the ``ds`` fixture) and pins BOTH the returned ``_response``/``api_response``
envelope AND the resulting settings/control/pellet writes, for every
``action`` x ``type`` branch reachable without real hardware.

Intent: lock down current behavior BEFORE the Task 9 decomposition into
per-action handlers + dispatch maps, so the refactor is provably
behavior-preserving. This includes pinning latent quirks verbatim (they
are NOT bugs to fix here):

- ``timer_action`` finds the ``notify_data`` timer entry by index, then
  branches on ``control["timer"]["paused"]`` -- two distinct paths under
  ``type == "start_timer"`` (fresh-start vs unpause). Both are pinned.
- The ``timer_action`` loop's ``index`` used to CARRY OVER when no
  ``notify_data`` entry was of ``type == "timer"``, mutating whatever entry
  the last loop iteration left ``index`` pointing at. This is now fixed:
  the loop initializes ``index = None`` and the handler returns an Error
  envelope without mutating anything when no timer entry is found
  (``test_timer_action_no_timer_entry_returns_error_without_mutation``).
- ``recipe_data`` with ``arg01=None``, and ``recipe_delete``/``recipe_start``
  with a falsy filename, fall through every ``return`` and yield ``None``.
  Pinned as-is.

Hazard neutralization: ``admin_action`` can reach reboot/shutdown/restart
helpers and ``os.system("rm ...")``. The ``sio`` fixture patches
``os.system`` and the module-level ``reboot_system``/``shutdown_system``/
``restart_control``/``restart_webapp``/``restart_scripts`` names (the ones
``_post_app_data`` actually resolves) to recording stubs, mirroring
tests/web/test_page_admin.py's hazard_guard. Nothing destructive runs.
"""

import json
import types
from unittest import mock

import pytest

from common.common import WriteKind
from common.datastore_accessors import (
    execute_control_writes,
    read_control,
    read_pellets_store,
    read_settings,
    read_status,
    write_control,
    write_generic_key,
    write_pellet_db,
    write_settings_store,
    write_status,
)
from common.defaults import default_control, default_pellets, default_settings

# Index of the single ``type == "timer"`` entry in a default notify_data list
# (12 probe/limit entries for 4 probes come first). Pinned so the timer tests
# can assert on the exact entry the function mutates.
_TIMER_IDX = 12


def _drain():
    """Apply queued MERGE control writes so a read_control() reflects them.

    ``write_control(..., WriteKind.MERGE, ...)`` only queues a partial; in
    production the control-loop drains it each tick. This harness has no
    control loop, so drain by hand before asserting on control state.
    """
    execute_control_writes()


@pytest.fixture
def sio(ds):
    """Seed a fresh datastore with defaults, import the socket_io module, and
    neutralize every hazardous dispatch for the test's duration.

    Yields a namespace with ``.mod`` (the socket_io module) and ``.calls``
    (an ordered list every reboot/shutdown/restart/os.system stub appends to).
    """
    write_settings_store(default_settings())
    write_control(default_control(), WriteKind.OVERWRITE, origin="test-socketio")
    write_pellet_db(default_pellets())
    write_status(read_status(init=True))
    # dash_data reads this generic key (normally written by the control
    # runtime); seed an empty map so _get_probe_data has something to read.
    write_generic_key("probe_device_info", {})

    import blueprints.mobile.socket_io as socket_io

    calls = []

    def _rec(name):
        def _inner(*args, **kwargs):
            calls.append((name, args, kwargs))

        return _inner

    def _rec_os(cmd):
        calls.append(("os.system", cmd))
        return 0

    with (
        mock.patch("os.system", side_effect=_rec_os),
        mock.patch.object(socket_io, "reboot_system", side_effect=_rec("reboot_system")),
        mock.patch.object(socket_io, "shutdown_system", side_effect=_rec("shutdown_system")),
        mock.patch.object(socket_io, "restart_control", side_effect=_rec("restart_control")),
        mock.patch.object(socket_io, "restart_webapp", side_effect=_rec("restart_webapp")),
        mock.patch.object(socket_io, "restart_scripts", side_effect=_rec("restart_scripts")),
    ):
        yield types.SimpleNamespace(mod=socket_io, calls=calls)


# =====================================================================
# _get_app_data -- 8 actions
# =====================================================================


def test_get_settings_data(sio):
    resp = sio.mod._get_app_data("settings_data")
    assert resp["result"] == "OK"
    assert resp["message"] is None
    assert resp["data"] == read_settings()


def test_get_dash_data(sio):
    # _get_app_data("dash_data") wraps _get_dash_data(settings, pelletdb) in an
    # OK envelope. Pin that dispatch/wrapping with a sentinel; the _get_dash_data
    # internals (probe assembly) are out of Task 9's decomposition scope and
    # need a fully control-runtime-seeded `current`, which this harness lacks.
    sentinel = {"grillName": "sentinel-dash"}
    with mock.patch.object(sio.mod, "_get_dash_data", return_value=sentinel) as m_dash:
        resp = sio.mod._get_app_data("dash_data")
    assert resp["result"] == "OK"
    assert resp["data"] is sentinel
    # called with (settings, pelletdb) read from the store
    args = m_dash.call_args.args
    assert args[0] == read_settings()
    assert args[1] == read_pellets_store()


def test_get_pellets_data(sio):
    resp = sio.mod._get_app_data("pellets_data")
    assert resp["result"] == "OK"
    assert resp["data"]["uuid"] == read_settings()["server_info"]["uuid"]
    assert resp["data"]["pellets"] == read_pellets_store()


def test_get_events_data(sio):
    resp = sio.mod._get_app_data("events_data")
    assert resp["result"] == "OK"
    assert resp["data"]["uuid"] == read_settings()["server_info"]["uuid"]
    assert isinstance(resp["data"]["events"], list)


def test_get_hopper_level(sio):
    resp = sio.mod._get_app_data("hopper_level")
    assert resp["result"] == "OK"
    assert resp["data"] == read_pellets_store()["current"]["hopper_level"]


def test_get_info_data_field_remap(sio):
    # Pin the exact system_info -> response remapping without depending on
    # real hardware probing: feed a canned _get_system_info result.
    canned = {
        "hardware_info": {
            "cpu_info": {
                "model": "PiModel",
                "model_name": "CPU-Name",
                "hardware": "HW",
                "cores": 4,
                "frequency": 1500,
            },
            "total_ram": 1000,
            "available_ram": 500,
        },
        "os_info": {
            "PRETTY_NAME": "PrettyOS",
            "VERSION": "12",
            "VERSION_CODENAME": "bookworm",
            "ARCHITECTURE": "arm64",
            "BITS": "64",
        },
        "network_info": {"iface": "wlan0"},
        "cpu_throttled": False,
        "cpu_under_voltage": True,
        "wifi_quality_value": 55,
        "wifi_quality_max": 70,
        "wifi_quality_percentage": 78,
        "uptime": "up 3 days",
        "cpu_temp": 42.5,
    }
    with mock.patch.object(sio.mod, "_get_system_info", return_value=canned):
        resp = sio.mod._get_app_data("info_data")
    assert resp["result"] == "OK"
    d = resp["data"]
    assert d["uuid"] == read_settings()["server_info"]["uuid"]
    assert d["platformInfo"]["systemModel"] == "PiModel"
    assert d["platformInfo"]["cpuModel"] == "CPU-Name"
    assert d["platformInfo"]["cpuCores"] == 4
    assert d["platformInfo"]["totalRam"] == 1000
    assert d["osInfo"]["prettyName"] == "PrettyOS"
    assert d["osInfo"]["codeName"] == "bookworm"
    assert d["osInfo"]["bits"] == "64"
    assert d["networkInfo"] == {"iface": "wlan0"}
    assert d["cpuUnderVolt"] is True
    assert d["wifiQualityPercentage"] == 78
    assert d["cpuTemp"] == 42.5


def test_get_manual_data(sio):
    resp = sio.mod._get_app_data("manual_data")
    assert resp["result"] == "OK"
    assert resp["data"]["manual"] == read_status()["outpins"]
    # default control mode is "Stop", so active is False
    assert resp["data"]["active"] is False
    assert resp["data"]["dcFan"] == read_settings()["platform"]["dc_fan"]


def test_get_recipe_data_details_none_found(sio):
    # No recipe files (mock the file lister) -> empty list -> Error.
    with mock.patch.object(sio.mod, "get_recipefilelist", return_value=[]):
        resp = sio.mod._get_app_data("recipe_data", "details")
    assert resp["result"] == "Error"
    assert resp["message"] == "Error: Recipes details not found"


def test_get_recipe_data_arg01_none_returns_none(sio):
    # Latent fall-through: recipe_data with arg01=None hits no return -> None.
    resp = sio.mod._get_app_data("recipe_data")
    assert resp is None


def test_get_invalid_action(sio):
    resp = sio.mod._get_app_data("bogus_action")
    assert resp["result"] == "Error"
    assert resp["message"] == "Error: Received request without valid action"


# =====================================================================
# _post_app_data -- update_action
# =====================================================================


def test_post_update_settings_valid_key(sio):
    payload = json.dumps({"globals": {"grill_name": "Characterized Grill"}})
    resp = sio.mod._post_app_data("update_action", "settings", payload)
    assert resp["result"] == "OK"
    assert read_settings()["globals"]["grill_name"] == "Characterized Grill"
    _drain()
    assert read_control()["settings_update"] is True


def test_post_update_settings_unknown_key(sio):
    payload = json.dumps({"not_a_settings_key": 1})
    resp = sio.mod._post_app_data("update_action", "settings", payload)
    assert resp["result"] == "Error"
    assert resp["message"] == "Error: Key not found in settings"


def test_post_update_control_valid_key(sio):
    payload = json.dumps({"mode": "Stop"})
    resp = sio.mod._post_app_data("update_action", "control", payload)
    assert resp["result"] == "OK"
    assert "mode" in resp["data"]


def test_post_update_control_unknown_key(sio):
    payload = json.dumps({"not_a_control_key": 1})
    resp = sio.mod._post_app_data("update_action", "control", payload)
    assert resp["result"] == "Error"
    assert resp["message"] == "Error: Key not found in control"


def test_post_update_invalid_type(sio):
    resp = sio.mod._post_app_data("update_action", "bogus", json.dumps({"globals": {}}))
    assert resp["result"] == "Error"
    assert resp["message"] == "Error: Received request without valid type"


# =====================================================================
# _post_app_data -- admin_action (hazards neutralized)
# =====================================================================


def test_post_admin_clear_history(sio):
    resp = sio.mod._post_app_data("admin_action", "clear_history")
    assert resp["result"] == "OK"


def test_post_admin_clear_events(sio):
    resp = sio.mod._post_app_data("admin_action", "clear_events")
    assert resp["result"] == "OK"
    assert ("os.system", "rm ./logs/events.log") in sio.calls


def test_post_admin_clear_pelletdb(sio):
    resp = sio.mod._post_app_data("admin_action", "clear_pelletdb")
    assert resp["result"] == "OK"
    assert ("os.system", "rm pelletdb.json") in sio.calls


def test_post_admin_clear_pelletdb_log(sio):
    # Seed a log entry, confirm it is cleared.
    pelletdb = read_pellets_store()
    pelletdb["log"]["2020-01-01 00:00:00"] = "x"
    write_pellet_db(pelletdb)
    resp = sio.mod._post_app_data("admin_action", "clear_pelletdb_log")
    assert resp["result"] == "OK"
    assert read_pellets_store()["log"] == {}


def test_post_admin_factory_defaults(sio):
    # Mutate settings first, confirm factory reset restores defaults.
    settings = read_settings()
    settings["globals"]["grill_name"] = "Dirty"
    write_settings_store(settings)
    resp = sio.mod._post_app_data("admin_action", "factory_defaults")
    assert resp["result"] == "OK"
    assert ("os.system", "rm settings.json") in sio.calls
    assert read_settings()["globals"]["grill_name"] == default_settings()["globals"]["grill_name"]


def test_post_admin_reboot(sio):
    resp = sio.mod._post_app_data("admin_action", "reboot")
    assert resp["result"] == "OK"
    assert any(c[0] == "reboot_system" for c in sio.calls)


def test_post_admin_shutdown(sio):
    resp = sio.mod._post_app_data("admin_action", "shutdown")
    assert resp["result"] == "OK"
    assert any(c[0] == "shutdown_system" for c in sio.calls)


def test_post_admin_restart_control(sio):
    resp = sio.mod._post_app_data("admin_action", "restart_control")
    assert resp["result"] == "OK"
    assert any(c[0] == "restart_control" for c in sio.calls)


def test_post_admin_restart_webapp(sio):
    resp = sio.mod._post_app_data("admin_action", "restart_webapp")
    assert resp["result"] == "OK"
    assert any(c[0] == "restart_webapp" for c in sio.calls)


def test_post_admin_restart_supervisor(sio):
    resp = sio.mod._post_app_data("admin_action", "restart_supervisor")
    assert resp["result"] == "OK"
    assert any(c[0] == "restart_scripts" for c in sio.calls)


def test_post_admin_invalid_type(sio):
    resp = sio.mod._post_app_data("admin_action", "bogus")
    assert resp["result"] == "Error"
    assert resp["message"] == "Error: Received request without valid type"


# =====================================================================
# _post_app_data -- units_action
# =====================================================================


def test_post_units_to_fahrenheit(sio):
    settings = read_settings()
    settings["globals"]["units"] = "C"
    write_settings_store(settings)
    resp = sio.mod._post_app_data("units_action", "f_units")
    assert resp["result"] == "OK"
    assert read_settings()["globals"]["units"] == "F"
    _drain()
    assert read_control()["units_change"] is True


def test_post_units_to_celsius(sio):
    # Default seeded units are "F".
    resp = sio.mod._post_app_data("units_action", "c_units")
    assert resp["result"] == "OK"
    assert read_settings()["globals"]["units"] == "C"


def test_post_units_noop_error(sio):
    # Already Fahrenheit -> f_units cannot change -> Error.
    resp = sio.mod._post_app_data("units_action", "f_units")
    assert resp["result"] == "Error"
    assert resp["message"] == "Error: Units could not be changed"


# =====================================================================
# _post_app_data -- pellets_action
# =====================================================================


def test_post_pellets_load_profile(sio):
    profile_id = next(iter(read_pellets_store()["archive"].keys()))
    payload = json.dumps({"pellets_action": {"profile": profile_id}})
    resp = sio.mod._post_app_data("pellets_action", "load_profile", payload)
    assert resp["result"] == "OK"
    assert read_pellets_store()["current"]["pelletid"] == profile_id
    _drain()
    assert read_control()["hopper_check"] is True


def test_post_pellets_load_profile_missing(sio):
    payload = json.dumps({"pellets_action": {}})
    resp = sio.mod._post_app_data("pellets_action", "load_profile", payload)
    assert resp["result"] == "Error"
    assert resp["message"] == "Error: Profile not included in request"


def test_post_pellets_hopper_check(sio):
    resp = sio.mod._post_app_data("pellets_action", "hopper_check", json.dumps({"pellets_action": {}}))
    assert resp["result"] == "OK"
    _drain()
    assert read_control()["hopper_check"] is True


def test_post_pellets_edit_brands_new(sio):
    payload = json.dumps({"pellets_action": {"new_brand": "Acme"}})
    resp = sio.mod._post_app_data("pellets_action", "edit_brands", payload)
    assert resp["result"] == "OK"
    assert "Acme" in read_pellets_store()["brands"]


def test_post_pellets_edit_brands_delete(sio):
    payload = json.dumps({"pellets_action": {"delete_brand": "Generic"}})
    resp = sio.mod._post_app_data("pellets_action", "edit_brands", payload)
    assert resp["result"] == "OK"
    assert "Generic" not in read_pellets_store()["brands"]


def test_post_pellets_edit_brands_unspecified(sio):
    payload = json.dumps({"pellets_action": {}})
    resp = sio.mod._post_app_data("pellets_action", "edit_brands", payload)
    assert resp["result"] == "Error"
    assert resp["message"] == "Error: Function not specified"


def test_post_pellets_edit_woods_new(sio):
    payload = json.dumps({"pellets_action": {"new_wood": "Mesquite2"}})
    resp = sio.mod._post_app_data("pellets_action", "edit_woods", payload)
    assert resp["result"] == "OK"
    assert "Mesquite2" in read_pellets_store()["woods"]


def test_post_pellets_edit_woods_delete(sio):
    existing = read_pellets_store()["woods"][0]
    payload = json.dumps({"pellets_action": {"delete_wood": existing}})
    resp = sio.mod._post_app_data("pellets_action", "edit_woods", payload)
    assert resp["result"] == "OK"
    assert existing not in read_pellets_store()["woods"]


def test_post_pellets_edit_woods_unspecified(sio):
    payload = json.dumps({"pellets_action": {}})
    resp = sio.mod._post_app_data("pellets_action", "edit_woods", payload)
    assert resp["result"] == "Error"
    assert resp["message"] == "Error: Function not specified"


def test_post_pellets_add_profile_no_load(sio):
    before = set(read_pellets_store()["archive"].keys())
    payload = json.dumps(
        {
            "pellets_action": {
                "brand_name": "B",
                "wood_type": "W",
                "rating": 5,
                "comments": "c",
                "add_and_load": False,
            }
        }
    )
    resp = sio.mod._post_app_data("pellets_action", "add_profile", payload)
    assert resp["result"] == "OK"
    after = set(read_pellets_store()["archive"].keys())
    assert len(after - before) == 1


def test_post_pellets_add_profile_and_load(sio):
    payload = json.dumps(
        {
            "pellets_action": {
                "brand_name": "B",
                "wood_type": "W",
                "rating": 5,
                "comments": "c",
                "add_and_load": True,
            }
        }
    )
    resp = sio.mod._post_app_data("pellets_action", "add_profile", payload)
    assert resp["result"] == "OK"
    pelletdb = read_pellets_store()
    # current pelletid now points at a freshly-added archive entry
    assert pelletdb["current"]["pelletid"] in pelletdb["archive"]
    _drain()
    assert read_control()["hopper_check"] is True


def test_post_pellets_edit_profile(sio):
    profile_id = next(iter(read_pellets_store()["archive"].keys()))
    payload = json.dumps(
        {
            "pellets_action": {
                "profile": profile_id,
                "brand_name": "EditedBrand",
                "wood_type": "EditedWood",
                "rating": 3,
                "comments": "edited",
            }
        }
    )
    resp = sio.mod._post_app_data("pellets_action", "edit_profile", payload)
    assert resp["result"] == "OK"
    assert read_pellets_store()["archive"][profile_id]["brand"] == "EditedBrand"


def test_post_pellets_edit_profile_missing(sio):
    payload = json.dumps({"pellets_action": {}})
    resp = sio.mod._post_app_data("pellets_action", "edit_profile", payload)
    assert resp["result"] == "Error"
    assert resp["message"] == "Error: Profile not included in request"


def test_post_pellets_delete_profile_current_blocked(sio):
    current = read_pellets_store()["current"]["pelletid"]
    payload = json.dumps({"pellets_action": {"profile": current}})
    resp = sio.mod._post_app_data("pellets_action", "delete_profile", payload)
    assert resp["result"] == "Error"
    assert resp["message"] == "Error: Cannot delete current profile"


def test_post_pellets_delete_profile_noncurrent(sio):
    # Add a second (non-current) profile, then delete it.
    pelletdb = read_pellets_store()
    pelletdb["archive"]["deadbeef"] = {"id": "deadbeef", "brand": "B", "wood": "W", "rating": 1, "comments": ""}
    write_pellet_db(pelletdb)
    payload = json.dumps({"pellets_action": {"profile": "deadbeef"}})
    resp = sio.mod._post_app_data("pellets_action", "delete_profile", payload)
    assert resp["result"] == "OK"
    assert "deadbeef" not in read_pellets_store()["archive"]


def test_post_pellets_delete_profile_missing(sio):
    payload = json.dumps({"pellets_action": {}})
    resp = sio.mod._post_app_data("pellets_action", "delete_profile", payload)
    assert resp["result"] == "Error"
    assert resp["message"] == "Error: Profile not included in request"


def test_post_pellets_delete_log(sio):
    log_key = next(iter(read_pellets_store()["log"].keys()))
    payload = json.dumps({"pellets_action": {"log_item": log_key}})
    resp = sio.mod._post_app_data("pellets_action", "delete_log", payload)
    assert resp["result"] == "OK"
    assert log_key not in read_pellets_store()["log"]


def test_post_pellets_delete_log_unspecified(sio):
    payload = json.dumps({"pellets_action": {}})
    resp = sio.mod._post_app_data("pellets_action", "delete_log", payload)
    assert resp["result"] == "Error"
    assert resp["message"] == "Error: Function not specified"


def test_post_pellets_invalid_type(sio):
    resp = sio.mod._post_app_data("pellets_action", "bogus", json.dumps({"pellets_action": {}}))
    assert resp["result"] == "Error"
    assert resp["message"] == "Error: Received request without valid type"


# =====================================================================
# _post_app_data -- timer_action (stateful; both start paths + latent bug)
# =====================================================================


def test_post_timer_start_fresh(sio):
    # paused == 0 path: start a fresh timer with an end computed from ranges.
    payload = json.dumps(
        {
            "timer_action": {
                "hours_range": 1,
                "minutes_range": 30,
                "timer_shutdown": True,
                "timer_keep_warm": False,
            }
        }
    )
    resp = sio.mod._post_app_data("timer_action", "start_timer", payload)
    assert resp["result"] == "OK"
    _drain()
    control = read_control()
    assert control["timer"]["start"] > 0
    assert control["timer"]["end"] > control["timer"]["start"]
    assert control["notify_data"][_TIMER_IDX]["req"] is True
    assert control["notify_data"][_TIMER_IDX]["shutdown"] is True
    assert control["notify_data"][_TIMER_IDX]["keep_warm"] is False


def test_post_timer_start_fresh_missing_ranges(sio):
    # paused == 0 but no ranges -> Error (partial in-memory mutation not persisted).
    payload = json.dumps({"timer_action": {}})
    resp = sio.mod._post_app_data("timer_action", "start_timer", payload)
    assert resp["result"] == "Error"
    assert resp["message"] == "Error: Start time not specified"


def test_post_timer_start_unpause(sio):
    # paused != 0 path: unpause recomputes end and clears paused.
    control = read_control()
    control["timer"]["paused"] = 100.0
    control["timer"]["end"] = 500.0
    control["timer"]["start"] = 50.0
    write_control(control, WriteKind.OVERWRITE, origin="test-socketio")
    resp = sio.mod._post_app_data("timer_action", "start_timer", json.dumps({"timer_action": {}}))
    assert resp["result"] == "OK"
    _drain()
    control = read_control()
    assert control["timer"]["paused"] == 0
    # end = (old_end - old_paused) + now = 400 + now, so strictly > 400
    assert control["timer"]["end"] > 400
    assert control["notify_data"][_TIMER_IDX]["req"] is True


def test_post_timer_pause(sio):
    resp = sio.mod._post_app_data("timer_action", "pause_timer", json.dumps({"timer_action": {}}))
    assert resp["result"] == "OK"
    _drain()
    control = read_control()
    assert control["timer"]["paused"] > 0
    assert control["notify_data"][_TIMER_IDX]["req"] is False


def test_post_timer_stop(sio):
    # Seed a running timer, then stop it.
    control = read_control()
    control["timer"]["start"] = 10.0
    control["timer"]["end"] = 900.0
    control["timer"]["paused"] = 5.0
    control["notify_data"][_TIMER_IDX]["req"] = True
    control["notify_data"][_TIMER_IDX]["shutdown"] = True
    control["notify_data"][_TIMER_IDX]["keep_warm"] = True
    write_control(control, WriteKind.OVERWRITE, origin="test-socketio")
    resp = sio.mod._post_app_data("timer_action", "stop_timer", json.dumps({"timer_action": {}}))
    assert resp["result"] == "OK"
    _drain()
    control = read_control()
    assert control["timer"]["start"] == 0
    assert control["timer"]["end"] == 0
    assert control["timer"]["paused"] == 0
    assert control["notify_data"][_TIMER_IDX]["req"] is False
    assert control["notify_data"][_TIMER_IDX]["shutdown"] is False
    assert control["notify_data"][_TIMER_IDX]["keep_warm"] is False


def test_post_timer_invalid_type(sio):
    resp = sio.mod._post_app_data("timer_action", "bogus", json.dumps({"timer_action": {}}))
    assert resp["result"] == "Error"
    assert resp["message"] == "Error: Received request without valid type"


def test_timer_action_no_timer_entry_returns_error_without_mutation(sio):
    """Fixed behavior: when notify_data has NO ``type == "timer"`` entry, the
    finder loop must not fall back to a stale/last ``index``. The handler
    returns an Error envelope and leaves the (non-timer) entry untouched."""
    control = read_control()
    control["notify_data"] = [{"type": "probe", "label": "X", "req": True, "shutdown": True, "keep_warm": True}]
    write_control(control, WriteKind.OVERWRITE, origin="test-socketio")
    resp = sio.mod._post_app_data("timer_action", "stop_timer", json.dumps({"timer_action": {}}))
    assert resp["result"] == "Error"
    _drain()
    control = read_control()
    # The non-timer entry at index 0 must be left untouched.
    assert control["notify_data"][0]["req"] is True
    assert control["notify_data"][0]["shutdown"] is True
    assert control["notify_data"][0]["keep_warm"] is True


# =====================================================================
# _post_app_data -- recipes_action
# =====================================================================


def test_post_recipe_delete(sio):
    payload = json.dumps({"recipes_action": {"filename": "foo.pfrecipe"}})
    resp = sio.mod._post_app_data("recipes_action", "recipe_delete", payload)
    assert resp["result"] == "OK"
    assert any(c[0] == "os.system" and "rm " in c[1] and "foo.pfrecipe" in c[1] for c in sio.calls)


def test_post_recipe_delete_falsy_filename_returns_none(sio):
    payload = json.dumps({"recipes_action": {"filename": ""}})
    resp = sio.mod._post_app_data("recipes_action", "recipe_delete", payload)
    assert resp is None


def test_post_recipe_start(sio):
    payload = json.dumps({"recipes_action": {"filename": "foo.pfrecipe"}})
    resp = sio.mod._post_app_data("recipes_action", "recipe_start", payload)
    assert resp["result"] == "OK"
    _drain()
    control = read_control()
    assert control["mode"] == "Recipe"
    assert control["recipe"]["filename"].endswith("foo.pfrecipe")


def test_post_recipe_start_falsy_filename_returns_none(sio):
    payload = json.dumps({"recipes_action": {"filename": ""}})
    resp = sio.mod._post_app_data("recipes_action", "recipe_start", payload)
    assert resp is None


def test_post_recipes_invalid_type(sio):
    resp = sio.mod._post_app_data("recipes_action", "bogus", json.dumps({"recipes_action": {}}))
    assert resp["result"] == "Error"
    assert resp["message"] == "Error: Received request without valid type"


# =====================================================================
# _post_app_data -- probes_action
# =====================================================================


def test_post_probe_update(sio):
    payload = json.dumps({"probes_action": {"label": "Grill", "name": "NewGrill"}})
    resp = sio.mod._post_app_data("probes_action", "probe_update", payload)
    assert resp["result"] == "OK"
    assert read_settings()["probe_settings"]["probe_map"]["probe_info"][0]["name"] == "NewGrill"


def test_post_probe_update_disallowed_key(sio):
    # "port" is not among {name,label,profile_id,enabled} -> Error.
    payload = json.dumps({"probes_action": {"label": "Grill", "port": "ADC0"}})
    resp = sio.mod._post_app_data("probes_action", "probe_update", payload)
    assert resp["result"] == "Error"
    assert resp["message"] == "Error: Missing required argument, probe cannot be updated"


def test_post_probe_update_label_not_found(sio):
    payload = json.dumps({"probes_action": {"label": "NoSuchLabel"}})
    resp = sio.mod._post_app_data("probes_action", "probe_update", payload)
    assert resp["result"] == "Error"
    assert resp["message"] == "Error: Probe was not found"


def test_post_probes_invalid_type(sio):
    resp = sio.mod._post_app_data("probes_action", "bogus", json.dumps({"probes_action": {}}))
    assert resp["result"] == "Error"
    assert resp["message"] == "Error: Received request without valid type"


# =====================================================================
# _post_app_data -- notify_action
# =====================================================================


def test_post_notify_update(sio):
    payload = json.dumps(
        {
            "notify_action": {
                "label": "Grill",
                "target_temp": 225,
                "target_shutdown": True,
                "target_keep_warm": False,
                "target_req": True,
            }
        }
    )
    resp = sio.mod._post_app_data("notify_action", "notify_update", payload)
    assert resp["result"] == "OK"
    _drain()
    control = read_control()
    grill_probe = next(n for n in control["notify_data"] if n["type"] == "probe" and n["label"] == "Grill")
    assert grill_probe["target"] == 225
    assert grill_probe["req"] is True


def test_post_notify_update_missing_label(sio):
    payload = json.dumps({"notify_action": {}})
    resp = sio.mod._post_app_data("notify_action", "notify_update", payload)
    assert resp["result"] == "Error"
    assert resp["message"] == "Error: Request missing probe label"


def test_post_notify_invalid_type(sio):
    resp = sio.mod._post_app_data("notify_action", "bogus", json.dumps({"notify_action": {}}))
    assert resp["result"] == "Error"
    assert resp["message"] == "Error: Received request without valid type"


# =====================================================================
# _post_app_data -- invalid action
# =====================================================================


def test_post_invalid_action(sio):
    resp = sio.mod._post_app_data("bogus_action", "whatever", json.dumps({}))
    assert resp["result"] == "Error"
    assert resp["message"] == "Error: Received request without valid action"
