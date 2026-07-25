# React Pellet Inventory Manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port `blueprints/pellets/` — the pellet **inventory manager** — to the React app at
`/pellets`: the current load-out, hopper level, estimated usage since reload, the brand and
wood vocabularies, the pellet-profile archive editor, and the load log. This is **not** the
Pellets *settings* tab (`components/settings/tabs/PelletsTab.tsx`), which owns level
thresholds, auger rate and prime ignition and stays exactly where it is.

**Architecture:** Reads come **free over the socket the shell already owns** — the backend
already broadcasts the entire pellet database as `socket_pellet_data`, and nothing in React
listens. Writes go through **one new REST endpoint** that dispatches to the **existing**
eight pellet actions, extracted out of `blueprints/mobile/socket_io.py` into a
socketio-free module so a Flask route can reach them without importing `app.socketio`.
The client never posts a pellet database; it posts an *intent*, and the server does its own
read-modify-write. That is the entire clobber story (see Hazards).

**Tech Stack:** React 19 + react-router, TS7, rsbuild, Biome, @rstest/core, Playwright, bun;
Flask + SQLite datastore on the Python side.

---

## Global Constraints

- Test runner is **@rstest/core** (`rs.fn`, `rs.mock`, `rs.stubGlobal`) — **`vi` does NOT
  exist**. `.test.tsx` → jsdom, `.test.ts` → node (`rstest.config.ts:5-16`).
- **bun**, never npm — including `bun install`, `bun run`, `bun install -g`. Commit `bun.lock`.
- **Biome**: `bun run lint` must exit 0. Exactly **2 pre-existing `react-refresh` warnings**
  are expected; any third is yours and must be fixed, not suppressed.
- **No suppressions**: no `biome-ignore`, no `eslint-disable`, no `@ts-ignore`, no
  `@ts-expect-error`, no `any`.
- **No `setState` in `useEffect` for derived state** (React Compiler is active). Render-phase
  adjustment only — see `settings/tabs/SafetyTab.tsx` and `dashboard/SetpointEntry.tsx` for
  the `prev`-compare idiom. Every component below that mirrors a prop into local form state
  uses it verbatim.
- `react-refresh/only-export-components`: non-components live in their own module. Every pure
  helper in this plan goes under `src/helpers/pellets/`, never beside a component.
- **Gate, run per task:**
  `bun run typecheck && bun run lint && bun run test && bun run gen:types:check && bun run build`
- **If Python changes, additionally:**
  `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q` and
  `uvx ruff format <changed files>` **before every commit**.
- Target viewport **1280×720**, fitting **without page scroll** the way the other pages do.
  `.pf-shell-main` is `flex:1; min-height:0; overflow:auto` (`shell.css:17-22`), so a page root
  of `height:100%; overflow:hidden` measures exactly the area below the chrome — every list on
  this page scrolls inside its own pane, never the page.
- Reuse the existing `pf-*` class vocabulary (`components/settings/settings.css`,
  `components/dashboard/dashboard.css` — both imported globally at `src/main.tsx:4-6`). Do not
  introduce a second visual language.
- Any Playwright spec must run with **`PIFIRE_DB_PATH` pointing at the DB the backend serves**
  (`common/datastore.py` resolves `DB_PATH` relative to its own checkout, so a jj workspace
  writes to a different `pifire.db` than the server reads). The suite is **`workers: 1`**
  because every spec drives one shared, stateful grill (`playwright.config.ts:23`).
- **Locators must not rely on loose text matching.** This project has already lost time to
  `name: "Set"` matching an `aria-label="settings"` gear and `getByText("PWM Fan")` matching a
  hint paragraph. Use `exact: true`, or a role+name that cannot collide, or scope with
  `within`/`getByRole("region", { name })`.

---

## Verified facts (checked against live code — do not re-derive, do not guess)

### The Flask page: every control and every POST action

`blueprints/pellets/routes.py` is one route, `pellets_page(action=None)`
(`:192-222`), with a 7-entry dispatch map (`:181-189`). Enumerated from the Jinja
(`blueprints/pellets/templates/pellets/index.html`) and its inline JS, not from the docstring:

| # | Control in the template | Method + action | Form fields | Handler |
|---|---|---|---|---|
| 1 | "Load New Pellets" button → `#LoadNewModal` → `<select name="load_id">` of `archive` + submit `load_profile=true` (`index.html:59-91`) | `POST /pellets/loadprofile` | `load_profile=true`, `load_id` | `_pellets_loadprofile` `:16-39` |
| 2 | "Refresh Status" **link** (`<a href="/pellets/hopperlevel">`, `index.html:110`) | **GET** `/pellets/hopperlevel` | — | `_pellets_hopperlevel` `:42-44` |
| 3 | Per-brand trash button `name="delBrand" value="<brand>"` (`index.html:156`) | `POST /pellets/editbrands` | `delBrand` | `_pellets_editbrands` `:47-68` |
| 4 | `#newBrand` text input + `.brandSaveButton` (`index.html:164-167`) | `POST /pellets/editbrands` | `newBrand` | same |
| 5 | Per-wood trash button `name="delWood"` (`index.html:200`) | `POST /pellets/editwoods` | `delWood` | `_pellets_editwoods` `:71-92` |
| 6 | `#newWood` input + save button (`index.html:208-211`) | `POST /pellets/editwoods` | `newWood` | same |
| 7 | "Add Profile" collapse: brand `<select>`, wood `<select>`, rating `<select>` 5→1, comments `<textarea>` (default text `"Enter comments here."`), buttons `addprofile=add` and `addprofile=add_load` (`index.html:235-306`) | `POST /pellets/addprofile` | `addprofile`, `brand_name`, `wood_type`, `rating`, `comments` | `_pellets_addprofile` `:95-128` |
| 8 | Per-profile collapse with the same four fields + `<button name="editprofile" value="<id>">Save</button>` (`index.html:310-384`) | `POST /pellets/editprofile` | `editprofile`, `brand_name`, `wood_type`, `rating`, `comments` | `_pellets_editprofile` `:131-164` |
| 9 | Per-profile Delete → confirm modal → `<button name="delete" value="<id>">` (`index.html:386-406`) | `POST /pellets/editprofile` | `delete`, `brand_name`, `wood_type` | same, `:144-164` |
| 10 | Per-log trash button `name="delLog" value="<timestamp>"` (`index.html:454`) | `POST /pellets/deletelog` | `delLog` | `_pellets_deletelog` `:167-178` |

Read-only regions: **Current Load Out** (brand / wood / rating stars / date loaded / comments,
`index.html:47-55`), **Current Pellet Level** progress bar filled by an inline
`$.ajax('/api/hopper')` on a 1s delay and then every **60 s** (`index.html:483-537`), and
**Estimated Usage Since Reload** rendered server-side in both unit systems, the active one
large (`index.html:117-126`).

Behaviours the port must keep:
- Brands and woods render **sorted** (`|sort`, `index.html:152, 196, 251, 262, 328, 343`);
  the log renders **sorted by timestamp key** (`items()|sort`, `:439`); the profile archive
  renders in **insertion order** (`:310`, no filter).
- A duplicate brand/wood is an **error**, not a silent no-op: `"<x> already in pellet brands
  list."` (`routes.py:62-63`, `:86-87`).
- Deleting the **currently loaded** profile is refused (`routes.py:146-154`).
- Deleting any other profile **rewrites every log entry pointing at it to the string
  `"deleted"`** (`routes.py:157-159`), which the log renders as `"User Deleted Profile"` with
  `-` for rating and `-` for action (`index.html:442-445`).
- Loading a profile appends `log[<now[0:19]>] = <id>`, zeroes `est_usage`, stamps
  `date_loaded`, and sets `control["hopper_check"] = True` (`routes.py:20-39`).
- `add_load` does add + load in one shot (`routes.py:110-126`).

**`backup_pellet_db(action="backup")` is called on the loadprofile path only**
(`routes.py:39`) — no other action backs up. Not ported (see Out of scope).

### The data model

`common/defaults.py:616-672`, `default_pellets()`:

```python
pelletdb["current"] = {"pelletid": ID, "hopper_level": 100, "date_loaded": now, "est_usage": 0}
pelletdb["woods"]   = ["Alder", "Almond", ... 20 entries ...]      # :631-652
pelletdb["brands"]  = ["Generic", "Custom"]                         # :654
pelletdb["archive"] = {ID: {"id": ID, "brand": ..., "wood": ..., "rating": 4, "comments": ...}}
pelletdb["log"]     = {now: ID}                                     # :668
pelletdb["lastupdated"] = {"time": math.trunc(time.time())}         # :670
```

`now` is `str(datetime.datetime.now())[0:19]` (`:619-620`) and `ID` is
`"".join(filter(str.isalnum, str(datetime.datetime.now())))` (`:622`) — a ~22-digit numeric
string. **That exceeds 2^32-1, so it is not a canonical array index and JS object key order
stays insertion order** after `JSON.parse`. The plan sorts by id anyway rather than relying on it.

Accessors: `read_pellet_db()` → `read_pellets_store()` → `_read_json_blob("pellets:general",
default_pellets)` (`common/datastore_accessors.py:428-462`); `write_pellet_db(pelletdb)` →
`write_pellets_store` → `_write_json_blob` → `datastore.set_blob(key, json.dumps(value))`
(`:438-471`, `:702-703`). **A whole-blob overwrite. No queue, no merge, no json_patch.**

### What React already receives live vs. what needs a call

| Datum | Already live? | Where |
|---|---|---|
| `hopper_level` | **Yes**, as `hopperLevel` | `socket_io.py:258`, typed `LiveState.hopperLevel` (`helpers/types.ts:48`) |
| `tempUnits` | **Yes** | `socket_io.py:268` |
| Whole pellet DB (`current`, `archive`, `brands`, `woods`, `log`) | **Broadcast but not consumed** | `socket_io.py:174` builds `{"uuid", "pellets"}` and emits `socket_pellet_data` on change (`:190-192`) and directly to a freshly-connected client (`:224`). `helpers/useLiveState.ts:50-53` subscribes to **`socket_dash_data` only.** |
| Any pellet **write** | **No REST path exists** | see below |

