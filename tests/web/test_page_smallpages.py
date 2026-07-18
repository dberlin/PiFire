"""Playwright coverage for five small blueprint pages: events, logs,
metrics, manual, and manifest (blueprints/{events,logs,metrics,manual,
manifest}/routes.py).

See tests/web/test_page_settings.py for the reference pattern and
tests/web/conftest.py for the shared harness (live_server, read-back
helpers, precondition seeding). Each page here is small (one or two
routes, few branches) so all five share this one module rather than
getting a file each.

Per-page notes on seeding and interaction style:

- **events** (`events_page`): the base GET only renders an empty
  `#events_list` div -- the actual paginated event table is fetched by
  events.js via a jQuery `.load()` POST (`eventslist` in the form) on
  page-ready and every 4s after. Events themselves live in a real,
  gitignored, un-isolated file (`./logs/events.log`, hardcoded in
  `common.common.write_log`/`create_logger` -- NOT under the
  per-module temp-SQLite db this harness otherwise gives each test
  file). Tests seed by appending directly to that file (see
  `_append_event_line`) rather than through `common.common.write_log`:
  `write_log`'s `create_logger()` guards FileHandler setup with `if not
  logger.hasHandlers():`, but `Logger.hasHandlers()` is True whenever
  *any ancestor* logger (including the root logger) has a handler, not
  just the logger itself -- and pytest's own logging plugin attaches
  handlers to the root logger before any test runs. Under pytest that
  guard is therefore always already-satisfied and the "events" logger's
  real `FileHandler` never gets attached, so `write_log()` silently
  never reaches the file (messages only flow to pytest's captured
  handlers) -- a real latent bug in `common/common.py`'s
  `create_logger()` (it should check `logger.handlers`, not
  `logger.hasHandlers()`), discovered while writing this module; see
  the task report. Each test uses a random per-test marker string so
  assertions are order-independent regardless of how much real log
  history has accumulated on the machine; nothing is truncated
  afterward (truncating is `admin`'s `clearevents` action, already
  covered in test_page_admin.py).
- **logs** (`logs_page`): lists `os.listdir(Config.LOGS_FOLDER)` (also
  the real `./logs/` dir, not test-isolated) filtered by
  `common.app.allowed_file`. Tests create their own uniquely-named
  `.log` file under that real directory as a precondition and remove
  it in a `finally` block -- unlike events.log, this file has no
  reason to exist afterward.
- **metrics** (`metrics_page`): metrics live in the per-module
  temp-SQLite `metrics` table (`common.datastore_accessors.read_metrics`/
  `write_metrics`), so seeding here IS properly test-isolated. Each
  test flushes (`write_metrics(flush=True)`) before seeding so it does
  not depend on execution order relative to the others.
- **manual** (`manual_page`): the page itself only renders a static
  shell (mode toggle button + the shared bottom control-panel
  partial); the actual manual-output actions
  (`/api/set/manual/<pin>/<value>`, `/api/set/mode/manual`) are fired
  by `blueprints/manual/static/manual/js/manual.js` and
  `static/js/control_panel.js` via fire-and-forget jQuery `$.ajax`
  calls (no page navigation), so real-UI tests wrap each click in
  `page.expect_response(...)` to know the POST has landed before
  reading back control state, then `drain_control_writes()` (the
  underlying `write_control` call uses the default `WriteKind.MERGE`)
  before asserting via `read_control_from_server()`. Per the task
  brief: the control loop isn't running in this harness, so
  `control['manual']['change']`/`['output']` are asserted as the flags
  a real control-loop tick would consume -- they are never actually
  drained back to `False` here (see `controller/runtime/modes/base.py`
  for where that happens in production).
- **manifest** (`manifest`): tiny, stateless GET; JSON body assertions
  plus a Content-Type header assertion (see that test's docstring).
"""

import json
import os
import uuid

from config import Config

from tests.web.conftest import (
    apply_control,
    apply_settings,
    drain_control_writes,
    read_control_from_server,
    requires_chromium,
)

pytestmark = requires_chromium

LOG_DIR = Config.LOGS_FOLDER


