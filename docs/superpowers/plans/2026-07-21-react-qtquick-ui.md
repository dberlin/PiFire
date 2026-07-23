# Plan: React reimplementation of the QT Quick on-device UI (as a remote browser app)

**Date:** 2026-07-21
**Decisions (from user):** target = **remote browser web app**; fidelity = **same screens, modern polish** (idiomatic React/CSS, not pixel-exact QML); deliverable now = **this plan + a proof-of-concept spike**.

## Context

PiFire's newest on-device display is a **QT Quick (QML) kiosk** under `display/qml/` (~2,155 lines QML/JS + ~799 lines PySide6 bridge) that runs fullscreen on the Pi's DSI/HDMI touchscreen. It is a focused touch *controller* — dashboard, menus, numeric keypads — distinct from the full-featured Flask web app (`app.py` + `blueprints/`). Both front-ends are independent views over the **same SQLite datastore + `write_control` command channel**.

Goal: reproduce that controller UI — the same screens, layout, accent theming, and signature animations — as a **React app served in the browser**, reusing PiFire's existing Flask-SocketIO API instead of the Qt poll/dispatch bridge. This gives a phone/tablet/desktop version of the on-device controller look without QML.

## What must be reproduced (from `display/qml/`)

**Screens** (StackView in `Main.qml`):
- **Splash** — centered logo, auto-advances to dash after ~500 ms.
- **DashScreen** (`screens/DashScreen.qml`) — `HeaderBar` + 3-column layout:
  - Left: "FOOD PROBES" + repeater of `ProbeCard`s (column hidden when 0 probes).
  - Center: big primary `Gauge` card, `CookTimeBar` + `Alert` ("LID OPEN") row, `ControlPanel` button row.
  - Right: `SystemCard` (fan/auger/igniter rows), two `DutyPill`s (P-MODE/SMOKE+ or AUGER/FAN DUTY by mode), `HopperCard`.
- **MenuScreen** — dim overlay + card + `MenuButton` list; variant keyed by mode (`Menus.js` tree).
- **HoldInput / NotifyInput** — full-screen overlay wrapping a numeric `Keypad` (sets setpoint / probe target).
- **QrCodeScreen** — shows web-UI URL.
- **Sleep overlay** — black cover when idle; tap to wake.

**Theme** (`Theme.qml`, reproduce tokens verbatim): dark warm palette (page `#0c0a09`, card `#2c231a`, inset `#1c1712`, text `#f4ede2`); 3 live-switchable accents — **Ember** `#ff8a2b`, **Ice** `#3cc7d0`, **Crimson** `#ff6a5a` — driving `accentColor`/`glowColor`/3-stop arc gradient; `cardRadius 18`, `pillRadius 999`, `animMs 250`. Fonts: Barlow / Barlow Semi Condensed (already in `static/font/`).

**Signature animations** (declarative → CSS/Framer Motion; timings must match feel):
| Element | Motion | Timing |
|---|---|---|
| Gauge value arc | ease to new temp | 250 ms `OutCubic` (→ `cubic-bezier(.33,1,.68,1)`) |
| Gauge glow | scale 1.0↔1.06 pulse while value>0 | 1600 ms InOutQuad loop |
| ProbeCard progress bar | width ease | 900 ms OutCubic |
| HopperCard fill | height ease | 900 ms OutCubic |
| HeaderBar live dot | opacity 1↔0.35 pulse | 1200 ms loop |
| Alert (lid open) | opacity flash | 500 ms loop |
| PressOverlay | warm glow on tap | 90 ms |
| FanIcon | spin 0→360 while active | 850 ms linear loop |
| AugerIcon | screw scroll + staggered falling pellets | 650 ms / 1400 ms |
| IgniterIcon | flame flicker + rising heat waves | multi-step / 1200 ms |

The Gauge (270° arc + setpoint marker + center readout) and the three animated SVG icons are the highest-fidelity pieces; **their SVG path data and timings are inline in the QML and can be lifted directly into React SVG components.** No line/graph charts exist in this UI (history graphs live only in the Flask web UI).

**Skip:** orphan components with no live references — `StatusIcon`, `HopperStatus`, `ModeBar`, `PModeControl`, `SmokePlusControl`, `TimerCard`, `CompactGauge` (~215 lines). Reproduce only what DashScreen actually assembles.

## Data & command layer — reuse existing SocketIO (no new backend bridge)

`blueprints/mobile/socket_io.py` already exposes exactly what's needed (verified):
- **Live data:** client emits `listen_app_data` on connect → server pushes `socket_dash_data` (~1 Hz, only on change), plus `socket_pellet_data`, `socket_event_data`. Payload shape is the `_get_dash_data` dict (`socket_io.py:210–263`): `currentMode`, `displayMode`, `primaryProbe`, `foodProbes[]`, `outputs.{fan,auger,igniter}`, `hopperLevel`, `smokePlus`, `pMode`, `timer{…}`, `lidOpenDetected`, `tempUnits`, `recipeStatus`, `grillName`, `errors/warnings`, etc.
- **Pull:** `get_app_data(action, arg01, arg02)` with `_GET_APP_DATA_DISPATCH` actions (`dash_data`, `settings_data`, `pellets_data`, `info_data`, `manual_data`, `recipe_data`, `hopper_level`).
- **Commands:** `post_app_data(action, type, json_data)` → `write_control(..., WriteKind.MERGE, origin=...)` — same channel `control.py` consumes. The QML→backend command set (`qtbackend.py:229–324` + `Menus.js`/`Actions.js`) maps 1:1 onto these posts (startup/stop/hold/notify/pmode/toggle outputs/prime/reboot…).

