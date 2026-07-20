"""Unit tests for notify.wled_profiles.WLEDProfileManager.

Covers the full public surface: device-address cleanup in __init__,
profile-number merging (_get_profile_numbers), user-customization logic
(_apply_user_customizations), the HTTP boundary (get_device_info /
create_preset / delete_profile), the push_all_profiles / clear_all_pifire_profiles
orchestration (success/partial-failure/connect-failure result envelopes),
and the state/event -> preset-number lookup helpers. All HTTP calls are
mocked at ``notify.wled_profiles.requests`` -- no real device or network is
touched, and ``time.sleep`` is patched so tests don't pay the real 0.1s/preset
delay.

Two latent bugs are documented (not fixed) and pinned by tests below, each
with a docstring citing file:line and severity:

1. ``push_all_profiles`` silently drops the "error" preset
   (notify/wled_profiles.py:79 vs :238, surfacing at :385) -- see
   ``test_push_all_profiles_error_state_silently_skipped_latent_bug``.
2. ``_apply_user_customizations`` mutates the shared module-level
   ``WLED_PROFILE_DEFINITIONS`` constant through a shallow ``.copy()``
   (notify/wled_profiles.py:349/388-392, mutation at :311/326) -- see
   ``test_apply_user_customizations_mutates_shared_global_definitions_latent_bug``.
"""

import copy
from unittest.mock import MagicMock, patch

import pytest
import requests

import notify.wled_profiles as wled_profiles
from notify.wled_profiles import (
    DEFAULT_PROFILE_NUMBERS,
    WLED_PROFILE_DEFINITIONS,
    WLEDProfileManager,
)


def _ok_response(json_data=None):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = json_data if json_data is not None else {}
    return resp


# ---------------------------------------------------------------------------
# __init__ / device address cleanup
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("http://10.0.0.5/", "10.0.0.5"),
        ("https://10.0.0.5/", "10.0.0.5"),
        ("  10.0.0.5  ", "10.0.0.5"),
        ("10.0.0.5/", "10.0.0.5"),
        ("wled.local", "wled.local"),
    ],
)
def test_device_address_cleanup(raw, expected):
    manager = WLEDProfileManager(raw)
    assert manager.device_address == expected


def test_init_default_settings_and_profile_numbers():
    manager = WLEDProfileManager("10.0.0.5")
    assert manager.settings == {}
    assert manager.profile_numbers == DEFAULT_PROFILE_NUMBERS


# ---------------------------------------------------------------------------
# _get_profile_numbers -- merging custom numbers with defaults
# ---------------------------------------------------------------------------


def test_get_profile_numbers_merges_custom_over_defaults():
    settings = {
        "notify_services": {
            "wled": {
                "profile_numbers": {"idle": 99, "cooking": 44, "brand_new_key": 5},
            }
        }
    }
    manager = WLEDProfileManager("10.0.0.5", settings=settings)

    # Overridden entries take the custom value.
    assert manager.profile_numbers["idle"] == 99
    assert manager.profile_numbers["cooking"] == 44
    # Untouched defaults survive the merge.
    assert manager.profile_numbers["booting"] == DEFAULT_PROFILE_NUMBERS["booting"]
    assert manager.profile_numbers["error"] == DEFAULT_PROFILE_NUMBERS["error"]
    # A custom-only key not present in the defaults is simply added.
    assert manager.profile_numbers["brand_new_key"] == 5


def test_get_profile_numbers_no_wled_settings_falls_back_to_defaults():
    manager = WLEDProfileManager("10.0.0.5", settings={"notify_services": {}})
    assert manager.profile_numbers == DEFAULT_PROFILE_NUMBERS


# ---------------------------------------------------------------------------
# _apply_user_customizations
# ---------------------------------------------------------------------------


def _profile_data_copy(name):
    profile_def = WLED_PROFILE_DEFINITIONS[name]
    data = profile_def["config"].copy()
    data["name"] = profile_def["name"]
    return data


