# React Recipes Editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Flask's `/recipes` surface with a React `/recipes` page — browse, view, run, and edit `.pfrecipe` archives — backed by a typed JSON API that never accepts a filesystem path from the client.

**Architecture:** A new `blueprints/api_files/recipes_api.py`, sibling to the shipped `cookfile_api.py`, exposes the recipe archive over `/api/files/recipes/*`. Every route resolves a **bare filename** through `common/file_browser.py::resolve_managed_file`, exactly as the cook-file surface does. On the client, `helpers/files/recipeApi.ts` mirrors `cookfileApi.ts`, and `components/recipes/` mirrors `components/cookfiles/`. Run status needs no polling endpoint: `socket_dash_data` already publishes `recipeStatus`.

**Tech Stack:** Flask + `blueprints/api_files`; React 19 + react-router + rsbuild; rstest for unit tests; Playwright for e2e; Tailwind v4 `@apply`; Biome + ESLint.

**Written 2026-07-27.** Supersedes the 17-row outline at the bottom of
`plans/2026-07-26-react-recipes-cookfile.md`, which said in terms that it had to
be written out in full before execution. The capability inventory in that
document's **F3** is the research this plan starts from and is not re-derived.

---

## Human rulings taken 2026-07-27, before a line was written

1. **No recipe comments panel.** `comments.json` is in every archive and
   `recipeassetmanager` has a `comments` branch, but nothing in either UI has
   ever written a recipe comment (`tests/web/test_page_recipes.py:33-38` says so
   outright). Building one from the schema alone invents a feature rather than
   porting one. The archive member is preserved byte-for-byte on every write;
   the React surface ignores it. **Backlog entry required** (Task 18).
2. **Two shipping slices.** Slice A (Tasks 1-8) is independently usable: browse,
   view, run, nav. Slice B (Tasks 9-17) is the editor. Review and merge between
   them.
3. **Both bugs found during research are fixed here, with regression tests.**
   See G7 and G8 below. They sit directly on the path this feature exposes.

---

## Global Constraints

Every task's requirements implicitly include this section.

### G1. Safety — read this before writing any test

- **Neutralize `os.system` / `subprocess` / `sudo` / `reboot` / `shutdown`
  BEFORE running any test that can reach the control process, the installer or
  the updater.** An `is_real_hardware()` flag is **not** enough — it defaults to
  `True`, and this repo has really rebooted the developer's machine **twice**.
- **Moving code out of a module a test `patch.object`s silently disarms the
  mock.** This has happened three times. If you relocate a function, re-check
  every `patch.object` that named its old home.
- **Never `pkill -f`** — it matches your own shell (exit 144). Use `pgrep`, then
  `kill <pid>`.
- **Do NOT run `bun run test:e2e`** (the full `app` project). `roundtrip.spec.ts`
  puts the developer's real grill into Startup mode and `settings.spec.ts`
  flushes the history store. Use `bun run test:e2e:fidelity`.
- **`POST /api/files/recipes/run` starts a real cook.** Every test that can
  reach it must stub the control write. The endpoint's own `mode == Stop` guard
  (G6) is a product requirement, not a test safeguard — do not lean on it.
- No test writes outside `tempfile.mkdtemp`. The `api_files_folders` fixture
  already isolates `RECIPE_FOLDER` in all the places it is read.

### G2. Toolchain

- **bun, never npm.** `bun install`, `bun run <script>`. Commit `bun.lock`.
- Python: `uv run pytest tests/`, with `QT_QPA_PLATFORM=offscreen
  SDL_VIDEODRIVER=dummy` exported. Bare `python` gives false failures.
- **`.venv/bin/ruff format` before every commit that touches Python.** Never
  `uvx ruff` — the repo pins ruff `<0.16` and a newer one floods legacy findings.
- Gate for every web-react task: `bun run typecheck && bun run lint && bun run test`.
  `lint` is `biome check . && eslint .` — formatting counts.
- The repo is Python 3.14+. `except A, B:` without parentheses is
  ruff-canonical here. Do not "fix" it.

### G3. Version control

- Commit with **`jj describe --stdin`** fed by a quoted heredoc. There is **no
  `-F` flag**. Never `git commit` — the repo is colocated and git silently forks
  history.
- `jj squash` opens an editor when both commits carry a description. Use
  `jj squash -u`.
- Backticks inside double-quoted zsh arguments get eaten. Use a quoted heredoc.

### G4. The path rule — the reason this blueprint exists

A client sends a **bare filename**. The server resolves it through
`resolve_managed_file(folder, name)`, which realpath-contains it. **No route
added by this plan may accept a path**, and no client added by this plan may
send one. The legacy `blueprints/recipes/` surface, which concatenates
`RECIPE_FOLDER + filename` unvalidated, stays exactly as it is — it is
load-bearing for the Flask page, which is not retired until the general
retirement pass (backlog ruling 5).

### G5. Response envelope

Writes answer `common/app.py::api_response`: `{"data", "result", "message"}`
with `result == "OK"`. Reads answer a bare payload. Statuses: **400**
`bad_request` with `data.field`; **404** `not_found` (uncontained or missing);
**409** conflict; **422** `unreadable` with `data.errortype`; **200** otherwise.

### G6. Running a recipe requires `mode == "Stop"`

`POST /api/files/recipes/run` answers **409** `not_stopped` unless
`control["mode"] == Mode.STOP`. This is a **deliberate divergence** from Flask,
which posts from any mode (`static/recipes/js/recipes.js:270-293`). It matches
the guard `POST /api/probe_map` already applies and makes every test on this
path safe by construction. Record it in the closeout.

### G7. `blueprints/mobile/socket_io.py:685` is a live command injection

```python
filepath = f"{recipe_folder}{filename}"
os.system(f"rm {filepath}")
```

`filename` comes straight from `request["recipes_action"]["filename"]` — no
`secure_filename`, no containment, no escaping. The sibling handler in
`blueprints/recipes/routes.py::_recipes_json_deletefile` was hardened and
regression-tested (`tests/web/test_page_recipes.py:492-531`); **this copy never
was**. Fixed in Task 2.

**Harden, do not delete.** No in-repo caller exists, but `post_app_data` is the
PiFire mobile app's API and `recipe_start` beside it is presumably live. Removing
the handler would break a third-party client for no safety gain that hardening
does not also deliver.

### G8. `convert_recipe_units` crashes every time it is called

```python
# file_mgmt/recipes.py:212-217
def convert_recipe_units(recipe, units):
    for probe, settemp in step["settemps"]:
```

No step has a `settemps` key — the schema is `trigger_temps: {primary, food[]}`.
`controller/runtime/controller.py:140-141` calls this whenever a recipe's saved
`metadata.units` differs from the live `settings["globals"]["units"]`, so running
such a recipe raises inside the control-process recipe loop. Fixed in Task 3.

**`0` means "unset" throughout the step schema** (`_default_recipe_metadata` seeds
disabled trigger temps as `0`). A unit conversion that maps `0 °F` to `-17 °C`
would silently arm every disabled trigger. `0` passes through unconverted.

### G9. Invariants a rewrite must not change

Each is pinned by an existing test; breaking one is a regression, not a
refactor.

| Invariant | Pinned by |
|---|---|
| `create_recipefile` disambiguates a same-minute collision with a `-N` suffix on the `.pfrecipe` name only | `tests/unit/file_mgmt/test_recipes.py` |
| Changing `food_probes` grows/shrinks `trigger_temps.food` on **every** step | `test_page_recipes.py:227-255` |
| Renaming an ingredient rewrites that name inside **every** instruction that referenced it; deleting removes it from every instruction | `test_page_recipes.py:258-293` |
| A step is **inserted at an index**, not appended | `test_page_recipes.py:340-345` |
| The `splash` asset writes **both** `metadata.image` and `metadata.thumbnail`, and clearing it clears both | `test_page_recipes.py:545-566` |
| Assets are served from `./static/img/tmp/{parent_id}/` via a symlink into a process-private base | `test_page_recipes.py:711-757` |
| `reciperunstatus` ignores the request's filename while a recipe is running | `test_page_recipes.py:397-426` |

### G10. Naming

Python: `snake_case`, handlers in `recipes_api.py` named for their action.
React: `PascalCase` components in `web-react/src/components/recipes/`, helpers in
`web-react/src/helpers/files/recipeApi.ts`. CSS classes are `pf-rcp-*`.

---

## Verified facts — checked against live code 2026-07-27, do not re-derive

### F1. The archive

A `.pfrecipe` is a zip with four JSON members plus `assets/` and
`assets/thumbs/`. Members confirmed in both `create_recipefile`
(`file_mgmt/recipes.py:160`) and `read_recipefile` (`:203`):
`metadata`, `recipe`, `comments`, `assets`.

**`metadata.json`** — `file_mgmt/recipes.py:35-52`:

```
author ""      username ""    id <uuid4>     title ""      description ""
image ""       thumbnail ""   units <globals.units>        prep_time 0
cook_time 0    rating 5       difficulty "Easy"            version "1.1.0"
food_probes 2
```

