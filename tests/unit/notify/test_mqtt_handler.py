"""Tests for notify.mqtt_handler.MqttNotificationHandler.

Mocks the MQTT client at its network boundary -- ``mqtt.Client`` (the
``paho.mqtt.client.Client`` constructor imported into mqtt_handler as
``mqtt``) -- with FakeMqttClient below. No real broker/socket is ever
touched: FakeMqttClient records calls in-memory and lets tests control
connect()/publish() return codes to drive every branch.

Real paho constants (MQTT_ERR_SUCCESS, MQTT_ERR_CONN_LOST, MQTT_ERR_AUTH,
connack_string, CallbackAPIVersion, MQTTv5) are used as-is -- they are pure
constants/formatters, not network calls.

Covers: connect success/failure/reuse/exception, __del__ cleanup, the
on_connect/on_disconnect/on_connect_fail/on_message callbacks, throttled
vs. live _check_connection, _publish_data's four return-code branches,
_publish's change-detection + sensor-membership gating across contexts,
_create_autodiscover's datatype/context branches, and notify()'s
dispatch/recursion/mode-change logic (including psutil-backed "system"
context, mocked so no real host introspection leaks into assertions).

Four latent bugs that earlier coverage passes routed *around* are now
fixed in the source and pinned here (each formerly-dodged path is
exercised directly):
  - `_publish` now calls `self._check_homeassistant()` (was a missing `()`
    on the bound method, so autodiscovery fired unconditionally): pinned by
    the enabled/disabled pair of `_publish` autodiscover-gating tests.
  - `_create_autodiscover`'s generic `elif context.startswith("probe_data")`
    branch now defaults `suffix = "Temp"` (was an UnboundLocalError when a
    probe label matched inside that branch): pinned by the matching-label
    generic probe_data test.
  - `_create_autodiscover` now defaults `component = "sensor"` for non-scalar
    values (dict/list/None previously left `component` unbound -> crash at the
    `_publish_autodiscover` call): pinned by the non-scalar value test.
  - `notify()`'s `notify_data` loop now guards `new_context` (a no-match
    label previously raised NameError or misrouted with a stale value):
    pinned by the unknown-label recursion test.
"""

import json
from types import SimpleNamespace
from unittest import mock

import paho.mqtt.client as mqtt
import pytest

import notify.mqtt_handler as MH
from common.modes import Mode

pytestmark = pytest.mark.filterwarnings("ignore::ResourceWarning")


# ---------------------------------------------------------------------------
# Fake MQTT client -- the mocked network boundary
# ---------------------------------------------------------------------------


class FakeMqttClient:
    """Stand-in for paho.mqtt.client.Client. Never touches a socket."""

    instances = []

    # Class-level defaults tests can override via monkeypatch.setattr before
    # constructing a handler (so a fresh instance picks them up in __init__).
    connect_return = mqtt.MQTT_ERR_SUCCESS
    publish_return = mqtt.MQTT_ERR_SUCCESS

    def __init__(self, *args, **kwargs):
        self.init_args = args
        self.init_kwargs = kwargs
        self.connect_calls = []
        self.publish_calls = []
        self.subscribe_calls = []
        self.will_set_calls = []
        self.username_pw_set_calls = []
        self.loop_started = False
        self.loop_stopped = False
        self.disconnected = False
        self._connected = False
        self.on_connect = None
        self.on_disconnect = None
        self.on_connect_fail = None
        self.on_message = None
        FakeMqttClient.instances.append(self)

    def will_set(self, topic, payload):
        self.will_set_calls.append((topic, payload))

    def username_pw_set(self, username, password):
        self.username_pw_set_calls.append((username, password))

    def connect(self, host, port, keepalive):
        self.connect_calls.append((host, port, keepalive))
        if self.connect_return == mqtt.MQTT_ERR_SUCCESS:
            self._connected = True
        return self.connect_return

    def loop_start(self):
        self.loop_started = True

    def loop_stop(self):
        self.loop_stopped = True

    def disconnect(self):
        self.disconnected = True
        self._connected = False

    def is_connected(self):
        return self._connected

    def publish(self, topic, payload, qos=0, retain=False, properties=None):
        self.publish_calls.append(
            {"topic": topic, "payload": payload, "qos": qos, "retain": retain, "properties": properties}
        )
        return SimpleNamespace(rc=self.publish_return)

    def subscribe(self, topic):
        self.subscribe_calls.append(topic)


@pytest.fixture(autouse=True)
def _reset_fake_client():
    FakeMqttClient.instances = []
    FakeMqttClient.connect_return = mqtt.MQTT_ERR_SUCCESS
    FakeMqttClient.publish_return = mqtt.MQTT_ERR_SUCCESS
    yield
    FakeMqttClient.instances = []
    FakeMqttClient.connect_return = mqtt.MQTT_ERR_SUCCESS
    FakeMqttClient.publish_return = mqtt.MQTT_ERR_SUCCESS


