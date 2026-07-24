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
    assert resp.get_json()["message"] == "invalid_minutes"
