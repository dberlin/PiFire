# Settings Foundation — Design Spec

**Date:** 2026-07-22
**Status:** Approved (design), pending implementation plan
**Phase:** 2a of the React web-UI replacement (foundation slice; 2b fans out remaining tabs)

## Context

Phase 1 made the `web-react` dashboard real (live socket reads + REST command
writes). Phase 2 begins replacing the Flask/Jinja **settings** surface. Rather
than build all ~13 settings tabs at once, this spec covers a **thin foundation
slice**: the app shell/routing, the settings read/write data layer, the one
small backend write endpoint, a styled form-primitive kit, and **three
representative tabs** chosen to exercise every settings write path exactly once.
Once this pattern is proven end-to-end, phase 2b fans out the remaining tabs
(Work Mode, Safety, Startup/Shutdown, History, Pellet levels, …) over the same
machinery with no new architecture.

### Why a backend endpoint is needed (and why it's the only backend change)

The current Jinja UI does **not** route most settings through `/api/settings`.
Each tab POSTs a server-rendered form to its own route (`POST /settings/cycle`,
`/settings/pwm`, …); those handlers mutate the settings dict **and set the
correct control-update flags inline** (`settings_update`, `controller_update`,
`distance_update`, …) via `save_settings_and_flag_update`
(`common/app.py:396`). `POST /api/settings` only does `deep_update` +
`write_settings` and sets **no flags** — so on its own it's safe only for
display-only settings (grill name, theme). A JSON SPA has two options:

- **(A)** reuse the existing `/settings/<action>` form routes — zero backend
  change, but the client must replicate each tab's irregular form-field contract
  and parse HTML responses; brittle coupling to the Jinja forms.
- **(B, chosen)** add **one** small JSON endpoint mirroring
  `save_settings_and_flag_update`: `{settings-delta, flags[]}` → `deep_update` +
  `write_settings` + set the named flags + `write_control`. One uniform JSON
  contract for every tab, decoupled from the Jinja forms. ~15 lines, reusing
  existing helpers — no new control logic.

Units is the one exception either way: changing units needs value **conversion**
+ `units_change` (and forces the grill to STOP), so it keeps using the existing
`/api/set/units/{F|C}` command, not a raw settings write.

## Goals

1. An app shell with client-side routing: the dashboard stays the scaled-canvas
   hero on `/`; settings live under `/settings/*` as responsive, scrolling pages.
2. A settings data layer: load `GET /api/settings`, edit locally, save deltas.
3. One backend endpoint that applies a settings delta **with** caller-named
   control flags.
4. A small styled form-primitive kit (Toggle, Select, NumberField, TextField,
   Section) matching the dashboard's design language.
5. Three working tabs proving all three write paths: **Grill Name + Theme**
   (plain `/api/settings`), **PWM** (new endpoint + `settings_update`), **Units**
   (existing `/api/set/units`).

## Non-Goals (this slice)

The remaining settings tabs (Work Mode/controller, Safety, Startup/Shutdown,
History, Pellet levels, Notifications, Probe config), the PWM **profiles** table
(`pwm.temp_range_list`/`profiles` — only the scalar PWM fields here), and all
standalone feature pages. Those are phase 2b+. No auth.

## Architecture

### Backend (one addition)

Add a JSON endpoint in `blueprints/api/routes.py`, as a module-level
`_`-prefixed handler registered in `_API_POST_ACTIONS` (following the repo's
established convention — no `services.py`). Proposed contract:

```
POST /api/settings/update
body: { "settings": <partial settings dict>, "flags": ["settings_update", ...] }
→ settings = read_settings()
  settings = deep_update(settings, body["settings"])
  control  = read_control()
  save_settings_and_flag_update(settings, control, *body["flags"], origin="api")
→ 200 { "result": "OK", "message": "...", "data": <settings> }
```

`flags` may be empty (display-only writes). Valid flags are the control-loop
triggers: `settings_update`, `controller_update`, `distance_update`,
`probe_profile_update`. The existing `POST /api/settings` is left unchanged.

### Dev tooling & build config (prerequisite)

- **Router mode = library, not framework.** Use `react-router` as a library
  (`<BrowserRouter>` + `<Routes>`), because the app is served as **static files
  by Flask/gunicorn**. Do NOT adopt `@react-router/dev` framework mode (its own
  build/SSR server would conflict with static serving). Consequence: the React
  Compiler needs **no** router-specific vite change — the existing
  `@vitejs/plugin-react` + `@rolldown/plugin-babel` + `reactCompilerPreset()`
  wiring in `vite.config.ts` already compiles router components (this is the
  react.dev "Vite + Babel" path, which the repo already matches). The plan must
  **confirm** this config still holds after adding the router; it should not
  switch to `vite-plugin-babel`/framework mode.
