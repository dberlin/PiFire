"""The JSON metrics surface the React /metrics page reads.

Read-only by construction: this blueprint has no POST, so nothing here can
change the grill. See docs/superpowers/plans/2026-07-28-react-metrics-page.md.
"""

import pytest

from app import app as flask_app

START = 1_700_000_000_000


@pytest.fixture
def client(ds):
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def seed(mode="Smoke", augerontime=100, duration_ms=60_000):
    """Insert one metrics row with a deterministic span.

    append_metric stamps its OWN starttime (time.time() * 1000) and id, so
    those two cannot be seeded through it -- they are written back afterwards
    with update_metrics, which amends the last row in place. Seeding them in
    the dict handed to append_metric looks like it works and does nothing.
    """
    from common.datastore_accessors import append_metric, update_metrics
    from common.defaults import default_metrics

    row = default_metrics()
    row["mode"] = mode
    row["augerontime"] = augerontime
    row["primary_setpoint"] = 225
    append_metric(row)
    update_metrics({"starttime": START, "endtime": 0 if duration_ms == 0 else START + duration_ms})


def test_listing_is_empty_when_nothing_has_been_recorded(ds, client):
    from common.datastore_accessors import flush_metrics

    flush_metrics()
    body = client.get("/api/metrics").get_json()
    assert body["result"] == "OK"
    assert body["data"]["metrics"] == []


def test_listing_returns_the_processed_record(ds, client):
    from common.datastore_accessors import flush_metrics

    flush_metrics()
    seed()

    body = client.get("/api/metrics").get_json()
    (record,) = body["data"]["metrics"]

    assert record["mode"] == "Smoke"
    assert record["primary_setpoint"] == 225
    #  The derived columns process_metrics writes, which is the whole reason
    #  this endpoint does not simply return read_all_metrics().
    #
    #  "60 s", not "1 m 0 s": the minute branch is `if seconds > 60`, so a span
    #  of exactly 60 000 ms falls to the seconds form.
    assert record["timeinmode"] == "60 s"
    assert record["augerontime_c"] == "100 s"
    assert record["estusage_m"] == "30 grams"
    assert record["estusage_i"].endswith("ounces)")
    #  bool, not 0/1: _metrics_row_to_dict coerces it, and the TypeScript type
    #  in web-react/src/helpers/metrics/metricsTypes.ts is written from this.
    assert record["smokeplus"] is True


def test_a_running_mode_reports_endtime_c_as_zero(ds, client):
    """The one non-obvious member of the payload.

    process_metrics writes a "%H:%M:%S" STRING into endtime_c for a finished
    mode and the integer 0 for a running one. The React client types it
    `string | number` because of this; pinned here so the union cannot be
    "simplified" away on either side without a red test.
    """
    from common.datastore_accessors import flush_metrics

    flush_metrics()
    seed(duration_ms=0)

    (record,) = client.get("/api/metrics").get_json()["data"]["metrics"]
    assert record["endtime_c"] == 0
    assert record["timeinmode"] == "Active"


def test_listing_carries_units_and_auger_rate(ds, client):
    from common.datastore_accessors import read_settings, write_settings

    settings = read_settings()
    settings["globals"]["units"] = "C"
    settings["globals"]["augerrate"] = 0.9
    write_settings(settings)

    data = client.get("/api/metrics").get_json()["data"]
    assert data["units"] == "C"
    assert data["augerrate"] == 0.9


def test_estimates_use_the_configured_auger_rate(ds, client):
    from common.datastore_accessors import flush_metrics, read_settings, write_settings

    flush_metrics()
    settings = read_settings()
    settings["globals"]["augerrate"] = 0.6
    write_settings(settings)
    seed()

    (record,) = client.get("/api/metrics").get_json()["data"]["metrics"]
    assert record["estusage_m"] == "60 grams"


def test_the_generic_api_catchall_does_not_swallow_this_path(ds, client):
    """blueprints/api registers /api/<action> for GET and POST, which matches
    the literal path /api/metrics.

    Werkzeug sorts static rules ahead of converter rules, so the specific rule
    should win -- but blueprints/api_admin/routes.py's module docstring records
    a case where a method mismatch made a request fall through to that
    catch-all and 404 from somewhere else entirely. Assert on the endpoint
    name, not just the status: a 200 from the catch-all would pass a status
    check and return a completely different body.
    """
    with flask_app.test_request_context("/api/metrics"):
        from flask import request

        assert request.endpoint == "api_metrics_bp.metrics_listing"


def test_the_surface_registers_no_write():
    """No POST reaches this blueprint. If one ever does, it must be a
    deliberate decision with its own test, not an inherited `methods` list.

    Asserted against the url_map rather than by POSTing: blueprints/api's
    /api/<action> rule accepts POST and would answer a POST to /api/metrics
    with something of its own, so a status check here would be measuring that
    blueprint's behaviour, not this one's.
    """
    writes = [
        rule
        for rule in flask_app.url_map.iter_rules()
        if rule.endpoint.startswith("api_metrics_bp.") and "POST" in rule.methods
    ]
    assert writes == []
