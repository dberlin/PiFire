"""The JSON tuner surface the React /tuner page drives.

SAFETY: opening a session moves the grill from Stop to MONITOR. Monitor lights
nothing -- it reads probes -- but it is a real mode change, so every test below
that opens one closes it, and the module-level fixture asserts the grill is
back in Stop afterwards. See
docs/superpowers/plans/2026-07-28-react-tuner-manual.md.
"""

import pytest

from app import app as flask_app


@pytest.fixture
def client(ds):
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def control_now():
    """read_control(), after draining any queued writes.

    The session endpoint writes control DELTAS, which only take effect when the
    control loop drains them (common.datastore_accessors.execute_control_writes).
    In production the real loop ticks every second; this Flask-only harness has
    no loop, so a read-back must drain first to see what the next tick would
    have produced. The endpoint's own RESPONSE is computed from intent and needs
    no drain -- only reads of the persisted control:general do.
    """
    from common.datastore_accessors import execute_control_writes, read_control

    execute_control_writes()
    return read_control()


@pytest.fixture(autouse=True)
def grill_left_stopped(ds):
    """Every test in this module must hand the grill back in Stop.

    autouse and post-yield: a test that opens a session and then fails its
    assertion would otherwise leave tuning_mode set for every test after it,
    and the failure would be attributed to the wrong test.
    """
    yield
    control = control_now()
    assert control["mode"] == "Stop", "a test left the grill out of Stop"
    assert not control.get("tuning_mode"), "a test left tuning_mode set"


def set_mode(mode):
    from common.common import WriteKind
    from common.control_delta import control_delta
    from common.datastore_accessors import write_control

    write_control(control_delta(set_values={"mode": mode}), WriteKind.DELTA, origin="test")
    #  Drain immediately so a test's precondition is live before it POSTs.
    from common.datastore_accessors import execute_control_writes

    execute_control_writes()


def test_opening_a_session_enables_tuning_and_monitors(ds, client):
    set_mode("Stop")
    body = client.post("/api/tuner/session", json={"open": True}).get_json()
    assert body["result"] == "OK"
    assert body["data"]["open"] is True
    assert body["data"]["mode"] == "Monitor"

    control = control_now()
    assert control["tuning_mode"] is True
    assert control["mode"] == "Monitor"

    client.post("/api/tuner/session", json={"open": False})


def test_closing_a_session_restores_stop(ds, client):
    set_mode("Stop")
    client.post("/api/tuner/session", json={"open": True})
    #  Stands in for the control loop ticking between open and close: without
    #  it the close would read the not-yet-drained Stop and decline to restore.
    control_now()

    body = client.post("/api/tuner/session", json={"open": False}).get_json()
    assert body["data"]["open"] is False
    assert body["data"]["restored"] is True

    control = control_now()
    assert control["tuning_mode"] is False
    assert control["mode"] == "Stop"


def test_closing_is_idempotent(ds, client):
    """The React hook closes on unmount, and an unmount can follow an explicit
    Finish. Closing twice must not be an error and must not touch the mode the
    second time.

    control_now() between the calls stands in for the control loop ticking
    between the user's actions in production: it drains the open write so the
    first close sees Monitor and genuinely restores, making the SECOND close
    the no-op this test is about.
    """
    set_mode("Stop")
    client.post("/api/tuner/session", json={"open": True})
    control_now()

    first = client.post("/api/tuner/session", json={"open": False}).get_json()
    assert first["data"]["restored"] is True
    control_now()

    second = client.post("/api/tuner/session", json={"open": False}).get_json()
    assert second["result"] == "OK"
    assert second["data"]["restored"] is False


def test_a_cooking_grill_refuses_to_open_a_session(ds, client):
    """Tuning from Hold would fight the controller for the probes and lie about
    what the grill is doing. Flask offers no such guard; this one matches the
    409 shape /api/admin/system already uses."""
    set_mode("Hold")
    try:
        resp = client.post("/api/tuner/session", json={"open": True})
        assert resp.status_code == 409
        body = resp.get_json()
        assert body["message"] == "not_tunable"
        assert body["data"]["mode"] == "Hold"

        assert not control_now().get("tuning_mode"), "a refused open still wrote tuning_mode"
    finally:
        set_mode("Stop")


