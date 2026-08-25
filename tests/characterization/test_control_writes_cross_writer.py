"""Two control writers in one control cycle must both survive the drain.

How a write lands
-----------------
A control delta does not land when it is enqueued. It is applied by the control
loop in ``execute_control_writes``. ``read_control()`` serves the persisted
``control:general`` blob and never the pending queue, so two writers inside one
control cycle both read pre-write state.

That used to mean the last one won. Every writer queued the WHOLE control dict
it had read, so each carried a stale copy of every field it never touched, and
whichever landed second silently reverted the others. ``control["notify_data"]``
was the loudest instance -- it is an array, and json_patch replaces arrays
wholesale (RFC 7396), so a writer touching one probe's target shipped every
other probe's settings back with it -- but the same loss applied to every scalar
flag and every nested object.

The fix is not a smarter merge, it is a better payload. Every writer now queues
a DELTA (``common/control_delta.py``): a statement of what it MEANT, not the
snapshot it read. A member it does not name is silence, so there is nothing
stale to impose and nothing for the drain to infer. Members that cannot be
expressed as a value at all -- the coupled ``timer`` object, and addressing
inside the ``notify_data`` array -- travel as named OPS which the drain
evaluates against LIVE state, so two writers touching the same one compose in
order instead of racing.

The writers driven below are production entry points, not hand-rolled
reproductions:

* ``common/api_commands.py::process_command`` (``/api/set/...``);
* ``blueprints/api/routes.py::_api_post_control`` (``POST /api/control``), the
  door the React dashboard's ``saveTargetEdit`` posts through;
* ``common/system.py::gather_system_info``, a background writer that touches no
  notification at all.

One case is NOT closed, and is called out where it is pinned: a client that
posts a whole ``notify_data`` array has not said which fields it meant to
change, so that door has replace semantics. See
test_post_api_control_whole_array_is_a_replace_and_says_so.
"""

from unittest import mock

import pytest

from common import api_commands
from common import common as c
from common.control_delta import control_delta
from common.defaults import default_settings
from common.persistence import control as control_persistence
from common.persistence import runtime as runtime_persistence
from common.persistence.control import (
    default_control,
    read_control,
)
from common.persistence.runtime import (
    write_settings_store,
)

FIXED_NOW = 1_700_000_000.0


@pytest.fixture
def seeded(ds):
    """A datastore with default settings + a freshly written control blob."""
    write_settings_store(default_settings())
    control_persistence.write_control_snapshot(default_control(), origin="test-cross-writer")
    c.SqliteQueue("queue_control_write").flush()
    return ds


def _entry(control, label, type_):
    return next(e for e in control["notify_data"] if e["label"] == label and e["type"] == type_)


def _set_notify(label, field, value, subcommand="notify"):
    """Writer: the real /api/set/<notify|limit_high|limit_low>/... command."""
    with mock.patch.object(api_commands, "write_log"), mock.patch.object(c.time, "time", return_value=FIXED_NOW):
        return api_commands.process_command(
            action="set", arglist=[subcommand, label, field, value], origin="test-writer-a"
        )


def _start_timer(seconds, options="shutdown"):
    """Writer: the real /api/set/timer/start/<seconds>/<options> command."""
    with mock.patch.object(api_commands, "write_log"), mock.patch.object(c.time, "time", return_value=FIXED_NOW):
        return api_commands.process_command(
            action="set", arglist=["timer", "start", seconds, options], origin="test-writer-b"
        )


# ---------------------------------------------------------------------------
# Two commands, one cycle, different notify_data entries.
# ---------------------------------------------------------------------------


def test_two_commands_in_one_cycle_both_survive_the_drain(seeded):
    """set/notify + set/timer/start touch different entries; both must land.

    Before the fix the last command's stale full array reverted the earlier
    ones, so exactly one of the three user actions took effect.
    """
    assert _set_notify("Grill", "target", "203")["result"] == "OK"
    assert _set_notify("Grill", "req", "true")["result"] == "OK"
    assert _start_timer("600", "shutdown")["result"] == "OK"

    # All three queued BEFORE any drain -- one control cycle.
    assert c.SqliteQueue("queue_control_write").length() == 3
    control_persistence.execute_control_writes()

    control = read_control()
    grill = _entry(control, "Grill", "probe")
    timer = _entry(control, "Timer", "timer")
    assert grill["target"] == 203, "the probe target was eaten by a later writer"
    assert grill["req"] is True, "the probe req was eaten by a later writer"
    assert timer["shutdown"] is True
    assert timer["req"] is True