def test_apply_customizations_cooking_color_green():
    manager = WLEDProfileManager(
        "10.0.0.5",
        settings={"notify_services": {"wled": {"suggested_config": {"cooking_color": "green"}}}},
    )
    data = _profile_data_copy("cooking")
    try:
        result = manager._apply_user_customizations("cooking", data)
        assert result["seg"][0]["col"] == [[0, 255, 0]]
    finally:
        WLED_PROFILE_DEFINITIONS["cooking"]["config"]["seg"][0]["col"] = [wled_profiles.WLED_COLORS["blue"]]


def test_apply_customizations_cooking_color_default_blue():
    manager = WLEDProfileManager("10.0.0.5", settings={})
    data = _profile_data_copy("cooking")
    try:
        result = manager._apply_user_customizations("cooking", data)
        assert result["seg"][0]["col"] == [[0, 0, 255]]
    finally:
        WLED_PROFILE_DEFINITIONS["cooking"]["config"]["seg"][0]["col"] = [wled_profiles.WLED_COLORS["blue"]]


def test_apply_customizations_idle_brightness_conversion():
    manager = WLEDProfileManager(
        "10.0.0.5",
        settings={"notify_services": {"wled": {"suggested_config": {"idle_brightness": 50}}}},
    )
    data = _profile_data_copy("idle")
    result = manager._apply_user_customizations("idle", data)
    # 50% -> int(50 * 2.55) == 127
    assert result["bri"] == 127


def test_apply_customizations_idle_brightness_default_20_percent():
    manager = WLEDProfileManager("10.0.0.5", settings={})
    data = _profile_data_copy("idle")
    result = manager._apply_user_customizations("idle", data)
    assert result["bri"] == int(20 * 2.55)


def test_apply_customizations_night_mode_idle_extra_dim_and_amber():
    manager = WLEDProfileManager(
        "10.0.0.5",
        settings={"notify_services": {"wled": {"suggested_config": {"idle_brightness": 20, "night_mode": True}}}},
    )
    data = _profile_data_copy("idle")
    try:
        result = manager._apply_user_customizations("idle", data)
        # bri first set to int(20*2.55)=51, then night mode extra-dims by 0.3
        assert result["bri"] == int(51 * 0.3)
        assert result["seg"][0]["col"] == [[255, 191, 0]]
    finally:
        WLED_PROFILE_DEFINITIONS["idle"]["config"]["seg"][0]["col"] = [wled_profiles.WLED_COLORS["white"]]


def test_apply_customizations_night_mode_cooking_dims_but_keeps_color():
    manager = WLEDProfileManager(
        "10.0.0.5",
        settings={"notify_services": {"wled": {"suggested_config": {"cooking_color": "green", "night_mode": True}}}},
    )
    data = _profile_data_copy("cooking")
    try:
        result = manager._apply_user_customizations("cooking", data)
        # night mode only dims cooking's brightness by 0.5; color customization
        # (green) from the cooking_color branch is left as-is.
        assert result["bri"] == int(255 * 0.5)
        assert result["seg"][0]["col"] == [[0, 255, 0]]
    finally:
        WLED_PROFILE_DEFINITIONS["cooking"]["config"]["seg"][0]["col"] = [wled_profiles.WLED_COLORS["blue"]]


def test_apply_customizations_night_mode_does_not_affect_other_profiles():
    manager = WLEDProfileManager(
        "10.0.0.5",
        settings={"notify_services": {"wled": {"suggested_config": {"night_mode": True}}}},
    )
    data = _profile_data_copy("booting")
    original = copy.deepcopy(data)
    result = manager._apply_user_customizations("booting", data)
    assert result == original  # night_mode block only applies to idle/cooking


def test_apply_customizations_other_profiles_untouched():
    manager = WLEDProfileManager("10.0.0.5", settings={})
    data = _profile_data_copy("booting")
    original = copy.deepcopy(data)
    result = manager._apply_user_customizations("booting", data)
    assert result == original


