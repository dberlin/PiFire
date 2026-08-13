import json

import pytest

from common import datastore_accessors as c
from common.persistence import history as history_persistence
from common.persistence import install_state as install_persistence
from common import defaults
from common.common import ErrorKind, read_events_records, flush_events_records
from common import datastore
from common.control_delta import control_delta


def test_default_control_manual_has_only_change_and_pwm_keys():
    # The per-pin boolean sub-keys (fan/auger/igniter/power) are vestigial: the
    # live manual-command handler (common/api_commands.py) only reads/writes
    # control['manual']['change'] (holds the active pin NAME, e.g. "igniter"),
    # ['output'] (set by the handler), and ['pwm']. Pins the intended shape.
    assert set(defaults.default_control()["manual"].keys()) == {"change", "pwm"}


def test_control_snapshot_replaces_immediately_without_consuming_the_delta_queue(ds):
    c.write_control_snapshot(
        {"mode": "Stop", "primary_setpoint": 100, "nested": {"kept": True}},
        origin="seed",
    )
    c.enqueue_control_delta(control_delta(set_values={"primary_setpoint": 225}), origin="web")

    c.write_control_snapshot(
        {"mode": "Startup", "primary_setpoint": 150, "nested": {"replacement": True}},
        origin="control",
    )

    assert c.read_control() == {
        "mode": "Startup",
        "primary_setpoint": 150,
        "nested": {"replacement": True},
    }
    assert c.read_pending_control_writes() == (
        {"__control_delta__": 1, "set": {"primary_setpoint": 225}, "origin": "web"},
    )

    c.execute_control_writes()

    assert c.read_control() == {
        "mode": "Startup",
        "primary_setpoint": 225,
        "nested": {"replacement": True},
    }


def test_control_snapshot_copies_the_callers_mapping(ds):
    snapshot = {"mode": "Hold", "manual": {"pwm": 50}}

    c.write_control_snapshot(snapshot, origin="control")
    snapshot["manual"]["pwm"] = 99
    snapshot["mode"] = "Stop"

    assert c.read_control() == {"mode": "Hold", "manual": {"pwm": 50}}


def test_errors_and_current_status_roundtrip(ds):
    c.write_errors(ErrorKind.CONTROL, ["e1"])
    assert c.read_errors(ErrorKind.CONTROL) == ["e1"]
    c.write_status({"mode": "Hold"})
    assert c.read_status() == {"mode": "Hold"}


def _seed_all_three():
    c.write_errors(ErrorKind.CONTROL, ["control banner"])
    c.write_errors(ErrorKind.DISPLAY, ["display banner"])
    c.write_errors(ErrorKind.WEB, ["web banner"])


@pytest.mark.parametrize(
    "flushed, survivors",
    [
        (ErrorKind.CONTROL, [(ErrorKind.DISPLAY, "display banner"), (ErrorKind.WEB, "web banner")]),
        (ErrorKind.DISPLAY, [(ErrorKind.CONTROL, "control banner"), (ErrorKind.WEB, "web banner")]),
        (ErrorKind.WEB, [(ErrorKind.CONTROL, "control banner"), (ErrorKind.DISPLAY, "display banner")]),
    ],
)
def test_flushing_one_kind_leaves_the_other_two_intact(ds, flushed, survivors):
    """Each producing process boots and clears its own banners. The other
    processes' banners are durable and are NOT this one's to discard."""
    _seed_all_three()

    c.flush_errors(flushed)

    for kind, banner in survivors:
        assert c.read_errors(kind) == [banner], f"flushing {flushed} erased {kind}'s banner"
    assert c.read_errors(flushed) == []


def test_read_all_groups_by_kind_in_declaration_order(ds):
    _seed_all_three()
    assert c.read_errors(ErrorKind.ALL) == ["control banner", "display banner", "web banner"]


def test_read_all_keeps_a_kind_in_place_when_that_kind_is_rewritten(ds):
    """write_errors replaces a kind's rows, so the replacements get fresh ids.
    Ordering by id alone would jump a whole process's banners to the end of the
    strip every time that process restarted."""
    c.write_errors(ErrorKind.CONTROL, ["control first"])
    c.write_errors(ErrorKind.DISPLAY, ["display first"])
    assert c.read_errors(ErrorKind.ALL) == ["control first", "display first"]

    c.write_errors(ErrorKind.CONTROL, ["control rewritten"])

    assert c.read_errors(ErrorKind.ALL) == ["control rewritten", "display first"]


