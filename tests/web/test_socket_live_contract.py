"""The live socket contract that web-react and mobile depend on.

`packages/pifire-core/src/liveConnection.ts::createLiveConnection` emits
`listen_app_data` on connect and listens for exactly two data events:
`socket_dash_data` and `socket_pellet_data`. Both in-tree apps reach it --
`web-react/src/helpers/useLiveState.ts` and `mobile/src/useLive.ts` both
import it from `@pifire/core/liveConnection`. (socket.io-client is a
dependency of packages/pifire-core, not of either app, so a search of the app
package.json files says "no client uses socket.io" and is wrong.)

Those two payloads are individually pinned against their wire contracts
elsewhere -- the dash one in test_socket_dash_payload_fields.py, the pellet one
in test_socketio_app_data.py. What was NOT pinned anywhere is the wiring: that
`_emit_app_data_to` actually sends those two event NAMES. This module pins that,
and re-pins both contracts here, so that removing the legacy
`get_app_data`/`post_app_data` dispatch -- which means cutting cases out of
test_socketio_app_data.py -- cannot quietly take the live feed's coverage with
it.
"""

from unittest import mock

import pytest

from common.defaults import default_control, default_pellets, default_settings
from common.persistence.control import write_control_snapshot
from common.persistence.runtime import (
    flush_current,
    init_status,
    write_generic_key,
    write_pellet_db,
    write_settings_store,
)
from common.web_contracts.core import DashSocketPayload, PelletSocketPayload

#: The complete set of data events the in-tree clients subscribe to.
LIVE_EVENTS = {"socket_dash_data", "socket_pellet_data"}


@pytest.fixture
def live(ds):
    """Seed the blobs `_get_dash_data` reads, and hand back the module.

    Mirrors the seeding in test_socketio_app_data.py's `sio` fixture: status,
    current and device-info do not self-heal the way the settings and pellet
    blobs do.
    """
    write_settings_store(default_settings())
    write_control_snapshot(default_control(), origin="test-live-contract")
    write_pellet_db(default_pellets())
    init_status()
    flush_current()
    write_generic_key("probe_device_info", {})

    from blueprints.mobile import socket_io

    # Process-local liveness state outlives the `ds` datastore, so a failure
    # leaking in from another test would put CONTROL_DOWN_ERROR in the payload.
    socket_io._set_control_alive(True)
    return socket_io


def _emitted_to_one_client(socket_io):
    """Capture every socketio.emit `_emit_app_data_to` makes, name -> payload."""
    emitted = {}

    def _capture(name, data, **kwargs):
        emitted[name] = data

    with mock.patch.object(socket_io.socketio, "emit", side_effect=_capture):
        socket_io._emit_app_data_to("client-1")
    return emitted


def test_a_connecting_client_is_sent_both_live_events(live):
    emitted = _emitted_to_one_client(live)

    assert LIVE_EVENTS <= set(emitted), (
        f"a client that renders purely from the socket lost an event it subscribes to: {emitted.keys()}"
    )


def test_dash_event_carries_a_payload_matching_its_wire_contract(live):
    payload = _emitted_to_one_client(live)["socket_dash_data"]

    validated = DashSocketPayload.model_validate(payload, strict=True)

    assert validated.model_dump(mode="json", by_alias=True, exclude_none=False) == payload


def test_pellet_event_carries_a_payload_matching_its_wire_contract(live):
    payload = _emitted_to_one_client(live)["socket_pellet_data"]

    validated = PelletSocketPayload.model_validate(payload, strict=True)

    assert validated.model_dump(mode="json", by_alias=True, exclude_none=False) == payload


def test_listen_app_data_is_the_entry_point_clients_emit(live):
    """createLiveConnection emits this by name on every connect."""
    assert callable(live.listen_app_data)
