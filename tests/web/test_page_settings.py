"""Exemplar Playwright coverage for the settings page
(blueprints/settings/routes.py's `settings_page`, a single route handling
19 POST `action` branches plus the base GET/POST render).

This module is the reference pattern for the rest of the Playwright
fan-out: see tests/web/conftest.py for the shared harness (live_server,
read-back helpers, precondition seeding) and its module docstring for the
fixture-scoping and thread-shared-datastore rationale.

Actions covered here (3 of ~19, chosen to demonstrate both interaction
styles -- see tests/web/conftest.py and the task report for guidance on
picking which style per action):

- (base GET, no action)  -- full-page render, key sections present.
- `display`   -- real-UI style: simple text field + <button type=submit>,
                 no JS gating the submit. Fill + click, assert persisted
                 value AND that the re-rendered page reflects it.
- `safety`    -- real-UI style: a checkbox + a numeric field in the same
                 plain HTML form, to prove the uncheck() interaction and
                 is_checked()-style server logic both come through.
- `pwm_duty_cycle` -- direct-POST style: this action's real UI is a JS
                 table (blueprints/settings/static/settings/js/settings.js)
                 that fetches GET /settings/pwm_duty_cycle on page load and
                 dynamically builds/reads table rows, POSTing a JSON body
                 (`dc_temps_list`/`dc_profiles`) built from live DOM state.
                 Driving that through real clicks would mean reverse-
                 engineering row-add/row-edit JS for no additional
                 assertion power; POSTing the same JSON body the JS would
                 have sent, then reading back through the same GET the JS
                 uses, proves the round trip pragmatically.

NOT covered here (left for later subagents): dashboard_config,
probe_select, probe_config, probe_config_save, notify, editprofile,
addprofile, controller_card, cycle, pwm, startup, history, pellets,
smartstart. (`display` also already has non-Playwright, Flask-test-client
coverage in tests/web/test_webapp_sqlite.py -- this module additionally
proves the same persistence through the real browser + real UI.)
"""

from tests.web.conftest import read_settings_from_server, requires_chromium

pytestmark = requires_chromium


def test_settings_page_renders_key_sections(live_server, page):
    resp = page.goto(f"{live_server}/settings/")

    assert resp.status == 200
    assert page.title().startswith("Settings")
    # The vertical pill nav and a sampling of its tabs (spanning several of
    # the ~19 actions' own tab-panes) are present.
    assert page.locator("#v-pills-tab").count() == 1
    # Note: v-pills-pwm-tab is NOT in this list -- it's gated behind
    # `settings['platform']['dc_fan']` in the template (not seeded True by
    # default), even though the pwm_duty_cycle backend action itself works
    # unconditionally. That's exactly why test_pwm_duty_cycle_via_direct_post
    # below drives it via direct POST rather than through the tab UI.
    for tab_id in (
        "v-pills-probes-tab",
        "v-pills-startup-tab",
        "v-pills-safety-tab",
        "v-pills-dash-tab",
        "v-pills-notifications-tab",
    ):
        assert page.locator(f"#{tab_id}").count() == 1, f"missing nav tab {tab_id!r}"
    # Seeded grill_name reaches the navbar via the base template.
    assert "E2E" in page.locator("#navbarGrillName").inner_text()


def test_display_sleep_timeout_via_real_ui(live_server, page):
    page.goto(f"{live_server}/settings/")
    page.click("#v-pills-dash-tab")
    page.wait_for_selector("#v-pills-dash.active")

    page.fill("#sleep_timeout", "137")
    with page.expect_navigation():
        page.locator('form[name="displaypower"] button[type="submit"]').click()

    # Persisted state: read through the datastore singleton live_server
    # shares with this process (see conftest.py's thread-shared-datastore
    # docs) -- not a second HTTP round trip.
    assert read_settings_from_server()["display"]["sleep_timeout"] == 137
    # Re-render: the POST handler falls through to the same
    # settings/index.html template, so the freshly-served page should
    # reflect the new value in the field it just saved.
    assert page.locator("#sleep_timeout").input_value() == "137"


def test_safety_checkbox_and_numeric_via_real_ui(live_server, page):
    page.goto(f"{live_server}/settings/")
    page.click("#v-pills-safety-tab")
    page.wait_for_selector("#v-pills-safety.active")

    # default_settings() ships startup_check=True; uncheck it and change a
    # numeric field in the same form, in one submit.
    assert page.locator("#startup_check").is_checked()
    # Bootstrap's custom-control-input pattern renders the real <input>
    # invisible (opacity: 0) with its <label> painted on top for styling,
    # so a plain click hit-tests the label instead of the input and (with
    # a fixed-top navbar also overlapping post-scroll) Playwright's
    # actionability check times out. force=True skips that check -- this
    # is a real DOM element receiving a real event, just not the top
    # paint layer at that point.
    page.locator("#startup_check").uncheck(force=True)
    page.fill("#manual_override_time", "45")
    with page.expect_navigation():
        page.locator('form[name="safetysettings"] button[type="submit"]').click()

    settings = read_settings_from_server()
    assert settings["safety"]["startup_check"] is False
    assert settings["safety"]["manual_override_time"] == 45
    assert page.locator("#startup_check").is_checked() is False
    assert page.locator("#manual_override_time").input_value() == "45"


def test_pwm_duty_cycle_via_direct_post(live_server, page):
    """`pwm_duty_cycle` is a JSON GET/POST pair driven by JS-built table
    rows (see module docstring). Post the same JSON shape the page's own
    JS would send, directly through the browser context, then read it back
    both via the datastore and via the same GET the page's JS uses."""
    new_temps = [5, 10, 20]
    new_profiles = [{"duty_cycle": dc} for dc in (30, 50, 70, 90)]

    post_resp = page.request.post(
        f"{live_server}/settings/pwm_duty_cycle",
        data={"dc_temps_list": new_temps, "dc_profiles": new_profiles},
    )
    assert post_resp.status == 200
    assert post_resp.json()["result"] == "success"

    settings = read_settings_from_server()
    assert settings["pwm"]["temp_range_list"] == new_temps
    assert [p["duty_cycle"] for p in settings["pwm"]["profiles"]] == [30, 50, 70, 90]

    # Re-render (JSON style): the GET counterpart the page's own JS fetches
    # on load reflects the same persisted values.
    get_resp = page.request.get(f"{live_server}/settings/pwm_duty_cycle")
    assert get_resp.status == 200
    body = get_resp.json()
    assert body["dc_temps_list"] == new_temps
    assert [p["duty_cycle"] for p in body["dc_profiles"]] == [30, 50, 70, 90]
