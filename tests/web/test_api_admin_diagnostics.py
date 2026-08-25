"""/api/admin/diagnostics/download -- the database plus every log, as one zip.

The database is the whole point of this endpoint, and it is the part that cannot
be zipped naively. common/datastore.py opens pifire.db in WAL mode and control.py,
app.py and display_process.py all write to it concurrently, so copying the live
file hands the recipient a torn snapshot missing every row still sitting in the
-wal. The builder runs VACUUM INTO instead; the WAL test below is what holds it
to that, and a shutil.copy implementation cannot pass it.
"""

import json
import os
import re
import sqlite3
import zipfile
from io import BytesIO

import pytest

import blueprints.api_admin.admin_api as admin_api


@pytest.fixture
def env(ds, tmp_path, monkeypatch):
    from app import app as flask_app

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    #  Resolved at call time by the builder, exactly as build_log_archive does,
    #  so this monkeypatch is seen.
    monkeypatch.setattr(admin_api, "LOG_FOLDER", str(log_dir) + os.sep)

    flask_app.config["TESTING"] = True
    with flask_app.test_client() as client:
        yield {"client": client, "dir": log_dir, "ds": ds}


def _download(env):
    resp = env["client"].get("/api/admin/diagnostics/download")
    assert resp.status_code == 200
    return resp


def _archive(env):
    return zipfile.ZipFile(BytesIO(_download(env).data))


def test_bundle_carries_the_database_and_every_log(env):
    (env["dir"] / "events.log").write_text("contents of events.log")
    (env["dir"] / "control.log").write_text("contents of control.log")

    archive = _archive(env)

    assert sorted(archive.namelist()) == [
        "logs/control.log",
        "logs/events.log",
        "pifire.db",
    ]
    assert archive.read("logs/events.log") == b"contents of events.log"


def test_bundled_database_carries_rows_still_in_the_wal(env, tmp_path):
    """The test a file copy cannot pass.

    The probe row is written and deliberately not checkpointed, so it lives only
    in pifire.db-wal. Reading the main database file alone -- which is what a
    recipient of a copied pifire.db does -- would not see it.
    """
    #  kv.value carries a json_valid CHECK constraint, hence the encoding.
    probe = json.dumps("written-but-not-checkpointed")
    env["ds"].set_blob("diagnostics-probe", probe)

    extracted = tmp_path / "extracted.db"
    extracted.write_bytes(_archive(env).read("pifire.db"))

    connection = sqlite3.connect(extracted)
    try:
        row = connection.execute("SELECT value FROM kv WHERE key=?", ("diagnostics-probe",)).fetchone()
    finally:
        connection.close()

    assert row is not None
    assert row[0] == probe


def test_bundled_database_stands_alone_without_its_wal(env):
    """VACUUM INTO emits a fully checkpointed database, so the recipient needs
    the one file and nothing else. Asserting it means a future change that ships
    the -wal and -shm alongside instead does not pass quietly."""
    env["ds"].set_blob("diagnostics-probe", json.dumps("standalone"))

    archive = _archive(env)
    assert [name for name in archive.namelist() if name.startswith("pifire.db")] == ["pifire.db"]


def test_download_leaves_the_live_database_readable_and_intact(env):
    """The source is opened read-only: a diagnostics download must not be able to
    disturb a running cook."""
    probe = json.dumps("still-here")
    env["ds"].set_blob("diagnostics-probe", probe)

    _download(env)

    assert env["ds"].get_blob("diagnostics-probe") == probe


def test_bundle_archives_rotated_log_members_too(env):
    (env["dir"] / "events.log").write_text("current")
    (env["dir"] / "events.log.1").write_text("rotated")
    (env["dir"] / "notes.txt").write_text("not a log")

    names = sorted(_archive(env).namelist())

    assert names == ["logs/events.log", "logs/events.log.1", "pifire.db"]


def test_download_is_an_attachment_with_a_stamped_name(env):
    resp = _download(env)

    disposition = resp.headers["Content-Disposition"]
    assert disposition.startswith("attachment")
    assert re.search(r"PiFire_Diagnostics_\d{8}-\d{6}\.zip", disposition)


def test_download_does_not_stage_in_a_predictable_path(env):
    """A guessable /tmp name is how an attacker plants or reads content a later
    step trusts, so the bundle is built in a private mkdtemp."""
    _download(env)

    assert not os.path.exists("/tmp/pifire-diagnostics")
