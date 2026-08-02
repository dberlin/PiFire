"""/api/admin/backups/* -- list, create, restore, upload, download.

Containment is the point of this module. The Flask equivalent built
`backup_path + request.form["localfile"]` by concatenation, so a `../` reached
anywhere the process could read; and because a restore READS a file and WRITES
it over live settings, that traversal was an arbitrary-file-LOAD, not merely an
arbitrary read. Every endpoint here gets a traversal test, plus the case that
actually proves containment: a VALID backup that simply lives elsewhere.

A settings restore calls restart_scripts(), so the fixture neutralizes it -- by
patching the name bound in blueprints.api_admin.routes' own globals, not
common.system's.
"""

import json
import os
import shutil
import tempfile
from unittest import mock

import pytest

import blueprints.api_admin.routes as admin_routes
import common.backups as backups_module


@pytest.fixture
def env(ds):
    """Isolated backup folder + a neutralized restart.

    BOTH BACKUP_PATH references are redirected: the blueprint reads
    current_app.config["BACKUP_PATH"], while common/backups.py's
    backup_settings() uses its own module-level constant. Patching one and not
    the other writes into the real checkout.
    """
    from app import app as flask_app

    #  `outside` MUST be the backup folder's PARENT, so that "../escape.json"
    #  actually resolves to a file that exists. With an unrelated temp dir the
    #  traversal resolves to nothing, os.path.isfile refuses it, and the test
    #  passes against a concatenating implementation -- proving nothing. That
    #  is not hypothetical: this fixture had exactly that shape, and a negative
    #  control (reintroducing the concatenation) left all 16 tests green.
    outside_dir = tempfile.mkdtemp(prefix="pifire_test_admin_backups_")
    tmp_dir = os.path.join(outside_dir, "backups")
    os.makedirs(tmp_dir)
    path = tmp_dir + os.sep

    saved = (flask_app.config["BACKUP_PATH"], backups_module.BACKUP_PATH)
    flask_app.config["TESTING"] = True
    flask_app.config["BACKUP_PATH"] = path
    backups_module.BACKUP_PATH = path

    calls = []
    with (
        mock.patch.object(admin_routes, "restart_scripts", side_effect=lambda: calls.append("restart_scripts")),
        mock.patch("os.system", side_effect=lambda cmd: calls.append(("os.system", cmd)) or 0),
        flask_app.test_client() as client,
    ):
        yield {
            "client": client,
            "dir": path,
            "outside": outside_dir + os.sep,
            "calls": calls,
        }

    flask_app.config["BACKUP_PATH"], backups_module.BACKUP_PATH = saved
    shutil.rmtree(outside_dir, ignore_errors=True)  # contains tmp_dir


