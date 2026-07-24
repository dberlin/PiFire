# React Wizard — Display retrofit + Distance step (design spec)

**Date:** 2026-07-24
**Branch:** massive-reworks-and-new-ui
**Companion to:** `2026-07-23-wizard-probes-config.md` (probes) and
`2026-07-24-wizard-grillplatform-config.md` (grillplatform)

## Goal

Finish the React wizard's hardware-module family: retrofit the **display** step
onto the `/api/wizard/module-values` round-trip (which also fixes a reachable
install-killing crash), build the **distance/hopper** step (the last remaining
placeholder), and harden the detached installer against unknown dependency keys.

## Background — what already exists

- `POST /api/wizard/module-values {section, module}` → `{settings, config}` was
  built **general across grillplatform/display/distance** in the grillplatform
  slice (`blueprints/api_wizard/routes.py`). No backend endpoint work is needed.
- `_build_state` already computes `settings_dep_values["distance"]`, and
  `/finish` already validates the distance selection (`missing_sections`) and
  writes `settings_dep_values` for every section. **The backend is fully ready
  for both steps** — this slice is frontend + one installer guard.
- `ModuleCard` already renders `settings_dependencies` (generic `<select>`,
  `hidden` flag, `i2c_bus_num` and `usb_serial_device` widgets) and takes the
  `disabled` prop added in the grillplatform slice.
- `GrillPlatformStep` established the async-switch pattern: fetch module-values →
  replace that section's dep map → single `onChange`, with loading + error state
  and no half-apply on failure.

### Manifest facts (verified against `wizard/wizard_manifest.json`)

- **distance**: 7 modules; default is `none`. Six (`hcsr04`, `vl53l0x`,
  `vl53l4cd`, `vl53l1x`, `prototype`, `none`) have **empty
  `settings_dependencies` and no `config`** — their card renders image +
  description only. The lone exception is `sen0628` (USB ToF), with a single dep
  `sen0628_device` (`type: "usb_serial_device"`, default `/dev/ttyACM0`, path
  `platform.devices.distance.device`) — already supported by `ModuleCard`'s
  existing `UsbSerialPicker`. All I2C/GPIO wiring for the other sensors lives on
  the **grillplatform** card (`device_distance_*`), not here.
- **display**: 12 of 30 modules carry exactly one dep, `buttonslevel`
  (→ `platform.buttonslevel`); the other 18 have none.

## The bug this fixes (Important)

`DisplayStep.onSelectModule` currently only sets the selection
(`onChange(selectModule(working, "display", m))`) and never refreshes
`settings_dep_values.display`. So switching from one of the 12 `buttonslevel`
modules to any of the other 18 leaves a **stale `buttonslevel` key** in the
display dep map. That key is POSTed at `/finish` and reaches the detached
installer, which indexes it unguarded at `wizard.py:244`:

```python
settingsLocation = WizardData["modules"][module][selected]["settings_dependencies"][setting]["settings"]
```

The selected module has no `buttonslevel` dep → **`KeyError` inside the detached
process** → the install dies silently, frozen at its last status line while the
browser polls forever. This is the same failure mode as the previously-fixed
`IndexError` regression pinned by `tests/unit/wizard/test_wizard_run_no_probes.py`.

Two independent fixes, both in scope:
1. **Frontend (removes the trigger):** the round-trip replaces the whole dep map
   with exactly the target module's keys, so stale keys cannot survive a switch.
2. **Installer (defense in depth):** skip + log an unknown dep key instead of
   raising, so no future producer of a stale key can silently kill an install.

## Design decisions (approved 2026-07-24)

### D1 — Display switch fetches `settings` only; the config bag stays client-held

On a display module switch the client applies **only** the returned `settings`
(dep-values). `working.display_config` is **not** touched. A user who edits e.g.
Screen Rotation, switches away, and switches back keeps their edit.

This deliberately diverges from legacy `_wizard_modulecard`, which re-reads
`settings["display"]["config"][module]` on every switch and thereby discards
unsaved edits — legacy behaves that way only because it re-renders the entire
card server-side. The React wizard already treats `display_config` (and
`probe_map`) as client-held draft state; preserving edits is consistent with
that architecture and strictly better UX. The endpoint still returns `config`
for display; the display step simply ignores it.

### D2 — Harden the installer's dependency lookup

`wizard.py`'s settings-write loop skips (and logs) a setting whose name is not in
the selected module's `settings_dependencies`, instead of raising `KeyError`.
Valid input behaves identically; only the crash path changes.

## Architecture

### Shared abstraction — `useModuleSwitch`

Three steps (grillplatform, display, distance) now need identical async-switch
mechanics: fetch module-values, hold `loading`, surface an error, and never
half-apply. Extract one hook:

```
web-react/src/helpers/wizard/useModuleSwitch.ts

export function useModuleSwitch(params: {
  baseUrl: string;
  section: WizardSection;
  errorMessage: string;
  apply: (values: ModuleValues, newModule: string) => void;
}): { loading: boolean; error: string | null; switchModule: (newModule: string) => void }
```

- `switchModule(newModule)` calls `fetchModuleValues(baseUrl, section, newModule)`;
  on success it invokes `apply(values, newModule)`; on rejection it sets `error`
  to `errorMessage` and **never** calls `apply`. `loading` is true for the
  duration.
- **Previous module:** `apply` is defined inside the component body and closes
  over the render's `working`, so it reads the pre-switch selection directly
  (`working.selections[section]`). This is exactly the semantics
  `GrillPlatformStep` has today (it captured `prevModule` before the await) — do
  not thread `prevModule` through the hook.

Each step supplies its own `apply`:

