# PiFire 1280×720 Dashboard Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reskin the 1280×720 local-display dashboard (QtQuick full-fidelity, pygame essential-motion) to match `docs/design/PiFire Dashboard.dc.html`, with a configurable Ember/Ice/Crimson accent and real Hold-mode auger/fan duty pills.

**Architecture:** One additive control-layer status field pair (`cycle_ratio`, `fan_duty`) feeds both stacks. QtQuick evolves its existing QML components in place (accent applied live via a `Theme.accent` binding to a new `backend.accentTheme`). pygame gets new, isolated flexobject types plus a bespoke 1280×720 layout emitted by `tools/generate_dsi_layout.py` (decoupled from the 800×480→1024×768 scaler so other resolutions are untouched).

**Tech Stack:** Python 3.14, PySide6 ≥ 6.11 (QtQuick/QML, QtQuick.Shapes, QtQuick.Effects), Pillow (pygame flex PIL renderer), pytest, ruff.

**Design reference:** `docs/design/PiFire Dashboard.dc.html` — the authoritative source for every color, size, radius, font weight, and animation. Visual-construction tasks transcribe values from it. The design's embedded `<script>` (fake `simTick` data) is reference only and is NOT ported.

**Spec:** `docs/superpowers/specs/2026-07-08-dashboard-redesign-design.md`.

## Global Constraints

- **Scope: 1280×720 only.** Do not modify the 800×480, 480×320, 1024×768, or 240×320 display layouts, nor the web dashboards. The pygame generator change must leave `dsi_1024x768t.json` byte-for-byte identical.
- **Never import PySide6/Qt in the control.py parent process** — Qt lives only in the display child (`display/qtapp.py`, `display/qtquick_flex.py`).
- **Run `ruff format` on every changed Python file before each commit** (standing repo rule).
- **Worktree only:** all work happens in this worktree; never `cd` to the main repo path.
- **Accent tokens (verbatim from the design):**
  - Ember — arc gradient `#ff5e1a → #ff8a2b → #ffc24b`, glow `#ff7a1a`, accent `#ff8a2b`.
  - Ice — arc gradient `#1f9fb8 → #35c7d0 → #7ef0d2`, glow `#2ec5d3`, accent `#3cc7d0`.
  - Crimson — arc gradient `#e11d48 → #ff5a4d → #ff9f43`, glow `#ff5a4d`, accent `#ff6a5a`.
- **Base palette (verbatim):** page bg `#0c0a09`, radial body bg `#241a12→#16110d→#0d0b09`, card `#1a1611`, inset `#14100c`, card border `rgba(255,255,255,0.05)`, text `#f4ede2`, dim text `#8a7f70`/`#7d7264`, label `#b7ac9c`, setpoint blue `#6cc8ff`, ok green `#5ec96f`, warn amber `#ffb020`, danger `#ff5a4d`.
- **Fonts:** Barlow (400/500/600/700) + Barlow Semi Condensed (500/600/700/800), OFL, bundled under `static/font/`.
- **Duty-pill rule:** Hold mode → `AUGER DUTY n%` / `FAN DUTY n%`; every other cooking mode → `P-MODE P-n` / `SMOKE+ ON|OFF`.
- **Gauge geometry (verbatim):** 270° sweep, arc start angle 135°, radius 90 in a 220×220 viewBox, `stroke-dasharray 424.12 565.49`; setpoint marker is a radial tick at the setpoint fraction.

---

## Conditional visibility (design `sc-if`/`sc-for` → plan)

Every appear/disappear directive in `docs/design/PiFire Dashboard.dc.html`, and where each is honored. This table is authoritative — a component is not "done" until its row's behavior is verified.

| Design directive | Meaning | QtQuick | pygame |
|---|---|---|---|
| `sc-if hasProbes` | left food-probe column appears only when food probes exist | Task 15: whole column `visible: backend.foodProbeCount > 0`; an invisible `RowLayout` child is dropped, so the center gauge (`Layout.fillWidth`) flexes into the space | Task 24/25: hide the `FOOD PROBES` label + every `probe_card_N` slot with no configured probe. Fixed layout → center does **not** reflow (documented compromise) |
| `sc-for probeCards` | one card per configured food probe | Task 15: `Repeater { model: backend.foodProbes }` | Task 25: up to 5 `probe_card_N` slots; unused ones hidden (Task 24) |
| `sc-if hasSetpoint` | gauge `SET n°` line only when setpoint > 0 | Task 8: line `visible: setpoint > 0` | Task 18: draw `SET n°` only when `sp > 0` |
| setpoint marker `spOpacity` | radial setpoint marker only when setpoint > 0 | Task 8: marker `visible: setpoint > 0` | Task 18: draw marker only when `sp > 0` |
| `sc-if lidOpen` | `LID OPEN` pill; cook-time bar reflows to full width when absent | Task 14/15: `Alert { visible: shown; shown: backend.lidOpen }` with a fixed `Layout.preferredWidth: 210`, and `CookTimeBar { Layout.fillWidth: true }` so cook-time expands when the alert is hidden | Task 24/25: a `lid_alert` object drawn only when `lid_open_detected`; the cook-time object keeps its fixed width (no reflow) |
| `sc-for btns` | mode-dependent control buttons (1, 2, or 4) | Task 14: `ControlPanel` `Repeater` over `Menus.controlPanelForMode(mode, recipe, recipePaused)`; each button `Layout.fillWidth: true` for even division regardless of count | Task 23/25: `button_row` renders the variable set, subdividing its width evenly by button count |

State-driven switches (not `sc-if`, but must still flip correctly):

| State | Behavior | QtQuick | pygame |
|---|---|---|---|
| Hold vs other cooking mode | duty pills → `AUGER/FAN DUTY` vs `P-MODE/SMOKE+` | Task 15 | Task 24 |
| fan/auger/igniter active | icon color + animation on only when active | Task 11 | Task 19 |
| cooking | header live dot green (else grey); mode glow animates only when cooking | Task 9 / Task 8 | Task 22 / Task 18 |
| hopper level | fill color + label by threshold (<15 red, <35 amber, else green) | Task 13 | Task 21 |

**Two behaviors the design does not specify — resolved in Open Decisions (see end of plan) before the affected task runs.**

---

## Phase 0 — Control-layer duty data

### Task 1: Publish `cycle_ratio` and `fan_duty` in status_data

**Files:**
- Modify: `controller/runtime/modes/base.py:567-575` (status_data assembly, inside the 0.5s publish gate)
- Modify: `common/common.py:2354-2372` (`read_status(init=True)` default dict)
- Test: `tests/test_control_mode_base.py` (add a test)

**Interfaces:**
- Produces: `status_data['cycle_ratio']` (float 0.0–1.0, rounded to 2dp) and `status_data['fan_duty']` (int 0–100) in every published status. Consumed later by `display/qtbackend.py` (Task 5) and `display/base_flex.py` (Task 24).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_control_mode_base.py` (reuses `_make_ctx`, `_RecordingMode`, and the one-bounded-tick clock trick already in the file):

```python
def test_status_publishes_duty_fields():
    ctx = _make_ctx()
    real_now = ctx.clock.now
    calls = {'n': 0}

    def _now():
        calls['n'] += 1
        return real_now() if calls['n'] == 1 else real_now() + 0.6

    ctx.clock.now = _now
    mode = _RecordingMode(ctx, WorkCycleState())
    mode.run()
    status = ctx.store.read_status()
    assert 'cycle_ratio' in status
    assert 'fan_duty' in status
    # Default state: no auger ratio set, DC fan disabled, fan output off.
    assert status['cycle_ratio'] == 0.0
    assert status['fan_duty'] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_control_mode_base.py::test_status_publishes_duty_fields -v`
Expected: FAIL with `KeyError: 'cycle_ratio'` (or assertion on missing key).

- [ ] **Step 3: Implement — publish the fields**

In `controller/runtime/modes/base.py`, immediately after the `status_data['outpins']` build loop (right after the `for item in self.settings['platform']['outputs']:` block completes, before `status_data.update(self.status_fragment())`):

```python
            status_data['cycle_ratio'] = round(self.state.cycle.ratio, 2)
            if self.settings['platform'].get('dc_fan'):
                status_data['fan_duty'] = int(control.get('duty_cycle', 0) or 0)
            else:
                status_data['fan_duty'] = 100 if status_data['outpins'].get('fan') else 0
