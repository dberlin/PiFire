# React Wizard — GrillPlatform Config (design spec)

**Date:** 2026-07-24
**Branch:** massive-reworks-and-new-ui
**Companion to:** the display slice and `2026-07-23-wizard-probes-config.md` (probes slice)

## Goal

Replace the placeholder `grillplatform` step of the React setup wizard
(`web-react/`) with a working module-config surface: board/platform selection
plus every GPIO/I2C/relay/fan field for the selected platform, persisted to
`settings["platform"]` on finish. Selecting a board on a fresh install also
pre-populates the probes step from that board's default probe map.

## Background — what already exists

The wizard "module-card" spine is already built and shipping for the display
and probes steps:

- **Backend is fully generic across all four sections.** `_build_state`
  (`blueprints/api_wizard/routes.py:61-126`) already computes
  `settings_dep_values["grillplatform"]` via
  `get_settings_dependencies_values`. `_wizard_install_info_from_payload`
  (routes.py:246-309) already writes the grillplatform module + settings.
  `/finish` (routes.py:312-351) already validates the grillplatform selection
  (`missing_sections`) and the whole-config I2C bus kinds
  (`wizard_bus_kinds` → `validate_bus_kinds`). **No new persist/validate code
  is needed server-side for the platform fields themselves** — the detached
  installer (`wizard.py`) writes every `platform.*` key generically by walking
  each dependency's manifest `settings` path.
- **`ModuleCard`** (`web-react/src/components/wizard/ModuleCard.tsx`) already
  renders `settings_dependencies`: it honors the per-field `hidden` flag
  (line 42), dispatches the `i2c_bus_num` widget with the paired
  `_num`→`_kind` field (line 47), the `usb_serial_device` widget, and a generic
  `<select>` from `dep.options` otherwise. grillplatform's "pin/PWM/DC-fan/
  trigger widgets" are **all** just this generic `<select>` renderer — there are
  no bespoke pin-grid/PWM widgets in the manifest.
- **`DisplayStep`** (`.../steps/DisplayStep.tsx`) is the exemplar: a thin
  wrapper around `ModuleCard` + the `wizardState` helpers.

### Manifest facts (verified against `wizard/wizard_manifest.json`)

- `modules.grillplatform` has **7 modules**; default (`default: true`) is
  `pcb_4.x.x`. The others: `custom`, `pcb_2.00a`, `pcb_3.01a`, `pcb_pwm`,
  `x86_numato`, `ft232h_relay`.
- Every field is a `<select>` over `{value: label}` `options` (first key is the
  default) or the shared `i2c_bus_num` / `usb_serial_device` widget. Fixed-board
  wiring uses `hidden: true` + a single option. There is **no `config` bag** on
  any grillplatform module (0/7).
- `dc_fan` is a visible field only on the Pi platforms (`custom`, `pcb_4.x.x`).
  `x86_numato`/`ft232h_relay` **omit `dc_fan` from their manifest** (it is
  derived at install from `system_type`/fan chip in `select_grillplat_module`),
  so the generic renderer already does the right thing — no special-casing.
- There is **no `frequency`** field under grillplatform (PWM frequency lives in
  `settings["pwm"]`, a separate subsystem — out of scope).
- `manifest.boards` has an entry for exactly the **4 PCB ids** (`pcb_2.00a`=3
  probes, `pcb_3.01a`/`pcb_pwm`/`pcb_4.x.x`=4 probes each); the board id is the
  same string as the grillplatform module key. `custom`/`x86_numato`/
  `ft232h_relay` have **no** board entry.

## Design decisions (approved 2026-07-24)

### D1 — Platform-switch dep-values: server round-trip (legacy parity)

Legacy re-renders the card on module switch via an AJAX round-trip
(`_wizard_modulecard`, legacy `blueprints/wizard/routes.py:105-119`) that reads
`get_settings_dependencies_values(settings, targetModule)` from the **live
settings tree**. The React SPA reproduces this exactly: switching platform
fetches the target module's dep-values from a new JSON endpoint and replaces
`settings_dep_values["grillplatform"]`. This is byte-exact legacy parity for
both fresh and existing installs (including the legacy quirk that a fresh-install
switch reads the generic default `platform.*` values, not the target board's
manifest fixed pins — `board-config.py` applies canonical pins at install). No
client-side default reproduction is needed.

### D2 — Board probe_map reseed: guarded, fresh-install only

