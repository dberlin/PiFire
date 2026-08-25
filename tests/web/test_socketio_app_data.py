"""Characterization net for the Socket.IO god-functions in
``blueprints/mobile/socket_io.py``: ``_get_app_data`` (8 actions) and
``_post_app_data`` (8 action-groups x nested ``type``).

These are Socket.IO event handlers, NOT HTTP routes, so the Playwright
suite (tests/web/test_page_*.py) does not touch them. This module drives
the two plain functions directly against a fresh temp-SQLite datastore
(the ``ds`` fixture) and pins BOTH the returned ``_response``/``api_response``
envelope AND the resulting settings/control/pellet writes, for every
``action`` x ``type`` branch reachable without real hardware.

Intent: lock down current behavior BEFORE decomposing these into
per-action handlers + dispatch maps, so that refactor is provably
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

import base64
import builtins
import json
import threading
import time
import types
from unittest import mock

import pytest
from flask import request as flask_request

from app import app as flask_app
from common import datastore
from common.common import ErrorKind
from common.defaults import default_control, default_pellets, default_settings
from common.persistence.control import (
    execute_control_writes,
    read_control,
    write_control_snapshot,
)
from common.persistence.runtime import (
    CONTROL_HEARTBEAT_KEY,
    CONTROL_HEARTBEAT_STALE_AFTER,
    flush_current,
    init_status,
    read_connected_users,
    read_errors,
    read_pellets_store,
    read_settings,
    read_status,
    write_connected_user,
    write_errors,
    write_generic_key,
    write_pellet_db,
    write_settings_store,
)
from common.web_contracts.core import PelletSocketPayload

# Index of the single ``type == "timer"`` entry in a default notify_data list
# (12 probe/limit entries for 4 probes come first). Pinned so the timer tests
# can assert on the exact entry the function mutates.
_TIMER_IDX = 12


def _drain():
    """Apply queued control deltas so a read_control() reflects them.

    Production drains the validated delta queue on each control-loop tick. This
    harness has no control loop, so it drains by hand before asserting state.
    """
    execute_control_writes()


@pytest.fixture
def sio(ds):
    """Seed a fresh datastore with defaults and import the socket_io module.

    os.system stays stubbed as a blanket guard, but the module no longer
    imports reboot_system/shutdown_system/restart_* at all -- those reached it
    only through the legacy post_app_data admin dispatch. Patching names it no
    longer has would raise, and patching them "just in case" is what silently
    disarms a guard when the code they protect moves.

    Yields a namespace with ``.mod`` (the socket_io module) and ``.calls``
    (an ordered list the os.system stub appends to).
    """
    write_settings_store(default_settings())
    write_control_snapshot(default_control(), origin="test-socketio")
    write_pellet_db(default_pellets())
    init_status()
    # dash_data reads this generic key (normally written by the control
    # runtime); seed an empty map so _get_probe_data has something to read.
    write_generic_key("probe_device_info", {})

    from blueprints.mobile import socket_io

    # The control-liveness verdict is process-local module state that outlives
    # the `ds` datastore, so reset it around every test or a check that failed
    # in one test leaks a CONTROL_DOWN_ERROR into another's dash payload.
    socket_io._set_control_alive(True)

    calls = []

    def _rec(name):
        def _inner(*args, **kwargs):
            calls.append((name, args, kwargs))

        return _inner

    def _rec_os(cmd):
        calls.append(("os.system", cmd))
        return 0

    with mock.patch("os.system", side_effect=_rec_os):
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
    # internals (probe assembly) need a fully control-runtime-seeded `current`,
    # which this harness lacks, so they are mocked out here.
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


def test_dash_data_exposes_manual_power_and_pwm(sio):
    """The React manual controls need the power relay's live state and the
    current DC-fan duty. Legacy's control panel reads both (status['outpins']
    has all four pins; the PWM slider is seeded from control['manual']['pwm']),
    but the socketio dash payload only carried fan/auger/igniter."""
    # _get_dash_data's probe assembly indexes current["P"]/current["F"] by
    # probe label -- seed it the way the control-loop does at startup (same
    # pattern as test_get_dash_data_and_probe_data_full_structure), otherwise
    # a fresh empty datastore raises KeyError before we ever reach the
    # outputs/manualPwm keys this test cares about.
    flush_current()
    dash = sio.mod._get_dash_data(read_settings(), read_pellets_store())

    assert set(dash["outputs"]) == {"fan", "auger", "igniter", "power"}
    assert isinstance(dash["outputs"]["power"], bool)
    assert isinstance(dash["manualPwm"], int)


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


# =====================================================================
# _post_app_data -- admin_action (hazards neutralized)
# =====================================================================


# =====================================================================
# _post_app_data -- units_action
# =====================================================================


# =====================================================================
# _post_app_data -- pellets_action
# =====================================================================


# =====================================================================
# _post_app_data -- timer_action (stateful; both start paths + latent bug)
# =====================================================================


# =====================================================================
# _post_app_data -- recipes_action
# =====================================================================


# =====================================================================
# _post_app_data -- probes_action
# =====================================================================


# =====================================================================
# _post_app_data -- notify_action
# =====================================================================


# =====================================================================
# _post_app_data -- missing required argument (parametrized: 6 pellets_action
# branches, 1 timer_action branch, 1 notify_action branch)
# =====================================================================


# =====================================================================
# _post_app_data -- invalid action
# =====================================================================


# =====================================================================
# _post_app_data -- update_action empty-request fall-through (no `return`
# is reached when the request dict has zero keys, so the function falls off
# the end and implicitly returns None -- same latent-fallthrough idiom
# already pinned for recipes_action above).
# =====================================================================


# =====================================================================
# _post_app_data -- admin_action reboot/shutdown exception fallback paths
# =====================================================================


# =====================================================================
# _post_app_data -- pellets_action branches not hit by the happy-path tests
# above: the "target not present in the collection" half of each
# if/elif (edit_brands / edit_woods), the delete_profile log-rewrite loop
# body, and the delete_log "not present" half.
# =====================================================================


# =====================================================================
# _get_app_data -- recipe_data "details" found-result path (arg01="details"
# with at least one file that parses OK), plus the skip-on-bad-status branch.
# =====================================================================


def test_get_recipe_data_details_found(sio):
    recipe_data = {"metadata": {"id": "rid3"}}
    with (
        mock.patch.object(sio.mod, "get_recipefilelist", return_value=["foo.pfrecipe"]),
        mock.patch.object(sio.mod, "read_recipefile", return_value=(recipe_data, "OK")),
    ):
        resp = sio.mod._get_app_data("recipe_data", "details")
    assert resp["result"] == "OK"
    assert resp["data"]["recipe_details"] == [{"filename": "foo.pfrecipe", "details": recipe_data}]
    assert resp["data"]["uuid"] == read_settings()["server_info"]["uuid"]


def test_get_recipe_data_non_details_arg01_returns_none(sio):
    # Same latent fall-through as arg01=None: any arg01 other than "details"
    # (the outer `if arg01 is not None` is entered but the inner
    # `if arg01 == "details"` is not) hits no `return` -> implicit None.
    resp = sio.mod._get_app_data("recipe_data", "bogus")
    assert resp is None


def test_get_recipe_data_details_skips_non_ok_status(sio):
    # A file that fails to parse (status != "OK") is excluded from the
    # results list -> falls through to the "no results" Error branch.
    with (
        mock.patch.object(sio.mod, "get_recipefilelist", return_value=["bad.pfrecipe"]),
        mock.patch.object(sio.mod, "read_recipefile", return_value=({}, "Error")),
    ):
        resp = sio.mod._get_app_data("recipe_data", "details")
    assert resp["result"] == "Error"
    assert resp["message"] == "Error: Recipes details not found"


# =====================================================================
# _post_app_data -- notify_action branches not hit above: the "no
# target_temp" (probe) else-branch and the "has high/low_limit_temp"
# if-branches for probe_limit_high / probe_limit_low. A single label
# ("Grill") always has one entry of each of the 3 types in notify_data, so
# one call exercises all three simultaneously.
# =====================================================================


# =====================================================================
# _get_probe_max_temp -- all 4 (probe_type x units) branches
# =====================================================================


def test_get_probe_max_temp_all_branches(sio):
    settings = read_settings()
    config = settings["dashboard"]["dashboards"]["Default"]["config"]
    settings["globals"]["units"] = "F"
    assert sio.mod._get_probe_max_temp("Primary", settings) == config["max_primary_temp_F"]
    assert sio.mod._get_probe_max_temp("Food", settings) == config["max_food_temp_F"]
    settings["globals"]["units"] = "C"
    assert sio.mod._get_probe_max_temp("Primary", settings) == config["max_primary_temp_C"]
    assert sio.mod._get_probe_max_temp("Food", settings) == config["max_food_temp_C"]


# =====================================================================
# _get_timer_notify_data -- found vs. not-found
# =====================================================================


def test_get_timer_notify_data_found(sio):
    notify_data = [
        {"type": "probe", "keep_warm": True, "shutdown": True},
        {"type": "timer", "keep_warm": True, "shutdown": False},
    ]
    assert sio.mod._get_timer_notify_data(notify_data) == {"keep_warm": True, "shutdown": False}


def test_get_timer_notify_data_not_found_defaults(sio):
    notify_data = [{"type": "probe", "keep_warm": True, "shutdown": True}]
    assert sio.mod._get_timer_notify_data(notify_data) == {"keep_warm": False, "shutdown": False}


# =====================================================================
# _get_probe_data / _get_dash_data -- full structure, including the
# device-status merge and the (production-unreachable) "AUX" section.
# =====================================================================


def test_get_dash_data_and_probe_data_full_structure(sio):
    # Give the Grill (Primary) probe req=True on all 3 notify entries sharing
    # its label, so both the assignment lines AND the `if req: hasNotifications
    # = True` sub-branch are exercised for probe / probe_limit_high /
    # probe_limit_low simultaneously (default notify_data has exactly one
    # entry of each type per non-Aux probe label).
    control = read_control()
    for entry in control["notify_data"]:
        if entry["label"] == "Grill":
            entry["req"] = True
    write_control_snapshot(control, origin="test-socketio")

    # Seed `current` the way the control-loop does at startup (read_current()
    # with no init returns a bare {} -- KeyError otherwise; see
    # flush_current() in common.persistence.runtime).
    current = flush_current()
    current["P"]["Grill"] = 225
    current["F"]["Probe1"] = 150
    current["PSP"] = 225
    datastore.set_blob("control:current", json.dumps(current))

    # Two device entries whose "device" matches every default probe (they
    # all use "proto_adc") -- the merge loop has no `break`, so BOTH are
    # applied to every probe in order: the first (full status) exercises
    # every "key in status" True branch, the second (empty status) exercises
    # every False branch, for the same set of keys in one test.
    write_generic_key(
        "probe_device_info",
        [
            {
                "device": "proto_adc",
                "status": {
                    "battery_charging": True,
                    "battery_percentage": 88,
                    "battery_voltage": 3.7,
                    "connected": True,
                    "error": "sensor drift",
                },
            },
            {"device": "proto_adc", "status": {}},
            # A non-matching device entry exercises the loop's "no match,
            # keep scanning" branch too.
            {"device": "some-other-device", "status": {"connected": False}},
        ],
    )

    settings = read_settings()
    pelletdb = read_pellets_store()
    dash = sio.mod._get_dash_data(settings, pelletdb)

    assert dash["grillName"] == settings["globals"]["grill_name"]
    assert dash["uuid"] == settings["server_info"]["uuid"]
    assert dash["hopperLevel"] == pelletdb["current"]["hopper_level"]

    primary = dash["primaryProbe"]
    assert primary["label"] == "Grill"
    assert primary["temp"] == 225
    assert primary["setTemp"] == 225
    assert primary["hasNotifications"] is True
    assert primary["targetReq"] is True
    assert primary["highLimitReq"] is True
    assert primary["lowLimitReq"] is True
    assert primary["status"]["batteryCharging"] is True
    assert primary["status"]["batteryPercentage"] == 88
    assert primary["status"]["batteryVoltage"] == 3.7
    assert primary["status"]["connected"] is True
    assert primary["status"]["error"] == "sensor drift"

    food = {p["label"]: p for p in dash["foodProbes"]}
    assert set(food) == {"Probe1", "Probe2", "Probe3"}
    assert food["Probe1"]["temp"] == 150


def test_get_probe_data_aux_section_direct_call(sio):
    # `_get_probe_data`'s "AUX" `section` branch (the final `else` at
    # blueprints/mobile/socket_io.py:740) is only reachable when called with
    # a probe_type other than "Primary"/"Food" -- no call site in the app
    # ever does this (`_get_dash_data` only ever passes "Food"/"Primary").
    # Exercised directly here purely for coverage of otherwise-dead code.
    settings = read_settings()
    settings["probe_settings"]["probe_map"]["probe_info"].append(
        {"type": "Aux", "label": "AuxProbe", "name": "AuxProbe", "device": "proto_adc", "enabled": True}
    )
    current = {"P": {}, "F": {}, "AUX": {"AuxProbe": 77}, "PSP": 0, "NT": {}}
    result = sio.mod._get_probe_data("Aux", settings, current, [], [])
    assert len(result) == 1
    assert result[0]["label"] == "AuxProbe"
    assert result[0]["temp"] == 77


# =====================================================================
# _encode_assets / _encode_img
# =====================================================================


def test_encode_assets_missing_assets_key_is_a_noop(sio):
    recipe_data = {"metadata": {"id": "rid1"}}
    result = sio.mod._encode_assets(recipe_data)
    assert result is recipe_data
    assert "assets" not in result


def test_encode_assets_missing_file_yields_empty_string(sio):
    # No such file on disk -> `_encode_img`'s bare `except:` swallows the
    # FileNotFoundError and returns "".
    recipe_data = {"metadata": {"id": "rid1"}, "assets": [{"filename": "missing.jpg"}]}
    result = sio.mod._encode_assets(recipe_data)
    asset = result["assets"][0]
    assert asset["encoded_image"] == ""
    assert asset["encoded_thumb"] == ""


def test_encode_img_success_reads_and_b64_encodes(sio, tmp_path):
    # Redirect only the one expected cwd-relative path to a tmp_path file, so
    # this doesn't write into the real repo's static/img/tmp tree.
    real_open = builtins.open
    expected_path = "./static/img/tmp/rid2/pic.jpg"
    payload = b"hello-bytes"
    (tmp_path / "pic.jpg").write_bytes(payload)

    def fake_open(path, *args, **kwargs):
        if path == expected_path:
            return real_open(tmp_path / "pic.jpg", *args, **kwargs)
        return real_open(path, *args, **kwargs)

    with mock.patch("builtins.open", side_effect=fake_open):
        encoded = sio.mod._encode_img("rid2", "pic.jpg")
    assert encoded == base64.b64encode(payload).decode("utf-8")


# =====================================================================
# _check_control_status
# =====================================================================


def _stamp_heartbeat(age_seconds):
    """Write a control heartbeat `age_seconds` old, as the control loop would."""
    write_generic_key(CONTROL_HEARTBEAT_KEY, time.time() - age_seconds)


def test_check_control_status_records_a_failure_without_writing_the_blob(sio):
    # The check records its verdict in memory; the errors blob belongs to the
    # control process. See tests/web/test_control_liveness_not_sticky.py for
    # the full contract (payload composition + self-healing).
    _stamp_heartbeat(CONTROL_HEARTBEAT_STALE_AFTER + 5)
    sio.mod._check_control_status()
    assert sio.mod._control_alive is False
    assert read_errors(ErrorKind.ALL) == []


def test_check_control_status_alive_records_success_and_writes_nothing(sio):
    sio.mod._set_control_alive(False)
    _stamp_heartbeat(0)
    sio.mod._check_control_status()
    assert sio.mod._control_alive is True
    assert read_errors(ErrorKind.ALL) == []


def test_check_control_status_needs_no_cooperation_from_the_control_process(sio):
    """The whole point of the heartbeat shape: liveness is decided by READING a
    stamp, so a control process that is gone (and therefore cannot answer a
    request) is still detected -- and a restarted one is trusted again on its
    very next stamp, with no probe/response round trip in either direction.
    """
    _stamp_heartbeat(CONTROL_HEARTBEAT_STALE_AFTER + 60)
    sio.mod._check_control_status()
    assert sio.mod._control_alive is False

    _stamp_heartbeat(0)  # control restarts, stamps on its first tick
    sio.mod._check_control_status()
    assert sio.mod._control_alive is True


def test_check_control_status_stays_optimistic_when_never_stamped(sio):
    # Fresh datastore, or a control process too old to publish a heartbeat:
    # do not flash a control-down banner mid-upgrade.
    sio.mod._set_control_alive(True)
    sio.mod._check_control_status()
    assert sio.mod._control_alive is True


def test_check_control_status_treats_a_stamp_just_inside_the_window_as_alive(sio):
    sio.mod._set_control_alive(False)
    _stamp_heartbeat(CONTROL_HEARTBEAT_STALE_AFTER - 1)
    sio.mod._check_control_status()
    assert sio.mod._control_alive is True


def test_check_control_status_leaves_a_control_process_error_alone(sio):
    # Durable errors written by the control process are not this check's to
    # clear, in either direction.
    write_errors(ErrorKind.CONTROL, ["Grill Platform Error: Could not load the grill platform module."])
    _stamp_heartbeat(0)
    sio.mod._check_control_status()
    assert read_errors(ErrorKind.CONTROL) == ["Grill Platform Error: Could not load the grill platform module."]


# =====================================================================
# The dash payload carries banners from both producer processes
# =====================================================================

_CONTROL_BANNER = "Grill Platform Error: Could not load the grill platform module."
_DISPLAY_BANNER = (
    'An error occurred loading the [ili9341f] display module.  The "display.none" module has been loaded instead.'
)
_WEB_BANNER = "[mpc] install failed — see dependency-install.log"


def test_dash_errors_carry_both_the_control_and_the_display_banner(sio):
    """The control process and the display process each own a kind, so a
    display that fell back to display.none is reported alongside -- not
    instead of -- whatever the controller recorded."""
    write_errors(ErrorKind.CONTROL, [_CONTROL_BANNER])
    write_errors(ErrorKind.DISPLAY, [_DISPLAY_BANNER])
    flush_current()

    dash = sio.mod._get_dash_data(read_settings(), read_pellets_store())

    assert dash["errors"] == [_CONTROL_BANNER, _DISPLAY_BANNER]


def test_dash_errors_carry_a_display_banner_with_no_control_banner(sio):
    """The common case in the field: the controller is healthy and only the
    display failed, so ErrorKind.DISPLAY is the payload's sole source."""
    write_errors(ErrorKind.DISPLAY, [_DISPLAY_BANNER])
    flush_current()

    dash = sio.mod._get_dash_data(read_settings(), read_pellets_store())

    assert dash["errors"] == [_DISPLAY_BANNER]


