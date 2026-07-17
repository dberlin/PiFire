# Tier 1 & Tier 2 Refactors — Design

**Date:** 2026-07-16
**Status:** Approved (design); implementation plan to follow.
**Scope:** Nine behavior-preserving refactors identified in a codebase-wide audit of PiFire, delivered as one phased design with **one branch + one PR per phase**.

## Goal

Remove the largest sources of duplication and the worst god-objects in the codebase, without changing runtime behavior. Each phase is an independent, independently-mergeable refactor guarded by the existing test suite (134 test files, including controller/mode golden tests and per-widget UI tests) plus phase-specific characterization tests where none exist today.

**Non-goals:** No feature changes. No new hardware support. No behavior changes except two deliberate, called-out exceptions (the Meater driver deletion and the `is_not_blank` crash-proofing), each gated behind characterization tests.

## Cross-cutting principles (apply to every phase)

1. **Behavior-preserving.** Pure refactor. The only intentional behavior changes are the Meater module deletion (Phase E) and the `is_not_blank` empty-value handling (Phase A), both explicitly scoped and test-gated below.
2. **Golden/characterization tests are the contract.** Where a snapshot harness already exists (controller loop, modes, work-cycle e2e), correctness means **byte-identical** golden output before and after. Where the code being touched has no snapshot today — notably the legacy fixed displays and the settings routes — the phase **begins** by adding a characterization test (committed separately) that captures current behavior, then refactors under it.
3. **One branch + one PR per phase**, each branched fresh from `main`, each independently reviewable and revertible.
4. **Serena for all edits** (symbolic tools) per project working agreement.
5. **Full suite green + `/verify` on the affected runtime surface** before a phase is "done." Never claim done on assertion alone.
6. **Public API stable within a phase** unless the phase's explicit goal is to change it (only Phase A rewrites import sites; only Phase E removes a module).

## Sequencing & dependencies

```
A. common.py hard split ............ FIRST, merge fast (wide import blast radius)
D. blueprints service layer ........ after A (new service fns land in the new common/ modules)
B. display fixed-base merge ........ needs new snapshot harness first (independent otherwise)
C. display driver matrix + encoder mixin ... after B (drivers point at the merged base)
E. Meater dedup + delete ........... independent
F. ControlMode.run() split ......... independent (golden-test protected)
G. grillplat mixin adoption ........ independent
H. notifications event table ....... independent
I. PID base class .................. independent
```

Only two hard ordering constraints: **A before D**, **B before C**. Everything else may proceed in any order or in parallel. **A is landed and merged first and fast** so no other phase must rebase across the codebase-wide import rewrite; long-lived divergence from that change is the single biggest risk in the effort.

---

## Phase A — Split `common/common.py` (3,351 lines) + in-file simplifications

**Delivered in two commits on one branch** (`refactor/common-split`), the low-risk in-file work first so the package split rebases onto simpler code.

### A1 — In-file simplifications (low risk)
- **`process_command()` (`common/common.py:2522-3185`, 666 lines):** replace the nested `if action==… / elif arglist[0]==…` ladders with a dispatch table mapping `(action, subcommand)` → small handler functions (`_cmd_get_temp`, `_cmd_set_mode`, `_cmd_set_manual`, …). Collapse the 4 near-identical manual-output branches (power/igniter/fan/auger) into one `_manual_toggle(control, pin_name, arglist)`. Move the large inline route docstrings out of the function body.
- **~14 blob read/write accessor pairs** (`read_control`/`write_control` and siblings; 30 `datastore.get_blob/set_blob` sites): introduce generic `_read_json_blob(key, default_factory)` / `_write_json_blob(key, value)` and define each public accessor as a thin wrapper from a `(name, key, default_factory)` table. Public names unchanged.
- **5 copies of file-read-with-retry** (`read_settings_file`, `read_pellet_db_file`, `read_wizard`, `read_updater_manifest`, `read_generic_json`): route all through one `_load_json_file(filename, default, retry_count)`; the recursive ValueError-retry lives in one place.
- **Python-2 `except IOError, OSError:` syntax (6 occurrences):** change to `except (IOError, OSError):`.
- **`is_not_blank` fix — see box below.** Lives in `common/app.py`, folded into this phase.
- **Wizard/updater install-status duplication** (`get_/set_wizard_install_status` vs `get_/set_updater_install_status`, byte-identical but key prefix): parameterize into `_get_install_status(prefix)` / `_set_install_status(prefix, …)` with named wrappers.

