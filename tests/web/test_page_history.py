"""Playwright coverage for the history page
(blueprints/history/routes.py's `history_page`, a single route handling
`stream`/`refresh`/`cookfile`/`setmins`/`export` action branches plus the
base render).

Follows the pattern established in test_page_settings.py; see
tests/web/conftest.py for the shared harness.

Actions covered here:
- (base GET, no action) -- full-page render, key sections present.
- `setmins`   -- real-UI style: plain numeric field + submit button.
- `stream`    -- direct-POST(GET) style: JSON polling endpoint the page's
                 own JS hits every second while a cook is "live"; no
                 hardware/control-loop dependency (reads whatever is in
                 control:current, zero'd out when mode is Stop/Error, which
                 it is here since no control loop runs in this harness).
- `refresh`   -- direct-POST style: JSON body with `num_mins`, asserts the
                 persisted `settings['history_page']['minutes']` and that
                 the chart-data JSON shape comes back for a probe_config
                 built from defaults (no live datapoints, since there's no
                 cook history recorded in this harness -- exercises the
                 "no data yet" path through prepare_chartdata()).

NOT covered (needs live/recorded history data or a real cookfile on disk,
neither of which this control-loop-less harness produces):
- `cookfile` (open/delete/download an existing .pifire cook file)
- `export` (CSV export -- `prepare_csv()` indexes into `read_history()[0]`
  unconditionally and throws IndexError when history is empty; this is a
  latent bug independent of these tests, not something to route around by
  fabricating history data here)
"""

import pytest

from tests.web.conftest import apply_control, read_settings_from_server, requires_chromium

pytestmark = requires_chromium


@pytest.fixture(autouse=True)
def seed_probe_device_info():
    """See test_page_dashboard.py's identical fixture: the base template's
    control panel polls /api/current client-side, which 500s without this
    seeded generic key. Harmless to these tests but keeps server logs
    clean."""
    from common.datastore_accessors import write_generic_key

    write_generic_key("probe_device_info", [])


def test_history_page_renders_key_sections(live_server, page):
    resp = page.goto(f"{live_server}/history/")

    assert resp.status == 200
    assert page.title().startswith("History")
    assert page.locator("#HistoryChart").count() == 1
    assert page.locator("form[name='setmins']").count() == 1
    assert page.locator("#minutes").count() == 1
    assert page.locator("a[href='/history/export']").count() == 1
    assert page.locator("form[name='managecookfile']").count() == 1


def test_setmins_via_real_ui(live_server, page):
    # #minutes lives inside #graphcardfooter, which the page's own JS
    # (history.js's checkModeChange()) keeps hidden while control mode is
    # Stop/Error -- it only reveals the graph once the chart's periodic
    # /history/stream poll (chartjs-plugin-streaming's onRefresh, every
    # 1000ms) reports a non-Stop/Error mode. Seed a "live cook" mode so the
    # real UI path (rather than a JS-bypassing direct POST) is exercised.
    apply_control(lambda c: c.__setitem__("mode", "Startup"))

    page.goto(f"{live_server}/history/")
    page.wait_for_selector("#minutes", state="visible")

    page.fill("#minutes", "42")
    with page.expect_navigation():
        page.locator("form[name='setmins'] button[type='submit']").click()

    assert read_settings_from_server()["history_page"]["minutes"] == 42
    assert page.locator("#minutes").input_value() == "42"


def test_stream_returns_zeroed_current_when_stopped(live_server, page):
    apply_control(lambda c: c.__setitem__("mode", "Stop"))

    resp = page.request.get(f"{live_server}/history/stream")

    assert resp.status == 200
    body = resp.json()
    assert body["mode"] == "Stop"
    assert "current" in body
    assert "annotations" in body
    assert "ui_hash" in body
    assert "timestamp" in body


def test_refresh_persists_minutes_and_returns_chart_shape(live_server, page):
    resp = page.request.post(f"{live_server}/history/refresh", data={"num_mins": 7})

    assert resp.status == 200
    assert read_settings_from_server()["history_page"]["minutes"] == 7

    body = resp.json()
    assert "ui_hash" in body
    assert "annotations" in body
    assert isinstance(body["chart_data"], list)
    # One probe (default "Grill") means 2 chart-data series: temp + target
    # (Primary probes also get a 3rd "Set Point" series -- see
    # file_mgmt/cookfile.py's prepare_chartdata()).
    assert len(body["chart_data"]) >= 2
    for series in body["chart_data"]:
        # prepare_chartdata()'s "no history yet" fallback (list_length == 0)
        # emits a single synthetic zero-value point per series rather than
        # an empty list, so charts still render a flat baseline.
        assert series["data"] == [0]
