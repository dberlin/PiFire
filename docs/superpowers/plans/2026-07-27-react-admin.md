# React Admin Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Flask's `/admin` surface with a React `/admin` page — system control, maintenance, backups and logs — behind a typed JSON API that never accepts a filesystem path and never reaches a shell.

**Architecture:** A new `blueprints/api_admin/`, sibling to the shipped `api_files/`. Every dangerous operation keeps its existing `common/system.py` implementation; the new endpoints are a *door*, not a reimplementation. On the client, `helpers/admin/adminApi.ts` and `components/admin/` follow the recipes slice exactly.

**Tech Stack:** Flask blueprint; React 19 + react-router + rsbuild; rstest; Playwright; Tailwind v4 `@apply`; Biome + ESLint.

**Written 2026-07-27**, from research verified against live code the same day.

---

## THIS PLAN TOUCHES REAL REBOOT AND SHUTDOWN. READ G1 FIRST.

Nine call sites can power off or restart the machine. This repo has suffered
**three real unintended reboots** from test and verification code. Every one of
them happened to someone who knew that and thought their case was different.

---

## Human rulings taken 2026-07-27, before a line was written

1. **The React page carries the destructive actions**, at parity with Flask —
   reboot, shutdown, restart, factory reset. Each goes through `ConfirmAction`,
   and each endpoint additionally refuses unless the grill is stopped.
2. **`GET /api/cmd/reboot|shutdown|restart` becomes POST-only**, with regression
   tests. A bare GET currently reboots the machine: reachable by any link,
   prefetch or crawler. No in-repo client uses the GET form.
3. **Both adjacent bugs are fixed here**: the backup-restore path containment,
   and the missing `is_real_hardware()` gate on `restart_control` /
   `restart_webapp`.

---

## Global Constraints

Every task's requirements implicitly include this section.

### G1. Safety — the whole reason this plan is written carefully

**The hazard inventory.** Nine call sites, three independent doors:

| # | Site | Command | Gated by `is_real_hardware()`? |
|---|---|---|---|
| 1 | `blueprints/admin/routes.py:109` | `os.system("rm ./logs/events.log")` | n/a |
| 2 | `blueprints/admin/routes.py:177` | `os.system("rm logs/*.log")` | n/a |
| 3 | `common/system.py:41` | `os.system("sleep 3 && sudo supervisorctl restart control &")` | **NO — Task 4 fixes** |
| 4 | `common/system.py:47` | `os.system("sleep 3 && sudo supervisorctl restart webapp &")` | **NO — Task 4 fixes** |
| 5 | `common/system.py:64-80` | `subprocess.run(["sudo","systemctl","restart",…])` | yes |
| 6 | `common/system.py:103-111` | `systemctl reboot` → `sudo reboot` → `os.system("sudo reboot")` | yes |
| 7 | `common/system.py:129-137` | `systemctl poweroff` → `shutdown -h now` → `os.system(…)` | yes |
| 8 | `blueprints/mobile/socket_io.py:543` | `os.system("sleep 3 && sudo reboot &")` | no (exception fallback) |
| 9 | `blueprints/mobile/socket_io.py:552` | `os.system("sleep 3 && sudo shutdown -h now &")` | no (exception fallback) |

**`is_real_hardware()` defaults to TRUE.** `default_settings()` ships
`platform.real_hw = True`, so a fresh test datastore does **not** disable the
dangerous branch. Two of the three real reboots happened to people relying on it.
**A `real_hw=False` fixture is not a safety mechanism.**

**Every dangerous call runs on a daemon thread after a 3-second sleep**, so the
HTTP response returns *before* the machine goes down. A green test result proves
nothing about whether you are about to reboot.

**The neutralization mechanism is already written — reuse it verbatim.**
`tests/web/test_page_admin.py:111-157`'s `hazard_guard` fixture. Two parts, both
required:

1. `mock.patch.object(<THE IMPORTING MODULE>, "reboot_system", …)` — patch the
   module that did `from common.system import reboot_system`, **not**
   `common.system` itself. The import binds the name into the importer's globals,
   so patching the origin silently misses the call site. This is the
   "moving code out from under a mock" trap, and it has bitten this repo three
   times.
2. `mock.patch("os.system", …)` globally, because the `rm` calls invoke it
   directly.

Plus `BACKUP_PATH` / `UPLOAD_FOLDER` redirection to a `tempfile.mkdtemp` for
anything touching backups.

**Prove the neutralization, do not assume it.** `test_page_admin.py:248-271`
additionally patches `subprocess.run` for the first destructive request and
asserts no `reboot`/`poweroff`/`shutdown`/`supervisor`-shaped argv ever reached
it. Every new destructive test does the same.

**Other standing rules:**
- Never `pkill -f`, and never interpolate `pgrep -f` into `kill` — the pattern
  matches your own shell. Exit 144, and on 2026-07-27 it took out both gunicorn
  and control.py.
- Do NOT run `bun run test:e2e` (the `app` project) casually — it moves the
  shared grill mode. `bun run test:e2e:fidelity` is the safe one. The one admin
  spec that must live in `app` is Task 14, and it may not touch a destructive
  action.
- No test writes outside `tempfile.mkdtemp`.

### G2. Toolchain

- **bun, never npm.** Commit `bun.lock`.
- Python: `uv run pytest tests/`, with `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy`.
- **`.venv/bin/ruff format` before every commit.** NEVER `uvx ruff` (repo pins <0.16).
- web-react gate: `bun run typecheck && bun run lint && bun run test`.
- Python 3.14+; `except A, B:` without parens is ruff-canonical — do not "fix" it.

### G3. Tooling for implementers

- **serena for symbol work** (`find_symbol`, `find_referencing_symbols`). Never
  grep for references — it has found 16 of 41 real refs here.
- **context-mode `ctx_execute` for any command with more than a few lines of
  output.** A pytest transcript through Bash has already exhausted one agent's
  context window.
- **`grep -r --include=GLOB` returns zero matches inside `ctx_execute`** — a
  sandbox bug. Scope by path instead.

### G4. Version control

- `jj describe --stdin` with a quoted heredoc. There is **no `-F` flag**. Never
  `git commit`.
- **Your edits are already in `@`** — there is nothing to squash. `jj squash`
  moves them into the parent. Run `jj new` before starting if `@` has someone
  else's description.
- Conventional commits: `feat(api_admin): …`, `fix(system): …`.

### G5. The path rule

A client sends a **bare filename**; the server resolves it through
`common/file_browser.py::resolve_managed_file`. No route added here may accept a
path. This is not theoretical — Task 3 fixes four existing violations.

### G6. Response envelope

`common/app.py::api_response` → `{"data", "result", "message"}`, `result == "OK"`.
**400** `bad_request` with `data.field`; **404** `not_found`; **409** conflict;
**422** `unreadable`; **200** otherwise.

### G7. Destructive endpoints refuse unless stopped

Every endpoint that reboots, shuts down, restarts, or factory-resets answers
**409 `not_stopped`** unless `control["mode"] == Mode.STOP`. This is a
**deliberate divergence** — Flask offers them from any mode. It matches
`POST /api/probe_map` and `POST /api/files/recipes/run`, and it means a test that
forgets to stub still cannot reboot from the default Stop-mode fixture by
accident. **It is a second line of defence, not the first. Stub anyway.**

---

## Verified facts — checked against live code 2026-07-27

### F1. The Flask admin surface

One route, `admin_page(action=None)` (`blueprints/admin/routes.py:341-417`),
dispatching on `_ADMIN_DISPATCH` (`:332-338`) →
`{reboot, shutdown, restart, setting, boot}`, with `setting` fanning out through
`_ADMIN_SETTING_DISPATCH` (`:286-302`) to fifteen sub-actions.

`_AdminActionContext` (`:30-44`) is a mutable bag (`settings, control, pelletdb,
errors, warnings, success, backup_path`) built per request. Handlers mutate it
**in place** and the tail `render_template` reads the same objects, which is why
handlers mostly return `None`. **The React port does not need this shape** — each
endpoint returns its own payload.

