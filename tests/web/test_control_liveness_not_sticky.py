"""Control-process liveness is an observation, not a durable error.

``blueprints/mobile/socket_io.py::_check_control_status`` reads the heartbeat
the control loop stamps as it works and, when that stamp has gone stale,
reports "The control process did not respond...". That string used to be
**appended to the errors blob**, which is the wrong store for it:

* Every other writer of the errors table (``controller/runtime/devices.py``,
  ``runner.py``, ``controller.py``, ``common/extra_installer.py``) records a
  fact about a *past* failure that cannot un-happen -- a display that would not
  load, a dependency install that failed. The lifecycle matches that: each
  producing process calls ``flush_errors()`` for its own kind at its own boot,
  so each kind holds "errors accumulated since that process started".
* Liveness is the opposite kind of fact. It is a statement about *right now*,
  made by the web process, and it stops being true the moment control answers
  again. Filed in the errors table it became permanent: ``read_errors()`` is a
  plain non-destructive read (unlike ``warnings`` on the very same payload,
  which drains and self-heals frame to frame), so a single missed answer rode
  every ``socket_dash_data`` frame until the control process restarted. No
  route, socket action or API command could clear it.

The fix is a non-sticky signal rather than a clearing endpoint: the check now
records its verdict in memory and ``_get_dash_data`` composes it into the
payload it is already building. Each durable kind keeps its single owner, the
web tier only ever reads the kinds it does not own, and the observation
self-heals on the next check that succeeds.

``blueprints/dash/routes.py::dash_page`` already worked this way -- it appends
the same string to the local list it hands the template and persists nothing.
These tests pin both consumers.
"""

import os
import time
import types
from unittest import mock

import pytest

from common.app import CONTROL_DOWN_ERROR
from common.common import ErrorKind
from common.persistence.control import (
    default_control,
    write_control_snapshot,
)
from common.persistence.runtime import (
    CONTROL_HEARTBEAT_KEY,
    CONTROL_HEARTBEAT_STALE_AFTER,
    init_status,
    read_errors,
    write_errors,
    write_generic_key,
    write_pellet_db,
    write_settings_store,
)
from common.defaults import default_pellets, default_settings
from tests.conftest import REPO_BASE

_DURABLE = "Grill Platform Error: Could not load the grill platform module."


@pytest.fixture
def consumers(ds):
    """Seed a datastore the socket emitter can build a real payload against."""
    write_settings_store(default_settings())
    write_control_snapshot(default_control(), origin="test-liveness")
    write_pellet_db(default_pellets())
    init_status()
    write_generic_key("probe_device_info", {})

    import blueprints.mobile.socket_io as socket_io

    # The liveness verdict is process-local module state; a test that leaves it
    # False would poison every later test in the session.
    previous = socket_io._control_alive
    socket_io._control_alive = True
    try:
        yield types.SimpleNamespace(sio=socket_io)
    finally:
        socket_io._control_alive = previous


def _check(consumers, alive):
    """Run one real liveness check against a fresh / stale control heartbeat."""
    age = 0 if alive else CONTROL_HEARTBEAT_STALE_AFTER + 5
    write_generic_key(CONTROL_HEARTBEAT_KEY, time.time() - age)
    consumers.sio._check_control_status()


def _socket_tick(consumers):
    """One socket_dash_data payload, as the broadcast loop would build it."""
    from common.persistence.runtime import read_pellets_store, read_settings_store

    settings = read_settings_store()
    pelletdb = read_pellets_store()

    # Keep probe assembly out of this liveness test, but use the real wire
    # structure rather than invalid empty dicts now that the producer validates
    # every frame against DashSocketPayload.
    def probe_payloads(probe_type, *_args):
        count = 1 if probe_type == "Primary" else 4
        return [consumers.sio._get_probe_structure(probe_type, settings) for _ in range(count)]

    with (
        mock.patch.object(consumers.sio, "_get_probe_data", side_effect=probe_payloads),
        mock.patch.object(consumers.sio, "read_current", return_value=[[], [], []]),
    ):
        return consumers.sio._get_dash_data(settings, pelletdb)