@pytest.fixture
def patched_client(monkeypatch):
    """Patch the network boundary and a deterministic hostname."""
    monkeypatch.setattr(MH.mqtt, "Client", FakeMqttClient)
    monkeypatch.setattr(MH, "getfqdn", lambda: "testhost.local")
    return FakeMqttClient


# ---------------------------------------------------------------------------
# Settings / probe fixtures
# ---------------------------------------------------------------------------


def _settings(**mqtt_overrides):
    settings = {
        "globals": {"debug_mode": False, "grill_name": "", "units": "F"},
        "modules": {"grillplat": "prototype"},
        "probe_settings": {
            "probe_map": {
                "probe_info": [
                    {"type": "Primary", "label": "Grill", "name": "Grill", "port": "ADC0"},
                    {"type": "Food", "label": "Probe1", "name": "Probe-1", "port": "ADC1"},
                ]
            }
        },
        "notify_services": {
            "mqtt": {
                "broker": "test.broker",
                "enabled": True,
                "homeassistant_autodiscovery_topic": "homeassistant",
                "id": "PiFireTest",
                "password": "",
                "port": "1883",
                "update_sec": "30",
                "username": "",
            }
        },
    }
    settings["notify_services"]["mqtt"].update(mqtt_overrides)
    return settings


def _make_handler(patched_client, **mqtt_overrides):
    return MH.MqttNotificationHandler(_settings(**mqtt_overrides))


# ---------------------------------------------------------------------------
# __init__ / default_payload
# ---------------------------------------------------------------------------


def test_init_connects_and_builds_default_payload(patched_client):
    handler = _make_handler(patched_client)

    assert isinstance(handler.client, FakeMqttClient)
    assert handler.client.is_connected()
    assert len(FakeMqttClient.instances) == 1

    fake = FakeMqttClient.instances[0]
    assert fake.connect_calls == [("test.broker", 1883, 10)]
    assert fake.will_set_calls == [("PiFireTest/availability", "offline")]
    assert fake.username_pw_set_calls == []  # empty username -> not called
    assert fake.loop_started is True

    payload = handler.default_payload
    assert payload["availability"] == [{"topic": "PiFireTest/availability"}]
    assert payload["device"]["identifiers"] == ["PiFireTest"]
    assert payload["device"]["manufacturer"] == "PiFire"
    assert payload["device"]["model"] == "prototype"
    assert payload["device"]["name"] == "PiFire"  # blank grill_name -> default
    assert payload["device"]["configuration_url"] == "http://testhost.local:5000/settings"


def test_init_uses_configured_grill_name(patched_client):
    settings = _settings()
    settings["globals"]["grill_name"] = "Backyard Smoker"
    handler = MH.MqttNotificationHandler(settings)
    assert handler.default_payload["device"]["name"] == "Backyard Smoker"


def test_init_sets_username_password_when_configured(patched_client):
    _make_handler(patched_client, username="alice", password="secret")
    fake = FakeMqttClient.instances[0]
    assert fake.username_pw_set_calls == [("alice", "secret")]


def test_init_swallows_exception_after_logger_is_created(patched_client, caplog):
    # `grill_name` is read after the logger is constructed, so a KeyError
    # here exercises __init__'s outer except (as opposed to a KeyError on an
    # earlier field, which would blow up trying to log before the logger
    # exists -- a distinct, unexercised bug we're not chasing here).
    settings = _settings()
    del settings["globals"]["grill_name"]
    with caplog.at_level("ERROR", logger="mqtt"):
        handler = MH.MqttNotificationHandler(settings)
    assert any("Error initializing the mqtt class object" in r.message for r in caplog.records)
    # default_payload never got assigned since the KeyError fired first.
    assert not hasattr(handler, "default_payload")


def test_debug_mode_selects_debug_log_level(patched_client, monkeypatch):
    # The "mqtt" logger name is hardcoded and cached by the stdlib logging
    # module, so once created its level sticks across tests regardless of
    # settings -- create_logger()'s "if not logger.handlers" guard skips
    # reconfiguring an existing logger. Spy the level actually *requested*
    # instead of the (possibly stale) logger's live level.
    import logging

    captured = {}
    real_create_logger = MH.create_logger

    def _spy(name, **kwargs):
        captured["level"] = kwargs.get("level")
        return real_create_logger(name, **kwargs)

    monkeypatch.setattr(MH, "create_logger", _spy)

    settings = _settings()
    settings["globals"]["debug_mode"] = True
    MH.MqttNotificationHandler(settings)

    assert captured["level"] == logging.DEBUG


# ---------------------------------------------------------------------------
# __del__
# ---------------------------------------------------------------------------


def test_del_publishes_offline_and_tears_down_client(patched_client):
    handler = _make_handler(patched_client)
    fake = handler.client
    # Bypass the 5s connect throttle so _publish_data actually runs.
    handler.last_conn_time = 0

    handler.__del__()

    assert fake.publish_calls[-1]["topic"] == "PiFireTest/availability"
    assert fake.publish_calls[-1]["payload"] == "offline"
    assert fake.loop_stopped is True
    assert fake.disconnected is True