def test_closing_a_session_does_not_stop_a_cook(ds, client):
    """The asymmetry in Flask that is CORRECT and must survive the port: close
    only restores Stop when the mode is currently Monitor. If a cook started
    while the session was open, closing leaves it alone."""
    set_mode("Stop")
    client.post("/api/tuner/session", json={"open": True})
    set_mode("Hold")
    try:
        body = client.post("/api/tuner/session", json={"open": False}).get_json()
        assert body["data"]["restored"] is False
        control = control_now()
        assert control["mode"] == "Hold"
        assert control["tuning_mode"] is False
    finally:
        set_mode("Stop")


def test_the_open_flag_must_be_a_bool(ds, client):
    resp = client.post("/api/tuner/session", json={"open": "yes"})
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "open"


def test_session_rejects_extra_json_members(ds, client):
    resp = client.post("/api/tuner/session", json={"open": True, "extra": True})
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "extra"


def test_the_generic_api_catchall_does_not_swallow_this_path(ds, client):
    """blueprints/api registers /api/<action>/<arg0> for GET and POST, which
    matches /api/tuner/session. See blueprints/api_admin/routes.py's docstring
    for the case where a request fell through to it and 404'd from elsewhere."""
    with flask_app.test_request_context("/api/tuner/session", method="POST"):
        from flask import request

        assert request.endpoint == "api_tuner_bp.tuner_session"


def seed_tr(values):
    """Write the control:tuning blob read_tr() reads.

    write_tr is the public writer for exactly this blob
    (common/datastore_accessors.py:654) -- do not reach for _write_json_blob.
    """
    from common.datastore_accessors import write_tr

    write_tr(values)


def test_tr_reports_a_reading_for_a_known_probe(ds, client):
    seed_tr({"Grill": 51234})
    body = client.get("/api/tuner/tr?probe=Grill").get_json()
    assert body["result"] == "OK"
    assert body["data"]["probe"] == "Grill"
    assert body["data"]["trohms"] == 51234


def test_tr_reports_null_for_a_probe_that_is_not_reporting(ds, client):
    """Flask answers {"trohms": 0} for a missing key, which a client cannot
    tell apart from a real zero-ohm reading. null is the honest answer and the
    page renders it as "waiting"."""
    seed_tr({"Grill": 51234})
    body = client.get("/api/tuner/tr?probe=Probe1").get_json()
    assert body["data"]["trohms"] is None


def test_tr_requires_a_probe(ds, client):
    resp = client.get("/api/tuner/tr")
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "probe"


def test_tr_does_not_write_control(ds, client):
    """The whole reason session and reading are separate endpoints. This is a
    GET and it must be inert: the page polls it once a second."""
    seed_tr({"Grill": 51234})
    before = control_now()
    client.get("/api/tuner/tr?probe=Grill")
    after = control_now()
    assert after["mode"] == before["mode"]
    assert after.get("tuning_mode") == before.get("tuning_mode")


def test_tr_reports_whether_a_session_is_open(ds, client):
    """A reading taken with no session is stale by definition -- control.py
    only refreshes the tuning blob in tuning mode -- so the flag rides along
    and the page can say so instead of showing a frozen number."""
    seed_tr({"Grill": 51234})
    assert client.get("/api/tuner/tr?probe=Grill").get_json()["data"]["tuning"] is False


def points(high=(400, 1200), medium=(250, 6000), low=(100, 40000)):
    """A real thermistor triple that solves to non-zero coefficients and a full
    20-point chart. Verified against blueprints/tuner/tuner.py on 2026-07-28."""
    return [
        {"segment": "High", "temp": high[0], "trohms": high[1]},
        {"segment": "Medium", "temp": medium[0], "trohms": medium[1]},
        {"segment": "Low", "temp": low[0], "trohms": low[1]},
    ]


def test_coefficients_are_computed_from_three_points(ds, client):
    body = client.post("/api/tuner/coefficients", json={"points": points()}).get_json()
    assert body["result"] == "OK"
    data = body["data"]
    for key in ("a", "b", "c"):
        assert isinstance(data[key], float)
    #  Not all three zero: that tuple is exactly what calc_shh_coefficients
    #  returns from its bare `except:`, and Flask fed it straight to the save
    #  form. A 200 carrying (0, 0, 0) is the bug this endpoint refuses to have.
    assert (data["a"], data["b"], data["c"]) != (0, 0, 0)


