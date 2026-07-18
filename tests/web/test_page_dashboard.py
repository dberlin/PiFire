"""Playwright coverage for the dashboard page
(blueprints/dash/routes.py's `dash_page` and `dash_config`).

Follows the pattern established in test_page_settings.py; see
tests/web/conftest.py for the shared harness.

Covered:
- Base GET render (`dash_page`) -- key dashboard sections/ids present.
  Note: `dash_page` calls `process_command(action="sys", ...)` then polls
  `get_system_command_output()` for up to its 1s timeout waiting for a
  control-process response that will never come (no control loop is
  running in this harness) -- so each GET to /dash/ takes about a second
  and renders an "unresponsive" warning banner. That's expected/inherent
  to this route, not a bug in the test.
- `dash_config` GET (fetched by the page's own `dashLoadConfig()` JS via
  jQuery .load()) and POST (`dashConfig_*` fields), driven through the
  real UI: click the gear icon to open the settings modal (which triggers
  the AJAX load), fill in the config form injected into the DOM, and
  submit it -- then assert the persisted per-dashboard config on the
  settings store.

NOT covered: switching `settings['dashboard']['current']` to a different
dashboard implementation (only "Default" ships by default) and the
probe-visibility-toggle / probe-config modals on this same page, which
are exercised indirectly elsewhere (settings-page probe_config tests) and
don't go through `dash_config`.
"""

import pytest

from tests.web.conftest import read_settings_from_server, requires_chromium

pytestmark = requires_chromium


@pytest.fixture(autouse=True)
def seed_probe_device_info():
    """`dash_page` calls `read_probe_status()`, which unconditionally reads
    the "probe_device_info" generic key and iterates it. That key is only
    ever written by the real control-process probe-reader loop (see
    common/datastore_accessors.py's read_probe_status / write_generic_key),
    which never runs in this harness -- so without seeding it, the key is
    still `None` and `json.loads(None)` blows up with a 500. Seed it to an
    empty list (no matching devices -> probes render with a status of
    empty state, which the template handles fine)."""
    from common.datastore_accessors import write_generic_key

    write_generic_key("probe_device_info", [])


def test_dash_page_renders_key_sections(live_server, page):
    resp = page.goto(f"{live_server}/dash/")

    assert resp.status == 200
    assert page.title().startswith("Dashboard")
    assert "E2E" in page.locator("#navbarGrillName").inner_text()
    # Default dashboard's status/time-elapsed cards and gear icon (opens the
    # dash_config modal) are always present regardless of probe config.
    assert page.locator("#card_status").count() == 1
    assert page.locator("#card_time_elapsed").count() == 1
    assert page.locator("span.gear-icon").count() == 1
    assert page.locator("#dashSettingsModal").count() == 1
    # History nav button.
    assert page.locator("#card_history_button a[href='/history']").count() == 1


def test_dash_config_via_real_ui(live_server, page):
    page.goto(f"{live_server}/dash/")

    # Clicking the gear icon runs dashSettings(), which AJAX-loads
    # /dash/config into #dash_config_card and shows the modal.
    page.locator("span.gear-icon").click()
    page.wait_for_selector("#dash_config_card form")
    page.wait_for_selector("#dashConfig_max_primary_temp_F")

    page.fill("#dashConfig_max_primary_temp_F", "550")
    page.select_option("#dashConfig_touch_screen_mode", "On")

    with page.expect_navigation():
        page.locator("#dash_config_card form button[type='submit']").click()

    settings = read_settings_from_server()
    dash_config = settings["dashboard"]["dashboards"]["Default"]["config"]
    assert dash_config["max_primary_temp_F"] == "550"
    assert dash_config["touch_screen_mode"] == "On"

    # dash_config redirects to /dash -- re-fetch the config card and check
    # the freshly-rendered form reflects the persisted values.
    get_resp = page.request.get(f"{live_server}/dash/config")
    assert get_resp.status == 200
    body = get_resp.text()
    assert 'value="550"' in body
    assert "selected" in body
