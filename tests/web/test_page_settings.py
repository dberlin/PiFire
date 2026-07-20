"""Playwright coverage for the settings page (blueprints/settings/routes.py's
`settings_page`, a single route handling 19 POST `action` branches plus the
base GET/POST render).

This module is the reference pattern for the rest of the Playwright
fan-out: see tests/web/conftest.py for the shared harness (live_server,
read-back helpers, precondition seeding) and its module docstring for the
fixture-scoping and thread-shared-datastore rationale.

All 19 action branches (plus the base render) are covered here, split by
interaction style:

- (base GET, no action)  -- full-page render, key sections present.
- Plain-HTML-form actions driven via the **real UI** (fill/check + click
  the form's own submit button + `page.expect_navigation()`): `display`,
  `safety`, `cycle`, `pwm`, `startup`, `history`, `pellets`, `notify`,
  `addprofile`, `editprofile`.
- JS-fragment/JSON actions driven via **direct POST** (`page.request.post`)
  because reconstructing the JS that drives them for real adds no
  assertion value: `dashboard_config`, `probe_select`, `probe_config`,
  `probe_config_save`, `controller_card`, `smartstart` (GET+POST),
  `pwm_duty_cycle` (GET+POST -- GET is exercised as part of the POST
  round-trip test below).
- `test_cycle_blank_pmode_is_skipped_not_crashed` -- a dedicated
  characterization test for the `is_not_blank` guard (common/app.py) that
  every numeric field in cycle/pwm/startup/history/safety/pellets shares:
  emptying a guarded field is silently skipped (prior value retained)
  rather than crashing. One test stands in for all six actions since they
  all route through the same guard function.

(`display` and `safety` also already have non-Playwright, Flask-test-client
coverage in tests/web/test_webapp_sqlite.py -- this module additionally
proves the same persistence through the real browser + real UI.)
"""

import re

from playwright.sync_api import FormData

from common.modes import Mode
from tests.web.conftest import (
    apply_control,
    apply_settings,
    drain_control_writes,
    read_control_from_server,
    read_settings_from_server,
    requires_chromium,
)

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


def test_dashboard_config_via_direct_post(live_server, page):
    """`dashboard_config` renders an HTML fragment (render_dash_settings)
    for the dashboard <select>'s onchange handler
    (settings.js: `$("#dashboardSettings").load("/settings/dashboard_config", ...)`)
    -- a jQuery .load(), i.e. a plain form-encoded POST, not JSON. This
    branch never calls write_settings -- there is nothing to persist,
    only the returned fragment to assert on.

    Note: passing `selected=""` (empty/omitted), or a value that doesn't
    match any known dashboard, used to read
    `settings["dashboard"]["selected"]` -- a key default_settings() never
    populates (it populates `["current"]` instead) -- causing a latent
    KeyError/500 on the empty path, and an IndexError if `dashboards` were
    ever empty on the invalid-value path. The route now falls back to the
    persisted `settings["dashboard"]["current"]` (default: "Default"), or
    the first available dashboard if even that isn't valid, instead of
    raising.
    """
    resp_basic = page.request.post(f"{live_server}/settings/dashboard_config", form={"selected": "Basic"})
    assert resp_basic.status == 200
    assert "basic/img/screenshot.png" in resp_basic.text()

    resp_default = page.request.post(f"{live_server}/settings/dashboard_config", form={"selected": "Default"})
    assert resp_default.status == 200
    assert "default/img/screenshot.png" in resp_default.text()

    # Empty `selected` (the <select>'s initial/omitted state) must not 500;
    # it falls back to the persisted `current` dashboard ("Default").
    resp_empty = page.request.post(f"{live_server}/settings/dashboard_config", form={"selected": ""})
    assert resp_empty.status == 200
    assert "default/img/screenshot.png" in resp_empty.text()

    # An unrecognized `selected` value must not 500 either; same fallback.
    resp_invalid = page.request.post(f"{live_server}/settings/dashboard_config", form={"selected": "Nonexistent"})
    assert resp_invalid.status == 200
    assert "default/img/screenshot.png" in resp_invalid.text()


def test_probe_select_via_direct_post(live_server, page):
    """`probe_select` renders the probe <select> + nested probe_config
    fragment (render_probe_select) for the probe dropdown's onchange
    handler (`$("#settings_probe_config").load("/settings/probe_config", ...)`
    is the sibling action; this one backs the <select> itself). Also a
    plain form-encoded POST, also read-only (no write_settings call)."""
    resp = page.request.post(f"{live_server}/settings/probe_select", form={"selected": "Probe2"})
    assert resp.status == 200
    body = resp.text()

    # The matched probe's <option> carries `selected`; the others don't.
    assert re.search(r'value="Probe2"[^<]*selected', body)
    assert not re.search(r'value="Probe1"[^<]*selected', body)
    # The nested probe_config fragment for the matched probe is rendered too.
    assert 'value="Probe-2"' in body


def test_probe_config_via_direct_post(live_server, page):
    """`probe_config` renders just the render_probe_config fragment (no
    wrapping <select>) for a given probe label -- the AJAX target behind
    the probe <select>'s onchange handler. Read-only, like probe_select."""
    resp = page.request.post(f"{live_server}/settings/probe_config", form={"selected": "Probe3"})
    assert resp.status == 200
    body = resp.text()
    assert 'value="Probe3"' in body  # hidden label field
    assert 'value="Probe-3"' in body  # display-name field value


def test_probe_config_save_via_direct_post(live_server, page):
    """`probe_config_save` is the JSON POST the "Save" button in the probe
    config fragment sends (settings.js gathers `.probe_config`-classed
    fields into a plain object and POSTs it as JSON) -- direct-POST style,
    matching the pwm_duty_cycle exemplar."""
    post_resp = page.request.post(
        f"{live_server}/settings/probe_config_save",
        data={
            "label": "Probe1",
            "name": "Probe One Custom",
            "type": "Food",
            "port": "ADC1",
            "device": "ADC",
            "enabled": "true",
            "profile_id": "ET73-HM",
        },
    )
    assert post_resp.status == 200
    assert post_resp.json()["result"] == "success"

    settings = read_settings_from_server()
    probe1 = next(p for p in settings["probe_settings"]["probe_map"]["probe_info"] if p["label"] == "Probe1")
    assert probe1["name"] == "Probe One Custom"
    assert probe1["profile"]["id"] == "ET73-HM"
    # The probe's display name is mirrored into history_page's probe_config too.
    assert settings["history_page"]["probe_config"]["Probe1"]["name"] == "Probe One Custom"
    drain_control_writes()
    assert read_control_from_server()["probe_profile_update"] is True

    # Unknown label: no matching probe, no write, distinct JSON result.
    unknown_resp = page.request.post(
        f"{live_server}/settings/probe_config_save",
        data={"label": "NoSuchProbeLabel"},
    )
    assert unknown_resp.status == 200
    assert unknown_resp.json()["result"] == "label_not_found"


