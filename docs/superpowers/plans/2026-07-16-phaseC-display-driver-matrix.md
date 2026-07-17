# Phase C — Collapse the Driver Clone Matrix + Extract Input Mixins — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the massive duplication across the 16 fixed-base display drivers by extracting the byte-identical rotary-encoder input, 3-button input, and Luma-panel display logic into shared mixins, turning each driver file into a thin (~5–15 line) subclass — with no runtime behavior change, guarded by a NEW input-behavior characterization suite written first.

**Architecture:** The 16 drivers (Phase B left them subclassing `DisplayBase` via the three resolution shims) fall into clean duplication groups: 9 Luma drivers share `_init_display_device`/`_display_clear`/`_display_canvas` differing only by panel class + native dims; 5 drivers share a debounced pyky040 encoder input block (byte-identical); 2 share a simpler encoder block; 3 share a gpiozero button block. We extract `display/_luma_panel.py`, `display/_encoder_input.py`, `display/_button_input.py`, then rewrite each driver as `class Display(<InputMixin>, <PanelMixin>, DisplayBase): <a few class attrs>`. **File count and the wizard manifest are UNCHANGED** — the driver registry resolves by filename (`importlib.import_module(f"display.{name}")`), so collapsing files would break already-deployed `settings["modules"]["display"]` values; we reduce code, not files. **The three shims are KEPT** as resolution profiles (they carry `_NOMINAL_*`/`_SQUARE`/`min_transition_delay`), which sidesteps the Phase-B silent-default footgun entirely.

**Tech Stack:** Python 3.14, pytest, Serena for all symbolic edits, `pyky040`/`luma.lcd`/`gpiozero`/`ST7789`/`spidev` (all hard module-scope imports; tests use a `sys.modules` stub overlay).

## Global Constraints

- **Behavior-preserving. Zero runtime behavior change** — this phase moves code into mixins verbatim; it does not alter input handling, rendering, or device setup. Any change to the pyky040 debounce, event dispatch, or luma device construction is a defect.
- **Serena for ALL code edits** (`create_text_file`, `replace_symbol_body`, `replace_content`, `insert_*_symbol`). Call `mcp__serena__initial_instructions` first and activate the project. Never hand-edit code files blind. New test files may use plain Write.
- **Test command is ALWAYS** `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest <args> -q` from the repo root. Bare `python3 -m pytest` gives false failures; missing the offscreen vars HANGS. Exit 124 = hang; stop and report.
- **`uvx ruff format <changed files>` before every commit** (a pre-commit hook runs it too — re-stage/amend). `ruff check` is not a gate.
- **Commit via `git commit -F <file>`** (this zsh eats backticks in double-quoted args). End every message with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- **⚠️ REBOOT HAZARD:** `base_fixed._menu_display` calls `os.system("...sudo reboot...")`, and every driver's `_init_input`/`_init_display_device` starts a real non-daemon `threading.Thread`. ANY test that constructs a driver MUST stub `threading.Thread` AND neutralize `os.system` (mirror `tests/ui/test_fixed_base_drivers_load.py`). Three real reboot incidents in this repo.
- **File count and `wizard/wizard_manifest.json` are UNCHANGED.** All 16 driver filenames stay; each keeps a `class Display`. The manifest's `["modules"]["display"]` section must be byte-identical at the end.
- **Do NOT delete the three shims** (`base_240x240.py`/`base_240x320.py`/`base_320x480.py`). They are the resolution-profile layer carrying `_NOMINAL_WIDTH`/`_NOMINAL_HEIGHT`/`_SQUARE`/`min_transition_delay`; drivers keep importing `DisplayBase` from them, so the Phase-B silent-default footgun never fires. (A future phase may revisit shim deletion with a settings migration; that is explicitly out of scope here.)
- **Leave `except IOError, OSError:` / bare-except tuples alone** — valid PEP 758 on Python 3.14; ruff canonicalizes to it.
- **Baseline suite: 1154 passed** (state after Phase B merge). Report the new total after each task; no task may reduce the pre-existing count.
- **Trust the live code, not the spec.** The spec was wrong about this area 3× (see below). Every claim here was verified against live code at plan time; re-confirm anything that may have drifted.

## Verified facts this plan rests on (Serena-assisted read of live code @ 2dd7d1b)

