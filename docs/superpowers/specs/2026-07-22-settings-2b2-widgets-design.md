# Settings 2b-2 — Chart Colors, Range-Profile Tables, Controller Tab — Design Spec

**Date:** 2026-07-22
**Status:** Approved (design + mockups), pending implementation plan
**Phase:** 2b-2 of the React web-UI replacement (second fan-out slice of settings)
**Mockups:** https://claude.ai/code/artifact/68f66fde-6924-4b60-a03a-388ddc1dee97 (approved by user)

## Context

2b-1 shipped the five scalar tabs; the toolchain is now TS7 + rsbuild + Biome +
rstest with the `src/components/` vs `src/helpers/` tree split (one-way layering,
enforced by `src/structure.test.ts`). This slice adds the three deferred
new-widget surfaces. All Flask behavior below was verified against
`blueprints/settings/routes.py`, `common/defaults.py`, and
`controller/controllers.json` on the live tree.

Decisions made in brainstorming (user-confirmed):
- **Metadata source: new read-only `GET /api/controller_metadata` endpoint** —
  the phase's ONE backend change.
- **Controller config gets its own 9th tab** (not a WorkModeTab section),
  slotted after Work Mode in the nav.
- **Coverage: new files this phase must reach ≥75% line coverage; pure helpers
  100%** — enforced per-file in each task's gate via rstest's built-in coverage.

## Goals

1. History tab **Chart Colors** section — per-probe color/flag editing of
   `history_page.probe_config`.
2. A generic **RangeProfileTable** widget used twice: SmartStart profiles
   (Startup tab) and PWM duty-cycle profiles (PWM tab).
3. A new **Controller tab** — metadata-driven form over the 9 controllers.
4. Coverage tooling wired into the gate with the thresholds above.

## Non-Goals

- Notifications and Probe-config pages (their own later sub-projects).
- The `numlist` option type (defined in Flask's coercers but used by zero
  options in current metadata — YAGNI; revisit only if metadata gains one).
