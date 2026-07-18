# Phase D — Blueprints Service Layer + God-Route Decomposition — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the duplicated web-service logic across blueprints (probe-config update, os/system-info wrappers, the render-context tail, the save-and-flag pattern, the Socket.IO response envelope) into shared helpers, then decompose the god-routes (`settings_page`, `admin_page`, `probeconfig_page`, `_post_app_data`/`_get_app_data`) behind dispatch maps — with **no runtime behavior change**, guarded by the existing Playwright web suite.

**Architecture:** Two groups on one branch (`refactor/blueprints-service`), **dedup first (D1, low risk), then decompose (D2, medium risk)**. D1 extracts services into `common/system.py`, `common/app.py`, and a new `common/web_services.py` (or `common/app.py` — see Task 1); each existing call site becomes a thin caller keeping only its own envelope/template shape. D2 turns each god-route's `if action == …` ladder into a `{action: handler}` (or `{(section, action): handler}`) dispatch map with one small function per branch, preserving exact control-write ordering and the order-dependent hotspots. Phase A already split `common/common.py` into a package (this plan imports from `common.datastore_accessors`, `common.system`, `common.defaults`, etc., NOT `common.common`).

**Tech Stack:** Python 3.14, Flask + Flask-SocketIO, pytest + pytest-playwright, Serena for all symbolic edits.

## Global Constraints

