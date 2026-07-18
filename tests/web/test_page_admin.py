"""Playwright coverage for the admin page (blueprints/admin/routes.py's
`admin_page`, a single route handling top-level `reboot`/`shutdown`/
`restart` actions, a `boot` POST action, and a `setting` POST action with
~14 response-key-gated sub-actions), following the pattern established in
tests/web/test_page_settings.py -- see tests/web/conftest.py for the shared
harness (live_server, read-back helpers, precondition seeding).

REBOOT / SHUTDOWN / RESTART HAZARD -- read this before touching this file
==========================================================================
This repo has had THREE real, unintended `sudo reboot`/`sudo shutdown`
dispatches fired by test/verification code in past sessions (see the
project memory note `feedback_neutralize_os_system_before_verification.md`).
`admin_page`'s `reboot`/`shutdown`/`restart` actions, and the `setting`
action's `factorydefaults` and (successful) `restoresettings` sub-actions,
all eventually call one of `common.system.reboot_system()` /
`shutdown_system()` / `restart_scripts()`.

Two things make a naive `os.system` patch INSUFFICIENT here (both are
exactly the failure modes the memory note documents):

1. `common/system.py`'s `reboot_system()`/`shutdown_system()`/
   `restart_scripts()` dispatch PRIMARILY via `subprocess.run(["sudo",
   "systemctl", "reboot"/"poweroff"/"restart", ...])`, falling back to
   `os.system("sudo reboot")` only in an exception path. Patching only
   `os.system` would leave the `subprocess.run` primary path live.
2. `blueprints/admin/routes.py` imports these by NAME at module load time
   (`from common.system import reboot_system, shutdown_system,
   restart_scripts`), so `admin_page()` looks up the name in
   `blueprints.admin.routes`'s own module globals when it calls
   `reboot_system()` -- NOT in `common.system`. A
   `mock.patch("common.system.reboot_system", ...)` would silently fail to
   intercept the call admin_page() actually makes (this is precisely the
   "moving code out from under a mock" failure mode from the memory note,
   here caused by a `from X import Y` rather than a code move, but with the
   identical silent-disarm effect).
3. Belt-and-suspenders is also NOT enough on its own: `default_settings()`
   ships `platform.real_hw = True` (correct for production, meaning
   `is_real_hardware()` -- which these three functions gate their real
   dispatch on -- returns True in a naively-seeded test DB too). Relying on
   `real_hw=False` alone was exactly the mistake that caused two of the
   three prior real reboots.

What this module does about it (the `hazard_guard` fixture below, module
scope + autouse, active for the ENTIRE module):

- Replaces `blueprints.admin.routes.reboot_system` /
  `.shutdown_system` / `.restart_scripts` -- the actual names admin_page()
  resolves at call time -- with recording no-op stubs. This is a complete
  function replacement: the real body (and its subprocess.run/os.system
  calls) never executes at all, regardless of `real_hw`.
- Also patches the shared `os.system` (used directly, unconditionally, by
  the `clearevents`/`clearpelletdb`/`delete_logs`/`factorydefaults`
  sub-actions for `rm ...` calls) to a recording no-op. Since `app.py` runs
  on a background THREAD in this SAME process (see conftest.py's
  "Thread-shared datastore" docs), patching the process-wide `os` module
  object intercepts the server thread's calls too -- there is only one
  `os` module in the process either way.
- PROVEN empirically, not just asserted by construction: the first
  destructive-capable test in this module (`test_reboot_action_is_...`)
  additionally patches `subprocess.run` for the duration of just that one
  request and asserts it was NEVER called -- i.e. proof that the real
  dispatch body genuinely never ran, not just that our stub happened to run
  first. Every hazard test besides also asserts on the recorder's captured
  call list.
- Also redirects `BACKUP_PATH`/`UPLOAD_FOLDER` (both the Flask app config
  admin_page reads, and the separate `common.backups.BACKUP_PATH` module
  constant `backup_settings()`/`backup_pellet_db()` use directly) to an
  isolated tmp directory for the module's lifetime, so `backupsettings`/
  `backuppelletdb`/`restoresettings`/`restorepelletdb` don't write real
  files into this repo's working tree (`admin_page` unconditionally
  `os.mkdir`s and `os.listdir`s `BACKUP_PATH` on EVERY request, so even the
  base GET-render test would otherwise create `./backups/` in the repo).

Given the above is proven (not just "should work"), this module exercises
`reboot`/`shutdown`/`restart`/`factorydefaults`/`restoresettings` LIVE
against the real server thread rather than characterizing them by code
reading alone -- the guarantee is that the exact call the route makes is
intercepted, which is strictly stronger than an `os.system`-only patch.
See `.superpowers/sdd/playwright-admin-report.md` for the full writeup
including the empirical proof output.

Actions covered: base GET render (system-info sections), `reboot`,
`shutdown`, `restart` (top-level, neutralized), `boot`, and the `setting`
sub-actions `debugenabled`, `clearhistory`, `clearevents`, `clearpelletdb`,
`clearpelletdblog`, `download_logs`, `delete_logs`, `download_settings`,
`download_control`, `download_pip_list`, `backupsettings`,
`restoresettings` (neutralized), `backuppelletdb`, `restorepelletdb`, and
`factorydefaults` (neutralized).
"""

