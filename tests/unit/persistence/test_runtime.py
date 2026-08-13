import copy
import json
import sqlite3

import pytest

from common import datastore
from common.common import ErrorKind
from common.defaults import default_pellets, default_settings
from common.pellets_schema import PelletDbValidationError
from common.persistence import runtime
from common.settings_schema import SettingsValidationError


PROBE_INFO = [
    {"label": "PitProbe", "name": "Pit", "type": "Primary", "enabled": True},
    {"label": "PinkProbe", "name": "Pink", "type": "Food", "enabled": True},
]

CURRENT_INPUT = {
    "probe_history": {
        "primary": {"PitProbe": 210},
        "food": {"PinkProbe": 140},
        "aux": {},
    },
    "primary_setpoint": 225,
    "notify_targets": {"PitProbe": 0, "PinkProbe": 165},
}

def _assert_default_settings(actual):
    expected = default_settings()
    expected["lastupdated"] = actual["lastupdated"]
    expected["server_info"]["uuid"] = actual["server_info"]["uuid"]
    expected["notify_services"]["onesignal"]["uuid"] = actual["notify_services"]["onesignal"]["uuid"]
    assert actual == expected


def _assert_default_pellets(actual):
    expected = default_pellets()
    pellet_id = actual["current"]["pelletid"]
    expected["current"]["pelletid"] = pellet_id
    expected["current"]["date_loaded"] = actual["current"]["date_loaded"]
    expected["archive"] = {pellet_id: next(iter(expected["archive"].values()))}
    log_timestamp = next(iter(actual["log"]))
    expected["log"] = {log_timestamp: {"pelletid": pellet_id, "deleted": False}}
    expected["lastupdated"] = actual["lastupdated"]
    assert actual == expected



@pytest.fixture
def configured_runtime(ds):
    settings = runtime.read_settings()
    settings["probe_settings"]["probe_map"]["probe_info"] = copy.deepcopy(PROBE_INFO)
    runtime.write_settings(settings)
    return ds


def test_absent_runtime_blobs_use_detached_default_factories(ds):
    datastore.delete_blob("settings:general")
    datastore.delete_blob("pellets:general")
    first_settings = runtime.read_settings()
    first_pellets = runtime.read_pellet_db()

    _assert_default_settings(first_settings)
    _assert_default_pellets(first_pellets)
    assert runtime.read_status() == {}
    assert runtime.read_current() == {}
    assert datastore.exists_blob("settings:general") is False
    assert datastore.exists_blob("pellets:general") is False

    first_settings["globals"]["grill_name"] = "mutated"
    first_pellets["current"]["hopper_level"] = 0
    _assert_default_settings(runtime.read_settings())
    _assert_default_pellets(runtime.read_pellet_db())


def test_seed_helpers_materialize_defaults_without_aliasing_returns(ds):
    settings = runtime.seed_settings_store()
    pellets = runtime.seed_pellets_store()

    settings_blob = datastore.get_blob("settings:general")
    pellets_blob = datastore.get_blob("pellets:general")
    assert settings_blob is not None
    assert pellets_blob is not None
    stored_settings = json.loads(settings_blob)
    stored_pellets = json.loads(pellets_blob)
    assert stored_settings == settings
    assert stored_pellets == pellets

    settings["globals"]["grill_name"] = "caller mutation"
    pellets["current"]["hopper_level"] = 0
    assert runtime.read_settings_store() == stored_settings
    assert runtime.read_pellets_store() == stored_pellets


def test_settings_write_validates_stamps_and_copies(monkeypatch, ds):
    monkeypatch.setattr(runtime.time, "time", lambda: 1234.9)
    settings = runtime.read_settings()
    settings["globals"]["grill_name"] = "Runtime"

    runtime.write_settings(settings)
    settings["globals"]["grill_name"] = "mutated after write"
    detached = runtime.read_settings()
    detached["globals"]["grill_name"] = "mutated after read"

    stored = runtime.read_settings_store()
    assert stored["globals"]["grill_name"] == "Runtime"
    assert stored["lastupdated"]["time"] == 1234


def test_invalid_settings_write_leaves_existing_blob_untouched(ds):
    runtime.write_settings_store(default_settings())
    before = runtime.read_settings()
    invalid = copy.deepcopy(before)
    invalid["safety"]["maxtemp"] = "not-a-temperature"

    with pytest.raises(SettingsValidationError):
        runtime.write_settings(invalid)

    assert runtime.read_settings() == before


