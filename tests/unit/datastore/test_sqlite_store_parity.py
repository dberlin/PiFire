import copy

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


def test_notify_data_cross_writer_delta_parity(store):
    # SqliteStore and InMemoryStore must agree that two writers in ONE cycle,
    # each addressing a DIFFERENT notify_data entry with a notify.set op, both
    # survive. This is the cross-process seam: the web process queues, the
    # control process drains, and the two stores are swappable only if they
    # resolve identically.
    from common.common import WriteKind
    from common.control_delta import control_delta
    from controller.runtime.store import InMemoryStore

    base = [
        {"label": "Grill", "type": "probe", "req": False, "target": 0},
        {"label": "Grill", "type": "probe_limit_high", "req": False, "target": 0},
        {"label": "Timer", "type": "timer", "req": False, "shutdown": False},
    ]
    expected = [
        {"label": "Grill", "type": "probe", "req": True, "target": 203},
        {"label": "Grill", "type": "probe_limit_high", "req": False, "target": 0},
        {"label": "Timer", "type": "timer", "req": True, "shutdown": True},
    ]

    arm_probe = control_delta(
        ops=[{"op": "notify.set", "label": "Grill", "type": "probe", "fields": {"req": True, "target": 203}}]
    )
    arm_timer = control_delta(
        ops=[{"op": "notify.set", "label": "Timer", "type": "timer", "fields": {"req": True, "shutdown": True}}]
    )

    for st in (store, InMemoryStore()):
        st.write_control({"mode": "Stop", "notify_data": [dict(e) for e in base]}, WriteKind.OVERWRITE)
        st.write_control(arm_probe, WriteKind.DELTA, origin="app")
        st.write_control(arm_timer, WriteKind.DELTA, origin="app-socketio")
        st.execute_control_writes()
        assert st.read_control()["notify_data"] == expected


def test_whole_dict_cross_writer_delta_parity(store):
    # The same seam for every member of the control dict rather than just the
    # notify_data array. Each writer names ONLY what it changed, so nothing it
    # never touched can be imposed -- which is what makes the order below
    # irrelevant and the two backends agree.
    from common.common import WriteKind
    from common.control_delta import control_delta
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
    expected = {
        "mode": "Hold",
        "updated": True,
        "s_plus": True,
        "primary_setpoint": 225,
        "settings_update": True,
        "manual": {"change": "fan", "output": True, "pwm": 100},
        "timer": {"start": 500.0, "paused": 0, "end": 1100.0},
    }

    writes = [
        control_delta(set_values={"mode": "Hold", "primary_setpoint": 225, "updated": True}),
        control_delta(set_values={"s_plus": True}),
        control_delta(set_values={"settings_update": True}),
        control_delta(set_values={"manual": {"change": "fan", "output": True}}),
        # `timer` may not travel under `set` -- it is a coupled value object, so
        # it is expressed as an op the drain evaluates against live state.
        control_delta(ops=[{"op": "timer.start_or_resume", "at": 500.0, "seconds": 600}]),
    ]

    for st in (store, InMemoryStore()):
        st.write_control(copy.deepcopy(base), WriteKind.OVERWRITE)
        for envelope in writes:
            st.write_control(envelope, WriteKind.DELTA, origin="parity")
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


def test_delta_envelope_parity_between_sqlite_and_in_memory(store):
    """The web process queues, the control process drains. Pin both ends.

    SqliteStore applies a delta in Python and rewrites the blob; InMemoryStore
    applies the same envelope to its dict. A drift between them is a
    cross-process bug that would only show on real hardware.
    """
    from common.common import WriteKind
    from common.control_delta import control_delta
    from controller.runtime.store import InMemoryStore

    base = {
        "mode": "Stop",
        "primary_setpoint": 0,
        "timer": {"start": 1000.0, "paused": 0, "end": 2000.0},
        "notify_data": [
            {"label": "Grill", "type": "probe", "req": False, "target": 0},
            {"label": "Timer", "type": "timer", "req": True, "shutdown": True, "keep_warm": False},
        ],
    }
    envelope = control_delta(
        set_values={"mode": "Hold", "primary_setpoint": 225},
        ops=[
            {"op": "timer.clear"},
            {"op": "notify.set", "label": "Grill", "type": "probe", "fields": {"target": 203}},
        ],
    )

    results = []
    for st in (store, InMemoryStore()):
        st.write_control(copy.deepcopy(base), WriteKind.OVERWRITE)
        st.write_control(envelope, WriteKind.DELTA, origin="parity")
        st.execute_control_writes()
        results.append(st.read_control())

    assert results[0] == results[1]
    assert results[0]["mode"] == "Hold"
    assert results[0]["timer"] == {"start": 0, "paused": 0, "end": 0}
    assert results[0]["notify_data"][0]["target"] == 203
    assert "origin" not in results[0]


_PARITY_PROBE_INFO = [
    {"label": "PitProbe", "name": "Pit", "type": "Primary", "enabled": True},
    {"label": "PinkProbe", "name": "Pink", "type": "Food", "enabled": True},
]

_PARITY_IN_DATA = {
    "probe_history": {
        "primary": {"PitProbe": 210},
        "food": {"PinkProbe": 140},
        "aux": {},
    },
    "primary_setpoint": 225,
    "notify_targets": {"PitProbe": 0, "PinkProbe": 165},
}


def _settings_with_probe_map(store):
    settings = store.read_settings()
    settings["probe_settings"]["probe_map"]["probe_info"] = _PARITY_PROBE_INFO
    return settings


def test_write_current_shape_parity(store):
    # The control loop hands write_current() probe_history-shaped data, and what
    # gets STORED is the transformed blob. A fake that kept the input verbatim
    # would let a test write and then read a shape production never produces.
    from common import datastore_accessors as dsa
    from controller.runtime.store import InMemoryStore

    settings = _settings_with_probe_map(store)
    dsa.write_settings(settings)
    fake = InMemoryStore(settings=settings)

    store.write_current(_PARITY_IN_DATA)
    fake.write_current(_PARITY_IN_DATA)

    real_current = store.read_current()
    fake_current = fake.read_current()
    assert set(real_current) == set(fake_current)
    for key in ("P", "F", "AUX", "PSP", "NT"):
        assert real_current[key] == fake_current[key], key
    assert real_current["LAST"] == fake_current["LAST"]


def test_flush_current_shape_parity(store):
    from common import datastore_accessors as dsa
    from controller.runtime.store import InMemoryStore

    settings = _settings_with_probe_map(store)
    dsa.write_settings(settings)
    fake = InMemoryStore(settings=settings)

    assert store.flush_current() == fake.flush_current()


def test_read_current_snapshot_parity(store):
    from common import datastore_accessors as dsa
    from controller.runtime.store import InMemoryStore

    settings = _settings_with_probe_map(store)
    dsa.write_settings(settings)
    fake = InMemoryStore(settings=settings)

    store.write_current(_PARITY_IN_DATA)
    fake.write_current(_PARITY_IN_DATA)

    real = store.read_current_snapshot()
    fake_snap = fake.read_current_snapshot()
    assert real.primary == fake_snap.primary == {"PitProbe": 210}
    assert real.food == fake_snap.food == {"PinkProbe": 140}
    assert real.primary_setpoint == fake_snap.primary_setpoint == 225
    assert real.last_readings.keys() == fake_snap.last_readings.keys()