def test_controller_card_via_direct_post(live_server, page):
    """`controller_card` renders render_controller_config for a given
    controller (the Work Mode tab's controller <select>'s onchange
    handler: `$('#controller_config').load("/settings/controller_card", ...)`).
    Read-only, direct-POST style."""
    resp = page.request.post(f"{live_server}/settings/controller_card", form={"selected": "fuzzy"})
    assert resp.status == 200
    body = resp.text()
    assert "fuzzy logic controller" in body.lower()
    assert "fuzzy.png" in body


def test_notify_via_real_ui(live_server, page):
    """`notify` is a large plain HTML form covering many notification
    services; exercise a representative subset (IFTTT + MQTT) spanning
    checkbox-enable and plain-text-field persistence."""
    page.goto(f"{live_server}/settings/")
    page.click("#v-pills-notifications-tab")
    page.wait_for_selector("#v-pills-notifications.active")

    page.locator("#ifttt_enabled").check(force=True)
    page.fill("#iftttapi", "test-ifttt-key")
    page.locator("#mqtt_enabled").check(force=True)
    page.fill("#mqtt_broker", "mqtt.example.com")
    page.fill("#mqtt_port", "8883")

    with page.expect_navigation():
        page.locator('form[name="notify"] button[type="submit"]').click()

    settings = read_settings_from_server()
    assert settings["notify_services"]["ifttt"]["enabled"] is True
    assert settings["notify_services"]["ifttt"]["APIKey"] == "test-ifttt-key"
    assert settings["notify_services"]["mqtt"]["enabled"] is True
    assert settings["notify_services"]["mqtt"]["broker"] == "mqtt.example.com"
    assert settings["notify_services"]["mqtt"]["port"] == "8883"
    drain_control_writes()
    assert read_control_from_server()["settings_update"] is True


def test_addprofile_via_real_ui(live_server, page):
    """`addprofile` -- the "New Probe Profile" collapse card on the Probe
    Profiles tab. Expand the collapse, fill Name/A/B/C, submit."""
    page.goto(f"{live_server}/settings/")
    page.click("#v-pills-probe-profiles-tab")
    page.wait_for_selector("#v-pills-probe-profiles.active")

    page.click('a[href="#addprofile"]')
    page.wait_for_selector("#addprofile.show")
    page.fill("#Name", "Test Probe Profile")
    page.fill("#A", "0.001")
    page.fill("#B", "0.0002")
    page.fill("#C", "0.0000003")

    with page.expect_navigation():
        page.locator('form[name="addprofile"] button[type="submit"]').click()

    settings = read_settings_from_server()
    matches = [p for p in settings["probe_settings"]["probe_profiles"].values() if p["name"] == "Test Probe Profile"]
    assert len(matches) == 1
    assert matches[0]["A"] == 0.001
    assert matches[0]["B"] == 0.0002
    assert matches[0]["C"] == 0.0000003


def test_editprofile_via_real_ui(live_server, page):
    """`editprofile` (edit branch) -- edit the existing "TWPS00" profile,
    which is in use by Probe2/Probe3 by default, proving both the profile
    dict itself AND the probe_map entries referencing it get updated, plus
    the control `probe_profile_update` flag. (The same action's `delete`
    branch is not separately exercised here; it shares the same simple
    is-this-profile-in-use guard already visible in the route source.)"""
    page.goto(f"{live_server}/settings/")
    page.click("#v-pills-probe-profiles-tab")
    page.wait_for_selector("#v-pills-probe-profiles.active")

    page.click('a[href="#editTWPS00"]')
    page.wait_for_selector("#editTWPS00.show")
    page.fill("#Name_TWPS00", "TWPS00 Renamed")
    page.fill("#A_TWPS00", "0.0011")
    page.fill("#B_TWPS00", "0.00021")
    page.fill("#C_TWPS00", "0.00000031")

    with page.expect_navigation():
        page.locator('button[name="editprofile"][value="TWPS00"]').click()

    settings = read_settings_from_server()
    profile = settings["probe_settings"]["probe_profiles"]["TWPS00"]
    assert profile["name"] == "TWPS00 Renamed"
    assert profile["A"] == 0.0011
    assert profile["B"] == 0.00021
    assert profile["C"] == 0.00000031

    # Probe2 uses TWPS00 by default and is untouched by other tests in this
    # module (unlike Probe1, which test_probe_config_save_via_direct_post
    # switches to a different profile) -- safe to assert regardless of
    # test execution order.
    probe2 = next(p for p in settings["probe_settings"]["probe_map"]["probe_info"] if p["label"] == "Probe2")
    assert probe2["profile"]["name"] == "TWPS00 Renamed"
    drain_control_writes()
    assert read_control_from_server()["probe_profile_update"] is True


def test_cycle_via_real_ui(live_server, page):
    """`cycle` -- the Work Mode tab's big plain-HTML form covering cycle
    timing, lid-open detection, fan PID, smoke plus, keep warm, and the
    per-controller cycle settings (holdcycletime/u_min/u_max) all in one
    submit. dc_fan is explicitly pinned False so the (dc_fan-gated)
    sp_duty_cycle field doesn't need to be accounted for."""
    apply_settings(lambda s: s["platform"].__setitem__("dc_fan", False))

    page.goto(f"{live_server}/settings/")
    page.click("#v-pills-work-mode-tab")
    page.wait_for_selector("#v-pills-work-mode.active")

    page.fill("#SmokeOnCycleTime", "20")
    page.fill("#SmokeOffCycleTime", "55")
    page.fill("#pmode", "5")
    page.locator("#lid_open_detect_enable").check(force=True)
    page.fill("#lid_open_threshold", "15")
    page.fill("#lid_open_pausetime", "120")
    page.locator("#fan_pid_enable").check(force=True)
    page.locator("#default_smoke_plus").check(force=True)
    page.fill("#sp_on_time", "8")
    page.fill("#sp_off_time", "12")
    page.fill("#sp_min_temp", "165")
    page.fill("#sp_max_temp", "225")
    page.fill("#keep_warm_temp", "170")
    page.locator("#keep_warm_s_plus").check(force=True)
    page.fill("#holdcycletime", "30")
    page.fill("#u_min", "0.15")
    page.fill("#u_max", "0.85")

    with page.expect_navigation():
        page.locator('form[name="cycle_settings"] button[type="submit"]').click()

    settings = read_settings_from_server()
    cd = settings["cycle_data"]
    assert cd["SmokeOnCycleTime"] == 20
    assert cd["SmokeOffCycleTime"] == 55
    assert cd["PMode"] == 5
    assert cd["LidOpenDetectEnabled"] is True
    assert cd["LidOpenThreshold"] == 15
    assert cd["LidOpenPauseTime"] == 120
    assert cd["FanPidEnabled"] is True
    assert cd["HoldCycleTime"] == 30
    assert cd["u_min"] == 0.15
    assert cd["u_max"] == 0.85
    assert settings["smoke_plus"]["enabled"] is True
    assert settings["smoke_plus"]["on_time"] == 8
    assert settings["smoke_plus"]["off_time"] == 12
    assert settings["smoke_plus"]["min_temp"] == 165
    assert settings["smoke_plus"]["max_temp"] == 225
    assert settings["keep_warm"]["temp"] == 170
    assert settings["keep_warm"]["s_plus"] is True
    drain_control_writes()
    assert read_control_from_server()["settings_update"] is True


