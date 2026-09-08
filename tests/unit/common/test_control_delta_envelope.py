"""The delta envelope is a CROSS-PROCESS wire format: the web process builds it,
the control process reads it. Both ends are pinned here."""

import pytest

from common.control_delta import (
    CONTROL_DELTA_KEY,
    CONTROL_DELTA_VERSION,
    ControlDeltaError,
    control_delta,
    is_control_delta,
    notify_ops_from_post,
    validate_control_delta,
)
from common.persistence import control as control_persistence
from common.sqlite_queue import SqliteQueue

from common.timer import default_timer
from tests.fakes.clock import clock_stamp


def test_enqueue_control_delta_validates_and_copies_before_queueing(ds):
    envelope = control_delta(set_values={"manual": {"pwm": 50}})

    control_persistence.enqueue_control_delta(envelope, origin="display")
    queued_set = envelope["set"]
    assert isinstance(queued_set, dict)
    manual = queued_set["manual"]
    assert isinstance(manual, dict)
    manual["pwm"] = 99

    assert SqliteQueue("queue_control_write").list() == [
        {
            CONTROL_DELTA_KEY: CONTROL_DELTA_VERSION,
            "set": {"manual": {"pwm": 50}},
            "origin": "display",
        }
    ]


def test_enqueue_control_delta_rejects_an_invalid_envelope_without_queueing(ds):
    malformed = {CONTROL_DELTA_KEY: CONTROL_DELTA_VERSION, "set": []}

    with pytest.raises(ControlDeltaError):
        control_persistence.enqueue_control_delta(malformed, origin="display")

    assert SqliteQueue("queue_control_write").length() == 0


def test_is_control_delta_distinguishes_an_envelope_from_a_legacy_partial():
    assert is_control_delta(control_delta(set_values={"updated": True})) is True
    assert is_control_delta({"updated": True, "mode": "Hold"}) is False
    assert is_control_delta(None) is False
    assert is_control_delta([{"op": "timer.clear"}]) is False


def test_set_may_not_carry_timer():
    """The rule that makes deleting CONTROL_COUPLED_MEMBERS sound."""
    with pytest.raises(ControlDeltaError, match="timer"):
        control_delta(set_values={"timer": default_timer()})


def test_set_may_not_carry_notify_data():
    with pytest.raises(ControlDeltaError, match="notify_data"):
        control_delta(set_values={"notify_data": []})


def test_an_unknown_top_level_key_is_rejected():
    with pytest.raises(ControlDeltaError, match="patch"):
        validate_control_delta({CONTROL_DELTA_KEY: CONTROL_DELTA_VERSION, "patch": {}})


def test_an_unknown_op_name_is_rejected():
    with pytest.raises(ControlDeltaError, match="timer.frobnicate"):
        control_delta(ops=[{"op": "timer.frobnicate"}])


@pytest.mark.parametrize("name", ["timer.pause", "timer.clear"])
@pytest.mark.parametrize("invalid_field", ["at", "seconds"])
def test_pause_and_clear_reject_epoch_authority_and_duration_fields(name, invalid_field):
    now = clock_stamp()
    with pytest.raises(ControlDeltaError):
        control_delta(
            ops=[
                {
                    "op": name,
                    "requested_wall_s": now.observed_wall_s,
                    "target_runtime_id": now.runtime_id,
                    invalid_field: 60,
                }
            ]
        )


@pytest.mark.parametrize("missing", ["requested_wall_s", "target_runtime_id"])
def test_timer_commands_require_provenance_and_target_generation(missing):
    now = clock_stamp()
    operation = {
        "op": "timer.pause",
        "requested_wall_s": now.observed_wall_s,
        "target_runtime_id": now.runtime_id,
    }
    del operation[missing]
    with pytest.raises(ControlDeltaError):
        control_delta(ops=[operation])


@pytest.mark.parametrize("requested_wall_s", [float("nan"), float("inf"), True])
def test_timer_wall_provenance_must_be_finite_metadata(requested_wall_s):
    with pytest.raises(ControlDeltaError):
        control_delta(
            ops=[
                {
                    "op": "timer.clear",
                    "requested_wall_s": requested_wall_s,
                    "target_runtime_id": clock_stamp().runtime_id,
                }
            ]
        )