### A2 — Package split + import rewrite (wide blast radius)
Split `common/common.py` into a package, moving (not rewriting) symbols:
- `common/datastore_accessors.py` — the blob read/write accessors from A1
- `common/system.py` — reboot/shutdown/restart, wifi-quality probing, os_info
- `common/defaults.py` — the `default_*` builders (~760 lines of config literals)
- `common/api_commands.py` — `process_command` + its handlers
- `common/settings_migration.py` — `upgrade_settings`/`downgrade_settings`/`read_settings_file`

**Hard split:** rewrite every `from common.common import X` across the codebase to import from the new module (dozens of sites in `blueprints/`, `controller/`, `notify/`, `probes/`, top-level scripts). No re-export facade. Verified by an import-smoke test + full suite.

> ### `is_not_blank` — investigated, not assumed
> `common/app.py:271` — `def is_not_blank(response, setting): return setting in response and setting != ""`.
> `setting` is the **key name**, so `setting != ""` is always true → the function currently means "is this key present." **All 38 callers live in `blueprints/settings/routes.py`** and each passes a constant string literal. Classification of what each branch does with the value: **32 `int(...)`, 3 `float(...)`, 2 other numeric (`min(...)` / select), 0 raw-string saves.** Therefore an empty submission today hits `int("")` → **500 crash**, never a saved value; **no caller depends on saving an intentional empty string.** Changing the helper to `setting in response and response.get(setting, "") != ""` converts that latent crash into "keep prior value" — the original intent. **Gate:** first add characterization tests for all 38 branches across three inputs (key absent / key present with value / key present empty) capturing today's behavior, then apply the fix and confirm only the empty-input case changes (crash → skip).

**Verification:** `tests/unit/common`, `tests/unit/datastore`, `tests/web` render tests; import-smoke across the repo; `/verify` on the web app + a controller boot.
**Risk:** A1 low; A2 medium (wide but mechanical). **Rollback:** revert the branch; facade-free split means the revert is a clean import restoration.

---

## Phase B — Merge the three legacy fixed-display bases

`display/base_240x240.py`, `base_240x320.py`, `base_320x480.py` (1436–1438 lines each) define one `DisplayBase` with the same 38-method structure. `diff base_240x320 base_320x480` = **10 lines of 1438** (99.3% identical); `base_240x240` ≈ 89% identical. `base_320x480` already contains `if self.WIDTH == 240:` branches — it is already a partial multi-resolution file — and the three `_display_loop` implementations have **silently drifted** (240x240 still carries monitor/delay logic the others dropped).

**Prerequisite (committed first):** these legacy bases have **no** characterization coverage today (existing UI tests cover flex/qtquick/dsi only). Add a snapshot harness that renders `DisplayBase` canvases to PIL images for each of the three resolutions across a representative set of control/status states, committed as the baseline.

**Refactor:** collapse into one `display/base_fixed.py` `DisplayBase` parameterized by `(WIDTH, HEIGHT)`, with resolution-specific icon coordinates and font sizes moved into a per-resolution layout dict (replacing inline `if self.WIDTH == …` ladders). Reconcile the drifted `_display_loop` to a single deliberate implementation (documented in the PR). Icon/gauge/rounded-rect/text primitives (identical across the three today) collapse automatically.

**Verification:** the new snapshot harness must produce pixel-identical output for 320x480 and 240x320; for 240x240 any intentional `_display_loop` reconciliation is called out and re-baselined explicitly. **Risk:** medium (three layouts to preserve). **Rollback:** revert branch.

---

## Phase C — Collapse driver clone matrix + extract encoder mixin