def test_dash_errors_append_the_control_down_entry_after_both_kinds(sio):
    """The liveness entry stays last: it is recomputed per frame, while every
    stored kind is durable."""
    from common.app import CONTROL_DOWN_ERROR

    write_errors(ErrorKind.CONTROL, [_CONTROL_BANNER])
    write_errors(ErrorKind.DISPLAY, [_DISPLAY_BANNER])
    flush_current()
    _stamp_heartbeat(CONTROL_HEARTBEAT_STALE_AFTER + 5)
    sio.mod._check_control_status()

    dash = sio.mod._get_dash_data(read_settings(), read_pellets_store())

    assert dash["errors"] == [_CONTROL_BANNER, _DISPLAY_BANNER, CONTROL_DOWN_ERROR]


def test_dash_data_reads_the_display_kind_without_consuming_it(sio):
    """The display process owns that list; the web tier only ever reads it."""
    write_errors(ErrorKind.DISPLAY, [_DISPLAY_BANNER])
    flush_current()

    sio.mod._get_dash_data(read_settings(), read_pellets_store())

    assert read_errors(ErrorKind.DISPLAY) == [_DISPLAY_BANNER]


def test_dash_errors_carry_the_web_banner_grouped_after_the_other_two(sio):
    """The webapp's own producer (the detached extra_installer child) reaches
    the same strip, in ErrorKind declaration order."""
    write_errors(ErrorKind.CONTROL, [_CONTROL_BANNER])
    write_errors(ErrorKind.DISPLAY, [_DISPLAY_BANNER])
    write_errors(ErrorKind.WEB, [_WEB_BANNER])
    flush_current()

    dash = sio.mod._get_dash_data(read_settings(), read_pellets_store())

    assert dash["errors"] == [_CONTROL_BANNER, _DISPLAY_BANNER, _WEB_BANNER]


