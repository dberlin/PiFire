# PiFire 1280×720 Dashboard Redesign — Design Spec

**Date:** 2026-07-08
**Design source:** `docs/design/PiFire Dashboard.dc.html` (imported from Claude Design project `4b80b418-6f24-4400-b97c-9c5a2e5700fe`)
**Status:** Approved for planning

## 1. Goal & scope

Reskin the **1280×720 local-display dashboard** in both display stacks to match the imported
`PiFire Dashboard.dc.html` design: a dark "ember" theme with a header bar, a left food-probe
column, a central radial grill gauge + cook-time + control buttons, and a right system/hopper column.

**In scope**
- QtQuick display `qtquick_dsi_1280x720t` — **full-fidelity** animations and effects.
- pygame flex display `dsi_1280x720t` — **essential motion** (fan spin, gauge/hopper/bar
  transitions, blinking live dot + lid alert, mode glow; elaborate auger-pellet and flame effects
  simplified to a clean active state).
- A configurable **accent theme** (Ember / Ice / Crimson) as a per-display setting.
- Real **auger/fan duty** values surfaced to the display for the Hold-mode pills.

**Explicitly out of scope**
- All other display resolutions (800×480, 480×320, 1024×768, 240×320) — untouched.
- The web dashboards (`dash_default.html`, `dash_basic.html`) and their `settings['dashboard']`
  system — untouched.
- Any new control/cooking behavior. This is presentation + one additive status field.

## 2. The design (reference)

1280×720, `font-family: Barlow` / `Barlow Semi Condensed`, base bg `#0c0a09`, cards `#1a1611`,
default accent ember `#ff8a2b`. Three accent themes are defined in the source
(`Ember`/`Ice`/`Crimson`), each with an arc gradient, a glow color, and an accent color.

Layout:
- **Header (58px):** blinking live dot (green when cooking, grey when stopped), `PiFire` wordmark
  (accent-colored "Fire"), `Controller` label, IP address, live clock (HH:MM), hamburger button.
- **Left column (298px):** "FOOD PROBES" label + up to 5 probe cards. Each card: probe name,
  target (`→ 203°` or `AMBIENT`, colored green when done / yellow while cooking / grey if no target),
  large temperature, and a progress bar (temp/target, green when done else accent).
- **Center column (flex):**
  - Radial grill gauge card: 270° arc (135°→405°) with accent gradient + glow, a blue setpoint
    marker line, center shows `GRILL`, large temp with `°F/°C`, `SET n°` (when setpoint > 0), and a
    mode pill (`SMOKE`/`HOLD`/`STARTUP`/…).
  - Cook-time bar (`Cook Time  H:MM:SS`) + optional blinking `LID OPEN` alert.
  - Control buttons row — mode-dependent set (e.g. Hold/Smoke/Startup → Set Temp, Smoke+, Shutdown,
    Stop; Stop → Prime, Startup, Monitor, Stop; Monitor → Startup, Stop; Shutdown → Stop).
- **Right column (300px):**
  - System card: Fan / Auger / Igniter rows, each with an animated icon, status text
    (`RUNNING`/`IDLE`, `FEEDING`/`IDLE`, `HOT`/`OFF`), and a status dot.
  - Two pills. **Hold mode:** `AUGER DUTY n%` / `FAN DUTY n%`. **Other cooking modes:**
    `P-MODE P-n` / `SMOKE+ ON|OFF`.
  - Hopper card: vertical fill bar + percentage, threshold colors
    (green ≥35, amber 15–35, red <15) with label `LEVEL OK` / `RUNNING LOW` / `REFILL PELLETS`.

The `.dc.html` is a mock; its `Component`/`simTick` JS (fake data, `new Date()`, random walk) is
**reference only** and is not ported.

## 3. Data model — what the design needs vs. what exists

All fields below already exist and reach the display via `read_current()` + `read_status()`
(pygame) and the `backend` QObject (QtQuick), **except** the two duty fields.

