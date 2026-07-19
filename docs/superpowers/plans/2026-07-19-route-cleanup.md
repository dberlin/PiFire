# Blueprint Route Cleanup Plan

> **For agentic workers:** Use `superpowers:subagent-driven-development` or
> `superpowers:executing-plans` to run this task-by-task. Every task is behavior-preserving and lands
> as its own PR/branch. Read live code, not this plan, if they ever disagree — and fix the plan.

## Context

After Phase D, the web layer's heavy business logic (chart building, event totals, annotations, CSV
prep, cookfile parsing) already lives in `common/app.py`, `common/system.py`, and `file_mgmt/`. The
large route files that remain are mostly **action dispatch** — one big view function with a long
`if "<key>" in request.form/json:` chain — plus **copy-pasted render/error blocks** and a few
**sequential transformation blocks**. This plan makes each route a thin Flask boundary (parse →
dispatch → respond) without changing any URL, method, response shape, template, redirect, filename,
or runtime side effect. It replaces the deleted 2026-07-19 roadmap/route-design/gates docs, whose
prescriptions (`services.py`, a "first" event-summary helper that already exists) were wrong against
the live code.

## The established pattern — follow this, do NOT invent `services.py`

Phase D refactored `settings`, `admin`, `probeconfig`, `mobile/socket_io.py`. There is **no
`services.py` anywhere**, and this plan does not add one. Three homes cover every case:

1. **Inline `_`-prefixed handlers in the same `routes.py`, wired through a module-level dispatch
   dict.** Contract: *mutate state in place and return `None` to fall through to the tail render, or
   return a Response to short-circuit.* Handlers read `request.form`/`request.json` directly. Key
   type matches the original branch structure — `(method, action)` (settings), `(section, action)`
   (probeconfig), or plain string (admin, socket_io). Two keys may map to one handler.

   ```python
   _SETTINGS_DISPATCH = {
       ("POST", "display"): _settings_display,
       ("GET", "smartstart"): _settings_smartstart_get,
   }
   def settings_page(action=None):
       ...
       handler = _SETTINGS_DISPATCH.get((request.method, action))
       if handler is not None:
           result = handler(settings, control, controller, event)
           if result is not None:
               return result
       return render_template("settings/index.html", ...)
   ```

2. **Substantial pure logic → sibling `<blueprint>.py` module** (precedent: `tuner/tuner.py`,
   `wizard/wizard.py`; no Flask imports, `from .tuner import *`). Only for non-trivial computation;
   tiny CRUD handlers stay inline.

3. **Cross-blueprint shared helpers → `common/*.py` or `file_mgmt/`** — never a per-domain module.
   This is where `prepare_event_totals`, `prepare_annotations`, `prepare_csv`, `paginate_list`,
   `allowed_file`, `update_probe_config`, `api_response`, etc. already live.

## Verification gates (per task)

The `tests/web/` harness runs the real `app.py` on a background thread against an isolated temp-SQLite
DB, asserts status + JSON/DOM shapes, reads back persisted state, and intercepts dangerous side
effects (`test_page_update.py` monkeypatches `os.system` + `subprocess.run`). Coverage is sufficient
to refactor behind for every in-scope domain except wizard.

For each task: confirm the domain's `tests/web` file is green, extract, confirm green again.

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web -q
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit tests/characterization -q
uvx ruff check .
```

Run `uvx ruff format` on changed files before every commit (standing repo rule). Before merging the
series: `uv run pytest -q` all green.

---

## Task 1 — `pellets` (proves the inline-handler pattern)

Cleanest fit: `pellets_page` is a textbook `if action == ...` chain of tiny `pelletdb` CRUD handlers.

- [ ] Branch `refactor/route-pellets`. Confirm `tests/web/test_page_pellets.py` (8) green.
- [ ] Convert to a `_PELLETS_DISPATCH` string-keyed dict of inline `_pellets_*` handlers
      (`loadprofile`, `hopperlevel`, `editbrands`, `editwoods`, `addprofile`, `editprofile`,
      `deletelog`). Keep handlers inline (5-15 lines each); no sibling module.
- [ ] Fix the `addprofile` `control = {}` inconsistency only if a test pins it; otherwise leave.
- [ ] Verify green, `ruff format`, commit.

## Task 2 — `api` (JSON shapes + one builder extraction)

`api_page` dispatches on `(action, method)`; `get/set/cmd/sys` short-circuit to `process_command`.

- [ ] Branch `refactor/route-api`. Confirm `tests/web/test_page_api.py` (23) green.
- [ ] Dispatch GET/POST actions via a dict; keep the `get/set/cmd/sys` short-circuit as-is.
- [ ] Extract the `current` branch (~51-88) into a named `build_current_status(...)` builder — it is
      field-by-field assembly, not a dispatch entry. Put it inline or in `common/app.py` if reused.
- [ ] Verify green, `ruff format`, commit.

## Task 3 — `cookfile` + `history` PAIRED (the highest-value dedup)

Do these together — they share a copy-pasted render block. Splitting them defeats the point.

- [ ] Branch `refactor/route-cookfile-history`. Confirm `tests/web/test_page_cookfile.py` (18),
      `test_page_history.py` (4), `test_history_export_route.py` (1) green.
- [ ] **Extract the shared render block first.** The ~24-line "reshape `cookfilestruct` +
      `render_template('cookfile/index.html', ...)`" block is copied 6× — `cookfile/routes.py`
      ~182-205, 242-265, 320-343, 365-388, 412-435 and `history/routes.py` ~88-111. Create one
      `render_cookfile_page(cookfilestruct, filenameonly, settings, errors)` in `file_mgmt/common`
      or `common/app.py` (cross-blueprint → shared home).
- [ ] Extract the `errortype` block (copied 5×: cookfile 285-290/306-311/352-356/398-403, history
      114-119) into `classify_cookfile_error(status)`.
- [ ] Convert `cookfile_page` (two-level: content-type, then action key) and `cookfile_update` to
      dispatch dicts. Give every action an explicit return (kills the dead fall-through tails at
      ~134-135 and ~437-438).
- [ ] Extract `cookfile_update`'s `graph_labels` branch (~513-564) into a named multi-step function
      (read → rename → write → remap `probe_mapper` → write) — transformation, not a dispatch entry.
- [ ] Convert `history_page` dispatch (`stream`/`refresh`/`cookfile`/`setmins`/`export` + nested
      `cookfile` sub-actions), now calling the shared helper.
- [ ] Verify all three test files green, `ruff format`, commit.

## Task 4 — `recipes` (dispatch + macro lookup table)

`recipes_data` is the most dispatch-heavy file: 12 top-level form actions + ~15 nested
`update`/`delete`/`add`/`refresh` sub-actions.

- [ ] Branch `refactor/route-recipes`. Confirm `tests/web/test_page_recipes.py` (20) green.
- [ ] Convert to nested dispatch dicts (top-level action, then sub-action).
- [ ] Fold the ~12 repeated `render_template_string("{% from 'recipes/_macro_recipes.html' import
      render_recipe_edit_X %}...")` calls into an `{action: macro_name}` lookup table.