import json
import os
import shutil
import tempfile
from unittest import mock

import pytest

import blueprints.admin.routes as admin_routes
import common.backups as backups_module
from common.common import write_generic_json
from common.datastore_accessors import read_history, read_pellet_db, write_history, write_pellet_db
from common.defaults import default_pellets, default_settings
from tests.web.conftest import (
    apply_control,
    apply_settings,
    read_settings_from_server,
    requires_chromium,
)

pytestmark = requires_chromium


@pytest.fixture(scope="module", autouse=True)
def hazard_guard(live_server):
    """Neutralizes every reboot/shutdown/restart/rm dispatch reachable from
    admin_page() for this module's entire lifetime, and redirects backup I/O
    to a tmp dir. See module docstring for the full rationale. Returns a
    dict of the mock objects, a `calls` list every stub appends to (in
    order, across ALL stubs), the tmp backup dir, and the live flask app
    object (for server_status resets between hazard tests).
    """
    tmp_backup_dir = tempfile.mkdtemp(prefix="pifire_test_admin_backups_")
    backup_path = tmp_backup_dir + os.sep

    from app import app as flask_app

    flask_app.config["BACKUP_PATH"] = backup_path
    flask_app.config["UPLOAD_FOLDER"] = backup_path

    calls = []

    def _record(name):
        def _inner(*args, **kwargs):
            calls.append((name, args, kwargs))

        return _inner

    def _record_os_system(cmd):
        calls.append(("os.system", cmd))
        return 0

    with (
        mock.patch("os.system", side_effect=_record_os_system) as m_os_system,
        mock.patch.object(admin_routes, "reboot_system", side_effect=_record("reboot_system")) as m_reboot,
        mock.patch.object(admin_routes, "shutdown_system", side_effect=_record("shutdown_system")) as m_shutdown,
        mock.patch.object(admin_routes, "restart_scripts", side_effect=_record("restart_scripts")) as m_restart,
        mock.patch.object(backups_module, "BACKUP_PATH", backup_path),
    ):
        yield {
            "calls": calls,
            "os_system": m_os_system,
            "reboot_system": m_reboot,
            "shutdown_system": m_shutdown,
            "restart_scripts": m_restart,
            "backup_dir": tmp_backup_dir,
            "flask_app": flask_app,
        }

    shutil.rmtree(tmp_backup_dir, ignore_errors=True)