def test_cycle_blank_pmode_is_skipped_not_crashed(live_server, page):
    """Characterizes the `is_not_blank` guard (common/app.py) shared by
    every numeric field across cycle/pwm/startup/history/safety/pellets:
    emptying a guarded field is silently skipped (prior value retained),
    not a crash. One test stands in for all six actions since they share
    the exact same guard function -- this was a real bug Phase A fixed
    (see project_phaseA_common_split memory)."""
    apply_settings(lambda s: s["platform"].__setitem__("dc_fan", False))
    apply_settings(lambda s: s["cycle_data"].__setitem__("PMode", 7))

    page.goto(f"{live_server}/settings/")
    page.click("#v-pills-work-mode-tab")
    page.wait_for_selector("#v-pills-work-mode.active")

    assert page.locator("#pmode").input_value() == "7"
    page.fill("#pmode", "")

    with page.expect_navigation():
        page.locator('form[name="cycle_settings"] button[type="submit"]').click()

    assert read_settings_from_server()["cycle_data"]["PMode"] == 7
    assert page.locator("#pmode").input_value() == "7"


def test_pwm_via_real_ui(live_server, page):
    """`pwm` -- the PWM Settings tab, gated behind `platform.dc_fan` (see
    the pwm_duty_cycle exemplar's note on this same gating); pin dc_fan
    True as a precondition so the tab/form exist to drive."""
    apply_settings(lambda s: s["platform"].__setitem__("dc_fan", True))

    page.goto(f"{live_server}/settings/")
    page.click("#v-pills-pwm-tab")
    page.wait_for_selector("#v-pills-pwm.active")

    page.locator("#pwm_control").check(force=True)
    page.fill("#pwm_update", "12")
    page.fill("#min_duty_cycle", "25")
    page.fill("#max_duty_cycle", "85")
    page.fill("#frequency", "150")

    with page.expect_navigation():
        page.locator('form[name="pwm"] button[type="submit"]').click()

    settings = read_settings_from_server()
    assert settings["pwm"]["pwm_control"] is True
    assert settings["pwm"]["update_time"] == 12
    assert settings["pwm"]["min_duty_cycle"] == 25
    assert settings["pwm"]["max_duty_cycle"] == 85
    assert settings["pwm"]["frequency"] == 150
    drain_control_writes()
    assert read_control_from_server()["settings_update"] is True


def test_startup_via_real_ui(live_server, page):
    """`startup` -- Startup & Shutdown tab. Several fields are shown/hidden
    by client-side JS toggles (after_startup_mode=='Hold', the exit-temp
    and prime-on-startup switches); drive those toggles for real so the
    now-visible fields can be filled, rather than force-filling hidden
    inputs. dc_fan pinned False so the dc_fan-gated startup pwm_duty_cycle
    field doesn't need to be accounted for."""
    apply_settings(lambda s: s["platform"].__setitem__("dc_fan", False))

    page.goto(f"{live_server}/settings/")
    page.click("#v-pills-startup-tab")
    page.wait_for_selector("#v-pills-startup.active")

    page.fill("#startup_duration", "200")
    page.select_option("#after_startup_mode", "Hold")
    page.wait_for_selector("#startup_hold_value_input", state="visible")
    # These reveals are jQuery slideDown()s (settings.js): "visible" fires as
    # soon as the animation starts, while the container's height/position is
    # still in motion -- a force=True click computed against that moving
    # target can land off the checkbox. A short settle avoids that flake.
    page.wait_for_timeout(300)
    page.fill("#startup_mode_setpoint", "225")
    page.locator("#startup_start_to_hold_prompt").check(force=True)

    page.locator("#startup_exit_temp_toggle").check(force=True)
    page.wait_for_selector("#startup_exit_temp_input", state="visible")
    page.wait_for_timeout(300)
    page.fill("#startup_exit_temp", "180")

    page.locator("#prime_on_startup_toggle").check(force=True)
    page.wait_for_selector("#prime_on_startup_input", state="visible")
    page.wait_for_timeout(300)
    page.fill("#prime_on_startup", "50")

    page.locator("#smartstart_enable").check(force=True)
    page.fill("#smartstart_exit_temp", "140")

    page.fill("#shutdown_duration", "90")
    page.locator("#auto_power_off").check(force=True)

    with page.expect_navigation():
        page.locator('form[name="startup_shutdown_settings"] button[type="submit"]').click()

    settings = read_settings_from_server()
    assert settings["startup"]["duration"] == 200
    assert settings["startup"]["start_to_mode"]["after_startup_mode"] == "Hold"
    assert settings["startup"]["start_to_mode"]["primary_setpoint"] == 225
    assert settings["startup"]["start_to_mode"]["start_to_hold_prompt"] is True
    assert settings["startup"]["startup_exit_temp"] == 180
    assert settings["startup"]["prime_on_startup"] == 50
    assert settings["startup"]["smartstart"]["enabled"] is True
    assert settings["startup"]["smartstart"]["exit_temp"] == 140
    assert settings["shutdown"]["shutdown_duration"] == 90
    assert settings["shutdown"]["auto_power_off"] is True
    drain_control_writes()
    assert read_control_from_server()["settings_update"] is True


def test_history_via_real_ui(live_server, page):
    """`history` -- History tab: numeric fields, two checkboxes, and the
    per-probe graph-color loop (`clr_temp_<label>` etc). Only the Grill
    probe's colors are filled/asserted; the rest are left as the browser
    provides them (see the color-picker note in the task report)."""
    page.goto(f"{live_server}/settings/")
    page.click("#v-pills-history-tab")
    page.wait_for_selector("#v-pills-history.active")

    page.fill("#historymins", "180")
    page.fill("#datapoints", "500")
    page.locator("#historyautorefresh").uncheck(force=True)
    page.locator("#ext_data").check(force=True)

    page.click('button[data-target="#collapseColors"]')
    page.wait_for_selector("#collapseColors.show")
    page.fill("#clr_temp_Grill", "rgb(10, 20, 30, 1)")
    page.fill("#clrbg_temp_Grill", "rgb(40, 50, 60, 1)")

    with page.expect_navigation():
        page.locator('form[name="miscsettings"] button[type="submit"]').click()

    settings = read_settings_from_server()
    assert settings["history_page"]["minutes"] == 180
    assert settings["history_page"]["datapoints"] == 500
    assert settings["history_page"]["autorefresh"] == "off"
    assert settings["globals"]["ext_data"] is True
    # The bootstrap-colorpicker widget bound to these inputs normalizes the
    # value on its own change handler, dropping a fully-opaque alpha
    # channel (filled as "rgb(10, 20, 30, 1)", persisted as "rgb(10, 20, 30)").
    assert settings["history_page"]["probe_config"]["Grill"]["line_color"] == "rgb(10, 20, 30)"
    assert settings["history_page"]["probe_config"]["Grill"]["bg_color"] == "rgb(40, 50, 60)"