- [ ] Verify green, `ruff format`, commit.

## Task 5 — `tuner` (move math to the sibling module)

- [ ] Branch `refactor/route-tuner`. Confirm `tests/web/test_page_tuner.py` (6) green. Do NOT touch
      controller runtime in this PR.
- [ ] Move `read_auto_status`'s ~100 lines of high/medium/low temp-Tr selection math (~107-211) into
      `tuner/tuner.py` alongside the existing SHH functions. The route keeps the control-write /
      mode-transition side effects.
- [ ] Verify green, `ruff format`, commit.

## Task 6 — `update` (dispatch only, keep side effects intact)

Behavior lives in `updater.py`; the route just sets status → `os.system(...)` → renders. Already well
characterized (`test_page_update.py` intercepts `os.system`/`subprocess`).

- [ ] Branch `refactor/route-update`. Confirm `tests/web/test_page_update.py` (14) green.
- [ ] Convert to dispatch. **Do not change any `os.system`/subprocess call or its guard** in this PR
      (the `branch_target` interpolation stays as-is here; see Task 8).
- [ ] Verify green, `ruff format`, commit.

## Task 7 — `wizard` (LAST; add tests first)

Highest operational risk: `finish` spawns `os.system("wizard.py &")`, and scan branches trigger
real hardware/network discovery. **Route-level coverage is thin.**

- [ ] Branch `refactor/route-wizard`. **Add characterization tests first**: pin `finish` (intercept
      the install kickoff like the existing reboot-modal test does), and the `bt_scan`,
      `thermoworks_discover`, `i2c_bus_scan`, `usb_serial_scan`, `modulecard`, `cancel`, and default
      render branches. Neutralize `os.system`/discovery in the fixtures before running anything.
- [ ] Convert to dispatch; heavy assembly already lives in `wizard/wizard.py`.
- [ ] Verify green, `ruff format`, commit.

## Task 8 — Latent defects (separate branch, sign-off gated)

The dispatch conversions expose these. Keep them OUT of the structural PRs; **get human sign-off
before changing any `os.system`/subprocess behavior** (standing repo rule).

- [ ] Branch `refactor/route-defect-fixes` after the structural PRs land.
- [ ] `recipes/routes.py:339` `os.system(f"rm {filepath}")` (filename from `request.json`) — command
      injection → `os.remove` + path validation. **Sign-off required.**
- [ ] `history/routes.py:81,128` hardcoded `"./history/" + response[...]` instead of `HISTORY_FOLDER`,
      + `os.remove` on unvalidated filename — path traversal. Use `HISTORY_FOLDER` + validate.
- [ ] `api/routes.py:135,154` bare `except:` on settings/control writes — narrow to specific types.
- [ ] Duplicated hardcoded `/tmp/pifire/{parent_id}` staging path (cookfile:226, recipes:63) →
      promote to config.
- [ ] `update/routes.py` `branch_target` interpolated into `os.system` — validate against the known
      branch list. **Sign-off required.**
- [ ] Each fix flips/adds a characterization test that pins the corrected behavior.

> **Not a defect:** `wizard/wizard.py:25` `except KeyError, TypeError:` is canonical Python 3.14
> syntax in this repo — do not "fix" it.

## Landing order

1 (pellets) → 2 (api) → 3 (cookfile+history) → 4 (recipes) → 5 (tuner) → 6 (update) → 7 (wizard) →
8 (defects, sign-off gated). Each is an independent, behavior-preserving PR.

## Self-review

- **No `services.py`**: every extraction targets inline handlers, a sibling `<blueprint>.py`, or
  `common`/`file_mgmt`, matching live precedent.
- **Highest-value work is first-class**: the 6× cookfile render block and 5× errortype block are
  explicit Task 3 steps, not an afterthought.
- **Dispatch vs transformation separated**: fat branches (api `current`, cookfile `graph_labels`,
  tuner `read_auto_status`) get named-step extraction, not a dispatch entry.
- **Behavior preserved**: defects are quarantined to Task 8 with sign-off gates; no behavior change
  smuggled into a structural PR.