- **Registry is pure filename convention.** `controller/runtime/devices.py:build_display()` reads `settings["modules"]["display"]` (a filename string like `"ili9341e"`), does `importlib.import_module(f"display.{name}")`, and calls `.Display(dev_pins=…, buttonslevel=…, rotation=…, units=…, config=…)`. No class registry. `wizard/wizard_manifest.json["modules"]["display"]` has one entry per driver keyed by filename (`filename` field == key). **Reducing file count breaks deployed installs → out of scope.**
- **The 16 fixed-base drivers, by group** (all `class Display(DisplayBase)`, `DisplayBase` imported from a shim):
  - **Luma backend (9):** `ili9341`, `ili9341b`, `ili9341e`, `ili9341em`, `ili9488`, `ili9488b`, `ili9488e`, `ili9488em` (import `from luma.lcd.device import ili9341|ili9488`), and `st7789e` (`from luma.lcd.device import st7789`).
  - **Pimoroni ST7789 backend (5):** `st7789_240x320`, `st7789_240x320b`, `st7789_240x320e`, `st7789v_240x320`, `st7789v_240x320e` (import `ST7789`; these OVERRIDE `self.WIDTH/HEIGHT` from `self.device.width/height` in `_init_display_device`).
  - **pygame backend (2):** `pygame_240x320`, `pygame_240x320b` (no luma; own overridden `_display_loop`).
- **Luma panel diff:** `ili9341X.py` ↔ `ili9488X.py` differ by **exactly 10 lines** at matched suffix (docstring, `luma.lcd.device` panel import, shim import `base_240x320`↔`base_320x480`, and the device ctor line `ili9341(…, width=320, height=240)`↔`ili9488(…, width=480, height=320)`). The luma device's `width`/`height` are the panel's NATIVE landscape dims = the shim's `_NOMINAL_WIDTH`/`_NOMINAL_HEIGHT`. `_display_clear` and `_display_canvas` are identical across all luma drivers.
- **Suffix semantics (spec was WRONG):** plain = no input; `b` = 3-button gpiozero input (adds ~64 lines); `e` = pyky040 rotary encoder (adds ~94 lines); `em` = `e` PLUS an explicit `spi=spidev.SpiDev()` passed to `luma…spi(...)` — a **max31865 SPI-conflict workaround** (NOT "encoder+menu"), a 2-line delta vs `e`.
- **Encoder input groups (verified by md5 of extracted method bodies):**
  - **Group A — debounced** (`_init_input` + `_click/_inc/_dec_callback` byte-identical, `st7789e` differs by one blank line only): `ili9341e`, `ili9341em`, `ili9488e`, `ili9488em`, `st7789e` (**5**). Contains the pyky040 setup (`Encoder(CLK,DT,SW).setup(scale_min=0,scale_max=100,step=1,inc_callback,dec_callback,sw_callback,polling_interval=200)` + a `threading.Thread(target=self.encoder.watch).start()`), and the "0.3s enter-cancels-updown" debounce using `last_direction`/`last_movement_time`/`enter_received`.
  - **Group B — trivial** (no debounce; `_inc/_dec_callback` just set `input_event` + bump counter): `st7789_240x320e`, `st7789v_240x320e` (**2**), byte-identical to each other.
  - **`_event_detect`:** byte-identical across 6 of the 7 encoder drivers; **`st7789e` diverges by 3 lines** (it also nulls `self.in_data`/`self.status_data` and sets `self.monitor_display = False`). This is a real per-driver difference the mixin must accommodate (parameterize or let `st7789e` override).
