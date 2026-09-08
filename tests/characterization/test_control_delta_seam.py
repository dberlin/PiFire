"""Queued control writes are versioned deltas only; persisted legacy rows are
rejected rather than guessed at startup."""

import json

import pytest

from common import common as c
from common import api_commands
from common.control_delta import CONTROL_DELTA_KEY, CONTROL_DELTA_VERSION, control_delta
from common.defaults import default_settings
from common.persistence import control as control_persistence
from common.persistence.control import (
    default_control,
    read_control,
)
from common.persistence.runtime import (
    write_settings_store,
)
from common.timer import parse_timer, pause_timer, remaining_seconds, start_timer
from tests.fakes.clock import clock_stamp

NOW = 1_700_000_000.0
STAMP = clock_stamp(wall_s=NOW)


@pytest.fixture
def seeded(ds, monkeypatch):
    write_settings_store(default_settings())
    control_persistence.write_control_snapshot(default_control(), origin="test-delta-seam")
    c.SqliteQueue("queue_control_write").flush()
    monkeypatch.setattr(api_commands, "read_control_heartbeat", lambda: STAMP)
    monkeypatch.setattr(api_commands, "local_clock_stamp", lambda: STAMP)
    return ds


def test_a_delta_is_queued_verbatim_with_an_origin_stamp(seeded):
    control_persistence.enqueue_control_delta(control_delta(set_values={"mode": "Hold"}), origin="app")
    rows = c.datastore.connection().execute("SELECT value FROM queue_control_write ORDER BY id").fetchall()
    assert json.loads(rows[0][0]) == {
        CONTROL_DELTA_KEY: CONTROL_DELTA_VERSION,
        "set": {"mode": "Hold"},
        "origin": "app",
    }


def test_a_delta_write_lands_on_the_blob(seeded):
    control_persistence.enqueue_control_delta(
        control_delta(set_values={"mode": "Hold", "primary_setpoint": 225}),
        origin="app",
    )
    control_persistence.execute_control_writes(timer_now=STAMP)
    control = read_control()
    assert control["mode"] == "Hold"
    assert control["primary_setpoint"] == 225


def test_delta_rows_apply_in_fifo_order(seeded):
    control_persistence.enqueue_control_delta(control_delta(set_values={"primary_setpoint": 225}), origin="first")
    control_persistence.enqueue_control_delta(control_delta(set_values={"primary_setpoint": 275}), origin="second")

    control_persistence.execute_control_writes(timer_now=STAMP)

    assert read_control()["primary_setpoint"] == 275
    assert c.SqliteQueue("queue_control_write").length() == 0


def test_a_malformed_versioned_row_is_rejected_atomically_and_dequeued(seeded, caplog):
    opening = read_control()
    queue = c.SqliteQueue("queue_control_write")
    queue.push({CONTROL_DELTA_KEY: CONTROL_DELTA_VERSION, "set": [], "origin": "malformed-writer"})
    row_id, _ = queue.list_with_ids()[0]

    with caplog.at_level("ERROR", logger="control"):
        control_persistence.execute_control_writes(timer_now=STAMP)

    assert read_control() == opening
    assert queue.length() == 0
    messages = [record.getMessage() for record in caplog.records]
    assert any(f"id={row_id}" in message and "origin='malformed-writer'" in message for message in messages)


def test_an_unversioned_legacy_row_is_rejected_dequeued_and_logged(seeded, caplog):
    opening = read_control()
    queue = c.SqliteQueue("queue_control_write")
    queue.push({"mode": "Startup", "origin": "legacy-web"})
    row_id, _ = queue.list_with_ids()[0]

    with caplog.at_level("ERROR", logger="control"):
        control_persistence.execute_control_writes(timer_now=STAMP)

    assert read_control() == opening
    assert queue.length() == 0
    messages = [record.getMessage() for record in caplog.records]
    assert any(f"id={row_id}" in message and "origin='legacy-web'" in message for message in messages)


def test_two_deltas_restoring_the_opening_value_are_not_confused_with_silence(seeded):
    """A write restoring the value the cycle began with is not read as silence."""
    opening = read_control()["primary_setpoint"]
    control_persistence.enqueue_control_delta(control_delta(set_values={"primary_setpoint": 225}), origin="a")
    control_persistence.enqueue_control_delta(control_delta(set_values={"primary_setpoint": opening}), origin="b")
    control_persistence.execute_control_writes(timer_now=STAMP)
    assert read_control()["primary_setpoint"] == opening


