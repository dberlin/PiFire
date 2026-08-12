"""Unit tests for notify.wled_handler.

Covers the full public surface: device-address cleanup + initial state fetch
in __init__, control-mode selection, the HTTP boundary (get_info /
send_notification / send_direct_command), the suggested-preset color/effect
mapping, and the three notify() dispatch paths (profiles / suggested /
traditional). All HTTP calls are mocked at ``notify.wled_handler.requests``
-- no real device or network is touched.

Also pins the fix for the F841 latent bug where the cooking branch hardcoded
``orange_cooking`` and ignored the user-configurable ``cooking_color``.

LATENT BUG (documented, not fixed here):
notify/wled_handler.py:425-429 defines ``_notify_suggested`` twice back to
back. The first definition (a 2-line stub with just a docstring) is
immediately shadowed by the second, full implementation, so there is no
behavioral effect at runtime -- Python just keeps the last definition. It is
dead code / a copy-paste artifact (severity: cosmetic), not a functional bug,
so no test is needed to "route around" it; noted here for the record.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest
import requests

from notify.wled_handler import WLEDNotificationHandler


def _make_handler():
    """Build a handler without running __init__ (which does network I/O)."""
    handler = WLEDNotificationHandler.__new__(WLEDNotificationHandler)
    handler.device_address = "1.2.3.4"
    handler.logger = logging.getLogger("test-wled")
    handler.last_updated = 0
    return handler


def _settings(use_profiles=False, use_suggested_presets=False, device_address="http://10.0.0.5/", extra=None):
    wled_cfg = {
        "device_address": device_address,
        "use_profiles": use_profiles,
        "use_suggested_presets": use_suggested_presets,
        "profile_numbers": {},
        "mode_presets": {"Startup": 2, "Smoke": 4},
        "event_presets": {
            "Temp_Achieved": 6,
            "Timer_Expired": 7,
            "Pellet_Level_Low": 8,
            "Grill_Error": 9,
            "Recipe_Next": 10,
        },
        "suggested_config": {"cooking_color": "blue"},
        "notify_duration": 120,
    }
    if extra:
        wled_cfg.update(extra)
    return {"notify_services": {"wled": wled_cfg}}


def _ok_response(json_data=None, text=""):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = text
    return resp


# ---------------------------------------------------------------------------
# __init__ / device address cleanup / initial state fetch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("http://10.0.0.5/", "10.0.0.5"),
        ("https://10.0.0.5/", "10.0.0.5"),
        ("  10.0.0.5  ", "10.0.0.5"),
        ("10.0.0.5///", "10.0.0.5"),  # rstrip("/") strips the whole run of trailing slashes
    ],
)
def test_init_cleans_device_address(raw, expected):
    with patch("notify.wled_handler.requests.get") as mock_get:
        mock_get.return_value = _ok_response({"name": "wled"})
        handler = WLEDNotificationHandler(_settings(device_address=raw))
    assert handler.device_address == expected


def test_init_fetches_initial_state_success():
    with patch("notify.wled_handler.requests.get") as mock_get:
        mock_get.return_value = _ok_response({"name": "wled", "ver": "0.14"})
        handler = WLEDNotificationHandler(_settings())
    assert handler.state == {"name": "wled", "ver": "0.14"}
    assert handler.last_mode is None
    assert handler.notify_duration == 1


def test_init_state_is_none_when_device_unreachable():
    with patch("notify.wled_handler.requests.get") as mock_get:
        mock_get.side_effect = requests.RequestException("unreachable")
        handler = WLEDNotificationHandler(_settings())
    assert handler.state is None


# ---------------------------------------------------------------------------
# get_control_mode
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("use_profiles", "use_suggested_presets", "expected"),
    [
        (True, True, "profiles"),  # profiles take priority when both are on
        (False, True, "suggested"),
        (False, False, "traditional"),
    ],
)
def test_get_control_mode(use_profiles, use_suggested_presets, expected):
    handler = _make_handler()
    handler.config = {"use_profiles": use_profiles, "use_suggested_presets": use_suggested_presets}
    assert handler.get_control_mode() == expected


# ---------------------------------------------------------------------------
# get_info (HTTP GET boundary)
# ---------------------------------------------------------------------------


def test_get_info_success_returns_json():
    handler = _make_handler()
    with patch("notify.wled_handler.requests.get") as mock_get:
        mock_get.return_value = _ok_response({"name": "wled"})
        result = handler.get_info()
    mock_get.assert_called_once_with("http://1.2.3.4/json/info")
    assert result == {"name": "wled"}


def test_get_info_request_exception_returns_none():
    handler = _make_handler()
    with patch("notify.wled_handler.requests.get") as mock_get:
        mock_get.side_effect = requests.RequestException("timeout")
        result = handler.get_info()
    assert result is None


# ---------------------------------------------------------------------------
# send_notification (HTTP POST boundary)
# ---------------------------------------------------------------------------


def test_send_notification_posts_expected_url_and_payload():
    handler = _make_handler()
    with patch("notify.wled_handler.requests.post") as mock_post:
        mock_post.return_value = _ok_response()
        handler.send_notification(preset=7)
    mock_post.assert_called_once_with("http://1.2.3.4/json/state", json={"on": True, "bri": 128, "ps": 7}, timeout=5)


def test_send_notification_default_preset_is_1():
    handler = _make_handler()
    with patch("notify.wled_handler.requests.post") as mock_post:
        mock_post.return_value = _ok_response()
        handler.send_notification()
    assert mock_post.call_args.kwargs["json"]["ps"] == 1


def test_send_notification_updates_last_updated_even_on_success():
    handler = _make_handler()
    handler.last_updated = 0
    with patch("notify.wled_handler.requests.post") as mock_post:
        mock_post.return_value = _ok_response()
        handler.send_notification(preset=1)
    assert handler.last_updated > 0


def test_send_notification_request_exception_still_updates_last_updated():
    """last_updated is set unconditionally after the try/except, so even a
    failed request bumps it -- pins this (possibly surprising) behavior."""
    handler = _make_handler()
    handler.last_updated = 0
    with patch("notify.wled_handler.requests.post") as mock_post:
        mock_post.side_effect = requests.RequestException("down")
        handler.send_notification(preset=1)
    assert handler.last_updated > 0


def test_send_profile_notification_delegates_to_send_notification():
    handler = _make_handler()
    handler.send_notification = MagicMock()
    handler.send_profile_notification(42)
    handler.send_notification.assert_called_once_with(42)


# ---------------------------------------------------------------------------
# send_direct_command (HTTP POST boundary, payload construction)
# ---------------------------------------------------------------------------


def test_send_direct_command_minimal_payload_has_no_seg_when_no_options():
    handler = _make_handler()
    with patch("notify.wled_handler.requests.post") as mock_post:
        mock_post.return_value = _ok_response()
        handler.send_direct_command()
    payload = mock_post.call_args.kwargs["json"]
    assert payload == {"on": True, "transition": 0}
    assert "seg" not in payload


def test_send_direct_command_on_false():
    handler = _make_handler()
    with patch("notify.wled_handler.requests.post") as mock_post:
        mock_post.return_value = _ok_response()
        handler.send_direct_command(on=False)
    assert mock_post.call_args.kwargs["json"]["on"] is False


@pytest.mark.parametrize("raw,clamped", [(-10, 0), (300, 255), (128, 128)])
def test_send_direct_command_clamps_brightness(raw, clamped):
    handler = _make_handler()
    with patch("notify.wled_handler.requests.post") as mock_post:
        mock_post.return_value = _ok_response()
        handler.send_direct_command(brightness=raw)
    assert mock_post.call_args.kwargs["json"]["bri"] == clamped


@pytest.mark.parametrize("raw,clamped", [(-5, 0), (999, 255), (100, 100)])
def test_send_direct_command_clamps_speed_and_intensity(raw, clamped):
    handler = _make_handler()
    with patch("notify.wled_handler.requests.post") as mock_post:
        mock_post.return_value = _ok_response()
        handler.send_direct_command(speed=raw, intensity=raw)
    seg = mock_post.call_args.kwargs["json"]["seg"][0]
    assert seg["sx"] == clamped
    assert seg["ix"] == clamped


def test_send_direct_command_effect_name_maps_to_fx_number():
    handler = _make_handler()
    with patch("notify.wled_handler.requests.post") as mock_post:
        mock_post.return_value = _ok_response()
        handler.send_direct_command(effect="breathe")
    assert mock_post.call_args.kwargs["json"]["seg"][0]["fx"] == 2


def test_send_direct_command_effect_int_passed_through():
    handler = _make_handler()
    with patch("notify.wled_handler.requests.post") as mock_post:
        mock_post.return_value = _ok_response()
        handler.send_direct_command(effect=99)
    assert mock_post.call_args.kwargs["json"]["seg"][0]["fx"] == 99


def test_send_direct_command_unknown_effect_name_is_dropped():
    """An effect string that isn't in WLED_EFFECTS and isn't an int hits
    neither branch, so no 'fx' key is set -- silently ignored, not an error."""
    handler = _make_handler()
    with patch("notify.wled_handler.requests.post") as mock_post:
        mock_post.return_value = _ok_response()
        handler.send_direct_command(effect="not_a_real_effect")
    payload = mock_post.call_args.kwargs["json"]
    assert "seg" not in payload


def test_send_direct_command_color_name_maps_to_rgb():
    handler = _make_handler()
    with patch("notify.wled_handler.requests.post") as mock_post:
        mock_post.return_value = _ok_response()
        handler.send_direct_command(color="green")
    assert mock_post.call_args.kwargs["json"]["seg"][0]["col"] == [[0, 255, 0]]


def test_send_direct_command_rainbow_color_sets_no_col():
    """Rainbow is effect-driven; the color branch explicitly skips setting
    'col' for the special 'rainbow' color name."""
    handler = _make_handler()
    with patch("notify.wled_handler.requests.post") as mock_post:
        mock_post.return_value = _ok_response()
        handler.send_direct_command(color="rainbow", effect="rainbow")
    seg = mock_post.call_args.kwargs["json"]["seg"][0]
    assert "col" not in seg
    assert seg["fx"] == 9


def test_send_direct_command_rgb_list_color_passed_through():
    handler = _make_handler()
    with patch("notify.wled_handler.requests.post") as mock_post:
        mock_post.return_value = _ok_response()
        handler.send_direct_command(color=[10, 20, 30])
    assert mock_post.call_args.kwargs["json"]["seg"][0]["col"] == [[10, 20, 30]]


def test_send_direct_command_malformed_rgb_list_is_ignored():
    handler = _make_handler()
    with patch("notify.wled_handler.requests.post") as mock_post:
        mock_post.return_value = _ok_response()
        handler.send_direct_command(color=[1, 2])  # wrong length, not a known name
    payload = mock_post.call_args.kwargs["json"]
    assert "seg" not in payload


def test_send_direct_command_logs_json_response_when_text_present():
    handler = _make_handler()
    with patch("notify.wled_handler.requests.post") as mock_post:
        mock_post.return_value = _ok_response(json_data={"success": True}, text="{}")
        handler.send_direct_command(color="red")  # no exception == success path taken


def test_send_direct_command_falls_back_to_raw_text_on_bad_json():
    """response.text is truthy but response.json() raises -- hits the bare
    except: branch that logs the raw text instead."""
    handler = _make_handler()
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.text = "not-json"
    resp.json.side_effect = ValueError("no json")
    with patch("notify.wled_handler.requests.post") as mock_post:
        mock_post.return_value = resp
        handler.send_direct_command(color="red")  # must not raise


def test_send_direct_command_request_exception_logs_and_updates_last_updated():
    handler = _make_handler()
    handler.last_updated = 0
    with patch("notify.wled_handler.requests.post") as mock_post:
        mock_post.side_effect = requests.RequestException("down")
        handler.send_direct_command(color="red")
    assert handler.last_updated > 0


# ---------------------------------------------------------------------------
# send_suggested_preset (color/effect selection per grill state)
# ---------------------------------------------------------------------------


def test_idle_non_night_is_dim_white():
    handler = _make_handler()
    handler.send_direct_command = MagicMock()
    handler.send_suggested_preset("idle", {"idle_brightness": 20, "night_mode": False})
    kwargs = handler.send_direct_command.call_args.kwargs
    assert kwargs["color"] == "white"
    assert kwargs["effect"] == "solid"


def test_idle_night_mode_is_dim_amber():
    handler = _make_handler()
    handler.send_direct_command = MagicMock()
    handler.send_suggested_preset("idle", {"idle_brightness": 20, "night_mode": True})
    kwargs = handler.send_direct_command.call_args.kwargs
    assert kwargs["color"] == "amber"


@pytest.mark.parametrize(
    ("preset", "expected_kwargs"),
    [
        ("booting", {"color": "white", "effect": "breathe"}),
        ("preheat", {"color": "orange", "effect": "breathe"}),
        ("cooldown", {"color": "orange", "effect": "fade"}),
        ("target_reached", {"color": "green", "effect": "solid"}),
        # This one pins speed, not effect -- the odd row out.
        ("probe_alarm", {"color": "red", "speed": 100}),
        ("low_pellets", {"color": "yellow", "effect": "breathe"}),
        ("error", {"color": "red", "effect": "solid"}),
    ],
)
def test_send_suggested_preset(preset, expected_kwargs):
    handler = _make_handler()
    handler.send_direct_command = MagicMock()

    handler.send_suggested_preset(preset, {})

    kwargs = handler.send_direct_command.call_args.kwargs
    for key, value in expected_kwargs.items():
        assert kwargs[key] == value, f"{preset}: {key}"


def test_cooking_night_mode_dims_brightness():
    handler = _make_handler()
    handler.send_direct_command = MagicMock()
    handler.send_suggested_preset("cooking", {"cooking_color": "blue", "night_mode": True, "idle_brightness": 20})
    kwargs = handler.send_direct_command.call_args.kwargs
    assert kwargs["brightness"] == int(51 * 0.5)


def test_cooking_non_night_mode_full_brightness():
    handler = _make_handler()
    handler.send_direct_command = MagicMock()
    handler.send_suggested_preset("cooking", {"cooking_color": "blue"})
    assert handler.send_direct_command.call_args.kwargs["brightness"] == 255


def test_overshoot_alarm_is_red_blink():
    handler = _make_handler()
    handler.send_direct_command = MagicMock()
    handler.send_suggested_preset("overshoot_alarm", {})
    kwargs = handler.send_direct_command.call_args.kwargs
    assert kwargs["color"] == "red" and kwargs["effect"] == "blink" and kwargs["speed"] == 255


def test_timer_done_is_rainbow_with_no_explicit_color():
    handler = _make_handler()
    handler.send_direct_command = MagicMock()
    handler.send_suggested_preset("timer_done", {})
    kwargs = handler.send_direct_command.call_args.kwargs
    assert kwargs["effect"] == "rainbow"
    assert "color" not in kwargs


def test_unknown_state_defaults_to_idle():
    handler = _make_handler()
    handler.send_direct_command = MagicMock()
    handler.send_suggested_preset("totally_bogus_state", {"night_mode": False})
    # Recurses into state == "idle"
    kwargs = handler.send_direct_command.call_args.kwargs
    assert kwargs["color"] == "white" and kwargs["effect"] == "solid"


# ---------------------------------------------------------------------------
# notify() dispatch
# ---------------------------------------------------------------------------


def test_notify_dispatches_to_profiles_handler():
    handler = _make_handler()
    handler.config = {"use_profiles": True, "use_suggested_presets": False}
    handler._notify_profiles = MagicMock()
    handler._notify_suggested = MagicMock()
    handler._notify_traditional = MagicMock()
    handler.notify("Test_Notify", {"mode": "Startup"}, {})
    handler._notify_profiles.assert_called_once_with("Test_Notify", {"mode": "Startup"}, {})
    handler._notify_suggested.assert_not_called()
    handler._notify_traditional.assert_not_called()


def test_notify_dispatches_to_suggested_handler():
    handler = _make_handler()
    handler.config = {"use_profiles": False, "use_suggested_presets": True}
    handler._notify_profiles = MagicMock()
    handler._notify_suggested = MagicMock()
    handler._notify_traditional = MagicMock()
    handler.notify("Test_Notify", {"mode": "Startup"}, {})
    handler._notify_suggested.assert_called_once()
    handler._notify_profiles.assert_not_called()
    handler._notify_traditional.assert_not_called()


def test_notify_dispatches_to_traditional_handler_by_default():
    handler = _make_handler()
    handler.config = {"use_profiles": False, "use_suggested_presets": False}
    handler._notify_profiles = MagicMock()
    handler._notify_suggested = MagicMock()
    handler._notify_traditional = MagicMock()
    handler.notify("Test_Notify", {"mode": "Startup"}, {})
    handler._notify_traditional.assert_called_once()
    handler._notify_profiles.assert_not_called()
    handler._notify_suggested.assert_not_called()


# ---------------------------------------------------------------------------
# _notify_profiles
# ---------------------------------------------------------------------------


def _profiles_handler():
    handler = _make_handler()
    handler.last_updated = 0
    handler.notify_duration = 1
    handler.last_mode = None
    handler.config = {"notify_duration": 120}
    handler.profile_manager = MagicMock()
    handler.profile_manager.get_profile_number_for_state.return_value = 4
    handler.profile_manager.get_profile_number_for_event.return_value = 6
    handler.send_profile_notification = MagicMock()
    return handler


def test_notify_profiles_grill_state_change_sends_preset():
    handler = _profiles_handler()
    handler._notify_profiles("GRILL_STATE", {"mode": "Smoke"}, {})
    handler.profile_manager.get_profile_number_for_state.assert_called_once_with("Smoke")
    handler.send_profile_notification.assert_called_once_with(4)
    assert handler.last_mode == "Smoke"
    assert handler.notify_duration == 1


def test_notify_profiles_grill_state_same_mode_is_noop():
    handler = _profiles_handler()
    handler.last_mode = "Smoke"
    handler._notify_profiles("GRILL_STATE", {"mode": "Smoke"}, {})
    handler.send_profile_notification.assert_not_called()


def test_notify_profiles_grill_state_control_none_logs_warning_no_crash():
    handler = _profiles_handler()
    handler._notify_profiles("GRILL_STATE", None, {})  # must not raise
    handler.send_profile_notification.assert_not_called()


def test_notify_profiles_grill_state_within_cooldown_is_skipped():
    handler = _profiles_handler()
    handler.last_updated = __import__("time").time()  # "just now" -> cooldown active
    handler.notify_duration = 120
    handler._notify_profiles("GRILL_STATE", {"mode": "Smoke"}, {})
    handler.send_profile_notification.assert_not_called()


def test_notify_profiles_test_notify_uses_startup_profile():
    handler = _profiles_handler()
    handler._notify_profiles("Test_Notify", {"mode": "Smoke"}, {})
    handler.profile_manager.get_profile_number_for_state.assert_called_once_with("Startup")
    handler.send_profile_notification.assert_called_once_with(4)


def test_notify_profiles_event_sets_cooldown_and_resets_last_mode():
    handler = _profiles_handler()
    handler.last_mode = "Smoke"
    handler._notify_profiles("Grill_Error", {"mode": "Smoke"}, {})
    handler.profile_manager.get_profile_number_for_event.assert_called_once_with("Grill_Error")
    handler.send_profile_notification.assert_called_once_with(6)
    assert handler.notify_duration == 120
    assert handler.last_mode is None


# ---------------------------------------------------------------------------
# _notify_suggested
# ---------------------------------------------------------------------------


def _suggested_handler():
    handler = _make_handler()
    handler.last_updated = 0
    handler.notify_duration = 1
    handler.last_mode = None
    handler.config = {"notify_duration": 120, "suggested_config": {"cooking_color": "blue"}}
    handler.send_suggested_preset = MagicMock()
    return handler


@pytest.mark.parametrize(
    "mode,expected_state",
    [
        ("Stop", "idle"),
        ("Startup", "booting"),
        ("Prime", "booting"),
        ("Reignite", "preheat"),
        ("Smoke", "cooking"),
        ("Hold", "cooking"),
        ("Shutdown", "cooldown"),
        ("Monitor", "idle"),  # unmapped mode falls through to the else -> idle
    ],
)
def test_notify_suggested_grill_state_maps_mode_to_state(mode, expected_state):
    handler = _suggested_handler()
    handler._notify_suggested("GRILL_STATE", {"mode": mode}, {})
    handler.send_suggested_preset.assert_called_once()
    assert handler.send_suggested_preset.call_args.args[0] == expected_state


def test_notify_suggested_grill_state_control_none_no_crash():
    handler = _suggested_handler()
    handler._notify_suggested("GRILL_STATE", None, {})
    handler.send_suggested_preset.assert_not_called()


def test_notify_suggested_grill_state_same_mode_is_noop():
    handler = _suggested_handler()
    handler.last_mode = "Smoke"
    handler._notify_suggested("GRILL_STATE", {"mode": "Smoke"}, {})
    handler.send_suggested_preset.assert_not_called()


@pytest.mark.parametrize(
    "event,expected_state",
    [
        ("Test_Notify", "booting"),
        ("Probe_Temp_Achieved", "target_reached"),
        ("Probe_Temp_Limit_Alarm_High", "probe_alarm"),
        ("Timer_Expired", "timer_done"),
        ("Pellet_Level_Low", "low_pellets"),
        ("Grill_Warning_Fault", "low_pellets"),
        ("Recipe_Step_Message", "target_reached"),
        ("Grill_Error", "error"),
        ("Control_Process_Stopped", "error"),
    ],
)
def test_notify_suggested_event_maps_to_state(event, expected_state):
    handler = _suggested_handler()
    handler._notify_suggested(event, {"mode": "Smoke"}, {})
    handler.send_suggested_preset.assert_called_once()
    assert handler.send_suggested_preset.call_args.args[0] == expected_state


def test_notify_suggested_event_sets_cooldown_and_resets_last_mode():
    handler = _suggested_handler()
    handler.last_mode = "Smoke"
    handler._notify_suggested("Timer_Expired", {"mode": "Smoke"}, {})
    assert handler.notify_duration == 120
    assert handler.last_mode is None


def test_notify_suggested_unrecognized_event_sends_nothing_but_still_sets_cooldown():
    """An event matching none of the elif branches falls through with no
    preset sent, but the trailing 'if notifyevent != GRILL_STATE' block still
    runs unconditionally, resetting cooldown/last_mode."""
    handler = _suggested_handler()
    handler.last_mode = "Smoke"
    handler._notify_suggested("Some_Unrelated_Event", {"mode": "Smoke"}, {})
    handler.send_suggested_preset.assert_not_called()
    assert handler.notify_duration == 120
    assert handler.last_mode is None


# ---------------------------------------------------------------------------
# _notify_traditional
# ---------------------------------------------------------------------------


def _traditional_handler():
    handler = _make_handler()
    handler.last_updated = 0
    handler.notify_duration = 1
    handler.last_mode = None
    handler.config = {
        "notify_duration": 120,
        "mode_presets": {"Startup": 2, "Smoke": 4},
        "event_presets": {
            "Temp_Achieved": 6,
            "Timer_Expired": 7,
            "Pellet_Level_Low": 8,
            "Grill_Error": 9,
            "Recipe_Next": 10,
        },
    }
    handler.send_notification = MagicMock()
    return handler


def test_notify_traditional_grill_state_sends_configured_preset():
    handler = _traditional_handler()
    handler._notify_traditional("GRILL_STATE", {"mode": "Smoke"}, {})
    handler.send_notification.assert_called_once_with(4)
    assert handler.last_mode == "Smoke"


def test_notify_traditional_grill_state_unconfigured_mode_sends_nothing_but_updates_last_mode():
    """When the new mode has no entry in mode_presets, preset stays the -1
    sentinel and no notification is sent -- but last_mode is still updated,
    so the state change won't be re-evaluated on the next tick."""
    handler = _traditional_handler()
    handler._notify_traditional("GRILL_STATE", {"mode": "Monitor"}, {})
    handler.send_notification.assert_not_called()
    assert handler.last_mode == "Monitor"


