# PySide6 / Qt Quick Display Module — Design

**Date:** 2026-07-04
**Status:** Approved design, pending spec review
**Scope:** Full-parity Qt Quick (QML) drop-in display module replacing the pygame
1280×720 UI on a functional level, redesigned with native Qt Quick visuals.

## Goal

Provide a PySide6 / Qt Quick display module for PiFire that is a **drop-in
replacement** for the existing pygame-based `dsi_1280x720t` display: same
`control.py` integration contract, same live behavior (dash + all menus + input
screens + splash), but rendered with native Qt Quick components and animations
instead of pygame/PIL blitting.

It ships as a **new selectable display alongside** the pygame module — the
wizard and settings can choose either; the working pygame module is not removed.

## Non-goals

- Pixel-for-pixel reproduction of the pygame look. This is an intentional
  QML-native redesign (same layout intent and function, modern visuals).
- Changes to the control loop, Redis schema, or the `write_control` /
  `read_current` / `read_status` contracts.
- Multi-resolution generalization beyond what the metadata JSON already allows.
  The first target is 1280×720; the base class stays resolution-agnostic.

## Background: how the current display works

- `control.py` (~line 1288) imports the display module by name from settings,
  instantiates `Display(dev_pins, buttonslevel, rotation, units, config)`, and
  otherwise only calls no-op stub methods: `display_status(in_data,
  status_data)`, `display_text(text)`, `clear_display()`, `display_splash()`,
  `display_network()`.
- The real UI is **self-contained**: `display/dsi_800x480t.py` starts a
  `multiprocessing.Process` running a display loop that reads live data
  **directly from Redis/Valkey** via `read_current()` / `read_status()` and
  writes commands back via `write_control(..., origin='display')`.
- `display/base_flex.py` (`DisplayBase`) holds the framework:
  - `_fetch_data()` — reads Redis into `self.in_data` / `self.status_data`.
  - `_command_handler()` — maps a `self.command` string (+ `self.command_data`)
    to the correct `write_control` payload for every action (startup, stop,
    monitor, hold, notify, prime, pmode, splus, next_step, reboot, poweroff,
    restart, output toggles, lid_open, hopper).
  - Plus PIL/flexobject rendering, asset loading, and menu-object state — all
    pygame/PIL-specific and **not** reused here.
- The 1280×720 UI is defined by `display/dsi_1280x720t.json`:
  - `metadata`: `screen_width` 1280, `screen_height` 720, `framerate` 20,
    `splash_*`, `max_food_probes` 5.
  - `dash` objects: `gauge` (primary), `control_panel`, 5× `gauge_compact`,
    `mode_bar`, 3× `status_icon`, `menu_icon`, `timer`, `alert`, `button`,
    `p_mode_control`, `splus_control`, `hopper_status`.
  - `menus` (14): `main`, `main_active_normal`, `main_active_monitor`,
    `main_active_recipe`, `system`, `qrcode`, `main_reboot`, `main_power_off`,
    `prime`, `prime_startup`, `prime_only`, `startup`, `pmode`, `message`.
  - `input` (2): `hold` (setpoint entry), `notify`.

### Data model (from Redis)

- `read_current()` → `{'P': {label: temp}, 'F': {label: temp}, 'AUX': {label:
  temp}, 'PSP': setpoint, 'NT': {label: notify_target}}`.
- `read_status()` → status dict incl. `mode`, `outpins`, `hopper_level` /
  `hopper_level_enabled`, `recipe` / `recipe_paused`, `lid_open_detected`,
  Smoke+ and P-mode state, timer fields.
- `config['probe_info']` (passed by control.py) gives probe labels/types/units
  and `max_temp` for gauges.

## Chosen approach: A — subclass `DisplayBase`, reuse data + command layer

New module subclasses `DisplayBase` so it inherits the constructor contract and
reuses the two UI-agnostic methods **verbatim**: `_fetch_data()` and
`_command_handler()`. It overrides the device/canvas/asset/loop init so **no PIL
or pygame code path runs**. Rendering and input move into a Qt Quick process.

Rejected alternatives:

- **B — fully standalone backend** (no `DisplayBase`): would duplicate the
  ~200-line, well-tested command→`write_control` mapping and risk drift.
- **C — Qt Widgets** instead of QML: the request is specifically Qt Quick / QML.

## Architecture

### Process & integration model

- `Display.__init__` (in the control.py process) does lightweight init and
  spawns a **`multiprocessing.Process` (spawn context)** for the UI.
- `QGuiApplication` + `QQmlApplicationEngine` are created **only inside the
  child process** — Qt is never imported/instantiated in the parent, avoiding
  fork-state issues.
- The five public methods remain no-op stubs (control.py already treats them so).
- If the child fails to start, the constructor error propagates and control.py's
  existing `try/except` falls back to `display_none` — behavior preserved.

### Module / file layout

- `display/qtquick_flex.py` — `class Display(DisplayBase)`: resolution-agnostic
  base; owns process spawn, overrides of the PIL/pygame init methods, and the
  bridge to the Qt process. Reuses inherited `_fetch_data` / `_command_handler`.