**`recipe.json`** — three keys (`:145-148`):

- `ingredients: []`, each `{"name": "", "quantity": "", "assets": []}` (`:55-65`)
- `instructions: []`, each `{"text": "", "ingredients": [], "assets": [], "step": 0}` (`:68-79`).
  **`instructions[].ingredients` is a list of ingredient NAME STRINGS**, not
  indices — which is why a rename must cascade (G9).
- `steps: []`, seeded with three defaults (`:92-131`). Per step:
  `mode` (str), `trigger_temps` (`{primary: int, food: [int, ...]}` with
  `len(food) == metadata.food_probes`), `hold_temp` (int), `timer` (int,
  **minutes** — `controller.py:319` multiplies by 60), `notify` (bool),
  `message` (str), `pause` (bool). **Steps carry no `assets` key.**

**`comments.json`** — `[]`, never written by any recipes code path.
**`assets.json`** — `[]`, written only by `file_mgmt/media.py::add_asset`, each
entry `{"id", "filename": "{id}.{type}", "type"}`.

### F2. Existing Python API

- `read_recipefile(filename)` → `(file_data, status)`; `file_data` carries the
  four members; `status` is `"OK"` or the first member's error
  (`file_mgmt/recipes.py:197-209`).
- **There is no `write_recipefile`.** Persistence is per-member via
  `file_mgmt/common.py::update_json_file_data(filedata, filename, jsonfile)`
  (`:106-139`), which rewrites the zip preserving every other member.
- `read_json_file_data(filename, jsonfile, unpackassets=True)`
  (`file_mgmt/common.py:45-103`) — the `assets` branch is what materialises
  images under `./static/img/tmp/{parent_id}`.
- `create_recipefile()` (`:134-194`) takes no arguments and returns the new
  `.pfrecipe` path.
- `remove_assets(filename, assetlist, filetype="cookfile")`
  (`file_mgmt/common.py:203`) is **already recipe-aware**: with
  `filetype="recipefile"` it scrubs names out of `metadata.image`,
  `metadata.thumbnail`, every `recipe["ingredients"][].assets` and every
  `recipe["instructions"][].assets`. Call it with that argument; do not write a
  parallel helper.
- `add_asset(filename, assetpath, assetfile)` (`file_mgmt/media.py:26`) →
  `(asset_id, filetype)`.
- **`get_recipefilelist_details` reads the module constant
  `file_mgmt.recipes.RECIPE_FOLDER`, not `current_app.config["RECIPE_FOLDER"]`**
  (`recipes.py:227-231`). Nothing in this plan calls it; note it so nobody
  "simplifies" a fixture by patching only one of the two.

### F3. Existing REST surface

`blueprints/api_files/routes.py` already serves **the listing for both kinds**:

```python
_KINDS = {
    "cookfiles": ("HISTORY_FOLDER", ".pifire"),
    "recipes":   ("RECIPE_FOLDER",  ".pfrecipe"),
}
```

`GET /api/files/recipes?page=&per_page=&reverse=` works **today** and needs no
work. `per_page` is whitelisted to `(5, 10, 25, 50, 100)`; `reverse` is any
string other than exactly `"false"`. Response is
`{items:[{filename,title,thumbnail}], page, last_page, per_page, reverse, total}`.

**Everything past the listing is cookfile-only** — `routes.py:127-341` hardcodes
`cookfile_folder()` / `cookfile_api.*` in every handler.

The shared helpers a recipes surface needs, verbatim (`routes.py:43-107`):

```python
def error(message, status, **data):
    """Uniform error envelope: {"result":"Error","message":...,"data":{...}}."""
    return jsonify(api_response("Error", message, data or None)), status


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


def json_body():
    """request.json, or {} for a body that is absent or not JSON."""
    try:
        return request.get_json(silent=True) or {}
    except BadRequest:
        return {}
```

`require_file` hardcodes the cook-file folder. **Task 1 parameterises it** —
see that task for the exact refactor and the call sites it touches.

### F4. Run status is already on the socket — R9 is answered

`blueprints/mobile/socket_io.py:330-336` composes into every `socket_dash_data`
frame:

```python
"recipeStatus": {
    "recipeMode": status["recipe"],
    "filename": control["recipe"]["filename"].split("/")[-1],
    "mode": status["mode"],
    "paused": status["recipe_paused"],
    "step": control["recipe"]["step"],
},
```

Emitted on the dash tick (`:217`, `:229`) **and directly to a client on connect**
(`:259`). **No polling endpoint is needed.** What the socket does *not* carry is
the current step's contents or the recipe's own metadata — the client already has
those from `GET /api/files/recipes/detail`, keyed by the `filename` and `step`
the socket supplies.

### F5. `control["recipe"]` and how a recipe starts

Shape (`common/defaults.py:531`):

```python
control["recipe"] = {"filename": "", "start_step": 0, "step": 0, "step_data": {}}
```

The only entry point today is a **Socket.IO event** —
`socket_io.py:687-703`, `_post_app_data_recipes` with `type == "recipe_start"`:

```python
write_control(
    control_delta(set_values={
        "updated": True,
        "mode": Mode.RECIPE,
        "recipe": {"filename": recipe_folder + filename},
    }),
    WriteKind.DELTA, origin="app-socketio",
)
```

**No REST route sets `Mode.RECIPE`** — verified across `common/api_commands.py`
and `blueprints/api/*`. Task 3 adds the first one.

`start_step`/`step`/`step_data` are populated by the control process itself when
`recipe_mode(start_step=...)` dispatches (`controller.py:611-613`). The client
must still send them explicitly: `_api_post_control` deep-merges, so a bare
`{filename}` inherits the previous run's `step`.

### F6. React pieces that are reusable as-is

| Symbol | File | Verdict |
|---|---|---|
| `FileListItem`, `FileListing`, `FileKind`, `PER_PAGE_CHOICES`, `FALLBACK_THUMB` | `helpers/files/fileTypes.ts` | **100% reusable.** `FileKind` already includes `"recipes"`. |
| `fetchFileListing(kind, {page, perPage, reverse, baseUrl})`, `thumbnailUrl` | `helpers/files/filesApi.ts` | **100% reusable.** |
| `ConfirmAction({open, title, message?, onConfirm, onCancel})` | `components/dashboard/ConfirmAction.tsx` | 100% reusable. |
| `SaveBar({onSave, saving, status, dirty?})` | `components/settings/SaveBar.tsx` | 100% reusable — depends only on the `SaveStatus` type. |
| `TextField`, `NumberField`, `Select`, `Toggle`, `StringListField`, `Section` | `components/settings/fields/` | 100% reusable. `NumberField` clamps on **blur**, not change. |
| `SaveStatus` | `helpers/settings/useSaveSettings.ts:7` | Type reusable; the hook itself is settings-coupled — copy the shape. |

`cookfileApi.ts:118-185` holds the envelope class and the `read`/`write`/
`postForm` helpers. **They are cookfile-path-hardcoded** (`/api/files/cookfiles/`
is baked into each). Task 4 lifts them into a shared module rather than copying
them a second time.

### F7. Contradictions found, and how this plan resolves them

1. The outline's R9 proposed `GET /api/files/recipes/run-status`. **Dropped** —
   F4 shows the socket already carries it.
2. The outline's R7 said "prefer whole-list writes over Flask's per-item toggle".
   Kept, and it is why `splash` needs its own explicit branch (G9).
3. The outline listed 17 tasks; this plan has 18 — Task 18 is the backlog write-up
   the standing rule requires.

---

## File Structure

### Python — created

| File | Responsibility |
|---|---|
| `blueprints/api_files/recipes_api.py` | Every recipe archive read/write handler. Takes resolved paths only. |
| `tests/web/test_api_files_recipes_read.py` | Detail, download, listing integration, traversal. |
| `tests/web/test_api_files_recipes_write.py` | create/upload/delete/run, metadata, ingredients, instructions, steps. |
| `tests/web/test_api_files_recipes_assets.py` | Asset upload/select/delete, splash pairing. |
| `tests/web/test_socket_recipe_delete_safety.py` | G7 regression: traversal + injection through the socket handler. |
| `tests/unit/file_mgmt/test_recipe_units.py` | G8 regression for `convert_recipe_units`. |

### Python — modified

| File | Change |
|---|---|
| `blueprints/api_files/routes.py` | Parameterise `require_file` by folder; add `recipe_folder()`; register the recipe routes. |
| `blueprints/mobile/socket_io.py` | G7: harden `recipe_delete`. |
| `file_mgmt/recipes.py` | G8: rewrite `convert_recipe_units` against the real schema. |
| `tests/web/archive_builders.py` | Extend `write_recipe` to seed steps/ingredients/instructions. |

### React — created

