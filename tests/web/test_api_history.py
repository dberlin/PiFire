import pytest

from app import app as flask_app


@pytest.fixture
def client(ds):
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def test_chart_returns_the_series_payload(ds, client):
    resp = client.get("/api/history/chart?minutes=10")
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body) >= {"time_labels", "chart_data", "probe_mapper", "annotations", "minutes"}
    assert body["minutes"] == 10
    assert isinstance(body["chart_data"], list)
    assert isinstance(body["time_labels"], list)


def test_chart_defaults_to_the_saved_window(ds, client):
    body = client.get("/api/history/chart").get_json()
    assert body["minutes"] >= 1


def test_chart_is_read_only(ds, client):
    """Unlike the legacy POST /history/refresh, asking for a window must NOT
    rewrite the user's saved history_page.minutes setting."""
    from common.datastore_accessors import read_settings

    before = read_settings()["history_page"]["minutes"]
    client.get("/api/history/chart?minutes=999")
    assert read_settings()["history_page"]["minutes"] == before


def test_chart_rejects_a_bad_window(ds, client):
    resp = client.get("/api/history/chart?minutes=notanumber")
    assert resp.status_code == 400


@pytest.mark.parametrize("minutes", [0, -1, -999])
def test_chart_rejects_a_non_positive_window(ds, client, minutes):
    """A zero or negative window is a client bug, not a request for "all data".

    Pinned rather than merely reasoned about: the arithmetic downstream
    (num_items = minutes * SAMPLES_PER_MINUTE, and read_history's
    rows[-num_items:]) treats a negative count as a slice from the front, so
    letting one through would silently return the WRONG END of the history.
    """
    resp = client.get(f"/api/history/chart?minutes={minutes}")
    assert resp.status_code == 400
    assert resp.get_json()["message"] == "invalid_minutes"


def test_chart_survives_an_absurdly_large_window(ds, client):
    """A window far larger than the stored history must clamp, not explode.

    read_history's rows[-num_items:] slice is already safe for an oversized
    count, but nothing pinned that, so a future change to the windowing math
    could start raising here without any test noticing.
    """
    resp = client.get("/api/history/chart?minutes=100000000")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["minutes"] == 100000000
    assert isinstance(body["time_labels"], list)