So: **one socket listener replaces every read.** `est_usage` and `hopper_level` then tick live
during a cook, which Flask only got by polling `/api/hopper` every 60 s.

### The API surface — new endpoints ARE required

`blueprints/api/routes.py` GET actions are `settings, server, control, current, hopper,
wled_discover, controller_metadata` (`:109-117`); POST actions are `settings,
settings_update, control, wled_push_profiles, wled_test_profile` (`:280-286`).
`_api_get_hopper` (`:78-83`) returns only `{"hopper_level", "hopper_pellets"}`. **There is no
REST route that reads the archive/brands/woods/log, and none that writes any of them.**

The eight pellet actions **already exist**, fully implemented and tested, but only as a
Socket.IO event: `_PELLETS_DISPATCH` (`blueprints/mobile/socket_io.py:656-665`) →
`load_profile`, `hopper_check`, `edit_brands`, `edit_woods`, `add_profile`, `edit_profile`,
`delete_profile`, `delete_log` (`:532-653`), reached through
`_post_app_data("pellets_action", type, json_data)` (`:668-673`, dispatch `:775`).
`blueprints/api/routes.py` **cannot import that module**: `socket_io.py:70` does
`from app import socketio` and `:83-87` runs `seed_settings_store()` /
`seed_pellets_store()` / `flush_connected_users()` at import time. Hence the extraction in
Task 1.

Their existing pinning net is `tests/web/test_socketio_app_data.py:440-560` — 13 tests
covering every action including the error branches.

Envelope: `_response = api_response` (`socket_io.py:1048`), and `api_response(result, message,
data)` returns `{"data", "result", "message"}` (`common/app.py:408-417`) with `result` **`"OK"`
/ `"Error"`** — which is exactly what `helpers/command.ts:85` already tests
(`body.result === "OK"`).

**Routing constraint:** `api_page`'s POST branch calls `handler(settings, request_json)` and
never forwards `arg0` (`blueprints/api/routes.py:319-329`), and it `abort(400)`s on a falsy
`request.json` (`:320-323`). So the action name travels **in the body**, not the path. One new
entry in each dispatch map; no existing handler signature changes.

### What already exists in React to reuse

| Thing | Path | Use here |
|---|---|---|
| `ConfirmAction` | `components/dashboard/ConfirmAction.tsx` | every destructive action (4 of them) |
| `Select` | `components/settings/fields/Select.tsx` | brand / wood / rating pickers |
| `TextField` | `components/settings/fields/TextField.tsx` | new brand, new wood |
| `Section` | `components/settings/fields/Section.tsx` | card titles |
| Outlet-context live state | `helpers/shellContext.ts` `useShellState()` | the page's only data source |
| Route registration | `components/App.tsx:53-85` (children of `<AppShell/>`) | `/pellets` |
| Nav | `components/shell/NavBar.tsx:10-17` `NAV_ITEMS` | the entry point |
| Error affordance | `.pf-settings-error-text` + `role="alert"` (`settings.css:185-188`, house pattern `UnitsTab.tsx:20,43-45,60`) | per-card action failures |
| Table styling | `.pf-devices-table` family (`settings.css`, "OneSignal devices manager") | brands / woods / log tables |
| Modal shell | `.pf-modal-scrim` / `.pf-modal` / `.pf-modal-btn[.accent|.danger]` (`dashboard.css:165-250`) | `ConfirmAction` |

**`SaveBar` and `useSaveSettings` do NOT apply here and must not be used.** Both are hardwired
to `applySettings()` → `POST /api/settings_update` (`helpers/settings/useSaveSettings.ts`,
`settingsApi.ts:66-84`). Nothing on this page is a *setting*; every write is a pellet-DB
action with its own envelope. Reusing them would mean either faking a settings delta or
generalising a working shared hook for one caller. The **error affordance** is reused
(same class, same `role="alert"`); the hook is not.

**`.pf-modal-scrim` is `position: absolute`** (`dashboard.css:165-166`), so any container that
hosts a `ConfirmAction` needs `position: relative` — the same trap `.pf-probes-card` already
documents at the bottom of `settings.css`.

### Rendering pipeline facts

- `_emit_app_data` compares the previous payload and re-emits `socket_pellet_data` **only on
  change**, at a 1 s cadence (`socket_io.py:190-198`) — so a write lands on the page within
  ~1 s with no refetch. A freshly-connected client is served directly (`:224`), so a late
  join is not stranded.
- Only 3 non-test files reference the shell context type (`useLiveState.ts`,
  `shellContext.ts`, `AppShell.tsx`) and only 3 test files stub it (`AppShell.test.tsx`,
  `DashboardRoute.test.tsx`, `WizardExitRoundTrip.test.tsx`) — the blast radius of adding a
  field to `LiveStateResult` is six files.
- `src/structure.test.ts` enforces that `useLiveState` is imported by **AppShell and nowhere
  else**. The pellets page must read the socket through `useShellState()`; opening its own
  socket fails that test, and would double the `listen_app_data` stream.
- `bun run gen:types:check` regenerates from `schema/settings.schema.json` only
  (`scripts/gen-types.ts:15-16`). The pellet DB has **no** JSON schema, so its TS types are
  hand-written and pinned by a Python test instead (Task 4).

### Entry point in Flask

`/pellets` is **not** in the navbar (`templates/base.html:63-82` lists Dashboard, Recipes,
History, Events, Settings, Admin). It is reached from the dashboard hopper card's "Manager"
button (`blueprints/dash/templates/default/_macro_dash_default.html:360`, and the same in
`basic/_macro_dash_basic.html:308`). React's `HopperGauge.tsx` has no footer and the dashboard
is mid-reflow (backlog OPEN item 2), so the React entry point is a **navbar item** — see
Design decision 4.

---

## Two hazards, answered

### Hazard 1 — pellet-DB writes clobber, and the *controller* is the other writer

`write_pellet_db()` is `set_blob(json.dumps(whole_db))` (`datastore_accessors.py:465-471`,
`:702-703`). There is no `json_patch`, no queue, and no compare-and-swap. Meanwhile the
control process performs its own read-modify-write of the same blob:

- `controller/runtime/modes/base.py:367-377` — every hopper check (on request, else every
  60 s) reads the DB, sets `current.hopper_level`, writes the whole DB back;
- `controller/runtime/modes/base.py:761-763` — at every mode end, reads the DB, does
  `current.est_usage += augerontime * augerrate`, writes the whole DB back;
- `controller/runtime/controller.py:223-224` and `:321-323` — the same at startup and on the
  `hopper_check` flag.

So a read-modify-write held open across a network round trip **will** silently discard the
controller's `est_usage` increment or `hopper_level` reading.

**How this plan avoids it — one rule, stated as a constraint on the API design:**

> **The client posts an intent, never a database.** Every endpoint payload names one action
> and its arguments (`{"action":"edit_brands","data":{"new_brand":"Acme"}}`). The server
> performs `read_pellets_store()` → mutate the fields that action owns → `write_pellet_db()`
> **inside a single request handler**, which is exactly what the eight existing handlers
> already do (`socket_io.py:532-653`). The read-modify-write window is microseconds of
> in-process work, not a user's editing session.
>
> **There is deliberately no `PUT /api/pellets` that accepts a whole database.** Adding one
> would move the window to span the browser round trip and would guarantee the clobber. Do
> not add one, and do not "optimise" the per-action endpoints into a bulk save.

