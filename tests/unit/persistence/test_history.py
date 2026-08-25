import json
import sqlite3

import pytest

from common.defaults import METRIC_COLUMNS, default_metrics
from common.persistence import control, history

SAMPLE_HISTORY = {
    "probe_history": {
        "primary": {"Grill": 225},
        "food": {"Food1": 145},
        "aux": {"Aux1": 80},
    },
    "primary_setpoint": 225,
    "notify_targets": {"Grill": 0, "Food1": 165},
}


def test_inactive_clear_request_is_queued_when_mode_startup_interleaves_enqueue(monkeypatch, ds):
    persisted = control.read_control()
    persisted["mode"] = "Stop"
    persisted["status"] = "inactive"
    persisted["cook_id"] = "inactive-session"
    control.write_control_snapshot(persisted, origin="control")
    history.write_history(SAMPLE_HISTORY)
    before = history.read_history()
    system_commands = history.SqliteQueue("queue_systemq")
    original_push = history.SqliteQueue.push

    def start_mode_then_push(queue, command):
        starting = control.read_control()
        starting["mode"] = "Startup"
        starting["status"] = "active"
        control.write_control_snapshot(starting, origin="control")
        return original_push(queue, command)

    monkeypatch.setattr(history.SqliteQueue, "push", start_mode_then_push)

    assert history.request_history_clear() == "queued"
    assert control.read_control()["mode"] == "Startup"
    assert control.read_control()["cook_id"] == "inactive-session"
    assert history.read_history() == before
    assert system_commands.list() == [["clear_history"]]


def test_empty_stores_preserve_exact_read_shapes_and_fresh_defaults(ds):
    assert history.read_history() == []
    assert history.read_all_metrics() == []
    assert history.read_metrics() == default_metrics()
    assert history.read_tr() == {}
    assert history.read_autotune() == []
    assert history.autotune_length() == 0

    first = history.read_metrics()
    first["mode"] = "caller mutation"
    assert history.read_metrics() == default_metrics()


def test_history_timestamp_columns_and_limit_boundaries(monkeypatch, ds):
    timestamps = iter((1.001, 2.002, 3.003))
    monkeypatch.setattr(history.time, "time", lambda: next(timestamps))

    for psp in (100, 200, 300):
        history.write_history(dict(SAMPLE_HISTORY, primary_setpoint=psp))

    expected = [
        {
            "T": 1000,
            "P": {"Grill": 225},
            "F": {"Food1": 145},
            "PSP": 100,
            "NT": {"Grill": 0, "Food1": 165},
            "AUX": {"Aux1": 80},
            "CR": None,
            "RCR": None,
            "FD": None,
        },
        {
            "T": 2001,
            "P": {"Grill": 225},
            "F": {"Food1": 145},
            "PSP": 200,
            "NT": {"Grill": 0, "Food1": 165},
            "AUX": {"Aux1": 80},
            "CR": None,
            "RCR": None,
            "FD": None,
        },
        {
            "T": 3003,
            "P": {"Grill": 225},
            "F": {"Food1": 145},
            "PSP": 300,
            "NT": {"Grill": 0, "Food1": 165},
            "AUX": {"Aux1": 80},
            "CR": None,
            "RCR": None,
            "FD": None,
        },
    ]
    assert history.read_history() == expected
    assert history.read_history(0) == expected
    assert history.read_history(-1) == expected
    assert history.read_history(10) == expected
    assert history.read_history(2) == expected[-2:]


def test_history_optional_extended_data_and_copy_ownership(monkeypatch, ds):
    monkeypatch.setattr(history.time, "time", lambda: 12.345)
    without_extended = dict(SAMPLE_HISTORY, ext_data={"fan": [1, 2]})
    with_extended = dict(SAMPLE_HISTORY, ext_data={"fan": [3, 4]})

    history.write_history(without_extended)
    history.write_history(with_extended, ext_data=True)
    with_extended["ext_data"]["fan"].append(5)

    first, second = history.read_history()
    assert set(first) == {"T", "P", "F", "PSP", "NT", "AUX", "CR", "RCR", "FD"}
    assert second["EXD"] == {"fan": [3, 4]}

    second["P"]["Grill"] = 999
    second["EXD"]["fan"].append(6)
    reread = history.read_history()[1]
    assert reread["P"] == {"Grill": 225}
    assert reread["EXD"] == {"fan": [3, 4]}


def test_history_retention_keeps_boundary_then_evicts_oldest(monkeypatch, ds):
    monkeypatch.setattr(history.time, "time", lambda: 1.0)

    for psp in (1, 2, 3):
        history.write_history(dict(SAMPLE_HISTORY, primary_setpoint=psp), maxsizelines=3)
    assert [row["PSP"] for row in history.read_history()] == [1, 2, 3]

    history.write_history(dict(SAMPLE_HISTORY, primary_setpoint=4), maxsizelines=3)
    assert [row["PSP"] for row in history.read_history()] == [2, 3, 4]

    history.flush_history()
    history.write_history(SAMPLE_HISTORY, maxsizelines=0)
    assert history.read_history() == []


def test_metric_append_stamps_mutable_input_and_preserves_column_shapes(monkeypatch, ds):
    monkeypatch.setattr(history.time, "time", lambda: 123.456)
    monkeypatch.setattr(history, "generate_uuid", lambda: "generated-id")
    metric = default_metrics()
    metric.update({"mode": "Startup", "primary_setpoint": 225, "smokeplus": False})

    assert history.append_metric(metric) is None
    assert metric["starttime"] == 123456.0
    assert metric["id"] == "generated-id"

    expected = metric.copy()
    for text_column in ("starttime_c", "endtime_c", "augerontime_c", "fanontime_c"):
        expected[text_column] = str(expected[text_column])
    assert history.read_metrics() == expected
    assert history.read_all_metrics() == [expected]
    assert list(history.read_metrics()) == METRIC_COLUMNS
    assert history.read_metrics()["smokeplus"] is False


