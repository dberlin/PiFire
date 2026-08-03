# Flask-Retirement Pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve the built React app from Flask (#5) and delete the legacy Jinja page tier (#71), closing the batch of deferred "still reachable" security items, while keeping the `api_*` blueprints, the mobile socket, and the two helper modules `api_*` import.

**Architecture:** Two ordered slices. **Slice A (Task 1)** adds a `spa` blueprint (registered last) that serves `web-react/dist/index.html` for every client-side route and serves the React bundle's `/static/{js,css,font}` from the build — while leaving Flask's default `/static` (hence `/static/img`) intact for `api_files` and React image refs. **Slice B (Tasks 2–5)** deletes the 14 whole page blueprints, retires `wizard`/`tuner` to helper-only packages, removes the dead shared templates + legacy `static/`, and repairs the test suite. Design: `docs/superpowers/specs/2026-07-29-flask-retirement-design.md`.

**Tech Stack:** Flask (app factory in `app.py`), pytest (`tests/web/`), React build under `web-react/dist/` (bun toolchain).

## Global Constraints

- **Python tests:** run with `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest`. Bare `python`/`pytest` gives false failures.
- **Python format/lint:** `.venv/bin/ruff format <files>` and `.venv/bin/ruff check <files>` before every Python commit. NEVER `uvx ruff` (repo pins ruff <0.16). Remove imports left unused by a deletion (ruff will flag them).
- **web-react:** bun, never npm. The full web-react gate is `bun run typecheck && bun run lint && bun run test && bun run build`. `bun run build` (re)generates `web-react/dist/`, which Task 1's serving depends on — the build must have run at least once so `dist/index.html` exists.
- **Kept surfaces — never touch:** every `blueprints/api*` package, `blueprints/api/` (`api_bp`, the legacy `/api` JSON blueprint React depends on), `blueprints/mobile/`, `common/*`, `updater.py`, and the two helper modules `blueprints/wizard/wizard.py` + `blueprints/tuner/tuner.py`.
- **Keep `templates/server_error.html`** (rendered by the live `@app.errorhandler`).
- **Suite-green invariant:** each task ends with the relevant test suite green. A retired route is *not* asserted via HTTP status after Task 1 (the SPA catch-all answers `/dash` etc. with `index.html`); "the page blueprint is gone" is asserted via the app's blueprint registry, not a 404.
- **Commits:** the repo is jj-colocated; `git add`/`git commit` steps below are reflected into jj. One commit per task (or per logical sub-step where noted).

## Verified facts (checked against live code — do not re-derive)

