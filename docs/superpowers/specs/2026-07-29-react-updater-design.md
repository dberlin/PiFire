# React Updater Page — Design

**Status:** design (brainstorming output), 2026-07-29
**Goal:** Port the Flask software-update page (`blueprints/update/`) to the React app as a dedicated `/update` route, reached from an Admin "System Update" card. It is the last un-ported *functional* Flask page.

## Summary

The updater lets a user see what version/branch they run, how far behind the
remote they are, switch branches, pull the latest code, reinstall
dependencies, and watch a long-running install progress to completion (which may
end in a reboot). The port follows the established page pattern — a new JSON REST
blueprint (`blueprints/api_update`, mirroring `api_tuner`/`api_admin`) plus a
React page and typed client — and reuses the wizard's install-progress polling,
which already speaks the exact same install-status contract.

This is a **high-blast-radius** page: its actions shell out to `git reset
--hard`, branch switches, `git pull`, `pip`, and can trigger a reboot. The design
keeps every existing guard and adds no new way to fire them; safety comes from
gating and test-neutralization, not from omitting features.

## Context — the Flask surface being ported

`blueprints/update/routes.py` is a GET/POST dispatch over `update_page`. All the
git/pip work lives in the top-level `updater.py` module; the routes are thin.

**Read operations** (real git queries; work on any checkout, no hardware gate):

