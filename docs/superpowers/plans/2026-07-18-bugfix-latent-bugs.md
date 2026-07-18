# Latent Bug Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Fix the latent bugs surfaced (but deliberately NOT fixed) during the Tier 1&2 refactors — the 14 cataloged in `docs/web-test-findings-2026-07-17.md` plus the ones found in the Phase A/D characterization work — each as its own small, test-flipped change.

**Architecture:** These are independent one-off fixes, one branch `fix/latent-bugs` (or split per severity into a few PRs). **Most bugs are currently PINNED by a passing characterization test that asserts the BUGGY behavior.** The fix procedure for each is TDD-in-reverse: (1) FLIP that test to assert the CORRECT behavior (→ RED), (2) fix the code (→ GREEN). Where no test pins it, add one. Fixes are ordered most-severe first; each is independent — cherry-pick freely.

**Tech Stack:** Python 3.14, Flask, pytest + pytest-playwright, Serena for symbolic edits.

## Global Constraints

- **Each fix flips its pinning characterization test** from asserting buggy→correct behavior in the SAME commit (or adds a test if none pins it). Never leave a test asserting the old buggy behavior after a fix.
- **Serena for all code edits.** Call `mcp__serena__initial_instructions` first.
- **Test command:** ALWAYS `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest <args> -q`. Bare `python3 -m pytest` gives false failures; missing offscreen vars HANG. Chromium is installed (Playwright tests RUN).
- **`uvx ruff format` before every commit.** `git commit -F <file>` (this zsh eats backticks). End every message `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- **⚠️ REBOOT HAZARD:** the update-page fix (#5) and anything touching `os.system`/reboot paths — keep the test-side mocking that patches at the module the call resolves from; never run those paths unmocked. `uptime` before/after.
- **Baseline suite: 1400 passed** (on `massive-reworks-and-new-ui` @ 78769f9). Branch `fix/latent-bugs` from there. Report the total after each fix; it should stay 1400 (a flipped test still counts, a new test adds).
- **Verify each fix END-TO-END**, not just via the flipped unit test — many have a Playwright characterization; confirm the corrected behavior renders/persists.
- **Trust live code.** File:line cites below are from `docs/web-test-findings-2026-07-17.md` + the phase ledgers; re-confirm with Serena before editing.

---

## GROUP 1 — Serious (correctness / crash / data-loss)

### Task 1: probeconfig — editing a virtual probe 500s on every call
`blueprints/probeconfig/routes.py` `add_probe`/`edit_probe`: when the edited probe is virtual (`"VIRT" in port`), the reorder walks `range(len(probe_info), 0, -1)` and indexes `probe_info[len(...)]` on the first iteration → IndexError → HTTP 500.
- **Pinning test:** `tests/web/test_page_probeconfig.py` — the test asserting a 500 on virtual-self-edit.
- **Fix:** the backward range must start at `len(probe_info) - 1` (or iterate `reversed(range(len(probe_info)))`), so the first index is in-bounds. Trace the insert-then-`pop`/`pop(found+1)` logic to confirm the corrected range still maintains the virtual-after-inputs ordering invariant the other branch relies on. This is subtle — read the whole reorder block and reason about the intended invariant before changing the bound.
- [ ] Flip the pinning test to assert the edit SUCCEEDS (200 + correct resulting `probe_info` order). RED.
- [ ] Fix the range bound (Serena). GREEN. Confirm the other virtual-port ordering tests still pass (the invariant must hold).
- [ ] Full suite + Playwright probeconfig green. Commit.

### Task 2: cookfile upload writes OUTSIDE the configured folder
`ulcookfilereq` saves to the LITERAL string `"HISTORY_FOLDER"` instead of the `HISTORY_FOLDER` variable → files land in a dir literally named `HISTORY_FOLDER` in cwd.
- **Pinning test:** the recipes/cookfile suite deliberately SKIPS exercising this (to avoid polluting cwd) — so add a test.
- **Fix:** replace the string literal `"HISTORY_FOLDER"` with the variable. One-line.
- [ ] Add a test that uploads a cookfile and asserts it lands in the CONFIGURED history folder (seed a temp folder), not a cwd `HISTORY_FOLDER` dir. RED against current code.
- [ ] Fix (Serena). GREEN. Commit.

### Task 3: `create_logger()` silently suppresses event logging
`common/common.py::create_logger()` uses `if not logger.hasHandlers():` — `hasHandlers()` walks ANCESTOR loggers, so when the root logger is pre-configured (pytest, or any embedding), the "events" FileHandler never attaches and `write_log`/`write_event` never reach `./logs/events.log`.
- **Pinning:** the smallpages agent worked AROUND this (wrote the file directly). No test asserts the bug per se.
- **Fix:** `if not logger.handlers:` (checks THIS logger's own handlers, not ancestors).
- [ ] Add a test: call `create_logger("events", ...)` under a configured root logger, `write_log(...)`, assert the line reaches the target file. RED.
- [ ] Fix (Serena). GREEN. **Regression-check:** confirm the fix doesn't cause DUPLICATE handlers (the guard exists to prevent re-adding on repeated `create_logger` calls — `logger.handlers` is the correct check for that too). Commit.

### Task 4: history CSV export crashes on empty history
`prepare_csv()` unconditionally indexes the history list → IndexError when history is empty (fresh install / after clear). Export route 500s.
- **Pinning test:** the mid-pages agent left `history` export/CSV uncovered noting this latent bug — add a test.
- **Fix:** guard the empty-history case (return an empty CSV / a header-only file / a friendly message — match what the caller expects; read the route).
- [ ] Add a test: request CSV export with empty history → assert no 500 (empty/header-only result). RED.
- [ ] Fix (Serena). GREEN. Commit.

### Task 5: update actions run `os.system` with no hardware gate (⚠️ reboot-adjacent)
`change_branch`/`do_update`/`do_upgrade` (`blueprints/update/routes.py`) call `os.system("... updater.py ... &")` unconditionally; `is_real_hardware()` doesn't gate `update_remote_branches`. GET render also runs real `git fetch`/`git tag`.
- **Pinning test:** `tests/web/test_page_update.py` uses a `no_real_subprocess` fixture asserting the exact command that WOULD run.
- **Fix — SCOPE CAREFULLY (get human sign-off):** this is arguably intended (users DO trigger updates from the UI). The real issues are (a) no confirmation/gate and (b) GET render shelling out. **Recommend: NOT a code-behavior change without product intent** — instead, this task may be just documenting/ticketing, OR adding a hardware/confirm gate if the human wants one. **STOP and ask the human** what "fix" means here before changing behavior; the other tasks are unambiguous, this one is a design decision.
- [ ] Present the options to the human; implement only the agreed change (may be no-op / doc-only).

### Task 6: `create_recipefile()` silently overwrites on title collision
Unlike `create_cookfile()`, a same-clock-minute same-title recipe silently overwrites the prior one with blank defaults → data loss.
- **Fix:** add same-title collision handling mirroring `create_cookfile()` (append a suffix / disambiguate). Read `create_cookfile`'s approach and match it.
- [ ] Add a test: create two recipes with the same title in the same minute → assert both survive (no overwrite). RED.
- [ ] Fix (Serena). GREEN. Commit.

### Task 7: asset upload 500s on a fresh environment
`uploadassets`/`ulmediafn`/`ulthumbfn` assume the `/tmp/pifire` PARENT dir exists; first upload on a fresh env 500s.
- **Fix:** `os.makedirs(..., exist_ok=True)` before writing.
- [ ] Add a test: upload an asset with the parent dir absent → assert success (dir created). RED.
- [ ] Fix (Serena). GREEN. Commit.

### Task 8: Celsius `set/notify/<label>/target` clobbers `primary_setpoint`
`common/api_commands.py`: under Celsius, the notify-target set writes `control['primary_setpoint']` instead of the notify target. User-facing (found by Phase A's `process_command` golden).
- **Pinning test:** `tests/characterization/test_process_command_golden.py` pins this (the golden asserts the buggy write). **Fixing it changes the golden** — regenerate ONLY the affected `set/notify/target` Celsius case, documented, per the golden's sanctioned-regeneration rule.
- [ ] Read the handler; confirm the correct target key (the notify target, not `primary_setpoint`).
- [ ] Fix (Serena). The golden case for that path changes → re-capture ONLY it (the harness's `CAPTURE_GOLDEN` for that one case) + update `GOLDEN_SHA256`; enumerate the change in the commit. Add/adjust an explicit test asserting the notify target is written. GREEN. Commit.

---

## GROUP 2 — Medium / Minor

### Task 9: `dashboard_config` KeyError on empty `selected`
`settings_page` `dashboard_config` (now `_settings_dashboard_config`) KeyErrors when `selected` is empty (`list(...keys())[0]` on empty). Pinned by a `test_page_settings.py` test asserting current behavior.
- [ ] Flip the pinning test to assert graceful handling (no 500; keep prior / sensible default). RED. Fix (guard empty `selected`). GREEN. Commit.

### Task 10: `common/backups.py` writes `./backups/manifest.json` via hardcoded path
Backup CONTENT honors `BACKUP_PATH`; only the manifest leaks to cwd. Pinned by the admin agent's observation.
- [ ] Add/flip a test asserting the manifest lands under `BACKUP_PATH`. RED. Fix the hardcoded literal to use the path. GREEN. Commit.

### Task 11: `cookfile_update` silently no-ops on a bare filename
Returns success-ish on a non-full-path filename rather than erroring.
- [ ] Add a test asserting a bare filename yields an error (not a silent no-op). RED. Fix. GREEN. Commit.

### Task 12: `cookfile_page` has no bare-GET render
Falls through to a JSON error; the real cook-detail render lives in `blueprints/history/routes.py`.
- [ ] Decide intended behavior (redirect / proper render / documented 404) with the human if unclear; add a test + fix. Commit.

### Task 13: `manifest` serves `Content-Type: text/cache-manifest`
Deprecated AppCache mimetype instead of a proper web-manifest/JSON type. Pinned by `test_page_smallpages.py` (asserts the current mimetype).
- [ ] Flip the pinning test to the correct type (`application/manifest+json`). RED. Fix the route's mimetype. GREEN. Confirm the manifest still loads in the Playwright render. Commit.

### Task 14: settings `auger_rate` `step="0.05"` blocks off-step submission
Browser-side validation silently blocks off-step values (a user entering 0.03 gets no save/feedback).
- **Fix:** in the template, relax `step` (e.g. `step="any"`) or align it to the real precision. This is a TEMPLATE change; the settings Playwright test worked around it with a step-aligned value — update that test to use an off-step value and assert it saves.
- [ ] Update the test to an off-step value asserting it persists. RED. Fix the template `step`. GREEN. Commit.

### Task 15: `default_control()` per-pin `manual` dict keys are vestigial
Live code reads only `change`/`output`/`pwm`; the per-pin sub-keys are dead.
- **Fix:** remove the dead keys from `default_control()` (`common/defaults.py`). LOW priority — verify NOTHING reads them (grep the whole tree) before removing; the `process_command`/manual golden may reference the control shape.
- [ ] Grep-confirm zero readers. Remove. Confirm the process_command golden + manual tests unaffected (if the golden captures the control shape, this changes it → re-baseline that case, documented). Commit.

### Task 16: `get_os_info()` passes `level=` to `write_log` → TypeError on its error path
`common/system.py::get_os_info()` calls `write_log(event, level="error", ...)`; `write_log(event, loggername="events")` has no `level` param → TypeError when os-info detection fails.
- [ ] Add a test that forces `get_os_info`'s error path (e.g. unreadable os-release) and asserts no TypeError. RED. Fix the `write_log` call (drop `level=` or add the param to `write_log` — check other callers first; likely just drop the kwarg). GREEN. Commit.

### Task 17: `set/lid_open` if/else branches are identical (dead branch)
`common/api_commands.py` — both branches do the same thing. Cosmetic; pinned by the golden (behavior is identical either way).
- [ ] Collapse the redundant if/else to a single branch. The golden is unaffected (behavior identical). Commit. (Pure cleanup — lowest priority.)

### Task 18: `process_command(arglist=[])` mutable default argument
Classic Python footgun; no current caller mutates it in a way that leaks, but it's a latent hazard.
- [ ] Change to `arglist=None` + `arglist = arglist or []` inside. Confirm the process_command golden unaffected (behavior identical for all current calls). Commit.

---

## Phase-D-found latent items (optional, from the Phase D ledger)

### Task 19: socketio `_post_app_data_timer` index-carryover
The timer-entry finder's loop `index` persists if no `type=="timer"` entry matches → operates on the wrong/last index. Pinned by `test_socketio_app_data.py::test_timer_action_latent_index_bug_no_timer_entry`.
- [ ] Flip that test to assert correct no-match handling (explicit not-found guard). RED. Fix (init `index=None`, guard before use). GREEN. Commit.

### Task 20: socketio double-`write_control` redundancy
`_post_app_data`/`units_action`/`_update_probe_config` call `_write_settings` (which writes control) then `write_control` AGAIN — a redundant write. Behavior is correct but wasteful.
- [ ] Confirm it's a pure redundant write (same data), remove the second `write_control`, assert the characterization tests still pass (same resulting state). Commit. (Lowest priority — efficiency, not correctness.)

## Self-Review
- **Coverage:** all 14 web-findings bugs (Tasks 1-15, minus a couple grouped) + the earlier Phase A golden-found bugs (Celsius notify #8, get_os_info write_log #16, lid_open #17, arglist mutable #18) + Phase D items (#19, #20). Every cataloged bug has a task.
- **Test-flip discipline:** each task that has a PINNING characterization test (probeconfig 500, dashboard_config, manifest mimetype, Celsius notify golden, timer index, auger_rate) explicitly FLIPS it in the fix commit — no test is left asserting old buggy behavior.
- **Sign-off gates:** Task 5 (update os.system — is it even a bug or intended?) and Task 12 (cookfile_page GET — intended behavior unclear) require human decision before implementing; flagged.
- **Golden re-baselines:** Tasks 8 (#Celsius) and possibly 15/17/18 touch `process_command` behavior/shape pinned by the golden — each documents its sanctioned single-case re-capture. Do NOT bulk-regenerate.
- **Ordering:** severity-first; all independent, cherry-pickable. Reboot-adjacent (#5) gated.
