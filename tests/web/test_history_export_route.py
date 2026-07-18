"""Task 4 (bugfix-latent-bugs) route-level companion to
`tests/unit/common/test_prepare_csv.py`: drives `GET /history/export`
(`blueprints/history/routes.py`'s `history_page` `export` action) through a
real Flask test client against an isolated, empty datastore, proving the
route itself no longer 500s -- not just the underlying `prepare_csv()` call.

Uses the same lightweight `app.test_client()` pattern as
tests/web/test_webapp_sqlite.py (no Playwright/live server needed for a
single GET). `app.py` is a process-wide singleton Flask app already
imported by other test modules; importing it here is safe regardless of
collection order.
"""

from app import app as flask_app


def test_history_export_route_returns_200_with_empty_history(ds):
    flask_app.config.update(TESTING=True)
    client = flask_app.test_client()

    resp = client.get("/history/export")

    assert resp.status_code == 200
    assert resp.data.decode() == "No Data\n"
