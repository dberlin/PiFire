"""The probe-map hash on the dash socket frame.

React reads the settings blob once at mount; it does not re-render from a
server-rendered DOM the way the old Jinja pages did, so there is nothing for
a `ui_hash` mismatch to trigger a reload prompt for. What *can* go stale is
the settings blob itself: `set_probe_map()` rebuilds `history_page.probe_config`,
`settings["recipe"]["probe_map"]`, `dashboard[*].custom.hidden_cards` and
`control["notify_data"]` off probe labels, none of which the socket payload
carries. Riding the existing hash on the frame the client already receives
lets it detect that and refetch settings silently.
"""

import copy

from common.app import create_ui_hash
from common.persistence.runtime import flush_current, init_status, read_pellet_db, read_settings, write_generic_key


def _dash_data(settings, **status_over):
    from blueprints.mobile import socket_io
    from common.persistence.runtime import read_status, write_status

    # Same seeding _get_dash_data needs elsewhere: status/current/device-info do
    # not self-heal the way the settings and pellet blobs do. Any status
    # override has to be applied AFTER init_status(), which resets the blob.
    init_status()
    flush_current()
    write_generic_key("probe_device_info", {})
    if status_over:
        write_status({**read_status(), **status_over})

    return socket_io._get_dash_data(settings, read_pellet_db())


def test_dash_frame_carries_the_ui_hash(ds):
    """The probe-map hash rides the frame the client already receives, so a
    probe reconfiguration needs no extra request to notice."""
    settings = read_settings()

    frame = _dash_data(settings)

    assert frame["uiHash"] == create_ui_hash(settings)


def test_create_ui_hash_uses_the_settings_it_is_given(ds):
    """Passing settings must avoid the read, or the 1 Hz frame pays for a
    datastore round trip it already has the answer to."""
    settings = read_settings()
    one = dict(settings)
    other = copy.deepcopy(settings)
    other["probe_settings"]["probe_map"]["probe_info"] = []

    assert create_ui_hash(one) != create_ui_hash(other)
