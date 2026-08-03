"""Every writer OUTSIDE the settings blueprint must produce a strictly-valid
settings tree.

These tests call validate_settings_tree() on the store's tree (or on the
value about to be persisted) after each writer runs, independently of
write_settings()'s own enforcement gate (see
tests/unit/common/test_write_settings_strict.py), so handler bugs surface
here directly -- validate_settings_tree() is used purely as a test oracle in
this file.

Scope: blueprints/api_admin/routes.py, blueprints/mobile/socket_io.py,
common/api_commands.py, blueprints/api_wizard/routes.py,
blueprints/api/routes.py, common/app.py, notify/notifications.py,
display/_base_flex.py, updater.py, wizard.py, plus the settings-migration
matrix (common/settings_migration.py).

blueprints/admin/routes.py, blueprints/history/routes.py and
blueprints/dash/routes.py -- the original legacy page blueprints this file
was scoped against -- were retired by the Flask-retirement pass. The
admin write sites were ported forward onto blueprints/api_admin/routes.py
(same handlers, JSON in/out) and are exercised there below. history and
dash have no write-capable kept equivalent at all (see the comment above
the old history/dash section, kept as a marker of what was deliberately
dropped and why); their only surviving write path is the generic
POST /api/settings_update delta merge that blueprints/api/routes.py
already covers here and in tests/web/test_api_settings_update.py.

Harness: reuses the shared `ds` fixture (tests/conftest.py, function-scoped
temp-SQLite datastore) + plain `flask_app.test_client()` pattern for every
HTTP-routed writer (proven by tests/web/test_api_settings_update.py) --
real Flask app, real routes.py dispatch, real write_settings()/SQLite
round-trip, no Playwright/Chromium (agent envs skip [chromium] tests, per
project convention). Socket.IO handlers are driven directly as plain
functions (they are not HTTP routes), mirroring
tests/web/test_socketio_app_data.py's `sio` fixture. `ds`'s "fresh"
datastore is seeded from cwd `./settings.json` (untracked/gitignored,
developer-machine-specific) -- see that module's docstring. Sections that
only assert "still strict after this write" tolerate that seeding as-is;
sections that assert exact round-tripped VALUES (units
conversion, migration matrix) explicitly reseed a canonical
`default_settings()` first, so they never depend on the local machine's file.

SAFETY (grepped for os.system/subprocess/reboot/shutdown across every module
this file exercises):
  - blueprints/api_admin/routes.py: os.system() only via os.remove-free log
    helpers (no shell), plus restart_scripts() on a settings restore. The
    `admin_client` fixture below patches
    blueprints.api_admin.routes.restart_scripts and the global os.system,
    mirroring tests/web/test_api_admin_backups.py's `env` fixture. Nothing
    destructive runs.
  - blueprints/mobile/socket_io.py: os.system() (clear_events/
    recipe_delete) + reboot_system()/shutdown_system()/
    restart_control()/restart_webapp()/restart_scripts(). The `sio` fixture
    below patches the same module-level names, mirroring
    tests/web/test_socketio_app_data.py's `sio` fixture. Nothing destructive
    runs.
  - blueprints/api_wizard/routes.py: os.system(f"{python_exec} wizard.py &")
    only on the `finish` action, which this file never exercises (only
    `cancel`, a pure settings write with no os.system on its path).
  - wizard.py / updater.py: real subprocess.Popen/subprocess.run calls behind
    `is_real_hardware()` guards. `is_real_hardware` is monkeypatched to
    `False` (wizard.py, via the same `no_install` fixture
    tests/unit/wizard/test_wizard_run_no_probes.py already uses) or simply
    never reached (updater.py's `-v`/`-l` flags do not call subprocess at
    all, confirmed by reading the source).
  - common/api_commands.py, blueprints/api/routes.py, common/app.py,
    notify/notifications.py, display/_base_flex.py: no os.system/subprocess
    on any write path exercised here (grepped; confirmed clean).
"""

import copy
import json
import os
import tempfile
import types
from unittest import mock

import pytest

from common.common import WriteKind
from common.datastore_accessors import read_settings, write_settings_store
from common.defaults import default_control, default_settings
from common.settings_schema import validate_settings_tree


