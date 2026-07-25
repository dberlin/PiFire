"""Warnings must survive being read by one consumer so the other still sees them.

``list_warnings`` has two independent consumers:

* ``blueprints/dash/routes.py::dash_page`` -- renders them into the page banner
  (templates/base.html) on a full HTML page load;
* ``blueprints/mobile/socket_io.py::_get_dash_data`` -- puts them in the
  ``socket_dash_data`` payload, which the broadcast loop re-emits on every tick
  and ``_emit_app_data_to`` sends to each freshly-connected client.

Both used to call ``read_warnings()``, which returned the warnings **and
flushed the table**. Whichever consumer ran first ate the other's warnings, so
a settings-upgrade notice (``common/settings_migration.py``) could be swallowed
by a background socket tick and never rendered for the user at all.

These tests pin the fixed contract: reads are non-destructive, and exactly one
consumer -- the page render, the place a human actually sees the banner --
drains.
"""

import types
from unittest import mock

import pytest
from flask import Flask

from common.common import WriteKind
from common.datastore_accessors import (
    default_control,
    init_status,
    read_warnings,
    write_control,
    write_generic_key,
    write_pellet_db,
    write_settings_store,
    write_warning,
)
from common.defaults import default_pellets, default_settings

_WARNING = "Upgrading your settings from 1.7.0 to 1.8.0."


@pytest.fixture
def consumers(ds):
    """Seed a datastore both consumers can run against."""
    write_settings_store(default_settings())
    write_control(default_control(), WriteKind.OVERWRITE, origin="test-warnings")
    write_pellet_db(default_pellets())
    init_status()
    write_generic_key("probe_device_info", {})

    import blueprints.dash.routes as dash_routes
    import blueprints.mobile.socket_io as socket_io

    return types.SimpleNamespace(dash=dash_routes, sio=socket_io)


def _socket_tick(consumers):
    """One socket_dash_data payload, as the broadcast loop would build it."""
    from common.datastore_accessors import read_pellets_store, read_settings_store

    settings = read_settings_store()
    pelletdb = read_pellets_store()
    # The probe assembly needs a control-runtime-seeded `current`, which this
    # harness deliberately lacks; stub it out. The warnings read under test
    # happens in _get_dash_data itself, not in the probe helpers.
    with (
        mock.patch.object(consumers.sio, "_get_probe_data", return_value=[{}, {}, {}, {}]),
        mock.patch.object(consumers.sio, "read_current", return_value=[[], [], []]),
    ):
        return consumers.sio._get_dash_data(settings, pelletdb)


def _render_dash_page(consumers):
    """Render blueprints.dash.routes.dash_page, capturing its template context."""
    captured = {}

    def _fake_render(_template, **ctx):
        captured.update(ctx)
        return "ok"

    app = Flask(__name__)
    with (
        app.test_request_context("/"),
        mock.patch.object(consumers.dash, "render_template", side_effect=_fake_render),
        mock.patch.object(consumers.dash, "process_command"),
        mock.patch.object(consumers.dash, "get_system_command_output", return_value={"result": "OK"}),
        mock.patch.object(consumers.dash, "read_probe_status", return_value={}),
    ):
        consumers.dash.dash_page()
    return captured


def test_socket_tick_does_not_eat_the_dash_pages_warnings(consumers):
    """The bug: a background socket tick used to consume the page's warnings."""
    write_warning(_WARNING)

    payload = _socket_tick(consumers)
    assert payload["warnings"] == [_WARNING]

    context = _render_dash_page(consumers)
    assert context["warnings"] == [_WARNING], (
        "the socket broadcast tick consumed the warning before the page could render it"
    )


def test_repeated_socket_ticks_keep_reporting_the_warning(consumers):
    """A client connecting late must still receive an outstanding warning."""
    write_warning(_WARNING)

    assert _socket_tick(consumers)["warnings"] == [_WARNING]
    assert _socket_tick(consumers)["warnings"] == [_WARNING]
    assert _socket_tick(consumers)["warnings"] == [_WARNING]


def test_the_page_render_is_the_single_drain_point(consumers):
    """dash_page shows the banner to a human, so it -- and only it -- clears."""
    write_warning(_WARNING)

    assert _render_dash_page(consumers)["warnings"] == [_WARNING]

    assert read_warnings() == []
    assert _socket_tick(consumers)["warnings"] == []
    assert _render_dash_page(consumers)["warnings"] == []
