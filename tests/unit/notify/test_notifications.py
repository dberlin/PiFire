"""Coverage-focused tests for notify.notifications -- the central alert/
notification dispatch hub.

This file complements test_notifications_events.py (which pins the EVENTS
table / send_notifications message-building + basic fan-out) and
test_apprise_result_logging.py (which pins _send_apprise_notifications'
success/failure/exception logging). It adds:

  - check_notify(): the main polling dispatch/gating loop (mqtt forward,
    early-return guard, influxdb/wled forwarding, per-notify_data-item
    branches: probe/probe_limit_high/probe_limit_low/timer/hopper/test,
    and the shutdown/keep_warm/reignite mode-transition tail).
  - send_notifications()'s remaining channel gates not covered by the
    sibling file: apprise, pushbullet, pushover, mqtt, wled.
  - The individual per-channel senders: _send_onesignal_notification,
    _send_ifttt_notification, _send_influxdb_notification,
    _send_mqtt_notification, _send_wled_notification.
  - Pure helpers: _check_condition, _smooth_temperatures, _estimate_eta.

Every external boundary (requests, apprise, the mqtt/wled/influxdb handler
classes, the datastore accessors) is mocked. No real network call, MQTT
broker, or WLED device is ever touched.

LATENT BUG (documented, not fixed -- see test_onesignal_invalid_player_id_cleanup_uses_wrong_settings_key):
notify/notifications.py:483-489 -- the OneSignal invalid-player-id cleanup
reads/writes `settings["onesignal"]["devices"]`, but every other access in
this function (and the whole app's settings schema) nests OneSignal config
under `settings["notify_services"]["onesignal"]`. Production settings.json
has no top-level "onesignal" key, so this raises KeyError, which is
swallowed by the enclosing `except Exception`. Net effect: OneSignal's
automatic invalid-device cleanup silently never runs, and a real HTTP
success gets logged as "OneSignal Notification failed: 'onesignal'"
instead -- misleading anyone reading logs. Severity: medium (feature
dead, error message misdirects).
"""

import math
from unittest.mock import MagicMock

import pytest

import notify.notifications as N
from common.modes import Mode


@pytest.fixture(autouse=True)
def _reset_handler_singletons():
    """Reset the module-level cached handler singletons so tests don't leak
    fakes into each other via the `if not <handler>:` cache-init guard."""
    N.influx_handler = None
    N.mqtt = None
    N.wled_handler = None
    yield
    N.influx_handler = None
    N.mqtt = None
    N.wled_handler = None


# ---------------------------------------------------------------------------
# _check_condition
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "condition,current,target,expected",
    [
        ("equal", 100, 100, True),
        ("equal", 100, 101, False),
        ("above", 101, 100, True),
        ("above", 100, 100, False),
        ("below", 99, 100, True),
        ("below", 100, 100, False),
        ("equal_above", 100, 100, True),
        ("equal_above", 101, 100, True),
        ("equal_above", 99, 100, False),
        ("equal_below", 100, 100, True),
        ("equal_below", 99, 100, True),
        ("equal_below", 101, 100, False),
        ("bogus_condition", 100, 100, False),
    ],
)
def test_check_condition(condition, current, target, expected):
    assert N._check_condition(condition, current, target) is expected


# ---------------------------------------------------------------------------
# _smooth_temperatures
# ---------------------------------------------------------------------------


def test_smooth_temperatures_shorter_than_window_returns_copy():
    temps = [1, 2]
    result = N._smooth_temperatures(temps, window_size=3)
    assert result == [1, 2]
    assert result is not temps  # a copy, not the same list object


def test_smooth_temperatures_applies_moving_average():
    temps = [10, 10, 10, 50, 10, 10, 10]
    result = N._smooth_temperatures(temps, window_size=3)
    # First/last (window_size // 2 = 1) points unchanged; middle points
    # averaged over a 3-wide window centered on each index.
    assert result[0] == 10
    assert result[-1] == 10
    assert result[1] == pytest.approx(10)
    assert result[2] == pytest.approx((10 + 10 + 50) / 3)
    assert result[3] == pytest.approx((10 + 50 + 10) / 3)
    assert result[4] == pytest.approx((50 + 10 + 10) / 3)
    assert result[5] == pytest.approx(10)
    assert len(result) == len(temps)


# ---------------------------------------------------------------------------
# _estimate_eta
# ---------------------------------------------------------------------------


def test_estimate_eta_target_already_reached_returns_none():
    assert N._estimate_eta([60, 70, 80], target_temperature=50) is None


def test_estimate_eta_interval_out_of_range_returns_none():
    assert N._estimate_eta([1, 2, 3], target_temperature=999, interval_seconds=0) is None
    assert N._estimate_eta([1, 2, 3], target_temperature=999, interval_seconds=61) is None


def test_estimate_eta_insufficient_history_returns_none():
    # min_history_minutes=1 default => needs 20 points at interval=3s; give 5.
    temps = [100, 101, 102, 103, 104]
    assert N._estimate_eta(temps, target_temperature=300, interval_seconds=3) is None


def test_estimate_eta_non_increasing_slope_returns_none():
    # Decreasing temperatures: target is not yet reached, but the trend can
    # never get there -- slope <= 0 branch.
    temps = list(range(200, 175, -1))  # 25 points, strictly decreasing
    assert len(temps) >= 20
    assert N._estimate_eta(temps, target_temperature=300, interval_seconds=3) is None


def test_estimate_eta_rising_trend_returns_positive_int():
    temps = [100 + 5 * i for i in range(25)]  # rising linearly, 25 points
    eta = N._estimate_eta(temps, target_temperature=300, interval_seconds=3)
    assert isinstance(eta, int)
    assert eta >= 0


def test_estimate_eta_exception_inside_try_is_caught(monkeypatch):
    monkeypatch.setattr(N, "_smooth_temperatures", MagicMock(side_effect=RuntimeError("boom")))
    temps = [100 + 5 * i for i in range(25)]
    assert N._estimate_eta(temps, target_temperature=300, interval_seconds=3) is None