def _assert_strict(settings=None):
    """validate_settings_tree() on the current store (or an explicit dict);
    raises SettingsValidationError on any handler bug."""
    validate_settings_tree(settings if settings is not None else read_settings())


@pytest.fixture
def client_and_store(ds):
    """Plain Flask test client + read_settings, for the two /api/settings*
    writers exercised below (blueprints/api/routes.py)."""
    from app import app as flask_app

    flask_app.config.update(TESTING=True)
    client = flask_app.test_client()
    return client, read_settings


# =====================================================================
# blueprints/api_admin/routes.py -- the kept JSON equivalents of
# blueprints/admin/routes.py's 6 write_settings sites (debugenabled x2
# branches, factorydefaults, restoresettings x2 branches, boot).
#
# blueprints/admin/routes.py itself is retired; the `admin_client` fixture
# below used to import it directly (blueprints.admin.routes), which no
# longer exists. Re-pointed at POST /api/admin/settings and
# POST /api/admin/backups/restore (blueprints/api_admin/routes.py).
#
# test_admin_factorydefaults_writes_strict is DELETED, not re-pointed:
# POST /api/admin/factory-reset has full strict-write + hazard coverage
# already in tests/web/test_api_admin_system.py
# (test_factory_reset_restores_defaults_and_restarts,
# test_factory_reset_clears_the_pellet_database), including the exact
# os.system/restart_scripts assertions this test made.
#
# test_admin_restoresettings_uploaded_file_upgrades_and_writes_strict is
# DELETED, not re-pointed: the kept surface splits "upload a file" and
# "restore by name" into two endpoints (no single call restores directly
# from uploaded bytes), and each half already has its own coverage --
# tests/web/test_api_admin_backups.py::test_upload_round_trips for the
# upload, and the re-pointed local-file test below for restore-by-name
# (which is the only path that still needs the migration proof). The
# migration-on-restore behavior this variant would otherwise duplicate is
# already exercised by test_admin_restoresettings_local_file_upgrades_and_writes_strict.
# =====================================================================


@pytest.fixture
def admin_client(ds, tmp_path):
    from app import app as flask_app
    import blueprints.api_admin.routes as admin_routes
    import common.backups as backups_module

    flask_app.config.update(TESTING=True)
    backup_dir = str(tmp_path / "backups") + os.sep
    os.makedirs(backup_dir, exist_ok=True)
    #  Both places BACKUP_PATH is read must be redirected: the blueprint
    #  reads current_app.config["BACKUP_PATH"], common/backups.py uses its
    #  own module-level constant. Patching one and not the other reaches
    #  into the real checkout (mirrors tests/web/test_api_admin_backups.py's
    #  `env` fixture).
    saved_backup_path = (flask_app.config.get("BACKUP_PATH"), backups_module.BACKUP_PATH)
    flask_app.config["BACKUP_PATH"] = backup_dir
    backups_module.BACKUP_PATH = backup_dir

    calls = []

    def _rec(name):
        def _inner(*a, **k):
            calls.append((name, a, k))

        return _inner

    def _rec_os(cmd):
        calls.append(("os.system", cmd))
        return 0

    with (
        mock.patch("os.system", side_effect=_rec_os),
        mock.patch.object(admin_routes, "restart_scripts", side_effect=_rec("restart_scripts")),
    ):
        yield types.SimpleNamespace(client=flask_app.test_client(), calls=calls, backup_dir=backup_dir)

    flask_app.config["BACKUP_PATH"], backups_module.BACKUP_PATH = saved_backup_path


def test_admin_debugenabled_disable_writes_strict(admin_client):
    resp = admin_client.client.post("/api/admin/settings", json={"debug_mode": False})
    assert resp.status_code == 200
    assert read_settings()["globals"]["debug_mode"] is False
    _assert_strict()


def test_admin_debugenabled_enable_writes_strict(admin_client):
    resp = admin_client.client.post("/api/admin/settings", json={"debug_mode": True})
    assert resp.status_code == 200
    assert read_settings()["globals"]["debug_mode"] is True
    _assert_strict()