| File | Responsibility |
|---|---|
| `helpers/files/apiEnvelope.ts` | `FileRequestError`, `toError`, `read`/`write`/`postForm` parameterised by kind. |
| `helpers/files/recipeTypes.ts` | `RecipeMetadata`, `Ingredient`, `Instruction`, `RecipeStep`, `RecipeDetail`, `RecipeAsset`. |
| `helpers/files/recipeApi.ts` | Typed client for `/api/files/recipes/*`. |
| `helpers/recipes/runStatus.ts` | Reads `recipeStatus` off the live socket payload. |
| `components/recipes/RecipeList.tsx` | `/recipes` — list, pager, per-page, upload, new, delete. |
| `components/recipes/RecipePage.tsx` | `/recipes/:filename` — detail shell, view/edit toggle. |
| `components/recipes/RecipeView.tsx` | Read-only render. |
| `components/recipes/RecipeRunStatus.tsx` | Active-step highlight from the socket. |
| `components/recipes/RecipeMetaEditor.tsx` | Slice B. |
| `components/recipes/IngredientsEditor.tsx` | Slice B. |
| `components/recipes/InstructionsEditor.tsx` | Slice B. |
| `components/recipes/StepsEditor.tsx` | Slice B. |
| `components/recipes/RecipeAssetManager.tsx` | Slice B. |
| `components/recipes/recipes.css` | `pf-rcp-*` vocabulary. |

### React — modified

| File | Change |
|---|---|
| `components/App.tsx` | Register `/recipes` and `/recipes/:filename`. |
| `components/shell/NavBar.tsx` | `Recipes` gets `to: "/recipes"`. |
| `components/shell/NavBar.test.tsx` | Move `Recipes` from the disabled set to the linked set. |
| `src/styleCoverage.test.ts` | Add `components/recipes` to `SURFACES`. |
| `tests/e2e/pageSpecs.ts` | `recipe-list`, `recipe-detail` specs. |
| `tests/e2e/apiFixtures.ts` | `stubRecipes(page)`. |

---

# SLICE A — browse, view, run

---

### Task 1: parameterise `require_file`, add the recipe folder and `GET /api/files/recipes/detail`

**Files:**
- Modify: `blueprints/api_files/routes.py`
- Create: `blueprints/api_files/recipes_api.py`
- Modify: `tests/web/archive_builders.py`
- Test: `tests/web/test_api_files_recipes_read.py`

**Interfaces:**
- Produces: `routes.require_file(name, folder, *, must_exist=True)`,
  `routes.recipe_folder()`, `recipes_api.load(path)`,
  `recipes_api.detail_payload(struct, filename)`, `recipes_api.unreadable(status, error)`.
- Consumes: `common.file_browser.resolve_managed_file`,
  `file_mgmt.recipes.read_recipefile`, `common.app.api_response`.

- [ ] **Step 1: extend the recipe fixture builder**

`tests/web/archive_builders.py::write_recipe(recipe_dir, title)` currently seeds
empty lists. Every recipe test needs a realistic archive. Add keyword arguments,
defaulting to the current behaviour so existing callers are unaffected:

```python
def write_recipe(
    recipe_dir,
    title,
    *,
    food_probes=2,
    ingredients=None,
    instructions=None,
    steps=None,
    units="F",
):
    """Build a minimal-but-realistic .pfrecipe under recipe_dir.

    Defaults match file_mgmt/recipes.py's own defaults so a fixture archive and
    a create_recipefile() archive are interchangeable in a test.
    """
    if steps is None:
        steps = [
            {
                "mode": "Smoke",
                "trigger_temps": {"primary": 0, "food": [0] * food_probes},
                "hold_temp": 0,
                "timer": 0,
                "notify": False,
                "message": "",
                "pause": False,
            }
        ]
    ...
```

Preserve the existing four-member layout and the `metadata["id"]` uuid — the
asset-serving contract (G9) keys off it.

- [ ] **Step 2: write the failing detail test**

```python
# tests/web/test_api_files_recipes_read.py
import pytest

from tests.web.archive_builders import write_recipe

pytestmark = pytest.mark.usefixtures("api_files_folders")


@pytest.fixture
def client(api_files_client):
    return api_files_client


@pytest.fixture
def folders(api_files_folders):
    return api_files_folders


def test_detail_returns_the_four_editable_sections(client, folders):
    _history, recipe_dir = folders
    name = write_recipe(
        recipe_dir,
        "Brisket",
        ingredients=[{"name": "Brisket", "quantity": "1 packer", "assets": []}],
        instructions=[{"text": "Trim it", "ingredients": ["Brisket"], "assets": [], "step": 0}],
    )
    resp = client.get(f"/api/files/recipes/detail?file={name}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["metadata"]["title"] == "Brisket"
    assert body["recipe"]["ingredients"][0]["name"] == "Brisket"
    assert body["recipe"]["instructions"][0]["ingredients"] == ["Brisket"]
    assert len(body["recipe"]["steps"]) == 1
    assert body["assets"] == []
    # Ruling 1: comments are preserved in the archive but never published.
    assert "comments" not in body
```

- [ ] **Step 3: run it to confirm it fails**

```
uv run pytest tests/web/test_api_files_recipes_read.py -v
```
Expected: 404 — the route does not exist.

- [ ] **Step 4: parameterise `require_file`**

In `blueprints/api_files/routes.py`, change the helper to take the folder and
add the recipe accessor beside `cookfile_folder`:

```python
def cookfile_folder():
    return current_app.config["HISTORY_FOLDER"]


def recipe_folder():
    return current_app.config["RECIPE_FOLDER"]


def require_file(name, folder, *, must_exist=True):
    """Resolve a client-supplied bare filename to a contained absolute path.

    `folder` is passed rather than looked up because two archive kinds now
    share this helper, and a default would let a new route silently resolve a
    recipe against the history folder.

    Returns (path, None) on success or (None, response) on failure, so callers
    read as `path, err = require_file(name, folder); if err: return err`.
    """
    if not name:
        return None, error("bad_request", 400, field="file")
    path = resolve_managed_file(folder, name)
    if path is None:
        return None, error("not_found", 404)
    if must_exist and not os.path.isfile(path):
        return None, error("not_found", 404)
    return path, None
```

Then update **every existing call site** to pass `cookfile_folder()`. There are
**thirteen**, all inside `routes.py`, enumerated by `findReferences` on
2026-07-27:

```
:99  _load_cookfile          :209 cookfile_label
:146 cookfile_download       :231 cookfile_recover
:158 cookfile_export         :249 cookfile_comments
:175 cookfile_upload  (must_exist=False)
:184 cookfile_delete         :282 cookfile_comment_assets
:194 cookfile_title          :301 cookfile_asset_upload
                             :313 cookfile_asset_delete
                             :328 cookfile_thumbnail
```

Note `:175` passes `must_exist=False` — it is an upload, naming a file that does
not exist yet. Keep that keyword when you add the folder argument.

**Verify with `findReferences`, not grep**, before and after: grep has
previously found 16 of 41 real references in this repo. A missed call site is a
`TypeError` at request time, which the existing cook-file tests catch — run them
in Step 7.

- [ ] **Step 5: write `recipes_api.py`**

```python
"""Recipe endpoint handlers for /api/files/recipes/*.

Every handler here takes a BARE FILENAME resolved by routes.require_file
against routes.recipe_folder(). None of them ever accepts a path, which is the
single behavioural difference from blueprints/recipes/routes.py.

comments.json is deliberately absent from every payload: no UI in either app has
ever written a recipe comment (tests/web/test_page_recipes.py:33-38). The member
is preserved on every write because update_json_file_data rewrites one member
and copies the rest.
"""

from common.app import api_response, classify_cookfile_error
from file_mgmt.recipes import read_recipefile


def load(path):
    """read_recipefile, with the (struct, status) shape the routes branch on."""
    return read_recipefile(path)


def unreadable(status, error):
    """422 for an archive that exists but will not open."""
    return error("unreadable", 422, errortype=classify_cookfile_error(status))


def detail_payload(struct, filename):
    """Everything the React editor needs, and nothing it does not."""
    return {
        "filename": filename,
        "metadata": struct["metadata"],
        "recipe": struct["recipe"],
        "assets": struct["assets"],
    }
```

- [ ] **Step 6: register the route**

```python
def _load_recipe(name):
    path, err = require_file(name, recipe_folder())
    if err:
        return None, None, err
    struct, status = recipes_api.load(path)
    if status != "OK":
        return None, None, recipes_api.unreadable(status, error)
    return struct, path, None


@api_files_bp.route("/recipes/detail", methods=["GET"])
def recipe_detail():
    name = request.args.get("file", "")
    struct, _path, err = _load_recipe(name)
    if err:
        return err
    return jsonify(recipes_api.detail_payload(struct, name)), 200
```

- [ ] **Step 7: add the traversal tests and run the whole api_files suite**

Mirror `tests/web/test_api_files_cookfile_read.py:140-164` exactly — both the
hostile-string parametrisation and the "a real archive that merely lives outside
the folder" case, which is the one that proves containment rather than hiding a
read error:

```python
@pytest.mark.parametrize(
    "hostile",
    ["../../../etc/passwd", "../secret.pfrecipe", "/etc/passwd", "..%2F..%2Fetc%2Fpasswd", ""],
)
def test_traversal_attempts_are_refused(client, folders, hostile):
    resp = client.get(f"/api/files/recipes/detail?file={hostile}")
    assert resp.status_code in (400, 404)
    assert b"passwd" not in resp.data
    assert b"root:" not in resp.data


def test_a_traversal_to_a_real_recipe_outside_the_folder_is_refused(client, folders, tmp_path):
    _history, recipe_dir = folders
    name = write_recipe(str(tmp_path) + "/", "Outside-Recipe")
    resp = client.get(f"/api/files/recipes/detail?file=../{name}")
    assert resp.status_code == 404
    assert "Outside-Recipe" not in resp.get_data(as_text=True)
```

```
uv run pytest tests/web/test_api_files_recipes_read.py tests/web/test_api_files_cookfile_read.py tests/web/test_api_files_cookfile_write.py tests/web/test_api_files_cookfile_assets.py tests/web/test_api_files_cookfile_comments.py tests/web/test_api_files_listing.py -v
```
Expected: all pass. The cook-file modules are the check on Step 4's refactor.

- [ ] **Step 8: format and commit**

```
.venv/bin/ruff format blueprints/api_files/ tests/web/ file_mgmt/
```

```
jj describe --stdin <<'MSG'
feat(api_files): serve recipe detail over the contained-path surface

require_file takes its folder explicitly now that two archive kinds share it;
a default would let a new route resolve a recipe against the history folder.

comments.json is not published: nothing in either UI has ever written a recipe
comment, so a payload carrying it would invite building from the schema alone.
MSG
```

---

### Task 2: file-level operations, and the socket command injection

**Files:**
- Modify: `blueprints/api_files/routes.py`, `blueprints/api_files/recipes_api.py`
- Modify: `blueprints/mobile/socket_io.py`
- Test: `tests/web/test_api_files_recipes_write.py`, `tests/web/test_socket_recipe_delete_safety.py`

**Interfaces:**
- Produces: `POST /recipes/create`, `POST /recipes/upload`, `GET /recipes/download`,
  `POST /recipes/delete`; `recipes_api.create()`, `recipes_api.save_upload(storage)`.

- [ ] **Step 1: write the failing G7 regression test first**

This is the security fix; it gets its test before anything else. Mirror
`tests/web/test_page_recipes.py:492-531`, which pins the same two attacks against
the already-hardened HTTP handler.

```python
# tests/web/test_socket_recipe_delete_safety.py
"""blueprints/mobile/socket_io.py's recipe_delete ran os.system(f"rm {path}")
on an unsanitized client string. Its HTTP sibling was hardened and tested; this
copy was not. These tests are that fix's net."""

import os

from blueprints.mobile import socket_io


def test_recipe_delete_refuses_a_traversal(monkeypatch, tmp_path, recipe_folder_at):
    outside = tmp_path / "sentinel.pfrecipe"
    outside.write_text("keep me")
    _delete(socket_io, f"../{outside.name}")
    assert outside.exists()


def test_recipe_delete_does_not_execute_a_shell_payload(monkeypatch, tmp_path, recipe_folder_at):
    marker = tmp_path / "pwned"
    _delete(socket_io, f"x.pfrecipe; touch {marker}")
    assert not marker.exists()


def test_recipe_delete_removes_a_real_recipe(recipe_folder_at):
    name = write_recipe(recipe_folder_at, "Deletable")
    _delete(socket_io, name)
    assert not os.path.isfile(os.path.join(recipe_folder_at, name))
```

Write `_delete` and the `recipe_folder_at` fixture to drive
`_post_app_data_recipes` directly, the way `tests/web/` already drives socket
handlers (`flask_app.test_request_context()` with `flask.request.sid` set).

- [ ] **Step 2: run it and watch the injection succeed**

```
uv run pytest tests/web/test_socket_recipe_delete_safety.py -v
```
Expected: `test_recipe_delete_does_not_execute_a_shell_payload` FAILS — `pwned`
exists. **That failure is the proof the bug is real. Record the output in the
task report.**

- [ ] **Step 3: harden the handler**

Replace the `os.system` branch with the same three guards its HTTP sibling uses
(`blueprints/recipes/routes.py:383-393`):

```python
safe_name = secure_filename(filename)
filepath = os.path.join(recipe_folder, safe_name)
if safe_name and os.path.isfile(filepath):
    os.remove(filepath)
```

Keep the handler. `post_app_data` is the mobile app's API and `recipe_start`
sits beside it; deleting the route would break a third-party client for a gain
hardening already delivers.

- [ ] **Step 4: confirm green, then grep**

```
uv run pytest tests/web/test_socket_recipe_delete_safety.py -v
grep -rn "os.system" blueprints/ file_mgmt/
```
Expected: tests pass; the grep returns nothing under `blueprints/mobile/`.

- [ ] **Step 5: the four file-level routes**

```python
@api_files_bp.route("/recipes/create", methods=["POST"])
def recipe_create():
    """Flask's equivalent is `recipeedit` with an empty filename
    (blueprints/recipes/routes.py:136-147). The new file's bare name is
    returned so the client can navigate to it."""
    path = create_recipefile()
    return jsonify(api_response("OK", None, {"filename": os.path.basename(path)})), 200


@api_files_bp.route("/recipes/download", methods=["GET"])
def recipe_download():
    path, err = require_file(request.args.get("file", ""), recipe_folder())
    if err:
        return err
    return send_file(path, as_attachment=True, max_age=0)


@api_files_bp.route("/recipes/upload", methods=["POST"])
def recipe_upload():
    added, problem = recipes_api.save_upload(request.files.get("recipe"))
    if problem:
        return error(problem, 400, field="recipe")
    return jsonify(api_response("OK", None, {"filename": added})), 200


@api_files_bp.route("/recipes/delete", methods=["POST"])
def recipe_delete_file():
    path, err = require_file(json_body().get("file", ""), recipe_folder())
    if err:
        return err
    os.remove(path)
    return jsonify(api_response("OK")), 200
```

`save_upload` must refuse an extension outside `.pfrecipe` and resolve its
destination through `require_file(..., must_exist=False)` — an upload names a
file that does not exist yet, which is exactly why that flag exists.

- [ ] **Step 6: tests, including a `GET` download traversal case**

Cover: create returns a name that then resolves through `detail`; two creates in
the same minute produce different names (G9); upload round-trips; upload of a
`.txt` is 400; delete removes; delete of a traversal is 404 and leaves the
outside file alone; download of a traversal is 404.

- [ ] **Step 7: format, run, commit**

```
uv run pytest tests/web/ -k "recipes or socket_recipe" -v
.venv/bin/ruff format blueprints/ tests/web/
```

```
jj describe --stdin <<'MSG'
fix(mobile): close the recipe_delete command injection, add file operations

socket_io's recipe_delete ran os.system(f"rm {filepath}") on a client-supplied
string with no sanitisation, no containment and no escaping. Its HTTP sibling
was hardened and regression-tested months ago; this copy was missed. The
injection test fails against the old code.

The handler is hardened rather than removed: post_app_data is the mobile app's
API, and recipe_start beside it has no replacement yet.
MSG
```

---

### Task 3: `POST /api/files/recipes/run`, and the unit-conversion crash

**Files:**
- Modify: `blueprints/api_files/routes.py`, `blueprints/api_files/recipes_api.py`
- Modify: `file_mgmt/recipes.py`
- Test: `tests/web/test_api_files_recipes_write.py`, `tests/unit/file_mgmt/test_recipe_units.py`

**Interfaces:**
- Produces: `POST /recipes/run` → 200 `{"filename"}` | 409 `not_stopped` | 404 | 422.

**READ G1 AND G6 BEFORE WRITING A TEST IN THIS TASK.** This route starts a real
cook. Every test here stubs `write_control`; none of them may reach the control
process.

- [ ] **Step 1: the failing units test**

```python
# tests/unit/file_mgmt/test_recipe_units.py
"""convert_recipe_units iterated step["settemps"], a key no step has, so it
raised on every call. controller.py:140 calls it whenever a recipe's saved units
differ from the live setting -- i.e. running such a recipe killed the recipe
loop."""

from file_mgmt.recipes import convert_recipe_units


def _recipe(**over):
    step = {
        "mode": "Hold",
        "hold_temp": 225,
        "timer": 0,
        "notify": False,
        "message": "",
        "pause": False,
        "trigger_temps": {"primary": 0, "food": [203, 0]},
    }
    step.update(over)
    return {"ingredients": [], "instructions": [], "steps": [step]}


def test_converting_f_to_c_converts_every_temperature_field():
    out = convert_recipe_units(_recipe(), "C")
    step = out["steps"][0]
    assert step["hold_temp"] == 107
    assert step["trigger_temps"]["food"][0] == 95


def test_zero_is_unset_and_survives_conversion():
    """0 is the disabled sentinel throughout the step schema. Converting it as a
    temperature would turn every disabled trigger into -17 C and arm it."""
    out = convert_recipe_units(_recipe(), "C")
    assert out["steps"][0]["trigger_temps"]["primary"] == 0
    assert out["steps"][0]["trigger_temps"]["food"][1] == 0
```