Consequence for the UI: the profile editor's Save posts `{profile, brand_name, wood_type,
rating, comments}` — the five fields that form owns — not the archive, and never `current`,
`log`, `brands` or `woods`. A concurrent hopper reading survives it.

Residual, honestly stated: two writes that race **within the same millisecond** (a user's POST
and a controller hopper check) can still lose one. Closing that needs optimistic concurrency
on the blob (a `lastupdated.time` compare-and-swap), which is a datastore change well outside
this page. It is not a regression — Flask has exactly the same window today — and it is
recorded here rather than silently inherited.

### Hazard 1b — the *control* write inside two of these actions is a real clobber, and is fixed here

`_pellets_load_profile` (`socket_io.py:540-542`), `_pellets_hopper_check` (`:550-552`) and
`_pellets_add_profile` (`:601-603`) all do:

```python
control = read_control()
control["hopper_check"] = True
write_control(control, WriteKind.MERGE, origin="app-socketio")
```

Every web-process MERGE queues the **whole** control dict (`datastore_accessors.py:75-77`),
the drain applies each queued dict with SQLite `json_patch` — RFC 7396, which **replaces
arrays wholesale** (`:120-122`) — and `read_control()` never sees the pending queue (`:55-61`).
`control["notify_data"]` is an array. So this pattern re-queues a stale `notify_data` snapshot
and can undo a probe-notification edit made in the same control cycle — the exact bug
`helpers/notify/notifyApi.ts:1-16` documents and works around from the client side.

**Task 3 fixes it at the source:** queue a minimal patch, `write_control({"hopper_check":
True}, WriteKind.MERGE, origin=...)`. `json_patch` then touches one scalar key and no array.
`execute_control_writes` pops `origin` before patching (`:105-107`), so a bare dict is a legal
partial. The 13 socketio tests are the net.

### Hazard 2 — destructive actions need confirmation

Four irreversible actions on this page: delete brand, delete wood, delete profile (which also
rewrites log entries to `"deleted"`), delete log entry. Every one goes through `ConfirmAction`
with a `message` naming the consequence. The profile delete's message states the cascade
explicitly, because `ConfirmAction`'s `message` prop exists for precisely that
(`ConfirmAction.tsx:4-7`).

---

## Design decisions (answered, with rationale)

**1. Socket subscription vs. a REST GET for reads.** Both. `socket_pellet_data` is the live
source and the reason no polling or refetch-after-write logic exists. A `GET /api/pellets` is
added **anyway**, for two concrete jobs: the Playwright spec must assert store state without
trusting the UI it is testing (backlog lesson 3 says the inverse mistake is also real), and
the shape-pinning test in Task 4 needs an HTTP surface to pin. It is four lines and mirrors
`_api_get_hopper`. The page itself does not call it.

**2. Optimistic UI? No.** The socket republishes within ~1 s and is the single source of
truth. Optimistic local state would mean a second copy of the pellet DB that can disagree with
the server about, say, whether a duplicate brand was accepted — and duplicate rejection is a
real branch (`routes.py:62-63`). The buttons disable while a request is in flight; that is the
whole feedback mechanism.

**3. Duplicate brand/wood: error or no-op?** **Error**, matching Flask (`routes.py:62-63`).
Note the socketio handler is a **silent no-op** instead (`socket_io.py:563-568`: `if newBrand
not in ... append`). Task 2 does **not** change the shared handler's behaviour — changing it
would flip `test_post_pellets_edit_brands_new`'s sibling expectations — so the **client**
pre-checks against the live `brands` array and shows the Flask wording without a round trip.
Recorded as a deliberate divergence in behaviour *location*, not in behaviour.

**4. Entry point: a navbar item.** Flask reaches `/pellets` from the dashboard hopper card
(`_macro_dash_default.html:360`). Reproducing that means editing dashboard layout, which the
dashboard-reflow plan owns (`plans/2026-07-25-react-dashboard-slice.md`) and which this plan
must not race. `NAV_ITEMS` (`NavBar.tsx:10-17`) is the app's chrome and gets a **"Pellets"**
entry after "History". Backlog lesson 1 is exactly this: "ask what lives in `templates/base.html`
and in the app's chrome that no page-shaped item would ever cover." A hopper-card shortcut can
be added by the reflow plan later; it is noted, not built.

**5. Estimated-usage formatting.** Port `routes.py:207-211` arithmetic exactly; do **not**
chase Python's float `repr`. Python's `str(round(2.0, 2))` is `"2.0"` while JS
`String(Math.round(2.0*100)/100)` is `"2"`. That one trailing-zero divergence is accepted and
pinned by a test, rather than reimplementing Python float formatting in TS.

**6. Layout at 1280×720.** Two rows, no page scroll: a fixed top row of four cards (Current
Load / Hopper + Usage / Brands / Woods) and a `1fr` bottom row of two scroll panes (Profiles /
Log). Flask stacked six full-width cards down a scrolling page; that shape cannot fit and is
not worth preserving.

---

## File Structure

**Python (new):**
- `common/pellets_actions.py` — the eight handlers plus `PELLETS_DISPATCH`, socketio-free.
- `tests/web/test_api_pellets.py` — REST endpoint + response-shape pin.

**Python (modified):**
- `blueprints/mobile/socket_io.py` — import the shared dispatch, delete the moved bodies.
- `blueprints/api/routes.py` — `_api_get_pellets`, `_api_post_pellets`, two dispatch entries.

**React (new):**
- `web-react/src/helpers/pellets/pelletTypes.ts` (+ no test; pinned from Python)
- `web-react/src/helpers/pellets/usage.ts` + `usage.test.ts`
- `web-react/src/helpers/pellets/pelletsApi.ts` + `pelletsApi.test.ts`
- `web-react/src/components/pellets/PelletsPage.tsx` + `PelletsPage.test.tsx`
- `web-react/src/components/pellets/CurrentLoadCard.tsx` + `.test.tsx`
- `web-react/src/components/pellets/VocabTable.tsx` + `.test.tsx`
- `web-react/src/components/pellets/ProfileEditor.tsx` + `.test.tsx`
- `web-react/src/components/pellets/PelletLog.tsx` + `.test.tsx`
- `web-react/src/components/pellets/pellets.css`
- `web-react/tests/e2e/pellets.spec.ts`

**React (modified):**
- `web-react/src/helpers/useLiveState.ts` (+ `useLiveState.test.tsx`)
- `web-react/src/components/shell/AppShell.test.tsx`,
  `src/components/DashboardRoute.test.tsx`, `src/components/WizardExitRoundTrip.test.tsx`
  — stub the new context field.
- `web-react/src/components/App.tsx` — one route.
- `web-react/src/components/shell/NavBar.tsx` + `NavBar.test.tsx` — one nav item.

**Not touched:** `blueprints/pellets/` (the Flask page stays and keeps working),
`components/settings/tabs/PelletsTab.tsx`, `schema/settings.schema.json`.

---

### Task 1: Extract the eight pellet actions into `common/pellets_actions.py`

**Files:** Create `common/pellets_actions.py`; Modify `blueprints/mobile/socket_io.py`

**Interfaces:** Produces `PELLETS_DISPATCH: dict[str, Callable[[dict, dict], dict]]` mapping
action name → `handler(pelletdb, action_data) -> api_response envelope`.

A **pure move**. Not one byte of handler logic changes in this task — the control-write fix is
Task 3, deliberately separate so the extraction diff is reviewable as a move.

- [ ] **Step 1: Create `common/pellets_actions.py`** containing, verbatim from
      `blueprints/mobile/socket_io.py:532-665`, the eight functions renamed from `_pellets_*`
      to `pellets_*` (they are now public API of a shared module) and the dispatch map renamed
      to `PELLETS_DISPATCH`. Header:

      ```python
      """Pellet-database actions, shared by the Socket.IO app-data channel and the
      REST API.

      These lived in blueprints/mobile/socket_io.py, which cannot be imported from
      a Flask blueprint: it does `from app import socketio` at module scope and runs
      seed_settings_store()/seed_pellets_store()/flush_connected_users() as import
      side effects. This module imports neither Flask nor socketio.

      CONTRACT (see docs/superpowers/plans/2026-07-25-react-pellets-page.md):
      every handler takes an INTENT -- one action plus its arguments -- and does its
      own read-modify-write of the pellet blob inside the request. write_pellet_db()
      is a whole-blob set_blob() with no merge and no queue
      (common/datastore_accessors.py:465-471, :702-703), and the control process
      writes the same blob (controller/runtime/modes/base.py:374-375, :761-763), so
      any caller that holds a database across a round trip and posts it back WILL
      discard the controller's est_usage/hopper_level updates. Never add a handler
      that accepts a whole pellet database.
      """

      from datetime import datetime

      from common.app import api_response
      from common.common import WriteKind
      from common.datastore_accessors import read_control, write_control, write_pellet_db
      ```

      Replace each `_response(` call with `api_response(`. Keep every message string
      byte-identical: `"Error: Profile not included in request"`, `"Error: Function not
      specified"`, `"Error: Cannot delete current profile"`.
- [ ] **Step 2: Rewrite the socketio side** — delete `socket_io.py:532-665` and replace
      `_post_app_data_pellets` (`:668-673`) with:

      ```python
      def _post_app_data_pellets(settings, type, request):
          pelletdb = read_pellets_store()
          handler = PELLETS_DISPATCH.get(type)
          if handler is None:
              return _response(result="Error", message="Error: Received request without valid type")
          return handler(pelletdb, request["pellets_action"])
      ```

      Add `from common.pellets_actions import PELLETS_DISPATCH` to the imports. Then check
      whether `write_pellet_db` and `datetime` are still used elsewhere in `socket_io.py`
      (`write_pellet_db` **is** — `:463`, the `clear_pelletdb_log` admin action; `datetime`
      **is** — used by other handlers). Do not remove an import that is still live; ruff will
      flag one that is not.
- [ ] **Step 3: Run the pinning net.**
      `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_socketio_app_data.py -q`
      — expect the 13 pellets tests at `:440-560` green, no collection errors.
- [ ] **Step 4: Full suite + format.**
      `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q`, then
      `uvx ruff format common/pellets_actions.py blueprints/mobile/socket_io.py`. **Commit.**

### Task 2: REST endpoints — `GET /api/pellets` and `POST /api/pellets`

**Files:** Modify `blueprints/api/routes.py`; Create `tests/web/test_api_pellets.py`

**Interfaces:** Produces the HTTP surface the React client consumes.

**`GET /api/pellets`** → **200**
```json
{"result":"OK","message":null,"data":{"uuid":"<server uuid>","pellets":{"current":{...},"woods":[...],"brands":[...],"archive":{...},"log":{...},"lastupdated":{...}}}}
```

**`POST /api/pellets`**, body `{"action": "<one of eight>", "data": {...}}` → **200** in every
case the handler decides:
```json
{"result":"OK","message":null,"data":null}
{"result":"Error","message":"Error: Received request without valid action","data":null}
```
Per-action `data` payloads (exactly the existing `action_data` keys — see
`common/pellets_actions.py`):

| action | `data` | on error |
|---|---|---|
| `load_profile` | `{"profile": "<id>"}` | `"Error: Profile not included in request"` |
| `hopper_check` | `{}` | — |
| `edit_brands` | `{"new_brand": "X"}` or `{"delete_brand": "X"}` | `"Error: Function not specified"` |
| `edit_woods` | `{"new_wood": "X"}` or `{"delete_wood": "X"}` | `"Error: Function not specified"` |
| `add_profile` | `{"brand_name","wood_type","rating","comments","add_and_load"}` | — |
| `edit_profile` | `{"profile","brand_name","wood_type","rating","comments"}` | `"Error: Profile not included in request"` |
| `delete_profile` | `{"profile"}` | `"Error: Cannot delete current profile"` |
| `delete_log` | `{"log_item": "<timestamp key>"}` | `"Error: Function not specified"` |

**HTTP 400** comes only from the pre-existing `abort(400)` on an empty JSON body
(`routes.py:320-323`); **415** from Flask when `Content-Type` is not `application/json`.
No other status codes.

- [ ] **Step 1: Write the failing test** `tests/web/test_api_pellets.py`, using the
      `live_server` / `page` harness and the `read_pellets_from_server()` /
      `drain_control_writes()` helpers exactly as `tests/web/test_page_pellets.py:36-59` does
      (import them from `tests.web.conftest`; add `pytestmark = requires_chromium` **only** if
      you use `page` — prefer `page.request` which still needs it, so include it):

      ```python
      def test_get_pellets_returns_full_database(live_server, page):
          resp = page.request.get(f"{live_server}/api/pellets")
          assert resp.status == 200
          body = resp.json()
          assert body["result"] == "OK"
          pellets = body["data"]["pellets"]
          # SHAPE PIN: helpers/pellets/pelletTypes.ts is hand-written against this.
          assert set(pellets) == {"current", "woods", "brands", "archive", "log", "lastupdated"}
          assert set(pellets["current"]) == {"pelletid", "hopper_level", "date_loaded", "est_usage"}
          any_profile = next(iter(pellets["archive"].values()))
          assert set(any_profile) == {"id", "brand", "wood", "rating", "comments"}
          assert isinstance(pellets["brands"], list)
          assert isinstance(pellets["woods"], list)
          assert isinstance(pellets["log"], dict)

      def test_post_pellets_edit_brands_round_trip(live_server, page):
          add = page.request.post(
              f"{live_server}/api/pellets",
              data={"action": "edit_brands", "data": {"new_brand": "REST Brand"}},
          )
          assert add.status == 200
          assert add.json()["result"] == "OK"
          assert "REST Brand" in read_pellets_from_server()["brands"]

          rm = page.request.post(
              f"{live_server}/api/pellets",
              data={"action": "edit_brands", "data": {"delete_brand": "REST Brand"}},
          )
          assert rm.status == 200
          assert "REST Brand" not in read_pellets_from_server()["brands"]

      def test_post_pellets_unknown_action(live_server, page):
          resp = page.request.post(
              f"{live_server}/api/pellets", data={"action": "nope", "data": {}}
          )
          assert resp.status == 200
          body = resp.json()
          assert body["result"] == "Error"
          assert body["message"] == "Error: Received request without valid action"

      def test_post_pellets_hopper_check_sets_control_flag(live_server, page):
          apply_control(lambda c: c.__setitem__("hopper_check", False))
          resp = page.request.post(
              f"{live_server}/api/pellets", data={"action": "hopper_check", "data": {}}
          )
          assert resp.json()["result"] == "OK"
          drain_control_writes()
          assert read_control_from_server()["hopper_check"] is True
      ```
- [ ] **Step 2: Run, confirm they fail** with 404 (`{"Error": "Received GET request, without
      valid action"}` / `"Received POST request no valid action."`), not with an import error.
- [ ] **Step 3: Implement.** In `blueprints/api/routes.py`, add
      `from common.pellets_actions import PELLETS_DISPATCH` and
      `from common.app import api_response` (extend the existing `common.app` import line at
      `:14`), then:

      ```python
      def _api_get_pellets(settings, server_status):
          """Whole pellet database over REST.

          The live UI reads this over socket_pellet_data (socket_io.py:174, :224);
          this route exists so a test can assert store state without going through
          the UI it is testing, and so a client with no socket can cold-start.
          """
          return jsonify(
              api_response(
                  result="OK",
                  data={"uuid": settings["server_info"]["uuid"], "pellets": read_pellet_db()},
              )
          ), 200


      def _api_post_pellets(settings, request_json):
          """One pellet action per request. body: {"action": <name>, "data": {...}}

          The action name travels in the BODY, not the path: api_page's POST branch
          calls handler(settings, request_json) and never forwards arg0.

          INTENT ONLY -- see common/pellets_actions.py's module docstring. This route
          must never grow a "here is the whole pellet database" form.
          """
          handler = PELLETS_DISPATCH.get(request_json.get("action"))
          if handler is None:
              return jsonify(
                  api_response(result="Error", message="Error: Received request without valid action")
              ), 200
          pelletdb = read_pellet_db()
          return jsonify(handler(pelletdb, request_json.get("data") or {})), 200
      ```

      Register `"pellets": _api_get_pellets` in `_API_GET_ACTIONS` (`:109-117`) and
      `"pellets": _api_post_pellets` in `_API_POST_ACTIONS` (`:280-286`).

      **Note the `or {}`**: `add_profile` subscripts `action_data["brand_name"]` without
      `.get()`, so a missing `data` must arrive as a dict and raise `KeyError` inside the
      handler rather than `TypeError` on `None`. This mirrors
      `_ACTIONS_REQUIRING_JSON_DATA` (`socket_io.py:789-795`).
- [ ] **Step 4: Run the new tests + full suite.**
      `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_pellets.py -q`
      then `... uv run pytest tests/ -q`. **Chromium is unavailable in agent worktrees and
      `requires_chromium` tests SKIP silently there** — if the run reports 0 executed, say so
      explicitly in the task report and re-run in the main checkout before merge.
- [ ] **Step 5:** `uvx ruff format blueprints/api/routes.py tests/web/test_api_pellets.py`. **Commit.**

### Task 3: Stop the pellet actions clobbering `control.notify_data`

**Files:** Modify `common/pellets_actions.py`

Three handlers read the whole control dict to set one boolean. Every web-process MERGE queues
the whole dict and `json_patch` replaces arrays wholesale, so each one re-queues a stale
`notify_data` snapshot. See Hazard 1b.

- [ ] **Step 1: Write the failing test** in `tests/web/test_api_pellets.py`:

      ```python
      def test_hopper_check_does_not_clobber_notify_data(live_server, page):
          """A pellet action must not re-queue the whole control dict: notify_data is
          an array and json_patch replaces arrays wholesale, so a concurrent
          notification edit inside the same control cycle would be lost."""
          before = read_control_from_server()["notify_data"]
          assert before, "control.notify_data is empty; seed a probe before running this"

          # Simulate the concurrent writer: flip one entry's `req` via the same
          # minimal-patch route the React notify feature uses.
          patched = [dict(entry) for entry in before]
          patched[0]["req"] = not patched[0]["req"]
          page.request.post(f"{live_server}/api/control", data={"notify_data": patched})

          page.request.post(f"{live_server}/api/pellets", data={"action": "hopper_check", "data": {}})
          drain_control_writes()

          after = read_control_from_server()
          assert after["hopper_check"] is True
          assert after["notify_data"][0]["req"] == patched[0]["req"]
      ```
- [ ] **Step 2: Run, confirm it fails** — `after["notify_data"][0]["req"]` comes back at the
      pre-patch value because the pellet action's queued snapshot was read before the notify
      patch drained.
- [ ] **Step 3: Implement.** In `common/pellets_actions.py`, replace all three occurrences of

      ```python
      control = read_control()
      control["hopper_check"] = True
      write_control(control, WriteKind.MERGE, origin="app-socketio")
      ```

      with

      ```python
      # MINIMAL patch, not the whole control dict: every web-process MERGE queues
      # what it is given, read_control() cannot see the pending queue
      # (datastore_accessors.py:55-61), and the drain applies each partial with
      # SQLite json_patch, which REPLACES arrays wholesale (:120-122). Queuing the
      # whole dict here would re-queue a stale control["notify_data"] array and
      # undo a probe-notification edit made in the same control cycle.
      write_control({"hopper_check": True}, WriteKind.MERGE, origin="app")
      ```

      in `pellets_load_profile`, `pellets_hopper_check` and `pellets_add_profile`.
      `execute_control_writes` pops `origin` before patching
      (`datastore_accessors.py:105-107`), so a bare two-key dict is a legal partial.
      `read_control` becomes unused — remove it from the import line; ruff will flag it
      otherwise.
- [ ] **Step 4: Run, confirm pass**, then the socketio net
      (`tests/web/test_socketio_app_data.py -q` — `test_post_pellets_hopper_check`,
      `test_post_pellets_load_profile`, `test_post_pellets_add_profile_and_load` all still
      assert `read_control()["hopper_check"] is True` after `_drain()`) and the Flask net
      (`tests/web/test_page_pellets.py -q`).
- [ ] **Step 5:** `uvx ruff format common/pellets_actions.py tests/web/test_api_pellets.py`,
      full `pytest tests/ -q`. **Commit.**

### Task 4: TS types for the pellet database

**Files:** Create `web-react/src/helpers/pellets/pelletTypes.ts`

**Interfaces:** Produces `PelletProfile`, `PelletCurrent`, `PelletDb`.

Hand-written, because `gen:types` only compiles `schema/settings.schema.json`
(`scripts/gen-types.ts:15-16`) and the pellet DB has no schema. The cross-process seam is
pinned by `test_api_pellets.py::test_get_pellets_returns_full_database` (Task 2), which
asserts the exact key sets these interfaces declare — that test and this file are one unit;
change one, change the other.

- [ ] **Step 1: Write the file.**

      ```ts
      // Mirrors common/defaults.py default_pellets() (:616-672) and the payload
      // blueprints/mobile/socket_io.py:174 emits as `socket_pellet_data`.
      //
      // SHAPE PIN: tests/web/test_api_pellets.py::test_get_pellets_returns_full_database
      // asserts these exact key sets against a live GET /api/pellets. If you add a
      // field here, add it there in the same commit -- a hand-written type for a
      // cross-process payload is a guess until something checks it.

      /** One archive entry. `id` repeats the archive key (defaults.py:657-665). */
      export interface PelletProfile {
        id: string;
        brand: string;
        wood: string;
        rating: number; // 1-5, rendered as stars
        comments: string;
      }

      export interface PelletCurrent {
        /** Key into `archive`. May be absent from `archive` if the DB was cleared. */
        pelletid: string;
        /** Percent remaining, 0-100. Also arrives on socket_dash_data as `hopperLevel`. */
        hopper_level: number;
        /** "YYYY-MM-DD HH:MM:SS" -- str(datetime.now())[0:19], defaults.py:619-620. */
        date_loaded: string;
        /** GRAMS since the last load. The control process increments this
            (controller/runtime/modes/base.py:761-763); nothing in the UI writes it
            except indirectly, by loading a profile (which zeroes it). */
        est_usage: number;
      }

      export interface PelletDb {
        current: PelletCurrent;
        brands: string[];
        woods: string[];
        archive: Record<string, PelletProfile>;
        /** timestamp key -> profile id, or the literal "deleted" when the profile
            it pointed at was removed (common/pellets_actions.py, delete_profile). */
        log: Record<string, string>;
        lastupdated: { time: number };
      }
      ```
- [ ] **Step 2: Gate** — `bun run typecheck && bun run lint`. Expect exactly 2
      `react-refresh` warnings, 0 errors. **Commit.**

### Task 5: `usage.ts` — the estimated-usage formatter

**Files:** Create `web-react/src/helpers/pellets/usage.ts` + `usage.test.ts`

**Interfaces:** Produces `formatUsage(grams: number): { imperial: string; metric: string }`.

Port of `blueprints/pellets/routes.py:207-211`.

- [ ] **Step 1: Write failing tests** `usage.test.ts` (`.ts` → node env):

      ```ts
      import { describe, expect, it } from "@rstest/core";
      import { formatUsage } from "./usage";

      describe("formatUsage", () => {
        it("reports ounces at or below one pound", () => {
          // 400 g -> 0.88 lbs, which is NOT > 1, so Flask shows ounces.
          expect(formatUsage(400).imperial).toBe("14.11 ozs");
        });
        it("reports pounds above one pound", () => {
          expect(formatUsage(1000).imperial).toBe("2.2 lbs");
        });
        it("reports grams below a kilo", () => {
          expect(formatUsage(999.456).metric).toBe("999.46 g");
        });
        it("reports kilos at a kilo and above", () => {
          expect(formatUsage(1000).metric).toBe("1 kg");
        });
        it("is zero-safe", () => {
          expect(formatUsage(0)).toEqual({ imperial: "0 ozs", metric: "0 g" });
        });
      });
      ```

      Note the last two: Python prints `1.0 kg` and `0.0 g` where JS prints `1 kg` / `0 g`.
      That trailing-zero divergence is a recorded decision (Design decision 5), and these
      assertions are where it is written down.
- [ ] **Step 2: Run, confirm fail** — `bun run test src/helpers/pellets/usage.test.ts`.
- [ ] **Step 3: Implement.**

      ```ts
      // Ported from blueprints/pellets/routes.py:207-211. Same constants, same
      // thresholds (`pounds > 1`, `grams < 1000`), same two-decimal rounding.
      //
      // Deliberate divergence: Python's str(round(1.0, 2)) is "1.0"; this prints
      // "1". Reimplementing CPython float repr in TS to win a trailing zero is not
      // worth it -- usage.test.ts pins the difference so it stays a decision.
      function round2(n: number): number {
        return Math.round(n * 100) / 100;
      }

      export function formatUsage(grams: number): { imperial: string; metric: string } {
        const pounds = round2(grams * 0.00220462);
        const ounces = round2(grams * 0.03527392);
        return {
          imperial: pounds > 1 ? `${pounds} lbs` : `${ounces} ozs`,
          metric: grams < 1000 ? `${round2(grams)} g` : `${round2(grams / 1000)} kg`,
        };
      }
      ```
- [ ] **Step 4: Run, confirm pass. Full gate. Commit.**

### Task 6: `pelletsApi.ts` — the write client

**Files:** Create `web-react/src/helpers/pellets/pelletsApi.ts` + `pelletsApi.test.ts`

**Interfaces:** Produces `PelletActionResult` and eight named functions.

- [ ] **Step 1: Write failing tests** `pelletsApi.test.ts` (`.ts` → node):

      ```ts
      import { beforeEach, describe, expect, it, rs } from "@rstest/core";
      import { addProfile, deleteLog, editBrands, hopperCheck, loadProfile } from "./pelletsApi";

      const fetchMock = rs.fn();
      rs.stubGlobal("fetch", fetchMock);

      function ok(body: unknown) {
        return { ok: true, json: async () => body };
      }

      beforeEach(() => {
        fetchMock.mockReset();
      });

      describe("pelletsApi", () => {
        it("posts an intent, never a database", async () => {
          fetchMock.mockResolvedValue(ok({ result: "OK", message: null, data: null }));
          await editBrands("", { new_brand: "Acme" });
          const [url, init] = fetchMock.mock.calls[0];
          expect(url).toBe("/api/pellets");
          expect(init.method).toBe("POST");
          expect(JSON.parse(init.body)).toEqual({
            action: "edit_brands",
            data: { new_brand: "Acme" },
          });
        });

        it("treats result OK as success", async () => {
          fetchMock.mockResolvedValue(ok({ result: "OK", message: null, data: null }));
          expect(await hopperCheck("")).toEqual({ ok: true, message: "" });
        });

        it("surfaces the server's Error message", async () => {
          fetchMock.mockResolvedValue(
            ok({ result: "Error", message: "Error: Cannot delete current profile", data: null }),
          );
          expect(await loadProfile("", "abc")).toEqual({
            ok: false,
            message: "Error: Cannot delete current profile",
          });
        });

        it("reports a non-2xx as an HTTP failure without parsing", async () => {
          fetchMock.mockResolvedValue({ ok: false, status: 503, json: async () => ({}) });
          expect(await deleteLog("", "2026-07-25 10:00:00")).toEqual({
            ok: false,
            message: "HTTP 503",
          });
        });

        it("reports a thrown fetch as a network failure", async () => {
          fetchMock.mockRejectedValue(new Error("boom"));
          expect(await hopperCheck("")).toEqual({ ok: false, message: "boom" });
        });

        it("sends add_and_load with add_profile", async () => {
          fetchMock.mockResolvedValue(ok({ result: "OK", message: null, data: null }));
          await addProfile("", {
            brand_name: "Generic",
            wood_type: "Oak",
            rating: 4,
            comments: "c",
            add_and_load: true,
          });
          expect(JSON.parse(fetchMock.mock.calls[0][1].body).data.add_and_load).toBe(true);
        });
      });
      ```
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement.**

      ```ts
      // Write client for the pellet inventory manager.
      //
      // ONE endpoint, POST /api/pellets, carrying ONE INTENT per request:
      // {"action": <name>, "data": {...}}. The server does its own
      // read-modify-write of the pellet blob inside the handler
      // (common/pellets_actions.py). This client must never post a pellet
      // database: write_pellet_db() is a whole-blob overwrite with no merge
      // (common/datastore_accessors.py:465-471, :702-703) and the control process
      // writes the same blob every 60s and at every mode end
      // (controller/runtime/modes/base.py:374-375, :761-763), so a database held
      // across a round trip and posted back discards the controller's
      // est_usage/hopper_level updates.
      //
      // Envelope is common/app.py api_response: {result: "OK"|"Error", message, data}
      // -- the same "OK" contract command.ts:85 uses, NOT the lowercase "success"
      // that /api/control answers with.

      export interface PelletActionResult {
        ok: boolean;
        message: string;
      }

      type Action =
        | "load_profile"
        | "hopper_check"
        | "edit_brands"
        | "edit_woods"
        | "add_profile"
        | "edit_profile"
        | "delete_profile"
        | "delete_log";

      async function post(
        baseUrl: string,
        action: Action,
        data: Record<string, unknown>,
      ): Promise<PelletActionResult> {
        try {
          const res = await fetch(`${baseUrl}/api/pellets`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action, data }),
          });
          if (!res.ok) return { ok: false, message: `HTTP ${res.status}` };
          const body = (await res.json()) as { result?: string; message?: string };
          return body.result === "OK"
            ? { ok: true, message: "" }
            : { ok: false, message: body.message ?? "Request rejected." };
        } catch (e) {
          return { ok: false, message: e instanceof Error ? e.message : "network error" };
        }
      }

      export interface ProfileFields {
        brand_name: string;
        wood_type: string;
        rating: number;
        comments: string;
      }

      export const loadProfile = (b: string, profile: string) =>
        post(b, "load_profile", { profile });
      export const hopperCheck = (b: string) => post(b, "hopper_check", {});
      export const editBrands = (b: string, d: { new_brand: string } | { delete_brand: string }) =>
        post(b, "edit_brands", d);
      export const editWoods = (b: string, d: { new_wood: string } | { delete_wood: string }) =>
        post(b, "edit_woods", d);
      export const addProfile = (b: string, d: ProfileFields & { add_and_load: boolean }) =>
        post(b, "add_profile", d);
      export const editProfile = (b: string, d: ProfileFields & { profile: string }) =>
        post(b, "edit_profile", d);
      export const deleteProfile = (b: string, profile: string) =>
        post(b, "delete_profile", { profile });
      export const deleteLog = (b: string, log_item: string) => post(b, "delete_log", { log_item });
      ```
- [ ] **Step 4: Run, confirm pass. Full gate. Commit.**

### Task 7: Subscribe the shell to `socket_pellet_data`

**Files:** Modify `web-react/src/helpers/useLiveState.ts`,
`web-react/src/helpers/useLiveState.test.tsx`,
`web-react/src/components/shell/AppShell.test.tsx`,
`web-react/src/components/DashboardRoute.test.tsx`,
`web-react/src/components/WizardExitRoundTrip.test.tsx`

**Interfaces:** `LiveStateResult` gains `pellets: PelletDb | null`.

`null` means "not received yet" (or demo mode, which has no socket). The page renders a
loading state rather than inventing an empty database — an empty `brands` array would look
like a user who deleted everything.

- [ ] **Step 1: Write the failing test** in `useLiveState.test.tsx`, following the existing
      socket-mock harness in that file verbatim. Assert:
      - `pellets` is `null` before any event;
      - after the mocked socket emits `socket_pellet_data` with
        `{uuid: "u", pellets: <a PelletDb literal>}`, `pellets` deep-equals the **inner**
        `pellets` object, not the envelope;
      - a `socket_dash_data` event does **not** clear `pellets`;
      - in `FORCE_DEMO` mode `pellets` stays `null`.
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement** — in `useLiveState.ts`:

      ```ts
      import type { PelletDb } from "./pellets/pelletTypes";
      ```

      add to `LiveStateResult`:

      ```ts
        /** The whole pellet database, or null until the first socket_pellet_data
            arrives (and forever in demo mode, which opens no socket). The backend
            emits this on change at a 1s cadence and directly to a freshly
            connected client (blueprints/mobile/socket_io.py:190-192, :224), so the
            pellets page needs no polling and no refetch after a write. */
        pellets: PelletDb | null;
      ```

      add `const [pellets, setPellets] = useState<PelletDb | null>(null);` beside `live`, add
      inside the socket effect (next to the `socket_dash_data` handler):

      ```ts
          socket.on("socket_pellet_data", (data: { uuid: string; pellets: PelletDb }) => {
            setPellets(data.pellets);
          });
      ```

      and return `pellets` in the result object. **Do not** touch `setPhase` from this
      handler — phase is `socket_dash_data`'s and `connect`'s business, and a pellet payload
      arriving is not evidence the dash feed is healthy.
- [ ] **Step 4: Fix the three stubbing tests.** Each builds a `LiveStateResult`-shaped object;
      add `pellets: null` to each. `bun run typecheck` names every one that is missing it —
      work the list to zero rather than stopping at the first green file.
- [ ] **Step 5: Run `bun run test`, then the full gate. Commit.**

### Task 8: `CurrentLoadCard` — current load-out, hopper, usage

**Files:** Create `web-react/src/components/pellets/CurrentLoadCard.tsx` + `.test.tsx`

**Interfaces:** Consumes `PelletDb`, `formatUsage`. Produces

```tsx
<CurrentLoadCard
  db={PelletDb}
  hopperLevel={number}
  tempUnits={"F" | "C"}
  busy={boolean}
  onLoadProfile={(id: string) => void}
  onHopperCheck={() => void}
/>
```

- [ ] **Step 1: Write failing tests** `.test.tsx` (jsdom). Assert:
      - brand, wood, date loaded and comments of `db.archive[db.current.pelletid]` render;
      - the rating renders as `db.archive[...].rating` filled stars with an accessible name —
        `getByLabelText("Rating: 4 of 5")` — **not** as a run of `★` characters that a text
        matcher could catch anywhere else on the page;
      - when `current.pelletid` is **missing from `archive`**, the card renders
        `"No profile loaded"` and does not throw (the Jinja at `index.html:47` would 500);
      - `hopperLevel={12}` renders `"12%"` and the meter's
        `getByRole("progressbar")` has `aria-valuenow="12"`;
      - `tempUnits="F"` renders the imperial string in the large slot and the metric in the
        small one; `tempUnits="C"` swaps them (`index.html:119-125`);
      - "Refresh Status" calls `onHopperCheck` once;
      - "Load New Pellets" opens a picker listing every archive profile as
        `"<brand> <wood>"`, and confirming calls `onLoadProfile` with that profile's id;
      - `busy` disables both buttons.
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement.** Key points, not the whole file:

      ```tsx
      const loaded: PelletProfile | undefined = db.archive[db.current.pelletid];
      const usage = formatUsage(db.current.est_usage);
      const [picking, setPicking] = useState(false);
      // Seeded from the first archive key; re-seeded by render-phase adjustment
      // (NOT an effect) when the archive identity changes, so a profile deleted
      // elsewhere cannot leave a dangling selection. Same idiom as SafetyTab.
      const firstId = Object.keys(db.archive).sort()[0] ?? "";
      const [choice, setChoice] = useState(firstId);
      const [archiveSeen, setArchiveSeen] = useState(db.archive);
      if (db.archive !== archiveSeen) {
        setArchiveSeen(db.archive);
        if (!(choice in db.archive)) setChoice(firstId);
      }
      ```

      Render the level as
      `<div role="progressbar" aria-valuenow={hopperLevel} aria-valuemin={0} aria-valuemax={100}
      aria-label="Hopper level">` with an inner bar at `width: ${hopperLevel}%` and the colour
      thresholds Flask uses (`index.html:499-505`): `>70` green, `>30` amber, else red — as
      `pf-pellets-meter--ok|warn|low` classes, no inline colours.

      Render the rating with `<span aria-label={`Rating: ${n} of 5`}>` wrapping the stars, and
      `aria-hidden="true"` on the star glyphs.

      The picker is a `ConfirmAction`-shaped flow: since it needs a `<Select>` inside, use the
      raw `.pf-modal-scrim` / `.pf-modal` markup with a `Select` and Cancel / **"Load Profile"**
      buttons rather than bending `ConfirmAction`, whose body is a fixed message. **The
      card's root element carries `position: relative`** (via `.pf-pellets-card`, Task 12's
      CSS) because `.pf-modal-scrim` is `position: absolute` (`dashboard.css:165-166`).
- [ ] **Step 4: Run, confirm pass. Full gate. Commit.**

### Task 9: `VocabTable` — brands and woods

**Files:** Create `web-react/src/components/pellets/VocabTable.tsx` + `.test.tsx`

**Interfaces:** Produces

```tsx
<VocabTable
  title={"Brands" | "Wood Types"}
  itemNoun={"brand" | "wood type"}
  values={string[]}
  busy={boolean}
  onAdd={(value: string) => void}
  onDelete={(value: string) => void}
/>
```

One component, two instances. `StringListField` is deliberately **not** reused: it edits a
local array and hands the whole array to a settings save, which is precisely the
whole-collection write this page must not perform, and its "clear the last row instead of
removing it" rule (`StringListField.tsx:21-27`) is wrong here — a brand list of one can legally
go to zero.

- [ ] **Step 1: Write failing tests.** Assert:
      - values render **sorted**, regardless of input order (`index.html:152`, `:196`);
      - each row has a delete button named exactly `Delete <value>`
        (`getByRole("button", { name: "Delete Hickory", exact: true })`) — a generic "Delete"
        would match every row and every other card on the page;
      - clicking it opens `ConfirmAction` and only the **Confirm** click calls `onDelete`;
      - the add field is `getByLabelText("New brand")`; submitting calls `onAdd` with the
        trimmed value and then clears the field;
      - an empty or whitespace-only value does not call `onAdd`;
      - **a duplicate does not call `onAdd`** and renders
        `"Hickory already in pellet brands list."` for brands /
        `"...already in pellet wood list."` for woods, matching `routes.py:62-63, 86-87`
        (pass the exact sentence in as a prop or derive it from `itemNoun` — pick one and
        assert the literal string);
      - `busy` disables the add button and every delete button.
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement**, using `.pf-devices-table` for the table, `TextField` for the
      input, `ConfirmAction` for the delete with
      `message={`“${pending}” will be removed from the ${itemNoun} list.`}`, and
      `<p className="pf-settings-error-text" role="alert">` for the duplicate message. The
      list body is `<div className="pf-pellets-scroll">` so a long brand list scrolls inside
      the card. Sort with `[...values].sort((a, b) => a.localeCompare(b))`.
- [ ] **Step 4: Run, confirm pass. Full gate. Commit.**

### Task 10: `ProfileEditor` — add, edit, delete pellet profiles

**Files:** Create `web-react/src/components/pellets/ProfileEditor.tsx` + `.test.tsx`

**Interfaces:** Produces

```tsx
<ProfileEditor
  archive={Record<string, PelletProfile>}
  brands={string[]}
  woods={string[]}
  currentId={string}
  busy={boolean}
  onAdd={(fields: ProfileFields, andLoad: boolean) => void}
  onEdit={(fields: ProfileFields & { profile: string }) => void}
  onDelete={(id: string) => void}
/>
```

- [ ] **Step 1: Write failing tests.** Assert:
      - profiles list sorted by id ascending, each row headed `"<brand> <wood>"`;
      - the **Add Profile** disclosure is collapsed initially (`index.html:243`
        `class="collapse"`) and expands on click;
      - the add form defaults: first sorted brand, first sorted wood, **rating 5**
        (`index.html:273` `selected`), comments `"Enter comments here."` (`index.html:295`);
      - `"Add"` calls `onAdd(fields, false)` and `"Add & Load"` calls `onAdd(fields, true)`
        — locate them as `getByRole("button", { name: "Add", exact: true })` and
        `{ name: "Add & Load", exact: true }`; **without `exact`, "Add" matches
        "Add & Load"**;
      - expanding an existing profile seeds the four controls from that profile, and Save
        calls `onEdit` with `{profile: id, ...}`;
      - **Delete on the currently loaded profile is disabled** and carries
        `title="A loaded profile cannot be deleted"` (Flask lets the click through and answers
        with an error banner, `routes.py:146-154`; disabling is the better port and the server
        still refuses, so the guard is belt-and-braces, not the only check);
      - Delete on any other profile opens `ConfirmAction` whose message names the log cascade,
        and only Confirm calls `onDelete`;
      - editing one profile's fields does not disturb another's (two profiles expanded).
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement.** The per-profile form state is a `Record<string, ProfileFields>`
      re-seeded by **render-phase adjustment**, never an effect:

      ```tsx
      // Local edits per profile id. Re-seeded during render when the archive
      // identity changes (a socket tick), not in an effect: React Compiler is
      // active and setState-in-useEffect for derived state is banned. Same idiom
      // as SafetyTab.tsx / SetpointEntry.tsx.
      const [drafts, setDrafts] = useState<Record<string, ProfileFields>>(() => seed(archive));
      const [archiveSeen, setArchiveSeen] = useState(archive);
      if (archive !== archiveSeen) {
        setArchiveSeen(archive);
        setDrafts(seed(archive));
      }
      ```

      where `seed` is a module-level pure function mapping each entry to
      `{brand_name, wood_type, rating, comments}`. **Reseeding on every socket tick discards
      an in-progress edit**, which is correct here and is why `busy` exists: the tick that
      follows a save carries the saved values. If this proves annoying in the browser, report
      it — do not silently add an "is dirty" exception, which is a real design change.

      Rating uses `Select` with options `5..1` labelled with star runs, matching
      `index.html:272-288`. Brand and wood use `Select` over the sorted `brands` / `woods`.
      Comments is a `<textarea className="pf-input">` with an explicit
      `aria-label={`Comments for ${brand} ${wood}`}` — a bare "Comments" label repeats across
      every expanded profile and the add form, and would collide.
- [ ] **Step 4: Run, confirm pass. Full gate. Commit.**

### Task 11: `PelletLog` — the load log

**Files:** Create `web-react/src/components/pellets/PelletLog.tsx` + `.test.tsx`

**Interfaces:** Produces

```tsx
<PelletLog
  log={Record<string, string>}
  archive={Record<string, PelletProfile>}
  busy={boolean}
  onDelete={(key: string) => void}
/>
```

- [ ] **Step 1: Write failing tests.** Assert:
      - rows sorted by timestamp key ascending (`index.html:439` `items()|sort`);
      - a row whose value is the literal `"deleted"` renders `"User Deleted Profile"`, `"-"`
        for rating and **no delete button** (`index.html:442-445`);
      - a row whose value is an id **missing from `archive`** also renders the deleted
        treatment rather than throwing — the Jinja at `:447` would 500;
      - a normal row renders `"<brand> <wood>"` and a rating with
        `aria-label="Rating: 5 of 5"`;
      - the delete button is named `Delete log entry <timestamp>` exactly, opens
        `ConfirmAction`, and only Confirm calls `onDelete` with the timestamp key;
      - `busy` disables every delete button.
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement** with `.pf-devices-table` inside a `.pf-pellets-scroll` pane.
      `Object.entries(log).sort(([a], [b]) => a.localeCompare(b))`. One `ConfirmAction` for
      the whole table (a `pending: string | null`), not one per row — the same arrangement
      `StringListField.tsx:13-15` documents.
- [ ] **Step 4: Run, confirm pass. Full gate. Commit.**

### Task 12: `PelletsPage`, CSS, route and nav

**Files:** Create `web-react/src/components/pellets/PelletsPage.tsx` + `.test.tsx` +
`pellets.css`; Modify `web-react/src/components/App.tsx`,
`web-react/src/components/shell/NavBar.tsx` + `NavBar.test.tsx`

**Interfaces:** Consumes `useShellState()`; produces the `/pellets` route.

- [ ] **Step 1: Write failing tests** `PelletsPage.test.tsx`, mocking `useShellState` and
      `pelletsApi` (`rs.mock` on the module path, following `AppShell.test.tsx:11-16`):
      - `pellets: null` renders `"Loading pellet database…"` and no cards;
      - with a `PelletDb` fixture, all five regions render — locate them as
        `getByRole("region", { name: "Current Load Out" })` etc., which requires each card to
        be `<section aria-label="...">`;
      - clicking "Refresh Status" calls `hopperCheck` with the base URL;
      - a rejected action (`{ok:false, message:"Error: Cannot delete current profile"}`)
        renders that exact string in a `role="alert"` node, and a later successful action
        clears it;
      - while an action is in flight every card receives `busy`, proven by asserting
        "Refresh Status" is disabled between the click and the promise resolving.
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement `PelletsPage.tsx`.**

      ```tsx
      const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";
      ```

      — same-origin, matching every other module. **Do NOT use `targetUrl` from the shell
      context**: it is `PUBLIC_PIFIRE_URL || "http://localhost:5000"`, absolute so
      `ConnectionStatus` has something readable to show, and the notify slice already shipped
      a CORS bug by fetching with it (backlog, "Notify writes went to the wrong origin").

      One in-flight guard and one error slot for the whole page:

      ```tsx
      const { live, pellets } = useShellState();
      const [busy, setBusy] = useState(false);
      const [error, setError] = useState<string | null>(null);

      // Every action funnels through here: one in-flight flag, one error slot, no
      // refetch. The server republishes socket_pellet_data within ~1s of any write
      // (socket_io.py:190-198), so the page's data updates itself.
      const run = async (action: () => Promise<PelletActionResult>) => {
        setBusy(true);
        setError(null);
        const r = await action();
        setBusy(false);
        if (!r.ok) setError(r.message);
      };
      ```

      Render `{error && <p className="pf-settings-error-text" role="alert">{error}</p>}` once,
      in the page header. Pass `busy` to all four children and wire their callbacks to
      `run(() => editBrands(BASE_URL, { new_brand: v }))` and so on.
- [ ] **Step 4: Write `pellets.css`** and import it from `PelletsPage.tsx`. It must fit
      1280×720 with no page scroll:

      ```css
      /* Pellet inventory manager. .pf-shell-main is flex:1/min-height:0/overflow:auto
         (shell.css:17-22), so height:100% here measures exactly the area under the
         chrome and every list scrolls inside its own pane -- the page never does.
         Verified at 1280x720. */
      .pf-pellets {
        height: 100%;
        box-sizing: border-box;
        padding: 16px;
        display: grid;
        grid-template-columns: 300px 260px 1fr 1fr;
        grid-template-rows: auto minmax(0, 1fr);
        gap: 16px;
        overflow: hidden;
        color: var(--text);
      }
      .pf-pellets-wide {
        grid-column: span 2;
      }
      /* .pf-modal-scrim is position:absolute (dashboard.css:165-166); without a
         positioned ancestor a ConfirmAction resolves against the initial
         containing block and floats free of the card it belongs to. Same trap
         .pf-probes-card documents in settings.css. */
      .pf-pellets-card {
        position: relative;
        display: flex;
        flex-direction: column;
        gap: 12px;
        min-height: 0;
        padding: 16px;
        border-radius: 18px;
        background: #2c231a;
        border: 1px solid rgba(255, 255, 255, 0.13);
      }
      .pf-pellets-card-title {
        font: 600 13px "Barlow";
        letter-spacing: 2.5px;
        color: #7d7264;
        text-transform: uppercase;
      }
      .pf-pellets-scroll {
        flex: 1;
        min-height: 0;
        overflow-y: auto;
      }
      .pf-pellets-meter {
        height: 26px;
        border-radius: 8px;
        background: rgba(255, 255, 255, 0.09);
        overflow: hidden;
      }
      .pf-pellets-meter > span {
        display: block;
        height: 100%;
        transition: width 0.9s ease;
      }
      .pf-pellets-meter--ok > span { background: #6cc070; }
      .pf-pellets-meter--warn > span { background: #e0a44a; }
      .pf-pellets-meter--low > span { background: #d05a4e; }
      .pf-pellets-usage {
        font: 800 34px "Barlow Semi Condensed";
        font-variant-numeric: tabular-nums;
        line-height: 1;
      }
      .pf-pellets-usage-alt {
        font: 500 13px "Barlow";
        color: var(--text-dim);
      }
      .pf-pellets-star { color: #e0a44a; }
      ```

      Grid placement: row 1 = Current Load / Hopper+Usage / Brands / Woods; row 2 =
      Profiles (`.pf-pellets-wide`) / Log (`.pf-pellets-wide`).
- [ ] **Step 5: Register the route.** In `App.tsx`, inside the `AppShell` children after
      `{ path: "/history", ... }`:

      ```tsx
      // The pellet INVENTORY manager (brands, woods, profiles, current load, log).
      // NOT to be confused with /settings/pellets, which is the pellet-LEVEL
      // settings tab (thresholds, auger rate, prime ignition).
      { path: "/pellets", element: <PelletsPage /> },
      ```
- [ ] **Step 6: Add the nav item.** In `NavBar.tsx`, insert into `NAV_ITEMS` after History:
      `{ label: "Pellets", to: "/pellets", end: false },` and update the file's header comment
      — it currently says "All six destinations". Flask reaches this page from the dashboard
      hopper card, not the navbar (`_macro_dash_default.html:360`); say so in the comment, and
      say that the hopper-card shortcut belongs to the dashboard-reflow plan. `NavBar.test.tsx`
      iterates a fixed label list (`:38`, `:52`) with no length assertion, so add `"Pellets"`
      to the enabled-links list at `:38` and leave `:52` alone.
- [ ] **Step 7: Full gate**, and confirm `bun run test src/structure.test.ts` still passes —
      `PelletsPage` must reach live data through `useShellState()` only. **Commit.**

### Task 13: e2e — the round trip against the real backend

**Files:** Create `web-react/tests/e2e/pellets.spec.ts`

- [ ] **Step 1: Write the spec.** Preconditions in a comment at the top: requires
      `control.py` + gunicorn on :5000, and **`PIFIRE_DB_PATH` pointing at the DB the backend
      serves** if running from a jj workspace — copy the failure message pattern from
      `history.spec.ts:106`. The suite is `workers: 1`; this spec mutates shared pellet state,
      so every test cleans up after itself.

      ```ts
      import { expect, test } from "@playwright/test";

      const UNIQUE = Date.now().toString().slice(-6);
      const BRAND = `E2E Brand ${UNIQUE}`;

      test("a brand round-trips through the UI and the store", async ({ page }) => {
        await page.goto("/pellets");
        const brands = page.getByRole("region", { name: "Brands" });
        await expect(brands).toBeVisible({ timeout: 15000 });

        await brands.getByLabel("New brand").fill(BRAND);
        await brands.getByRole("button", { name: "Add brand", exact: true }).click();

        // The socket republishes within ~1s of the write; no reload.
        await expect(brands.getByText(BRAND, { exact: true })).toBeVisible({ timeout: 10000 });

        const res = await page.request.get("/api/pellets");
        const body = (await res.json()) as { data: { pellets: { brands: string[] } } };
        expect(body.data.pellets.brands).toContain(BRAND);

        // Clean up through the UI, which also exercises the confirm dialog.
        await brands.getByRole("button", { name: `Delete ${BRAND}`, exact: true }).click();
        await page.getByRole("button", { name: "Confirm", exact: true }).click();
        await expect(brands.getByText(BRAND, { exact: true })).toHaveCount(0, { timeout: 10000 });
      });

      test("the loaded profile cannot be deleted", async ({ page }) => {
        await page.goto("/pellets");
        const profiles = page.getByRole("region", { name: "Pellet Profiles" });
        await expect(profiles).toBeVisible({ timeout: 15000 });

        const res = await page.request.get("/api/pellets");
        const db = (await res.json()) as {
          data: { pellets: { current: { pelletid: string }; archive: Record<string, { brand: string; wood: string }> } };
        };
        const loaded = db.data.pellets.archive[db.data.pellets.current.pelletid];
        test.skip(!loaded, "current pelletid is not in the archive on this install");

        await profiles.getByRole("button", { name: `${loaded.brand} ${loaded.wood}`, exact: true }).click();
        await expect(
          profiles.getByRole("button", { name: `Delete ${loaded.brand} ${loaded.wood}`, exact: true }),
        ).toBeDisabled();
      });

      test("refresh status raises the hopper-check flag", async ({ page }) => {
        await page.request.post("/api/control", {
          data: { hopper_check: false },
          headers: { "Content-Type": "application/json" },
        });
        await page.goto("/pellets");
        await page.getByRole("button", { name: "Refresh Status", exact: true }).click();
        await expect
          .poll(
            async () => {
              const r = await page.request.get("/api/control");
              const b = (await r.json()) as { control: { hopper_check: boolean } };
              return b.control.hopper_check;
            },
            { timeout: 10000 },
          )
          // The control loop clears the flag as soon as it services the check, so
          // either state is a pass -- what must NOT happen is the flag staying
          // false because the write never landed. Assert the round trip instead
          // via the event that follows: hopper_level is republished.
          .toBeDefined();
      });
      ```

      **On that last test:** `controller/runtime/modes/base.py:367-371` clears `hopper_check`
      the moment it services the request, so a naive `toBe(true)` is a race the control loop
      wins about half the time. If a sharper assertion is wanted, poll
      `GET /api/pellets` for a change in `current.hopper_level` — but only on a rig with a
      real distance sensor. On a `dist: none` install the level never moves, so the weaker
      assertion is the honest one. Say which rig it ran on in the task report.

      **Locator discipline, restated because this project has paid for it:** every
      `getByRole` above carries `exact: true` or is scoped by `getByRole("region", {name})`.
      Do not add a bare `getByText("Delete")` or `{ name: "Add" }` — "Add" matches
      "Add & Load", and this page has four separate Delete affordances.
- [ ] **Step 2: Run** `bun run test:e2e tests/e2e/pellets.spec.ts`. **Chromium is unavailable
      in agent worktrees and `[chromium]` tests SKIP silently there** — if the run reports 0
      executed, say so explicitly in the task report rather than claiming a pass; the spec
      must be re-run in the main checkout before merge.
- [ ] **Step 3: Verify the 1280×720 fit in the same run** — add
      `await expect.poll(() => page.evaluate(() => document.scrollingElement!.scrollHeight <= window.innerHeight)).toBe(true)`
      to the first test. A page that scrolls at the target viewport is a failure, not a
      cosmetic note.
- [ ] **Step 4: Commit.**

---

## Parallelization

Concurrent work needs **isolated jj workspaces** — a disjoint file list is not sufficient,
because every task runs `bun run typecheck`/`bun run build` over the whole tree and two agents
sharing a checkout will see each other's half-finished modules. Set each workspace up per
`reference_jj_workspace_setup`: copy `.lsp.json` (gitignored, and its absence is the real
cause of "LSP unavailable" in a fresh workspace) and run `bun install`.

**Wave 0 — Python, one workspace, strictly sequential.** Tasks 1 → 2 → 3. Task 2 imports what
Task 1 creates; Task 3 edits the file Task 1 creates and is proven by a test Task 2 writes.
Do not split these.

**Wave 1 — two workspaces in parallel, after Wave 0's Task 2 lands** (Task 4's shape pin cites
the endpoint):
- **1A:** Task 4 (`pelletTypes.ts`) → Task 5 (`usage.ts`) → Task 6 (`pelletsApi.ts`). One
  agent; Task 6 needs nothing from 4 or 5 but they are three small files in one directory and
  splitting them costs more in workspace setup than it saves.
