# Qt Quick Display — Full Functional Parity Design

**Date:** 2026-07-05
**Status:** Approved design, pending spec review
**Branch:** random-test-changes
**Builds on:** [2026-07-04-qtquick-display-design.md](2026-07-04-qtquick-display-design.md)

## Goal

Bring the PySide6 / Qt Quick display to **full functional parity** with the
pygame `dsi_1280x720t` display. The QML-native visual redesign is preserved;
parity is behavioral (every action reachable and correct, every status-driven
behavior replicated). Parity is proven by an automated guard test that
cross-checks the QT side against the pygame layout JSON, not eyeballed.

## Background: authoritative pygame behavior

Source of truth for behavior is `display/base_flex.py` (`_update_dash_objects`,
`_command_handler`, `_fetch_data`, `_process_touch`/`_process_button`) and the
layout `display/dsi_1280x720t.json`. The audit of `_update_dash_objects`
([base_flex.py:407-645](../../display/base_flex.py)) plus the JSON action map
yields the complete gap list below.

### Confirmed parity gaps (all in scope)

1. **Probe/grill notifications.** Every gauge (primary + each food probe) has
   `button_list: ['input_notify']`; tapping opens a notify-target keypad for
   that probe, writing `notify_data`. Unreachable in QT today.
2. **Screen idle timeout + backlight sleep/wake.** Pygame sleeps the backlight
   after `TIMEOUT=10s` in Stop, disables the timeout during a cook, wakes to the
   dash on touch, and auto-wakes when the mode leaves Stop. Absent in QT.
3. **Dynamic control panel.** The four control-panel buttons change by mode:
   - Stop / Prime / Monitor → `[menu_prime, menu_startup, cmd_monitor, cmd_stop]`
     labels `[Prime, Startup, Monitor, Stop]`
   - Startup / Reignite → `[cmd_startup, cmd_smoke, input_hold, cmd_stop]`
     labels `[Startup, Smoke, Hold, Stop]`
   - Smoke / Hold / Shutdown → `[cmd_smoke, input_hold, cmd_stop, cmd_shutdown]`
     labels `[Smoke, Hold, Stop, Shutdown]`
   - Recipe (mode ≠ Shutdown) → `[cmd_next_step, cmd_none, cmd_stop, cmd_shutdown]`
     labels `[Next, <Startup|Smoke|Hold|None>, Stop, Shutdown]`; when
     `recipe_paused`, the active button is "Next". QT's panel is static.
4. **Hold lid-open countdown.** In Hold with `lid_open_detected`, the timer
   shows a countdown from `lid_open_endtime`, label "Lid Pause". QT's timer only
   covers Startup/Reignite (`start_duration`), Prime (`prime_duration`),
   Shutdown (`shutdown_duration`).
5. **Recipe mode-bar label.** `"Recipe: <mode>"` when recipe and mode ≠ Shutdown.
6. **Primary-gauge notify indicator.** Primary gauge shows notify target
   (`in_data['NT'][primary]`) like the food gauges show their target.
7. **Mode-conditional controls.** P-mode control is active only in
   Startup/Reignite/Smoke; output icons animate while their output is on.

## Design by area

### A. Notifications (gaps 1, 6)

- `Gauge.qml` / `CompactGauge.qml`: add `property string probeName`, a
  `signal tapped()`, and a `TapHandler`. `Gauge` also gains
  `property real target` shown as a notify indicator when `> 0`.
- `DashScreen.qml`: primary gauge `onTapped: openInput("notify", backend.primaryName)`;
  each food `CompactGauge` `onTapped: openInput("notify", model.name)`. `DashScreen`
  re-emits an `openInput(name, origin)` signal.
- `Main.qml`: `openInput(name, origin)` gains `origin`, forwarded to
  `NotifyInput.origin`. Notify screen already calls `backend.setNotify(origin, target)`.
- `qtbackend.py`: add `primaryNotifyTarget` property (from `in_data['NT']` keyed
  by the primary probe name), emitted on `primaryChanged`. `DashScreen` binds the
  primary gauge's `target` to it.
- `Display._dispatch_command('cmd_notify', {origin, target})` already writes the
  matching `notify_data` entry — no change.

### B. Idle sleep/wake + backlight (gap 2)

- **Backlight into the child.** `qtapp.run_app` sets up backlight
  (`rpi_backlight.Backlight` on real hardware with `/sys/class/backlight/`, else
  `DummyBacklight`), reusing the class already defined in `qtquick_flex.py`.
- **Idle state machine in the backend (testable).** `PiFireBackend` gains:
  - `registerInteraction()` slot — records the interaction time (injected clock)
    and forces awake.
  - Idle evaluation each `poll()`: awake whenever `mode != 'Stop'`; in Stop,
    asleep once `now - last_interaction > TIMEOUT` (10s). Leaving Stop auto-wakes.
  - `asleep` bool property + `asleepChanged` signal.