- [ ] **Step 2: run it and watch it raise**

```
uv run pytest tests/unit/file_mgmt/test_recipe_units.py -v
```
Expected: `KeyError: 'settemps'` (or `TypeError`). Record the traceback.

- [ ] **Step 3: rewrite the function against the real schema**

The helper is `common/common.py:399` — `convert_temp(units, temp)`, where
`units` is the **target** and the return is an `int`. (Do not reach for
`convert_temp_delta` at `:414`: that one is scale-only, for differences between
two readings. Every value here is an absolute temperature.)

```python
from common.common import convert_temp


def _convert_setpoint(temp, units):
    """0 is the disabled sentinel for hold_temp and for both trigger_temps
    members, so it passes through unconverted: 0 F -> -17 C would arm every
    disabled trigger on the recipe."""
    return 0 if not temp else convert_temp(units, temp)


def convert_recipe_units(recipe, units):
    """Convert every temperature in a recipe's steps to `units`."""
    for step in recipe["steps"]:
        step["hold_temp"] = _convert_setpoint(step["hold_temp"], units)
        triggers = step["trigger_temps"]
        triggers["primary"] = _convert_setpoint(triggers["primary"], units)
        triggers["food"] = [_convert_setpoint(temp, units) for temp in triggers["food"]]
    return recipe
```

- [ ] **Step 4: the run route**

```python
@api_files_bp.route("/recipes/run", methods=["POST"])
def recipe_run():
    """Start a recipe.

    Refuses unless the grill is stopped -- a deliberate divergence from Flask,
    which posts from any mode (static/recipes/js/recipes.js:270-293). It matches
    the guard POST /api/probe_map applies and it is the difference between a
    test suite that can exercise this route and one that cannot.

    start_step and step are sent explicitly because _api_post_control
    deep-merges: a bare {filename} inherits the previous run's step.
    """
    path, err = require_file(json_body().get("file", ""), recipe_folder())
    if err:
        return err
    struct, status = recipes_api.load(path)
    if status != "OK":
        return recipes_api.unreadable(status, error)
    control = read_control()
    if control.get("mode") != Mode.STOP:
        return error("not_stopped", 409, mode=control.get("mode"))
    write_control(
        control_delta(set_values={
            "updated": True,
            "mode": Mode.RECIPE,
            "recipe": {"filename": path, "start_step": 0, "step": 0},
        }),
        WriteKind.DELTA,
        origin="api-files",
    )
    return jsonify(api_response("OK")), 200
```

The server builds the path from the resolved bare filename. **The client never
sends `./recipes/...`** — that is the whole point of G4, and it is what
`recipes.js:274` does wrong today.

- [ ] **Step 5: tests, with the control write stubbed**

```python
def test_run_refuses_unless_stopped(client, folders, monkeypatch):
    writes = []
    monkeypatch.setattr(routes, "write_control", lambda *a, **k: writes.append(a))
    monkeypatch.setattr(routes, "read_control", lambda: {"mode": "Hold"})
    name = write_recipe(folders[1], "Brisket")
    resp = client.post("/api/files/recipes/run", json={"file": name})
    assert resp.status_code == 409
    assert resp.get_json()["message"] == "not_stopped"
    assert writes == []


def test_run_sends_start_step_and_step_explicitly(client, folders, monkeypatch):
    """_api_post_control deep-merges, so a bare {filename} inherits the previous
    run's step and starts mid-recipe."""
    ...
    assert delta["recipe"] == {"filename": ANY, "start_step": 0, "step": 0}
```

Also assert the filename in the delta is an **absolute contained path**, and
that a traversal never reaches `write_control`.

- [ ] **Step 6: run, format, commit**

```
uv run pytest tests/unit/file_mgmt/test_recipe_units.py tests/web/test_api_files_recipes_write.py -v
.venv/bin/ruff format file_mgmt/ blueprints/ tests/
```

```
jj describe --stdin <<'MSG'
fix(recipes): repair convert_recipe_units, and add a REST way to start a recipe

convert_recipe_units iterated step["settemps"], a key the schema has never had,
so it raised on every call -- and controller.py calls it whenever a recipe's
saved units differ from the live setting, taking the recipe loop down with it.
0 is the disabled sentinel and stays 0; converting it would arm every disabled
trigger.

Starting a recipe had no REST route at all, only a Socket.IO event. The new one
refuses unless the grill is stopped, which Flask does not, and sends start_step
and step explicitly because the control write deep-merges.
MSG
```

---

### Task 4: TypeScript types and the recipe API client

**Files:**
- Create: `web-react/src/helpers/files/apiEnvelope.ts`, `recipeTypes.ts`, `recipeApi.ts`
- Create: `web-react/src/helpers/files/recipeApi.test.ts`
- Modify: `web-react/src/helpers/files/cookfileApi.ts`

**Interfaces:**
- Produces: `fetchRecipeDetail`, `createRecipe`, `uploadRecipe`, `deleteRecipe`,
  `runRecipe`, `recipeDownloadUrl`, and the `Recipe*` types.

- [ ] **Step 1: lift the envelope helpers out of `cookfileApi.ts`**

`cookfileApi.ts:118-185` holds `CookFileRequestError`, `toError`, `read`,
`write` and `postForm`, each with `/api/files/cookfiles/` baked into the URL.
Move them to `helpers/files/apiEnvelope.ts` parameterised by `FileKind`, rename
the error class `FileRequestError`, and re-export the old name from
`cookfileApi.ts` so no cook-file call site changes:

```ts
export async function read<T>(kind: FileKind, path: string, file: string, baseUrl: string): Promise<T> {
  const res = await fetch(`${baseUrl}/api/files/${kind}/${path}?file=${encodeURIComponent(file)}`);
  if (res.ok) return (await res.json()) as T;
  throw new FileRequestError(await toError(res));
}
```

Keep `toError`'s comment about a non-JSON 404 from a proxy — it is why the
`.catch(() => ({}))` is there.

- [ ] **Step 2: the types**

```ts
/** metadata.json. `units` is the recipe's OWN units, which may differ from the
 *  live setting -- the control process converts on the way in. */
export interface RecipeMetadata {
  author: string;
  username: string;
  id: string;
  title: string;
  description: string;
  image: string;
  thumbnail: string;
  units: string;
  prep_time: number;
  cook_time: number;
  rating: number;
  difficulty: string;
  version: string;
  food_probes: number;
}

export interface Ingredient {
  name: string;
  quantity: string;
  assets: string[];
}

/** `ingredients` holds ingredient NAMES, not indices, which is why renaming an
 *  ingredient rewrites every instruction that referenced it. */
export interface Instruction {
  text: string;
  ingredients: string[];
  assets: string[];
  step: number;
}

/** `timer` is MINUTES -- controller.py multiplies by 60. `0` means unset for
 *  hold_temp and for both trigger_temps members. */
export interface RecipeStep {
  mode: string;
  hold_temp: number;
  timer: number;
  notify: boolean;
  message: string;
  pause: boolean;
  trigger_temps: { primary: number; food: number[] };
}

export interface RecipeDetail {
  filename: string;
  metadata: RecipeMetadata;
  recipe: { ingredients: Ingredient[]; instructions: Instruction[]; steps: RecipeStep[] };
  assets: RecipeAsset[];
}
```

- [ ] **Step 3: the client**

```ts
export const fetchRecipeDetail = (file: string, baseUrl = BASE_URL) =>
  read<RecipeDetail>("recipes", "detail", file, baseUrl);

export const createRecipe = (baseUrl = BASE_URL) =>
  write<{ filename: string }>("recipes", "create", {}, baseUrl);

export const runRecipe = (file: string, baseUrl = BASE_URL) =>
  write<null>("recipes", "run", { file }, baseUrl);

/** A plain URL, not a fetch: the download is an <a href download>, which is why
 *  the endpoint is GET where Flask's is POST. */
export const recipeDownloadUrl = (file: string, baseUrl = BASE_URL) =>
  `${baseUrl}/api/files/recipes/download?file=${encodeURIComponent(file)}`;
```

- [ ] **Step 4: tests (rstest — `import { describe, expect, it, rs } from "@rstest/core"`)**

Assert the URL each call builds (mirroring `filesApi.test.ts`'s
`(globalThis.fetch as ReturnType<typeof rs.fn>).mock.calls[0][0]` shape), that a
409 surfaces as a `FileRequestError` carrying `status: 409`, and that
`recipeDownloadUrl` percent-encodes a name with a space.

- [ ] **Step 5: gate and commit**

```
cd web-react && bun run typecheck && bun run lint && bun run test
```

---

### Task 5: `/recipes` — the list, the route, the navbar

**Files:**
- Create: `web-react/src/components/recipes/RecipeList.tsx`, `RecipeList.test.tsx`, `recipes.css`
- Modify: `web-react/src/components/App.tsx`, `shell/NavBar.tsx`, `shell/NavBar.test.tsx`, `src/styleCoverage.test.ts`

