"""Browser regression tests for two related nested-modal bugs: closing the
I2C "Discover" modal (nested inside the wizard's Add Probe Device modal)

1. used to strip Bootstrap's `modal-open` class off <body> unconditionally,
   breaking the outer modal's own scrolling and letting the page behind it
   scroll instead.
2. clicking the nested modal's X/Close ([data-dismiss="modal"]) button used
   to bubble up and close the OUTER modal too, discarding the in-progress
   Add/Edit Probe Device form.

See blueprints/probeconfig/static/probeconfig/js/probeconfig.js for the
fixes (a delegated `hidden.bs.modal` handler that restores `modal-open` if
another modal is still shown, and a capture-phase click interceptor that
stops a nested modal's dismiss click from reaching an ancestor modal).

Runs a real Flask dev server in a background thread against an isolated
temp SQLite DB (same pattern as test_webapp_sqlite.py's module-level
seeding) and drives it with a real Chromium browser via pytest-playwright.
Requires Chromium to be installed once via `uv run playwright install
chromium`; skips cleanly (rather than erroring) if it isn't.
"""

import os
import tempfile
import threading

import pytest

_PLAYWRIGHT_UNAVAILABLE_REASON = None
try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as _pw:
        if not os.path.exists(_pw.chromium.executable_path):
            _PLAYWRIGHT_UNAVAILABLE_REASON = f"chromium not installed at {_pw.chromium.executable_path!r} -- run `uv run playwright install chromium`"
except Exception as exc:  # pragma: no cover - only exercised if playwright itself is unusable here
    _PLAYWRIGHT_UNAVAILABLE_REASON = f"playwright unavailable: {exc}"

pytestmark = pytest.mark.skipif(_PLAYWRIGHT_UNAVAILABLE_REASON is not None, reason=_PLAYWRIGHT_UNAVAILABLE_REASON or "")


@pytest.fixture(scope="module")
def live_server():
    """Runs the real app.py Flask app on a background thread against an
    isolated temp SQLite DB, and returns its base URL. Mirrors the DB
    isolation pattern in test_webapp_sqlite.py (PIFIRE_DB_PATH env var +
    datastore._reset_for_tests), but via a real HTTP server instead of
    Flask's test client, since a browser needs an actual socket to hit."""
    from werkzeug.serving import make_server

    from common import datastore
    from common.common import WriteKind
    from common.datastore_accessors import (
        write_control,
        write_pellets_store,
        write_settings_store,
        write_status,
        read_status,
    )
    from common.defaults import default_control, default_pellets, default_settings

    tmp_dir = tempfile.mkdtemp(prefix="pifire_test_wizard_modal_")
    db_path = os.path.join(tmp_dir, "wizard_modal_e2e.db")
    os.environ["PIFIRE_DB_PATH"] = db_path
    datastore._reset_for_tests(db_path)
    datastore.init()

    settings = default_settings()
    settings["globals"]["grill_name"] = "E2E Modal Scroll Test Grill"
    settings["globals"]["first_time_setup"] = False
    write_settings_store(settings)
    write_pellets_store(default_pellets())
    write_status(read_status(init=True))
    write_control(default_control(), WriteKind.OVERWRITE, origin="test-wizard-modal-e2e")

    from app import app as flask_app

    srv = make_server("127.0.0.1", 0, flask_app)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        srv.shutdown()
        thread.join(timeout=5)
        datastore._reset_for_tests(None)
        os.environ.pop("PIFIRE_DB_PATH", None)