- `app.py:49` — `app = Flask(__name__)` (default `static_folder="static"`, `static_url_path="/static"`).
- `app.py:144-151` — `index()` redirects `/` to `/wizard/welcome` (fresh) or `/dash`. React owns the fresh-install→wizard redirect (`web-react/src/components/App.tsx:47-57`), so removing `index()` loses nothing.
- `app.py:161-164` — mobile blueprint registered at the tail; the `spa` blueprint registers after it.
- `web-react/dist/index.html` references `/static/js/*`, `/static/css/*`, `/static/font/*`; build tree is `dist/index.html` + `dist/static/{js,css,font}`.
- **`/static/img/**` is a KEPT tree, NOT legacy:** `api_files` serves uploaded cookfile/recipe images at `/static/img/tmp/{id}/{asset}` (via Flask's default static route; `static/img/tmp` is a gitignored runtime dir), and React `<img>` refs use `/static/img/tmp/*` (21×), `/static/img/wizard/*` (vendor component photos, 6×), and `/static/img/pifire-cf-thumb.png` (fallback). Therefore Flask's `static_folder` must **stay** PiFire's `static/` — repointing it to the build 404s every `/static/img`. Only the legacy page-asset subdirs `static/{css,font,js}` are retired.
- `blueprints/wizard/__init__.py` and `blueprints/tuner/__init__.py` each do `X_bp = Blueprint(...)` then `from . import routes`. `api_wizard` imports `blueprints.wizard.wizard`; `api_tuner` imports `blueprints.tuner.tuner`; both helpers import only `common.*`.
- `resolve_dashboard` (`blueprints/settings/routes.py:25`) — serena-confirmed referencers: only its caller in the deleted settings handler + `tests/unit/web/test_resolve_dashboard.py`.
- `templates/shutdown.html` is rendered only by `blueprints/admin/routes.py` (retired) → delete it.
- `templates/` shared partials: `base.html`, `_macro_control_panel.html`, `_macro_generic_config.html`, `_macro_timer.html`, `_log_list.html` (used only by page templates); `server_error.html` (kept).
- `tests/web/test_page_*.py`: 13 files. `test_page_api.py` covers the **kept** `/api` blueprint (keep); the other 12 cover retired pages (delete). `test_tuner_template_allowlist.py` (POST `/tuner/`) and `test_history_export_route.py` (GET `/history/export`) also exercise retired routes.

## File Structure

**Created:**
- `blueprints/spa/__init__.py`, `blueprints/spa/routes.py` — the SPA-serving blueprint.
- `tests/web/test_spa.py` — SPA-serving tests.

**Modified:**
- `app.py` — register `spa_bp` last; delete `index()`; remove now-dead imports and the 16 page-blueprint import/register lines. (Flask's `static_folder` stays default — do NOT repoint it.)
- `blueprints/wizard/__init__.py`, `blueprints/tuner/__init__.py` — reduce to helper-only packages.
- `docs/superpowers/backlogs/react-migration-backlog.md` — mark #5, #71, and the closed "still reachable" items.
- `templates/server_error.html` — only if it references now-deleted legacy `/static` assets (inline styles).

**Deleted:**
- `blueprints/{admin,events,logs,history,metrics,dash,pellets,cookfile,probeconfig,recipes,settings,update,manual,manifest}/` (whole).
- `blueprints/wizard/{routes.py,templates/,static/}`, `blueprints/tuner/{routes.py,templates/,static/}` (keep `wizard.py`/`tuner.py`/`__init__.py`).
- `templates/{base.html,_macro_control_panel.html,_macro_generic_config.html,_macro_timer.html,_log_list.html,shutdown.html}`; the legacy page-asset subdirs `static/{css,font,js}` only — **`static/img/**` is kept**.
- `tests/web/test_page_{pellets,tuner,probeconfig,history,smallpages,settings,wizard,update,dashboard,recipes,cookfile,admin}.py`, `tests/web/test_tuner_template_allowlist.py`, `tests/web/test_history_export_route.py`, `tests/unit/web/test_resolve_dashboard.py`, plus any straggler identified in Task 5.

## Parallelization

Tasks are **inherently sequential**: Tasks 1–3 all edit `app.py` (concurrent edits to one file conflict), and every task must leave the suite green, so ordering is A→2→3→4→5. `base.html` deletion (Task 4) must follow the page deletions (Tasks 2–3). No task in this plan is safely parallelizable; run them serially in one workspace.

---

### Task 1: `spa` blueprint — serve the React build (Slice A)

**Files:**
- Create: `blueprints/spa/__init__.py`, `blueprints/spa/routes.py`
- Modify: `app.py:144-151` (delete `index()`), `app.py` tail (register `spa_bp` after mobile), `app.py` imports (drop now-unused `redirect`). **`app.py:49` `Flask(__name__)` stays unchanged.**
- Test: `tests/web/test_spa.py`

**Interfaces:**
- Consumes: `web-react/dist/index.html` + `dist/static/{js,css,font}` (build output).
- Produces: `spa_bp` (Flask Blueprint, no url_prefix); `/` and `/<path:path>` serve the SPA; `/static/{js,css,font}/<path>` serve the React bundle; unknown `api/`+`mobile/` paths return JSON 404. `/static/img/**` is left to Flask's default static handler (unchanged).

- [ ] **Step 1: Write the failing test** — `tests/web/test_spa.py`

```python
import re


def test_root_serves_spa(client):
    r = client.get("/")
    assert r.status_code == 200
    assert b"/static/" in r.data  # SPA shell references hashed assets


def test_deep_link_serves_same_spa_shell(client):
    # A React-router path with no Flask route boots the SPA (same index.html).
    assert client.get("/admin").get_data() == client.get("/").get_data()


def test_hashed_bundle_asset_is_served(client):
    # A /static/js|css|font asset from the build serves via the spa rules.
    index = client.get("/").get_data(as_text=True)
    m = re.search(r'/static/(?:js|css|font)/[^"\']+', index)
    assert m, "index.html referenced no /static/{js,css,font} asset"
    assert client.get(m.group(0)).status_code == 200


def test_static_img_still_served_by_flask_default(client):
    # REGRESSION: the spa /static/{js,css,font} rules must NOT shadow /static/img.
    # api_files serves uploads there, and React references it directly.
    assert client.get("/static/img/pifire-cf-thumb.png").status_code == 200


def test_unknown_api_path_is_json_404(client):
    r = client.get("/api/does-not-exist-xyz")
    assert r.status_code == 404
    assert "text/html" not in r.content_type


def test_unknown_mobile_path_is_json_404(client):
    r = client.get("/mobile/does-not-exist-xyz")
    assert r.status_code == 404
    assert "text/html" not in r.content_type
```

- [ ] **Step 2: Run it, verify it fails**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_spa.py -v`
Expected: FAIL — `/` currently 302-redirects (not 200 with `/static/`); `/api/...` returns Flask's HTML 404. (`test_static_img_still_served_by_flask_default` may already pass, since Flask's default static serves `/static/img` today — that is the invariant we must preserve.)

- [ ] **Step 3: Create `blueprints/spa/__init__.py`**

```python
from flask import Blueprint

spa_bp = Blueprint("spa_bp", __name__)

from . import routes  # noqa: E402,F401  # side-effect import: registers routes
```

- [ ] **Step 4: Create `blueprints/spa/routes.py`**

```python
import os

from flask import abort, jsonify, send_from_directory

from blueprints.spa import spa_bp

# Absolute paths into the built React app (repo-root/web-react/dist), resolved
# from this file's location so serving never depends on the process CWD.
_DIST = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "web-react",
    "dist",
)
_STATIC = os.path.join(_DIST, "static")


# The build references /static/js, /static/css, /static/font. These three rules
# are MORE specific than Flask's built-in "/static/<path:filename>", so Werkzeug
# matches them first and serves the React bundle — while /static/img/** falls
# through to the default static handler (PiFire's static/img: api_files uploads +
# the wizard/controller vendor images React references). Do NOT repoint Flask's
# static_folder; that would 404 every /static/img.
@spa_bp.route("/static/js/<path:filename>")
def spa_js(filename):
    return send_from_directory(os.path.join(_STATIC, "js"), filename)


@spa_bp.route("/static/css/<path:filename>")
def spa_css(filename):
    return send_from_directory(os.path.join(_STATIC, "css"), filename)


@spa_bp.route("/static/font/<path:filename>")
def spa_font(filename):
    return send_from_directory(os.path.join(_STATIC, "font"), filename)


@spa_bp.route("/")
@spa_bp.route("/<path:path>")
def spa(path=""):
    # Unmatched API/socket paths stay JSON 404s — never serve the SPA shell
    # there, or clients can't distinguish a missing endpoint from an app route.
    if path.startswith(("api/", "mobile/")):
        return jsonify({"error": "not found"}), 404
    if not os.path.isfile(os.path.join(_DIST, "index.html")):
        abort(404)
    return send_from_directory(_DIST, "index.html")
```

- [ ] **Step 5: Leave Flask's default static UNCHANGED** — `app.py:49`

Keep `app = Flask(__name__)` exactly as-is. Flask's built-in `/static/<path:filename>` continues serving PiFire's `static/` — critically `/static/img/**`, a KEPT tree used by `api_files` (uploads under `static/img/tmp`) and by React `<img>` refs (`/static/img/wizard/*`, `/static/img/pifire-cf-thumb.png`). The spa blueprint's three `/static/{js,css,font}` rules (Step 4) shadow the default only for the React bundle. Do NOT repoint `static_folder` — it 404s every `/static/img`.

- [ ] **Step 6: Delete `index()` and register `spa_bp` last**

Delete the whole `index()` route (`app.py:144-151`). After the mobile registration block (`app.py:161-164`), add:

```python
"""
==============================================================================
 Register SPA Blueprint (serves the React build; must be registered LAST so
 real backend routes win over the catch-all)
==============================================================================
"""
from blueprints.spa import spa_bp

app.register_blueprint(spa_bp)
```

Remove `redirect` from the `from flask import ...` line (`app.py:22`) — it was used only by the deleted `index()`. Keep `render_template` (still used by `handle_500`).

- [ ] **Step 7: Format, run tests**

Run: `.venv/bin/ruff format app.py blueprints/spa/ tests/web/test_spa.py && .venv/bin/ruff check app.py blueprints/spa/`
Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_spa.py -v`
Expected: PASS (all 5).

- [ ] **Step 8: Confirm no regression** — the page blueprints are still registered here, so their tests must still pass.

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web -q`
Expected: the `api_*`, `mobile`, and `api_files` (incl. `test_api_files_*_assets.py`, which fetch `/static/img/tmp`) suites PASS — that is the load-bearing proof this task did not break a kept surface. The Chromium real-UI **page** tests (`test_page_*[chromium]`, `test_page_smallpages[chromium]`, `test_wizard_nested_modal_scroll[chromium]`, `test_tuner_template_allowlist`) WILL fail here: they drive the live Flask pages, whose `/static/css|js` now resolves to the React bundle (spa rules shadow the legacy files). Every one of those pages is retired in Tasks 2/3, and their tests are deleted there — do NOT fix or delete them in this task. Record the failing set in the ledger and confirm it is entirely retired-page tests (no `api_*`/`mobile`/`api_files` failure). This is the known, expected consequence, not a regression.

- [ ] **Step 9: Commit**

```bash
git add blueprints/spa/ tests/web/test_spa.py app.py
git commit -m "feat(spa): serve the React build via a spa blueprint; retire the index redirect"
```

---

### Task 2: Delete the 14 whole page blueprints (Slice B)

**Files:**
- Modify: `app.py` — remove the 14 `import` lines and 14 `register_blueprint` lines for the whole-delete set.
- Delete: `blueprints/{admin,events,logs,history,metrics,dash,pellets,cookfile,probeconfig,recipes,settings,update,manual,manifest}/`
- Delete tests: `tests/web/test_page_{pellets,probeconfig,history,smallpages,settings,update,dashboard,recipes,cookfile,admin}.py`, `tests/web/test_history_export_route.py`, `tests/unit/web/test_resolve_dashboard.py`

**Interfaces:**
- Consumes: nothing (deletions). Confirmed: no Python importer of these packages outside `app.py` + `test_resolve_dashboard.py`.
- Produces: these 14 blueprints no longer registered; their routes fall through to the SPA catch-all.

- [ ] **Step 1: Remove the 14 page imports + registrations from `app.py`**

Delete these `import` lines: `admin_bp, events_bp, logs_bp, manifest_bp, manual_bp, history_bp, metrics_bp, dash_bp, pellets_bp, cookfile_bp, probeconfig_bp, recipes_bp, settings_bp, update_bp` (app.py:68,70-78,80-82,91). Delete the matching `app.register_blueprint(...)` lines (app.py:94,96-104,106-108,117). **Leave `tuner_bp`/`wizard_bp` (lines 79,83,105,109) for Task 3.** Leave every `api_*` and `api_bp` line.

- [ ] **Step 2: Delete the 14 blueprint packages**

```bash
git rm -r blueprints/admin blueprints/events blueprints/logs blueprints/history \
  blueprints/metrics blueprints/dash blueprints/pellets blueprints/cookfile \
  blueprints/probeconfig blueprints/recipes blueprints/settings blueprints/update \
  blueprints/manual blueprints/manifest
```

- [ ] **Step 3: Delete the retired page tests**

```bash
git rm tests/web/test_page_pellets.py tests/web/test_page_probeconfig.py \
  tests/web/test_page_history.py tests/web/test_page_smallpages.py \
  tests/web/test_page_settings.py tests/web/test_page_update.py \
  tests/web/test_page_dashboard.py tests/web/test_page_recipes.py \
  tests/web/test_page_cookfile.py tests/web/test_page_admin.py \
  tests/web/test_history_export_route.py tests/unit/web/test_resolve_dashboard.py
```

(`test_page_tuner.py` and `test_tuner_template_allowlist.py` go in Task 3 with the tuner page; `test_page_wizard.py` too. `test_page_api.py` is **kept** — it covers `api_bp`.)

- [ ] **Step 4: Import-sanity — the app must still import**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run python -c "import app; print('ok')"`
Expected: `ok` (no ImportError from a dangling reference).

- [ ] **Step 5: Format, run the web suite**

Run: `.venv/bin/ruff format app.py && .venv/bin/ruff check app.py`
Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web -q`
Expected: PASS. Any failure here is a test that exercised one of the just-deleted pages and survived — note it for Task 5 triage (do not mass-delete blindly in this task).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor: retire 14 legacy Jinja page blueprints (React serves them now)"
```

---

### Task 3: Retire `wizard`/`tuner` to helper-only packages (Slice B)

**Files:**
- Modify: `app.py` — remove `wizard_bp`/`tuner_bp` import + register lines.
- Modify: `blueprints/wizard/__init__.py`, `blueprints/tuner/__init__.py` — reduce to plain packages.
- Delete: `blueprints/wizard/{routes.py,templates,static}`, `blueprints/tuner/{routes.py,templates,static}` (keep `wizard.py`, `tuner.py`).
- Delete tests: `tests/web/test_page_wizard.py`, `tests/web/test_page_tuner.py`, `tests/web/test_tuner_template_allowlist.py`

**Interfaces:**
- Consumes: `api_wizard` imports `blueprints.wizard.wizard`; `api_tuner` imports `blueprints.tuner.tuner` — these MUST keep working.
- Produces: `wizard`/`tuner` packages contain only their helper module; no page routes registered.

- [ ] **Step 1: Reduce `blueprints/wizard/__init__.py`** to (drop the Blueprint + `from . import routes`):

```python
# The Flask wizard page blueprint was retired (React serves the wizard UI via
# api_wizard). This package survives only for wizard.py, imported by
# blueprints.api_wizard.
```

- [ ] **Step 2: Reduce `blueprints/tuner/__init__.py`** to:

```python
# The Flask tuner page blueprint was retired (React serves the tuner UI via
# api_tuner). This package survives only for tuner.py, imported by
# blueprints.api_tuner.
```

- [ ] **Step 3: Delete the page routes/templates/static (keep the helpers)**

```bash
git rm blueprints/wizard/routes.py blueprints/tuner/routes.py
git rm -r blueprints/wizard/templates blueprints/tuner/templates
git rm -r blueprints/wizard/static blueprints/tuner/static 2>/dev/null || true
```

Verify `blueprints/wizard/wizard.py` and `blueprints/tuner/tuner.py` remain.

- [ ] **Step 4: Remove `wizard_bp`/`tuner_bp` from `app.py`**

Delete `from blueprints.wizard import wizard_bp` (line 83), `from blueprints.tuner import tuner_bp` (line 79), and their `register_blueprint` lines (109, 105).

- [ ] **Step 5: Delete the wizard/tuner page tests**

```bash
git rm tests/web/test_page_wizard.py tests/web/test_page_tuner.py tests/web/test_tuner_template_allowlist.py
```

- [ ] **Step 6: Prove the surviving helper imports still resolve**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run python -c "from blueprints.wizard.wizard import read_wizard; import blueprints.tuner.tuner; import app; print('ok')"`
Expected: `ok`.

- [ ] **Step 7: The api_wizard/api_tuner suites MUST stay green**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_wizard.py tests/web/test_api_tuner.py tests/web/test_api_tuner_auto.py -q`
Expected: PASS (this is the proof the partial deletion didn't break the kept surface).

- [ ] **Step 8: Format, commit**

```bash
.venv/bin/ruff format app.py blueprints/wizard/__init__.py blueprints/tuner/__init__.py
git add -A
git commit -m "refactor: retire wizard/tuner page routes, keep helper modules for api_*"
```

---

### Task 4: Delete dead shared templates + legacy static (Slice B)

**Files:**
- Delete: `templates/{base.html,_macro_control_panel.html,_macro_generic_config.html,_macro_timer.html,_log_list.html,shutdown.html}`; the legacy page-asset subdirs `static/{css,font,js}`. **KEEP `static/img/**`** (api_files uploads + React vendor images).
- Modify (conditional): `templates/server_error.html` if it references deleted `/static/{css,js}` assets.

**Interfaces:**
- Consumes: nothing. Confirmed: only retired page templates extended `base.html`; `shutdown.html` was rendered only by the retired admin blueprint; `static/img/**` is a kept surface (do not delete).
- Produces: no dead Jinja page assets remain; `/static/img` + `server_error.html` still work.

- [ ] **Step 1: Guard — confirm no surviving referencer of each shared partial**

Run: `grep -rn "base.html\|_macro_control_panel\|_macro_generic_config\|_macro_timer\|_log_list\|shutdown.html" --include="*.py" --include="*.html" blueprints templates common app.py | grep -v "templates/server_error.html"`
Expected: empty (all referencers were retired). If any surviving referencer appears, stop and reconcile before deleting.

- [ ] **Step 2: Delete the dead templates + legacy static**

```bash
git rm templates/base.html templates/_macro_control_panel.html \
  templates/_macro_generic_config.html templates/_macro_timer.html \
  templates/_log_list.html templates/shutdown.html
# Delete ONLY the legacy page-asset subdirs. KEEP static/img/** — it is a live
# kept tree: api_files uploads (static/img/tmp) + wizard/controller vendor images
# React references (/static/img/wizard/*, /static/img/pifire-cf-thumb.png).
git rm -r static/css static/font static/js
```

- [ ] **Step 2b: Guard — `/static/img` still serves after the deletion**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_files_cookfile_assets.py tests/web/test_api_files_recipes_assets.py tests/web/test_spa.py -q`
Expected: PASS — deleting `static/{css,font,js}` must not touch `/static/img` serving (`test_static_img_still_served_by_flask_default` + the api_files asset tests prove it).

- [ ] **Step 3: Audit `server_error.html` for deleted `/static` links**

Run: `grep -n "/static/" templates/server_error.html || echo "no legacy static refs"`
If it references now-deleted `/static/...` assets, inline the minimal CSS it needs directly into `server_error.html` so the 500 page still styles. If "no legacy static refs", leave it unchanged.

- [ ] **Step 4: Prove the error page still renders**

Add/confirm a test in `tests/web/test_spa.py` (or a small `tests/web/test_error_page.py`):

```python
def test_server_error_template_renders(flask_app):
    # render_template must still find server_error.html after the base.html purge.
    # `flask_app` is the fixture name in tests/web/conftest.py (not `app`).
    with flask_app.test_request_context():
        from flask import render_template
        html = render_template("server_error.html")
        assert html  # non-empty, no TemplateNotFound
```

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_spa.py -q` (and the new test if separate).
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: delete dead base template, macros, and legacy static tree"
```

---

### Task 5: Test-suite triage + full gate + backlog (Slice B)

**Files:**
- Delete/repair: any straggler test that exercised a retired route (candidates from the fact-find: `tests/web/test_webapp_sqlite.py`, `tests/web/test_admin_restore_containment.py`, `tests/web/test_metrics_auger_rate.py`, `tests/web/test_settings_controller_render.py`, `tests/web/test_wizard_finish_reboot_modal.py`).
- Modify: `docs/superpowers/backlogs/react-migration-backlog.md`.

**Interfaces:**
- Consumes: the full test suite as the oracle for what retirement broke.
- Produces: a green full suite; backlog reflects #5/#71 shipped.

- [ ] **Step 1: Run the full Python suite, capture failures**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest -q`
For each failure, classify by reading the test:
- It GET/POSTs a **retired** route (`/dash`, `/admin`, `/settings`, `/tuner`, `/history/export`, `/wizard` page, `/metrics`, ...) and asserts page behavior → **delete the test** (the behavior is gone).
- It targets a **kept** surface (`/api/*`, `/mobile`, a `common` helper) and only incidentally broke → **repair the test** (e.g. update a URL, drop a dead import).

Do not delete a test that covers a kept surface. When unsure whether a route is retired, check the app's registered blueprints (Step 2 helper).

- [ ] **Step 2: Assert the retirement holds (URL map, not HTTP status)**

After Task 1 the SPA catch-all answers `/dash` with `index.html`, so an HTTP status can't prove a page is gone. Assert instead that no Flask *route* owns a retired prefix — this is robust to blueprint-name strings (mobile registers as `"mobile"`, not `"mobile_bp"`). Add to `tests/web/test_spa.py`:

```python
def test_retired_page_routes_are_gone(flask_app):
    rules = {r.rule for r in flask_app.url_map.iter_rules()}
    # The SPA catch-all is "/<path:path>"; none of these prefixes should be a
    # real Flask rule anymore. (api_wizard/api_tuner live under /api/, so the
    # bare /wizard//tuner/ checks don't touch them.)
    for prefix in (
        "/dash", "/admin/", "/settings/", "/tuner/", "/history/", "/metrics/",
        "/pellets/", "/recipes/", "/cookfile/", "/probeconfig/", "/manual/",
        "/events/", "/logs/", "/manifest/", "/wizard/", "/update/",
    ):
        stale = [r for r in rules if r.startswith(prefix)]
        assert not stale, f"{prefix} still routed: {stale}"
    # Kept backend surfaces still routed.
    assert any(r.startswith("/api/") for r in rules)
    assert any(r.startswith("/mobile") for r in rules)
    assert "/<path:path>" in rules  # the SPA catch-all itself
```

- [ ] **Step 3: Re-run the full Python suite to green**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest -q`
Expected: PASS, 0 failed.

- [ ] **Step 4: web-react gate (build is what the SPA serves)**

Run (in `web-react/`): `bun run typecheck && bun run lint && bun run test && bun run build`
Expected: all green. Then re-run `tests/web/test_spa.py` (it reads the freshly built `dist/`).

- [ ] **Step 5: e2e smoke**

Run the route-mocked e2e (`web-react`): confirm the suite passes as before — no e2e depends on a Flask page.

- [ ] **Step 6: Update the backlog**

In `docs/superpowers/backlogs/react-migration-backlog.md`: mark **#5** (SPA serve) and **#71** (deregister legacy page blueprints) SHIPPED, and mark the batch of deferred "still reachable" items closed by this pass (traversal/template-injection doors on the retired pages are now unreachable — the routes don't exist). Note `/mobile` stays as the socket/API surface.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "test: repair suite after page retirement; mark #5/#71 shipped in backlog"
```

## Self-Review Notes (author)

- **Spec coverage:** Slice A (spa blueprint, static repoint, JSON-404 guard, index removal) → Task 1. Slice B whole-deletes → Task 2; wizard/tuner partial → Task 3; dead templates + legacy static + shutdown.html + server_error audit → Task 4; test triage + full gate + backlog → Task 5. `resolve_dashboard` test deleted in Task 2. All four spec "open items" resolved in Verified Facts.
- **Type/name consistency:** `spa_bp` (no url_prefix), `_DIST`, and `spa(path="")` are used identically across Task 1's files and Task 5's registry assertion.
- **No placeholders:** every deletion is an explicit path list; the only intentionally open set is Task 5's straggler triage, which is genuinely suite-driven (candidates are named) — that is the honest shape of the work, not a placeholder.