def test_pellets_via_real_ui(live_server, page):
    """`pellets` -- Pellets tab: warning checkbox + numerics, hopper
    full/empty distances (which also flip control's `distance_update`),
    auger rate, and the prime-ignition checkbox."""
    page.goto(f"{live_server}/settings/")
    page.click("#v-pills-pellets-tab")
    page.wait_for_selector("#v-pills-pellets.active")

    page.locator("#pellet_warning").check(force=True)
    page.fill("#warning_time", "30")
    page.fill("#warning_level", "35")
    page.fill("#full", "5")
    page.fill("#empty", "95")
    page.fill("#auger_rate", "0.42")
    page.locator("#prime_ignition").check(force=True)

    with page.expect_navigation():
        page.locator('form[name="pelletsettings"] button[type="submit"]').click()

    settings = read_settings_from_server()
    assert settings["pelletlevel"]["warning_enabled"] is True
    assert settings["pelletlevel"]["warning_time"] == 30
    assert settings["pelletlevel"]["warning_level"] == 35
    assert settings["pelletlevel"]["full"] == 5
    assert settings["pelletlevel"]["empty"] == 95
    assert settings["globals"]["augerrate"] == 0.42
    assert settings["globals"]["prime_ignition"] is True

    drain_control_writes()
    control = read_control_from_server()
    assert control["distance_update"] is True
    assert control["settings_update"] is True


def test_smartstart_via_direct_post(live_server, page):
    """`smartstart` -- GET returns the current temp_range_list/profiles as
    JSON; POST accepts the same shape as JSON and persists it. Same
    direct-POST rationale as pwm_duty_cycle: the real UI is a JS-built
    table (smartStartTable) with no additional assertion value to gain
    from reconstructing its row-add modal JS."""

    def _seed(s):
        s["startup"]["smartstart"]["temp_range_list"] = [60, 80, 90]
        s["startup"]["smartstart"]["profiles"] = [
            {"startuptime": 360, "augerontime": 15, "p_mode": 0},
            {"startuptime": 360, "augerontime": 15, "p_mode": 1},
            {"startuptime": 240, "augerontime": 15, "p_mode": 3},
            {"startuptime": 240, "augerontime": 15, "p_mode": 5},
        ]

    apply_settings(_seed)

    get_resp = page.request.get(f"{live_server}/settings/smartstart")
    assert get_resp.status == 200
    body = get_resp.json()
    assert body["temps_list"] == [60, 80, 90]
    assert len(body["profiles"]) == 4

    new_temps = [65, 85, 95]
    new_profiles = [
        {"startuptime": 300, "augerontime": 12, "p_mode": 0},
        {"startuptime": 300, "augerontime": 12, "p_mode": 1},
        {"startuptime": 200, "augerontime": 12, "p_mode": 3},
        {"startuptime": 200, "augerontime": 12, "p_mode": 5},
    ]
    post_resp = page.request.post(
        f"{live_server}/settings/smartstart",
        data={"temps_list": new_temps, "profiles": new_profiles},
    )
    assert post_resp.status == 200
    assert post_resp.json()["result"] == "success"

    settings = read_settings_from_server()
    assert settings["startup"]["smartstart"]["temp_range_list"] == new_temps
    assert settings["startup"]["smartstart"]["profiles"] == new_profiles

    get_resp2 = page.request.get(f"{live_server}/settings/smartstart")
    assert get_resp2.status == 200
    body2 = get_resp2.json()
    assert body2["temps_list"] == new_temps
    assert body2["profiles"] == new_profiles


# ---------------------------------------------------------------------------
# Round 2: branch coverage for the remaining (method, action) branches the
# tests above don't reach. The tests above were written against the "happy
# path" real UI / representative direct-POST per action; branch coverage
# (`--cov-report=term-missing` with `--cov-branch`) showed 122 still-missing
# branches concentrated in: (a) the empty/unmatched-selection fallback path
# of probe_select/probe_config/dashboard_config, (b) every notification
# service in `notify` OTHER than the ifttt/mqtt pair driven above (each
# service's enabled-checkbox True arm, and every optional field's
# "in response" False arm -- real browser forms always submit their text
# inputs, even empty, so those False arms are only reachable by a
# deliberately-thin direct POST), (c) editprofile's entire `delete` branch,
# (d) addprofile's `apply_profile` and error arms, (e) the "blank input
# skipped" / "checkbox unchecked" False arms across
# cycle/pwm/startup/history/safety/pellets that the real-UI tests above
# always fill in the True direction, and (f) the cycle action's per-option
# controller-config-type loop (float/int/bool/string).

# ---- dashboard_config / probe_select / probe_config: empty & unmatched ---


def test_dashboard_config_current_also_invalid_falls_back_to_first(live_server, page):
    """routes.py:28 -- when `selected` is empty AND the persisted
    `dashboard.current` is *also* not a known dashboard (e.g. a stale/
    corrupted value), the route falls back a second time to
    `next(iter(dashboards))` rather than raising. Distinct from the
    empty-only case in test_dashboard_config_via_direct_post, which falls
    back to the (valid) `current`.

    NOTE: `settings['dashboard']['current']` must be restored to a valid
    dashboard before this test returns -- unlike this AJAX-fragment action
    (which has its own fallback), the base page template
    (settings/index.html -> _macro_settings.html's render_dash_select)
    indexes `dashboards[current]` directly with NO equivalent guard, so an
    invalid `current` left behind would 500 every subsequent test in this
    module that renders the full settings page. That asymmetry -- the fix
    protects the fragment endpoint but not the page reading the same
    persisted field -- is noted here rather than chased since fixing the
    template is out of this task's route-coverage scope."""
    apply_settings(lambda s: s["dashboard"].__setitem__("current", "NoSuchDashboard"))
    try:
        resp = page.request.post(f"{live_server}/settings/dashboard_config", form={"selected": ""})
        assert resp.status == 200
        # The first dashboard in default_settings()'s dashboards dict
        # ("Default") is the second-fallback target; its screenshot path is
        # served from /dash/static/default/img/screenshot.png.
        assert "default/img/screenshot.png" in resp.text()
    finally:
        apply_settings(lambda s: s["dashboard"].__setitem__("current", "Default"))


def test_probe_select_empty_selected_defaults_to_first_probe(live_server, page):
    """routes.py:37-38 -- an empty `selected` (the <select>'s unset state)
    defaults to the first probe in probe_map, not just the branch already
    covered by a real label in test_probe_select_via_direct_post."""
    resp = page.request.post(f"{live_server}/settings/probe_select", form={"selected": ""})
    assert resp.status == 200
    assert re.search(r'value="Grill"[^<]*selected', resp.text())


def test_probe_config_empty_and_unmatched_selected_default_to_first_probe(live_server, page):
    """routes.py:51-52 (empty selected) and :55->60/:61 (a `selected` value
    that matches no known probe -- the for/else falls through with
    `probe_info` still None, and the `if probe_info == None` guard catches
    it) both default to the first probe (Grill) instead of crashing."""
    empty_resp = page.request.post(f"{live_server}/settings/probe_config", form={"selected": ""})
    assert empty_resp.status == 200
    assert 'value="Grill"' in empty_resp.text()

    unmatched_resp = page.request.post(f"{live_server}/settings/probe_config", form={"selected": "NoSuchProbeLabel"})
    assert unmatched_resp.status == 200
    assert 'value="Grill"' in unmatched_resp.text()