- **Button input (bonus, equally clean):** `_init_input` (gpiozero `Button`) + `_enter/_up/_down_callback` + `_event_detect` are byte-identical across `ili9341b`, `ili9488b`, `st7789_240x320b` (**3**).
- **Imports are hard + module-scope** (`from pyky040 import pyky040`, `from luma.lcd.device import …`, `import gpiozero`, etc.) — no `try/except`, no lazy import. A mixin module must keep the same contract; importing a driver on a box without the lib raises `ModuleNotFoundError` (caught at runtime by `devices.py`'s fallback to `display.none`).
- **NO existing test exercises the input logic.** `tests/ui/test_fixed_base_drivers_load.py` (the Phase B suite) imports+instantiates all 16 with `threading.Thread` stubbed and `os.system` neutralized, and asserts `Display` exists, `(WIDTH,HEIGHT)` per resolution, and `min_transition_delay` per resolution — but `.watch()` never runs and no callback/`_event_detect` is ever invoked. **The extraction has ZERO behavioral regression coverage until Task 1 adds it.**

---

### Task 1: Characterize the input behavior (the prerequisite net)

Nothing tests the encoder/button callback + debounce + `_event_detect` logic today. Before extracting it into mixins, pin its observable behavior with unit tests that drive the callbacks directly (no hardware, no threads).

**Files:**
- Create: `tests/ui/test_driver_input_behavior.py`
- Reference (do not modify): `tests/ui/test_fixed_base_drivers_load.py` (mirror its `sys.modules` stub overlay + `threading.Thread`/`os.system` neutralization).

**Interfaces:**
- Produces: characterization of, per input group, the effect of calling the callbacks and `_event_detect` on the instance's `input_event`/`input_counter`/`last_direction`/`enter_received`/`menu_active` state and whether `_menu_display` is invoked.

- [ ] **Step 1: Build a hermetic driver-construction helper**

Mirror `test_fixed_base_drivers_load.py`'s stub overlay (stub `luma.core.interface.serial.spi`, `luma.lcd.device.*`, `ST7789.ST7789`, `gpiozero.Button`, `pyky040.pyky040.Encoder`, `spidev.SpiDev`) + `mock.patch.object(mod.threading, "Thread")` + `mock.patch("os.system", side_effect=AssertionError)`. Read that file first with Serena and reuse its exact pattern. Construct one representative driver per input group: `ili9341e` (Group A encoder), `st7789_240x320e` (Group B encoder), `ili9341b` (button). The `pyky040.Encoder` stub must let `.setup(...)` record the callbacks so the test can invoke them.

- [ ] **Step 2: Characterize Group A (debounced) encoder**

Drive `_inc_callback(1)` / `_dec_callback(1)` / `_click_callback()` on a constructed `ili9341e` and assert the resulting `input_event`, `input_counter`, `last_direction`, `enter_received` transitions — including the "enter received cancels a pending up/down" path. Then set `input_event="UP"` with `input_counter>0` and call `_event_detect()`; assert it invokes `_menu_display("UP")` (patch `_menu_display` to record) and resets `input_counter=0`. Write the assertions to encode CURRENT behavior exactly (this is characterization — capture the debounce quirks, including the `< 0.3` branch that reads as near-always-true, verbatim; do NOT "fix" them).

- [ ] **Step 3: Characterize Group B (trivial) encoder + button input**

Same approach for `st7789_240x320e` (Group B: `_inc_callback` just sets `input_event="UP"` + bumps counter, no debounce state) and `ili9341b` (button: `_up_callback`/`_down_callback`/`_enter_callback` + its `_event_detect`). Assert the distinct behavior of each so the mixin split is justified and guarded.

- [ ] **Step 4: Characterize `st7789e`'s `_event_detect` divergence**

Construct `st7789e`, set up an event, call `_event_detect()`, and assert it ALSO nulls `in_data`/`status_data` and sets `monitor_display=False` (the 3-line divergence from the other 6). This pins the difference the mixin must preserve.

- [ ] **Step 5: Run + commit**

Run: `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ui/test_driver_input_behavior.py -v` → PASS (baseline captured). Report the count.
Commit (`-F`):
```
test(display): characterize rotary-encoder + button input behavior

Pins the pyky040 debounce (enter-cancels-updown), the trivial-encoder
variant, the button input, and st7789e's _event_detect divergence (it also
nulls in_data/status_data + monitor_display). No test exercised this logic
before; it is the guardrail for the Phase C mixin extraction.
```

---

### Task 2: Extract `EncoderInputMixin` + `SimpleEncoderInputMixin`; repoint the 7 encoder drivers

Move the two encoder input blocks into one module and rewrite the 7 encoder drivers to inherit them, deleting their inline copies. Behavior frozen by Task 1.

**Files:**
- Create: `display/_encoder_input.py`
- Modify (Serena): `ili9341e.py`, `ili9341em.py`, `ili9488e.py`, `ili9488em.py`, `st7789e.py` (Group A); `st7789_240x320e.py`, `st7789v_240x320e.py` (Group B)

**Interfaces:**
- Produces:
  - `EncoderInputMixin` — provides `_init_input`, `_click_callback`, `_inc_callback`, `_dec_callback`, `_event_detect` (the Group A debounced block, verbatim from `ili9341e.py`). Hard `from pyky040 import pyky040` + `import time`/`threading` at module scope.
  - `SimpleEncoderInputMixin` — the Group B block verbatim from `st7789_240x320e.py`.
  - A mechanism for `st7789e`'s `_event_detect` 3-line divergence: EITHER a `_reset_data_on_event = False` class flag the mixin's `_event_detect` honors (st7789e sets it True), OR st7789e overrides `_event_detect`. Choose based on Task 1's characterization; justify in the report. Prefer the flag if it keeps both behaviors in one method without duplication.

- [ ] **Step 1: Create `display/_encoder_input.py` with both mixins, verbatim from the source drivers**

With Serena, read `ili9341e.py`'s `_init_input`/callbacks/`_event_detect` (`find_symbol include_body=True`) and place them VERBATIM into `EncoderInputMixin`. Read `st7789_240x320e.py`'s block into `SimpleEncoderInputMixin`. Do not alter any logic. Keep the `pyky040`/`time`/`threading` imports at module scope (same hard-import contract).

- [ ] **Step 2: Handle `st7789e`'s `_event_detect` divergence**

Implement the chosen mechanism (flag or override) so `st7789e` gets the extra `in_data=None`/`status_data=None`/`monitor_display=False` resets while the other 4 Group A drivers do not. Task 1 Step 4 must still pass.

- [ ] **Step 3: Repoint the 5 Group A drivers**

For each of `ili9341e`, `ili9341em`, `ili9488e`, `ili9488em`, `st7789e`: with Serena, delete the inline `_init_input`/`_click_callback`/`_inc_callback`/`_dec_callback`/`_event_detect` and the now-unneeded `from pyky040 import pyky040` import, and add `EncoderInputMixin` to the class bases: `class Display(EncoderInputMixin, DisplayBase):`. Keep each driver's `__init__` (some set `self.last_direction=None` etc. — verify what remains needed after the mixin provides `_init_input`; the mixin's `_init_input` already sets that state, so the driver `__init__` may only need `self.config = config; super().__init__(...)`). Keep `_init_display_device`/`_display_clear`/`_display_canvas` (Task 4 handles those). st7789e sets the divergence flag if that mechanism was chosen.