def test_estimate_eta_zero_denominator_returns_none():
    # A single data point (min_history_minutes/interval tuned so 1 point
    # clears the min-data-points bar) makes the weighted-x variance zero
    # -> denominator == 0 -> explicit "cannot calculate slope" guard.
    eta = N._estimate_eta(
        [100], target_temperature=200, interval_seconds=60, max_history_minutes=5, min_history_minutes=1
    )
    assert eta is None


def test_estimate_eta_nan_or_inf_prediction_returns_none(monkeypatch):
    # Force the slope/intercept math to yield an infinite prediction without
    # needing a pathological natural dataset.
    temps = [100 + 5 * i for i in range(25)]
    monkeypatch.setattr(N.math, "isinf", lambda x: True)
    assert N._estimate_eta(temps, target_temperature=300, interval_seconds=3) is None


# ---------------------------------------------------------------------------
# _send_onesignal_notification
# ---------------------------------------------------------------------------


def _onesignal_settings(devices=None, app_id="app123"):
    return {
        "notify_services": {
            "onesignal": {
                "app_id": app_id,
                "devices": devices or {},
            }
        }
    }


def test_onesignal_no_devices_registered_warns(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(N, "_event_logger", lambda settings: logger)
    settings = _onesignal_settings(devices={})

    N._send_onesignal_notification(settings, "Title", "Body", "channel1")

    assert any("No Devices Registered" in str(c.args[0]) for c in logger.warning.call_args_list)


def test_onesignal_success_posts_expected_payload(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(N, "_event_logger", lambda settings: logger)
    settings = _onesignal_settings(devices={"dev1": {"device_name": "Phone"}})

    resp = MagicMock()
    resp.status_code = 200
    resp.text = "ok"
    resp.json.return_value = {}
    mock_post = MagicMock(return_value=resp)
    monkeypatch.setattr(N.requests, "post", mock_post)

    N._send_onesignal_notification(settings, "Title", "Body", "chan")

    mock_post.assert_called_once()
    url, kwargs = mock_post.call_args.args[0], mock_post.call_args.kwargs
    assert url == "https://onesignal.com/api/v1/notifications"
    payload = N.json.loads(kwargs["data"])
    assert payload["app_id"] == "app123"
    assert payload["include_player_ids"] == ["dev1"]
    assert payload["headings"] == {"en": "Title"}
    assert payload["contents"] == {"en": "Body"}
    assert payload["existing_android_channel_id"] == "chan"
    assert not any("Failed" in str(c.args[0]) for c in logger.warning.call_args_list)


def test_onesignal_non_200_logs_warning(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(N, "_event_logger", lambda settings: logger)
    settings = _onesignal_settings(devices={"dev1": {"device_name": "Phone"}})

    resp = MagicMock()
    resp.status_code = 400
    resp.text = "bad request"
    resp.json.return_value = {}
    monkeypatch.setattr(N.requests, "post", MagicMock(return_value=resp))

    N._send_onesignal_notification(settings, "Title", "Body", "chan")

    assert any("OneSignal Notification Failed" in str(c.args[0]) for c in logger.warning.call_args_list)


def test_onesignal_generic_exception_is_caught_and_logged(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(N, "_event_logger", lambda settings: logger)
    settings = _onesignal_settings(devices={"dev1": {"device_name": "Phone"}})
    monkeypatch.setattr(N.requests, "post", MagicMock(side_effect=RuntimeError("network down")))

    N._send_onesignal_notification(settings, "Title", "Body", "chan")

    assert any("OneSignal Notification failed" in str(c.args[0]) for c in logger.warning.call_args_list)


def test_onesignal_invalid_player_id_cleanup_uses_wrong_settings_key(monkeypatch):
    """LATENT BUG (see module docstring): the invalid_player_ids cleanup
    block reads/writes settings["onesignal"]["devices"] instead of
    settings["notify_services"]["onesignal"]["devices"]. Production
    settings have no top-level "onesignal" key, so this KeyErrors and is
    swallowed by the surrounding `except Exception`, meaning:
      1. write_settings() is never actually called to prune the device.
      2. A real HTTP 200 success gets mis-logged as a generic failure.
    This test pins that exact (buggy) behavior rather than "fixing" it.
    notify/notifications.py:483-489.
    """
    logger = MagicMock()
    monkeypatch.setattr(N, "_event_logger", lambda settings: logger)
    write_settings_mock = MagicMock()
    monkeypatch.setattr(N, "write_settings", write_settings_mock)

    # Realistic settings shape: onesignal only exists under notify_services.
    settings = _onesignal_settings(devices={"bad-device": {"device_name": "Old Phone"}})
    assert "onesignal" not in settings  # top-level key absent, as in production

    resp = MagicMock()
    resp.status_code = 200
    resp.text = "ok"
    resp.json.return_value = {"errors": {"invalid_player_ids": ["bad-device"]}}
    monkeypatch.setattr(N.requests, "post", MagicMock(return_value=resp))

    # Must not raise -- the KeyError is caught internally.
    N._send_onesignal_notification(settings, "Title", "Body", "chan")

    # The cleanup never actually ran: write_settings was never called, and
    # the device is still present in the "real" (notify_services-nested) config.
    write_settings_mock.assert_not_called()
    assert "bad-device" in settings["notify_services"]["onesignal"]["devices"]
    # The HTTP call *succeeded* (status 200) yet gets logged as a failure.
    assert any("OneSignal Notification failed" in str(c.args[0]) for c in logger.warning.call_args_list)


def test_onesignal_invalid_player_id_cleanup_succeeds_if_schema_matched(monkeypatch):
    """Covers the cleanup's "success" branch (info log + pop + write_settings)
    that only executes if settings *did* carry a matching top-level
    "onesignal" key -- which real settings.json never does (see the bug test
    above). Included purely to exercise these lines; not representative of
    production data."""
    logger = MagicMock()
    monkeypatch.setattr(N, "_event_logger", lambda settings: logger)
    write_settings_mock = MagicMock()
    monkeypatch.setattr(N, "write_settings", write_settings_mock)

    settings = _onesignal_settings(devices={"bad-device": {"device_name": "Old Phone"}})
    settings["onesignal"] = {"devices": {"bad-device": {"device_name": "Old Phone"}}}

    resp = MagicMock()
    resp.status_code = 200
    resp.text = "ok"
    resp.json.return_value = {"errors": {"invalid_player_ids": ["bad-device"]}}
    monkeypatch.setattr(N.requests, "post", MagicMock(return_value=resp))

    N._send_onesignal_notification(settings, "Title", "Body", "chan")

    write_settings_mock.assert_called_once()
    assert "bad-device" not in settings["onesignal"]["devices"]
    assert any("invalid id and has been removed" in str(c.args[0]) for c in logger.info.call_args_list)


def test_onesignal_bare_except_branch_is_reachable(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(N, "_event_logger", lambda settings: logger)
    monkeypatch.setattr(N.requests, "post", MagicMock(side_effect=BaseException("not an Exception subclass")))
    settings = _onesignal_settings(devices={"dev1": {"device_name": "Phone"}})

    N._send_onesignal_notification(settings, "Title", "Body", "chan")

    assert any("unknown reason" in str(c.args[0]).lower() for c in logger.warning.call_args_list)


# ---------------------------------------------------------------------------
# _send_ifttt_notification
# ---------------------------------------------------------------------------


def test_ifttt_success_posts_expected_url(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(N, "_event_logger", lambda settings: logger)
    resp = MagicMock()
    resp.text = "Congratulations!"
    mock_post = MagicMock(return_value=resp)
    monkeypatch.setattr(N.requests, "post", mock_post)

    settings = {"notify_services": {"ifttt": {"APIKey": "mykey"}}}
    N._send_ifttt_notification(settings, "Test_Notify", {"value1": "hi"})

    mock_post.assert_called_once_with(
        "https://maker.ifttt.com/trigger/Test_Notify/with/key/mykey", data={"value1": "hi"}
    )
    assert any("IFTTT Notification Success" in str(c.args[0]) for c in logger.info.call_args_list)


def test_ifttt_request_exception_logs_warning(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(N, "_event_logger", lambda settings: logger)
    monkeypatch.setattr(N.requests, "post", MagicMock(side_effect=RuntimeError("down")))

    settings = {"notify_services": {"ifttt": {"APIKey": "mykey"}}}
    N._send_ifttt_notification(settings, "Test_Notify", {"value1": "hi"})

    assert any("IFTTT Notification Failed" in str(c.args[0]) for c in logger.warning.call_args_list)


# ---------------------------------------------------------------------------
# _send_apprise_notifications -- "no locations" branch (else at end of func)
# ---------------------------------------------------------------------------


def test_apprise_no_locations_configured_warns(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(N, "_event_logger", lambda settings: logger)
    settings = {"notify_services": {"apprise": {"locations": [], "enabled": True}}}

    N._send_apprise_notifications(settings, "Title", "Body")

    assert any("No Apprise Locations Configured" in str(c.args[0]) for c in logger.warning.call_args_list)


def test_apprise_bare_except_branch_is_reachable(monkeypatch):
    """The generic `except Exception` clause catches virtually everything, so
    the trailing bare `except:` is normally dead. Prove it's still reachable
    (and still safe) by raising something that does NOT subclass Exception."""
    logger = MagicMock()
    monkeypatch.setattr(N, "_event_logger", lambda settings: logger)
    handler = MagicMock()
    handler.notify.side_effect = BaseException("not an Exception subclass")
    monkeypatch.setattr(N.apprise, "Apprise", lambda: handler)
    settings = {"notify_services": {"apprise": {"locations": ["json://x"], "enabled": True}}}

    N._send_apprise_notifications(settings, "Title", "Body")

    assert any("unknown reason" in str(c.args[0]).lower() for c in logger.warning.call_args_list)


def test_apprise_url_bare_except_branch_is_reachable(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(N, "_event_logger", lambda settings: logger)
    handler = MagicMock()
    handler.notify.side_effect = BaseException("not an Exception subclass")
    monkeypatch.setattr(N.apprise, "Apprise", lambda: handler)
    settings = {"notify_services": {"apprise": {"locations": [], "enabled": True}}}

    N._send_apprise_url(settings, ["json://x"], "Title", "Body", "SomeService")

    assert any("unknown reason" in str(c.args[0]).lower() for c in logger.warning.call_args_list)


def test_apprise_url_result_false_logs_warning(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(N, "_event_logger", lambda settings: logger)
    handler = MagicMock()
    handler.notify.return_value = False
    monkeypatch.setattr(N.apprise, "Apprise", lambda: handler)
    settings = {"notify_services": {"apprise": {"locations": [], "enabled": True}}}

    N._send_apprise_url(settings, ["json://x"], "Title", "Body", "SomeService")

    assert any("SomeService Notification failed!" in str(c.args[0]) for c in logger.warning.call_args_list)


def test_apprise_url_regular_exception_logs_warning(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(N, "_event_logger", lambda settings: logger)
    handler = MagicMock()
    handler.notify.side_effect = RuntimeError("boom")
    monkeypatch.setattr(N.apprise, "Apprise", lambda: handler)
    settings = {"notify_services": {"apprise": {"locations": [], "enabled": True}}}

    N._send_apprise_url(settings, ["json://x"], "Title", "Body", "SomeService")

    assert any("SomeService Notification failed: boom" in str(c.args[0]) for c in logger.warning.call_args_list)


# ---------------------------------------------------------------------------
# _send_influxdb_notification
# ---------------------------------------------------------------------------


def test_send_influxdb_notification_constructs_and_caches_handler(monkeypatch):
    fake_handler = MagicMock()
    fake_cls = MagicMock(return_value=fake_handler)
    monkeypatch.setattr("notify.influxdb_handler.InfluxNotificationHandler", fake_cls)

    settings = {"notify_services": {"influxdb": {"url": "http://x", "enabled": True}}}
    control, pelletdb, in_data, grill_platform = {"mode": Mode.SMOKE}, {"current": {}}, {}, MagicMock()

    N._send_influxdb_notification("GRILL_STATE", control, settings, pelletdb, in_data, grill_platform)
    N._send_influxdb_notification("GRILL_STATE", control, settings, pelletdb, in_data, grill_platform)

    fake_cls.assert_called_once_with(settings)  # only constructed once (cached)
    assert fake_handler.notify.call_count == 2
    fake_handler.notify.assert_called_with("GRILL_STATE", control, settings, pelletdb, in_data, grill_platform)


# ---------------------------------------------------------------------------
# _send_mqtt_notification
# ---------------------------------------------------------------------------


def _mqtt_fake_handler():
    handler = MagicMock()
    handler.last_mode = Mode.STARTUP
    handler.pub_times = {"pid": 0, "pellet": 0, "base": 0}
    handler.pub_rate = 30
    return handler


def test_send_mqtt_notification_constructs_and_caches_handler(monkeypatch):
    fake_handler = _mqtt_fake_handler()
    fake_cls = MagicMock(return_value=fake_handler)
    monkeypatch.setattr("notify.mqtt_handler.MqttNotificationHandler", fake_cls)

    settings = {"notify_services": {"mqtt": {"broker": "b", "enabled": True}}}
    control = {"mode": Mode.SMOKE}

    N._send_mqtt_notification(control, settings)
    N._send_mqtt_notification(control, settings)

    fake_cls.assert_called_once_with(settings)


def test_send_mqtt_notification_immediate_event_publishes_notify_event(monkeypatch):
    fake_handler = _mqtt_fake_handler()
    monkeypatch.setattr("notify.mqtt_handler.MqttNotificationHandler", MagicMock(return_value=fake_handler))
    settings = {"notify_services": {"mqtt": {"broker": "b", "enabled": True}}}
    control = {"mode": fake_handler.last_mode}  # mode unchanged -> only notify_event should fire eagerly

    N._send_mqtt_notification(control, settings, notify_event="Test_Notify")

    fake_handler.notify.assert_any_call("notify_event", {"msg": "Test_Notify"})


def test_send_mqtt_notification_mode_change_publishes_pid_pellet_control_devices_probes(monkeypatch):
    fake_handler = _mqtt_fake_handler()
    monkeypatch.setattr("notify.mqtt_handler.MqttNotificationHandler", MagicMock(return_value=fake_handler))
    settings = {"notify_services": {"mqtt": {"broker": "b", "enabled": True}}}
    control = {"mode": Mode.SMOKE}  # differs from fake_handler.last_mode -> mode_changed True
    pelletdb = {"current": {"hopper_level": 50}}
    pid_data = {"PB": 10}
    in_data = {"probe_history": {"F": {"Grill": 200}}}
    grill_platform = MagicMock(current={"auger": True})

    N._send_mqtt_notification(
        control, settings, pelletdb=pelletdb, in_data=in_data, grill_platform=grill_platform, pid_data=pid_data
    )

    fake_handler.notify.assert_any_call("pid", pid_data)
    fake_handler.notify.assert_any_call("pellet", pelletdb["current"])
    fake_handler.notify.assert_any_call("control", control)
    fake_handler.notify.assert_any_call("system", control)
    fake_handler.notify.assert_any_call("devices", grill_platform.current)
    fake_handler.notify.assert_any_call("probe_data", in_data["probe_history"])


def test_send_mqtt_notification_unchanged_mode_within_pub_rate_skips_base_publish(monkeypatch):
    fake_handler = _mqtt_fake_handler()
    now = N.time.time()
    fake_handler.pub_times = {"pid": now, "pellet": now, "base": now}
    monkeypatch.setattr("notify.mqtt_handler.MqttNotificationHandler", MagicMock(return_value=fake_handler))
    settings = {"notify_services": {"mqtt": {"broker": "b", "enabled": True}}}
    control = {"mode": fake_handler.last_mode}  # unchanged

    N._send_mqtt_notification(control, settings, pelletdb={"current": {}}, pid_data={"PB": 1})

    control_calls = [c for c in fake_handler.notify.call_args_list if c.args[0] == "control"]
    assert control_calls == []


# ---------------------------------------------------------------------------
# _send_wled_notification
# ---------------------------------------------------------------------------


def test_send_wled_notification_constructs_and_caches_handler(monkeypatch):
    fake_handler = MagicMock()
    fake_cls = MagicMock(return_value=fake_handler)
    monkeypatch.setattr("notify.wled_handler.WLEDNotificationHandler", fake_cls)

    settings = {"notify_services": {"wled": {"device_address": "1.2.3.4", "enabled": True}}}
    control = {"mode": Mode.SMOKE}

    N._send_wled_notification("GRILL_STATE", control, settings)
    N._send_wled_notification("Test_Notify", control, settings)

    fake_cls.assert_called_once_with(settings)  # constructed once, cached
    fake_handler.notify.assert_any_call("GRILL_STATE", control, settings)
    fake_handler.notify.assert_any_call("Test_Notify", control, settings)


# ---------------------------------------------------------------------------
# send_notifications: remaining channel gates (apprise/pushbullet/pushover/
# mqtt/wled) not exercised by test_notifications_events.py
# ---------------------------------------------------------------------------


def _gating_settings(**enabled_overrides):
    settings = {
        "globals": {"debug_mode": False, "units": "F"},
        "safety": {"maxtemp": 550},
        "notify_services": {
            "apprise": {"locations": "", "enabled": False},
            "ifttt": {"APIKey": "", "enabled": False},
            "pushbullet": {"APIKey": "", "PublicURL": "", "enabled": False},
            "pushover": {"APIKey": "", "UserKeys": "", "PublicURL": "", "enabled": False},
            "onesignal": {"app_id": "", "devices": {}, "enabled": False},
            "mqtt": {"broker": "", "enabled": False},
            "wled": {"device_address": "", "enabled": False},
        },
    }
    for key, value in enabled_overrides.items():
        settings["notify_services"][key]["enabled"] = True
    return settings


def _patch_common(monkeypatch, settings, control=None, pelletdb=None):
    control = control if control is not None else {"safety": {"startuptemp": 100}, "recipe": {}}
    pelletdb = pelletdb if pelletdb is not None else {"current": {"hopper_level": 42}}
    monkeypatch.setattr(N, "read_settings", lambda *a, **k: settings)
    monkeypatch.setattr(N, "read_control", lambda *a, **k: control)
    monkeypatch.setattr(N, "read_pellet_db", lambda *a, **k: pelletdb)


def test_send_notifications_apprise_channel_fires_when_locations_and_enabled(monkeypatch):
    settings = _gating_settings(apprise=True)
    settings["notify_services"]["apprise"]["locations"] = "json://x"
    _patch_common(monkeypatch, settings)
    apprise_mock = MagicMock()
    monkeypatch.setattr(N, "_send_apprise_notifications", apprise_mock)
    for name in (
        "_send_ifttt_notification",
        "_send_pushbullet_notification",
        "_send_pushover_notification",
        "_send_onesignal_notification",
        "_send_mqtt_notification",
        "_send_wled_notification",
    ):
        monkeypatch.setattr(N, name, MagicMock())

    N.send_notifications("Test_Notify")

    apprise_mock.assert_called_once()


def test_send_notifications_pushbullet_channel_fires_when_key_and_enabled(monkeypatch):
    settings = _gating_settings(pushbullet=True)
    settings["notify_services"]["pushbullet"]["APIKey"] = "k"
    _patch_common(monkeypatch, settings)
    pushbullet_mock = MagicMock()
    monkeypatch.setattr(N, "_send_pushbullet_notification", pushbullet_mock)
    for name in (
        "_send_apprise_notifications",
        "_send_ifttt_notification",
        "_send_pushover_notification",
        "_send_onesignal_notification",
        "_send_mqtt_notification",
        "_send_wled_notification",
    ):
        monkeypatch.setattr(N, name, MagicMock())

    N.send_notifications("Test_Notify")

    pushbullet_mock.assert_called_once()


def test_send_notifications_pushover_channel_fires_when_key_userkeys_and_enabled(monkeypatch):
    settings = _gating_settings(pushover=True)
    settings["notify_services"]["pushover"]["APIKey"] = "k"
    settings["notify_services"]["pushover"]["UserKeys"] = "u1"
    _patch_common(monkeypatch, settings)
    pushover_mock = MagicMock()
    monkeypatch.setattr(N, "_send_pushover_notification", pushover_mock)
    for name in (
        "_send_apprise_notifications",
        "_send_ifttt_notification",
        "_send_pushbullet_notification",
        "_send_onesignal_notification",
        "_send_mqtt_notification",
        "_send_wled_notification",
    ):
        monkeypatch.setattr(N, name, MagicMock())

    N.send_notifications("Test_Notify")

    pushover_mock.assert_called_once()


def test_send_notifications_pushover_missing_userkeys_does_not_fire(monkeypatch):
    settings = _gating_settings(pushover=True)
    settings["notify_services"]["pushover"]["APIKey"] = "k"
    settings["notify_services"]["pushover"]["UserKeys"] = ""  # blank -> gate fails
    _patch_common(monkeypatch, settings)
    pushover_mock = MagicMock()
    monkeypatch.setattr(N, "_send_pushover_notification", pushover_mock)
    for name in (
        "_send_apprise_notifications",
        "_send_ifttt_notification",
        "_send_pushbullet_notification",
        "_send_onesignal_notification",
        "_send_mqtt_notification",
        "_send_wled_notification",
    ):
        monkeypatch.setattr(N, name, MagicMock())

    N.send_notifications("Test_Notify")

    pushover_mock.assert_not_called()


def test_send_notifications_mqtt_channel_fires_and_rereads_control(monkeypatch):
    settings = _gating_settings(mqtt=True)
    settings["notify_services"]["mqtt"]["broker"] = "broker.local"
    read_control_calls = {"count": 0}

    def _read_control(*a, **k):
        read_control_calls["count"] += 1
        return {"safety": {"startuptemp": 100}, "recipe": {}}

    monkeypatch.setattr(N, "read_control", _read_control)
    monkeypatch.setattr(N, "read_settings", lambda *a, **k: settings)
    monkeypatch.setattr(N, "read_pellet_db", lambda *a, **k: {"current": {"hopper_level": 42}})
    mqtt_mock = MagicMock()
    monkeypatch.setattr(N, "_send_mqtt_notification", mqtt_mock)
    for name in (
        "_send_apprise_notifications",
        "_send_ifttt_notification",
        "_send_pushbullet_notification",
        "_send_pushover_notification",
        "_send_onesignal_notification",
        "_send_wled_notification",
    ):
        monkeypatch.setattr(N, name, MagicMock())

    N.send_notifications("Test_Notify")

    mqtt_mock.assert_called_once()
    assert mqtt_mock.call_args.kwargs.get("notify_event") == "Test Notification"
    # send_notifications re-reads control specifically for the mqtt branch.
    assert read_control_calls["count"] >= 2


def test_send_notifications_wled_channel_fires_when_address_and_enabled(monkeypatch):
    settings = _gating_settings(wled=True)
    settings["notify_services"]["wled"]["device_address"] = "1.2.3.4"
    _patch_common(monkeypatch, settings)
    wled_mock = MagicMock()
    monkeypatch.setattr(N, "_send_wled_notification", wled_mock)
    for name in (
        "_send_apprise_notifications",
        "_send_ifttt_notification",
        "_send_pushbullet_notification",
        "_send_pushover_notification",
        "_send_onesignal_notification",
        "_send_mqtt_notification",
    ):
        monkeypatch.setattr(N, name, MagicMock())

    N.send_notifications("Test_Notify")

    wled_mock.assert_called_once_with("Test_Notify", N.read_control(), settings)


def test_send_notifications_all_channels_disabled_none_fire(monkeypatch):
    settings = _gating_settings()  # nothing enabled
    _patch_common(monkeypatch, settings)
    mocks = {}
    for name in (
        "_send_apprise_notifications",
        "_send_ifttt_notification",
        "_send_pushbullet_notification",
        "_send_pushover_notification",
        "_send_onesignal_notification",
        "_send_mqtt_notification",
        "_send_wled_notification",
    ):
        mocks[name] = MagicMock()
        monkeypatch.setattr(N, name, mocks[name])

    N.send_notifications("Test_Notify")

    for name, mock_obj in mocks.items():
        mock_obj.assert_not_called()


# ---------------------------------------------------------------------------
# check_notify(): the main polling dispatch loop
# ---------------------------------------------------------------------------


def _cn_settings(mqtt_enabled=False, influxdb_enabled=False, wled_enabled=False):
    return {
        "notify_services": {
            "mqtt": {"enabled": mqtt_enabled},
            "influxdb": {"url": "http://x" if influxdb_enabled else "", "enabled": influxdb_enabled},
            "wled": {"device_address": "1.2.3.4" if wled_enabled else "", "enabled": wled_enabled},
        },
        "pelletlevel": {"warning_time": 20, "warning_level": 25},
        "keep_warm": {"temp": 165, "s_plus": False},
    }


def _cn_control(notify_data=None, mode=Mode.SMOKE):
    return {
        "mode": mode,
        "notify_data": notify_data or [],
        "recipe": {"step_data": {"trigger_temps": {}, "triggered": False, "timer": 0, "message": ""}},
        "timer": {"start": 0, "paused": 0, "end": 0},
        "safety": {},
        "updated": False,
        "primary_setpoint": 0,
        "s_plus": False,
    }


def _patch_check_notify_deps(monkeypatch, write_control_mock=None, send_notifications_mock=None):
    monkeypatch.setattr(N, "write_control", write_control_mock or MagicMock())
    monkeypatch.setattr(N, "send_notifications", send_notifications_mock or MagicMock())


def test_check_notify_forwards_to_mqtt_when_enabled(monkeypatch):
    settings = _cn_settings(mqtt_enabled=True)
    control = _cn_control()
    mqtt_mock = MagicMock()
    monkeypatch.setattr(N, "_send_mqtt_notification", mqtt_mock)

    result = N.check_notify(settings, control)  # no pelletdb/grill_platform -> early return after mqtt

    mqtt_mock.assert_called_once_with(control, settings, None, None, None, None)
    assert result is None  # early-return path returns None implicitly


def test_check_notify_early_returns_without_pelletdb_or_grill_platform(monkeypatch):
    settings = _cn_settings()
    control = _cn_control()
    influx_mock = MagicMock()
    wled_mock = MagicMock()
    monkeypatch.setattr(N, "_send_influxdb_notification", influx_mock)
    monkeypatch.setattr(N, "_send_wled_notification", wled_mock)

    N.check_notify(settings, control, pelletdb=None, grill_platform=None)

    influx_mock.assert_not_called()
    wled_mock.assert_not_called()


def test_check_notify_forwards_to_influxdb_and_wled_when_configured(monkeypatch):
    settings = _cn_settings(influxdb_enabled=True, wled_enabled=True)
    control = _cn_control()
    influx_mock = MagicMock()
    wled_mock = MagicMock()
    monkeypatch.setattr(N, "_send_influxdb_notification", influx_mock)
    monkeypatch.setattr(N, "_send_wled_notification", wled_mock)

    N.check_notify(settings, control, pelletdb={"current": {}}, grill_platform=MagicMock())

    influx_mock.assert_called_once()
    wled_mock.assert_called_once()


def test_check_notify_probe_condition_met_sends_and_clears_request(monkeypatch):
    settings = _cn_settings()
    item = {
        "req": True,
        "type": "probe",
        "label": "Probe1",
        "condition": "equal_above",
        "target": 200,
        "shutdown": False,
        "keep_warm": False,
    }
    control = _cn_control(notify_data=[item])
    send_mock = MagicMock()
    _patch_check_notify_deps(monkeypatch, send_notifications_mock=send_mock)

    in_data = {
        "probe_history": {"F": {"Probe1": 205}, "tr": {"ignored": 999}},
        "notify_targets": {"Probe1": 200},
    }

    result = N.check_notify(settings, control, in_data=in_data, pelletdb={"current": {}}, grill_platform=MagicMock())

    send_mock.assert_called_once_with("Probe_Temp_Achieved", label="Probe1", target=200)
    assert result["notify_data"][0]["req"] is False
    assert result["notify_data"][0]["target"] == 0
    assert result["notify_data"][0]["eta"] is None


def test_check_notify_probe_recipe_mode_sets_step_triggered(monkeypatch):
    settings = _cn_settings()
    item = {
        "req": True,
        "type": "probe",
        "label": "Probe1",
        "condition": "equal_above",
        "target": 200,
        "shutdown": False,
        "keep_warm": False,
    }
    control = _cn_control(notify_data=[item], mode=Mode.RECIPE)
    control["recipe"]["step_data"]["trigger_temps"] = {"Probe1": 200}
    _patch_check_notify_deps(monkeypatch)

    in_data = {"probe_history": {"F": {"Probe1": 205}}, "notify_targets": {"Probe1": 200}}

    result = N.check_notify(settings, control, in_data=in_data, pelletdb={"current": {}}, grill_platform=MagicMock())

    assert result["recipe"]["step_data"]["triggered"] is True


def test_check_notify_probe_recipe_mode_trigger_temp_not_positive_leaves_untriggered(monkeypatch):
    # Covers the `if control["recipe"]["step_data"]["trigger_temps"][label] > 0`
    # False branch: recipe mode but this probe's configured trigger is <= 0,
    # so step_data["triggered"] must stay untouched (False).
    settings = _cn_settings()
    item = {
        "req": True,
        "type": "probe",
        "label": "Probe1",
        "condition": "equal_above",
        "target": 200,
        "shutdown": False,
        "keep_warm": False,
    }
    control = _cn_control(notify_data=[item], mode=Mode.RECIPE)
    control["recipe"]["step_data"]["trigger_temps"] = {"Probe1": 0}
    _patch_check_notify_deps(monkeypatch)

    in_data = {"probe_history": {"F": {"Probe1": 205}}, "notify_targets": {"Probe1": 200}}

    result = N.check_notify(settings, control, in_data=in_data, pelletdb={"current": {}}, grill_platform=MagicMock())

    assert result["recipe"]["step_data"]["triggered"] is False


def test_check_notify_probe_limit_high_triggers_alarm_once(monkeypatch):
    settings = _cn_settings()
    item = {
        "req": True,
        "type": "probe_limit_high",
        "label": "Grill",
        "condition": "above",
        "target": 300,
        "triggered": False,
        "shutdown": False,
        "keep_warm": False,
    }
    control = _cn_control(notify_data=[item])
    send_mock = MagicMock()
    _patch_check_notify_deps(monkeypatch, send_notifications_mock=send_mock)
    in_data = {"probe_history": {"F": {"Grill": 310}}, "notify_targets": {}}

    result = N.check_notify(settings, control, in_data=in_data, pelletdb={"current": {}}, grill_platform=MagicMock())

    send_mock.assert_called_once_with("Probe_Temp_Limit_Alarm", label="Grill", target=300)
    assert result["notify_data"][0]["triggered"] is True


def test_check_notify_probe_limit_condition_clears_when_back_in_range(monkeypatch):
    settings = _cn_settings()
    item = {
        "req": True,
        "type": "probe_limit_high",
        "label": "Grill",
        "condition": "above",
        "target": 300,
        "triggered": True,  # was already triggered
        "shutdown": False,
        "keep_warm": False,
    }
    control = _cn_control(notify_data=[item])
    send_mock = MagicMock()
    _patch_check_notify_deps(monkeypatch, send_notifications_mock=send_mock)
    in_data = {"probe_history": {"F": {"Grill": 250}}, "notify_targets": {}}  # back in range

    result = N.check_notify(settings, control, in_data=in_data, pelletdb={"current": {}}, grill_platform=MagicMock())

    send_mock.assert_not_called()
    assert result["notify_data"][0]["triggered"] is False


def test_check_notify_probe_update_eta_writes_computed_eta(monkeypatch):
    settings = _cn_settings()
    item = {
        "req": True,
        "type": "probe",
        "label": "Probe1",
        "condition": "equal_above",
        "target": 999,  # not met -> just the ETA branch runs
        "shutdown": False,
        "keep_warm": False,
    }
    control = _cn_control(notify_data=[item])
    _patch_check_notify_deps(monkeypatch)
    history = [{"F": {"Probe1": 100 + i}, "P": {}} for i in range(25)]
    monkeypatch.setattr(N, "read_history", lambda **k: history)
    monkeypatch.setattr(N, "_estimate_eta", lambda *a, **k: 123)

    in_data = {"probe_history": {"F": {"Probe1": 124}}, "notify_targets": {}}

    result = N.check_notify(
        settings, control, in_data=in_data, pelletdb={"current": {}}, grill_platform=MagicMock(), update_eta=True
    )

    assert result["notify_data"][0]["eta"] == 123


def test_check_notify_probe_update_eta_reads_p_key_when_f_key_absent(monkeypatch):
    # Covers the `elif item["label"] in datapoint["P"]` fallback -- history
    # rows can carry probe readings under "P" (e.g. a PID/percent context)
    # instead of "F".
    settings = _cn_settings()
    item = {
        "req": True,
        "type": "probe",
        "label": "Probe1",
        "condition": "equal_above",
        "target": 999,
        "shutdown": False,
        "keep_warm": False,
    }
    control = _cn_control(notify_data=[item])
    _patch_check_notify_deps(monkeypatch)
    history = [{"F": {}, "P": {"Probe1": 100 + i}} for i in range(25)]
    captured = {}
    monkeypatch.setattr(N, "read_history", lambda **k: history)

    def _fake_eta(temperatures, *a, **k):
        captured["temperatures"] = temperatures
        return 42

    monkeypatch.setattr(N, "_estimate_eta", _fake_eta)

    in_data = {"probe_history": {"F": {"Probe1": 124}}, "notify_targets": {}}

    result = N.check_notify(
        settings, control, in_data=in_data, pelletdb={"current": {}}, grill_platform=MagicMock(), update_eta=True
    )

    assert captured["temperatures"] == [100 + i for i in range(25)]
    assert result["notify_data"][0]["eta"] == 42


def test_check_notify_timer_expired_sends_and_resets_timer(monkeypatch):
    settings = _cn_settings()
    item = {"req": True, "type": "timer", "shutdown": False, "keep_warm": False}
    control = _cn_control(notify_data=[item])
    control["timer"] = {"start": 111, "paused": 0, "end": 1}  # end already in the past
    send_mock = MagicMock()
    _patch_check_notify_deps(monkeypatch, send_notifications_mock=send_mock)

    result = N.check_notify(settings, control, pelletdb={"current": {}}, grill_platform=MagicMock())

    send_mock.assert_called_once_with("Timer_Expired")
    assert result["timer"] == {"start": 0, "paused": 0, "end": 0}
    assert result["notify_data"][0]["req"] is False


def test_check_notify_timer_recipe_mode_sets_step_triggered(monkeypatch):
    settings = _cn_settings()
    item = {"req": True, "type": "timer", "shutdown": False, "keep_warm": False}
    control = _cn_control(notify_data=[item], mode=Mode.RECIPE)
    control["timer"] = {"start": 1, "paused": 0, "end": 1}
    control["recipe"]["step_data"]["timer"] = 60
    _patch_check_notify_deps(monkeypatch)

    result = N.check_notify(settings, control, pelletdb={"current": {}}, grill_platform=MagicMock())

    assert result["recipe"]["step_data"]["triggered"] is True


def test_check_notify_timer_not_yet_expired_does_not_fire(monkeypatch):
    settings = _cn_settings()
    item = {"req": True, "type": "timer", "shutdown": False, "keep_warm": False}
    control = _cn_control(notify_data=[item])
    control["timer"] = {"start": 0, "paused": 0, "end": N.time.time() + 3600}  # far future
    send_mock = MagicMock()
    _patch_check_notify_deps(monkeypatch, send_notifications_mock=send_mock)

    N.check_notify(settings, control, pelletdb={"current": {}}, grill_platform=MagicMock())

    send_mock.assert_not_called()


def test_check_notify_timer_recipe_mode_timer_not_positive_leaves_untriggered(monkeypatch):
    # Covers the `if control["recipe"]["step_data"]["timer"] > 0` False branch.
    settings = _cn_settings()
    item = {"req": True, "type": "timer", "shutdown": False, "keep_warm": False}
    control = _cn_control(notify_data=[item], mode=Mode.RECIPE)
    control["timer"] = {"start": 1, "paused": 0, "end": 1}
    control["recipe"]["step_data"]["timer"] = 0
    _patch_check_notify_deps(monkeypatch)

    result = N.check_notify(settings, control, pelletdb={"current": {}}, grill_platform=MagicMock())

    assert result["recipe"]["step_data"]["triggered"] is False


def test_check_notify_hopper_low_sends_and_updates_last_check(monkeypatch):
    settings = _cn_settings()
    item = {"req": True, "type": "hopper", "last_check": 0, "shutdown": False, "keep_warm": False}
    control = _cn_control(notify_data=[item])
    send_mock = MagicMock()
    _patch_check_notify_deps(monkeypatch, send_notifications_mock=send_mock)

    result = N.check_notify(settings, control, pelletdb={"current": {"hopper_level": 10}}, grill_platform=MagicMock())

    send_mock.assert_called_once_with("Pellet_Level_Low")
    assert result["notify_data"][0]["last_check"] > 0


def test_check_notify_hopper_within_warning_window_does_not_recheck(monkeypatch):
    settings = _cn_settings()
    item = {"req": True, "type": "hopper", "last_check": N.time.time(), "shutdown": False, "keep_warm": False}
    control = _cn_control(notify_data=[item])
    send_mock = MagicMock()
    _patch_check_notify_deps(monkeypatch, send_notifications_mock=send_mock)

    N.check_notify(settings, control, pelletdb={"current": {"hopper_level": 10}}, grill_platform=MagicMock())

    send_mock.assert_not_called()


def test_check_notify_hopper_above_warning_level_does_not_fire(monkeypatch):
    settings = _cn_settings()
    item = {"req": True, "type": "hopper", "last_check": 0, "shutdown": False, "keep_warm": False}
    control = _cn_control(notify_data=[item])
    send_mock = MagicMock()
    _patch_check_notify_deps(monkeypatch, send_notifications_mock=send_mock)

    N.check_notify(settings, control, pelletdb={"current": {"hopper_level": 90}}, grill_platform=MagicMock())

    send_mock.assert_not_called()


def test_check_notify_test_type_sends_and_clears_request(monkeypatch):
    settings = _cn_settings()
    item = {"req": True, "type": "test", "shutdown": False, "keep_warm": False}
    control = _cn_control(notify_data=[item])
    send_mock = MagicMock()
    _patch_check_notify_deps(monkeypatch, send_notifications_mock=send_mock)

    result = N.check_notify(settings, control, pelletdb={"current": {}}, grill_platform=MagicMock())

    send_mock.assert_called_once_with("Test_Notify")
    assert result["notify_data"][0]["req"] is False


def test_check_notify_item_not_requested_is_skipped(monkeypatch):
    settings = _cn_settings()
    item = {"req": False, "type": "test", "shutdown": False, "keep_warm": False}
    control = _cn_control(notify_data=[item])
    send_mock = MagicMock()
    write_mock = MagicMock()
    _patch_check_notify_deps(monkeypatch, write_control_mock=write_mock, send_notifications_mock=send_mock)

    N.check_notify(settings, control, pelletdb={"current": {}}, grill_platform=MagicMock())

    send_mock.assert_not_called()
    write_mock.assert_not_called()  # the whole item body (incl. write_control) is skipped


def test_check_notify_shutdown_flag_transitions_mode_to_shutdown(monkeypatch):
    settings = _cn_settings()
    # req starts True so the item body (incl. the shutdown/keep_warm/reignite
    # tail) actually runs; the "test" branch clears req -> False before the
    # `not control["notify_data"][index]["req"]` shutdown gate is checked.
    item = {"req": True, "type": "test", "shutdown": True, "keep_warm": False}
    control = _cn_control(notify_data=[item], mode=Mode.HOLD)
    _patch_check_notify_deps(monkeypatch)

    result = N.check_notify(settings, control, pelletdb={"current": {}}, grill_platform=MagicMock())

    assert result["mode"] == Mode.SHUTDOWN
    assert result["updated"] is True
    assert result["notify_data"][0]["shutdown"] is False


def test_check_notify_keep_warm_flag_transitions_mode_and_setpoint(monkeypatch):
    settings = _cn_settings()
    settings["keep_warm"] = {"temp": 170, "s_plus": True}
    # req starts True for the same reason as the shutdown test above.
    item = {"req": True, "type": "test", "shutdown": False, "keep_warm": True}
    control = _cn_control(notify_data=[item], mode=Mode.SMOKE)
    _patch_check_notify_deps(monkeypatch)

    result = N.check_notify(settings, control, pelletdb={"current": {}}, grill_platform=MagicMock())

    assert result["mode"] == Mode.HOLD
    assert result["primary_setpoint"] == 170
    assert result["s_plus"] is True
    assert result["notify_data"][0]["keep_warm"] is False


def test_check_notify_reignite_flag_transitions_mode_and_saves_laststate(monkeypatch):
    settings = _cn_settings()
    # reignite's condition doesn't require req to have been cleared, but the
    # item body only runs at all when req starts True.
    item = {"req": True, "type": "test", "shutdown": False, "keep_warm": False, "reignite": True, "triggered": True}
    control = _cn_control(notify_data=[item], mode=Mode.HOLD)
    _patch_check_notify_deps(monkeypatch)

    result = N.check_notify(settings, control, pelletdb={"current": {}}, grill_platform=MagicMock())

    assert result["mode"] == Mode.REIGNITE
    assert result["safety"]["reignitelaststate"] == Mode.HOLD
    assert result["updated"] is True


def test_check_notify_writes_control_after_each_processed_item(monkeypatch):
    settings = _cn_settings()
    item = {"req": True, "type": "test", "shutdown": False, "keep_warm": False}
    control = _cn_control(notify_data=[item])
    write_mock = MagicMock()
    _patch_check_notify_deps(monkeypatch, write_control_mock=write_mock)

    N.check_notify(settings, control, pelletdb={"current": {}}, grill_platform=MagicMock())

    write_mock.assert_called_once_with(control, N.WriteKind.OVERWRITE, origin="notifications")