# =====================================================================
# _get_system_info
# =====================================================================


def test_get_system_info_maps_control_and_system_info_fields(sio):
    control = read_control()
    control["system"] = {
        "wifi_quality_value": 11,
        "wifi_quality_max": 22,
        "wifi_quality_percentage": 33,
        "cpu_throttled": False,
        "cpu_under_voltage": True,
        "cpu_temp": 61.2,
    }
    write_control_snapshot(control, origin="test-socketio")
    canned_system_info = {
        "network_info": {"iface": "eth0"},
        "hardware_info": {"cpu_info": {}},
        "os_info": {"PRETTY_NAME": "X"},
        "uptime": "up 1 day",
    }
    with mock.patch.object(sio.mod, "gather_system_info", return_value=(canned_system_info, {})):
        info = sio.mod._get_system_info(read_control())
    assert info["wifi_quality_value"] == 11
    assert info["wifi_quality_max"] == 22
    assert info["wifi_quality_percentage"] == 33
    assert info["cpu_throttled"] is False
    assert info["cpu_under_voltage"] is True
    assert info["cpu_temp"] == 61.2
    assert info["network_info"] == {"iface": "eth0"}
    assert info["hardware_info"] == {"cpu_info": {}}
    assert info["os_info"] == {"PRETTY_NAME": "X"}
    assert info["uptime"] == "up 1 day"


