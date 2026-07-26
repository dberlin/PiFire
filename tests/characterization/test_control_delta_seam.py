"""The drain must handle a queue that mixes legacy whole-dict patches and delta
envelopes, in push order, for the whole migration."""

import json

import pytest

from common import common as c
from common import datastore_accessors as dsa
from common.common import WriteKind
from common.control_delta import CONTROL_DELTA_KEY, control_delta
from common.datastore_accessors import (
    default_control,
    read_control,
    write_control,
    write_settings_store,
)
from common.defaults import default_settings

NOW = 1_700_000_000.0


@pytest.fixture
def seeded(ds):
    write_settings_store(default_settings())
    write_control(default_control(), WriteKind.OVERWRITE, origin="test-delta-seam")
    c.SqliteQueue("queue_control_write").flush()
    return ds


def test_a_delta_is_queued_verbatim_with_an_origin_stamp(seeded):
    write_control(control_delta(set_values={"mode": "Hold"}), WriteKind.DELTA, origin="app")
    rows = c.datastore.connection().execute("SELECT value FROM queue_control_write ORDER BY id").fetchall()
    assert json.loads(rows[0][0]) == {CONTROL_DELTA_KEY: 1, "set": {"mode": "Hold"}, "origin": "app"}


def test_a_delta_write_lands_on_the_blob(seeded):
    write_control(control_delta(set_values={"mode": "Hold", "primary_setpoint": 225}), WriteKind.DELTA, origin="app")
    dsa.execute_control_writes()
    control = read_control()
    assert control["mode"] == "Hold"
    assert control["primary_setpoint"] == 225


def test_a_legacy_whole_dict_write_is_unaffected_by_the_delta_branch(seeded):
    control = read_control()
    control["mode"] = "Startup"
    write_control(control, WriteKind.MERGE, origin="legacy")
    dsa.execute_control_writes()
    assert read_control()["mode"] == "Startup"


def test_a_delta_and_a_legacy_patch_in_one_cycle_both_land_in_push_order(seeded):
    write_control(control_delta(set_values={"primary_setpoint": 225}), WriteKind.DELTA, origin="delta")
    stale = read_control()
    stale["s_plus"] = True
    write_control(stale, WriteKind.MERGE, origin="legacy")
    assert c.SqliteQueue("queue_control_write").length() == 2
    dsa.execute_control_writes()
    control = read_control()
    assert control["primary_setpoint"] == 225, "the legacy patch's stale copy must not revert the delta"
    assert control["s_plus"] is True


def test_a_legacy_patch_queued_first_does_not_stop_a_later_delta(seeded):
    stale = read_control()
    stale["s_plus"] = True
    write_control(stale, WriteKind.MERGE, origin="legacy")
    write_control(control_delta(set_values={"primary_setpoint": 225}), WriteKind.DELTA, origin="delta")
    dsa.execute_control_writes()
    control = read_control()
    assert control["s_plus"] is True
    assert control["primary_setpoint"] == 225


def test_two_deltas_restoring_the_opening_value_are_not_confused_with_silence(seeded):
    """Residual 2 at the seam."""
    opening = read_control()["primary_setpoint"]
    write_control(control_delta(set_values={"primary_setpoint": 225}), WriteKind.DELTA, origin="a")
    write_control(control_delta(set_values={"primary_setpoint": opening}), WriteKind.DELTA, origin="b")
    dsa.execute_control_writes()
    assert read_control()["primary_setpoint"] == opening


def test_a_delta_on_a_fresh_store_is_not_silently_dropped(ds):
    """Mirrors the seed guard at common/datastore_accessors.py:120-121."""
    write_settings_store(default_settings())
    c.datastore.delete_blob("control:general")
    write_control(control_delta(set_values={"mode": "Hold"}), WriteKind.DELTA, origin="app")
    dsa.execute_control_writes()
    assert read_control()["mode"] == "Hold"


def test_a_future_version_envelope_is_dropped_rather_than_applied(seeded, caplog):
    c.SqliteQueue("queue_control_write").push({CONTROL_DELTA_KEY: 99, "set": {"mode": "Hold"}, "origin": "future"})
    dsa.execute_control_writes()
    assert read_control()["mode"] != "Hold"


def _cmd(*args, origin="test"):
    from unittest import mock

    from common import api_commands

    with mock.patch.object(api_commands, "write_log"), mock.patch.object(c.time, "time", return_value=NOW):
        return api_commands.process_command(action="set", arglist=list(args), origin=origin)


def test_stop_then_pause_in_one_cycle_leaves_the_timer_stopped(seeded):
    control = read_control()
    control["timer"] = {"start": 1000.0, "paused": 0, "end": 2000.0}
    write_control(control, WriteKind.OVERWRITE, origin="seed")
    c.SqliteQueue("queue_control_write").flush()

    assert _cmd("timer", "stop")["result"] == "OK"
    assert _cmd("timer", "pause")["result"] == "OK"
    dsa.execute_control_writes()

    assert read_control()["timer"] == {"start": 0, "paused": 0, "end": 0}


def test_stop_then_resume_in_one_cycle_does_not_bring_back_the_old_end_time(seeded):
    control = read_control()
    control["timer"] = {"start": 1000.0, "paused": 1500.0, "end": 2000.0}
    write_control(control, WriteKind.OVERWRITE, origin="seed")
    c.SqliteQueue("queue_control_write").flush()

    assert _cmd("timer", "stop")["result"] == "OK"
    assert _cmd("timer", "start", "500")["result"] == "OK"
    dsa.execute_control_writes()

    assert read_control()["timer"] == {"start": NOW, "paused": 0, "end": NOW + 500}


def test_start_then_stop_in_one_cycle_leaves_the_timer_stopped(seeded):
    """Residual 2, through the real commands.

    Note the fourth member: default_control()'s timer is
    {start, paused, end, shutdown} (common/defaults.py). `shutdown` has one
    consumer (controller/runtime/modes/base.py) and no timer command has ever
    written it -- the stop/clear paths disarm notify_data's shutdown flag, not
    this one -- so timer.clear leaves it alone, exactly as the code it replaced
    did. Asserted whole rather than by member so a future op that starts
    touching it cannot slip through.
    """
    assert _cmd("timer", "start", "600")["result"] == "OK"
    assert _cmd("timer", "stop")["result"] == "OK"
    dsa.execute_control_writes()

    assert read_control()["timer"] == {"start": 0, "paused": 0, "end": 0, "shutdown": False}
