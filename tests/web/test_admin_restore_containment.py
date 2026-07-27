"""The admin restore path took a client-supplied filename and concatenated it.

`local_file = request.form["localfile"]` went straight into
`ctx.backup_path + local_file` at four sites. A `../` walked anywhere the
process could read -- and because a restore READS a file and WRITES it over live
settings (or the pellet database), the traversal was also an arbitrary-file-load
primitive, not merely an arbitrary read.

`common/file_browser.py::resolve_managed_file` exists to close exactly this and
was already used by blueprints/api_files; the admin blueprint never adopted it.

These are HTTP-level tests on purpose: tests/web/test_page_admin.py drives the
same handlers through Playwright, and `requires_chromium` would let a
containment test SKIP silently on a checkout without a browser.
"""

import io
import json
import os
import shutil
import tempfile
from unittest import mock

import pytest

import blueprints.admin.routes as admin_routes
import common.backups as backups_module


@pytest.fixture
def admin_env(ds):
    """Isolated backup folder + every lifecycle call neutralized.

    Patches the names bound in blueprints.admin.routes' OWN globals, not
    common.system's: routes.py does `from common.system import restart_scripts`
    at import, so patching the origin would miss the call site. See the module
    docstring of tests/web/test_page_admin.py -- this repo has had three real
    unintended reboots.
    """
    from app import app as flask_app

    tmp_dir = tempfile.mkdtemp(prefix="pifire_test_restore_")
    backup_path = tmp_dir + os.sep
    outside_dir = tempfile.mkdtemp(prefix="pifire_test_outside_")

    saved = (
        flask_app.config["BACKUP_PATH"],
        flask_app.config["UPLOAD_FOLDER"],
        backups_module.BACKUP_PATH,
    )
    flask_app.config["TESTING"] = True
    flask_app.config["BACKUP_PATH"] = backup_path
    flask_app.config["UPLOAD_FOLDER"] = backup_path
    backups_module.BACKUP_PATH = backup_path

    calls = []

    def _record(name):
        def _inner(*args, **kwargs):
            calls.append((name, args, kwargs))

        return _inner

    with (
        mock.patch("os.system", side_effect=lambda cmd: calls.append(("os.system", cmd)) or 0),
        mock.patch.object(admin_routes, "restart_scripts", side_effect=_record("restart_scripts")),
        mock.patch.object(admin_routes, "reboot_system", side_effect=_record("reboot_system")),
        mock.patch.object(admin_routes, "shutdown_system", side_effect=_record("shutdown_system")),
        flask_app.test_client() as client,
    ):
        yield {
            "client": client,
            "backup_path": backup_path,
            "outside_dir": outside_dir + os.sep,
            "calls": calls,
        }

    (
        flask_app.config["BACKUP_PATH"],
        flask_app.config["UPLOAD_FOLDER"],
        backups_module.BACKUP_PATH,
    ) = saved
    shutil.rmtree(tmp_dir, ignore_errors=True)
    shutil.rmtree(outside_dir, ignore_errors=True)


def _write_settings_backup(folder, name, grill_name):
    """A structurally valid settings backup, so a successful restore would
    actually take -- otherwise a refusal proves nothing."""
    from common.defaults import default_settings

    payload = default_settings()
    payload["globals"]["grill_name"] = grill_name
    with open(os.path.join(folder, name), "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    return name


def _post_restore(env, key, local_file):
    """The admin form always carries an uploadfile part, empty when the user
    picked a local file instead."""
    return env["client"].post(
        "/admin/setting",
        data={
            key: "true",
            "localfile": local_file,
            "uploadfile": (io.BytesIO(b""), ""),
        },
        content_type="multipart/form-data",
    )


@pytest.mark.parametrize("key", ["restoresettings", "restorepelletdb"])
@pytest.mark.parametrize(
    "hostile",
    ["../escape.json", "../../etc/passwd", "/etc/passwd", "subdir/../../escape.json"],
)
def test_traversal_is_refused(admin_env, key, hostile):
    resp = _post_restore(admin_env, key, hostile)
    assert resp.status_code == 200
    #  A refused restore must not restart the server. Asserted for both arms,
    #  but only the settings arm restarts at all -- hence the success-message
    #  check below, which is what makes the pelletdb arm mean anything.
    assert admin_env["calls"] == []
    body = resp.get_data(as_text=True)
    assert "Successfully restored settings." not in body
    assert "Successfully restored pellet database." not in body


def test_a_real_backup_outside_the_folder_is_refused(admin_env):
    """The case that proves containment rather than hiding a read error: a
    perfectly valid backup that simply lives somewhere else."""
    _write_settings_backup(admin_env["outside_dir"], "escape.json", "PWNED")
    resp = _post_restore(admin_env, "restoresettings", "../escape.json")
    assert resp.status_code == 200
    assert admin_env["calls"] == []

    from common.datastore_accessors import read_settings

    assert read_settings()["globals"]["grill_name"] != "PWNED"


def test_a_legitimate_local_restore_still_works(admin_env):
    """The fix must not break the feature it is guarding."""
    _write_settings_backup(admin_env["backup_path"], "PiFire_20260101-120000.json", "RestoredGrill")
    resp = _post_restore(admin_env, "restoresettings", "PiFire_20260101-120000.json")
    assert resp.status_code == 200

    from common.datastore_accessors import read_settings

    assert read_settings()["globals"]["grill_name"] == "RestoredGrill"
    assert [c[0] for c in admin_env["calls"]] == ["restart_scripts"]