- **Behavior-preserving.** No runtime behavior change. Where the investigation found a *real* pre-existing behavioral difference between two copies (the probe `enabled` coercion; the os-info default strings), the merged helper must PRESERVE both behaviors OR the difference is surfaced to the human as a deliberate, signed-off change (Tasks 1 and 2 flag these). Do not silently pick one.
- **The Playwright web suite is the D2 characterization net** (`tests/web/test_page_settings.py` [19 actions], `test_page_admin.py`, `test_page_probeconfig.py`, `test_page_api.py`, etc. — 154 tests, all green). Every D2 decomposition must keep these **byte-green**. `settings_page`/`admin_page`/`probeconfig_page` are covered; **`_post_app_data`/`_get_app_data` are Socket.IO event handlers NOT covered by the HTTP-route Playwright suite** — Task 9 must add characterization FIRST (see it).
- **14 cataloged latent bugs** (`docs/web-test-findings-2026-07-17.md`) are pinned by passing characterization tests that assert CURRENT (buggy) behavior. Decomposition must PRESERVE that behavior — do not "fix" a bug mid-refactor (it would flip its test red). Fixes are a separate effort.
- **Serena for ALL code edits.** `mcp__serena__initial_instructions` first, activate the project. Never hand-edit blind.
- **Test command is ALWAYS** `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest <args> -q`. Bare `python3 -m pytest` gives false failures; missing offscreen vars HANG. Exit 124 = hang, stop. The Playwright tests need Chromium (installed here; they RUN, not skip).
- **`uvx ruff format <changed files>` before every commit** (pre-commit hook runs it too — re-stage/amend). `ruff check` is not a gate.
- **Commit via `git commit -F <file>`** (zsh eats backticks). End every message `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- **⚠️ REBOOT HAZARD (3 prior incidents):** `admin_page` calls `reboot_system`/`shutdown_system`/`restart_scripts` → `os.system("...sudo reboot...")`, and `factorydefaults` does `os.system("rm settings.json")`. The Playwright admin tests already neutralize these by patching at `blueprints.admin.routes` (the from-import binding site, NOT `common.system`). Task 7 must keep those handlers resolving from the same module the tests patch, or the tests stop intercepting and a real reboot fires. Do not run admin destructive actions unmocked.
- **Branch base:** `refactor/blueprints-service` from `massive-reworks-and-new-ui` (currently `5bbdc1b`, contains A+B+C + the Playwright suite). PR targets `massive-reworks-and-new-ui`.
- **Baseline suite: 1332 passed.** Report the new total after each task; no task may reduce the pre-existing count.
- **Trust live code, not the spec.** The spec's D-section is pre-Phase-A and was wrong on multiple counts (corrected in "Verified facts" below). Re-confirm any file:line before relying on it.

## Verified facts this plan rests on (Serena-assisted read @ `2dd7d1b`; spec corrected)

- **God-routes (current file:line):** `settings_page` `blueprints/settings/routes.py:11-675` (665 lines, 19 actions, FLAT `if request.method=="POST" and action=="X"` chain — each an independent `if`, not `elif`); `admin_page` `blueprints/admin/routes.py:29-354` (326 lines: top-level `reboot`/`shutdown`/`restart`, a `POST … action=="setting"` block with 15 sub-branches keyed on response keys, a `boot` action, then UNCONDITIONAL system-info gather + `render_template`); `probeconfig_page` `blueprints/probeconfig/routes.py:10-387` (378 lines, two-level `section` × `action`, 8 leaf actions); `_post_app_data` `blueprints/mobile/socket_io.py:362-677` (315 lines, `action` × nested `type` — already dispatch-shaped); `_get_app_data` `socket_io.py:269-359` (90 lines, single `action` ladder).
- **Spec correction — `cookfile_update` is NOT god-sized:** it's `blueprints/cookfile/routes.py:458-599` (142 lines), dispatching on JSON-key presence (`comments`/`metadata`/`graph_labels`/`media`). The large function in that file is `cookfile_page` (~430 lines), unnamed by the spec. **Leave both out of D2's dispatch-map scope** (cookfile_update is small; cookfile_page decomposition is not worth the risk here and is now Playwright-covered as-is).
- **Spec correction — probe-config duplication is 2 copies, not 3:** `settings/routes.py:72-114` (`probe_config_save` branch) and `socket_io.py:820-863` (`_update_probe_config`) share the `probe_edited`-building loop. `probeconfig_page`'s `add_probe`/`edit_probe` operates on the **`wizardInstallInfo`** store (a different on-disk store), NOT `settings["probe_settings"]` — it is NOT a third copy (`probe_edited` appears only in the two files above). **Real diffs between the 2 copies:** (a) DTO source (`request.json` vs a param dict `request["probes_action"]`); (b) `enabled` coercion — settings does `== "true"` (string), socket_io passes raw via `.get()` — a REAL behavioral difference; (c) control flags (settings sets only `probe_profile_update`; socket_io also `settings_update`); (d) response vocab (`jsonify({"result":"success"|"label_not_found"})` vs `_response("OK"|"Error")`).
- **Spec correction — os-info: Phase A extracted the PRIMITIVE, the WRAPPER is still duplicated.** `common/system.py:get_os_info` (Phase A) does the raw `/etc/os-release`+`uname` read. The caching/backfill/`BITS`-computation WRAPPER is independently reimplemented in `admin/routes.py:368-403` (`_get_os_info`, logs errors, `"Unknown."`) and `socket_io.py:1009-1041` (`_get_os_info`, bare `except`, `"Unknown"` no period) — a real cosmetic diff. Same for the system-info gather sequence: `admin_page` inline (`:257-330`) vs `socket_io._get_system_info` (`:935-1005`).
- **Render-context tail:** the two kwargs `page_theme=settings["globals"].get("page_theme","light")` and `grill_name=settings["globals"].get("grill_name","")` are byte-identical across **39** `render_template` sites in 14 blueprints. **No Flask `context_processor` exists** (grep-confirmed) — a clean context_processor target.
- **Save-and-flag:** `control["<x>_update"] = True` at **17** sites; flag names VARY (`settings_update` ×9, `probe_profile_update` ×3, `controller_update`, `distance_update` ×2, `units_change` ×2). A shared helper needs the flag name(s) as a parameter.
- **Response envelope:** `socket_io.py:931-932` `_response(result, message=None, data=None) -> {"data","result","message"}`, **67 uses**, always a BARE dict (Socket.IO handlers, no `jsonify`, no status). The WLED REST endpoints (`api/routes.py`: `wled_discover`/`wled_push_profiles`/`wled_test_profile`) use `jsonify({result,message,<flat extra keys>}), <status>` with `"success"`/`"error"` vocab and flat keys (`devices`/`profiles_pushed`). **These are a DIFFERENT convention** — see Task 5's decision.
- **`is_not_blank`:** 38 callers, all in `settings/routes.py`, post-Phase-A `.strip()`-based (`common/app.py`). All are simple `if is_not_blank(r,"x"): settings[...]=int/float(r["x"])` guards — independent, not order-dependent. Task 6 must carry each into its handler verbatim.
- **Order-dependent hotspots (D2 must preserve exactly):** `settings_page` `history` action has an explicit `# This check should be the last in this group` (`settings/routes.py:~541`); `settings_page` `cycle` action embeds a nested `for item,value in response.items(): if item.startswith("controller_config_")` dispatch keyed on `option["option_type"]`; `admin_page` `factorydefaults` does `rm settings.json` BEFORE recreate+`restart_scripts` and returns early; `admin_page` restore has `local_file != "none"` checked before `remote_file`; `probeconfig_page` `add_probe`/`edit_probe` has the virtual-port insert-then-pop reorder (and the KNOWN virtual-self-edit 500 bug — preserve it, it's pinned); `_post_app_data` `timer_action` is stateful (`control["timer"]["paused"]` gates two paths under one `type`).