def test_admin_boot_writes_strict(admin_client):
    resp = admin_client.client.post("/api/admin/settings", json={"boot_to_monitor": True})
    assert resp.status_code == 200
    assert read_settings()["globals"]["boot_to_monitor"] is True
    _assert_strict()


def test_admin_boot_false_writes_strict(admin_client):
    """The legacy form route treated an unchecked checkbox (the key entirely
    absent from the POST body) as `False`. The kept JSON endpoint has no
    such default -- an empty body is refused outright (`if not body: return
    error(...)`, blueprints/api_admin/routes.py) -- so the closest surviving
    equivalent is an explicit `False`, which exercises the same
    `settings["globals"].update(body)` write path with the opposite value."""
    resp = admin_client.client.post("/api/admin/settings", json={"boot_to_monitor": False})
    assert resp.status_code == 200
    assert read_settings()["globals"]["boot_to_monitor"] is False
    _assert_strict()


def test_admin_restoresettings_local_file_upgrades_and_writes_strict(admin_client):
    """FIXED (blueprints/admin/routes.py, ported forward to
    blueprints/api_admin/routes.py::admin_backup_restore): restoresettings
    previously called read_settings_file() WITHOUT init=True, so restoring
    an older-format backup (missing fields a later release added) wrote an
    incomplete tree straight to disk instead of migrating it forward. This
    backup deliberately omits versions.cookfile/versions.recipe (fields that
    did not exist in older PiFire releases) to prove the restore still
    migrates on the kept endpoint."""
    legacy_backup = default_settings()
    legacy_backup["globals"]["grill_name"] = "Restored Legacy"
    del legacy_backup["versions"]["cookfile"]
    del legacy_backup["versions"]["recipe"]
    backup_filename = "PiFire_legacy_test.json"
    with open(admin_client.backup_dir + backup_filename, "w") as f:
        json.dump(legacy_backup, f)

    resp = admin_client.client.post(
        "/api/admin/backups/restore",
        json={"kind": "settings", "file": backup_filename},
    )
    assert resp.status_code == 200
    assert read_settings()["globals"]["grill_name"] == "Restored Legacy"
    _assert_strict()


def test_admin_restoresettings_invalid_backup_rejected_no_crash(admin_client):
    """Boundary catch, ported to the kept endpoint. FIXED here too:
    blueprints/api_admin/routes.py::admin_backup_restore had no try/except
    around write_settings(), so a bad backup raised SettingsValidationError
    straight through the view function -- caught only by app.py's generic
    InternalServerError handler, which renders server_error.html (a 500,
    not a graceful rejection). Confirmed by driving this exact payload
    through the route before the fix landed. The route now catches
    SettingsValidationError the same way blueprints/api/routes.py's
    _api_post_settings_update already does, and the store is left untouched
    (write_settings() validates before persisting)."""
    before = read_settings()
    bad_backup = default_settings()
    bad_backup["safety"]["maxtemp"] = "nope"
    backup_filename = "bad_upload.json"
    with open(admin_client.backup_dir + backup_filename, "w") as f:
        json.dump(bad_backup, f)

    resp = admin_client.client.post(
        "/api/admin/backups/restore",
        json={"kind": "settings", "file": backup_filename},
    )
    assert resp.status_code == 400
    assert "safety.maxtemp" in resp.get_json()["data"]["detail"]
    assert read_settings() == before


# =====================================================================
# blueprints/mobile/socket_io.py -- 5 write sites reachable without real
# hardware: update_action/settings, admin_action/factory_defaults,
# units_action/f_units+c_units, probes_action/probe_update.
# =====================================================================


@pytest.fixture
def sio(ds):
    write_settings_store(default_settings())
    from common.datastore_accessors import write_control, write_pellet_db, init_status
    from common.defaults import default_pellets

    write_control(default_control(), WriteKind.OVERWRITE, origin="test-writer-matrix")
    write_pellet_db(default_pellets())
    init_status()

    import blueprints.mobile.socket_io as socket_io

    calls = []

    def _rec(name):
        def _inner(*a, **k):
            calls.append((name, a, k))

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


def test_socketio_update_settings_writes_strict(sio):
    payload = json.dumps({"globals": {"grill_name": "Strict Socket"}})
    resp = sio.mod._post_app_data("update_action", "settings", payload)
    assert resp["result"] == "OK"
    assert read_settings()["globals"]["grill_name"] == "Strict Socket"
    _assert_strict()


