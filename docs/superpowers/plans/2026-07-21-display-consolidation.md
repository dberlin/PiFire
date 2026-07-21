# Display Driver Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Consolidate the DSI/flex display drivers: name the resolution-agnostic base honestly (`dsi_base`), delete the redundant `protoflex` module while preserving its unique 320×240 layout, make the fixed-family base class name unambiguous, and hoist duplicated pygame/flex helpers into a shared location — all behavior-preserving.

**Architecture:** PiFire display drivers are filename-keyed in `wizard/wizard_manifest.json` → `display/<key>.py`. There are two families: FIXED (`base_fixed.py` `DisplayBase` ← per-resolution `base_<res>.py` `DisplayBase(_Base)` ← concrete `Display(mixins, DisplayBase)`) and FLEX (`base_flex.py` `DisplayBase` ← concrete `Display(DisplayBase)`). The DSI drivers are flex: `dsi_800x480t.py` is a fully resolution-agnostic pygame/touch engine (reads all dimensions from its JSON layout), and `dsi_1024x600t/768t`, `dsi_1280x720t` are already 16-line stubs re-exporting its `Display`. `protoflex.py` is a ~95% duplicate of `dsi_800x480t.py`; `protoflex_800x480.json` is byte-identical to `dsi_800x480t.json` (name aside); only `protoflex_320x240.json` is a distinct, hand-tuned compact layout.

**Tech Stack:** Python 3.14, pygame, PIL, pytest, uv, ruff.

## Global Constraints

- **Behavior-preserving.** No display renders differently; the manifest still resolves every non-deleted display key to a module exporting `Display`. The registry is filename-based — a manifest key `X` requires `display/X.py` exporting `Display`.
- **Test runner (always):** `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest <target>`. Coverage via `--cov=<module> --cov-report=term-missing`. Do not start the real `_display_loop` (infinite `while True`) or let `os.system`/reboot fire in tests — reuse the existing overlay/guard harness in `tests/ui/test_fixed_drivers_methods.py` / `test_pygame_qt_drivers.py`.
- **The coverage tests characterize these exact modules by name** (`tests/ui/test_pygame_qt_drivers.py`, `test_fixed_drivers_methods.py`, `test_dsi_module.py`, `test_base_flex_dash_update.py`, `test_display_launch.py`, `tests/conftest.py`). Every rename/deletion updates the referencing tests in the SAME task — never leave a dangling import or a test asserting a deleted module.
- **ruff format before every commit:** `uvx ruff format <changed files>`.
- **Commit with `git commit -F <file>`** (zsh eats backticks). End every commit body with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- After each task, the full suite (`QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest -q`) must stay green (baseline: 2459 passed) and the touched display modules must keep coverage ≥50%.
- Branch: `refactor/display-consolidation` (already created, holds the wizard-note commit). Do NOT switch branches or create worktrees.

---

### Task 1: Make the fixed-family base class name unambiguous

**Problem:** `base_fixed.py` defines `class DisplayBase`, and each per-resolution `base_<res>.py` ALSO defines `class DisplayBase(_Base)` where `_Base = the base_fixed class` (imported `as _Base`). Same name at two levels; only the import alias disambiguates.

**Files:**
- Modify: `display/base_fixed.py` (rename `class DisplayBase` → `class _DisplayBase`)
- Modify: `display/base_240x240.py`, `display/base_240x320.py`, `display/base_320x480.py` (import line)
- Test: existing display tests (`tests/ui/`) must stay green

**Interfaces produced:** `display.base_fixed._DisplayBase` (the fixed-family core base).

