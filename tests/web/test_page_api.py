"""Playwright coverage for the `api` blueprint (blueprints/api/routes.py's
single `api_page` catch-all route, plus its three WLED sub-actions).

Pattern: see tests/web/test_page_settings.py (exemplar) and
tests/web/conftest.py (harness docs). `api_page` is a pure JSON/REST
endpoint (no HTML to drive through the UI), so every test here uses
`page.request.get/post` directly rather than real-UI interaction.

Overlap with tests/web/test_webapp_sqlite.py: that module already covers
`GET /api/settings`, `GET /api/current`, and a simple `POST /api/settings`
(flat key) via the Flask test client. This module does not repeat those;
it covers the rest of the route's GET/POST action ladder (`server`,
`control`, `hopper`, the `get`/`set`/`cmd`/`sys` `process_command` dispatch,
the unknown-action error paths) and adds one `POST /api/settings` test that
exercises `deep_update`'s *nested*-merge behavior (preserving sibling keys),
which the existing flat-key test doesn't exercise.

WLED endpoints (`wled_discover`, `wled_push_profiles`, `wled_test_profile`)
make real network calls in production (mDNS/zeroconf discovery, HTTP to a
WLED device). There is no WLED hardware in this test environment, so:

- `wled_discover` (mDNS/zeroconf browsing for the full clamped 5-30s
  timeout window, regardless of whether any device answers) is mocked at
  `notify.wled_discovery.discover_wled_devices` -- the route does a lazy
  `from notify.wled_discovery import discover_wled_devices` *inside* the
  request handler on every call, so patching the attribute on the real
  module (in-process; live_server shares this process via a background
  thread -- see conftest.py's thread-shared-datastore docs) is visible to
  the very next request the live server handles. This avoids both the
  multi-second real wait and any sandbox/multicast flakiness.
- `wled_push_profiles`/`wled_test_profile` open plain `requests` HTTP
  calls to `http://<device_address>/...`. Pointing `device_address` at a
  closed local TCP port (`127.0.0.1:1`) makes the underlying
  `requests.get/post` fail with an immediate `ConnectionError` (connection
  refused), not a multi-second timeout -- so the *real* code path's
  device-unreachable error envelope is exercised with no mocking and no
  hang. `wled_push_profiles`'s *success* envelope (`profiles_pushed`,
  `profiles` keys) is additionally covered by mocking
  `notify.wled_profiles.WLEDProfileManager` (same lazy-import-patch
  technique), since there is no way to reach that branch against a real
  address in this environment. `wled_test_profile`'s success envelope is
  covered by mocking `requests.post` directly for the same reason.
"""

from unittest.mock import MagicMock, patch

from tests.web.conftest import (
    drain_control_writes,
    read_control_from_server,
    read_settings_from_server,
    requires_chromium,
)

pytestmark = requires_chromium

# A closed local TCP port: nothing listens here, so any `requests` call
# against it fails fast with a connection-refused error rather than
# waiting out a timeout. Used to exercise the WLED endpoints' real
# device-unreachable error path without mocking and without hanging.
_CLOSED_PORT_ADDRESS = "127.0.0.1:1"


# --- api_page: GET actions ----------------------------------------------


def test_get_server_status(live_server, page):
    resp = page.request.get(f"{live_server}/api/server")
    assert resp.status == 201
    assert resp.json()["server_status"] == "available"


def test_get_control(live_server, page):
    resp = page.request.get(f"{live_server}/api/control")
    assert resp.status == 201
    body = resp.json()
    assert "control" in body
    assert "mode" in body["control"]


def test_get_hopper(live_server, page):
    resp = page.request.get(f"{live_server}/api/hopper")
    assert resp.status == 200
    body = resp.json()
    assert body["hopper_level"] == 100
    assert body["hopper_pellets"] == "Generic Alder"


def test_get_unknown_action_returns_404(live_server, page):
    resp = page.request.get(f"{live_server}/api/not_a_real_action")
    assert resp.status == 404
    assert resp.json()["Error"] == "Received GET request, without valid action"


# --- api_page: POST /api/settings (deep_update nested-merge) -----------


def test_post_settings_deep_merges_nested_keys(live_server, page):
    """Extends test_webapp_sqlite.py's flat-key POST /api/settings test:
    proves deep_update recurses into nested dicts, updating one leaf key
    while leaving sibling keys under the same parent untouched (a plain
    dict.update() at the top level would have clobbered the whole
    'globals' subtree instead)."""
    before = read_settings_from_server()
    original_units = before["globals"]["units"]

    resp = page.request.post(
        f"{live_server}/api/settings",
        data={"globals": {"grill_name": "API Nested Merge Grill"}},
    )
    assert resp.status == 201
    body = resp.json()
    assert body["result"] == "success"
    assert body["settings"] == "success"  # legacy/compat key, still asserted

    settings = read_settings_from_server()
    assert settings["globals"]["grill_name"] == "API Nested Merge Grill"
    # Sibling key under the same parent dict survives the merge.
    assert settings["globals"]["units"] == original_units


