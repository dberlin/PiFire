"""/api/admin/state -- the single read the React admin page renders from.

No test in this module reaches a destructive action; those live in
test_api_admin_system.py behind the hazard fixture. This one is deliberately
kept free of that machinery so a failure here is unambiguous.
"""

import json
import os
import shutil
import tempfile
from unittest import mock

import pytest


@pytest.fixture
def backup_dir():
    """Redirect BOTH places BACKUP_PATH is read.

    blueprints/admin reads current_app.config["BACKUP_PATH"]; common/backups.py
    imports the module-level constant. Patching one and not the other writes
    into the real checkout.
    """
    from app import app as flask_app
    import common.backups as backups_module

    tmp_dir = tempfile.mkdtemp(prefix="pifire_test_admin_state_")
    path = tmp_dir + os.sep
    saved = (flask_app.config["BACKUP_PATH"], backups_module.BACKUP_PATH)
    flask_app.config["BACKUP_PATH"] = path
    backups_module.BACKUP_PATH = path
    yield path
    flask_app.config["BACKUP_PATH"], backups_module.BACKUP_PATH = saved
    shutil.rmtree(tmp_dir, ignore_errors=True)


def _write_backup(folder, name):
    with open(os.path.join(folder, name), "w", encoding="utf-8") as handle:
        json.dump({"globals": {}}, handle)
    return name


def test_state_publishes_the_keys_the_page_renders(client, backup_dir):
    """The React types are generated against this exact key set."""
    resp = client.get("/api/admin/state")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert set(data) == {"system", "settings", "backups", "logs", "mode"}
    assert set(data["settings"]) == {"debug_mode", "boot_to_monitor"}
    assert set(data["backups"]) == {"settings", "pelletdb"}


def test_backups_are_split_by_kind_from_their_filename_prefix(client, backup_dir):
    """backup_settings() writes PiFire_<ts>.json and backup_pellet_db() writes
    PelletDB_<ts>.json into the same folder, so the prefix is the only thing
    that distinguishes them."""
    _write_backup(backup_dir, "PiFire_20260101-120000.json")
    _write_backup(backup_dir, "PelletDB_20260101-120000.json")
    _write_backup(backup_dir, "manifest.json")

    data = client.get("/api/admin/state").get_json()["data"]
    assert data["backups"]["settings"] == ["PiFire_20260101-120000.json"]
    assert data["backups"]["pelletdb"] == ["PelletDB_20260101-120000.json"]


def test_manifest_is_not_offered_as_a_restorable_backup(client, backup_dir):
    """manifest.json lives in the same folder and is bookkeeping, not a backup.
    Offering it would let a user 'restore' it and overwrite live settings with
    a manifest."""
    _write_backup(backup_dir, "manifest.json")
    data = client.get("/api/admin/state").get_json()["data"]
    assert data["backups"]["settings"] == []
    assert data["backups"]["pelletdb"] == []


def test_state_reports_the_current_mode(client, backup_dir):
    """The page disables every destructive control unless the grill is stopped,
    so it needs the mode in the same read."""
    data = client.get("/api/admin/state").get_json()["data"]
    assert isinstance(data["mode"], str)


# ---------------------------------------------------------------------------
# Maintenance clears and the two toggles.
#
# Factory reset is deliberately NOT here -- it calls restart_scripts(), so it
# lives in test_api_admin_system.py behind the proven hazard fixture.
# ---------------------------------------------------------------------------


def test_clear_events_does_not_shell_out(client, tmp_path, monkeypatch):
    """Flask runs `os.system("rm ./logs/events.log")`. This surface builds the
    path server-side and calls os.remove, so no shell is ever involved."""
    import blueprints.api_admin.admin_api as admin_api

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "events.log").write_text("noise")
    monkeypatch.setattr(admin_api, "LOG_FOLDER", str(log_dir) + os.sep)

    with mock.patch("os.system") as m_system:
        resp = client.post("/api/admin/maintenance", json={"action": "clear_events"})

    assert resp.status_code == 200
    assert not (log_dir / "events.log").exists()
    m_system.assert_not_called()