- **ESLint (new — the repo has no JS linting today).** Add a flat config
  (`eslint.config.js`) with: `@eslint/js` recommended, `typescript-eslint`,
  `eslint-plugin-react-hooks` **recommended-latest** (this includes both the
  rules-of-hooks / exhaustive-deps rules AND the React Compiler rule
  `react-hooks/react-compiler`, which validates the compiler's assumptions), and
  `eslint-plugin-react-refresh`. Add `"lint": "eslint ."` to `package.json`
  scripts. `bun run lint` must be clean and is part of the verification gate
  going forward. Installed via **bun** (`bun add -d`), commit `bun.lock`.

### Frontend

- **Router:** `react-router` (library mode, per above). Routes:
  `/` → the existing `Dashboard`; `/settings` → settings shell with nested tab
  routes (`/settings/general`, `/settings/pwm`, `/settings/units`, …).
- **App shell:** the design's header **menu** (hamburger) navigates to settings;
  a settings shell renders a nav rail (tab list) + the active tab's content in a
  responsive, scrolling layout (NOT the scaled 1280×720 canvas — real reflow).
- **Settings data layer** (`src/settings/useSettings.ts`): `GET /api/settings`
  into typed state; a `saveSettings(delta, flags)` that POSTs to
  `/api/settings/update`; a `setUnits(F|C)` that calls the existing command
  client. Optimistic-free: reload settings after a successful save (mirrors the
  dashboard's server-driven model).
- **Form primitives** (`src/settings/fields/`): `Toggle`, `Select`,
  `NumberField`, `TextField`, `Section` — controlled components styled with the
  existing tokens (`theme.css`), each emitting `(path, value)` changes a tab
  collects into a delta.
- **Tabs** (`src/settings/tabs/`): `GeneralTab` (grill name → `globals.grill_name`
  plain save; theme → `globals.page_theme` plain save), `PwmTab` (scalar
  `pwm.*` fields → save with `["settings_update"]`), `UnitsTab` (`globals.units`
  → `setUnits`).

## Components / files (new)

- `web-react/src/settings/useSettings.ts` — load/save hook + types slice.
- `web-react/src/settings/settingsApi.ts` — `getSettings()`, `applySettings(delta, flags)`; pure `buildSettingsUrl` for testability.
- `web-react/src/settings/SettingsShell.tsx` — nav rail + `<Outlet/>`, responsive.
- `web-react/src/settings/fields/{Toggle,Select,NumberField,TextField,Section}.tsx`.
- `web-react/src/settings/tabs/{GeneralTab,PwmTab,UnitsTab}.tsx`.
- `web-react/src/settings/settings.css` — responsive settings layout + field styles.
- Modify `web-react/src/App.tsx` — introduce the router (`/` vs `/settings/*`).
- Modify the dashboard header menu button → navigate to `/settings`.
- Backend: `blueprints/api/routes.py` — add `_api_post_settings_update` + register.
- Tooling: `web-react/eslint.config.js` (new flat config) + `package.json`
  (`react-router` dep, eslint dev-deps, `lint` script); confirm `vite.config.ts`
  compiler wiring unchanged.

## Data flow

1. Enter `/settings/*` → `useSettings` fetches `GET /api/settings` once → typed state.
2. User edits fields → tab accumulates a delta (changed subtree only).
3. Save:
   - General/Theme → `applySettings(delta, [])` → `POST /api/settings/update`.
   - PWM → `applySettings(delta, ["settings_update"])`.
   - Units → `command.setUnits(...)` (existing `/api/set/units/{F|C}`), then re-fetch.
4. On success → re-fetch settings (server-driven truth); show a saved indicator.
   The live dashboard continues to reflect any control-affecting change on its
   next socket frame.

## Error handling

- `GET /api/settings` failure → a settings-level error state with retry.
- Save failure (non-OK envelope / network) → inline error on the tab; fields
  keep the user's edits (no silent loss); no optimistic state to roll back.
- Units change warns that it will **stop the grill** (backend forces STOP) before
  applying — a confirm gate, reusing the phase-1 `ConfirmAction` modal.

## Testing

- **Unit (vitest):** `buildSettingsUrl` + `applySettings` body shape (delta +
  flags); a pure delta-builder for each tab (given form state → correct partial
  settings dict + flag set); units confirm-gate logic.
- **Backend (pytest):** the new `/api/settings/update` endpoint — a settings
  delta persists AND the named flags land in `control` (assert via
  `read_control`), using the existing web test harness
  (`tests/web/conftest.py`); empty-flags case sets none.
- **e2e (Playwright, live prototype backend):** navigate `/` → `/settings`;
  change **grill name** and assert it round-trips (re-fetch shows it, dashboard
  header updates); toggle a **PWM** field and assert the control `settings_update`
  path fired (value persists after reload); change **units** F→C via the confirm
  gate and assert the dashboard reflects °C.

## Rollout / verification

`bunx tsc -b` clean · **`bun run lint` clean** (react-hooks + react-compiler
rules) · vitest green · the new pytest green under
`QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/...` ·
`bun run build` green · e2e round-trips green against `control.py` + gunicorn.
Dashboard route unaffected; demo mode still works.

## Phase 2b (follow-on, out of scope here)

Fan out the remaining tabs over this exact machinery (no new architecture),
each mapping to its settings subtree + required flags: Work Mode
(`cycle_data`/`controller`/`smoke_plus`/`keep_warm`; `settings_update` +
`controller_update`), Safety, Startup/Shutdown/SmartStart, History, Pellet
levels (`distance_update`), the PWM/SmartStart **profiles** tables, then
Notifications and Probe config as their own larger sub-projects.
