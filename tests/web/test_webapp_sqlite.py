"""Task 18 (T6): webapp / blueprint free-function path reads+writes SQLite, no Valkey.

The blueprints (blueprints/api, blueprints/dash, common/app.py, etc.) call
`common.common` free functions directly -- they never go through the
`Store` seam exercised by test_datastore*.py / test_common_*.py. This file
proves that path is genuinely backed by SQLite, with valkey-server stopped.

A fresh SQLite DB is seeded BEFORE `app` (the module-level Flask app in the
root app.py) is imported, since app.py performs a settings read at import
time (for log-level setup). That ordering requirement means the seeding
has to happen at module-import time here too, not inside a fixture.
"""

import os
import sys
import tempfile

# --- Seed a fresh SQLite DB BEFORE importing `app` -------------------------
_TMP_DIR = tempfile.mkdtemp(prefix="pifire_test_webapp_")
_DB_PATH = os.path.join(_TMP_DIR, "webapp_test.db")
os.environ["PIFIRE_DB_PATH"] = _DB_PATH

from common import datastore  # noqa: E402
from common.common import (  # noqa: E402
    WriteKind,
    default_control,
    default_pellets,
    default_settings,
    load_wizard_install_info,
    read_connected_users,
    read_current,
    read_history,
    read_settings,
    store_wizard_install_info,
    read_status,
    remove_connected_user,
    write_connected_user,
    write_control,
    write_current,
    write_generic_key,
    write_history,
    write_pellets_store,
    write_settings_store,
    write_status,
)

datastore._reset_for_tests(_DB_PATH)
datastore.init()

_SEEDED_GRILL_NAME = "T18 Seeded Grill"
_seed_settings = default_settings()
_seed_settings["globals"]["grill_name"] = _SEEDED_GRILL_NAME
write_settings_store(_seed_settings)
write_pellets_store(default_pellets())
write_status(read_status(init=True))
write_control(default_control(), WriteKind.OVERWRITE, origin="test")
# read_probe_status() (used by the /api/current route) reads this generic
# key; in production it's populated by the control loop's probe discovery.
write_generic_key("probe_device_info", {})
write_current(
    {
        "probe_history": {"primary": {"Probe1": 225}, "food": {}, "aux": {}},
        "primary_setpoint": 225,
        "notify_targets": {},
    }
)

from app import app as flask_app  # noqa: E402


def setup_function(function):
    # Other test modules' fixtures (`ds`, `db`, etc.) repoint the shared
    # datastore singleton to their own tmp_path DBs and restore it to
    # _ORIGINAL_DB_PATH on teardown -- not back to ours. Since all test
    # modules are collected (and this module's seeding above runs) before
    # any test function anywhere runs, by the time our own test functions
    # execute, other tests interleaved by pytest's run order may have
    # already repointed the datastore elsewhere. Repoint back to our
    # seeded DB before every test in this module so both the free-function
    # assertions and the `flask_app` test-client requests are guaranteed
    # to hit our seeded data regardless of full-suite run order.
    datastore._reset_for_tests(_DB_PATH)


def teardown_module(module):
    datastore._reset_for_tests(None)
    os.environ.pop("PIFIRE_DB_PATH", None)


# --- Goal 2: boot the real app and drive it through blueprint routes -------


def test_api_settings_route_reads_sqlite_via_blueprint():
    flask_app.config.update(TESTING=True)
    client = flask_app.test_client()

    resp = client.get("/api/settings")

    assert resp.status_code == 201
    payload = resp.get_json()
    assert payload["settings"]["globals"]["grill_name"] == _SEEDED_GRILL_NAME


def test_api_current_route_reads_sqlite_via_blueprint():
    flask_app.config.update(TESTING=True)
    client = flask_app.test_client()

    resp = client.get("/api/current")

    assert resp.status_code == 201
    payload = resp.get_json()
    assert payload["current"]["P"]["Probe1"] == 225
    assert payload["current"]["PSP"] == 225


