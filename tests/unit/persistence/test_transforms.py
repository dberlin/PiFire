from common.current_schema import CurrentSchema
from common.persistence.transforms import (
    apply_control_delta,
    current_snapshot,
    history_row_to_dict,
    initial_status,
)


def test_initial_status_preserves_the_exact_existing_defaults_when_distance_module_is_missing():
    status = initial_status(
        {"globals": {"units": "C"}},
        {"current": {"hopper_level": 42}},
    )

    assert status == {
        "s_plus": False,
        "hopper_level_enabled": False,
        "hopper_level": 42,
        "units": "C",
        "mode": "Stop",
        "recipe": False,
        "startup_timestamp": 0,
        "start_time": 0,
        "start_duration": 0,
        "shutdown_duration": 0,
        "prime_duration": 0,
        "prime_amount": 0,
        "lid_open_detected": False,
        "lid_open_endtime": 0,
        "p_mode": 0,
        "recipe_paused": False,
        "outpins": {"auger": False, "fan": False, "igniter": False, "power": False},
        "cycle_ratio": 0,
        "fan_duty": 0,
    }


def test_initial_status_enables_hopper_level_for_a_configured_distance_module():
    status = initial_status(
        {"globals": {"units": "F"}, "modules": {"dist": "ultrasonic"}},
        {"current": {"hopper_level": 37}},
    )

    assert status["hopper_level_enabled"] is True
    assert status["hopper_level"] == 37
    assert status["units"] == "F"


def test_initial_status_returns_fresh_nested_values_on_every_call():
    settings = {"globals": {"units": "F"}, "modules": {"dist": "none"}}
    pellet_db = {"current": {"hopper_level": 100}}

    first = initial_status(settings, pellet_db)
    second = initial_status(settings, pellet_db)
    first["outpins"]["fan"] = True

    assert second["outpins"] == {"auger": False, "fan": False, "igniter": False, "power": False}
    assert settings == {"globals": {"units": "F"}, "modules": {"dist": "none"}}
    assert pellet_db == {"current": {"hopper_level": 100}}


def test_current_snapshot_maps_the_existing_current_shape_and_timestamp_exactly():
    incoming = {
        "probe_history": {
            "primary": {"PitProbe": 210},
            "food": {"PinkProbe": 140},
            "aux": {"Ambient": 72},
        },
        "primary_setpoint": 225,
        "notify_targets": {"PitProbe": 0, "PinkProbe": 165, "Ambient": None},
    }

    snapshot = current_snapshot(None, incoming, 1_707_345_482_984)

    assert isinstance(snapshot, CurrentSchema)
    assert snapshot.model_dump() == {
        "primary": {"PitProbe": 210},
        "food": {"PinkProbe": 140},
        "aux": {"Ambient": 72},
        "primary_setpoint": 225,
        "notify_targets": {"PitProbe": 0, "PinkProbe": 165, "Ambient": None},
        "timestamp": 1_707_345_482_984,
        "last_readings": {
            "PitProbe": {"temp": 210, "ts": 1_707_345_482_984},
            "PinkProbe": {"temp": 140, "ts": 1_707_345_482_984},
            "Ambient": {"temp": 72, "ts": 1_707_345_482_984},
        },
    }


def test_current_snapshot_accepts_a_previous_schema_with_optional_fields_defaulted():
    previous = CurrentSchema.model_validate(
        {
            "P": {"PitProbe": 209},
            "F": {"PinkProbe": 139},
            "AUX": {},
            "PSP": 225,
            "NT": {"PitProbe": 0, "PinkProbe": 165},
        }
    )
    incoming = {
        "probe_history": {
            "primary": {"PitProbe": 210},
            "food": {"PinkProbe": 140},
            "aux": {},
        },
        "primary_setpoint": 225,
        "notify_targets": {"PitProbe": 0, "PinkProbe": 165},
    }

    snapshot = current_snapshot(previous, incoming, 2_000)

    assert previous.timestamp == 0
    assert previous.last_readings == {}
    assert snapshot.timestamp == 2_000
    assert snapshot.last_readings["PitProbe"].model_dump() == {"temp": 210, "ts": 2_000}
    assert snapshot.last_readings["PinkProbe"].model_dump() == {"temp": 140, "ts": 2_000}


