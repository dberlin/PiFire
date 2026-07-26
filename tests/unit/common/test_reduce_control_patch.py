"""Unit contract for common.common.reduce_control_patch.

The behavioural cases -- real writers losing each other's changes through the
real queue-and-drain path -- live in
tests/characterization/test_control_writes_cross_writer.py. This file pins the
reduction's own rules. The coupled-member exclusion it used to carry is
gone: no writer computes a whole timer state any more (common/control_delta.py).
"""

from common.common import reduce_control_patch


def test_members_equal_to_the_ancestor_are_dropped():
    base = {"mode": "Stop", "s_plus": False, "primary_setpoint": 0}
    patch = {"mode": "Stop", "s_plus": True, "primary_setpoint": 0}
    assert reduce_control_patch(patch, base) == {"s_plus": True}


def test_members_that_differ_are_kept():
    assert reduce_control_patch({"mode": "Hold"}, {"mode": "Stop"}) == {"mode": "Hold"}


def test_a_member_the_ancestor_lacks_is_always_kept():
    # The writer is adding it, so it is necessarily a change.
    assert reduce_control_patch({"system": {"cpu_temp": 40}}, {}) == {"system": {"cpu_temp": 40}}


def test_nested_objects_are_reduced_member_by_member():
    base = {"manual": {"change": False, "output": False, "pwm": 100}}
    patch = {"manual": {"change": "fan", "output": False, "pwm": 100}}
    assert reduce_control_patch(patch, base) == {"manual": {"change": "fan"}}


def test_a_nested_object_identical_to_the_ancestor_is_dropped_entirely():
    base = {"manual": {"change": False, "pwm": 100}, "s_plus": False}
    patch = {"manual": {"change": False, "pwm": 100}, "s_plus": True}
    assert reduce_control_patch(patch, base) == {"s_plus": True}


def test_reduction_is_recursive():
    base = {"a": {"b": {"c": 1, "d": 2}}}
    patch = {"a": {"b": {"c": 1, "d": 9}}}
    assert reduce_control_patch(patch, base) == {"a": {"b": {"d": 9}}}


def test_an_array_identical_to_the_ancestor_is_dropped():
    # notify_data's cheap path: the writer plainly did not touch it, so it must
    # not be imposed over an earlier writer's changes in the same cycle.
    base = {"notify_data": [{"label": "Grill", "type": "probe", "req": False}]}
    assert reduce_control_patch(dict(base), base) == {}


def test_an_array_that_differs_survives_for_the_element_merge():
    base = {"notify_data": [{"label": "Grill", "type": "probe", "req": False}]}
    patch = {"notify_data": [{"label": "Grill", "type": "probe", "req": True}]}
    assert reduce_control_patch(patch, base) == patch


def test_a_type_change_is_a_change():
    assert reduce_control_patch({"x": {"a": 1}}, {"x": 5}) == {"x": {"a": 1}}
    assert reduce_control_patch({"x": 5}, {"x": {"a": 1}}) == {"x": 5}


def test_the_patch_is_not_aliased_to_its_input():
    patch = {"manual": {"pwm": 40}}
    reduced = reduce_control_patch(patch, {"manual": {"pwm": 100}})
    reduced["manual"]["pwm"] = 1
    assert patch["manual"]["pwm"] == 40


def test_a_non_mapping_ancestor_disables_reduction():
    assert reduce_control_patch({"a": 1}, None) == {"a": 1}


# --- the coupled-member exclusion, DELETED -----------------------------------


def test_reduce_control_patch_no_longer_special_cases_timer():
    """CONTROL_COUPLED_MEMBERS existed because two writers could each compute a
    whole timer state from a stale read. No writer can any more: the REST and
    socket timer paths emit ops (common/control_delta.py) and both
    arbitrary-patch doors refuse a `timer` value. A legacy patch that still
    carries one is a stale snapshot, and reducing it member-wise is now strictly
    better than imposing it whole."""
    base = {"timer": {"start": 1000.0, "paused": 0, "end": 2000.0}}
    patch = {"timer": {"start": 1000.0, "paused": 1700000000.0, "end": 2000.0}}
    assert reduce_control_patch(patch, base) == {"timer": {"paused": 1700000000.0}}


def test_a_timer_identical_to_the_ancestor_is_still_dropped():
    base = {"timer": {"start": 0, "paused": 0, "end": 0}, "s_plus": False}
    patch = {"timer": {"start": 0, "paused": 0, "end": 0}, "s_plus": True}
    assert reduce_control_patch(patch, base) == {"s_plus": True}


def test_a_member_named_timer_deeper_in_the_tree_was_always_ordinary():
    base = {"recipe": {"step_data": {"timer": {"a": 1, "b": 2}}}}
    patch = {"recipe": {"step_data": {"timer": {"a": 1, "b": 9}}}}
    assert reduce_control_patch(patch, base) == {"recipe": {"step_data": {"timer": {"b": 9}}}}