def test_a_delta_on_a_fresh_store_is_not_silently_dropped(ds):
    """Mirrors the fresh-store seed guard in ``common.persistence.control``."""
    write_settings_store(default_settings())
    c.datastore.delete_blob("control:general")
    control_persistence.enqueue_control_delta(control_delta(set_values={"mode": "Hold"}), origin="app")
    control_persistence.execute_control_writes(timer_now=STAMP)
    assert read_control()["mode"] == "Hold"


def test_a_future_version_envelope_is_rejected_and_dequeued(seeded, caplog):
    queue = c.SqliteQueue("queue_control_write")
    queue.push({CONTROL_DELTA_KEY: 99, "set": {"mode": "Hold"}, "origin": "future"})
    row_id, _ = queue.list_with_ids()[0]

    with caplog.at_level("ERROR", logger="control"):
        control_persistence.execute_control_writes(timer_now=STAMP)

    assert read_control()["mode"] != "Hold"
    assert queue.length() == 0
    assert any(
        f"id={row_id}" in record.getMessage() and "origin='future'" in record.getMessage() for record in caplog.records
    )


def _cmd(*args, origin="test"):
    from unittest import mock

    from common import api_commands

    with mock.patch.object(api_commands, "write_log"), mock.patch.object(c.time, "time", return_value=NOW):
        return api_commands.process_command(action="set", arglist=list(args), origin=origin)


def test_stop_then_pause_in_one_cycle_leaves_the_timer_stopped(seeded):
    control = read_control()
    control["timer"] = start_timer(1000, STAMP)
    control_persistence.write_control_snapshot(control, origin="seed")
    c.SqliteQueue("queue_control_write").flush()

    assert _cmd("timer", "stop")["result"] == "OK"
    assert _cmd("timer", "pause")["result"] == "OK"
    control_persistence.execute_control_writes(timer_now=STAMP)

    timer = parse_timer(read_control()["timer"])
    assert timer["state"] == "stopped"
    assert remaining_seconds(timer, STAMP) == 0
    assert timer["action_armed"] is False


def test_stop_then_resume_in_one_cycle_does_not_bring_back_the_old_end_time(seeded):
    control = read_control()
    control["timer"] = pause_timer(start_timer(1000, STAMP), STAMP)
    control_persistence.write_control_snapshot(control, origin="seed")
    c.SqliteQueue("queue_control_write").flush()

    assert _cmd("timer", "stop")["result"] == "OK"
    assert _cmd("timer", "start", "500")["result"] == "OK"
    control_persistence.execute_control_writes(timer_now=STAMP)

    timer = parse_timer(read_control()["timer"])
    assert timer["state"] == "running"
    assert remaining_seconds(timer, STAMP) == 500


def test_start_then_stop_in_one_cycle_leaves_the_timer_stopped(seeded):
    """A start followed by an explicit stop must restore a disarmed countdown."""
    assert _cmd("timer", "start", "600")["result"] == "OK"
    assert _cmd("timer", "stop")["result"] == "OK"
    control_persistence.execute_control_writes(timer_now=STAMP)

    timer = parse_timer(read_control()["timer"])
    assert timer["state"] == "stopped"
    assert remaining_seconds(timer, STAMP) == 0
    assert timer["action_armed"] is False


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
    # Local to this module because it hangs off `seeded`, not `ds`, so it
    # cannot share tests/web/conftest.py's client. Kept context-managed to
    # match it.
    from app import app as flask_app

    flask_app.config["TESTING"] = True
    with flask_app.test_client() as test_client:
        yield test_client


@pytest.fixture
def sio(seeded):
    """The socket door. Mirrors tests/web/test_socketio_app_data.py's fixture,
    including its neutralization of every hazardous dispatch."""
    from unittest import mock

    from blueprints.mobile import socket_io

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
    control_persistence.execute_control_writes(timer_now=STAMP)
    control = read_control()
    assert control["mode"] == "Startup"
    assert control["s_plus"] is True


