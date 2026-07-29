"""Notify targets convert on a temperature-units change (#35).

`convert_settings_units` only touches `settings`; a notify target lives in
`control["notify_data"]` and used to survive a units change unchanged -- a 203 F
target read as the number 203 after a switch to Celsius. `_cmd_set_units` now
also converts every armed notify target, expressed as addressed `notify.set`
ops so a notify write landing in the same control cycle is not clobbered.

These tests pin the pure op-builder (`notify_target_conversion_ops`) and its
drain round-trip. The all-zero no-op is what keeps the units golden
(tests/characterization/test_process_command_golden.py) untouched: default
control carries no armed target, so the writer emits no extra delta.
"""

from common.common import notify_target_conversion_ops
from common.control_delta import apply_control_delta, control_delta


def _probe_notify(target_probe=0, target_high=0, target_low=0):
    """The three entries a probe owns, plus a timer entry that has no target."""
    return [
        {"label": "Grill", "type": "probe", "req": True, "target": target_probe},
        {"label": "Grill", "type": "probe_limit_high", "req": True, "target": target_high},
        {"label": "Grill", "type": "probe_limit_low", "req": True, "target": target_low},
        {"label": "Timer", "type": "timer", "req": False, "shutdown": False, "keep_warm": False},
    ]


def test_converts_probe_and_both_limit_targets_fahrenheit_to_celsius():
    ops = notify_target_conversion_ops(_probe_notify(203, 250, 150), "C")
    # int((t - 32) * 5/9): 203->95, 250->121, 150->65
    assert [(o["type"], o["fields"]["target"]) for o in ops] == [
        ("probe", 95),
        ("probe_limit_high", 121),
        ("probe_limit_low", 65),
    ]
    assert all(o["op"] == "notify.set" and o["label"] == "Grill" for o in ops)


def test_converts_celsius_to_fahrenheit():
    ops = notify_target_conversion_ops(_probe_notify(95, 121, 65), "F")
    # int(t * 9/5 + 32): 95->203, 121->249, 65->149
    assert [o["fields"]["target"] for o in ops] == [203, 249, 149]


def test_skips_the_zero_off_sentinel_and_entries_without_a_target():
    # target 0 means "no target set"; converting it (convert_temp("C", 0) = -17)
    # would fabricate a garbage below-freezing target. Timer/hopper carry no
    # target key at all. Both must be left alone.
    ops = notify_target_conversion_ops(_probe_notify(0, 250, 0), "C")
    assert [o["type"] for o in ops] == ["probe_limit_high"]


def test_all_zero_control_yields_no_ops():
    # This is the property the units golden depends on: default control has no
    # armed target, so the writer adds no delta and the frozen fixture holds.
    assert notify_target_conversion_ops(_probe_notify(0, 0, 0), "C") == []
    assert notify_target_conversion_ops([], "F") == []


def test_ops_apply_cleanly_and_touch_only_the_target():
    control = {"notify_data": _probe_notify(203, 250, 150)}
    apply_control_delta(control, control_delta(ops=notify_target_conversion_ops(control["notify_data"], "C")))
    by_type = {e["type"]: e for e in control["notify_data"]}
    assert by_type["probe"]["target"] == 95
    assert by_type["probe_limit_high"]["target"] == 121
    assert by_type["probe_limit_low"]["target"] == 65
    # req and the timer entry are untouched -- only `target` moved.
    assert by_type["probe"]["req"] is True
    assert by_type["timer"] == {
        "label": "Timer",
        "type": "timer",
        "req": False,
        "shutdown": False,
        "keep_warm": False,
    }