def _open_add_probe_device_modal_with_i2c_discover(page, base_url):
    """Navigates to the wizard's Probe Input tab, opens the Add Probe Device
    modal, selects a module with an i2c_bus_num setting (which injects a
    Discover button + nested modal), and pads the outer modal so it actually
    needs to scroll -- matching a real device-config form with many fields.
    Returns the inner Discover modal's element id."""
    page.goto(f"{base_url}/wizard/", wait_until="networkidle")

    page.click("#v-pills-probes-tab")
    page.wait_for_selector("#v-pills-probes.active")

    page.click("#addProbeDevice")
    page.wait_for_selector("#addProbeDeviceModal.show")

    page.select_option("#addProbeDeviceSelect", "mcp9600_adafruit")
    page.wait_for_selector('#addProbeDeviceField button:has-text("Discover")')

    page.evaluate("""() => {
		const filler = document.createElement('div');
		filler.style.height = '2000px';
		document.querySelector('#addProbeDeviceModal > .modal-dialog > .modal-content > .modal-body').appendChild(filler);
	}""")

    page.locator('#addProbeDeviceField button:has-text("Discover")').first.click()
    page.wait_for_selector('.modal.show[id^="i2c_"]')
    inner_modal_id = page.evaluate("document.querySelector('.modal.show[id^=\"i2c_\"]').id")

    # Bootstrap's fade-in transition + hide() no-ops while `_isTransitioning`
    # is true, so let the modal finish showing before dismissing it.
    page.wait_for_timeout(500)

    return inner_modal_id


def test_closing_nested_i2c_discover_modal_keeps_outer_modal_scrollable(live_server, page):
    inner_modal_id = _open_add_probe_device_modal_with_i2c_discover(page, live_server)

    # Exactly what selectI2CBus(value, itemID) does after a user picks a
    # discovered bus: $(modal).modal('hide') called directly (NOT via the
    # [data-dismiss="modal"] X/Close button, which has a separate,
    # already-known bug of bubbling and closing the outer modal too).
    page.evaluate("(id) => window.jQuery('#' + id).modal('hide')", inner_modal_id)
    page.wait_for_function("(id) => !document.getElementById(id).classList.contains('show')", arg=inner_modal_id)
    page.wait_for_timeout(200)

    assert page.evaluate("document.querySelector('#addProbeDeviceModal').classList.contains('show')"), (
        "outer Add Probe Device modal should still be open after only the inner Discover modal was hidden"
    )
    assert page.evaluate("document.body.classList.contains('modal-open')"), (
        "body should keep the 'modal-open' class while the outer modal is still shown"
    )
    assert page.evaluate("getComputedStyle(document.querySelector('#addProbeDeviceModal')).overflowY") == "auto", (
        "outer modal should remain internally scrollable (overflow-y: auto)"
    )

    # The real-world symptom: with modal-open incorrectly stripped, wheel
    # input over the modal scrolls the page behind it instead of the modal.
    box = page.locator("#addProbeDeviceModal > .modal-dialog > .modal-content > .modal-body").bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.wheel(0, 300)
    page.wait_for_timeout(150)
    body_scroll_top = page.evaluate("document.documentElement.scrollTop || document.body.scrollTop")
    assert body_scroll_top == 0, f"page behind the modal scrolled ({body_scroll_top}px) instead of the modal itself"


def test_closing_nested_i2c_discover_modal_via_close_button_leaves_outer_modal_open(live_server, page):
    inner_modal_id = _open_add_probe_device_modal_with_i2c_discover(page, live_server)

    # Click the inner modal's own X (close) button in its header -- the
    # [data-dismiss="modal"] path, not the direct $(modal).modal('hide')
    # call covered by the other test in this file.
    page.click(f'#{inner_modal_id} .modal-header [data-dismiss="modal"]')
    page.wait_for_function("(id) => !document.getElementById(id).classList.contains('show')", arg=inner_modal_id)
    page.wait_for_timeout(200)

    assert page.evaluate("document.querySelector('#addProbeDeviceModal').classList.contains('show')"), (
        "outer Add Probe Device modal should still be open -- clicking the nested "
        "Discover modal's X button should only close that modal, not bubble up "
        "and close the outer Add Probe Device modal too"
    )
    assert page.evaluate("document.body.classList.contains('modal-open')"), (
        "body should keep the 'modal-open' class while the outer modal is still shown"
    )
