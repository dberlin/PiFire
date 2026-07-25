import pytest


@pytest.fixture
def store(tmp_path):
    from common import datastore

    datastore._reset_for_tests(str(tmp_path / "t.db"))
    datastore.init()
    from controller.runtime.store import SqliteStore

    yield SqliteStore()
    datastore._reset_for_tests(None)


def test_sqlite_store_smoke(store):
    # Read-only smoke: exercises the pass-through against a hermetic SQLite DB.
    assert isinstance(store.read_control(), dict)
    assert isinstance(store.read_settings(), dict)


def test_sqlite_display_queue_roundtrip(store):
    store.display_commands().flush()
    store.display_commands().push(["text", "ERROR"])
    assert store.display_commands().drain() == [["text", "ERROR"]]


def test_sqlite_append_metric_without_metrics_does_not_crash(store):
    # Regression: append_metric() with no metrics must defer to
    # common's default_metrics() (passing None crashed on metrics['starttime']).
    # The control loop calls this at the start of every work cycle.
    store.flush_metrics()  # reset metrics list
    store.append_metric()  # must NOT raise
    current = store.read_metrics()
    assert isinstance(current, dict)
    assert "starttime" in current  # populated from default_metrics() + starttime


def test_sqlite_control_write_semantics_parity(store):
    # Proves SqliteStore's OVERWRITE / MERGE / execute_control_writes match the
    # deferred deep-merge semantics that InMemoryStore replicates, against a
    # real (temp-DB) SQLite backend.
    from common.common import WriteKind

    store.write_control({"mode": "Stop", "nested": {"x": 1, "y": 2}}, WriteKind.OVERWRITE)
    assert store.read_control() == {"mode": "Stop", "nested": {"x": 1, "y": 2}}
    # MERGE is deferred until execute_control_writes
    store.write_control({"nested": {"x": 9}}, WriteKind.MERGE, origin="test")
    assert store.read_control()["nested"] == {"x": 1, "y": 2}
    store.execute_control_writes()
    # deep-merged: x replaced, y preserved, mode untouched, origin stripped
    assert store.read_control()["nested"] == {"x": 9, "y": 2}
    assert store.read_control()["mode"] == "Stop"
    assert "origin" not in store.read_control()


def test_control_merge_null_handling_parity(store):
    # SqliteStore (real json_patch) and InMemoryStore (deep_update) must agree on
    # null handling: dict-nested nulls are ignored (key kept), list-nested nulls
    # are preserved. This is the contract that keeps the two backends swappable.
    from common.common import WriteKind
    from controller.runtime.store import InMemoryStore

    seed = {"mode": "Stop", "manual": {"change": "pwm"}, "notify_data": [{"eta": 0}]}
    partial = {"mode": None, "primary_setpoint": 275, "manual": {"change": None}, "notify_data": [{"eta": None}]}
    expected = {
        "mode": "Stop",  # client null ignored
        "primary_setpoint": 275,  # non-null applied
        "manual": {"change": "pwm"},  # dict-nested null ignored, key kept
        "notify_data": [{"eta": None}],  # list replaced atomically, null preserved
    }

    mem = InMemoryStore()
    for st in (store, mem):
        st.write_control(dict(seed), WriteKind.OVERWRITE)
        st.write_control(dict(partial), WriteKind.MERGE, origin="app")
        st.execute_control_writes()
        assert st.read_control() == expected


def test_notify_data_cross_writer_merge_parity(store):
    # SqliteStore (json_patch + merge_notify_data) and InMemoryStore
    # (deep_update + merge_notify_data) must agree that two writers in ONE
    # cycle, each sending the whole notify_data array built from the same
    # pre-drain read, both survive. This is the cross-process seam: the web
    # process queues, the control process drains, and the two stores are
    # swappable only if they resolve that identically.
    from common.common import WriteKind
    from controller.runtime.store import InMemoryStore

    base = [
        {"label": "Grill", "type": "probe", "req": False, "target": 0},
        {"label": "Grill", "type": "probe_limit_high", "req": False, "target": 0},
        {"label": "Timer", "type": "timer", "req": False, "shutdown": False},
    ]

    def _writer(mutate):
        """A whole-array write built from the caller's stale (pre-drain) read."""
        stale = [dict(entry) for entry in base]
        mutate(stale)
        return {"notify_data": stale}

    def _arm_probe(array):
        array[0].update(req=True, target=203)

    def _arm_timer(array):
        array[2].update(req=True, shutdown=True)

    expected = [
        {"label": "Grill", "type": "probe", "req": True, "target": 203},
        {"label": "Grill", "type": "probe_limit_high", "req": False, "target": 0},
        {"label": "Timer", "type": "timer", "req": True, "shutdown": True},
    ]

    mem = InMemoryStore()
    for st in (store, mem):
        st.write_control({"mode": "Stop", "notify_data": [dict(e) for e in base]}, WriteKind.OVERWRITE)
        st.write_control(_writer(_arm_probe), WriteKind.MERGE, origin="app")
        st.write_control(_writer(_arm_timer), WriteKind.MERGE, origin="app-socketio")
        st.execute_control_writes()
        assert st.read_control()["notify_data"] == expected