def test_socketio_admin_factory_defaults_writes_strict(sio):
    resp = sio.mod._post_app_data("admin_action", "factory_defaults")
    assert resp["result"] == "OK"
    # FIXED: the handler used to `rm settings.json` before reseeding -- a dead
    # call against a file that does not exist once SQLite is the store.
    assert not any(c[0] == "os.system" and "settings.json" in c[1] for c in sio.calls), sio.calls
    _assert_strict()


def test_socketio_units_f_writes_strict(sio):
    settings = read_settings()
    settings["globals"]["units"] = "C"
    write_settings_store(settings)
    resp = sio.mod._post_app_data("units_action", "f_units")
    assert resp["result"] == "OK"
    assert read_settings()["globals"]["units"] == "F"
    _assert_strict()


def test_socketio_units_c_writes_strict(sio):
    resp = sio.mod._post_app_data("units_action", "c_units")
    assert resp["result"] == "OK"
    assert read_settings()["globals"]["units"] == "C"
    _assert_strict()


def test_socketio_probe_update_writes_strict(sio):
    payload = json.dumps({"probes_action": {"label": "Grill", "name": "Strict Grill"}})
    resp = sio.mod._post_app_data("probes_action", "probe_update", payload)
    assert resp["result"] == "OK"
    assert read_settings()["probe_settings"]["probe_map"]["probe_info"][0]["name"] == "Strict Grill"
    _assert_strict()


# =====================================================================
# common/api_commands.py -- units F<->C conversion (both directions,
# round-trip sanity) and pmode.
# =====================================================================


def test_api_commands_set_units_round_trip_strict_and_sane(ds):
    from common.api_commands import process_command

    write_settings_store(default_settings())
    starting = read_settings()
    assert starting["globals"]["units"] == "F"
    starting_pwm_bands = list(starting["pwm"]["temp_range_list"])

    process_command(action="set", arglist=["units", "C"], origin="test")
    after_c = read_settings()
    assert after_c["globals"]["units"] == "C"
    _assert_strict(after_c)
    # pwm.temp_range_list is a DELTA ("degrees below setpoint"), not an
    # absolute reading -- FIXED (common/common.py convert_settings_units):
    # it was never converted at all before. Confirm it actually moved.
    assert after_c["pwm"]["temp_range_list"] != starting_pwm_bands
    # Absolute-reading fields round-trip via the existing convert_temp path.
    assert after_c["safety"]["maxtemp"] == 287  # 550F -> C truncated

    process_command(action="set", arglist=["units", "F"], origin="test")
    after_f = read_settings()
    assert after_f["globals"]["units"] == "F"
    _assert_strict(after_f)
    # Round-trip sanity: int-truncation drift is expected (same as every
    # other pre-existing convert_temp field), but must stay small.
    for before, after in zip(starting_pwm_bands, after_f["pwm"]["temp_range_list"]):
        assert abs(before - after) <= 2
    assert abs(starting["safety"]["maxtemp"] - after_f["safety"]["maxtemp"]) <= 2


def test_api_commands_set_units_noop_when_same_units_stays_strict(ds):
    from common.api_commands import process_command

    write_settings_store(default_settings())
    process_command(action="set", arglist=["units", "F"], origin="test")  # already F: no-op branch
    _assert_strict()


def test_api_commands_set_pmode_writes_strict(ds):
    from common.api_commands import process_command

    write_settings_store(default_settings())
    process_command(action="set", arglist=["pmode", "7"], origin="test")
    assert read_settings()["cycle_data"]["PMode"] == 7
    _assert_strict()


