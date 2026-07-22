# Dashboard-Real — Design Spec

**Date:** 2026-07-22
**Status:** Approved (design), pending implementation plan
**Phase:** 1 of the React web-UI replacement (see Roadmap)

## Context

The `web-react/` app is a React 19 + Vite spike that reimplements the PiFire
controller UI as a browser app. Its dashboard was ported from a Claude Design
mock and currently runs against an **offline demo simulator** (`VITE_DEMO=1`).
It has never been exercised against the real PiFire backend.

We have decided to make the React app a **full replacement** for the existing
Flask/Jinja web UI. This spec covers **phase 1: making the dashboard real** —
connecting it to the genuine backend, correcting the data contract, and wiring
control actions to real commands. Settings and feature pages are later phases.

### Why this is more than "point at a URL"

Auditing the backend surfaced that the dashboard's controls are **broken against
a real backend today**:

- The app sends `post_app_data(action="update", …)`, but the real socket
  dispatch key is `"update_action"` (`blueprints/mobile/socket_io.py:732`). Every
  button press is a silent no-op on a real Pi. The demo never caught it because
  the offline path just `console.info`s commands.
- The hand-written `DashData`/`ProbeData` types are a thin subset of the real
  `socket_dash_data` payload (`_get_dash_data`, socket_io.py:197) — missing
  `criticalError`, `pwmControl`, `lidOpenDetect*`, `startupTimestamp`,
  `modeStartTime`, `hasDcFan`, `hasDistanceSensor`, `allowManualOutputs`, and the
  full rich probe structure (battery, limits, per-probe status).
- The design's control buttons are static labels with no input flow; PiFire's
  Hold mode **requires a target temperature**.

## Goals

1. The dashboard renders live data from the real backend, running locally in
   prototype mode (no physical Pi required).
2. Every dashboard control performs the correct real action (mode changes,
   setpoint, Smoke+, timer, system commands).
3. The React data types match the real `socket_dash_data` payload verbatim.
4. Connection/error states are surfaced honestly (reconnect, errors/warnings,
   critical error, control-process-down).
5. Demo mode (`VITE_DEMO=1`) still works as the offline dev path.

## Non-Goals (phase 1)

Settings pages, feature pages (recipes/cookfile/tuner/history/metrics/updater/
admin/wizard), authentication, and any backend changes. The backend already
exposes everything phase 1 needs; **no Python changes are expected**.

## Architecture

Clean read/write split over the existing backend API:

- **Reads (live stream):** SocketIO `socket_dash_data` push (~1 Hz), the channel
  the backend already emits and the app already listens on. Kept.
- **Writes (commands):** REST, using PiFire's authoritative command grammar
  (`common/api_commands.py` `_COMMAND_DISPATCH`) via `blueprints/api/routes.py`
  (`/api/<action>/<arg0>/<arg1>/…`, methods GET+POST). This is the tested command
  path the CLI/API already use, and it sets up phase 2 (`/api/settings`) cleanly.

```
 React dashboard ──socket "socket_dash_data" (read, 1 Hz)──▶ app.py / socket_io
                └─REST POST /api/set|cmd/… (write)─────────▶ app.py / api → process_command → write_control
                                                                                 │
 control.py (prototype grill platform) ──writes current/status/control──▶ datastore (SQLite)
                                                                                 │
 socket_io _get_dash_data reads datastore ◀──────────────────────────────────────┘
```

### Command contract (dashboard subset)

Writes are `POST` (semantically correct for state changes; the route also
accepts GET). Mode strings are **lowercase**.

| Action | Endpoint |
|---|---|
| Startup | `POST /api/set/mode/startup` |
| Smoke | `POST /api/set/mode/smoke` |
| Hold @ temp | `POST /api/set/psp/{temp}` (sets `primary_setpoint` **and** forces Hold) |
| Monitor | `POST /api/set/mode/monitor` |
| Prime | `POST /api/set/mode/prime/{grams}[/{next_mode}]` |
| Shutdown | `POST /api/set/mode/shutdown` |
| Stop | `POST /api/set/mode/stop` |
| Smoke+ toggle | `POST /api/set/splus/{true\|false}` |
| P-mode | `POST /api/set/pmode/{n}` |
| Timer | `POST /api/set/timer/{start/{sec}\|pause\|stop\|shutdown/{bool}\|keep_warm/{bool}}` |
| System | `POST /api/cmd/{reboot\|shutdown\|restart}` |

Envelope: the API returns `{data, result, message}` (`common/app.py:api_response`).
`result != "OK"` (or a non-2xx) is treated as a command failure and surfaced.

## Components

New/changed, all under `web-react/src/`:

### 1. `types.ts` — expanded data contract
Rewrite `DashData` and `ProbeData` to mirror `_get_dash_data` /
`_get_probe_structure` exactly, including nested `timer`, `outputs`,
`recipeStatus`, and the full probe object (`eta`, `setTemp`, `maxTemp`, `target`,
`lowLimitTemp`, `highLimitTemp`, `*Req/*Shutdown/*Triggered/*Reignite`, `device`,
and `status:{batteryCharging,batteryPercentage,batteryVoltage,connected,error}`).
Keep the `[k:string]:unknown` index signature for forward-compat. Update
`fixture.ts` and `demoData.ts` to the new shape (a captured real payload is
preferred for the fixture — see Testing).