Selecting a board (re)seeds `working.probe_map` from
`manifest.boards[board_id].probe_map`, but **only** when:
1. `first_time_setup` is `true`, **and**
2. the current `probe_map` has **not diverged** from the *previous* board's
   default (deep equality) — so any manual probe edits the user made are never
   clobbered.

Boards without a `boards` entry (`custom`/`x86_numato`/`ft232h_relay`) reseed to
an empty map (`{probe_devices: [], probe_info: []}`) under the same guard.
Existing installs (`first_time_setup: false`) never touch `probe_map` on switch.

## Architecture

grillplatform becomes a `DisplayStep`-shaped slice over the existing spine, with
`configSource="none"`, plus two new seams for D1/D2.

### Backend (`blueprints/api_wizard/routes.py`)

1. **`POST /api/wizard/module-values`** — body `{section, module}`, returns
   `{settings, config}`, mirroring legacy `_wizard_modulecard`:
   - `moduleData = read_wizard()["modules"][section][module]`;
     `settings = get_settings_dependencies_values(read_settings(), moduleData)`.
   - `config` is display-only: `settings["display"]["config"].get(module, {})`
     for `section == "display"` (the callout-#2 KeyError guard), `{}` for
     `grillplatform`/`distance`. General across the 3 non-probe sections so
     display/distance can adopt it later; grillplatform consumes only `settings`.
   - Unknown `section` (not in `grillplatform`/`display`/`distance`) or unknown
     `module` → `400` with `{"result": "error", "message": "unknown_module"}`.
     `probes` is not a valid section here (probes has no module-card).

2. **`_build_state`** additions:
   - Ship `board_probe_maps: {board_id: {probe_devices, probe_info}}` built from
     `wizard_data.get("boards", {})` (each `[id]["probe_map"]`). Missing
     `probe_map` on a board → skip that board.
   - On `first_time_setup`, seed the returned `probe_map` from
     `info["probe_map"]` (the board default that `wizardInstallInfoDefaults`
     already computes at legacy `wizard.py:70-72` and that `_build_state`
     currently discards at routes.py:107) instead of the live-settings probe_map.
     Existing-install path is unchanged.

### Frontend

3. `web-react/src/helpers/wizard/wizardApi.ts` →
   `fetchModuleValues(baseUrl, section, module): Promise<{settings: Record<string, string | null>, config: Record<string, unknown>}>`.

4. `web-react/src/helpers/wizard/wizardTypes.ts` →
   `WizardState.board_probe_maps: Record<string, ProbeMap>`; a
   `ModuleValues { settings: Record<string, string | null>; config: Record<string, unknown> }`
   type.

5. `web-react/src/helpers/wizard/wizardState.ts` →
   - `setSectionDepValues(w, section, values): WizardWorking` — replace one
     section's dep map wholesale (used after a module-values fetch).
   - `replaceProbeMap(w, map): WizardWorking`.
   - **`reseedProbeMapForBoard(currentMap, prevBoardMap, newBoardMap, firstTimeSetup): ProbeMap`**
     — the one piece of real logic, pure and TDD'd like the probe reducer.
     Returns `newBoardMap` when `firstTimeSetup && deepEqual(currentMap, prevBoardMap)`,
     otherwise returns `currentMap` unchanged. `prevBoardMap`/`newBoardMap` are
     `board_probe_maps[module] ?? EMPTY_PROBE_MAP`. Deep equality via a stable
     structural compare (a small `deepEqual` helper or `JSON.stringify` on the
     normalized maps — the maps are plain JSON with stable key order from the
     manifest).

6. `web-react/src/components/wizard/steps/GrillPlatformStep.tsx` — mirrors
   `DisplayStep` but:
   - `<ModuleCard section="grillplatform" configSource="none" configValues={{}} .../>`.
   - **async `onSelectModule(newModule)`**: hold `prevModule =
     working.selections.grillplatform`; `fetchModuleValues(baseUrl,
     "grillplatform", newModule)`; on success apply `selectModule` →
     `setSectionDepValues("grillplatform", res.settings)` → guarded
     `replaceProbeMap(reseedProbeMapForBoard(...))`, then `onChange`. Local
     `loading` state (disable the select / show a spinner while fetching) and an
     `error` banner if the fetch fails (leave the prior selection intact on
     failure — do not half-apply).
   - `onDepChange` / dep editing routes through the existing
     `setDepValue(working, "grillplatform", key, value)` (no round-trip on
     field edits, only on module switch).

7. `web-react/src/components/wizard/WizardShell.tsx` → render
   `GrillPlatformStep` for the `grillplatform` case (replacing its
   `PlaceholderStep`; distance stays a placeholder). grillplatform is already
   step index 1, before probes (index 2), so the reseed lands before the user
   reaches the probes step.

## Data flow

1. `/state` → `WizardState` now carries `board_probe_maps` and (fresh install)
   the default board's `probe_map`.
2. User opens grillplatform (step 1). `ModuleCard` shows the current/default
   platform's fields from `settings_dep_values["grillplatform"]`.
3. User switches platform → `fetchModuleValues` → dep-values replaced from live
   settings (D1); `probe_map` guarded-reseeded from the new board (D2).
4. User edits fields → `setDepValue` updates `working` client-side.
5. Step transitions flush the draft (existing `saveDraft`); the probes step
   reads the (possibly reseeded) `working.probe_map`.
6. `/finish` → `_wizard_install_info_from_payload` writes grillplatform module +
   settings, validates bus kinds, fires the installer (all existing).

## Error handling

- `fetchModuleValues` failure: `GrillPlatformStep` shows an inline error banner
  and leaves the previous selection/dep-values/probe_map untouched (no partial
  apply). The user can retry by re-selecting.
- Unknown section/module server-side → `400 unknown_module` (defensive; the UI
  only ever sends valid manifest module keys).
- `reseedProbeMapForBoard` is total (never throws): a board id absent from
  `board_probe_maps` resolves to `EMPTY_PROBE_MAP`.
- No new `/finish` behavior — the existing STOP-gate (409), missing-selection
  (400), and bus-conflict (422) paths already cover grillplatform.

## Testing (coverage stays ≥75% lines per file)

- **`reseedProbeMapForBoard` unit tests** (`.test.ts`, node): unedited current
  map + fresh install → reseeds to new board; edited current map → preserved;
  new board has no entry → empty map (when unedited); `firstTimeSetup: false` →
  no-op regardless; prev board absent from map (initial `null` selection) →
  treated as `EMPTY`.
- **`setSectionDepValues` / `replaceProbeMap` unit tests** — immutability +
  correct section targeting.
- **`GrillPlatformStep` component test** (`.test.tsx`, jsdom) with
  `fetchModuleValues` mocked (`rs.mock`): switching module applies server
  settings + reseeds probe_map; fetch rejection shows the error banner and does
  not mutate working state; loading state disables interaction.
- **Backend endpoint tests** (`tests/web/test_api_wizard.py`): `module-values`
  returns settings for a grillplatform module; returns `{}` config for
  grillplatform and the guarded `.get(module, {})` config for display;
  `400 unknown_module` for a bad module; `_build_state` ships
  `board_probe_maps` and (fresh install) the default board's probe_map.
- **e2e** (`web-react/tests/e2e/wizard.spec.ts`): open grillplatform → fields
  render → switch board → advance to probes step and see the reseeded probe set
  → finish. (e2e is re-run in the main checkout per repo convention; agent
  worktrees skip `[chromium]`.)

## Global constraints (from the project)

- **Toolchain:** TS7 (`bun run typecheck`, `noUnusedLocals`), rsbuild, Biome,
  **`@rstest/core`** (`rs.fn`/`rs.mock` — NOT vitest/`vi`; `.test.ts`→node,
  `.test.tsx`→jsdom). **bun, not npm.**
- **Python tests:**
  `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest`. `uvx ruff
  format` on changed Python before every commit. PEP 758 bare-tuple `except A,
  B` is canonical (do not "fix").
- **Security:** any test path that reaches `/finish`'s installer `os.system`
  must monkeypatch it — no test fires the real installer.
- Coverage ≥75% lines per changed file.

## Out of scope (follow-ups)

- Retrofitting `DisplayStep`/distance to use the `module-values` round-trip on
  switch (they share the latent no-reseed gap; the endpoint is built general so
  this is later just wiring). Record in the react-migration backlog.
- The distance step (still a placeholder).
- `PlatformTab` (the scalar `settings["platform"]` settings tab — a separate
  backlog item, unrelated to this wizard slice).
- Any change to `dc_fan` derivation or `board-config.py` install behavior — the
  round-trip reproduces legacy exactly.
