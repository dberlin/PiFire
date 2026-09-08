import pytest
from pydantic import ValidationError

from common.clock_domain import CLOCK_STAMP_SCHEMA, ClockStamp
from common.current_schema import (
    CurrentSchema,
    LastReading,
    build_current,
    dump_legacy,
    load_current,
    to_snapshot,
    zeroed_current,
)

# The blob a real install held on 2026-08-03, read out of pifire.db. Written by
# a build that predates LAST, which is why timestamp and last_readings must
# default rather than be required.
LIVE_PRE_LAST_BLOB = {
    "P": {"PitProbe": 0},
    "F": {"PinkProbe": 0},
    "PSP": 0,
    "NT": {"PinkProbe": 0, "PitProbe": 0},
    "AUX": {},
}


def test_live_pre_last_blob_validates_with_defaults():
    schema = CurrentSchema.model_validate(LIVE_PRE_LAST_BLOB)
    assert schema.primary == {"PitProbe": 0}
    assert schema.food == {"PinkProbe": 0}
    assert schema.aux == {}
    assert schema.primary_setpoint == 0
    assert schema.notify_targets == {"PinkProbe": 0, "PitProbe": 0}
    assert schema.timestamp == 0
    assert schema.last_readings == {}


def test_canonical_names_are_accepted_too():
    schema = CurrentSchema.model_validate(
        {
            "primary": {"PitProbe": 210},
            "food": {},
            "aux": {},
            "primary_setpoint": 225,
            "notify_targets": {},
            "timestamp": 1707345482984,
            "last_readings": {},
        }
    )
    assert schema.primary == {"PitProbe": 210}
    assert schema.primary_setpoint == 225


def test_historical_last_reading_keeps_unknown_clock():
    blob = {
        "P": {"PitProbe": 210},
        "F": {"PinkProbe": None},
        "AUX": {},
        "PSP": 0,
        "NT": {"PitProbe": 0, "PinkProbe": 165},
        "TS": 1707345482984,
        "LAST": {"PinkProbe": {"temp": 140, "ts": 1707345400000}},
    }
    schema = CurrentSchema.model_validate(blob)
    assert schema.last_readings["PinkProbe"].clock_stamp is None
    assert dump_legacy(schema)["LAST"]["PinkProbe"] == {
        "temp": 140,
        "ts": 1707345400000,
        "clock_stamp": None,
    }


def test_integer_zero_stays_an_integer():
    # float would round-trip PSP: 0 as 0.0, which is a visible change to
    # /api/get/current and breaks the characterization golden.
    dumped = dump_legacy(CurrentSchema.model_validate(LIVE_PRE_LAST_BLOB))
    assert isinstance(dumped["PSP"], int)
    assert isinstance(dumped["P"]["PitProbe"], int)


def test_none_readings_survive():
    schema = CurrentSchema.model_validate({"P": {"PitProbe": None}, "NT": {"PitProbe": None}})
    assert schema.primary["PitProbe"] is None
    assert schema.notify_targets["PitProbe"] is None


def test_unmodeled_key_is_rejected():
    with pytest.raises(ValidationError):
        CurrentSchema.model_validate(dict(LIVE_PRE_LAST_BLOB, SURPRISE=1))


def test_load_current_returns_none_on_a_bad_blob():
    assert load_current({"SURPRISE": 1}) is None


def test_load_current_parses_a_good_blob():
    assert load_current(LIVE_PRE_LAST_BLOB).primary == {"PitProbe": 0}


PROBE_INFO = [
    {"label": "PitProbe", "type": "Primary"},
    {"label": "PinkProbe", "type": "Food"},
    {"label": "Ambient", "type": "Aux"},
]


def test_zeroed_current_rebuilds_from_the_probe_map():
    schema = zeroed_current(PROBE_INFO)
    assert schema.primary == {"PitProbe": 0}
    assert schema.food == {"PinkProbe": 0}
    assert schema.aux == {"Ambient": 0}
    assert schema.notify_targets == {"PitProbe": 0, "PinkProbe": 0, "Ambient": 0}
    assert schema.last_readings == {}


def test_zeroed_current_omits_the_timestamp_when_asked():
    dumped = dump_legacy(zeroed_current(PROBE_INFO), exclude_timestamp=True)
    assert set(dumped) == {"P", "F", "AUX", "PSP", "NT", "LAST"}