`_ADMIN_SETTING_DISPATCH` is iterated **in insertion order**, first matching form
key wins. Order is load-bearing and preserved from the original.

The fifteen sub-actions: `debugenabled`, `clearhistory`, `clearevents`,
`clearpelletdb`, `clearpelletdblog`, `factorydefaults`, `download_logs`,
`delete_logs`, `download_settings`, `download_control`, `download_pip_list`,
`backupsettings`, `restoresettings`, `backuppelletdb`, `restorepelletdb`.

### F2. Backups

- `BACKUP_PATH = "./backups/"` (`common/common.py:56`). Read **twice**: as
  `current_app.config["BACKUP_PATH"]` by the blueprint, and as the module
  constant by `common/backups.py:29`. **A fixture must patch both.**
- `backup_settings()` (`common/backups.py:39-59`) writes
  `PiFire_<timestamp>.json`; `backup_pellet_db(action="backup")` (`:107-155`)
  writes `PelletDB_<timestamp>.json`. Both maintain `manifest.json`.
- Restore re-parses through `read_settings_file(filename=…, init=True)` —
  `init=True` deliberately runs the same version-overlay/`upgrade_settings()`
  migration a live boot applies. **Keep it.**
- `read_pellet_db_file()` has a bounded self-repair recursion (`retry_count`,
  max 5) that calls back into `backup_pellet_db(action="restore")`.

### F3. The containment bug Task 3 fixes

`blueprints/admin/routes.py` builds the restore path as raw concatenation —
`ctx.backup_path + local_file` at **`:220`, `:235`, `:262`, `:272`** — where
`local_file = request.form["localfile"]` (`:212`, `:259`) is client-supplied and
never validated. `resolve_managed_file` exists precisely to close this and is
currently used only by `blueprints/api_files/`.

### F4. The GET hazard Task 2 fixes

`/api/cmd/reboot|shutdown|restart` reach `_cmd_cmd_reboot` / `_cmd_cmd_shutdown`
/ `_cmd_cmd_restart` (`common/api_commands.py:883-904`) through the generic
`/<action>/<arg0>` route (`blueprints/api/routes.py:463-477`), which **does not
gate `action == "cmd"` by method**. A GET reboots the machine.

### F5. The socket door duplicates five of these

`_post_app_data_admin` (`blueprints/mobile/socket_io.py:504-566`) exposes
`clear_history`, `clear_events`, `clear_pelletdb`, `clear_pelletdb_log`,
`factory_defaults`, `reboot`, `shutdown`, `restart_control`, `restart_webapp`,
`restart_supervisor`. **Out of scope** — it is the mobile app's API and is
independently tested. Do not touch it beyond Task 4's shared gate.

### F6. React already links to Flask admin

`components/wizard/InstallProgress.tsx:69,72` render `<a href="/admin/reboot">`
and `<a href="/admin/restart">`, and `WizardShell.tsx:136` sets
`window.location.href = "/admin/restart"`. **These keep working** — the Flask
blueprint stays live until the general retirement pass. Do not repoint them at
the new endpoints in this slice; the wizard's install flow is its own surface and
changing it here would put a real reboot behind an untested code path.

### F7. Navbar

`components/shell/NavBar.tsx:25` — `{ label: "Admin", to: null, end: false }`,
rendered as a disabled span. `NavBar.test.tsx` asserts Admin is a non-link with
`aria-disabled="true"`; **that case must be edited in the same change** that
enables the route, exactly as the recipes slice did for Recipes.

---

## File Structure

