"""Shared Playwright harness for tests/web/*.

This generalizes the single-purpose inline `live_server` fixture proven in
test_wizard_nested_modal_scroll.py into a reusable harness so every
blueprint page can be covered the same way: real `app.py` Flask app, real
Chromium (via pytest-playwright), isolated temp-SQLite DB per test module.

Fixture scoping decision
-------------------------
`live_server` is **module**-scoped (one live server + one temp-SQLite DB per
test *file*, shared across all tests in that file), matching the existing
wizard e2e test's precedent. Rationale:

- Session scope would mean every page's test file mutates the SAME
  datastore, so an earlier module's writes (e.g. probeconfig tests adding
  devices) could leak into a later module's settings-page assertions. That
  cross-module bleed is exactly the fragility test_webapp_sqlite.py
  documents (see its module-docstring / setup_function comment) for the
  free-function + test-client path; module-scoped live_server sidesteps it
  for the Playwright path by giving each test *file* a clean DB.
- Function scope (fresh server per test) would be safer still but far
  slower -- spinning up a werkzeug server + browser navigation per test
  adds real wall-clock cost across dozens of pages x multiple actions each.
- Within one module, tests still share state (whatever an earlier test in
  the same file wrote persists into the next). Precondition-seeding tests
  should not assume defaults; use `apply_settings`/`apply_control` (or seed
  a fresh value) to make each test's starting state explicit rather than
  relying on ordering.

`browser`/`page`: reuse pytest-playwright's built-in fixtures. No custom
wrapper is needed -- `page` (function-scoped) composes cleanly with a
module-scoped `live_server`, as already proven by
test_wizard_nested_modal_scroll.py running multiple tests against one
live_server with the plain `page` fixture.

Thread-shared datastore (the load-bearing trick)
-------------------------------------------------
`live_server` runs the real Flask app via `werkzeug.serving.make_server` on
a background **thread**, not a subprocess. Because it's a thread, it runs
inside the SAME Python process as the test function driving the browser --
so it shares the same imported `common.datastore` module and the same
underlying SQLite connection/singleton state. A test can therefore:

    page.click("...")  # drives the real UI, which POSTs to the live server
    assert read_settings_from_server()["pwm"]["update_time"] == 42

with NO IPC, NO second DB connection, and no polling/retry loop -- it is a
completely ordinary in-process function call that happens to observe
whatever the background thread's request handler just wrote. This is
confirmed working by tests/web/test_page_settings.py.

(If `live_server` were ever changed to run the app in a subprocess instead,
this trick would break silently -- read-backs would see stale data. Don't
make that change without also switching the read-back helpers to read the
DB file directly.)
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
            _PLAYWRIGHT_UNAVAILABLE_REASON = (
                f"chromium not installed at {_pw.chromium.executable_path!r} -- "
                "run `uv run playwright install chromium`"
            )
except Exception as exc:  # pragma: no cover - only exercised if playwright itself is unusable here
    _PLAYWRIGHT_UNAVAILABLE_REASON = f"playwright unavailable: {exc}"

# Every tests/web/test_*.py module should do:
#
#   from tests.web.conftest import requires_chromium
#   pytestmark = requires_chromium
#
# at module scope, mirroring the guard originally inlined in
# test_wizard_nested_modal_scroll.py, so the module skips cleanly (not an
# error) when chromium isn't installed.
requires_chromium = pytest.mark.skipif(
    _PLAYWRIGHT_UNAVAILABLE_REASON is not None, reason=_PLAYWRIGHT_UNAVAILABLE_REASON or ""
)


def _seed_fresh_db(db_path, grill_name):
    """Point the datastore singleton at a fresh temp-SQLite file and seed it
    with the standard default_settings/default_control/default_pellets +
    write_status baseline every live_server needs before app.py can even be
    imported (app.py reads settings at import time for log-level setup)."""
    from common import datastore
    from common.common import WriteKind
    from common.datastore_accessors import (
        read_status,
        write_control,
        write_pellets_store,
        write_settings_store,
        write_status,
    )
    from common.defaults import default_control, default_pellets, default_settings

    os.environ["PIFIRE_DB_PATH"] = db_path
    datastore._reset_for_tests(db_path)
    datastore.init()

    settings = default_settings()
    settings["globals"]["grill_name"] = grill_name
    settings["globals"]["first_time_setup"] = False
    write_settings_store(settings)
    write_pellets_store(default_pellets())
    write_status(read_status(init=True))
    write_control(default_control(), WriteKind.OVERWRITE, origin="test-web-e2e")


@pytest.fixture(scope="module")
def live_server(request):
    """Runs the real app.py Flask app on a background thread against an
    isolated temp-SQLite DB, and yields its base URL (e.g.
    'http://127.0.0.1:54321'). See module docstring for scoping rationale
    and the thread-shared-datastore mechanism this enables.
    """
    from werkzeug.serving import make_server

    from common import datastore

    tmp_dir = tempfile.mkdtemp(prefix="pifire_test_web_")
    db_path = os.path.join(tmp_dir, "web_e2e.db")
    grill_name = f"E2E {request.module.__name__} Grill"
    _seed_fresh_db(db_path, grill_name)

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


# --- Read-back helpers -------------------------------------------------
#
# These are thin wrappers around the ordinary common.datastore_accessors
# read functions. The wrapping exists purely to document, at the call site
# in a test, *why* a plain in-process function call is a valid way to
# observe what a browser action against live_server just persisted: see
# "Thread-shared datastore" above. There is no network hop, no second DB
# handle, and nothing to poll -- by the time page.click()/page.goto()
# returns, the live server's request handler (running on another thread of
# this same process) has already completed its write.


def read_settings_from_server():
    """Read current settings via the datastore singleton live_server shares
    with this process. Use after driving a UI action or posting a form to
    live_server to assert what actually got persisted."""
    from common.datastore_accessors import read_settings

    return read_settings()


def read_control_from_server():
    """Read current control via the datastore singleton live_server shares
    with this process. See read_settings_from_server()."""
    from common.datastore_accessors import read_control

    return read_control()


# --- Precondition seeding helpers ---------------------------------------


def apply_settings(mutate):
    """Read current settings, apply `mutate(settings)` in place (or have it
    return a replacement dict), write the result back, and return it.

    Use this in a test to set up preconditions before driving the UI, e.g.:

        apply_settings(lambda s: s["cycle_data"].__setitem__("PMode", 2))
    """
    from common.datastore_accessors import read_settings, write_settings_store

    settings = read_settings()
    result = mutate(settings)
    write_settings_store(result if result is not None else settings)
    return result if result is not None else settings


def apply_control(mutate, *, origin="test-web-e2e"):
    """Read current control, apply `mutate(control)` in place (or have it
    return a replacement dict), write the result back (MERGE), and return
    it. See apply_settings() for the pattern."""
    from common.common import WriteKind
    from common.datastore_accessors import read_control, write_control

    control = read_control()
    result = mutate(control)
    final = result if result is not None else control
    write_control(final, WriteKind.MERGE, origin=origin)
    return final
