# Refactor Batch: dash-update + route leaf-handlers + one real bug

**Date:** 2026-07-21
**Branch base:** `massive-reworks-and-new-ui`
**Spirit:** same as Phases A–E — behavior-preserving structural cleanup, each change pinned by existing characterization tests, ruff-format before every commit.

## Context

Static analysis (pyright 1.1.411 + ruff C901) over the tree surfaced the next tier of cleanup targets after the A–E dedup work. Most of the highest complexity numbers are **leaf handlers that the earlier Phase A–D dispatch refactor exposed** — the outer dispatch maps were built, but the inner branchy bodies were never split. This batch decomposes the three highest-ROI ones (all well covered by tests) and fixes the one genuine correctness bug found along the way.

Explicitly **out of scope / corrections**:
- `common/api_commands.py` `_command_handler` (mccabe 38) — **already refactored** to `_COMMAND_DISPATCH`; the flag was stale (described pre-refactor `common/common.py`). No work.
- ~25 `reportPossiblyUnbound` flags in `api_commands.py` / `file_mgmt/common.py` — all guarded false positives. Optional trivial suppression only; not in this batch.

## Tasks

### Task 1 — `_update_dash_objects` → per-widget updaters ⭐ flagship
**File:** `display/_base_flex.py:609–973` (~365 lines, mccabe 65)
**Shape:** ~20 sequential, independent "if widget in dash_map, recompute its `object_data`, push it" blocks. **Not** a switch — no interleaved control flow.
**Refactor:** Extract one `_update_<widget>(self)` method per block (mode_bar, control_panel, button_row, primary_gauge, header_bar, food gauges, probe cards, output icons, system_card, timer, cook_time, lid indicator/alert/button, p_mode, smoke_plus, hopper, hopper_vertical, duty pills). `_update_dash_objects` becomes a short sequence of calls (or iterates an updater registry). This **extends an existing pattern** — `_button_row_for_mode` (484), `_duty_pills` (525), `_timer_seconds_and_label` (548), `_cook_time_data` (569) are already extracted pure helpers.
**Safety net:** `tests/ui/test_base_flex_dash_update.py` (28 tests) drives `_configure_dash`/`_build_objects`/`_build_dash_map`/`_update_dash_objects` end-to-end. No new tests required; run before/after and confirm identical pass.
**Risk:** low. Self-contained, no cross-file coupling.

### Task 2 — `_post_app_data_pellets` → second-level dispatch dict
**File:** `blueprints/mobile/socket_io.py:493–604` (mccabe 26)
**Shape:** flat `if type=="…"/elif` over 9 sub-actions (load_profile, hopper_check, edit_brands, edit_woods, add_profile, edit_profile, delete_profile, delete_log, else), most with an inner key-present switch. The *outer* `_POST_APP_DATA_DISPATCH["pellets_action"]` already routes here; this inner `type` switch is the untouched next layer.
**Refactor:** Build `_PELLETS_DISPATCH = {type: handler}` with `_pellets_load_profile`, `_pellets_hopper_check`, `_pellets_edit_brands`, `_pellets_edit_woods`, `_pellets_add_profile`, `_pellets_edit_profile`, `_pellets_delete_profile`, `_pellets_delete_log`, each `(pelletdb, action_data) -> response`. `_post_app_data_pellets` reads the db once, looks up handler, returns else-error for unknown. Mirrors the existing top-level dispatch exactly.
**Watch:** lines 569 & 580 use bare `if type ==` (not `elif`) — preserve that exact truthiness when splitting (they are independent checks today).
**Safety net:** `tests/web/test_socketio_app_data.py` — **full** coverage, every branch + inner sub-branch has a dedicated test. Lowest-risk route task; **do first among routes.**

### Task 3 — `_settings_cycle` → extract controller-config + coerce helper
**File:** `blueprints/settings/routes.py:332–413` (mccabe 29)
**Shape:** mostly flat field-mapping (335–385) plus one genuinely nested `selectController` block (386–408) with an inner double loop and a 5-way `option_type` chain (float/int/bool/numlist/else).
**Refactor:** Extract `_apply_controller_config(response, settings, controller)` for 386–407, and inside it `_coerce_option_value(option_type, value)` collapsing the type chain (small dict or `match`). The controller-config extraction alone drops complexity below threshold.
**Safety net:** good — `test_cycle_via_real_ui`, `test_cycle_blank_pmode_is_skipped_not_crashed`, `test_cycle_controller_config_option_types` (exercises int/float/bool/string arms). Safest route task.

### Task 4 — Fix `read_probe_status` missing-else 🐛 real bug
**File:** `common/datastore_accessors.py:671–693`
**Bug:** `if probe["type"]=="Primary"/elif "Food"/elif "Aux"` assigns `section` with **no else/default**. A probe with any other `type` either raises `UnboundLocalError` (first probe) or silently writes to the **previous probe's** `section` bucket (data corruption).
**Fix:** add an explicit `else` — skip the probe (`continue`) or route to a default/`AUX` bucket, matching intended behavior. Confirm which by checking how `probe_status` buckets are consumed downstream; prefer the least-surprising (likely `continue` + a logged warning, since unknown types shouldn't silently land in AUX). Add a small unit test covering an unexpected `type`.
**Risk:** low, but it *is* a behavior change on the error path — call it out in the commit and confirm the chosen default with the maintainer if ambiguous.

## Execution notes
- One task per commit, on the current branch (multiple concurrent Claude sessions share it — scope reviews to exact SHAs).
- Run `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/` green before and after each task; the touched-file suites are the tight loop (`tests/ui/test_base_flex_dash_update.py`, `tests/web/test_socketio_app_data.py`, `tests/web/test_page_settings.py`).
- `uvx ruff format` changed files before each commit; re-run any touched `tests/web/*` [chromium] tests in the main checkout (agent worktrees skip them).
- Verify complexity actually dropped: `uvx ruff check --select C901 --config 'lint.mccabe.max-complexity=12'` should no longer list `_update_dash_objects`, `_post_app_data_pellets`, `_settings_cycle`.

## Deferred (follow-up batch, not now)
- `_settings_notify` (mccabe 42) — easy per-service extraction but only ifttt/mqtt characterized; **add per-service tests first**.
- `_probeconfig_ports_add_edit_probe` (mccabe 32) — genuine domain logic; extract `_build_new_probe`/`_validate_probe`/`_reposition_virtual_probe`/`_reposition_input_probe` (the two reposition loops are the value; already pinned by ordering-invariant tests).
- `_base_fixed._display_current` (279 lines, triple-duplicated F/C scaling) and `_menu_display` (378, stateful — riskiest).
- `flexobject.py` gauge/scaling mixin dedup (GaugeCircle/GaugeCompact/GaugeEmber).