def _write_settings_backup(folder, name, grill_name):
    from common.defaults import default_settings

    payload = default_settings()
    payload["globals"]["grill_name"] = grill_name
    with open(os.path.join(folder, name), "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    return name


HOSTILE = ["../escape.json", "../../etc/passwd", "/etc/passwd", "sub/../../escape.json", ""]


# --------------------------------------------------------------------------
# create / list
# --------------------------------------------------------------------------


def test_create_settings_backup_returns_a_bare_name_that_then_lists(env):
    resp = env["client"].post("/api/admin/backups/create", json={"kind": "settings"})
    assert resp.status_code == 200
    name = resp.get_json()["data"]["filename"]
    assert os.sep not in name, "a path escaped into the response"
    assert name in env["client"].get("/api/admin/backups").get_json()["data"]["settings"]


def test_create_pelletdb_backup_lists_under_its_own_kind(env):
    resp = env["client"].post("/api/admin/backups/create", json={"kind": "pelletdb"})
    assert resp.status_code == 200
    name = resp.get_json()["data"]["filename"]
    listing = env["client"].get("/api/admin/backups").get_json()["data"]
    assert name in listing["pelletdb"]
    assert name not in listing["settings"]


def test_an_unknown_kind_is_refused(env):
    resp = env["client"].post("/api/admin/backups/create", json={"kind": "everything"})
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "kind"


# --------------------------------------------------------------------------
# restore
# --------------------------------------------------------------------------


def test_restoring_settings_applies_them_and_restarts(env):
    _write_settings_backup(env["dir"], "PiFire_01-01-26_120000.json", "RestoredGrill")
    resp = env["client"].post(
        "/api/admin/backups/restore",
        json={"kind": "settings", "file": "PiFire_01-01-26_120000.json"},
    )
    assert resp.status_code == 200

    from common.datastore_accessors import read_settings

    assert read_settings()["globals"]["grill_name"] == "RestoredGrill"
    assert env["calls"] == ["restart_scripts"]


def test_restoring_the_pellet_database_does_not_restart(env):
    """Matches Flask: settings are read once at boot by processes this request
    cannot reach; the pellet database is re-read on demand."""
    from common.datastore_accessors import read_pellet_db

    with open(os.path.join(env["dir"], "PelletDB_01-01-26_120000.json"), "w", encoding="utf-8") as h:
        json.dump(read_pellet_db(), h)

    resp = env["client"].post(
        "/api/admin/backups/restore",
        json={"kind": "pelletdb", "file": "PelletDB_01-01-26_120000.json"},
    )
    assert resp.status_code == 200
    assert env["calls"] == []


def test_a_malformed_pellet_backup_is_refused_and_the_store_is_untouched(env):
    """The settings branch of this route validates and refuses a bad backup
    with a 400. The pellet branch wrote whatever JSON the file held straight
    into the live store, and the same UI is what let the operator upload it."""
    from common.datastore_accessors import read_pellets_store

    before = read_pellets_store()
    with open(os.path.join(env["dir"], "PelletDB_01-01-26_130000.json"), "w", encoding="utf-8") as h:
        json.dump({"current": {"hopper_level": "not a number"}}, h)

    resp = env["client"].post(
        "/api/admin/backups/restore",
        json={"kind": "pelletdb", "file": "PelletDB_01-01-26_130000.json"},
    )

    assert resp.status_code == 400
    assert resp.get_json()["message"] == "invalid_backup"
    assert read_pellets_store() == before
    assert env["calls"] == []


def test_a_well_formed_pellet_backup_still_restores(env):
    from common.datastore_accessors import read_pellets_store
    from common.defaults import default_pellets

    payload = default_pellets()
    payload["brands"] = ["Generic", "Custom", "Restored Brand"]
    with open(os.path.join(env["dir"], "PelletDB_01-01-26_140000.json"), "w", encoding="utf-8") as h:
        json.dump(payload, h)

    resp = env["client"].post(
        "/api/admin/backups/restore",
        json={"kind": "pelletdb", "file": "PelletDB_01-01-26_140000.json"},
    )

    assert resp.status_code == 200
    assert "Restored Brand" in read_pellets_store()["brands"]


@pytest.mark.parametrize("hostile", HOSTILE)
@pytest.mark.parametrize("kind", ["settings", "pelletdb"])
def test_restore_refuses_a_traversal(env, kind, hostile):
    resp = env["client"].post("/api/admin/backups/restore", json={"kind": kind, "file": hostile})
    assert resp.status_code in (400, 404)
    assert env["calls"] == []


def test_restore_refuses_a_real_backup_outside_the_folder(env):
    """The case that proves containment rather than hiding a read error."""
    _write_settings_backup(env["outside"], "escape.json", "PWNED")
    resp = env["client"].post("/api/admin/backups/restore", json={"kind": "settings", "file": "../escape.json"})
    assert resp.status_code == 404
    assert env["calls"] == []

    from common.datastore_accessors import read_settings

    assert read_settings()["globals"]["grill_name"] != "PWNED"


def test_settings_restore_is_refused_unless_stopped(env):
    _write_settings_backup(env["dir"], "PiFire_01-01-26_120000.json", "RestoredGrill")
    with mock.patch.object(admin_routes, "read_control", return_value={"mode": "Hold"}):
        resp = env["client"].post(
            "/api/admin/backups/restore",
            json={"kind": "settings", "file": "PiFire_01-01-26_120000.json"},
        )
    assert resp.status_code == 409
    assert env["calls"] == []


# --------------------------------------------------------------------------
# download / upload
# --------------------------------------------------------------------------


def test_download_streams_the_backup(env):
    _write_settings_backup(env["dir"], "PiFire_01-01-26_120000.json", "Downloadable")
    resp = env["client"].get("/api/admin/backups/download?kind=settings&file=PiFire_01-01-26_120000.json")
    assert resp.status_code == 200
    assert b"Downloadable" in resp.data


@pytest.mark.parametrize("hostile", HOSTILE)
def test_download_refuses_a_traversal(env, hostile):
    resp = env["client"].get(f"/api/admin/backups/download?kind=settings&file={hostile}")
    assert resp.status_code in (400, 404)
    assert b"root:" not in resp.data


def test_upload_round_trips(env):
    import io

    payload = json.dumps({"globals": {"grill_name": "Uploaded"}}).encode()
    resp = env["client"].post(
        "/api/admin/backups/upload",
        data={"kind": "settings", "backup": (io.BytesIO(payload), "PiFire_uploaded.json")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    assert os.path.isfile(os.path.join(env["dir"], "PiFire_uploaded.json"))


def test_upload_refuses_a_non_json_extension(env):
    import io

    resp = env["client"].post(
        "/api/admin/backups/upload",
        data={"kind": "settings", "backup": (io.BytesIO(b"#!/bin/sh"), "evil.sh")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    assert not os.path.isfile(os.path.join(env["dir"], "evil.sh"))


def test_upload_cannot_escape_the_folder(env):
    import io

    resp = env["client"].post(
        "/api/admin/backups/upload",
        data={"kind": "settings", "backup": (io.BytesIO(b"{}"), "../escaped.json")},
        content_type="multipart/form-data",
    )
    #  secure_filename flattens the name, so this lands inside the folder rather
    #  than being refused -- what must NOT happen is a write above it.
    assert resp.status_code in (200, 400)
    assert not os.path.exists(os.path.join(env["outside"], "escaped.json"))