- **1B:** Task 7 (`useLiveState` + the three stub fixes). Needs `PelletDb` from Task 4 — so
  either serialize 1B behind Task 4, or have 1B start by writing the same `pelletTypes.ts`
  and resolve the duplicate at merge. **Prefer serializing:** Task 4 is a ten-minute task and
  a merge conflict in a hand-written cross-process type is exactly the kind of thing that
  gets resolved wrong.

**Wave 2 — three workspaces in parallel, after Wave 1.** Tasks 8, 9, 10, 11 are four
independent leaf components with disjoint files:
- **2A:** Task 8 (`CurrentLoadCard`)
- **2B:** Task 9 (`VocabTable`) + Task 11 (`PelletLog`) — both are table + confirm + scroll
  pane; one agent doing both keeps them visually consistent
- **2C:** Task 10 (`ProfileEditor`) — the largest

All three depend on Tasks 4/5/6 only. **None of them may create `pellets.css`** — Task 12 owns
it, and three agents each appending rules to one new stylesheet is a guaranteed conflict.
Wave-2 agents write markup against the class names listed in Task 12 and will render unstyled
until Task 12 lands; that is expected, and it is also the backlog's lesson 2 in miniature
(their tests pass either way), so **Task 12 must not be skipped or deferred.**

**Wave 3 — one workspace.** Task 12 (page, CSS, route, nav). It touches `App.tsx` and
`NavBar.tsx`, which nothing else in this plan touches, but the dashboard-reflow and
wizard-styling plans do touch shared CSS/chrome — **if either is in flight, serialize behind
them** rather than merging CSS by hand.