- Editing probe names/types from the colors section (that's probe config).
- 2b-1 follow-up nice-to-haves (waitFor conversion, fallback-default
  alignment, aria hints) — separate cleanup, must not ride along.

## Architecture

### Backend (the one change): `GET /api/controller_metadata`

Route in the settings blueprint (or app.py beside the existing `/api/settings`)
returning `read_generic_json("./controller/controllers.json")` verbatim:
`{"metadata": {<9 controller keys>: {friendly_name, module_name, description,
config: [{option_name, option_friendly_name, option_description, option_type,
option_default, option_min, option_max, …}]}}}`. Read-only, no auth change, no
settings write. Pytest web test: 200, `metadata` present, `pid` in keys,
`pid.config[0].option_name` truthy.

### Write paths (all through existing `POST /api/settings_update`)

| Surface | Delta | Flags | Flask parity |
|---|---|---|---|
| Chart Colors | `history_page.probe_config.<label>.{enabled, line_color, bg_color, line_color_target, bg_color_target, dash_setpoint, fill}` (+ `line_color_setpoint`, `bg_color_setpoint` on the Primary probe) | `[]` | `_settings_history` — bare write |
| SmartStart table | `startup.smartstart.{temp_range_list, profiles}` replaced wholesale | `[]` | `_settings_smartstart_post` — bare `write_settings` |
| PWM table | `pwm.{temp_range_list, profiles}` replaced wholesale | `[]` | `_settings_pwm_duty_cycle_post` — bare `write_settings` |
| Controller | `controller.{selected, config: {<selected>: {…}}}` — config for the selected controller rebuilt whole | `["controller_update"]` | `_settings_cycle` → `_apply_controller_config` sets `control["controller_update"]` |

`deep_update` replaces non-dict values including lists (verified,
`common/common.py:591`), so wholesale array replacement through the generic
endpoint is exact. `controller_update` is already in the endpoint's flag
whitelist.

### Colors: storage format and ColorField

Stored color strings are the nonstandard `rgb(r, g, b, 1)` form (from
`COLOR_LIST`, `common/defaults.py:24`). New pure helpers in
`helpers/settings/colorFormat.ts`:
- `rgbStringToHex(rgb: string): string` — parses `rgb(r, g, b[, a])` → `#rrggbb`
  (alpha ignored; malformed input falls back to `#000000`).
- `hexToRgbString(hex: string): string` — `#rrggbb` → `rgb(r, g, b, 1)` (always
  alpha 1, matching every stored value).
100% covered, round-trip property asserted on all 12 COLOR_LIST values.

`ColorField` (new primitive, `components/settings/fields/ColorField.tsx`):
label + `<input type="color">` + the current value swatch; converts on the way
in/out so consumers deal only in stored-format strings. Follows the existing
field-primitive props shape (`label`, `value`, `onChange`).

### Chart Colors section (HistoryTab)

Data source: `settings.history_page.probe_config` — an object keyed by probe
label; each entry carries `name`, `type` ("Primary"/"Food"), `enabled`,
`line_color`, `bg_color`, `line_color_target`, `bg_color_target`,
`dash_setpoint`, `fill`, and (Primary only) `line_color_setpoint`,
`bg_color_setpoint`. Render one card per label (order: object key order):
header (probe `name`, type chip, enabled Toggle), color grid (ColorFields for
the fields present on that entry — presence-driven, so Primary shows setpoint
colors and Food doesn't), and Dash-setpoint/Fill toggles. Save merges into the
tab's existing bare-write delta. Local state follows the house render-phase
`prevSettings` sync; the whole `probe_config` subtree is rebuilt into the delta
on save (same full-rebuild style as WorkModeTab).

Empty state: `probe_config` empty object → section renders a "No probes
configured" hint (still no crash).

### RangeProfileTable (generic widget)

`components/settings/RangeProfileTable.tsx`. Props:

```ts
interface RangeProfileColumn {
  key: string;            // profile object key, e.g. "startuptime"
  label: string;          // column header
  suffix?: string;        // "s", "%", …
  min?: number; max?: number;
}
interface RangeProfileTableProps {
  boundaries: number[];             // temp_range_list (N)
  profiles: Record<string, number>[]; // N+1 rows
  columns: RangeProfileColumn[];
  rangeHeader: string;              // "Range" / "ΔT range"
  unit: string;                     // "°F"/"°C" for range labels ("°" display)
  onChange(boundaries: number[], profiles: Record<string, number>[]): void;
}
```

Behavior:
- Derives range labels live from boundaries: row 0 `< b0`, row i `b(i-1) – bi-1`
  (integer display), last row `≥ bN-1`. Boundary values are edited inline in the
  Range column (row i edits boundary i, i < N).
- **Invariant `profiles.length === boundaries.length + 1` is enforced by
  construction**: "+ Add" appends a boundary (default: last boundary + 10) AND a
  profile row (copy of the last row); row-remove ✕ deletes that profile row and
  its adjacent boundary (removing the last row deletes the last boundary).
  Minimum 2 rows / 1 boundary — remove disabled below that.
- Non-numeric input coerces per NumberField's existing behavior; column
  min/max clamp on change.
- The widget is controlled — no internal copy of the arrays.

Usage:
- **StartupTab** Smart Start section: columns `startuptime` ("Startup time",
  s), `augerontime` ("Auger on", s), `p_mode` ("P-Mode", min 0 max 9);
  boundaries = `startup.smartstart.temp_range_list`.
- **PwmTab**: single column `duty_cycle` ("Duty cycle", %, min/max from
  `pwm.min_duty_cycle`/`pwm.max_duty_cycle` current values); boundaries =
  `pwm.temp_range_list`.
Each tab's Save replaces its two arrays wholesale in the delta (bare `[]`).

### Controller tab

- `SettingsShell` nav + route: 9th entry "Controller", path `controller`,
  positioned after Work Mode. `App.tsx` adds the route.
- `settingsLoader` gains a third parallel fetch: `getControllerMetadata(baseUrl)`
  (`helpers/settings/settingsApi.ts`) → `GET /api/controller_metadata`,
  fail-open to `null` (Promise.all never rejects from it). Outlet context
  becomes `{settings, mode, controllerMeta}`; existing tabs ignore the extra key.
- `ControllerTab.tsx`: if `controllerMeta` is null → error state ("Controller
  metadata unavailable") with no form. Otherwise: Select over the 9 controllers
  (friendly names), description text for the selected one, then one field per
  `config` option: `float`/`int` → NumberField (min/max from
  `option_min`/`option_max` when non-null; int coerces to integer on save),
  `bool` → Toggle. Values: `settings.controller.config[selected][option_name]`
  ?? `option_default`. Zero-config controllers (fuzzy, ml) show a "no
  configuration options" hint. Unknown `option_type` → skip the field and log
  nothing (parity: Flask's coercer would pass it through; none exist today).
- Save: delta `{controller: {selected, config: {[selected]: {<coerced values>}}}}`
  with `["controller_update"]`. Switching the Select swaps the rendered fields
  immediately (values re-derived from config/defaults); nothing is written
  until Save.

### Coverage gate

- `rstest` coverage enabled via CLI/config (`--coverage`, provider per rstest
  0.11 support — exact wiring verified at plan time; the config d.ts exposes
  `coverage: boolean | {provider: 'istanbul'|'v8', …}`).
- New script `"test:coverage": "rstest run --coverage"` (or config-flag
  equivalent). The per-task gate for THIS phase runs it and checks the report:
  **every new file ≥75% lines; `colorFormat.ts` and any other new pure helper
  100%**. Not applied retroactively to pre-existing files.

## Testing

- RTL (jsdom) per component: ColorField (renders swatch, emits stored-format
  string), RangeProfileTable (label derivation, add/remove maintaining the
  invariant, min-rows floor, clamping, onChange payloads), Chart Colors section
  (per-probe cards, Primary-only setpoint fields, save delta + `[]` flags),
  ControllerTab (metadata-driven render for pid, zero-config hint for fuzzy,
  selector swap, save delta + `["controller_update"]`, null-metadata error
  state), StartupTab/PwmTab table integration (save replaces both arrays,
  exact delta asserted).
- Pure (node): colorFormat round-trip incl. all COLOR_LIST values + malformed
  fallback.
- Backend: pytest web test for `/api/controller_metadata` (in the existing
  Playwright-adjacent pytest web suite, `tests/web/`).
- E2e additions to `web-react/tests/e2e/settings.spec.ts`: (1) Grill line-color
  change round-trips through Save + reload; (2) SmartStart startuptime edit
  round-trips; (3) Controller select PID Standard, edit PB, Save, reload,
  values persist.
- All rstest conventions per the toolchain: `rs.*` mocks, glob env split,
  `pluginReact` in rstest.config.ts already present.

## Verification

Per task: `bun run typecheck && bun run lint && bun run test && bun run build`
green; coverage check per the gate above; no new
eslint-disable/biome-ignore; structure.test.ts still green (new files land in
the right trees: widgets/fields under `components/`, colorFormat/api under
`helpers/`). E2e (with gunicorn restart — it must pick up the new
`/api/controller_metadata` route) green at the tasks that touch wiring.
Suite counts strictly grow; console pristine.
