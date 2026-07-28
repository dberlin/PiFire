"""The auto-tuning half of the /api/tuner surface.

SAFETY: opening a session moves the live grill to Monitor. Every test that
opens one closes it, and the autouse fixture asserts the grill is back in Stop.
See docs/superpowers/plans/2026-07-28-react-tuner-auto.md.
"""

import pytest

from app import app as flask_app


@pytest.fixture
def client(ds):
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def control_now():
    from common.datastore_accessors import execute_control_writes, read_control

    execute_control_writes()
    return read_control()


@pytest.fixture(autouse=True)
def grill_left_stopped(ds):
    yield
    control = control_now()
    assert control["mode"] == "Stop", "a test left the grill out of Stop"
    assert not control.get("tuning_mode"), "a test left tuning_mode set"


def set_mode(mode):
    from common.common import WriteKind
    from common.control_delta import control_delta
    from common.datastore_accessors import execute_control_writes, write_control

    write_control(control_delta(set_values={"mode": mode}), WriteKind.DELTA, origin="test")
    execute_control_writes()


def test_opening_a_session_flushes_the_autotune_store(ds, client):
    """A fresh session must not inherit samples from a previous one. Flask
    flushed on the first auto-status poll; the session is where "start fresh"
    lives now."""
    from common.datastore_accessors import read_autotune, write_autotune

    write_autotune({"ref_T": 100, "probe_Tr": 40000})
    assert len(read_autotune()) == 1

    set_mode("Stop")
    client.post("/api/tuner/session", json={"open": True})
    try:
        assert read_autotune() == []
    finally:
        #  Drain the open write before closing, so close sees Monitor and
        #  restores Stop -- otherwise it reads the not-yet-drained Stop, declines
        #  to restore, and the grill is left in Monitor. (Same ordering the
        #  slice-1 session tests handle; the control loop ticks between actions
        #  in production.)
        control_now()
        client.post("/api/tuner/session", json={"open": False})
        control_now()


def test_closing_a_session_does_not_touch_the_autotune_store(ds, client):
    """Close restores grill state; it must not also discard the samples a just-
    finished auto tune may still want to read back."""
    from common.datastore_accessors import flush_autotune, read_autotune, write_autotune

    set_mode("Stop")
    client.post("/api/tuner/session", json={"open": True})
    control_now()
    write_autotune({"ref_T": 100, "probe_Tr": 40000})

    client.post("/api/tuner/session", json={"open": False})
    control_now()
    assert len(read_autotune()) == 1
    #  Clean up so the next test's flush-on-open assertion starts empty.
    flush_autotune()