### 2. `command.ts` — typed REST command client (new)
`createCommand(baseUrl)` returning typed helpers:
`setMode(mode)`, `hold(temp)`, `setSmokePlus(on)`, `setPMode(n)`,
`timerStart(sec)/timerPause()/timerStop()`, `prime(grams,next?)`,
`system(cmd)`. Each builds the `/api/…` URL, POSTs, parses the envelope, and
throws/returns a typed result on failure. Pure URL-building is factored into a
testable `buildCommandUrl(...)`.

### 3. `useDashData.ts` — hardened live hook
- Keep the socket `socket_dash_data` subscription; keep `VITE_DEMO` path.
- Add reconnect/backoff and a richer `ConnectionPhase`
  (`connecting|live|unreachable|demo`) plus a derived `controlAlive` flag from
  the backend's 30 s control-process health error.
- Expose `command` (from `command.ts`, bound to the same target) instead of the
  old socket `send`. Remove the broken `post_app_data` send path.

### 4. Dashboard control flow
- `dashboard/controlButtons.ts` → map buttons to **real** transitions:
  - Stopped/Error/off: **Startup**, **Prime**, **Monitor**.
  - Cooking (Startup/Smoke/Hold/Prime/Reignite): **Smoke**, **Hold** (opens
    setpoint entry), **Smoke+** toggle, **Shutdown**, **Stop**.
  - Monitor: **Startup**, **Stop**.
  Each descriptor carries a typed `command` invocation (not action/type strings).
- `dashboard/SetpointEntry.tsx` (new): a numeric stepper/keypad modal for Hold /
  "Set Temp". Tapping the gauge's `SET` value re-opens it to adjust live. Submits
  `hold(temp)`.
- `dashboard/ConfirmAction.tsx` (new): confirm gate for **Stop** and **Shutdown**.
- `Dashboard.tsx`: consume `command`; render error/warning/critical banners from
  `dash.errors`/`dash.warnings`/`dash.criticalError`; reflect real connection +
  `controlAlive` in the header live-dot/status.

### 5. Dev harness
- `vite.config.ts`: proxy **`/api`** as well as `/socket.io` to
  `VITE_PIFIRE_URL || http://localhost:5000`.
- `package.json`: add `dev:backend` documenting the two-process prototype launch
  (`control.py` + `app.py`). README section on running the real backend locally.

## Data flow

1. `control.py` (prototype platform) runs the control loop, writing
   current/status/control to the datastore.
2. `app.py` SocketIO emits `socket_dash_data` ~1 Hz; the hook `setDash`es it.
3. `deriveView(dash)` (unchanged, pure) maps it to the view model; components
   render.
4. User action → `command.*()` → `POST /api/set|cmd/…` → `process_command` →
   `write_control` → control loop picks it up → next `socket_dash_data` reflects
   the change. UI is fully server-driven (no optimistic local state beyond the
   in-flight/disabled button treatment).

## Error handling

- **Socket down / unreachable:** `ConnectionStatus` screen (existing), naming the
  target URL; keep retrying underneath.
- **Control process down:** connected but `controlAlive=false` → header shows a
  "controller offline" state; commands disabled with a tooltip.
- **Command failure:** non-OK envelope / network error → transient toast; the
  button returns to idle; no optimistic state to roll back.
- **Backend errors/warnings/criticalError:** rendered as banners; `criticalError`
  is prominent and blocks control affordances as appropriate.

## Testing

- **Unit (vitest):** keep `deriveView`/`fmtDuration` tests; add
  `buildCommandUrl` tests (every command → exact URL, incl. lowercase modes,
  Hold-needs-temp, timer sub-paths); update `demoData` tests for the new shape.
- **Fixture capture:** boot the prototype backend, capture a real
  `socket_dash_data` payload, and use it verbatim as `fixture.ts` so types are
  validated against reality.
- **Round-trip smoke (Playwright, against the running prototype backend):** load
  the app pointed at the local backend; assert live data renders; click
  **Startup**, then set **Hold** to a temp; assert the mode/setpoint change
  round-trips back into the dashboard via the socket. This is the proof that
  "real" works end-to-end.

## Rollout / verification

`bunx tsc -b` clean · vitest green · production build green · Playwright
round-trip green against `control.py`+`app.py` in prototype mode. Demo mode
(`VITE_DEMO=1`) still renders.

## Roadmap (subsequent phases, out of scope here)

2. Settings foundation (`/api/settings` layer, schema-driven forms, app shell) +
   easy tabs (Units, Grill Name, Theme, Work Mode, Safety, PWM, Startup/Shutdown,
   History, Pellet levels).
3. Probe config (temp profiles + assignment + hardware device/port mapping).
4. Notifications (apprise/ifttt/pushbullet/pushover/onesignal/influxdb/mqtt/wled).
5. Feature pages (pellet manager, recipes, cookfile, tuner, history charts,
   metrics/events/logs, updater, admin, wizard).