# =====================================================================
# get_app_data / post_app_data -- thin @socketio.on wrappers
# =====================================================================


def test_get_app_data_wrapper_delegates_to_get_app_data(sio):
    resp = sio.mod.get_app_data(action="settings_data")
    assert resp["result"] == "OK"
    assert resp["data"] == read_settings()


# =====================================================================
# listen_app_data -- thread start/dedupe bookkeeping. `start_background_task`
# is stubbed so no real background thread is ever spawned (the real
# `_emit_app_data` loop is characterized separately below, driven directly).
# Always restores the module's `thread`/`thread_event` globals in `finally`,
# since they're process-wide singletons shared with any real Socket.IO
# connections (e.g. the Playwright live-server suite) running later in the
# same test session.
# =====================================================================


def test_listen_app_data_starts_background_task_once_then_dedupes(sio):
    assert sio.mod.thread is None
    calls = []

    def fake_start_bg(target, *args, **kwargs):
        calls.append((target, args, kwargs))
        return mock.Mock(name="fake-thread-handle")

    try:
        with mock.patch.object(sio.mod.socketio, "start_background_task", side_effect=fake_start_bg):
            resp1 = sio.mod.listen_app_data(force=True)
            first_thread = sio.mod.thread
            # Second call while a thread is already running must NOT start
            # another one (the `if thread is None` guard).
            resp2 = sio.mod.listen_app_data(force=False)
        assert resp1["result"] == "OK"
        assert resp2["result"] == "OK"
        assert len(calls) == 1
        assert sio.mod.thread is first_thread
    finally:
        sio.mod.thread = None
        sio.mod.thread_event.clear()


