"""Every settings-blueprint writer must produce a strictly-valid tree.

Pre-enforcement matrix (S2 Task 3): write_settings() does not yet validate;
these tests call validate_settings_tree() on the store's tree after each
`_SETTINGS_DISPATCH` handler runs, so handler bugs surface BEFORE the
Task-5 gate flips validation on for real. Enforcement is NOT flipped here --
validate_settings_tree() is used purely as a test oracle.

Harness choice: the plain `flask_app.test_client()` + `ds` (tests/conftest.py,
function-scoped temp-SQLite datastore) pattern already proven by
tests/web/test_api_settings_update.py and tests/web/test_webapp_sqlite.py's
`test_settings_display_post_*` tests -- a real Flask app, real routes.py
dispatch, real write_settings()/SQLite round-trip, but no Playwright/Chromium
(which tests/web/conftest.py's live_server needs and agent envs skip). Every
POST goes through the actual `_SETTINGS_DISPATCH` handler; read-back goes
through the actual `read_settings()` accessor -- both real code paths, only
the browser is swapped for werkzeug's WSGI test client.

Payloads mirror what blueprints/settings/templates/settings/index.html and
_macro_probes.html actually submit: checkboxes as "on" (or omitted), numbers
as form strings, probe_config_save/smartstart/pwm_duty_cycle as JSON bodies
(their JS callers use $.ajax with contentType "application/json").
"""

from common.settings_schema import validate_settings_tree

import pytest

from common.datastore_accessors import read_settings


@pytest.fixture
def client_and_store(ds):
    from app import app as flask_app

    flask_app.config.update(TESTING=True)
    client = flask_app.test_client()
    return client, read_settings


def _assert_store_strict(read_settings_fn):
    validate_settings_tree(read_settings_fn())  # raises SettingsValidationError on any handler bug


# --- Render-only handlers (no write_settings call) ---------------------
#
# dashboard_config / probe_select / probe_config / controller_card never
# mutate the settings tree -- they just re-render a fragment from the
# request. Covered here anyway (S2 Task 3 Step 2 asks for every dispatch
# entry): a broken render (KeyError/IndexError) would still be a handler
# bug worth pinning, and confirming the store is untouched-but-still-strict
# closes out the dispatch table.


def test_settings_dashboard_config_post_leaves_store_strict(client_and_store):
    client, read_settings = client_and_store
    resp = client.post("/settings/dashboard_config", data={"selected": "Default"})
    assert resp.status_code == 200
    _assert_store_strict(read_settings)


def test_settings_probe_select_post_leaves_store_strict(client_and_store):
    client, read_settings = client_and_store
    resp = client.post("/settings/probe_select", data={"selected": ""})
    assert resp.status_code == 200
    _assert_store_strict(read_settings)


def test_settings_probe_config_post_leaves_store_strict(client_and_store):
    client, read_settings = client_and_store
    resp = client.post("/settings/probe_config", data={"selected": ""})
    assert resp.status_code == 200
    _assert_store_strict(read_settings)


def test_settings_controller_card_post_leaves_store_strict(client_and_store):
    client, read_settings = client_and_store
    resp = client.post("/settings/controller_card", data={"selected": "pid"})
    assert resp.status_code == 200
    _assert_store_strict(read_settings)


# --- Writer handlers -----------------------------------------------------


def test_settings_display_post_writes_strict(client_and_store):
    client, read_settings = client_and_store
    client.post("/settings/display", data={"sleep_timeout": "123"})
    assert read_settings()["display"]["sleep_timeout"] == 123
    _assert_store_strict(read_settings)


def test_settings_probe_config_save_post_writes_strict(client_and_store):
    client, read_settings = client_and_store
    payload = {
        "label": "Grill",
        "name": "Grill Probe",
        "type": "Primary",
        "device": "proto_adc",
        "port": "ADC0",
        "enabled": "true",
        "profile_id": "99b8f02d-233d-11ee-a7a2-e5396c02c5fd",
    }
    resp = client.post("/settings/probe_config_save", json=payload)
    assert resp.get_json()["result"] == "success"
    assert read_settings()["probe_settings"]["probe_map"]["probe_info"][0]["name"] == "Grill Probe"
    _assert_store_strict(read_settings)