def _assert_no_hazardous_subprocess_calls(mock_run, action_label):
    """The admin page's own on-load JS (`/update/check`) independently calls
    `subprocess.run(['git', 'rev-list', ...])` for the update checker, and a
    leftover page from an earlier test in this module can still be polling
    it in the background -- so a blanket `assert_not_called()` on a
    globally-patched `subprocess.run` is flaky (observed empirically: it
    fired here in practice). Instead, assert specifically that nothing
    reboot/poweroff/shutdown/supervisor-restart shaped was ever dispatched,
    which is the actual safety property this test proves and tolerates
    unrelated app subsystems sharing the same patched name.
    """
    hazardous_tokens = ("reboot", "poweroff", "shutdown", "supervisor")
    for call in mock_run.call_args_list:
        argv = call.args[0] if call.args else call.kwargs.get("args", [])
        argv_str = " ".join(str(a) for a in argv).lower()
        assert not any(tok in argv_str for tok in hazardous_tokens), (
            f"{action_label}: hazardous subprocess.run call leaked through: {argv}"
        )


def _reset_server_status(flask_app):
    """The `reboot`/`shutdown`/`restart` actions set `current_app.server_status`
    to a terminal value ('rebooting'/'shutdown'/'restarting') that, on real
    hardware, only ever clears because the process actually restarts. Since
    our hazard_guard prevents that restart, reset it by hand after each
    hazard test so later tests in this module see a clean 'available'
    state (server_status doesn't gate admin_page's own render, but it's
    good hygiene for whatever else shares this module-scoped live_server).
    """
    from common.server_status import set_server_status

    with flask_app.app_context():
        set_server_status("available")


# --- Base GET render -----------------------------------------------------


def test_admin_page_renders_key_sections(live_server, page, hazard_guard):
    apply_settings(lambda s: s["globals"].__setitem__("grill_name", "E2E Admin Grill"))

    resp = page.goto(f"{live_server}/admin/")

    assert resp.status == 200
    assert page.title().startswith("Admin")
    assert page.locator("#v-pills-tab").count() == 1
    for tab_id in (
        "v-pills-debug-tab",
        "v-pills-data-tab",
        "v-pills-power-tab",
        "v-pills-boot-tab",
        "v-pills-system-tab",
    ):
        assert page.locator(f"#{tab_id}").count() == 1, f"missing nav tab {tab_id!r}"

    # Unconditional system-info gathering (uptime/os_info/network/hardware)
    # runs on every GET regardless of action -- assert its sections render.
    page.click("#v-pills-system-tab")
    page.wait_for_selector("#v-pills-system.active")
    system_tab_text = page.locator("#v-pills-system").inner_text()
    assert "Uptime" in system_tab_text
    assert "Platform Info" in system_tab_text
    assert "OS Info" in system_tab_text
    assert "Network Info" in system_tab_text


# --- Destructive top-level actions (neutralized) -------------------------


def test_reboot_action_is_neutralized_and_proven_intercepted(live_server, page, hazard_guard):
    """PROOF the hazard_guard patch intercepts the call admin_page() actually
    makes, run as the FIRST destructive-capable test in this module (see
    module docstring). Additionally patches `subprocess.run` narrowly for
    just this one request as empirical confirmation that the real dispatch
    body -- which would otherwise call subprocess.run(['sudo', 'systemctl',
    'reboot']) -- never executed at all.
    """
    calls_before = len(hazard_guard["calls"])
    with mock.patch("subprocess.run") as m_subprocess_run:
        resp = page.request.get(f"{live_server}/admin/reboot")

    assert resp.status == 200
    assert "Rebooting" in resp.text()
    assert hazard_guard["reboot_system"].call_count == 1
    new_calls = hazard_guard["calls"][calls_before:]
    assert new_calls == [("reboot_system", (), {})]
    # The real dispatch body never ran -- if it had, this would have fired
    # a hazardous call; see _assert_no_hazardous_subprocess_calls docstring
    # for why this isn't a blanket assert_not_called().
    _assert_no_hazardous_subprocess_calls(m_subprocess_run, "reboot")

    _reset_server_status(hazard_guard["flask_app"])