**Interfaces:**
- Consumes: `fetchFileListing("recipes", ...)`, `createRecipe`, `uploadRecipe`,
  `deleteRecipe`, `recipeDownloadUrl`, `ConfirmAction`.

- [ ] **Step 1: build `RecipeList` on the `CookFileList` template**

`components/cookfiles/CookFileList.tsx` (255 lines) is the pattern: listing
fetch, pager, per-page select from `PER_PAGE_CHOICES`, upload input, delete
through `ConfirmAction`, `FALLBACK_THUMB` for a missing thumbnail. Two
differences, both deliberate:

1. **`reverse: false` by default.** Flask's recipe list opens
   `gotoRFPage(1, false, 10)` (`static/recipes/js/recipes.js:84`) — the opposite
   of the cook-file list. Pass it explicitly; the helper's default is `true`.
2. **A "New Recipe" button** calling `createRecipe()` then navigating to
   `/recipes/{filename}`. Cook files have no equivalent — they are produced by
   cooking.

- [ ] **Step 2: register the routes**

In `components/App.tsx`, inside the same `AppShell` children array that holds
`/history` and `/cookfiles/:filename`:

```tsx
{ path: "/recipes", element: <RecipeList /> },
{ path: "/recipes/:filename", element: <RecipePage /> },
```

- [ ] **Step 3: enable the navbar entry**

`components/shell/NavBar.tsx:18-26` — change one line:

```ts
  { label: "Recipes", to: "/recipes", end: false },
```

- [ ] **Step 4: update `NavBar.test.tsx`**

Two cases must change together. `"renders the three unported destinations as
disabled non-links"` iterates `["Recipes", "Events", "Admin"]` asserting
`tagName !== "A"` and `aria-disabled === "true"`; drop `"Recipes"` from it and
rename it for **two** destinations. Then add `Recipes → /recipes` to the ported
set alongside Dashboard/History/Pellets/Settings.

- [ ] **Step 5: `recipes.css` and the coverage guard**

Open with `@reference "../../theme.css";` and author with `@apply`, matching
`cookfiles.css`:

```css
@reference "../../theme.css";

.pf-rcp-toolbar {
  @apply mb-[12px] flex flex-wrap items-center gap-[12px];
}
```

Reuse the shared `.pf-section`, `.pf-modal-btn`, `.pf-settings-hint`,
`.pf-field*`, `.pf-input` vocabulary rather than redeclaring it. Add
`components/recipes` to `SURFACES` in `src/styleCoverage.test.ts` — the guard
that catches a `pf-*` class with no rule. Note that `components/cookfiles` is
**not** in `SURFACES`; do not copy that omission.

- [ ] **Step 6: gate and commit**

```
cd web-react && bun run typecheck && bun run lint && bun run test
```

---

### Task 6: `RecipePage` + `RecipeView` — the read-only surface

**Files:**
- Create: `web-react/src/components/recipes/RecipePage.tsx`, `RecipeView.tsx`, and both tests

- [ ] **Step 1: `RecipePage` on the `CookFilePage` template**

`components/cookfiles/CookFilePage.tsx` (181 lines) is the shell: `useParams`,
one fetch, loading and error branches, an `onChanged` refetch callback threaded
into every child that writes. Reuse the structure verbatim. The 422 branch has
no recipe counterpart — there is no repair/upgrade path for `.pfrecipe` — so a
422 renders the error and nothing else.

- [ ] **Step 2: `RecipeView`, covering F3 capability 8**

Splash image, author, star rating (1-5), prep/cook time, difficulty badge,
food-probe count, description, an ingredients table with per-ingredient asset
thumbnails, an instructions table (text + ingredients used + program step), and
the program-step list. Read-only: every editor lands in Slice B.

Two rendering rules that come from the schema, not from taste:

- **`0` renders as "—", not "0"**, for `hold_temp` and both `trigger_temps`
  members. It is the disabled sentinel (G8).
- **`timer` is minutes.** Label it so; the raw number is not self-describing.

- [ ] **Step 3: tests and gate**

Assert the disabled-sentinel rendering explicitly — it is the one thing a reader
of the JSON would get wrong.

---

### Task 7: `RecipeRunStatus` — off the socket, not a poll

**Files:**
- Create: `web-react/src/helpers/recipes/runStatus.ts`, `components/recipes/RecipeRunStatus.tsx`, tests

- [ ] **Step 1: read `recipeStatus` from the live payload**

F4: `socket_dash_data` already carries
`{recipeMode, filename, mode, paused, step}`, on every frame **and** directly to
each client on connect. Add the field to whatever type `useLiveState` publishes
and read it. **Do not add a polling endpoint** — Flask polls `reciperunstatus`
every 4 s (`recipes.js:289`) only because it has no socket on that page.

- [ ] **Step 2: the component**

Highlight the active step and scroll it into view (F3 capability 17). Show a
paused indicator from `paused`. When `recipeMode` is true and `filename` differs
from the file being viewed, say which recipe is running rather than highlighting
a step of the wrong one — Flask handles this by ignoring the request's filename
entirely (G9), which a client-side view cannot do.

- [ ] **Step 3: a Run button, wired to Task 3's endpoint**

Disabled unless the grill is stopped, with the 409 `not_stopped` message
surfaced if it races. This is the first destructive action on the page: it goes
through `ConfirmAction`.

---

### Task 8: Slice A gate — e2e specs, fidelity baselines, full suite

**Files:**
- Modify: `web-react/tests/e2e/pageSpecs.ts`, `tests/e2e/apiFixtures.ts`
- Create: `web-react/tests/e2e/baselines/recipe-list-{1280x720,390x844}.json`, `recipe-detail-*.json`

- [ ] **Step 1: `stubRecipes(page)` in `apiFixtures.ts`**

Route `**/api/files/recipes` and `**/api/files/recipes/detail` to a committed
fixture, the way `stubProbeModules` does. The fidelity projects run against the
demo server, which has no backend — a real fetch would render the error branch
and the baseline would encode it.

- [ ] **Step 2: two `PageSpec` entries**

```ts
{
  name: "recipe-list",
  path: "/recipes",
  ready: ".pf-rcp-surface .pf-rcp-table",
  root: ".pf-shell",
  stubs: stubRecipes,
  landmarks: [...SHELL, ".pf-rcp-surface", ".pf-rcp-toolbar", ".pf-rcp-table", ".pf-btn"],
},
```

Write them out rather than generating them, for the same reason the
`settings-probes` spec is written out: they need their own fixture.

- [ ] **Step 3: capture the baselines, scoped**

```
cd web-react && bun run baseline:capture -- -g "recipe-"
```

**Never hand-edit a baseline JSON.** After capturing, verify the diff is **pure
additions** — new files only, no existing baseline modified. If any existing
file changed, stop: the new specs perturbed a shared landmark and the spec is
wrong, not the baseline.

- [ ] **Step 4: the full gate**

```
cd web-react && bun run typecheck && bun run typecheck:e2e && bun run lint && bun run test && bun run test:e2e:fidelity
uv run pytest tests/ -q
```

**Restart gunicorn before trusting any e2e result** — a worker started before a
backend change serves the old code and new endpoints 404, which reads as a
broken frontend. This has cost three separate tasks.

**Do not run `bun run test:e2e`.** See G1.

- [ ] **Step 5: commit and stop for review**

Slice A is independently shippable: browse, view, run. Hand it to review before
starting Slice B.

---

# SLICE B — the editor

Slice A must be reviewed and merged before this begins. Every task here writes
to an archive, so every task here needs the `api_files_folders` fixture and a
traversal test.

**One rule governs this whole slice:** a write endpoint reads the member it is
about to change, mutates it, and calls `update_json_file_data` for that member
only. It never rewrites a member it did not change — that is how `comments.json`
survives a feature that ignores it (ruling 1).

---

### Task 9: metadata writes, and the `food_probes` reshape

**Files:**
- Modify: `blueprints/api_files/routes.py`, `recipes_api.py`
- Test: `tests/web/test_api_files_recipes_write.py`

**Interfaces:**
- Produces: `POST /recipes/metadata` `{file, fields: {...}}` → 200.

- [ ] **Step 1: the failing reshape test**

This is the one piece of metadata that is not a field write. Mirror
`tests/web/test_page_recipes.py:227-255`, which pins Flask's behaviour.

```python
def test_raising_food_probes_pads_every_step(client, folders):
    """food_probes is structural: trigger_temps.food must carry exactly one
    entry per food probe on EVERY step, or the controller's probe_map remap
    (controller.py:156-163) indexes past the end."""
    name = write_recipe(folders[1], "Brisket", food_probes=2, steps=[_step(), _step()])
    resp = client.post("/api/files/recipes/metadata", json={"file": name, "fields": {"food_probes": 4}})
    assert resp.status_code == 200
    detail = client.get(f"/api/files/recipes/detail?file={name}").get_json()
    assert [len(s["trigger_temps"]["food"]) for s in detail["recipe"]["steps"]] == [4, 4]
    assert detail["recipe"]["steps"][0]["trigger_temps"]["food"][2] == 0


def test_lowering_food_probes_truncates_every_step(client, folders):
    ...
    assert [len(s["trigger_temps"]["food"]) for s in detail["recipe"]["steps"]] == [1, 1]
```