# ---- notify: every service besides the ifttt/mqtt pair driven above ------


def test_notify_remaining_services_enabled_with_blank_optional_fields(live_server, page):
    """routes.py's notify handler: covers the enabled=True arm (True is
    never the *default* state, so this needs an explicit checkbox) for
    apprise/pushbullet/pushover/onesignal/influxdb/wled_enabled/
    wled_use_suggested_presets/wled_night_mode, and -- by omitting every
    service's optional text field -- the "field NOT in response" False arm
    of each of those fields' own `if "<field>" in response:` guard (e.g.
    pushbullet_apikey, influxdb_url, wled_device_address...). A real
    browser form always submits its (possibly-empty) text inputs, so that
    False arm is otherwise unreachable through the real-UI test above.

    wled_use_profiles is deliberately omitted: default_settings() ships it
    **True**, so the real-UI notify test (which never touches it, but
    submits the whole rendered form) already exercises the True arm just by
    leaving the pre-checked box alone. Omitting it here exercises the False
    arm (routes.py:210) for the first time.
    """
    # appriselocations needs *repeated* form keys (response.getlist(...)),
    # which the simple `form=` dict param can't express (it stringifies a
    # Python list instead of repeating the field) -- FormData.append() does.
    # A trailing empty entry also exercises the `if len(location):` filter
    # that drops blank rows.
    form_data = (
        FormData()
        .append("apprise_enabled", "on")
        .append("appriselocations", "http://example.com/apprise-1")
        .append("appriselocations", "")
        .append("pushbullet_enabled", "on")
        .append("pushover_enabled", "on")
        .append("onesignal_enabled", "on")
        .append("influxdb_enabled", "on")
        .append("wled_enabled", "on")
        .append("wled_use_suggested_presets", "on")
        .append("wled_night_mode", "on")
    )
    resp = page.request.post(f"{live_server}/settings/notify", form=form_data)
    assert resp.status == 200

    settings = read_settings_from_server()
    notify = settings["notify_services"]
    assert notify["apprise"]["enabled"] is True
    assert notify["apprise"]["locations"] == ["http://example.com/apprise-1"]
    assert notify["pushbullet"]["enabled"] is True
    assert notify["pushbullet"]["APIKey"] == ""  # untouched: field omitted
    assert notify["pushover"]["enabled"] is True
    assert notify["pushover"]["UserKeys"] == ""  # untouched: field omitted
    assert notify["onesignal"]["enabled"] is True
    assert notify["influxdb"]["enabled"] is True
    assert notify["influxdb"]["url"] == ""  # untouched: field omitted
    assert notify["mqtt"]["enabled"] is False  # omitted -> explicit False arm
    assert notify["ifttt"]["enabled"] is False  # omitted -> explicit False arm
    assert notify["wled"]["enabled"] is True
    assert notify["wled"]["use_suggested_presets"] is True
    assert notify["wled"]["suggested_config"]["night_mode"] is True
    assert notify["wled"]["use_profiles"] is False  # omitted -> False arm (routes.py:210)
    assert notify["wled"]["device_address"] == "wled.local"  # untouched: field omitted


def test_notify_apprise_locations_omitted_clears_list(live_server, page):
    """routes.py:98 -- when `appriselocations` is absent entirely (as
    opposed to present-with-a-value, covered above), the else arm resets
    locations to []."""
    apply_settings(lambda s: s["notify_services"]["apprise"].__setitem__("locations", ["stale@example.com"]))

    resp = page.request.post(f"{live_server}/settings/notify", form={})
    assert resp.status == 200
    assert read_settings_from_server()["notify_services"]["apprise"]["locations"] == []


def test_notify_device_delete_and_edit(live_server, page):
    """routes.py:161-162 (delete_device), :165-171 (edit_device with a
    non-empty id), and :165->172 (edit_device present but empty -- the
    `!= ""` guard's False arm, a no-op) -- device-management branches
    driven by the OneSignal device list's JS (confirm-then-POST for
    delete, inline edit form for edit), exercised here via direct POST
    like the module's other JS-fragment actions."""

    def _seed(s):
        s["notify_services"]["onesignal"]["devices"] = {
            "dev-old": {"friendly_name": "Old Device", "device_name": "OldPhone", "app_version": "1.0"},
            "dev-edit": {"friendly_name": "Edit Me", "device_name": "EditPhone", "app_version": "1.0"},
        }

    apply_settings(_seed)

    del_resp = page.request.post(f"{live_server}/settings/notify", form={"delete_device": "dev-old"})
    assert del_resp.status == 200
    assert "dev-old" not in read_settings_from_server()["notify_services"]["onesignal"]["devices"]

    edit_resp = page.request.post(
        f"{live_server}/settings/notify",
        form={
            "edit_device": "dev-edit",
            "FriendlyName_dev-edit": "Renamed Device",
            "DeviceName_dev-edit": "RenamedPhone",
            "AppVersion_dev-edit": "2.0",
        },
    )
    assert edit_resp.status == 200
    dev = read_settings_from_server()["notify_services"]["onesignal"]["devices"]["dev-edit"]
    assert dev == {
        "friendly_name": "Renamed Device",
        "device_name": "RenamedPhone",
        "app_version": "2.0",
    }

    noop_resp = page.request.post(f"{live_server}/settings/notify", form={"edit_device": ""})
    assert noop_resp.status == 200
    # Unchanged: the empty-id guard is a no-op, not a "clear all" or similar.
    assert read_settings_from_server()["notify_services"]["onesignal"]["devices"]["dev-edit"] == dev


def test_notify_delete_nonexistent_device_crashes_uncaught(live_server, page):
    """LATENT BUG (blueprints/settings/routes.py:162), severity LOW-MEDIUM:
    `delete_device` pops straight from the devices dict with NO exception
    handling -- unlike every other delete-style branch in this route
    (editprofile's `delete`, e.g., wraps the same pop in try/except and
    turns a missing id into a graceful `event['type']='error'`). POSTing a
    `delete_device` id that isn't in the devices dict (a stale device list,
    a double-click, two browser tabs racing) raises an uncaught KeyError,
    and the whole settings-page render 500s instead of showing an inline
    error. Pinned as a characterization test (route-coverage task scope --
    not fixing), not silently green-washed."""
    resp = page.request.post(f"{live_server}/settings/notify", form={"delete_device": "does-not-exist"})
    assert resp.status == 500


# ---- editprofile: the entire `delete` branch, plus edit's error arms -----


def test_editprofile_delete_unused_profile_succeeds(live_server, page):
    """routes.py:229-243 -- deleting a profile no probe currently
    references removes it and writes settings."""
    resp = page.request.post(
        f"{live_server}/settings/editprofile",
        form={"delete": "PT-1000-PiFire", "Name_PT-1000-PiFire": "PT-1000-Grill-Probe-PiFire"},
    )
    assert resp.status == 200
    assert "PT-1000-PiFire" not in read_settings_from_server()["probe_settings"]["probe_profiles"]