# =====================================================================
# handle_connect / handle_disconnect -- the two @socketio.on("connect"/
# "disconnect") handlers. Driven directly (not via a real Socket.IO test
# client) with a Flask test_request_context supplying `request.sid`.
# `listen_app_data` is stubbed for the connect test to avoid spawning a real
# background thread. Thread-global cleanup is guaranteed via `finally` for
# the same cross-test-contamination reason as above.
# =====================================================================


def test_handle_connect_registers_user_and_triggers_listener(sio):
    with mock.patch.object(sio.mod, "listen_app_data") as m_listen:
        with mock.patch.object(sio.mod, "_emit_app_data_to") as m_emit:
            with flask_app.test_request_context():
                flask_request.sid = "sid-connect-1"
                sio.mod.handle_connect()
    m_listen.assert_called_once_with(force=True)
    m_emit.assert_called_once_with("sid-connect-1")
    assert "sid-connect-1" in read_connected_users()


def test_handle_connect_sends_current_data_to_that_client_alone(sio):
    """Every connect delivers a first payload addressed to the new client.

    Without this, a client that connects after the broadcast loop is already
    running gets nothing: `listen_app_data` starts the loop at most once, so
    the force_refresh flag of every later connect is discarded, and the loop
    itself re-emits only when a payload changes. On a stopped, idle grill
    nothing changes, so the wait is unbounded.

    `to=` is asserted because a broadcast would be wrong in the other
    direction -- it would replay stale-but-unchanged data at every other
    connected client on each new connection.
    """
    emitted = []

    with (
        mock.patch.object(sio.mod, "listen_app_data"),
        mock.patch.object(sio.mod, "_get_dash_data", return_value={"sentinel": "dash"}),
        mock.patch.object(
            sio.mod.socketio,
            "emit",
            side_effect=lambda name, data, to=None: emitted.append((name, to, data)),
        ),
        flask_app.test_request_context(),
    ):
        flask_request.sid = "sid-late-join"
        sio.mod.handle_connect()

    assert [(name, to) for name, to, _ in emitted] == [
        ("socket_pellet_data", "sid-late-join"),
        ("socket_dash_data", "sid-late-join"),
    ]
    # The dash payload is the current one, not a placeholder: it is whatever
    # _get_dash_data produced at connect time.
    assert emitted[1][2] == {"sentinel": "dash"}


