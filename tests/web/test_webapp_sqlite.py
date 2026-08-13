"""webapp / blueprint free-function path reads+writes SQLite, no Valkey.

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
from common.persistence.control import (
    write_control_snapshot,
)
from common.persistence.runtime import (
    read_connected_users,
    read_current,
    read_settings,
    init_status,
    remove_connected_user,
    write_connected_user,
    write_current,
    write_generic_key,
    write_pellets_store,
    write_settings_store,
)
from common.datastore_accessors import (
    read_history,
    write_history,
)
from common.defaults import default_control, default_pellets, default_settings  # noqa: E402


def _seed_database():
    datastore._reset_for_tests(_DB_PATH)
    datastore.init()
    settings = default_settings()
    settings["globals"]["grill_name"] = _SEEDED_GRILL_NAME
    write_settings_store(settings)
    write_pellets_store(default_pellets())
    init_status()
    write_control_snapshot(default_control(), origin="test")
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


_SEEDED_GRILL_NAME = "T18 Seeded Grill"
_seed_database()

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
    _seed_database()


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


def test_api_settings_post_writes_through_to_sqlite():
    """Round-trip a write through the blueprint (write_settings) and confirm
    it lands in SQLite by reading it back through common.common directly."""
    flask_app.config.update(TESTING=True)
    client = flask_app.test_client()

    resp = client.post("/api/settings", json={"globals": {"grill_name": "T18 Written Via Blueprint"}})

    assert resp.status_code == 201
    assert resp.get_json()["result"] == "success"
    assert read_settings()["globals"]["grill_name"] == "T18 Written Via Blueprint"


# --- Goal 3 (always exercised): the common.common free-function path -------
# This is the essential assertion for this module -- it does not depend on
# the app booting, and proves the blueprint-facing read/write functions work
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
    # slated for removal and are not imported by anything on the
    # webapp/blueprint path exercised above. That's the meaningful
    # assertion for "no Valkey present" here: PiFire's own code never
    # reaches for a Valkey client.
    assert "common.valkey_queue" not in sys.modules
    assert "common.valkey_handler" not in sys.modules