def test_metric_rows_keep_distinct_display_identities(monkeypatch, ds):
    generated_ids = iter(("startup-row", "hold-row"))
    monkeypatch.setattr(history, "generate_uuid", lambda: next(generated_ids))

    history.append_metric(dict(default_metrics(), mode="Startup"))
    history.append_metric(dict(default_metrics(), mode="Hold"))

    assert [(row["id"], row["mode"]) for row in history.read_all_metrics()] == [
        ("startup-row", "Startup"),
        ("hold-row", "Hold"),
    ]


def test_metric_update_changes_only_last_row_and_inserts_when_empty(monkeypatch, ds):
    ids = iter(("first-id", "second-id"))
    monkeypatch.setattr(history, "generate_uuid", lambda: next(ids))
    monkeypatch.setattr(history.time, "time", lambda: 10.0)

    first = default_metrics()
    first.update({"mode": "Startup", "primary_setpoint": 100})
    second = default_metrics()
    second.update({"mode": "Hold", "primary_setpoint": 200, "pellet_brand_type": "Alder"})
    history.append_metric(first)
    history.append_metric(second)

    history.update_metrics({"mode": "Smoke", "pellet_brand_type": None, "unknown": "ignored"})
    rows = history.read_all_metrics()
    assert rows[0]["mode"] == "Startup"
    assert rows[0]["primary_setpoint"] == 100
    assert rows[1]["mode"] == "Smoke"
    assert rows[1]["primary_setpoint"] == 200
    assert rows[1]["pellet_brand_type"] is None
    assert "unknown" not in rows[1]

    unchanged = history.read_all_metrics()
    assert history.update_metrics({"unknown": "ignored"}) is None
    assert history.read_all_metrics() == unchanged

    history.flush_metrics()
    assert history.read_all_metrics() == []
    assert history.update_metrics({"mode": "First update"}) is None
    inserted = history.read_metrics()
    assert list(inserted) == METRIC_COLUMNS
    assert inserted["mode"] == "First update"
    assert inserted["id"] is None
    assert inserted["starttime"] is None
    assert inserted["smokeplus"] is False


def test_metric_flush_is_isolated_but_history_flush_couples_all_owned_state(monkeypatch, ds):
    monkeypatch.setattr(history.time, "time", lambda: 1.0)
    monkeypatch.setattr(history, "generate_uuid", lambda: "metric-id")
    history.write_history(SAMPLE_HISTORY)
    history.append_metric(default_metrics())
    live_control = control.read_control()
    live_control["cook_id"] = "cook-session-7"
    control.write_control_snapshot(live_control, origin="control")

    assert history.flush_metrics() is None
    assert len(history.read_history()) == 1
    assert history.read_all_metrics() == []
    assert control.read_control()["cook_id"] == "cook-session-7"

    history.append_metric(default_metrics())
    ds.set_blob("control:current", json.dumps({"marker": "must be reset"}))
    assert history.flush_history() is None
    assert history.read_history() == []
    assert history.read_all_metrics() == []
    assert control.read_control()["cook_id"] is None
    assert json.loads(ds.get_blob("control:current")) != {"marker": "must be reset"}


def test_history_flush_rolls_back_every_session_field_when_control_clear_fails(monkeypatch, ds):
    monkeypatch.setattr(history.time, "time", lambda: 1.0)
    history.write_history(SAMPLE_HISTORY)
    history.append_metric(default_metrics())
    live_control = control.read_control()
    live_control["cook_id"] = "cook-session-rollback"
    control.write_control_snapshot(live_control, origin="control")
    ds.set_blob("control:current", json.dumps({"marker": "retained"}))
    connection = ds.connection()
    connection.execute(
        """
        CREATE TRIGGER fail_cook_id_clear
        BEFORE UPDATE OF value ON kv
        WHEN OLD.key = 'control:general'
        BEGIN
            SELECT RAISE(FAIL, 'injected control snapshot failure');
        END
        """
    )

    with pytest.raises(sqlite3.IntegrityError, match="injected control snapshot failure"):
        history.flush_history()

    assert len(history.read_history()) == 1
    assert len(history.read_all_metrics()) == 1
    assert control.read_control()["cook_id"] == "cook-session-rollback"
    assert json.loads(ds.get_blob("control:current")) == {"marker": "retained"}


def test_tuning_blob_round_trip_and_copy_ownership(ds):
    assert history.read_tr() == {}
    values = {"Grill": {"samples": [41000, 40500]}}

    assert history.write_tr(values) is None
    values["Grill"]["samples"].append(40000)
    assert history.read_tr() == {"Grill": {"samples": [41000, 40500]}}

    loaded = history.read_tr()
    loaded["Grill"]["samples"].append(39500)
    assert history.read_tr() == {"Grill": {"samples": [41000, 40500]}}


def test_autotune_queue_preserves_order_values_length_and_flush_shape(ds):
    first = {"ref_T": 100, "probe_Tr": [40000]}
    second = {"ref_T": 113, "probe_Tr": [37000]}

    assert history.write_autotune(first) is None
    assert history.write_autotune(second) is None
    first["probe_Tr"].append(39000)

    expected = [
        {"ref_T": 100, "probe_Tr": [40000]},
        {"ref_T": 113, "probe_Tr": [37000]},
    ]
    assert history.autotune_length() == 2
    assert history.read_autotune() == expected

    loaded = history.read_autotune()
    loaded[0]["probe_Tr"].append(38000)
    assert history.read_autotune() == expected

    assert history.flush_autotune() == []
    assert history.read_autotune() == []
    assert history.autotune_length() == 0