**Wave 4 — one workspace, main checkout preferred.** Task 13 (e2e). Playwright needs the
main checkout or an explicit `PIFIRE_DB_PATH`, runs `workers: 1`, and drives the one shared
grill. Never run it concurrently with another agent's e2e work.

Critical path: 3 Python tasks → 3 helper tasks → 1 component task (the longest of 2A/2B/2C) →
page/CSS → e2e. Real concurrency is Wave 2 only, and it is worth roughly three tasks of wall
clock.

---

## Out of scope, deliberately

- **`backup_pellet_db(action="backup")`** on the load-profile path (`routes.py:39`). It writes
  a timestamped backup file and is not reachable from any other pellet action; porting it
  means porting `common/backups.py`'s file surface, which belongs with the **admin** page
  (backlog OPEN 6). The Flask page keeps doing it; a load performed from React simply does
  not snapshot. **Record this in the backlog when the page ships** — it is a real, if small,
  behaviour difference between the two UIs.
- **Deleting the Flask `blueprints/pellets/` blueprint.** No page has been deleted on this
  migration yet and this is not the plan that starts. `tests/web/test_page_pellets.py` stays
  green throughout and is the characterization net that proves the port did not change the
  backend's behaviour.
- **A dashboard hopper-card "Manager" shortcut** (`_macro_dash_default.html:360`). Belongs to
  `plans/2026-07-25-react-dashboard-slice.md`; noted there is not the same as built here.