- [ ] **Step 4: Repoint the 2 Group B drivers** (`st7789_240x320e`, `st7789v_240x320e`) to `SimpleEncoderInputMixin` the same way.

- [ ] **Step 5: Verify + commit**

Run: `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ui/test_driver_input_behavior.py tests/ui/test_fixed_base_drivers_load.py -q` → PASS (input behavior unchanged; all 16 still load). Then full suite → 1154 + Task 1's tests.
Confirm the MRO resolves `_init_input`/`_event_detect` from the mixin and everything else from `DisplayBase` (the mixin has no `_init_display_device`, so the driver's own still wins). Commit (`-F`).

---

### Task 3: Extract `ButtonInputMixin`; repoint the 3 button drivers

**Files:**
- Create: `display/_button_input.py`
- Modify (Serena): `ili9341b.py`, `ili9488b.py`, `st7789_240x320b.py`

**Interfaces:**
- Produces: `ButtonInputMixin` — `_init_input` (gpiozero `Button` setup + `_init_menu` + thread), `_enter_callback`, `_up_callback`, `_down_callback`, `_event_detect`, verbatim from `ili9341b.py`. Hard `import gpiozero`/`time`/`threading` at module scope.

- [ ] **Step 1:** Create `display/_button_input.py` with `ButtonInputMixin` verbatim from `ili9341b.py`'s input block (Serena `find_symbol include_body=True`).
- [ ] **Step 2:** Repoint `ili9341b`, `ili9488b`, `st7789_240x320b` to `class Display(ButtonInputMixin, DisplayBase):`, deleting their inline input blocks + the gpiozero import.
- [ ] **Step 3:** Run `tests/ui/test_driver_input_behavior.py tests/ui/test_fixed_base_drivers_load.py -q` → PASS; full suite → green. Commit (`-F`).

---

### Task 4: Extract the Luma panel mixin; thin the 8 ili9341/ili9488 drivers

Move the shared Luma `_init_display_device`/`_display_clear`/`_display_canvas` into one parameterized mixin so the **8** `ili9341*`/`ili9488*` drivers (the exact clone matrix) become thin registrations differing only by panel class + dims (+ the `em` spidev workaround).

**⚠️ SCOPE (verified against live code — the "9th luma driver" does NOT fit):** `st7789e` is Luma-backed but its `_init_display_device` differs from the ili drivers in MORE than panel+dims — its `spi(...)` call omits `bus_speed_hz`/`reset_hold_time`/`reset_release_time`, its device ctor uses `bus_speed=4000000` and NO `rotate=` kwarg. **`st7789e` is therefore EXCLUDED from `LumaPanelMixin`'s `_init_display_device`** — keep its own inline `_init_display_device`. (It already got `EncoderInputMixin` in Task 2.) It MAY still share `_display_clear`/`_display_canvas` if those are byte-identical to the ili drivers' — check with Serena and, if identical, let `st7789e` inherit just those from `LumaPanelMixin` while overriding `_init_display_device`; if not, leave st7789e's display methods inline too. Report which.

**Files:**
- Create: `display/_luma_panel.py`
- Modify (Serena): `ili9341`, `ili9341b`, `ili9341e`, `ili9341em`, `ili9488`, `ili9488b`, `ili9488e`, `ili9488em` (the 8); and `st7789e` ONLY if it can inherit `_display_clear`/`_display_canvas` (not `_init_display_device`)

**Interfaces:**
- Produces:
  - `LumaPanelMixin` — `_display_clear` + `_display_canvas` (identical across all luma drivers, verbatim) + a parameterized `_init_display_device` that reads `self._LUMA_PANEL_CLASS` and constructs the luma device with `width=self._NOMINAL_WIDTH, height=self._NOMINAL_HEIGHT` (the panel's native landscape dims, which the shim already provides), then starts the display thread. Class attr `_LUMA_PANEL_CLASS` set per driver.
  - `MaxSpiLumaPanelMixin` (or a `_LUMA_USE_EXPLICIT_SPIDEV = True` flag) for the `em` variant's `spi=spidev.SpiDev()` workaround. Choose flag vs subclass; justify.

- [ ] **Step 1: Read the 8 ili `_init_display_device` bodies and confirm the ONLY differences** are the panel class, the `width`/`height` literals (verified == `_NOMINAL_*`: ili9341→320/240, ili9488→480/320), and (for the two `em`) the explicit `spidev.SpiDev()`. Serena `find_symbol _init_display_device include_body=True` on each; diff pairwise. The `spi(...)` kwargs (`bus_speed_hz=32000000, reset_hold_time=0.2, reset_release_time=0.2`) and the device kwargs (`active_low=False, gpio_LIGHT=led_pin, rotate=self.rotation`) are identical across all 8 — confirm. (st7789e is excluded per the scope note above; do NOT fold its device init.)
- [ ] **Step 2: Create `display/_luma_panel.py`** with `LumaPanelMixin` (parameterized `_init_display_device` using `self._LUMA_PANEL_CLASS` + `self._NOMINAL_WIDTH/_HEIGHT`) and the `em` variant mechanism. Keep the luma imports where they belong: the PANEL CLASS import stays in each driver (so `ili9341.py` still `from luma.lcd.device import ili9341` and sets `_LUMA_PANEL_CLASS = ili9341`) — the mixin must NOT hard-import a specific panel.
- [ ] **Step 3: Thin each of the 8 ili drivers** to: import its panel class, import its shim `DisplayBase`, import `LumaPanelMixin` (+ input mixin from Tasks 2–3 where applicable), and declare `class Display(<InputMixin>, LumaPanelMixin, DisplayBase): _LUMA_PANEL_CLASS = <panel>` (+ the spidev flag for `em`). Delete the inline `_init_display_device`/`_display_clear`/`_display_canvas`. Verify MRO. (st7789e: per the scope note, at most inherit `_display_clear`/`_display_canvas`; keep its own `_init_display_device`.) Example end state for `ili9341em.py`:
  ```python
  from luma.lcd.device import ili9341
  from display.base_240x320 import DisplayBase
  from display._luma_panel import LumaPanelMixin
  from display._encoder_input import EncoderInputMixin

  class Display(EncoderInputMixin, LumaPanelMixin, DisplayBase):
      _LUMA_PANEL_CLASS = ili9341
      _LUMA_USE_EXPLICIT_SPIDEV = True
  ```
- [ ] **Step 4: Verify + commit.** `tests/ui/test_fixed_base_drivers_load.py` (incl. the geometry + delay + rotation assertions) + `test_driver_input_behavior.py` → PASS; full suite green. The `st7789*` Pimoroni drivers and pygame drivers are NOT luma — leave their `_init_display_device` alone (out of this task; note that the Pimoroni ST7789 device-geometry override must still work). Commit (`-F`).

---

### Task 5: Regression sweep + manifest unchanged + footgun guard

Prove the whole collapse is transparent: all 16 drivers still load/instantiate with correct geometry+delay, the manifest is byte-identical, and the kept-shims still supply the resolution attributes (the footgun stays disarmed).

**Files:**
- Modify: `tests/ui/test_fixed_base_drivers_load.py` (extend if needed) OR add `tests/ui/test_driver_collapse_regression.py`

- [ ] **Step 1: Confirm the manifest is untouched.** `git diff <phase-base> -- wizard/wizard_manifest.json` → empty. Assert in a test that all 16 filenames resolve to a module with `class Display` (the Phase B `test_manifest_lists_all_16_driver_identifiers` may already cover this — verify and extend if it doesn't assert the class exists).
- [ ] **Step 2: Footgun guard.** Add/confirm a test asserting each driver still reports its correct `(WIDTH,HEIGHT)` and `min_transition_delay` (the Phase B suite does this via the shims — confirm it still passes now that drivers are thin, proving the shim attrs still flow through the new MRO). Explicitly assert `st7789e` → 240×240 + `min_transition_delay=1.0` and an `ili9341*` → 320×240 + `0.1`.
- [ ] **Step 3: Line-count win.** Report the before/after total line count of the 16 driver files + the 3 new mixin modules to quantify the dedup (the spec targets "~600 lines from the driver/encoder work").
- [ ] **Step 4: Full suite + commit.** Green; report the final total. Commit (`-F`).

---

## Open decision to confirm at execution (surface to the human, like Phase B)

**Shim fate.** The `base_320x480.py` shim's docstring (written during Phase B) says "Phase C repoints drivers straight at base_fixed and deletes this module." **This plan deliberately does NOT do that** — it keeps the three shims as the resolution-profile layer, because (a) the registry is filename-based so files can't be reduced anyway, and (b) deleting the shims forces every one of the 16 drivers to re-declare `_NOMINAL_*`/`_SQUARE`/`min_transition_delay`, reintroducing the exact silent-default footgun the Phase B whole-branch review flagged (esp. `st7789e` → wrong dims + lost settle). Keeping the shims is strictly safer and less duplicative. If the human wants the shims deleted anyway, that is a separate, riskier change (per-driver attrs + a guard against the silent default + a settings-migration discussion) and should be its own phase. **Recommend: keep the shims; update the stale `base_320x480.py` docstring to reflect that.**

## Self-Review

- **Spec coverage (Phase C section):** Luma clone-matrix collapse → Task 4 (parameterized `LumaPanelMixin`; the 9 luma files become thin registrations); `EncoderInputMixin` extraction → Task 2 (both the debounced Group A and the trivial Group B, plus the `st7789e` `_event_detect` divergence the spec missed); button dedup (a bonus the investigation surfaced) → Task 3; "thin registrations" + manifest/driver-list unchanged → Tasks 4–5. The spec's "8 files → thin registrations" is honored as *thin subclasses at the same file paths* (the registry forbids fewer files without a migration — documented).
- **Behavior-preservation:** the sole gate is "no runtime change"; every extraction is verbatim, guarded by Task 1's new input-behavior characterization (which did not exist before) plus the Phase B driver-load/geometry suite. The one real per-driver difference (st7789e's `_event_detect`) is explicitly characterized (Task 1 Step 4) and preserved (Task 2 Step 2).
- **Placeholder scan:** the only deferred-to-implementation content is the exact verbatim method bodies (read via Serena at execution) and the flag-vs-override choice for two divergences (st7789e `_event_detect`; the `em` spidev workaround) — both explicitly instructed with a decision criterion, not hand-waved.
- **Type/name consistency:** `EncoderInputMixin`/`SimpleEncoderInputMixin`/`ButtonInputMixin`/`LumaPanelMixin`, `_LUMA_PANEL_CLASS`, `_LUMA_USE_EXPLICIT_SPIDEV`, `_reset_data_on_event` are used consistently across Tasks 2–5.
- **Ordering:** characterization net (1) precedes every extraction; input mixins (2–3) before the panel mixin (4) so each driver's final `class Display(<Input>, LumaPanelMixin, DisplayBase)` MRO is assembled incrementally and verified at each step; regression + footgun guard last (5).
- **Footgun:** neutralized by construction (shims kept), and re-guarded by Task 5 Step 2.