```

In `common/common.py`, inside the `read_status(init=True)` dict (after `'outpins': {...},`), add:

```python
            'cycle_ratio': 0,
            'fan_duty': 0,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_control_mode_base.py -v`
Expected: PASS (all tests in file).

- [ ] **Step 5: ruff + commit**

```bash
ruff format controller/runtime/modes/base.py common/common.py tests/test_control_mode_base.py
git add controller/runtime/modes/base.py common/common.py tests/test_control_mode_base.py
git commit -m "feat(control): publish cycle_ratio and fan_duty in display status"
```

---

## Phase 1 — Accent-theme setting

### Task 2: Add `accent_theme` display option to the wizard manifest

**Files:**
- Modify: `wizard/wizard_manifest.json` — add an `accent_theme` config option to the `dsi_1280x720t` and `qtquick_dsi_1280x720t` entries under `modules.display` (each module's `config` array)
- Test: `tests/test_dsi_1280x720t_manifest.py` and `tests/test_qtquick_manifest.py` (add an assertion)

**Interfaces:**
- Produces: `settings['display']['config']['dsi_1280x720t']['accent_theme']` and `['qtquick_dsi_1280x720t']['accent_theme']`, default `"Ember"`. `_default_display_config()` (`common/common.py:360`) picks it up automatically. Read by pygame (Task 24) and Qt (Task 6).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_dsi_1280x720t_manifest.py`:

```python
def test_accent_theme_option_present():
    import json, os
    base = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    with open(os.path.join(base, 'wizard', 'wizard_manifest.json')) as f:
        manifest = json.load(f)
    opts = manifest['modules']['display']['dsi_1280x720t']['config']
    names = [o['option_name'] for o in opts]
    assert 'accent_theme' in names
    accent = next(o for o in opts if o['option_name'] == 'accent_theme')
    assert accent['default'] == 'Ember'
    assert set(accent['list_values']) == {'Ember', 'Ice', 'Crimson'}
```

