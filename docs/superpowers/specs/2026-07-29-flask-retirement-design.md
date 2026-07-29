# Flask-Retirement Pass — Design

**Status:** design (brainstorming output), 2026-07-29
**Goal:** Make Flask serve the React build (#5) and delete the legacy Jinja
page tier (#71), closing the batch of "still reachable" security items deferred
by earlier React-migration slices. Keep the `api_*` blueprints, the mobile
socket feed, and the two helper modules `api_*` import.

## Summary

The React app (`web-react/`) now covers every functional Flask page — the
updater was the last one ported. This pass retires the Jinja page tier and puts
React in front of users, in two ordered slices within one spec:

- **Slice A (#5) — additive:** a new `spa` blueprint serves the built React app
  (`web-react/dist/`) — `index.html` for every client-side route (deep links
  included) and the hashed assets — so visiting `/dash`, `/admin`, `/update`,
  `/settings/...` boots the SPA. Lands first.
- **Slice B (#71) — subtractive:** delete the legacy page blueprints and their
  characterization tests, now that React replaces them. Lands after A.

The `api_*` blueprints (React's backend), `blueprints/mobile/` (the socket
feed + mobile API), `common/*`, and the two helper modules that live *inside*
page-blueprint packages but are imported by `api_*` (`wizard.py`, `tuner.py`)
all survive untouched.

## Context — verified facts (checked against live code, do not re-derive)

- **React is a complete front-end, incl. the installer.** `blueprints/api_wizard/routes.py:440`
  fires `os.system(f"{python_exec} wizard.py &")` in `/finish` — the same
  detached-installer kickoff the Flask wizard does. So the Flask `wizard` page
  blueprint is redundant.
- **No SPA serving exists yet.** `app.py:145` `index()` only redirects to
  `/wizard/welcome` (fresh install) or `/dash`. There is no `send_from_directory`
  / dist-serving path. `app = Flask(__name__)` (`app.py:49`) — default
  `static_folder="static"`, `static_url_path="/static"`, serving PiFire's legacy
  Jinja page assets.
- **The React build references `/static/*`.** `web-react/dist/index.html` loads
  `/static/js/*`, `/static/css/*`, `/static/font/*`; the build tree is
  `dist/index.html` + `dist/static/{js,css,font}`. This **collides** with Flask's
  built-in `/static` route (see Slice A resolution).
- **Only two page-blueprint packages own a module `api_*` imports:**
  `api_wizard` imports `blueprints.wizard.wizard`; `api_tuner` imports
  `blueprints.tuner.tuner`. Both helper modules import only from `common.*`
  (`wizard.py`: `common.common`, `common.datastore_accessors`; `tuner.py`:
  `math`, `common.common`) — deleting page routes/templates cannot break them.
- **No other Python importer of any retired page blueprint** exists besides
  `app.py`'s registration lines and one unit test (below). Confirmed repo-wide.
- **`resolve_dashboard`** (`blueprints/settings/routes.py:25`) is referenced
  (serena `find_referencing_symbols`) only by its own caller in the settings
  handler being deleted and by `tests/unit/web/test_resolve_dashboard.py`. No
  live `api_*`/`common` code uses it — it dies with the page.
- **`blueprints/mobile/`** is `__init__.py` + `socket_io.py` only — pure
  `@socketio.on(...)` handlers, no `render_template`, no page routes. Nothing to
  retire; it stays as-is. This settles /mobile's fate.
- **`templates/base.html`** is extended only by page-blueprint templates (all
  retired). **`templates/server_error.html`** is rendered by the live
  `@app.errorhandler(InternalServerError)` (`app.py:126-129`) and must survive.
  `dist/index.html` does not reference Flask's `/manifest` (the PWA manifest is
  React's concern, not the Flask page tier), so `manifest_bp` is retireable.

## Slice A — `spa` blueprint (serve the React build)

**New unit: `blueprints/spa/`** (`__init__.py` + `routes.py`), a small blueprint
whose one responsibility is serving the built SPA. Registered **last** in
`app.py`, after every `api_*` blueprint and `mobile_bp`.

### Asset serving — resolve the `/static` collision

The React build references absolute `/static/*`, the same prefix as Flask's
built-in static route. Because Slice B deletes the legacy `static/` tree (the
pages that used it are gone), the clean resolution is to **repoint the app
factory at the build**:

```python
app = Flask(__name__, static_folder="web-react/dist/static", static_url_path="/static")
```

Flask's built-in static rule then serves the hashed `/static/js|css|font/*`
directly, with higher route specificity than the catch-all — no extra code, and
the `/static` URL contract the build expects is preserved. (`static_folder` is
resolved relative to `app.py`'s directory, the repo root.)

### App routes / deep links

```python
@spa_bp.route("/")
@spa_bp.route("/<path:path>")
def spa(path=""):
    # JSON 404 for unmatched API/socket paths — never serve HTML there
    if path.startswith(("api/", "mobile/")):
        abort(404)
    return send_from_directory(<repo-root>/web-react/dist, "index.html")
```

- Any client-side route (`/dash`, `/admin`, `/update`, `/settings/probes`, ...)
  that is not a real backend route falls through to the catch-all and boots the
  SPA; React Router resolves it.
- **Guard:** an unmatched path under `api/` or `mobile/` returns a JSON 404, so a
  mistyped/removed API path stays a JSON 404 instead of silently serving
  `index.html` (which would break client error handling and mask 404s).
- `dist/index.html` is resolved via an absolute path derived from the app root,
  not a relative CWD path.

### `index()` replacement

The catch-all's `/` route supersedes `app.py`'s current `index()` redirect
(`app.py:145-151`). React already owns first-run routing (its wizard route reads
install state), so `/` simply serves the SPA. The plan confirms React performs
the fresh-install → wizard redirect before deleting the Flask redirect; if a
server-side hint is still wanted, `/` may 302 to `/wizard/welcome` on a fresh
install, but the default is "serve the SPA and let React route."

## Slice B — retire the legacy page blueprints (#71)

### Delete whole (package + `test_page_*.py`)

Blueprints with no surviving importer — delete `blueprints/<name>/` entirely
(routes, templates, static) plus the matching `tests/web/test_page_<name>.py`,
and remove their `import` + `register_blueprint` lines from `app.py`:

`admin, events, logs, history, metrics, dash, pellets, cookfile, probeconfig,
recipes, settings, update, manual, manifest`

### Delete routes/templates, keep the package + helper module

`wizard/` and `tuner/`: delete `routes.py`, `templates/`, any page `static/`, and
remove their `import`/`register_blueprint` lines from `app.py` — but **keep the
package** and its helper module (`wizard/wizard.py`, `tuner/tuner.py`), which
`api_wizard`/`api_tuner` import. Keep each package's `__init__.py` if the helper
import path (`blueprints.wizard.wizard`) needs it; the plan verifies the exact
import path still resolves after `routes.py` is gone.

### Also delete (dead-with-the-pages)

- `templates/base.html` and the top-level partials referenced only by deleted
  pages: `_macro_control_panel.html`, `_macro_generic_config.html`,
  `_macro_timer.html`, `_log_list.html` — the plan greps for any surviving
  referencer before deleting each.
- PiFire's legacy `static/` tree (page CSS/JS/img), now that `static_folder`
  points at the React build.
- `tests/unit/web/test_resolve_dashboard.py` (its function under test is deleted
  with the settings page).

### Keep (must survive)

- `templates/server_error.html` — live `@errorhandler`. The plan audits its
  `/static` references; if it linked legacy static now deleted, inline the styles
  so the 500 page still renders.
- `templates/shutdown.html` — the plan checks its `render_template` callsite
  (likely a kept/control path); delete only if no live route renders it.
- All `api_*` blueprints, `blueprints/mobile/`, `common/*`, `updater.py`,
  `wizard.py`, `tuner.py`.

## Ordering & parallelization

- **A before B.** React must be served before the Jinja pages are removed.
- Within **B**, the whole-package deletions are mutually independent (disjoint
  files) and parallelizable; the `wizard`/`tuner` partial-deletions and the
  `app.py` edit touch shared/overlapping files and are done together/serially.
- The `app.py` registration edits (both slices touch `app.py`) are sequenced, not
  parallel, to avoid conflicting edits to one file.

## Testing

- **Slice A — new `tests/web/test_spa.py`:**
  - `GET /` returns 200 and the `dist/index.html` bytes (assert a stable marker
    from the built HTML).
  - a deep link (`GET /admin`) returns the same `index.html` (SPA boot, not a
    Flask page).
  - a hashed asset (`GET /static/js/<built-file>` or a representative path)
    serves from `dist/static` with 200.
  - `GET /api/does-not-exist` and `GET /mobile/does-not-exist` return **404**
    with a non-HTML body (the JSON-404 guard), not `index.html`.
- **Slice B:**
  - the retired pages' `tests/web/test_page_*.py` are **deleted** (not converted).
  - the `api_*` suites (`tests/web/test_api_*.py`) and the mobile-socket tests
    stay green — the proof that retirement did not nick a live surface.
  - a regression assertion that a representative retired route (e.g.
    `GET /dash` as a Flask *page* — distinct from the SPA catch-all) is no longer
    served by a page blueprint. NOTE: after Slice A the SPA catch-all answers
    `/dash` with `index.html`; the meaningful assertion is that the **page
    blueprint** is gone (no `dash_bp` registered), verified via the app's URL map
    / blueprint list, not an HTTP status.
- **React:** the existing web-react suite + route-mocked e2e stay green; no React
  source changes are required by this pass (build output is consumed as-is).
- **Full run** after B: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run
  pytest` (Python) and `bun run typecheck && bun run lint && bun run test && bun
  run build` (web-react), plus the e2e.

## Risks & mitigations

- **Runtime cross-reference a static scan can't see** — a *kept* surface (a
  surviving template, `server_error.html`, mobile, or React) issuing an AJAX call
  or link to a retired route/prefix. Mitigation: before deleting, grep the
  templates/JS of kept surfaces for the retired URL prefixes
  (`/admin`, `/dash`, `/settings`, `/manifest`, ...); run the full `api_*` + e2e
  suite after deletion.
- **`server_error.html` losing styling** when legacy `static/` is deleted —
  audited explicitly (see Keep), inline styles if needed.
- **Helper import path breakage** — deleting `wizard/routes.py`/`tuner/routes.py`
  must not break `from blueprints.wizard.wizard import ...`. Mitigation: keep the
  package `__init__.py` and re-run `api_wizard`/`api_tuner` tests immediately
  after the partial deletion.
- **CWD-relative paths** — `static_folder` and `send_from_directory` must resolve
  from the app root, not the process CWD; use absolute paths derived from
  `app.py`'s location.

## Scope (YAGNI)

**In:** the `spa` blueprint (asset serving + SPA catch-all + JSON-404 guard),
repointing `static_folder`, deleting the 16 page blueprints (14 whole + 2
partial), their tests, the dead top-level templates and legacy `static/`, and the
`app.py` registration edits.

**Out (recorded, not built):**
- The post-update **"what's new" release-notes modal** — app-shell chrome,
  already deferred by the updater slice in `react-migration-backlog.md`.
- Any React source/feature change — this pass only serves the existing build and
  removes Flask pages.
- Deleting `common/*` helpers that pages used but `api_*` still import — out of
  scope; only page-blueprint packages and their exclusive templates/static go.

## Open items for the plan to resolve (not blockers)

- Confirm React performs the fresh-install → wizard redirect, so deleting
  `index()` loses nothing (else keep a `/`-only server 302).
- Confirm `templates/shutdown.html`'s render callsite (delete vs keep).
- Confirm the exact surviving-import path for `wizard.py`/`tuner.py` after
  `routes.py` deletion (package `__init__.py` contents).
- Enumerate the exact `test_page_*.py` files to delete (one per retired page).
