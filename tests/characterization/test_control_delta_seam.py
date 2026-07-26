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


# --- the two arbitrary-patch doors -----------------------------------------
#
# Both take a client-supplied patch, which is ALREADY a statement of intent --
# the client sent only what it means -- so neither needs a client change to
# become a delta; the server wraps it. Two members are special, and are the
# reason wrapping is not merely mechanical: notify_data travels WHOLE (an
# omitted entry is a deletion, not silence) and becomes an explicit
# notify.replace, and `timer` is refused outright.


@pytest.fixture
def client(seeded):
    from app import app as flask_app

    flask_app.config.update(TESTING=True)
    return flask_app.test_client()


@pytest.fixture
def sio(seeded):
    """The socket door. Mirrors tests/web/test_socketio_app_data.py's fixture,
    including its neutralization of every hazardous dispatch."""
    from unittest import mock

    import blueprints.mobile.socket_io as socket_io

    with (
        mock.patch.object(socket_io, "restart_control"),
        mock.patch.object(socket_io, "restart_webapp"),
        mock.patch.object(socket_io, "restart_scripts"),
    ):
        yield socket_io


def test_post_control_rejects_a_timer_value(client):
    """control["timer"] is a coupled value object. A client that posts one is
    computing a timer state from a read it cannot trust; make it use the REST
    timer grammar, which is now an op."""
    resp = client.post("/api/control", json={"timer": {"start": 0, "paused": 0, "end": 0}})
    assert resp.status_code == 400
    assert "timer" in resp.get_json()["message"]


def test_post_control_still_accepts_ordinary_members(client):
    assert client.post("/api/control", json={"mode": "Startup", "s_plus": True}).status_code == 201
    dsa.execute_control_writes()
    control = read_control()
    assert control["mode"] == "Startup"
    assert control["s_plus"] is True


def test_post_control_routes_notify_data_through_the_replace_op(client):
    """saveTargetEdit's shape. An omitted entry still means DELETE -- now by name."""
    entries = [{"label": "Only", "type": "probe", "req": True, "target": 165}]
    assert client.post("/api/control", json={"notify_data": entries}).status_code == 201
    dsa.execute_control_writes()
    assert read_control()["notify_data"] == entries


def test_socket_control_door_rejects_a_timer_value(sio):
    resp = sio._post_app_data("update_action", "control", json.dumps({"timer": {"start": 0, "paused": 0, "end": 0}}))
    assert resp["result"] == "Error"
    assert "timer" in resp["message"]
    assert c.SqliteQueue("queue_control_write").length() == 0


def test_socket_control_door_still_accepts_ordinary_members(sio):
    assert sio._post_app_data("update_action", "control", json.dumps({"s_plus": True}))["result"] == "OK"
    dsa.execute_control_writes()
    assert read_control()["s_plus"] is True


def test_socket_timer_stop_then_pause_leaves_the_timer_stopped(sio):
    """The socket door is a SECOND implementation of the same timer grammar
    common/api_commands.py serves. Both now emit the same ops, so this pair
    composes here exactly as it does over REST."""
    control = read_control()
    control["timer"] = {"start": 1000.0, "paused": 0, "end": 2000.0}
    write_control(control, WriteKind.OVERWRITE, origin="seed")
    c.SqliteQueue("queue_control_write").flush()

    payload = json.dumps({"timer_action": {}})
    assert sio._post_app_data("timer_action", "stop_timer", payload)["result"] == "OK"
    assert sio._post_app_data("timer_action", "pause_timer", payload)["result"] == "OK"
    dsa.execute_control_writes()

    assert read_control()["timer"] == {"start": 0, "paused": 0, "end": 0}


def _notify_entry(label, type_):
    return next(e for e in read_control()["notify_data"] if e["label"] == label and e["type"] == type_)


def test_a_notify_target_set_back_to_the_cycles_opening_value_still_lands(seeded):
    """Residual 2 for notify_data -- and the plan's own example of it was wrong.

    A restore is only invisible when it restores the value THIS CYCLE began
    with. Setting a target to 203, draining, then setting it to 0 alongside a
    concurrent writer already worked: 0 differs from that drain's ancestor, so
    merge_notify_data applied it. The case that did NOT work is both writes in
    ONE cycle -- the second field equals the ancestor exactly, so it carried no
    evidence its writer touched anything and the FIRST write won.
    """
    opening = _notify_entry("Grill", "probe")["target"]
    assert _cmd("notify", "Grill", "target", "203")["result"] == "OK"
    assert _cmd("notify", "Grill", "target", str(opening))["result"] == "OK"
    dsa.execute_control_writes()
    assert _notify_entry("Grill", "probe")["target"] == opening


def test_a_notify_write_is_not_reverted_by_a_concurrent_whole_dict_writer(seeded):
    """The other half: a notify op and an unrelated command sharing one cycle."""
    assert _cmd("notify", "Grill", "target", "203")["result"] == "OK"
    assert _cmd("splus", "true")["result"] == "OK"
    dsa.execute_control_writes()
    assert _notify_entry("Grill", "probe")["target"] == 203
    assert read_control()["s_plus"] is True
