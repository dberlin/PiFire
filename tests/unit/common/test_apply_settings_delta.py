import copy

from common.defaults import default_settings
from common.settings_schema import apply_settings_delta


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