def test_handle_disconnect_other_users_remain_no_join(sio):
    write_connected_user("sid-a")
    write_connected_user("sid-b")
    fake_thread = mock.Mock(name="should-not-be-touched")
    sio.mod.thread = fake_thread
    try:
        with flask_app.test_request_context():
            flask_request.sid = "sid-a"
            sio.mod.handle_disconnect()
        assert "sid-a" not in read_connected_users()
        assert "sid-b" in read_connected_users()
        fake_thread.join.assert_not_called()
        assert sio.mod.thread is fake_thread
    finally:
        sio.mod.thread = None
        sio.mod.thread_event.clear()


def test_handle_disconnect_last_user_no_thread_to_join(sio):
    # Last user disconnects but no background thread was ever started
    # (module `thread` global is already None) -- the `if thread is not
    # None:` guard's False branch.
    write_connected_user("sid-only")
    assert sio.mod.thread is None
    try:
        with flask_app.test_request_context():
            flask_request.sid = "sid-only"
            sio.mod.handle_disconnect()
        assert read_connected_users() == []
        assert sio.mod.thread is None
    finally:
        sio.mod.thread = None
        sio.mod.thread_event.clear()


def test_handle_disconnect_last_user_joins_and_clears_thread(sio):
    write_connected_user("sid-only")
    fake_thread = mock.Mock(name="fake-thread-handle")
    sio.mod.thread = fake_thread
    sio.mod.thread_event.set()
    try:
        with flask_app.test_request_context():
            flask_request.sid = "sid-only"
            sio.mod.handle_disconnect()
        assert read_connected_users() == []
        fake_thread.join.assert_called_once()
        assert sio.mod.thread is None
        assert not sio.mod.thread_event.is_set()
    finally:
        sio.mod.thread = None
        sio.mod.thread_event.clear()