### Python — created
| File | Responsibility |
|---|---|
| `blueprints/api_admin/__init__.py` | Blueprint declaration, `url_prefix="/api/admin"`. |
| `blueprints/api_admin/routes.py` | Every admin endpoint. Takes no paths. |
| `blueprints/api_admin/admin_api.py` | The handlers, mirroring `cookfile_api.py`. |
| `tests/web/test_api_admin_system.py` | Destructive actions, each with proof of neutralization. |
| `tests/web/test_api_admin_maintenance.py` | Clears, factory reset, toggles. |
| `tests/web/test_api_admin_backups.py` | Backup/restore/upload/download + traversal. |
| `tests/web/test_api_cmd_requires_post.py` | Task 2 regression. |

### Python — modified
| File | Change |
|---|---|
| `blueprints/admin/routes.py` | Task 3: four restore sites through `resolve_managed_file`. |
| `common/system.py` | Task 4: gate `restart_control`/`restart_webapp`. |
| `blueprints/api/routes.py` or `common/api_commands.py` | Task 2: `cmd` becomes POST-only. |
| `app.py` | Register `api_admin_bp`. |

### React — created
`helpers/admin/adminApi.ts`, `helpers/admin/adminTypes.ts`,
`components/admin/AdminPage.tsx`, `SystemCard.tsx`, `MaintenanceCard.tsx`,
`BackupsCard.tsx`, `LogsCard.tsx`, `admin.css`.

### React — modified
`components/App.tsx`, `components/shell/NavBar.tsx` (+ its test),
`src/styleCoverage.test.ts`, `tests/e2e/pageSpecs.ts`, `tests/e2e/apiFixtures.ts`.

---

# SLICE A — the backend, and the three fixes

The three fixes come first, deliberately. They harden the surface the rest of the
slice builds on, and each is independently reviewable.

---

### Task 1: `blueprints/api_admin/` scaffolding and `GET /api/admin/state`

**Files:** create `blueprints/api_admin/{__init__,routes,admin_api}.py`; modify `app.py`; test `tests/web/test_api_admin_maintenance.py`.

**Interfaces produced:** `admin_api.state_payload(settings, control)`, `routes.error`, `routes.json_body`, `routes.require_stopped()`.

- [ ] **Step 1: write the failing test**

`GET /api/admin/state` returns everything the page renders without a write:
system info, `debug_mode`, `boot_to_monitor`, the backup listings for both kinds,
and the available log files. Assert the exact key set — the React types are
generated against it.

- [ ] **Step 2: run it, confirm 404, then build the blueprint**

Copy the shape of `blueprints/api_files/__init__.py` + `routes.py` exactly:
blueprint in `__init__.py`, `from . import routes` at the bottom, `error()` and
`json_body()` helpers verbatim from `api_files/routes.py:43-107`.

- [ ] **Step 3: add `require_stopped()`, the G7 guard, once**

```python
def require_stopped():
    """(None) if the grill is stopped, else (response) refusing with 409.

    G7: every destructive endpoint gates on this. It is a second line of
    defence behind test stubbing, not a replacement for it.
    """
    control = read_control()
    if control.get("mode") != Mode.STOP:
        return error("not_stopped", 409, mode=control.get("mode"))
    return None
```

- [ ] **Step 4: register in `app.py`, run, format, commit**

---

### Task 2: `/api/cmd/*` becomes POST-only

**Files:** modify `blueprints/api/routes.py` (or `common/api_commands.py` — put the gate wherever the method is visible); test `tests/web/test_api_cmd_requires_post.py`.

- [ ] **Step 1: the failing test, which proves the hazard exists**

```python
def test_get_cannot_reboot(client, hazard_stubs):
    """A bare GET currently reboots the machine. Any link, prefetch or crawler
    that touches the URL is enough."""
    resp = client.get("/api/cmd/reboot")
    assert resp.status_code == 405
    assert hazard_stubs["calls"] == []
```

**Before writing the fix, run this test and record that it FAILS with the reboot
stub having been called.** That recorded output is the evidence the hazard was
real. Use the `hazard_guard` mechanism from G1 — patch the *importing* module.

- [ ] **Step 2: gate `cmd` by method**

Only the `cmd` action changes. Every other `/api/<action>` form keeps its current
methods; narrowing them is a separate decision and out of scope.

- [ ] **Step 3: assert POST still works, for all three**