| Design element | Source (exists today) |
|---|---|
| Grill temp / setpoint / notify / max | `primaryTemp` / `primarySetpoint` / `primaryNotifyTarget` / `primaryMax`; pygame `in_data['P']`,`PSP`,`NT` |
| Food probes (name/temp/target/max) | `backend.foodProbes` model; pygame `in_data['F']`,`NT` + `probe_info` |
| Mode / mode text | `backend.mode`/`modeText`; pygame `status_data['mode']` |
| Fan / Auger / Igniter on | `fanOn`/`augerOn`/`igniterOn`; pygame `status_data['outpins']` |
| Lid open | `lidOpen`; pygame `status_data['lid_open_detected']` |
| Cook time | `timerText`/`timerLabel`; pygame timer computed from `start_time`/durations |
| P-mode / Smoke+ | `pMode`/`smokePlus`; pygame `status_data['p_mode']`/`s_plus` |
| Hopper level / enabled | `hopperLevel`/`hopperEnabled`; pygame `status_data['hopper_level']`/`hopper_level_enabled` |
| IP address | `backend.ipAddress`; pygame injects IP into the qrcode object (available in `base_flex`) |
| Clock | **Computed in the display layer** (QML `Timer` / pygame per-frame local time) — no backend change |
| **Auger duty (Hold)** | **NEW** `status_data['cycle_ratio']` |
| **Fan duty (Hold)** | **NEW** `status_data['fan_duty']` |

### 3.1 New status fields (only control-layer change)

In `controller/runtime/modes/base.py`, where `status_data` is assembled and written every ~0.5s
(the single choke point all modes share), add:

- `status_data['cycle_ratio']` = `round(self.state.cycle.ratio, 2)` — auger on-time fraction 0..1.
  Already computed in Hold/Smoke/Startup; `0` where not applicable.
- `status_data['fan_duty']` = `control['duty_cycle']` on PWM/DC-fan builds
  (`settings['platform']['dc_fan']`), else `100 if outpins['fan'] else 0`.

Both are additive; existing consumers ignore unknown keys. `read_status(init=True)` in
`common/common.py` gains the two keys (default `0`) so the shape stays canonical.

**Duty-pill behavior (adopted from the mock; flag if undesired):** show `AUGER DUTY` / `FAN DUTY`
only in **Hold**; in other cooking modes show `P-MODE` / `SMOKE+`.

## 4. Accent-theme setting

- Add `accent_theme` (type `list`, values `Ember`/`Ice`/`Crimson`, default `Ember`) to the display
  modules' `config` in `wizard/wizard_manifest.json` (same pattern as `rotation`). It is picked up by
  `_default_display_config()` and stored at `settings['display']['config'][<module>]['accent_theme']`.
- It reaches both stacks through the existing `config` dict built in
  `controller/runtime/devices.py:build_display` — no new transport.
- **pygame:** read once at start via `self.config.get('accent_theme', 'Ember')`. Changing it applies
  on display restart (matches `rotation`).
- **QtQuick:** **live**. `qtbackend.PiFireBackend` exposes a notifiable `accentTheme` property,
  refreshed inside the existing `poll()` (reads settings each cycle) and emitting a change signal when
  it differs. `Theme.qml` gains a writable `property string accent` with color/gradient/glow tokens
  derived from it; `Main.qml` binds `Theme.accent` to `backend.accentTheme`, so a settings change
  recolors the running UI without restart.

Accent token definitions (from the source design):

| Accent | Arc gradient stops | Glow | Accent color |
|---|---|---|---|
| Ember | `#ff5e1a → #ff8a2b → #ffc24b` | `#ff7a1a` | `#ff8a2b` |
| Ice | `#1f9fb8 → #35c7d0 → #7ef0d2` | `#2ec5d3` | `#3cc7d0` |
| Crimson | `#e11d48 → #ff5a4d → #ff9f43` | `#ff5a4d` | `#ff6a5a` |

## 5. QtQuick implementation (full fidelity)

**Approach — evolve the QML in place.** Only the 1280×720 Qt display exists, and the dash
components are not shared with the menu/input screens (those use `MenuButton`/`Keypad`/`MenuScreen`),
so restyling is contained and low-risk. Alternative (parallel new screen behind a flag) rejected as
unnecessary indirection for a single target.

Changes under `display/`:

- **`qml/Theme.qml`** — extend the singleton: full ember palette; a writable `accent` property with
  derived readonly tokens (`accentColor`, `arcGradientStops`, `glowColor`, plus surface/edge/text/
  status colors, radius, spacing, and font-size tokens); font-family tokens for Barlow / Barlow Semi
  Condensed.
- **`qml/Fonts.qml`** (new singleton) **or** `FontLoader`s in `Main.qml` — load bundled Barlow +
  Barlow Semi Condensed; components read `font.family` from Theme (they hard-code the system font today).
- **`qtapp.py`** — no accent context property needed (accent comes live from `backend.accentTheme`);
  continue to expose existing metadata context properties.
- **`qtbackend.py`** — add `augerDuty` (int %, from `cycle_ratio*100`) and `fanDuty` (int %, from
  `fan_duty`) properties on `statusChanged`; add notifiable `accentTheme` (string) refreshed in `poll()`.
- **New components** (`qml/components/`):
  - `HeaderBar.qml` — live dot (blink `SequentialAnimation`), `PiFire` wordmark, `Controller` label,
    IP (`backend.ipAddress`), clock (`Timer` @ 1s → `Qt.formatTime`), hamburger `MenuButton`
    (`onClicked: requestMenu("")`).
  - `ProbeCard.qml` — replaces `CompactGauge` on the dash: name, target string, big temp, progress
    bar; `onTapped: requestInput("notify", name)`.
  - `SystemCard.qml` + `FanIcon.qml` / `AugerIcon.qml` / `IgniterIcon.qml` — `QtQuick.Shapes`
    vector icons; fan `RotationAnimation`, auger feed + falling-pellet `SequentialAnimation`, igniter
    flame flicker + rising-heat animation. Each row: icon, label, status text, status dot; tapping
    toggles via existing `backend.toggleFan/Auger/Igniter`.
  - `DutyPill.qml` — mode-aware content (Hold → duty, else P-MODE / SMOKE+).
  - `HopperCard.qml` — vertical fill + %, threshold colors; `onClicked: backend.hopperCheck()`.
  - `CookTimeBar.qml` — cook-time row; restyle `Alert.qml` for the LID OPEN pill.
- **Rework existing** `Gauge.qml` — ember gradient arc (`ShapePath.fillGradient` /
  `ConicalGradient` or segmented arc), setpoint marker line, pulsing glow (`MultiEffect`/blur layer),
  center `GRILL` / temp / `SET n°` / mode pill.
- **`ControlPanel.qml`** — restyle to the large bordered buttons (data-driven set is unchanged;
  `Menus.controlPanelForMode` already returns the right buttons per mode).
- **`DashScreen.qml`** — rewritten to header + 3-column layout; existing `requestMenu`/`requestInput`
  signals, toggle wiring, and the `Main.qml` nav focus-chain preserved.

**Effects caveat:** glow/shadow preferred via `QtQuick.Effects` `MultiEffect` (Qt 6.11). Verify the
module loads in the deployed PySide6 wheel during implementation; fallback is a blurred duplicate
`Shape`/`Rectangle` layer (no external module).

## 6. pygame flex implementation (essential motion)

**Approach — new dedicated flexobject types + a rewritten 1280×720 layout JSON; existing types
untouched.** `flexobject.py` classes are shared by every other resolution's JSON, so restyling them in
place would regress 800×480 / 480×320 / 1024×768 / protoflex. New types isolate the redesign and keep
it reversible. Alternative (a `style` flag on existing types) rejected — more conditional branches in
shared, already-large classes.

Changes under `display/`:

- **`flexobject.py`** — new `FlexObject` subclasses, registered in `FlexObject_TypeMap`, each reading
  an injected `accent` palette from its object `data`:
  - `header_bar` — logo + live dot + IP + clock + hamburger (hamburger is the touch target →
    `menu_*`).
  - `probe_card` — name, target, big temp, progress bar (touch → `input_notify`).
  - `gauge` **ember variant** (new class, e.g. `GaugeEmber`) — 270° arc approximating the gradient via
    short color-interpolated segments, glow (blur), setpoint marker, center temp + mode pill.
  - `system_card` — fan/auger/igniter rows in one card; fan spin retained, auger/igniter simplified to
    a clean active state (essential motion). Touch subdivided per row → toggles.
  - `duty_pill` — mode-aware pill (Hold → duty from `cycle_ratio`/`fan_duty`, else P-MODE / SMOKE+).
  - `hopper_vertical` — vertical fill bar + %, threshold colors.
  - `button_row` — the mode-dependent control buttons (touch subdivided per button → `cmd_*`),
    mirroring the existing `control_panel` command mapping.