- [ ] **Step 1: Confirm the current importers.** Run `grep -rn "base_fixed import\|base_fixed.DisplayBase" display/ tests/`. Expected: only `base_240x240.py`, `base_240x320.py`, `base_320x480.py` import it (`from display.base_fixed import DisplayBase as _Base`), and no test imports `base_fixed.DisplayBase` directly. If any OTHER importer exists, update it too.
- [ ] **Step 2: Rename the class.** In `display/base_fixed.py`, rename `class DisplayBase:` → `class _DisplayBase:`. Grep the file for any internal self-reference to the old name (e.g. in `__init_subclass__`, factory helpers, `super()` is unaffected) and update.
- [ ] **Step 3: Update the three importers.** In each `base_<res>.py`, change `from display.base_fixed import DisplayBase as _Base` → `from display.base_fixed import _DisplayBase as _Base`. Leave the local `class DisplayBase(_Base)` as-is (that is the per-resolution base, correctly named).
- [ ] **Step 4: Verify.** Run `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ui -q` — all green, no import errors. Then a quick load check: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run python -c "import display.ssd1306b, display.ili9341, display.st7789p"` succeeds.
- [ ] **Step 5: ruff format + commit** (subject: `refactor(display): rename fixed-family core base to _DisplayBase for clarity`).

---

### Task 2: Rename `dsi_800x480t.py` → `dsi_base.py`; make `dsi_800x480t` a stub

**Files:**
- Rename: `display/dsi_800x480t.py` → `display/dsi_base.py` (use `git mv`)
- Create: new `display/dsi_800x480t.py` (16-line stub re-exporting `Display` from `dsi_base`, matching the existing `dsi_1024x768t.py` template)
- Modify: `display/dsi_1024x600t.py`, `display/dsi_1024x768t.py`, `display/dsi_1280x720t.py` (repoint `from display.dsi_800x480t import Display` → `from display.dsi_base import Display`)
- Modify tests that import the module for internals: `tests/ui/test_pygame_qt_drivers.py` (`import display.dsi_800x480t as dsi_mod` — the tests monkeypatch module internals like `multiprocessing`; repoint to `display.dsi_base`), `tests/ui/test_dsi_module.py`, `tests/conftest.py` (`DSI_LAYOUT_SRC` path — unchanged, it points at `dsi_800x480t.json` which still exists), `tests/ui/test_base_flex_dash_update.py`, `tests/ui/test_display_launch.py` — inspect each; where a test imports `display.dsi_800x480t` only to get `Display`, it can stay (the stub re-exports it), but where it patches module-level names (multiprocessing, os, Path, DummyBacklight), it MUST import `display.dsi_base`.
- Unchanged: `display/dsi_800x480t.json` keeps its name (paired with the `dsi_800x480t` manifest key).

**Interfaces produced:** `display.dsi_base.Display` (the resolution-agnostic flex/pygame engine). `display.dsi_800x480t.Display` remains importable (re-export).

- [ ] **Step 1:** `git mv display/dsi_800x480t.py display/dsi_base.py`. Update its module docstring to describe it as the resolution-agnostic DSI/pygame flex base (not "800x480").
- [ ] **Step 2:** Create `display/dsi_800x480t.py` as a stub identical in shape to `display/dsi_1024x768t.py`: a docstring noting the 800×480 behavior comes from `display/dsi_800x480t.json`, then `from display.dsi_base import Display  # noqa: F401  # public re-export`.
- [ ] **Step 3:** Repoint the three existing stubs (`dsi_1024x600t.py`, `dsi_1024x768t.py`, `dsi_1280x720t.py`) to `from display.dsi_base import Display`.
- [ ] **Step 4:** Update the referencing tests. For each of `tests/ui/test_pygame_qt_drivers.py`, `tests/ui/test_dsi_module.py`, `tests/ui/test_base_flex_dash_update.py`, `tests/ui/test_display_launch.py`: read how it uses `dsi_800x480t`. If it patches/reads module-level internals, change the import to `display.dsi_base`. If it only constructs `Display`, either import works — prefer `display.dsi_base` for the engine tests so intent is clear. Keep `tests/conftest.py`'s `DSI_LAYOUT_SRC = .../dsi_800x480t.json` as-is.
- [ ] **Step 5: Verify.** `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ui -q` green; coverage of `display.dsi_base` ≥50% (the tests now attribute the engine's coverage to `dsi_base`). Load check: `python -c "import display.dsi_800x480t, display.dsi_base, display.dsi_1024x768t; assert display.dsi_800x480t.Display is display.dsi_base.Display"`.
- [ ] **Step 6: ruff format + commit** (subject: `refactor(display): rename dsi_800x480t engine to dsi_base; 800x480 becomes a stub`).

---

### Task 3: Delete `protoflex`; preserve its 320×240 layout as `dsi_320x240t`

**Rationale:** `protoflex.py` duplicates the `dsi_base` engine; `protoflex_800x480.json` duplicates `dsi_800x480t.json`. Only `protoflex_320x240.json` (compact 320×240, `max_food_probes` 2, 54 widgets) is unique — re-home it onto `dsi_base`.

**Files:**
- Delete: `display/protoflex.py`, `display/protoflex_800x480.json`
- Rename: `display/protoflex_320x240.json` → `display/dsi_320x240t.json` (`git mv`), and update its `metadata.name` field `"protoflex_320x240"` → `"dsi_320x240t"`
- Create: `display/dsi_320x240t.py` (stub re-exporting `Display` from `dsi_base`, same template as the other dsi stubs; docstring notes 320×240 behavior comes from `dsi_320x240t.json`)
- Modify: `wizard/wizard_manifest.json` — remove the `protoflex` display entry (lines ~4704-4742); add a `dsi_320x240t` entry (copy the `dsi_800x480t` entry shape: `friendly_name` e.g. "DSI/Pygame Flex Display 320x240 w/Touch", `filename: dsi_320x240t`, `display_data_filename`/config pointing at `./display/dsi_320x240t.json`, `input_types_supported` copied from the old protoflex entry `["button","encoder","touch"]`). Keep the manifest valid JSON.
- Modify: `settings.json` — the `display.config.protoflex` stub (`display_data_filename: ./display/protoflex_320x240.json`, inputs button/encoder/touch): replace the key `protoflex` with `dsi_320x240t` and repoint its `display_data_filename` to `./display/dsi_320x240t.json`. (`display.selected` is `none`, so no active selection changes.)
- Modify tests referencing protoflex: `tests/ui/test_pygame_qt_drivers.py`, `tests/ui/test_fixed_drivers_methods.py` — remove/redirect any construction or comment that names `protoflex`. If a test constructs the protoflex `Display`, either delete that test (its coverage is now redundant with `dsi_base`) or repoint it to `dsi_base` with the 320×240 JSON. Do NOT leave a test importing `display.protoflex`.

**Interfaces produced:** `display.dsi_320x240t.Display` (= `dsi_base.Display`); manifest key `dsi_320x240t`.

- [ ] **Step 1:** Confirm protoflex is not the active/selected display anywhere: `grep -n '"selected"' settings.json` shows `display.selected == "none"`; `grep -rn protoflex` shows only its own files, the manifest entry, the settings config stub, and the two test files. If anything else references it, surface it before deleting.
- [ ] **Step 2:** `git mv display/protoflex_320x240.json display/dsi_320x240t.json`; edit its `metadata.name` to `"dsi_320x240t"`. `git rm display/protoflex.py display/protoflex_800x480.json`.
- [ ] **Step 3:** Create `display/dsi_320x240t.py` stub (re-export `Display` from `dsi_base`).
- [ ] **Step 4:** Edit `wizard/wizard_manifest.json`: delete the `protoflex` block; add the `dsi_320x240t` block. Validate with `python -c "import json; json.load(open('wizard/wizard_manifest.json'))"`.
- [ ] **Step 5:** Edit `settings.json`: rename the `display.config.protoflex` stub to `dsi_320x240t` with the updated `display_data_filename`. Validate `python -c "import json; json.load(open('settings.json'))"`.
- [ ] **Step 6:** Update the two test files: remove protoflex construction/asserts; if useful, add/keep a construction of `dsi_base.Display` with `./display/dsi_320x240t.json` to characterize the 320×240 path. No dangling `import display.protoflex`.
- [ ] **Step 7: Verify.** Full suite green; `python -c "import display.dsi_320x240t; assert display.dsi_320x240t.Display is display.dsi_base.Display"`; `grep -rn protoflex .` returns nothing outside `.git`/docs/plan. Confirm the manifest still lists a 320×240 flex option.
- [ ] **Step 8: ruff format + commit** (subject: `refactor(display): remove redundant protoflex; preserve 320x240 layout as dsi_320x240t`).

---

### Task 4: Hoist shared pygame/flex helpers into `base_flex.py`

**Rationale:** `dsi_base.py` (and to a lesser extent `ili9341f.py`, `qtquick_flex.py`) still carry duplicable helpers — notably the verbatim `DummyBacklight` class, plus pygame surface init and the PIL-canvas→pygame-surface blit and rotation/touch-coordinate transforms.

**Files:**
- Modify: `display/base_flex.py` (add shared helpers), `display/dsi_base.py` (use them), and any other flex driver that had a verbatim copy (`ili9341f.py`).
- Test: `tests/ui/` display tests stay green.

- [ ] **Step 1:** Identify the truly-shared, behavior-identical pieces. Start with `DummyBacklight` (byte-identical across drivers). Grep `grep -rn "class DummyBacklight" display/`. Move ONE canonical `DummyBacklight` into `display/base_flex.py` (or a small `display/_flex_helpers.py` if `base_flex.py` is already large) and import it where it was duplicated.
- [ ] **Step 2:** Only after DummyBacklight lands and is green, evaluate the canvas→surface blit and rotation/touch transforms. Extract a helper ONLY where the logic is genuinely identical across ≥2 drivers and the extraction does not change behavior (the rotation transform lives only in `dsi_base` now that protoflex is gone — do NOT invent a shared abstraction for a single caller; YAGNI). If nothing else is duplicated ≥2×, stop after DummyBacklight and say so in the report.
- [ ] **Step 3: Verify.** Full suite green; coverage of the touched flex modules unchanged or higher; no display renders differently.
- [ ] **Step 4: ruff format + commit** (subject: `refactor(display): hoist shared flex helpers (DummyBacklight, …) into base_flex`).

---

## Self-Review

- **Coverage of the ask:** #1 dsi_base rename → Task 2; #2 protoflex removal → Task 3 (with 320×240 preserved per the layout analysis); #3 base-class clarity → Task 1 (fixed family, the only collision); #4 shared helpers → Task 4. Wizard note → already committed on this branch.
- **Filename-registry safety:** every deleted manifest key loses its module (protoflex), every kept/added key (`dsi_800x480t`, `dsi_320x240t`, `dsi_1024x*`, `dsi_1280x720t`) has a module exporting `Display`. Verified by load checks in each task.
- **Test coupling:** each rename/deletion updates its referencing tests in the same task (Global Constraints + explicit per-task steps).
- **YAGNI guard:** Task 4 Step 2 explicitly forbids extracting a "shared" helper that now has a single caller.
