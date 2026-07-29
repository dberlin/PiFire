"""Coverage for the two wizard helpers api_wizard still calls.

`parse_bt_device_info` warns the user when a discovered Bluetooth probe is
already claimed by a configured device, and `get_settings_dependencies_values`
has to survive a settings tree that does not carry the key yet -- the normal
case during a first-time install, where a crash would strand the wizard.
"""

from unittest.mock import patch

from blueprints.wizard.wizard import get_settings_dependencies_values, parse_bt_device_info


def _settings_with_hardware_id(hardware_id):
    return {
        "probe_settings": {
            "probe_map": {
                "probe_devices": [
                    {"device": "ThermoWorks_0", "name": "ThermoWorks", "config": {"hardware_id": hardware_id}}
                ]
            }
        }
    }


def test_parse_bt_device_info_flags_a_hardware_id_already_in_use(ds):
    devices = [{"hw_id": "AA:BB:CC", "info": "Signal -60dBm. "}]
    with patch("blueprints.wizard.wizard.read_settings", return_value=_settings_with_hardware_id("AA:BB:CC")):
        rows = parse_bt_device_info(devices)
    # The user needs to know why picking this device again will not work.
    assert "already in use by ThermoWorks_0" in rows[0]["info"]


def test_parse_bt_device_info_leaves_an_unclaimed_device_alone(ds):
    devices = [{"hw_id": "11:22:33", "info": "Signal -60dBm. "}]
    with patch("blueprints.wizard.wizard.read_settings", return_value=_settings_with_hardware_id("AA:BB:CC")):
        rows = parse_bt_device_info(devices)
    assert rows[0]["info"] == "Signal -60dBm. "


def test_parse_bt_device_info_handles_a_device_with_no_hardware_id(ds):
    # A configured device that carries no hardware_id must not match every
    # discovered peripheral -- get(...) returning None has to stay a non-match.
    devices = [{"hw_id": "11:22:33", "info": ""}]
    settings = {"probe_settings": {"probe_map": {"probe_devices": [{"device": "ADC_0", "name": "ADC", "config": {}}]}}}
    with patch("blueprints.wizard.wizard.read_settings", return_value=settings):
        rows = parse_bt_device_info(devices)
    assert rows[0]["info"] == ""


def test_settings_dependency_values_reads_a_nested_path():
    settings = {"platform": {"fan_controller": {"chip": "emc2101"}}}
    module_data = {"settings_dependencies": {"fan_chip": {"settings": ["platform", "fan_controller", "chip"]}}}
    assert get_settings_dependencies_values(settings, module_data) == {"fan_chip": "emc2101"}


def test_settings_dependency_values_falls_back_to_none_for_a_missing_key():
    # First-time setup: the platform's settings have never been written, so the
    # card still has to render with an empty value instead of raising.
    module_data = {"settings_dependencies": {"fan_chip": {"settings": ["platform", "fan_controller", "chip"]}}}
    assert get_settings_dependencies_values({}, module_data) == {"fan_chip": None}


def test_settings_dependency_values_falls_back_when_the_path_hits_a_non_mapping():
    # A stale scalar where the wizard expects a sub-tree indexes into a str/int
    # and raises TypeError rather than KeyError; both must degrade to None.
    settings = {"platform": {"fan_controller": 0}}
    module_data = {"settings_dependencies": {"fan_chip": {"settings": ["platform", "fan_controller", "chip"]}}}
    assert get_settings_dependencies_values(settings, module_data) == {"fan_chip": None}