def test_all_is_a_read_only_selector(ds):
    with pytest.raises(ValueError):
        c.write_errors(ErrorKind.ALL, ["nope"])
    with pytest.raises(ValueError):
        c.flush_errors(ErrorKind.ALL)


@pytest.mark.parametrize("bad", ["control", None])
def test_a_bare_kind_is_rejected_by_every_accessor(ds, bad):
    with pytest.raises(ValueError):
        c.read_errors(bad)
    with pytest.raises(ValueError):
        c.write_errors(bad, ["nope"])
    with pytest.raises(ValueError):
        c.flush_errors(bad)


@pytest.mark.parametrize("kind", list(ErrorKind))
def test_reading_a_fresh_database_yields_an_empty_list_for_every_kind(ds, kind):
    assert c.read_errors(kind) == []


@pytest.mark.parametrize("kind", [ErrorKind.CONTROL, ErrorKind.DISPLAY, ErrorKind.WEB])
def test_write_replaces_the_kinds_list_rather_than_appending(ds, kind):
    c.write_errors(kind, ["a"])
    c.write_errors(kind, ["b"])
    assert c.read_errors(kind) == ["b"]


def test_flush_returns_the_new_empty_state_not_the_discarded_contents(ds):
    """Callers use the result as a fresh accumulator; handing back the
    pre-flush list would resurrect the previous run's banners."""
    c.write_errors(ErrorKind.CONTROL, ["stale banner"])
    assert c.flush_errors(ErrorKind.CONTROL) == []
    assert c.read_errors(ErrorKind.CONTROL) == []


def test_init_drops_the_legacy_error_blobs_and_is_idempotent(ds):
    """The kv rows the errors table replaced would otherwise outlive every
    process that could clear them."""
    datastore.set_blob("errors", json.dumps(["legacy control"]))
    datastore.set_blob("display_errors", json.dumps(["legacy display"]))

    datastore.init()

    assert datastore.exists_blob("errors") is False
    assert datastore.exists_blob("display_errors") is False

    datastore.init()  # a second boot has nothing left to delete

    assert datastore.exists_blob("errors") is False
    assert datastore.exists_blob("display_errors") is False


def test_autotune_uses_queue(ds):
    history_persistence.flush_autotune()
    history_persistence.write_autotune({"tr": 1})
    history_persistence.write_autotune({"tr": 2})
    assert history_persistence.read_autotune() == [{"tr": 1}, {"tr": 2}]
    assert history_persistence.autotune_length() == 2
    history_persistence.flush_autotune()
    assert history_persistence.read_autotune() == []


def test_read_warnings_snapshot_does_not_consume(ds):
    # The non-destructive property. Its absence was the original cross-consumer
    # bug: the Socket.IO poll ate the warnings before another consumer saw them.
    c.write_warning("first")
    c.write_warning("second")
    snap = c.read_warnings_snapshot()
    assert snap["warnings"] == ["first", "second"]
    assert c.read_warnings_snapshot()["warnings"] == ["first", "second"]
    c.clear_warnings_through(snap["max_id"])
    assert c.read_warnings_snapshot()["warnings"] == []


def test_read_warnings_snapshot_max_id_matches_the_returned_strings(ds):
    c.write_warning("first")
    c.write_warning("second")
    snap = c.read_warnings_snapshot()
    c.write_warning("third")  # raised after the snapshot; must outlive the clear
    # max_id belongs to the LAST string in the snapshot, so clearing through it
    # clears exactly what was returned and nothing more -- not "third", which
    # any over-large id (e.g. an unbounded clear) would also have caught.
    c.clear_warnings_through(snap["max_id"])
    assert c.read_warnings_snapshot()["warnings"] == ["third"]


def test_read_warnings_snapshot_is_empty_with_null_max_id(ds):
    assert c.read_warnings_snapshot() == {"warnings": [], "max_id": None}


def test_connected_users_add_remove(ds):
    assert c.read_connected_users() == []
    c.write_connected_user("sidA")
    c.write_connected_user("sidB")
    assert sorted(c.read_connected_users()) == ["sidA", "sidB"]
    c.remove_connected_user("sidA")
    assert c.read_connected_users() == ["sidB"]
    c.flush_connected_users()
    assert c.read_connected_users() == []