def test_apply_user_customizations_mutates_shared_global_definitions_latent_bug():
    """LATENT BUG (notify/wled_profiles.py:349 & :388-392, mutation at :311/:326)
    severity: medium.

    ``push_all_profiles``/callers build ``profile_data`` via
    ``profile_def["config"].copy()`` -- a *shallow* copy. The nested
    ``seg`` list (and the dict at ``seg[0]``) is NOT copied, so
    ``profile_data["seg"][0]["col"] = [...]`` in ``_apply_user_customizations``
    mutates the very same dict object that lives inside the module-level
    ``WLED_PROFILE_DEFINITIONS`` constant. Once a night_mode push happens,
    the "idle" preset's amber night-mode color leaks permanently into
    ``WLED_PROFILE_DEFINITIONS["idle"]`` and is never reset back to white on
    a later non-night-mode push, because the idle branch only touches "col"
    inside the night_mode block. This is state corruption shared across
    ``WLEDProfileManager`` instances/requests within the process, not merely
    within one manager's profile_data.
    """
    original_col = copy.deepcopy(WLED_PROFILE_DEFINITIONS["idle"]["config"]["seg"][0]["col"])
    try:
        manager = WLEDProfileManager(
            "10.0.0.5",
            settings={"notify_services": {"wled": {"suggested_config": {"night_mode": True}}}},
        )
        data = _profile_data_copy("idle")
        manager._apply_user_customizations("idle", data)

        # The bug: the *global* constant's nested seg/col was mutated too,
        # not just the local `data` dict.
        assert WLED_PROFILE_DEFINITIONS["idle"]["config"]["seg"][0]["col"] == [[255, 191, 0]]
        assert WLED_PROFILE_DEFINITIONS["idle"]["config"]["seg"][0]["col"] != original_col

        # Demonstrate the fallout: a later non-night-mode idle customization
        # still sees amber, because nothing in the non-night-mode idle branch
        # ever resets "col".
        manager_no_night = WLEDProfileManager("10.0.0.5", settings={})
        data2 = _profile_data_copy("idle")
        result2 = manager_no_night._apply_user_customizations("idle", data2)
        assert result2["seg"][0]["col"] == [[255, 191, 0]]  # still amber, not white!
    finally:
        WLED_PROFILE_DEFINITIONS["idle"]["config"]["seg"][0]["col"] = original_col


# ---------------------------------------------------------------------------
# get_device_info -- HTTP boundary
# ---------------------------------------------------------------------------


def test_get_device_info_success_returns_parsed_json():
    manager = WLEDProfileManager("10.0.0.5")
    with patch(
        "notify.wled_profiles.requests.get", return_value=_ok_response({"name": "MyWLED", "ver": "0.14"})
    ) as mock_get:
        result = manager.get_device_info()

    mock_get.assert_called_once_with("http://10.0.0.5/json/info", timeout=5)
    assert result == {"name": "MyWLED", "ver": "0.14"}


def test_get_device_info_request_exception_returns_none():
    manager = WLEDProfileManager("10.0.0.5")
    with patch("notify.wled_profiles.requests.get", side_effect=requests.exceptions.ConnectionError("refused")):
        result = manager.get_device_info()
    assert result is None


def test_get_device_info_http_error_returns_none():
    manager = WLEDProfileManager("10.0.0.5")
    bad_resp = MagicMock()
    bad_resp.raise_for_status.side_effect = requests.exceptions.HTTPError("500")
    with patch("notify.wled_profiles.requests.get", return_value=bad_resp):
        result = manager.get_device_info()
    assert result is None


# ---------------------------------------------------------------------------
# create_preset -- HTTP boundary + payload shape
# ---------------------------------------------------------------------------