- **`base_flex.py`** —
  - Inject the accent palette (resolved from `config['accent_theme']`) into each new object's `data`
    at build (`_configure_dash`/build).
  - Add `_update_dash_objects` branches feeding `status_data['cycle_ratio']`/`fan_duty` into the duty
    pills and machine state into the system card; feed IP + local clock into the header.
- **`display/dsi_1280x720t.json`** — rewrite the `profile_1.dash` (and `profile_2` if rotation-relevant)
  object list to the header + 3-column composition using the new types. `metadata.dash_background`
  points at the new ember background; `menus`/`input` in that JSON are unchanged.
- **Background asset** — a pre-rendered 1280×720 ember radial-gradient PNG under
  `static/img/display/` (covers the radial gradient PIL can't do live; matches the existing
  `dash_background` pattern).

Only `dsi_1280x720t.json` references the new types, so no other flex display changes.

## 7. Fonts & assets

- Bundle **Barlow** (regular/medium/semibold/bold) and **Barlow Semi Condensed** (medium/semibold/
  bold/extrabold) TTFs — OFL, from Google Fonts — under `static/font/`.
  - QtQuick loads them via `FontLoader`; pygame references them by full path (the flex renderer
    currently resolves bare filenames from the OS font path, which is unreliable — new code uses
    explicit paths).
  - If the fonts cannot be fetched in the build environment, fall back to the nearest condensed system
    face and flag it; the layout/token system does not depend on the exact face.
- New ember background PNG (1280×720) under `static/img/display/`.

## 8. Testing

- **Control:** unit test asserting `cycle_ratio` and `fan_duty` appear in `status_data` / the
  `read_status` shape, with correct AC-fan (`100/0`) vs. PWM (`duty_cycle`) behavior.
- **QtBackend:** extend `tests/test_qtbackend.py` for `augerDuty`/`fanDuty`/`accentTheme` (value +
  change-signal on settings change).
- **QML load:** extend `tests/test_qml_load.py` / parity tests so the new components + rewritten
  `DashScreen` load without error under each accent.
- **pygame:** a render smoke test that builds the `dsi_1280x720t` dash with the new types and asserts
  every object renders to a canvas without error; targeted unit checks on the new classes (e.g.
  hopper threshold colors, duty-pill mode switch, gauge arc extents).
- Follow the repo rule: `ruff format` changed Python before committing.

## 9. Build order (for the implementation plan)

1. Control-layer `cycle_ratio`/`fan_duty` + status-shape update + tests.
2. `accent_theme` setting: wizard manifest + defaults, plumb into both `config` reads.
3. Fonts + ember background asset bundling.
4. QtQuick: `Theme` tokens + Fonts → backend props (`augerDuty`/`fanDuty`/`accentTheme`) →
   components → `Gauge`/`ControlPanel` restyle → `DashScreen` → live-theme binding; tests.
5. pygame: new flexobject types → `base_flex` wiring (accent inject, duty, clock/IP) →
   `dsi_1280x720t.json` layout → background; tests.
6. Verification on both stacks (drive each display, confirm live data + theme switch).

## 10. Risks / open items

- **`MultiEffect`/GraphicalEffects availability** in the deployed PySide6 — verify early; blurred-layer
  fallback exists.
- **Font fetch** in the build environment — fallback face if unavailable.
- **pygame gradient fidelity** — segmented-color arc + pre-rendered background is the "essential"
  compromise; true live gradients are not attempted in PIL.
- **`base_flex._init_background` latent bug** (crops with `size[0]` for both width and height,
  non-square objects) — new full-width objects should rely on the full per-frame background redraw
  rather than per-object captured background slices; note during implementation.
- Duty-pill mode split is taken from the mock; revisit if duty should also show in Smoke.