- [ ] **Step 4: format, run the full `tests/web/` and `tests/unit/` API suites, commit**

The mobile app is a third-party client. Note in the commit message that no
in-repo caller used the GET form.

---

### Task 3: backup restore stops taking a client path

**Files:** modify `blueprints/admin/routes.py`; test `tests/web/test_page_admin.py` (extend).

- [ ] **Step 1: the failing traversal test**

Mirror the recipes slice's pair: a hostile-string parametrisation, **and** a
real backup file that merely lives outside the folder — the second is what proves
containment rather than hiding a read error.

```python
def test_restore_refuses_a_backup_outside_the_folder(hazard_guard, ...):
    outside = <a valid PiFire_*.json written to tmp_path, NOT the backup dir>
    resp = client.post("/admin/setting", data={"restoresettings": "true",
                                               "localfile": f"../{outside.name}"})
    #  The settings must be untouched and no restart may have been dispatched.
    assert hazard_guard["calls"] == []
```

- [ ] **Step 2: route all four sites through `resolve_managed_file`**

`:220`, `:235`, `:262`, `:272`. `must_exist=True` for a restore. On `None`,
append to `ctx.errors` and fall through to the normal render — **do not** raise;
this blueprint renders a page, not JSON.

- [ ] **Step 3: confirm the existing admin suite still passes, format, commit**

---

### Task 4: gate `restart_control` and `restart_webapp`

**Files:** modify `common/system.py`; test `tests/unit/system/test_system_lifecycle.py` (extend).

- [ ] **Step 1: the failing tests**

Every other dangerous call in `common/system.py` checks `is_real_hardware()`
first. These two do not, and both shell out to `sudo supervisorctl`. Use the
existing `sync_thread` fixture (`tests/unit/system/test_system_lifecycle.py:36-39`)
which swaps `threading.Thread` for a synchronous stand-in, and patch
`cc.is_real_hardware` / `cc.os.system` on the `common.system` module (valid here
because these tests target that module directly).

Assert: with `real_hw=False`, no `os.system` call is made; with `real_hw=True`,
the exact command string is dispatched.

- [ ] **Step 2: add the gate, matching the shape the neighbouring functions use**

- [ ] **Step 3: check for callers that relied on the ungated behaviour**

Use `find_referencing_symbols` on both functions. The socket door is the only
known caller; confirm it, and confirm the `real_hw=False` no-op is acceptable
there (it is — the same is already true of `reboot`/`shutdown` beside it).

- [ ] **Step 4: format, run `tests/unit/system/` and `tests/web/test_socketio_app_data.py`, commit**

---

### Task 5: `POST /api/admin/system` — reboot, shutdown, restart

**Files:** modify `blueprints/api_admin/{routes,admin_api}.py`; test `tests/web/test_api_admin_system.py`.

**READ G1 IN FULL BEFORE WRITING A LINE OF THIS TASK.**

- [ ] **Step 1: build the hazard fixture for this module FIRST, before any endpoint**

Copy `tests/web/test_page_admin.py:111-157` verbatim, changing only the module it
patches — it must patch `blueprints.api_admin.routes` (or wherever the import
lands), because that is the importing module. Include the `subprocess.run`
assertion helper.

**Run one deliberately trivial test through the fixture and assert `calls == []`
before writing the endpoint.** A fixture that does not actually intercept is
worse than no fixture.

- [ ] **Step 2: the endpoint**

`POST /api/admin/system` `{action: "reboot"|"shutdown"|"restart"}`. Unknown action
→ 400 `data.field == "action"`. Not stopped → 409 (G7). Otherwise call the
existing `reboot_system()` / `shutdown_system()` / `restart_scripts()` — **do not
reimplement them, and do not add a new shell-out.**

- [ ] **Step 3: tests, each asserting BOTH that the right stub fired AND that no hazardous argv reached `subprocess.run`**

- [ ] **Step 4: format, run, commit**

---

### Task 6: maintenance, toggles, and factory reset