def test_editprofile_delete_in_use_profile_blocked(live_server, page):
    """routes.py:232-237 -- deleting a profile a probe currently references
    is blocked with an inline error instead of silently orphaning the
    probe's profile reference. TWPS00 is referenced by Probe1/2/3 in
    default_settings()."""
    resp = page.request.post(
        f"{live_server}/settings/editprofile",
        form={"delete": "TWPS00", "Name_TWPS00": "whatever"},
    )
    assert resp.status == 200
    assert "Cannot delete this profile" in resp.text()
    assert "TWPS00" in read_settings_from_server()["probe_settings"]["probe_profiles"]


def test_editprofile_delete_missing_profile_sets_graceful_error(live_server, page):
    """routes.py:244-246 -- the except arm: popping an id that's already
    gone (e.g. a stale form / double-submit) is caught and turned into an
    inline error, *provided* the matching Name_<id> field is still present
    (as a real stale-but-rendered form would send it) -- see the crash test
    above for what happens when it isn't."""
    resp = page.request.post(
        f"{live_server}/settings/editprofile",
        form={"delete": "already-gone-id", "Name_already-gone-id": "Ghost Profile"},
    )
    assert resp.status == 200
    assert "Error: Failed to remove Ghost Profile profile" in resp.text()


def test_editprofile_edit_profile_not_in_use_skips_probe_map_and_control(live_server, page):
    """routes.py:274->exit -- editing a profile no probe currently
    references still updates the profile dict itself, but does NOT touch
    any probe_map entry and does NOT flag `control['probe_profile_update']`
    (the True-branch counterpart, where a probe DOES use the profile, is
    covered by test_editprofile_via_real_ui)."""
    apply_control(lambda c: c.__setitem__("probe_profile_update", False))

    resp = page.request.post(
        f"{live_server}/settings/editprofile",
        form={
            "editprofile": "PT-1000-OEM",
            "Name_PT-1000-OEM": "Renamed Unused Profile",
            "A_PT-1000-OEM": "0.0012",
            "B_PT-1000-OEM": "0.00023",
            "C_PT-1000-OEM": "0.00000034",
        },
    )
    assert resp.status == 200

    profile = read_settings_from_server()["probe_settings"]["probe_profiles"]["PT-1000-OEM"]
    assert profile["name"] == "Renamed Unused Profile"
    assert profile["A"] == 0.0012

    drain_control_writes()
    assert read_control_from_server()["probe_profile_update"] is False


def test_editprofile_edit_invalid_numeric_input_sets_error(live_server, page):
    """routes.py:277-279 -- a non-numeric A/B/C value raises inside the
    try, caught by the except and turned into an inline error; the profile
    dict is left unchanged (the dict literal raises before assignment)."""
    before = read_settings_from_server()["probe_settings"]["probe_profiles"]["PT-1000-OEM"]

    resp = page.request.post(
        f"{live_server}/settings/editprofile",
        form={
            "editprofile": "PT-1000-OEM",
            "Name_PT-1000-OEM": "Should Not Save",
            "A_PT-1000-OEM": "not-a-number",
            "B_PT-1000-OEM": "0.1",
            "C_PT-1000-OEM": "0.1",
        },
    )
    assert resp.status == 200
    assert "Something bad happened" in resp.text()
    assert read_settings_from_server()["probe_settings"]["probe_profiles"]["PT-1000-OEM"] == before


def test_editprofile_edit_empty_editprofile_value_sets_error(live_server, page):
    """routes.py:280-282 -- `editprofile` present but empty (the collapse
    form's hidden default) is the "nothing selected" case, distinct from
    the key being absent entirely (routes.py:248->exit, exercised by every
    `delete`-only POST above)."""
    resp = page.request.post(f"{live_server}/settings/editprofile", form={"editprofile": ""})
    assert resp.status == 200
    assert "Profile NOT saved" in resp.text()


# ---- addprofile: apply_profile branch + both error arms ------------------


def test_addprofile_with_apply_profile_updates_probe_map(live_server, page):
    """routes.py:299-306 -- `apply_profile` (the "and apply to probe X"
    checkbox on the add-profile form) both creates the new profile AND
    immediately assigns it to the named probe's probe_map entry, the same
    cross-write test_editprofile_via_real_ui proves for the edit path."""
    resp = page.request.post(
        f"{live_server}/settings/addprofile",
        form={
            "Name": "Apply Test Profile",
            "A": "0.002",
            "B": "0.0003",
            "C": "0.00000004",
            "apply_profile": "Probe3",
        },
    )
    assert resp.status == 200
    assert "Successfully added Apply Test Profile" in resp.text()

    settings = read_settings_from_server()
    matches = [p for p in settings["probe_settings"]["probe_profiles"].values() if p["name"] == "Apply Test Profile"]
    assert len(matches) == 1
    new_id = matches[0]["id"]

    probe3 = next(p for p in settings["probe_settings"]["probe_map"]["probe_info"] if p["label"] == "Probe3")
    assert probe3["profile"]["id"] == new_id
    assert probe3["profile"]["name"] == "Apply Test Profile"


def test_addprofile_missing_required_field_sets_error(live_server, page):
    """routes.py:315-317 -- any of Name/A/B/C blank is rejected before any
    conversion is attempted."""
    before_count = len(read_settings_from_server()["probe_settings"]["probe_profiles"])

    resp = page.request.post(
        f"{live_server}/settings/addprofile",
        form={"Name": "", "A": "1", "B": "2", "C": "3"},
    )
    assert resp.status == 200
    assert "All fields must be completed" in resp.text()
    assert len(read_settings_from_server()["probe_settings"]["probe_profiles"]) == before_count


def test_addprofile_invalid_numeric_input_sets_error(live_server, page):
    """routes.py:312-314 -- a non-numeric A/B/C raises inside the try,
    caught by the except; no profile is added (the dict literal raises
    before the UUID is ever assigned a key)."""
    before_count = len(read_settings_from_server()["probe_settings"]["probe_profiles"])

    resp = page.request.post(
        f"{live_server}/settings/addprofile",
        form={"Name": "Bad Profile", "A": "not-a-number", "B": "1", "C": "1"},
    )
    assert resp.status == 200
    assert "Something bad happened" in resp.text()
    assert len(read_settings_from_server()["probe_settings"]["probe_profiles"]) == before_count


# ---- cycle: blank/unchecked False arms + the controller-option-type loop -