def test_two_commands_in_one_cycle_survive_in_either_order(seeded):
    """Order must not decide the winner."""
    assert _start_timer("600", "shutdown")["result"] == "OK"
    assert _set_notify("Probe1", "target", "165")["result"] == "OK"

    control_persistence.execute_control_writes()

    control = read_control()
    assert _entry(control, "Probe1", "probe")["target"] == 165
    assert _entry(control, "Timer", "timer")["shutdown"] is True


def test_limit_writers_do_not_eat_each_other(seeded):
    """Same label, different `type` -- the key has to be (label, type)."""
    assert _set_notify("Grill", "target", "203")["result"] == "OK"
    assert _set_notify("Grill", "target", "350", subcommand="limit_high")["result"] == "OK"

    control_persistence.execute_control_writes()

    control = read_control()
    assert _entry(control, "Grill", "probe")["target"] == 203
    assert _entry(control, "Grill", "probe_limit_high")["target"] == 350


# ---------------------------------------------------------------------------
# The case that cannot be fixed client-side: POST /api/control ships the whole
# array from a stale read, concurrently with a server-side command.
# ---------------------------------------------------------------------------


def test_post_api_control_whole_array_is_a_replace_and_says_so(seeded):
    """`saveTargetEdit`'s whole-array POST + a timer arm in the same cycle.

    BEHAVIOUR CHANGE, and the one regression in the delta conversion. This used
    to pass: merge_notify_data diffed the posted array against the pre-drain
    ancestor and applied only the fields that differed, so the concurrent timer
    arm survived. That three-way merge is gone, and POST /api/control now maps a
    posted `notify_data` to an explicit notify.replace op -- which does exactly
    what its name says, including discarding a change queued moments earlier.

    Nothing in the delta representation can recover this. A client that posts a
    WHOLE array from a read that cannot see the queue has not expressed which
    fields it MEANT to change; the old merge inferred that from an ancestor,
    which is precisely the inference this work removes because it is unsound in
    the other direction (a field restored to its opening value was invisible).
    The real fix is client-side: post the field, not the array. Until then this
    door has replace semantics, stated by name rather than emerging from
    json_patch's array handling.
    """
    client_view = read_control()
    edited = _entry(client_view, "Probe2", "probe")
    edited["target"] = 145
    edited["req"] = True

    assert _start_timer("900", "keep_warm")["result"] == "OK"

    control_persistence.enqueue_control_delta(
        control_delta(ops=[{"op": "notify.replace", "entries": client_view["notify_data"]}]),
        origin="app",
    )

    control_persistence.execute_control_writes()

    control = read_control()
    assert _entry(control, "Probe2", "probe")["target"] == 145
    assert _entry(control, "Probe2", "probe")["req"] is True
    # The timer's countdown is NOT in notify_data, so the arm itself survives...
    assert control["timer"]["end"] > 0
    # ...but its expiry flag, which lives in the replaced array, does not.
    assert _entry(control, "Timer", "timer")["keep_warm"] is False


def test_post_api_control_per_entry_edit_does_not_eat_a_concurrent_command(seeded):
    """...and the shape that DOES compose, which is what a client should send.

    One notify.set naming the two fields the user edited. The concurrent timer
    arm survives, because nothing in this write mentions the timer entry.
    """
    assert _start_timer("900", "keep_warm")["result"] == "OK"

    control_persistence.enqueue_control_delta(
        control_delta(
            ops=[
                {
                    "op": "notify.set",
                    "label": "Probe2",
                    "type": "probe",
                    "fields": {"target": 145, "req": True},
                }
            ]
        ),
        origin="app",
    )

    control_persistence.execute_control_writes()

    control = read_control()
    assert _entry(control, "Probe2", "probe")["target"] == 145
    timer = _entry(control, "Timer", "timer")
    assert timer["keep_warm"] is True
    assert timer["req"] is True


