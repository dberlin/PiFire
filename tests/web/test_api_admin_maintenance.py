"""/api/admin/state -- the single read the React admin page renders from.

No test in this module reaches a destructive action; those live in
test_api_admin_system.py behind the hazard fixture. This one is deliberately
kept free of that machinery so a failure here is unambiguous.
"""

import json
import os
import shutil
import tempfile

import pytest


@pytest.fixture
def client(ds):
    """Flask test client over an isolated temp SQLite datastore."""
    from app import app as flask_app

    flask_app.config["TESTING"] = True
    with flask_app.test_client() as test_client:
        yield test_client


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
