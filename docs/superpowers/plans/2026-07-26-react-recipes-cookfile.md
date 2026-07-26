# React Cook-File Browser Implementation Plan (recipes + cookfile, part 1 of 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port `blueprints/cookfile/` — the **cook-file browser and detail viewer** — to the React app: the saved-cook list (with upload, download, delete, pagination, sort), and the per-file page (chart with annotations, events table with totals, two CSV exports, title/probe-label editing, comments, media attach/upload/delete, thumbnail, repair/upgrade). The cook-file list is rendered as a second section of the already-shipped `/history` page, exactly as Flask does it, plus a new `/cookfiles/:filename` detail route.

---

## SCOPE DECISION: **SPLIT into two plans. This document is plan 1 (cookfile) written in full; plan 2 (recipes) is a task-list outline only at the bottom.**

Stated up front, with the evidence, because the backlog groups them and that grouping is only ~15% right.

### What recipes and cookfile genuinely share

1. **The archive container, not the schema.** Both are ZIP archives of JSON members read and written by the *same* four functions in `file_mgmt/common.py`: `read_json_file_data` (`file_mgmt/common.py:45`), `update_json_file_data` (`:106`), `remove_assets` (`:203`), `fixup_assets` (`:142`). Both unpack images through `file_mgmt/media.py` `add_asset` (`:26`) / `unpack_thumb` (`:165`) / `set_thumbnail` (`:153`) and serve them from the same `./static/img/tmp/{parent_id}/` symlink (`file_mgmt/common.py:85-88`, `file_mgmt/media.py:181-184`).
2. **The *listing* surface, and only the listing surface.** `file_mgmt/recipes.py:220-250` (`get_recipefilelist` / `get_recipefilelist_details`) and `blueprints/cookfile/routes.py:609-641` (`_get_cookfilelist` / `_get_cookfilelist_details`) are near-identical: scan folder for an extension → build `[{filename, title: "", thumbnail: ""}]` → `paginate_list(..., "filename", reverse, itemsperpage, page)` (`common/app.py:46`) → re-read each item's `metadata.json` and `unpack_thumb` it. The *only* differences are the extension (`.pfrecipe` vs `.pifire`) and the folder config key.
3. **Upload validation.** Both use `allowed_file` (`common/app.py:27`, extensions from `config.py:10`) + `werkzeug.utils.secure_filename`.

### What they do NOT share

| | cookfile (`.pifire`) | recipe (`.pfrecipe`) |
|---|---|---|
| JSON members | `metadata, graph_data, raw_data, graph_labels, events, comments, assets` (`file_mgmt/cookfile.py:181`) | `metadata, recipe, comments, assets` (`file_mgmt/recipes.py:203`) |
| metadata keys | `title, starttime, endtime, units, thumbnail, id, version` (`file_mgmt/cookfile.py:50-58`) | `author, username, id, title, description, image, thumbnail, units, prep_time, cook_time, rating, difficulty, version, food_probes` (`file_mgmt/recipes.py:34-51`) |
| body | time-series + metrics | `ingredients / instructions / steps` (`file_mgmt/recipes.py:145-147`) |
| comments | full CRUD (`blueprints/cookfile/routes.py:460-465`) | `comments.json` exists but **no route anywhere creates one** — confirmed by `tests/web/test_page_recipes.py:36-40` |
| verbs | chart, CSV export, repair/upgrade, label rename | run-a-program, live run status, food-probe count reshaping steps |
| detail route | `blueprints/history/routes.py:104` `opencookfile` renders `cookfile/index.html` | `blueprints/recipes/routes.py:126` `recipeview` returns a fragment |

Two dispatch tables of **11 form actions + 5 JSON actions** (cookfile) and **12 form actions + 2 JSON actions** (recipes) with **zero overlapping action names**. The detail pages share no component, no endpoint and no type.

### Why cookfile goes first

- It is a **hole in an already-migrated page**. `/history` shipped in React with the chart only; the Flask `/history` also owns the cook-file list (`blueprints/history/templates/history/index.html:64`, `:107-112`). React users currently cannot see, open, upload or delete a saved cook at all. `docs/superpowers/plans/2026-07-24-react-history-chart.md:16` deferred exactly this.
- It **reuses the shipped uPlot chart** (see "Chart reuse" below), so the largest single component is already written and tested.
- It **lands the shared listing endpoint**, which plan 2 then consumes for free (Task 2 below ships `/api/files/recipes` alongside `/api/files/cookfiles` — the one piece of genuine sharing, built once).
- The recipes editor is materially larger (five nested editors, an asset manager, and a live run-status view driven by control mode), and none of it is on the critical path of a page that already exists.

**Task count: 14** (this plan). Plan 2 outline: 17.

---

## Global Constraints

Copied verbatim; these are non-negotiable.

### Safety

