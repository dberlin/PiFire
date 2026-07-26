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

from tests.web.conftest import (
    apply_control,
    drain_control_writes,
    read_control_from_server,
    read_settings_from_server,
    requires_chromium,
)

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


# =====================================================================
# The notification modal's low-limit expiry action.
#
# "Shutdown PiFire" and "Attempt Re-ignite" are mutually exclusive
# checkboxes (_macro_dash_default.html:293-306, each unchecks the other),
# and only the Primary probe renders them. setNotify() read the SHUTDOWN
# box for BOTH flags until 2026-07-26, which made re-ignite unreachable
# from this page in both directions -- ticking it sent reignite:false, and
# ticking shutdown armed reignite as a side effect. The backend runs
# `if shutdown ... elif keep_warm ... elif reignite`
# (notify/notifications.py:141-167), so shutdown won either way.
#
# These drive the real page, so they also cover the per-entry
# notify_updates write setNotify now posts.
# =====================================================================

LOW_LIMIT_ENTRY = ("Grill", "probe_limit_low")


def _seed_probe_cards():
    """Render actual probe cards. The module's autouse fixture seeds
    `probe_device_info` to [] to keep dash_page from 500ing, but
    read_probe_status() then matches no probe against a device, so
    probe_status['P']/['F'] come back empty and dash_default.html emits no
    probe cards at all -- and so no notify bell and no notify modal. Every
    default probe is on the `proto_adc` device, so one entry is enough."""
    from common.datastore_accessors import write_generic_key

    write_generic_key("probe_device_info", [{"device": "proto_adc", "status": {}, "config": {}}])


def _reset_notify_data():
    """Restore control["notify_data"] to defaults.

    live_server is module-scoped, so without this each test inherits whatever
    the previous one armed -- and initTarget() then renders the low-limit
    switch ALREADY ON, so a blind toggle turns it off and the test fails on a
    state it never set."""
    from common.datastore_accessors import default_control

    apply_control(lambda c: c.__setitem__("notify_data", default_control()["notify_data"]))


def _tick(page, selector):
    """Ensure a Bootstrap custom-control checkbox is ON, via its own click().

    Neither Playwright `check()` nor `click()` can reach these: the <input>
    sits at z-index:-1 behind an EMPTY <label> that draws the switch entirely
    from CSS pseudo-elements, so the input is obscured and the label has no
    box. `HTMLElement.click()` runs the checkbox's activation behaviour AND
    the template's mutual-exclusion handlers
    (_macro_dash_default.html:293-306), which is exactly the path under test.

    Idempotent on purpose -- a toggle would turn OFF a switch the page had
    already rendered on. Asserted rather than assumed, so a silent no-op fails
    here instead of surfacing as "the flag never armed" three steps later.
    """
    if page.eval_on_selector(selector, "el => el.checked") is not True:
        page.eval_on_selector(selector, "el => el.click()")
    assert page.eval_on_selector(selector, "el => el.checked") is True, f"{selector} did not tick"


def _arm_low_limit(page, live_server, *, reignite):
    """Open the Primary probe's notify modal, arm its low-limit alert with
    one of the two expiry actions, and submit. Returns the resulting
    notify_data entry after the queued write is drained."""
    _seed_probe_cards()
    _reset_notify_data()
    page.goto(f"{live_server}/dash/")
    # Wait for the page's own /api/current poll to populate notify_data --
    # setNotify searches it to decide an entry exists before addressing it.
    page.wait_for_function("notify_data.length > 0")
    # Stand in for the probe gauge. setNotify reads
    # probeGauges[label].getValue() to pre-compute each limit entry's
    # `triggered` flag, and dash_default.js:66 only builds a gauge once the
    # socket broadcast delivers a temperature -- which needs the control
    # process this harness deliberately does not run. Without a stub,
    # setNotify throws on the first limit branch it enters and NO request is
    # ever sent, which surfaces as an unexplained request-wait timeout.
    # 0 degrees is a real reading and makes `triggered` deterministic against
    # the 100-degree limit set below: false for a high limit, true for a low.
    page.evaluate("probeGauges['Grill'] = { getValue: () => 0 };")

    page.click("#Grill_notify_btn")
    page.wait_for_selector("#Grill_notifyModal.show")
    # Expand the accordion panel BEFORE ticking the switch, not after. The
    # switch's empty <label> renders through absolutely-positioned CSS
    # pseudo-elements that reach outside its own zero-width box and under the
    # expander button next to it, so a click on the button lands on the label
    # and toggles the switch back off. Ticking last is also the order a user
    # would work in: open the panel, set the temperature, arm it.
    page.click("button[data-target='#Grill_collapseThree']")
    page.wait_for_selector("#Grill_collapseThree.show")
    page.fill("#Grill_low_limit_tempOutputId", "100")
    _tick(page, "#Grill_limit_low_temp")
    action = "reignite" if reignite else "shutdown"
    _tick(page, f"#Grill_low_limit_{action}")

    # Wait on the RESPONSE, not on the modal closing. The Set button carries
    # data-dismiss="modal", so the modal is gone the instant it is clicked --
    # well before setNotify's AJAX POST has reached the server. Draining on
    # that signal read back an untouched entry and made every assertion below
    # vacuously "the flag is not set".
    with page.expect_response(lambda r: r.url.endswith("/api/control") and r.request.method == "POST") as response:
        page.click("#Grill_notify_enable")
    assert response.value.status == 201, response.value.text()
    drain_control_writes()
    label, type_ = LOW_LIMIT_ENTRY
    return next(e for e in read_control_from_server()["notify_data"] if e["label"] == label and e["type"] == type_)


def test_low_limit_reignite_checkbox_arms_reignite_not_shutdown(live_server, page):
    entry = _arm_low_limit(page, live_server, reignite=True)
    assert entry["reignite"] is True, "ticking 'Attempt Re-ignite' did not arm reignite"
    assert entry["shutdown"] is False, "ticking 'Attempt Re-ignite' armed shutdown as well"
    assert entry["req"] is True
    assert entry["target"] == 100


def test_low_limit_shutdown_checkbox_does_not_also_arm_reignite(live_server, page):
    entry = _arm_low_limit(page, live_server, reignite=False)
    assert entry["shutdown"] is True
    assert entry["reignite"] is False, "ticking 'Shutdown PiFire' armed reignite as a side effect"


def test_setnotify_leaves_the_other_entries_for_that_label_alone(live_server, page):
    """The per-entry write, end to end through the real page: arming the low
    limit must not touch the target or high-limit entries sharing the label,
    nor any other probe's."""
    before = {(e["label"], e["type"]): dict(e) for e in read_control_from_server()["notify_data"]}
    _arm_low_limit(page, live_server, reignite=True)
    after = {(e["label"], e["type"]): dict(e) for e in read_control_from_server()["notify_data"]}

    assert set(before) == set(after), "an entry was added or deleted"
    for key, entry in after.items():
        if key == LOW_LIMIT_ENTRY:
            continue
        assert entry == before[key], f"{key} changed"