---

## GROUP D1 — Extract shared services (low risk, dedup)

### Task 1: `update_probe_config()` — collapse the 2 probe-config copies

**Files:** Create/extend `common/app.py` (add `update_probe_config`); modify `blueprints/settings/routes.py` (`probe_config_save` branch) and `blueprints/mobile/socket_io.py` (`_update_probe_config`). Tests: the Playwright `test_page_settings.py::*probe_config_save*` + any socket_io probe test must stay green.

**Interfaces:**
- Produces: `update_probe_config(settings, control, probe_dto) -> (settings, control, result)` where `probe_dto` is a NORMALIZED dict (`label`, and any of `name`/`type`/`port`/`device`/`enabled`/`profile_id` already coerced by the caller). It runs the shared `probe_edited`-building loop, mutates `settings`, sets `control["probe_profile_update"]=True`, and returns a result flag (`"success"`/`"label_not_found"`). It does NOT write, does NOT set `settings_update`, does NOT build a response envelope — callers keep those.

- [ ] **Step 1: Read both copies** (`settings/routes.py:72-114`, `socket_io.py:820-863`) with Serena `include_body=True`; diff them; confirm the 4 differences from Verified facts.
- [ ] **Step 2: ⚠️ Decision — the `enabled` coercion difference.** settings does `True if probe_config.get("enabled",False)=="true" else False`; socket_io passes `.get("enabled", existing)` raw. **Preserve BOTH** by having the helper accept an already-resolved `enabled` in `probe_dto`, and each caller do its OWN coercion before calling (settings keeps `== "true"`; socket_io keeps raw). Do NOT unify the coercion — that would change one caller's behavior. Confirm this preserves both in your report.
- [ ] **Step 3: Add `update_probe_config` to `common/app.py`** (Serena) with the shared loop, parameterized on the normalized dto.
- [ ] **Step 4: Repoint `settings_page`'s `probe_config_save`** to build its dto (with `== "true"` coercion), call the helper, then do its own `write_settings`+`write_control`+`jsonify({"result": result})` mapping (`"success"`→success, `"label_not_found"`→label_not_found).
- [ ] **Step 5: Repoint `socket_io._update_probe_config`** to build its dto (raw enabled), call the helper, set `settings_update`, `_write_settings`, and map to `_response("OK"|"Error")`.
- [ ] **Step 6:** `timeout 180 env … uv run pytest tests/web -q` → green; full suite → 1332. Commit.

### Task 2: `get_display_os_info()` + `gather_system_info()` — collapse the os/system-info wrappers

**Files:** `common/system.py` (add both); `blueprints/admin/routes.py` (`_get_os_info`, inline system-info in `admin_page`) and `blueprints/mobile/socket_io.py` (`_get_os_info`, `_get_system_info`). Tests: `test_page_admin.py` (renders os/system-info sections) must stay green.

**Interfaces:**
- Produces: `get_display_os_info() -> dict` — the cache-read → live-fallback (`get_os_info()`) → backfill-defaults → `BITS`-from-`ARCHITECTURE` wrapper. `gather_system_info(control) -> dict` — the shared `process_command`/`get_system_command_output` sequence (wifi/throttle/cpu-temp/network/hardware) writing `control["system"][...]`.