def test_settings_notify_post_writes_strict(client_and_store):
    client, read_settings = client_and_store

    profile_states = read_settings()["notify_services"]["wled"]["profile_numbers"].keys()
    payload = {
        "apprise_enabled": "on",
        "appriselocations": ["mailto://user@example.com", "json://example.com/hook"],
        "ifttt_enabled": "on",
        "iftttapi": "ifttt-key-123",
        "pushbullet_enabled": "on",
        "pushbullet_apikey": "pb-key",
        "pushbullet_publicurl": "https://pb.example.com",
        "pushover_enabled": "on",
        "pushover_apikey": "po-key",
        "pushover_userkeys": "po-user",
        "pushover_publicurl": "https://po.example.com",
        "onesignal_enabled": "on",
        "edit_device": "device-1",
        "FriendlyName_device-1": "Kitchen Phone",
        "DeviceName_device-1": "Pixel",
        "AppVersion_device-1": "1.2.3",
        "influxdb_enabled": "on",
        "influxdb_url": "http://influx.example.com",
        "influxdb_token": "tok",
        "influxdb_org": "org",
        "influxdb_bucket": "bucket",
        "mqtt_enabled": "on",
        "mqtt_id": "PiFire",
        "mqtt_broker": "mqtt.example.com",
        "mqtt_port": "1883",
        "mqtt_user": "mqttuser",
        "mqtt_pw": "mqttpw",
        "mqtt_auto_d": "homeassistant",
        "mqtt_freq": "30",
        "wled_enabled": "on",
        "wled_device_address": "wled.local",
        "wled_use_suggested_presets": "on",
        "wled_cooking_color": "red",
        "wled_idle_brightness": "50",
        "wled_led_count": "120",
        "wled_night_mode": "on",
        "wled_use_profiles": "on",
        "wled_notify_duration": "180",
    }
    for state in profile_states:
        payload[f"wled_profile_{state}"] = "77"

    client.post("/settings/notify", data=payload)

    written = read_settings()
    assert written["notify_services"]["mqtt"]["port"] == "1883"
    assert written["notify_services"]["wled"]["suggested_config"]["idle_brightness"] == 50
    assert written["notify_services"]["onesignal"]["devices"]["device-1"]["friendly_name"] == "Kitchen Phone"
    _assert_store_strict(read_settings)


def test_settings_addprofile_post_writes_strict(client_and_store):
    client, read_settings = client_and_store
    client.post(
        "/settings/addprofile",
        data={"Name": "Custom Probe Profile", "A": "0.00023", "B": "0.00023", "C": "0.00000010"},
    )
    profiles = read_settings()["probe_settings"]["probe_profiles"]
    assert any(p["name"] == "Custom Probe Profile" for p in profiles.values())
    _assert_store_strict(read_settings)


def test_settings_editprofile_edit_post_writes_strict(client_and_store):
    client, read_settings = client_and_store
    unique_id = "ET73-HM"  # unused-by-default profile id from probes/probes.json
    client.post(
        "/settings/editprofile",
        data={
            "editprofile": unique_id,
            f"Name_{unique_id}": "Edited ET73",
            f"A_{unique_id}": "0.00023",
            f"B_{unique_id}": "0.00023",
            f"C_{unique_id}": "0.00000010",
        },
    )
    assert read_settings()["probe_settings"]["probe_profiles"][unique_id]["name"] == "Edited ET73"
    _assert_store_strict(read_settings)


def test_settings_editprofile_delete_post_writes_strict(client_and_store):
    client, read_settings = client_and_store
    unique_id = "iGrill-HM"  # unused-by-default profile id from probes/probes.json
    client.post(
        "/settings/editprofile",
        data={"delete": unique_id, f"Name_{unique_id}": "iGrill-Heatermeter"},
    )
    assert unique_id not in read_settings()["probe_settings"]["probe_profiles"]
    _assert_store_strict(read_settings)


def test_settings_cycle_post_writes_strict(client_and_store):
    client, read_settings = client_and_store
    client.post(
        "/settings/cycle",
        data={
            "pmode": "3",
            "holdcycletime": "20",
            "SmokeOnCycleTime": "10",
            "SmokeOffCycleTime": "30",
            "u_min": "0.15",
            "u_max": "0.85",
            "lid_open_detect_enable": "on",
            "lid_open_threshold": "20",
            "lid_open_pausetime": "60",
            "fan_pid_enable": "on",
            "sp_on_time": "5",
            "sp_off_time": "5",
            "sp_duty_cycle": "80",
            "sp_min_temp": "160",
            "sp_max_temp": "220",
            "default_smoke_plus": "on",
            "sp_fan_ramp": "on",
            "keep_warm_temp": "165",
            "keep_warm_s_plus": "on",
            "selectController": "pid",
            "controller_config_PB": "60.0",
            "controller_config_Td": "45.0",
            "controller_config_Ti": "180.0",
            "controller_config_center": "0.5",
        },
    )
    written = read_settings()
    assert written["cycle_data"]["PMode"] == 3
    assert written["cycle_data"]["u_min"] == 0.15
    assert written["controller"]["config"]["pid"]["PB"] == 60.0
    _assert_store_strict(read_settings)


def test_settings_pwm_post_writes_strict(client_and_store):
    client, read_settings = client_and_store
    # min/max chosen to keep the default profiles [20, 35, 50, 75, 100]
    # within [min_duty_cycle, max_duty_cycle] (PwmSettings._check_profiles).
    client.post(
        "/settings/pwm",
        data={
            "pwm_control": "on",
            "pwm_update": "15",
            "min_duty_cycle": "15",
            "max_duty_cycle": "100",
            "frequency": "20000",
        },
    )
    written = read_settings()
    assert written["pwm"]["min_duty_cycle"] == 15
    assert written["pwm"]["update_time"] == 15
    _assert_store_strict(read_settings)