# --- api_page: POST /api/control (MERGE write) --------------------------


def test_post_control_merges_via_write_control(live_server, page):
    resp = page.request.post(
        f"{live_server}/api/control",
        data={"mode": "Startup", "s_plus": True},
    )
    assert resp.status == 201
    body = resp.json()
    assert body["result"] == "success"
    assert body["control"] == "success"  # legacy/compat key

    drain_control_writes()
    control = read_control_from_server()
    assert control["mode"] == "Startup"
    assert control["s_plus"] is True


def test_post_no_json_body_returns_400(live_server, page):
    """`request.json` is falsy (parses to `None`) for a JSON body of
    literal `null` -- the route's own `if not request.json: abort(400)`
    guard. (A non-JSON Content-Type instead trips Flask/werkzeug's own
    415 Unsupported Media Type before that guard is ever reached -- a
    distinct, earlier failure mode, not exercised here.)"""
    resp = page.request.post(
        f"{live_server}/api/settings",
        data="null",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 400


def test_post_unknown_action_returns_404(live_server, page):
    resp = page.request.post(f"{live_server}/api/not_a_real_action", data={"x": 1})
    assert resp.status == 404
    assert resp.json()["Error"] == "Received POST request no valid action."


# --- api_page: get/set/cmd/sys -> common.api_commands.process_command --


def test_get_uuid_dispatch(live_server, page):
    resp = page.request.get(f"{live_server}/api/get/uuid")
    assert resp.status == 201
    body = resp.json()
    assert body["result"] == "OK"
    assert body["data"]["uuid"] == read_settings_from_server()["server_info"]["uuid"]


def test_get_unrecognized_subcommand(live_server, page):
    """No (action, arg0) or action-only handler registered for
    ('get', 'not_a_real_subcommand') -- exercises
    _process_command_unknown's 'get' branch."""
    resp = page.request.get(f"{live_server}/api/get/not_a_real_subcommand")
    assert resp.status == 201
    body = resp.json()
    assert body["result"] == "ERROR"
    assert "not_a_real_subcommand" in body["message"]


def test_set_psp_dispatch_writes_control(live_server, page):
    """`set`/`psp` is dispatched via a plain GET (matching real API usage,
    e.g. /api/set/psp/225) -- the route's action-dispatch branch runs
    before the GET/POST split, so a GET request performs the write."""
    resp = page.request.get(f"{live_server}/api/set/psp/199")
    assert resp.status == 201
    assert resp.json()["result"] == "OK"

    drain_control_writes()
    control = read_control_from_server()
    assert control["mode"] == "Hold"
    assert control["primary_setpoint"] == 199


def test_sys_action_times_out_with_no_control_process(live_server, page):
    """`sys` waits (up to get_system_command_output's 1s default timeout)
    for a queued response the real control process would normally supply.
    This harness runs only the Flask app (see conftest.py), so no control
    process ever answers -- the 1s bounded wait always ends in the
    documented 'could not be found' fallback, exercised here as a genuine
    (bounded, non-hanging) characterization of that path."""
    resp = page.request.get(f"{live_server}/api/sys/supported_commands")
    assert resp.status == 201
    body = resp.json()
    assert body["result"] == "ERROR"
    assert body["message"] == "The requested command output could not be found."


# --- wled_discover (GET) -- mocked, no real mDNS -------------------------


def test_wled_discover_success_with_mocked_devices(live_server, page):
    fake_devices = [{"name": "Bar Lights", "ip": "192.168.1.50", "port": 80}]
    with patch("notify.wled_discovery.discover_wled_devices", return_value=fake_devices) as mock_discover:
        resp = page.request.get(f"{live_server}/api/wled_discover")

    assert resp.status == 200
    body = resp.json()
    assert body["result"] == "success"
    assert body["message"] == "Found 1 WLED devices"
    assert body["devices"] == fake_devices
    mock_discover.assert_called_once()


def test_wled_discover_no_devices_found(live_server, page):
    """The realistic no-hardware-present outcome: discovery runs (mocked
    here to avoid a real multi-second mDNS sweep) and finds nothing."""
    with patch("notify.wled_discovery.discover_wled_devices", return_value=[]):
        resp = page.request.get(f"{live_server}/api/wled_discover")

    assert resp.status == 200
    body = resp.json()
    assert body["result"] == "success"
    assert body["devices"] == []


def test_wled_discover_timeout_query_param_is_clamped(live_server, page):
    """?timeout is clamped to [5, 30] before being passed to
    discover_wled_devices -- assert the clamped value the mock actually
    received, both above and below the range."""
    with patch("notify.wled_discovery.discover_wled_devices", return_value=[]) as mock_discover:
        page.request.get(f"{live_server}/api/wled_discover?timeout=999")
    mock_discover.assert_called_once_with(30)

    with patch("notify.wled_discovery.discover_wled_devices", return_value=[]) as mock_discover:
        page.request.get(f"{live_server}/api/wled_discover?timeout=1")
    mock_discover.assert_called_once_with(5)


def test_wled_discover_exception_returns_error_envelope(live_server, page):
    with patch("notify.wled_discovery.discover_wled_devices", side_effect=RuntimeError("mdns unavailable")):
        resp = page.request.get(f"{live_server}/api/wled_discover")

    assert resp.status == 500
    body = resp.json()
    assert body["result"] == "error"
    assert "mdns unavailable" in body["message"]
    assert body["devices"] == []


# --- wled_push_profiles (POST) -------------------------------------------


def test_wled_push_profiles_missing_device_address(live_server, page):
    resp = page.request.post(f"{live_server}/api/wled_push_profiles", data={"device_address": ""})
    assert resp.status == 400
    body = resp.json()
    assert body["result"] == "error"
    assert body["message"] == "Device address is required"


def test_wled_push_profiles_unreachable_device_real_network_path(live_server, page):
    """No mocking: a closed local port makes the real WLEDProfileManager's
    get_device_info() HTTP GET fail fast (connection refused, not a
    timeout), landing on the route's `result["success"] is False` branch
    with the real production code -- the genuine no-hardware error path."""
    resp = page.request.post(
        f"{live_server}/api/wled_push_profiles",
        data={"device_address": _CLOSED_PORT_ADDRESS, "profile_numbers": {}},
    )
    assert resp.status == 500
    body = resp.json()
    assert body["result"] == "error"
    assert body["message"] == "Could not connect to WLED device"


def test_wled_push_profiles_success_envelope_mocked(live_server, page):
    """Success path (result["success"] is True) is unreachable without a
    real WLED device, so mock WLEDProfileManager itself to assert the
    route's success envelope shape."""
    fake_manager = MagicMock()
    fake_manager.push_all_profiles.return_value = {
        "success": True,
        "profiles_pushed": 3,
        "profiles": ["idle", "cooking", "error"],
        "message": "",
    }
    with patch("notify.wled_profiles.WLEDProfileManager", return_value=fake_manager) as mock_cls:
        resp = page.request.post(
            f"{live_server}/api/wled_push_profiles",
            data={"device_address": "192.168.1.77", "profile_numbers": {"idle": 1}},
        )

    assert resp.status == 200
    body = resp.json()
    assert body["result"] == "success"
    assert body["profiles_pushed"] == 3
    assert body["profiles"] == ["idle", "cooking", "error"]
    assert "3 profiles" in body["message"]
    mock_cls.assert_called_once()
    fake_manager.push_all_profiles.assert_called_once_with(custom_profile_numbers={"idle": 1})


def test_wled_push_profiles_exception_returns_error_envelope(live_server, page):
    with patch("notify.wled_profiles.WLEDProfileManager", side_effect=RuntimeError("boom")):
        resp = page.request.post(
            f"{live_server}/api/wled_push_profiles",
            data={"device_address": "192.168.1.77"},
        )
    assert resp.status == 500
    body = resp.json()
    assert body["result"] == "error"
    assert "boom" in body["message"]


# --- wled_test_profile (POST) --------------------------------------------


def test_wled_test_profile_missing_device_address(live_server, page):
    resp = page.request.post(f"{live_server}/api/wled_test_profile", data={"device_address": ""})
    assert resp.status == 400
    body = resp.json()
    assert body["result"] == "error"
    assert body["message"] == "Device address is required"


def test_wled_test_profile_unreachable_device_real_network_path(live_server, page):
    """No mocking: closed local port -> real `requests.post` raises
    ConnectionError (a requests.RequestException subclass) immediately,
    exercising the route's real `except requests.RequestException` branch."""
    resp = page.request.post(
        f"{live_server}/api/wled_test_profile",
        data={"device_address": f"http://{_CLOSED_PORT_ADDRESS}", "profile_number": 2},
    )
    assert resp.status == 500
    body = resp.json()
    assert body["result"] == "error"
    assert "Failed to communicate with WLED device" in body["message"]


def test_wled_test_profile_success_envelope_mocked(live_server, page):
    fake_response = MagicMock()
    fake_response.raise_for_status.return_value = None
    with patch("requests.post", return_value=fake_response) as mock_post:
        resp = page.request.post(
            f"{live_server}/api/wled_test_profile",
            data={"device_address": "192.168.1.77", "profile_number": 4},
        )

    assert resp.status == 200
    body = resp.json()
    assert body["result"] == "success"
    assert body["message"] == "Profile 4 activated successfully"
    mock_post.assert_called_once()
    called_url = mock_post.call_args.args[0]
    assert called_url == "http://192.168.1.77/json/state"
    assert mock_post.call_args.kwargs["json"] == {"on": True, "bri": 128, "ps": 4}