def _append_event_line(message, level="INFO"):
    """Append one line to the real events.log file directly, in the same
    shape common.common.write_log's logger produces ("<date> <time> <tz>
    [<LEVEL>] <message>\\n"), bypassing write_log/create_logger -- see the
    module docstring for why write_log itself doesn't reach the file
    under pytest."""
    with open(os.path.join(LOG_DIR, "events.log"), "a") as f:
        f.write(f"2026-01-01 00:00:00 +0000 [{level}] {message}\n")


# --- events --------------------------------------------------------------


def test_events_page_renders_key_sections(live_server, page):
    resp = page.goto(f"{live_server}/events/")

    assert resp.status == 200
    assert page.title().startswith("Events")
    assert page.locator("#events_list").count() == 1
    assert page.get_by_text("Event Log").count() == 1


def test_events_list_via_real_ui(live_server, page):
    """events.js loads `#events_list` on page-ready via a POST with
    `reverse=true` (most-recent-first) -- seed two events and confirm both
    the content and the reverse ordering land in the real, JS-driven
    render."""
    marker = f"e2e-event-{uuid.uuid4().hex[:8]}"
    _append_event_line(f"{marker} first")
    _append_event_line(f"{marker} second")

    page.goto(f"{live_server}/events/")
    page.wait_for_selector(f"text={marker} second")

    rows_text = "\n".join(page.locator("#events_list table tbody tr").all_inner_texts())
    idx_second = rows_text.find(f"{marker} second")
    idx_first = rows_text.find(f"{marker} first")
    assert idx_second != -1
    assert idx_first != -1
    assert idx_second < idx_first  # most-recently-written event sorts first


def test_events_list_pagination_via_direct_post(live_server, page):
    """Direct-POST the same `eventslist` shape events.js sends, to prove
    `itemsperpage`/`reverse` are honored server-side without needing to
    reconstruct the paging buttons' onclick JS."""
    marker = f"e2e-page-{uuid.uuid4().hex[:8]}"
    for i in range(3):
        _append_event_line(f"{marker} item{i}")

    resp = page.request.post(
        f"{live_server}/events/",
        form={"eventslist": "true", "page": "1", "reverse": "true", "itemsperpage": "1"},
    )
    assert resp.status == 200
    body = resp.text()
    # itemsperpage=1 + reverse=true -> only the very last-written line.
    assert f"{marker} item2" in body
    assert f"{marker} item1" not in body
    assert f"{marker} item0" not in body


# --- logs ------------------------------------------------------------------


def test_logs_page_renders_key_sections(live_server, page):
    log_name = f"e2e_test_{uuid.uuid4().hex[:8]}.log"
    log_path = os.path.join(LOG_DIR, log_name)
    with open(log_path, "w") as f:
        f.write("seed line one\nseed line two\n")

    try:
        resp = page.goto(f"{live_server}/logs/")
        assert resp.status == 200
        assert page.title().startswith("Logs")
        assert page.locator(f'#selectLog option[value="{log_name}"]').count() == 1
    finally:
        os.remove(log_path)


def test_logs_selection_via_real_ui(live_server, page):
    """Selecting a log in the `<select>` fires logs.js's `getData()`,
    which POSTs `eventslist` and loads the paginated line list into
    `#logs_list` -- drive that dropdown for real."""
    log_name = f"e2e_test_{uuid.uuid4().hex[:8]}.log"
    log_path = os.path.join(LOG_DIR, log_name)
    with open(log_path, "w") as f:
        f.write("alpha marker line\nbeta marker line\n")

    try:
        page.goto(f"{live_server}/logs/")
        with page.expect_response(lambda r: r.url.rstrip("/").endswith("/logs") and r.request.method == "POST"):
            page.select_option("#selectLog", log_name)

        page.wait_for_selector("text=alpha marker line")
        logs_text = page.locator("#logs_list").inner_text()
        assert "alpha marker line" in logs_text
        assert "beta marker line" in logs_text
    finally:
        os.remove(log_path)


def test_logs_download_via_direct_post(live_server, page):
    log_name = f"e2e_dl_{uuid.uuid4().hex[:8]}.log"
    log_path = os.path.join(LOG_DIR, log_name)
    content = "download me please\n"
    with open(log_path, "w") as f:
        f.write(content)

    try:
        resp = page.request.post(
            f"{live_server}/logs/",
            form={"download": "true", "selectLog": log_name},
        )
        assert resp.status == 200
        assert "attachment" in resp.headers.get("content-disposition", "")
        assert resp.text() == content
    finally:
        os.remove(log_path)


