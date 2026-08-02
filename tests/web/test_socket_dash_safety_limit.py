"""The grill's shutdown limit has to reach the dashboard.

``settings["safety"]["maxtemp"]`` is what ``controller/runtime/logic/safety.py``
shuts the grill down above, and it is user-editable. The React dashboard bounds
every setpoint it offers by it, so it has to be on the dash payload -- the only
channel that route has, since it runs without a loader.

Pinned at the producing end because the TypeScript side can only pin its own
fixture: a field renamed or dropped here would leave that fixture green and the
real modal falling back to a fixed ceiling with nothing failing.
"""

from common.datastore_accessors import (
    flush_current,
    init_status,
    read_pellet_db,
    read_settings,
    write_generic_key,
    write_settings,
)


def _dash_data():
    from blueprints.mobile import socket_io

    # Same seeding _get_dash_data needs elsewhere: status/current/device-info do
    # not self-heal the way the settings and pellet blobs do.
    init_status()
    flush_current()
    write_generic_key("probe_device_info", {})

    return socket_io._get_dash_data(read_settings(), read_pellet_db())


def test_payload_carries_the_safety_limit(ds):
    assert _dash_data()["safetyMaxTemp"] == read_settings()["safety"]["maxtemp"]


def test_the_limit_tracks_the_setting_rather_than_a_constant(ds):
    # Not the 550 default, and not the 500 the UI used to hardcode, so neither
    # can make this pass by coincidence.
    settings = read_settings()
    settings["safety"]["maxtemp"] = 412
    write_settings(settings)

    assert _dash_data()["safetyMaxTemp"] == 412


def test_the_limit_is_not_the_gauge_ceiling(ds):
    # primaryProbe.maxTemp comes from the dashboard's display config and is a
    # different number; conflating them is exactly the mistake this guards.
    settings = read_settings()
    settings["safety"]["maxtemp"] = 412
    write_settings(settings)

    data = _dash_data()
    assert data["safetyMaxTemp"] == 412
    assert data["primaryProbe"]["maxTemp"] != 412