**Files:** modify `blueprints/api_admin/{routes,admin_api}.py`; test `tests/web/test_api_admin_maintenance.py`.

- [ ] **Step 1: `POST /api/admin/maintenance`**

`{action: "clear_history"|"clear_events"|"clear_pelletdb"|"clear_pelletdb_log"}`.

**`clear_events` must NOT shell out.** Flask runs
`os.system("rm ./logs/events.log")` (`:109`). The new endpoint uses
`os.remove`/`pathlib` with the path built server-side. Same for anything else
that would reach a shell — this surface adds none.

- [ ] **Step 2: `POST /api/admin/settings` for the two toggles**

`debug_mode` and `boot_to_monitor`. `debug_mode` must also raise the
`settings_update` control flag, as `_admin_setting_debugenabled` does
(`:80-95`) — otherwise the running control process never learns.

**Verify `boot_to_monitor` passes `PartialSettingsSchema`** before relying on the
generic settings path; the research could not confirm it. If it does not, that is
a schema gap to fix here, not to route around.

- [ ] **Step 3: `POST /api/admin/factory-reset`**

Mirrors `_admin_setting_factorydefaults` (`:128-166`) exactly: `flush_history()`,
`flush_control()`, `clear_pellet_db()`, `write_settings(default_settings())`,
control reseed, then `restart_scripts()`. G7 guard applies. Full hazard fixture
applies. **Pellet DB clearing is deliberate** — it was a ruling, not an accident;
see the backlog.

- [ ] **Step 4: tests, format, commit**

---

### Task 7: backups — list, create, restore, upload, download

**Files:** modify `blueprints/api_admin/{routes,admin_api}.py`; test `tests/web/test_api_admin_backups.py`.

- [ ] **Step 1: the fixture redirects BOTH `BACKUP_PATH` references**

`current_app.config["BACKUP_PATH"]` *and* `common.backups.BACKUP_PATH`
(F2). Patching one and not the other writes into the real checkout.

- [ ] **Step 2: the endpoints**

- `GET /api/admin/backups?kind=settings|pelletdb` → the listing.
- `POST /api/admin/backups/create` `{kind}` → the new bare filename.
- `POST /api/admin/backups/restore` `{kind, file}` → **`resolve_managed_file`**,
  then the existing read/write pair. Settings restore keeps `init=True` (F2) and
  triggers `restart_scripts()`; pellet restore does not restart, matching Flask.
- `POST /api/admin/backups/upload` multipart → extension-checked, resolved with
  `must_exist=False`.
- `GET /api/admin/backups/download?kind=&file=` → `send_file`, resolved.

- [ ] **Step 3: a traversal test per endpoint, plus the outside-the-folder case**

- [ ] **Step 4: format, run, commit**

---

### Task 8: logs

**Files:** modify `blueprints/api_admin/{routes,admin_api}.py`; test.

- [ ] **Step 1: `GET /api/admin/logs` and `GET /api/admin/logs/download`**

Reuse `_zip_files_logs` (`blueprints/admin/routes.py:420-428`) rather than
rewriting the zip. Note it stages into `/tmp` — keep that, and keep the tests off
any predictable path.

- [ ] **Step 2: `POST /api/admin/logs/delete` — without a shell**

Flask runs `os.system("rm logs/*.log")` (`:177`) inside a bare `except:`. The new
endpoint globs server-side and `os.remove`s, reporting what it deleted. No shell.

- [ ] **Step 3: tests, format, commit. Slice A ends here — hand to review.**

---

# SLICE B — the React page

---

### Task 9: types and `adminApi.ts`
Mirror `helpers/files/recipeApi.ts`, reusing the shared `read`/`write`/`postForm`
helpers already lifted into `helpers/files/apiEnvelope.ts`. Every destructive call
surfaces the 409 `not_stopped` message.

### Task 10: `/admin` route, nav, and `AdminPage` shell
Register the route; flip `NavBar.tsx:25` to `to: "/admin"`; **edit both
`NavBar.test.tsx` cases together** (F7). Page shell fetches `GET /api/admin/state`
once, with an `onChanged` refetch threaded into every card.