# --- metrics -----------------------------------------------------------


def test_metrics_page_no_data_render(live_server, page):
    from common.datastore_accessors import write_metrics

    write_metrics(flush=True)

    resp = page.goto(f"{live_server}/metrics/")
    assert resp.status == 200
    assert page.get_by_text("No Data").count() == 1
    assert page.locator('a[href="/metrics/export"]').count() == 0


def test_metrics_page_renders_seeded_modes(live_server, page):
    from common.datastore_accessors import write_metrics
    from common.defaults import default_metrics

    write_metrics(flush=True)

    hold_metric = default_metrics()
    hold_metric["mode"] = "Hold"
    hold_metric["primary_setpoint"] = 225
    write_metrics(hold_metric, new_metric=True)

    manual_metric = default_metrics()
    manual_metric["mode"] = "Manual"
    write_metrics(manual_metric, new_metric=True)

    resp = page.goto(f"{live_server}/metrics/")
    assert resp.status == 200
    assert page.get_by_text("Hold Mode").count() == 1
    assert page.get_by_text("Manual Mode").count() == 1
    assert page.locator('a[href="/metrics/export"]').count() == 1


def test_metrics_export_via_direct_post(live_server, page):
    """`/metrics/export` streams a CSV built by
    `common.app.prepare_metrics_csv` -- direct-GET it (matching the plain
    `<a href>` the page itself renders) and check the header row + a
    seeded value both landed in the body."""
    from common.datastore_accessors import write_metrics
    from common.defaults import default_metrics

    write_metrics(flush=True)
    metric = default_metrics()
    metric["mode"] = "Smoke"
    metric["augerontime"] = 42
    write_metrics(metric, new_metric=True)

    resp = page.request.get(f"{live_server}/metrics/export")
    assert resp.status == 200
    assert "attachment" in resp.headers.get("content-disposition", "")
    body = resp.text()
    assert "mode" in body  # header row (metrics_items keys)
    assert "Smoke" in body


# --- manual ------------------------------------------------------------


def test_manual_page_renders_initial_state(live_server, page):
    apply_control(lambda c: c.update({"mode": "Stop"}))

    resp = page.goto(f"{live_server}/manual/")
    assert resp.status == 200
    assert page.title().startswith("Manual")

    button = page.locator("#manual_toggle_button")
    assert button.inner_text().strip() == "Turn On Manual Mode"
    assert button.get_attribute("value") == "off"
    assert page.locator("#manual_inactive_card").is_visible()


def test_manual_toggle_enters_manual_mode_via_real_ui(live_server, page):
    """The page's own toggle button fires manual.js's fire-and-forget
    `$.ajax` POST to `/api/set/mode/manual` (no page navigation) --
    `page.expect_response` is used instead of `expect_navigation` to know
    the write has landed before reading control back."""
    apply_control(lambda c: c.update({"mode": "Stop"}))

    page.goto(f"{live_server}/manual/")
    with page.expect_response(lambda r: "/api/set/mode/manual" in r.url):
        page.locator("#manual_toggle_button").click()

    drain_control_writes()
    assert read_control_from_server()["mode"] == "Manual"
    # manual.js polls /api/get/mode every 500ms and flips the button/card
    # once it observes the new mode -- confirm the real UI catches up too.
    page.wait_for_selector("#manual_toggle_button:has-text('Turn Off Manual Mode')", timeout=3000)
    assert page.locator("#manual_active_card").is_visible()