def test_notify_traditional_grill_state_control_none_no_crash():
    handler = _traditional_handler()
    handler._notify_traditional("GRILL_STATE", None, {})
    handler.send_notification.assert_not_called()


def test_notify_traditional_grill_state_same_mode_is_noop():
    handler = _traditional_handler()
    handler.last_mode = "Smoke"
    handler._notify_traditional("GRILL_STATE", {"mode": "Smoke"}, {})
    handler.send_notification.assert_not_called()


@pytest.mark.parametrize(
    "event,expected_preset",
    [
        ("Test_Notify", 2),
        ("Probe_Temp_Achieved", 6),
        ("Probe_Temp_Limit_Alarm_High", 9),
        ("Timer_Expired", 7),
        ("Pellet_Level_Low", 8),
        ("Grill_Warning_Fault", 8),
        ("Recipe_Step_Message", 10),
        ("Grill_Error", 9),
        ("Control_Process_Stopped", 9),
    ],
)
def test_notify_traditional_event_sends_configured_preset(event, expected_preset):
    handler = _traditional_handler()
    handler._notify_traditional(event, {"mode": "Smoke"}, {})
    handler.send_notification.assert_called_once_with(expected_preset)


def test_notify_traditional_event_missing_from_config_sends_nothing():
    handler = _traditional_handler()
    handler.config["event_presets"] = {}  # nothing configured -> all lookups fall back to -1
    handler._notify_traditional("Timer_Expired", {"mode": "Smoke"}, {})
    handler.send_notification.assert_not_called()