@pytest.mark.parametrize(
    "invalid",
    [
        {"seconds": 0},
        {"seconds": 1.5},
        {"seconds": True},
        {"shutdown": 1},
        {"keep_warm": "false"},
    ],
)
def test_start_with_options_rejects_invalid_duration_and_flags(invalid):
    now = clock_stamp()
    with pytest.raises(ControlDeltaError):
        control_delta(
            ops=[
                {
                    "op": "timer.start_with_options",
                    "requested_wall_s": now.observed_wall_s,
                    "target_runtime_id": now.runtime_id,
                    "seconds": 60,
                    "shutdown": False,
                    "keep_warm": False,
                    **invalid,
                }
            ]
        )


def test_notify_set_requires_label_type_and_fields():
    with pytest.raises(ControlDeltaError, match="fields"):
        control_delta(ops=[{"op": "notify.set", "label": "Grill", "type": "probe"}])


def test_delete_paths_must_be_non_empty_lists_of_strings():
    with pytest.raises(ControlDeltaError, match="delete"):
        control_delta(delete_paths=[[]])
    with pytest.raises(ControlDeltaError, match="delete"):
        control_delta(delete_paths=[["recipe", 3]])


def test_the_constructor_deep_copies_so_a_later_mutation_cannot_reach_the_queue():
    values = {"manual": {"pwm": 50}}
    envelope = control_delta(set_values=values)
    values["manual"]["pwm"] = 99
    assert envelope["set"]["manual"]["pwm"] == 50


# --- notify_ops_from_post: the client door onto the notify ops --------------


def test_a_post_without_notify_members_is_left_alone():
    assert notify_ops_from_post({"mode": "Hold", "s_plus": True}) == ({"mode": "Hold", "s_plus": True}, None)


def test_notify_updates_become_one_notify_set_op_each():
    members, ops = notify_ops_from_post(
        {
            "s_plus": True,
            "notify_updates": [
                {"label": "Grill", "type": "probe", "fields": {"req": True}},
                {"label": "Grill", "type": "probe_limit_high", "fields": {"req": False}},
            ],
        }
    )
    assert members == {"s_plus": True}
    assert ops == [
        {"op": "notify.set", "label": "Grill", "type": "probe", "fields": {"req": True}},
        {"op": "notify.set", "label": "Grill", "type": "probe_limit_high", "fields": {"req": False}},
    ]


def test_notify_data_becomes_a_replace_op():
    entries = [{"label": "Only", "type": "probe", "req": True}]
    assert notify_ops_from_post({"notify_data": entries}) == ({}, [{"op": "notify.replace", "entries": entries}])


def test_the_caller_s_payload_is_not_mutated():
    payload = {"mode": "Hold", "notify_data": []}
    notify_ops_from_post(payload)
    assert payload == {"mode": "Hold", "notify_data": []}


def test_both_notify_keys_apply_the_replace_first_so_addressed_updates_win():
    _, ops = notify_ops_from_post(
        {"notify_data": [], "notify_updates": [{"label": "Grill", "type": "probe", "fields": {"req": True}}]}
    )
    assert [op["op"] for op in ops] == ["notify.replace", "notify.set"]


def test_a_malformed_notify_updates_payload_is_rejected():
    with pytest.raises(ControlDeltaError, match="notify_updates must be a list"):
        notify_ops_from_post({"notify_updates": {"label": "Grill"}})
    with pytest.raises(ControlDeltaError, match="must be a mapping"):
        notify_ops_from_post({"notify_updates": ["Grill"]})


def test_a_notify_update_is_validated_by_the_op_it_becomes():
    """No second validator to drift from notify.set's own."""
    with pytest.raises(ControlDeltaError, match="missing field"):
        control_delta(ops=notify_ops_from_post({"notify_updates": [{"label": "Grill"}]})[1])
    with pytest.raises(ControlDeltaError, match="unknown field"):
        control_delta(
            ops=notify_ops_from_post(
                {"notify_updates": [{"label": "Grill", "type": "probe", "fields": {}, "nope": 1}]}
            )[1]
        )
