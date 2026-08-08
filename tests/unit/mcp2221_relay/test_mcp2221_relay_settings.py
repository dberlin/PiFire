import json

from common.defaults import default_settings
from common.settings_migration import _apply_shape_migrations
from common.settings_schema import SETTINGS_SCHEMA_VERSION, validate_settings_tree
from wizard import select_grillplat_module


def _selection(chip="none"):
    return {
        "modules": {"grillplat": "prototype"},
        "platform": {
            "system_type": "mcp2221_relay",
            "dc_fan": False,
            "fan_controller": {"chip": chip},
        },
    }


def test_defaults_and_schema_model_the_relay_adapter_serial_and_named_gpio_pins():
    settings = default_settings()
    assert settings["platform"]["mcp2221"] == {"serial": ""}

    settings["platform"]["system_type"] = "mcp2221_relay"
    settings["platform"]["outputs"].update({"power": "GP0", "igniter": "GP1", "auger": "GP2", "fan": "GP3"})
    validate_settings_tree(settings)


def test_schema_seven_migration_adds_the_mcp2221_selector():
    settings = {"schema_version": 7, "platform": {"current": "custom"}}

    assert _apply_shape_migrations(settings, SETTINGS_SCHEMA_VERSION) is True
    assert settings["schema_version"] == SETTINGS_SCHEMA_VERSION
    assert settings["platform"]["mcp2221"] == {"serial": ""}


def test_wizard_selects_mcp2221_relay_and_only_enables_dc_fan_for_pwm_chips():
    for chip, expected_dc_fan in (("none", False), ("emc2101", True), ("emc2301", True)):
        settings = _selection(chip)
        select_grillplat_module(settings)
        assert settings["modules"]["grillplat"] == "mcp2221_relay"
        assert settings["platform"]["dc_fan"] is expected_dc_fan


def test_manifest_registers_mcp2221_relay_gpio_and_independent_fan_bus_selection():
    with open("wizard/wizard_manifest.json") as handle:
        manifest = json.load(handle)

    entry = manifest["modules"]["grillplatform"]["mcp2221_relay"]
    assert entry["filename"] == "mcp2221_relay"
    assert any(dependency.startswith("easymcp2221") for dependency in entry["py_dependencies"])

    dependencies = entry["settings_dependencies"]
    assert dependencies["mcp2221_serial"]["settings"] == ["platform", "mcp2221", "serial"]
    assert dependencies["mcp2221_serial"]["type"] == "mcp2221_serial"
    assert dependencies["fan_i2c_bus"]["type"] == "i2c_bus"
    assert dependencies["fan_i2c_bus"]["settings"] == ["platform", "fan_controller", "i2c_bus"]

    for output in ("power", "igniter", "auger", "fan"):
        assert set(dependencies[f"output_{output}"]["options"]) == {"GP0", "GP1", "GP2", "GP3"}