So the React data layer is a thin `socket.io-client` wrapper: connect → `listen_app_data` → reduce `socket_dash_data` into app state; user actions → `post_app_data`. This is the **same contract the existing web dashboard already uses**, so it's proven.

Note: the QML backend *derives* some display state locally (timer countdown text, cook-elapsed clock, idle/sleep machine at `qtbackend.py:180–221`). In the browser app these become small client-side derivations from the raw timer/timestamp fields already present in `socket_dash_data` (`startupTimestamp`, `modeStartTime`, `timer.start/paused/end`, durations). No backend change required.

## Architecture

- **Stack:** Vite + React + TypeScript, `socket.io-client`, Framer Motion (or plain CSS keyframes/transitions where cheaper) for the animation table above, SVG components for gauge/icons. Design tokens as CSS custom properties driven by an `accent` context (Ember/Ice/Crimson), mirroring `Theme.qml`.
- **State:** one `useDashData()` hook owning the socket connection + reducer over `socket_dash_data`; screen-local UI state (menu open, keypad value) in component state; a tiny `sendCommand()` that wraps `post_app_data`. No heavyweight store needed.
- **Components (mirror QML tree):** `Gauge`, `ProbeCard`, `HopperCard`, `SystemCard`, `FanIcon`/`AugerIcon`/`IgniterIcon`, `DutyPill`, `CookTimeBar`, `Alert`, `PressOverlay`, `ControlPanel`, `Keypad`, `MenuButton`, `HeaderBar`; screens `SplashScreen`, `DashScreen`, `MenuScreen`, `HoldInput`, `NotifyInput`, `QrCodeScreen`, `SleepOverlay`. Menu tree ported from `Menus.js`, action routing from `Actions.js`.
- **Responsive** (replaces QML's `compact` breakpoint at width ≤ 1100): CSS grid/flex with a breakpoint so it collapses gracefully phone → tablet → desktop kiosk. "Modern polish" latitude: re-implement visuals idiomatically (CSS gradients, backdrop blur) rather than matching QML pixel geometry, keeping the same information architecture and motion feel.
- **Serving:** build to static assets served by Flask under a new route (e.g. `/ui2` or a `react` blueprint serving `dist/`), sharing the existing SocketIO namespace. Dev: Vite dev server proxying `/socket.io` to the running PiFire app.
- **Location:** new top-level `web-react/` (source) building into a Flask-served `static/` dir. Keeps it fully isolated from the existing Jinja web UI and from the QML kiosk.

## Phasing

1. **Data spine** — `useDashData()` + `sendCommand()` against a live (or recorded) `socket_dash_data`. Prove live temps flow in and a command (e.g. Stop) round-trips.
2. **Theme + Gauge** — tokens, 3 accents, the 270° animated gauge (the marquee piece).
3. **DashScreen** — full 3-column assembly with all cards/icons + animation table.
4. **Menus + inputs** — MenuScreen from `Menus.js`, Keypad, Hold/Notify overlays, action routing.
5. **Splash / sleep / QR / responsive polish** and Flask serving route.
6. **Parity check** against the QML screens and the `tests/ui/test_qtquick_*.py` behavior specs.

## Proof-of-concept spike (deliverable now)

Scaffold `web-react/` with Vite + React + TS and build **the DashScreen center column against live data** — the highest-signal slice that de-risks both hard problems (animation fidelity + the socket data contract):
- `socket.io-client` wired to `listen_app_data`/`socket_dash_data`, with a **recorded-payload fallback fixture** (captured from `_get_dash_data`) so the spike renders without a running Pi.
- The **animated 270° `Gauge`** (value arc easing 250 ms OutCubic, glow pulse, setpoint marker, center readout) fed by `primaryProbe.temp/target`.
- The **`ControlPanel`** button row for the current mode, with `sendCommand` → `post_app_data` wired for at least Startup/Stop (round-trips through `write_control`).
- Theme tokens + one accent (Ember) as CSS variables.
Explicitly deferred in the spike: food-probe column, right column (system/hopper/duty), menus, keypads, splash/sleep, responsive breakpoints.

**Open question for the spike:** run Node/Vite tooling in this environment? If yes I scaffold and run it; if not, I deliver the spike as committed source + exact `npm`/`vite` commands for you to run. (Default: scaffold source, attempt install, fall back to instructions.)

## Verification

- Spike: `npm run dev` in `web-react/`, open in browser; with PiFire running, watch the gauge track live primary temp and confirm Startup/Stop change `control:general` (observable in the existing web UI or datastore). Without a Pi: fixture payload renders the gauge + panel; a mock socket asserts `post_app_data` is emitted with the right `(action, type, json_data)`.
- Full build: component-level tests (Vitest + Testing Library) for gauge angle math, keypad overwrite-first-digit logic, menu-variant-by-mode selection; a socket reducer test over a recorded `socket_dash_data` sequence. Cross-check screens against `display/qml/screens/*` and the `tests/ui/test_qtquick_*.py` specs.