def test_manual_outputs_toggle_on_via_real_ui(live_server, page):
    """Drive the shared control-panel's manual buttons
    (`#cp_manual_mode_{power,igniter,auger,fan}_btn`, rendered by
    `_macro_control_panel.html` on every page, including this one) for
    real. Each click POSTs `/api/set/manual/<pin>/toggle`; `_manual_toggle`
    (common/api_commands.py) resolves "toggle" against the *display*
    status's `outpins[pin]` (not control) -- pinned False here for all
    pins, so every toggle in this harness (no control loop to flip
    outpins back) turns the output ON, never alternates. That's the
    documented, expected characterization for this control-loop-less
    harness (see module docstring)."""
    apply_control(lambda c: c.update({"mode": "Manual"}))

    from common.datastore_accessors import read_status, write_status

    status = read_status()
    for pin in ("power", "igniter", "auger", "fan"):
        status["outpins"][pin] = False
    write_status(status)

    page.goto(f"{live_server}/manual/")
    assert page.locator("#manual_group").is_visible()

    for pin_name, selector in (
        ("power", "#cp_manual_mode_power_btn"),
        ("igniter", "#cp_manual_mode_igniter_btn"),
        ("auger", "#cp_manual_mode_auger_btn"),
        ("fan", "#cp_manual_mode_fan_btn"),
    ):
        with page.expect_response(lambda r, p=pin_name: f"/api/set/manual/{p}/toggle" in r.url):
            page.locator(selector).click()
        drain_control_writes()
        control = read_control_from_server()
        assert control["manual"]["change"] == pin_name
        assert control["manual"]["output"] is True


def test_manual_output_toggle_off_resets_fan_pwm_via_real_ui(live_server, page):
    """The fan's manual-toggle branch is the one branch that additionally
    resets `control['manual']['pwm']` to 100 when turned off
    (`reset_pwm_when_off=True` in `_manual_toggle`, common/api_commands.py)
    -- pin the display status's fan outpin True so this toggle resolves to
    "off" and exercises that reset."""
    apply_control(lambda c: c.update({"mode": "Manual", "manual": {**c["manual"], "pwm": 40}}))

    from common.datastore_accessors import read_status, write_status

    status = read_status()
    status["outpins"]["fan"] = True
    write_status(status)

    page.goto(f"{live_server}/manual/")
    with page.expect_response(lambda r: "/api/set/manual/fan/toggle" in r.url):
        page.locator("#cp_manual_mode_fan_btn").click()

    drain_control_writes()
    control = read_control_from_server()
    assert control["manual"]["change"] == "fan"
    assert control["manual"]["output"] is False
    assert control["manual"]["pwm"] == 100


def test_manual_pwm_via_direct_post(live_server, page):
    """`/api/set/manual/pwm/<speed>` backs the manual-mode PWM slider
    (`cpUpdatePWM()` in control_panel.js); direct-POST it like the
    settings-module exemplars do for JS-driven, non-form actions."""
    apply_control(lambda c: c.update({"mode": "Manual"}))

    resp = page.request.post(f"{live_server}/api/set/manual/pwm/55")
    assert resp.status == 201

    drain_control_writes()
    control = read_control_from_server()
    assert control["manual"]["change"] == "pwm"
    assert control["manual"]["output"] is True
    assert control["manual"]["pwm"] == 55


def test_manual_output_toggle_guarded_when_not_manual_via_direct_post(live_server, page):
    """`_cmd_set_manual` refuses manual-output changes unless mode is
    already 'Manual' or `settings['safety']['allow_manual_changes']` is
    True -- confirm the guard actually blocks the write (an ERROR result,
    control left untouched) rather than silently applying it."""
    apply_settings(lambda s: s["safety"].__setitem__("allow_manual_changes", False))
    apply_control(lambda c: c.update({"mode": "Stop", "manual": {**c["manual"], "change": False, "output": False}}))

    resp = page.request.post(f"{live_server}/api/set/manual/power/true")
    assert resp.status == 201
    assert json.loads(resp.text())["result"] == "ERROR"

    drain_control_writes()
    control = read_control_from_server()
    assert control["manual"]["change"] is False
    assert control["manual"]["output"] is False


# --- manifest ------------------------------------------------------------


def test_manifest_get_content(live_server, page):
    """Plain GET + JSON-content assertions. The route
    (`blueprints/manifest/routes.py`) sets `Content-Type` to the
    PWA-correct `application/manifest+json`, not the legacy, deprecated
    `text/cache-manifest` (the old HTML5 AppCache mimetype) it used to
    serve. See the task report."""
    resp = page.request.get(f"{live_server}/manifest/")

    assert resp.status == 200
    assert "application/manifest+json" in resp.headers.get("content-type", "")

    body = json.loads(resp.text())
    assert body["short_name"] == "PiFire"
    assert body["name"] == "PiFire - Pellet Smoker Controller"
    assert body["start_url"] == "/"
    assert body["display"] == "standalone"
    assert len(body["icons"]) == 3
    for icon in body["icons"]:
        assert icon["src"].startswith("/static/img/launcher-icon")
