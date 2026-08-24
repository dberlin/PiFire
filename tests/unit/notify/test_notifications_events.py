"""
Characterization + refactor tests for notify.notifications.send_notifications.

Pins the exact (title, body, channel, query_args) tuple produced by every live
event string, plus the sender fan-out gating, before/through the EVENTS-table
refactor. All network/apprise senders are mocked -- no real
notification is ever sent by this module.
"""

import pytest

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


@pytest.mark.parametrize(
    ("event", "title", "body", "exact", "channel", "query_args"),
    [
        pytest.param(
            "Grill_Error_01",
            "Grill Error!",
            # Behavior change: "exceded" -> "exceeded" typo fix.
            "Grill exceeded maximum temperature limit of 550F! Shutting down. ",
            False,
            "pifire_error_alerts",
            {"value1": "550"},
            id="grill-error-01",
        ),
        pytest.param(
            "Grill_Error_02",
            "Grill Error!",
            "Grill temperature dropped below minimum startup temperature of 100F!"
            " Shutting down to prevent firepot overload. ",
            False,
            "pifire_error_alerts",
            {"value1": "100"},
            id="grill-error-02",
        ),
        pytest.param(
            "Grill_Error_03",
            "Grill Error!",
            # No trailing <now> suffix -- exact match.
            "Grill temperature dropped below minimum startup temperature of 100F!"
            " Starting a re-ignite attempt, per user settings.",
            True,
            "pifire_error_alerts",
            {"value1": "100"},
            id="grill-error-03",
        ),
        pytest.param(
            "Recipe_Step_Message",
            "Recipe Message",
            "Flip the brisket. ",
            False,
            "pifire_recipe_message",
            {"value1": "Flip the brisket. "},
            id="recipe-step",
        ),
        pytest.param(
            "Timer_Expired",
            "Timer Complete",
            "Your timer has expired, time to check your cook!",
            True,
            "pifire_timer_alerts",
            {"value1": "Your timer has expired."},
            id="timer-expired",
        ),
        pytest.param(
            "Test_Notify",
            "Test Notification",
            "This is a test notification from PiFire.",
            True,
            "pifire_test_message",
            {"value1": "This is a test notification from PiFire."},
            id="test-notify",
        ),
        pytest.param(
            "Control_Process_Stopped",
            "Control Process Stopped!",
            "The control process has encountered an issue and has been stopped. "
            "Check on your grill as soon as possible to prevent damage!",
            True,
            "pifire_error_alerts",
            {"value1": "Control Process Stopped"},
            id="control-process-stopped",
        ),
        pytest.param(
            "Thermocouple_Fault_Primary",
            "Primary Thermocouple Fault!",
            "Primary thermocouple fault detected. PiFire is shutting down because "
            "the control temperature is unavailable.",
            True,
            "pifire_error_alerts",
            {"value1": "Primary thermocouple fault"},
            id="thermocouple-fault-primary",
        ),
        pytest.param(
            "Thermocouple_Fault_Primary_Observed",
            "Primary Thermocouple Fault Observed!",
            "A control-probe thermocouple fault was detected. Observe mode did not stop heating.",
            True,
            "pifire_error_alerts",
            {"value1": "Primary thermocouple fault observed"},
            id="thermocouple-fault-primary-observed",
        ),
        pytest.param(
            "Thermocouple_Fault_Secondary",
            "Thermocouple Fault!",
            "A food or auxiliary thermocouple fault was detected. The affected probe "
            "is unavailable; grill control continues.",
            True,
            "pifire_error_alerts",
            {"value1": "Secondary thermocouple fault"},
            id="thermocouple-fault-secondary",
        ),
        pytest.param(
            "Zzz",
            "PiFire: Unknown Notification issue",
            "Whoops! PiFire had the following unhandled notify event: Zzz at ",
            False,
            "default",
            {"value1": "Unknown Notification issue"},
            id="unmatched-falls-back",
        ),
    ],
)
def test_notification_event(monkeypatch, event, title, body, exact, channel, query_args):
    rec = _capture(monkeypatch, event)

    assert rec["title"] == title
    if exact:
        assert rec["body"] == body
    else:
        assert rec["body"].startswith(body)
    assert rec["channel"] == channel
    assert rec["query_args"] == query_args


def test_pellet_level_low(monkeypatch):
    rec = _capture(monkeypatch, "Pellet_Level_Low")
    assert rec["title"] == "Low Pellet Level"
    assert rec["body"] == "Your pellet level is currently at 42%"
    assert rec["channel"] == "pifire_pellet_alerts"
    assert rec["query_args"] == {"value1": rec["body"]}


def test_grill_error_00_is_dropped_and_falls_back(monkeypatch, caplog):
    # Behavior change: Grill_Error_00 is a dead, never-emitted event and is
    # dropped from EVENTS -- it now routes to the Unknown-Notification
    # fallback, logged at ERROR.
    with caplog.at_level("ERROR", logger="events"):
        rec = _capture(monkeypatch, "Grill_Error_00")
    assert rec["title"] == "PiFire: Unknown Notification issue"
    assert rec["body"].startswith("Whoops! PiFire had the following unhandled notify event: Grill_Error_00 at ")
    assert rec["channel"] == "default"
    assert rec["query_args"] == {"value1": "Unknown Notification issue"}
    assert any(r.levelname == "ERROR" for r in caplog.records)


def test_grill_warning_is_dropped_and_falls_back(monkeypatch, caplog):
    # Behavior change: Grill_Warning is a dead, never-emitted event and is
    # dropped from EVENTS -- it now routes to the Unknown-Notification
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


class _FakeAppriseHandler:
    """Records .add(...) URLs and .notify(...) kwargs; never touches the network."""

    def __init__(self):
        self.added_urls = []
        self.notify_calls = []

    def add(self, url):
        self.added_urls.append(url)

    def notify(self, title, body):
        self.notify_calls.append({"title": title, "body": body})
        return True


def test_send_pushover_notification_builds_apprise_urls(monkeypatch):
    fake = _FakeAppriseHandler()
    monkeypatch.setattr(N.apprise, "Apprise", lambda: fake)
    settings = _base_settings()
    settings["notify_services"]["pushover"] = {
        "APIKey": "tok",
        "UserKeys": "u1, u2",
        "PublicURL": "http://x",
        "enabled": True,
    }

    N._send_pushover_notification(settings, "Title", "Body")

    assert fake.added_urls == ["pover://u1@tok?url=http://x", "pover://u2@tok?url=http://x"]
    assert fake.notify_calls == [{"title": "Title", "body": "Body"}]


def test_send_pushbullet_notification_builds_apprise_url(monkeypatch):
    fake = _FakeAppriseHandler()
    monkeypatch.setattr(N.apprise, "Apprise", lambda: fake)
    settings = _base_settings()
    settings["notify_services"]["pushbullet"] = {
        "APIKey": "k",
        "PublicURL": "http://y",
        "enabled": True,
    }

    N._send_pushbullet_notification(settings, "Title", "Body")

    assert fake.added_urls == ["pbul://k@k?url=http://y"]
    assert fake.notify_calls == [{"title": "Title", "body": "Body"}]
