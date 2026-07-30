"""Control-process liveness is an observation, not a durable error.

``blueprints/mobile/socket_io.py::_check_control_status`` reads the heartbeat
the control loop stamps as it works and, when that stamp has gone stale,
reports "The control process did not respond...". That string used to be
**appended to the errors blob**, which is the wrong store for it:

* Every other writer of that blob is the control process or one of its
  subprocesses (``controller/runtime/devices.py``, ``runner.py``,
  ``controller.py``, ``common/extra_installer.py``), and each records a fact
  about a *past* failure that cannot un-happen -- a display that would not
  load, a dependency install that failed. The blob's whole lifecycle matches
  that: ``flush_errors()`` is called exactly once, from ``control.py``'s boot
  path, so "errors accumulated since the control process started".
* Liveness is the opposite kind of fact. It is a statement about *right now*,
  made by the web process, and it stops being true the moment control answers
  again. Filed in the errors blob it became permanent: ``read_errors()`` is a
  plain non-destructive read (unlike ``warnings`` on the very same payload,
  which drains and self-heals frame to frame), so a single missed answer rode
  every ``socket_dash_data`` frame until the control process restarted. No
  route, socket action or API command could clear it.

The fix is a non-sticky signal rather than a clearing endpoint: the check now
records its verdict in memory and ``_get_dash_data`` composes it into the
payload it is already building. The durable blob keeps its single owner (the
control process), the web tier only ever reads it, and the observation
self-heals on the next check that succeeds.

``blueprints/dash/routes.py::dash_page`` already worked this way -- it appends
the same string to the local list it hands the template and persists nothing.
These tests pin both consumers.
"""

import time
import types
from unittest import mock

import pytest

from common.app import CONTROL_DOWN_ERROR
from common.common import WriteKind
from common.datastore_accessors import (
    CONTROL_HEARTBEAT_KEY,
    CONTROL_HEARTBEAT_STALE_AFTER,
    default_control,
    init_status,
    read_errors,
    write_control,
    write_errors,
    write_generic_key,
    write_pellet_db,
    write_settings_store,
)
from common.defaults import default_pellets, default_settings

_DURABLE = "Grill Platform Error: Could not load the grill platform module."


@pytest.fixture
def consumers(ds):
    """Seed a datastore the socket emitter can build a real payload against."""
    write_settings_store(default_settings())
    write_control(default_control(), WriteKind.OVERWRITE, origin="test-liveness")
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
    from common.datastore_accessors import read_pellets_store, read_settings_store

    settings = read_settings_store()
    pelletdb = read_pellets_store()
    # The probe assembly needs a control-runtime-seeded `current`, which this
    # harness deliberately lacks; stub it out. The errors composition under
    # test happens in _get_dash_data itself, not in the probe helpers.
    with (
        mock.patch.object(consumers.sio, "_get_probe_data", return_value=[{}, {}, {}, {}]),
        mock.patch.object(consumers.sio, "read_current", return_value=[[], [], []]),
    ):
        return consumers.sio._get_dash_data(settings, pelletdb)


def test_a_failed_liveness_check_does_not_write_the_durable_errors_blob(consumers):
    """The bug: a transient observation was persisted into the control
    process's error store, where nothing the user can reach clears it."""
    _check(consumers, alive=False)

    assert read_errors() == [], "the web tier persisted a liveness observation into the errors blob"


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
    write_errors([_DURABLE])

    _check(consumers, alive=True)

    assert read_errors() == [_DURABLE]
    assert _socket_tick(consumers)["errors"] == [_DURABLE]


def test_a_control_process_error_survives_a_failed_check_and_is_reported_alongside(consumers):
    write_errors([_DURABLE])

    _check(consumers, alive=False)

    assert read_errors() == [_DURABLE], "a failed liveness check rewrote the control process's blob"
    assert _socket_tick(consumers)["errors"] == [_DURABLE, CONTROL_DOWN_ERROR]
