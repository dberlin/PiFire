from common import datastore_accessors as c
from common import defaults
from common.common import WriteKind, strip_null_members, read_events_records
from common import datastore


def test_default_control_manual_has_only_change_and_pwm_keys():
    # The per-pin boolean sub-keys (fan/auger/igniter/power) are vestigial: the
    # live manual-command handler (common/api_commands.py) only reads/writes
    # control['manual']['change'] (holds the active pin NAME, e.g. "igniter"),
    # ['output'] (set by the handler), and ['pwm']. Pins the intended shape.
    assert set(defaults.default_control()["manual"].keys()) == {"change", "pwm"}


def test_control_overwrite_and_read(ds):
    c.write_control({"mode": "Stop", "n": {"a": 1}}, WriteKind.OVERWRITE, origin="t")
    assert c.read_control() == {"mode": "Stop", "n": {"a": 1}}


def test_control_merge_matches_oracle(ds, oracle):
    exp = oracle("control_merge")
    c.write_control({"mode": "Stop", "nested": {"a": 1, "b": 2}}, WriteKind.OVERWRITE, origin="test")
    c.write_control({"nested": {"b": 9, "c": 3}}, WriteKind.MERGE, origin="webapp")
    assert c.read_control() == exp["before_execute"]  # MERGE deferred
    c.execute_control_writes()
    assert c.read_control() == exp["after_execute"]  # deep-merge, origin stripped


def test_control_merge_preserves_list_nested_nulls(ds):
    # notify_data is a list; its elements carry eta=None. json_patch replaces
    # arrays atomically without walking them, so a null nested inside a list
    # element must survive the merge verbatim (not be treated as a delete).
    c.write_control({"mode": "Stop", "notify_data": []}, WriteKind.OVERWRITE, origin="test")
    c.write_control({"notify_data": [{"label": "Grill", "eta": None, "target": 0}]}, WriteKind.MERGE, origin="notify")
    c.execute_control_writes()
    nd = c.read_control()["notify_data"]
    assert nd == [{"label": "Grill", "eta": None, "target": 0}]  # eta:None preserved, key present


def test_control_merge_does_not_delete_dict_nested_null_key(ds):
    # A partial carrying a dict-nested null (e.g. manual.change=None) must NOT
    # delete the key -- json_patch would (RFC 7386), but strip_null_members drops
    # the null first, so the stored value is left intact. This guards the
    # controller's unconditional control['manual']['change'] access from KeyError.
    c.write_control({"mode": "Stop", "manual": {"change": "pwm", "output": True}}, WriteKind.OVERWRITE, origin="test")
    c.write_control(
        {"primary_setpoint": 225, "manual": {"change": None, "output": None}}, WriteKind.MERGE, origin="app"
    )
    c.execute_control_writes()
    control = c.read_control()
    assert control["primary_setpoint"] == 225  # non-null keys still merge
    assert "change" in control["manual"] and "output" in control["manual"]  # keys NOT deleted
    assert control["manual"] == {"change": "pwm", "output": True}  # prior values preserved


def test_control_merge_ignores_client_supplied_nulls(ds):
    # The generic /api/control passthrough merges arbitrary client JSON. A client
    # sending a null is ignored (no-op), never deleting or nulling a stored key.
    c.write_control({"mode": "Stop", "primary_setpoint": 225}, WriteKind.OVERWRITE, origin="test")
    c.write_control({"mode": None, "primary_setpoint": 300}, WriteKind.MERGE, origin="app")
    c.execute_control_writes()
    control = c.read_control()
    assert control["mode"] == "Stop"  # client null ignored, prior value kept
    assert control["primary_setpoint"] == 300  # non-null client value applied


def test_control_merge_on_empty_db_seeds_default(ds):
    # With no control:general row yet, a MERGE must still land (seed default, then
    # patch) rather than being silently dropped by the UPDATE.
    assert datastore.get_blob("control:general") is None
    c.write_control({"primary_setpoint": 275}, WriteKind.MERGE, origin="app")
    c.execute_control_writes()
    control = c.read_control()
    assert control["primary_setpoint"] == 275
    assert control["mode"] == "Stop"  # rest of default_control present