def test_shutdown_action_is_neutralized(live_server, page, hazard_guard):
    calls_before = len(hazard_guard["calls"])
    with mock.patch("subprocess.run") as m_subprocess_run:
        resp = page.request.get(f"{live_server}/admin/shutdown")

    assert resp.status == 200
    assert "Shutting Down" in resp.text()
    assert hazard_guard["shutdown_system"].call_count == 1
    assert hazard_guard["calls"][calls_before:] == [("shutdown_system", (), {})]
    _assert_no_hazardous_subprocess_calls(m_subprocess_run, "shutdown")

    _reset_server_status(hazard_guard["flask_app"])


def test_restart_action_is_neutralized(live_server, page, hazard_guard):
    calls_before = len(hazard_guard["calls"])
    with mock.patch("subprocess.run") as m_subprocess_run:
        resp = page.request.get(f"{live_server}/admin/restart")

    assert resp.status == 200
    assert "Restarting Server" in resp.text()
    assert hazard_guard["restart_scripts"].call_count == 1
    assert hazard_guard["calls"][calls_before:] == [("restart_scripts", (), {})]
    _assert_no_hazardous_subprocess_calls(m_subprocess_run, "restart")

    _reset_server_status(hazard_guard["flask_app"])


# --- Safe `setting` sub-actions ------------------------------------------


def test_debugenabled_toggle_via_real_ui(live_server, page, hazard_guard):
    apply_settings(lambda s: s["globals"].__setitem__("debug_mode", False))
    page.goto(f"{live_server}/admin/")
    page.click("#v-pills-debug-tab")
    page.wait_for_selector("#v-pills-debug.active")

    with page.expect_navigation():
        page.locator('button[name="debugenabled"]').click()
    assert read_settings_from_server()["globals"]["debug_mode"] is True

    page.click("#v-pills-debug-tab")
    page.wait_for_selector("#v-pills-debug.active")
    with page.expect_navigation():
        page.locator('button[name="debugenabled"]').click()
    assert read_settings_from_server()["globals"]["debug_mode"] is False


def test_clearhistory_via_direct_post(live_server, page, hazard_guard):
    write_history(
        {
            "probe_history": {"primary": {"Grill": 200}, "food": {}, "aux": {}},
            "primary_setpoint": 200,
            "notify_targets": {},
        }
    )
    assert len(read_history()) > 0

    resp = page.request.post(f"{live_server}/admin/setting", form={"clearhistory": "true"})

    assert resp.status == 200
    assert read_history() == []


def test_clearpelletdblog_via_direct_post(live_server, page, hazard_guard):
    pelletdb = read_pellet_db()
    pelletdb["log"]["2024-01-01_000000"] = "seed-log-id"
    write_pellet_db(pelletdb)
    assert read_pellet_db()["log"] != {}

    resp = page.request.post(f"{live_server}/admin/setting", form={"clearpelletdblog": "true"})

    assert resp.status == 200
    assert read_pellet_db()["log"] == {}


def test_clearevents_neutralized_via_direct_post(live_server, page, hazard_guard):
    calls_before = len(hazard_guard["calls"])

    resp = page.request.post(f"{live_server}/admin/setting", form={"clearevents": "true"})

    assert resp.status == 200
    new_calls = hazard_guard["calls"][calls_before:]
    assert any(c[0] == "os.system" and "events.log" in c[1] for c in new_calls), new_calls


def test_clearpelletdb_neutralized_via_direct_post(live_server, page, hazard_guard):
    calls_before = len(hazard_guard["calls"])

    resp = page.request.post(f"{live_server}/admin/setting", form={"clearpelletdb": "true"})

    assert resp.status == 200
    new_calls = hazard_guard["calls"][calls_before:]
    assert any(c[0] == "os.system" and "pelletdb.json" in c[1] for c in new_calls), new_calls