Add the analogous `test_accent_theme_option_present` to `tests/test_qtquick_manifest.py` (using key `qtquick_dsi_1280x720t`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_dsi_1280x720t_manifest.py::test_accent_theme_option_present tests/test_qtquick_manifest.py::test_accent_theme_option_present -v`
Expected: FAIL (`StopIteration`/`KeyError`).

- [ ] **Step 3: Implement — add the option**

In `wizard/wizard_manifest.json`, inside `modules.display.dsi_1280x720t.config` (mirror the shape of the sibling `rotation` option already in that array), append:

```json
{
  "option_name": "accent_theme",
  "friendly_name": "Accent Theme",
  "description": "Dashboard accent color theme.",
  "hidden": false,
  "option_type": "list",
  "list_values": ["Ember", "Ice", "Crimson"],
  "list_labels": ["Ember", "Ice", "Crimson"],
  "default": "Ember"
}
```

(Match the exact key names used by the neighboring `rotation` option in the same file — e.g. if it uses `"option_type"` vs `"type"`, follow that. `_default_display_config` only reads `option_name` and `default`, so those two are load-bearing; the rest drive the wizard UI.)

Add the identical block to `modules.display.qtquick_dsi_1280x720t.config`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_dsi_1280x720t_manifest.py tests/test_qtquick_manifest.py -v`
Expected: PASS.

- [ ] **Step 5: commit** (JSON only, no ruff needed)

```bash
git add wizard/wizard_manifest.json tests/test_dsi_1280x720t_manifest.py tests/test_qtquick_manifest.py
git commit -m "feat(display): add accent_theme display setting (Ember/Ice/Crimson)"
```

---

## Phase 2 — Fonts & background asset

### Task 3: Bundle Barlow fonts

**Files:**
- Create: `static/font/Barlow-Regular.ttf`, `Barlow-Medium.ttf`, `Barlow-SemiBold.ttf`, `Barlow-Bold.ttf`, `BarlowSemiCondensed-Medium.ttf`, `BarlowSemiCondensed-SemiBold.ttf`, `BarlowSemiCondensed-Bold.ttf`, `BarlowSemiCondensed-ExtraBold.ttf`
- Create: `static/font/OFL.txt` (the Barlow license)
- Test: `tests/test_fonts_present.py`

**Interfaces:**
- Produces: font files at known paths. QtQuick loads them (Task 7); pygame references them by absolute path (Tasks 17–23).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_fonts_present.py
import os

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
FONTS = [
    'Barlow-Regular.ttf', 'Barlow-Medium.ttf', 'Barlow-SemiBold.ttf', 'Barlow-Bold.ttf',
    'BarlowSemiCondensed-Medium.ttf', 'BarlowSemiCondensed-SemiBold.ttf',
    'BarlowSemiCondensed-Bold.ttf', 'BarlowSemiCondensed-ExtraBold.ttf',
]

def test_barlow_fonts_bundled():
    for name in FONTS:
        p = os.path.join(BASE, 'static', 'font', name)
        assert os.path.exists(p), f'missing {name}'
        assert os.path.getsize(p) > 1000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_fonts_present.py -v`
Expected: FAIL (missing files).

- [ ] **Step 3: Obtain and place the fonts**

Download the Barlow family (OFL) from Google Fonts and copy the eight static TTFs above into `static/font/`, plus the `OFL.txt`. Example:

```bash
cd "$(git rev-parse --show-toplevel)"
tmp=$(mktemp -d)
curl -L -o "$tmp/barlow.zip" "https://fonts.google.com/download?family=Barlow"
unzip -o "$tmp/barlow.zip" -d "$tmp/barlow"
cp "$tmp/barlow"/static/Barlow-Regular.ttf static/font/
cp "$tmp/barlow"/static/Barlow-Medium.ttf static/font/
cp "$tmp/barlow"/static/Barlow-SemiBold.ttf static/font/
cp "$tmp/barlow"/static/Barlow-Bold.ttf static/font/
cp "$tmp/barlow"/static/BarlowSemiCondensed-Medium.ttf static/font/
cp "$tmp/barlow"/static/BarlowSemiCondensed-SemiBold.ttf static/font/
cp "$tmp/barlow"/static/BarlowSemiCondensed-Bold.ttf static/font/
cp "$tmp/barlow"/static/BarlowSemiCondensed-ExtraBold.ttf static/font/
cp "$tmp/barlow"/OFL.txt static/font/
```

If network access is unavailable in the build environment: STOP and report. Fallback is to use a nearest-available condensed system face (adjust `Theme.fontFamily` in Task 7 and the pygame font paths in Tasks 17–23 to the fallback), and note the substitution in the task's commit message. Do not silently skip — the layout tokens are face-independent but the plan must record which face shipped.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_fonts_present.py -v`
Expected: PASS.

- [ ] **Step 5: commit**

```bash
git add static/font/ tests/test_fonts_present.py
git commit -m "chore(display): bundle Barlow + Barlow Semi Condensed fonts (OFL)"
```

### Task 4: Generate the ember 1280×720 background asset

**Files:**
- Create: `tools/generate_ember_background.py` (one-shot generator, committed for reproducibility)
- Create: `static/img/display/background_ember_1280x720.png`
- Test: `tests/test_ember_background.py`

**Interfaces:**
- Produces: a 1280×720 PNG matching the design's radial body gradient (`radial-gradient(120% 90% at 50% 118%, #241a12 0%, #16110d 42%, #0d0b09 100%)`). Referenced by the pygame generator (Task 25) as `metadata.dash_background`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ember_background.py
import os
from PIL import Image

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
PATH = os.path.join(BASE, 'static', 'img', 'display', 'background_ember_1280x720.png')

def test_ember_background_dimensions():
    assert os.path.exists(PATH)
    with Image.open(PATH) as im:
        assert im.size == (1280, 720)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ember_background.py -v`
Expected: FAIL (missing file).

- [ ] **Step 3: Implement the generator and run it**

```python
# tools/generate_ember_background.py
"""Render the ember radial-gradient dashboard background (1280x720)."""
import os
from PIL import Image

W, H = 1280, 720
# radial-gradient(120% 90% at 50% 118%, #241a12 0%, #16110d 42%, #0d0b09 100%)
CX, CY = 0.50 * W, 1.18 * H
RX, RY = 1.20 * W, 0.90 * H
STOPS = [(0.00, (0x24, 0x1a, 0x12)), (0.42, (0x16, 0x11, 0x0d)), (1.00, (0x0d, 0x0b, 0x09))]


def _lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _sample(frac):
    for i in range(len(STOPS) - 1):
        p0, c0 = STOPS[i]
        p1, c1 = STOPS[i + 1]
        if frac <= p1:
            t = 0 if p1 == p0 else (frac - p0) / (p1 - p0)
            return _lerp(c0, c1, t)
    return STOPS[-1][1]


def main():
    img = Image.new('RGB', (W, H))
    px = img.load()
    for y in range(H):
        for x in range(W):
            d = (((x - CX) / RX) ** 2 + ((y - CY) / RY) ** 2) ** 0.5
            px[x, y] = _sample(min(d, 1.0))
    base = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    out = os.path.join(base, 'static', 'img', 'display', 'background_ember_1280x720.png')
    img.save(out)
    print(f'Wrote {out}')


if __name__ == '__main__':
    main()
```

Run: `python tools/generate_ember_background.py`

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_ember_background.py -v`
Expected: PASS.

- [ ] **Step 5: ruff + commit**

```bash
ruff format tools/generate_ember_background.py tests/test_ember_background.py
git add tools/generate_ember_background.py static/img/display/background_ember_1280x720.png tests/test_ember_background.py
git commit -m "feat(display): add ember dashboard background (1280x720)"
```

---

## Phase 3 — QtQuick (full fidelity)

### Task 5: Backend `augerDuty` / `fanDuty` properties

**Files:**
- Modify: `display/qtbackend.py` (init, `poll`, properties)
- Test: `tests/test_qtbackend.py`

**Interfaces:**
- Consumes: `status['cycle_ratio']`, `status['fan_duty']` (Task 1); `status['startup_timestamp']`, `status['mode']`; `probe_info['food']` (constructor).
- Produces: `backend.augerDuty` (int %, `round(cycle_ratio*100)`), `backend.fanDuty` (int %), both `notify=statusChanged`; `backend.foodProbeCount` (int, `constant` — `len(probe_info['food'])`); `backend.cookElapsedText` (str `H:MM:SS`/`00:00`, `notify=timerChanged` — D2). `augerDuty`/`fanDuty` → `DutyPill` (Task 12); `foodProbeCount` → `DashScreen` (Task 15, collapse food column); `cookElapsedText` → `CookTimeBar` (Task 14).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_qtbackend.py`:

```python
def test_poll_exposes_duty_cycles():
    in_data = {'P': {'Grill': 225}, 'F': {}, 'AUX': {}, 'PSP': 250, 'NT': {}}
    status = {'mode': 'Hold', 'units': 'F', 'outpins': {'fan': True},
              'cycle_ratio': 0.35, 'fan_duty': 100}
    b = make_backend(in_data, status)
    b.poll()
    assert b.augerDuty == 35
    assert b.fanDuty == 100


def test_food_probe_count_reflects_config():
    # PROBE_INFO has one food probe.
    b = make_backend({'P': {}, 'F': {}, 'AUX': {}, 'PSP': 0, 'NT': {}}, {'mode': 'Stop', 'units': 'F', 'outpins': {}})
    assert b.foodProbeCount == 1
    none = PiFireBackend(lambda: (None, None), lambda c, d: None,
                         {'primary': {'name': 'Grill'}, 'food': [], 'aux': []})
    assert none.foodProbeCount == 0


def test_cook_elapsed_text_counts_up_else_zero():
    status = {'mode': 'Smoke', 'units': 'F', 'outpins': {}, 'startup_timestamp': 1000.0}
    b = make_backend({'P': {}, 'F': {}, 'AUX': {}, 'PSP': 0, 'NT': {}}, status)
    b._now = lambda: 1000.0 + 125  # 2:05 elapsed
    b.poll()
    assert b.cookElapsedText == '02:05'
    b._fetch_fn = lambda: (
        {'P': {}, 'F': {}, 'AUX': {}, 'PSP': 0, 'NT': {}},
        {'mode': 'Stop', 'units': 'F', 'outpins': {}, 'startup_timestamp': 0},
    )
    b.poll()
    assert b.cookElapsedText == '00:00'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_qtbackend.py::test_poll_exposes_duty_cycles -v`
Expected: FAIL (`AttributeError: augerDuty`).

- [ ] **Step 3: Implement**

In `display/qtbackend.py` `__init__`, add near the other status fields (the food count is derived once from `probe_info`, which is fixed at construction):

```python
        self._auger_duty = 0
        self._fan_duty = 0
        self._food_count = len(self._probe_info.get('food', []))
        self._cook_elapsed_text = '00:00'
```

In `poll()`, after the existing `_set('_s_plus', ...)` line:

```python
        self._set('_auger_duty', int(round((status.get('cycle_ratio', 0) or 0) * 100)), self.statusChanged)
        self._set('_fan_duty', int(status.get('fan_duty', 0) or 0), self.statusChanged)
```

In `poll()`, after the existing `self._update_timer_text(status, now)` call, add the elapsed update:

```python
        self._update_cook_elapsed(status, now)
```

Add the helper method (near `_update_timer_text`):

```python
    def _update_cook_elapsed(self, status, now):
        ts = status.get('startup_timestamp', 0) or 0
        if ts and status.get('mode', 'Stop') not in ('Stop', 'Monitor'):
            secs = max(int(now - ts), 0)
            h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
            text = (f'{h}:' if h else '') + f'{m:02d}:{s:02d}'
        else:
            text = '00:00'
        self._set('_cook_elapsed_text', text, self.timerChanged)
```

Add the properties near `pMode`:

```python
    @Property(int, notify=statusChanged)
    def augerDuty(self):
        return self._auger_duty

    @Property(int, notify=statusChanged)
    def fanDuty(self):
        return self._fan_duty

    @Property(int, constant=True)
    def foodProbeCount(self):
        return self._food_count

    @Property(str, notify=timerChanged)
    def cookElapsedText(self):
        return self._cook_elapsed_text
```

- [ ] **Step 4: Run test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_qtbackend.py -v`
Expected: PASS.

- [ ] **Step 5: ruff + commit**

```bash
ruff format display/qtbackend.py tests/test_qtbackend.py
git add display/qtbackend.py tests/test_qtbackend.py
git commit -m "feat(qtdisplay): expose augerDuty/fanDuty on the backend"
```

### Task 6: Live `accentTheme` backend property + app wiring

**Files:**
- Modify: `display/qtbackend.py` (`__init__` signature + fields, `poll`, signal, property)
- Modify: `display/qtapp.py` (`build_backend` — inject `accent_fn`)
- Test: `tests/test_qtbackend.py`

**Interfaces:**
- Consumes: an injected `accent_fn() -> str` (throttled read of the accent setting).
- Produces: `backend.accentTheme` (str, `notify=accentThemeChanged`), refreshed at most once/second inside `poll()`. Consumed by `Main.qml` → `Theme.accent` (Task 7).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_qtbackend.py`:

```python
def test_accent_theme_updates_live_and_throttles():
    state = {'accent': 'Ember'}
    b = PiFireBackend(
        lambda: ({'P': {}, 'F': {}, 'AUX': {}, 'PSP': 0, 'NT': {}}, {'mode': 'Stop', 'units': 'F', 'outpins': {}}),
        lambda c, d: None,
        PROBE_INFO,
        accent_fn=lambda: state['accent'],
    )
    clock = {'t': 1000.0}
    b._now = lambda: clock['t']
    events = []
    b.accentThemeChanged.connect(lambda: events.append(b.accentTheme))
    b.poll()
    assert b.accentTheme == 'Ember'
    # Change within 1s window: not yet re-read.
    state['accent'] = 'Ice'
    clock['t'] = 1000.5
    b.poll()
    assert b.accentTheme == 'Ember'
    # After >1s: picked up + signal fired.
    clock['t'] = 1002.0
    b.poll()
    assert b.accentTheme == 'Ice'
    assert 'Ice' in events
```

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_qtbackend.py::test_accent_theme_updates_live_and_throttles -v`
Expected: FAIL (`TypeError: unexpected keyword 'accent_fn'`).

- [ ] **Step 3: Implement**

In `display/qtbackend.py`, add the signal beside the others:

```python
    accentThemeChanged = Signal()
```

Change the constructor signature to `def __init__(self, fetch_fn, command_fn, probe_info, accent_fn=None, parent=None):` and add in the body:

```python
        self._accent_fn = accent_fn
        self._accent_theme = 'Ember'
        self._last_accent_check = 0.0
```

In `poll()`, after `self._update_idle(mode, now)` (reuse the `now` already computed at `now = self._now()`):

```python
        if self._accent_fn is not None and (now - self._last_accent_check) >= 1.0:
            self._last_accent_check = now
            self._set('_accent_theme', self._accent_fn() or 'Ember', self.accentThemeChanged)
```

Add the property:

```python
    @Property(str, notify=accentThemeChanged)
    def accentTheme(self):
        return self._accent_theme
```

In `display/qtapp.py` `build_backend`, add the accent reader and pass it in:

```python
def build_backend(config):
    """Construct the backend wired to the framework's data + command layer."""
    from display.qtquick_flex import Display
    from common import read_settings_valkey

    def _accent_fn():
        try:
            s = read_settings_valkey()
            module = s['modules']['display']
            return s['display']['config'][module].get('accent_theme', 'Ember')
        except Exception:
            return 'Ember'

    dispatcher = Display.for_dispatch(config, config.get('units', 'F'))
    backend = PiFireBackend(_fetch, dispatcher._dispatch_command, config.get('probe_info', {}), accent_fn=_accent_fn)
    backend._accent_theme = config.get('accent_theme', 'Ember')
    backend._ip_address = config.get('ip_address', '') or backend.ipAddress
    return backend
```

- [ ] **Step 4: Run test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_qtbackend.py -v`
Expected: PASS.

- [ ] **Step 5: ruff + commit**

```bash
ruff format display/qtbackend.py display/qtapp.py tests/test_qtbackend.py
git add display/qtbackend.py display/qtapp.py tests/test_qtbackend.py
git commit -m "feat(qtdisplay): expose live accentTheme from the accent setting"
```

### Task 7: Theme tokens, FontLoader, and live accent binding

**Files:**
- Modify: `display/qml/Theme.qml` (extend the singleton)
- Create: `display/qml/Fonts.qml` (singleton FontLoaders) + register in `display/qml/qmldir`
- Modify: `display/qml/Main.qml` (bind `Theme.accent` to `backend.accentTheme`)
- Test: `tests/test_qml_load.py`

**Interfaces:**
- Produces: `Theme.accent` (writable string), and readonly derived tokens used by every component:
  `Theme.accentColor` (color), `Theme.glowColor` (color), `Theme.arcStops` (list of `{position, color}` or three color props `arcStop0/1/2`), plus base palette tokens (`Theme.page`, `Theme.card`, `Theme.inset`, `Theme.cardBorder`, `Theme.textColor`, `Theme.dim`, `Theme.label`, `Theme.setpoint`, `Theme.okColor`, `Theme.warn`, `Theme.dangerColor`), sizing (`Theme.cardRadius: 18`, `Theme.pillRadius: 999`), and fonts (`Theme.sans` = Barlow family, `Theme.condensed` = Barlow Semi Condensed family). `Fonts.sans`/`Fonts.condensed` expose the loaded family names.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_qml_load.py` (follow the file's existing offscreen-load helper; if it exposes `load_qml(path)` use it, else mirror its engine setup):

```python
def test_theme_exposes_accent_tokens(qml_engine):
    # Load Theme singleton and assert the new accent tokens resolve for each accent.
    from PySide6.QtQml import QQmlComponent
    from PySide6.QtCore import QUrl
    import os
    qml_dir = os.path.join(os.path.dirname(__file__), '..', 'display', 'qml')
    comp = QQmlComponent(qml_engine, QUrl.fromLocalFile(os.path.join(qml_dir, 'Theme.qml')))
    theme = comp.create()
    assert theme is not None, comp.errorString()
    theme.setProperty('accent', 'Ember')
    assert theme.property('accentColor') is not None
    theme.setProperty('accent', 'Ice')
    assert theme.property('accentColor') is not None
```

If `tests/test_qml_load.py` has no `qml_engine` fixture, add one that sets `QT_QPA_PLATFORM=offscreen` and yields a `QQmlEngine` with `addImportPath(display/qml)` — mirror `tests/test_qtquick_display.py`'s engine construction.

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_qml_load.py::test_theme_exposes_accent_tokens -v`
Expected: FAIL (`accentColor` is undefined/null).

- [ ] **Step 3: Implement**

Rewrite `display/qml/Theme.qml` (keep it a singleton). Example structure:

```qml
pragma Singleton
import QtQuick

QtObject {
    // Selected accent — bound live from backend.accentTheme in Main.qml.
    property string accent: "Ember"

    // Base palette (design-verbatim)
    readonly property color page:        "#0c0a09"
    readonly property color card:        "#1a1611"
    readonly property color inset:       "#14100c"
    readonly property color cardBorder:  Qt.rgba(1, 1, 1, 0.05)
    readonly property color textColor:   "#f4ede2"
    readonly property color dim:         "#8a7f70"
    readonly property color label:       "#7d7264"
    readonly property color probeLabel:  "#b7ac9c"
    readonly property color setpoint:    "#6cc8ff"
    readonly property color okColor:     "#5ec96f"
    readonly property color warn:        "#ffb020"
    readonly property color dangerColor: "#ff5a4d"

    // Accent-derived tokens
    readonly property color accentColor: accent === "Ice" ? "#3cc7d0" : accent === "Crimson" ? "#ff6a5a" : "#ff8a2b"
    readonly property color glowColor:   accent === "Ice" ? "#2ec5d3" : accent === "Crimson" ? "#ff5a4d" : "#ff7a1a"
    readonly property color arcStop0:    accent === "Ice" ? "#1f9fb8" : accent === "Crimson" ? "#e11d48" : "#ff5e1a"
    readonly property color arcStop1:    accent === "Ice" ? "#35c7d0" : accent === "Crimson" ? "#ff5a4d" : "#ff8a2b"
    readonly property color arcStop2:    accent === "Ice" ? "#7ef0d2" : accent === "Crimson" ? "#ff9f43" : "#ffc24b"

    // Sizing
    readonly property int cardRadius: 18
    readonly property int animMs: 250

    // Fonts (from the Fonts singleton)
    readonly property string sans: Fonts.sans
    readonly property string condensed: Fonts.condensed

    // Back-compat aliases for existing menu/input components:
    readonly property color background: page
    readonly property color surface: card
    readonly property color primary: setpoint
    readonly property color notify: "#ffff00"
    readonly property color text: textColor
    readonly property color subtext: dim
    readonly property color danger: dangerColor
    readonly property color ok: okColor
    readonly property int radius: cardRadius
    readonly property string fontFamily: sans
}
```

Create `display/qml/Fonts.qml`:

```qml
pragma Singleton
import QtQuick

QtObject {
    property FontLoader _barlow: FontLoader { source: "../../static/font/Barlow-SemiBold.ttf" }
    property FontLoader _barlowSemi: FontLoader { source: "../../static/font/BarlowSemiCondensed-Bold.ttf" }
    readonly property string sans: _barlow.status === FontLoader.Ready ? _barlow.name : "sans-serif"
    readonly property string condensed: _barlowSemi.status === FontLoader.Ready ? _barlowSemi.name : sans
}
```

(Load the remaining weights with additional `FontLoader`s if per-weight family names are needed; on most platforms one load per family registers the family and `font.weight` selects the weight. Verify the `source` relative path resolves from `display/qml/`; adjust to an absolute `file:` path built from a context property if needed.)

Register both singletons in `display/qml/qmldir`:

```
singleton Theme 1.0 Theme.qml
singleton Fonts 1.0 Fonts.qml
```

In `display/qml/Main.qml`, add inside the `Window` (after the `StackView`) a live binding:

```qml
    Binding { target: Theme; property: "accent"; value: backend ? backend.accentTheme : "Ember" }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_qml_load.py -v`
Expected: PASS.

- [ ] **Step 5: commit**

```bash
git add display/qml/Theme.qml display/qml/Fonts.qml display/qml/qmldir display/qml/Main.qml tests/test_qml_load.py
git commit -m "feat(qtdisplay): ember Theme tokens, Barlow fonts, live accent binding"
```

### Task 8: Restyle `Gauge.qml` (ember arc + setpoint marker + glow + center)

**Files:**
- Modify: `display/qml/components/Gauge.qml`
- Test: `tests/test_qml_load.py`

**Interfaces:**
- Consumes: existing props `value, setpoint, target, maxValue, label, units, probeName`, signal `tapped()`; new Theme tokens (Task 7).
- Produces: unchanged public API (DashScreen still sets the same props). Visual only.

- [ ] **Step 1: Write the failing test** — add a load+instantiate assertion in `tests/test_qml_load.py` that creates `Gauge.qml` with `value: 225; maxValue: 600; setpoint: 250` and asserts it instantiates (`comp.create() is not None`, `comp.errorString()` empty).

- [ ] **Step 2: Run** — Expected FAIL only if the component has an error; if it currently loads, first make the test assert a NEW element added below (e.g. an `objectName: "setpointMarker"` child) so it is red before implementation.

- [ ] **Step 3: Implement** — Transcribe the design's central gauge (design file, the center-column gauge card `<svg viewBox="0 0 220 220">` block):
  - Keep the two `QtQuick.Shapes` arcs (track `#2a241d`, value arc). Set the value arc stroke to a gradient built from `Theme.arcStop0/1/2` (use `ShapePath.fillGradient` with a `ConicalGradient`/`LinearGradient`, or approximate with `strokeColor: Theme.accentColor` if gradient-on-stroke is unavailable — a solid accent stroke with a glow is acceptable within full-fidelity here since QML stroke gradients are limited).
  - Add a **setpoint marker**: a short radial line at angle `135 + 270 * clamp(setpoint/maxValue,0,1)` degrees, color `Theme.setpoint`, visible when `setpoint > 0` (give it `objectName: "setpointMarker"`). **Angle convention (preview-verified):** `PathAngleArc` and this marker both measure the angle **clockwise from 3 o'clock with screen y-down** — draw the marker as a `Shape` `ShapePath` line between `(cx + (r-13)·cosθ, cy + (r-13)·sinθ)` and `(cx + (r+9)·cosθ, cy + (r+9)·sinθ)` (θ in radians). Do **not** place it by rotating a 12-o'clock item — that lands ~90° off (early bug in the preview).
  - Add a pulsing **glow** behind the arc: a blurred disc via `QtQuick.Effects` `MultiEffect` (blur) or a semi-transparent `Rectangle`/`Shape` with a `SequentialAnimation on opacity` (0.30↔0.62, 3.2s) gated on cooking.
  - Center `Column`: `GRILL` label (Theme.label, condensed/sans), big temp (`Math.round(value)`, Barlow Semi Condensed ExtraBold), `°` + units, `SET n°` (Theme.setpoint, visible when setpoint>0), and the **mode pill** (rounded, accent-tinted bg/border, `Theme.accentColor` text). Pull the mode text from a new `mode` prop passed by DashScreen (Task 15) or keep the existing ModeBar separate — this task keeps the pill fed by a new `modeLabel` string prop defaulting to "".
  - **MultiEffect availability:** before relying on `import QtQuick.Effects`, load it in isolation (a scratch QML with a `MultiEffect`); if it fails on the deployed PySide6, use the animated semi-transparent `Rectangle` glow fallback and note it in the commit.

- [ ] **Step 4: Run** — `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_qml_load.py -v` → PASS.

- [ ] **Step 5: commit** — `git add display/qml/components/Gauge.qml tests/test_qml_load.py && git commit -m "feat(qtdisplay): restyle primary gauge (ember arc, setpoint, glow, mode pill)"`

### Task 9: `HeaderBar.qml` (live dot, logo, IP, clock, hamburger)

**Files:**
- Create: `display/qml/components/HeaderBar.qml`
- Test: `tests/test_qml_load.py`

**Interfaces:**
- Consumes: `backend.ipAddress`, `backend.mode` (for live-dot color); a self-contained `Timer` for the clock.
- Produces: signal `menuRequested()`; height 58. Consumed by DashScreen (Task 15).

- [ ] **Step 1: Write the failing test** — load `HeaderBar.qml`, assert instantiates and exposes `menuRequested` signal.
- [ ] **Step 2: Run** → FAIL (file missing).
- [ ] **Step 3: Implement** — Transcribe the design header (the 58px top `<div>`): live dot `Rectangle` (radius, color `cooking ? Theme.okColor : Theme.label`, `SequentialAnimation on opacity` 2.4s blink), `Pi` + accent `Fire` wordmark (`Theme.sans` 20 bold), `CONTROLLER` label, right side: IP `Text { text: backend.ipAddress }`, clock:

```qml
property string clock: ""
Timer { interval: 1000; running: true; repeat: true; triggeredOnStart: true
        onTriggered: header.clock = Qt.formatTime(new Date(), "hh:mm") }
```

hamburger button (three bars) with `TapHandler { onTapped: header.menuRequested() }`. `cooking` = `["Startup","Reignite","Smoke","Hold","Recipe"].indexOf(backend.mode) >= 0`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: commit** — `git commit -m "feat(qtdisplay): add HeaderBar (live dot, logo, IP, clock, menu)"`

### Task 10: `ProbeCard.qml`

**Files:**
- Create: `display/qml/components/ProbeCard.qml`
- Test: `tests/test_qml_load.py`

**Interfaces:**
- Consumes: `name, temp, target, maxTemp, units` (from the `foodProbes` model roles).
- Produces: signal `tapped()`. Consumed by DashScreen (Task 15).

- [ ] **Step 1–2:** failing load test (file missing).
- [ ] **Step 3:** Transcribe the design probe card (left column `<sc-for>` card): name (uppercase, Theme.probeLabel), target string (`→ N°` / `AMBIENT`, color `done ? Theme.okColor : target>0 ? "#ffd23f" : Theme.label`), big temp (Barlow Semi Condensed ExtraBold, Theme.textColor), progress bar (height 6, `width = clamp(temp/target)`, color `done ? Theme.okColor : Theme.accentColor`, `Behavior on width { NumberAnimation }`). `done = target>0 && temp >= target-1`. `TapHandler { onTapped: card.tapped() }`.
- [ ] **Step 4–5:** load test PASS → `git commit -m "feat(qtdisplay): add ProbeCard"`

### Task 11: `SystemCard.qml` + `FanIcon` / `AugerIcon` / `IgniterIcon`

**Files:**
- Create: `display/qml/components/SystemCard.qml`, `FanIcon.qml`, `AugerIcon.qml`, `IgniterIcon.qml`
- Test: `tests/test_qml_load.py`

**Interfaces:**
- Consumes: `backend.fanOn/augerOn/igniterOn`, `backend.toggleFan/toggleAuger/toggleIgniter`, `Theme.accentColor`.
- Produces: `SystemCard` renders three rows; each row taps its toggle. Consumed by DashScreen (Task 15).

- [ ] **Step 1–2:** failing load test.
- [ ] **Step 3:** Transcribe the design "System" card and its three inline SVGs into `QtQuick.Shapes`:
  - `FanIcon` — three-blade fan `Shape` (`PathSvg` from the design's `<path d="M50 50 Q...">` blades), `RotationAnimation on rotation { running: active; from:0; to:360; duration:850; loops: Animation.Infinite }`.
  - `AugerIcon` — the clipped screw + falling pellets; feed translate `NumberAnimation` + two pellet `SequentialAnimation`s (design keyframes `pf-augerFeed`, `pf-pellet`), running when `active`.
  - `IgniterIcon` — coil `Shape` with `pf-flicker` opacity `SequentialAnimation` and rising heat waves (`pf-heat`), running when `active`.
  Each row: icon + label (`FAN`/`AUGER`/`IGNITER`) + status text (`RUNNING`/`IDLE`, etc., color `active ? Theme.accentColor : Theme.label`) + status dot; `TapHandler` → the matching backend toggle; row border tinted when active.
- [ ] **Step 4–5:** load test PASS → `git commit -m "feat(qtdisplay): add animated SystemCard (fan/auger/igniter)"`

### Task 12: `DutyPill.qml` (mode-aware)

**Files:**
- Create: `display/qml/components/DutyPill.qml`
- Test: `tests/test_qml_load.py`

**Interfaces:**
- Consumes: `label`, `value`, `highlighted` (bool) props set by DashScreen from `backend.mode`/`augerDuty`/`fanDuty`/`pMode`/`smokePlus`.
- Produces: a labeled value pill. Two instances in DashScreen (Task 15).

- [ ] **Step 1–2:** failing load test.
- [ ] **Step 3:** Transcribe the design's two pill `<div>`s: label (uppercase small), value (Barlow Semi Condensed ExtraBold), bg/border tinted when `highlighted` (ok-green tint) else neutral card. Pure presentational; DashScreen computes the mode-aware content.
- [ ] **Step 4–5:** load PASS → `git commit -m "feat(qtdisplay): add DutyPill"`

### Task 13: `HopperCard.qml`

**Files:**
- Create: `display/qml/components/HopperCard.qml`
- Test: `tests/test_qml_load.py`

**Interfaces:**
- Consumes: `backend.hopperLevel`, `backend.hopperEnabled`, `backend.hopperCheck()`.
- Produces: signal `checkRequested()`. Consumed by DashScreen (Task 15).

- [ ] **Step 1–2:** failing load test.
- [ ] **Step 3:** Transcribe the design hopper card: header (`HOPPER` label + `n%` big number in threshold color), vertical fill (`Rectangle` anchored bottom, `height = parent.height * level/100`, gradient by threshold, `Behavior on height`), status label (`LEVEL OK`/`RUNNING LOW`/`REFILL PELLETS`). Thresholds: `level<15` red, `<35` amber, else green (color function `hopperColor(level)` as a JS function on the item). `TapHandler` → `checkRequested()`. Whole card `visible: backend.hopperEnabled`.
- [ ] **Step 4–5:** load PASS → `git commit -m "feat(qtdisplay): add HopperCard (vertical fill)"`

### Task 14: `CookTimeBar.qml` + `Alert` restyle + `ControlPanel` restyle

**Files:**
- Create: `display/qml/components/CookTimeBar.qml`
- Modify: `display/qml/components/Alert.qml`, `display/qml/components/ControlPanel.qml`
- Test: `tests/test_qml_load.py`

**Interfaces:**
- `CookTimeBar` (D2): shows the active countdown when running, else elapsed cook time. Label + value bind as `backend.timerText.length > 0 ? backend.timerLabel : "COOK TIME"` and `backend.timerText.length > 0 ? backend.timerText : backend.cookElapsedText`. `Layout.fillWidth: true` so it expands when the LID OPEN alert is hidden.
- `Alert` restyled to the design's blinking `LID OPEN` pill (keep props `message`, `shown`); `visible: shown`, fixed `Layout.preferredWidth: 210` so it takes the design's fixed slot and the cook-time bar reflows to full width when it's hidden.
- `ControlPanel` restyled to the design's large bordered buttons; **data-driven button set unchanged** (`Menus.controlPanelForMode(mode, recipe, recipePaused)`), signals `openMenu`/`openInput` unchanged.

- [ ] **Step 1–2:** failing load test (assert `CookTimeBar.qml` instantiates; assert restyled `Alert`/`ControlPanel` still instantiate with their existing props).
- [ ] **Step 3:** Transcribe the design's cook-time bar and lid-open pill (the center-column `Cook Time` row + `LID OPEN` block) and the control-buttons row styling (`border:2px solid {{b.border}}; background:{{b.bg}}; color:{{b.text}}`, radius 16, Barlow bold 25). Keep `ControlPanel`'s `Repeater`/`Actions.activate` wiring intact — only restyle the `MenuButton` visuals (accent from `Theme.accentColor`, danger `Theme.dangerColor`, ok `Theme.okColor`).
- [ ] **Step 4–5:** load PASS → `git commit -m "feat(qtdisplay): restyle cook-time, lid alert, control buttons"`

### Task 15: Rewrite `DashScreen.qml`

**Files:**
- Modify: `display/qml/screens/DashScreen.qml`
- Test: `tests/test_qml_load.py`, `tests/test_qtquick_parity.py`, `tests/test_qtquick_display.py`

**Interfaces:**
- Consumes: all components above + `backend`.
- Produces: unchanged screen signals `requestMenu(string)`, `requestInput(string, origin)` (Main.qml depends on them).

- [ ] **Step 1: Write the failing test** — extend `tests/test_qml_load.py` to load the full `Main.qml` engine and assert `DashScreen` instantiates and still declares `requestMenu`/`requestInput`. If `tests/test_qtquick_parity.py` asserts a set of dash element `objectName`s, update its expected set to the new components (HeaderBar, ProbeCard×N, Gauge, CookTimeBar, ControlPanel, SystemCard, DutyPill×2, HopperCard).
- [ ] **Step 2: Run** → FAIL against the new expectations.
- [ ] **Step 3: Implement** — Rebuild the layout to the design's header + 3-column structure.

  **Layout sizing (critical — verified by rendering `tools/qt_dashboard_preview.qml` with `--shot`):** a nested `ColumnLayout`/`RowLayout` computes its **own implicit size from its children**, and that overrides `Layout.preferredWidth`/`preferredHeight` (which are only advisory). Left unpinned, the side columns balloon to ~570px and collapse the center to a sliver (gauge clipped to vertical lines, buttons/pills overgrown). `minimumWidth`/`maximumWidth` and `maximumHeight` work because they *clamp* the computed size. Both axes were independently confirmed to need pinning. So **pin** every fixed-size region:
  - Side columns: `Layout.preferredWidth: 298`/`300` **plus** `Layout.minimumWidth` and `Layout.maximumWidth` equal to it. Center column: `Layout.fillWidth: true` + a `Layout.minimumWidth` (~380).
  - Fixed-height rows (cook-time row, control-buttons row, duty-pill row): `Layout.preferredHeight` **plus** `Layout.maximumHeight` equal to it, so only the gauge card (center) and hopper card (right) absorb vertical slack via `Layout.fillHeight`.
  - The gauge card and hopper card: `Layout.fillHeight: true` + a `Layout.minimumHeight` so their content (the 392px gauge, the vertical fill) is never clipped.
  - **Inside** cards, lay out "label-left / value-right" rows with **anchors** (`anchors.left`/`anchors.right` on the two texts inside a full-width `Item`), NOT a manual `Item { width: parent.width - x - other.width }` spacer — that spacer creates an implicit-width feedback loop that inflates the card and defeats the column pins. (This was the original preview bug.)
  - Alternatives that also work but are not used here: wrap each panel in a plain `Item` with an explicit `width`/`height` (plain Items honor explicit size) and anchor the inner Layout to fill it; or drop the RowLayout for the top-level split and use anchors (fixed sides to edges, center anchored between) — deterministic, but complicates the `hasProbes` collapse, which the pinned `RowLayout` handles for free by dropping the invisible column. GridLayout is a poor fit (it shares row heights across columns, but the three columns have different vertical rhythms).

  Structure:
  - Root `ColumnLayout`: `HeaderBar { onMenuRequested: dash.requestMenu("") }` then a `RowLayout` body (margins 16–18, spacing 16).
  - **Left column** (width 298) — the whole column collapses when there are no food probes so the center gauge flexes into the space, matching the design's `<sc-if hasProbes>`. Wrap it in a `ColumnLayout { Layout.preferredWidth: 298; visible: backend.foodProbeCount > 0 }` (an invisible `RowLayout` child is dropped from the layout, so `Layout.fillWidth` center expands): "FOOD PROBES" label + `Repeater { model: backend.foodProbes; ProbeCard { name: model.name; temp: model.temp; target: model.target; maxTemp: model.maxTemp; units: backend.units; onTapped: dash.requestInput("notify", model.name) } }`.
  - **Center column:** `Gauge { value: backend.primaryTemp; setpoint: backend.primarySetpoint; target: backend.primaryNotifyTarget; maxValue: backend.primaryMax; units: backend.units; modeLabel: backend.modeText; onTapped: dash.requestInput("notify", backend.primaryName) }`, then `RowLayout { CookTimeBar; Alert { shown: backend.lidOpen; message: "LID OPEN" } }`, then `ControlPanel { mode: backend.mode; recipe: backend.recipe; recipePaused: backend.recipePaused; onOpenMenu: (n)=>dash.requestMenu(n); onOpenInput: (n,o)=>dash.requestInput(n,o) }`.
  - **Right column** (width 300): `SystemCard {}`, a `RowLayout` of two `DutyPill`s computed from mode:
    ```qml
    property bool hold: backend.mode === "Hold"
    DutyPill { label: dash.hold ? "AUGER DUTY" : "P-MODE"
               value: dash.hold ? backend.augerDuty + "%" : "P-" + backend.pMode
               highlighted: false }
    DutyPill { label: dash.hold ? "FAN DUTY" : "SMOKE+"
               value: dash.hold ? backend.fanDuty + "%" : (backend.smokePlus ? "ON" : "OFF")
               highlighted: dash.hold ? backend.fanOn : backend.smokePlus }
    ```
    then `HopperCard { onCheckRequested: backend.hopperCheck() }`.
  - Preserve the Main.qml nav focus-chain (no change needed — it walks focusable items).
- [ ] **Step 4: Run** — `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_qml_load.py tests/test_qtquick_parity.py tests/test_qtquick_display.py -v` → PASS.
- [ ] **Step 5: commit** — `git commit -m "feat(qtdisplay): rebuild DashScreen to the ember 3-column layout"`

---

## Phase 4 — pygame flex (essential motion)

### Task 16: Accent-palette helper + new-type registration scaffold

**Files:**
- Modify: `display/flexobject.py` (add `resolve_accent(name)` helper + register new type names in `FlexObject_TypeMap`, pointing at classes added in Tasks 17–23)
- Test: `tests/test_flexobject_accent.py`

**Interfaces:**
- Produces: `flexobject.resolve_accent(name) -> dict` returning `{'accent': (r,g,b,a), 'glow': (...), 'arc': [(r,g,b), (r,g,b), (r,g,b)]}` for `'Ember'|'Ice'|'Crimson'` (default Ember). Consumed by all new flexobject classes and by `base_flex` injection (Task 24).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_flexobject_accent.py
from display.flexobject import resolve_accent

def test_resolve_accent_ember_default():
    a = resolve_accent('Ember')
    assert a['accent'][:3] == (255, 138, 43)   # #ff8a2b
    assert resolve_accent('nonsense')['accent'] == a['accent']

def test_resolve_accent_ice_crimson():
    assert resolve_accent('Ice')['accent'][:3] == (60, 199, 208)     # #3cc7d0
    assert resolve_accent('Crimson')['accent'][:3] == (255, 106, 90) # #ff6a5a
```

- [ ] **Step 2: Run** → FAIL (`ImportError: resolve_accent`).
- [ ] **Step 3: Implement** — add to `display/flexobject.py`:

```python
def _hex(h):
    h = h.lstrip('#')
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)


_ACCENTS = {
    'Ember':   {'accent': _hex('ff8a2b'), 'glow': _hex('ff7a1a'), 'arc': [_hex('ff5e1a')[:3], _hex('ff8a2b')[:3], _hex('ffc24b')[:3]]},
    'Ice':     {'accent': _hex('3cc7d0'), 'glow': _hex('2ec5d3'), 'arc': [_hex('1f9fb8')[:3], _hex('35c7d0')[:3], _hex('7ef0d2')[:3]]},
    'Crimson': {'accent': _hex('ff6a5a'), 'glow': _hex('ff5a4d'), 'arc': [_hex('e11d48')[:3], _hex('ff5a4d')[:3], _hex('ff9f43')[:3]]},
}


def resolve_accent(name):
    return _ACCENTS.get(name, _ACCENTS['Ember'])
```

Reserve the new type names in `FlexObject_TypeMap` (values are the class names created in the next tasks — add the map entries now, classes follow; if the plan is executed strictly in order, add each map entry in its class's task instead to keep each task import-clean). For a scaffold-first approach, create minimal `FlexObject` subclasses that render an empty card, then flesh out per task.

- [ ] **Step 4: Run** → `python -m pytest tests/test_flexobject_accent.py -v` PASS.
- [ ] **Step 5: ruff + commit** — `git commit -m "feat(display): accent palette resolver for flex dashboard"`

### Tasks 17–23: New flexobject types (one task each)

Each task: create a `FlexObject` subclass in `display/flexobject.py`, register it in `FlexObject_TypeMap`, and unit-test it renders to a PIL canvas of the object's `size` without error and reflects its data. Model every class on the existing patterns documented in the codebase (`GaugeCircle`, `GaugeCompact`, `StatusIcon`, `HopperStatus`): `__init__` stores `objectData` + background slice; override `_draw_object(self)` to draw on `self.working_canvas` (an `RGBA` PIL image sized to the object's working dimensions), then the framework resizes to `objectData['size']`; override `_define_touch_areas` where the object is tappable; use `self._draw_text(...)`, `self._create_icon(...)`, `ImageDraw` primitives (`rounded_rectangle`, `arc`, `polygon`, `line`), and the injected `objectData['accent']` palette. Fonts: load Barlow via absolute path `os.path.join(BASE, 'static/font/Barlow-SemiBold.ttf')` etc.

The shared test pattern (adapt names per task):

```python
# tests/test_flex_<type>.py
from display.flexobject import <ClassName>

def _obj(**data):
    base = {'name': 't', 'type': '<type>', 'position': [0, 0], 'size': [300, 160],
            'accent': {'accent': (255,138,43,255), 'glow': (255,122,26,255), 'arc': [(255,94,26),(255,138,43),(255,194,75)]},
            'data': {}, 'button_list': [], 'button_value': [], 'touch_areas': []}
    base.update(data)
    return base

def test_renders_without_error():
    from PIL import Image
    obj = <ClassName>('<type>', _obj(), Image.new('RGBA', (1280, 720)))
    obj.update_object_data(_obj(**{...}))  # feed representative live data
    canvas = obj.get_canvas()  # or the framework's accessor used by other flex tests
    assert canvas.size == (300, 160)
```

(Confirm the exact render/canvas accessor by reading how `tests/` currently render a flexobject — mirror it; if none exists, drive `update_object_data` and assert `obj.objectData['size']` and that no exception is raised.)

- [ ] **Task 17 — `probe_card` (`ProbeCard`):** name, target string (`→ N°`/`AMBIENT`), big temp (Barlow Semi Condensed), progress bar (temp/target, ok-green when done else accent). Touch → `input_notify`. Design ref: left-column probe card.
- [ ] **Task 18 — `gauge` ember variant (`GaugeEmber`, new type name e.g. `"gauge_ember"`):** 270° arc from 135°, radius per size; approximate the gradient by drawing the arc in ~48 short segments with colors interpolated across `accent['arc']`; glow via a blurred accent disc; setpoint tick at the setpoint fraction (Theme setpoint blue); center `GRILL` + big temp + `SET n°` + mode pill. Sweep animates toward the live temp (essential motion; reuse the existing `GaugeCircle._animate_object` stepping approach). Touch → `input_notify`.
- [ ] **Task 19 — `system_card` (`SystemCard`):** one card containing three rows (fan/auger/igniter): icon (FontAwesome glyph via `_create_icon`; fan glyph rotates when active — reuse StatusIcon's rotation stepping; auger/igniter show a static "active" tint, per essential-motion), label, status text, status dot. Touch subdivided into three row rects → `cmd_fan_toggle` / `cmd_auger_toggle` / `cmd_igniter_toggle`.
- [ ] **Task 20 — `duty_pill` (`DutyPill`):** a labeled value pill; data carries `label`, `value`, `highlight` (bool). Presentational; base_flex computes mode-aware content (Task 24).
- [ ] **Task 21 — `hopper_vertical` (`HopperVertical`):** header (`HOPPER` + `n%` in threshold color) + vertical fill bar (bottom-anchored, threshold gradient) + status label. Touch → `cmd_hopper_level`.
- [ ] **Task 22 — `header_bar` (`HeaderBar`):** live dot (green when cooking else grey), `PiFire` wordmark (accent `Fire`), `CONTROLLER`, IP (`objectData['data']['ip']`), clock (`objectData['data']['clock']`), hamburger. Touch on the hamburger rect → `menu_main`.
- [ ] **Task 23 — `button_row` (`ButtonRow`):** the mode-dependent control buttons in one row; data carries parallel `button_text`/`button_list`/`button_value`/`button_active` (same shape as the existing `control_panel`). Touch subdivided per button → the mapped command. Reuse `ControlPanel`'s per-button touch subdivision logic.

Each: **Steps** = write failing render/behaviour test → run FAIL → implement class + register in `FlexObject_TypeMap` → run PASS → `ruff format display/flexobject.py tests/test_flex_<type>.py` → commit `feat(display): add <type> flex object`.

### Task 24: `base_flex` wiring — accent inject, duty, clock/IP, live-data branches

**Files:**
- Modify: `display/base_flex.py` (config read of `accent_theme`; inject accent into new objects at build; `_update_dash_objects` branches for the new object names)
- Test: `tests/test_base_flex_dash_update.py`

**Interfaces:**
- Consumes: `self.config['accent_theme']`, `self.status_data['cycle_ratio']/['fan_duty']/['mode']/['outpins']/['hopper_level']/['p_mode']/['s_plus']`, `self.in_data`, and the display IP (from the same source that seeds the qrcode object's `ip_address`).
- Produces: the new dash objects receive live data every frame. No public API change.

- [ ] **Step 1: Write the failing test** — construct a `DisplayBase`-derived test double (mirror how existing display tests build one, e.g. `tests/test_dsi_1280x720t_module.py` / any base_flex test) with the new 1280×720 layout, feed a `status_data` with `mode:'Hold', cycle_ratio:0.4, fan_duty:100`, run the update, and assert: the two `duty_pill` objects now carry `AUGER DUTY`/`40%` and `FAN DUTY`/`100%`; in `mode:'Smoke'` they carry `P-MODE`/`P-n` and `SMOKE+`/`ON|OFF`; the `header_bar` object's `data['ip']` and `data['clock']` are populated; the `hopper_vertical` object's `data['level']` tracks `hopper_level`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** —
  - At display start, read `self.accent = resolve_accent(self.config.get('accent_theme', 'Ember'))`.
  - Where dash objects are constructed (the build loop that instantiates `FlexObject`s), inject `object_data['accent'] = self.accent` for the new-type objects.
  - In `_update_dash_objects`, add branches keyed by object `name`:
    - `duty_pill_left`/`duty_pill_right` — compute mode-aware `label`/`value`/`highlight` (Hold → auger/fan duty from `cycle_ratio*100`/`fan_duty`; else P-MODE/SMOKE+).
    - `header_bar` — set `data['clock']` from `time.strftime('%H:%M')` and `data['ip']` from the display IP; set the live-dot cooking flag from `mode`.
    - `system_card` — set fan/auger/igniter active flags from `status_data['outpins']`.
    - `hopper_vertical` — set `data['level']` from `hopper_level`. **D1:** when `hopper_level_enabled` is false, mark the object hidden/skip drawing (leave the slot blank).
    - `cook_time` (**D2**) — show the active countdown when a timer is running (reuse the existing timer-seconds computation from the current `timer` branch, formatted with its label), else show elapsed cook time computed from `status_data['startup_timestamp']` (`H:MM:SS`, `00:00` when `startup_timestamp` is 0 or mode in `Stop`/`Monitor`) with label `COOK TIME`.
    - `lid_alert` (**sc-if lidOpen**) — active/drawn only when `status_data['lid_open_detected']`; otherwise not drawn (the fixed cook-time slot does not reflow — documented compromise).
    - `probe_card_N` — set temp/target from `in_data['F']`/`NT` (reuse the existing food-gauge mapping from `_configure_dash`). **No-probes handling:** at build, hide any `probe_card_N` slot (and the `FOOD PROBES` label) that has no configured probe in `probe_info['food']` — set the object inactive/skip so it does not draw. (The pygame layout is fixed absolute-position, so the center gauge does not reflow into the vacated space the way QtQuick does; hiding the empty slots is the essential-motion compromise. Note this limitation in the commit.)
    Follow the existing "only update on change vs `last_status_data`/`last_in_data`" pattern already in the method.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: ruff + commit** — `git commit -m "feat(display): wire live data + accent into the ember dash objects"`

### Task 25: Bespoke 1280×720 layout in the generator + regenerate

**Files:**
- Modify: `tools/generate_dsi_layout.py` (add a dedicated 1280×720 dashboard builder; leave 1024×768 scaling untouched)
- Regenerate: `display/dsi_1280x720t.json`
- Modify: `tests/test_dsi_layout_generator.py` (the 1280×720 byte-for-byte assertion now covers the bespoke output), `tests/test_dsi_1280x720t_layout.py`, `tests/test_dsi_1280x720t_module.py`
- Test: as above

**Interfaces:**
- Consumes: the new flexobject type names (Tasks 17–23).
- Produces: `display/dsi_1280x720t.json` whose `profile_1.dash` is the ember redesign (header + 3 columns using the new types), `metadata.dash_background` = the ember PNG; `profile_2` and 1024×768 unchanged.

- [ ] **Step 1: Write the failing tests** —
  - In `tests/test_dsi_1280x720t_layout.py`, assert the new dash contains objects named `header_bar`, `probe_card_0`..`probe_card_4`, `primary_gauge` (type `gauge_ember`), `cook_time`, `lid_alert`, `button_row`, `system_card`, `duty_pill_left`, `duty_pill_right`, `hopper_vertical`; every object's `position`+`size` fits within `[0,0,1280,720]`; and `metadata.dash_background` endswith `background_ember_1280x720.png`.
  - Keep `test_reproduces_committed_1024x768_byte_for_byte` unchanged (guards the untouched resolution).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** — In `tools/generate_dsi_layout.py`, add `_dashboard_1280x720()` returning the bespoke `profile_1.dash` list (absolute 1280×720 coordinates transcribed from the design: header `[0,0,1280,58]`; left column x≈18 w≈298 with a label + 5 probe cards; center gauge card + cook-time bar with an adjacent fixed `lid_alert` slot + button row; right column x≈962 w≈300 with system card, two duty pills, hopper). Then in `build`, after the scale pass:

```python
    if (width, height) == (1280, 720):
        data['profile_1']['dash'] = _dashboard_1280x720()
        data['metadata']['dash_background'] = './static/img/display/background_ember_1280x720.png'
```

Run `python tools/generate_dsi_layout.py` to regenerate `display/dsi_1280x720t.json`. Confirm `git diff --stat` shows **only** `dsi_1280x720t.json` changed (not `dsi_1024x768t.json`).

- [ ] **Step 4: Run** — `python -m pytest tests/test_dsi_layout_generator.py tests/test_dsi_1280x720t_layout.py tests/test_dsi_1280x720t_module.py -v` → PASS.
- [ ] **Step 5: ruff + commit** — `git commit -m "feat(display): bespoke ember 1280x720 pygame dashboard layout"`

---

## Phase 5 — Verification

### Task 26: End-to-end drive of both stacks + full suite

**Files:** none (verification), plus any fixups surfaced.

- [ ] **Step 1: Full test suite** — `python -m pytest -q` → all green (or only pre-existing unrelated failures, which you must confirm exist on `HEAD~` before this branch's work).
- [ ] **Step 2: QtQuick offscreen load under each accent** — with `QT_QPA_PLATFORM=offscreen`, load `Main.qml` via `qtapp.build_engine` for a config with `accent_theme` = each of Ember/Ice/Crimson and assert `engine.rootObjects()` is non-empty and no QML warnings are emitted (capture `qInstallMessageHandler`). Confirm changing the setting mid-run flips `backend.accentTheme` (drive `poll()` with the accent_fn returning a new value + advanced clock).
- [ ] **Step 3: pygame render smoke** — build the 1280×720 flex `Display` in a headless/dummy pygame video mode (`SDL_VIDEODRIVER=dummy`), feed representative `in_data`/`status_data` for Stop/Smoke/Hold, run one composite frame, and assert the canvas is 1280×720 and no exception is raised. Mirror any existing pygame display smoke test if present.
- [ ] **Step 4: Use the `verify` skill** to drive the affected flow and observe behavior (both displays), per repo practice.
- [ ] **Step 5: Final commit** if any fixups — `git commit -m "test(display): verify ember dashboard on both stacks"`

---

## Open Decisions — RESOLVED (2026-07-08)

### D1 — Hopper card when the pellet sensor is disabled → **HIDE**

When `hopperEnabled == false` (i.e. `settings['modules']['dist'] == 'none'`), hide the hopper card. Qt Task 13: `HopperCard { visible: backend.hopperEnabled }`. pygame Task 21/24: skip drawing `hopper_vertical`. The right column keeps the System card + duty pills; the vacated hopper area is left empty (Qt: pills stay top-anchored; pygame: fixed slot left blank).

### D2 — "Cook Time" → **active countdown when one is running, else elapsed cook time**

The bar shows the active countdown (`timerLabel + timerText`, for Startup/Reignite/Prime/Shutdown/lid-pause) whenever `timerText` is non-empty; otherwise it shows elapsed cook time labeled `COOK TIME`. This preserves the startup/shutdown countdown within the design's single bar. Implemented as:
- **Qt (Task 5):** add a `cookElapsedText` property, computed in `poll()` from `status['startup_timestamp']` — `"00:00"` when not cooking (timestamp `0`/mode `Stop`), else `H:MM:SS` since cook start. `CookTimeBar` (Task 14) binds label+value to `timerText` when non-empty, else `"COOK TIME"` + `cookElapsedText`.
- **pygame (Task 24):** the cook-time object shows the countdown when a timer is active, else the elapsed string computed from `startup_timestamp` in `_update_dash_objects`.

(Verify `startup_timestamp` resets to `0` on Stop; if it does not, gate elapsed on `mode not in ('Stop','Monitor')`.)

---

## Self-Review

**Spec coverage (§ → task):**
- §2 duty status fields → Task 1. §4 accent setting → Task 2 (+ live read in Task 6, pygame read in Task 24). §5 QtQuick (Theme/fonts/backend/components/DashScreen/live theme) → Tasks 5–15. §6 pygame (new types/base_flex/generated layout/background) → Tasks 16–25. §7 fonts/assets → Tasks 3–4. §8 testing → every task's test steps + Task 26. §9 build order → phase order matches. §10 risks: MultiEffect (Task 8 verification note), font fetch (Task 3 fallback), pygame gradient (Task 18 segmented arc), duty-pill split (Task 15/24), generator decoupling (Task 25).
- **Refinement vs spec:** spec §6 said "rewrite `dsi_1280x720t.json`"; the file is generated, so Task 25 edits the generator and regenerates instead — same outcome, correct mechanism. Recorded here and in the spec's §6 note is unnecessary; the plan is authoritative for mechanism.

**Placeholder scan:** Mechanical tasks (1, 2, 5, 6, 7, 16, 24, 25) carry full code. Visual tasks (8–15, 17–23) intentionally specify files, interfaces, exact design tokens/geometry, representative code, and a concrete load/render acceptance test, and direct the implementer to transcribe pixel values from `docs/design/PiFire Dashboard.dc.html` — the authoritative source — rather than duplicating the entire design here. This is deliberate for a design-reskin, not an omission.

**Type consistency:** `cycle_ratio`(float 0–1)/`fan_duty`(int 0–100) produced in Task 1 → consumed as `augerDuty`=round(*100)/`fanDuty` in Task 5 and as duty-pill content in Task 24. `accent_fn`→`accentTheme`→`Theme.accent` chain consistent across Tasks 2/6/7. `resolve_accent` shape (`accent`/`glow`/`arc`) consistent across Tasks 16–24. New flex type names (`header_bar`, `probe_card`, `gauge_ember`, `system_card`, `duty_pill`, `hopper_vertical`, `button_row`) consistent across Tasks 16–25.