IN_DATA = {
    "probe_history": {
        "primary": {"PitProbe": 210},
        "food": {"PinkProbe": 140},
        "aux": {},
    },
    "primary_setpoint": 225,
    "notify_targets": {"PitProbe": 0, "PinkProbe": 165},
}


def test_build_current_maps_probe_history_onto_the_sections():
    schema = build_current(IN_DATA, None, 1000)
    assert schema.primary == {"PitProbe": 210}
    assert schema.food == {"PinkProbe": 140}
    assert schema.aux == {}
    assert schema.primary_setpoint == 225
    assert schema.timestamp == 1000


def test_build_current_stamps_every_live_reading():
    schema = build_current(IN_DATA, None, 1000)
    assert schema.last_readings["PitProbe"] == LastReading(temp=210, ts=1000)
    assert schema.last_readings["PinkProbe"] == LastReading(temp=140, ts=1000)


def test_build_current_carries_a_stale_probe_forward():
    first = build_current(IN_DATA, None, 1000)
    gone = {
        "probe_history": {"primary": {"PitProbe": 212}, "food": {"PinkProbe": None}, "aux": {}},
        "primary_setpoint": 225,
        "notify_targets": {"PitProbe": 0, "PinkProbe": 165},
    }
    second = build_current(gone, first, 5000)
    assert second.food["PinkProbe"] is None
    assert second.last_readings["PinkProbe"] == LastReading(temp=140, ts=1000)
    assert second.last_readings["PitProbe"] == LastReading(temp=212, ts=5000)


def test_build_current_drops_a_probe_that_never_reported():
    never = {
        "probe_history": {"primary": {"PitProbe": None}, "food": {}, "aux": {}},
        "primary_setpoint": 0,
        "notify_targets": {"PitProbe": 0},
    }
    assert build_current(never, None, 1000).last_readings == {}


def test_snapshot_is_frozen_and_carries_canonical_names():
    import dataclasses

    snap = to_snapshot(build_current(IN_DATA, None, 1000))
    assert snap.primary == {"PitProbe": 210}
    assert snap.primary_setpoint == 225
    assert snap.timestamp == 1000
    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.primary_setpoint = 300


def test_snapshot_does_not_alias_the_model():
    schema = build_current(IN_DATA, None, 1000)
    snap = to_snapshot(schema)
    schema.primary["PitProbe"] = 999
    assert snap.primary["PitProbe"] == 210


def test_snapshot_last_readings_cannot_be_mutated():
    schema = build_current(IN_DATA, None, 1000)
    snap = to_snapshot(schema)
    with pytest.raises(ValidationError):
        snap.last_readings["PitProbe"].temp = 999


def test_last_reading_stamp_round_trips_without_numeric_coercion():
    stamp = ClockStamp(
        CLOCK_STAMP_SCHEMA,
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
        100.0,
        1_800_000_000.0,
        0.0,
    )
    schema = build_current(IN_DATA, None, 1_800_000_000_000, clock_stamp=stamp)
    restored = CurrentSchema.model_validate_json(schema.model_dump_json(by_alias=True))
    assert restored.last_readings["PinkProbe"].clock_stamp == stamp
    assert dump_legacy(restored)["LAST"]["PinkProbe"]["clock_stamp"] == stamp.as_dict()


@pytest.mark.parametrize(
    "stamp",
    [
        1_800_000_000,
        {},
        {
            "schema_version": 1,
            "boot_id": "11111111-1111-4111-8111-111111111111",
            "runtime_id": "22222222-2222-4222-8222-222222222222",
            "observed_monotonic_s": "100",
            "observed_wall_s": 1_800_000_000,
            "suspend_offset_s": 0,
        },
        {
            "schema_version": 1,
            "boot_id": "11111111-1111-4111-8111-111111111111",
            "runtime_id": "22222222-2222-4222-8222-222222222222",
            "observed_monotonic_s": 100,
            "observed_wall_s": float("nan"),
            "suspend_offset_s": 0,
        },
    ],
)
def test_malformed_last_reading_stamp_discards_current_cache(stamp):
    assert load_current({"LAST": {"PinkProbe": {"temp": 140, "ts": 1000, "clock_stamp": stamp}}}) is None


@pytest.mark.parametrize("value", ["140", True, float("nan"), float("inf")])
def test_malformed_numeric_reading_discards_current_cache(value):
    assert load_current({"F": {"PinkProbe": value}}) is None
    assert load_current({"LAST": {"PinkProbe": {"temp": value, "ts": 1000}}}) is None