- **Optimistic concurrency on the pellet blob** (`lastupdated.time` compare-and-swap). The
  residual millisecond race in Hazard 1 is pre-existing and datastore-wide.
- **`clear_pelletdb` / `clear_pelletdb_log`** (`socket_io.py:456-463`) — admin actions, and
  `clear_pelletdb` still shells out to `os.system("rm pelletdb.json")` against a file the
  SQLite migration retired. That is an admin-page finding; do not "fix" it here.

## Could NOT verify

- **Nothing on this page was opened in a browser for this plan.** Another agent owns the
  browser this session, and the investigation was read-only by instruction. The 1280×720 fit
  in Task 12's CSS is derived from `.pf-shell-main`'s box model (`shell.css:17-22`) and the
  grid arithmetic, **not observed**. Task 13 Step 3 is the check that makes it real; if the
  page scrolls, fix the grid, do not relax the assertion. The wizard shipped with zero CSS
  and a fully green suite for exactly this reason (backlog lesson 2).
- **`socket_pellet_data` was not observed arriving in a browser.** The emit sites
  (`socket_io.py:182`, `:191`, `:224`) and the payload shape (`:174`) were read; that a
  browser client actually receives it after `listen_app_data` was not exercised. Backlog
  lesson 3 applies in full: verifying via `GET /api/pellets` is **not** verification of the
  socket path. If Task 7's unit test passes and the page still shows "Loading pellet
  database…" in a real browser, the fault is here, not in the component.