def test_download_logs_via_direct_post(live_server, page, hazard_guard):
    resp = page.request.post(f"{live_server}/admin/setting", form={"download_logs": "true"})

    assert resp.status == 200
    assert "attachment" in resp.headers.get("content-disposition", "")
    assert len(resp.body()) > 0


def test_delete_logs_neutralized_via_direct_post(live_server, page, hazard_guard):
    calls_before = len(hazard_guard["calls"])

    resp = page.request.post(f"{live_server}/admin/setting", form={"delete_logs": "true"})

    assert resp.status == 200
    assert "Log files deleted." in resp.text()
    new_calls = hazard_guard["calls"][calls_before:]
    assert any(c[0] == "os.system" and "logs/*.log" in c[1] for c in new_calls), new_calls


def test_download_settings_via_direct_post(live_server, page, hazard_guard):
    apply_settings(lambda s: s["globals"].__setitem__("grill_name", "Download Settings Grill"))

    resp = page.request.post(f"{live_server}/admin/setting", form={"download_settings": "true"})

    assert resp.status == 200
    body = resp.json()
    assert body["globals"]["grill_name"] == "Download Settings Grill"


def test_download_control_via_direct_post(live_server, page, hazard_guard):
    apply_control(lambda c: c.__setitem__("mode", "Startup"))

    resp = page.request.post(f"{live_server}/admin/setting", form={"download_control": "true"})

    assert resp.status == 200
    body = resp.json()
    assert body["mode"] == "Startup"


def test_download_pip_list_via_direct_post(live_server, page, hazard_guard, tmp_path):
    # download_pip_list send_file()s the repo-root-relative 'pip_list.json'
    # unconditionally; create it if absent so the route has something real
    # to serve, and clean it up afterwards regardless of outcome.
    pip_list_path = os.path.join(os.getcwd(), "pip_list.json")
    created = not os.path.exists(pip_list_path)
    if created:
        with open(pip_list_path, "w") as f:
            json.dump([{"name": "flask", "version": "3.0.0"}], f)

    try:
        resp = page.request.post(f"{live_server}/admin/setting", form={"download_pip_list": "true"})
        assert resp.status == 200
        body = resp.json()
        assert isinstance(body, list)
        if created:
            assert body[0]["name"] == "flask"
    finally:
        if created:
            os.remove(pip_list_path)


def test_backupsettings_via_direct_post(live_server, page, hazard_guard):
    apply_settings(lambda s: s["globals"].__setitem__("grill_name", "Backup Me"))

    resp = page.request.post(f"{live_server}/admin/setting", form={"backupsettings": "true"})

    assert resp.status == 200
    body = resp.json()
    assert body["globals"]["grill_name"] == "Backup Me"
    # Landed in the neutralized tmp BACKUP_PATH, not the real repo tree.
    backup_files = os.listdir(hazard_guard["backup_dir"])
    assert any(f.startswith("PiFire_") and f.endswith(".json") for f in backup_files), backup_files


def test_backuppelletdb_via_direct_post(live_server, page, hazard_guard):
    pelletdb = read_pellet_db()
    pelletdb["log"]["backup-marker"] = "marker-id"
    write_pellet_db(pelletdb)

    resp = page.request.post(f"{live_server}/admin/setting", form={"backuppelletdb": "true"})

    assert resp.status == 200
    body = resp.json()
    assert body["log"]["backup-marker"] == "marker-id"
    backup_files = os.listdir(hazard_guard["backup_dir"])
    assert any(f.startswith("PelletDB_") and f.endswith(".json") for f in backup_files), backup_files