# =====================================================================
# blueprints/history/routes.py and blueprints/dash/routes.py are both
# retired with NO write-capable equivalent -- and deliberately so, not by
# oversight:
#
#   * GET /api/history/chart (blueprints/api_history/routes.py) is the kept
#     replacement for /history/refresh, and its docstring explains the
#     write this test pinned was intentionally dropped: "Deliberately NOT
#     the legacy POST /history/refresh: that route persists
#     settings['history_page']['minutes'] as a side effect of being asked
#     for a window, which would let a client's transient zoom overwrite the
#     user's saved preference." /history/setmins wrote the same field via a
#     second route with no kept counterpart at all.
#   * Dashboard widget config (settings.dashboard.dashboards[*].config) has
#     no dedicated write route on the kept surface; it is a loose
#     `dict[str, dict]` schema field (common/settings_schema.py's
#     `Dashboard.dashboards`), so the only way left to write it is the
#     generic POST /api/settings_update delta merge -- the exact same
#     generic mechanism as every other settings write.
#
# In both cases the only kept write path is the generic delta-merge
# mechanism, which is exactly what
# test_api_post_settings_update_valid_delta_writes_strict below already
# pins (plus tests/web/test_api_settings_update.py and
# test_api_settings_controller_gate.py for the two-layer
# validate/reject-and-leave-store-untouched behavior). There is no
# route-specific handler code left for either history or dash to catch a
# bug in, so these three tests (test_history_refresh_num_mins_writes_strict,
# test_history_setmins_writes_strict, test_dash_config_post_writes_strict)
# are deleted rather than re-pointed: re-pointing them would only exercise
# the already-covered generic mechanism a second time under a different
# name.
# =====================================================================
# blueprints/wizard/routes.py -- 1 reachable write site (cancel), ported to
# POST /api/wizard/cancel (blueprints/api_wizard/routes.py::wizard_cancel,
# a verbatim port of the same two statements: clear
# settings["globals"]["first_time_setup"] and write_settings()). `finish`
# is the only handler with an os.system() call and is deliberately not
# exercised here.
# =====================================================================


def test_wizard_cancel_writes_strict(client_and_store):
    client, read_settings_fn = client_and_store
    resp = client.post("/api/wizard/cancel")
    assert resp.status_code == 200
    assert read_settings_fn()["globals"]["first_time_setup"] is False
    _assert_strict()


# =====================================================================
# blueprints/api/routes.py -- settings + settings_update actions (the
# latter also exercises common/app.py's save_settings_and_flag_update).
# =====================================================================


def test_api_post_settings_valid_delta_writes_strict(client_and_store):
    client, read_settings_fn = client_and_store
    resp = client.post("/api/settings", json={"globals": {"grill_name": "API Delta"}})
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["result"] == "success"
    assert read_settings_fn()["globals"]["grill_name"] == "API Delta"
    _assert_strict()