def test_admin_page_renders_with_none_cpu_temp():
    """Regression: the admin CPU card was gated only on
    `'cpu_temp' in control['system'].keys()`, but routes.py stores that key
    with value None whenever the temperature reading is unavailable
    (unsupported platform or a timed-out system command). The template then
    evaluated `cpu_temp < 50`, raising
    `TypeError: '<' not supported between instances of 'NoneType' and 'int'`
    -> HTTP 500 on GET /admin/.

    In this harness there is no control process, so every system command
    times out: get_supported_cmds() returns an ERROR dict and the route's
    cpu_temp block is skipped, letting whatever we seed into
    control['system'] reach the template verbatim. Seeding cpu_temp=None
    reproduces exactly the production crash path."""
    flask_app.config.update(TESTING=True)
    client = flask_app.test_client()

    control = default_control()
    control["system"]["cpu_temp"] = None
    write_control(control, WriteKind.OVERWRITE, origin="test")

    resp = client.get("/admin/")

    # Pre-fix this raised TypeError inside the template -> 500.
    assert resp.status_code == 200
    # The CPU card must be suppressed rather than rendered with a None value.
    assert "CPU Temperature" not in resp.get_data(as_text=True)


def test_admin_page_renders_cpu_card_with_real_cpu_temp():
    """Complement to the None case: when cpu_temp is a real number the card
    renders normally (guards the fix against over-suppressing the card)."""
    flask_app.config.update(TESTING=True)
    client = flask_app.test_client()

    control = default_control()
    control["system"]["cpu_temp"] = 42.0
    write_control(control, WriteKind.OVERWRITE, origin="test")

    resp = client.get("/admin/")

    assert resp.status_code == 200
    assert "CPU Temperature" in resp.get_data(as_text=True)


def test_api_settings_post_writes_through_to_sqlite():
    """Round-trip a write through the blueprint (write_settings) and confirm
    it lands in SQLite by reading it back through common.common directly."""
    flask_app.config.update(TESTING=True)
    client = flask_app.test_client()

    resp = client.post("/api/settings", json={"globals": {"grill_name": "T18 Written Via Blueprint"}})

    assert resp.status_code == 201
    assert resp.get_json()["result"] == "success"
    assert read_settings()["globals"]["grill_name"] == "T18 Written Via Blueprint"


def test_probeconfig_add_usb_hid_probe_not_blocked_by_stale_platform_bus():
    """Regression: adding an ft232h probe in the wizard must not be rejected
    because the *previously saved* platform fan bus is 'basic'. Mid-wizard,
    read_settings() holds the old running config, not the user's in-progress
    selections; the probe step must validate the in-progress probe devices
    only. Cross-subsystem conflicts are enforced at runtime by open_i2c_bus."""
    flask_app.config.update(TESTING=True)
    client = flask_app.test_client()

    # Saved (stale) config: fan controller on the onboard 'basic' bus.
    settings = read_settings()
    settings.setdefault("platform", {}).setdefault("fan_controller", {})["i2c_bus_kind"] = "basic"
    write_settings_store(settings)

    # In-progress wizard state: no probe devices configured yet.
    store_wizard_install_info({"probe_map": {"probe_devices": [], "probe_info": []}})

    resp = client.post(
        "/probeconfig/",
        data={
            "section": "devices",
            "action": "add_device",
            "name": "FT232HProbe",
            "module": "mcp9600_adafruit",
            "probes_devspec_i2c_bus_kind": "ft232h",
        },
    )
    assert resp.status_code == 200

    devices = load_wizard_install_info()["probe_map"]["probe_devices"]
    added = [device for device in devices if device["device"] == "FT232HProbe"]
    assert added, "ft232h probe was rejected even though it is a valid selection"
    assert added[0]["config"]["i2c_bus_kind"] == "ft232h"