def test_notify_traditional_event_sets_cooldown_and_resets_last_mode():
    handler = _traditional_handler()
    handler.last_mode = "Smoke"
    handler._notify_traditional("Timer_Expired", {"mode": "Smoke"}, {})
    assert handler.notify_duration == 120
    assert handler.last_mode is None


def test_notify_traditional_unrecognized_event_sends_nothing_and_leaves_cooldown_untouched():
    """Unlike _notify_suggested, the cooldown/last_mode reset here is nested
    inside 'if preset != -1', so an event matching no branch (preset stays
    -1) sends nothing AND does not reset cooldown/last_mode."""
    handler = _traditional_handler()
    handler.last_mode = "Smoke"
    handler.notify_duration = 1
    handler._notify_traditional("Some_Unrelated_Event", {"mode": "Smoke"}, {})
    handler.send_notification.assert_not_called()
    assert handler.notify_duration == 1
    assert handler.last_mode == "Smoke"


def test_cooking_uses_configured_green_color():
    handler = _make_handler()
    handler.send_direct_command = MagicMock()

    handler.send_suggested_preset("cooking", {"cooking_color": "green"})

    assert handler.send_direct_command.called
    assert handler.send_direct_command.call_args.kwargs["color"] == "green"


def test_cooking_defaults_to_blue_when_unconfigured():
    handler = _make_handler()
    handler.send_direct_command = MagicMock()

    handler.send_suggested_preset("cooking", {})

    assert handler.send_direct_command.call_args.kwargs["color"] == "blue"


def test_cooking_does_not_hardcode_orange_cooking():
    """Old buggy behavior always passed color='orange_cooking'; this pins the fix."""
    handler = _make_handler()
    handler.send_direct_command = MagicMock()

    handler.send_suggested_preset("cooking", {"cooking_color": "green"})

    assert handler.send_direct_command.call_args.kwargs["color"] != "orange_cooking"
