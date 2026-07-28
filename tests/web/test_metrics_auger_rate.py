"""The legacy /metrics page must estimate pellet usage from the grill's own
auger rate.

See docs/superpowers/plans/2026-07-28-react-metrics-page.md.
"""

from unittest.mock import patch

import pytest

from app import app as flask_app


@pytest.fixture
def client(ds):
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def test_metrics_page_passes_the_configured_auger_rate(ds, client):
    import blueprints.metrics.routes as metrics_routes
    from common.datastore_accessors import read_settings, write_settings

    settings = read_settings()
    settings["globals"]["augerrate"] = 0.9
    write_settings(settings)

    #  Patched on the IMPORTING module's own globals: the route bound the name
    #  at import time, so patching common.common would leave it pointing at the
    #  real function and the assertion would never fire.
    with patch.object(metrics_routes, "process_metrics", return_value=[]) as spy:
        assert client.get("/metrics/").status_code == 200

    assert spy.call_args.kwargs["augerrate"] == 0.9