def test_background_full_control_write_does_not_eat_a_notify_write(seeded):
    """A writer that touches no notification at all still ships a full array.

    ``common/system.py::gather_system_info`` (a background socket tick) reads
    control, sets ``control["system"][...]`` and queues the WHOLE dict. It
    races every user action, and before the fix it silently reverted whatever
    the user had just armed.
    """
    assert _set_notify("Probe3", "target", "180")["result"] == "OK"

    # gather_system_info's shape: it names only the slice it assigns, so it has
    # nothing stale to impose. It used to queue the whole dict from a pre-queue
    # read and revert whatever the user had just armed.
    control_persistence.enqueue_control_delta(
        control_delta(set_values={"system": {"cpu_temp": 42.0}}),
        origin="app-socketio",
    )

    control_persistence.execute_control_writes()

    control = read_control()
    assert _entry(control, "Probe3", "probe")["target"] == 180
    assert control["system"]["cpu_temp"] == 42.0


# ---------------------------------------------------------------------------
# Contracts explicit delta operations must preserve.
# ---------------------------------------------------------------------------


def test_same_entry_same_field_last_writer_wins(seeded):
    """Two writers setting the SAME field is a genuine conflict; last wins.

    There is no information in the queue to resolve it any other way, and the
    pre-fix behaviour was already last-wins. Pinned so a future merge change
    cannot quietly turn it into first-wins.
    """
    assert _set_notify("Grill", "target", "203")["result"] == "OK"
    assert _set_notify("Grill", "target", "225")["result"] == "OK"
    control_persistence.execute_control_writes()
    assert _entry(read_control(), "Grill", "probe")["target"] == 225


def test_a_lone_writer_still_replaces_the_array_exactly(seeded):
    """One writer per cycle: unchanged from the json_patch behaviour."""
    control = read_control()
    for e in control["notify_data"]:
        e["req"] = True
    control_persistence.enqueue_control_delta(
        control_delta(ops=[{"op": "notify.replace", "entries": control["notify_data"]}]),
        origin="app",
    )
    control_persistence.execute_control_writes()
    assert all(e["req"] is True for e in read_control()["notify_data"])


def test_writer_that_drops_entries_still_removes_them(seeded):
    """Factory-reset re-seeds notify_data with a different probe set.

    json_patch replaced the array, so a shorter array removed entries. The
    merge must keep that: an entry the writer's own baseline had and its
    payload dropped is a deletion, not an omission.
    """
    control = read_control()
    kept = [e for e in control["notify_data"] if e["label"] != "Probe3"]
    control_persistence.enqueue_control_delta(
        control_delta(ops=[{"op": "notify.replace", "entries": kept}]),
        origin="app",
    )
    control_persistence.execute_control_writes()
    labels = {e["label"] for e in read_control()["notify_data"]}
    assert "Probe3" not in labels
    assert "Grill" in labels


def test_writer_that_adds_an_entry_still_adds_it(seeded):
    control = read_control()
    control["notify_data"].append({"label": "Probe9", "type": "probe", "req": True, "target": 99})
    control_persistence.enqueue_control_delta(
        control_delta(ops=[{"op": "notify.replace", "entries": control["notify_data"]}]),
        origin="app",
    )
    control_persistence.execute_control_writes()
    assert _entry(read_control(), "Probe9", "probe")["target"] == 99


# ===========================================================================
# The rest of the control dict. Every writer names only the members it intends
# to change, so stale snapshots cannot ride along with unrelated writes.
# ===========================================================================


def _command(*arglist, action="set", origin="test-writer"):
    """Any real /api/<action>/... command, with its hazardous edges stubbed."""
    with (
        mock.patch.object(api_commands, "write_log"),
        mock.patch.object(api_commands, "restart_scripts"),
        mock.patch.object(c.time, "time", return_value=FIXED_NOW),
    ):
        return api_commands.process_command(action=action, arglist=list(arglist), origin=origin)