def test_pellet_write_validates_and_copies(ds):
    pellets = runtime.read_pellet_db()
    pellets["current"]["hopper_level"] = 87

    runtime.write_pellet_db(pellets)
    pellets["current"]["hopper_level"] = 0
    detached = runtime.read_pellet_db()
    detached["current"]["hopper_level"] = 1

    assert runtime.read_pellets_store()["current"]["hopper_level"] == 87


def test_invalid_pellet_write_leaves_existing_blob_untouched(ds):
    runtime.write_pellets_store(default_pellets())
    before = runtime.read_pellet_db()
    invalid = copy.deepcopy(before)
    invalid["current"]["hopper_level"] = "not-a-level"

    with pytest.raises(PelletDbValidationError):
        runtime.write_pellet_db(invalid)

    assert runtime.read_pellet_db() == before


def test_current_round_trip_uses_durable_wire_shape_and_detached_copies(configured_runtime):
    current_input = copy.deepcopy(CURRENT_INPUT)
    runtime.write_current(current_input)
    committed = runtime.read_current()

    assert set(committed) == {"P", "F", "AUX", "PSP", "NT", "TS", "LAST"}
    assert committed["P"] == {"PitProbe": 210}
    assert committed["LAST"]["PinkProbe"]["temp"] == 140

    current_input["probe_history"]["primary"]["PitProbe"] = 999
    detached = runtime.read_current()
    detached["P"]["PitProbe"] = 888
    snapshot = runtime.read_current_snapshot()
    snapshot.primary["PitProbe"] = 777

    assert runtime.read_current() == committed
    assert runtime.read_current_snapshot().primary["PitProbe"] == 210


def test_current_snapshot_recovers_corrupt_cache_from_probe_map(configured_runtime):
    datastore.set_blob("control:current", json.dumps({"SURPRISE": 1}))

    snapshot = runtime.read_current_snapshot()

    assert snapshot.primary == {"PitProbe": 0}
    assert snapshot.food == {"PinkProbe": 0}
    assert snapshot.last_readings == {}


def test_flush_current_rebuilds_zeroed_wire_shape(configured_runtime):
    runtime.write_current(CURRENT_INPUT)

    flushed = runtime.flush_current()

    assert flushed == {
        "P": {"PitProbe": 0},
        "F": {"PinkProbe": 0},
        "AUX": {},
        "PSP": 0,
        "NT": {"PitProbe": 0, "PinkProbe": 0},
        "LAST": {},
    }
    assert "TS" not in flushed


def test_status_defaults_initialization_and_copy_ownership(ds):
    settings = runtime.read_settings()
    settings["modules"]["dist"] = "ultrasonic"
    settings["globals"]["units"] = "C"
    runtime.write_settings(settings)
    pellets = runtime.read_pellet_db()
    pellets["current"]["hopper_level"] = 72
    runtime.write_pellet_db(pellets)

    status = runtime.init_status()

    assert status["mode"] == "Stop"
    assert status["hopper_level_enabled"] is True
    assert status["hopper_level"] == 72
    assert status["units"] == "C"
    status["outpins"]["fan"] = True
    assert runtime.read_status()["outpins"]["fan"] is False

    replacement = {"mode": "Hold", "nested": {"value": 1}}
    runtime.write_status(replacement)
    replacement["nested"]["value"] = 2
    detached = runtime.read_status()
    detached["nested"]["value"] = 3
    assert runtime.read_status() == {"mode": "Hold", "nested": {"value": 1}}


@pytest.mark.parametrize("kind", list(ErrorKind))
def test_errors_default_to_empty_for_every_read_selector(ds, kind):
    assert runtime.read_errors(kind) == []


def test_error_kinds_are_isolated_and_all_reads_declaration_order(ds):
    runtime.write_errors(ErrorKind.CONTROL, ["control"])
    runtime.write_errors(ErrorKind.DISPLAY, ["display"])
    runtime.write_errors(ErrorKind.WEB, ["web"])
    runtime.write_errors(ErrorKind.CONTROL, ["control replaced"])

    assert runtime.read_errors(ErrorKind.ALL) == ["control replaced", "display", "web"]
    assert runtime.flush_errors(ErrorKind.DISPLAY) == []
    assert runtime.read_errors(ErrorKind.DISPLAY) == []
    assert runtime.read_errors(ErrorKind.ALL) == ["control replaced", "web"]