def test_clear_events_tolerates_a_missing_log(client, tmp_path, monkeypatch):
    """`rm` on a missing file is an error Flask swallowed; here it is success."""
    import blueprints.api_admin.admin_api as admin_api

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setattr(admin_api, "LOG_FOLDER", str(log_dir) + os.sep)
    resp = client.post("/api/admin/maintenance", json={"action": "clear_events"})
    assert resp.status_code == 200


def test_clear_pelletdb_log_empties_it(client):
    """pelletdb["log"] is a dict keyed by timestamp ({now: profile_id}), not a
    list -- .clear() works on both, which is why the handler reads the same
    either way, but a test must seed it correctly."""
    from common.datastore_accessors import read_pellet_db, write_pellet_db

    pelletdb = read_pellet_db()
    pelletdb["log"]["1767225600000"] = {"pelletid": "sentinel-profile-id", "deleted": False}
    write_pellet_db(pelletdb)
    assert read_pellet_db()["log"] != {}

    resp = client.post("/api/admin/maintenance", json={"action": "clear_pelletdb_log"})
    assert resp.status_code == 200
    assert read_pellet_db()["log"] == {}


def test_debug_mode_toggle_persists_and_flags_the_control_process(client):
    """_admin_setting_debugenabled raises settings_update alongside the write;
    without it the running control process never learns the setting changed.

    Asserted on the QUEUED write rather than on read_control(): the flag goes
    out as a delta that the control process drains, and no control process runs
    in this test -- so read_control() would report the pre-write value and the
    assertion would be testing the queue, not the intent.
    """
    import blueprints.api_admin.routes as admin_routes
    from common.datastore_accessors import read_settings

    with mock.patch.object(admin_routes, "write_control") as m_write:
        resp = client.post("/api/admin/settings", json={"debug_mode": True})

    assert resp.status_code == 200
    assert read_settings()["globals"]["debug_mode"] is True
    m_write.assert_called_once()
    #  control_delta names the member map "set" in the envelope, not
    #  "set_values" -- that is the keyword argument, not the wire key.
    delta = m_write.call_args.args[0]
    assert delta["set"]["settings_update"] is True


def test_boot_to_monitor_alone_does_not_flag_the_control_process(client):
    """Only debug_mode needs the control process to re-read settings."""
    import blueprints.api_admin.routes as admin_routes

    with mock.patch.object(admin_routes, "write_control") as m_write:
        client.post("/api/admin/settings", json={"boot_to_monitor": True})
    m_write.assert_not_called()


def test_boot_to_monitor_toggle_passes_schema_validation(client):
    """The plan flagged this as unverified: write_settings validates against the
    settings schema, so a field the schema rejects would 500 here."""
    from common.datastore_accessors import read_settings

    resp = client.post("/api/admin/settings", json={"boot_to_monitor": True})
    assert resp.status_code == 200
    assert read_settings()["globals"]["boot_to_monitor"] is True


@pytest.mark.parametrize(
    ("endpoint", "payload", "expected_field"),
    [
        ("/api/admin/maintenance", {"action": "rm_rf_slash"}, "action"),
        ("/api/admin/maintenance", {"action": "clear_history", "extra": True}, "extra"),
        ("/api/admin/settings", {"grill_name": "pwned"}, "grill_name"),
        ("/api/admin/settings", {"debug_mode": "yes"}, "debug_mode"),
    ],
    ids=["unknown_maintenance_action", "extra_json_member", "unknown_setting_key", "non_boolean_toggle"],
)
def test_admin_maintenance_or_settings_rejects_bad_input(client, endpoint, payload, expected_field):
    resp = client.post(endpoint, json=payload)
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == expected_field


def test_a_missing_backup_folder_is_an_empty_list_not_a_500(client):
    """A fresh install has no ./backups until something writes one."""
    from app import app as flask_app
    import common.backups as backups_module

    missing = os.path.join(tempfile.gettempdir(), "pifire_absent_backups_dir") + os.sep
    saved = (flask_app.config["BACKUP_PATH"], backups_module.BACKUP_PATH)
    flask_app.config["BACKUP_PATH"] = missing
    backups_module.BACKUP_PATH = missing
    try:
        resp = client.get("/api/admin/state")
        assert resp.status_code == 200
        assert resp.get_json()["data"]["backups"] == {"settings": [], "pelletdb": []}
    finally:
        flask_app.config["BACKUP_PATH"], backups_module.BACKUP_PATH = saved