### Task 11: `SystemCard` — the destructive four
Reboot, shutdown, restart, factory reset. **Every one through `ConfirmAction`**,
with copy that names what will actually happen. Disabled unless the grill is
stopped, with the 409 surfaced if it races. Factory reset names the pellet
database explicitly — it is the least reversible thing on the page.

### Task 12: `MaintenanceCard` — clears and toggles
Four clears, each confirmed. Two toggles, saved through the `SaveStatus` pattern.

### Task 13: `BackupsCard`
List both kinds, create, download, upload, restore. Restore is confirmed and
warns that settings-restore restarts the server.

### Task 14: `LogsCard`, e2e, and the full gate
Log list + download + confirmed delete.

**The e2e spec must not touch a destructive action.** Assert their disabled
state and stub at the API boundary. It lives in the `app` project, which is why
Task 14 is where it lands — and `bun run test:e2e` stays banned.

Baselines: add `admin` to `pageSpecs.ts` with its own `stubAdmin` fixture, capture
with `-g "admin"`, and verify the diff is **pure additions**.

Full gate: `bun run typecheck && bun run typecheck:e2e && bun run lint && bun run test && bun run test:e2e:fidelity`, plus `uv run pytest tests/ -q`.

### Task 15: backlog closeout
Mark `admin` shipped under item 8. Record: the GET→POST change and that no
in-repo client used GET; the restore containment fix; the `restart_control` /
`restart_webapp` gate; the G7 Stop-mode divergence from Flask; that the socket
door still carries its own duplicate hazards and was deliberately untouched; and
that the wizard still hard-links to Flask `/admin/reboot` and `/admin/restart`.

---

## Parallelization

Tasks 2, 3 and 4 are **independent of each other and of Task 1** — different
files, different tests. Run them concurrently in isolated jj workspaces. Tasks
5-8 all extend `api_admin/routes.py` and must be sequential after Task 1.

Slice B parallelises after Task 9: Tasks 11-14 each own one component, but all
touch `admin.css`, so they need isolated workspaces.

```sh
jj workspace add ../pifire-admin-t<N>
cp .lsp.json ../pifire-admin-t<N>/ && (cd ../pifire-admin-t<N> && bun install)
export PORT=52<N>3 DEMO_PORT=52<N>4 PIFIRE_BACKEND_URL=http://localhost:51<N>0
export PIFIRE_DB_PATH="$PWD/pifire.db"
```

**`PIFIRE_BACKEND_URL`, never `PUBLIC_PIFIRE_URL`** — rsbuild injects `PUBLIC_*`
into the browser bundle, turning every same-origin request cross-origin.

---

## Out of scope, deliberately

- **The Socket.IO admin door** (F5). It is the mobile app's API, independently
  tested, and carries its own `os.system` fallbacks. Task 4's gate is the only
  thing that reaches it.
- **Repointing the wizard's `/admin/reboot` and `/admin/restart` links** (F6).
- **Retiring `blueprints/admin/`** — no Flask page is retired until the general
  pass (backlog ruling 5). Task 3 fixes a bug in it; it stays live.
- **Narrowing the method on `/api` actions other than `cmd`.**
- **Auth.** There is none on `/api` today and adding it is its own project.

## Could NOT verify — flagged, not guessed

- Whether `boot_to_monitor` passes `PartialSettingsSchema` (Task 6 Step 2).
- Whether `tests/web/test_socketio_app_data.py` uses the same object-patching
  pattern as `hazard_guard`. Read it before extending it in Task 4.

## Self-review checklist

- [ ] Every new endpoint has a traversal test.
- [ ] Every destructive test asserts BOTH the stub fired AND no hazardous argv
      reached `subprocess.run`.
- [ ] `grep -rn "os.system" blueprints/api_admin/` returns nothing.
- [ ] No new test writes outside `tempfile.mkdtemp`.
- [ ] No route added here accepts a path.
- [ ] `jj st` clean after a full run.
