import copy

import pytest
from common.persistence import runtime as runtime_persistence


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


def test_generic_key_roundtrip_parity(store):
    """ControllerModelStore is constructed with a Store's read/write_generic_key
    as reader/writer, not the module-level SQLite functions, so the two paths
    must round-trip identically.
    """
    from controller.runtime.store import InMemoryStore

    payload = {"version": 1, "models": {"pid_sp": {"revision": 3, "K": 700.0}}}
    for st in (store, InMemoryStore()):
        st.write_generic_key("controller_model_state", payload)
        assert st.read_generic_key("controller_model_state") == payload


def test_generic_key_absent_key_raises_type_error_on_both(store):
    """ControllerModelStore._read_state() catches TypeError specifically to mean
    "nothing written yet, safe to write" -- both stores must raise that, not
    KeyError or None, for a key that was never written.
    """
    from controller.runtime.store import InMemoryStore

    for st in (store, InMemoryStore()):
        with pytest.raises(TypeError):
            st.read_generic_key("never_written_key")


def test_sqlite_append_metric_without_metrics_does_not_crash(store):
    # Regression: append_metric() with no metrics must defer to
    # common's default_metrics() (passing None crashed on metrics['starttime']).
    # The control loop calls this at the start of every work cycle.
    store.flush_metrics()  # reset metrics list
    store.append_metric()  # must NOT raise
    current = store.read_metrics()
    assert isinstance(current, dict)
    assert "starttime" in current  # populated from default_metrics() + starttime


def test_control_delta_fifo_and_snapshot_bypass_parity(store):
    from common.control_delta import control_delta
    from controller.runtime.store import InMemoryStore

    for st in (store, InMemoryStore()):
        st.write_control_snapshot({"mode": "Stop", "primary_setpoint": 100}, origin="seed")
        st.enqueue_control_delta(control_delta(set_values={"primary_setpoint": 225}), origin="first")
        st.enqueue_control_delta(control_delta(set_values={"primary_setpoint": 275}), origin="second")

        assert st.read_control() == {"mode": "Stop", "primary_setpoint": 100}

        st.write_control_snapshot({"mode": "Startup", "primary_setpoint": 150}, origin="control")
        assert st.read_control() == {"mode": "Startup", "primary_setpoint": 150}

        st.execute_control_writes()
        assert st.read_control() == {"mode": "Startup", "primary_setpoint": 275}


def test_control_writers_copy_their_callers_inputs_on_both_stores(store):
    from common.control_delta import control_delta
    from controller.runtime.store import InMemoryStore

    for st in (store, InMemoryStore()):
        snapshot = {"mode": "Hold", "manual": {"pwm": 50}}
        st.write_control_snapshot(snapshot, origin="control")
        snapshot["manual"]["pwm"] = 99

        delta = control_delta(set_values={"manual": {"pwm": 60}})
        st.enqueue_control_delta(delta, origin="display")
        delta_set = delta["set"]
        assert isinstance(delta_set, dict)
        manual = delta_set["manual"]
        assert isinstance(manual, dict)
        manual["pwm"] = 100

        assert st.read_control()["manual"]["pwm"] == 50
        st.execute_control_writes()
        assert st.read_control()["manual"]["pwm"] == 60


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ({"__control_delta__": 1, "set": []}, "set must be a mapping, got list"),
        ({"mode": "Startup"}, "unversioned legacy control write"),
    ],
)
def test_invalid_queued_control_rows_are_rejected_and_dequeued_with_store_parity(store, caplog, payload, reason):
    from common.sqlite_queue import SqliteQueue
    from controller.runtime.store import InMemoryStore

    stores = (store, InMemoryStore())
    for st in stores:
        st.write_control_snapshot({"mode": "Stop", "primary_setpoint": 100}, origin="seed")
        queued = copy.deepcopy(payload)
        queued["origin"] = "persisted-writer"
        if st is store:
            SqliteQueue("queue_control_write").push(queued)
        else:
            st._write_queue.append(queued)

        with caplog.at_level("ERROR", logger="control"):
            st.execute_control_writes()

        assert st.read_control() == {"mode": "Stop", "primary_setpoint": 100}
        if st is store:
            assert SqliteQueue("queue_control_write").length() == 0
        else:
            assert not st._write_queue

    matching = [record.getMessage() for record in caplog.records if reason in record.getMessage()]
    assert len(matching) == len(stores)
    assert all("origin='persisted-writer'" in message for message in matching)


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
    # and still be wrong about production. Pins the rule in both directions:
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
        st.write_control_snapshot(copy.deepcopy(base), origin="seed")
        st.enqueue_control_delta(envelope, origin="parity")
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