def test_strip_null_members_recurses_dicts_but_not_lists():
    # Unit coverage for the helper: dict keys with None dropped at any depth;
    # lists (and nulls inside them) returned untouched.
    src = {"a": 1, "b": None, "nested": {"x": None, "y": 2}, "items": [{"eta": None}], "z": None}
    stripped = []
    assert strip_null_members(src, stripped) == {"a": 1, "nested": {"y": 2}, "items": [{"eta": None}]}
    # dotted paths of dropped keys reported; list-nested nulls not walked/reported
    assert sorted(stripped) == ["b", "nested.x", "z"]


def test_control_merge_logs_error_when_stripping_nulls(ds, caplog):
    # A MERGE partial carrying a null trips a diagnostic (ERROR level, so it
    # survives control.log's production ERROR gate) naming the stripped path and
    # origin, so the still-sending-nulls source can be found.
    c.write_control({"mode": "Stop", "manual": {"change": "pwm"}}, WriteKind.OVERWRITE, origin="test")
    c.write_control({"manual": {"change": None}}, WriteKind.MERGE, origin="app")
    with caplog.at_level("ERROR", logger="control"):
        c.execute_control_writes()
    hits = [r for r in caplog.records if "stripped null member" in r.message]
    assert hits and hits[0].levelname == "ERROR"
    assert "manual.change" in hits[0].message
    assert any("origin='app'" in r.getMessage() for r in caplog.records)


def test_control_merge_null_free_partial_logs_nothing(ds, caplog):
    # The common case (no nulls) must stay quiet -- no diagnostic noise.
    c.write_control({"mode": "Stop"}, WriteKind.OVERWRITE, origin="test")
    c.write_control({"primary_setpoint": 225}, WriteKind.MERGE, origin="app")
    with caplog.at_level("ERROR", logger="control"):
        c.execute_control_writes()
    assert not [r for r in caplog.records if "stripped null member" in r.message]


def test_errors_and_current_status_roundtrip(ds):
    c.write_errors(["e1"])
    assert c.read_errors() == ["e1"]
    c.write_status({"mode": "Hold"})
    assert c.read_status() == {"mode": "Hold"}


def test_autotune_uses_queue(ds):
    c.read_autotune(flush=True)
    c.write_autotune({"tr": 1})
    c.write_autotune({"tr": 2})
    assert c.read_autotune() == [{"tr": 1}, {"tr": 2}]
    assert c.read_autotune(size_only=True) == 2
    c.read_autotune(flush=True)
    assert c.read_autotune() == []


def test_warnings_read_and_clear_matches_oracle(ds, oracle):
    exp = oracle("warnings")
    c.write_warning("first")
    c.write_warning("second")
    assert c.read_warnings() == exp["read1"]
    assert c.read_warnings() == exp["read2_after_clear"]


def test_connected_users_add_remove(ds):
    assert c.read_connected_users() == []
    c.write_connected_user("sidA")
    c.write_connected_user("sidB")
    assert sorted(c.read_connected_users()) == ["sidA", "sidB"]
    c.remove_connected_user("sidA")
    assert c.read_connected_users() == ["sidB"]
    c.read_connected_users(flush=True)
    assert c.read_connected_users() == []


def test_flush_control_clears_only_control_not_history(ds):
    # seed history + a control blob + a queued write
    c.write_history(
        {"probe_history": {"primary": {"G": 1}, "food": {}, "aux": {}}, "primary_setpoint": 1, "notify_targets": {}}
    )
    c.write_control({"mode": "Hold"}, WriteKind.OVERWRITE, origin="t")
    c.write_control({"x": 1}, WriteKind.MERGE, origin="t")
    control = c.read_control(flush=True)
    assert control == defaults.default_control()  # reseeded default
    from common.sqlite_queue import SqliteQueue

    assert SqliteQueue("queue_control_write").length() == 0  # queue cleared
    assert len(c.read_history()) == 1  # history untouched


def test_wizard_install_status_roundtrip(ds):
    c.set_wizard_install_status(50, "Running", "log")
    assert c.get_wizard_install_status() == (50, "Running", "log")


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


def test_read_events_records_flush_clears_and_returns_empty(ds):
    assert read_events_records(flush=True) == []
