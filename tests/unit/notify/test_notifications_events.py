"""
Characterization + refactor tests for notify.notifications.send_notifications.

Pins the exact (title, body, channel, query_args) tuple produced by every live
event string, plus the sender fan-out gating, before/through the EVENTS-table
refactor (Phase H). All network/apprise senders are mocked -- no real
notification is ever sent by this module.
"""

import notify.notifications as N


def _base_settings():
    return {
        "globals": {"debug_mode": False, "units": "F"},
        "safety": {"maxtemp": 550},
        "notify_services": {
            "apprise": {"locations": "", "enabled": False},
            "ifttt": {"APIKey": "key", "enabled": True},
            "pushbullet": {"APIKey": "", "PublicURL": "", "enabled": False},
            "pushover": {"APIKey": "", "UserKeys": "", "PublicURL": "", "enabled": False},
            "onesignal": {"app_id": "app", "devices": {}, "enabled": True},
            "mqtt": {"broker": "", "enabled": False},
            "wled": {"device_address": "", "enabled": False},
        },
    }


def _base_control():
    return {
        "safety": {"startuptemp": 100},
        "recipe": {"step_data": {"message": "Flip the brisket. "}},
    }


def _base_pelletdb():
    return {"current": {"hopper_level": 42}}


def _capture(monkeypatch, event, label="Probe", target=0, settings=None, control=None, pelletdb=None):
    settings = settings or _base_settings()
    control = control or _base_control()
    pelletdb = pelletdb or _base_pelletdb()
    rec = {}
    monkeypatch.setattr(N, "read_settings", lambda *a, **k: settings)
    monkeypatch.setattr(N, "read_control", lambda *a, **k: control)
    monkeypatch.setattr(N, "read_pellet_db", lambda *a, **k: pelletdb)

    def fake_onesignal(s, title, body, channel):
        rec["title"], rec["body"], rec["channel"] = title, body, channel

    def fake_ifttt(s, ev, query_args):
        rec["query_args"] = query_args

    monkeypatch.setattr(N, "_send_onesignal_notification", fake_onesignal)
    monkeypatch.setattr(N, "_send_ifttt_notification", fake_ifttt)
    # silence every other sender
    for name in (
        "_send_apprise_notifications",
        "_send_pushbullet_notification",
        "_send_pushover_notification",
        "_send_mqtt_notification",
        "_send_wled_notification",
    ):
        monkeypatch.setattr(N, name, lambda *a, **k: None)
    N.send_notifications(event, label=label, target=target)
    return rec


def test_probe_temp_achieved(monkeypatch):
    rec = _capture(monkeypatch, "Probe_Temp_Achieved", label="Probe", target=0)
    assert rec["title"] == "Probe Target Achieved"
    assert rec["body"].startswith("Probe target of 0F achieved at ")
    assert rec["channel"] == "pifire_temp_alerts"
    assert rec["query_args"] == {"value1": True}
    assert rec["query_args"]["value1"] is True


def test_probe_temp_achieved_label_and_target(monkeypatch):
    rec = _capture(monkeypatch, "Probe_Temp_Achieved", label="Grate", target=225)
    assert rec["title"] == "Grate Target Achieved"
    assert rec["body"].startswith("Grate target of 225F achieved at ")
    assert rec["channel"] == "pifire_temp_alerts"
    assert rec["query_args"]["value1"] is True


def test_probe_temp_limit_alarm(monkeypatch):
    rec = _capture(monkeypatch, "Probe_Temp_Limit_Alarm", label="Probe", target=0)
    assert rec["title"] == "Probe Limit Reached"
    assert rec["body"].startswith("Probe limit of 0F exceeded at ")
    assert rec["channel"] == "pifire_temp_alerts"
    assert rec["query_args"] == {"value1": True}
    assert rec["query_args"]["value1"] is True


def test_timer_expired(monkeypatch):
    rec = _capture(monkeypatch, "Timer_Expired")
    assert rec["title"] == "Timer Complete"
    assert rec["body"] == "Your timer has expired, time to check your cook!"
    assert rec["channel"] == "pifire_timer_alerts"
    assert rec["query_args"] == {"value1": "Your timer has expired."}


def test_pellet_level_low(monkeypatch):
    rec = _capture(monkeypatch, "Pellet_Level_Low")
    assert rec["title"] == "Low Pellet Level"
    assert rec["body"] == "Your pellet level is currently at 42%"
    assert rec["channel"] == "pifire_pellet_alerts"
    assert rec["query_args"] == {"value1": rec["body"]}


def test_grill_error_01(monkeypatch):
    rec = _capture(monkeypatch, "Grill_Error_01")
    assert rec["title"] == "Grill Error!"
    # Approved behavior change (Task 2): "exceded" -> "exceeded" typo fix.
    assert rec["body"].startswith("Grill exceeded maximum temperature limit of 550F! Shutting down. ")
    assert rec["channel"] == "pifire_error_alerts"
    assert rec["query_args"] == {"value1": "550"}


def test_grill_error_02(monkeypatch):
    rec = _capture(monkeypatch, "Grill_Error_02")
    assert rec["title"] == "Grill Error!"
    assert rec["body"].startswith(
        "Grill temperature dropped below minimum startup temperature of 100F!"
        " Shutting down to prevent firepot overload. "
    )
    assert rec["channel"] == "pifire_error_alerts"
    assert rec["query_args"] == {"value1": "100"}