def test_create_preset_success_builds_expected_payload_and_url():
    manager = WLEDProfileManager("10.0.0.5")
    profile_data = {
        "on": True,
        "bri": 51,
        "transition": 10,
        "seg": [{"fx": 0, "col": [[255, 255, 255]]}],
        "name": "PiFire - Idle",
    }

    with patch("notify.wled_profiles.requests.post", return_value=_ok_response()) as mock_post:
        result = manager.create_preset(1, profile_data)

    assert result is True
    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert args[0] == "http://10.0.0.5/json"
    assert kwargs["timeout"] == 10
    payload = kwargs["json"]
    assert payload["psave"] == 1
    assert payload["n"] == "PiFire - Idle"
    assert payload["ib"] is True
    assert payload["sb"] is True
    # Original state fields are preserved in the combined payload.
    assert payload["on"] is True
    assert payload["bri"] == 51
    assert payload["seg"] == [{"fx": 0, "col": [[255, 255, 255]]}]


def test_create_preset_does_not_mutate_caller_profile_data():
    manager = WLEDProfileManager("10.0.0.5")
    profile_data = {"on": True, "bri": 51, "transition": 10, "seg": [], "name": "PiFire - Idle"}
    original = copy.deepcopy(profile_data)

    with patch("notify.wled_profiles.requests.post", return_value=_ok_response()):
        manager.create_preset(1, profile_data)

    assert profile_data == original  # payload is a .copy(), psave/ib/sb not leaked back


def test_create_preset_request_exception_returns_false():
    manager = WLEDProfileManager("10.0.0.5")
    profile_data = {"name": "PiFire - Idle", "seg": []}
    with patch("notify.wled_profiles.requests.post", side_effect=requests.exceptions.Timeout("timed out")):
        result = manager.create_preset(1, profile_data)
    assert result is False


def test_create_preset_http_error_returns_false():
    manager = WLEDProfileManager("10.0.0.5")
    profile_data = {"name": "PiFire - Idle", "seg": []}
    bad_resp = MagicMock()
    bad_resp.raise_for_status.side_effect = requests.exceptions.HTTPError("500")
    with patch("notify.wled_profiles.requests.post", return_value=bad_resp):
        result = manager.create_preset(1, profile_data)
    assert result is False


# ---------------------------------------------------------------------------
# push_all_profiles -- orchestration + result envelope
# ---------------------------------------------------------------------------


def test_push_all_profiles_device_unreachable_returns_failure_envelope_without_posting():
    manager = WLEDProfileManager("10.0.0.5")
    with (
        patch("notify.wled_profiles.requests.get", side_effect=requests.exceptions.ConnectionError("refused")),
        patch("notify.wled_profiles.requests.post") as mock_post,
    ):
        result = manager.push_all_profiles()

    assert result == {"success": False, "message": "Could not connect to WLED device", "profiles_pushed": 0}
    mock_post.assert_not_called()


def test_push_all_profiles_success_envelope():
    manager = WLEDProfileManager("10.0.0.5")
    with (
        patch("notify.wled_profiles.requests.get", return_value=_ok_response({"name": "MyWLED"})),
        patch("notify.wled_profiles.requests.post", return_value=_ok_response()) as mock_post,
        patch("notify.wled_profiles.time.sleep"),
    ):
        result = manager.push_all_profiles()

    assert result["success"] is True
    assert result["profiles_pushed"] == 11  # see latent-bug test: "error" is silently skipped
    assert result["errors"] == []
    assert result["message"] == "Successfully created 11 profiles"
    assert mock_post.call_count == 11
    pushed_names = {p["name"] for p in result["profiles"]}
    assert pushed_names == set(DEFAULT_PROFILE_NUMBERS.keys()) - {"error"}


def test_push_all_profiles_custom_profile_numbers_overrides_settings():
    manager = WLEDProfileManager("10.0.0.5")
    with (
        patch("notify.wled_profiles.requests.get", return_value=_ok_response({"name": "MyWLED"})),
        patch("notify.wled_profiles.requests.post", return_value=_ok_response()) as mock_post,
        patch("notify.wled_profiles.time.sleep"),
    ):
        result = manager.push_all_profiles(custom_profile_numbers={"idle": 77})

    assert result["profiles_pushed"] == 1
    assert result["profiles"][0]["number"] == 77
    mock_post.assert_called_once()
    assert mock_post.call_args.kwargs["json"]["psave"] == 77


