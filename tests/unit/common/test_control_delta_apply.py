"""apply_control_delta over the `set` and `delete` channels."""

import logging

from common.control_delta import CONTROL_DELTA_KEY, apply_control_delta, control_delta


def _control():
    return {
        "mode": "Stop",
        "updated": False,
        "primary_setpoint": 0,
        "manual": {"change": False, "pwm": 100},
        "recipe": {"filename": "", "step": 0, "step_data": {"hold_temp": 225}},
        "timer": {"start": 0, "paused": 0, "end": 0},
        "notify_data": [{"label": "Timer", "type": "timer", "req": False}],
    }


def test_set_assigns_top_level_members():
    control = _control()
    apply_control_delta(control, control_delta(set_values={"mode": "Hold", "primary_setpoint": 225}))
    assert control["mode"] == "Hold"
    assert control["primary_setpoint"] == 225


def test_set_deep_merges_a_nested_member_without_clobbering_siblings():
    control = _control()
    apply_control_delta(control, control_delta(set_values={"manual": {"pwm": 50}}))
    assert control["manual"] == {"change": False, "pwm": 50}


def test_an_absent_member_is_silence_not_a_deletion():
    control = _control()
    apply_control_delta(control, control_delta(set_values={"updated": True}))
    assert control["mode"] == "Stop"
    assert control["primary_setpoint"] == 0


def test_a_none_value_assigns_null_and_does_not_delete():
    """The asymmetry with the legacy path. json_patch (RFC 7386) DELETES on a
    null member, which is why strip_null_members exists. A delta applies in
    Python, so a null is just a value and deletion has its own channel."""
    control = _control()
    apply_control_delta(control, control_delta(set_values={"primary_setpoint": None}))
    assert "primary_setpoint" in control
    assert control["primary_setpoint"] is None


def test_delete_removes_a_nested_path():
    control = _control()
    apply_control_delta(control, control_delta(delete_paths=[["recipe", "step_data"]]))
    assert "step_data" not in control["recipe"]
    assert control["recipe"]["filename"] == ""


def test_delete_of_a_missing_path_is_a_no_op():
    control = _control()
    before = dict(control)
    apply_control_delta(control, control_delta(delete_paths=[["recipe", "never_existed"], ["nope"]]))
    assert control == before


def test_set_is_applied_before_delete():
    control = _control()
    apply_control_delta(
        control,
        control_delta(set_values={"recipe": {"step_data": {"hold_temp": 250}}}, delete_paths=[["recipe", "step_data"]]),
    )
    assert "step_data" not in control["recipe"]


def test_the_applier_does_not_alias_the_envelope():
    control = _control()
    envelope = control_delta(set_values={"manual": {"pwm": 50}})
    apply_control_delta(control, envelope)
    control["manual"]["pwm"] = 99
    assert envelope["set"]["manual"]["pwm"] == 50


def test_apply_control_delta_drops_an_unknown_version_and_logs(caplog):
    """Direction B' of the upgrade analysis: a FUTURE writer, THIS drain."""
    control = _control()
    envelope = {CONTROL_DELTA_KEY: 99, "set": {"mode": "Hold"}}
    with caplog.at_level(logging.ERROR, logger="control"):
        apply_control_delta(control, envelope)
    assert control["mode"] == "Stop", "a partially-understood delta must not be applied"
    assert "unsupported control delta version" in caplog.text