def test_i2c_bus_scan_extended_lists_discovered_adapters(monkeypatch):
    flask_app.config.update(TESTING=True)
    client = flask_app.test_client()

    import blueprints.wizard.routes as wizard_routes

    monkeypatch.setattr(
        wizard_routes,
        "discover_extended_i2c_buses",
        lambda: [
            {"bus_num": 7, "name": "MCP2221 usb-i2c bridge", "serial": "AB12"},
            {"bus_num": 1, "name": "onboard adapter", "serial": None},
        ],
    )

    resp = client.post("/wizard/i2c_bus_scan", data={"itemID": "distance_devspec_i2c_bus_num", "kind": "extended"})
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "i2c-7" in body
    assert "i2c-1" in body
    assert "serial:AB12" in body
    assert "By Bus Number" in body
    assert "By Serial" in body


def test_i2c_bus_scan_extended_omits_by_serial_group_when_no_serials(monkeypatch):
    flask_app.config.update(TESTING=True)
    client = flask_app.test_client()

    import blueprints.wizard.routes as wizard_routes

    monkeypatch.setattr(
        wizard_routes,
        "discover_extended_i2c_buses",
        lambda: [{"bus_num": 1, "name": "onboard adapter", "serial": None}],
    )

    resp = client.post("/wizard/i2c_bus_scan", data={"itemID": "distance_devspec_i2c_bus_num", "kind": "extended"})
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "By Bus Number" in body
    assert "By Serial" not in body


def test_i2c_bus_scan_no_devices_shows_error(monkeypatch):
    flask_app.config.update(TESTING=True)
    client = flask_app.test_client()

    import blueprints.wizard.routes as wizard_routes

    monkeypatch.setattr(wizard_routes, "discover_mcp2221_devices", lambda: [])

    resp = client.post("/wizard/i2c_bus_scan", data={"itemID": "distance_devspec_i2c_bus_num", "kind": "mcp2221"})
    assert resp.status_code == 200
    assert "No mcp2221 I2C buses discovered." in resp.get_data(as_text=True)


def test_wizard_modulecard_renders_i2c_bus_num_as_free_text():
    # device_distance_i2c_bus_num / i2c_bus_num (fan controller) live under
    # grillplatform module settings_dependencies (e.g. x86_numato), not under
    # the distance sensor modules themselves.
    flask_app.config.update(TESTING=True)
    client = flask_app.test_client()

    resp = client.post("/wizard/modulecard", data={"module": "x86_numato", "section": "grillplatform"})
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'type="text"' in body
    assert "Discover" in body


def test_wizard_finish_blocks_unworkable_bus_combo():
    """Finish-step whole-config check: a probe on the ft232h bus while the fan
    controller is left on the onboard 'basic' bus is the one unworkable combo.
    The finish step must reject it (and NOT start an install) using the user's
    in-progress selections. Only the conflict path is exercised -- the success
    path launches the installer via os.system."""
    flask_app.config.update(TESTING=True)
    client = flask_app.test_client()

    # The finish branch only runs when the grill is stopped.
    control = default_control()
    control["mode"] = "Stop"
    write_control(control, WriteKind.OVERWRITE, origin="test")

    # In-progress wizard: one probe assigned to the ft232h bus.
    store_wizard_install_info(
        {
            "probe_map": {
                "probe_devices": [{"device": "P1", "module": "mcp9600_adafruit", "config": {"i2c_bus_kind": "ft232h"}}],
                "probe_info": [],
            }
        }
    )

    resp = client.post(
        "/wizard/finish",
        data={
            "grillplatformSelect": "x86_numato",
            "displaySelect": "none",
            "distanceSelect": "none",
            "probes_units": "F",
            # Fan controller left on the onboard 'basic' bus -> conflicts with the
            # ft232h probe. This is the platform selection the per-device step
            # could not see.
            "grillplatform_i2c_bus_kind": "basic",
        },
    )

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "I2C Bus Configuration Error" in body
    assert "Starting Install" not in body  # the install page was not rendered