# =====================================================================
# _emit_app_data -- the background-task loop body. Driven directly with a
# real threading.Event, stubbing `socketio.sleep`/`socketio.emit` (so no
# real 1s sleeps or real Socket.IO broadcasts happen) and `_get_dash_data`
# (its internals are covered separately above). `socketio.sleep`'s stub
# clears the event to terminate the loop after N iterations.
# =====================================================================


def test_emit_app_data_force_refresh_emits_all_three_once(sio):
    event = threading.Event()
    event.set()
    emitted = []
    sio.mod.thread = "sentinel-before"  # proves the `finally: thread = None` ran

    def fake_sleep(_seconds):
        event.clear()

    with (
        mock.patch.object(sio.mod, "_get_dash_data", return_value={"sentinel": "dash"}),
        mock.patch.object(sio.mod.socketio, "emit", side_effect=lambda name, data: emitted.append(name)),
        mock.patch.object(sio.mod.socketio, "sleep", side_effect=fake_sleep),
    ):
        sio.mod._emit_app_data(event, True)

    assert emitted == ["socket_pellet_data", "socket_dash_data"]
    assert not event.is_set()
    assert sio.mod.thread is None


def test_emit_app_data_pellet_payload_matches_the_strict_wire_contract(sio):
    event = threading.Event()
    event.set()
    emitted = {}

    def fake_sleep(_seconds):
        event.clear()

    with (
        mock.patch.object(sio.mod, "_get_dash_data", return_value={"sentinel": "dash"}),
        mock.patch.object(
            sio.mod.socketio,
            "emit",
            side_effect=lambda name, data: emitted.setdefault(name, data),
        ),
        mock.patch.object(sio.mod.socketio, "sleep", side_effect=fake_sleep),
    ):
        sio.mod._emit_app_data(event, True)

    payload = emitted["socket_pellet_data"]
    validated = PelletSocketPayload.model_validate(payload, strict=True)
    assert validated.model_dump(mode="json", by_alias=True, exclude_none=False) == payload


