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

`playwright`/`browser_context_args`/`browser`: override pytest-playwright's
session-scoped fixture chain at module scope. The sync Playwright API keeps an
asyncio loop active in the test thread until `playwright.stop()`; session scope
therefore breaks any later test that calls `asyncio.run()` when modules are
randomized. Module scope tears that loop down with each web test file while
still sharing one browser across the file's function-scoped `page` fixtures.

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

import json
import os
import shutil
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
# at module scope, so the module skips cleanly (not an error) when chromium
# isn't installed.
requires_chromium = pytest.mark.skipif(
    _PLAYWRIGHT_UNAVAILABLE_REASON is not None, reason=_PLAYWRIGHT_UNAVAILABLE_REASON or ""
)


@pytest.fixture(scope="module")
def playwright():
    """Keep the sync API's event loop confined to each web test module."""
    with sync_playwright() as instance:
        yield instance


@pytest.fixture(scope="module")
def browser_context_args(pytestconfig, playwright, device, base_url, _pw_artifacts_folder):
    """Mirror pytest-playwright's context options at module scope."""
    context_args = {}
    if device:
        context_args.update(playwright.devices[device])
    if base_url:
        context_args["base_url"] = base_url
    if pytestconfig.getoption("--video") in {"on", "retain-on-failure"}:
        context_args["record_video_dir"] = _pw_artifacts_folder.name
    return context_args


@pytest.fixture(scope="module")
def browser(playwright, browser_name, browser_type_launch_args, connect_options):
    """Share one browser per module without a session-long event loop."""
    browser_type = getattr(playwright, browser_name)
    if connect_options:
        browser = browser_type.connect(
            **{
                **connect_options,
                "headers": {
                    "x-playwright-launch-options": json.dumps(browser_type_launch_args),
                    **(connect_options.get("headers") or {}),
                },
            }
        )
    else:
        browser = browser_type.launch(**browser_type_launch_args)
    try:
        yield browser
    finally:
        browser.close()


@pytest.fixture(autouse=True)
def _reset_server_status():
    """`set_server_status` stores on `current_app`, which is the same `app.py`
    singleton every test module imports -- so a mocked-restart test (restart
    is never mocked at the set_server_status call site, only the process-level
    restart_scripts/reboot_system/shutdown_system it triggers) leaves
    "restarting"/"rebooting"/"shutdown" sitting on the shared app object for
    every test that runs afterward, in any module, for the rest of the
    session. In production this self-heals: the real restart replaces the
    process and app.py re-sets "available" at the next boot. Tests never
    restart the process, so nothing ever heals it here -- reset explicitly.
    """
    yield
    from app import app as flask_app

    flask_app.server_status = "available"


