# Phase B — Merge the Three Legacy Fixed-Display Bases — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse `display/base_240x240.py`, `display/base_240x320.py`, and `display/base_320x480.py` (~1,438 lines each, ~89–99% identical) into one parameterized `display/base_fixed.py`, guarded by a NEW pixel-hash snapshot harness written and baselined first, with the three old modules reduced to thin shims so **no driver file changes in this phase** (Phase C repoints them).

**Architecture:** The three bases share an identical 38-method `DisplayBase`; the two larger ones (`base_240x320`, `base_320x480`) are byte-identical except the `_init_globals` dimension constants and the file docstring. `base_240x240` diverges by ~158 lines: ~90–100 are cosmetic layout constants, ~45 are a genuinely richer `_display_loop`, and ~15–18 are real behavioral differences in `_menu_display`. We (1) build a snapshot harness that renders the `_display_*` methods to PIL images and freezes their pixel hashes as the baseline, (2) create `base_fixed.py` with a per-resolution layout dict and one reconciled implementation, (3) fold each old resolution in under the frozen baseline — pixel-identical for the two large bases, explicitly re-baselined for 240x240 — and (4) leave `base_240x240.py`/`base_240x320.py`/`base_320x480.py` as re-export shims.

**Tech Stack:** Python 3.14, PIL/Pillow (RGBA `Image`/`ImageDraw`/`ImageFont`), pytest, Serena for all symbolic edits.

## Global Constraints

- **Behavior-preserving. Two small, human-approved changes in Task 5, both effectively behavior-preserving in practice:** (a) the two `_display_loop`s unify to the richer implementation, with the per-transition settle exposed as a `min_transition_delay` class attribute set per resolution to each family's CURRENT value (1.0s for 240x240, 0.1s for the two large) — so steady-state update rate is unchanged and no display regresses; (b) the merged `_menu_display` keeps 240x240's `None`-guard (a crash-fix). Every other change must produce pixel-identical render output for 240x320 and 320x480, and for 240x240 only the specific, enumerated menu differences that are consciously re-baselined.
- **Serena for ALL code edits** (`replace_symbol_body`, `insert_*_symbol`, `replace_content`, `create_text_file`). Call `initial_instructions` first and activate the project. Never hand-edit code files blind. (New test files may use plain Write.)
- **Test command is ALWAYS** `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest <args> -q` from the repo root, wrapped in `timeout 180`. Bare `python3 -m pytest` resolves an interpreter without PySide6 and reports false failures. Exit code 124 = a hang; stop and report.
- **`uvx ruff format <changed files>` before every commit** (standing repo rule; a pre-commit hook also runs it — re-stage and amend if it reformats). `ruff check` is NOT a gate in this repo (it carries hundreds of pre-existing findings); only `ruff format` is.
- **Commit messages via `git commit -F <file>`** — this zsh eats backticks inside double-quoted args and will silently gut the message. End every message with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- **⚠️ REBOOT HAZARD:** `_menu_display` in all three bases calls `os.system("sleep 3 && sudo shutdown -h now &")` and `os.system("sleep 3 && sudo reboot &")` (`base_320x480.py:1159,1163` and siblings). ANY test that constructs a base and could reach the menu `Power_` path MUST neutralize `os.system` first. The snapshot harness neutralizes it unconditionally as defense-in-depth. This repo has had three real `sudo reboot` incidents; do not add a fourth.
- **Leave `except IOError, OSError:` / other bare-except tuples alone** — valid PEP 758 syntax on Python 3.14; ruff canonicalizes to it.
- **Baseline suite is green at 1029 passed.** Every task adds tests; report the new total after each. No task may reduce the pre-existing count.
- **No driver-file edits in this phase.** The 16 drivers keep importing `DisplayBase` from the three old module paths; those paths become shims. Phase C repoints and collapses drivers.
- **The plan/spec has a documented history of factual errors — trust the live code.** All file:line citations below were verified against `massive-reworks-and-new-ui` at the time of writing; re-confirm any that has drifted before relying on it.

## Verified facts this plan rests on (from a Serena-assisted read of live code)

