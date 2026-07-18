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

from tests.web.conftest import (
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

    Note: passing `selected=""` (empty/omitted) falls back to
    `settings["dashboard"]["selected"]` in the route, a key
    default_settings() never populates -- a latent KeyError/500 on that
    path, discovered while writing this test. Real usage never hits it
    (the <select> always posts a real dashboard key), so this test only
    exercises the working, always-valid-selected path; see the task
    report for this finding.
    """
    resp_basic = page.request.post(f"{live_server}/settings/dashboard_config", form={"selected": "Basic"})
    assert resp_basic.status == 200
    assert "basic/img/screenshot.png" in resp_basic.text()

    resp_default = page.request.post(f"{live_server}/settings/dashboard_config", form={"selected": "Default"})
    assert resp_default.status == 200
    assert "default/img/screenshot.png" in resp_default.text()


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
    # auger_rate's <input step="0.05">; a value off that step (e.g. 0.42)
    # fails the browser's native constraint validation and silently blocks
    # the submit (no request is ever sent) -- use a step-aligned value.
    page.fill("#auger_rate", "0.35")
    page.locator("#prime_ignition").check(force=True)

    with page.expect_navigation():
        page.locator('form[name="pelletsettings"] button[type="submit"]').click()

    settings = read_settings_from_server()
    assert settings["pelletlevel"]["warning_enabled"] is True
    assert settings["pelletlevel"]["warning_time"] == 30
    assert settings["pelletlevel"]["warning_level"] == 35
    assert settings["pelletlevel"]["full"] == 5
    assert settings["pelletlevel"]["empty"] == 95
    assert settings["globals"]["augerrate"] == 0.35
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
