"""Two control writers in one control cycle must both survive the drain.

The seam
--------
Web-process control writes are queued as MERGE partials
(``write_control(control, WriteKind.MERGE)``) and applied by the control loop
in ``common/datastore_accessors.py::execute_control_writes``. Per RFC 7396,
SQLite's ``json_patch`` **replaces arrays wholesale**, and
``control["notify_data"]`` is an array.

Every writer therefore has to send the whole array back. Its copy comes from
``read_control()``, which reads the ``control:general`` blob -- a blob that does
NOT reflect the pending write queue, because the queue only drains inside the
control loop. So two writers in one control cycle each build a full array from
the same stale read, and whichever lands second silently discards the first's
change.

This is the same shape as ``tests/web/test_warnings_cross_consumer.py``: two
real callers, driven through the real code path, where one eats the other's
data. Nothing here is a hand-rolled reproduction -- the writers are production
entry points:

* ``common/api_commands.py::process_command`` (``/api/set/...``), which reads
  control, mutates one ``notify_data`` entry and queues the whole dict;
* ``blueprints/api/routes.py::_api_post_control`` (``POST /api/control``), the
  door the React dashboard's ``saveTargetEdit`` posts a whole ``notify_data``
  array through -- the case that provably cannot be fixed client-side, because
  a server-side command's own ``read_control()`` is equally stale;
* ``common/system.py::gather_system_info``, a *background* writer that touches
  no notification at all yet still ships a stale full ``notify_data`` alongside
  its ``control["system"]`` update.

The fix is a three-way element-wise merge of ``notify_data`` inside the drain,
keyed on ``(label, type)``, using the pre-drain blob as the common ancestor --
so a writer only imposes the fields it actually changed.
"""

from unittest import mock

import pytest

from common import api_commands
from common import common as c
from common import datastore_accessors as dsa
from common.common import WriteKind
from common.datastore_accessors import (
    default_control,
    read_control,
    write_control,
    write_settings_store,
)
from common.defaults import default_settings

FIXED_NOW = 1_700_000_000.0


@pytest.fixture
def seeded(ds):
    """A datastore with default settings + a freshly written control blob."""
    write_settings_store(default_settings())
    write_control(default_control(), WriteKind.OVERWRITE, origin="test-cross-writer")
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
    dsa.execute_control_writes()

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

    dsa.execute_control_writes()

    control = read_control()
    assert _entry(control, "Probe1", "probe")["target"] == 165
    assert _entry(control, "Timer", "timer")["shutdown"] is True


def test_limit_writers_do_not_eat_each_other(seeded):
    """Same label, different `type` -- the key has to be (label, type)."""
    assert _set_notify("Grill", "target", "203")["result"] == "OK"
    assert _set_notify("Grill", "target", "350", subcommand="limit_high")["result"] == "OK"

    dsa.execute_control_writes()

    control = read_control()
    assert _entry(control, "Grill", "probe")["target"] == 203
    assert _entry(control, "Grill", "probe_limit_high")["target"] == 350


# ---------------------------------------------------------------------------
# The case that cannot be fixed client-side: POST /api/control ships the whole
# array from a stale read, concurrently with a server-side command.
# ---------------------------------------------------------------------------


def test_post_api_control_full_array_does_not_eat_a_concurrent_command(seeded):
    """`saveTargetEdit`'s whole-array POST + a timer arm in the same cycle.

    The client cannot avoid this: it has no way to see the pending queue, and
    neither does the command handler it races. Only the drain can.
    """
    # The client's read -- taken before either write is queued.
    client_view = read_control()
    edited = _entry(client_view, "Probe2", "probe")
    edited["target"] = 145
    edited["req"] = True

    # Server-side command arms the timer from its own (equally stale) read.
    assert _start_timer("900", "keep_warm")["result"] == "OK"

    # ...then the client's POST lands, carrying the whole array.
    write_control({"notify_data": client_view["notify_data"]}, WriteKind.MERGE, origin="app")

    dsa.execute_control_writes()

    control = read_control()
    assert _entry(control, "Probe2", "probe")["target"] == 145
    assert _entry(control, "Probe2", "probe")["req"] is True
    timer = _entry(control, "Timer", "timer")
    assert timer["keep_warm"] is True, "the client's whole-array POST reverted the timer arm"
    assert timer["req"] is True


def test_background_full_control_write_does_not_eat_a_notify_write(seeded):
    """A writer that touches no notification at all still ships a full array.

    ``common/system.py::gather_system_info`` (a background socket tick) reads
    control, sets ``control["system"][...]`` and queues the WHOLE dict. It
    races every user action, and before the fix it silently reverted whatever
    the user had just armed.
    """
    assert _set_notify("Probe3", "target", "180")["result"] == "OK"

    stale = read_control()  # gather_system_info's read: pre-queue, hence stale
    stale["system"] = {"cpu_temp": 42.0}
    write_control(stale, WriteKind.MERGE, origin="app-socketio")

    dsa.execute_control_writes()

    control = read_control()
    assert _entry(control, "Probe3", "probe")["target"] == 180
    assert control["system"]["cpu_temp"] == 42.0


# ---------------------------------------------------------------------------
# Contract the merge must preserve.
# ---------------------------------------------------------------------------


def test_same_entry_same_field_last_writer_wins(seeded):
    """Two writers setting the SAME field is a genuine conflict; last wins.

    There is no information in the queue to resolve it any other way, and the
    pre-fix behaviour was already last-wins. Pinned so a future merge change
    cannot quietly turn it into first-wins.
    """
    assert _set_notify("Grill", "target", "203")["result"] == "OK"
    assert _set_notify("Grill", "target", "225")["result"] == "OK"
    dsa.execute_control_writes()
    assert _entry(read_control(), "Grill", "probe")["target"] == 225


def test_a_lone_writer_still_replaces_the_array_exactly(seeded):
    """One writer per cycle: unchanged from the json_patch behaviour."""
    control = read_control()
    for e in control["notify_data"]:
        e["req"] = True
    write_control({"notify_data": control["notify_data"]}, WriteKind.MERGE, origin="app")
    dsa.execute_control_writes()
    assert all(e["req"] is True for e in read_control()["notify_data"])


def test_writer_that_drops_entries_still_removes_them(seeded):
    """Factory-reset re-seeds notify_data with a different probe set.

    json_patch replaced the array, so a shorter array removed entries. The
    merge must keep that: an entry the writer's own baseline had and its
    payload dropped is a deletion, not an omission.
    """
    control = read_control()
    kept = [e for e in control["notify_data"] if e["label"] != "Probe3"]
    write_control({"notify_data": kept}, WriteKind.MERGE, origin="app")
    dsa.execute_control_writes()
    labels = {e["label"] for e in read_control()["notify_data"]}
    assert "Probe3" not in labels
    assert "Grill" in labels


def test_writer_that_adds_an_entry_still_adds_it(seeded):
    control = read_control()
    control["notify_data"].append({"label": "Probe9", "type": "probe", "req": True, "target": 99})
    write_control({"notify_data": control["notify_data"]}, WriteKind.MERGE, origin="app")
    dsa.execute_control_writes()
    assert _entry(read_control(), "Probe9", "probe")["target"] == 99
