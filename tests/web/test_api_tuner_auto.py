"""The auto-tuning half of the /api/tuner surface.

SAFETY: opening a session moves the live grill to Monitor. Every test that
opens one closes it, and the autouse fixture asserts the grill is back in Stop.
See docs/superpowers/plans/2026-07-28-react-tuner-auto.md.
"""

import pytest


def control_now():
    from common.persistence.control import execute_control_writes, read_control

    execute_control_writes()
    return read_control()


@pytest.fixture(autouse=True)
def grill_left_stopped(ds):
    yield
    control = control_now()
    assert control["mode"] == "Stop", "a test left the grill out of Stop"
    assert not control.get("tuning_mode"), "a test left tuning_mode set"


def set_mode(mode):
    from common.control_delta import control_delta
    from common.persistence.control import enqueue_control_delta, execute_control_writes

    enqueue_control_delta(control_delta(set_values={"mode": mode}), origin="test")
    execute_control_writes()


def test_opening_a_session_flushes_the_autotune_store(ds, client):
    """A fresh session must not inherit samples from a previous one. Flask
    flushed on the first auto-status poll; the session is where "start fresh"
    lives now."""
    from common.persistence.history import read_autotune, write_autotune

    write_autotune({"ref_T": 100, "probe_Tr": 40000})
    assert len(read_autotune()) == 1

    set_mode("Stop")
    client.post("/api/tuner/session", json={"open": True})
    try:
        assert read_autotune() == []
    finally:
        # Drain the open write before closing, so close sees Monitor and restores
        # Stop -- otherwise it reads the not-yet-drained Stop, declines to
        # restore, and the grill is left in Monitor. (In production the control
        # loop ticks between actions.)
        control_now()
        client.post("/api/tuner/session", json={"open": False})
        control_now()


def test_closing_a_session_does_not_touch_the_autotune_store(ds, client):
    """Close restores grill state; it must not also discard the samples a just-
    finished auto tune may still want to read back."""
    from common.persistence.history import flush_autotune, read_autotune, write_autotune

    set_mode("Stop")
    client.post("/api/tuner/session", json={"open": True})
    control_now()
    write_autotune({"ref_T": 100, "probe_Tr": 40000})

    client.post("/api/tuner/session", json={"open": False})
    control_now()
    assert len(read_autotune()) == 1
    #  Clean up so the next test's flush-on-open assertion starts empty.
    flush_autotune()


def seed_tr(values):
    from common.persistence.history import write_tr

    write_tr(values)


def seed_current(primary=None, food=None, aux=None):
    """Write the control:current blob read_current() reads, through its own
    public writer.

    write_current transforms a probe_history wrapper into the {P, F, AUX} blob
    (confirmed live: P=primary, F=food, AUX=aux, keyed by label), so seeding
    means handing it that wrapper rather than the blob."""
    from common.persistence.runtime import write_current

    write_current(
        {
            "probe_history": {
                "primary": primary or {},
                "food": food or {},
                "aux": aux or {},
            },
            "primary_setpoint": 0,
            "notify_targets": {},
        }
    )


def test_auto_status_requires_both_probes(ds, client):
    for body in ({"reference": "Grill"}, {"probe": "Grill"}, {}):
        resp = client.post("/api/tuner/auto-status", json=body)
        assert resp.status_code == 400
        assert resp.get_json()["data"]["field"] in ("probe", "reference")


def test_auto_status_rejects_extra_json_members(ds, client):
    resp = client.post(
        "/api/tuner/auto-status",
        json={"probe": "Grill", "reference": "Ref", "extra": True},
    )
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "extra"


def test_auto_status_records_a_sample_and_reports_it(ds, client):
    from common.persistence.history import flush_autotune, read_autotune

    flush_autotune()
    seed_tr({"Grill": 41000})
    seed_current(primary={"Ref": 225})

    body = client.post("/api/tuner/auto-status", json={"probe": "Grill", "reference": "Ref"}).get_json()
    assert body["result"] == "OK"
    data = body["data"]
    assert data["current_tr"] == 41000
    assert data["current_temp"] == 225
    #  The sample landed in the queue with Flask's key names, so
    #  calc_auto_tune_status (unchanged) can consume it.
    (sample,) = read_autotune()
    assert sample == {"ref_T": 225, "probe_Tr": 41000}
    assert data["samples"] == 1
    assert data["ready"] is False


def test_auto_status_reports_null_for_a_probe_that_is_not_reporting(ds, client):
    """A probe absent from the tuning blob (Tr) or the current blob (temp) is
    null, not Flask's -1 sentinel, and no sample is recorded from it."""
    from common.persistence.history import flush_autotune, read_autotune

    flush_autotune()
    seed_tr({"Grill": 41000})
    seed_current()

    data = client.post("/api/tuner/auto-status", json={"probe": "Grill", "reference": "Missing"}).get_json()["data"]
    assert data["current_tr"] == 41000
    assert data["current_temp"] is None
    assert read_autotune() == [], "a sample was recorded from a missing reference"


def test_auto_status_finds_the_reference_in_food_and_aux_too(ds, client):
    from common.persistence.history import flush_autotune

    flush_autotune()
    seed_tr({"Grill": 41000})
    seed_current(food={"Food1": 160})
    data = client.post("/api/tuner/auto-status", json={"probe": "Grill", "reference": "Food1"}).get_json()["data"]
    assert data["current_temp"] == 160


def test_auto_status_becomes_ready_once_the_spread_is_wide_enough(ds, client):
    """More than ten samples spanning >= 50 F flips ready and fills the three
    derived points. Seeded directly rather than driven a poll at a time."""
    from common.persistence.history import flush_autotune, write_autotune

    flush_autotune()
    seed_tr({"Grill": 41000})
    seed_current(primary={"Ref": 240})
    #  Twelve samples from 100 F to 240 F: a 143 F spread, well over the 50 F
    #  minimum. Distinct temps so calc_auto_tune_status picks real high/low.
    for i in range(12):
        write_autotune({"ref_T": 100 + i * 13, "probe_Tr": 40000 - i * 3000})

    data = client.post("/api/tuner/auto-status", json={"probe": "Grill", "reference": "Ref"}).get_json()["data"]
    assert data["ready"] is True
    assert data["high_temp"] > data["low_temp"]
    assert data["high_temp"] - data["low_temp"] >= 50


def test_auto_status_writes_no_control(ds, client):
    """Sample accumulation is tuning DATA, not grill state. The only control
    writes on this surface are the two session calls."""
    from common.persistence.history import flush_autotune

    flush_autotune()
    seed_tr({"Grill": 41000})
    seed_current(primary={"Ref": 225})
    before = control_now()
    client.post("/api/tuner/auto-status", json={"probe": "Grill", "reference": "Ref"})
    after = control_now()
    assert after["mode"] == before["mode"]
    assert after.get("tuning_mode") == before.get("tuning_mode")


def test_auto_status_skips_an_early_zero_reading(ds, client):
    """The DS18B20 slow-start guard: with few samples and a zero temp, the poll
    reports but records nothing, so a cold probe's 0 does not poison the solve."""
    from common.persistence.history import flush_autotune, read_autotune

    flush_autotune()
    seed_tr({"Grill": 41000})
    seed_current(primary={"Ref": 0})
    client.post("/api/tuner/auto-status", json={"probe": "Grill", "reference": "Ref"})
    assert read_autotune() == [], "an early zero reading was recorded"