@pytest.mark.parametrize("bad_kind", [ErrorKind.ALL, "control", None])
def test_error_writes_and_flushes_reject_non_owner_selectors(ds, bad_kind):
    with pytest.raises(ValueError):
        runtime.write_errors(bad_kind, ["not written"])
    with pytest.raises(ValueError):
        runtime.flush_errors(bad_kind)


@pytest.mark.parametrize("bad_kind", ["control", None])
def test_error_reads_reject_non_enum_kinds(ds, bad_kind):
    with pytest.raises(ValueError):
        runtime.read_errors(bad_kind)


def test_error_replacement_rolls_back_when_an_insert_fails(ds):
    runtime.write_errors(ErrorKind.CONTROL, ["old first", "old second"])
    ds.connection().execute(
        """
        CREATE TEMP TRIGGER fail_error_insert
        BEFORE INSERT ON errors
        WHEN NEW.message = 'explode'
        BEGIN
            SELECT RAISE(ABORT, 'simulated error insert failure');
        END
        """
    )

    try:
        with pytest.raises(sqlite3.IntegrityError, match="simulated error insert failure"):
            runtime.write_errors(ErrorKind.CONTROL, ["new first", "explode"])
        assert runtime.read_errors(ErrorKind.CONTROL) == ["old first", "old second"]
    finally:
        ds.connection().execute("DROP TRIGGER fail_error_insert")


def test_warning_snapshot_is_non_destructive_and_empty_has_no_high_water(ds):
    assert runtime.read_warnings_snapshot() == {"warnings": [], "max_id": None}
    runtime.write_warning("first")
    runtime.write_warning("second")

    snapshot = runtime.read_warnings_snapshot()

    assert snapshot["warnings"] == ["first", "second"]
    assert runtime.read_warnings_snapshot() == snapshot


def test_warning_written_after_snapshot_survives_bounded_clear(ds):
    runtime.write_warning("seen first")
    runtime.write_warning("seen second")
    snapshot = runtime.read_warnings_snapshot()
    runtime.write_warning("written after snapshot")

    runtime.clear_warnings_through(snapshot["max_id"])

    assert runtime.read_warnings_snapshot()["warnings"] == ["written after snapshot"]


def test_warning_clear_failure_preserves_snapshot_and_later_write(ds):
    runtime.write_warning("seen first")
    snapshot = runtime.read_warnings_snapshot()
    runtime.write_warning("written after snapshot")
    ds.connection().execute(
        """
        CREATE TEMP TRIGGER fail_warning_clear
        BEFORE DELETE ON list_warnings
        BEGIN
            SELECT RAISE(ABORT, 'simulated warning clear failure');
        END
        """
    )

    try:
        with pytest.raises(sqlite3.IntegrityError, match="simulated warning clear failure"):
            runtime.clear_warnings_through(snapshot["max_id"])
        assert runtime.read_warnings_snapshot()["warnings"] == [
            "seen first",
            "written after snapshot",
        ]
    finally:
        ds.connection().execute("DROP TRIGGER fail_warning_clear")

    runtime.clear_warnings_through(snapshot["max_id"])
    assert runtime.read_warnings_snapshot()["warnings"] == ["written after snapshot"]


def test_connected_users_preserve_order_and_remove_all_matching_ids(ds):
    assert runtime.read_connected_users() == []
    runtime.write_connected_user("sid-a")
    runtime.write_connected_user("sid-b")
    runtime.write_connected_user("sid-a")
    assert runtime.read_connected_users() == ["sid-a", "sid-b", "sid-a"]

    runtime.remove_connected_user("sid-a")
    assert runtime.read_connected_users() == ["sid-b"]
    assert runtime.flush_connected_users() == []
    assert runtime.read_connected_users() == []


def test_probe_status_routes_aux_devices_to_the_aux_bucket(ds):
    runtime.write_generic_key(
        "probe_device_info",
        [
            {
                "device": "ambient-device",
                "status": {"temperature": 72},
                "config": {"units": "F"},
            }
        ],
    )

    result = runtime.read_probe_status(
        [
            {
                "type": "Aux",
                "label": "Ambient",
                "device": "ambient-device",
                "enabled": False,
            }
        ]
    )

    assert result["P"] == {}
    assert result["F"] == {}
    assert result["AUX"]["Ambient"] == {
        "status": {"temperature": 72},
        "config": {"units": "F"},
        "enabled": False,
        "profile": None,
        "port": None,
        "type": "Aux",
        "device": "ambient-device",
        "label": "Ambient",
        "name": None,
    }


