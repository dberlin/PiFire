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
- `export` (CSV export with actually-recorded history data -- the
  previously-latent empty-history crash in `prepare_csv()` is now covered
  directly by tests/unit/common/test_prepare_csv.py and at the route level
  by tests/web/test_history_export_route.py, neither of which need this
  Playwright harness)

`cookfile` (open/delete/download) -- security hardening coverage
------------------------------------------------------------------
`_history_cookfile`'s `delcookfile`/`opencookfile`/`dlcookfile` branches used
to build paths with a hardcoded `"./history/"` prefix (ignoring the
configured `HISTORY_FOLDER`) and no validation of the form-supplied
filename, so `delcookfile=../../some/file` (or `dlcookfile=...`) could
delete/serve a file anywhere `os.remove`/`send_file` could reach --
classic path traversal. Fixed via `_safe_history_path()` (a realpath-
containment check, not `secure_filename` -- cook titles can contain spaces
and other characters `secure_filename` would mangle, breaking legitimate
deletes). The tests below (`_isolated_history_folder` fixture, an
independent per-test temp dir separate from the module-scoped one in
test_page_cookfile.py) cover: a legitimate delete still works, and all
three actions reject a `../`-style traversal attempt without touching
anything outside the configured history folder.
"""

import os
import tempfile

import pytest

from tests.web.conftest import apply_control, read_settings_from_server, requires_chromium

pytestmark = requires_chromium


@pytest.fixture
def _isolated_history_folder(live_server):
    """Per-test isolated HISTORY_FOLDER, patched the same way
    test_page_cookfile.py's module-scoped fixture does (app.config +
    the two file_mgmt module-level HISTORY_FOLDER constants), but
    function-scoped here since these tests create/delete files and must
    not leak state between each other."""
    import shutil

    from app import app as flask_app
    import file_mgmt.cookfile as cookfile_mod
    import file_mgmt.common as common_mod

    tmp_dir = tempfile.mkdtemp(prefix="pifire_test_history_traversal_")
    history_dir = os.path.join(tmp_dir, "history") + "/"
    os.makedirs(history_dir, exist_ok=True)

    orig_app_folder = flask_app.config["HISTORY_FOLDER"]
    orig_cookfile_mod_folder = cookfile_mod.HISTORY_FOLDER
    orig_common_mod_folder = common_mod.HISTORY_FOLDER
    flask_app.config["HISTORY_FOLDER"] = history_dir
    cookfile_mod.HISTORY_FOLDER = history_dir
    common_mod.HISTORY_FOLDER = history_dir

    yield tmp_dir, history_dir

    flask_app.config["HISTORY_FOLDER"] = orig_app_folder
    cookfile_mod.HISTORY_FOLDER = orig_cookfile_mod_folder
    common_mod.HISTORY_FOLDER = orig_common_mod_folder
    shutil.rmtree(tmp_dir, ignore_errors=True)


def test_delcookfile_deletes_file_within_history_folder(live_server, page, _isolated_history_folder):
    """Behavior-preserving: a legitimate delcookfile (plain filename inside
    the configured HISTORY_FOLDER) still deletes the file and redirects
    back to /history."""
    _tmp_dir, history_dir = _isolated_history_folder
    target = os.path.join(history_dir, "Real-CookFile.pifire")
    with open(target, "w") as f:
        f.write("not a real pifire zip, just needs to exist")
    assert os.path.isfile(target)

    resp = page.request.post(
        f"{live_server}/history/cookfile", form={"delcookfile": "Real-CookFile.pifire"}, max_redirects=0
    )

    assert resp.status in (301, 302, 303, 307, 308)
    assert not os.path.isfile(target)


def test_delcookfile_rejects_path_traversal(live_server, page, _isolated_history_folder):
    """Security fix: `delcookfile=../<sentinel>` must NOT resolve outside
    the configured HISTORY_FOLDER. Before the fix, `_history_cookfile` did
    `os.remove("./history/" + response["delcookfile"])` with no validation
    at all -- a traversal payload would have deleted the sentinel file
    created here, which lives outside the isolated history dir entirely."""
    tmp_dir, _history_dir = _isolated_history_folder
    sentinel = os.path.join(tmp_dir, "sentinel.pifire")
    with open(sentinel, "w") as f:
        f.write("must survive the request")
    assert os.path.isfile(sentinel)

    resp = page.request.post(
        f"{live_server}/history/cookfile", form={"delcookfile": "../sentinel.pifire"}, max_redirects=0
    )

    # The handler still redirects back to /history (its existing no-op /
    # error response shape) -- the load-bearing assertion is that the
    # traversal target was never touched.
    assert resp.status in (301, 302, 303, 307, 308)
    assert os.path.isfile(sentinel), "traversal payload deleted a file outside HISTORY_FOLDER"


def test_opencookfile_rejects_path_traversal(live_server, page, _isolated_history_folder):
    """`opencookfile=../<sentinel>` must not be read from outside
    HISTORY_FOLDER. Before the fix, `HISTORY_FOLDER + response["opencookfile"]`
    had no containment check (only HISTORY_FOLDER was already applied, not
    the hardcoded-path bug delcookfile/dlcookfile had) -- but the traversal
    itself was still unvalidated. Renders the existing cferror.html error
    path rather than leaking the sentinel's contents."""
    tmp_dir, _history_dir = _isolated_history_folder
    sentinel = os.path.join(tmp_dir, "sentinel.pifire")
    with open(sentinel, "w") as f:
        f.write("SECRET-SENTINEL-CONTENT-should-not-be-served")

    resp = page.request.post(f"{live_server}/history/cookfile", form={"opencookfile": "../sentinel.pifire"})

    assert resp.status == 200
    body = resp.text()
    assert "SECRET-SENTINEL-CONTENT-should-not-be-served" not in body
    assert "Cook File Loading Error" in body or "Cook File" in body


def test_dlcookfile_rejects_path_traversal(live_server, page, _isolated_history_folder):
    """`dlcookfile=../<sentinel>` must not be served via send_file. Before
    the fix this had the same hardcoded `"./history/"` + no-validation bug
    as delcookfile."""
    tmp_dir, _history_dir = _isolated_history_folder
    sentinel = os.path.join(tmp_dir, "sentinel.pifire")
    with open(sentinel, "w") as f:
        f.write("SECRET-SENTINEL-CONTENT-should-not-be-served")

    resp = page.request.post(
        f"{live_server}/history/cookfile", form={"dlcookfile": "../sentinel.pifire"}, max_redirects=0
    )

    assert resp.status in (301, 302, 303, 307, 308)
    body = resp.text()
    assert "SECRET-SENTINEL-CONTENT-should-not-be-served" not in body


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
    assert body["time_labels"] == []
    for series in body["chart_data"]:
        # prepare_chartdata()'s "no history yet" branch (list_length == 0)
        # returns EMPTY point lists. It used to emit one synthetic zero-value
        # point per series so Chart.js drew a flat baseline -- but that
        # baseline was a temperature reading nobody took, indistinguishable
        # from a real 0. history.js assigns time_labels/chart_data straight
        # into Chart.js (see blueprints/history/static/history/js/history.js
        # around line 240), so empty arrays render an empty chart instead.
        assert series["data"] == []
        # The dataset itself survives -- legend and colors intact, no points.
        assert series["label"]