- [ ] **Step 2: run it, watch it 404, then implement**

```python
_INT_FIELDS = ("prep_time", "cook_time", "rating", "food_probes")
_STR_FIELDS = ("title", "author", "description", "difficulty", "units")


def set_metadata(path, fields):
    """Apply a whole-metadata patch.

    Flask writes one field per request (routes.py:155-176); this takes a patch
    because the React editor's SaveBar saves a form, not a keystroke. The
    food_probes branch is the only one that touches recipe.json, and it must
    reshape EVERY step -- a step whose food list is short of food_probes makes
    controller.py:156-163 index past the end during the probe-map remap.
    """
    metadata, status = read_json_file_data(path, "metadata")
    if status != "OK":
        return status
    ...
```

Reject an unknown field name with 400 `data.field` naming it, rather than
writing it. Flask accepts anything; a typed client has no reason to.

- [ ] **Step 3: reshape then write, in that order, and only what changed**

If `food_probes` is in the patch, read `recipe`, resize every step's
`trigger_temps.food` (pad with `0`, truncate from the end), and
`update_json_file_data(recipe, path, "recipe")`. Then write `metadata`. If
`food_probes` is absent, **do not touch `recipe.json` at all**.

- [ ] **Step 4: tests for the rest**

Typed coercion (`prep_time`, `cook_time`, `rating`, `food_probes` are ints;
everything else is a string), unknown field → 400, traversal → 404, and that a
metadata write leaves `comments.json` byte-identical.

- [ ] **Step 5: format, run, commit**

---

### Task 10: ingredients — and the cross-member rename cascade

**Interfaces:**
- Produces: `POST /recipes/ingredients` `{file, action: "add"|"update"|"delete", index?, name?, quantity?}`.

- [ ] **Step 1: the failing cascade tests**

`instructions[].ingredients` holds ingredient **names**, so a rename that does
not cascade orphans every instruction that referenced it. Mirror
`test_page_recipes.py:258-293`.

```python
def test_renaming_an_ingredient_rewrites_every_instruction_that_used_it(client, folders):
    name = write_recipe(
        folders[1], "Brisket",
        ingredients=[{"name": "Sugar", "quantity": "1c", "assets": []}],
        instructions=[
            {"text": "Rub", "ingredients": ["Sugar"], "assets": [], "step": 0},
            {"text": "Rest", "ingredients": [], "assets": [], "step": 1},
        ],
    )
    client.post("/api/files/recipes/ingredients", json={
        "file": name, "action": "update", "index": 0, "name": "Brown Sugar", "quantity": "1c",
    })
    detail = client.get(f"/api/files/recipes/detail?file={name}").get_json()
    assert detail["recipe"]["instructions"][0]["ingredients"] == ["Brown Sugar"]
    assert detail["recipe"]["instructions"][1]["ingredients"] == []


def test_deleting_an_ingredient_removes_it_from_every_instruction(client, folders):
    ...
    assert detail["recipe"]["instructions"][0]["ingredients"] == []
    assert detail["recipe"]["ingredients"] == []
```

- [ ] **Step 2: implement, cascading BEFORE the mutation**

On delete, remove the name from every instruction **first**, then `pop(index)` —
after the pop the name is gone and there is nothing to match on. Flask gets this
order right (`routes.py:227-237`); copy the order, not just the effect.

- [ ] **Step 3: an out-of-range index is 400, not an IndexError**

`index` arrives from a client. Validate it against `len(ingredients)` and answer
400 `data.field == "index"`. Add a test.

- [ ] **Step 4: format, run, commit**

---

### Task 11: instructions

**Interfaces:**
- Produces: `POST /recipes/instructions` `{file, action, index?, text?, ingredients?, step?}`.

- [ ] **Step 1: tests**

Add appends `{"text": "", "ingredients": [], "assets": [], "step": 0}`. Update
replaces `text`, `ingredients` (a whole list) and `step` (int). Delete pops.
Nothing cascades — no other member references an instruction.

- [ ] **Step 2: reject an ingredient name that is not in the recipe**

Flask does not check this; the React multi-select can only offer real names, so
a request carrying an unknown one is a bug in something. 400 `data.field ==
"ingredients"`. **This is a new rule — record it in the closeout.**

- [ ] **Step 3: index validation, format, run, commit**

---

### Task 12: program steps — insert at an index

**Interfaces:**
- Produces: `POST /recipes/steps` `{file, action: "insert"|"update"|"delete", index, step?}`.

- [ ] **Step 1: the failing positional-insert test**

```python
def test_a_step_is_inserted_at_the_index_not_appended(client, folders):
    """Flask inserts (routes.py:281). A recipe is an ordered program; appending
    would put a new step after Shutdown."""
    name = write_recipe(folders[1], "Brisket", steps=[_step(mode="Smoke"), _step(mode="Shutdown")])
    client.post("/api/files/recipes/steps", json={"file": name, "action": "insert", "index": 0})
    detail = client.get(f"/api/files/recipes/detail?file={name}").get_json()
    assert [s["mode"] for s in detail["recipe"]["steps"]] == ["Smoke", "Smoke", "Shutdown"]
    assert len(detail["recipe"]["steps"]) == 3


def test_an_inserted_step_gets_one_trigger_temp_per_food_probe(client, folders):
    """Built from metadata.food_probes (routes.py:270-271), not from a
    neighbouring step -- a neighbour may itself be stale."""
    ...
    assert detail["recipe"]["steps"][0]["trigger_temps"]["food"] == [0, 0, 0]
```

- [ ] **Step 2: implement insert / update / delete**

The default new step matches `file_mgmt/recipes.py`'s own defaults:
`{"hold_temp": 0, "message": "", "mode": "Smoke", "notify": False, "pause": False,
"timer": 0, "trigger_temps": {"primary": 0, "food": [0] * food_probes}}`.

- [ ] **Step 3: validate an update's payload**

`mode` against a whitelist, `hold_temp`/`timer`/`trigger_temps.*` as ints,
`notify`/`pause` as bools, `len(trigger_temps.food) == metadata.food_probes`
(400 otherwise — a mismatch here is exactly the corruption Task 9 exists to
prevent). Test each rejection.

- [ ] **Step 4: format, run, commit**

---

### Task 13: assets — whole-list writes, and the splash pairing

**Interfaces:**
- Produces: `POST /recipes/assets/upload` (multipart), `POST /recipes/assets`
  `{file, section, index, assets}`, `POST /recipes/assets/delete` `{file, assets}`.

- [ ] **Step 1: the failing splash test**

```python
def test_selecting_a_splash_asset_sets_both_image_and_thumbnail(client, folders):
    """Flask writes both together (routes.py:412-413) and clears both together
    (:423-424). They are one user-facing choice; splitting them leaves a recipe
    whose card and header disagree."""
    ...
    assert metadata["image"] == asset_name
    assert metadata["thumbnail"] == asset_name


def test_clearing_the_splash_clears_both(client, folders):
    ...
    assert metadata["image"] == ""
    assert metadata["thumbnail"] == ""
```

- [ ] **Step 2: whole-list writes, not per-item toggles**

`POST /recipes/assets` takes the section's **complete** asset list and replaces
it. Flask sends `{action: "add"|"remove", asset_name}` and infers direction
(`routes.py:396-428`); the cook-file comments surface already rejected that
pattern in plan 1 Task 6, because a stale client can send an `add` for something
already present and get `OK` for a no-op. Sections: `splash` (a single name, or
`""`), `ingredients`, `instructions` — each with an `index`.

- [ ] **Step 3: upload through a private staging directory**

Mirror `_recipes_form_uploadassets` (`routes.py:80-109`): stage each upload in
`tempfile.mkdtemp(prefix="pifire-upload-")`, call
`add_asset(path, staging, asset_filename)`, `shutil.rmtree(staging)` in a
`finally`. **No predictable `/tmp/pifire/{id}` path may be created** — 
`test_page_recipes.py:664-708` asserts that, and it is a real finding, not
style.

- [ ] **Step 4: delete through the recipe-aware helper**

`remove_assets(path, assets, filetype="recipefile")`. That branch already scrubs
`metadata.image`, `metadata.thumbnail`, every ingredient's `assets` and every
instruction's `assets`. **Do not write a parallel scrubber** (F2).

- [ ] **Step 5: assert the serving contract still holds**

Port `test_page_recipes.py:711-757`: the bytes served at
`/static/img/tmp/{parent_id}/{asset}` match the archived full-size bytes. It is
the invariant that breaks silently.

- [ ] **Step 6: format, run, commit**

---

### Task 14: `RecipeMetaEditor`

**Files:**
- Create: `web-react/src/components/recipes/RecipeMetaEditor.tsx` + test

- [ ] **Step 1: build it from the settings field widgets**

