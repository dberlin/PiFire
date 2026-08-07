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

def _calibration_command(revision, maximum_temperature_c=130.0):
    return {
        "action": "start",
        "revision": revision,
        "maximum_temperature_c": maximum_temperature_c,
        "ambient_c": 20.0,
        "ambient_source": "configured",
        "empty_grill_confirmed": True,
        "pellets_confirmed": True,
    }


def test_calibration_operation_applies_only_a_strictly_newer_live_revision():
    control = {"mpc_calibration": _calibration_command(2)}
    four = _calibration_command(4)
    three = _calibration_command(3)
    conflicting_four = _calibration_command(4, maximum_temperature_c=140.0)

    apply_control_delta(control, control_delta(ops=[{"op": "mpc_calibration.set", "command": four}]))
    assert control["mpc_calibration"] == four

    apply_control_delta(control, control_delta(ops=[{"op": "mpc_calibration.set", "command": three}]))
    assert control["mpc_calibration"] == four

    apply_control_delta(
        control,
        control_delta(ops=[{"op": "mpc_calibration.set", "command": conflicting_four}]),
    )
    assert control["mpc_calibration"] == four

    apply_control_delta(control, control_delta(ops=[{"op": "mpc_calibration.set", "command": four}]))
    assert control["mpc_calibration"] == four



def test_apply_control_delta_drops_an_unknown_version_and_logs(caplog):
    """Direction B' of the upgrade analysis: a FUTURE writer, THIS drain."""
    control = _control()
    envelope = {CONTROL_DELTA_KEY: 99, "set": {"mode": "Hold"}}
    with caplog.at_level(logging.ERROR, logger="control"):
        apply_control_delta(control, envelope)
    assert control["mode"] == "Stop", "a partially-understood delta must not be applied"
    assert "unsupported control delta version" in caplog.text


def _notify_control():
    return {
        "notify_data": [
            {"label": "Grill", "type": "probe", "req": False, "target": 0, "eta": None},
            {"label": "Grill", "type": "probe_limit_high", "req": False, "target": 0, "triggered": False},
            {"label": "Timer", "type": "timer", "req": False, "shutdown": False, "keep_warm": False},
        ]
    }


def _entry(control, label, type_):
    return next(e for e in control["notify_data"] if e["label"] == label and e["type"] == type_)


def test_notify_set_field_merges_the_addressed_entry_only():
    control = _notify_control()
    apply_control_delta(
        control,
        control_delta(
            ops=[
                {
                    "op": "notify.set",
                    "label": "Grill",
                    "type": "probe",
                    "fields": {"target": 203, "req": True},
                }
            ]
        ),
    )
    assert _entry(control, "Grill", "probe")["target"] == 203
    assert _entry(control, "Grill", "probe")["req"] is True
    assert _entry(control, "Grill", "probe")["eta"] is None, "untouched fields survive"
    assert _entry(control, "Grill", "probe_limit_high")["target"] == 0, "same label, different type"


def test_notify_set_appends_when_the_entry_does_not_exist():
    control = _notify_control()
    apply_control_delta(
        control,
        control_delta(ops=[{"op": "notify.set", "label": "Probe9", "type": "probe", "fields": {"target": 165}}]),
    )
    assert _entry(control, "Probe9", "probe") == {"label": "Probe9", "type": "probe", "target": 165}
    assert len(control["notify_data"]) == 4


def test_two_notify_sets_on_the_same_entry_both_land_when_they_touch_different_fields():
    """The residual-2 case for notify_data: neither is inferred, so neither is dropped."""
    control = _notify_control()
    apply_control_delta(
        control,
        control_delta(ops=[{"op": "notify.set", "label": "Grill", "type": "probe", "fields": {"target": 203}}]),
    )
    apply_control_delta(
        control,
        control_delta(ops=[{"op": "notify.set", "label": "Grill", "type": "probe", "fields": {"req": True}}]),
    )
    assert _entry(control, "Grill", "probe")["target"] == 203
    assert _entry(control, "Grill", "probe")["req"] is True


def test_a_notify_set_back_to_the_starting_value_still_lands():
    """Under reduce_control_patch this write was indistinguishable from silence."""
    control = _notify_control()
    apply_control_delta(
        control,
        control_delta(ops=[{"op": "notify.set", "label": "Grill", "type": "probe", "fields": {"target": 203}}]),
    )
    apply_control_delta(
        control,
        control_delta(ops=[{"op": "notify.set", "label": "Grill", "type": "probe", "fields": {"target": 0}}]),
    )
    assert _entry(control, "Grill", "probe")["target"] == 0


def test_notify_delete_removes_exactly_one_entry():
    control = _notify_control()
    apply_control_delta(control, control_delta(ops=[{"op": "notify.delete", "label": "Grill", "type": "probe"}]))
    assert [(e["label"], e["type"]) for e in control["notify_data"]] == [
        ("Grill", "probe_limit_high"),
        ("Timer", "timer"),
    ]


def test_notify_replace_swaps_the_whole_array():
    control = _notify_control()
    fresh = [{"label": "Only", "type": "probe", "req": True}]
    apply_control_delta(control, control_delta(ops=[{"op": "notify.replace", "entries": fresh}]))
    assert control["notify_data"] == fresh
    fresh[0]["req"] = False
    assert control["notify_data"][0]["req"] is True, "replace deep-copies"


def test_notify_replace_then_set_composes_in_order():
    control = _notify_control()
    apply_control_delta(
        control,
        control_delta(
            ops=[
                {"op": "notify.replace", "entries": [{"label": "Only", "type": "probe", "req": False}]},
                {"op": "notify.set", "label": "Only", "type": "probe", "fields": {"req": True}},
            ]
        ),
    )
    assert control["notify_data"] == [{"label": "Only", "type": "probe", "req": True}]