def test_current_snapshot_carries_a_stale_reading_with_its_original_timestamp():
    previous = CurrentSchema.model_validate(
        {
            "P": {"PitProbe": 210},
            "F": {"PinkProbe": 140},
            "AUX": {},
            "PSP": 225,
            "NT": {"PitProbe": 0, "PinkProbe": 165},
            "TS": 1_000,
            "LAST": {
                "PitProbe": {"temp": 210, "ts": 1_000},
                "PinkProbe": {"temp": 140, "ts": 1_000},
            },
        }
    )
    incoming = {
        "probe_history": {
            "primary": {"PitProbe": 212},
            "food": {"PinkProbe": None},
            "aux": {},
        },
        "primary_setpoint": 225,
        "notify_targets": {"PitProbe": 0, "PinkProbe": 165},
    }

    snapshot = current_snapshot(previous, incoming, 5_000)

    assert snapshot.timestamp == 5_000
    assert snapshot.food == {"PinkProbe": None}
    assert snapshot.last_readings["PinkProbe"].model_dump() == {"temp": 140, "ts": 1_000}
    assert snapshot.last_readings["PitProbe"].model_dump() == {"temp": 212, "ts": 5_000}


def test_current_snapshot_does_not_alias_previous_or_incoming_nested_values():
    previous = CurrentSchema.model_validate(
        {
            "P": {},
            "F": {"PinkProbe": 140},
            "LAST": {"PinkProbe": {"temp": 140, "ts": 1_000}},
        }
    )
    incoming = {
        "probe_history": {
            "primary": {"PitProbe": 212},
            "food": {"PinkProbe": None},
            "aux": {},
        },
        "primary_setpoint": 225,
        "notify_targets": {"PitProbe": 0, "PinkProbe": 165},
    }

    snapshot = current_snapshot(previous, incoming, 5_000)
    incoming["probe_history"]["primary"]["PitProbe"] = 999
    incoming["notify_targets"]["PinkProbe"] = 999

    assert snapshot.primary == {"PitProbe": 212}
    assert snapshot.notify_targets == {"PitProbe": 0, "PinkProbe": 165}

    snapshot.primary["PitProbe"] = 777
    snapshot.last_readings.clear()

    assert previous.food == {"PinkProbe": 140}
    assert previous.last_readings["PinkProbe"].model_dump() == {"temp": 140, "ts": 1_000}
    assert incoming["probe_history"]["primary"] == {"PitProbe": 999}
    assert incoming["notify_targets"] == {"PitProbe": 0, "PinkProbe": 999}


def test_history_row_to_dict_preserves_timestamp_values_and_omits_absent_extended_data():
    row = (
        1_707_345_482_984,
        225,
        '{"PitProbe": 210}',
        '{"PinkProbe": 140}',
        '{"Ambient": 72}',
        '{"PitProbe": 0, "PinkProbe": 165}',
        None,
        None,
        None,
        None,
    )

    assert history_row_to_dict(row) == {
        "T": 1_707_345_482_984,
        "P": {"PitProbe": 210},
        "F": {"PinkProbe": 140},
        "PSP": 225,
        "NT": {"PitProbe": 0, "PinkProbe": 165},
        "AUX": {"Ambient": 72},
        # Emitted even when NULL -- see history_row_to_dict for why these
        # cannot be conditional the way EXD is.
        "CR": None,
        "RCR": None,
        "FD": None,
    }


def test_history_row_to_dict_decodes_extended_data_into_a_detached_result():
    row = (
        5_000,
        225,
        '{"PitProbe": 210}',
        "{}",
        "{}",
        '{"PitProbe": 0}',
        '{"auger": {"on": true}}',
        0.25,
        None,
        65,
    )

    first = history_row_to_dict(row)
    second = history_row_to_dict(row)
    first["P"]["PitProbe"] = 999
    first["EXD"]["auger"]["on"] = False

    assert second == {
        "T": 5_000,
        "P": {"PitProbe": 210},
        "F": {},
        "PSP": 225,
        "NT": {"PitProbe": 0},
        "AUX": {},
        "CR": 0.25,
        "RCR": None,
        "FD": 65,
        "EXD": {"auger": {"on": True}},
    }


def test_apply_control_delta_deep_merges_against_live_state_without_aliasing_the_delta():
    control = {"mode": "Stop", "nested": {"x": 1, "y": 2}}
    delta = {
        "__control_delta__": 1,
        "set": {"mode": "Hold", "nested": {"x": 9}},
    }

    returned = apply_control_delta(control, delta)
    delta["set"]["nested"]["x"] = 999

    assert returned is control
    assert control == {"mode": "Hold", "nested": {"x": 9, "y": 2}}
