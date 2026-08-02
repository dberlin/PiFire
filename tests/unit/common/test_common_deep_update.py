from common.common import deep_update


def test_deep_update_replaces_a_tagged_union_wholesale_on_kind_change():
    # A stored kernel bus addressed by serial, narrowed by a delta that only
    # names the new kind: the old kind's selector must not survive the merge.
    stored = {"bus": {"kind": "kernel", "serial": "AB12"}}
    result = deep_update(stored, {"bus": {"kind": "mcp2221"}})
    assert result == {"bus": {"kind": "mcp2221"}}
    assert "serial" not in result["bus"]


def test_deep_update_replaces_a_tagged_union_wholesale_when_nested_in_a_settings_tree():
    settings = {"platform": {"devices": {"distance": {"i2c_bus": {"kind": "kernel", "serial": "AB12"}}}}}
    delta = {"platform": {"devices": {"distance": {"i2c_bus": {"kind": "mcp2221"}}}}}
    result = deep_update(settings, delta)
    bus = result["platform"]["devices"]["distance"]["i2c_bus"]
    assert bus == {"kind": "mcp2221"}
    assert "serial" not in bus


def test_deep_update_narrows_a_kernel_selector_to_basic():
    stored = {"bus": {"kind": "kernel", "adapter": "CP2112"}}
    result = deep_update(stored, {"bus": {"kind": "basic"}})
    assert result["bus"] == {"kind": "basic"}


def test_deep_update_still_merges_plain_nested_dicts():
    # The tagged-union replace path must not swallow ordinary section merges.
    stored = {"nested": {"a": 1, "b": 2}}
    result = deep_update(stored, {"nested": {"b": 9, "c": 3}})
    assert result == {"nested": {"a": 1, "b": 9, "c": 3}}
