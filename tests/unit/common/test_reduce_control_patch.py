"""Unit contract for common.common.reduce_control_patch.

The behavioural cases -- real writers losing each other's changes through the
real queue-and-drain path -- live in
tests/characterization/test_control_writes_cross_writer.py. This file pins the
reduction's own rules, including the coupled-member exclusion.
"""

from common.common import CONTROL_COUPLED_MEMBERS, reduce_control_patch


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


# --- coupled members --------------------------------------------------------


def test_timer_is_the_coupled_member():
    assert CONTROL_COUPLED_MEMBERS == frozenset({"timer"})


def test_a_coupled_member_is_kept_whole_when_any_part_differs():
    base = {"timer": {"start": 1000.0, "paused": 0, "end": 2000.0}}
    patch = {"timer": {"start": 1000.0, "paused": 1700.0, "end": 2000.0}}
    # NOT reduced to {"timer": {"paused": 1700.0}}: start/paused/end describe one
    # countdown, and merging them independently synthesizes states no writer
    # computed (see CONTROL_COUPLED_MEMBERS).
    assert reduce_control_patch(patch, base) == patch


def test_a_coupled_member_identical_to_the_ancestor_is_still_dropped():
    base = {"timer": {"start": 0, "paused": 0, "end": 0}, "s_plus": False}
    patch = {"timer": {"start": 0, "paused": 0, "end": 0}, "s_plus": True}
    assert reduce_control_patch(patch, base) == {"s_plus": True}


def test_coupling_does_not_apply_at_nested_levels():
    # A member that merely happens to be named 'timer' deeper in the tree is an
    # ordinary object.
    base = {"recipe": {"step_data": {"timer": {"a": 1, "b": 2}}}}
    patch = {"recipe": {"step_data": {"timer": {"a": 1, "b": 9}}}}
    assert reduce_control_patch(patch, base) == {"recipe": {"step_data": {"timer": {"b": 9}}}}
