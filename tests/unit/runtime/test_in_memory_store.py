from common.control_delta import control_delta
from controller.runtime.store import InMemoryStore


def test_snapshot_replaces_whole_control_immediately():
    store = InMemoryStore(control={"mode": "Stop", "a": 1})

    store.write_control_snapshot({"mode": "Hold"}, origin="control")

    assert store.read_control() == {"mode": "Hold"}


def test_delta_is_deferred_until_execute():
    store = InMemoryStore(control={"mode": "Stop", "nested": {"x": 1, "y": 2}})

    store.enqueue_control_delta(control_delta(set_values={"nested": {"x": 9}}), origin="display")

    assert store.read_control()["nested"] == {"x": 1, "y": 2}
    store.execute_control_writes()
    assert store.read_control()["nested"] == {"x": 9, "y": 2}
    assert store.read_control()["mode"] == "Stop"


def test_deltas_apply_in_fifo_order():
    store = InMemoryStore(control={"v": 0})
    store.enqueue_control_delta(control_delta(set_values={"v": 1}), origin="first")
    store.enqueue_control_delta(control_delta(set_values={"v": 2}), origin="second")

    store.execute_control_writes()

    assert store.read_control()["v"] == 2


def test_display_queue_drain_is_fifo_and_empties():
    s = InMemoryStore()
    s.display_commands().push(("text", "ERROR"))
    s.display_commands().push(("clear", None))
    assert s.display_commands().drain() == [("text", "ERROR"), ("clear", None)]
    assert s.display_commands().drain() == []


def test_read_control_returns_a_copy():
    s = InMemoryStore(control={"mode": "Stop"})
    c = s.read_control()
    c["mode"] = "Hold"
    assert s.read_control()["mode"] == "Stop"


def test_write_history_accepts_maxsizelines():
    s = InMemoryStore()
    s.write_history({"x": 1}, maxsizelines=100, ext_data=True)
    assert s.read_history() == [{"x": 1}]


def test_flush_metrics_empties_the_list():
    s = InMemoryStore(metrics={"a": 1})
    s.flush_metrics()
    assert s.read_all_metrics() == []


def test_in_memory_history_cap():
    from controller.runtime.store import InMemoryStore

    s = InMemoryStore()
    sample = {
        "probe_history": {"primary": {"G": 1}, "food": {}, "aux": {}},
        "primary_setpoint": 1,
        "notify_targets": {},
    }
    for _ in range(5):
        s.write_history(sample, maxsizelines=3)
    assert len(s.read_history()) == 3  # was unbounded; must now cap


def test_status_initialization_persists_a_detached_snapshot():
    settings = {
        "globals": {"units": "C"},
        "modules": {"dist": "ultrasonic"},
    }
    pellets = {"current": {"hopper_level": 42}}
    store = InMemoryStore(settings=settings, pellet_db=pellets)

    initialized = store.init_status()
    initialized["outpins"]["fan"] = True

    assert store.read_status()["units"] == "C"
    assert store.read_status()["hopper_level_enabled"] is True
    detached = store.read_status()
    detached["outpins"]["auger"] = True
    assert store.read_status()["outpins"]["auger"] is False
    assert store.read_status()["hopper_level"] == 42
    assert store.read_status()["outpins"]["fan"] is False


def test_generic_values_are_detached_at_write_and_read_boundaries():
    store = InMemoryStore()
    payload = {"version": 1, "models": {"grey": {"revision": 3}}}

    store.write_generic_key("controller_model_state", payload)
    payload["models"]["grey"]["revision"] = 4
    returned = store.read_generic_key("controller_model_state")
    returned["models"]["grey"]["revision"] = 5

    assert store.read_generic_key("controller_model_state") == {
        "version": 1,
        "models": {"grey": {"revision": 3}},
    }


def test_current_write_blob_and_snapshot_boundaries_are_detached():
    settings = {
        "probe_settings": {
            "probe_map": {
                "probe_info": [
                    {"label": "PitProbe", "name": "Pit", "type": "Primary", "enabled": True},
                ]
            }
        }
    }
    current_input = {
        "probe_history": {"primary": {"PitProbe": 210}, "food": {}, "aux": {}},
        "primary_setpoint": 225,
        "notify_targets": {"PitProbe": 0},
    }
    store = InMemoryStore(settings=settings)
    store.write_current(current_input)
    committed = store.read_current()

    current_input["probe_history"]["primary"]["PitProbe"] = 999
    detached_blob = store.read_current()
    detached_blob["P"]["PitProbe"] = 888
    detached_snapshot = store.read_current_snapshot()
    detached_snapshot.primary["PitProbe"] = 777

    assert store.read_current() == committed
    assert store.read_current_snapshot().primary["PitProbe"] == 210