def test_push_all_profiles_partial_failure_reports_errors_and_success_false():
    manager = WLEDProfileManager("10.0.0.5")

    def post_side_effect(url, json=None, timeout=None):
        if json.get("n") == "PiFire - Preheat":
            raise requests.exceptions.ConnectionError("device busy")
        return _ok_response()

    with (
        patch("notify.wled_profiles.requests.get", return_value=_ok_response({"name": "MyWLED"})),
        patch("notify.wled_profiles.requests.post", side_effect=post_side_effect),
        patch("notify.wled_profiles.time.sleep"),
    ):
        result = manager.push_all_profiles()

    assert result["success"] is False
    assert result["profiles_pushed"] == 10  # 11 eligible - 1 failed
    assert result["errors"] == ["Failed to create preheat (preset 3)"]
    assert result["message"] == "Created 10 profiles with 1 errors"


def test_push_all_profiles_error_state_silently_skipped_latent_bug():
    """LATENT BUG (notify/wled_profiles.py:79 vs :238, surfacing at :385)
    severity: medium-high.

    ``DEFAULT_PROFILE_NUMBERS`` keys the error preset as "error" (line 79),
    but ``WLED_PROFILE_DEFINITIONS`` defines the same preset under the key
    "error_fault" (line 238) instead. ``push_all_profiles``'s membership
    guard ``if profile_name in WLED_PROFILE_DEFINITIONS`` (line 385) is
    therefore always False for "error", so the error/fault preset is
    silently never created on the WLED device -- no error is raised or
    logged; ``profiles_pushed`` is just 1 short of the full 12.

    This is functionally significant because
    ``get_profile_number_for_event()`` (line 485) *does* use the "error"
    key from ``self.profile_numbers`` and happily returns a preset number
    for Grill_Error / Control_Process_Stopped events. So at runtime the
    control process tells the WLED device to activate a preset that was
    never pushed -- the red error-alert LED state silently never displays.
    """
    manager = WLEDProfileManager("10.0.0.5")
    with (
        patch("notify.wled_profiles.requests.get", return_value=_ok_response({"name": "MyWLED"})),
        patch("notify.wled_profiles.requests.post", return_value=_ok_response()) as mock_post,
        patch("notify.wled_profiles.time.sleep"),
    ):
        result = manager.push_all_profiles()

    assert result["profiles_pushed"] == 11  # NOT 12, despite 12 entries in DEFAULT_PROFILE_NUMBERS
    pushed_names = {p["name"] for p in result["profiles"]}
    assert "error" not in pushed_names
    assert "error_fault" not in pushed_names  # the WLED_PROFILE_DEFINITIONS entry is never reached either

    posted_preset_names = {call.kwargs["json"]["n"] for call in mock_post.call_args_list}
    assert "PiFire - Error/Fault" not in posted_preset_names

    # Yet the number the manager will report for a real error event is a
    # perfectly well-formed preset number -- just one that was never created.
    error_preset_number = manager.get_profile_number_for_event("Grill_Error")
    assert error_preset_number == DEFAULT_PROFILE_NUMBERS["error"]
    assert error_preset_number not in {p["number"] for p in result["profiles"]}


# ---------------------------------------------------------------------------
# delete_profile / clear_all_pifire_profiles
# ---------------------------------------------------------------------------


def test_delete_profile_success_builds_expected_payload_and_url():
    manager = WLEDProfileManager("10.0.0.5")
    with patch("notify.wled_profiles.requests.post", return_value=_ok_response()) as mock_post:
        result = manager.delete_profile(7)

    assert result is True
    mock_post.assert_called_once_with("http://10.0.0.5/json/state", json={"pdel": 7}, timeout=5)