def test_a_failed_liveness_check_does_not_write_the_durable_errors_blob(consumers):
    """The bug: a transient observation was persisted into the control
    process's error store, where nothing the user can reach clears it."""
    _check(consumers, alive=False)

    assert read_errors(ErrorKind.ALL) == [], "the web tier persisted a liveness observation into the errors table"


def test_the_payload_reports_the_control_process_as_down(consumers):
    """Not persisting it must not mean not reporting it: the dashboard still
    derives `controlAlive` by matching this string in `dash.errors`
    (web-react/src/helpers/dashboard/health.ts)."""
    _check(consumers, alive=False)

    assert CONTROL_DOWN_ERROR in _socket_tick(consumers)["errors"]


def test_the_report_self_heals_on_the_next_successful_check(consumers):
    """The whole point: once control answers again, the very next payload is clean."""
    _check(consumers, alive=False)
    assert CONTROL_DOWN_ERROR in _socket_tick(consumers)["errors"]

    _check(consumers, alive=True)

    assert CONTROL_DOWN_ERROR not in _socket_tick(consumers)["errors"]


def test_repeated_ticks_keep_reporting_while_control_stays_down(consumers):
    """A client connecting late must still be told the control process is down;
    the signal is recomputed per frame, not consumed by the first reader."""
    _check(consumers, alive=False)

    for _ in range(3):
        assert CONTROL_DOWN_ERROR in _socket_tick(consumers)["errors"]


def test_a_control_process_error_is_untouched_by_a_successful_check(consumers):
    """Guard against "fixing" this by having the check delete from the blob:
    errors the control process wrote are durable and are NOT ours to clear."""
    write_errors(ErrorKind.CONTROL, [_DURABLE])

    _check(consumers, alive=True)

    assert read_errors(ErrorKind.CONTROL) == [_DURABLE]
    assert _socket_tick(consumers)["errors"] == [_DURABLE]


def test_a_control_process_error_survives_a_failed_check_and_is_reported_alongside(consumers):
    write_errors(ErrorKind.CONTROL, [_DURABLE])

    _check(consumers, alive=False)

    assert read_errors(ErrorKind.CONTROL) == [_DURABLE], "a failed liveness check rewrote the control process's list"
    assert _socket_tick(consumers)["errors"] == [_DURABLE, CONTROL_DOWN_ERROR]


_SEED = """
import os
from common import datastore
from common.common import ErrorKind
from common.persistence.control import write_control_snapshot
from common.persistence.runtime import (
    init_status, write_errors, write_pellets_store, write_settings_store,
)
from common.defaults import default_control, default_pellets, default_settings

datastore._reset_for_tests(os.environ["PIFIRE_DB_PATH"])
datastore.init()
write_settings_store(default_settings())
write_pellets_store(default_pellets())
init_status()
write_control_snapshot(default_control(), origin="test")
write_errors(ErrorKind.CONTROL, ["control banner"])
write_errors(ErrorKind.DISPLAY, ["display banner"])
write_errors(ErrorKind.WEB, ["web banner"])
"""


def test_booting_the_webapp_clears_only_its_own_kind(tmp_path):
    """app.py flushes ErrorKind.WEB at its own boot, exactly as control.py and
    display_process.py do for theirs. The control and display processes are
    separately supervised, so their banners are not the webapp's to discard.

    Boots the real app.py in a subprocess: the flush runs at module import, so
    an already-imported `app` in this session would not re-run it.
    """
    import sqlite3
    import subprocess
    import sys

    db = str(tmp_path / "boot.db")
    env = {**os.environ, "PIFIRE_DB_PATH": db, "QT_QPA_PLATFORM": "offscreen", "SDL_VIDEODRIVER": "dummy"}

    subprocess.run([sys.executable, "-c", _SEED], cwd=REPO_BASE, env=env, check=True, timeout=120)
    subprocess.run([sys.executable, "-c", "import app"], cwd=REPO_BASE, env=env, check=True, timeout=120)

    with sqlite3.connect(db) as conn:
        stored = conn.execute("SELECT kind, message FROM errors ORDER BY kind, id").fetchall()

    assert ("web", "web banner") not in stored, "the webapp did not clear its own banners at boot"
    assert ("control", "control banner") in stored, "the webapp cleared the control process's banners"
    assert ("display", "display banner") in stored, "the webapp cleared the display process's banners"