def test_restorepelletdb_via_direct_post(live_server, page, hazard_guard):
    backup_pelletdb = default_pellets()
    backup_pelletdb["log"] = {"seed-restore": "id-999"}
    backup_filename = "PelletDB_test_restore.json"
    write_generic_json(backup_pelletdb, os.path.join(hazard_guard["backup_dir"], backup_filename))

    # An empty 'uploadfile' part mirrors what a browser sends when the file
    # input is left empty in a real multipart submit -- request.files
    # requires the key to be present at all, and the route's local_file
    # branch (checked first) is what actually drives this restore.
    resp = page.request.post(
        f"{live_server}/admin/setting",
        multipart={
            "restorepelletdb": "true",
            "localfile": backup_filename,
            "uploadfile": {"name": "", "mimeType": "application/octet-stream", "buffer": b""},
        },
    )

    assert resp.status == 200
    assert "Successfully restored pellet database." in resp.text()
    assert read_pellet_db()["log"] == {"seed-restore": "id-999"}


# --- `boot` action --------------------------------------------------------


def test_boot_action_via_real_ui(live_server, page, hazard_guard):
    apply_settings(lambda s: s["globals"].__setitem__("boot_to_monitor", False))
    page.goto(f"{live_server}/admin/")
    page.click("#v-pills-boot-tab")
    page.wait_for_selector("#v-pills-boot.active")
    assert page.locator("#boot_to_monitor").is_checked() is False

    page.locator("#boot_to_monitor").check(force=True)
    with page.expect_navigation():
        page.locator('form[action="/admin/boot"] button[type="submit"]').click()

    assert read_settings_from_server()["globals"]["boot_to_monitor"] is True
    assert page.locator("#boot_to_monitor").is_checked() is True


# --- Most disruptive to shared state -- run last -------------------------


def test_restoresettings_neutralized_via_direct_post(live_server, page, hazard_guard):
    backup_settings_dict = default_settings()
    backup_settings_dict["globals"]["grill_name"] = "Restored From Backup"
    backup_filename = "PiFire_test_restore.json"
    write_generic_json(backup_settings_dict, os.path.join(hazard_guard["backup_dir"], backup_filename))

    apply_settings(lambda s: s["globals"].__setitem__("grill_name", "Before Restore"))

    calls_before = len(hazard_guard["calls"])
    resp = page.request.post(
        f"{live_server}/admin/setting",
        multipart={
            "restoresettings": "true",
            "localfile": backup_filename,
            "uploadfile": {"name": "", "mimeType": "application/octet-stream", "buffer": b""},
        },
    )

    assert resp.status == 200
    assert "Restarting Server" in resp.text()
    assert hazard_guard["restart_scripts"].call_count >= 1
    assert ("restart_scripts", (), {}) in hazard_guard["calls"][calls_before:]
    assert read_settings_from_server()["globals"]["grill_name"] == "Restored From Backup"

    _reset_server_status(hazard_guard["flask_app"])


def test_factorydefaults_neutralized(live_server, page, hazard_guard):
    apply_settings(lambda s: s["globals"].__setitem__("grill_name", "Should Be Wiped"))
    write_history(
        {
            "probe_history": {"primary": {"Grill": 200}, "food": {}, "aux": {}},
            "primary_setpoint": 200,
            "notify_targets": {},
        }
    )
    assert len(read_history()) > 0

    calls_before = len(hazard_guard["calls"])
    resp = page.request.post(f"{live_server}/admin/setting", form={"factorydefaults": "true"})

    assert resp.status == 200
    assert "Restarting Server" in resp.text()
    assert hazard_guard["restart_scripts"].call_count >= 1
    new_calls = hazard_guard["calls"][calls_before:]
    system_calls = [c for c in new_calls if c[0] == "os.system"]
    assert any("settings.json" in c[1] for c in system_calls), system_calls
    assert any("pelletdb.json" in c[1] for c in system_calls), system_calls
    assert ("restart_scripts", (), {}) in new_calls

    settings = read_settings_from_server()
    assert settings["globals"]["grill_name"] == default_settings()["globals"]["grill_name"]
    assert read_history() == []

    _reset_server_status(hazard_guard["flask_app"])