def test_post_control_routes_notify_data_through_the_replace_op(client):
    """The legacy whole-array shape. An omitted entry still means DELETE -- now
    by name. No in-repo client posts this; it is kept for ones that already do."""
    entries = [{"label": "Only", "type": "probe", "req": True, "target": 165}]
    assert client.post("/api/control", json={"notify_data": entries}).status_code == 201
    control_persistence.execute_control_writes(timer_now=STAMP)
    assert read_control()["notify_data"] == entries


def test_post_control_rejects_explicit_null_notify_shutdown(client):
    response = client.post(
        "/api/control",
        json={
            "notify_data": [
                {"label": "Only", "type": "probe", "req": True, "shutdown": None},
            ],
        },
    )
    assert response.status_code == 400
    body = response.get_json()
    assert set(body) == {"control", "result", "message"}
    assert body["control"] == "error"
    assert body["result"] == "error"
    assert "shutdown" in body["message"]


def test_post_control_routes_notify_updates_through_per_entry_set_ops(client):
    """saveTargetEdit's shape. Names ONE entry and the fields it changes."""
    update = {"label": "Grill", "type": "probe", "fields": {"req": True, "target": 165, "shutdown": True}}
    assert client.post("/api/control", json={"notify_updates": [update]}).status_code == 201
    control_persistence.execute_control_writes(timer_now=STAMP)
    entry = _notify_entry("Grill", "probe")
    assert (entry["req"], entry["target"], entry["shutdown"]) == (True, 165, True)


def test_a_posted_notify_update_does_not_clobber_a_concurrent_timer_arm(client):
    """THE regression this key exists to close.

    A whole `notify_data` array posted from a queue-blind read reverts every
    other entry to whatever the client last saw -- including the timer entry a
    second writer armed inside the same control cycle. It cannot say WHICH
    fields it meant, so nothing at the drain can tell an intentional deletion
    from an omission. An addressed update says it, so both writes land.
    """
    stale = read_control()["notify_data"]  # what a client's cached copy holds
    assert _cmd("timer", "start", "600")["result"] == "OK"
    update = {"label": "Grill", "type": "probe", "fields": {"req": True, "target": 165}}
    assert client.post("/api/control", json={"notify_updates": [update]}).status_code == 201
    control_persistence.execute_control_writes(timer_now=STAMP)

    assert _notify_entry("Grill", "probe")["target"] == 165
    assert _notify_entry("Timer", "timer")["req"] is True, "the timer arm was clobbered"
    assert remaining_seconds(parse_timer(read_control()["timer"]), STAMP) == 600

    # And the same pair through the OLD door still loses the timer arm, which is
    # why no in-repo client posts it any more.
    assert client.post("/api/control", json={"notify_data": stale}).status_code == 201
    control_persistence.execute_control_writes(timer_now=STAMP)
    assert _notify_entry("Timer", "timer")["req"] is False


def test_post_control_rejects_a_malformed_notify_update(client):
    """Named at request time, in this process, rather than swallowed into the
    generic 201 where a caller cannot tell a rejection from an accepted write."""
    resp = client.post("/api/control", json={"notify_updates": [{"type": "probe", "fields": {}}]})
    assert resp.status_code == 400
    assert "label" in resp.get_json()["message"]
    assert c.SqliteQueue("queue_control_write").length() == 0


def _notify_entry(label, type_):
    return next(e for e in read_control()["notify_data"] if e["label"] == label and e["type"] == type_)