def test_cycle_all_blank_and_unchecked_skips_every_guarded_field(live_server, page):
    """The False arm of every `is_not_blank`/`is_checked` guard in
    `_settings_cycle` (routes.py:337-384), plus the whole controller-config
    block being skipped when `selectController` is blank
    (routes.py:386->410) -- test_cycle_via_real_ui above exercises every
    one of these guards' True arm; an entirely empty POST exercises all of
    their False arms at once. Numeric/blank-skipped fields must retain
    their seeded sentinel value; checkbox fields (which have no
    "unchanged" state -- `is_checked` only ever reads the current request)
    must flip to their explicit False assignment."""

    def _seed(s):
        cd = s["cycle_data"]
        cd.update(PMode=42, HoldCycleTime=99, SmokeOnCycleTime=11, SmokeOffCycleTime=22, u_min=0.11, u_max=0.91)
        cd["LidOpenDetectEnabled"] = True
        cd["LidOpenThreshold"] = 33
        cd["LidOpenPauseTime"] = 44
        cd["FanPidEnabled"] = True
        s["smoke_plus"].update(on_time=5, off_time=6, min_temp=150, max_temp=200, enabled=True)
        s["keep_warm"].update(temp=160, s_plus=True)

    apply_settings(_seed)
    apply_control(lambda c: c.__setitem__("controller_update", False))

    resp = page.request.post(f"{live_server}/settings/cycle", form={})
    assert resp.status == 200

    settings = read_settings_from_server()
    cd = settings["cycle_data"]
    # Blank numeric fields: unchanged (is_not_blank False arm).
    assert cd["PMode"] == 42
    assert cd["HoldCycleTime"] == 99
    assert cd["SmokeOnCycleTime"] == 11
    assert cd["SmokeOffCycleTime"] == 22
    assert cd["u_min"] == 0.11
    assert cd["u_max"] == 0.91
    assert cd["LidOpenThreshold"] == 33
    assert cd["LidOpenPauseTime"] == 44
    assert settings["smoke_plus"]["on_time"] == 5
    assert settings["smoke_plus"]["off_time"] == 6
    assert settings["smoke_plus"]["min_temp"] == 150
    assert settings["smoke_plus"]["max_temp"] == 200
    assert settings["keep_warm"]["temp"] == 160
    # Unchecked checkboxes: explicit False (is_checked False arm's own assignment).
    assert cd["LidOpenDetectEnabled"] is False
    assert cd["FanPidEnabled"] is False
    assert settings["smoke_plus"]["enabled"] is False
    assert settings["keep_warm"]["s_plus"] is False
    # selectController blank -> whole controller block skipped.
    assert settings["controller"]["selected"] != ""  # untouched, still whatever it was
    drain_control_writes()
    assert read_control_from_server()["controller_update"] is False


def test_cycle_sp_fan_ramp_and_sp_duty_cycle_checked(live_server, page):
    """routes.py:366 (sp_fan_ramp True arm) and :370 (sp_duty_cycle
    is_not_blank True arm) -- sp_duty_cycle is gated behind `platform.
    dc_fan` in the real UI (see test_pwm_via_real_ui's note), and no
    existing test checks sp_fan_ramp at all; both are one-off direct-POST
    here since reconstructing the dc_fan-visible UI path adds nothing."""
    resp = page.request.post(
        f"{live_server}/settings/cycle",
        form={"sp_fan_ramp": "on", "sp_duty_cycle": "67"},
    )
    assert resp.status == 200
    settings = read_settings_from_server()
    assert settings["smoke_plus"]["fan_ramp"] is True
    assert settings["smoke_plus"]["duty_cycle"] == 67


def test_cycle_controller_config_option_types(live_server, page):
    """routes.py:392-408 -- the per-option `controller_config_<name>` loop
    converts each field per its metadata `option_type`. The `mpc`
    controller (controller/controllers.json) has float/int/bool/string
    options, covering 4 of the 5 branches; `numlist` (routes.py:403-404)
    has no real controller metadata using that type anywhere in
    controllers.json -- it looks like dead/defensive code, not chased here
    per the task's "genuinely unreachable" carve-out."""
    resp = page.request.post(
        f"{live_server}/settings/cycle",
        form={
            "selectController": "mpc",
            "controller_config_n_horizon": "12",  # int
            "controller_config_t_step": "0.75",  # float
            "controller_config_enable_fan_input": "true",  # bool
            "controller_config_policy_net_path": "/tmp/policy.pt",  # string -> else arm
        },
    )
    assert resp.status == 200

    settings = read_settings_from_server()
    assert settings["controller"]["selected"] == "mpc"
    assert settings["controller"]["config"]["mpc"] == {
        "n_horizon": 12,
        "t_step": 0.75,
        "enable_fan_input": True,
        "policy_net_path": "/tmp/policy.pt",
    }
    drain_control_writes()
    assert read_control_from_server()["controller_update"] is True


# ---- pwm: blank/unchecked False arms --------------------------------------


def test_pwm_blank_and_unchecked_skips_every_field(live_server, page):
    """routes.py:422 (pwm_control False arm) and :423-430 (every is_not_blank
    False arm) -- test_pwm_via_real_ui above exercises all the True arms."""

    def _seed(s):
        s["pwm"].update(pwm_control=True, update_time=9, min_duty_cycle=15, max_duty_cycle=95, frequency=333)

    apply_settings(_seed)

    resp = page.request.post(f"{live_server}/settings/pwm", form={})
    assert resp.status == 200

    pwm = read_settings_from_server()["pwm"]
    assert pwm["pwm_control"] is False
    assert pwm["update_time"] == 9
    assert pwm["min_duty_cycle"] == 15
    assert pwm["max_duty_cycle"] == 95
    assert pwm["frequency"] == 333


# ---- startup: blank/unchecked False arms, the pwm_duty_cycle clamp, and --
# ---- a latent validation-is-a-no-op bug in prime_on_startup --------------


def test_startup_blank_and_unchecked_skips_every_guarded_field(live_server, page):
    """routes.py:441-469's False arms. `after_startup_mode` and
    `startup_mode_setpoint` are read unconditionally (no is_not_blank
    guard), so they're the only fields this POST must include."""

    def _seed(s):
        s["shutdown"].update(shutdown_duration=77, auto_power_off=True)
        s["startup"].update(duration=88, startup_exit_temp=99, prime_on_startup=15)
        s["startup"]["smartstart"].update(enabled=True, exit_temp=66)
        s["startup"]["start_to_mode"]["start_to_hold_prompt"] = True

    apply_settings(_seed)

    resp = page.request.post(
        f"{live_server}/settings/startup",
        form={"after_startup_mode": "Hold", "startup_mode_setpoint": "200"},
    )
    assert resp.status == 200

    startup = read_settings_from_server()
    assert startup["shutdown"]["shutdown_duration"] == 77  # blank -> unchanged
    assert startup["startup"]["duration"] == 88  # blank -> unchanged
    assert startup["shutdown"]["auto_power_off"] is False  # unchecked -> explicit False
    assert startup["startup"]["smartstart"]["enabled"] is False  # unchecked -> explicit False
    assert startup["startup"]["smartstart"]["exit_temp"] == 66  # blank -> unchanged
    assert startup["startup"]["startup_exit_temp"] == 99  # blank -> unchanged
    assert startup["startup"]["prime_on_startup"] == 15  # whole prime block skipped -> unchanged
    assert startup["startup"]["start_to_mode"]["after_startup_mode"] == "Hold"
    assert startup["startup"]["start_to_mode"]["primary_setpoint"] == 200
    assert startup["startup"]["start_to_mode"]["start_to_hold_prompt"] is False  # unchecked -> explicit False