- `display/qtquick_dsi_1280x720t.py` — thin wrapper (mirrors the
  `dsi_1280x720t.py` → `dsi_800x480t.py` pattern) selecting the 1280×720 JSON.
- `display/qtquick_dsi_1280x720t.json` — metadata (size, framerate, splash,
  probe/gauge config). Structural layout lives in QML, so this JSON is smaller
  than the pygame one (no per-object absolute positions required).
- `display/qml/` — QML asset tree (see below).
- Settings + `wizard_manifest.json` registration so the display is selectable,
  with `input_types_supported: [button, touch]`, `rotation`.

### Python ↔ QML bridge

A `Backend(QObject)` instance registered as a context property in the QML engine
(runs in the child process):

- **Live data (poll → properties):** a `QTimer` at the JSON framerate calls the
  inherited `_fetch_data()`, then publishes into `Q_PROPERTY`s and a food-probe
  `QAbstractListModel`:
  - `mode`, primary `temp` / `setpoint` / `maxTemp`, food-probe model (label,
    temp, notify target), `hopperLevel` / `hopperEnabled`, timer fields,
    `smokePlusActive`, `pMode`, output states, `lidOpen`, `recipe` /
    `recipePaused`.
  - Change signals fire only on delta, so QML bindings refresh minimally.
- **Actions (QML → Python):** `Q_INVOKABLE` slots — `startup()`, `stop()`,
  `monitor()`, `primeStartup(amount)`, `primeOnly(amount)`, `setHold(temp)`,
  `setNotify(...)`, `setPMode(n)`, `toggleSmokePlus()`, `nextStep()`, `reboot()`,
  `powerOff()`, `restart()`, output toggles, `lidOpen()`. Each sets
  `self.command` / `self.command_data` and calls the inherited
  `_command_handler()`.
- **Backlight & sleep:** reuse the `rpi_backlight` / `DummyBacklight` pattern
  from the pygame module. Idle-timeout sleep + wake-on-touch is driven in QML,
  signaling the backend to toggle the backlight.

### QML structure (native redesign)

- `Main.qml` — fullscreen window sized from JSON metadata; a `StackView` /
  `Loader` screen manager: **Splash → Dash → Menu/Input overlays**. The splash
  reuses the existing splash image referenced in the metadata (shown as an
  `Image` for `splash_delay` ms); it is not redesigned.
- `Theme.qml` (singleton) — palette, fonts, radii, animation durations; the
  single place to tune/modernize visuals.
- **Dash components (1:1 with flexobjects):** `Gauge.qml` (radial arc via
  `Shapes`, animated temp/setpoint/notify indicators + glow), `CompactGauge.qml`,
  `ControlPanel.qml`, `ModeBar.qml`, `StatusIcon.qml`, `MenuIcon.qml`,
  `TimerCard.qml`, `Alert.qml`, `HopperStatus.qml`, `PModeControl.qml`,
  `SmokePlusControl.qml`.
- **Menus (14):** `MainMenu` with normal/monitor/recipe/active variants,
  `SystemMenu`, `PrimeMenu` / `PrimeStartup` / `PrimeOnly`, `StartupMenu`,
  `PModeMenu`, `QRCodeScreen`, `RebootScreen`, `PowerOffScreen`, `MessageScreen`.
- **Inputs (2):** `HoldInput.qml` (touch numeric keypad for setpoint),
  `NotifyInput.qml`.

### Input handling (touch + button parity)

- Touch is native (`TapHandler` / `MouseArea`) and is the primary path.
- Button/encoder parity: a focus-navigation layer maps UP/DOWN/ENTER — delivered
  from the existing GPIO/event path via a backend signal — onto QML `FocusScope`
  traversal and activation, so both input modes reach every screen. Built **after**
  dash + menus work under touch (included for parity, not blocking early work).

## Testing

- **Backend logic** (data mapping, action→payload, list-model updates) tested
  headless against a fake Redis/Valkey, independent of QML.
- **QML load smoke test** in CI with `QT_QPA_PLATFORM=offscreen` to catch import
  and binding errors across all screens.
- **Contract test**: constructing `Display(...)` with a stub config starts and
  cleanly tears down the child process; the five public stubs are safe no-ops.

## Error handling

- Constructor failures propagate → control.py falls back to `display_none`
  (existing behavior, preserved).
- Redis read failures inside the poll timer are caught and logged; the UI holds
  last-known values rather than crashing.
- QML runtime warnings are logged via the Qt message handler into the `control`
  logger for field diagnosis.

## Deliverables

1. `display/qtquick_flex.py`
2. `display/qtquick_dsi_1280x720t.py`
3. `display/qtquick_dsi_1280x720t.json`
4. `display/qml/` component + screen tree with `Theme.qml`
5. Settings + `wizard_manifest.json` registration
6. Headless backend tests + offscreen QML smoke test

## Open items / deferred

- GPIO button-navigation layer lands after the touch dash + menus are working.
- Visual polish (exact palette, glow, animation curves) is iterated in
  `Theme.qml` after functional parity.