- [ ] **Step 1:** Read all four functions (Serena); confirm the primitive `get_os_info` is already in `common/system.py` and the wrappers duplicate the caching/backfill.
- [ ] **Step 2: ⚠️ Decision — the default-string diff** (`"Unknown."` vs `"Unknown"`; logged vs bare `except`). These are user-facing default strings. **Recommend unifying to one** (`"Unknown"` + logged error) as a tiny, documented cosmetic change, OR preserve per-caller if you'd rather zero-change — flag which you chose for sign-off. Whichever, the Playwright admin test asserts the RENDERED os-info section; if it asserts a specific "Unknown." string, keep that or update the test in the same commit (test change, documented).
- [ ] **Step 3:** Add `get_display_os_info` + `gather_system_info` to `common/system.py` (Serena).
- [ ] **Step 4:** Repoint `admin_page` + `socket_io` to call them, each keeping its own error-list/template-shape (`admin` appends human-readable `errors[]`; socket_io builds its `info_details` return shape).
- [ ] **Step 5:** `tests/web` green; full suite 1332. Commit.

### Task 3: Flask `context_processor` for `page_theme` + `grill_name`

**Files:** `common/app.py` or `app.py` (register a `@app.context_processor`); then remove the two now-redundant kwargs from the 39 `render_template` sites (they'll be injected globally). Tests: every page render test must stay green.

- [ ] **Step 1:** Add a context_processor that returns `{"page_theme": settings["globals"].get("page_theme","light"), "grill_name": settings["globals"].get("grill_name","")}` — reading current settings (via `read_settings()`). Confirm it's registered on the real `app` and available to ALL templates.
- [ ] **Step 2:** Verify a page still renders the theme/name WITHOUT the explicit kwargs (temporarily remove from ONE render call, run that page's Playwright test → green). This proves the processor works before touching all 39.
- [ ] **Step 3:** Remove the two kwargs from all 39 `render_template` sites (Serena `replace_content` per file; grep `page_theme=` to find them). A template that referenced them still works (context_processor supplies them).
- [ ] **Step 4:** Full `tests/web` → green (every page render test proves the theme/name still reach the templates); full suite 1332. Commit.

### Task 4: `save_settings_and_flag_update()` helper

**Files:** `common/app.py` (add); repoint the ~17 write+flag sites across `settings/routes.py`, `admin/routes.py`, `socket_io.py`. Tests: persistence assertions in the Playwright suite must stay green.

**Interfaces:**
- Produces: `save_settings_and_flag_update(settings, control, *flags, origin="app")` — `write_settings(settings)`; for each flag name in `flags`, `control[flag]=True`; `write_control(control, WriteKind.MERGE, origin=origin)`. Flags vary per site, so they're variadic.

- [ ] **Step 1:** Add the helper (Serena). Read the ~17 sites (grep `control\["\w+_update"\] = True` / `units_change` / `distance_update`) and note each site's exact flag set + whether it uses MERGE vs OVERWRITE (only migrate MERGE sites; leave OVERWRITE/flush sites).
- [ ] **Step 2:** Repoint the sites where the pattern is exactly write+flag(s)+merge-write, passing that site's flag name(s). Leave any site with extra interleaved logic alone (report it).
- [ ] **Step 3:** `tests/web` green (the persistence assertions confirm the same writes+flags happen); full suite 1332. Commit.

### Task 5: `api_response()` for the Socket.IO envelope — **scoped decision**

**Files:** `common/app.py` (add `api_response`); repoint `socket_io.py`'s `_response` (67 uses). Tests: socket_io tests + `test_page_api.py`.

- [ ] **Step 1: ⚠️ DECISION — do NOT force WLED into this.** The 67 `_response` uses are uniform bare dicts (Socket.IO). The WLED REST endpoints use `jsonify(...), status` with a DIFFERENT `"success"`/`"error"` vocab and **flat extra keys** (`devices`, `profiles_pushed`) that a fixed `data=` param would have to NEST — changing the wire shape and BREAKING the JS/mobile clients (`test_page_api.py` asserts the flat shape). **Recommend: `api_response` replaces only the Socket.IO `_response` (relocate it to `common/app.py` so both mobile and any future Socket.IO consumer share it), and leave the WLED HTTP endpoints exactly as they are this phase.** Get sign-off if the human wants WLED unified (it needs careful wire-shape preservation + possibly test changes).
- [ ] **Step 2:** Move `_response` to `common/app.py` as `api_response(result, message=None, data=None)` (same bare-dict shape); repoint socket_io's 67 uses to import it (or keep `_response = api_response` as a thin local alias to minimize churn — your call, note it).
- [ ] **Step 3:** socket_io tests + `tests/web` green; full suite 1332. Commit.

---

## GROUP D2 — Decompose the god-routes (medium risk; Playwright net gates it)

> Each D2 task turns a `if action==…` ladder into per-action handler functions + a `{action: handler}` dispatch dict, preserving EXACT control-write ordering and the hotspots in Verified facts. The Playwright page test for that route is the byte-green gate. Extract handlers ONE at a time, running that page's Playwright test after each, exactly as Phase A's `process_command` dispatch-table task did.

### Task 6: `settings_page` → dispatch map (19 handlers)

**Files:** `blueprints/settings/routes.py`. Gate: `tests/web/test_page_settings.py` (19-action coverage) byte-green after every extraction.

- [ ] **Step 1:** Read `settings_page` fully (Serena). List the 19 `(action)` branches. Extract the SMALLEST first to a module-level `_settings_<action>(settings, control, response, …)` handler, replace its inline block with a call, run `test_page_settings.py` → green. This proves the shape.
- [ ] **Step 2:** Extract the remaining branches one at a time, running `test_page_settings.py` after each. **Preserve verbatim:** every `is_not_blank` guard; the `history` action's `# This check should be the last` ordering; the `cycle` action's nested `controller_config_` option-type dispatch (carry it as a unit); the GET-JSON branches (`smartstart` GET, `pwm_duty_cycle` GET).
- [ ] **Step 3:** Replace the ladder with `_SETTINGS_DISPATCH = {action: handler}`; `settings_page` becomes a thin lookup + the shared render tail (which Task 3 simplified). Preserve the final `render_template` fall-through.
- [ ] **Step 4:** `test_page_settings.py` (all 19) + full `tests/web` green; full suite 1332. Commit.

### Task 7: `admin_page` → dispatch map (⚠️ reboot-safe)

**Files:** `blueprints/admin/routes.py`. Gate: `tests/web/test_page_admin.py` byte-green.

- [ ] **Step 1: Reboot safety FIRST.** The Playwright admin tests patch `reboot_system`/`shutdown_system`/`restart_scripts` at `blueprints.admin.routes`. Your handlers MUST still reference those names as module-level attributes of `blueprints.admin.routes` (i.e. keep the `from common.system import reboot_system, …` import in that module and call the bare names) so the patch keeps intercepting. If you move a handler to another module, the test's patch target moves too — do NOT; keep admin handlers in `admin/routes.py`.
- [ ] **Step 2:** Extract handlers one at a time (top-level `reboot`/`shutdown`/`restart`/`boot`; the 15 `setting` sub-actions; the system-info-gather tail from Task 2). Run `test_page_admin.py` after each. **Preserve verbatim:** `factorydefaults` order (`rm settings.json` → recreate → `restart_scripts` → early return); restore precedence (`local_file != "none"` before `remote_file`).
- [ ] **Step 3:** Dispatch dict(s) — note the `setting` sub-actions are keyed on RESPONSE KEYS not an `action` value, so that block may be a `{response_key: handler}` inner map. Preserve the unconditional system-info gather + render tail.
- [ ] **Step 4:** `test_page_admin.py` + full `tests/web` green (confirm NO real reboot: the tests' `no_real_subprocess`/reboot patches must still show zero hazardous calls); full suite 1332. Commit.

### Task 8: `probeconfig_page` → `(section, action)` dispatch

**Files:** `blueprints/probeconfig/routes.py`. Gate: `tests/web/test_page_probeconfig.py` (incl. the virtual-port ordering tests) byte-green.

- [ ] **Step 1:** Extract each of the 8 leaf handlers (`devices`: delete_device/add_config/add_device/edit_config/edit_device; `ports`: delete_probe/config/add_probe|edit_probe), one at a time, running `test_page_probeconfig.py` after each. **Preserve verbatim the virtual-port insert-then-pop reorder** in add_probe/edit_probe — including the KNOWN virtual-self-edit IndexError→500 bug (it's pinned by a characterization test; keep it, don't fix it here).
- [ ] **Step 2:** `{(section, action): handler}` dispatch; preserve the two section tails (`render_template_string`) and the GET render.
- [ ] **Step 3:** `test_page_probeconfig.py` + full `tests/web` green; full suite 1332. Commit.

### Task 9: `_post_app_data` / `_get_app_data` → dispatch maps — **characterize FIRST**

**Files:** `blueprints/mobile/socket_io.py`. **⚠️ These are Socket.IO event handlers NOT covered by the HTTP Playwright suite** — there is NO characterization net for them yet.

- [ ] **Step 1: Add characterization FIRST** (a new `tests/web/test_socketio_app_data.py` or `tests/mobile/…`). Drive `_get_app_data`/`_post_app_data` directly (they're plain functions taking `(action, …)` — call them with representative payloads against the seeded `live_server` datastore, or via a Socket.IO test client if one composes; simplest is direct function calls with the `ds` fixture). Assert the `_response`/`api_response` payloads AND resulting settings/control writes for each `action` × `type`. **Preserve the `timer_action` stateful behavior** (the `control["timer"]["paused"]` two-path gate) and note the latent index bug the investigation flagged (pin it, don't fix). Commit this net BEFORE decomposing.
- [ ] **Step 2:** Decompose `_get_app_data` (8 actions) then `_post_app_data` (8 action-groups × nested type) into per-action handlers + dispatch maps, running the new characterization after each extraction. `_post_app_data` is already dispatch-shaped, so this is mechanical; `timer_action` is the one stateful branch — carry it as a unit.
- [ ] **Step 3:** New characterization + full `tests/web` + full suite green. Commit.

---

## Self-Review

- **Spec coverage (Phase D section):** D1 probe-config dedup → Task 1 (corrected to 2 copies); os/system-info dedup → Task 2 (corrected: extract the wrapper, primitive already done by A); save-and-flag → Task 4; render-tail → Task 3 (context_processor); response envelope → Task 5 (scoped to Socket.IO, WLED flagged). D2 god-route decomposition → Tasks 6 (settings, 19), 7 (admin, reboot-safe), 8 (probeconfig, virtual-port), 9 (_post/_get_app_data, characterize-first). `cookfile_update` correctly dropped (not god-sized). All covered.
- **Deliberate-change gates:** the only real behavioral diffs found (probe `enabled` coercion — Task 1 preserves both; os-info default strings — Task 2 flags for sign-off) are surfaced, not silently resolved. Everything else is behavior-preserving under the Playwright net.
- **Characterization coverage:** settings/admin/probeconfig are Playwright-covered (D2 Tasks 6-8 gated). The ONE gap — Socket.IO `_post/_get_app_data` — is closed by Task 9 Step 1 BEFORE its decomposition. The 14 cataloged bugs' tests pin current behavior; D2 preserves it.
- **Placeholder scan:** the only deferred-to-implementation content is per-handler verbatim bodies (read via Serena at execution) and the two sign-off decisions (Tasks 1/2) + the WLED scope decision (Task 5) — all explicitly flagged with a recommendation, not hand-waved.
- **Type/name consistency:** `update_probe_config`, `get_display_os_info`, `gather_system_info`, `save_settings_and_flag_update`, `api_response`, `_SETTINGS_DISPATCH`, `_settings_<action>` used consistently across tasks.
- **Ordering:** D1 (dedup, low risk) precedes D2 (decompose); within D2, each route extracts handlers one-at-a-time under its Playwright gate, and the un-covered Socket.IO route characterizes before decomposing. Reboot safety (Task 7 Step 1) precedes admin extraction.
- **Risk note:** Task 7 (admin, reboot) and Task 8 (probeconfig, virtual-port + the known 500) are the highest-risk; both are fully Playwright-gated. Task 5's WLED scoping and Tasks 1/2's behavioral diffs need human sign-off at execution.