def test_flush_control_clears_only_control_not_history(ds):
    # seed history + a control blob + a queued write
    history_persistence.write_history(
        {"probe_history": {"primary": {"G": 1}, "food": {}, "aux": {}}, "primary_setpoint": 1, "notify_targets": {}}
    )
    c.write_control_snapshot({"mode": "Hold"}, origin="t")
    c.enqueue_control_delta(control_delta(set_values={"x": 1}), origin="t")
    control = c.flush_control()
    assert control == defaults.default_control()  # reseeded default
    from common.sqlite_queue import SqliteQueue

    assert SqliteQueue("queue_control_write").length() == 0  # queue cleared
    assert len(history_persistence.read_history()) == 1  # history untouched


def test_wizard_install_status_roundtrip(ds):
    install_persistence.set_wizard_install_status(50, "Running", "log")
    assert install_persistence.get_wizard_install_status() == (50, "Running", "log")


def test_read_generic_key_roundtrip(ds):
    c.write_generic_key("some_key", {"a": 1})
    assert c.read_generic_key("some_key") == {"a": 1}


def test_read_events_records_returns_dicts(ds, monkeypatch):
    fake_events = [[f"2024-01-0{i}", f"0{i}:00:00", f"message {i}\n"] for i in range(1, 5)]

    def fake_read_events(legacy=True):
        return fake_events, len(fake_events)

    monkeypatch.setattr("common.common.read_events", fake_read_events)

    result = read_events_records()

    assert isinstance(result, list)
    assert len(result) == len(fake_events)
    for idx, event in enumerate(result):
        assert set(event.keys()) == {"date", "time", "message"}
        assert event["date"] == fake_events[idx][0]
        assert event["time"] == fake_events[idx][1]
        assert event["message"] == fake_events[idx][2].strip("\n")


def test_read_events_records_caps_at_60(ds, monkeypatch):
    fake_events = [[f"2024-01-01", "00:00:00", f"message {i}\n"] for i in range(100)]

    def fake_read_events(legacy=True):
        return fake_events, len(fake_events)

    monkeypatch.setattr("common.common.read_events", fake_read_events)

    result = read_events_records()

    assert len(result) == 60


def test_flush_events_records_clears_and_returns_empty(ds):
    assert flush_events_records() == []


def test_read_probe_status_skips_unknown_type_without_raising(ds):
    # Regression: an unexpected probe['type'] used to leave `section` unbound,
    # raising UnboundLocalError when it was the first probe. Now such probes are
    # skipped, not misfiled. The known Primary probe must still be reported.
    c.write_generic_key(
        "probe_device_info",
        [
            {"device": "dev_unknown", "status": {"s": 1}, "config": {}},
            {"device": "dev_primary", "status": {"s": 2}, "config": {}},
        ],
    )
    probe_info = [
        {"type": "Bogus", "label": "Weird", "device": "dev_unknown"},
        {"type": "Primary", "label": "Grill", "device": "dev_primary"},
    ]

    result = c.read_probe_status(probe_info)  # must not raise UnboundLocalError

    # Known probe still filed correctly...
    assert result["P"]["Grill"]["status"] == {"s": 2}
    # ...and the unknown-type probe is not misfiled into any section.
    assert "Weird" not in result["P"]
    assert "Weird" not in result["F"]
    assert "Weird" not in result["AUX"]


def test_read_probe_status_unknown_type_not_misfiled_after_prior_probe(ds):
    # Regression: with no else-branch, a probe with an unexpected type retained
    # the PREVIOUS probe's `section`, silently misfiling its data into the wrong
    # bucket. Here the unknown-type probe follows a Primary probe; it must not
    # land in the "P" bucket.
    c.write_generic_key(
        "probe_device_info",
        [
            {"device": "dev_primary", "status": {"s": 1}, "config": {}},
            {"device": "dev_unknown", "status": {"s": 9}, "config": {}},
        ],
    )
    probe_info = [
        {"type": "Primary", "label": "Grill", "device": "dev_primary"},
        {"type": "Bogus", "label": "Weird", "device": "dev_unknown"},
    ]

    result = c.read_probe_status(probe_info)

    assert result["P"]["Grill"]["status"] == {"s": 1}
    assert "Weird" not in result["P"]  # not misfiled into the prior probe's bucket
    assert "Weird" not in result["F"]
    assert "Weird" not in result["AUX"]