def test_delete_profile_request_exception_returns_false():
    manager = WLEDProfileManager("10.0.0.5")
    with patch("notify.wled_profiles.requests.post", side_effect=requests.exceptions.Timeout("timed out")):
        result = manager.delete_profile(7)
    assert result is False


def test_clear_all_pifire_profiles_success_envelope():
    manager = WLEDProfileManager("10.0.0.5")
    with patch.object(manager, "delete_profile", return_value=True) as mock_delete:
        result = manager.clear_all_pifire_profiles()

    assert result == {
        "success": True,
        "message": "Successfully deleted 12 profiles",
        "profiles_deleted": 12,
        "errors": [],
    }
    assert mock_delete.call_count == 12


def test_clear_all_pifire_profiles_partial_failure_envelope():
    manager = WLEDProfileManager("10.0.0.5")

    def delete_side_effect(preset_number):
        return preset_number != DEFAULT_PROFILE_NUMBERS["night_mode"]

    with patch.object(manager, "delete_profile", side_effect=delete_side_effect):
        result = manager.clear_all_pifire_profiles()

    assert result["success"] is False
    assert result["profiles_deleted"] == 11
    assert result["errors"] == [f"Failed to delete night_mode (preset {DEFAULT_PROFILE_NUMBERS['night_mode']})"]
    assert result["message"] == "Deleted 11 profiles with 1 errors"


# ---------------------------------------------------------------------------
# get_profile_number_for_state
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "state,expected_profile_name",
    [
        ("Stop", "idle"),
        ("Startup", "booting"),
        ("Prime", "booting"),
        ("Reignite", "preheat"),
        ("Smoke", "cooking"),
        ("Hold", "cooking"),
        ("Shutdown", "cooldown"),
        ("Some_Unknown_State", "idle"),
    ],
)
def test_get_profile_number_for_state(state, expected_profile_name):
    manager = WLEDProfileManager("10.0.0.5")
    assert manager.get_profile_number_for_state(state) == DEFAULT_PROFILE_NUMBERS[expected_profile_name]


def test_get_profile_number_for_state_uses_instance_profile_numbers():
    manager = WLEDProfileManager(
        "10.0.0.5", settings={"notify_services": {"wled": {"profile_numbers": {"cooking": 123}}}}
    )
    assert manager.get_profile_number_for_state("Smoke") == 123


# ---------------------------------------------------------------------------
# get_profile_number_for_event
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "event,expected_profile_name",
    [
        ("Probe_Temp_Achieved", "target_reached"),
        ("Probe_Temp_Limit_Alarm", "overshoot_alarm"),
        ("Timer_Expired", "timer_done"),
        ("Pellet_Level_Low", "low_pellets"),
        ("Grill_Warning", "low_pellets"),
        ("Recipe_Step_Message", "target_reached"),
        ("Grill_Error", "error"),
        ("Control_Process_Stopped", "error"),
    ],
)
def test_get_profile_number_for_event(event, expected_profile_name):
    manager = WLEDProfileManager("10.0.0.5")
    assert manager.get_profile_number_for_event(event) == DEFAULT_PROFILE_NUMBERS[expected_profile_name]


def test_get_profile_number_for_event_substring_match():
    """The lookup is a substring ("in") check, not an equality check, so any
    event string that merely *contains* a known keyword matches -- e.g. a
    namespaced event like "grill1.Grill_Error" would match "Grill_Error"."""
    manager = WLEDProfileManager("10.0.0.5")
    assert (
        manager.get_profile_number_for_event("some.prefix.Timer_Expired.suffix")
        == DEFAULT_PROFILE_NUMBERS["timer_done"]
    )


def test_get_profile_number_for_event_unknown_falls_back_to_error():
    manager = WLEDProfileManager("10.0.0.5")
    assert manager.get_profile_number_for_event("Totally_Unknown_Event") == DEFAULT_PROFILE_NUMBERS["error"]
