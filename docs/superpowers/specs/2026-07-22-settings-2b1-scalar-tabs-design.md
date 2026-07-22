# Settings 2b-1 — Scalar Tabs + RTL Harness — Design Spec

**Date:** 2026-07-22
**Status:** Approved (design), pending implementation plan
**Phase:** 2b-1 of the React web-UI replacement (first fan-out slice of settings)

## Context

Phase 2a stood up the settings foundation (data-router shell, `GET /api/settings`
loader, the `POST /api/settings_update` write endpoint, a form-primitive kit, and
three tabs). Phase 2b fans the remaining settings sections out over that machinery.
This spec is **phase 2b-1: the five scalar tabs + a React Testing Library (RTL)
harness**. The new-widget sections (History chart colors, SmartStart/PWM
profile tables, and the metadata-driven controller-config form) are **phase 2b-2**.

**No backend changes.** Every section here is a settings subtree, so it writes
through the *existing* `POST /api/settings_update` with the correct control flags;
React performs the same field coercion the Flask `_settings_*` handlers did. This
was verified against `blueprints/settings/routes.py` and `common/defaults.py`.

## Goals

1. Five working scalar settings tabs (Safety, Pellet levels, Work Mode basics,
   Startup/Shutdown, History basics), each reusing the phase-2a primitives and
   the loader/`useSaveSettings` pattern, wired into the settings nav/routes.
2. **Adopt RTL** as the component-testing approach: convert the two
   component-proxy pure tests to rendered-component tests, add RTL tests for every
   existing component that has none, and keep genuinely-pure functions as pure
   unit tests.
3. Fold in the deferred phase-2a minors (UnitsTab `CommandResult.ok` check,
   router `HydrateFallback`).

## Non-Goals (this slice → 2b-2)

- History **chart colors** (`history_page.probe_config.*` rgba fields → needs a
  new `ColorField`).
- **SmartStart** and **PWM** profile **tables** (`*.temp_range_list`/`profiles` →
  needs a new range-profile table widget).
- Work Mode **controller config** (`controller.selected`/`config` → needs the
  dynamic metadata-driven form from `controllers.json`).
- Notifications and Probe config (their own later sub-projects).

## Architecture

### Write path (unchanged endpoint, per-section flags)

All tabs `save(delta, flags)` via `useSaveSettings` → `POST /api/settings_update`.
Flags per section (verified against the Flask handlers):

| Tab | Settings subtree | Flags | Client-side coercions/care |
|---|---|---|---|
| **Safety** | `safety.*` (7 keys) | `[]` (bare write — `_settings_safety` sets no flag) | numbers + toggles |
| **Pellet levels** | `pelletlevel.*`, `globals.augerrate` (float), `globals.prime_ignition` | `["settings_update"]`, **plus `"distance_update"`** when `empty` or `full` changed from the loaded value | numbers + toggles |
| **Work Mode (basics)** | `cycle_data.*` (10), `smoke_plus.*` (7), `keep_warm.*` (2) | `["settings_update"]` | numbers + toggles |
| **Startup/Shutdown** | `startup.*`, `shutdown.*`, `startup.smartstart.{enabled,exit_temp}`, `startup.start_to_mode.*` | `["settings_update"]` | clamp `startup.prime_on_startup` to 0–200; clamp `startup.pwm_duty_cycle` to `pwm.min_duty_cycle..max_duty_cycle`; `after_startup_mode` is a **Select** |
| **History (basics)** | `history_page.{minutes,datapoints,clearhistoryonstart,autorefresh}`, `globals.ext_data` | `[]` (bare write) | `autorefresh` stored as the **string `"on"/"off"`** (not bool); **`ext_data` gated** — see below |

The whitelist added in 2a already permits `settings_update`, `controller_update`,
`distance_update`, `probe_profile_update`, so `distance_update` is accepted.

### `ext_data` mode gate

The Flask `_settings_history` handler only writes `globals.ext_data` when the
grill is **Stopped** (changing it mid-cook yields a cook whose history rows have
inconsistent columns). The generic `/api/settings_update` does **not** enforce
this, so the client must. Implementation: **`settingsLoader` also fetches the
current mode** and returns `{ settings, mode }` (mode via `GET /api/get/mode` or
`/api/current`). The History tab disables the `ext_data` toggle (with a hint)
unless `mode === "Stop"`. `{ settings, mode }` is provided to tabs via the
`Outlet` context; existing tabs (`useOutletContext<{ settings }>`) are unaffected
(extra key ignored), though they may widen the type.

### RTL harness