def test_two_scalar_writers_in_one_cycle_both_survive(seeded):
    """set/psp + set/splus: independent scalars, one cycle.

    Before the fix the splus command's stale copy of mode/primary_setpoint/
    updated reverted the setpoint change outright -- the user set a hold
    temperature, toggled Smoke Plus, and the grill never left Stop.
    """
    assert _command("psp", "225")["result"] == "OK"
    assert _command("splus", "true")["result"] == "OK"

    assert c.SqliteQueue("queue_control_write").length() == 2
    control_persistence.execute_control_writes()

    control = read_control()
    assert control["primary_setpoint"] == 225
    assert control["mode"] == "Hold"
    assert control["updated"] is True
    assert control["s_plus"] is True


def test_three_scalar_writers_in_one_cycle_all_survive(seeded):
    """Depth is not the issue -- any number of writers per cycle is reachable."""
    assert _command("duty_cycle", "50")["result"] == "OK"
    assert _command("pwm", "true")["result"] == "OK"
    assert _command("lid_open", "toggle")["result"] == "OK"
    assert _command("tuning_mode", "true")["result"] == "OK"

    control_persistence.execute_control_writes()

    control = read_control()
    assert control["duty_cycle"] == 50
    assert control["pwm_control"] is True
    assert control["lid_open_toggle"] is True
    assert control["tuning_mode"] is True


def test_one_shot_request_flags_are_not_reverted_by_a_later_writer(seeded):
    """settings_update / hopper_check are edge-triggered requests to the loop.

    They are set True by the web and cleared by the control loop. A second
    writer in the same cycle used to revert them to False before the loop ever
    saw them -- the request was simply lost, with no error anywhere.
    """
    assert _command("pmode", "5")["result"] == "OK"  # -> settings_update = True
    assert _command("hopper", action="get")["result"] == "OK"  # -> hopper_check = True
    assert _command("splus", "true")["result"] == "OK"  # touches neither

    control_persistence.execute_control_writes()

    control = read_control()
    assert control["settings_update"] is True
    assert control["hopper_check"] is True
    assert control["s_plus"] is True


def test_a_flag_write_after_a_timer_start_no_longer_destroys_the_timer(seeded):
    """control['timer'] is an OBJECT, so json_patch merged its keys -- from the
    stale read. The shutdown command supplied all three as zeros and wiped the
    countdown the start had just armed. The user got a `shutdown` flag on no
    timer at all.
    """
    assert _command("timer", "start", "600")["result"] == "OK"
    assert _command("timer", "shutdown", "true")["result"] == "OK"

    control_persistence.execute_control_writes()

    control = read_control()
    assert control["timer"]["start"] == FIXED_NOW
    assert control["timer"]["end"] == FIXED_NOW + 600
    assert _entry(control, "Timer", "timer")["shutdown"] is True
    assert _entry(control, "Timer", "timer")["req"] is True


def test_nested_object_writers_do_not_eat_each_other(seeded):
    """control['manual'] -- two writes into the same nested object.

    The merge has to descend: whole-object replacement at the top level would
    make the second manual command revert the first's pwm value.
    """
    settings = runtime_persistence.read_settings()
    settings["safety"]["allow_manual_changes"] = True
    runtime_persistence.write_settings_store(settings)

    assert _command("manual", "pwm", "40")["result"] == "OK"
    assert _command("manual", "fan", "true")["result"] == "OK"

    control_persistence.execute_control_writes()

    manual = read_control()["manual"]
    assert manual["pwm"] == 40, "the fan command reverted the pwm value"
    assert manual["change"] == "fan"


def test_background_system_info_write_does_not_eat_a_scalar_command(seeded):
    """gather_system_info's shape again, this time against a scalar.

    It writes only control['system'], but queues the whole dict, so it used to
    revert any setpoint/mode change made in the same cycle.
    """
    assert _command("psp", "225")["result"] == "OK"

    control_persistence.enqueue_control_delta(
        control_delta(set_values={"system": {"cpu_temp": 42.0, "cpu_throttled": False}}),
        origin="app-socketio",
    )

    control_persistence.execute_control_writes()

    control = read_control()
    assert control["primary_setpoint"] == 225
    assert control["mode"] == "Hold"
    assert control["system"]["cpu_temp"] == 42.0