def test_write_current_shape_parity(store, monkeypatch):
    # The control loop hands write_current() probe_history-shaped data, and what
    # gets STORED is the transformed blob. A fake that kept the input verbatim
    # would let a test write and then read a shape production never produces.
    from controller.runtime.store import InMemoryStore

    settings = _settings_with_probe_map(store)
    runtime_persistence.write_settings(settings)
    fake = InMemoryStore(settings=settings)
    # Both writers call the shared stdlib time module independently. Freeze it
    # so this parity assertion tests their transformation rather than whether
    # both calls happened within the same millisecond.
    monkeypatch.setattr(runtime_persistence.time, "time", lambda: 1_700_000_000.123)

    store.write_current(_PARITY_IN_DATA)
    fake.write_current(_PARITY_IN_DATA)

    real_current = store.read_current()
    fake_current = fake.read_current()
    assert set(real_current) == set(fake_current)
    for key in ("P", "F", "AUX", "PSP", "NT"):
        assert real_current[key] == fake_current[key], key
    assert real_current["LAST"] == fake_current["LAST"]


def test_flush_current_shape_parity(store):
    from controller.runtime.store import InMemoryStore

    settings = _settings_with_probe_map(store)
    runtime_persistence.write_settings(settings)
    fake = InMemoryStore(settings=settings)

    assert store.flush_current() == fake.flush_current()


def test_read_current_snapshot_parity(store):
    from controller.runtime.store import InMemoryStore

    settings = _settings_with_probe_map(store)
    runtime_persistence.write_settings(settings)
    fake = InMemoryStore(settings=settings)

    store.write_current(_PARITY_IN_DATA)
    fake.write_current(_PARITY_IN_DATA)

    real = store.read_current_snapshot()
    fake_snap = fake.read_current_snapshot()
    assert real.primary == fake_snap.primary == {"PitProbe": 210}
    assert real.food == fake_snap.food == {"PinkProbe": 140}
    assert real.primary_setpoint == fake_snap.primary_setpoint == 225
    assert real.last_readings.keys() == fake_snap.last_readings.keys()


def test_status_initialization_and_snapshot_ownership_parity(store):
    from controller.runtime.store import InMemoryStore

    settings = store.read_settings()
    settings["globals"]["units"] = "C"
    settings["modules"]["dist"] = "ultrasonic"
    runtime_persistence.write_settings(settings)
    pellet_db = store.read_pellet_db()
    pellet_db["current"]["hopper_level"] = 37
    store.write_pellet_db(pellet_db)

    statuses = []
    for st in (store, InMemoryStore(settings=settings, pellet_db=pellet_db)):
        status = st.init_status()
        statuses.append(copy.deepcopy(status))
        assert status["units"] == "C"
        assert status["hopper_level_enabled"] is True
        assert status["hopper_level"] == 37
        assert st.read_status() == status

        status["outpins"]["fan"] = True
        assert st.read_status()["outpins"]["fan"] is False

    assert statuses[0] == statuses[1]


def test_history_write_and_flush_coupling_parity(store):
    from controller.runtime.store import InMemoryStore

    settings = _settings_with_probe_map(store)
    runtime_persistence.write_settings(settings)
    history_projections = []

    for st in (store, InMemoryStore(settings=settings)):
        current_input = copy.deepcopy(_PARITY_IN_DATA)
        history_input = {
            **copy.deepcopy(_PARITY_IN_DATA),
            "ext_data": {"mode": "Hold", "cycle": 3},
        }
        st.write_current(current_input)
        st.append_metric()
        st.write_history(history_input, maxsizelines=10, ext_data=True)

        persisted = st.read_history()
        assert len(persisted) == 1
        row = persisted[0]
        if "probe_history" in row:
            history_projections.append(
                {
                    "P": row["probe_history"]["primary"],
                    "F": row["probe_history"]["food"],
                    "AUX": row["probe_history"]["aux"],
                    "PSP": row["primary_setpoint"],
                    "NT": row["notify_targets"],
                    "EXD": row["ext_data"],
                }
            )
        else:
            history_projections.append(
                {key: row[key] for key in ("P", "F", "AUX", "PSP", "NT", "EXD")}
            )
        history_input["probe_history"]["primary"]["PitProbe"] = 999
        history_input["ext_data"]["cycle"] = 99
        assert st.read_history() == persisted

        st.flush_history()
        assert st.read_history() == []
        assert st.read_all_metrics() == []
        assert st.read_current()["P"] == {"PitProbe": 0}
        assert st.read_current()["F"] == {"PinkProbe": 0}
        assert st.read_current()["LAST"] == {}

    assert history_projections[0] == history_projections[1]


def test_error_owner_read_flush_and_all_order_parity(store):
    from common.common import ErrorKind
    from controller.runtime.store import InMemoryStore

    for st in (store, InMemoryStore()):
        st.write_errors(ErrorKind.CONTROL, ["control-a", "control-b"])
        st.write_errors(ErrorKind.DISPLAY, ["display-a"])

        assert st.read_errors(ErrorKind.CONTROL) == ["control-a", "control-b"]
        assert st.read_errors(ErrorKind.ALL) == ["control-a", "control-b", "display-a"]
        assert st.flush_errors(ErrorKind.CONTROL) == []
        assert st.read_errors(ErrorKind.ALL) == ["display-a"]

        with pytest.raises(ValueError, match="read-only selector"):
            st.write_errors(ErrorKind.ALL, ["not-owned"])