- **SQLite (`pifire.db`) is authoritative.** `settings.json` is ONLY ever an export produced by `scripts/export-settings-json.py` when a human runs it — never live state. Nothing in this plan reads or writes `settings.json`.
- **The e2e suite is globally destructive to whatever backend it reaches and runs `workers: 1` for that reason** (`web-react/playwright.config.ts:23`). Any spec added here must assume another spec may have wiped history out from under it.
- **These pages accept FILE UPLOADS. Any test touching upload must not write outside a tmp path, and any path handling in the plan must address traversal on the uploaded filename explicitly.** Every new endpoint resolves the client-supplied name through one containment helper (Task 1) and every upload test runs against a `tempfile.mkdtemp` history folder.
- **Any test that can reach a shell-out path must neutralize `os.system`/`subprocess` FIRST; an `is_real_hardware()` flag is not enough (it defaults to True and has really rebooted the developer's machine twice).** Two live shell-outs are in reach of this work — `file_mgmt/recipes.py:192-193` and `file_mgmt/cookfile.py:163-164`, both `os.system(f"rm -rf {path}")`. **Task 1 deletes both** and the tests assert `os.system` was never called.

### Toolchain

- **bun, NOT npm.** `bun install`, `bun run <script>`. Commit `bun.lock`.
- **Gates every task must pass:** `bun run typecheck`, `bun run lint` (Biome 2.5.5 + ESLint), `bun run test` (rstest — NOT vitest; `bun run test`, never bare `bun test`), `bun run gen:types:check`.
- **rstest only globs `src/**/*.test.ts(x)` — a root-level test file silently never runs.** (Verified nuance: `web-react/rstest.config.ts:66` adds a third project `include: ["*.test.ts"]` that matches the *package root only*. Put every test authored by this plan under `src/`.)
- **Python:** `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/`. Format with `.venv/bin/ruff format` (**NOT** `uvx ruff` — the repo pins ruff <0.16 on purpose).
- **House React style:** no `setState` in `useEffect` for derived state (React Compiler); use render-phase adjustment. See `components/history/HistoryPage.tsx:69-74` for the exact idiom.

### Repo conventions

- Test runner is **@rstest/core** (`rs.fn`, `rs.mock`, `rs.stubGlobal`) — **`vi` does NOT exist**. `.test.tsx` → jsdom, `.test.ts` → node.
- **Biome**: `bun run lint` must exit 0. Exactly **2 pre-existing `react-refresh` warnings** are expected (`App.tsx`, `WizardShell.tsx`); any third is yours and must be fixed, not suppressed.
- **No suppressions**: no `biome-ignore`, no `eslint-disable`, no `@ts-ignore`, no `@ts-expect-error`, no `any`.
- **Coverage ≥75% lines per changed file** (`rstest.config.ts:58`).
- `react-refresh/only-export-components`: non-components live in their own module — pure helpers go under `src/helpers/files/`, never beside a component.
- **Do not modify the Flask `cookfile`, `recipes` or `history` blueprints' templates or JS.** The legacy pages keep working until they are retired. Backend *dedup* (Task 1) is shared and intended, and must leave every existing test green.
- **PEP 758 bare-tuple `except A, B` is canonical** in this repo (Python 3.14+). Do NOT rewrite it to `except (A, B)`.
- Reuse the existing `pf-*` class vocabulary (`components/settings/settings.css`, `components/dashboard/dashboard.css`, both imported at `src/main.tsx`). Do not introduce a second visual language.
- **Locators must not rely on loose text matching.** Use `exact: true`, or a role+name that cannot collide, or scope with `within` / `getByRole("region", { name })`.
- Test isolation: the suite must leave no artifacts in the repo root (`pifire.db`, `os_info.json`, `settings.json`, `pelletdb.json`). Check after running.
- **jj boundary protocol:** the controller runs `jj new` before each dispatch; the implementer finalizes with a single `jj desc -m`.

---

## Verified facts (checked against live code — do not re-derive, do not guess)

Every claim below cites the file and line it was read from.

### F1. The backlog's claim, adjudicated

> "recipes + cookfile — recipe editor and cook-file browser (share a data model and need a JSON listing endpoint that does not exist yet)" — `docs/superpowers/react-migration-backlog.md:297-298`

- **"need a JSON listing endpoint that does not exist yet" — TRUE.** Both listings return **HTML fragments**, not JSON:
  - recipes: `_recipes_form_recipefilelist` → `render_template("recipes/_recipefile_list.html", ...)` (`blueprints/recipes/routes.py:112-123`), driven by `$('#recipefilelist').load('/recipes/data', ...)` (`blueprints/recipes/static/recipes/js/recipes.js:314`).
  - cookfiles: `_cf_form_cookfilelist` → `render_template("cookfile/_cookfile_list.html", ...)` (`blueprints/cookfile/routes.py:256-267`), driven by `$('#cookfilelist').load('/cookfile', senddata)` (`blueprints/history/static/history/js/history.js:175`).
  There is no JSON listing route for either. Confirmed by the full route inventory in F5.
- **"share a data model" — MISLEADING, and the reason this plan splits.** They share a *container* and a *listing shape*; they share no schema, no action name and no page. See the scope decision above.

### F2. Cook-file page — every user-facing capability

Sources: `blueprints/cookfile/routes.py`, `blueprints/cookfile/templates/cookfile/index.html`, `blueprints/cookfile/static/cookfile/js/cookfile.js`, `blueprints/history/routes.py`, `blueprints/history/templates/history/index.html`.

**List (rendered on `/history`):**

| # | Capability | Flask wiring |
|---|---|---|
| 1 | Paginated list of `.pifire` files with title + thumbnail | `POST /cookfile` form `cookfilelist` (`cookfile/routes.py:256`); default call `gotoCFPage(1, true, 10)` (`history/js/history.js:337`) |
| 2 | Page navigation: First / prev / numbered / next / Last | `_cookfile_list.html:33-78` |
| 3 | Sort toggle (filename asc/desc) | `_cookfile_list.html` sort group; `reverse` through `paginate_list` |
| 4 | Items-per-page 5/10/25/50/100 | `_cookfile_list.html` dropdown |
| 5 | Open a file | `POST /history/cookfile` form `opencookfile` (`history/routes.py:104-129`) |
| 6 | Download a file | `POST /history/cookfile` form `dlcookfile` (`history/routes.py:130-135`) |
| 7 | Delete a file (modal-confirmed) | `POST /history/cookfile` form `delcookfile` (`history/routes.py:97-103`) |
| 8 | Upload a `.pifire` | `POST /cookfile` form `ulcookfilereq` + file `ulcookfile` (`cookfile/routes.py:184-197`) |
| 9 | "Send to Cloud" | `_cookfile_list.html:16` — **`disabled`. Not a capability. Do not port.** |

**Detail page (`cookfile/index.html`):**

| # | Capability | Flask wiring |
|---|---|---|
| 10 | Metadata card: title (editable), filename, units, start time, end time | `index.html:118-151`; save via `POST /cookfile/update {metadata, filename, editTitle}` (`routes.py:483-495`) |
| 11 | Thumbnail display + "Change Thumbnail" carousel over existing assets | `index.html:34-87`; `POST /cookfile` form `thumbSelected` (`routes.py:200-211`) |
| 12 | Upload a new thumbnail image | `POST /cookfile` multipart `ulthumbfn` + `ulthumbnail` (`routes.py:214-253`, sets thumb via `set_thumbnail`) |
| 13 | Probe **label rename** table (current name → new name, per probe) | `index.html:153-177`; `POST /cookfile/update {graph_labels, filename, old_label, new_label}` → `_rename_graph_label` (`routes.py:498-562`) rewrites `graph_labels.json` AND remaps `graph_data.json`'s `probe_mapper` + `chart_data[i].label` |
| 14 | Download the whole `.pifire` | `POST /cookfile` form `dl_cookfile` (`routes.py:159-162`) |
| 15 | **Chart** of the whole cook, loaded async after page render | `POST /cookfile {full_graph, filename}` (`routes.py:34-49`) → `refreshChart()` (`cookfile.js:102-129`) |
| 16 | Chart: annotations for mode changes, with an **on/off toggle** | `index.html:215-217`; `cookfile.js:135-149`; data from `prepare_annotations` (`common/app.py:92`) |
| 17 | Chart: wheel/pinch zoom, click-drag pan (x **and** y), **Reset Zoom** | `cookfile.js:22-39`, `:152-154` (chartjs-plugin-zoom) |
| 18 | Download raw-data CSV | `POST /cookfile` form `dl_graphfile` → `prepare_csv(cookfiledata["raw_data"], filename)` (`routes.py:174-181`) |
| 19 | Events table: Mode / Begin / End / Auger Time / Est. Pellet Use / Pellet Level Start / End | `index.html:247-296`; fields pre-computed at cook time by `process_metrics` (`common/common.py:521-556`) and stored in `events.json` |
| 20 | Events **Totals** row (cook time, auger time, est. usage, pellet levels) | `prepare_event_totals` (`common/app.py:147-170`) |
| 21 | Per-event detail modal (9 mode-specific renderings) | `index.html:297-338` |
| 22 | Download events/metrics CSV | `POST /cookfile` form `dl_eventfile` → `prepare_metrics_csv` (`routes.py:165-171`) |
| 23 | Comments: add | `POST /cookfile/update {comments, filename, commentnew}` (`routes.py:398-418`) |
| 24 | Comments: edit (fetch text) + save (with `edited` timestamp) | `routes.py:432-457` |
| 25 | Comments: delete | `routes.py:421-429` |
| 26 | Comment media: "Attach Media" picker showing every asset with selected state | `POST /cookfile {managemediacomment, cookfilename, commentid}` (`routes.py:65-91`) |
| 27 | Comment media: toggle one asset on/off a comment | `POST /cookfile/update {media, filename, commentid, assetfilename, state}` (`routes.py:565-585`) |
| 28 | Comment media: refresh a comment's thumbnails | `POST /cookfile {getcommentassets, ...}` (`routes.py:52-62`) |
| 29 | Media lightbox with prev/next **within a comment's asset list**, wrapping | `POST /cookfile {navimage, ...}` (`routes.py:108-142`) |
| 30 | Upload media (multi-file) | `POST /cookfile` multipart `ulmediafn` + `ulmedia` (`routes.py:214-253`) |
| 31 | Delete media: checkbox grid over all assets → remove selected | `POST /cookfile` form `delmedialist` + `delAssetlist` (`routes.py:324-342`) |
| 32 | List all assets (for the delete grid) | `POST /cookfile {getallmedia, cookfilename}` (`routes.py:94-105`) |
| 33 | Error page with **Attempt Conversion** (version errors) or **Attempt Repair** | `cookfile/cferror.html`; `upgradeCF` (`routes.py:305-321`) / `repairCF` (`routes.py:270-302`); error class from `classify_cookfile_error` (`common/app.py:309`) |
| 34 | Bare `GET /cookfile/` → **404** | `routes.py:362-363` `abort(404)` |

### F3. Recipes page — every user-facing capability (for plan 2; enumerated here so nothing is lost)

Sources: `blueprints/recipes/routes.py`, `templates/recipes/*.html`, `static/recipes/js/recipes.js`.

Welcome screen (Load Recipe / New Recipe) → toolbar (Recipe Book / Run / New / Edit). Capabilities:

1. Paginated recipe list with title + thumbnail (`routes.py:112`), default `gotoRFPage(1, false, 10)` (`recipes.js:84`) — note **`reverse=false`**, opposite of cookfiles.
2. Per-row: Run, View, Edit, Download, Delete (`_recipefile_list.html:16-25`); "Send to Cloud" is `disabled` — not a capability.
3. Pagination / sort / items-per-page — identical control set to cookfiles.
4. Upload a `.pfrecipe` (`routes.py:68-77`).
5. Download a `.pfrecipe`: `GET /recipes/data/download/<filename>` (`routes.py:439`, `:443-446`).
6. Delete a `.pfrecipe`: `POST /recipes/data {deletefile, filename}` (`routes.py:383-393`) — **already containment-guarded** via `secure_filename` + `os.path.isfile`.
7. **New recipe**: `recipeedit` with `filename == ""` → `create_recipefile()` (`routes.py:136-147`, `file_mgmt/recipes.py:133-194`).
8. View a recipe (read-only): image, author, star rating, prep/cook time, difficulty badge, food-probe count, description, ingredients table (with asset thumbs), instructions table (text + ingredients used + program step), program steps (`_recipe_view.html`).
9. Edit metadata fields, each saved independently: `title`, `author`, `rating` (1-5), `prep_time`, `cook_time`, `difficulty` (Easy/Intermediate/Hard/Advanced), `food_probes`, `description` (`_macro_recipes.html:119-268`; `routes.py:155-176`).
10. **`food_probes` is structural**: changing it grows/shrinks `trigger_temps.food` on **every** step (`routes.py:159-167`).
11. Ingredients: add / update (name + quantity) / delete — and updating a name **rewrites that ingredient's name inside every instruction that references it** (`routes.py:177-190`); deleting removes it from every instruction (`routes.py:227-237`).
12. Instructions: add / update (text, multi-select ingredients, program-step number) / delete (`routes.py:191-201`, `:238-242`).
13. Program steps: insert-at-index / update / delete (`routes.py:202-217`, `:243-247`, `:267-283`). Step fields: `mode` (Smoke|Hold in the editor; Startup/Shutdown exist in defaults), `hold_temp`, `timer`, `trigger_temps.primary`, `trigger_temps.food[]`, `pause`, `notify`, `message`. Max temp is 600 °F / 300 °C (`_macro_recipes.html:490-494`).
14. Asset manager per section (`splash` | `ingredients` | `instructions` | `delete`): select/deselect images, upload images, delete images (`routes.py:319-342`, `:396-428`).
15. Asset lightbox carousel (`recipeshowasset`, `routes.py:345-359`).
16. **Run a recipe**: `POST /api/control {updated:true, mode:"Recipe", recipe:{filename: "./recipes/"+name}}` (`recipes.js:270-293`).
17. **Live run status**: poll `reciperunstatus` every 4 s, highlight the active step from `control["recipe"]["step"]`, auto-scroll to it (`recipes.js:296-303`, `_recipe_status.html`).
18. On page load, if `control.mode == "Recipe"` already, jump straight into run status (`recipes.js:526-550`).

### F4. Chart reuse — how much of the History page's uPlot chart is genuinely reusable

**Reusable verbatim, zero changes (≈95% of the chart code):**

| File | Lines | Verdict |
|---|---|---|
| `components/history/HistoryChart.tsx` | 145 | **100% reusable.** Props are `{times: number[] (epoch SECONDS), series: ChartSeries[], height?}` — no history-specific coupling anywhere. Already does drag-zoom (`:119 cursor: { drag: { x: true, y: false } }`), live legend, resize handling, and rebuild-vs-`setData` shape tracking. |
| `components/history/tooltipPlugin.ts` | 85 | 100% reusable — takes a `SeriesShape[]`. |
| `components/history/tooltipPosition.ts`, `tooltipFormat.ts`, `tooltipRow.ts`, `scaleReset.ts` | — | 100% reusable, pure functions. |
| `components/history/historyChart.css` | — | 100% reusable. |
| `components/history/historyAdapter.ts` (`toChartInput`, `hasPlottableHistory`) | 67 | **Reusable, one guard needed.** It reads **only** `time_labels` and `chart_data` (`:29-31`, `:57-66`) — the exact two keys a cookfile's `full_graph` payload carries (`blueprints/cookfile/routes.py:42-47`). It does **not** touch `graph_labels`, `minutes`, `probe_mapper` or `annotations`, so a cookfile payload is structurally compatible. |

**NOT reusable:**

| Thing | Why |
|---|---|
| `components/history/HistoryPage.tsx` (198 lines) | Entirely about the *live* window: minutes control, 5 s auto-refresh poll, request-id in-flight tracking, a settings read for `history_page.autorefresh`, and a chart remount key derived from the window. A cook file is a fixed, finite dataset — none of that applies. The cookfile page fetches once. |
| Annotations | **`HistoryChart` has no annotation support at all** — documented at `helpers/history/historyApi.ts:44-46` ("HistoryChart has no annotation support yet, so these are carried but not drawn"). The Flask cookfile page draws them and has a toggle (F2 #16). **New work: Task 11.** |
| Wheel/pinch zoom + y-axis pan | `HistoryChart` gives x-only drag-zoom; Flask gives xy wheel/pinch zoom and xy pan (`cookfile.js:22-39`). **Accepted regression** — drag-zoom + a Reset button is the shipped `/history` interaction and matching it keeps one chart, not two. Called out in "Out of scope". |

**The one real adapter gap (verified):** `toChartInput` does `data.time_labels.map((ms) => ms / MS_PER_SECOND)` (`historyAdapter.ts:58`). Live history writes numeric epoch ms, so that is right there. A cook file's `graph_data.json` is whatever `prepare_chartdata` wrote at cook time (`file_mgmt/cookfile.py:465` — numeric ms), **but** `upgrade_cookfile`'s pre-v1.5 path and hand-built files can carry string labels (`tests/web/test_page_cookfile.py:151-152` builds `"time_labels": ["12:00:00", "12:05:00"]`). A string divided by 1000 yields `NaN` and uPlot silently renders nothing. **Task 11 adds `cookfileAdapter.ts` with a numeric guard rather than loosening the history adapter.**

### F5. API surface — what exists, and what must be newly created

**Already exists and is reused unchanged:**

- `GET /api/history/chart?minutes=` — `blueprints/api_history/routes.py:16`. Live history only; irrelevant to a saved file, but its shape is the template the new chart endpoint copies.
- `GET /api/settings`, `POST /api/control`, `GET|POST /api/pellets` — untouched.
- `/static/img/tmp/{parent_id}/...` and `/static/img/tmp/{parent_id}/thumbs/...` — asset serving, created as a symlink by `read_json_file_data` (`file_mgmt/common.py:85-88`) and `unpack_thumb` (`file_mgmt/media.py:181-184`). Proxied in dev by `rsbuild.config.ts:36`. **No new endpoint needed for images.**

**Exists but MUST NOT be reused from React — every one takes an unvalidated filesystem path from the client:**

| Route | Line | Problem |
|---|---|---|
| `POST /cookfile` `dl_cookfile` | `blueprints/cookfile/routes.py:159-162` | `send_file(request.form["dl_cookfile"])` with **no containment check at all** — arbitrary file read of anything the process can open. |
| `POST /cookfile` `full_graph` / `dl_graphfile` / `dl_eventfile` | `:34-49`, `:174-181`, `:165-171` | Raw client path into `read_cookfile` / `read_json_file_data` — arbitrary *zip* read. |
| `POST /cookfile` `thumbSelected` / `delmedialist` | `:205`, `:326` | `HISTORY_FOLDER + filename` string concat — `../` escapes. `delmedialist` reaches `remove_assets`, which **rewrites the archive in place** (`file_mgmt/common.py:257-274`). |
| `POST /cookfile` `repairCF` / `upgradeCF` | `:272`, `:307` | Raw client path into `upgrade_cookfile`, which **writes back** (`file_mgmt/cookfile.py:306`). |
| `POST /cookfile/update` (all four branches) | `:468-593` | Raw client path into `update_json_file_data` — arbitrary zip modification. `tests/web/test_page_cookfile.py:537-542` documents this as intended ("the caller (real JS) always sends the FULL path"). |

The codebase already knows how to do this correctly — `_safe_history_path` (`blueprints/history/routes.py:18-35`) realpath-contains, and `_recipes_json_deletefile` (`blueprints/recipes/routes.py:388-390`) uses `secure_filename` + `isfile`. It was simply never applied to the cookfile blueprint. **This is why the plan creates a new blueprint rather than pointing React at the old routes.**

Two more disqualifiers for reuse: the download routes are **POST-only**, so a plain `<a href download>` cannot use them; and `/cookfile` is **not proxied by the dev server** (`rsbuild.config.ts:27-37` proxies only `/socket.io`, `/api`, `/static/img`).

**MUST BE NEWLY CREATED — new blueprint `blueprints/api_files/`, `url_prefix="/api/files"`.**

Filenames travel as a **query/body parameter, never a path segment** (a path segment invites `%2F` decoding surprises and forces per-route escaping); every one is resolved by the single containment helper from Task 1.

| # | Method + path | Request | Response (200) |
|---|---|---|---|
| E1 | `GET /api/files/cookfiles` | `?page=1&per_page=10&reverse=true` | `{items: [{filename, title, thumbnail}], page, last_page, per_page, reverse, total}` |
| E2 | `GET /api/files/recipes` | same | same *(shared handler — the one genuinely shared piece; consumed by plan 2)* |
| E3 | `GET /api/files/cookfiles/detail` | `?file=NAME` | `{filename, metadata{…, starttime, endtime, starttime_epoch, endtime_epoch}, graph_labels, events[], event_totals{}, comments[], assets[]}` |
| E4 | `GET /api/files/cookfiles/chart` | `?file=NAME` | `{time_labels: number[], chart_data: Dataset[], probe_mapper, annotations}` |
| E5 | `GET /api/files/cookfiles/download` | `?file=NAME` | `.pifire` attachment |
| E6 | `GET /api/files/cookfiles/export` | `?file=NAME&kind=data\|events` | CSV attachment |
| E7 | `POST /api/files/cookfiles/upload` | multipart `file` | `{result:"OK", data:{filename}}` |
| E8 | `POST /api/files/cookfiles/delete` | `{file}` | `{result:"OK"}` |
| E9 | `POST /api/files/cookfiles/title` | `{file, title}` | `{result:"OK"}` |
| E10 | `POST /api/files/cookfiles/label` | `{file, old_label, new_label}` | `{result:"OK", data:{new_label_safe}}` |
| E11 | `POST /api/files/cookfiles/recover` | `{file, action:"upgrade"\|"repair"}` | `{result:"OK"}` |
| E12 | `POST /api/files/cookfiles/comments` | `{file, action:"add"\|"update"\|"delete", id?, text?}` | `{result:"OK", data:{id?, date?, time?, edited?, text?}}` |
| E13 | `POST /api/files/cookfiles/comments/assets` | `{file, id, assets: string[]}` | `{result:"OK", data:{assets}}` |
| E14 | `POST /api/files/cookfiles/assets/upload` | multipart `file` + repeatable `assets` | `{result:"OK", data:{assets: [{id, filename, type}]}}` |
| E15 | `POST /api/files/cookfiles/assets/delete` | `{file, assets: string[]}` | `{result:"OK"}` |
| E16 | `POST /api/files/cookfiles/thumbnail` | `{file, asset}` | `{result:"OK"}` |

**Error contract, uniform across E3-E16:**

- `400 {result:"Error", message:"bad_request", data:{field}}` — missing/ill-typed parameter, disallowed upload extension, bad `kind`.
- `404 {result:"Error", message:"not_found"}` — name fails containment, or resolves to a path that is not an existing file.
- `422 {result:"Error", message:<status string>, data:{errortype:"version"|"asset"|"other"}}` — the file exists but `read_cookfile` / `upgrade_cookfile` returned a non-`"OK"` status. `errortype` comes from `classify_cookfile_error` (`common/app.py:309`) and is what drives the React repair/upgrade prompt (F2 #33).

Write responses use `api_response(result, message, data)` (`common/app.py:422`), the same `{"data","result","message"}` envelope `helpers/pellets/pelletsApi.ts` already speaks (`result === "OK"`). Read responses (E1-E4) return bare payloads + an HTTP status, matching `blueprints/api_history/routes.py:48`.

### F6. Existing test coverage that pins current behaviour

- **`tests/web/test_page_cookfile.py`** — 17 Playwright tests, module-scoped `live_server`, `_isolated_history_folder` fixture that patches **three** places (`app.config["HISTORY_FOLDER"]`, `file_mgmt.cookfile.HISTORY_FOLDER`, `file_mgmt.common.HISTORY_FOLDER` — `:104-126`) and a `_write_cookfile` helper (`:129-214`) that hand-builds a valid `.pifire`. **This is the safety net; every one of these must stay green.** Reuse `_write_cookfile` verbatim in the new test modules.
  - **Not covered by it:** `dl_cookfile`, `dl_graphfile`, `dl_eventfile` — the three download branches have **no test at all**. Grep confirms no `dl_` string in the file. The new E5/E6 tests are the first coverage this behaviour has ever had.
- **`tests/web/test_page_recipes.py`** — 23 Playwright tests, same harness shape, `_isolated_recipe_folder` patching two places (`:81-98`).
- **`tests/unit/file_mgmt/test_cookfile.py`** — 25 unit tests over `prepare_chartdata` / `read_cookfile` / `upgrade_cookfile` / `create_cookfile`.
- **`tests/unit/file_mgmt/test_downsample.py`** — 28 tests over the LTTB selector.
- **`tests/unit/file_mgmt/test_recipes.py`** — 1 test: `create_recipefile` collision.
- **`tests/web/test_api_history.py`** — the model for the new endpoint tests.

**Git-log check performed as instructed.** `git log --oneline -15 -- blueprints/cookfile/ blueprints/recipes/ file_mgmt/` shows the latent-bug sweep already landed. Current behaviour is **not** historical behaviour in three places, all now pinned by tests:
- `GET /cookfile/` returns **404** (was a 200 JSON error envelope) — `routes.py:362-363`, pinned at `test_page_cookfile.py:225-231`.
- `ulcookfilereq` saves to the configured folder (was the literal string `"HISTORY_FOLDER"`) — pinned at `:487-526`.
- `_recipes_json_deletefile` validates with `secure_filename` (was `os.system("rm ...")`) — commit `9baaed66`, pinned at `test_page_recipes.py:494-546`.

### F7. Contradictions found between the docs and live code

1. **`docs/superpowers/react-migration-backlog.md:297-298` — "share a data model."** They share a container and a listing shape, not a data model. Adjudicated above; drove the split.
2. **`tests/web/test_page_recipes.py:53-63` module docstring — "Latent bug: `create_recipefile()` has no same-title dedup."** **STALE.** `file_mgmt/recipes.py:166-169` now has the disambiguation loop, and `tests/unit/file_mgmt/test_recipes.py:45` pins it. Task 1 fixes the docstring.
3. **Two live `os.system("rm -rf …")` shell-outs remain** at `file_mgmt/recipes.py:192-193` and `file_mgmt/cookfile.py:163-164`, despite commit `9baaed66` ("replace os.system rm with validated os.remove") — that commit only fixed the *delete route*, not the *create* path. Both are reachable from the code this work exercises. Task 1 removes them.
4. **`prepare_metrics_csv` / `prepare_csv` mis-name the output when `HISTORY_FOLDER` is not literally `./history/`.** `common/app.py:173-176` and `:203-213` do `filename.replace("./history/", "")` and then `"/tmp/" + filename + ".csv"`. Given `/tmp/pifire_test_history_x/history/E2E.pifire`, that composes `/tmp//tmp/pifire_test_history_x/history/E2E.pifire-Pifire-Export.csv` and `open()` raises. This is exactly why the three `dl_*` branches are untestable under the isolated-folder fixture — and why they have no tests. **E6 passes `os.path.basename(...)` in, so the new endpoint is correct under any folder; `common/app.py` is not modified.**
5. **`HistoryPage`'s "Export CSV" link is broken in the dev server.** `components/history/HistoryPage.tsx:169` links to `${BASE_URL}/history/export`, and `/history` is not in the dev proxy list (`rsbuild.config.ts:27-37`), so in `bun run dev` that link downloads the SPA's `index.html`. Reported, not fixed here (out of scope); every download this plan adds lives under `/api/files/`, which **is** proxied.
6. **`POST /api/control` deep-merges.** `_api_post_control` wraps the patch in a delta (`blueprints/api/routes.py:262`) and `_deep_assign` (`common/control_delta.py:229-238`) merges nested dicts, so `recipes.js`'s `{recipe: {filename}}` leaves `recipe.start_step` / `recipe.step` at whatever the previous run left. Harmless today (`start_step` is only ever `0`, and `recipe_mode` resets `step` on exit — `controller/runtime/controller.py:193`), but **plan 2 must post `{filename, start_step: 0, step: 0}` explicitly.** Recorded so it is not rediscovered.

---

## File Structure

Every file this plan creates or modifies, and its single responsibility.

### Python — created

| Path | Responsibility |
|---|---|
| `common/file_browser.py` | **Only** managed-folder concerns: realpath containment (`resolve_managed_file`), extension listing (`list_managed_files`), and the paginated `{filename,title,thumbnail}` listing (`browse_files`). No Flask imports beyond `current_app` avoidance — folder is always passed in. |
| `blueprints/api_files/__init__.py` | The `api_files_bp` Blueprint object. |
| `blueprints/api_files/routes.py` | Route table + request-parsing helpers (`_folder`, `_require_file`, `_json_field`, `_error`). No business logic. |
| `blueprints/api_files/cookfile_api.py` | One handler function per cook-file endpoint (E3-E16). |
| `tests/unit/common/test_file_browser.py` | Containment + listing/pagination unit tests. |
| `tests/web/test_api_files_listing.py` | E1/E2 HTTP tests. |
| `tests/web/test_api_files_cookfile_read.py` | E3-E6 HTTP tests. |
| `tests/web/test_api_files_cookfile_write.py` | E7-E16 HTTP tests. |

### Python — modified

| Path | Change |
|---|---|
| `app.py` | Register `api_files_bp` at `/api/files`. |
| `file_mgmt/recipes.py` | `os.system` → `shutil.rmtree`; `get_recipefilelist`/`get_recipefilelist_details` delegate to `common/file_browser.py`. |
| `file_mgmt/cookfile.py` | `os.system` → `shutil.rmtree`. |
| `blueprints/cookfile/routes.py` | `_get_cookfilelist`/`_get_cookfilelist_details` delegate to `common/file_browser.py`. Nothing else. |
| `tests/web/test_page_recipes.py` | Correct the stale "no same-title dedup" docstring paragraph. |

### React — created

| Path | Responsibility |
|---|---|
| `web-react/src/helpers/files/fileTypes.ts` | Shared listing types (`FileListItem`, `FileListing`). Used by cookfiles now, recipes in plan 2. |
| `web-react/src/helpers/files/filesApi.ts` (+ `.test.ts`) | `fetchFileListing(kind, opts)` for E1/E2 only. |
| `web-react/src/helpers/files/cookfileApi.ts` (+ `.test.ts`) | Cook-file types + one function per E3-E16. |
| `web-react/src/components/cookfiles/CookFileList.tsx` (+ `.test.tsx`) | The list section: table, pagination, sort, per-page, upload, download, delete. |
| `web-react/src/components/cookfiles/CookFilePage.tsx` (+ `.test.tsx`) | `/cookfiles/:filename` route: fetch detail, own the error/repair state, compose the panels. |
| `web-react/src/components/cookfiles/CookFileMeta.tsx` (+ `.test.tsx`) | Metadata card, title edit, probe-label rename, thumbnail, download buttons. |
| `web-react/src/components/cookfiles/CookFileChart.tsx` (+ `.test.tsx`) | Fetches E4, adapts, renders `HistoryChart`, owns annotation toggle + reset zoom. |
| `web-react/src/components/cookfiles/EventsTable.tsx` (+ `.test.tsx`) | Events table + totals row + per-event detail disclosure. |
| `web-react/src/components/cookfiles/CommentList.tsx` (+ `.test.tsx`) | Comment add/edit/delete + attached thumbnails + lightbox. |
| `web-react/src/components/cookfiles/MediaPanel.tsx` (+ `.test.tsx`) | Asset grid, upload, delete-selected, set-thumbnail, attach-to-comment. |
| `web-react/src/components/cookfiles/cookfileAdapter.ts` (+ `.test.ts`) | E4 payload → `ChartInput`, with the numeric-time guard (F4). |
| `web-react/src/components/cookfiles/cookfiles.css` | Layout for the list table and the media grid. |
| `web-react/src/components/history/annotationPlugin.ts` (+ `.test.ts`) | uPlot `draw`-hook plugin drawing mode-change lines + labels. Lives beside the chart it extends. |
| `web-react/tests/e2e/cookfiles.spec.ts` | Round trip against the real backend. |

### React — modified

| Path | Change |
|---|---|
| `web-react/src/components/App.tsx` | Add the `/cookfiles/:filename` route under `AppShell`. |
| `web-react/src/components/history/HistoryChart.tsx` (+ `.test.tsx`) | Optional `annotations` prop → `annotationPlugin`. Default off; `/history` behaviour unchanged. |
| `web-react/src/components/history/HistoryPage.tsx` (+ `.test.tsx`) | Render `<CookFileList />` below the chart, as Flask does. |

---

# Tasks

### Task 1: `common/file_browser.py` — containment, listing, and the death of two `os.system` calls

**Files:**
- Create: `common/file_browser.py`
- Create: `tests/unit/common/test_file_browser.py`
- Modify: `file_mgmt/recipes.py` (lines 192-193, 220-250)
- Modify: `file_mgmt/cookfile.py` (lines 163-164)
- Modify: `blueprints/cookfile/routes.py` (lines 609-641)
- Modify: `tests/web/test_page_recipes.py` (module docstring, lines 47-59 -- CORRECTED, the plan said 53-63)

**Interfaces:**
- Produces:
  ```python
  def resolve_managed_file(folder: str, name: str) -> str | None
  def list_managed_files(folder: str, extension: str) -> list[str]
  def browse_files(folder: str, extension: str, *, page: int = 1,
                   per_page: int = 10, reverse: bool = True) -> dict
  ```
  `browse_files` returns
  `{"items": [{"filename": str, "title": str, "thumbnail": str}], "page": int, "last_page": int, "per_page": int, "reverse": bool, "total": int}`.
  `thumbnail` is the value `unpack_thumb` returns — a **relative** `"{parent_id}/{name}"` to be prefixed with `/static/img/tmp/` — or `""`.
- Consumes: `common.app.paginate_list`, `file_mgmt.common.read_json_file_data`, `file_mgmt.media.unpack_thumb`.

- [x] **Step 1: Neutralize the shell-outs BEFORE running anything**

Two live `os.system("rm -rf …")` calls sit in code this task's tests execute. Confirm they are still there, then remove them — do not test around them.

```bash
cd /home/dannyb/sources/PiFire
grep -rn "os.system\|subprocess\|sudo\|reboot\|shutdown -" file_mgmt/ common/file_browser.py 2>/dev/null
```

Expected before the fix (exactly two hits):
```
file_mgmt/recipes.py:193:    os.system(command)
file_mgmt/cookfile.py:164:        os.system(command)
```

Expected after Step 2: **no hits at all.**

- [x] **Step 2: Replace both with `shutil.rmtree`**

In `file_mgmt/recipes.py`, add `import shutil` beside the existing `import os` (line 15) and replace lines 191-194:

```python
    # 4. Cleanup temporary files
    shutil.rmtree(recipe_file_path, ignore_errors=True)
    return filename
```

In `file_mgmt/cookfile.py`, add `import shutil` beside `import os` (line 15) and replace lines 162-164:

```python
        # 4. Cleanup temporary files
        shutil.rmtree(cook_file_path, ignore_errors=True)
```

Rationale to put in neither file (it is obvious from the diff): a title is user-controlled and reaches the shell unquoted; `rm -rf` on a crafted title is a live command-injection and a live data-loss primitive. `shutil.rmtree` cannot be injected into.

- [x] **Step 3: Write the failing containment/listing tests**

Create `tests/unit/common/test_file_browser.py`:

```python
"""Managed-folder browsing: containment, extension listing, pagination.

The cookfile blueprint resolves client-supplied filenames by string
concatenation (`HISTORY_FOLDER + filename`, blueprints/cookfile/routes.py:205)
or not at all (`send_file(request.form["dl_cookfile"])`, :162). The history
blueprint got this right (`_safe_history_path`, blueprints/history/routes.py:18)
and the recipes delete route got it right a different way (`secure_filename`,
blueprints/recipes/routes.py:388). This module is the single implementation the
new /api/files surface uses, so there is one place to be right.

secure_filename is deliberately NOT used: cook titles are user-chosen and
legitimately contain spaces and parentheses that secure_filename mangles,
silently breaking opens and downloads for valid files. A realpath-containment
check validates the resulting PATH instead of the name's character set --
the same reasoning as _safe_history_path's docstring.
"""

import os

import pytest

from common.file_browser import browse_files, list_managed_files, resolve_managed_file


@pytest.fixture
def folder(tmp_path):
    d = tmp_path / "history"
    d.mkdir()
    return str(d) + "/"


def test_resolves_a_plain_name(folder):
    open(os.path.join(folder, "A-CookFile.pifire"), "w").close()
    resolved = resolve_managed_file(folder, "A-CookFile.pifire")
    assert resolved == os.path.realpath(os.path.join(folder, "A-CookFile.pifire"))


def test_allows_spaces_and_parentheses_that_secure_filename_would_mangle(folder):
    name = "Brisket (Sunday) #2.pifire"
    open(os.path.join(folder, name), "w").close()
    assert resolve_managed_file(folder, name) is not None


def test_rejects_parent_traversal(folder):
    assert resolve_managed_file(folder, "../../etc/passwd") is None
    assert resolve_managed_file(folder, "../secret.pifire") is None


def test_rejects_absolute_paths(folder):
    assert resolve_managed_file(folder, "/etc/passwd") is None


def test_rejects_empty_and_the_folder_itself(folder):
    assert resolve_managed_file(folder, "") is None
    assert resolve_managed_file(folder, ".") is None


def test_rejects_a_symlink_pointing_outside(folder, tmp_path):
    outside = tmp_path / "outside.pifire"
    outside.write_text("x")
    os.symlink(str(outside), os.path.join(folder, "link.pifire"))
    assert resolve_managed_file(folder, "link.pifire") is None


def test_resolves_names_that_do_not_exist_yet(folder):
    """Containment is a PATH check, not an existence check -- upload needs a
    contained destination for a file that is not there yet. Existence is the
    caller's separate assertion."""
    assert resolve_managed_file(folder, "New.pifire") is not None


def test_list_filters_by_extension_and_creates_a_missing_folder(tmp_path):
    missing = str(tmp_path / "nope") + "/"
    assert list_managed_files(missing, ".pifire") == []
    assert os.path.isdir(missing)


def test_list_filters_by_extension(folder):
    for name in ("a.pifire", "b.pifire", "c.pfrecipe", "notes.txt"):
        open(os.path.join(folder, name), "w").close()
    assert sorted(list_managed_files(folder, ".pifire")) == ["a.pifire", "b.pifire"]
    assert list_managed_files(folder, ".pfrecipe") == ["c.pfrecipe"]


def test_browse_paginates_sorts_and_reports_totals(folder):
    for i in range(25):
        open(os.path.join(folder, f"cook-{i:02d}.pifire"), "w").close()

    page1 = browse_files(folder, ".pifire", page=1, per_page=10, reverse=False)
    assert [i["filename"] for i in page1["items"]][:2] == ["cook-00.pifire", "cook-01.pifire"]
    assert page1["total"] == 25
    assert page1["last_page"] == 3
    assert page1["page"] == 1
    assert page1["per_page"] == 10
    assert page1["reverse"] is False

    rev = browse_files(folder, ".pifire", page=1, per_page=10, reverse=True)
    assert rev["items"][0]["filename"] == "cook-24.pifire"
    assert rev["reverse"] is True


def test_browse_clamps_a_page_past_the_end(folder):
    for i in range(3):
        open(os.path.join(folder, f"c{i}.pifire"), "w").close()
    out = browse_files(folder, ".pifire", page=99, per_page=10)
    assert out["page"] == 1
    assert out["last_page"] == 1
    assert len(out["items"]) == 3


def test_browse_reports_ERROR_title_for_an_unreadable_archive(folder):
    with open(os.path.join(folder, "broken.pifire"), "w") as f:
        f.write("not a zip")
    out = browse_files(folder, ".pifire")
    assert out["items"] == [{"filename": "broken.pifire", "title": "ERROR", "thumbnail": ""}]


def test_browse_of_an_empty_folder(folder):
    out = browse_files(folder, ".pifire")
    assert out == {
        "items": [],
        "page": 1,
        "last_page": 1,
        "per_page": 10,
        "reverse": True,
        "total": 0,
    }
```

Run it — every test must fail with `ModuleNotFoundError: No module named 'common.file_browser'`:

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/common/test_file_browser.py -q
```

- [x] **Step 4: Write `common/file_browser.py`**

```python
#!/usr/bin/env python3
"""
PiFire - Managed File Folder Browsing
=====================================

One implementation of the three things every "folder of PiFire archives"
surface needs: containment-checking a client-supplied name, listing a folder
by extension, and building a paginated {filename, title, thumbnail} listing.

Extracted from the two near-identical copies that already existed --
file_mgmt/recipes.py's get_recipefilelist/get_recipefilelist_details and
blueprints/cookfile/routes.py's _get_cookfilelist/_get_cookfilelist_details --
which differed only in the extension and the folder they read. Both now
delegate here, so the legacy pages and the new /api/files surface cannot drift.
"""

import os

from common.app import paginate_list
from file_mgmt.common import read_json_file_data
from file_mgmt.media import unpack_thumb


def resolve_managed_file(folder, name):
    """Resolve `name` against `folder` and require the result to stay inside it.

    Returns the resolved absolute path, or None if `name` is empty or would
    escape `folder` (via `../`, an absolute path, or a symlink pointing out).

    Deliberately NOT secure_filename: cook and recipe titles are user-chosen and
    may legitimately contain spaces, parentheses and `#`, which secure_filename
    mangles -- silently breaking opens/downloads/deletes for valid files. This
    validates the resulting PATH instead of the name's character set. Same
    reasoning, and same implementation, as blueprints/history/routes.py's
    _safe_history_path, which this supersedes for new code.

    Existence is NOT checked here: upload needs a contained destination for a
    file that does not exist yet. Callers that require an existing file assert
    os.path.isfile() themselves.
    """
    if not name:
        return None
    base = os.path.realpath(folder)
    candidate = os.path.realpath(os.path.join(folder, name))
    if candidate == base or not candidate.startswith(base + os.sep):
        return None
    return candidate


def list_managed_files(folder, extension):
    """Bare filenames in `folder` ending in `extension`. Creates `folder` if it
    is missing -- both callers this replaces did, and a first-boot install has
    neither ./history/ nor ./recipes/."""
    if not os.path.exists(folder):
        os.makedirs(folder, exist_ok=True)
    return [name for name in os.listdir(folder) if name.endswith(extension)]


def file_details(folder, filenames):
    """Read each archive's metadata.json for its title and unpack its thumbnail.

    A file that will not open reports title "ERROR" and no thumbnail rather than
    raising -- one corrupt archive must not blank the whole listing, which is
    the behaviour both replaced functions had.
    """
    out = []
    for name in filenames:
        path = folder + name
        metadata, status = read_json_file_data(path, "metadata")
        if status != "OK":
            out.append({"filename": name, "title": "ERROR", "thumbnail": ""})
            continue
        thumbnail = (
            unpack_thumb(metadata["thumbnail"], path, metadata["id"]) if "thumbnail" in metadata else ""
        )
        out.append({"filename": name, "title": metadata["title"], "thumbnail": thumbnail})
    return out


def browse_files(folder, extension, *, page=1, per_page=10, reverse=True):
    """Paginated listing of a managed folder.

    Only the requested page's archives are opened -- paginate_list slices first,
    then file_details reads. That is the existing behaviour and it matters: a
    hundred-cook folder would otherwise unzip a hundred archives per request.
    """
    names = [{"filename": name} for name in list_managed_files(folder, extension)]
    total = len(names)
    pagination = paginate_list(names, "filename", reverse, per_page, page)
    return {
        "items": file_details(folder, [item["filename"] for item in pagination["displaydata"]]),
        "page": pagination["curpage"],
        "last_page": pagination["lastpage"],
        "per_page": pagination["itemspage"],
        "reverse": bool(reverse),
        "total": total,
    }
```

Run — all green:

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/common/test_file_browser.py -q
```

Expected: `16 passed`.

- [x] **Step 5: Delegate the two legacy listing pairs**

In `file_mgmt/recipes.py`, replace `get_recipefilelist` / `get_recipefilelist_details` (lines 220-250) with:

```python
def get_recipefilelist(folder=None):
    if folder is None:
        folder = current_app.config["RECIPE_FOLDER"]
    return list_managed_files(folder, ".pfrecipe")


def get_recipefilelist_details(recipefilelist):
    #  RECIPE_FOLDER, not current_app.config: this is the module constant the
    #  original read, and tests/web/test_page_recipes.py patches BOTH. Changing
    #  which one is read here would silently move that fixture's target.
    return file_details(RECIPE_FOLDER, [item["filename"] for item in recipefilelist])
```

with `from common.file_browser import file_details, list_managed_files` added to the imports. (CORRECTED: the helper is exported as `file_details`, not `_details` -- importing a leading-underscore name across modules is not something to ship.) Drop the now-unused `read_json_file_data` / `unpack_thumb` imports if nothing else in the module uses them (check first — `read_json_file_data` IS still used by `read_recipefile`).

In `blueprints/cookfile/routes.py`, replace `_get_cookfilelist` / `_get_cookfilelist_details` (lines 609-641) with the same two-liner shape against `.pifire` and `current_app.config["HISTORY_FOLDER"]`.

- [x] **Step 6: Fix the stale docstring**

In `tests/web/test_page_recipes.py`, replace the "Latent bug: `create_recipefile()` has no same-title dedup" paragraph (lines 47-59) with:

```
Same-title collisions (FIXED, pinned elsewhere)
------------------------------------------------
`create_recipefile()` derives its title from
`datetime.now().strftime("%Y-%m-%d--%H%M")` (minute resolution). It used to
have no collision check, so two recipes created in the same clock-minute
silently truncated each other. It now disambiguates with a `-N` suffix
(file_mgmt/recipes.py:166-170), pinned by
tests/unit/file_mgmt/test_recipes.py. `_create_recipe()` below still picks
the most-recently-modified `.pfrecipe` rather than assuming a name, which
stays correct either way.
```

- [x] **Step 7: Verify nothing regressed, then commit**

```bash
grep -rn "os.system" file_mgmt/ ; echo "exit=$?"
```
Expected: no output, `exit=1`.

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest \
  tests/unit/common/test_file_browser.py tests/unit/file_mgmt/ tests/web/test_page_cookfile.py \
  tests/web/test_page_recipes.py tests/web/test_page_history.py -q
```
Expected: all pass (cookfile/recipes/history web tests SKIP in a no-chromium worktree — re-run them in the main checkout before merging).

```bash
.venv/bin/ruff format common/file_browser.py file_mgmt/recipes.py file_mgmt/cookfile.py \
  blueprints/cookfile/routes.py tests/unit/common/test_file_browser.py tests/web/test_page_recipes.py
jj desc -m "refactor(files): shared managed-folder browsing + kill two os.system rm -rf calls"
```

**Deliverable:** a containment helper with 16 passing tests, two shell-outs gone, and both legacy listings delegating to one implementation with every existing test still green.

---

### Task 2: `GET /api/files/cookfiles` and `GET /api/files/recipes` (E1, E2)

**Files:**
- Create: `blueprints/api_files/__init__.py`, `blueprints/api_files/routes.py`
- Create: `tests/web/test_api_files_listing.py`
- Modify: `app.py` (imports near line 85, registrations near line 106)

**Interfaces:**
- Produces: `GET /api/files/<kind>` where `<kind>` ∈ `{cookfiles, recipes}`.
  - Query: `page` (int ≥ 1, default 1), `per_page` (int in `{5,10,25,50,100}`, default 10), `reverse` (`"true"`/`"false"`, default `"true"`).
  - 200 → `{"items":[{"filename","title","thumbnail"}], "page","last_page","per_page","reverse","total"}`
  - 400 → `{"result":"Error","message":"bad_request","data":{"field": "<name>"}}`
- Consumes: `common.file_browser.browse_files`, `common.app.api_response`.

- [x] **Step 1: Write the failing HTTP tests**

Create `tests/web/test_api_files_listing.py`.

**CORRECTED against live code.** The draft below reaches for the playwright
`live_server`/`page` fixtures and `pytestmark = requires_chromium`. That is wrong for
this surface and contradicts the plan's own F6, which names
`tests/web/test_api_history.py` as "the model for the new endpoint tests" -- and that
module uses `flask_app.test_client()` + the `ds` fixture. These endpoints have no DOM,
so playwright buys nothing, and `requires_chromium` would make every test here AND the
traversal/containment tests in Tasks 3-7 SKIP silently on a chromium-less checkout. A
security test that can silently not run is not a security test. All /api/files test
modules therefore use `test_client` + `ds`. The archive builders live in
`tests/web/archive_builders.py` (a shared, non-collected module) instead of being
copy-pasted into three new test modules.

Original draft, for reference:

```python
"""GET /api/files/cookfiles and /api/files/recipes -- the JSON listing
endpoints the React file browser needs and that did not exist before.

Both legacy listings return HTML fragments (blueprints/cookfile/routes.py:267
renders cookfile/_cookfile_list.html; blueprints/recipes/routes.py:123 renders
recipes/_recipefile_list.html), so there was nothing for a typed client to
consume. These are read-only and share one handler -- the single piece the
cookfile and recipe surfaces genuinely have in common.
"""

import json
import os
import shutil
import tempfile
import time
import uuid
import zipfile

import pytest

from tests.web.conftest import read_settings_from_server, requires_chromium

pytestmark = requires_chromium


@pytest.fixture(scope="module", autouse=True)
def _isolated_folders(live_server):
    """Patch all FIVE places a folder is read -- app.config for each kind, plus
    the module-level constants file_mgmt.cookfile / file_mgmt.common /
    file_mgmt.recipes each define separately. Missing any one of them makes a
    test write into the real repo checkout."""
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


def _write_cookfile(history_dir, title):
    """Minimal-but-valid .pifire. Same construction as
    tests/web/test_page_cookfile.py::_write_cookfile -- see that module's
    docstring for why every member is load-bearing."""
    from common.defaults import default_metrics

    version = read_settings_from_server()["versions"]["cookfile"]
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - 3600_000
    ev = default_metrics()
    ev.update({"id": 0, "starttime": start_ms, "endtime": now_ms, "mode": "Smoke",
               "augerontime": 120, "pellet_level_start": 100, "pellet_level_end": 95})
    ev2 = default_metrics()
    ev2.update({"id": 1, "starttime": now_ms, "endtime": now_ms, "mode": "Stop",
                "augerontime": 30, "pellet_level_start": 95, "pellet_level_end": 90})
    files = {
        "metadata.json": {"title": title, "starttime": start_ms, "endtime": now_ms, "units": "F",
                          "thumbnail": "", "id": str(uuid.uuid4()), "version": version},
        "graph_data.json": {"time_labels": [start_ms, now_ms],
                            "chart_data": [{"label": "Grill", "borderColor": "#f00",
                                            "data": [{"x": start_ms, "y": 225},
                                                     {"x": now_ms, "y": 230}]}],
                            "probe_mapper": {"probes": {"grill1": 0}, "targets": {}, "primarysp": {}}},
        "raw_data.json": [{"T": start_ms, "P": {"grill1": 225}, "PSP": 225,
                           "F": {"probe1": 150}, "NT": {"grill1": 225, "probe1": 165}, "AUX": {}}],
        "graph_labels.json": {"probes": {"grill1": "Grill"}, "targets": {}, "primarysp": {}},
        "events.json": [ev, ev2],
        "comments.json": [],
        "assets.json": [],
    }
    name = f"{title}.pifire"
    with zipfile.ZipFile(history_dir + name, "w", zipfile.ZIP_DEFLATED) as z:
        for member, data in files.items():
            z.writestr(member, json.dumps(data))
    return name


def _write_recipe(recipe_dir, title):
    files = {
        "metadata.json": {"title": title, "id": str(uuid.uuid4()), "author": "", "description": "",
                          "image": "", "thumbnail": "", "units": "F", "prep_time": 0,
                          "cook_time": 0, "rating": 5, "difficulty": "Easy", "version": "1.1.0",
                          "food_probes": 2, "username": ""},
        "recipe.json": {"ingredients": [], "instructions": [], "steps": []},
        "comments.json": [],
        "assets.json": [],
    }
    name = f"{title}.pfrecipe"
    with zipfile.ZipFile(recipe_dir + name, "w", zipfile.ZIP_DEFLATED) as z:
        for member, data in files.items():
            z.writestr(member, json.dumps(data))
    return name


def test_cookfiles_listing_returns_json_with_titles(live_server, page, _isolated_folders):
    history_dir, _ = _isolated_folders
    _write_cookfile(history_dir, "AAA-Cook")
    _write_cookfile(history_dir, "BBB-Cook")

    resp = page.request.get(f"{live_server}/api/files/cookfiles?page=1&per_page=10&reverse=false")
    assert resp.status == 200
    body = resp.json()
    names = [i["filename"] for i in body["items"]]
    assert "AAA-Cook.pifire" in names and "BBB-Cook.pifire" in names
    titles = {i["filename"]: i["title"] for i in body["items"]}
    assert titles["AAA-Cook.pifire"] == "AAA-Cook"
    assert body["page"] == 1
    assert body["per_page"] == 10
    assert body["reverse"] is False
    assert body["total"] >= 2


def test_cookfiles_listing_defaults_match_the_flask_page(live_server, page, _isolated_folders):
    """history.js:337 calls gotoCFPage(1, true, 10) -- page 1, reverse, 10 per
    page. The endpoint's defaults must be the same so the React list opens on
    the same rows the Flask list did."""
    resp = page.request.get(f"{live_server}/api/files/cookfiles")
    assert resp.status == 200
    body = resp.json()
    assert (body["page"], body["per_page"], body["reverse"]) == (1, 10, True)


def test_recipes_listing_uses_the_same_shape(live_server, page, _isolated_folders):
    _, recipe_dir = _isolated_folders
    _write_recipe(recipe_dir, "Pulled-Pork")

    resp = page.request.get(f"{live_server}/api/files/recipes")
    assert resp.status == 200
    body = resp.json()
    assert set(body) == {"items", "page", "last_page", "per_page", "reverse", "total"}
    assert {"filename": "Pulled-Pork.pfrecipe", "title": "Pulled-Pork", "thumbnail": ""} in body["items"]


def test_the_two_kinds_do_not_see_each_others_files(live_server, page, _isolated_folders):
    history_dir, recipe_dir = _isolated_folders
    _write_cookfile(history_dir, "Only-Cook")
    _write_recipe(recipe_dir, "Only-Recipe")

    cooks = page.request.get(f"{live_server}/api/files/cookfiles").json()
    recipes = page.request.get(f"{live_server}/api/files/recipes").json()
    assert all(i["filename"].endswith(".pifire") for i in cooks["items"])
    assert all(i["filename"].endswith(".pfrecipe") for i in recipes["items"])


def test_pagination_reports_last_page(live_server, page, _isolated_folders):
    history_dir, _ = _isolated_folders
    for i in range(12):
        _write_cookfile(history_dir, f"Page-{i:02d}")
    body = page.request.get(f"{live_server}/api/files/cookfiles?per_page=5").json()
    assert body["per_page"] == 5
    assert body["last_page"] >= 3
    assert len(body["items"]) == 5


def test_unknown_kind_is_404(live_server, page):
    resp = page.request.get(f"{live_server}/api/files/pelletfiles")
    assert resp.status == 404


@pytest.mark.parametrize(
    "query,field",
    [("page=abc", "page"), ("page=0", "page"), ("per_page=7", "per_page"), ("per_page=xyz", "per_page")],
)
def test_bad_query_parameters_are_400_and_name_the_field(live_server, page, query, field):
    resp = page.request.get(f"{live_server}/api/files/cookfiles?{query}")
    assert resp.status == 400
    body = resp.json()
    assert body["result"] == "Error"
    assert body["message"] == "bad_request"
    assert body["data"]["field"] == field


def test_listing_never_leaks_a_filesystem_path(live_server, page, _isolated_folders):
    """Only bare filenames cross the wire. A client that is handed a path will
    send one back, and every legacy cookfile route that accepts one is an
    unvalidated open (blueprints/cookfile/routes.py:162)."""
    history_dir, _ = _isolated_folders
    _write_cookfile(history_dir, "NoPath-Cook")
    body = page.request.get(f"{live_server}/api/files/cookfiles").json()
    for item in body["items"]:
        assert "/" not in item["filename"]
        assert history_dir not in json.dumps(item)
```

- [x] **Step 2: Create the blueprint**

`blueprints/api_files/__init__.py`:

```python
from flask import Blueprint

api_files_bp = Blueprint("api_files_bp", __name__, url_prefix="/api/files")

from . import routes  # noqa: E402,F401
```

`blueprints/api_files/routes.py`:

```python
"""Read-only and write JSON endpoints for PiFire's managed archive folders.

Why a new blueprint instead of pointing React at /cookfile and /recipes:
every mutating action in blueprints/cookfile/routes.py takes a FILESYSTEM PATH
from the client and uses it unvalidated -- `send_file(request.form
["dl_cookfile"])` at :162 is an arbitrary file read, and cookfile_update's four
branches feed a raw client path to update_json_file_data. The legacy pages keep
those routes; new code does not get to inherit them. Here, a client sends a BARE
FILENAME and the server resolves it through common.file_browser
.resolve_managed_file, which realpath-contains it to the configured folder.

Two more reasons reuse was not on the table: the legacy download actions are
POST-only, so an <a href download> cannot use them; and the dev server proxies
only /socket.io, /api and /static/img (web-react/rsbuild.config.ts:27-37), so a
/cookfile URL does not even reach Flask in `bun run dev`.
"""

from flask import current_app, jsonify, request

from common.app import api_response
from common.file_browser import browse_files

from . import api_files_bp

#: kind -> (app.config folder key, extension). The ONE place the two archive
#: kinds share behaviour; everything below this line is cookfile-specific.
_KINDS = {
    "cookfiles": ("HISTORY_FOLDER", ".pifire"),
    "recipes": ("RECIPE_FOLDER", ".pfrecipe"),
}

#: Mirrors the per-page dropdown the Flask lists offer
#: (cookfile/_cookfile_list.html, recipes/_recipefile_list.html). A whitelist,
#: not a range: an unbounded per_page is an unbounded number of archives to
#: unzip per request.
_PER_PAGE_CHOICES = (5, 10, 25, 50, 100)


def error(message, status, **data):
    return jsonify(api_response("Error", message, data or None)), status


def _int_arg(name, default, *, minimum=1, choices=None):
    """Parse a query int, or raise ValueError carrying the offending field."""
    raw = request.args.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except TypeError, ValueError:
        raise ValueError(name)
    if value < minimum:
        raise ValueError(name)
    if choices is not None and value not in choices:
        raise ValueError(name)
    return value


@api_files_bp.route("/<kind>", methods=["GET"])
def file_listing(kind):
    entry = _KINDS.get(kind)
    if entry is None:
        return error("not_found", 404, kind=kind)
    folder_key, extension = entry

    try:
        page = _int_arg("page", 1)
        per_page = _int_arg("per_page", 10, choices=_PER_PAGE_CHOICES)
    except ValueError as exc:
        return error("bad_request", 400, field=str(exc))

    reverse = request.args.get("reverse", "true").lower() != "false"
    folder = current_app.config[folder_key]
    return jsonify(browse_files(folder, extension, page=page, per_page=per_page, reverse=reverse)), 200
```

- [x] **Step 3: Register it**

In `app.py`, beside the `api_history` import (line 85):

```python
from blueprints.api_files import api_files_bp
```

and beside its registration (line 106):

```python
app.register_blueprint(api_files_bp, url_prefix="/api/files")
```

- [x] **Step 4: Verify and commit**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_files_listing.py -q
```
Expected: `13 passed` (the harness change means they never skip).

```bash
.venv/bin/ruff format blueprints/api_files/ tests/web/test_api_files_listing.py app.py
jj desc -m "feat(api-files): JSON listing endpoints for cook files and recipes"
```

**Deliverable:** `curl localhost:5000/api/files/cookfiles` returns JSON. The listing endpoint the backlog said did not exist now exists, for both kinds, from one handler.

---

### Task 3: cook-file detail + chart endpoints (E3, E4)

**Files:**
- Create: `blueprints/api_files/cookfile_api.py`
- Create: `tests/web/test_api_files_cookfile_read.py`
- Modify: `blueprints/api_files/routes.py` (route registrations + `_require_file` helper)

**Interfaces:**
- Produces:
  ```python
  def load_cookfile(name) -> tuple[dict | None, str, str]   # (struct, path, status)
  ```
  and the two Flask views behind `GET /api/files/cookfiles/detail` and `GET /api/files/cookfiles/chart`.
- Consumes: `file_mgmt.cookfile.read_cookfile`, `common.app.prepare_annotations` / `prepare_event_totals` / `classify_cookfile_error`, `common.common.epoch_to_time`.

- [x] **Step 1: Write the failing tests**

Create `tests/web/test_api_files_cookfile_read.py`. **CORRECTED:** the shared
`api_files_client` / `api_files_folders` fixtures live in `tests/web/conftest.py` (where
pytest fixtures belong) and the archive builders in `tests/web/archive_builders.py`, so
nothing is copied between modules. `page.request.get(...)` below is
`client.get(...)`, `resp.status` is `resp.status_code`, `resp.json()` is
`resp.get_json()`. Draft, translated:

```python
def test_detail_returns_metadata_events_and_comments(live_server, page, _isolated_folders):
    history_dir, _ = _isolated_folders
    name = _write_cookfile(history_dir, "Detail-Cook")

    resp = page.request.get(f"{live_server}/api/files/cookfiles/detail?file={name}")
    assert resp.status == 200
    body = resp.json()
    assert body["filename"] == name
    assert body["metadata"]["title"] == "Detail-Cook"
    assert body["metadata"]["units"] == "F"
    assert len(body["events"]) == 2
    assert body["events"][0]["mode"] == "Smoke"
    assert body["graph_labels"]["probes"] == {"grill1": "Grill"}
    assert body["comments"] == []
    assert body["assets"] == []


def test_detail_formats_times_and_keeps_the_epochs(live_server, page, _isolated_folders):
    """render_cookfile_page (common/app.py:289-290) MUTATES metadata,
    replacing the epochs with HH:MM:SS -- so the Flask template can never show
    a date. The endpoint sends both: the display string the table shows, and
    the raw epoch a client needs to render a locale-correct date."""
    history_dir, _ = _isolated_folders
    name = _write_cookfile(history_dir, "Times-Cook")
    body = page.request.get(f"{live_server}/api/files/cookfiles/detail?file={name}").json()

    assert isinstance(body["metadata"]["starttime_epoch"], int)
    assert isinstance(body["metadata"]["endtime_epoch"], int)
    assert body["metadata"]["starttime"].count(":") == 2   # HH:MM:SS
    assert body["metadata"]["endtime"].count(":") == 2


def test_detail_includes_event_totals(live_server, page, _isolated_folders):
    history_dir, _ = _isolated_folders
    name = _write_cookfile(history_dir, "Totals-Cook")
    body = page.request.get(f"{live_server}/api/files/cookfiles/detail?file={name}").json()
    totals = body["event_totals"]
    assert set(totals) >= {"augerontime", "estusage_m", "estusage_i", "cooktime",
                           "pellet_level_start", "pellet_level_end"}


def test_chart_returns_the_full_graph_payload(live_server, page, _isolated_folders):
    """Same four keys the legacy `full_graph` action returns
    (blueprints/cookfile/routes.py:42-47), minus the arbitrary-path read."""
    history_dir, _ = _isolated_folders
    name = _write_cookfile(history_dir, "Chart-Cook")
    resp = page.request.get(f"{live_server}/api/files/cookfiles/chart?file={name}")
    assert resp.status == 200
    body = resp.json()
    assert set(body) == {"time_labels", "chart_data", "probe_mapper", "annotations"}
    assert body["chart_data"][0]["label"] == "Grill"
    assert body["probe_mapper"]["probes"] == {"grill1": 0}
    assert all(isinstance(t, (int, float)) for t in body["time_labels"])


def test_missing_file_parameter_is_400(live_server, page):
    resp = page.request.get(f"{live_server}/api/files/cookfiles/detail")
    assert resp.status == 400
    assert resp.json()["data"]["field"] == "file"


def test_unknown_file_is_404(live_server, page, _isolated_folders):
    resp = page.request.get(f"{live_server}/api/files/cookfiles/detail?file=Nope.pifire")
    assert resp.status == 404
    assert resp.json()["message"] == "not_found"


@pytest.mark.parametrize(
    "hostile",
    ["../../../etc/passwd", "../secret.pifire", "/etc/passwd", "..%2F..%2Fetc%2Fpasswd", ""],
)
def test_traversal_attempts_are_refused(live_server, page, _isolated_folders, hostile):
    """The legacy equivalents accept these: `full_graph` passes filename
    straight to read_cookfile (routes.py:37) and `dl_cookfile` passes it
    straight to send_file (:162)."""
    resp = page.request.get(f"{live_server}/api/files/cookfiles/detail?file={hostile}")
    assert resp.status in (400, 404)
    assert "passwd" not in resp.text()


def test_a_corrupt_archive_is_422_with_an_errortype(live_server, page, _isolated_folders):
    """Drives the React repair prompt -- the equivalent of cferror.html's
    'Attempt Repair' / 'Attempt Conversion' branch."""
    history_dir, _ = _isolated_folders
    with open(history_dir + "Broken.pifire", "w") as f:
        f.write("not a zip at all")
    resp = page.request.get(f"{live_server}/api/files/cookfiles/detail?file=Broken.pifire")
    assert resp.status == 422
    body = resp.json()
    assert body["result"] == "Error"
    assert body["data"]["errortype"] in ("version", "asset", "other")


def test_an_old_version_archive_is_422_with_errortype_version(live_server, page, _isolated_folders):
    history_dir, _ = _isolated_folders
    name = _write_cookfile(history_dir, "Old-Cook")
    # Rewrite metadata.json with a version below settings["versions"]["cookfile"].
    from file_mgmt.common import read_json_file_data, update_json_file_data

    metadata, status = read_json_file_data(history_dir + name, "metadata", unpackassets=False)
    assert status == "OK"
    metadata["version"] = "0.0.1"
    assert update_json_file_data(metadata, history_dir + name, "metadata") == "OK"

    resp = page.request.get(f"{live_server}/api/files/cookfiles/detail?file={name}")
    assert resp.status == 422
    assert resp.json()["data"]["errortype"] == "version"
```

- [x] **Step 2: Add `_require_file` to `blueprints/api_files/routes.py`**

```python
from common.file_browser import resolve_managed_file


def cookfile_folder():
    return current_app.config["HISTORY_FOLDER"]


def require_file(name, *, must_exist=True):
    """Resolve a client-supplied bare filename to a contained absolute path.

    Returns (path, None) on success or (None, response) on failure, so callers
    read as `path, err = require_file(name); if err: return err`.
    """
    if not name:
        return None, error("bad_request", 400, field="file")
    path = resolve_managed_file(cookfile_folder(), name)
    if path is None:
        return None, error("not_found", 404)
    if must_exist and not os.path.isfile(path):
        return None, error("not_found", 404)
    return path, None
```

(add `import os` at the top).

- [x] **Step 3: Write `blueprints/api_files/cookfile_api.py`**

```python
"""Cook-file endpoint handlers for /api/files/cookfiles/*.

Every handler here takes a BARE FILENAME resolved by routes.require_file. None
of them ever accepts a path, which is the single behavioural difference from
blueprints/cookfile/routes.py.
"""

import copy

from flask import jsonify

from common.app import classify_cookfile_error, prepare_annotations, prepare_event_totals
from common.common import epoch_to_time
from file_mgmt.cookfile import read_cookfile


def load(path):
    """read_cookfile + its status. Returns (struct, status)."""
    return read_cookfile(path)


def unreadable(status, error):
    """The uniform 422 for 'the file exists but will not load'. `errortype` is
    what cferror.html branches on (cookfile/cferror.html:24) and what the React
    page turns into an Attempt Repair / Attempt Conversion prompt."""
    return error(status, 422, errortype=classify_cookfile_error(status))


def detail_payload(struct, filename):
    """Reshape a cookfilestruct for the client.

    Deliberately NOT render_cookfile_page's reshape (common/app.py:283-306):
    that one MUTATES metadata in place, replacing the start/end epochs with
    HH:MM:SS strings, so the page can only ever show a time of day and never a
    date. Here the epochs are KEPT alongside the formatted strings -- the
    client can render either, and a second read of the same struct is not
    corrupted by the first.

    Comment text is also left ALONE. render_cookfile_page does
    `comment["text"].replace("\\n", "<br>")` (:287) because Jinja is about to
    emit it as HTML; React renders text nodes and `white-space: pre-wrap`, so
    injecting markup here would be both wrong and an XSS vector.
    """
    metadata = copy.deepcopy(struct["metadata"])
    start = metadata.get("starttime") or 0
    end = metadata.get("endtime") or 0
    metadata["starttime_epoch"] = start
    metadata["endtime_epoch"] = end
    metadata["starttime"] = epoch_to_time(start / 1000) if start else ""
    metadata["endtime"] = epoch_to_time(end / 1000) if end else ""

    events = struct["events"]
    #  prepare_event_totals indexes events[-1]/events[0]/events[-2]
    #  unconditionally (common/app.py:163-168), so a file with fewer than two
    #  events raises IndexError. The Flask page simply 500s on such a file;
    #  here an incomplete cook reports no totals and still renders.
    totals = prepare_event_totals(events) if len(events) >= 2 else {}

    return {
        "filename": filename,
        "metadata": metadata,
        "graph_labels": struct["graph_labels"],
        "events": events,
        "event_totals": totals,
        "comments": struct["comments"],
        "assets": struct["assets"],
    }


def chart_payload(struct):
    """The same four keys the legacy `full_graph` action returns
    (blueprints/cookfile/routes.py:42-47). `annotations` is keyed
    `event_<n>` -- a dict, not a list."""
    return {
        "chart_data": struct["graph_data"]["chart_data"],
        "time_labels": struct["graph_data"]["time_labels"],
        "probe_mapper": struct["graph_data"]["probe_mapper"],
        "annotations": prepare_annotations(0, struct["events"]),
    }
```

- [x] **Step 4: Register the two routes in `blueprints/api_files/routes.py`**

```python
from . import cookfile_api


@api_files_bp.route("/cookfiles/detail", methods=["GET"])
def cookfile_detail():
    name = request.args.get("file", "")
    path, err = require_file(name)
    if err:
        return err
    struct, status = cookfile_api.load(path)
    if status != "OK":
        return cookfile_api.unreadable(status, error)
    return jsonify(cookfile_api.detail_payload(struct, name)), 200


@api_files_bp.route("/cookfiles/chart", methods=["GET"])
def cookfile_chart():
    name = request.args.get("file", "")
    path, err = require_file(name)
    if err:
        return err
    struct, status = cookfile_api.load(path)
    if status != "OK":
        return cookfile_api.unreadable(status, error)
    return jsonify(cookfile_api.chart_payload(struct)), 200
```

- [x] **Step 5: Verify and commit** (24 passed)

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_files_cookfile_read.py -q
.venv/bin/ruff format blueprints/api_files/ tests/web/test_api_files_cookfile_read.py
jj desc -m "feat(api-files): cook-file detail and chart endpoints with path containment"
```

**Deliverable:** `GET /api/files/cookfiles/detail?file=X` and `.../chart?file=X` return typed JSON, 404 on traversal, and 422 with an `errortype` on a broken archive.

---

### Task 4: file-level operations — download, CSV export, upload, delete (E5-E8)

**Files:**
- Modify: `blueprints/api_files/routes.py`, `blueprints/api_files/cookfile_api.py`
- Create: `tests/web/test_api_files_cookfile_write.py`

**Interfaces:**
- `GET /api/files/cookfiles/download?file=NAME` → `.pifire` attachment; 404.
- `GET /api/files/cookfiles/export?file=NAME&kind=data|events` → CSV attachment; 400 on bad `kind`; 404; 422.
- `POST /api/files/cookfiles/upload`, multipart field `file` → `{result:"OK", data:{filename}}`; 400 `disallowed_file` / `bad_request`.
- `POST /api/files/cookfiles/delete`, JSON `{file}` → `{result:"OK"}`; 404.

- [x] **Step 1: Write the failing tests**

Create `tests/web/test_api_files_cookfile_write.py`. **CORRECTED:** nothing is copied --
the fixtures are `api_files_client` / `api_files_folders` from `tests/web/conftest.py`
and the builders come from `tests/web/archive_builders.py`. Playwright calls translate
as in Task 3; `multipart={...}` becomes
`data={"file": (io.BytesIO(b"..."), name)}, content_type="multipart/form-data"` and
`data={"file": name}` becomes `json={"file": name}`.

```python
def test_download_streams_the_archive_bytes(live_server, page, _isolated_folders):
    """First test this behaviour has ever had: the legacy `dl_cookfile` branch
    (blueprints/cookfile/routes.py:159-162) has NO test -- grep the string
    'dl_' in tests/web/test_page_cookfile.py and you get nothing."""
    history_dir, _ = _isolated_folders
    name = _write_cookfile(history_dir, "Download-Cook")
    with open(history_dir + name, "rb") as f:
        on_disk = f.read()

    resp = page.request.get(f"{live_server}/api/files/cookfiles/download?file={name}")
    assert resp.status == 200
    assert resp.body() == on_disk
    assert "attachment" in resp.headers["content-disposition"]
    assert name in resp.headers["content-disposition"]


def test_download_refuses_to_read_outside_the_history_folder(live_server, page, _isolated_folders):
    """The legacy branch does `send_file(request.form["dl_cookfile"])` with no
    check whatsoever -- an arbitrary file read. This one is contained."""
    resp = page.request.get(f"{live_server}/api/files/cookfiles/download?file=../../../etc/passwd")
    assert resp.status == 404
    assert "root:" not in resp.text()


def test_export_data_csv_has_a_header_and_one_row_per_sample(live_server, page, _isolated_folders):
    history_dir, _ = _isolated_folders
    name = _write_cookfile(history_dir, "Export-Cook")
    resp = page.request.get(f"{live_server}/api/files/cookfiles/export?file={name}&kind=data")
    assert resp.status == 200
    text = resp.text()
    assert text.splitlines()[0].startswith("Time,")
    assert "grill1 Temp" in text.splitlines()[0]
    assert "attachment" in resp.headers["content-disposition"]


def test_export_events_csv(live_server, page, _isolated_folders):
    history_dir, _ = _isolated_folders
    name = _write_cookfile(history_dir, "ExportEv-Cook")
    resp = page.request.get(f"{live_server}/api/files/cookfiles/export?file={name}&kind=events")
    assert resp.status == 200
    assert len(resp.text().splitlines()) >= 3   # header + two events


def test_export_works_under_a_non_default_history_folder(live_server, page, _isolated_folders):
    """The bug this endpoint routes around: prepare_csv/prepare_metrics_csv do
    `filename.replace("./history/", "")` and then `"/tmp/" + filename + ".csv"`
    (common/app.py:173-176, :203-213). Given an absolute path under a temp dir
    that composes `/tmp//tmp/.../X.pifire-Pifire-Export.csv` and open() raises.
    This endpoint passes os.path.basename(name) in, so it is correct under any
    folder -- which is exactly why the legacy dl_graphfile/dl_eventfile
    branches could never be tested under the isolated-folder fixture."""
    history_dir, _ = _isolated_folders
    assert not history_dir.startswith("./history/")
    name = _write_cookfile(history_dir, "TempFolder-Cook")
    for kind in ("data", "events"):
        resp = page.request.get(f"{live_server}/api/files/cookfiles/export?file={name}&kind={kind}")
        assert resp.status == 200, f"{kind} export failed under a temp history folder"


def test_export_rejects_an_unknown_kind(live_server, page, _isolated_folders):
    history_dir, _ = _isolated_folders
    name = _write_cookfile(history_dir, "BadKind-Cook")
    resp = page.request.get(f"{live_server}/api/files/cookfiles/export?file={name}&kind=sql")
    assert resp.status == 400
    assert resp.json()["data"]["field"] == "kind"


def test_upload_saves_into_the_configured_folder(live_server, page, _isolated_folders):
    history_dir, _ = _isolated_folders
    stray = os.path.join(os.getcwd(), "HISTORY_FOLDER")
    try:
        resp = page.request.post(
            f"{live_server}/api/files/cookfiles/upload",
            multipart={"file": {"name": "Uploaded.pifire", "mimeType": "application/octet-stream",
                                "buffer": b"fake cookfile bytes"}},
        )
        assert resp.status == 200
        assert resp.json()["result"] == "OK"
        assert resp.json()["data"]["filename"] == "Uploaded.pifire"
        assert os.path.isfile(history_dir + "Uploaded.pifire")
        assert not os.path.exists(stray)
    finally:
        shutil.rmtree(stray, ignore_errors=True)


@pytest.mark.parametrize(
    "hostile_name",
    ["../../evil.pifire", "/tmp/evil.pifire", "..\\..\\evil.pifire", "sub/dir/evil.pifire"],
)
def test_upload_filename_cannot_escape_the_folder(live_server, page, _isolated_folders, hostile_name):
    """Traversal on the UPLOADED filename, explicitly. secure_filename flattens
    the name and resolve_managed_file then contains the result; both run, and
    the assertion is that nothing lands outside history_dir."""
    history_dir, _ = _isolated_folders
    before = set(os.listdir(history_dir))
    parent = os.path.dirname(history_dir.rstrip("/"))
    parent_before = set(os.listdir(parent))

    resp = page.request.post(
        f"{live_server}/api/files/cookfiles/upload",
        multipart={"file": {"name": hostile_name, "mimeType": "application/octet-stream",
                            "buffer": b"x"}},
    )
    assert resp.status in (200, 400)
    assert set(os.listdir(parent)) == parent_before, "an upload escaped the history folder"
    for created in set(os.listdir(history_dir)) - before:
        assert "/" not in created and ".." not in created
    assert not os.path.exists("/tmp/evil.pifire")


def test_upload_rejects_a_disallowed_extension(live_server, page, _isolated_folders):
    """config.py:10 ALLOWED_EXTENSIONS gates this, same as the legacy route."""
    history_dir, _ = _isolated_folders
    resp = page.request.post(
        f"{live_server}/api/files/cookfiles/upload",
        multipart={"file": {"name": "payload.sh", "mimeType": "text/plain", "buffer": b"rm -rf /"}},
    )
    assert resp.status == 400
    assert resp.json()["message"] == "disallowed_file"
    assert not os.path.exists(history_dir + "payload.sh")


def test_upload_with_no_file_part_is_400(live_server, page):
    resp = page.request.post(f"{live_server}/api/files/cookfiles/upload", multipart={})
    assert resp.status == 400


def test_delete_removes_the_file(live_server, page, _isolated_folders):
    history_dir, _ = _isolated_folders
    name = _write_cookfile(history_dir, "Delete-Cook")
    resp = page.request.post(f"{live_server}/api/files/cookfiles/delete", data={"file": name})
    assert resp.status == 200
    assert resp.json()["result"] == "OK"
    assert not os.path.exists(history_dir + name)


def test_delete_refuses_traversal_and_unknown_names(live_server, page, _isolated_folders, tmp_path):
    victim = tmp_path / "victim.pifire"
    victim.write_text("do not delete me")
    for hostile in ("../victim.pifire", "/etc/hosts", "", "Nope.pifire"):
        resp = page.request.post(f"{live_server}/api/files/cookfiles/delete", data={"file": hostile})
        assert resp.status in (400, 404)
    assert victim.exists()
```

Add `import os`, `import shutil`, `import pytest` to the module imports.

- [x] **Step 2: Implement in `cookfile_api.py`**

```python
import os

from werkzeug.utils import secure_filename

from common.app import allowed_file, prepare_csv, prepare_metrics_csv
from file_mgmt.common import read_json_file_data

#: kind -> (which cookfile member to read, which CSV builder)
_EXPORTS = {"data": "raw_data", "events": "events"}


def build_export(path, name, kind):
    """Produce a CSV on disk and return (csv_path, status).

    `os.path.basename(name)` is what goes to the builders, NOT the full path:
    prepare_csv/prepare_metrics_csv both compose their output as
    `"/tmp/" + filename + ".csv"` after a `.replace("./history/", "")` that
    only matches the DEFAULT folder (common/app.py:175, :210). Handing them an
    absolute path under any other folder composes a /tmp path with embedded
    directories that do not exist, and open() raises. Passing the bare name
    makes this correct for every folder without touching common/app.py, which
    the legacy routes still call.
    """
    stem = os.path.basename(name)
    if kind == "data":
        struct, status = read_cookfile(path)
        if status != "OK":
            return None, status
        return prepare_csv(struct["raw_data"], stem), "OK"
    events, status = read_json_file_data(path, "events")
    if status != "OK":
        return None, status
    return prepare_metrics_csv(events, stem), "OK"


def save_upload(storage):  # CORRECTED: `folder` was unused
    """Save an uploaded archive into `folder`.

    TWO guards, both required and neither sufficient alone:
      1. allowed_file() gates the extension against config.py's
         ALLOWED_EXTENSIONS -- the same gate the legacy route uses.
      2. secure_filename() flattens the name, and the CALLER then re-resolves
         the flattened name through resolve_managed_file. secure_filename alone
         is a character-set filter, not a containment proof, and this is a
         write: a name that survives it but still escapes would create a file
         outside the folder.
    Returns (safe_name, None) or (None, error_message).
    """
    if storage is None or not storage.filename:
        return None, "bad_request"
    if not allowed_file(storage.filename):
        return None, "disallowed_file"
    safe_name = secure_filename(storage.filename)
    if not safe_name:
        return None, "bad_request"
    return safe_name, None
```

- [x] **Step 3: Register the four routes**

In `blueprints/api_files/routes.py`:

```python
from flask import send_file
from werkzeug.exceptions import BadRequest


def json_body():
    """request.json, or {} for a body that is absent or not JSON. Playwright's
    `data=` sends JSON; a browser fetch sends JSON. A form-encoded body is not
    supported by these routes on purpose -- one shape, one parser."""
    try:
        return request.get_json(silent=True) or {}
    except BadRequest:
        return {}


@api_files_bp.route("/cookfiles/download", methods=["GET"])
def cookfile_download():
    path, err = require_file(request.args.get("file", ""))
    if err:
        return err
    return send_file(path, as_attachment=True, max_age=0)


@api_files_bp.route("/cookfiles/export", methods=["GET"])
def cookfile_export():
    name = request.args.get("file", "")
    kind = request.args.get("kind", "")
    if kind not in ("data", "events"):
        return error("bad_request", 400, field="kind")
    path, err = require_file(name)
    if err:
        return err
    csv_path, status = cookfile_api.build_export(path, name, kind)
    if status != "OK":
        return cookfile_api.unreadable(status, error)
    return send_file(csv_path, as_attachment=True, max_age=0)


@api_files_bp.route("/cookfiles/upload", methods=["POST"])
def cookfile_upload():
    safe_name, problem = cookfile_api.save_upload(request.files.get("file"))
    if problem:
        return error(problem, 400, field="file")
    #  Re-contain the FLATTENED name: secure_filename is a character filter,
    #  resolve_managed_file is the containment proof, and this is a write.
    path, err = require_file(safe_name, must_exist=False)
    if err:
        return err
    request.files["file"].save(path)
    return jsonify(api_response("OK", None, {"filename": safe_name})), 200


@api_files_bp.route("/cookfiles/delete", methods=["POST"])
def cookfile_delete():
    path, err = require_file(json_body().get("file", ""))
    if err:
        return err
    os.remove(path)
    return jsonify(api_response("OK")), 200
```

- [x] **Step 4: Verify and commit** (22 passed)

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_files_cookfile_write.py -q
ls /tmp/evil.pifire 2>&1 | grep -q "No such file" && echo "no traversal leak"
.venv/bin/ruff format blueprints/api_files/ tests/web/test_api_files_cookfile_write.py
jj desc -m "feat(api-files): cook-file download, CSV export, upload and delete"
```

**Deliverable:** four working endpoints, with the first-ever test coverage of cook-file download and CSV export, and explicit traversal tests on both the query name and the uploaded filename.

---

### Task 5: mutations — title, probe-label rename, repair/upgrade (E9-E11)

**Files:**
- Modify: `blueprints/api_files/routes.py`, `blueprints/api_files/cookfile_api.py`
- Modify: `tests/web/test_api_files_cookfile_write.py`

**Interfaces:**
- `POST /api/files/cookfiles/title` `{file, title}` → `{result:"OK"}`.
- `POST /api/files/cookfiles/label` `{file, old_label, new_label}` → `{result:"OK", data:{new_label_safe}}`; `{result:"Error", message:"label_exists"}` at 409.
- `POST /api/files/cookfiles/recover` `{file, action:"upgrade"|"repair"}` → `{result:"OK"}`; 422 on failure.

- [x] **Step 1: Write the failing tests (append to `test_api_files_cookfile_write.py`)**

```python
def _read_member(history_dir, name, member):
    from file_mgmt.common import read_json_file_data

    data, status = read_json_file_data(history_dir + name, member, unpackassets=False)
    assert status == "OK"
    return data


def test_title_rename_persists(live_server, page, _isolated_folders):
    history_dir, _ = _isolated_folders
    name = _write_cookfile(history_dir, "Title-Cook")
    resp = page.request.post(f"{live_server}/api/files/cookfiles/title",
                             data={"file": name, "title": "Sunday Brisket"})
    assert resp.status == 200 and resp.json()["result"] == "OK"
    assert _read_member(history_dir, name, "metadata")["title"] == "Sunday Brisket"


def test_title_rename_does_not_rename_the_file(live_server, page, _isolated_folders):
    """Flask's editTitle only touches metadata.json (routes.py:483-495). The
    filename is the identity the list and every URL use; renaming it here would
    break the open browser tab."""
    history_dir, _ = _isolated_folders
    name = _write_cookfile(history_dir, "Stable-Cook")
    page.request.post(f"{live_server}/api/files/cookfiles/title",
                      data={"file": name, "title": "Something Else"})
    assert os.path.isfile(history_dir + name)


def test_label_rename_updates_labels_mapper_and_chart_label(live_server, page, _isolated_folders):
    """_rename_graph_label (routes.py:498-553) is a five-step rewrite across
    TWO json members. All three effects are asserted."""
    history_dir, _ = _isolated_folders
    name = _write_cookfile(history_dir, "Label-Cook")
    resp = page.request.post(f"{live_server}/api/files/cookfiles/label",
                             data={"file": name, "old_label": "grill1", "new_label": "Main Grill"})
    assert resp.status == 200
    safe = resp.json()["data"]["new_label_safe"]
    assert safe == "MainGrill"   # create_safe_name strips non-alnum (common/app.py:325)

    labels = _read_member(history_dir, name, "graph_labels")
    assert labels["probes"][safe] == "Main Grill"
    assert "grill1" not in labels["probes"]

    graph = _read_member(history_dir, name, "graph_data")
    assert safe in graph["probe_mapper"]["probes"]
    assert graph["chart_data"][graph["probe_mapper"]["probes"][safe]]["label"] == "Main Grill"


def test_label_rename_to_an_existing_label_is_refused(live_server, page, _isolated_folders):
    history_dir, _ = _isolated_folders
    name = _write_cookfile(history_dir, "Dup-Cook")
    page.request.post(f"{live_server}/api/files/cookfiles/label",
                      data={"file": name, "old_label": "grill1", "new_label": "Main Grill"})
    resp = page.request.post(f"{live_server}/api/files/cookfiles/label",
                             data={"file": name, "old_label": "MainGrill", "new_label": "Main Grill"})
    assert resp.status == 409
    assert resp.json()["message"] == "label_exists"


def test_label_rename_requires_all_three_fields(live_server, page, _isolated_folders):
    history_dir, _ = _isolated_folders
    name = _write_cookfile(history_dir, "Fields-Cook")
    for body in ({"file": name, "old_label": "grill1"}, {"file": name, "new_label": "X"}):
        resp = page.request.post(f"{live_server}/api/files/cookfiles/label", data=body)
        assert resp.status == 400


# CORRECTED: the plan also drafted a "detail 422s, then upgrade, then detail 200s" test
# using `version="0.0.1"`. That version takes upgrade_cookfile's PRE-1.5 branch
# (file_mgmt/cookfile.py:269), which reads flat `grill1_setpoint`/`grill1_temp` keys a
# modern hand-built archive does not have -> KeyError. There is no version string that
# both fails read_cookfile's check AND skips the pre-1.5 conversion, so the shipped test
# uses a genuine v1.0-shaped archive (tests/web/archive_builders.py::write_legacy_cookfile,
# mirroring tests/unit/file_mgmt/test_cookfile.py::_write_old_format_pifire) and asserts
# the converted graph_labels come back in the modern nested shape.
def test_recover_upgrade_rewrites_a_current_version_file_as_a_no_op(live_server, page, _isolated_folders):
    history_dir, _ = _isolated_folders
    name = _write_cookfile(history_dir, "Upgrade-Cook")
    resp = page.request.post(f"{live_server}/api/files/cookfiles/recover",
                             data={"file": name, "action": "upgrade"})
    assert resp.status == 200 and resp.json()["result"] == "OK"
    assert _read_member(history_dir, name, "metadata")["title"] == "Upgrade-Cook"


def test_recover_repair_runs_upgrade_then_fixup_assets(live_server, page, _isolated_folders):
    history_dir, _ = _isolated_folders
    name = _write_cookfile(history_dir, "Repair-Cook")
    resp = page.request.post(f"{live_server}/api/files/cookfiles/recover",
                             data={"file": name, "action": "repair"})
    assert resp.status == 200 and resp.json()["result"] == "OK"


def test_recover_rejects_an_unknown_action(live_server, page, _isolated_folders):
    history_dir, _ = _isolated_folders
    name = _write_cookfile(history_dir, "Action-Cook")
    resp = page.request.post(f"{live_server}/api/files/cookfiles/recover",
                             data={"file": name, "action": "delete"})
    assert resp.status == 400
    assert resp.json()["data"]["field"] == "action"


def test_every_mutation_refuses_traversal(live_server, page, _isolated_folders):
    """One sweep over all three, so a new mutation added without require_file
    fails here rather than shipping."""
    for path_suffix, body in (
        ("title", {"file": "../x.pifire", "title": "t"}),
        ("label", {"file": "../x.pifire", "old_label": "a", "new_label": "b"}),
        ("recover", {"file": "../x.pifire", "action": "upgrade"}),
    ):
        resp = page.request.post(f"{live_server}/api/files/cookfiles/{path_suffix}", data=body)
        assert resp.status in (400, 404), path_suffix
```

- [x] **Step 2: Implement**

Add to `cookfile_api.py`:

```python
from common.app import create_safe_name
from file_mgmt.common import fixup_assets, update_json_file_data
from file_mgmt.cookfile import upgrade_cookfile


def set_title(path, title):
    metadata, status = read_json_file_data(path, "metadata")
    if status != "OK":
        return status
    metadata["title"] = title
    return update_json_file_data(metadata, path, "metadata")


def rename_label(path, old_label, new_label):
    """Rename a probe's display label across graph_labels.json AND
    graph_data.json.

    Ported from blueprints/cookfile/routes.py's _rename_graph_label (:498-553)
    rather than imported, because that helper answers "already exists" and
    "read failed" with the same jsonify({"result": "ERROR"}) -- the client
    cannot tell a user mistake from a corrupt file. Here the two are distinct
    return values so the route can answer 409 vs 422.

    Returns (safe_label, None) or (None, "label_exists" | <read/write status>).
    """
    labels, status = read_json_file_data(path, "graph_labels")
    if status != "OK":
        return None, status

    safe = create_safe_name(new_label)
    for category in labels:
        if safe in labels[category]:
            return None, "label_exists"

    for category in labels:
        if old_label in labels[category]:
            labels[category].pop(old_label)
            labels[category][safe] = new_label

    status = update_json_file_data(labels, path, "graph_labels")
    if status != "OK":
        return None, status

    graph, status = read_json_file_data(path, "graph_data")
    if status != "OK":
        return None, status
    for category in graph["probe_mapper"]:
        mapper = graph["probe_mapper"][category]
        if old_label not in mapper:
            continue
        mapper[safe] = mapper.pop(old_label)
        #  The three series a probe contributes get three different suffixes
        #  (file_mgmt/cookfile.py:372, :388) and the chart label must keep them.
        addendum = {"targets": " Target", "primarysp": " Set Point"}.get(category, "")
        graph["chart_data"][mapper[safe]]["label"] = new_label + addendum

    status = update_json_file_data(graph, path, "graph_data")
    if status != "OK":
        return None, status
    return safe, None


def recover(path, action):
    """upgrade | repair. `repair` is upgrade(repair=True) followed by
    fixup_assets, matching blueprints/cookfile/routes.py:270-302."""
    struct, status = upgrade_cookfile(path, repair=(action == "repair"))
    if status != "OK":
        return status
    if action == "repair":
        struct, status = read_cookfile(path)
        if status != "OK":
            return status
        struct, status = fixup_assets(path, struct)
    return status
```

Routes:

```python
@api_files_bp.route("/cookfiles/title", methods=["POST"])
def cookfile_title():
    body = json_body()
    path, err = require_file(body.get("file", ""))
    if err:
        return err
    title = body.get("title")
    if not isinstance(title, str):
        return error("bad_request", 400, field="title")
    status = cookfile_api.set_title(path, title)
    if status != "OK":
        return cookfile_api.unreadable(status, error)
    return jsonify(api_response("OK")), 200


@api_files_bp.route("/cookfiles/label", methods=["POST"])
def cookfile_label():
    body = json_body()
    path, err = require_file(body.get("file", ""))
    if err:
        return err
    old_label, new_label = body.get("old_label"), body.get("new_label")
    if not isinstance(old_label, str) or not old_label:
        return error("bad_request", 400, field="old_label")
    if not isinstance(new_label, str) or not new_label.strip():
        return error("bad_request", 400, field="new_label")
    safe, problem = cookfile_api.rename_label(path, old_label, new_label)
    if problem == "label_exists":
        return error("label_exists", 409)
    if problem:
        return cookfile_api.unreadable(problem, error)
    return jsonify(api_response("OK", None, {"new_label_safe": safe})), 200


@api_files_bp.route("/cookfiles/recover", methods=["POST"])
def cookfile_recover():
    body = json_body()
    action = body.get("action")
    if action not in ("upgrade", "repair"):
        return error("bad_request", 400, field="action")
    path, err = require_file(body.get("file", ""))
    if err:
        return err
    status = cookfile_api.recover(path, action)
    if status != "OK":
        return cookfile_api.unreadable(status, error)
    return jsonify(api_response("OK")), 200
```

- [x] **Step 3: Verify and commit** (39 passed)

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_files_cookfile_write.py -q
.venv/bin/ruff format blueprints/api_files/ tests/web/test_api_files_cookfile_write.py
jj desc -m "feat(api-files): cook-file title, probe-label rename and repair/upgrade"
```

**Deliverable:** the three metadata mutations, with `label_exists` distinguishable from a corrupt file (which the Flask route cannot do).

---

### Task 6: comments (E12, E13)

**Files:**
- Modify: `blueprints/api_files/routes.py`, `blueprints/api_files/cookfile_api.py`
- Create: `tests/web/test_api_files_cookfile_comments.py`

**Interfaces:**
- `POST /api/files/cookfiles/comments` `{file, action, id?, text?}`:
  - `action:"add"`, `text` → `{result:"OK", data:{id, date, time, text, edited:"", assets:[]}}`
  - `action:"update"`, `id`, `text` → `{result:"OK", data:{id, text, edited, date, time}}`
  - `action:"delete"`, `id` → `{result:"OK"}`
  - unknown id → 404 `{message:"comment_not_found"}`
- `POST /api/files/cookfiles/comments/assets` `{file, id, assets: string[]}` → `{result:"OK", data:{assets}}`.

- [x] **Step 1: Write the failing tests**

```python
def test_comment_lifecycle_add_update_delete(live_server, page, _isolated_folders):
    history_dir, _ = _isolated_folders
    name = _write_cookfile(history_dir, "Comment-Cook")
    url = f"{live_server}/api/files/cookfiles/comments"

    added = page.request.post(url, data={"file": name, "action": "add", "text": "First light"})
    assert added.status == 200
    data = added.json()["data"]
    cid = data["id"]
    assert data["text"] == "First light"
    assert data["edited"] == ""
    assert data["assets"] == []
    assert _read_member(history_dir, name, "comments")[0]["text"] == "First light"

    updated = page.request.post(url, data={"file": name, "action": "update", "id": cid,
                                           "text": "Second light"})
    assert updated.status == 200
    assert updated.json()["data"]["edited"] != ""
    stored = _read_member(history_dir, name, "comments")[0]
    assert stored["text"] == "Second light"

    deleted = page.request.post(url, data={"file": name, "action": "delete", "id": cid})
    assert deleted.status == 200
    assert _read_member(history_dir, name, "comments") == []


def test_comment_text_keeps_its_newlines_and_is_never_html(live_server, page, _isolated_folders):
    """render_cookfile_page does `text.replace("\\n", "<br>")` (common/app.py:287)
    because Jinja emits it as HTML. React renders a text node, so the API must
    NOT inject markup -- doing so would print a literal <br> at best and be an
    XSS vector at worst."""
    history_dir, _ = _isolated_folders
    name = _write_cookfile(history_dir, "Newline-Cook")
    body = page.request.post(f"{live_server}/api/files/cookfiles/comments",
                             data={"file": name, "action": "add",
                                   "text": "line one\nline two <script>x</script>"}).json()
    assert body["data"]["text"] == "line one\nline two <script>x</script>"
    assert "<br>" not in body["data"]["text"]


def test_unknown_comment_id_is_404_not_a_false_success(live_server, page, _isolated_folders):
    history_dir, _ = _isolated_folders
    name = _write_cookfile(history_dir, "MissingC-Cook")
    for action in ("update", "delete"):
        resp = page.request.post(f"{live_server}/api/files/cookfiles/comments",
                                 data={"file": name, "action": action, "id": "nope", "text": "x"})
        assert resp.status == 404
        assert resp.json()["message"] == "comment_not_found"


def test_comment_on_an_unreadable_file_is_422_not_a_crash(live_server, page, _isolated_folders):
    """The legacy `comments` branch reads without checking status and then does
    cookfiledata.append(...) on a dict -- AttributeError -> HTTP 500. Pinned as
    a clean error at tests/web/test_page_cookfile.py:696-709 for the legacy
    route; asserted directly here."""
    history_dir, _ = _isolated_folders
    with open(history_dir + "Bad.pifire", "w") as f:
        f.write("nope")
    resp = page.request.post(f"{live_server}/api/files/cookfiles/comments",
                             data={"file": "Bad.pifire", "action": "add", "text": "x"})
    assert resp.status == 422


def test_setting_a_comments_asset_list_replaces_it_wholesale(live_server, page, _isolated_folders):
    """Flask toggles ONE asset per request and infers add-vs-remove from a
    client-sent `state` string (routes.py:565-585) -- so a stale client view
    silently inverts the operation. This endpoint takes the whole list the user
    ended up with, which cannot invert."""
    history_dir, _ = _isolated_folders
    name = _write_cookfile(history_dir, "Assets-Cook")
    cid = page.request.post(f"{live_server}/api/files/cookfiles/comments",
                            data={"file": name, "action": "add", "text": "c"}).json()["data"]["id"]

    resp = page.request.post(f"{live_server}/api/files/cookfiles/comments/assets",
                             data={"file": name, "id": cid, "assets": ["a.png", "b.png"]})
    assert resp.status == 200
    assert resp.json()["data"]["assets"] == ["a.png", "b.png"]
    assert _read_member(history_dir, name, "comments")[0]["assets"] == ["a.png", "b.png"]

    cleared = page.request.post(f"{live_server}/api/files/cookfiles/comments/assets",
                                data={"file": name, "id": cid, "assets": []})
    assert cleared.status == 200
    assert _read_member(history_dir, name, "comments")[0]["assets"] == []


def test_comment_assets_must_be_a_list_of_strings(live_server, page, _isolated_folders):
    history_dir, _ = _isolated_folders
    name = _write_cookfile(history_dir, "AssetType-Cook")
    cid = page.request.post(f"{live_server}/api/files/cookfiles/comments",
                            data={"file": name, "action": "add", "text": "c"}).json()["data"]["id"]
    for bad in ("a.png", [1, 2], {"a": 1}):
        resp = page.request.post(f"{live_server}/api/files/cookfiles/comments/assets",
                                 data={"file": name, "id": cid, "assets": bad})
        assert resp.status == 400
```

- [x] **Step 2: Implement in `cookfile_api.py`**

```python
import datetime

from common.common import generate_uuid


def _load_comments(path):
    return read_json_file_data(path, "comments")


def add_comment(path, text):
    comments, status = _load_comments(path)
    if status != "OK" or not isinstance(comments, list):
        return None, status if status != "OK" else "Error: comments unreadable."
    now = datetime.datetime.now()
    entry = {
        "text": text,
        "id": generate_uuid(),
        "edited": "",
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "assets": [],
    }
    comments.append(entry)
    status = update_json_file_data(comments, path, "comments")
    return (entry, None) if status == "OK" else (None, status)


def update_comment(path, comment_id, text):
    comments, status = _load_comments(path)
    if status != "OK" or not isinstance(comments, list):
        return None, status if status != "OK" else "Error: comments unreadable."
    for entry in comments:
        if entry["id"] != comment_id:
            continue
        entry["text"] = text
        entry["edited"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        status = update_json_file_data(comments, path, "comments")
        return (entry, None) if status == "OK" else (None, status)
    return None, "comment_not_found"


def delete_comment(path, comment_id):
    comments, status = _load_comments(path)
    if status != "OK" or not isinstance(comments, list):
        return status if status != "OK" else "Error: comments unreadable."
    for entry in comments:
        if entry["id"] == comment_id:
            comments.remove(entry)
            return update_json_file_data(comments, path, "comments")
    return "comment_not_found"


def set_comment_assets(path, comment_id, assets):
    """Replace a comment's asset list wholesale.

    Flask toggles ONE asset and infers add-vs-remove from a client-supplied
    `state` string (routes.py:565-585): if the client's view of "selected" is
    stale, the toggle does the OPPOSITE of what the user clicked. A whole-list
    write states the intent and cannot invert.
    """
    comments, status = _load_comments(path)
    if status != "OK" or not isinstance(comments, list):
        return None, status if status != "OK" else "Error: comments unreadable."
    for entry in comments:
        if entry["id"] == comment_id:
            entry["assets"] = list(assets)
            status = update_json_file_data(comments, path, "comments")
            return (entry["assets"], None) if status == "OK" else (None, status)
    return None, "comment_not_found"
```

- [x] **Step 3: Register the two routes**

```python
_COMMENT_ACTIONS = ("add", "update", "delete")


@api_files_bp.route("/cookfiles/comments", methods=["POST"])
def cookfile_comments():
    body = json_body()
    action = body.get("action")
    if action not in _COMMENT_ACTIONS:
        return error("bad_request", 400, field="action")
    path, err = require_file(body.get("file", ""))
    if err:
        return err

    if action == "add":
        text = body.get("text")
        if not isinstance(text, str):
            return error("bad_request", 400, field="text")
        entry, problem = cookfile_api.add_comment(path, text)
    elif action == "update":
        text, cid = body.get("text"), body.get("id")
        if not isinstance(text, str):
            return error("bad_request", 400, field="text")
        if not isinstance(cid, str) or not cid:
            return error("bad_request", 400, field="id")
        entry, problem = cookfile_api.update_comment(path, cid, text)
    else:
        cid = body.get("id")
        if not isinstance(cid, str) or not cid:
            return error("bad_request", 400, field="id")
        entry, problem = None, cookfile_api.delete_comment(path, cid)
        problem = None if problem == "OK" else problem

    if problem == "comment_not_found":
        return error("comment_not_found", 404)
    if problem:
        return cookfile_api.unreadable(problem, error)
    return jsonify(api_response("OK", None, entry)), 200


@api_files_bp.route("/cookfiles/comments/assets", methods=["POST"])
def cookfile_comment_assets():
    body = json_body()
    path, err = require_file(body.get("file", ""))
    if err:
        return err
    cid = body.get("id")
    assets = body.get("assets")
    if not isinstance(cid, str) or not cid:
        return error("bad_request", 400, field="id")
    if not isinstance(assets, list) or not all(isinstance(a, str) for a in assets):
        return error("bad_request", 400, field="assets")
    stored, problem = cookfile_api.set_comment_assets(path, cid, assets)
    if problem == "comment_not_found":
        return error("comment_not_found", 404)
    if problem:
        return cookfile_api.unreadable(problem, error)
    return jsonify(api_response("OK", None, {"assets": stored})), 200
```

- [x] **Step 4: Verify and commit**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_files_cookfile_comments.py -q
.venv/bin/ruff format blueprints/api_files/ tests/web/test_api_files_cookfile_comments.py
jj desc -m "feat(api-files): cook-file comment CRUD and comment asset lists"
```

**Deliverable:** comment CRUD that returns 404 for an unknown id (the Flask route returns a generic `{"result":"ERROR"}`) and never injects HTML into stored text.

---

### Task 7: assets — upload, delete, thumbnail (E14-E16)

**Files:**
- Modify: `blueprints/api_files/routes.py`, `blueprints/api_files/cookfile_api.py`
- Create: `tests/web/test_api_files_cookfile_assets.py`

**Interfaces:**
- `POST /api/files/cookfiles/assets/upload`, multipart: `file` (name) + repeatable `assets` → `{result:"OK", data:{assets:[{id, filename, type}]}}`.
- `POST /api/files/cookfiles/assets/delete`, JSON `{file, assets: string[]}` → `{result:"OK"}`.
- `POST /api/files/cookfiles/thumbnail`, JSON `{file, asset}` → `{result:"OK"}`.

- [x] **Step 1: Write the failing tests**

```python
import io

from PIL import Image


@pytest.fixture
def _static_img_tmp_cleanup():
    """Any read with unpackassets=True symlinks ./static/img/tmp/{id} into the
    repo tree (file_mgmt/common.py:85-88). Gitignored, but removed anyway so
    the working tree stays clean -- copied from
    tests/web/test_page_cookfile.py:429-443."""
    base = "./static/img/tmp"
    before = set(os.listdir(base)) if os.path.isdir(base) else set()
    yield
    if os.path.isdir(base):
        for leftover in set(os.listdir(base)) - before:
            target = os.path.join(base, leftover)
            if os.path.islink(target):
                os.unlink(target)


def _png(color=(0, 200, 0), size=(16, 16)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def test_asset_upload_runs_the_real_pillow_pipeline(live_server, page, _isolated_folders,
                                                    _static_img_tmp_cleanup):
    """add_asset rotates, thumbnails and resizes with real Pillow
    (file_mgmt/media.py:26-61). Not mocked -- a mocked pipeline would not catch
    a thumbnail that never lands in the archive."""
    history_dir, _ = _isolated_folders
    name = _write_cookfile(history_dir, "AssetUp-Cook")

    tmp_root = tempfile.gettempdir()
    staging_before = {n for n in os.listdir(tmp_root) if n.startswith("pifire-upload-")}

    resp = page.request.post(
        f"{live_server}/api/files/cookfiles/assets/upload",
        multipart={"file": name,
                   "assets": {"name": "shot.png", "mimeType": "image/png", "buffer": _png()}},
    )
    assert resp.status == 200
    stored = resp.json()["data"]["assets"]
    assert len(stored) == 1 and stored[0]["type"] == "png"

    with zipfile.ZipFile(history_dir + name) as z:
        members = set(z.namelist())
    arc = f"{stored[0]['id']}.png"
    assert f"assets/{arc}" in members
    assert f"assets/thumbs/{arc}" in members

    # No per-request staging dir survives, and no predictable /tmp/pifire path.
    assert {n for n in os.listdir(tmp_root) if n.startswith("pifire-upload-")} == staging_before


def test_asset_upload_rejects_a_disallowed_extension(live_server, page, _isolated_folders):
    history_dir, _ = _isolated_folders
    name = _write_cookfile(history_dir, "AssetBad-Cook")
    resp = page.request.post(
        f"{live_server}/api/files/cookfiles/assets/upload",
        multipart={"file": name,
                   "assets": {"name": "evil.svg", "mimeType": "image/svg+xml",
                              "buffer": b"<svg onload=alert(1)>"}},
    )
    assert resp.status == 400
    assert resp.json()["message"] == "disallowed_file"


def test_asset_upload_filename_cannot_escape_the_staging_dir(live_server, page, _isolated_folders,
                                                             _static_img_tmp_cleanup):
    """Traversal on the asset's own filename, distinct from the archive name."""
    history_dir, _ = _isolated_folders
    name = _write_cookfile(history_dir, "AssetTrav-Cook")
    parent = os.path.dirname(history_dir.rstrip("/"))
    parent_before = set(os.listdir(parent))
    page.request.post(
        f"{live_server}/api/files/cookfiles/assets/upload",
        multipart={"file": name,
                   "assets": {"name": "../../escape.png", "mimeType": "image/png", "buffer": _png()}},
    )
    assert set(os.listdir(parent)) == parent_before


def test_uploaded_asset_is_served_from_static_img_tmp(live_server, page, _isolated_folders,
                                                      _static_img_tmp_cleanup):
    """The browser-serving invariant: bytes at /static/img/tmp/{id}/{asset}
    equal the fullsize asset inside the zip. Mirrors
    tests/web/test_page_cookfile.py:446-484."""
    history_dir, _ = _isolated_folders
    name = _write_cookfile(history_dir, "AssetServe-Cook")
    parent_id = _read_member(history_dir, name, "metadata")["id"]
    stored = page.request.post(
        f"{live_server}/api/files/cookfiles/assets/upload",
        multipart={"file": name,
                   "assets": {"name": "served.png", "mimeType": "image/png",
                              "buffer": _png((200, 20, 20), (24, 24))}},
    ).json()["data"]["assets"]
    arc = f"{stored[0]['id']}.{stored[0]['type']}"

    with zipfile.ZipFile(history_dir + name) as z:
        archived = z.read(f"assets/{arc}")

    served = page.request.get(f"{live_server}/static/img/tmp/{parent_id}/{arc}")
    assert served.status == 200
    assert served.body() == archived
    assert not os.path.exists(f"/tmp/pifire/{parent_id}")


def test_asset_delete_removes_it_from_the_archive_and_from_comments(live_server, page,
                                                                    _isolated_folders,
                                                                    _static_img_tmp_cleanup):
    history_dir, _ = _isolated_folders
    name = _write_cookfile(history_dir, "AssetDel-Cook")
    stored = page.request.post(
        f"{live_server}/api/files/cookfiles/assets/upload",
        multipart={"file": name,
                   "assets": {"name": "gone.png", "mimeType": "image/png", "buffer": _png()}},
    ).json()["data"]["assets"]
    arc = f"{stored[0]['id']}.{stored[0]['type']}"

    cid = page.request.post(f"{live_server}/api/files/cookfiles/comments",
                            data={"file": name, "action": "add", "text": "c"}).json()["data"]["id"]
    page.request.post(f"{live_server}/api/files/cookfiles/comments/assets",
                      data={"file": name, "id": cid, "assets": [arc]})

    resp = page.request.post(f"{live_server}/api/files/cookfiles/assets/delete",
                             data={"file": name, "assets": [arc]})
    assert resp.status == 200
    assert _read_member(history_dir, name, "assets") == []
    assert _read_member(history_dir, name, "comments")[0]["assets"] == []


def test_thumbnail_is_set_from_an_existing_asset(live_server, page, _isolated_folders,
                                                 _static_img_tmp_cleanup):
    history_dir, _ = _isolated_folders
    name = _write_cookfile(history_dir, "Thumb-Cook")
    stored = page.request.post(
        f"{live_server}/api/files/cookfiles/assets/upload",
        multipart={"file": name,
                   "assets": {"name": "t.png", "mimeType": "image/png", "buffer": _png()}},
    ).json()["data"]["assets"]
    arc = f"{stored[0]['id']}.{stored[0]['type']}"

    resp = page.request.post(f"{live_server}/api/files/cookfiles/thumbnail",
                             data={"file": name, "asset": arc})
    assert resp.status == 200
    assert _read_member(history_dir, name, "metadata")["thumbnail"] == arc


def test_thumbnail_rejects_an_asset_the_file_does_not_have(live_server, page, _isolated_folders):
    """Flask's thumbSelected writes whatever string it is handed
    (routes.py:207-209) -- test_page_cookfile.py:322-331 stores the literal
    "asset123.png" for a file with no assets, producing a broken <img> forever."""
    history_dir, _ = _isolated_folders
    name = _write_cookfile(history_dir, "ThumbBad-Cook")
    resp = page.request.post(f"{live_server}/api/files/cookfiles/thumbnail",
                             data={"file": name, "asset": "does-not-exist.png"})
    assert resp.status == 400
    assert resp.json()["data"]["field"] == "asset"
    assert _read_member(history_dir, name, "metadata")["thumbnail"] == ""
```

Add `import tempfile`, `import zipfile` to the module imports.

- [x] **Step 2: Implement in `cookfile_api.py`**

```python
import shutil
import tempfile

from file_mgmt.common import remove_assets
from file_mgmt.media import add_asset, set_thumbnail


def upload_assets(path, storages):
    """Add one or more images to the archive.

    Each upload is staged in a private per-request tempfile.mkdtemp (0700,
    app-owned) and rmtree'd in a finally -- the same arrangement
    blueprints/cookfile/routes.py:235-249 uses after the predictable
    /tmp/pifire/{id} path was removed. secure_filename flattens the name before
    it is written, so an asset called "../../x.png" cannot escape the staging
    dir either.
    """
    added = []
    for storage in storages:
        if not storage or not storage.filename:
            continue
        if not allowed_file(storage.filename):
            return None, "disallowed_file"
        safe_name = secure_filename(storage.filename)
        if not safe_name:
            return None, "bad_request"
        staging = tempfile.mkdtemp(prefix="pifire-upload-")
        try:
            storage.save(os.path.join(staging, safe_name))
            asset_id, filetype = add_asset(path, staging, safe_name)
            added.append({"id": asset_id, "filename": f"{asset_id}.{filetype}", "type": filetype})
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    if not added:
        return None, "bad_request"
    return added, None


def delete_assets(path, assets):
    """remove_assets rewrites the whole archive and scrubs the names out of
    metadata.thumbnail, comments[].assets and assets.json
    (file_mgmt/common.py:203-278)."""
    return remove_assets(path, list(assets))


def apply_thumbnail(path, asset_filename):
    """Set metadata.thumbnail, but only to an asset the file actually holds.

    Flask writes whatever string it is handed (routes.py:207-209), so a stale
    or hostile client can point the thumbnail at a file that does not exist and
    the list renders a broken image forever -- pinned, as current behaviour, at
    tests/web/test_page_cookfile.py:322-331.
    """
    assets, status = read_json_file_data(path, "assets", unpackassets=False)
    if status != "OK":
        return status
    if asset_filename not in {a["filename"] for a in assets}:
        return "unknown_asset"
    set_thumbnail(path, asset_filename)
    return "OK"
```

- [x] **Step 3: Register the three routes**

```python
@api_files_bp.route("/cookfiles/assets/upload", methods=["POST"])
def cookfile_asset_upload():
    path, err = require_file(request.form.get("file", ""))
    if err:
        return err
    added, problem = cookfile_api.upload_assets(path, request.files.getlist("assets"))
    if problem:
        return error(problem, 400, field="assets")
    return jsonify(api_response("OK", None, {"assets": added})), 200


@api_files_bp.route("/cookfiles/assets/delete", methods=["POST"])
def cookfile_asset_delete():
    body = json_body()
    path, err = require_file(body.get("file", ""))
    if err:
        return err
    assets = body.get("assets")
    if not isinstance(assets, list) or not all(isinstance(a, str) for a in assets):
        return error("bad_request", 400, field="assets")
    status = cookfile_api.delete_assets(path, assets)
    if status != "OK":
        return cookfile_api.unreadable(status, error)
    return jsonify(api_response("OK")), 200


@api_files_bp.route("/cookfiles/thumbnail", methods=["POST"])
def cookfile_thumbnail():
    body = json_body()
    path, err = require_file(body.get("file", ""))
    if err:
        return err
    asset = body.get("asset")
    if not isinstance(asset, str) or not asset:
        return error("bad_request", 400, field="asset")
    status = cookfile_api.apply_thumbnail(path, asset)
    if status == "unknown_asset":
        return error("bad_request", 400, field="asset")
    if status != "OK":
        return cookfile_api.unreadable(status, error)
    return jsonify(api_response("OK")), 200
```

- [x] **Step 4: Verify, check for tree pollution, commit**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_files_cookfile_assets.py -q
git status --short   # expect: no new files in the repo root, no static/img/tmp/* symlinks
ls /tmp | grep pifire-upload- ; echo "staging dirs left: $?"   # expect none
.venv/bin/ruff format blueprints/api_files/ tests/web/test_api_files_cookfile_assets.py
jj desc -m "feat(api-files): cook-file asset upload, delete and thumbnail selection"
```

**Deliverable:** the full media surface, with the real Pillow pipeline exercised, traversal tested on the asset filename separately from the archive name, and a thumbnail that cannot be pointed at a non-existent asset.

---


**CORRECTED:** the drafted `assert not os.path.exists("/tmp/pifire")` fails on any dev
box that ran PiFire before the predictable-staging-path fix -- a stale /tmp/pifire full
of old uuid dirs is still there. The shipped test asserts on the per-cook entry
`/tmp/pifire/{parent_id}` instead, which is what the fix actually guarantees.
### Task 8: TypeScript types + API clients

**Files:**
- Create: `web-react/src/helpers/files/fileTypes.ts`
- Create: `web-react/src/helpers/files/filesApi.ts` + `filesApi.test.ts`
- Create: `web-react/src/helpers/files/cookfileApi.ts` + `cookfileApi.test.ts`

**Interfaces:**
- Produces:
  ```ts
  export interface FileListItem { filename: string; title: string; thumbnail: string }
  export interface FileListing { items: FileListItem[]; page: number; last_page: number;
                                 per_page: number; reverse: boolean; total: number }
  export type FileKind = "cookfiles" | "recipes";
  export function fetchFileListing(kind: FileKind, opts?: {page?: number; perPage?: number;
                                   reverse?: boolean; baseUrl?: string}): Promise<FileListing>
  export function thumbnailUrl(thumbnail: string, baseUrl?: string): string
  ```
  and, in `cookfileApi.ts`, `CookFileDetail` / `CookFileChartData` / `CookFileComment` / `CookFileAsset` / `CookFileError` plus one function per E3-E16.
- Consumes: E1-E16.

- [x] **Step 1: `fileTypes.ts`**

```ts
/** One row of a managed-folder listing (GET /api/files/{cookfiles,recipes}).
 *
 * `filename` is a BARE name, never a path — the server refuses anything that
 * resolves outside the configured folder (common/file_browser.py
 * resolve_managed_file), and it is the identity used by every other endpoint
 * and by the /cookfiles/:filename route.
 *
 * `thumbnail` is a RELATIVE "{parentId}/{name}" produced by
 * file_mgmt/media.py's unpack_thumb, to be prefixed with /static/img/tmp/ —
 * or "" when the archive has no thumbnail or could not be opened. Use
 * thumbnailUrl() rather than concatenating at each call site. */
export interface FileListItem {
  filename: string;
  title: string;
  thumbnail: string;
}

export interface FileListing {
  items: FileListItem[];
  page: number;
  last_page: number;
  per_page: number;
  reverse: boolean;
  total: number;
}

export type FileKind = "cookfiles" | "recipes";

/** The per-page choices the server whitelists (blueprints/api_files/routes.py
 * _PER_PAGE_CHOICES), mirroring the Flask lists' dropdown. Anything else 400s. */
export const PER_PAGE_CHOICES = [5, 10, 25, 50, 100] as const;

/** Placeholder shipped with Flask (static/img/pifire-cf-thumb.png), used by
 * both legacy lists when an archive has no thumbnail. */
export const FALLBACK_THUMB = "/static/img/pifire-cf-thumb.png";
```

- [x] **Step 2: `filesApi.ts`**

```ts
import { FALLBACK_THUMB, type FileKind, type FileListing } from "./fileTypes";

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

/** Resolve a listing row's `thumbnail` to a URL, falling back to the shipped
 * placeholder. Both Flask lists do exactly this
 * (cookfile/_cookfile_list.html:22-26, recipes/_recipefile_list.html:30-34). */
export function thumbnailUrl(thumbnail: string, baseUrl: string = BASE_URL): string {
  return thumbnail ? `${baseUrl}/static/img/tmp/${thumbnail}` : `${baseUrl}${FALLBACK_THUMB}`;
}

export interface ListingOptions {
  page?: number;
  perPage?: number;
  reverse?: boolean;
  baseUrl?: string;
}

/**
 * GET /api/files/{kind} — the JSON listing that did not exist before this
 * work. The legacy equivalents return Jinja fragments
 * (blueprints/cookfile/routes.py:267, blueprints/recipes/routes.py:123), which
 * a typed client cannot consume.
 *
 * Defaults deliberately match the Flask pages' first call so the React list
 * opens on the same rows: cookfiles `gotoCFPage(1, true, 10)`
 * (history/js/history.js:337). The recipes page uses reverse=false
 * (recipes.js:84) — its caller passes that explicitly.
 */
export async function fetchFileListing(
  kind: FileKind,
  { page = 1, perPage = 10, reverse = true, baseUrl = BASE_URL }: ListingOptions = {},
): Promise<FileListing> {
  const qs = new URLSearchParams({
    page: String(page),
    per_page: String(perPage),
    reverse: String(reverse),
  });
  const res = await fetch(`${baseUrl}/api/files/${kind}?${qs}`);
  if (!res.ok) throw new Error(`GET /api/files/${kind} failed: HTTP ${res.status}`);
  return (await res.json()) as FileListing;
}
```

- [x] **Step 3: `filesApi.test.ts`**

```ts
import { expect, rs, test } from "@rstest/core";
import { fetchFileListing, thumbnailUrl } from "./filesApi";

function mockFetch(body: unknown, ok = true, status = 200) {
  const fetchMock = rs.fn(async () => ({ ok, status, json: async () => body }));
  rs.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

test("sends the Flask page's default window", async () => {
  const fetchMock = mockFetch({ items: [], page: 1, last_page: 1, per_page: 10, reverse: true, total: 0 });
  await fetchFileListing("cookfiles");
  const url = String(fetchMock.mock.calls[0][0]);
  expect(url).toContain("/api/files/cookfiles?");
  expect(url).toContain("page=1");
  expect(url).toContain("per_page=10");
  expect(url).toContain("reverse=true");
});

test("passes an explicit window through", async () => {
  const fetchMock = mockFetch({ items: [], page: 3, last_page: 5, per_page: 25, reverse: false, total: 120 });
  const listing = await fetchFileListing("recipes", { page: 3, perPage: 25, reverse: false });
  expect(String(fetchMock.mock.calls[0][0])).toContain("/api/files/recipes?");
  expect(listing.page).toBe(3);
});

test("throws on a non-ok response rather than returning a half-listing", async () => {
  mockFetch({}, false, 400);
  await expect(fetchFileListing("cookfiles")).rejects.toThrow("HTTP 400");
});

test("thumbnailUrl prefixes a relative thumbnail and falls back when empty", () => {
  expect(thumbnailUrl("abc-123/thumb.png", "")).toBe("/static/img/tmp/abc-123/thumb.png");
  expect(thumbnailUrl("", "")).toBe("/static/img/pifire-cf-thumb.png");
});
```

- [x] **Step 4: `cookfileApi.ts`**

Types first — every field verified against the endpoint payloads in Tasks 3-7:

```ts
import type { HistoryAnnotation, HistoryDataset, HistoryProbeMapper } from "../history/historyApi";

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

/** A cook file's metadata.json, plus the two epoch fields the endpoint adds.
 *
 * `starttime`/`endtime` are HH:MM:SS display strings (the Flask template can
 * only ever show a time of day — common/app.py:289-290 overwrites the epochs
 * in place). `*_epoch` are the raw millisecond epochs, kept so a client can
 * render a date. */
export interface CookFileMetadata {
  title: string;
  units: string;
  thumbnail: string;
  id: string;
  version: string;
  starttime: string;
  endtime: string;
  starttime_epoch: number;
  endtime_epoch: number;
}

/** One row of events.json. The `_c` fields were computed at cook time by
 * process_metrics (common/common.py:521-556) and stored — they are not
 * recomputed on read. */
export interface CookFileEvent {
  id: number;
  mode: string;
  starttime_c: string;
  endtime_c: string;
  augerontime_c: string;
  estusage_m: string;
  estusage_i: string;
  pellet_level_start: number;
  pellet_level_end: number;
  timeinmode: string;
}

export interface CookFileTotals {
  augerontime: string;
  estusage_m: string;
  estusage_i: string;
  cooktime: string;
  pellet_level_start: number;
  pellet_level_end: number;
}

export interface CookFileComment {
  id: string;
  text: string;
  date: string;
  time: string;
  edited: string;
  assets: string[];
}

export interface CookFileAsset {
  id: string;
  filename: string;
  type: string;
}

/** Probe key -> display label, per series role. The rename table edits
 * `probes` only, which is all the Flask table offers
 * (cookfile/index.html:162). */
export interface CookFileLabels {
  probes: Record<string, string>;
  targets: Record<string, string>;
  primarysp: Record<string, string>;
}

export interface CookFileDetail {
  filename: string;
  metadata: CookFileMetadata;
  graph_labels: CookFileLabels;
  events: CookFileEvent[];
  event_totals: CookFileTotals | Record<string, never>;
  comments: CookFileComment[];
  assets: CookFileAsset[];
}

/** GET /api/files/cookfiles/chart. Structurally a subset of HistoryChartData:
 * no `graph_labels`, no `minutes`. historyAdapter's toChartInput reads only
 * `time_labels` and `chart_data`, which is why the shipped adapter is reusable
 * (see cookfileAdapter.ts for the one guard it needs). */
export interface CookFileChartData {
  time_labels: unknown[];
  chart_data: HistoryDataset[];
  probe_mapper: HistoryProbeMapper;
  annotations: Record<string, HistoryAnnotation>;
}

/** The 422 body: the file opened but would not load. `errortype` drives the
 * repair/convert prompt, exactly as cookfile/cferror.html:24 branches on it. */
export interface CookFileError {
  status: number;
  message: string;
  errortype: "version" | "asset" | "other" | null;
}

export class CookFileRequestError extends Error {
  readonly detail: CookFileError;
  constructor(detail: CookFileError) {
    super(detail.message);
    this.name = "CookFileRequestError";
    this.detail = detail;
  }
}
```

Then the calls. A shared `read`/`write` pair keeps the error contract in one place:

```ts
async function read<T>(path: string, file: string, baseUrl: string): Promise<T> {
  const res = await fetch(`${baseUrl}/api/files/cookfiles/${path}?file=${encodeURIComponent(file)}`);
  if (res.ok) return (await res.json()) as T;
  throw new CookFileRequestError(await toError(res));
}

async function toError(res: Response): Promise<CookFileError> {
  //  A 404 from a proxy (or an HTML error page) is not JSON. Never let a parse
  //  failure mask the status the caller has to branch on.
  const body = (await res.json().catch(() => ({}))) as {
    message?: string;
    data?: { errortype?: CookFileError["errortype"] };
  };
  return {
    status: res.status,
    message: body.message ?? `HTTP ${res.status}`,
    errortype: body.data?.errortype ?? null,
  };
}

/** POST helper for every write endpoint. Envelope is common/app.py
 * api_response: {data, result, message}, with result === "OK" on success —
 * the same contract helpers/pellets/pelletsApi.ts already speaks. */
async function write<T>(path: string, body: unknown, baseUrl: string): Promise<T> {
  const res = await fetch(`${baseUrl}/api/files/cookfiles/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new CookFileRequestError(await toError(res));
  const envelope = (await res.json()) as { result?: string; message?: string; data?: T };
  if (envelope.result !== "OK") {
    throw new CookFileRequestError({
      status: res.status,
      message: envelope.message ?? "rejected",
      errortype: null,
    });
  }
  return envelope.data as T;
}

export const fetchCookFileDetail = (file: string, baseUrl = BASE_URL) =>
  read<CookFileDetail>("detail", file, baseUrl);

export const fetchCookFileChart = (file: string, baseUrl = BASE_URL) =>
  read<CookFileChartData>("chart", file, baseUrl);

/** Download URLs are plain hrefs, not fetches — the browser must own the
 * save dialog. They live under /api/files, which IS proxied in dev
 * (rsbuild.config.ts:29); HistoryPage's /history/export link is not, and
 * downloads the SPA's index.html in `bun run dev`. */
export const cookFileDownloadUrl = (file: string, baseUrl = BASE_URL) =>
  `${baseUrl}/api/files/cookfiles/download?file=${encodeURIComponent(file)}`;

export const cookFileExportUrl = (file: string, kind: "data" | "events", baseUrl = BASE_URL) =>
  `${baseUrl}/api/files/cookfiles/export?file=${encodeURIComponent(file)}&kind=${kind}`;

export const deleteCookFile = (file: string, baseUrl = BASE_URL) =>
  write<null>("delete", { file }, baseUrl);

export const setCookFileTitle = (file: string, title: string, baseUrl = BASE_URL) =>
  write<null>("title", { file, title }, baseUrl);

export const renameCookFileLabel = (
  file: string,
  oldLabel: string,
  newLabel: string,
  baseUrl = BASE_URL,
) => write<{ new_label_safe: string }>("label", { file, old_label: oldLabel, new_label: newLabel }, baseUrl);

export const recoverCookFile = (file: string, action: "upgrade" | "repair", baseUrl = BASE_URL) =>
  write<null>("recover", { file, action }, baseUrl);

export const addCookFileComment = (file: string, text: string, baseUrl = BASE_URL) =>
  write<CookFileComment>("comments", { file, action: "add", text }, baseUrl);

export const updateCookFileComment = (file: string, id: string, text: string, baseUrl = BASE_URL) =>
  write<CookFileComment>("comments", { file, action: "update", id, text }, baseUrl);

export const deleteCookFileComment = (file: string, id: string, baseUrl = BASE_URL) =>
  write<null>("comments", { file, action: "delete", id }, baseUrl);

export const setCommentAssets = (file: string, id: string, assets: string[], baseUrl = BASE_URL) =>
  write<{ assets: string[] }>("comments/assets", { file, id, assets }, baseUrl);

export const deleteCookFileAssets = (file: string, assets: string[], baseUrl = BASE_URL) =>
  write<null>("assets/delete", { file, assets }, baseUrl);

export const setCookFileThumbnail = (file: string, asset: string, baseUrl = BASE_URL) =>
  write<null>("thumbnail", { file, asset }, baseUrl);

/** Multipart, so it does not go through write(): the archive name rides as a
 * form field and each image as a repeated `assets` part, matching
 * request.files.getlist("assets") on the server. */
export async function uploadCookFileAssets(
  file: string,
  images: File[],
  baseUrl = BASE_URL,
): Promise<CookFileAsset[]> {
  const form = new FormData();
  form.append("file", file);
  for (const image of images) form.append("assets", image);
  const res = await fetch(`${baseUrl}/api/files/cookfiles/assets/upload`, { method: "POST", body: form });
  if (!res.ok) throw new CookFileRequestError(await toError(res));
  const envelope = (await res.json()) as { result?: string; data?: { assets: CookFileAsset[] } };
  if (envelope.result !== "OK") throw new CookFileRequestError(await toError(res));
  return envelope.data?.assets ?? [];
}

export async function uploadCookFile(archive: File, baseUrl = BASE_URL): Promise<string> {
  const form = new FormData();
  form.append("file", archive);
  const res = await fetch(`${baseUrl}/api/files/cookfiles/upload`, { method: "POST", body: form });
  if (!res.ok) throw new CookFileRequestError(await toError(res));
  const envelope = (await res.json()) as { result?: string; data?: { filename: string } };
  if (envelope.result !== "OK") throw new CookFileRequestError(await toError(res));
  return envelope.data?.filename ?? archive.name;
}

/** Asset URLs. Fullsize at /{id}/{name}, thumbnail at /{id}/thumbs/{name} —
 * the layout read_json_file_data creates (file_mgmt/common.py:71-83) and both
 * Flask templates use (cookfile/index.html:28, :382). */
export const assetUrl = (parentId: string, name: string, baseUrl = BASE_URL) =>
  `${baseUrl}/static/img/tmp/${parentId}/${name}`;

export const assetThumbUrl = (parentId: string, name: string, baseUrl = BASE_URL) =>
  `${baseUrl}/static/img/tmp/${parentId}/thumbs/${name}`;
```

- [x] **Step 5: `cookfileApi.test.ts` — cover the error contract, not just the happy path**

At minimum:
- `fetchCookFileDetail` returns the parsed body on 200.
- A **422** throws `CookFileRequestError` carrying `errortype: "version"` — this is what drives the repair prompt, so it must survive the client.
- A **404** throws with `status: 404` and `errortype: null`.
- A non-JSON error body still yields `HTTP 500`, not a parse throw.
- `write()` throws when the envelope is `{result: "Error"}` **even on HTTP 200** (the `api_response` envelope can carry a failure at 200).
- `cookFileDownloadUrl` / `cookFileExportUrl` percent-encode a filename containing a space and `#`.
- `uploadCookFileAssets` appends one `assets` part per image and one `file` field.

```ts
test("a 422 carries the errortype the repair prompt needs", async () => {
  rs.stubGlobal("fetch", rs.fn(async () => ({
    ok: false,
    status: 422,
    json: async () => ({ result: "Error", message: "WARNING: Older cookfile version format! ",
                         data: { errortype: "version" } }),
  })));
  await expect(fetchCookFileDetail("X.pifire", "")).rejects.toMatchObject({
    detail: { status: 422, errortype: "version" },
  });
});

test("download urls percent-encode names with spaces and hashes", () => {
  expect(cookFileDownloadUrl("Sunday Brisket #2.pifire", "")).toBe(
    "/api/files/cookfiles/download?file=Sunday%20Brisket%20%232.pifire",
  );
});
```

- [x] **Step 6: Gate and commit**

```bash
cd web-react
bun run typecheck && bun run lint && bun run test && bun run gen:types:check
jj desc -m "feat(web-react): typed clients for the /api/files cook-file surface"
```

**Deliverable:** every endpoint reachable from TypeScript with a single, tested error contract.

---

### Task 9: `CookFileList` — the saved-cook list on `/history`

**Files:**
- Create: `web-react/src/components/cookfiles/CookFileList.tsx` + `.test.tsx`
- Create: `web-react/src/components/cookfiles/cookfiles.css`
- Modify: `web-react/src/components/history/HistoryPage.tsx` + `HistoryPage.test.tsx`

**Interfaces:**
- Consumes `fetchFileListing`, `thumbnailUrl`, `deleteCookFile`, `uploadCookFile`, `cookFileDownloadUrl`.
- Produces `export function CookFileList(): JSX.Element` — self-contained, no props. Links each row to `/cookfiles/:filename`.

- [x] **Step 1: Write the component test first**

```tsx
import { expect, rs, test } from "@rstest/core";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { CookFileList } from "./CookFileList";

const LISTING = {
  items: [
    { filename: "2026-07-20--1400-CookFile.pifire", title: "Sunday Brisket", thumbnail: "id-1/t.png" },
    { filename: "2026-07-19--0900-CookFile.pifire", title: "", thumbnail: "" },
  ],
  page: 1, last_page: 3, per_page: 10, reverse: true, total: 24,
};

function mount() {
  return render(
    <MemoryRouter>
      <CookFileList />
    </MemoryRouter>,
  );
}

test("lists saved cooks with titles and links to the detail route", async () => { /* ... */ });
test("falls back to the placeholder thumbnail when the archive has none", async () => { /* ... */ });
test("shows the filename when a cook has no title", async () => { /* ... */ });
test("next page refetches with page+1 and does not blank the table while loading", async () => { /* ... */ });
test("the sort button flips reverse and refetches", async () => { /* ... */ });
test("changing per-page refetches and resets to page 1", async () => { /* ... */ });
test("delete asks for confirmation first, then refetches the listing", async () => { /* ... */ });
test("dismissing the delete confirmation deletes nothing", async () => { /* ... */ });
test("a failed listing shows an error banner and keeps the last good rows", async () => { /* ... */ });
test("an empty folder shows an empty state, not an empty table", async () => { /* ... */ });
test("upload posts the chosen file then refetches", async () => { /* ... */ });
test("an ERROR-titled row is still openable so the repair prompt is reachable", async () => { /* ... */ });
```

The last one matters: `browse_files` reports `title: "ERROR"` for an archive it cannot open (Task 1), and that row is precisely the one a user needs to click to reach the repair prompt. It must not be disabled.

- [x] **Step 2: Implement**

Rules this component must obey:
- **Derived state is computed in render.** The request-id / `Outcome` idiom from `HistoryPage.tsx:38-74` is the house pattern for "is a request in flight" — copy it rather than inventing a `loading` state set inside an effect.
- **Keep the previous listing across a refetch and across a failure** (same reasoning as `HistoryPage.tsx:49-51`): paging must not flash an empty table.
- **Destructive action needs confirmation.** Follow `components/pellets/ProfileEditor.tsx`'s confirm pattern; do not use `window.confirm`.
- Row link: `<Link to={`/cookfiles/${encodeURIComponent(item.filename)}`}>`.
- Download is an `<a href={cookFileDownloadUrl(...)} download>`, never a fetch.
- Do **not** port the `disabled` "Send to Cloud" button (F2 #9).

Sketch of the load path:

```tsx
const [request, setRequest] = useState({ id: 0, page: 1, perPage: 10, reverse: true });
const [listing, setListing] = useState<FileListing | null>(null);
const [outcome, setOutcome] = useState<{ id: number; failed: boolean } | null>(null);

useEffect(() => {
  let cancelled = false;
  const { id, page, perPage, reverse } = request;
  fetchFileListing("cookfiles", { page, perPage, reverse })
    .then((fresh) => {
      if (cancelled) return;
      setListing(fresh);
      setOutcome({ id, failed: false });
    })
    .catch(() => {
      if (!cancelled) setOutcome({ id, failed: true });
    });
  return () => {
    cancelled = true;
  };
}, [request]);

// Plain render-time computation — no effect, no mirrored state.
const loading = outcome === null || outcome.id !== request.id;
const failed = !loading && outcome.failed;
```

`reload()` is `setRequest((r) => ({ ...r, id: r.id + 1 }))`, used after a delete and after an upload.

- [x] **Step 3: Mount it on `/history`**

In `HistoryPage.tsx`, add a second `pf-section` below the chart section:

```tsx
<div className="pf-section">
  <h2 className="pf-section-title">Saved cooks</h2>
  <div className="pf-section-body">
    <CookFileList />
  </div>
</div>
```

This is the faithful placement: Flask renders the cook-file list on `/history` (`blueprints/history/templates/history/index.html:64`), not on a page of its own. **No `NavBar.tsx` change** — adding a nav entry would diverge from Flask and would require editing `NavBar.test.tsx`'s list assertions for no user-visible gain.

Update `HistoryPage.test.tsx`: mock `./cookfiles/CookFileList` to a stub so the existing chart assertions stay isolated, and add one test that the section renders.

- [x] **Step 4: Gate and commit**

```bash
cd web-react
bun run typecheck && bun run lint && bun run test && bun run gen:types:check && bun run build
jj desc -m "feat(web-react): saved-cook list on /history with upload, download and delete"
```

**Deliverable:** `/history` shows the saved cooks, paginated, sortable, with working upload/download/delete — the capability React users did not have at all.

---


**CORRECTED (rstest, not vitest):** `rs.mock`'s factory must be SYNCHRONOUS, so the
usual `async () => ({ ...(await import(mod)) })` partial-mock idiom throws
"An async mock factory is not supported". Use the import attribute instead:
`import * as actual from "..." with { rstest: "importActual" };` then spread `actual`.
### Task 10: `CookFilePage` + `CookFileMeta` — the detail route

**Files:**
- Create: `web-react/src/components/cookfiles/CookFilePage.tsx` + `.test.tsx`
- Create: `web-react/src/components/cookfiles/CookFileMeta.tsx` + `.test.tsx`
- Modify: `web-react/src/components/App.tsx`

**Interfaces:**
- `CookFilePage` reads `useParams<{ filename: string }>()`, fetches `fetchCookFileDetail`, owns `detail | error | loading`, and renders `CookFileMeta` / `CookFileChart` / `EventsTable` / `CommentList` / `MediaPanel`.
- `CookFileMeta` props:
  ```ts
  interface CookFileMetaProps {
    filename: string;
    metadata: CookFileMetadata;
    labels: CookFileLabels;
    assets: CookFileAsset[];
    onChanged: () => void;
  }
  ```

- [x] **Step 1: Tests**

`CookFilePage.test.tsx`:
- renders the title once the detail resolves
- shows a loading hint before it resolves
- **a 422 with `errortype: "version"` shows "Attempt Conversion", not "Attempt Repair"** — mirrors `cferror.html:24-38`
- a 422 with `errortype: "asset"` or `"other"` shows "Attempt Repair"
- clicking the recover button calls `recoverCookFile` with the matching action and refetches on success
- a 404 shows a "not found" state with a link back to `/history`, and **no** repair button (there is nothing to repair)
- the chart/events/comments panels are absent while the file is unreadable

`CookFileMeta.test.tsx`:
- shows title, filename, units, start and end time
- editing the title calls `setCookFileTitle` and reflects the new value
- a failed title save surfaces an error and **restores the previous value** (the repo has a whole plan on save-failure surfacing — `2026-07-25-react-save-failure-surfacing.md`)
- one rename row per entry in `labels.probes`, seeded with the current name
- saving a label calls `renameCookFileLabel` and re-keys the row to `new_label_safe` (the server's `create_safe_name` result, **not** the typed text — `common/app.py:325` strips non-alphanumerics, so "Main Grill" becomes "MainGrill")
- a **409 `label_exists`** shows "That label already exists", distinct from a generic failure
- thumbnail falls back to the placeholder when `metadata.thumbnail` is `""`
- Download buttons are anchors carrying the right `href`

- [x] **Step 2: Implement `CookFilePage`**

```tsx
// The cook-file detail route. One fetch on mount (and on `reloadNonce`), no
// polling: a saved cook is a finished, immutable dataset except for the edits
// this page itself makes. That is the whole difference from HistoryPage, which
// is why none of its window/refresh machinery is reused here.
//
// Flask reaches this view through a form POST that re-renders the entire page
// server-side after every mutation (render_cookfile_page, common/app.py:270).
// Here each panel calls its endpoint and then asks the page to refetch, so a
// title edit does not blow away an open comment editor.
export function CookFilePage() {
  const { filename = "" } = useParams<{ filename: string }>();
  const [reloadNonce, setReloadNonce] = useState(0);
  const [detail, setDetail] = useState<CookFileDetail | null>(null);
  const [problem, setProblem] = useState<CookFileError | null>(null);
  // ... request-id idiom as in Task 9 ...
}
```

The recover prompt:

```tsx
{problem?.status === 422 && (
  <div className="pf-banner pf-banner--error">
    <p>This cook file could not be loaded.</p>
    <p className="pf-settings-hint">{problem.message}</p>
    {/* cferror.html:24 branches on exactly this: a version error offers
        conversion, anything else offers repair. Both warn about data loss,
        because upgrade_cookfile rewrites every member in place
        (file_mgmt/cookfile.py:306). */}
    <button type="button" className="pf-modal-btn" onClick={() => recover(
      problem.errortype === "version" ? "upgrade" : "repair",
    )}>
      {problem.errortype === "version" ? "Attempt Conversion" : "Attempt Repair"}
    </button>
    <p className="pf-settings-hint">Warning: some file elements may be lost.</p>
  </div>
)}
```

- [x] **Step 3: Implement `CookFileMeta`**

Local edit state seeded from props uses the **render-phase adjustment** idiom (`components/settings/tabs/SafetyTab.tsx`, `components/dashboard/SetpointEntry.tsx`) — never `useEffect` + `setState`:

```tsx
const [titleDraft, setTitleDraft] = useState(metadata.title);
const [seededTitle, setSeededTitle] = useState(metadata.title);
if (metadata.title !== seededTitle) {
  setSeededTitle(metadata.title);
  setTitleDraft(metadata.title);
}
```

Label rows are `Object.entries(labels.probes)`. On save, call `renameCookFileLabel` and then `onChanged()` — the parent refetch is what re-keys the row, so the component never has to reconcile the safe-name transform itself.

- [x] **Step 4: Route**

In `App.tsx`, inside the `AppShell` children array, after the `/history` entry:

```tsx
// The detail view for ONE saved cook file. The list that links here is a
// section of /history, mirroring Flask, where the cook-file list lives on
// the history page and "Open" posts through to cookfile/index.html
// (blueprints/history/routes.py:104).
{ path: "/cookfiles/:filename", element: <CookFilePage /> },
```

- [x] **Step 5: Gate and commit**

```bash
cd web-react
bun run typecheck && bun run lint && bun run test && bun run gen:types:check && bun run build
jj desc -m "feat(web-react): cook-file detail route with metadata, label rename and recovery"
```

**Deliverable:** `/cookfiles/<name>` renders a cook file's metadata, edits its title and probe labels, and offers the correct recovery action for a broken archive.

---


**CORRECTED (build order):** CookFilePage composes CookFileMeta only in this task.
CookFileChart, EventsTable, CommentList and MediaPanel do not exist until Tasks 11-13,
so each of those tasks adds its own `pf-section` to the page rather than this task
shipping four dead stubs.

**Also corrected:** the label rename sends the probe KEY as `old_label`
(`_rename_graph_label` pops it from `graph_labels[category]`'s KEYS), not the display
name the plan's prose implies. The accessible names of the rename controls use the key
for the same reason: two probes may share a display name.
### Task 11: the chart — reuse `HistoryChart`, add annotations

**Files:**
- Create: `web-react/src/components/history/annotationPlugin.ts` + `.test.ts`
- Create: `web-react/src/components/cookfiles/cookfileAdapter.ts` + `.test.ts`
- Create: `web-react/src/components/cookfiles/CookFileChart.tsx` + `.test.tsx`
- Modify: `web-react/src/components/history/HistoryChart.tsx` + `.test.tsx`

**Interfaces:**
- `annotationPlugin(annotations: HistoryAnnotation[]): uPlot.Plugin` — a `draw` hook.
- `HistoryChart` gains `annotations?: HistoryAnnotation[]` (epoch **seconds** on `xMin`), default `undefined` → plugin not installed, `/history` unchanged.
- `toCookChartInput(data: CookFileChartData): ChartInput | null` — `null` when nothing is plottable.
- `CookFileChart` props: `{ filename: string }`.

- [x] **Step 1: `cookfileAdapter.ts` — the one real gap**

```ts
import { hasPlottableHistory, toChartInput } from "../history/historyAdapter";
import type { ChartInput } from "../history/historyAdapter";
import type { CookFileChartData } from "../../helpers/files/cookfileApi";
import type { HistoryAnnotation, HistoryChartData } from "../../helpers/history/historyApi";

/**
 * Adapt a saved cook file's chart payload to the shipped uPlot chart.
 *
 * The shipped adapter is reused as-is: toChartInput reads ONLY `time_labels`
 * and `chart_data` (historyAdapter.ts:57-66), the exact two keys this payload
 * carries. It is not re-implemented here.
 *
 * The one thing that differs is the x axis. Live history always writes numeric
 * epoch milliseconds (common/datastore_accessors.py), so historyAdapter's
 * `ms / 1000` is safe there. A cook file's graph_data.json is whatever
 * prepare_chartdata wrote AT COOK TIME, and pre-v1.5 archives (and hand-built
 * ones) can carry HH:MM:SS strings — "12:00:00" / 1000 is NaN, and uPlot
 * renders a NaN x-axis as a blank canvas with no error anywhere. Rather than
 * loosening the live-history adapter, non-numeric labels are rejected here and
 * the caller shows "this cook file's chart data is in an old format" with the
 * repair action beside it.
 */
export function toCookChartInput(data: CookFileChartData): ChartInput | null {
  const times = data.time_labels;
  if (times.length === 0) return null;
  if (!times.every((t) => typeof t === "number" && Number.isFinite(t))) return null;

  const asHistory = {
    time_labels: times as number[],
    chart_data: data.chart_data,
  } as HistoryChartData;

  if (!hasPlottableHistory(asHistory)) return null;
  return toChartInput(asHistory);
}

/** Annotations arrive as a DICT keyed `event_<n>` (common/app.py:142), with
 * `xMin` in epoch MILLISECONDS. HistoryChart's x axis is epoch SECONDS, the
 * same conversion toChartInput does for the data. */
export function toChartAnnotations(
  annotations: Record<string, HistoryAnnotation>,
): HistoryAnnotation[] {
  return Object.values(annotations).map((a) => ({
    ...a,
    xMin: a.xMin / 1000,
    xMax: a.xMax / 1000,
  }));
}
```

Tests must include: numeric labels adapt; **string labels return `null`**; an empty payload returns `null`; a payload whose datasets are all empty returns `null`; annotations convert ms→s and preserve `borderColor` and `label.content`.

- [x] **Step 2: `annotationPlugin.ts`**

```ts
import type uPlot from "uplot";
import type { HistoryAnnotation } from "../../helpers/history/historyApi";

/**
 * Draw mode-change markers as vertical rules with a rotated caption.
 *
 * The Flask charts get this from chartjs-plugin-annotation
 * (cookfile.js:19-21, :122). uPlot has no annotation concept, so this is a
 * `draw` hook: uPlot calls it after the series are painted, and valToPos
 * converts a data-space x into a canvas x for whatever zoom is current — which
 * is what makes the markers track a drag-zoom for free.
 *
 * `xMin` must already be epoch SECONDS (see cookfileAdapter.toChartAnnotations).
 */
export function annotationPlugin(annotations: HistoryAnnotation[]): uPlot.Plugin {
  return {
    hooks: {
      draw: (u: uPlot) => {
        const ctx = u.ctx;
        ctx.save();
        //  Clip to the plotting area so a marker outside the current zoom
        //  window cannot paint over the axes.
        ctx.beginPath();
        ctx.rect(u.bbox.left, u.bbox.top, u.bbox.width, u.bbox.height);
        ctx.clip();
        for (const a of annotations) {
          const x = u.valToPos(a.xMin, "x", true);
          if (x < u.bbox.left || x > u.bbox.left + u.bbox.width) continue;
          ctx.strokeStyle = a.borderColor;
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.moveTo(x, u.bbox.top);
          ctx.lineTo(x, u.bbox.top + u.bbox.height);
          ctx.stroke();
          const caption = a.label?.content;
          if (caption) {
            ctx.save();
            ctx.translate(x + 4, u.bbox.top + 4);
            ctx.fillStyle = a.borderColor;
            ctx.font = "11px sans-serif";
            ctx.textBaseline = "top";
            ctx.fillText(caption, 0, 0);
            ctx.restore();
          }
        }
        ctx.restore();
      },
    },
  };
}
```

Test it against a fake `uPlot` object (a `ctx` of `rs.fn()` spies plus a `bbox` and a `valToPos`): asserts one `stroke()` per in-range annotation, zero for an out-of-range one, the caption drawn when `label.content` is present and skipped when it is not, and `save`/`restore` balanced.

- [x] **Step 3: Extend `HistoryChart`**

Add the optional prop and install the plugin. Two things to get right:

```ts
export interface HistoryChartProps {
  times: number[];
  series: ChartSeries[];
  height?: number;
  /** Mode-change markers, x in epoch SECONDS. Omitted on /history, which
   *  receives annotations from the API but has never drawn them
   *  (helpers/history/historyApi.ts:44-46). */
  annotations?: HistoryAnnotation[];
}
```

- The plugin list is read **only when uPlot is constructed**, so a change of annotations must join `seriesShape`/`height` in the "rebuild" condition — otherwise toggling them off would do nothing until the next shape change. Add a `useStableAnnotations` memo mirroring `useStableSeriesShape` (`HistoryChart.tsx:38-41`) and include it in `rebuild` and in the effect deps.
- `plugins: [tooltipPlugin(seriesShape), ...(annotations ? [annotationPlugin(annotations)] : [])]`.

`HistoryChart.test.tsx` gains: with no `annotations` prop the plugin count is unchanged (guards `/history` against a visual regression); with annotations it is installed; changing the annotations array rebuilds the plot.

- [x] **Step 4: `CookFileChart.tsx`**

```tsx
// Fetches the chart separately from the detail payload, exactly as Flask does
// (cookfile/index.html renders first with a "Loading Graph..." spinner, then
// cookfile.js:133 calls refreshChart). A long cook's graph_data is by far the
// largest member of the archive, and making the metadata card wait for it
// would leave the page blank for seconds on a Pi.
export function CookFileChart({ filename }: { filename: string }) { /* ... */ }
```

Controls, matching F2 #16/#17 as far as the shipped chart allows:
- **Annotation Enable** checkbox (default on), passing `undefined` when off.
- **Reset zoom** button using the remount-key idiom from `HistoryPage.tsx:127` (`key={`${filename}-${resetNonce}`}`).
- **Download CSV File** anchor → `cookFileExportUrl(filename, "data")`, mirroring the button under the Flask graph card (`index.html:223`).
- When `toCookChartInput` returns `null`: render an explicit message distinguishing "no chart data in this file" from "this file's chart data is in an old format" (the string-label case), the latter pointing at the repair action.

- [x] **Step 5: Gate and commit**

```bash
cd web-react
bun run typecheck && bun run lint && bun run test && bun run gen:types:check && bun run build
jj desc -m "feat(web-react): cook-file chart reusing HistoryChart, plus uPlot annotations"
```

**Deliverable:** a saved cook renders on the same uPlot chart the live history page uses, now with mode-change annotations and a toggle — and `/history` is byte-identical because the prop defaults to off.

---

### Task 12: `EventsTable` — events, totals, per-event detail, CSV

**Files:**
- Create: `web-react/src/components/cookfiles/EventsTable.tsx` + `.test.tsx`

**Interfaces:**
```ts
interface EventsTableProps {
  filename: string;
  events: CookFileEvent[];
  totals: CookFileTotals | Record<string, never>;
  units: string;
}
```

- [x] **Step 1: Tests**

```tsx
test("renders one row per event with the stored display strings", () => { /* ... */ });
test("shows imperial pellet usage for F and metric for C", () => { /* ... */ });
test("renders the totals row", () => { /* ... */ });
test("omits the totals row when event_totals is empty", () => { /* ... */ });
test("shows the no-data state when events is empty, and hides the CSV link", () => { /* ... */ });
test("expanding a row reveals that event's detail fields", () => { /* ... */ });
test("the CSV link points at the events export", () => { /* ... */ });
```

Two behaviours are load-bearing and must be asserted explicitly:

1. **The units switch reads the COOK FILE's units, not the app's.** `cookfile/index.html:268-272` branches on `metadata['units']` for the per-row usage column and `:285-289` does the same for the totals row. A cook recorded in °F must keep showing pounds/ounces even after the user switches the app to °C — the numbers in the archive were never converted.
2. **`event_totals` may legitimately be `{}`.** Task 3 returns an empty dict when a file has fewer than two events, because `prepare_event_totals` indexes `events[-2]` unconditionally (`common/app.py:168`). The Flask page 500s on such a file; this table must render the rows it has and simply omit the totals.

- [x] **Step 2: Implement**

- Columns exactly as `index.html:250-259`: Mode, Begin, End, Auger Time, Est. Pellet Use, Pellet Level Start, Pellet Level End, plus a detail control.
- Every cell is a **stored string** (`starttime_c`, `endtime_c`, `augerontime_c`, `estusage_i`/`estusage_m`) computed at cook time by `process_metrics`. Do **not** recompute or reformat them — that would silently disagree with the CSV export, which reads the same rows.
- Usage column: `units === "F" ? event.estusage_i : event.estusage_m`.
- The Flask page renders **nine** mode-specific detail modals (`index.html:309-329`, macros `render_stop`/`render_startup`/…). Porting nine bespoke layouts is not warranted for a diagnostic panel: render a **generic key/value disclosure** over the event's own fields, skipping the `_c`/display duplicates already shown in the row. Recorded in "Out of scope" as a deliberate simplification, not an oversight.
- Use a native `<details>`/`<summary>` disclosure rather than a modal — no focus trap to get wrong, and it prints.
- CSV link: `<a href={cookFileExportUrl(filename, "events")} download>`, rendered only when `events.length > 0` (matching `index.html:342`, which hides the footer for an empty event list).

- [x] **Step 3: Gate and commit**

```bash
cd web-react
bun run typecheck && bun run lint && bun run test && bun run gen:types:check
jj desc -m "feat(web-react): cook-file events table with totals and CSV export"
```

**Deliverable:** the events table with totals, per-event detail and a working CSV link, correct for both unit systems.

---

### Task 13: `CommentList` + `MediaPanel`

**Files:**
- Create: `web-react/src/components/cookfiles/CommentList.tsx` + `.test.tsx`
- Create: `web-react/src/components/cookfiles/MediaPanel.tsx` + `.test.tsx`

**Interfaces:**
```ts
interface CommentListProps {
  filename: string;
  parentId: string;          // metadata.id — the /static/img/tmp/{id} folder
  comments: CookFileComment[];
  assets: CookFileAsset[];
  onChanged: () => void;
}

interface MediaPanelProps {
  filename: string;
  parentId: string;
  assets: CookFileAsset[];
  thumbnail: string;
  onChanged: () => void;
}
```

- [x] **Step 1: `CommentList` tests**

```tsx
test("renders each comment's date, time and text", () => { /* ... */ });
test("shows an Edited badge only when `edited` is non-empty", () => { /* ... */ });
test("newlines in a comment render as line breaks without injecting HTML", () => { /* ... */ });
test("adding a comment posts the text, clears the box and refetches", () => { /* ... */ });
test("editing a comment shows a textarea seeded with its text, and Save posts it", () => { /* ... */ });
test("cancelling an edit restores the original text and posts nothing", () => { /* ... */ });
test("deleting a comment asks for confirmation first", () => { /* ... */ });
test("a failed save keeps the editor open with the user's text intact", () => { /* ... */ });
test("attached assets render as thumbnails linking to the fullsize image", () => { /* ... */ });
test("the attach picker preselects the comment's current assets", () => { /* ... */ });
test("saving the picker posts the WHOLE resulting asset list, not a toggle", () => { /* ... */ });
test("the lightbox steps prev/next within the comment's assets and wraps", () => { /* ... */ });
```

Three notes for the implementer:

- **Never `dangerouslySetInnerHTML`.** Flask stores plain text and converts `\n` to `<br>` at render time (`common/app.py:287`); Task 6 deliberately does not. Render the text node and let `white-space: pre-wrap` handle newlines. The test above is the guard.
- **The picker posts the whole list.** Flask toggles one asset per click and infers the direction from a client-sent `state` string (`routes.py:565-585`), so a stale view inverts the operation. `setCommentAssets` takes the final list.
- **Prev/next wraps within the comment's own assets**, matching `navimage` (`routes.py:130-141`) — not across all assets in the file. This is pure client-side arithmetic now; no endpoint needed, and E12/E13 deliberately have no `navimage` equivalent.

- [x] **Step 2: `MediaPanel` tests**

```tsx
test("renders a thumbnail grid over every asset", () => { /* ... */ });
test("uploading images posts them and refetches", () => { /* ... */ });
test("multiple files are uploaded in one request", () => { /* ... */ });
test("selecting assets and confirming deletes exactly those", () => { /* ... */ });
test("delete requires confirmation and names the count", () => { /* ... */ });
test("choosing a thumbnail posts that asset and marks it current", () => { /* ... */ });
test("the current thumbnail is indicated in the grid", () => { /* ... */ });
test("an upload rejected as disallowed_file shows the reason", () => { /* ... */ });
test("the empty state invites an upload instead of showing an empty grid", () => { /* ... */ });
```

- [x] **Step 3: Implement**

- Thumbnails: `assetThumbUrl(parentId, asset.filename)`. Fullsize: `assetUrl(parentId, asset.filename)`.
- The file input takes `accept="image/*"` and `multiple` (matching `index.html:504`), and is cleared after a successful upload so re-picking the same file re-fires `change`.
- Delete selection is local state; the request carries the array once. Confirmation names the count ("Remove 3 images?").
- All four flows end with `onChanged()` so the page refetches and every panel sees the new asset list — a comment's thumbnails must disappear when the underlying asset is deleted, and `remove_assets` already scrubs the comment references server-side (`file_mgmt/common.py:220-228`), so a refetch is the whole synchronisation story.

- [x] **Step 4: Gate and commit**

```bash
cd web-react
bun run typecheck && bun run lint && bun run test && bun run gen:types:check && bun run build
jj desc -m "feat(web-react): cook-file comments and media panels"
```

**Deliverable:** comments and media at parity with the Flask page, minus the toggle-inversion bug.

---

### Task 14: e2e round trip and the full gate

**Files:**
- Create: `web-react/tests/e2e/cookfiles.spec.ts`
- Modify: `docs/superpowers/react-migration-backlog.md`

**Interfaces:** none — this task proves the whole slice against a real backend.

- [ ] **Step 1: Write the spec**

```ts
import { expect, test } from "@playwright/test";

// This suite drives ONE shared, stateful PiFire (playwright.config.ts:23,
// workers: 1) and is globally destructive to it. It therefore creates its own
// cook file by UPLOADING one rather than by running a cook: a real cook cycle
// would move the global grill mode and flush the history store out from under
// history.spec.ts, which seeds rows to get a chart to render.
//
// The uploaded fixture is built in-test so the spec is self-contained and
// leaves nothing behind: every test deletes what it created in `finally`.
test.describe("cook file browser", () => {
  test("uploads, lists, opens, edits and deletes a cook file", async ({ page }) => {
    // 1. upload a fabricated .pifire through the real file input
    // 2. /history shows it in Saved cooks
    // 3. click through to /cookfiles/<name>
    // 4. chart canvas renders (uPlot mounts a <canvas> inside .pf-history-chart)
    // 5. events table shows the Totals row
    // 6. rename the title; reload; the new title survives
    // 7. add a comment; reload; it survives
    // 8. delete the file from /history; it disappears from the list
  });

  test("a corrupt archive offers Attempt Repair, not a blank page", async ({ page }) => {
    // upload bytes that are not a zip, open it, assert the recover prompt
  });

  test("the CSV export link downloads a CSV, not the SPA shell", async ({ page }) => {
    // Guards the /api/files placement: /history/export is NOT proxied by the
    // dev server (rsbuild.config.ts:27-37) and downloads index.html instead.
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("link", { name: "Download CSV File", exact: true }).click(),
    ]);
    expect(download.suggestedFilename()).toMatch(/\.csv$/);
  });
});
```

Requirements:
- Build the `.pifire` in the spec with a small zip helper (or check a fixture into `web-react/tests/e2e/artifacts/`), matching the member set `read_cookfile` requires (`file_mgmt/cookfile.py:181`) and a `version` the server accepts.
- **Locators must not rely on loose text matching** — use `getByRole` with `exact: true`, or scope with `getByRole("region", { name: "Saved cooks" })`. "Download CSV File" appears under both the graph card and the events card in Flask; in React they must be distinguishable, so scope each one.
- Clean up in `finally` — the suite is destructive and shared, and a leftover cook file changes the listing another test asserts on.
- Run with `PIFIRE_DB_PATH` pointing at the DB the backend serves.

- [ ] **Step 2: Run the whole gate, in the main checkout**

```bash
cd web-react
bun install
bun run typecheck && bun run lint && bun run test && bun run gen:types:check && bun run build
bun run test:e2e
```

```bash
cd /home/dannyb/sources/PiFire
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q
```

Expected: the Python suite is green **and larger than before** — count it and record the delta. Note that `tests/web/*` SKIP in a no-chromium worktree, so this run **must** happen in the main checkout before merging.

```bash
git status --short   # expect: no pifire.db, no settings.json, no os_info.json,
                     # no pelletdb.json, no static/img/tmp/* symlinks
```

- [ ] **Step 3: Update the backlog**

In `docs/superpowers/react-migration-backlog.md`:
- Move the cook-file half of the "recipes + cookfile" entry (line 297) into SHIPPED, naming this plan.
- Rewrite the remaining entry as **recipes only**, and **correct the "share a data model" claim** — record that they share a container and a listing shape, that `/api/files/recipes` already exists, and point at the plan-2 outline below.
- Add the History-page note: `/history` now hosts the saved-cook list.

- [ ] **Step 4: Commit**

```bash
jj desc -m "test(web-react): e2e cook-file round trip; docs: reconcile the backlog"
```

**Deliverable:** a green full gate and a backlog that matches the code.

---

## Parallelization

```
T1 (file_browser + kill os.system)
 ├─→ T2 (listing E1/E2) ─┐
 │                       ├─→ T8 (TS types + clients) ─┬─→ T9  (CookFileList + /history)
 └─→ T3 (detail+chart) ──┤                            ├─→ T10 (CookFilePage + Meta)
     T4 (files E5-E8) ───┤                            ├─→ T11 (chart + annotations)
     T5 (mutations) ─────┤                            ├─→ T12 (EventsTable)
     T6 (comments) ──────┤                            └─→ T13 (Comments + Media)
     T7 (assets) ────────┘                                        │
                                                                  ▼
                                                          T14 (e2e + full gate)
```

- **Wave 1 — T1 alone.** Everything depends on `resolve_managed_file`, and it removes two shell-outs that later tasks' tests execute. Do not start anything else until it lands.
- **Wave 2 — dispatch T2, T3, T4, T5, T6, T7 CONCURRENTLY.** Each owns one test module and one region of `blueprints/api_files/`. **Caveat: they all append to `blueprints/api_files/routes.py` and (T3-T7) to `cookfile_api.py`.** File-disjointness is *not* achieved here, so either run them in two sub-waves (T2+T3+T4, then T5+T6+T7) or accept that the controller resolves append-only conflicts at linearization. The second is realistic because every task appends whole functions at the end of the file; the first is safer. **Recommendation: two sub-waves.**
- **Wave 3 — T8 alone.** It consumes the shape of every endpoint from wave 2 and is the single source of the TS types; splitting it would create two drifting copies.
- **Wave 4 — dispatch T9, T10, T11, T12, T13 CONCURRENTLY.** These *are* genuinely file-disjoint: one component pair each. Two shared-file exceptions to schedule around:
  - **T9 modifies `HistoryPage.tsx`** and **T11 modifies `HistoryChart.tsx`/`HistoryChart.test.tsx`** — different files, no conflict.
  - **T10 modifies `App.tsx`**; nothing else in this wave touches it.
  - T10 renders the components T11/T12/T13 create. Have T10 import them and let the type checker fail until they land, or have T10 land last within the wave. **Recommendation: T11/T12/T13 first, then T9+T10.**
- **Wave 5 — T14.** Must run in the **main checkout** (chromium).
- **Reviews parallelize** (read-only): review every wave-2 task concurrently, and every wave-4 task concurrently.

**Isolated jj workspaces are MANDATORY for any concurrent wave. Disjoint file sets alone are not sufficient** — concurrent agents sharing one working copy cross-pollute commits regardless of which files they touch, and this repo has multiple concurrent Claude sessions committing to the same branch.

```bash
jj workspace add --name cf2 -r <plan-commit> ../PiFire-cf2   # T2
jj workspace add --name cf3 -r <plan-commit> ../PiFire-cf3   # T3
jj workspace add --name cf4 -r <plan-commit> ../PiFire-cf4   # T4
# ... one per concurrent task ...

# In EVERY new workspace that touches web-react:
cp /home/dannyb/sources/PiFire/.lsp.json ../PiFire-cfN/       # gitignored; its absence is the real cause of "LSP unavailable"
cd ../PiFire-cfN/web-react && bun install                     # node_modules is gitignored, ~200MB
```

**Behavioural-reach caution (this repo has been bitten by it three times):** file-disjointness is necessary but not sufficient.

- T1 changes the *return shape* of `get_recipefilelist_details` / `_get_cookfilelist_details` from a hand-rolled loop to a shared one. Anything asserting on the legacy HTML listings (`tests/web/test_page_cookfile.py::test_history_lists_and_opens_cookfile_via_real_ui`, `test_page_recipes.py::test_recipefilelist_via_direct_post`) exercises the merged behaviour and must be re-run **after** linearization, not just inside T1's workspace.
- T11 modifies `HistoryChart`, which `/history` renders. `web-react/tests/e2e/history.spec.ts` and the dashboard layout baselines are downstream; run the full e2e suite at integration, not per-task.
- No task in this plan adds a runtime dependency, so no `bun install` is needed at linearization beyond the per-workspace one — but **verify `bun.lock` is unchanged** before merging, and commit it if it is not.

**Integration (controller):** `jj workspace forget cf2 cf3 …` → linearize with **change ids** → `cd web-react && bun install` → verify the merged state (`bun run typecheck && bun run lint && bun run test && bun run gen:types:check && bun run build`) → `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q` in the **main checkout** (so the chromium web tests actually run) → `bun run test:e2e` → `rm -rf ../PiFire-cf*` → update the ledger and backlog.

---

## Out of scope, deliberately

1. **The recipes editor.** Split out; outline below.
2. **Fixing the legacy Flask cookfile routes' unvalidated paths** (F5). `send_file(request.form["dl_cookfile"])` at `blueprints/cookfile/routes.py:162` is an arbitrary file read, and `cookfile_update`'s four branches accept an arbitrary path into `update_json_file_data`. Containing them would **flip characterization tests that deliberately pass full paths** (`tests/web/test_page_cookfile.py:537-542` documents the full-path contract as intended), so it is a behaviour change needing human sign-off — the same shape as the two bugs that needed it in the latent-bugfix sweep. **File it as its own item.** This plan's contribution is that the *new* surface cannot inherit the problem, and `common/file_browser.resolve_managed_file` is sitting there ready when someone takes it on.
3. **Wheel/pinch zoom and y-axis pan on the chart.** Flask has them via chartjs-plugin-zoom (`cookfile.js:22-39`); `HistoryChart` has x-drag-zoom plus a Reset button. Accepted regression — matching Flask would mean either a second chart component or a zoom rework that changes `/history` too.
4. **Wiring annotations into `/history` itself.** `HistoryChart` gains the capability in Task 11 and `/api/history/chart` already returns `annotations` (`blueprints/api_history/routes.py:46`), so this is a one-line prop. Left out because it changes the live page's appearance and its layout baselines, which deserves its own before/after look.
5. **The nine mode-specific event-detail modals** (`cookfile/index.html:309-329`). Replaced with a generic key/value disclosure — see Task 12.
6. **"Send to Cloud."** `disabled` in both Flask lists. Not a capability.
7. **Fixing `prepare_csv` / `prepare_metrics_csv`'s folder-dependent output naming** (F7 #4). The new endpoint routes around it with `os.path.basename`; the legacy routes still call the broken path. Fixing `common/app.py` would be correct but touches a function three legacy routes and `/history/export` share.
8. **Fixing `HistoryPage`'s dev-broken `/history/export` link** (F7 #5). One line in `rsbuild.config.ts` (proxy `/history`) or a move to `/api/files`. Reported; not bundled here.
9. **A `NavBar` entry.** The list lives on `/history`, as in Flask.

---

## Could NOT verify (flagged, not guessed)

- **Whether any real, in-the-wild `.pifire` carries string `time_labels`.** The pre-v1.5 upgrade path (`file_mgmt/cookfile.py:267-302`) rebuilds `graph_data` through `prepare_chartdata`, which writes numeric epochs — so a *converted* file should be numeric. The only string-labelled example found is the hand-built test fixture (`tests/web/test_page_cookfile.py:151`). The guard in Task 11 is cheap and fails visibly rather than blankly; keep it regardless, but the "old format" message may never be seen in practice.
- **Rendering cost of a long cook on Pi-class hardware.** `full_graph` sends the archive's stored `graph_data` with **no downsampling** — the LTTB work applies to `prepare_chartdata` at *cook* time and at *live* read time, not to re-reading a saved file. A multi-day cook could carry a lot of points. uPlot handles far more than Chart.js, so this is expected to be an improvement over the Flask page, but it was not measured. If it bites, the fix is to run `select_indices` over the stored series in the chart endpoint — do not pre-optimise.
- **Whether `paginate_list` is stable for equal sort keys.** Filenames within a folder are unique, so it does not arise here.

---

## Self-review checklist (run before declaring the plan done)

- [ ] Every endpoint in F5 has a task, a test module and a traversal test.
- [ ] Every capability in F2 is either implemented by a task or listed in "Out of scope" with a reason.
- [ ] No task depends on a file another concurrent task creates.
- [ ] `grep -rn "os.system" file_mgmt/` returns nothing.
- [ ] No new test writes outside `tempfile.mkdtemp`.
- [ ] `git status --short` is clean after a full run.

---
---

# Plan 2 (OUTLINE ONLY): React Recipes Editor

> **This is a task-list outline, not an executable plan.** It has no code, no
> exact interfaces and no verified line-level detail beyond F3 above. Write it
> out in full — as its own document, `docs/superpowers/plans/<date>-react-recipes.md`
> — before executing it. The capability inventory in **F3** and the data-model
> facts in the scope decision are the research it should start from; they were
> verified against live code on 2026-07-26 and do not need re-deriving.

**Depends on plan 1:** Task 1 (`common/file_browser.py`) and Task 2 (`GET /api/files/recipes`, which ships in plan 1 and needs no further backend work). Both are prerequisites, not parallel work.

**Estimated 17 tasks:**

| # | Task | Notes to carry forward |
|---|---|---|
| R1 | `GET /api/files/recipes/detail?file=` — full recipe (metadata + ingredients + instructions + steps + assets) | Mirrors plan 1 Task 3. `read_recipefile` (`file_mgmt/recipes.py:197`) returns all four members. |
| R2 | `POST /api/files/recipes/create` — new recipe; `download`, `upload`, `delete` | `create_recipefile` is shell-out-free after plan 1 T1. Returns the new bare filename. The existing delete route is already safe but is form/JSON-mixed; the new one is uniform. |
| R3 | `POST /api/files/recipes/metadata` — one field per request, or a whole-metadata patch | **`food_probes` is structural**: it must reshape `trigger_temps.food` on every step in the same write (`blueprints/recipes/routes.py:159-167`). Test the grow and the shrink. |
| R4 | `POST /api/files/recipes/ingredients` — add / update / delete | **Renaming an ingredient rewrites every instruction that references it, and deleting removes it from every instruction** (`routes.py:180-186`, `:231-234`). Both are cross-member writes; pin them. |
| R5 | `POST /api/files/recipes/instructions` — add / update / delete | Fields: `text`, `ingredients[]`, `step`, `assets[]`. |
| R6 | `POST /api/files/recipes/steps` — insert-at-index / update / delete | Insert is positional (`routes.py:281`), not append. New steps get one `0` per `food_probes`. |
| R7 | `POST /api/files/recipes/assets` — upload / select / deselect / delete, per section | Sections: `splash` \| `ingredients` \| `instructions` \| `delete`. `splash` writes **both** `metadata.image` and `metadata.thumbnail` (`routes.py:412-414`). Prefer whole-list writes over Flask's per-item toggle, as plan 1 Task 6 did for comments. |
| R8 | `POST /api/files/recipes/run` — start a recipe | Wraps `POST /api/control`. **Must send `{filename, start_step: 0, step: 0}` explicitly** — `_api_post_control` deep-merges (F7 #6), so a bare `{filename}` inherits the previous run's step. The server should build the path from the bare filename; the client must not send `./recipes/...`. |
| R9 | `GET /api/files/recipes/run-status` — or derive it from the socket | **Decide first**: `control.recipe.step` and `control.mode` may already reach the client via `socket_dash_data`. Check `blueprints/mobile/socket_io.py` and `controller/runtime/modes/base.py:488-505` (`status_data["recipe"]`, `status_data["recipe_paused"]`) before adding a polling endpoint. Flask polls every 4 s (`recipes.js:289`); the socket would make that free. |
| R10 | TS types + `recipeApi.ts` client | Reuses `helpers/files/fileTypes.ts` and `fetchFileListing("recipes", { reverse: false })` from plan 1 — note the **opposite default sort** (`recipes.js:84`). |
| R11 | `RecipeList` + `/recipes` route + nav | Flask's recipes page IS its own destination and `NavBar.tsx:19` already has a disabled "Recipes" entry — enable it here (unlike cookfiles). Update `NavBar.test.tsx`. |
| R12 | `RecipeView` — the read-only view | Star rating, difficulty badge, ingredients/instructions tables, program steps. |
| R13 | `RecipeEditor` — metadata + description | Per-field save with the save-failure surfacing pattern. |
| R14 | `IngredientsEditor` + `InstructionsEditor` | The cross-reference rewrites in R4 mean a save here must refetch both sections. |
| R15 | `StepsEditor` | The largest single component: mode select, hold temp (shown only for Hold), primary + per-food trigger temps with enable switches, timer, pause, notify + message. Max 600 °F / 300 °C from `metadata.units`. |
| R16 | `RecipeAssetManager` | Four sections, upload, select, delete. |
| R17 | `RecipeRunStatus` + e2e + full gate | Active-step highlight and scroll-into-view. **The e2e spec must not actually start a cook** — the suite is `workers: 1` and globally destructive; starting Recipe mode moves the shared grill mode and flushes history out from under `history.spec.ts`. Assert the POST payload, or stub at the API boundary. |

**Hazards to carry into that plan:**

- **`create_recipefile` shells out** — fixed by plan 1 Task 1. Re-verify with `grep -rn "os.system" file_mgmt/` before writing any test that creates a recipe.
- **Running a recipe is a real grill action.** Every test that can reach `POST /api/control` with `mode: "Recipe"` must neutralize the path first; `is_real_hardware()` is not enough.
- **`recipes.js` sends `./recipes/` + filename to `/api/control`** (`:274`). The new endpoint must accept a bare name and build the path server-side, or the React client re-introduces the client-supplied-path problem plan 1 exists to avoid.
- **Recipe comments have no UI and no route.** `comments.json` exists in the archive but nothing writes it (`tests/web/test_page_recipes.py:36-40`). Do not build a comments panel from the schema alone; confirm with the human whether it is wanted.

</content>
</invoke>
