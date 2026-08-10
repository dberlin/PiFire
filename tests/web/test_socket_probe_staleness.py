"""Per-probe freshness, from the control write through to the dash payload.

A probe device may have no reading to give: a network-polled one returns None
for a channel whose cache has gone stale rather than inventing a number, and
that None travels all the way to both UIs. Neither can work out how old the
last real value was, because ``current["TS"]`` timestamps the whole blob and
goes on advancing while one probe is stale.

Pinned at the producing end because neither UI can pin it: the web fixture and
the Qt stub are both hand-written, so a field dropped here leaves them green
and the real cards showing a stale number as though it were live.
"""

import json

from common.web_contracts.core import ProbeDataPayload, ProbeStatusPayload
from common import datastore
from common.datastore_accessors import (
    flush_current,
    init_status,
    read_current,
    read_pellet_db,
    read_settings,
    write_current,
    write_generic_key,
)


def _labels():
    probes = read_settings()["probe_settings"]["probe_map"]["probe_info"]
    primary = next(p["label"] for p in probes if p["type"] == "Primary")
    food = next(p["label"] for p in probes if p["type"] == "Food")
    return primary, food


def _write(primary_temp, food_temp):
    """Write one control pass. `food_temp` lands on the FIRST food probe; the
    rest report normally, so a carried-forward value cannot be mistaken for
    something the whole structure does."""
    probes = read_settings()["probe_settings"]["probe_map"]["probe_info"]
    _, first_food = _labels()
    history = {"primary": {}, "food": {}, "aux": {}}
    for probe in probes:
        if probe["type"] == "Primary":
            history["primary"][probe["label"]] = primary_temp
        elif probe["type"] == "Food":
            history["food"][probe["label"]] = food_temp if probe["label"] == first_food else 100
        else:
            history["aux"][probe["label"]] = 0
    write_current(
        {
            "probe_history": history,
            "primary_setpoint": 225,
            "notify_targets": {},
        }
    )


def _dash_probes():
    from blueprints.mobile import socket_io

    return socket_io._get_dash_data(read_settings(), read_pellet_db())


def test_a_real_reading_is_stamped_as_the_last_one(ds):
    _write(225, 140)

    _, food = _labels()
    assert read_current()["LAST"][food]["temp"] == 140
    assert read_current()["LAST"][food]["ts"] > 0


def test_a_null_reading_carries_the_previous_value_forward(ds):
    _write(225, 140)
    _, food = _labels()
    stamped = read_current()["LAST"][food]

    _write(230, None)

    # The probe's own value is left as the None it really is -- carrying the
    # old number into `F` would make the staleness undetectable downstream.
    assert read_current()["F"][food] is None
    assert read_current()["LAST"][food] == stamped


def test_a_probe_that_has_never_reported_gets_no_last_entry(ds):
    _write(225, None)

    _, food = _labels()
    assert food not in read_current()["LAST"]


def test_a_recovered_probe_restamps_rather_than_keeping_the_stale_entry(ds):
    _write(225, 140)
    _, food = _labels()
    stale = read_current()["LAST"][food]

    _write(225, None)
    _write(225, 155)

    assert read_current()["LAST"][food]["temp"] == 155
    assert read_current()["LAST"][food]["ts"] >= stale["ts"]


def test_flushing_drops_the_last_readings_with_the_temps(ds):
    _write(225, 140)

    assert flush_current()["LAST"] == {}


def _food_probe(payload):
    _, food = _labels()
    return next(p for p in payload["foodProbes"] if p["label"] == food)


def test_the_wire_carries_the_last_reading_and_its_age_for_a_null_probe(ds):
    init_status()
    write_generic_key("probe_device_info", {})
    _write(225, 140)
    _write(225, None)

    probe = _food_probe(_dash_probes())

    # temp stays null: the card has to know the reading is absent before it can
    # decide to show the previous one as stale.
    assert probe["temp"] is None
    assert probe["status"]["lastTemp"] == 140
    assert probe["status"]["lastReadingAge"] >= 0


def test_a_blob_written_before_LAST_existed_still_serves(ds):
    # An upgraded web tier can be reading a control:current written by a
    # control process that has not restarted yet, so the key is simply absent.
    # It must degrade to "no last reading", not raise.
    init_status()
    write_generic_key("probe_device_info", {})
    _write(225, 140)
    stored = read_current()
    del stored["LAST"]
    datastore.set_blob("control:current", json.dumps(stored))

    probe = _food_probe(_dash_probes())

    assert probe["temp"] == 140
    assert "lastTemp" not in probe["status"]
    assert "lastReadingAge" not in probe["status"]


def test_a_reporting_probe_publishes_a_last_reading_that_agrees_with_temp(ds):
    init_status()
    write_generic_key("probe_device_info", {})
    _write(225, 140)

    probe = _food_probe(_dash_probes())

    assert probe["temp"] == 140
    assert probe["status"]["lastTemp"] == 140
    assert probe["status"]["lastReadingAge"] == 0


def test_probe_structure_matches_the_strict_wire_contract(ds):
    from blueprints.mobile import socket_io

    payload = socket_io._get_probe_structure("Food", read_settings())
    validated = ProbeDataPayload.model_validate(payload, strict=True)

    assert validated.model_dump(mode="json", by_alias=True, exclude_none=False) == payload


def test_probe_status_preserves_plugin_specific_members():
    payload = {"connected": True, "pluginSignal": -42}

    validated = ProbeStatusPayload.model_validate(payload, strict=True)

    assert validated.model_dump(mode="json", by_alias=True, exclude_none=False) == payload
