import copy
import json

import pytest


@pytest.fixture
def db(tmp_path):
    from common import datastore

    datastore._reset_for_tests(str(tmp_path / "t.db"))
    datastore.init()
    yield datastore
    datastore._reset_for_tests(None)


PROBE_INFO = [
    {"label": "PitProbe", "name": "Pit", "type": "Primary", "enabled": True},
    {"label": "PinkProbe", "name": "Pink", "type": "Food", "enabled": True},
]

IN_DATA = {
    "probe_history": {
        "primary": {"PitProbe": 210},
        "food": {"PinkProbe": 140},
        "aux": {},
    },
    "primary_setpoint": 225,
    "notify_targets": {"PitProbe": 0, "PinkProbe": 165},
}


def _seed_probe_map(db):
    from common import datastore_accessors as dsa

    settings = dsa.read_settings()
    settings["probe_settings"]["probe_map"]["probe_info"] = PROBE_INFO
    dsa.write_settings(settings)


def test_flush_current_writes_the_same_key_set_as_before(db):
    # The characterization golden seeds every case through flush_current(), so
    # a key appearing here appears in the public get_current response.
    from common import datastore_accessors as dsa

    _seed_probe_map(db)
    dsa.flush_current()
    stored = json.loads(db.get_blob("control:current"))
    assert set(stored) == {"P", "F", "AUX", "PSP", "NT", "LAST"}
    assert stored["P"] == {"PitProbe": 0}
    assert stored["F"] == {"PinkProbe": 0}
    assert stored["NT"] == {"PitProbe": 0, "PinkProbe": 0}
    assert stored["LAST"] == {}


def test_write_current_stores_the_letter_spelled_blob(db):
    from common import datastore_accessors as dsa

    _seed_probe_map(db)
    dsa.write_current(IN_DATA)
    stored = json.loads(db.get_blob("control:current"))
    assert set(stored) == {"P", "F", "AUX", "PSP", "NT", "TS", "LAST"}
    assert stored["P"] == {"PitProbe": 210}
    assert stored["PSP"] == 225
    assert stored["TS"] > 0
    assert stored["LAST"]["PinkProbe"]["temp"] == 140


def test_read_current_still_returns_the_raw_letter_dict(db):
    from common import datastore_accessors as dsa

    _seed_probe_map(db)
    dsa.write_current(IN_DATA)
    current = dsa.read_current()
    assert isinstance(current, dict)
    assert current["P"] == {"PitProbe": 210}


def test_read_current_snapshot_returns_canonical_names(db):
    from common import datastore_accessors as dsa

    _seed_probe_map(db)
    dsa.write_current(IN_DATA)
    snap = dsa.read_current_snapshot()
    assert snap.primary == {"PitProbe": 210}
    assert snap.food == {"PinkProbe": 140}
    assert snap.primary_setpoint == 225
    assert snap.last_readings["PinkProbe"].temp == 140


def test_read_current_snapshot_survives_a_corrupt_blob(db):
    # The blob is a cache of the last control pass. A display or the control
    # loop taking an exception from it is strictly worse than one tick of
    # zeroes, and the next pass refills it.
    from common import datastore_accessors as dsa

    _seed_probe_map(db)
    db.set_blob("control:current", json.dumps({"SURPRISE": 1}))
    snap = dsa.read_current_snapshot()
    assert snap.primary == {"PitProbe": 0}
    assert snap.food == {"PinkProbe": 0}
    assert snap.last_readings == {}


def test_write_current_carries_a_stale_probe_across_passes(db):
    from common import datastore_accessors as dsa

    _seed_probe_map(db)
    dsa.write_current(IN_DATA)
    first = json.loads(db.get_blob("control:current"))["LAST"]["PinkProbe"]
    gone = {
        "probe_history": {"primary": {"PitProbe": 212}, "food": {"PinkProbe": None}, "aux": {}},
        "primary_setpoint": 225,
        "notify_targets": {"PitProbe": 0, "PinkProbe": 165},
    }
    dsa.write_current(gone)
    stored = json.loads(db.get_blob("control:current"))
    assert stored["F"]["PinkProbe"] is None
    assert stored["LAST"]["PinkProbe"] == first


def test_current_write_and_read_snapshots_do_not_alias_inputs_or_each_other(db):
    from common import datastore_accessors as dsa

    _seed_probe_map(db)
    current_input = copy.deepcopy(IN_DATA)
    dsa.write_current(current_input)
    committed = dsa.read_current()

    current_input["probe_history"]["primary"]["PitProbe"] = 999
    current_input["notify_targets"]["PinkProbe"] = 999
    detached_blob = dsa.read_current()
    detached_blob["P"]["PitProbe"] = 888
    detached_snapshot = dsa.read_current_snapshot()
    detached_snapshot.primary["PitProbe"] = 777

    assert dsa.read_current() == committed
    assert dsa.read_current_snapshot().primary["PitProbe"] == 210


def test_get_temp_reports_a_stale_probe_as_none(db):
    # A probe with no reading must reach the API as null, not as 0, and an
    # unknown label must be an error rather than a null reading.
    from common import api_commands, datastore_accessors as dsa

    _seed_probe_map(db)
    dsa.write_current(
        {
            "probe_history": {"primary": {"PitProbe": 210}, "food": {"PinkProbe": None}, "aux": {}},
            "primary_setpoint": 225,
            "notify_targets": {"PitProbe": 0, "PinkProbe": 165},
        }
    )
    data = {"data": {}, "result": "OK"}
    api_commands._cmd_get_temp(data, None, None, ["temp", "PinkProbe"], None)
    assert data["result"] == "OK"
    assert data["data"]["temp"] is None

    data = {"data": {}, "result": "OK"}
    api_commands._cmd_get_temp(data, None, None, ["temp", "PitProbe"], None)
    assert data["data"]["temp"] == 210

    data = {"data": {}, "result": "OK"}
    api_commands._cmd_get_temp(data, None, None, ["temp", "NoSuchProbe"], None)
    assert data["result"] == "ERROR"