Add jsdom + Testing Library to Vitest without disturbing the fast pure tests:
- Dev-deps (bun): `@testing-library/react`, `@testing-library/jest-dom`,
  `@testing-library/user-event`, `jsdom`.
- `vite.config.ts` `test.setupFiles` → a `src/test-setup.ts` that imports
  `@testing-library/jest-dom/vitest` (harmless for node tests).
- **Component tests are `*.test.tsx` with a `// @vitest-environment jsdom`
  docblock**; pure tests stay `*.test.ts` (node env). This keeps pure tests fast
  and node-scoped while component tests get a DOM.
- A small `renderTab(tab, { settings, mode })` test helper renders a tab inside a
  memory router that supplies the `Outlet` context (and a `useSaveSettings`/save
  spy), so tabs can be rendered and driven in isolation.

## Testing strategy (the RTL adoption)

**Convert to RTL** (logic extracted from components → test through the render):
- `dashboard/deriveView.test.ts` → `Dashboard.test.tsx`: render `<Dashboard>` (or
  the widgets) with fixture `dash` data and assert the actual DOM (mode badge
  text, probe cards, FAN/AUGER/IGNITER status, hopper %/label, pill values) and
  accent/animate behavior — replacing assertions on the intermediate view-model.
- `dashboard/controlButtons.test.ts` → `ControlButtons.test.tsx`: render
  `<ControlButtons>` per mode and assert the rendered buttons, and that clicking
  fires the expected `command`/opens the setpoint/confirm modal.

**Add RTL tests** for components that currently have none: `GrillGauge`,
`ProbeCard`, `SystemStatus`, `HopperGauge`, `SetpointEntry`, `ConfirmAction`,
`Banners`, the five field primitives (`Toggle`/`Select`/`NumberField`/`TextField`/
`Section`), `SettingsShell`, and each of the eight settings tabs (the three from
2a + the five new). Each test renders the component and asserts its key rendered
output and interaction (e.g., a `NumberField` shows its value/suffix and calls
`onChange` with the parsed number; a tab renders loaded values, edits a field,
clicks Save, and asserts the `save` spy got the right `(delta, flags)`).

**Keep as pure unit tests** (genuinely pure; RTL adds nothing / cannot apply):
`command.ts`, `settings/settingsApi.ts` (URL + fetch-body builders), `health.ts`
(`clampSetpoint`/`deriveControlAlive`), `settings/delta.ts` (`setPath`),
`components/gaugeMath.ts`, `demoData.ts`, `types.ts` (fixture shape), and
`fmtDuration`.

## Deferred-minor fixes (folded in)

- **`UnitsTab.confirmChange`** checks `CommandResult.ok` before updating local
  `units` / revalidating; on failure it surfaces an error and does not show the
  new unit.
- **`HydrateFallback`**: give the data router a `HydrateFallbackElement` (or a
  small `HydrateFallback` route component) so the "No HydrateFallback element
  provided during initial hydration" console warning is gone (pristine console).

## Components / files (high level)

- New tabs: `web-react/src/settings/tabs/{SafetyTab,PelletsTab,WorkModeTab,StartupShutdownTab,HistoryTab}.tsx` + their `.test.tsx`.
- `web-react/src/settings/settingsRoutes.ts` — loader returns `{ settings, mode }` (new `getMode`/status fetch in `settingsApi.ts`).
- `web-react/src/settings/SettingsShell.tsx` + `App.tsx` — add the 5 nav entries + routes; add `HydrateFallback`.
- RTL harness: `web-react/src/test-setup.ts`, `vite.config.ts` (setupFiles), `package.json` (dev-deps), a `renderTab` test util.
- Converted tests: `Dashboard.test.tsx`, `ControlButtons.test.tsx` (replacing the two `.test.ts`); new `*.test.tsx` for the remaining components.
- `web-react/src/settings/tabs/UnitsTab.tsx` (ok-check).

## Verification

`bunx tsc -b` clean · `bun run lint` clean (react-hooks + React Compiler, no new
suppressions) · `bun run test` green (pure tests + all new RTL tests; console must
be pristine — no HydrateFallback warning) · `bun run build` green · a Playwright
e2e that saves at least one scalar tab (e.g. Safety `maxtemp`) and asserts it
round-trips, run against the live prototype backend (restart gunicorn first — it
has no `--reload`). Dashboard route + demo mode unaffected.

## Phase 2b-2 (next, out of scope here)

`ColorField` + History probe-color grid; a generic range-profile table for
SmartStart + PWM profiles; and the dynamic metadata-driven controller-config form
(8 controllers from `controllers.json`, up to 27 fields for MPC).