def test_an_uncomputable_set_is_refused_rather_than_saved_as_zeros(ds, client):
    """calc_shh_coefficients swallows every exception and returns (0, 0, 0).
    Two identical resistances divide by zero in step 3."""
    resp = client.post(
        "/api/tuner/coefficients",
        json={"points": points(high=(400, 5000), medium=(250, 5000))},
    )
    assert resp.status_code == 422
    assert resp.get_json()["message"] == "uncomputable"


def test_the_chart_is_reported_as_missing_rather_than_empty(ds, client):
    """calc_shh_chart abandons the whole series the moment temp_to_tr throws --
    which its own docstring says is common. An empty list and a list that
    genuinely has no points look identical, so the flag carries the difference.
    """
    body = client.post("/api/tuner/coefficients", json={"points": points()}).get_json()
    data = body["data"]
    assert isinstance(data["chart"], list)
    assert data["chart_ok"] == (len(data["chart"]) > 0)


def test_all_three_segments_are_required(ds, client):
    resp = client.post("/api/tuner/coefficients", json={"points": points()[:2]})
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "points"


def test_a_non_numeric_reading_is_refused(ds, client):
    bad = points()
    bad[0]["trohms"] = "lots"
    resp = client.post("/api/tuner/coefficients", json={"points": bad})
    assert resp.status_code == 400


def test_coefficients_does_not_write_control(ds, client):
    before = control_now()
    client.post("/api/tuner/coefficients", json={"points": points()})
    assert control_now()["mode"] == before["mode"]


PROFILE = {"name": "Test Probe", "a": 0.0007343140544, "b": 0.0002157437229, "c": 0.0000000951568577}


def test_saving_a_profile_stores_it_under_a_new_id(ds, client):
    from common.datastore_accessors import read_settings

    body = client.post("/api/tuner/profile", json=PROFILE).get_json()
    assert body["result"] == "OK"
    new_id = body["data"]["id"]
    assert body["data"]["applied"] is None

    profiles = read_settings()["probe_settings"]["probe_profiles"]
    assert profiles[new_id]["name"] == "Test Probe"
    assert profiles[new_id]["A"] == PROFILE["a"]
    assert profiles[new_id]["id"] == new_id


def test_applying_a_profile_attaches_it_to_the_probe(ds, client):
    from common.datastore_accessors import read_settings

    label = read_settings()["probe_settings"]["probe_map"]["probe_info"][0]["label"]
    body = client.post("/api/tuner/profile", json={**PROFILE, "apply_to": label}).get_json()
    assert body["data"]["applied"] == label

    probe_info = read_settings()["probe_settings"]["probe_map"]["probe_info"]
    attached = next(p for p in probe_info if p["label"] == label)
    assert attached["profile"]["id"] == body["data"]["id"]


def test_applying_to_an_unknown_probe_is_refused_and_saves_nothing(ds, client):
    """Flask's _settings_addprofile loops looking for the label and silently
    does nothing when it does not match -- reporting success for a profile that
    was saved but never applied."""
    from common.datastore_accessors import read_settings

    before = set(read_settings()["probe_settings"]["probe_profiles"])
    resp = client.post("/api/tuner/profile", json={**PROFILE, "apply_to": "Nonexistent"})
    assert resp.status_code == 404
    assert set(read_settings()["probe_settings"]["probe_profiles"]) == before


@pytest.mark.parametrize("field", ["name", "a", "b", "c"])
def test_every_field_is_required(ds, client, field):
    payload = dict(PROFILE)
    del payload[field]
    resp = client.post("/api/tuner/profile", json=payload)
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == field


def test_a_blank_name_is_refused(ds, client):
    resp = client.post("/api/tuner/profile", json={**PROFILE, "name": "   "})
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "name"


def test_a_non_numeric_coefficient_is_refused(ds, client):
    resp = client.post("/api/tuner/profile", json={**PROFILE, "a": "nope"})
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "a"


def test_saving_does_not_write_control(ds, client):
    before = control_now()
    client.post("/api/tuner/profile", json=PROFILE)
    assert control_now()["mode"] == before["mode"]