def test_whole_dict_cross_writer_merge_parity(store):
    # Same seam, now for every member of the control dict rather than just the
    # notify_data array: SqliteStore reduces then json_patches, InMemoryStore
    # reduces then deep_updates, and they must land on the same state. Both
    # writers below queue the WHOLE dict from the same pre-drain read, which is
    # what every production call site does.
    from common.common import WriteKind
    from controller.runtime.store import InMemoryStore

    base = {
        "mode": "Stop",
        "updated": False,
        "s_plus": False,
        "primary_setpoint": 0,
        "settings_update": False,
        "manual": {"change": False, "output": False, "pwm": 100},
        "timer": {"start": 0, "paused": 0, "end": 0},
    }

    def _writer(**changes):
        stale = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
        for key, value in changes.items():
            if isinstance(value, dict):
                stale[key].update(value)
            else:
                stale[key] = value
        return stale

    expected = {
        "mode": "Hold",
        "updated": True,
        "s_plus": True,
        "primary_setpoint": 225,
        "settings_update": True,
        "manual": {"change": "fan", "output": True, "pwm": 100},
        "timer": {"start": 500.0, "paused": 0, "end": 1100.0},
    }

    mem = InMemoryStore()
    for st in (store, mem):
        st.write_control({k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}, WriteKind.OVERWRITE)
        st.write_control(_writer(mode="Hold", primary_setpoint=225, updated=True), WriteKind.MERGE, origin="app")
        st.write_control(_writer(s_plus=True), WriteKind.MERGE, origin="app-socketio")
        st.write_control(_writer(settings_update=True), WriteKind.MERGE, origin="app")
        st.write_control(_writer(manual={"change": "fan", "output": True}), WriteKind.MERGE, origin="app")
        st.write_control(_writer(timer={"start": 500.0, "end": 1100.0}), WriteKind.MERGE, origin="display")
        st.execute_control_writes()
        assert st.read_control() == expected


def test_sqlite_update_metrics_amend_last_parity(store):
    # update_metrics(metrics) amends the last record in place rather than
    # appending, matching InMemoryStore's update behavior.
    store.flush_metrics()
    store.append_metric()
    metrics = store.read_metrics()
    metrics["mode"] = "Hold"
    store.update_metrics(metrics)
    assert store.read_metrics()["mode"] == "Hold"
    assert len(store.read_all_metrics()) == 1  # replaced, not appended


def test_update_metrics_partial_dict_parity(store):
    # Presence, not truthiness, decides which columns move -- and BOTH backends
    # must agree, because the controller writes metrics through whichever Store
    # it was handed. InMemoryStore used to REPLACE the last record wholesale, so
    # a partial dict left the fake holding a one-key row while SqliteStore kept
    # every unmentioned column's prior value; a test could pass against the fake
    # and still be wrong about production. Pins the seam in both directions:
    # unmentioned columns survive, an explicit None still nulls.
    from controller.runtime.store import InMemoryStore

    mem = InMemoryStore()
    for st in (store, mem):
        st.flush_metrics()
        st.append_metric()
        st.update_metrics({"mode": "Startup", "primary_setpoint": 225, "pellet_brand_type": "Generic-Alder"})

        st.update_metrics({"mode": "Hold"})
        row = st.read_metrics()
        assert row["mode"] == "Hold"  # provided key applied
        assert row["primary_setpoint"] == 225  # unmentioned column survives
        assert row["pellet_brand_type"] == "Generic-Alder"

        st.update_metrics({"pellet_brand_type": None})  # explicit null still nulls
        assert st.read_metrics()["pellet_brand_type"] is None
        assert st.read_metrics()["mode"] == "Hold"  # still untouched
        assert len(st.read_all_metrics()) == 1  # amended, never appended