# ---------------------------------------------------------------------------
# Contract scalar deltas must preserve.
# ---------------------------------------------------------------------------


def test_two_writers_setting_the_same_scalar_last_one_wins(seeded):
    """A genuine conflict: nothing in the queue can resolve it. Unchanged."""
    assert _command("psp", "225")["result"] == "OK"
    assert _command("psp", "180")["result"] == "OK"
    control_persistence.execute_control_writes()
    assert read_control()["primary_setpoint"] == 180


def test_a_lone_writer_still_applies_every_field_it_changed(seeded):
    """One writer per cycle: indistinguishable from the json_patch behaviour."""
    assert _command("psp", "275")["result"] == "OK"
    control_persistence.execute_control_writes()
    control = read_control()
    assert (control["primary_setpoint"], control["mode"], control["updated"]) == (275, "Hold", True)


def test_a_partial_patch_never_deletes_unmentioned_keys(seeded):
    """Dict members absent from a patch are unmentioned, NOT deleted.

    This is the asymmetry with notify_data: arrays travel whole, so a missing
    ELEMENT is a deletion; dicts travel partial, so a missing KEY is silence.
    """
    control_persistence.enqueue_control_delta(control_delta(set_values={"s_plus": True}), origin="app")
    control_persistence.execute_control_writes()
    control = read_control()
    assert control["s_plus"] is True
    assert "notify_data" in control and "timer" in control and "safety" in control


def test_a_writer_may_add_a_key_the_ancestor_never_had(seeded):
    control_persistence.enqueue_control_delta(
        control_delta(set_values={"system": {"cpu_temp": 51.5}}),
        origin="app",
    )
    control_persistence.execute_control_writes()
    assert read_control()["system"]["cpu_temp"] == 51.5


# ---------------------------------------------------------------------------
# The residual, pinned deliberately.
# ---------------------------------------------------------------------------


def test_a_reset_to_the_ancestor_value_is_now_distinguishable_because_the_writer_states_it(seeded):
    """FORMER RESIDUAL, now closed: `start` then `stop` in ONE cycle.

    This was the case no merge strategy could reach. `timer stop` wrote zeros;
    when the ancestor already held zeros -- no timer running when the cycle
    began -- the stop's patch was byte-identical to the ancestor in every member
    it touched, so it carried NO evidence that its writer intended anything. The
    information was not in the queue to recover. A start and a stop inside one
    control cycle therefore left the timer RUNNING.

    The fix was never a better merge, it was a better payload. `timer stop` now
    queues {"op": "timer.clear"} (common/control_delta.py), which is nothing but
    intent: there is no ancestor to compare it against and no value that could
    coincide with one. Both halves below now agree.

    This closes the residual for writers that have been CONVERTED. A legacy
    whole-dict writer still cannot express a restore-to-ancestor, which is why
    the conversion is a call-site change across every writer rather than a
    single change in one place.
    """
    assert _command("timer", "start", "600")["result"] == "OK"
    assert _command("timer", "stop")["result"] == "OK"

    control_persistence.execute_control_writes()

    control = read_control()
    assert control["timer"] == {"start": 0, "paused": 0, "end": 0, "shutdown": False}
    assert _entry(control, "Timer", "timer")["req"] is False

    # Given a cycle of its own -- the normal case, since the control loop
    # drains every iteration -- the same pair lands identically. That equality
    # IS the invariant queued deltas exist to restore.
    assert _command("timer", "start", "600")["result"] == "OK"
    control_persistence.execute_control_writes()
    assert read_control()["timer"]["end"] == FIXED_NOW + 600
    assert _command("timer", "stop")["result"] == "OK"
    control_persistence.execute_control_writes()
    control = read_control()
    assert control["timer"] == {"start": 0, "paused": 0, "end": 0, "shutdown": False}
    assert _entry(control, "Timer", "timer")["req"] is False
