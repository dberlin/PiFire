import copy

from common.defaults import default_settings
import pytest

from common.settings_schema import (
    SettingsDeltaError,
    apply_settings_delta,
    settings_delta,
    validate_settings_delta,
    validate_settings_tree,
)


def test_apply_settings_delta_replaces_a_tagged_union_wholesale_on_kind_change():
    # A stored kernel bus addressed by serial, narrowed by a delta that only
    # names the new kind: the old kind's selector must not survive the merge.
    settings = copy.deepcopy(default_settings())
    settings["platform"]["devices"]["distance"]["i2c_bus"] = {"kind": "kernel", "serial": "AB12"}
    result = apply_settings_delta(settings, {"platform": {"devices": {"distance": {"i2c_bus": {"kind": "mcp2221"}}}}})
    bus = result["platform"]["devices"]["distance"]["i2c_bus"]
    assert bus == {"kind": "mcp2221"}
    assert "serial" not in bus


def test_apply_settings_delta_narrows_a_kernel_selector_to_basic():
    settings = copy.deepcopy(default_settings())
    settings["platform"]["fan_controller"]["i2c_bus"] = {"kind": "kernel", "adapter": "CP2112"}
    result = apply_settings_delta(settings, {"platform": {"fan_controller": {"i2c_bus": {"kind": "basic"}}}})
    assert result["platform"]["fan_controller"]["i2c_bus"] == {"kind": "basic"}


def test_apply_settings_delta_merges_an_ordinary_nested_scalar_without_clobbering_siblings():
    # Proves the atomic-path lift is scoped to the tagged-union field itself,
    # not "the whole section it lives in" -- otherwise this would be
    # indistinguishable from deep_update's old hack, just relocated.
    settings = copy.deepcopy(default_settings())
    settings["platform"]["devices"]["distance"]["i2c_bus"] = {"kind": "kernel", "adapter": "CP2112"}
    result = apply_settings_delta(settings, {"platform": {"devices": {"distance": {"echo": 99}}}})
    distance = result["platform"]["devices"]["distance"]
    assert distance["echo"] == 99
    assert distance["trig"] == 23
    assert distance["i2c_bus"] == {"kind": "kernel", "adapter": "CP2112"}


def test_a_bare_partial_still_only_merges_and_can_never_remove_a_key():
    settings = {"controller": {"config": {"mpc": {"C_f": 9.0, "C_c": 300.0}}}}

    result = apply_settings_delta(settings, {"controller": {"config": {"mpc": {"C_c": 350.0}}}})

    assert result["controller"]["config"]["mpc"] == {"C_f": 9.0, "C_c": 350.0}


def test_a_settings_delta_removes_exactly_the_paths_it_names():
    """Omission is silence, so removal needs saying. Without this an option a
    controller has retired outlives every save that leaves it out."""
    settings = {"controller": {"config": {"mpc": {"C_f": 9.0, "h_fc": 1.3, "C_c": 300.0}, "pid": {"PB": 60.0}}}}

    result = apply_settings_delta(
        settings,
        settings_delta(
            set_values={"controller": {"config": {"mpc": {"C_c": 350.0}}}},
            delete_paths=[["controller", "config", "mpc", "C_f"], ["controller", "config", "mpc", "h_fc"]],
        ),
    )

    assert result["controller"]["config"]["mpc"] == {"C_c": 350.0}
    assert result["controller"]["config"]["pid"] == {"PB": 60.0}


def test_deleting_a_path_that_is_not_there_is_already_satisfied():
    settings = {"controller": {"config": {"mpc": {"C_c": 300.0}}}}

    result = apply_settings_delta(
        settings,
        settings_delta(delete_paths=[["controller", "config", "mpc", "gone"], ["nothing", "here"]]),
    )

    assert result["controller"]["config"]["mpc"] == {"C_c": 300.0}


def test_settings_delta_deletes_a_onesignal_device_end_to_end():
    """Pins both ends of the seam NotificationsTab's delete-device save
    crosses: web-react's settingsDelta() builds exactly this envelope shape
    (M15), and a unit test on the payload alone proves the *shape* is right,
    not that the device is actually gone. This runs the real envelope through
    apply_settings_delta AND a validate_settings_tree round-trip, and checks
    the device is absent while its sibling device and the service's own
    fields survive."""
    settings = copy.deepcopy(default_settings())
    settings["notify_services"]["onesignal"] = {
        "enabled": True,
        "uuid": "some-uuid-string",
        "app_id": "app-id-123",
        "devices": {
            "player-id-aaa": {"friendly_name": "Kitchen", "device_name": "Pixel", "app_version": "1.0"},
            "player-id-bbb": {"friendly_name": "Garage", "device_name": "iPhone", "app_version": "1.1"},
        },
    }

    result = apply_settings_delta(
        settings,
        settings_delta(delete_paths=[["notify_services", "onesignal", "devices", "player-id-aaa"]]),
    )
    validated = validate_settings_tree(result, persisted=False)

    onesignal = validated["notify_services"]["onesignal"]
    assert "player-id-aaa" not in onesignal["devices"]
    assert onesignal["devices"]["player-id-bbb"] == {
        "friendly_name": "Garage",
        "device_name": "iPhone",
        "app_version": "1.1",
    }
    assert onesignal["enabled"] is True
    assert onesignal["uuid"] == "some-uuid-string"
    assert onesignal["app_id"] == "app-id-123"


@pytest.mark.parametrize(
    "envelope",
    [
        {"__settings_delta__": 2},
        {"__settings_delta__": 1, "unknown": {}},
        {"__settings_delta__": 1, "delete": "controller"},
        {"__settings_delta__": 1, "delete": [[]]},
        {"__settings_delta__": 1, "delete": [["controller", 7]]},
        {"__settings_delta__": 1, "set": []},
    ],
)
def test_a_malformed_settings_delta_is_refused_rather_than_half_applied(envelope):
    with pytest.raises(SettingsDeltaError):
        validate_settings_delta(envelope)
