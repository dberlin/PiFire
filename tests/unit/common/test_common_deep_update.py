from common.common import deep_update


def test_deep_update_merges_a_mapping_that_carries_a_kind_key():
    # deep_update has no awareness of the settings domain's tagged-union
    # convention -- a "kind" key is just another key to merge, same as any
    # other. The settings domain (apply_settings_delta) is what knows to
    # treat such a value atomically; this utility stays generic.
    stored = {"bus": {"kind": "kernel", "serial": "AB12"}}
    result = deep_update(stored, {"bus": {"kind": "mcp2221"}})
    assert result == {"bus": {"kind": "mcp2221", "serial": "AB12"}}


def test_deep_update_still_merges_plain_nested_dicts():
    stored = {"nested": {"a": 1, "b": 2}}
    result = deep_update(stored, {"nested": {"b": 9, "c": 3}})
    assert result == {"nested": {"a": 1, "b": 9, "c": 3}}