def test_settings_startup_post_writes_strict(client_and_store):
    client, read_settings = client_and_store
    client.post(
        "/settings/startup",
        data={
            "shutdown_duration": "300",
            "startup_duration": "200",
            "auto_power_off": "on",
            "smartstart_enable": "on",
            "smartstart_exit_temp": "130",
            "startup_exit_temp": "50",
            "prime_on_startup": "15",
            "after_startup_mode": "Hold",
            "startup_mode_setpoint": "225",
            "startup_start_to_hold_prompt": "on",
            "pwm_duty_cycle": "50",
        },
    )
    written = read_settings()
    assert written["startup"]["prime_on_startup"] == 15
    assert written["startup"]["start_to_mode"]["after_startup_mode"] == "Hold"
    _assert_store_strict(read_settings)


def test_settings_startup_prime_on_startup_out_of_range_still_clamps_valid(client_and_store):
    """routes.py:479-483 silently zeroes prime_on_startup outside [0, 200]
    (legacy pre-S2 clamp) instead of rejecting -- that pre-write clamp still
    lands a value the S2 schema accepts, so this stays green pre-enforcement.
    """
    client, read_settings = client_and_store
    client.post(
        "/settings/startup",
        data={
            "prime_on_startup": "500",
            "after_startup_mode": "Smoke",
            "startup_mode_setpoint": "165",
        },
    )
    assert read_settings()["startup"]["prime_on_startup"] == 0
    _assert_store_strict(read_settings)


def test_settings_history_post_writes_strict(client_and_store):
    client, read_settings = client_and_store
    client.post(
        "/settings/history",
        data={
            "historymins": "30",
            "clearhistorystartup": "on",
            "historyautorefresh": "on",
            "datapoints": "120",
            "clr_temp_Grill": "rgb(0, 64, 255, 1)",
            "clrbg_temp_Grill": "rgb(0, 128, 255, 1)",
            "clr_setpoint_Grill": "rgb(0, 64, 255, 1)",
            "clrbg_setpoint_Grill": "rgb(0, 128, 255, 1)",
            "clr_target_Grill": "rgb(132, 0, 0, 1)",
            "clrbg_target_Grill": "rgb(200, 0, 0, 1)",
        },
    )
    written = read_settings()
    assert written["history_page"]["minutes"] == 30
    assert written["history_page"]["probe_config"]["Grill"]["line_color"] == "rgb(0, 64, 255, 1)"
    _assert_store_strict(read_settings)


def test_settings_safety_post_writes_strict(client_and_store):
    client, read_settings = client_and_store
    client.post(
        "/settings/safety",
        data={
            "minstartuptemp": "75",
            "maxstartuptemp": "100",
            "maxtemp": "550",
            "reigniteretries": "1",
            "startup_check": "on",
            "manual_override_time": "30",
        },
    )
    _assert_store_strict(read_settings)


def test_settings_pellets_post_writes_strict(client_and_store):
    client, read_settings = client_and_store
    client.post(
        "/settings/pellets",
        data={
            "pellet_warning": "on",
            "warning_time": "30",
            "warning_level": "20",
            "empty": "20",
            "full": "5",
            "auger_rate": "0.35",
            "prime_ignition": "on",
        },
    )
    written = read_settings()
    assert written["pelletlevel"]["empty"] == 20
    assert written["globals"]["augerrate"] == 0.35
    _assert_store_strict(read_settings)


def test_settings_smartstart_post_writes_strict(client_and_store):
    client, read_settings = client_and_store
    payload = {
        "temps_list": [60, 80, 90],
        "profiles": [
            {"startuptime": 360, "augerontime": 15, "p_mode": 0},
            {"startuptime": 360, "augerontime": 15, "p_mode": 1},
            {"startuptime": 240, "augerontime": 15, "p_mode": 3},
            {"startuptime": 240, "augerontime": 15, "p_mode": 5},
        ],
    }
    resp = client.post("/settings/smartstart", json=payload)
    assert resp.get_json()["result"] == "success"
    assert read_settings()["startup"]["smartstart"]["temp_range_list"] == [60, 80, 90]
    _assert_store_strict(read_settings)


def test_settings_pwm_duty_cycle_post_writes_strict(client_and_store):
    client, read_settings = client_and_store
    payload = {
        "dc_temps_list": [3, 7, 10, 15],
        "dc_profiles": [
            {"duty_cycle": 20},
            {"duty_cycle": 35},
            {"duty_cycle": 50},
            {"duty_cycle": 75},
            {"duty_cycle": 100},
        ],
    }
    resp = client.post("/settings/pwm_duty_cycle", json=payload)
    assert resp.get_json()["result"] == "success"
    assert read_settings()["pwm"]["temp_range_list"] == [3, 7, 10, 15]
    _assert_store_strict(read_settings)
