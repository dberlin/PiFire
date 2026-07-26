"""The delta envelope is a CROSS-PROCESS wire format: the web process builds it,
the control process reads it. Both ends are pinned here."""

import pytest

from common.common import WriteKind
from common.control_delta import (
    CONTROL_DELTA_KEY,
    CONTROL_DELTA_VERSION,
    ControlDeltaError,
    control_delta,
    is_control_delta,
    validate_control_delta,
)


def test_write_kind_has_a_delta_member_distinct_from_merge():
    assert WriteKind.DELTA is not WriteKind.MERGE
    assert WriteKind.DELTA is not WriteKind.OVERWRITE


def test_a_set_only_delta_has_exactly_the_expected_wire_shape():
    assert control_delta(set_values={"mode": "Hold", "primary_setpoint": 225}) == {
        CONTROL_DELTA_KEY: CONTROL_DELTA_VERSION,
        "set": {"mode": "Hold", "primary_setpoint": 225},
    }


def test_empty_members_are_omitted_not_emitted_as_empty_containers():
    assert control_delta(set_values={"updated": True}) == {
        CONTROL_DELTA_KEY: 1,
        "set": {"updated": True},
    }


def test_is_control_delta_distinguishes_an_envelope_from_a_legacy_partial():
    assert is_control_delta(control_delta(set_values={"updated": True})) is True
    assert is_control_delta({"updated": True, "mode": "Hold"}) is False
    assert is_control_delta(None) is False
    assert is_control_delta([{"op": "timer.clear"}]) is False


def test_set_may_not_carry_timer():
    """The rule that makes deleting CONTROL_COUPLED_MEMBERS sound."""
    with pytest.raises(ControlDeltaError, match="timer"):
        control_delta(set_values={"timer": {"start": 0, "paused": 0, "end": 0}})


def test_set_may_not_carry_notify_data():
    with pytest.raises(ControlDeltaError, match="notify_data"):
        control_delta(set_values={"notify_data": []})


def test_an_unknown_top_level_key_is_rejected():
    with pytest.raises(ControlDeltaError, match="patch"):
        validate_control_delta({CONTROL_DELTA_KEY: 1, "patch": {}})


def test_origin_is_an_allowed_top_level_key():
    validate_control_delta({CONTROL_DELTA_KEY: 1, "set": {"updated": True}, "origin": "app"})


def test_an_unknown_op_name_is_rejected():
    with pytest.raises(ControlDeltaError, match="timer.frobnicate"):
        control_delta(ops=[{"op": "timer.frobnicate"}])


def test_timer_pause_requires_at():
    with pytest.raises(ControlDeltaError, match="at"):
        control_delta(ops=[{"op": "timer.pause"}])


def test_timer_clear_takes_no_fields():
    assert control_delta(ops=[{"op": "timer.clear"}]) == {
        CONTROL_DELTA_KEY: 1,
        "ops": [{"op": "timer.clear"}],
    }


def test_notify_set_requires_label_type_and_fields():
    with pytest.raises(ControlDeltaError, match="fields"):
        control_delta(ops=[{"op": "notify.set", "label": "Grill", "type": "probe"}])


def test_delete_paths_must_be_non_empty_lists_of_strings():
    assert control_delta(delete_paths=[["recipe", "step_data"]]) == {
        CONTROL_DELTA_KEY: 1,
        "delete": [["recipe", "step_data"]],
    }
    with pytest.raises(ControlDeltaError, match="delete"):
        control_delta(delete_paths=[[]])
    with pytest.raises(ControlDeltaError, match="delete"):
        control_delta(delete_paths=[["recipe", 3]])


def test_the_constructor_deep_copies_so_a_later_mutation_cannot_reach_the_queue():
    values = {"manual": {"pwm": 50}}
    envelope = control_delta(set_values=values)
    values["manual"]["pwm"] = 99
    assert envelope["set"]["manual"]["pwm"] == 50