`TextField` (title, author), a textarea for description, `NumberField` for
`prep_time`/`cook_time`/`food_probes`, `Select` for `difficulty`
(Easy/Intermediate/Hard/Advanced) and `units`, a 1-5 star control for `rating`.
Save through `SaveBar` with the `SaveStatus` shape (F6).

- [ ] **Step 2: `food_probes` warns before it saves**

It is the one destructive metadata field: lowering it truncates a trigger temp
off **every** step, and no undo exists. Route it through `ConfirmAction` naming
how many steps are affected. Refetch the whole detail afterwards — Task 9's
endpoint changed `recipe.json` too.

- [ ] **Step 3: tests and gate**

---

### Task 15: `IngredientsEditor` + `InstructionsEditor`

- [ ] **Step 1: both editors refetch the whole detail after any write**

Task 10's cascade means an ingredient rename silently rewrites instructions. An
editor that patches its own local state after a save will show stale
instructions. **Refetch; do not reconcile locally.** Assert this in the tests —
it is the defect this task exists to avoid.

- [ ] **Step 2: the instruction ingredient picker offers only real names**

Task 11's endpoint rejects an unknown name, so the picker is a multi-select over
the current ingredient list, not free text.

- [ ] **Step 3: delete confirmations**

Deleting an ingredient changes instructions the user is not looking at.
`ConfirmAction` names how many.

- [ ] **Step 4: tests and gate**

---

### Task 16: `StepsEditor` — the largest component

- [ ] **Step 1: the per-step form**

`mode` select (Smoke | Hold in the editor; Startup/Shutdown exist in the
defaults and must render if present but need not be offered), `hold_temp` shown
only for Hold, `trigger_temps.primary` and one field per food probe each with an
enable switch, `timer` (**minutes**), `pause`, `notify`, `message`.

- [ ] **Step 2: the enable switch writes `0`, and `0` renders as disabled**

`0` is the sentinel (G8). An enable switch off writes `0`; a field showing `0`
renders as disabled rather than as a temperature. This is the single most
error-prone detail in the slice.

- [ ] **Step 3: bounds**

Max 600 °F / 300 °C from `metadata.units`
(`templates/recipes/_macro_recipes.html:490-494`). `NumberField` clamps on
**blur**, not change — clamping on change makes a bounded field untypeable.

- [ ] **Step 4: insert, not append**

The add control is per-position ("insert above"), matching Task 12. A single
"Add step" button that appends would contradict the endpoint.

- [ ] **Step 5: tests and gate**

---

### Task 17: `RecipeAssetManager`, e2e, and the full gate

- [ ] **Step 1: the asset manager**

Four sections (`splash`, `ingredients`, `instructions`, plus delete-from-archive),
upload, select/deselect, delete. `MediaPanel.tsx` is the template — grid,
selection, `ConfirmAction` on delete. Whole-list writes (Task 13).

- [ ] **Step 2: an e2e round trip that never starts a cook**

Create → edit metadata → add an ingredient → add an instruction referencing it →
insert a step → delete. **The spec must not POST `/recipes/run`.** The suite is
`workers: 1` against one shared PiFire; entering Recipe mode moves the global
grill mode and flushes history out from under `history.spec.ts`. Assert the Run
button's disabled state and stub at the API boundary if the payload needs
asserting.

- [ ] **Step 3: recapture the two baselines if the detail page moved**

Only if a landmark actually changed. Verify the diff is confined to
`recipe-*.json`.

- [ ] **Step 4: the full gate**

```
cd web-react && bun run typecheck && bun run typecheck:e2e && bun run lint && bun run test && bun run test:e2e:fidelity
uv run pytest tests/ -q
.venv/bin/ruff format .
```

Restart gunicorn first. Do **not** run `bun run test:e2e`.

---

### Task 18: backlog and closeout

The standing rule in `docs/superpowers/react-migration-backlog.md`: a slice is
not done until its deferrals are recorded there, because a plan document is read
once and then never again.

- [ ] **Step 1: mark `recipes` shipped under item 8**

Strike the `- [ ] **recipes**` line, and correct what it says — the listing
endpoint it called missing has existed since plan 1.

- [ ] **Step 2: record every deferral**

- **Recipe comments are deliberately unbuilt** (ruling 1). `comments.json` is
  preserved on every write; no UI reads or writes it; `recipeassetmanager`'s
  `comments` branch in the Flask blueprint stays unreachable.
- **`POST /recipes/run` refuses unless stopped** (G6) — a divergence from Flask,
  which posts from any mode.
- **Instruction writes reject an unknown ingredient name** (Task 11 Step 2) — a
  new rule Flask does not enforce.
- **Asset writes are whole-list** where Flask toggles per item (Task 13 Step 2).
- **Wheel/pinch zoom and per-recipe asset lightbox carousel** (F3 capability 15)
  are not ported — same accepted regression as the cook-file chart.
- **`get_recipefilelist_details` still reads the module constant
  `file_mgmt.recipes.RECIPE_FOLDER`** rather than `current_app.config`, so a
  fixture must patch both. Nothing in this plan depends on it; it is a trap for
  the next person.
- **The Flask `/recipes` page stays live.** No page is retired until the general
  retirement pass (backlog ruling 5).

- [ ] **Step 3: record the two bugs under "Bugs found and fixed this cycle"**

G7 (the socket command injection — note that its HTTP sibling had been hardened
and tested, and this copy was missed, because that is the reusable lesson) and
G8 (`convert_recipe_units`, including that `0` is the disabled sentinel).

- [ ] **Step 4: commit**

---

## Parallelization

Slice A is a chain: Task 1 creates the module every later backend task extends,
and Task 4 depends on the routes existing. Tasks 5-7 could run concurrently
after Task 4, but all three touch `recipes.css`, so they need **isolated jj
workspaces** — disjoint files alone are not enough when two agents share a
stylesheet.

Slice B parallelises properly. Tasks 9-13 (backend) touch different handlers in
`recipes_api.py` and different test modules; Tasks 14-17 (frontend) each own one
component. Run 9-13 concurrently, then 14-17 concurrently, with a barrier
between: every frontend task needs its endpoint to exist.

```sh
jj workspace add ../pifire-recipes-t<N>
# then, in EVERY new workspace that touches web-react:
cp .lsp.json ../pifire-recipes-t<N>/ && (cd ../pifire-recipes-t<N> && bun install)
export PORT=52<N>3 DEMO_PORT=52<N>4
export PIFIRE_BACKEND_URL=http://localhost:51<N>0
export PIFIRE_DB_PATH="$PWD/pifire.db"
```

`.lsp.json` and `node_modules/` are gitignored, so `jj workspace add` skips
them. **`PIFIRE_BACKEND_URL`, never `PUBLIC_PIFIRE_URL`** — rsbuild injects every
`PUBLIC_*` variable into the browser bundle, which turns every same-origin
request into a cross-origin one that skips the dev proxy, and Flask sends no
CORS headers.

---

## Out of scope, deliberately

- **Recipe comments** — ruling 1.
- **Retiring `blueprints/recipes/`** — backlog ruling 5. It stays live and
  `tests/web/test_page_recipes.py` stays its characterization net.
- **The asset lightbox carousel** (F3 #15) — the cook-file lightbox exists and
  can be generalised later; it is not a blocker for editing a recipe.
- **Cross-validating a recipe's steps against the live probe map.** The
  controller remaps `trigger_temps` through `settings["recipe"]["probe_map"]`
  (`controller.py:156-163`); a recipe saved against a different probe map is a
  real failure mode, but diagnosing it is its own piece of work.
- **A migration for recipes saved with mismatched units.** Task 3 fixes the
  conversion; it does not rewrite archives.

---

## Could NOT verify — flagged, not guessed

- **Whether the PiFire mobile app actually calls `recipe_start`/`recipe_delete`.**
  No in-repo caller exists. G7 hardens rather than deletes on that uncertainty;
  if the human confirms the app is dead, deleting both handlers is the better
  end state.
- **Whether `metadata.units` is ever anything but `"F"`/`"C"`.** `_default_recipe_metadata`
  seeds it from `settings["globals"]["units"]`, and `convert_temp` branches on
  `== "F"` with C as the else — so a third value silently converts to Celsius.
  Task 3 does not add a guard; it inherits the existing behaviour.

*Resolved 2026-07-27 after serena's Python language server was restarted:* the
temperature helper is `convert_temp(units, temp)` (`common/common.py:399`), and
`require_file` has exactly thirteen call sites, all in `routes.py` (listed in
Task 1 Step 4).

---

## Self-review checklist — run before declaring the plan done

- [ ] Every endpoint has a task, a test module and a traversal test.
- [ ] Every capability in F3 of the plan-1 document is implemented by a task or
      listed in "Out of scope" with a reason.
- [ ] `grep -rn "os.system" blueprints/mobile/` returns nothing.
- [ ] No new test writes outside `tempfile.mkdtemp`.
- [ ] No route added here accepts a path; no client added here sends one.
- [ ] `jj st` is clean after a full run.