def test_grill_error_03(monkeypatch):
    rec = _capture(monkeypatch, "Grill_Error_03")
    assert rec["title"] == "Grill Error!"
    # No trailing <now> suffix -- exact match.
    assert rec["body"] == (
        "Grill temperature dropped below minimum startup temperature of 100F!"
        " Starting a re-ignite attempt, per user settings."
    )
    assert rec["channel"] == "pifire_error_alerts"
    assert rec["query_args"] == {"value1": "100"}


def test_recipe_step_message(monkeypatch):
    rec = _capture(monkeypatch, "Recipe_Step_Message")
    assert rec["title"] == "Recipe Message"
    assert rec["body"].startswith("Flip the brisket. ")
    assert rec["channel"] == "pifire_recipe_message"
    assert rec["query_args"] == {"value1": "Flip the brisket. "}


def test_test_notify(monkeypatch):
    rec = _capture(monkeypatch, "Test_Notify")
    assert rec["title"] == "Test Notification"
    assert rec["body"] == "This is a test notification from PiFire."
    assert rec["channel"] == "pifire_test_message"
    assert rec["query_args"] == {"value1": "This is a test notification from PiFire."}


def test_control_process_stopped(monkeypatch):
    rec = _capture(monkeypatch, "Control_Process_Stopped")
    assert rec["title"] == "Control Process Stopped!"
    assert rec["body"] == (
        "The control process has encountered an issue and has been stopped. "
        "Check on your grill as soon as possible to prevent damage!"
    )
    assert rec["channel"] == "pifire_error_alerts"
    assert rec["query_args"] == {"value1": "Control Process Stopped"}


def test_unmatched_event_falls_back(monkeypatch):
    rec = _capture(monkeypatch, "Zzz")
    assert rec["title"] == "PiFire: Unknown Notification issue"
    assert rec["body"].startswith("Whoops! PiFire had the following unhandled notify event: Zzz at ")
    assert rec["channel"] == "default"
    assert rec["query_args"] == {"value1": "Unknown Notification issue"}


def test_grill_error_00_is_dropped_and_falls_back(monkeypatch, caplog):
    # Approved behavior change (Task 2): Grill_Error_00 is a dead, never-emitted
    # event and is dropped from EVENTS -- it now routes to the Unknown-Notification
    # fallback, logged at ERROR.
    with caplog.at_level("ERROR", logger="events"):
        rec = _capture(monkeypatch, "Grill_Error_00")
    assert rec["title"] == "PiFire: Unknown Notification issue"
    assert rec["body"].startswith("Whoops! PiFire had the following unhandled notify event: Grill_Error_00 at ")
    assert rec["channel"] == "default"
    assert rec["query_args"] == {"value1": "Unknown Notification issue"}
    assert any(r.levelname == "ERROR" for r in caplog.records)


def test_grill_warning_is_dropped_and_falls_back(monkeypatch, caplog):
    # Approved behavior change (Task 2): Grill_Warning is a dead, never-emitted
    # event and is dropped from EVENTS -- it now routes to the Unknown-Notification
    # fallback, logged at ERROR.
    with caplog.at_level("ERROR", logger="events"):
        rec = _capture(monkeypatch, "Grill_Warning")
    assert rec["title"] == "PiFire: Unknown Notification issue"
    assert rec["body"].startswith("Whoops! PiFire had the following unhandled notify event: Grill_Warning at ")
    assert rec["channel"] == "default"
    assert rec["query_args"] == {"value1": "Unknown Notification issue"}
    assert any(r.levelname == "ERROR" for r in caplog.records)


def test_fan_out_gating_only_ifttt_and_onesignal_fire(monkeypatch):
    settings = _base_settings()
    control = _base_control()
    pelletdb = _base_pelletdb()
    monkeypatch.setattr(N, "read_settings", lambda *a, **k: settings)
    monkeypatch.setattr(N, "read_control", lambda *a, **k: control)
    monkeypatch.setattr(N, "read_pellet_db", lambda *a, **k: pelletdb)

    counters = {
        "onesignal": 0,
        "ifttt": 0,
        "apprise": 0,
        "pushbullet": 0,
        "pushover": 0,
        "mqtt": 0,
        "wled": 0,
    }

    def make_counter(key):
        def _f(*a, **k):
            counters[key] += 1

        return _f

    monkeypatch.setattr(N, "_send_onesignal_notification", make_counter("onesignal"))
    monkeypatch.setattr(N, "_send_ifttt_notification", make_counter("ifttt"))
    monkeypatch.setattr(N, "_send_apprise_notifications", make_counter("apprise"))
    monkeypatch.setattr(N, "_send_pushbullet_notification", make_counter("pushbullet"))
    monkeypatch.setattr(N, "_send_pushover_notification", make_counter("pushover"))
    monkeypatch.setattr(N, "_send_mqtt_notification", make_counter("mqtt"))
    monkeypatch.setattr(N, "_send_wled_notification", make_counter("wled"))

    N.send_notifications("Test_Notify")

    assert counters["onesignal"] == 1
    assert counters["ifttt"] == 1
    assert counters["apprise"] == 0
    assert counters["pushbullet"] == 0
    assert counters["pushover"] == 0
    assert counters["mqtt"] == 0
    assert counters["wled"] == 0