- **QML.** `Main.qml` overlays a full-screen black `Rectangle` bound to
  `backend.asleep`; a top `TapHandler`/`MouseArea` calls
  `backend.registerInteraction()` on any touch (and, when waking, ensures the dash
  is shown). The child connects `asleepChanged` → backlight power/brightness.
- Clock is injected (`self._now`) so tests drive time deterministically.

### C. Dynamic control panel + shared action routing (gap 3)

- **Shared routing.** New `display/qml/Actions.js` with
  `activate(item, handlers)` implementing the `menu_close` / `menu_*` /
  `input_*` / `cmd_*` routing currently inline in `MenuScreen`. `MenuScreen`
  refactors to call it (behavior unchanged).
- **Mapping.** `Menus.js` gains `controlPanelForMode(mode, recipe, recipePaused)`
  returning an ordered list of `{label, action, value?, active?}` exactly matching
  the pygame sets in gap 3 (including the Recipe variant and the paused "Next"
  active state). The non-actionable recipe indicator uses `action: "cmd_none"`.
- **Component.** `ControlPanel.qml` renders a `Repeater` over
  `controlPanelForMode(...)`, styled per action (Stop → danger). Each button
  routes through `Actions.js` with handlers `{backend, openMenu, openInput}`;
  `cmd_none` is inert. `DashScreen` passes `openMenu`/`openInput` through.

### D. Timer, recipe label, mode-conditional controls (gaps 4, 5, 7)

- `qtbackend.py`:
  - `timerText` extended: in Hold with `lid_open_detected`, count down
    `lid_open_endtime - now`; `timerLabel` = "Lid Pause". Startup/Reignite/Prime/
    Shutdown keep their durations with `timerLabel` = "Timer". Otherwise empty.
  - `modeText` property: `"Recipe: <mode>"` when `recipe` and mode ≠ Shutdown,
    else `mode`. Emitted on `statusChanged`/`modeChanged`.
  - `pModeActive` property: true when mode ∈ {Startup, Reignite, Smoke}.
- QML: `TimerCard` shows `timerLabel` + `timerText`; `ModeBar` binds `modeText`;
  `PModeControl` dims (lower opacity) when `!backend.pModeActive`; `StatusIcon`
  gets a subtle pulse animation while `active`.

### E. Verification — parity guard + behavior tests

- **Command coverage** (`tests/test_qtquick_parity.py`): parse every `cmd_*`
  from the pygame JSON dash + menus + input sections. For each, call
  `Display._dispatch_command(cmd, sample_value)` with `write_control`/`read_*`
  stubbed and assert it is handled — it either issues the expected control write
  or is a recognized no-op (`cmd_none`) — never an unhandled command.
- **Menu coverage:** parse every `menu_*` target from the pygame JSON. Assert
  each resolves on the QT side: `Menus.menuFor(name)` returns a defined (non
  fallback) menu, or the name maps to a dedicated screen (`qrcode`). Evaluated by
  loading a tiny QML harness that calls the JS and returns the result, so the
  actual `Menus.js` is exercised (not a Python copy).
- **Input coverage:** parse `input_*`; assert a `screens/<Name>Input.qml` exists
  for each (`hold`, `notify`).
- **Control-panel coverage:** via the same QML-eval harness, assert
  `controlPanelForMode` returns the correct action list for every mode
  (Stop, Prime, Monitor, Startup, Reignite, Smoke, Hold, Shutdown, Recipe) and
  the paused-recipe "Next" active flag.
- **Status-behavior surface:** assert `PiFireBackend` exposes the parity
  properties/slots (`modeText`, `primaryNotifyTarget`, `timerLabel`,
  `pModeActive`, `asleep`, `registerInteraction`) — a compile-time-ish guard that
  the behaviors are present.
- **Targeted behavior tests** (extend `tests/test_qtbackend.py`): notify-origin
  dispatch payload; the sleep/wake state machine across Stop→cook→idle→touch;
  Hold lid-open countdown + label; `modeText` recipe label; `pModeActive` per
  mode.

The guard reads the pygame JSON at test time, so if pygame later gains an action
the QT side lacks, the guard fails.

## QML-eval harness (shared test utility)

Menu/control-panel coverage needs to run the real JS. A small helper loads an
inline QML `Item` that imports `Menus.js`/`Actions.js`, evaluates the function
under test, and exposes the JSON-stringified result via a property the Python
test reads. This keeps `Menus.js` the single source of truth (no Python
duplicate to drift).

## Non-goals

- No pixel/layout parity; the redesign stands.
- No changes to `control.py`, Redis schema, or the `write_control`/`read_*`
  contracts.
- No new runtime dependencies (PySide6 already present).

## Testing summary

- `tests/test_qtbackend.py` — extended with notify, sleep/wake, timer/label,
  pmode behavior tests.
- `tests/test_qtquick_parity.py` — new guard: command/menu/input/control-panel
  coverage vs. pygame JSON + backend surface check.
- `tests/test_qml_load.py` — extended to load the new/edited screens.
- All run headless with `QT_QPA_PLATFORM=offscreen`.