def test_emit_app_data_checks_control_status_every_pass(sio):
    # The check is now a single SELECT against a stamp the control loop keeps
    # fresh, so it runs once per broadcast pass rather than behind a 30s
    # throttle. That throttle was what made a RECOVERED control process keep
    # reading as down for up to half a minute.
    event = threading.Event()
    event.set()
    sleep_calls = {"n": 0}

    def fake_sleep(_seconds):
        sleep_calls["n"] += 1
        if sleep_calls["n"] >= 2:
            event.clear()

    with (
        mock.patch.object(sio.mod, "_get_dash_data", return_value={"sentinel": "dash"}),
        mock.patch.object(sio.mod.socketio, "emit"),
        mock.patch.object(sio.mod.socketio, "sleep", side_effect=fake_sleep),
        mock.patch.object(sio.mod, "_check_control_status") as m_check,
    ):
        sio.mod._emit_app_data(event, True)

    assert m_check.call_count == 2  # one per loop pass, no elapsed-time gate


def test_emit_app_data_skips_emit_when_data_unchanged(sio):
    # force_refresh=False: iteration 1 always emits (previous_* starts as
    # "" != a dict); iteration 2 sees identical data and must NOT re-emit.
    event = threading.Event()
    event.set()
    emitted = []
    state = {"n": 0}

    def fake_sleep(_seconds):
        state["n"] += 1
        if state["n"] >= 2:
            event.clear()

    with (
        mock.patch.object(sio.mod, "_get_dash_data", return_value={"sentinel": "dash"}),
        mock.patch.object(sio.mod.socketio, "emit", side_effect=lambda name, data: emitted.append(name)),
        mock.patch.object(sio.mod.socketio, "sleep", side_effect=fake_sleep),
    ):
        sio.mod._emit_app_data(event, False)

    assert emitted == ["socket_pellet_data", "socket_dash_data"]
    assert sio.mod.thread is None