Depends on B (drivers subclass the merged base).
- **Luma driver matrix:** `ili9341{,b,e,em}` ↔ `ili9488{,b,e,em}` differ by **exactly 10 lines** each (panel class + width/height + base import). Collapse into one parameterized Luma driver taking `(panel_class, width, height)`; the 8 files become thin registrations.
- **`EncoderInputMixin`:** `_init_input` + `_click/_inc/_dec_callback` + `_event_detect` (the pyky040 rotary handling incl. the 0.3s enter-cancels-updown debounce) is **md5-identical across 6+ drivers** (`ili9341e/em`, `ili9488e/em`, `st7789e`, and a second identical variant in `st7789_240x320e`/`st7789v_240x320e`). Extract a mixin (or two variants); "e"/"em" drivers inherit it and keep only `_init_display_device`/`_display_canvas`.

**Verification:** existing `tests/ui/test_display_launch.py` + module-load tests confirm every registered driver still imports and instantiates against fakes; manifest test confirms the wizard's driver list is unchanged. **Risk:** low (differences are constants / md5-identical). **Rollback:** revert branch.

---

## Phase D — Blueprints service layer + dispatch maps

Depends on A (service functions land in the new `common/` modules). **Dedup first (low risk), then decompose.**

**D1 — Extract shared services** into `common/app.py` (or a new `common/services/`):
- **Probe-config update (3 near-identical copies):** `settings/routes.py:80-122`, `mobile/socket_io.py:778-822`, and the `probeconfig/routes.py` add/edit path → one `update_probe_config(settings, control, probe_dto)`; callers adapt their DTO.
- **System-info + os-info duplication:** `admin/routes.py:267-338` vs `socket_io.py:894-964`, and `_get_os_info` defined twice (`admin/routes.py:378-413`, `socket_io.py:967-999`) → `get_system_info()` / `get_os_info_normalized()` in `common/system.py`.
- **Save-and-flag + render-page tails** (repeated ~40×): `save_settings_and_flag_update(settings, **flags)` and a `render_page(template, **ctx)` helper (or Flask `context_processor`) that injects `page_theme`/`grill_name`/`settings`.
- **Response envelope:** one `api_response(result, message=None, data=None, status=201)` shared by socket_io (`_response`, 67 uses) and the REST WLED endpoints.

**D2 — Decompose the god routes** over that service layer, behind `{action: handler}` (and `{(action, type): handler}`) dispatch maps: `settings_page` (683 lines, 19 branches), `_post_app_data`/`_get_app_data` (315 lines), `probeconfig_page` (379 lines), `admin_page` (365 lines), `cookfile_update`.

**Prerequisite:** extend `tests/web` with per-action characterization tests (POST each `action` with representative form data, assert resulting settings/control/blob writes) before decomposing. **Verification:** `tests/web` + `/verify` driving the settings and admin pages in a browser. **Risk:** D1 low, D2 medium (subtle virtual-port ordering in probeconfig — tests first). **Rollback:** revert branch.

---

## Phase E — Meater: shared core, delete `bt_meater.py`

`probes/bt_meater.py` (493) and `bt_meater_exp.py` (688) duplicate the entire Meater protocol byte-for-byte (`toCelsius`, `get_short`, `ambient_correction`, `toFahrenheit*`, `convert_to_temperatures`, and the `Meater_Device`/`ReadProbes` plumbing); only the BLE transport differs (`bluepy` vs `simplepyble`). `common/common.py:1402` already migrates users `bt_meater → bt_meater_exp`.

**Decision (approved): finish the transition.** Make `bt_meater_exp` the sole implementation and **delete `bt_meater.py`**. Extract the shared protocol/math/`Meater_Device`/`ReadProbes` into a `probes/meater_common.py` that `bt_meater_exp` consumes (leaving it only its simplepyble transport handler). Keep the settings migration. Remove `bt_meater` from the wizard manifest / driver registry.

**Verification:** unit tests on `convert_to_temperatures`/`ambient_correction` against known byte sequences (no hardware in CI); manifest + `display_launch`/probe-registry load tests confirm no dangling reference to the deleted module; grep for `bt_meater` residue. **Risk:** medium — deletes the bluepy path. Mitigation: the extracted math is independently unit-tested; the migration already steers users to `_exp`. **Rollback:** revert branch (restores the module and manifest entry).

---

## Phase F — Split `ControlMode.run()` (414 lines)