# --- Goal 3 (always exercised): the common.common free-function path -------
# This is the essential T6 assertion -- it does not depend on the app
# booting, and proves the blueprint-facing read/write functions work
# against SQLite with no Valkey client involved.


def test_settings_free_function_roundtrip():
    settings = default_settings()
    settings["globals"]["grill_name"] = "T18 Free-Function Grill"
    write_settings_store(settings)

    assert read_settings()["globals"]["grill_name"] == "T18 Free-Function Grill"


def test_current_free_function_read():
    current = read_current()

    assert current["P"]["Probe1"] == 225
    assert current["PSP"] == 225


def test_history_free_function_roundtrip():
    before = len(read_history())
    write_history(
        {
            "probe_history": {"primary": {"Probe1": 200}, "food": {}, "aux": {}},
            "primary_setpoint": 200,
            "notify_targets": {},
        }
    )

    history = read_history()

    assert len(history) == before + 1
    assert history[-1]["PSP"] == 200
    assert history[-1]["P"]["Probe1"] == 200


def test_connected_users_socketio_path_roundtrip():
    assert "sid-t18" not in read_connected_users()

    write_connected_user("sid-t18")
    assert "sid-t18" in read_connected_users()

    remove_connected_user("sid-t18")
    assert "sid-t18" not in read_connected_users()


def test_no_pifire_valkey_module_imported():
    # NOTE: `'valkey' in sys.modules` is True by this point, but NOT because
    # of anything in PiFire's own datastore/webapp path. It comes from a
    # third-party transitive import: app.py -> flask_socketio ->
    # python-socketio's `socketio/redis_manager.py`, which unconditionally
    # does `import valkey` at module load time to define an *optional*
    # Redis/Valkey-backed Socket.IO message-queue backend. That backend is
    # never instantiated here -- app.py constructs `SocketIO(app,
    # cors_allowed_origins='*')` with no `message_queue=` argument, and no
    # connection to any Valkey/Redis server is ever attempted (confirmed by
    # the app.py import + every request in this file succeeding with
    # valkey-server stopped).
    #
    # The two modules that are actually PiFire's own Valkey KV-store client
    # code (common/valkey_queue.py, common/valkey_handler.py) are dead code
    # slated for removal in Task 19 and are not imported by anything on the
    # webapp/blueprint path exercised above. That's the meaningful
    # assertion for "no Valkey present" here: PiFire's own code never
    # reaches for a Valkey client.
    assert "common.valkey_queue" not in sys.modules
    assert "common.valkey_handler" not in sys.modules


def test_settings_display_post_sets_sleep_timeout():
    from app import app as flask_app

    client = flask_app.test_client()
    client.post("/settings/display", data={"sleep_timeout": "123"})
    assert read_settings()["display"]["sleep_timeout"] == 123


def test_settings_display_post_clamps_negative():
    from app import app as flask_app

    client = flask_app.test_client()
    client.post("/settings/display", data={"sleep_timeout": "-9"})
    assert read_settings()["display"]["sleep_timeout"] == 0


def test_settings_display_post_blank_does_not_500_or_change_value():
    from app import app as flask_app

    client = flask_app.test_client()
    client.post("/settings/display", data={"sleep_timeout": "77"})
    assert read_settings()["display"]["sleep_timeout"] == 77

    resp = client.post("/settings/display", data={"sleep_timeout": ""})
    assert resp.status_code != 500
    assert read_settings()["display"]["sleep_timeout"] == 77


def test_settings_display_post_non_numeric_does_not_500_or_change_value():
    from app import app as flask_app

    client = flask_app.test_client()
    client.post("/settings/display", data={"sleep_timeout": "77"})
    assert read_settings()["display"]["sleep_timeout"] == 77

    resp = client.post("/settings/display", data={"sleep_timeout": "abc"})
    assert resp.status_code != 500
    assert read_settings()["display"]["sleep_timeout"] == 77