def _seed_fresh_db(db_path, grill_name):
    """Point the datastore singleton at a fresh temp-SQLite file and seed it
    with the standard default_settings/default_control/default_pellets +
    init_status baseline every live_server needs before app.py can even be
    imported (app.py reads settings at import time for log-level setup)."""
    from common import datastore
    from common.persistence.control import (
        write_control_snapshot,
    )
    from common.persistence.runtime import (
        init_status,
        write_pellets_store,
        write_settings_store,
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
    init_status()
    write_control_snapshot(default_control(), origin="test-web-e2e")


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
# These are thin wrappers around the ordinary common.persistence domain
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
    from common.persistence.runtime import read_settings

    return read_settings()


def read_control_from_server():
    """Read current control via the datastore singleton live_server shares
    with this process. See read_settings_from_server()."""
    from common.persistence.control import read_control

    return read_control()


def drain_control_writes():
    """Apply queued control deltas to ``control:general``.

    In production this happens every iteration of the real control-runtime
    loop. This test harness runs only Flask, so call this after a UI action or
    POST that queues intent and before reading back the resulting live state."""
    from common.persistence.control import execute_control_writes

    execute_control_writes()


# --- Precondition seeding helpers ---------------------------------------


def apply_settings(mutate):
    """Read current settings, apply `mutate(settings)` in place (or have it
    return a replacement dict), write the result back, and return it.

    Use this in a test to set up preconditions before driving the UI, e.g.:

        apply_settings(lambda s: s["cycle_data"].__setitem__("PMode", 2))
    """
    from common.persistence.runtime import read_settings, write_settings_store

    settings = read_settings()
    result = mutate(settings)
    write_settings_store(result if result is not None else settings)
    return result if result is not None else settings


def apply_control(mutate, *, origin="test-web-e2e"):
    """Read current control, apply ``mutate(control)`` in place (or use its
    replacement result), write that authoritative test snapshot immediately,
    and return it. See :func:`apply_settings` for the pattern."""
    from common.persistence.control import read_control, write_control_snapshot

    control = read_control()
    result = mutate(control)
    final = result if result is not None else control
    write_control_snapshot(final, origin=origin)
    return final


# ---------------------------------------------------------------------------
# /api/files harness
#
# The three tests/web/test_api_files_*.py modules are pure JSON HTTP tests --
# no DOM -- so they use flask_app.test_client() + the `ds` fixture, the shape
# test_api_history.py established, rather than the Playwright `live_server`
# above. That matters beyond speed: several of those tests assert that path
# traversal is refused, and `requires_chromium` would let them SKIP silently on
# a checkout without a browser.
#
# The fixtures live here rather than in one of the three modules because
# function-scoped fixtures do not cross module boundaries and all three need
# the identical isolated-folder setup.
# ---------------------------------------------------------------------------


@pytest.fixture
def api_files_client(ds):
    """Flask test client over an isolated temp SQLite datastore."""
    from app import app as flask_app

    flask_app.config["TESTING"] = True
    with flask_app.test_client() as client:
        yield client


@pytest.fixture
def api_files_folders():
    """Redirect the history and recipe folders at a temp dir, and yield both.

    Patches all FIVE places a folder is read -- app.config for each kind, plus
    the module-level constants file_mgmt.cookfile, file_mgmt.common and
    file_mgmt.recipes each define separately. Missing any one of them makes a
    test write into the real repo checkout.
    """
    from app import app as flask_app
    import file_mgmt.common as common_mod
    import file_mgmt.cookfile as cookfile_mod
    import file_mgmt.recipes as recipes_mod

    tmp_dir = tempfile.mkdtemp(prefix="pifire_test_files_")
    history_dir = os.path.join(tmp_dir, "history") + "/"
    recipe_dir = os.path.join(tmp_dir, "recipes") + "/"
    os.makedirs(history_dir, exist_ok=True)
    os.makedirs(recipe_dir, exist_ok=True)

    saved = (
        flask_app.config["HISTORY_FOLDER"],
        flask_app.config["RECIPE_FOLDER"],
        cookfile_mod.HISTORY_FOLDER,
        common_mod.HISTORY_FOLDER,
        recipes_mod.RECIPE_FOLDER,
    )
    flask_app.config["HISTORY_FOLDER"] = history_dir
    flask_app.config["RECIPE_FOLDER"] = recipe_dir
    cookfile_mod.HISTORY_FOLDER = history_dir
    common_mod.HISTORY_FOLDER = history_dir
    recipes_mod.RECIPE_FOLDER = recipe_dir

    yield history_dir, recipe_dir

    (
        flask_app.config["HISTORY_FOLDER"],
        flask_app.config["RECIPE_FOLDER"],
        cookfile_mod.HISTORY_FOLDER,
        common_mod.HISTORY_FOLDER,
        recipes_mod.RECIPE_FOLDER,
    ) = saved
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture
def client(ds):
    """Flask test client over the isolated temp SQLite datastore from `ds`.

    Enters the app's test-request context and exits it on teardown. Nineteen
    modules each carried their own copy of this; four of them used a bare
    `return flask_app.test_client()`, which never enters the context manager
    and so never runs its teardown. This is the single definition.

    Modules needing a differently-seeded client (see test_api_mpc_calibration.py)
    or the files-API client (`api_files_client`) override `client` locally --
    a module-level fixture shadows this one, which is intended.
    """
    from app import app as flask_app

    flask_app.config["TESTING"] = True
    with flask_app.test_client() as test_client:
        yield test_client