def test_generic_keys_round_trip_detached_json_values_and_absence_raises(ds):
    with pytest.raises(TypeError):
        runtime.read_generic_key("missing")

    value = {"nested": {"items": [1, 2]}}
    runtime.write_generic_key("runtime:test", value)
    value["nested"]["items"].append(3)
    detached = runtime.read_generic_key("runtime:test")
    detached["nested"]["items"].append(4)

    assert runtime.read_generic_key("runtime:test") == {"nested": {"items": [1, 2]}}


@pytest.mark.parametrize(
    ("name", "snapshot"),
    [
        (None, {"revision": 0}),
        ("   ", {"revision": 0}),
        ("pid_sp", []),
    ],
)
def test_controller_model_checkpoint_rejects_invalid_identity_or_shape(
    ds, name, snapshot
):
    assert runtime.write_controller_model_checkpoint(name, snapshot) is False
    assert datastore.get_blob("controller_model_state") is None


@pytest.mark.parametrize("revision", [True, "1", -1])
def test_controller_model_checkpoint_rejects_invalid_revisions(ds, revision):
    assert (
        runtime.write_controller_model_checkpoint("pid_sp", {"revision": revision}) is False
    )
    assert datastore.get_blob("controller_model_state") is None


@pytest.mark.parametrize(
    "snapshot",
    [
        pytest.param({"revision": 1, "model": float("nan")}, id="non-finite-number"),
        pytest.param({"revision": 1, "model": object()}, id="non-json-value"),
    ],
)
def test_controller_model_checkpoint_rejects_non_json_snapshots(ds, snapshot):
    assert runtime.write_controller_model_checkpoint("pid_sp", snapshot) is False
    assert datastore.get_blob("controller_model_state") is None


@pytest.mark.parametrize(
    "stored",
    [
        pytest.param("{not json", id="malformed-json"),
        pytest.param(json.dumps([]), id="non-object-state"),
        pytest.param(
            json.dumps({"version": 2, "models": {}}), id="unsupported-version"
        ),
        pytest.param(json.dumps({"version": 1, "models": []}), id="non-object-models"),
        pytest.param(
            json.dumps({"version": 1, "models": {"pid_sp": "not a snapshot"}}),
            id="non-object-existing-snapshot",
        ),
    ],
)
def test_controller_model_checkpoint_rejects_corrupt_stored_state_without_overwriting(
    ds, stored
):
    if stored == "{not json":
        ds.connection().execute("PRAGMA ignore_check_constraints = ON")
    datastore.set_blob("controller_model_state", stored)
    ds.connection().execute("PRAGMA ignore_check_constraints = OFF")

    assert (
        runtime.write_controller_model_checkpoint("pid_sp", {"revision": 1}) is False
    )
    assert datastore.get_blob("controller_model_state") == stored


def test_controller_model_checkpoint_is_owned_monotonic_and_atomic(ds):
    snapshot = {"revision": 3, "model": {"gain": 1.5}}
    assert runtime.write_controller_model_checkpoint("pid_sp", snapshot) is True
    snapshot["model"]["gain"] = 99

    assert runtime.write_controller_model_checkpoint(
        "pid_sp", {"revision": 3, "model": {"gain": 2.0}}
    ) is False
    assert runtime.read_generic_key("controller_model_state") == {
        "version": 1,
        "models": {"pid_sp": {"revision": 3, "model": {"gain": 1.5}}},
    }

    ds.connection().execute(
        """
        CREATE TRIGGER fail_controller_model_checkpoint
        BEFORE UPDATE ON kv
        WHEN NEW.key = 'controller_model_state'
        BEGIN
            SELECT RAISE(ABORT, 'simulated checkpoint failure');
        END
        """
    )
    try:
        with pytest.raises(sqlite3.IntegrityError, match="simulated checkpoint failure"):
            runtime.write_controller_model_checkpoint(
                "pid_sp", {"revision": 4, "model": {"gain": 2.0}}
            )
        assert runtime.read_generic_key("controller_model_state") == {
            "version": 1,
            "models": {"pid_sp": {"revision": 3, "model": {"gain": 1.5}}},
        }
    finally:
        ds.connection().execute("DROP TRIGGER fail_controller_model_checkpoint")