- `get_update_data(settings)` → `{version, branch_target, branches[], remote_url, remote_version}`
  (current tag+version, current branch, available branches, remote URL, remote's version).
- `get_available_updates(branch="")` → `{success, commits_behind, message}`
  (runs `git fetch` + `git rev-list --left-only --count origin/<branch>...@`).
- `get_log(num_commits)` → `(result, error_msg)` (git log of the last N commits).
- `get_updater_install_status()` → `(percent, status, output)` — read from SQLite
  (`_get_install_status("updater")`), written by the background `updater.py` process.

**Mutating operations.** Each POST fires a **detached** background process
`os.system(f"{python_exec} updater.py <flags> &")`, **gated on `is_real_hardware()`**,
and seeds `set_updater_install_status(0, "Starting…", "")` so the page can poll
progress. The exact commands Flask fires:

| Flask form key | Command fired | Extra guard |
|---|---|---|
| `update_remote_branches` | `updater.py -r` | — |
| `change_branch` | `updater.py -b <branch_target>` | `branch_target ∈ branches`, else alert |
| `do_update` | `updater.py -u <branch_target> -p` | `control.mode == STOP`, else alert |
| `do_upgrade` | `updater.py -i` | `control.mode == STOP`, else alert |

**Install-status sentinels** (identical to the wizard's): the background process
writes ascending `percent`, ending at `percent = 142 if reboot else 101`
(`updater.py:548`). The React wizard's `InstallProgress.tsx` already treats
`percent > 100` as finished and `142` as "finished, reboot required"
(`REBOOT_REQUIRED_PERCENT = 142`).

**`is_real_hardware(settings)`** is `settings["platform"]["real_hw"]`
(`common/system.py:25`). On a non-Pi (including this dev box) it is `False`, so the
mutating shell-outs are skipped — reads and progress polling still work.

## Architecture

Three units, each independently testable:

1. **`blueprints/api_update/`** — a new JSON REST blueprint. Owns HTTP: envelopes,
   status codes, guards, and firing the background process. Calls the existing
   `updater.py` functions unchanged and the existing install-status accessors. No
   rendered HTML — this drops Flask's `render_template_string` paths (the
   post-message and change-branch alerts), removing that template-injection
   surface, exactly as the tuner slice did for its fragment endpoint.

2. **`web-react/src/helpers/update/updateApi.ts` (+ types)** — a typed client for
   `/api/update/*`, modeled on `helpers/admin/adminApi.ts`. Pure network + envelope
   unwrapping; no React.

3. **React UI** — a `SystemUpdateCard` on the existing `AdminPage`, and a new
   `/update` route rendering `UpdatePage`. Progress uses the wizard's
   install-status polling contract.

### REST surface — `blueprints/api_update/`

All responses are `api_response(...)` envelopes (`common/app.py`), matching
`api_tuner`. Reads carry data; mutations return `{started: true}`.

**Reads** (no gate — real git, safe anywhere):

| Method / path | Source | Success payload |
|---|---|---|
| `GET /api/update/state` | `get_update_data(settings)` | `{version, branch, branches, remote_url, remote_version}` |
| `GET /api/update/check` | `get_available_updates()` | `{current, behind}` — or `Error` envelope with `get_available_updates`'s message |
| `GET /api/update/log?commits=N` | `get_log(N)` | `{output}` — or `Error` with the git error text |
| `GET /api/update/status` | `get_updater_install_status()` | `{percent, status, output}` |

`commits` is validated as a positive integer (Flask's `r["show_log"].isnumeric()`),
defaulting to 10; a non-numeric value is a 400, not a silent fallback.

**Mutations** (fire the detached `updater.py`, gated on `is_real_hardware()`):

| Method / path | Body | Fires | Guard → error |
|---|---|---|---|
| `POST /api/update/branches/refresh` | — | `updater.py -r` | — |
| `POST /api/update/branch` | `{target}` | `updater.py -b <target>` | `target ∉ branches` → 400 |
| `POST /api/update/pull` | — | `updater.py -u <branch> -p` | mode ≠ STOP → 409 |
| `POST /api/update/upgrade` | — | `updater.py -i` | mode ≠ STOP → 409 |

Each mutation, when it proceeds: `set_updater_install_status(0, "Starting…", "")`,
then fires the process **only if `is_real_hardware()`**, then returns
`{started: true}` (200). On a non-Pi it still returns `{started: true}` (the status
row is seeded, no process runs) — matching Flask's local no-op. The 409/400 guards
are checked **before** seeding status, and return without firing.

The `python_exec` prefix (`settings["globals"].get("python_exec", "python")`) and
the trailing `&` are preserved from Flask. The command string is built from a
fixed template with only the validated `target`/`branch` interpolated — never a
raw request field.

## Safety model (load-bearing)

This section is the reason the page is a careful port, not a thin one.

1. **`is_real_hardware()` gate preserved.** The shell-out happens only under the
   gate, exactly as Flask does. A non-Pi seeds status and returns success without
   running anything.
2. **STOP-mode guard, server-side.** `pull` and `upgrade` refuse with 409 unless
   `control.mode == STOP`. Flask enforces this in the handler and renders an alert;
   here it is an HTTP status the client renders, and it is enforced in the API, not
   a template a client could bypass.
3. **Branch allowlist, server-side.** `change_branch` rejects a target that is not
   in the fetched `branches` list (400). No client-supplied string reaches the git
   command except after this check.
4. **Tests neutralize the shell-out before any verification.** Per the standing
   repo rule: `os.system`/`subprocess`/reboot are monkeypatched in every test that
   touches a mutation, asserting the *right command string* is produced and the
   gates hold — **no test ever runs git, pip, or a reboot**, and the e2e never
   triggers a real update. Read-endpoint tests run against the live repo (a real
   git checkout) because those operations are safe.
5. **No new firing surface.** The React UI exposes exactly the four actions Flask
   does, each behind a confirmation and disabled with a reason when its guard would
   reject it.

## React UI

### `SystemUpdateCard` on `AdminPage`

A new card (alongside `SystemCard`/`MaintenanceCard`/`BackupsCard`/`LogsCard`)
showing the current version and a lightweight behind-count from
`GET /api/update/check`, with a `<Link to="/update">` "Open Updater". This mirrors
Flask's Admin → "Go to the Updater" flow
(`blueprints/admin/templates/admin/index.html`).

### `/update` route — `UpdatePage`

A single page with these regions, driven by `GET /api/update/state` + `/check`:

- **Status header** — current version+tag, current branch, remote URL, remote
  version, and a "N commits behind" / "up to date" line from `/check`.
- **Branch** — a `<select>` of `branches`, a "Change Branch" button (confirm), and
  a "Refresh remote branches" button (`/branches/refresh`).
- **Actions** — "Update to latest" (`/pull`) and "Upgrade dependencies"
  (`/upgrade`), each behind a confirm. When the grill is not stopped, both are
  disabled with an inline note ("Stop the grill before updating"), matching the
  server's 409; a 409 that slips through is surfaced as that same message.
- **Log** — a "Show last N commits" control that fetches `/api/update/log` and
  renders the output in a scrollable block.
- **Progress** — once any mutation returns `{started: true}`, the page switches to
  polling `GET /api/update/status` and renders `percent`/`status`/`output`,
  reusing the wizard's sentinel contract: `percent > 100` = done, `142` = done +
  reboot required (show a reboot notice). On done, it re-reads `/state` + `/check`
  so the header reflects the new version/branch.

The install-progress polling in `InstallProgress.tsx` and the updater share one
contract; the plan will decide whether to extract a shared `useInstallStatus`
hook or copy the small polling loop — an implementation call, not a design one.

Reached from Admin, **not the global navbar** — Flask has no updater nav entry
either (it lives under Admin).

## Scope (YAGNI)

**In:** everything above — the four reads, the four mutations with their guards,
the Admin card, the `/update` page with progress polling.

**Out (recorded, not built):** the post-update **"what's new" release-notes
modal**. It is app-shell chrome — a one-time modal shown on *any* route when
`settings["globals"]["updated_message"]` is set (`templates/base.html:165-230`),
bodied by `GET /update/post-message`'s `render_template_string`. It belongs with
the shell, not this page, and is recorded under *Deferred by the updater slice
(design)* in `react-migration-backlog.md`.

**Untouched:** `blueprints/update/` stays live (the Flask UI and its
characterization net remain), retiring only with the general Flask-retirement pass
(ruling 5). `updater.py` is called unchanged — its git/pip maths is not
re-implemented.

## Testing

- **Python (`blueprints/api_update`):** read endpoints tested against the live git
  checkout (real `state`/`check`/`log`); the `status` endpoint against a seeded
  install-status row. Mutation endpoints with `os.system`/`subprocess`
  monkeypatched, asserting: the exact command string fired, `is_real_hardware()`
  gating (no fire when `real_hw` is False), the STOP-mode 409 on pull/upgrade, the
  branch-allowlist 400, and the `commits` validation 400. The existing Flask
  `tests/web/test_page_update.py` characterization net stays untouched as the
  legacy net.
- **React:** unit tests for `updateApi.ts` (URLs/methods/envelope handling),
  `UpdatePage` (each region + the guard-disabled states + the progress transition
  and the `142` reboot branch), and `SystemUpdateCard`. A mocked-backend e2e drives
  check → branch list → log → start → progress-to-done, and asserts it **never**
  reaches a real update (backend routes are mocked/gated).

## Verified facts (checked against live code — do not re-derive)

- `blueprints/update/routes.py` — dispatch, `is_real_hardware()` gate on all four
  mutations, STOP-mode guard on `do_update`/`do_upgrade`, and the exact
  `os.system(f"{python_exec} updater.py …&")` command strings above.
- `updater.py` — `get_update_data` returns `{version, branch_target, branches,
  remote_url, remote_version}`; `get_available_updates` returns `{success,
  commits_behind, message}` and runs `git fetch` + `git rev-list --count`;
  `get_log` returns `(result, error_msg)`; completion writes `percent = 142 if
  reboot else 101` (`:548`).
- `common/datastore_accessors.py` — `get_updater_install_status()` →
  `(percent, status, output)` from SQLite `_get_install_status("updater")`;
  `set_updater_install_status(percent, status, output)`.
- `common/system.py:25` — `is_real_hardware(settings)` returns
  `settings["platform"]["real_hw"]`.
- `web-react/src/components/wizard/InstallProgress.tsx` — `REBOOT_REQUIRED_PERCENT
  = 142`, `percent > 100` means finished; the identical contract the updater writes.
- `web-react/src/components/admin/AdminPage.tsx` — card layout the
  `SystemUpdateCard` joins; `helpers/admin/adminApi.ts` — the typed-client shape
  `updateApi.ts` mirrors.
- Flask reaches `/update` from `blueprints/admin/templates/admin/index.html`
  ("System Updates" tab → "Go to the Updater"); no navbar entry.

## Open risks (for the plan to resolve, not blockers)

- **`useInstallStatus` extraction vs copy** — the wizard and updater share the
  status contract; the plan decides whether to factor a shared hook. Either is
  fine; extraction must not disturb the wizard's passing tests.