def test_del_logs_when_teardown_raises(patched_client, caplog):
    handler = _make_handler(patched_client)
    handler._publish_data = mock.Mock(side_effect=RuntimeError("boom"))
    with caplog.at_level("ERROR", logger="mqtt"):
        handler.__del__()
    assert any("Error occurred closing mqtt client" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# _connect
# ---------------------------------------------------------------------------


def test_connect_failure_leaves_client_none(patched_client, caplog):
    FakeMqttClient.connect_return = mqtt.MQTT_ERR_CONN_LOST
    with caplog.at_level("ERROR", logger="mqtt"):
        handler = _make_handler(patched_client)
    assert handler.client is None
    assert any("Error" in r.message and "connecting to" in r.message for r in caplog.records)


def test_connect_reuses_existing_client(patched_client):
    handler = _make_handler(patched_client)
    fake = handler.client
    assert len(FakeMqttClient.instances) == 1
    assert fake.init_args[0] is mqtt.CallbackAPIVersion.VERSION2

    handler._connect()

    assert len(FakeMqttClient.instances) == 1  # no new client constructed
    assert handler.client is fake
    assert len(fake.connect_calls) == 2  # connected a second time


def test_connect_swallows_exception_from_client_construction(monkeypatch, caplog):
    def _raise(*a, **k):
        raise OSError("no such host")

    monkeypatch.setattr(MH.mqtt, "Client", _raise)
    monkeypatch.setattr(MH, "getfqdn", lambda: "testhost.local")

    with caplog.at_level("ERROR", logger="mqtt"):
        handler = MH.MqttNotificationHandler(_settings())

    assert handler.client is None
    assert any("Error occurred connecting to the mqtt broker" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# on_connect / on_disconnect / on_connect_fail / on_message
# ---------------------------------------------------------------------------


def test_on_connect_publishes_availability_and_restores_subscriptions(patched_client):
    handler = _make_handler(patched_client)
    handler.subscriptions = ["topic/a", "topic/b"]
    handler._subscribe = mock.Mock()
    handler.last_conn_time = 12345

    reason_code = mqtt.ReasonCode(mqtt.PacketTypes.CONNACK, identifier=0)
    handler._on_connect(handler.client, None, {}, reason_code, None)

    assert handler.last_conn_time == 0
    online_calls = [c for c in handler.client.publish_calls if c["payload"] == "online"]
    assert online_calls and online_calls[-1]["topic"] == "PiFireTest/availability"
    assert online_calls[-1]["qos"] == 1
    handler._subscribe.assert_has_calls([mock.call("topic/a"), mock.call("topic/b")])


def test_on_disconnect_logs_error_for_a_failure_reason(patched_client, caplog):
    handler = _make_handler(patched_client)
    reason_code = mqtt.ReasonCode(mqtt.PacketTypes.DISCONNECT, identifier=128)
    with caplog.at_level("INFO", logger="mqtt"):
        handler._on_disconnect(handler.client, None, None, reason_code, None)
    assert any(r.levelname == "ERROR" and "Unspecified error" in r.message for r in caplog.records)


def test_on_disconnect_logs_info_for_a_success_reason(patched_client, caplog):
    handler = _make_handler(patched_client)
    reason_code = mqtt.ReasonCode(mqtt.PacketTypes.DISCONNECT, identifier=0)
    with caplog.at_level("INFO", logger="mqtt"):
        handler._on_disconnect(handler.client, None, None, reason_code, None)
    assert any(r.levelname == "INFO" and "Normal disconnection" in r.message for r in caplog.records)


def test_on_connect_fail_logs_error(patched_client, caplog):
    handler = _make_handler(patched_client)
    with caplog.at_level("ERROR", logger="mqtt"):
        handler._on_connect_fail(handler.client, None)
    assert any("Connection failure" in r.message for r in caplog.records)


def test_on_message_is_a_noop(patched_client):
    handler = _make_handler(patched_client)
    assert handler._on_message(handler.client, None, SimpleNamespace(topic="x", payload=b"y")) is None


# ---------------------------------------------------------------------------
# _check_connection / _check_homeassistant
# ---------------------------------------------------------------------------


def test_check_connection_throttled_immediately_after_connect(patched_client):
    handler = _make_handler(patched_client)
    assert handler._check_connection() is False  # inside the 5s throttle window


def test_check_connection_reconnects_when_client_is_none(patched_client):
    handler = _make_handler(patched_client)
    handler.client = None
    handler.last_conn_time = 0

    assert handler._check_connection() is True
    assert len(FakeMqttClient.instances) == 2  # a fresh client was constructed


def test_check_connection_reconnects_when_disconnected(patched_client):
    handler = _make_handler(patched_client)
    fake = handler.client
    fake._connected = False
    handler.last_conn_time = 0

    assert handler._check_connection() is True
    assert handler.client is fake  # same client instance, just reconnected
    assert len(fake.connect_calls) == 2


def test_check_homeassistant_true_when_topic_configured(patched_client):
    handler = _make_handler(patched_client)
    assert handler._check_homeassistant() is True


def test_check_homeassistant_false_when_topic_blank(patched_client):
    handler = _make_handler(patched_client, homeassistant_autodiscovery_topic="")
    assert handler._check_homeassistant() is False


# ---------------------------------------------------------------------------
# _publish_data
# ---------------------------------------------------------------------------


def test_publish_data_returns_false_when_not_connected(patched_client):
    handler = _make_handler(patched_client)
    handler.last_conn_time = __import__("time").time()  # force the throttle
    handler.client.publish_calls.clear()

    assert handler._publish_data(topic="t", payload="p") is False
    assert handler.client.publish_calls == []


def test_publish_data_success_passes_through_args(patched_client):
    handler = _make_handler(patched_client)
    handler.last_conn_time = 0  # bypass the 5s post-connect throttle
    ok = handler._publish_data(topic="foo/bar", payload="hello", qos=1, retain=True, properties="props")
    assert ok is True
    call = handler.client.publish_calls[-1]
    assert call == {"topic": "foo/bar", "payload": "hello", "qos": 1, "retain": True, "properties": "props"}


def test_publish_data_conn_lost_returns_false(patched_client, caplog):
    handler = _make_handler(patched_client)
    handler.last_conn_time = 0
    handler.client.publish_return = mqtt.MQTT_ERR_CONN_LOST
    with caplog.at_level("ERROR", logger="mqtt"):
        result = handler._publish_data(topic="t", payload="p")
    assert result is False
    assert any("mqtt connection is lost" in r.message for r in caplog.records)


def test_publish_data_auth_error_clears_client(patched_client, caplog):
    handler = _make_handler(patched_client)
    handler.last_conn_time = 0
    handler.client.publish_return = mqtt.MQTT_ERR_AUTH
    with caplog.at_level("ERROR", logger="mqtt"):
        result = handler._publish_data(topic="t", payload="p")
    assert result is None  # falls off the elif with no explicit return
    assert handler.client is None
    assert any("Cannot publish data for t" in r.message for r in caplog.records)


def test_publish_data_other_error_returns_false(patched_client, caplog):
    handler = _make_handler(patched_client)
    handler.last_conn_time = 0
    handler.client.publish_return = mqtt.MQTT_ERR_NO_CONN
    with caplog.at_level("ERROR", logger="mqtt"):
        result = handler._publish_data(topic="t", payload="p")
    assert result is False


# ---------------------------------------------------------------------------
# _publish_autodiscover
# ---------------------------------------------------------------------------


def test_publish_autodiscover_success_marks_category_initialized(patched_client):
    handler = _make_handler(patched_client)
    handler._publish_autodiscover("auger", "some/topic", json.dumps({"a": 1}))
    assert "auger" in handler.initialized_topics
    call = handler.client.publish_calls[-1]
    assert call["qos"] == 2
    assert call["retain"] is True


def test_publish_autodiscover_no_duplicate_category(patched_client):
    handler = _make_handler(patched_client)
    handler.initialized_topics.append("auger")
    handler._publish_autodiscover("auger", "some/topic", json.dumps({"a": 1}))
    assert handler.initialized_topics.count("auger") == 1


def test_publish_autodiscover_conn_lost_does_not_mark_initialized(patched_client, caplog):
    handler = _make_handler(patched_client)
    handler.client.publish_return = mqtt.MQTT_ERR_CONN_LOST
    with caplog.at_level("ERROR", logger="mqtt"):
        handler._publish_autodiscover("auger", "some/topic", "{}")
    assert "auger" not in handler.initialized_topics
    assert any("Cannot publish autodiscover data" in r.message for r in caplog.records)


def test_publish_autodiscover_other_error_logs(patched_client, caplog):
    handler = _make_handler(patched_client)
    handler.client.publish_return = mqtt.MQTT_ERR_AUTH
    with caplog.at_level("ERROR", logger="mqtt"):
        handler._publish_autodiscover("auger", "some/topic", "{}")
    assert "auger" not in handler.initialized_topics
    assert any("Cannot publish autodiscover data" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# _publish (change detection + sensor-membership gating)
# ---------------------------------------------------------------------------


def test_publish_devices_first_value_triggers_publish_and_autodiscover(patched_client):
    handler = _make_handler(patched_client)
    handler.last_conn_time = 0
    handler._create_autodiscover = mock.Mock()

    handler._publish("devices", {"auger": True})

    calls = [c for c in handler.client.publish_calls if c["topic"] == "PiFireTest/devices"]
    assert calls and json.loads(calls[-1]["payload"]) == {"auger": True}
    handler._create_autodiscover.assert_called_once_with("devices", {"auger": True})


def test_publish_skips_autodiscover_when_homeassistant_disabled(patched_client):
    # Pins the `if self._check_homeassistant():` gate: with the autodiscovery
    # topic blank, the data still publishes but autodiscovery is suppressed.
    # (Before the `()` fix the bound method was always truthy, so this path
    # was impossible to reach.)
    handler = _make_handler(patched_client, homeassistant_autodiscovery_topic="")
    handler.last_conn_time = 0
    handler._create_autodiscover = mock.Mock()

    handler._publish("devices", {"auger": True})

    calls = [c for c in handler.client.publish_calls if c["topic"] == "PiFireTest/devices"]
    assert calls and json.loads(calls[-1]["payload"]) == {"auger": True}  # data still published
    handler._create_autodiscover.assert_not_called()  # but autodiscovery suppressed


def test_publish_devices_unchanged_value_does_not_republish(patched_client):
    handler = _make_handler(patched_client)
    handler.last_conn_time = 0
    handler._create_autodiscover = mock.Mock()
    handler._publish("devices", {"auger": True})
    handler.client.publish_calls.clear()
    handler._create_autodiscover.reset_mock()

    handler._publish("devices", {"auger": True})

    assert handler.client.publish_calls == []
    handler._create_autodiscover.assert_not_called()


def test_publish_skips_autodiscover_when_publish_data_fails(patched_client):
    # Left inside the 5s post-connect throttle deliberately: _publish_data()
    # returns False, so `if self._publish_data(...):` must not call
    # _create_autodiscover -- even though change detection (and self.last
    # bookkeeping) already ran.
    handler = _make_handler(patched_client)
    handler._create_autodiscover = mock.Mock()

    handler._publish("devices", {"auger": True})

    assert handler.client.publish_calls == []
    handler._create_autodiscover.assert_not_called()
    assert handler.last["devices_auger"] is True


def test_publish_devices_unrecognized_sensor_is_dropped(patched_client):
    handler = _make_handler(patched_client)
    handler._publish("devices", {"totally_unknown_device": True})
    assert handler.client.publish_calls == []


@pytest.mark.parametrize(
    ("topic_suffix", "payload"),
    [
        ("pellet", {"hopper_level": 42}),
        ("control_notify_data_ADC1", {"target": 225}),
        ("probe_data_primary", {"Grill": 225}),
    ],
)
def test_publish_topic_and_payload(patched_client, topic_suffix, payload):
    handler = _make_handler(patched_client)
    handler.last_conn_time = 0
    handler._create_autodiscover = mock.Mock()

    handler._publish(topic_suffix, payload)

    calls = [c for c in handler.client.publish_calls if c["topic"] == f"PiFireTest/{topic_suffix}"]
    assert calls and json.loads(calls[-1]["payload"]) == payload


# ---------------------------------------------------------------------------
# _subscribe
# ---------------------------------------------------------------------------


def test_subscribe_appends_new_topic_once(patched_client):
    handler = _make_handler(patched_client)
    handler._subscribe("a/b")
    handler._subscribe("a/b")
    assert handler.subscriptions == ["a/b"]
    assert handler.client.subscribe_calls == ["a/b", "a/b"]


# ---------------------------------------------------------------------------
# _create_autodiscover
# ---------------------------------------------------------------------------


def _discover(handler, context, data):
    """Seed `.last` (the precondition for autodiscovery) for every device in
    `data`, spy `_publish_autodiscover`, run `_create_autodiscover`, and
    return {category: (topic, discovery_dict)}."""
    for device in data:
        handler.last[f"{context}_{device}"] = data[device]
    handler._publish_autodiscover = mock.Mock()
    handler._create_autodiscover(context, data)
    result = {}
    for call in handler._publish_autodiscover.call_args_list:
        category, topic, payload = call.args
        result[category] = (topic, json.loads(payload))
    return result


def test_autodiscover_bool_special_device_stays_enabled(patched_client):
    handler = _make_handler(patched_client)
    result = _discover(handler, "devices", {"auger": True})
    topic, discovery = result["auger"]
    assert topic == "homeassistant/binary_sensor/PiFireTest/devices_auger/config"
    assert discovery["payload_on"] is True
    assert discovery["payload_off"] is False
    assert discovery["enabled_by_default"] is True
    assert "devices_auger" in handler.initialized_topics


def test_autodiscover_bool_non_special_device_disabled_by_default(patched_client):
    handler = _make_handler(patched_client)
    result = _discover(handler, "devices", {"some_flag": True})
    _, discovery = result["some_flag"]
    assert discovery["enabled_by_default"] is False


def test_autodiscover_str_device_is_plain_sensor(patched_client):
    handler = _make_handler(patched_client)
    result = _discover(handler, "system", {"status_text": "ok"})
    topic, discovery = result["status_text"]
    assert "/sensor/" in topic
    assert discovery["value_template"] == "{{ value_json.status_text }}"


def test_autodiscover_probe_data_primary_temperature(patched_client):
    handler = _make_handler(patched_client)
    result = _discover(handler, "probe_data_primary", {"Grill": 225})
    _, discovery = result["Grill"]
    assert discovery["device_class"] == "temperature"
    assert discovery["unit_of_measurement"] == "°F"
    assert discovery["state_class"] == "measurement"


def test_autodiscover_probe_data_tr_is_diagnostic_ohms(patched_client):
    handler = _make_handler(patched_client)
    result = _discover(handler, "probe_data_tr", {"Grill": 100000})
    _, discovery = result["Grill"]
    assert discovery["unit_of_measurement"] == "ohms"
    assert discovery["enabled_by_default"] is False
    assert discovery["entity_category"] == "diagnostic"


def test_autodiscover_generic_probe_data_context_no_label_match(patched_client):
    # Exercises the `elif context.startswith("probe_data")` branch's for-loop
    # with no matching probe label: name/topic stay at their pre-loop defaults.
    handler = _make_handler(patched_client)
    result = _discover(handler, "probe_data_extra", {"no_such_probe_label": 1})
    topic, discovery = result["no_such_probe_label"]
    assert discovery["state_class"] == "measurement"
    assert discovery["name"] == "No Such Probe Label"  # default, suffix not appended
    assert topic.endswith("/probe_data_extra_no_such_probe_label/config")  # topic_name unchanged


def test_autodiscover_generic_probe_data_context_matching_label(patched_client):
    # Pins the `suffix = "Temp"` default in the generic probe_data branch: a
    # matching probe label appends the suffix and rewrites topic_name to the
    # probe's port. (Before the fix this raised UnboundLocalError on `suffix`.)
    handler = _make_handler(patched_client)
    result = _discover(handler, "probe_data_extra", {"Grill": 225})
    topic, discovery = result["Grill"]
    assert discovery["state_class"] == "measurement"
    assert discovery["name"] == "Grill Temp"  # base name + generic-branch suffix
    assert topic.endswith("/probe_data_extra_ADC0/config")  # topic_name -> probe port


def test_autodiscover_control_notify_sets_target_name(patched_client):
    handler = _make_handler(patched_client)
    data = {"label": "Probe1", "name": "Probe-1", "target": 225}
    result = _discover(handler, "control_notify_data_ADC1", data)
    _, discovery = result["target"]
    assert discovery["name"] == "Probe-1 Target"
    assert discovery["device_class"] == "temperature"


def test_autodiscover_control_notify_no_matching_probe_keeps_defaults(patched_client):
    # Covers the for/break loop's no-match path: discovery["name"]/topic_name
    # stay at their pre-loop defaults when no configured probe's label
    # matches data["label"].
    handler = _make_handler(patched_client)
    data = {"label": "NoSuchProbe", "name": "Ghost", "target": 225}
    result = _discover(handler, "control_notify_data_ADCX", data)
    topic, discovery = result["target"]
    assert discovery["device_class"] == "temperature"  # still set before the loop
    assert discovery["name"] == "Target"  # default `device.title()...`, never overwritten
    assert topic.endswith("/control_notify_data_ADCX_target/config")  # topic_name unchanged


def test_autodiscover_pid_context_is_diagnostic(patched_client):
    handler = _make_handler(patched_client)
    result = _discover(handler, "pid_config", {"PB": 10})
    _, discovery = result["PB"]
    assert discovery["entity_category"] == "diagnostic"
    assert discovery["enabled_by_default"] is False
    assert discovery["unit_of_measurement"] == "s"  # PB device-specific override


@pytest.mark.parametrize(
    ("group", "reading", "key", "expected"),
    [
        (
            "pid",
            {"u_max": 0.5},
            "u_max",
            {"unit_of_measurement": "%", "value_template": "{{ value_json.u_max | round(2)}}"},
        ),
        ("system", {"cpu_temp": 45.2}, "cpu_temp", {"device_class": "temperature", "unit_of_measurement": "°C"}),
        # Uses the global units setting, which the handler fixture pins to F.
        (
            "control",
            {"primary_setpoint": 225},
            "primary_setpoint",
            {"device_class": "temperature", "unit_of_measurement": "°F"},
        ),
    ],
)
def test_autodiscover_fields(patched_client, group, reading, key, expected):
    handler = _make_handler(patched_client)

    result = _discover(handler, group, reading)

    _, discovery = result[key]
    for field, value in expected.items():
        assert discovery[field] == value, field


def test_autodiscover_memory_and_duty_cycle_units(patched_client):
    handler = _make_handler(patched_client)
    result = _discover(handler, "system", {"available_memory": 1024, "cpu": 12})
    assert result["available_memory"][1]["unit_of_measurement"] == "b"
    assert result["cpu"][1]["unit_of_measurement"] == "%"


def test_autodiscover_non_scalar_value_defaults_to_plain_sensor(patched_client):
    # Pins the `component = "sensor"` default for values that aren't
    # bool/str/int/float. A dict value hits none of the datatype branches; the
    # default keeps `component` bound (was UnboundLocalError at publish time).
    handler = _make_handler(patched_client)
    result = _discover(handler, "system", {"weird": {"nested": 1}})
    topic, discovery = result["weird"]
    assert "/sensor/" in topic
    assert topic.endswith("/system_weird/config")
    assert discovery["value_template"] == "{{ value_json.weird }}"
    # No scalar-only enrichment applied.
    assert "state_class" not in discovery
    assert "system_weird" in handler.initialized_topics


def test_autodiscover_none_value_defaults_to_plain_sensor(patched_client):
    # Same guard as above for a None value (another non-scalar type).
    handler = _make_handler(patched_client)
    result = _discover(handler, "system", {"maybe": None})
    topic, _ = result["maybe"]
    assert "/sensor/" in topic


def test_autodiscover_skips_already_initialized_topic(patched_client):
    handler = _make_handler(patched_client)
    handler.last["devices_auger"] = True
    handler.initialized_topics.append("devices_auger")
    handler._publish_autodiscover = mock.Mock()
    handler._create_autodiscover("devices", {"auger": True})
    handler._publish_autodiscover.assert_not_called()


def test_autodiscover_skips_device_not_in_last(patched_client):
    # Precondition `if device_name in self.last` gates autodiscovery -- a
    # device that was never published (no entry in .last) is skipped.
    handler = _make_handler(patched_client)
    handler._publish_autodiscover = mock.Mock()
    handler._create_autodiscover("devices", {"auger": True})
    handler._publish_autodiscover.assert_not_called()


# ---------------------------------------------------------------------------
# notify() dispatch / recursion / mode-change
# ---------------------------------------------------------------------------


def test_notify_publishes_flat_context(patched_client):
    handler = _make_handler(patched_client)
    handler._publish = mock.Mock()
    handler.notify("devices", {"auger": True})
    handler._publish.assert_called_once_with("devices", {"auger": True})


def test_notify_context_not_recognized_no_publish(patched_client):
    handler = _make_handler(patched_client)
    handler._publish = mock.Mock()
    handler.notify("totally_unrecognized", {"a": 1})
    handler._publish.assert_not_called()


def test_notify_notify_data_recurses_with_probe_port(patched_client):
    handler = _make_handler(patched_client)
    spy = mock.MagicMock(wraps=handler.notify)
    handler.notify = spy
    handler._publish = mock.Mock()

    handler.notify("devices", {"notify_data": [{"label": "Probe1", "target": 225}]})

    spy.assert_any_call("devices", {"notify_data": [{"label": "Probe1", "target": 225}]})
    spy.assert_any_call("devices_notify_data_ADC1", {"label": "Probe1", "target": 225})


def test_notify_notify_data_unknown_label_skips_recursion(patched_client):
    # Pins the `new_context` guard in the notify_data loop: a label matching no
    # configured probe resolves no port, so the recursive notify is skipped
    # entirely (was a NameError / stale-value misroute before the guard).
    handler = _make_handler(patched_client)
    spy = mock.MagicMock(wraps=handler.notify)
    handler.notify = spy
    handler._publish = mock.Mock()

    handler.notify("devices", {"notify_data": [{"label": "NoSuchProbe", "target": 225}]})

    # Only the outer call happened; no `devices_notify_data_*` recursion.
    spy.assert_called_once_with("devices", {"notify_data": [{"label": "NoSuchProbe", "target": 225}]})
    recursed = [c for c in spy.call_args_list if str(c.args[0]).startswith("devices_notify_data")]
    assert recursed == []


def test_notify_nested_dict_recurses_into_publishable_context(patched_client):
    # "probe_data" itself is not in CONTEXTS (so the outer call doesn't
    # publish), but "probe_data" + "_" + "primary" == "probe_data_primary",
    # which is -- so the nested-dict recursion must land there.
    handler = _make_handler(patched_client)
    spy = mock.MagicMock(wraps=handler.notify)
    handler.notify = spy
    handler._publish = mock.Mock()

    handler.notify("probe_data", {"primary": {"Grill": 225}})

    spy.assert_any_call("probe_data_primary", {"Grill": 225})
    handler._publish.assert_any_call("probe_data_primary", {"Grill": 225})


def test_notify_caches_control_only_once(patched_client):
    handler = _make_handler(patched_client)
    handler._publish = mock.Mock()
    first = {"mode": Mode.SMOKE}
    second = {"mode": Mode.HOLD}

    handler.notify("control", first)
    assert handler.control is first

    handler.notify("control", second)
    assert handler.control is first  # not overwritten by the second call


def test_notify_system_context_gathers_psutil_data(patched_client, monkeypatch):
    handler = _make_handler(patched_client)
    handler._publish = mock.Mock()

    monkeypatch.setattr(MH.psutil, "cpu_percent", lambda interval=None: 33.3)
    monkeypatch.setattr(MH.psutil, "virtual_memory", lambda: SimpleNamespace(available=111, free=222))
    monkeypatch.setattr(MH.psutil, "sensors_temperatures", lambda: {"cpu_thermal": [SimpleNamespace(current=45.6)]})

    handler.notify("system", {"ignored": "input"})

    handler._publish.assert_called_once_with(
        "system",
        {"cpu": 33.3, "available_memory": 111, "free_memory": 222, "cpu_temp": 45.6},
    )


def test_notify_system_context_without_cpu_thermal_key(patched_client, monkeypatch):
    handler = _make_handler(patched_client)
    handler._publish = mock.Mock()

    monkeypatch.setattr(MH.psutil, "cpu_percent", lambda interval=None: 1.0)
    monkeypatch.setattr(MH.psutil, "virtual_memory", lambda: SimpleNamespace(available=1, free=2))
    monkeypatch.setattr(MH.psutil, "sensors_temperatures", dict)

    handler.notify("system", {})

    payload = handler._publish.call_args.args[1]
    assert "cpu_temp" not in payload


def test_notify_system_context_without_sensors_temperatures_support(patched_client, monkeypatch):
    # Covers the `"sensors_temperatures" in dir(psutil)` False branch --
    # platforms (e.g. non-Linux) where psutil doesn't expose the sensor API
    # at all. Swap in a bare namespace lacking the attribute entirely.
    handler = _make_handler(patched_client)
    handler._publish = mock.Mock()

    fake_psutil = SimpleNamespace(
        cpu_percent=lambda interval=None: 2.0,
        virtual_memory=lambda: SimpleNamespace(available=3, free=4),
    )
    monkeypatch.setattr(MH, "psutil", fake_psutil)

    handler.notify("system", {})

    payload = handler._publish.call_args.args[1]
    assert payload == {"cpu": 2.0, "available_memory": 3, "free_memory": 4}
    assert "cpu_temp" not in payload


def test_notify_mode_change_triggers_notify_event_and_zeroes_pid(patched_client):
    handler = _make_handler(patched_client)
    handler._publish = mock.Mock()
    handler.last_mode = Mode.STARTUP

    handler.notify("control", {"mode": Mode.SMOKE})

    # notify_event fired for the mode transition
    notify_event_calls = [c for c in handler._publish.call_args_list if c.args[0] == "notify_event"]
    assert notify_event_calls
    assert notify_event_calls[-1].args[1] == {"msg": "Entered Smoke mode"}

    # PID sensors zeroed since Smoke != Hold
    pid_calls = [c for c in handler._publish.call_args_list if c.args[0] == "pid"]
    assert pid_calls
    assert all(v == 0 for v in pid_calls[-1].args[1].values())
    assert set(pid_calls[-1].args[1].keys()) == set(handler.PID_SENSORS)

    assert handler.last_mode == Mode.SMOKE


def test_notify_mode_unchanged_does_not_refire_notify_event(patched_client):
    handler = _make_handler(patched_client)
    handler._publish = mock.Mock()
    handler.last_mode = Mode.SMOKE

    handler.notify("control", {"mode": Mode.SMOKE})

    notify_event_calls = [c for c in handler._publish.call_args_list if c.args[0] == "notify_event"]
    assert notify_event_calls == []


def test_notify_hold_mode_does_not_zero_pid(patched_client):
    handler = _make_handler(patched_client)
    handler._publish = mock.Mock()
    handler.last_mode = Mode.SMOKE

    handler.notify("control", {"mode": Mode.HOLD})

    pid_calls = [c for c in handler._publish.call_args_list if c.args[0] == "pid"]
    assert pid_calls == []


def test_notify_stop_mode_zeroes_probe_temps_by_type(patched_client):
    handler = _make_handler(patched_client)
    handler._publish = mock.Mock()
    handler.last_mode = Mode.SMOKE

    handler.notify("control", {"mode": Mode.STOP})

    calls_by_context = {c.args[0]: c.args[1] for c in handler._publish.call_args_list}
    assert calls_by_context["probe_data_primary"] == {"Grill": 0}
    assert calls_by_context["probe_data_food"] == {"Probe1": 0}
    assert calls_by_context["probe_data_aux"] == {}
    assert calls_by_context["probe_data_tr"] == {"Grill": 0, "Probe1": 0}


def test_notify_stop_mode_zeroes_aux_and_ignores_other_probe_types(patched_client):
    # Covers the "aux" branch and the trailing `else: pass` branch of the
    # probe-type dispatch, neither of which the default two-probe fixture
    # (Primary + Food) reaches.
    settings = _settings()
    settings["probe_settings"]["probe_map"]["probe_info"] = [
        {"type": "aux", "label": "Ambient", "name": "Ambient", "port": "ADC2"},
        {"type": "Something_Else", "label": "Weird", "name": "Weird", "port": "ADC3"},
    ]
    handler = MH.MqttNotificationHandler(settings)
    handler._publish = mock.Mock()
    handler.last_mode = Mode.SMOKE

    handler.notify("control", {"mode": Mode.STOP})

    calls_by_context = {c.args[0]: c.args[1] for c in handler._publish.call_args_list}
    assert calls_by_context["probe_data_aux"] == {"Ambient": 0}
    assert calls_by_context["probe_data_primary"] == {}
    assert calls_by_context["probe_data_food"] == {}
    # Both probes still get a tr_data entry regardless of type.
    assert calls_by_context["probe_data_tr"] == {"Ambient": 0, "Weird": 0}


def test_notify_swallows_exception_and_logs(patched_client, caplog):
    handler = _make_handler(patched_client)
    # "control" context with no "mode" key trips the KeyError inside the
    # mode-change check -- caught by notify()'s broad except.
    with caplog.at_level("ERROR", logger="mqtt"):
        handler.notify("control", {"no_mode_key": True})
    assert any("Error occurred publishing device data" in r.message for r in caplog.records)