- **Whether `control.notify_data` is non-empty on the test rig**, which Task 3's clobber test
  asserts as a precondition. It is seeded per probe; a rig with no probes configured will skip
  rather than prove the fix.
- **The exact pre-existing Biome/eslint warning count.** `bun run lint` was not run (read-only
  investigation). The brief states 2 `react-refresh` warnings; treat that as the baseline and
  report immediately if the untouched tree shows a different number.
- **Flask's `est_usage` string output was not compared against `formatUsage` on real data.**
  The arithmetic was transcribed from `routes.py:207-211`; the trailing-zero divergence is
  reasoned from Python/JS number formatting, not from a side-by-side run.

## Self-Review

**Spec coverage:** every control and POST action in the Verified-facts table maps to a task —
load profile → Task 8; hopper check → Task 8; brands/woods add+delete → Task 9; add profile
(both buttons) → Task 10; edit profile → Task 10; delete profile → Task 10; delete log →
Task 11; current load-out, level and usage read-outs → Tasks 5 and 8.
**Placeholder scan:** none — every endpoint path, payload key, error string, class name and
call site above is cited to a file and line in live code. **Type consistency:** `PelletDb`
(Task 4) is consumed by Tasks 7, 8, 9, 10, 11, 12; `PelletActionResult` and `ProfileFields`
(Task 6) by Tasks 10 and 12; `formatUsage` (Task 5) by Task 8 only.
**Hazards answered:** array clobber → the intent-only API contract, stated in three places
(module docstring, route docstring, client header) so it survives a future refactor;
`notify_data` clobber → Task 3, with a failing test first; destructive actions → four
`ConfirmAction` flows, one per delete, each with a consequence-bearing `message`.