`controller/runtime/modes/base.py:229-643`. The method already carries `# ---- SENSE / SAFETY / ACT / PUBLISH ----` banners marking self-contained blocks. Extract private helpers preserving exact control-write ordering:
- `_setup_recipe_triggers(control)` (256-285)
- `_process_control_flags(...)` (settings/distance/hopper/switch, 358-409)
- `_apply_manual_overrides(control, now, current_output_status)` (413-474)
- `_build_status_data(control, pelletdb, …) -> dict` (545-586)
- `_handle_recipe_end(control)` (599-611)

`run()` shrinks to a ~120-line skeleton.

**Verification:** `tests/characterization/test_controller_loop_golden.py`, `test_modes_golden.py`, `tests/e2e/test_work_cycle_e2e.py` must produce **byte-identical** golden output. **Risk:** medium (core loop) but the golden harness makes drift immediately visible. **Rollback:** revert branch.

---

## Phase G — grillplat: adopt `SystemCommandsMixin`

`grillplat/system_commands.py:SystemCommandsMixin` already exists and is used by `x86_numato.py` and `ft232h_relay.py`. `raspberry_pi_all.py:50` and `prototype.py:31` declare a bare `class GrillPlatform:` and re-implement ~170/166 lines of the same system-info methods inline.

**Refactor:** both extend `SystemCommandsMixin`. Delete the ~6 identical methods (`supported_commands`, `check_throttled`, `check_alive`, `scan_bluetooth`, `os_info`, `network_info`); **keep the two genuinely Pi-specific overrides** in `raspberry_pi_all` (`check_cpu_temp` via `vcgencmd`, `hardware_info` via `/proc/cpuinfo`). `prototype` inherits wholesale.

**Verification:** `tests/unit/platform` against fakes; confirm the mixin's portable psutil versions match prior output for the shared methods. **Risk:** low-medium (keep the 2 overrides). **Rollback:** revert branch.

---

## Phase H — notifications: data-drive the event map

`notify/notifications.py`.
- **`send_notifications` (167-308, ~140-line if/elif):** replace the event→message control flow with an `EVENTS` dict keyed by event substring → message template `(title, body, channel, query_args)`.
- **Logger boilerplate (~7 copies):** extract module-level `_event_logger()`.
- **Apprise senders:** collapse `_send_pushover_notification`/`_send_pushbullet_notification` into one `_send_apprise_url(settings, urls, title, body, service_name)`.
- **Follow-on note (not in this phase):** once `EVENTS` exists, `wled_handler._notify_traditional` should consume it rather than re-switching on the same strings — tracked separately.

**Verification:** unit tests asserting each event string maps to the same `(title, body, channel)` tuple the old branches produced. **Risk:** low. **Rollback:** revert branch.

---

## Phase I — `PIDControllerBase`

`controller/pid.py`, `pid_clamping.py`, `pid_clamping_percent_pb.py`, `pid_ac.py`, `pid_parallel.py`, `pid_sp.py` all subclass `controller/base.py:ControllerBase` and duplicate the same 5-method scaffolding (`_calculate_gains` with identical `kp=-1/pb; ki=kp/ti; kd=kp*td`, `set_target`, `set_gains`, `get_k`, `set_config`, and `function_list` registration). Only `update()` legitimately differs.

**Refactor:** add `PIDControllerBase(ControllerBase)` owning the shared surface; each variant keeps only its `update()` and any gain-clamp/`inter_max` specifics (parameterized). All variants remain user-selectable via `controller/controllers.json`.

**Verification:** existing controller unit/characterization tests; assert each variant's `update()` output is unchanged for a fixed input series. **Risk:** low-medium. **Rollback:** revert branch.

---

## Open follow-ons (explicitly out of scope here)
- Tier 3 items (probe `_store_reading` helper, grillplat fan-ramp base, `wizard.run_wizard` split, `board-config` table, Smoke/Startup mixin, `upgrade_settings` step list, `controller/update_ml.py` dead-code decision).
- `wled_handler` consuming the Phase H `EVENTS` table.

## Success criteria
Each phase merges as its own green PR with no behavior change (per its golden/characterization gate), and the audited duplication/god-objects are measurably reduced: ~2,900 lines from the display base merge, ~600 from the driver/encoder work, ~300+ from the Meater dedup, the 666- and 414-line god functions decomposed, and `common.py` split into a cohesive package.