| Step | `apply` composition |
|---|---|
| Display | `selectModule` → `setSectionDepValues(..., values.settings)` (config bag untouched) |
| Distance | `selectModule` → `setSectionDepValues(..., values.settings)` |
| GrillPlatform | the above **+** `replaceProbeMap(reseedProbeMapForBoard(...))` (unchanged behavior) |

`GrillPlatformStep` is refactored onto the hook. This is **behavior-preserving**:
its three existing tests are the pin and must stay green untouched.

### Components

- **`DisplayStep.tsx`** (modify): switch handler routes through `useModuleSwitch`;
  `disabled={loading}` on the card; error banner. Everything else — 
  `configSource="settings-by-module"`, `configValues={displayConfigFor(...)}`,
  `onConfigChange` → `setDisplayConfig`, `onDepChange` → `setDepValue` — is
  unchanged.
- **`DistanceStep.tsx`** (create): mirrors `DisplayStep`'s shape with
  `configSource="none"`, `configValues={{}}`, `onConfigChange={() => {}}`.
- **`WizardShell.tsx`** (modify): `case "distance"` renders `DistanceStep`;
  `PlaceholderStep` becomes unused by the shell (keep the component and its test
  — it is still exercised directly and is harmless).

### Installer (`wizard.py`)

In the settings-write loop, replace the unguarded index with a lookup that skips
unknown keys:

```python
dependencies = WizardData["modules"][module][selected]["settings_dependencies"]
dependency = dependencies.get(setting)
if dependency is None:
    # A setting name that isn't in the selected module's manifest entry (e.g. a
    # stale key left over from a module switch). Skip it rather than raising --
    # this loop runs in the DETACHED installer process, where an uncaught
    # exception freezes the install at its last status line forever.
    set_wizard_install_status(percent, status, f"   - Skipped unknown setting {setting}")
    continue
settings = set_nested_key_value(settings, dependency["settings"], selected_setting)
```

(Exact placement must preserve the existing `units` special-case branch that runs
before this lookup.)

## Data flow

1. User reaches the display or distance step; `ModuleCard` renders the current
   module's fields from `working.settings_dep_values[section]`.
2. Switch → `useModuleSwitch` fetches `/module-values` → `apply` replaces that
   section's dep map (display additionally leaves `display_config` alone).
3. Field edits → `setDepValue` / `setDisplayConfig`, client-side only.
4. Step transitions flush the draft; `/finish` writes each section's dep map.
5. The installer writes each dep to its manifest `settings` path, now skipping
   unknown keys.

## Error handling

- Fetch failure: inline error banner, previous selection and dep-values left
  untouched, retry by re-selecting. Per-step copy supplied via `errorMessage`.
- Unknown section/module server-side → existing `400 unknown_module`.
- Installer: unknown dep key → skipped + logged, install continues.

## Testing (coverage ≥75% lines per file; `bun run lint` in every gate)

- **`useModuleSwitch` unit tests** (`.test.ts`): success invokes `apply` once
  with the fetched values; rejection sets the error and never invokes `apply`;
  `loading` toggles true→false on both paths.
- **`DisplayStep`**: switching replaces `settings_dep_values.display` wholesale
  so a **stale key from the previous module is gone** (the crash-fix assertion);
  a `display_config` edit **survives** switch-away-and-back (pins D1); fetch
  failure shows the banner and does not call `onChange`.
- **`DistanceStep`**: a no-dep module (e.g. `none`) renders a bare card; the
  `sen0628` module renders the USB serial field; switching applies fetched
  settings.
- **`GrillPlatformStep`**: its three existing tests must pass **unmodified**
  after the hook refactor (the behavior-preservation pin).
- **`WizardShell`**: the distance step renders `DistanceStep`, not the
  placeholder.
- **Installer** (`tests/unit/wizard/`): a stale/unknown dep key in
  `install_info["modules"]["display"]["settings"]` no longer raises and the
  install completes. Reuse the existing `no_install` fixture from
  `tests/unit/wizard/test_wizard_run_no_probes.py` (it neutralizes
  `subprocess.run`, `is_real_hardware`, and `time.sleep`). **Before running,
  grep the exercised path for `os.system` / `subprocess` / `sudo` / `reboot` /
  `shutdown` and confirm every side effect is neutralized** — no test may fire a
  real install.
- **e2e** (`web-react/tests/e2e/wizard.spec.ts`): the distance step renders its
  module card and switches modules. (Run in the main checkout; agent worktrees
  skip `[chromium]`.)

## Global constraints

- **Toolchain:** TS7 (`bun run typecheck`, `noUnusedLocals`), rsbuild, Biome,
  **`@rstest/core`** (`rs.fn`/`rs.mock`, NOT vitest/`vi`; `.test.ts`→node,
  `.test.tsx`→jsdom). **bun, not npm.**
- **`bun run lint` must exit 0** for the branch tip (Biome enforces format; two
  pre-existing `react-refresh` *warnings* on `App.tsx`/`WizardShell.tsx` are
  acceptable — errors are not).
- **Python:** `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest`;
  `uvx ruff format` changed Python before every commit; PEP 758 bare-tuple
  `except A, B` is canonical — do not rewrite.
- **Security:** no test may fire the real installer.

## Out of scope (follow-ups)

- **Display config manifest defaults:** `ConfigOptionField` renders `String(value)`
  and does not fall back to the manifest `option.default`, so a never-configured
  display module shows no selection for its list options. Pre-existing in the
  shipped display slice; a separate small fix.
- `PlatformTab` (the scalar `settings["platform"]` settings tab).
- `first_time_setup` auto-redirect to `/wizard`.
- Any change to `dc_fan` derivation or `board-config.py` install behavior.