def test_a_notify_target_set_back_to_the_cycles_opening_value_still_lands(seeded):
    """A restore-to-opening for notify_data.

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
    control_persistence.execute_control_writes(timer_now=STAMP)
    assert _notify_entry("Grill", "probe")["target"] == opening


def test_a_notify_write_is_not_reverted_by_a_concurrent_whole_dict_writer(seeded):
    """The other half: a notify op and an unrelated command sharing one cycle."""
    assert _cmd("notify", "Grill", "target", "203")["result"] == "OK"
    assert _cmd("splus", "true")["result"] == "OK"
    control_persistence.execute_control_writes(timer_now=STAMP)
    assert _notify_entry("Grill", "probe")["target"] == 203
    assert read_control()["s_plus"] is True


def test_a_setpoint_set_back_to_its_opening_value_survives_a_concurrent_writer(seeded):
    """A restore-to-opening for a scalar: both writes inside ONE cycle, the
    second restoring the value the cycle began with."""
    opening = read_control()["primary_setpoint"]
    assert _cmd("psp", "225")["result"] == "OK"
    assert _cmd("psp", str(opening))["result"] == "OK"
    control_persistence.execute_control_writes(timer_now=STAMP)
    assert read_control()["primary_setpoint"] == opening


def test_a_manual_pwm_change_and_a_fan_toggle_in_one_cycle_both_land(seeded):
    """A fan toggle used to carry a whole stale manual object, so it re-imposed
    the pwm the cycle began with. It now names only change/output."""
    control = read_control()
    control["mode"] = "Manual"
    control_persistence.write_control_snapshot(control, origin="seed")
    c.SqliteQueue("queue_control_write").flush()
    assert _cmd("manual", "pwm", "50")["result"] == "OK"
    assert _cmd("manual", "fan", "true")["result"] == "OK"
    control_persistence.execute_control_writes(timer_now=STAMP)
    manual = read_control()["manual"]
    assert manual["pwm"] == 50
    assert manual["change"] == "fan"
    assert manual["output"] is True


def test_a_background_system_write_does_not_hide_a_restore_to_the_opening_value(seeded):
    """gather_system_info writes only control["system"]. Under the reduce it
    still carried a stale copy of everything else, and a concurrent writer
    restoring a member to its opening value was invisible.

    The sibling in tests/characterization/test_control_writes_cross_writer.py
    (test_background_full_control_write_does_not_eat_a_notify_write) passes
    under BOTH write models, because a plain change differs from the ancestor.
    Only a restore-to-opening tells the two apart.
    """
    opening = _notify_entry("Grill", "probe")["target"]
    assert _cmd("notify", "Grill", "target", "203")["result"] == "OK"
    assert _cmd("notify", "Grill", "target", str(opening))["result"] == "OK"
    # A background writer naming only its own slice, exactly as gather_system_info
    # now does.
    control_persistence.enqueue_control_delta(
        control_delta(set_values={"system": {"cpu_temp": 42.0}}),
        origin="app-socketio",
    )
    control_persistence.execute_control_writes(timer_now=STAMP)

    control = read_control()
    assert _notify_entry("Grill", "probe")["target"] == opening
    assert control["system"]["cpu_temp"] == 42.0


# ---------------------------------------------------------------------------
# THE invariant.
# ---------------------------------------------------------------------------

_PAIRS = [
    (("timer", "start", "600"), ("timer", "stop")),
    (("timer", "stop"), ("timer", "pause")),
    (("timer", "pause"), ("timer", "stop")),
    (("psp", "225"), ("splus", "true")),
    (("psp", "225"), ("psp", "0")),
    (("notify", "Grill", "target", "203"), ("notify", "Grill", "req", "true")),
    (("notify", "Grill", "target", "203"), ("notify", "Grill", "target", "0")),
    (("splus", "true"), ("pmode", "2")),
    (("timer", "start", "600"), ("timer", "shutdown", "true")),
]


@pytest.mark.parametrize("first,second", _PAIRS, ids=lambda p: "_".join(str(x) for x in p))
def test_two_commands_in_one_cycle_match_the_same_two_one_cycle_apart(seeded, first, second):
    """THE invariant: two commands in one cycle land exactly what the same two
    land a cycle apart. Every op and every `set` exists to hold it.

    Scoped to ACCEPTED commands: request-time validation (e.g. the 4-argument
    timer form's paused-timer rejection) reads a stale blob, and no queue
    representation can fix a synchronous HTTP answer.
    """

    def _run(drain_between):
        control_persistence.write_control_snapshot(default_control(), origin="prop")
        c.SqliteQueue("queue_control_write").flush()
        assert _cmd(*first)["result"] == "OK"
        if drain_between:
            control_persistence.execute_control_writes(timer_now=STAMP)
        assert _cmd(*second)["result"] == "OK"
        control_persistence.execute_control_writes(timer_now=STAMP)
        result = read_control()
        # Each independent run creates fresh timer identities; identity equality
        # is not part of the queue scheduling invariant.
        result["timer"].pop("timer_id")
        return result

    assert _run(drain_between=False) == _run(drain_between=True)