- **WIDTH/HEIGHT origin (`_init_globals`):** `base_240x240.py:63-76` hardcodes `self.WIDTH = self.HEIGHT = 240` (rotation-independent — square panel). `base_240x320.py:57-75` and `base_320x480.py:57-75` derive them from `self.rotation`: `if self.rotation in [90,270,1,3]: WIDTH,HEIGHT = (240,320)` else `(320,240)` — and `(320,480)`/`(480,320)` respectively. **Config is never consulted** for dimensions.
- **`base_240x320.py` and `base_320x480.py` are byte-identical** except the `_init_globals` constants (lines ~65-70) and the docstring (line 9). `diff` = 15 lines total.
- **All resolution-conditional layout branches live only in the two large bases** and key off `self.WIDTH == 240` (a proxy for "small portrait 240-wide panel") or `font_point_size = A if self.WIDTH == 240 else B`. Full inventory (identical line numbers in both large files): `:859,866-869,873-876,887-890,893-896,899-902,916-919` (icon positions), `:934-937,946-949,966-975` (text coords), `:1279,1284-1289,1317,1331,1364` (font sizes). `base_240x240.py` has ZERO such branches (WIDTH is always 240) — it uses plain constants where the large files branch.
- **The 38 methods** (from `base_320x480.py`): drawing primitives `_rounded_rectangle`(:337) … `_draw_gauge`(:568) are identical across all three; the render sink is `_display_canvas(self, canvas)`(:667, a no-op `pass` in the base); other no-op driver hooks are `_init_display_device`(:77), `_init_input`(:83), `_display_clear`(:661). Public API: `display_status`(:1407), `display_splash`(:1416), `clear_display`(:1422), `display_text`(:1428), `display_network`(:1435). Render methods: `_display_splash`(:673), `_display_text`(:683), `_display_network`(:695), `_display_current`(:709), `_menu_display`(:1029).
- **Drawing surface:** every render method builds a fresh `PIL.Image.new("RGBA", (self.WIDTH, self.HEIGHT), …)`, draws with `ImageDraw`, and calls `self._display_canvas(img)`. Overriding/capturing `_display_canvas` is the snapshot seam. `_init_display_device` is a no-op in the base, so constructing a bare `DisplayBase` starts NO hardware thread.
- **Assets & fonts:** `_init_assets` loads `static/img/display/background.jpg` and `static/img/display/color-boot-splash.png` (both present; require repo-root cwd). Fonts: `self.primary_font = "trebuc.ttf"` (a **system** font, bare filename — `ImageFont.truetype` RAISES `OSError` if absent, so the display code itself cannot render without it) and `static/font/FA-Free-Solid.otf` (repo-bundled). **The snapshot suite therefore skips cleanly when `trebuc.ttf` is unavailable** (e.g. a bare CI box); baseline capture and post-refactor verification both happen on a box that has it, so before/after pixel equality is environment-consistent.
- **`_display_current` state inputs:** `in_data` → `probe_history.primary` (dict label→temp), `primary_setpoint`, `notify_targets`, `probe_history.food` (dict, up to 2 gauges). `status_data` → `mode` (Startup/Reignite/Smoke/Hold/Prime/Shutdown/Stop/Monitor/Error), `outpins.{fan,igniter,auger}` (bool), `notify_data` (list of `{req,type}`), `recipe_paused`, `recipe`, `s_plus`, `hopper_level_enabled`, `hopper_level`, `p_mode`, `units` (F/C). Icon priority: pause > recipe > notify. `hopper_level` color bands: >70 green, >30 orange, else red.
- **`_display_loop` drift (Task 5's decision):** `base_240x240.py:230-320` carries `monitor_display`/`loop_delay`/`clear_delay`/`continue` pacing and nulls `in_data`/`status_data` after each render; `base_240x320.py:229-292` == `base_320x480.py:229-292` use a flat `time.sleep(0.1)`, no `continue`, no `monitor_display`, and never null the data. This loop is an infinite `while True` — **the snapshot harness does not and cannot cover it**; Task 3 characterizes it separately.
- **`_menu_display` real behavioral diffs in 240x240 vs the large files:** explicit `return`s after mode-change writes (`base_240x240.py:1122,1133,1146,1211,1223`); `Stop`/`Power_` branches null `in_data`/`status_data` with `clear_display()` commented out (`:1139-1141,1151`) where the large files actively call `clear_display()` (`:1142,1151`); copy `"Shutdown..."` vs `"Shutting Down..."` (`:1158`); `menu_active = True` set on Hold sub-menu entry (`:1192`) — absent in the large files; a `None`-guard `if self.in_data is None or …` (`:1194`) that the large files lack (`:1193`, would `TypeError` if `in_data` is None).
- **16 drivers subclass these bases** (all `class Display(DisplayBase)`, identical `__init__` signature, none override `_init_globals`): 1 on 240x240 (`st7789e.py:23`), 11 on 240x320 (`ili9341*`, `pygame_240x320*`, `st7789_240x320*`, `st7789v_240x320*`), 4 on 320x480 (`ili9488*`). 5 of the 240x320 drivers (`st7789*` family) overwrite `self.WIDTH/HEIGHT` from `self.device.width/height` in `_init_display_device` (after `_init_globals`). `dsi_800x480t.py` and `qtquick_flex.py` use the unrelated `base_flex` and are OUT of scope.

---

### Task 1: Snapshot harness — render a `DisplayBase` to a stable pixel hash

Build the reusable harness the whole phase depends on: construct a bare `DisplayBase`, drive one render method, capture the PIL image via a patched `_display_canvas`, and reduce it to a deterministic hash of the raw pixel bytes. Neutralize `os.system` and skip cleanly if `trebuc.ttf` is missing.

**Files:**
- Create: `tests/ui/fixed_base_harness.py` (the harness — not a `test_*` file, so pytest won't collect it directly)
- Create: `tests/ui/test_fixed_base_harness_smoke.py` (proves the harness renders and hashes stably)

**Interfaces:**
- Produces:
  - `FONT_AVAILABLE: bool` — True iff `ImageFont.truetype("trebuc.ttf", 20)` succeeds; used with `@pytest.mark.skipif`.
  - `make_base(module, rotation=0, units="F") -> DisplayBase` — imports `DisplayBase` from the given module path string (e.g. `"display.base_320x480"`), instantiates it with `os.system` neutralized and `_display_canvas` patched to record into `base._captured`, returns the instance. No hardware, no threads.
  - `render(base, method_name, *args) -> str` — **pins the four animation state variables** (`fan_rotation=0, auger_step=0, icon_color=100, inc_pulse_color=True`) so the frame is deterministic, then calls `base.<method_name>(*args)`, returns `sha256(base._captured.convert("RGBA").tobytes()).hexdigest()`. Raises if nothing was captured.

> **⚠️ Determinism (validated against live code, do NOT skip the pin):** `_display_current` MUTATES animation state every call — `_draw_fan_icon` does `self.fan_rotation += 30` (`base_320x480.py:418`), `_draw_auger_icon` does `self.auger_step += 1` (`:445`), and the primary-gauge pulse does `self.icon_color += 20`/`-= 20` with `self.inc_pulse_color` toggling (`:846-855`). Without pinning these four to their `_init_globals` defaults before each render, the SAME input yields DIFFERENT pixels on successive calls and every golden is unstable. Pinning captures "the first frame after construction," which is faithful and reproducible. Verified: 4 pinned renders of a fan+igniter+auger-on Smoke frame produce identical hashes; un-pinned they differ.
  - `SAMPLE_IN_DATA`, `SAMPLE_STATUS_DATA` — one representative `_display_current` input pair (Smoke mode, one food probe, fan on) for smoke use; the full matrix lands in Task 2.

- [ ] **Step 1: Write the harness module**

```python
# tests/ui/fixed_base_harness.py
"""Hermetic snapshot harness for the legacy fixed DisplayBase classes.

Renders a base's `_display_*` methods to a PIL image (captured at the
`_display_canvas` sink) and hashes the raw pixel bytes. os.system is
neutralized because `_menu_display` shells out to `sudo reboot`.
"""

import hashlib
import importlib
from unittest import mock

from PIL import ImageFont

try:
    ImageFont.truetype("trebuc.ttf", 20)
    FONT_AVAILABLE = True
except OSError:
    FONT_AVAILABLE = False


def make_base(module, rotation=0, units="F"):
    mod = importlib.import_module(module)
    with mock.patch("os.system", side_effect=AssertionError("os.system blocked in snapshot harness")):
        base = mod.DisplayBase(
            dev_pins={}, buttonslevel="HIGH", rotation=rotation, units=units, config={}
        )
    base._captured = None
    base._display_canvas = lambda canvas: setattr(base, "_captured", canvas)
    return base


def _pin_animation(base):
    # _display_current advances these every call (fan rotation, auger shift,
    # gauge color pulse). Pin them so a given input renders identical pixels.
    base.fan_rotation = 0
    base.auger_step = 0
    base.icon_color = 100
    base.inc_pulse_color = True


def render(base, method_name, *args):
    _pin_animation(base)
    base._captured = None
    getattr(base, method_name)(*args)
    assert base._captured is not None, f"{method_name} produced no canvas"
    return hashlib.sha256(base._captured.convert("RGBA").tobytes()).hexdigest()


SAMPLE_IN_DATA = {
    "probe_history": {"primary": {"Grill": 225}, "food": {"Probe1": 145}},
    "primary_setpoint": 225,
    "notify_targets": {"Grill": 0, "Probe1": 165},
}
SAMPLE_STATUS_DATA = {
    "mode": "Smoke",
    "outpins": {"fan": True, "igniter": False, "auger": False},
    "notify_data": [],
    "recipe_paused": False,
    "recipe": False,
    "s_plus": False,
    "hopper_level_enabled": True,
    "hopper_level": 80,
    "p_mode": 2,
    "units": "F",
}
```

> **Note for the implementer:** verify the `DisplayBase.__init__` signature against live code before relying on the `make_base` kwargs — it was `__init__(self, dev_pins, buttonslevel="HIGH", rotation=0, units="F", config={})` at plan time. If `_init_assets`/`_init_menu` touch `read_control`/`write_control` at construction time (they should not — those are only in `_menu_display`), patch `common.datastore_accessors.read_control`/`write_control` in `make_base` too and note it in your report.

- [ ] **Step 2: Write the smoke test**

```python
# tests/ui/test_fixed_base_harness_smoke.py
import pytest

from tests.ui.fixed_base_harness import (
    FONT_AVAILABLE, make_base, render, SAMPLE_IN_DATA, SAMPLE_STATUS_DATA,
)

pytestmark = pytest.mark.skipif(not FONT_AVAILABLE, reason="trebuc.ttf not installed")


def test_render_current_is_deterministic():
    b1 = make_base("display.base_320x480")
    b2 = make_base("display.base_320x480")
    h1 = render(b1, "_display_current", SAMPLE_IN_DATA, SAMPLE_STATUS_DATA)
    h2 = render(b2, "_display_current", SAMPLE_IN_DATA, SAMPLE_STATUS_DATA)
    assert h1 == h2  # same input -> same pixels -> same hash


def test_splash_and_text_render():
    b = make_base("display.base_320x480")
    assert len(render(b, "_display_splash")) == 64
    assert len(render(b, "_display_text")) == 64  # renders self.display_data ("" default) or set via display_text


def test_no_hardware_no_reboot():
    # os.system is neutralized in make_base; constructing must not raise or shell out.
    make_base("display.base_240x240")
    make_base("display.base_240x320")
```

- [ ] **Step 3: Run — expect PASS (or SKIP if no font)**

Run: `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ui/test_fixed_base_harness_smoke.py -v`
Expected: 3 passed (or 3 skipped on a font-less box). If `_display_text` needs `display_data` set first, adjust: call `b.display_text("hello")` before `render(b, "_display_text")` and note the actual method contract in your report.

- [ ] **Step 4: Commit**

```
test(display): snapshot harness for the legacy fixed DisplayBase classes

Renders _display_* methods to a PIL image captured at the _display_canvas
sink and hashes the raw pixel bytes. os.system is neutralized (menu shells
out to sudo reboot); the suite skips when trebuc.ttf is unavailable.
```

---

### Task 2: Freeze the baseline golden manifest for all three resolutions

Render the full state matrix for each of the three CURRENT bases and commit the pixel hashes as the frozen contract. This is the **prerequisite gate** — Tasks 4–5 may only pass if they reproduce these hashes (except the 240x240 entries Task 5 consciously re-baselines).

**Files:**
- Create: `tests/ui/test_fixed_base_golden.py` (the parametrized golden test)
- Create: `tests/ui/fixtures/fixed_base_golden.json` (committed baseline: `{case_name: sha256}`)

**Interfaces:**
- Consumes: `tests/ui/fixed_base_harness.py` from Task 1.
- Produces: a `CASES` table (one entry per `(module, rotation, method, state_name)`) and a committed hash manifest. `case_name` format: `f"{short_module}:{method}:{state}:{rotation}"` (e.g. `320x480:current:smoke_fanon:0`).

- [ ] **Step 1: Enumerate the state matrix in the test**

Build `CASES` covering, for each of the three modules (`base_240x240`, `base_240x320`, `base_320x480`) and the rotations that change dimensions (0 and 90 for the two large bases; 0 only for the square base):
- `_display_splash` (no args)
- `_display_text` after `display_text("Network Error")`
- `_display_network` with a sample IP string
- `_display_current` across these `status_data["mode"]` × input states (reuse the axes from the verified facts): `Startup` (with `p_mode`, countdown via `start_duration`/`start_time`), `Smoke` (p_mode shown, distinct coords), `Hold` (setpoint arc, lid-open pause timer via `lid_open_detected`/`lid_open_endtime`), `Prime`, `Reignite`, `Shutdown`, plus these cross-cuts on a Smoke base: all `outpins` on; 2 food probes; `notify_data` with 2 active non-hopper entries; `recipe_paused=True`; `recipe=True`; `s_plus=True`; `hopper_level` at 80/50/10 (the three color bands); `units="C"`; and `primary_setpoint`/temps at 0 (the `<= 0` short-circuit path).

Define the state dicts as module-level constants in the test file (full literals — no placeholders). Keep each state minimal but valid per the key list in the verified facts.

- [ ] **Step 2: Write the golden test with a capture-on-miss fallback**

```python
# tests/ui/test_fixed_base_golden.py
import json
import os
import pathlib

import pytest

from tests.ui.fixed_base_harness import FONT_AVAILABLE, make_base, render

pytestmark = pytest.mark.skipif(not FONT_AVAILABLE, reason="trebuc.ttf not installed")

GOLDEN = pathlib.Path(__file__).parent / "fixtures" / "fixed_base_golden.json"

# CASES: list of (case_name, module, rotation, method, args_factory)
# args_factory() -> tuple of args for the render method (states defined above).
CASES = [
    # ... full enumeration from Step 1, e.g.:
    # ("320x480:current:smoke_fanon:0", "display.base_320x480", 0, "_display_current",
    #   lambda: (IN_SMOKE, ST_SMOKE_FANON)),
]


def _load_golden():
    return json.loads(GOLDEN.read_text()) if GOLDEN.exists() else {}


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_matches_golden(case):
    name, module, rotation, method, args_factory = case
    base = make_base(module, rotation=rotation)
    h = render(base, method, *args_factory())
    golden = _load_golden()
    if os.environ.get("CAPTURE_GOLDEN") == "1":
        golden[name] = h
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(dict(sorted(golden.items())), indent=2) + "\n")
        pytest.skip(f"captured {name}")
    assert name in golden, f"no baseline for {name}; run with CAPTURE_GOLDEN=1 once"
    assert h == golden[name], f"pixel hash changed for {name}"
```

- [ ] **Step 3: Capture the baseline against the CURRENT (unmodified) bases**

Run: `CAPTURE_GOLDEN=1 timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ui/test_fixed_base_golden.py -q`
Then run WITHOUT `CAPTURE_GOLDEN` and confirm every case PASSES against the just-frozen manifest:
Run: `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ui/test_fixed_base_golden.py -q`
Expected: all cases pass. Record the case count in your report.

> **This is the contract.** After this commit, `CAPTURE_GOLDEN` must NOT be used again except in Task 5's deliberate, enumerated 240x240 re-baseline. A reviewer will reject any silent regeneration.

- [ ] **Step 4: Commit**

```
test(display): freeze pixel-hash baseline for the three fixed bases

<N> cases across base_240x240/240x320/320x480 x rotations x the display
render methods and control/status state matrix. This is the frozen contract
the base_fixed merge must reproduce; regeneration is forbidden outside the
documented 240x240 re-baseline.
```

---

### Task 3: Characterize `_display_loop` per-iteration behavior (the one uncovered method)

`_display_loop` is an infinite `while True` loop the snapshot harness cannot exercise. Its 240x240-vs-others divergence (Task 5's deliberate change) needs its own visible characterization: drive exactly ONE iteration with `time.time`/`time.sleep` mocked and a forced exit, recording which branches fire and which sleep duration is used.

**Files:**
- Create: `tests/ui/test_fixed_base_loop.py`

**Interfaces:**
- Consumes: the three base modules directly.
- Produces: per-resolution assertions on one loop iteration's observable effects (sleep duration, `monitor_display` mutation, `in_data` nulling, `_display_current` invocation) that Task 5 must either preserve or consciously change.

- [ ] **Step 1: Write a single-iteration driver**

Patch `time.sleep` to raise a sentinel `_StopLoop` after the first call (so `while True` exits deterministically), patch `time.time` to a fixed value, set `input_enabled=False`, `display_active=True`, `display_timeout=None`, `in_data=SAMPLE_IN_DATA`, `status_data=SAMPLE_STATUS_DATA`, patch `_display_current` to record its call, then call `_display_loop()` and catch `_StopLoop`. Assert:
- the recorded `time.sleep` argument (240x240: `clear_delay` = 1 on first render via `monitor_display` False→True path; large bases: flat `0.1`),
- 240x240 sets `monitor_display = True` and nulls `in_data`/`status_data`; the large bases do neither,
- `_display_current` was called once for all three.

Write the full test with explicit literals (mock the `os.system`, patch `common.datastore_accessors` reads if construction needs them). Reuse `make_base` from Task 1 for construction, then override the loop-relevant attributes on the instance.

- [ ] **Step 2: Run — capture current behavior for all three**

Run: `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ui/test_fixed_base_loop.py -v`
Expected: PASS, encoding today's divergent behavior. This is the "before" side of Task 5's decision.

- [ ] **Step 3: Commit**

```
test(display): characterize _display_loop per-iteration behavior

Drives one iteration of each base's infinite display loop with time mocked,
pinning the 240x240 monitor_display/clear_delay pacing vs the flat-0.1s loop
of the two large bases. The snapshot harness cannot reach this method; this
is the guardrail for the Task 5 loop reconciliation.
```

---

### Task 4: Create `base_fixed.py`; reduce the two large bases to shims (pixel-identical)

Move the shared 38-method body into `display/base_fixed.py`, parameterized by nominal `(width, height)` with all layout driven by the existing `self.WIDTH == 240` branches. Point `base_240x320.py` and `base_320x480.py` at it as thin shims. Because those two are byte-identical today, their golden hashes must be **unchanged** (no re-baseline).

**Files:**
- Create: `display/base_fixed.py`
- Modify → shim: `display/base_240x320.py`, `display/base_320x480.py`
- (240x240 stays untouched until Task 5)

**Interfaces:**
- Produces: `display.base_fixed.DisplayBase` — same public API and constructor signature as today, plus a class-level or constructor-level `_NOMINAL = (width, height)` mechanism (see Step 2) so `_init_globals` can apply the rotation-swap for non-square panels.
- Consumes: nothing new.

- [ ] **Step 1: Copy `base_320x480.py` to `base_fixed.py` verbatim, then parameterize `_init_globals`**

With Serena, create `display/base_fixed.py` as an exact copy of `base_320x480.py`'s `DisplayBase` (it is the canonical large-base body). Then change ONLY `_init_globals`'s dimension block to read nominal dims from a class attribute instead of hardcoding 320/480:

```python
class DisplayBase:
    # Nominal (landscape) panel size; subclass/shim sets this. Non-square panels
    # swap W/H for portrait rotations, exactly as the legacy bases did.
    _NOMINAL_WIDTH = 480
    _NOMINAL_HEIGHT = 320
    _SQUARE = False

    def _init_globals(self):
        # ... keep the docstring ...
        if self._SQUARE:
            self.WIDTH = self._NOMINAL_WIDTH
            self.HEIGHT = self._NOMINAL_HEIGHT
        elif self.rotation in [90, 270, 1, 3]:
            self.WIDTH = self._NOMINAL_HEIGHT
            self.HEIGHT = self._NOMINAL_WIDTH
        else:
            self.WIDTH = self._NOMINAL_WIDTH
            self.HEIGHT = self._NOMINAL_HEIGHT
        # ... keep the rest (inc_pulse_color, icon_color, fan_rotation, auger_step, delays) ...
```

Everything else in `base_fixed.py` is the verbatim large-base body (all layout branches already key off `self.WIDTH == 240`, so they need no change). Do NOT touch the drawing primitives, render methods, or `_menu_display`.

- [ ] **Step 2: Make `base_320x480.py` a shim**

Replace the entire file body with:

```python
"""Compat shim: 320x480 fixed display base. Real implementation in base_fixed.
Phase C repoints drivers straight at base_fixed and deletes this module."""
from display.base_fixed import DisplayBase as _Base


class DisplayBase(_Base):
    _NOMINAL_WIDTH = 480
    _NOMINAL_HEIGHT = 320
    _SQUARE = False
```

- [ ] **Step 3: Make `base_240x320.py` a shim**

```python
"""Compat shim: 240x320 fixed display base. Real implementation in base_fixed."""
from display.base_fixed import DisplayBase as _Base


class DisplayBase(_Base):
    _NOMINAL_WIDTH = 320
    _NOMINAL_HEIGHT = 240
    _SQUARE = False
```

- [ ] **Step 4: Verify the two large bases are pixel-identical to baseline**

Run: `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ui/test_fixed_base_golden.py -q -k "320x480 or 240x320"`
Expected: every 240x320 and 320x480 case PASSES unchanged (byte-identical bodies → identical pixels). If ANY differs, the parameterization altered rendering — stop and diff. Do NOT re-baseline.

Then the loop characterization for the two large bases:
Run: `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ui/test_fixed_base_loop.py -q -k "240x320 or 320x480"`
Expected: PASS unchanged.

- [ ] **Step 5: Full suite (drivers still import via shims)**

Run: `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q`
Expected: 1029 + Task 1–3 additions, all green. The 11+4 drivers importing the two shims must still load.

- [ ] **Step 6: Commit**

```
refactor(display): extract base_fixed; 240x320/320x480 become shims

base_320x480's DisplayBase body moves verbatim to display/base_fixed.py with
_init_globals parameterized by a nominal (W,H) + _SQUARE flag; all layout
already keys off self.WIDTH==240 so nothing else changes. The two large bases
are now thin subclasses. Pixel hashes unchanged (verified byte-identical).
```

---

### Task 5: Fold 240x240 into `base_fixed.py`; reconcile the loop and menu; re-baseline 240x240

Bring the square resolution under `base_fixed` via a per-resolution layout dict for the ~15 constants that differ, reconcile the `_menu_display` behavioral diffs and the `_display_loop` drift to ONE implementation, make `base_240x240.py` a shim, and explicitly re-baseline the 240x240 goldens — enumerating every intentional pixel change.

**⚠️ This task contains the phase's one deliberate behavior change (`_display_loop`) and requires human sign-off before the reconciliation commit — see Step 4.**

**Files:**
- Modify: `display/base_fixed.py` (add square-layout entries; reconcile loop + menu)
- Modify → shim: `display/base_240x240.py`
- Modify: `tests/ui/fixtures/fixed_base_golden.json` (240x240 entries only, re-baselined)

**Interfaces:**
- Consumes: `base_fixed.DisplayBase` from Task 4.
- Produces: a `_LAYOUT` lookup keyed on `self.WIDTH == 240 and self.HEIGHT == 240` (the square case) vs the existing width-based branches, so the square panel gets its own icon/font constants. The public API is unchanged.

- [ ] **Step 1: Add the square case to `_SQUARE` config and layout**

In `base_fixed.py`, the square panel already renders through the same methods; the square layout differs only in the ~90–100 cosmetic constants catalogued in the verified facts. Introduce them as a per-panel layout: extend the existing `if self.WIDTH == 240:` branches (and the `font_point_size = A if self.WIDTH == 240 else B` ternaries) to a three-way form where the square panel (detect via `self._SQUARE`) gets `base_240x240.py`'s constants. Work method-by-method, driven by a direct diff of `base_240x240.py` against the pre-shim `base_240x320.py` (`git show <base>:display/base_240x320.py`) for each render method, transcribing 240x240's constants into the `self._SQUARE` branch. Do NOT change the non-square branches (Task 4 froze them).

- [ ] **Step 2: Reconcile `_menu_display` behavioral diffs**

For each of the enumerated 240x240 menu diffs (explicit `return`s; `Stop`/`Power_` nulling vs `clear_display()`; `"Shutdown..."` vs `"Shutting Down..."`; `menu_active=True` on Hold entry; the `None`-guard), pick ONE behavior for the merged base and document the choice. **Recommended default (confirm at sign-off):** adopt the large bases' behavior (active `clear_display()`, `"Shutting Down..."`, no square-only `return`s) EXCEPT keep 240x240's `None`-guard `if self.in_data is None or self.in_data["primary_setpoint"] == 0:` — it is a strict crash-safety improvement (the large bases `TypeError` if `in_data` is None before first status). The `None`-guard adoption is a second, minor, deliberate behavior change; call it out. Menu rendering that reaches `_display_*` is covered by the goldens; the control-flow diffs (returns, clear vs null) are not fully golden-covered, so reason about each explicitly in your report.

- [ ] **Step 3: Unify to the richer loop, with the transition settle as a per-display `min_transition_delay` class attribute (DECISION MADE — see below)**

**The human sign-off in the original Step 4 has already been obtained; this is the settled design — implement it, do not re-ask.** Adopt `base_240x240.py`'s richer `_display_loop` (the `monitor_display` pacing, `loop_delay`/`clear_delay`, `continue`-based re-eval, post-render `in_data`/`status_data` nulling) as the SINGLE loop for all panels. The ONLY behavioral knob that differs between the old loops is the post-transition settle (`clear_delay` — the first-frame hold after a clear/mode-change, which gives slow physical panels time to draw). Expose it as a named class attribute `min_transition_delay` (NOT keyed by `_SQUARE`), so fast displays keep instant transitions and slow ones can settle:

- In `base_fixed.py`, add a class attribute `min_transition_delay = 0.1` (fast default).
- In `_init_globals`, set the loop's timing from it: `self.loop_delay = 0.1` (steady-state cadence, fixed for all), `self.clear_delay = self.min_transition_delay` (the tunable transition settle), and `self.monitor_display = False`. (`base_fixed` inherited the FLAT loop from `base_320x480` in Task 4, which has NONE of these attrs — you are adding them now.)
- **Replace `base_fixed.py`'s `_display_loop` with `base_240x240.py`'s richer version VERBATIM** (read it with Serena `find_symbol _display_loop include_body=True` in `display/base_240x240.py`). Keep its `self.clear_delay`/`self.loop_delay`/`self.monitor_display` references as-is — they now resolve to the values `_init_globals` sets from `min_transition_delay`. This keeps the loop byte-identical to the one Task 3 characterized.

**Why this is behavior-preserving in practice** (feeder pushes new data every 0.1s, `display_process.py:42`): all panels stay ~10 fps in steady state (feeder-bound) regardless of loop; the ONLY observable difference is the first-frame-after-transition hold. Setting `min_transition_delay` per resolution to each family's CURRENT value preserves that exactly:
- `base_240x240` shim → `min_transition_delay = 1.0` (st7789e's current `clear_delay = 1`).
- `base_240x320` / `base_320x480` shims → `min_transition_delay = 0.1` (their current flat-loop behavior: no post-transition pause; 0.1 == the steady cadence, so no visible settle).

The 15 large-base drivers thus keep instant transitions; the 1 square driver (`st7789e`) keeps its 1 s settle — **all via the shims, zero driver edits.** Update `tests/ui/test_fixed_base_loop.py` to assert the now-unified richer loop for all three resolutions, with `clear_delay` = each resolution's `min_transition_delay` (1.0 for 240x240; 0.1 for the two large), and a comment noting the 15 large-base panels moved from a flat loop to the richer loop with a 0.1 s (i.e. effectively unchanged) transition settle.

- [ ] **Step 4: `_menu_display` None-guard — DECISION MADE**

Keep 240x240's `None`-guard `if self.in_data is None or self.in_data["primary_setpoint"] == 0:` in the merged `_menu_display` (a strict crash-safety improvement — the large bases `TypeError` if `in_data` is None before the first status frame). This is the phase's second small, deliberate, documented behavior change. For the OTHER menu diffs (explicit `return`s, `Stop`/`Power_` `clear_display()` vs nulling, `"Shutdown..."` vs `"Shutting Down..."`, `menu_active=True` on Hold entry), adopt the large bases' behavior (active `clear_display()`, `"Shutting Down..."`, no square-only `return`s, no extra `menu_active`) and document each choice in your report — these control-flow diffs are not golden-covered, so reason about each explicitly.

- [ ] **Step 5: Make `base_240x240.py` a shim**

```python
"""Compat shim: 240x240 fixed display base. Real implementation in base_fixed."""
from display.base_fixed import DisplayBase as _Base


class DisplayBase(_Base):
    _NOMINAL_WIDTH = 240
    _NOMINAL_HEIGHT = 240
    _SQUARE = True
    min_transition_delay = 1.0  # st7789e is a slow SPI panel; hold the first
    #                             frame after a transition so it can fully draw.
```

Also set `min_transition_delay = 0.1` explicitly on the `base_240x320` and `base_320x480` shims (from Task 4) so every resolution's transition timing is visible at the shim, not implicit via the base default — a one-line addition to each of those two shims.

- [ ] **Step 6: Re-baseline the 240x240 goldens — explicitly and enumerated**

The 240x240 render output legitimately changes only where Step 2's menu reconciliation altered a rendered frame (the pure-layout constants in Step 1 must reproduce the OLD 240x240 pixels — if a `_display_current`/`_display_splash`/`_display_text`/`_display_network` 240x240 hash changed, that is a LAYOUT BUG, not a re-baseline; fix it). Re-capture ONLY the genuinely-changed 240x240 cases:

Run: `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ui/test_fixed_base_golden.py -q -k "240x240"` and inspect which cases fail.
For each failing case, confirm it is a menu-reconciliation change (expected) and not a layout regression (bug). Then re-capture: `CAPTURE_GOLDEN=1 … -k "240x240"`, and in the commit message enumerate every 240x240 case whose hash changed and why.

Non-menu 240x240 render cases MUST still pass without re-baselining.

- [ ] **Step 7: Full suite + loop + golden**

Run: `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q`
Expected: green. `base_240x240.py`, `base_240x320.py`, `base_320x480.py` are now all shims; `base_fixed.py` is the single implementation.

- [ ] **Step 8: Commit**

```
refactor(display): fold 240x240 into base_fixed; unify the display loop

240x240's layout constants move into base_fixed's square-panel branch; the
three legacy bases are now shims over one implementation. The two loops are
unified to the richer (240x240) _display_loop, with the post-transition
settle exposed as a per-display `min_transition_delay` class attribute:
1.0s on the 240x240 shim (st7789e's existing pacing), 0.1s on the two large
shims (their existing instant-transition behavior) -- so steady-state update
rate is unchanged (~10fps, feeder-bound) and no display regresses. Second
deliberate change: the merged _menu_display keeps 240x240's None-guard, a
crash-fix for entering the Hold menu before the first status frame.
Re-baselined 240x240 golden cases: <enumerate each and why>. All non-menu
render output unchanged; the two large bases stay pixel-identical.
```

---

### Task 6: Driver load + instantiation regression + manifest check

Confirm all 16 drivers still import and instantiate against fakes through the shims, and that the wizard's driver list is unchanged. No driver files change in this phase; this task proves the shims are transparent.

**Files:**
- Create: `tests/ui/test_fixed_base_drivers_load.py`
- Reference (do not modify): the wizard manifest / driver registry (locate it — likely `wizard/` JSON or `display/`-scanned list; confirm with Serena `find_symbol`/`search_for_pattern`).

**Interfaces:**
- Consumes: the 16 driver modules and the shims.

- [ ] **Step 1: Enumerate the 16 drivers and write a load+instantiate test**

Parametrize over the 16 module paths (from the verified-facts driver table). For each: import the module, assert it has `Display`, and instantiate `Display(dev_pins={}, buttonslevel="HIGH", rotation=0, units="F", config={})` with `os.system` neutralized and hardware libs mocked as needed (mirror how `tests/ui/test_display_launch.py` / any existing driver-load test mocks luma/pygame/spi — locate the pattern with Serena first; the `st7789*` family reads `self.device.width/height` in `_init_display_device`, so their device must be mocked to expose those). Assert `WIDTH`/`HEIGHT` come out as expected per resolution+rotation.

For drivers that touch real SPI/pygame in `_init_display_device`, prefer instantiating with `_init_display_device` patched to a no-op (the base default) OR mock the hardware modules — whichever the existing driver-load tests already do. If a driver genuinely cannot be instantiated headless, assert import-only for that one and record it in the report.

- [ ] **Step 2: Assert the wizard/driver manifest is unchanged**

Locate the manifest that lists selectable displays (Serena `search_for_pattern` for the driver filenames or `display_map`/`modules` in the wizard/settings). Assert every one of the 16 driver identifiers is still present and resolvable. If the manifest is generated by scanning `display/`, assert the scan still yields the same set (the shims keep the module names, so it should).

- [ ] **Step 3: Run**

Run: `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ui/test_fixed_base_drivers_load.py -v`
Expected: all 16 load/instantiate (or import-only where noted); manifest unchanged.

- [ ] **Step 4: Full suite**

Run: `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q`
Expected: green; record the final total.

- [ ] **Step 5: Commit**

```
test(display): all 16 fixed-base drivers load + instantiate via the shims

Proves the base_240x240/240x320/320x480 shims are transparent to every
driver subclass and the wizard driver list is unchanged. No driver files
changed this phase; Phase C repoints them at base_fixed and removes the shims.
```

---

## Self-Review

- **Spec coverage (Phase B section):** snapshot harness prerequisite → Tasks 1–2; collapse into one `base_fixed.py` parameterized by `(WIDTH,HEIGHT)` with a per-resolution layout → Tasks 4–5; reconcile the drifted `_display_loop` (documented, sign-off-gated) → Task 5 Steps 3–4; pixel-identical for 320x480 and 240x320, explicit re-baseline for 240x240 → Tasks 4 (identical) and 5 Step 6 (re-baseline); drivers still import + manifest unchanged → Task 6. Drawing primitives collapse automatically because Task 4 moves the canonical body verbatim. All covered.
- **Deliberate-change gating:** the `_display_loop` reconciliation and the `None`-guard menu change are the phase's only behavior changes, both isolated to Task 5 and gated on human sign-off (Step 4), mirroring the design's "documented in the PR" and "called out and re-baselined explicitly" requirements. Everything else is proven behavior-preserving by the frozen Task 2 baseline.
- **Placeholder scan:** the only deferred-to-implementation content is the exact `CASES`/state-dict enumeration (Task 2 Step 1) and the per-method 240x240 constant transcription (Task 5 Step 1), both of which require diffing live method bodies at execution time and are explicitly instructed as such (with the verified layout-branch inventory as the checklist), not hand-waved.
- **Type/name consistency:** `make_base`/`render`/`FONT_AVAILABLE`/`SAMPLE_IN_DATA`/`SAMPLE_STATUS_DATA` (Task 1) are consumed unchanged in Tasks 2–3; `_NOMINAL_WIDTH`/`_NOMINAL_HEIGHT`/`_SQUARE` (Task 4) are used consistently in the Task 4–5 shims; `fixed_base_golden.json` + `CAPTURE_GOLDEN` are consistent across Tasks 2 and 5.
- **Ordering:** harness (1) → frozen baseline (2) and loop characterization (3) precede any production change; the two byte-identical large bases fold first (4, no re-baseline) to validate the extraction shape before the riskier 240x240 fold + reconciliation (5); driver/manifest regression last (6).
- **Risk note:** the pixel-hash approach depends on `trebuc.ttf`; the suite skips without it, and baseline-capture + verify happen on the same box, so the before/after gate is environment-consistent. If the executor's box lacks the font, Phase B cannot be verified there — flag it and run on a box that has it.