def test_api_post_settings_update_valid_delta_writes_strict(client_and_store):
    client, read_settings_fn = client_and_store
    resp = client.post(
        "/api/settings_update",
        json={"settings": {"globals": {"grill_name": "API Update Delta"}}, "flags": ["settings_update"]},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["result"] == "success"
    assert read_settings_fn()["globals"]["grill_name"] == "API Update Delta"
    _assert_strict()


# =====================================================================
# common/app.py -- save_settings_and_flag_update, direct unit test (in
# addition to the api/routes.py + socket_io coverage above, both of which
# call through it).
# =====================================================================


def test_save_settings_and_flag_update_writes_strict(ds):
    from common.app import save_settings_and_flag_update
    from common.datastore_accessors import execute_control_writes, read_control

    write_settings_store(default_settings())
    settings = read_settings()
    settings["globals"]["grill_name"] = "Direct App Helper"
    control = default_control()

    save_settings_and_flag_update(settings, control, "settings_update", origin="test")
    execute_control_writes()  # write_control queues a MERGE partial; drain it to read back.

    assert read_settings()["globals"]["grill_name"] == "Direct App Helper"
    assert read_control()["settings_update"] is True
    _assert_strict()


# =====================================================================
# notify/notifications.py -- _send_onesignal_notification's invalid-player-
# id device cleanup (the only write_settings site in this module).
# =====================================================================


def test_onesignal_invalid_player_id_cleanup_writes_strict(ds):
    import notify.notifications as N

    write_settings_store(default_settings())
    settings = read_settings()
    settings["notify_services"]["onesignal"]["devices"] = {"bad-device": {"device_name": "Old Phone"}}
    write_settings_store(settings)

    fake_response = mock.Mock()
    fake_response.status_code = 200
    fake_response.text = "ok"
    fake_response.json.return_value = {"errors": {"invalid_player_ids": ["bad-device"]}}

    with mock.patch.object(N.requests, "post", return_value=fake_response):
        N._send_onesignal_notification(read_settings(), "Title", "Body", "chan")

    assert "bad-device" not in read_settings()["notify_services"]["onesignal"]["devices"]
    _assert_strict()


# =====================================================================
# display/_base_flex.py -- the pmode command write (the only write_settings
# site in this module). Hardware neutralized via a minimal in-memory
# layout, mirroring tests/ui/test_base_flex_dash_update.py's harness.
# =====================================================================


@pytest.fixture
def flex_display(ds, tmp_path):
    write_settings_store(default_settings())
    from common.datastore_accessors import write_control
    from common.defaults import default_control as _default_control

    write_control(_default_control(), WriteKind.OVERWRITE, origin="test-writer-matrix")

    from display._base_flex import DisplayBase

    class _DummyDisplay(DisplayBase):
        def __init__(self, config):
            self.display_profile = "profile_1"
            super().__init__(dev_pins={}, config=config)

    layout = {
        "metadata": {
            "name": "writer_matrix_test",
            "screen_width": 800,
            "screen_height": 480,
            "splash_delay": 10,
            "framerate": 30,
            "max_food_probes": 5,
            "dash_background": "./static/img/display/background.png",
            "splash_image": "./static/img/display/splash_800x480.png",
        },
        "profile_1": {"home": [], "dash": [], "menus": {"qrcode": {}}, "input": {}},
    }
    layout_path = tmp_path / "layout.json"
    layout_path.write_text(json.dumps(layout))

    config = {
        "display_data_filename": str(layout_path),
        "default_profile": "profile_1",
        "input_types_supported": [],
        "buttonslevel": "HIGH",
    }
    return _DummyDisplay(config)


def test_display_pmode_command_writes_strict(flex_display):
    flex_display.command = "pmode"
    flex_display.command_data = 6
    flex_display._command_handler()
    assert read_settings()["cycle_data"]["PMode"] == 6
    _assert_strict()


# =====================================================================
# updater.py -- the two write_settings sites live in the `if __name__ ==
# "__main__":` script body (module scope, not a function), reached via
# `-v`/`-l`. Neither flag's branch calls subprocess/os.system (confirmed by
# reading the source: only -u/-b/-i trigger install_dependencies(), which
# this test never selects), so this is safe to drive with runpy + a
# monkeypatched sys.argv, no mocking required.
# =====================================================================


def test_updater_main_uv_flag_writes_strict(ds):
    import runpy
    import sys

    write_settings_store(default_settings())
    old_argv = sys.argv
    sys.argv = ["updater.py", "-v"]
    try:
        runpy.run_path(os.path.join(os.path.dirname(__file__), "..", "..", "updater.py"), run_name="__main__")
    finally:
        sys.argv = old_argv

    written = read_settings()
    assert written["globals"]["uv"] is True
    assert written["globals"]["venv"] is True
    assert written["globals"]["python_exec"] == ".venv/bin/python"
    _assert_strict()


def test_updater_main_legacyvenv_flag_writes_strict(ds):
    import runpy
    import sys

    write_settings_store(default_settings())
    old_argv = sys.argv
    sys.argv = ["updater.py", "-l"]
    try:
        runpy.run_path(os.path.join(os.path.dirname(__file__), "..", "..", "updater.py"), run_name="__main__")
    finally:
        sys.argv = old_argv

    written = read_settings()
    assert written["globals"]["uv"] is False
    assert written["globals"]["venv"] is True
    assert written["globals"]["python_exec"] == "bin/python"
    _assert_strict()


# =====================================================================
# wizard.py -- run_wizard()'s two write_settings sites (module-level
# function, unlike updater.py). Reuses the exact `no_install` neutralization
# pattern from tests/unit/wizard/test_wizard_run_no_probes.py.
# =====================================================================


@pytest.fixture
def no_install(monkeypatch):
    import logging

    import wizard

    monkeypatch.setattr(wizard, "logger", logging.getLogger("wizard_writer_matrix_test"), raising=False)
    monkeypatch.setattr(wizard, "is_real_hardware", lambda *a, **k: False)
    monkeypatch.setattr(wizard.time, "sleep", lambda *a, **k: None)

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(wizard.subprocess, "run", lambda *a, **k: _Result())


def test_run_wizard_writes_strict(ds, no_install):
    import wizard
    from common import datastore_accessors
    from common.common import read_wizard

    settings = default_settings()
    settings["probe_settings"]["probe_map"]["probe_devices"] = []
    datastore_accessors.write_settings_store(settings)

    wizard_data = read_wizard()
    install_info = wizard.wizardInstallInfoExisting(settings, wizard_data)

    wizard.run_wizard(settings, wizard_data, install_info)

    _assert_strict()


# =====================================================================
# Migration matrix -- for every starting-version fixture the settings-
# migration tests already use (tests/unit/common/test_settings_migration.py),
# run the REAL production migration entry point and assert the result is
# strictly valid.
#
# NOTE on why this drives read_settings_file(init=True) rather than a bare
# upgrade_settings() call: upgrade_settings() is one stage of a 3-stage
# pipeline (see common/settings_migration.py:read_settings_file) --
# (1) upgrade_settings() runs the versioned transform blocks, (2)
# settings["versions"] is unconditionally replaced with the current
# defaults, (3) `deep_update(settings_default, settings)` overlays the
# result on top of a FULL default tree, backfilling any field a legacy
# fixture's block(s) didn't touch (e.g. notify_services.onesignal.uuid,
# versions.cookfile/recipe on a fixture built from an old `versions` shape).
# common/datastore.py's real startup path (_first_boot_import) calls
# read_settings_file(init=True) directly and persists ITS result -- so
# read_settings_file(init=True) is the actual reachable "writer" the
# strict-validation gate cares about, not the bare intermediate helper.
# (Calling upgrade_settings() alone on these fixtures does NOT pass
# validate_settings_tree for several of them -- by design, not a bug.)
# =====================================================================


@pytest.fixture
def migration_env(tmp_path, monkeypatch):
    from common import datastore

    monkeypatch.setenv("PIFIRE_DB_PATH", str(tmp_path / "t.db"))
    datastore._reset_for_tests(str(tmp_path / "t.db"))
    datastore.init()

    backups_path = str(tmp_path / "backups") + os.sep
    os.makedirs(backups_path, exist_ok=True)
    monkeypatch.setattr("common.settings_migration.BACKUP_PATH", backups_path)
    monkeypatch.setattr("common.backups.BACKUP_PATH", backups_path)

    yield tmp_path
    datastore._reset_for_tests(None)


def _migrate_and_check(tmp_path, name, old_settings):
    from common.settings_migration import read_settings_file

    p = tmp_path / f"{name}.json"
    p.write_text(json.dumps(old_settings))
    result = read_settings_file(filename=str(p), init=True)
    _assert_strict(result)
    return result


def test_migration_v1_4_cascade_full_migrates_strict(migration_env):
    d = default_settings()
    old = copy.deepcopy(d)
    old["versions"] = {"server": "1.4.0", "build": 0}
    old["startup"] = {"start_to_mode": {}}
    old["start_to_mode"] = {"grill1_setpoint": 225}
    old["dashboard"] = {"sentinel_old_dash": True}
    for key in d["notify_services"].keys():
        old[key] = {"legacy_marker": key}
    del old["notify_services"]
    old["probe_settings"]["probe_options"] = {"x": 1}
    old["probe_settings"]["probe_sources"] = {"x": 1}
    old["probe_settings"]["probes_enabled"] = {"x": 1}
    old["modules"]["adc"] = "mcp3008"
    old["modules"]["grillplat"] = "some_real_platform"
    prof_key = next(iter(old["probe_settings"]["probe_profiles"].keys()))
    old["probe_settings"]["probe_profiles"][prof_key].pop("id", None)
    old["cycle_data"] = {"SmokeCycleTime": 30, "HoldCycleTime": 25}
    old["globals"]["startup_timer"] = 999
    old["globals"]["startup_exit_temp"] = 111
    old["globals"]["shutdown_timer"] = 222
    old["globals"]["auto_power_off"] = True
    old["globals"]["buttonslevel"] = "LOW"
    old["dev_pins"] = {"display": {"dc": 42}}

    _migrate_and_check(migration_env, "v1_4_full", old)


def test_migration_v1_4_cascade_preserves_start_to_mode_strict(migration_env):
    d = default_settings()
    old = copy.deepcopy(d)
    old["versions"] = {"server": "1.4.0", "build": 0}
    old["startup"] = {"start_to_mode": {}}
    old["start_to_mode"] = {"grill1_setpoint": 225}
    for key in d["notify_services"].keys():
        old[key] = {}
    del old["notify_services"]
    old["probe_settings"]["probe_options"] = {}
    old["probe_settings"]["probe_sources"] = {}
    old["probe_settings"]["probes_enabled"] = {}
    old["modules"]["adc"] = "mcp3008"
    old["cycle_data"] = {"SmokeCycleTime": 30, "HoldCycleTime": 25}

    result = _migrate_and_check(migration_env, "v1_4_preserve", old)
    assert result["startup"]["start_to_mode"]["primary_setpoint"] == 225


def test_migration_block4_platform_prototype_strict(migration_env):
    d = default_settings()
    old = copy.deepcopy(d)
    old["versions"] = {"server": "1.7.0", "build": 100}
    old["modules"]["grillplat"] = "prototype"
    old["globals"]["dc_fan"] = True
    old["globals"]["real_hw"] = False
    old["globals"]["standalone"] = False
    old["globals"]["triggerlevel"] = "LOW"
    old["inpins"] = {"selector": 55}
    old["outpins"] = {"auger": 77}

    _migrate_and_check(migration_env, "block4", old)


@pytest.mark.parametrize("build", [7, 8])
def test_migration_dashboard_build7_boundary_strict(migration_env, build):
    d = default_settings()
    old = copy.deepcopy(d)
    old["versions"] = {"server": "1.7.0", "build": build}
    old["dashboard"] = {"sentinel": True}

    _migrate_and_check(migration_env, f"dash_build{build}", old)


@pytest.mark.parametrize("build", [32, 33])
def test_migration_bt_meater_rename_boundary_strict(migration_env, build):
    d = default_settings()
    old = copy.deepcopy(d)
    old["versions"] = {"server": "1.9.0", "build": build}
    old["probe_settings"]["probe_map"]["probe_devices"] = [
        {"device": "d1", "module": "bt_meater_alt", "module_filename": "x"},
        {"device": "d2", "module": "bt_meater", "module_filename": "x"},
        {"device": "d3", "module": "prototype", "module_filename": "x"},
    ]

    _migrate_and_check(migration_env, f"bt_meater{build}", old)


@pytest.mark.parametrize("venv", [True, False])
def test_migration_python_exec_venv_strict(migration_env, venv):
    d = default_settings()
    old = copy.deepcopy(d)
    old["versions"] = {"server": "1.10.0", "build": 0}
    old["globals"]["venv"] = venv

    _migrate_and_check(migration_env, f"pyexec_{venv}", old)


@pytest.mark.parametrize("build", [51, 52])
def test_migration_module_filename_boundary_strict(migration_env, build):
    d = default_settings()
    old = copy.deepcopy(d)
    old["versions"] = {"server": "1.10.0", "build": build}
    old["probe_settings"]["probe_map"]["probe_devices"] = [{"device": "d1", "module": "prototype"}]

    _migrate_and_check(migration_env, f"modfile{build}", old)


def test_migration_current_version_noop_strict(migration_env):
    d = default_settings()
    old = copy.deepcopy(d)
    old["versions"] = dict(d["versions"])
    old["globals"]["updated_message"] = False
    removed_key = next(iter(old["probe_settings"]["probe_profiles"].keys()))
    old["probe_settings"]["probe_profiles"].pop(removed_key)

    _migrate_and_check(migration_env, "current_noop", old)


def test_migration_downgrade_no_backup_resets_to_defaults_strict(migration_env):
    d = default_settings()
    old = copy.deepcopy(d)
    old["versions"]["server"] = "99.99.99"  # newer than current code -> downgrade path

    _migrate_and_check(migration_env, "downgrade", old)