def test_startup_prime_on_startup_out_of_range_is_not_actually_clamped(live_server, page):
    """LATENT BUG (blueprints/settings/routes.py:457-461), severity MEDIUM:

        if is_not_blank(response, "prime_on_startup"):
            prime_amount = int(response["prime_on_startup"])
            if prime_amount < 0 or prime_amount > 200:
                prime_amount = 0  # Validate input, set to disabled if exceeding limits.
            settings["startup"]["prime_on_startup"] = int(response["prime_on_startup"])

    `prime_amount` is computed and clamped to 0 when out of [0, 200], but
    the clamped variable is never used -- the last line re-parses
    `response["prime_on_startup"]` directly, so the validation is a no-op
    and any out-of-range value (this is the "how much pellet primer to run
    on startup" setting, a real actuator-timing value) is saved verbatim.
    Pinned as a characterization test (route-coverage task scope -- not
    fixing); documents the ACTUAL (buggy) behavior, not the intended one."""
    resp = page.request.post(
        f"{live_server}/settings/startup",
        form={"after_startup_mode": "Smoke", "startup_mode_setpoint": "165", "prime_on_startup": "250"},
    )
    assert resp.status == 200
    # Intended behavior (per the dead comment) would clamp this to 0.
    # Actual behavior: the out-of-range value passes straight through.
    assert read_settings_from_server()["startup"]["prime_on_startup"] == 250


def test_startup_pwm_duty_cycle_clamped_to_pwm_settings_range(live_server, page):
    """routes.py:471-475 -- `startup.pwm_duty_cycle` IS correctly
    double-clamped (min() then max()) against the current pwm settings'
    min/max_duty_cycle, unlike prime_on_startup above."""
    apply_settings(lambda s: s["pwm"].update(min_duty_cycle=20, max_duty_cycle=80))

    over_resp = page.request.post(
        f"{live_server}/settings/startup",
        form={
            "after_startup_mode": "Smoke",
            "startup_mode_setpoint": "165",
            "pwm_duty_cycle": "150",
        },
    )
    assert over_resp.status == 200
    assert read_settings_from_server()["startup"]["pwm_duty_cycle"] == 80

    under_resp = page.request.post(
        f"{live_server}/settings/startup",
        form={
            "after_startup_mode": "Smoke",
            "startup_mode_setpoint": "165",
            "pwm_duty_cycle": "5",
        },
    )
    assert under_resp.status == 200
    assert read_settings_from_server()["startup"]["pwm_duty_cycle"] == 20


# ---- history: blank/checked-True arms + the active-mode block ------------


def test_history_blank_fields_and_checked_toggles(live_server, page):
    """routes.py:486-507's remaining arms: blank historymins/datapoints
    (True arm covered by test_history_via_real_ui), and
    clearhistorystartup/historyautorefresh/ext_data all checked ON (the
    real-UI test above unchecks autorefresh and never touches
    clearhistorystartup, so their True/False arms are inverted here).
    Control mode is pinned to STOP so the ext_data change isn't blocked by
    the active-mode guard (covered separately below)."""
    apply_control(lambda c: c.__setitem__("mode", Mode.STOP))

    def _seed(s):
        s["history_page"].update(minutes=999, datapoints=999, clearhistoryonstart=False, autorefresh="off")
        s["globals"]["ext_data"] = True

    apply_settings(_seed)

    resp = page.request.post(
        f"{live_server}/settings/history",
        form={"clearhistorystartup": "on", "historyautorefresh": "on"},
    )
    assert resp.status == 200

    settings = read_settings_from_server()
    assert settings["history_page"]["minutes"] == 999  # blank -> unchanged
    assert settings["history_page"]["datapoints"] == 999  # blank -> unchanged
    assert settings["history_page"]["clearhistoryonstart"] is True
    assert settings["history_page"]["autorefresh"] == "on"
    assert settings["globals"]["ext_data"] is False  # unchecked -> explicit False


def test_history_ext_data_blocked_while_grill_active(live_server, page):
    """routes.py:500-502 -- ext_data cannot be changed while
    `control['mode'] != Mode.STOP` (a live cook is running); the route
    returns an inline error instead of applying the change."""
    apply_control(lambda c: c.__setitem__("mode", Mode.HOLD))
    apply_settings(lambda s: s["globals"].__setitem__("ext_data", False))

    resp = page.request.post(f"{live_server}/settings/history", form={"ext_data": "on"})
    assert resp.status == 200
    assert "cannot be changed in any active mode" in resp.text()
    assert read_settings_from_server()["globals"]["ext_data"] is False  # unchanged

    apply_control(lambda c: c.__setitem__("mode", Mode.STOP))  # restore for later tests


# ---- safety: blank/checked-True arms --------------------------------------


def test_safety_blank_fields_and_checked_toggles(live_server, page):
    """routes.py:539-558's remaining arms: blank minstartuptemp/
    maxstartuptemp/reigniteretries/maxtemp/manual_override_time (blank arm
    -- test_safety_checkbox_and_numeric_via_real_ui above always fills
    manual_override_time), and allow_manual_changes checked ON (never
    touched above; startup_check's True arm is also re-covered here since
    the real-UI test only exercises unchecking it)."""

    def _seed(s):
        s["safety"].update(
            minstartuptemp=55,
            maxstartuptemp=66,
            reigniteretries=7,
            maxtemp=77,
            manual_override_time=88,
            startup_check=False,
            allow_manual_changes=False,
        )

    apply_settings(_seed)

    resp = page.request.post(
        f"{live_server}/settings/safety",
        form={"startup_check": "on", "allow_manual_changes": "on"},
    )
    assert resp.status == 200

    safety = read_settings_from_server()["safety"]
    assert safety["minstartuptemp"] == 55
    assert safety["maxstartuptemp"] == 66
    assert safety["reigniteretries"] == 7
    assert safety["maxtemp"] == 77
    assert safety["manual_override_time"] == 88
    assert safety["startup_check"] is True
    assert safety["allow_manual_changes"] is True


# ---- pellets: blank/unchecked False arms -----------------------------------


def test_pellets_blank_and_unchecked_skips_every_field(live_server, page):
    """routes.py:567-587's False arms -- test_pellets_via_real_ui above
    exercises every True arm (including empty/full's control
    `distance_update` side effect); an entirely empty POST exercises all
    the False/blank arms, including confirming `distance_update` is NOT
    forced when empty/full are both blank."""

    def _seed(s):
        s["pelletlevel"].update(warning_enabled=True, warning_time=11, warning_level=22, empty=33, full=44)
        s["globals"].update(augerrate=0.99, prime_ignition=True)

    apply_settings(_seed)
    apply_control(lambda c: c.__setitem__("distance_update", False))

    resp = page.request.post(f"{live_server}/settings/pellets", form={})
    assert resp.status == 200

    settings = read_settings_from_server()
    pl = settings["pelletlevel"]
    assert pl["warning_enabled"] is False
    assert pl["warning_time"] == 11
    assert pl["warning_level"] == 22
    assert pl["empty"] == 33
    assert pl["full"] == 44
    assert settings["globals"]["augerrate"] == 0.99
    assert settings["globals"]["prime_ignition"] is False

    drain_control_writes()
    assert read_control_from_server()["distance_update"] is False
