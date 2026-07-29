from common.datastore_accessors import (
    flush_current,
    init_status,
    read_pellet_db,
    read_settings,
    write_generic_key,
    write_warning,
)


def _dash_data():
    # Build the real payload the React shell subscribes to, rather than
    # asserting against a hand-written literal: a fixture written from the
    # producer's own fallbacks only proves it agrees with itself.
    from blueprints.mobile import socket_io

    # _get_dash_data also assembles probe/status data from "control:status",
    # "control:current" and "probe_device_info"; none of these self-heal like
    # the settings/pellets blobs do, so seed them the way the control-loop
    # does at startup (see init_status()/flush_current() in
    # datastore_accessors).
    init_status()
    flush_current()
    write_generic_key("probe_device_info", {})

    return socket_io._get_dash_data(read_settings(), read_pellet_db())


def test_payload_carries_warnings_and_their_high_water_mark(ds):
    write_warning("hopper low")
    data = _dash_data()
    assert data["warnings"] == ["hopper low"]
    assert isinstance(data["warningsMaxId"], int)


def test_payload_max_id_is_none_when_there_are_no_warnings(ds):
    data = _dash_data()
    assert data["warnings"] == []
    assert data["warningsMaxId"] is None


def test_payload_read_is_non_destructive(ds):
    # The poll repeats; a consuming read would hand a warning to exactly one
    # payload and lose it for every client that reconnects afterwards.
    write_warning("hopper low")
    _dash_data()
    assert _dash_data()["warnings"] == ["hopper low"]
