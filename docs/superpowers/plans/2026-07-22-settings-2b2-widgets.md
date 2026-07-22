# Settings 2b-2 (Chart Colors, Range Tables, Controller Tab) + Coverage Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the three deferred settings surfaces (History chart colors, SmartStart/PWM range-profile tables, metadata-driven Controller tab) AND bring every existing web-react source file to ≥75% line coverage with permanent threshold enforcement.

**Architecture:** Coverage first (tooling → backfill → flip thresholds on), so the feature tasks land under an enforced gate. Then one backend read-only endpoint, then the three feature slices bottom-up: pure helpers → field primitive → tab sections → the new tab. All writes go through the existing `POST /api/settings_update` with Flask-verified flags.

**Tech Stack:** React 19 + rsbuild 2.1.7, TS7 typecheck, Biome + slim eslint, rstest 0.11.3 + `@rstest/coverage-istanbul` (installed), RTL, bun. Backend: Flask blueprint `blueprints/api/routes.py`, pytest (`uv run pytest`).

## Global Constraints

- **bun only** — never npm/npx. Commit `bun.lock` with dependency changes (none expected — coverage provider already installed).
- Full gate per task: `cd web-react && bun run typecheck && bun run lint && bun run test && bun run build` → all green, console pristine. From Task 1 on, ALSO `bun run test:coverage` (green; thresholds enforce from Task 5 on).
- Test conventions: rstest — `rs.*` mocks (`rs.fn`, `rs.mock`, hoisted like `vi.mock`), env split by extension (`*.test.tsx` → jsdom project, `*.test.ts` → node), `renderRoute(ui, context)` from `src/test-utils.tsx` for anything using `useOutletContext`, global `afterEach(cleanup)` already installed. **`rs.mock` path strings are invisible to tsc — get them right by hand and verify by running the test.**
- Tree + layering rules (enforced by `src/structure.test.ts`): components → `src/components/**` (PascalCase.tsx), logic → `src/helpers/**` (camelCase.ts); helpers must NEVER import from components; no case-folded sibling module names.
- House style: loader-state sync via render-phase `prevSettings` compare — NEVER `useEffect`+setState. NO new `eslint-disable` or `biome-ignore` anywhere.
- Write-path flags (Flask-verified, exact): Chart Colors bare `[]`; SmartStart table bare `[]`; PWM table bare `[]`; Controller `["controller_update"]`.
- Coverage policy: every non-excluded `src/**` file ≥75% lines (excluded: `main.tsx`, `*.d.ts`, `test-setup.ts`, `test-utils.tsx`, all `*.test.*`); new pure helpers 100%. Tests assert real behavior — an assertion-free render written to farm coverage is a defect.
- Backend tasks: run `uvx ruff format` on changed `.py` files before committing. Test with `uv run pytest` (bare `python` gives false failures).
- E2e (Tasks 12): live backend on :5000; **restart gunicorn first** (no `--reload` — it must load the new `/api/controller_metadata` route): find master pid of `gunicorn ... app:app`, kill it, relaunch from repo root `uv run gunicorn -k gthread --threads 25 -b 0.0.0.0:5000 -w 1 app:app`, verify `curl -s -o /dev/null -w '%{http_code}' http://localhost:5000/api/current` → 201. control.py keeps running — never touch it. First e2e run after restart may cold-start-flake (roundtrip.spec.ts timeout) — rerun before diagnosing.
- Stage commits with explicit paths from `web-react/` (bare `git add -A` there has staged repo-root strays).
- Baseline (from the spec, measured 2026-07-22): suite 80.1% lines imported-files-only; below 75: WorkModeTab 51.2, SafetyTab 65.2, buttonsForMode 66.7, settingsApi 35.3; at exactly 75.0: ControlButtons, Dashboard; invisible/0%: App, AppPrefs, DashboardRoute, ConnectionStatus, SettingsError, GeneralTab, PwmTab, UnitsTab, useDashData, settingsRoutes, useSaveSettings.

---

### Task 1: Coverage tooling (report-only — thresholds come in Task 5)

**Files:**
- Modify: `web-react/rstest.config.ts`, `web-react/package.json`

**Interfaces:**
- Produces: `bun run test:coverage` — istanbul text report where files NO test imports appear at 0%. Tasks 2–4 use its per-file table as their exit criterion; Task 5 adds `thresholds`.

- [ ] **Step 1: Add the coverage block** to `web-react/rstest.config.ts` at the TOP level of the `defineConfig({...})` object (sibling of `projects`, not inside a project):

```ts
coverage: {
  provider: "istanbul",
  all: true,
  include: ["src/**/*.{ts,tsx}"],
  exclude: [
    "src/**/*.test.*",
    "src/main.tsx",
    "src/**/*.d.ts",
    "src/test-setup.ts",
    "src/test-utils.tsx",
  ],
},
```

- [ ] **Step 2: Add the script** to `package.json`: `"test:coverage": "rstest run --coverage"`.

- [ ] **Step 3: Run it** — `bun run test:coverage`. Expected: 114 tests pass; the table now ALSO lists the previously-invisible files (`App.tsx`, `AppPrefs.tsx`, `DashboardRoute.tsx`, `ConnectionStatus.tsx`, `SettingsError.tsx`, `GeneralTab.tsx`, `PwmTab.tsx`, `UnitsTab.tsx`, `useDashData.ts`, `settingsRoutes.ts`, `useSaveSettings.ts`) at or near 0%. If any of them is still absent, the `all: true`/`include` wiring is wrong — fix before proceeding. Paste the full per-file table into your report (it is the phase's honest baseline).

- [ ] **Step 4: Full gate + commit**

```bash
bun run typecheck && bun run lint && bun run test && bun run build
git add rstest.config.ts package.json
git commit -m "test(web-react): wire istanbul coverage with all-files reporting"
```

---

### Task 2: Helpers coverage backfill (settingsApi, routes/save hooks, useDashData, buttonsForMode)

**Files:**
- Modify: `web-react/src/helpers/settings/settingsApi.test.ts` (extend), `web-react/src/helpers/dashboard/buttonsForMode.test.ts`? — NOTE: no such file exists; the mode matrix lives in `components/dashboard/ControlButtons.test.tsx`. Create `web-react/src/helpers/dashboard/buttonsForMode.test.ts` (pure).
- Create: `web-react/src/helpers/settings/settingsRoutes.test.ts`, `web-react/src/helpers/settings/useSaveSettings.test.tsx`, `web-react/src/helpers/useDashData.test.tsx`

**Interfaces:**
- Consumes: Task 1's `bun run test:coverage` table.
- Produces: all files under `src/helpers/` ≥75% lines. Exit criterion is the coverage table, not test count.

- [ ] **Step 1: settingsApi error/edge tests** (extend the existing node test; the file exports `getSettings`, `getMode`, `applySettings`-family and currently sits at 35.3% — lines 13-26,45 uncovered are the fetch bodies). Stub `globalThis.fetch` with `rs.fn` (wrap `afterEach` restore in a block — `rs.unstubAllGlobals()` returns non-void). Cases:
  - `getSettings` resolves parsed `settings` from `{settings: {...}}` envelope; HTTP non-ok → throws.
  - `getMode` returns the mode string from `{data: {mode}}`; fetch REJECTS → returns `""` (fail-open — this is load-bearing for the loader).
  - the settings-update POST helper sends `{settings, flags}` JSON body to `/api/settings_update` and surfaces `{result:"error"}` as a failed result.
  Example shape:

```ts
it("getMode fails open to empty string", async () => {
  rs.stubGlobal("fetch", rs.fn().mockRejectedValue(new Error("down")));
  await expect(getMode("")).resolves.toBe("");
});
```

- [ ] **Step 2: settingsRoutes loader test** (node) — `rs.mock("./settingsApi")`, stub `getSettings`→fixture settings, `getMode`→`"Stop"`, (after Task 11 also `getControllerMetadata` — today just these two): assert `settingsLoader()` resolves `{settings, mode}` with both values, and that a `getSettings` rejection propagates (loader errors surface to the route error element).

- [ ] **Step 3: useSaveSettings hook test** (jsdom, `.test.tsx`) — use `renderHook` from `@testing-library/react` wrapped in a memory-router provider (`useSaveSettings` calls `useRevalidator`; reuse `renderRoute`'s router-wrapping approach — if `renderHook` fights the router, render a probe component through `renderRoute` instead). Stub fetch. Assert: calling `save(delta, flags)` POSTs the exact `{settings: delta, flags}` body, returns ok on `{result:"success"}`, non-success → not-ok result, and a revalidation is triggered on success.

- [ ] **Step 4: useDashData socket test** (jsdom, `.test.tsx`) — `rs.mock("socket.io-client")` exporting `io: rs.fn()` returning a fake socket `{on: rs.fn(), off: rs.fn(), close: rs.fn(), emit: rs.fn()}` that captures handlers. Render via `renderHook`. Cases: (a) demo mode (`PUBLIC_DEMO` path — import.meta.env is baked; drive the demo branch via whatever the hook exposes, else skip demo and note it); (b) live mode: registering `socket_dash_data` handler, first frame updates state, `connect`/`disconnect` toggles the connected flag; (c) unmount closes the socket. Target ≥75% of `useDashData.ts` — the interval/reconnect tails may stay uncovered; that's fine above the bar.

- [ ] **Step 5: buttonsForMode pure matrix** (node) — one `it` per mode: Stop, Startup, Reignite, Smoke, Hold, Shutdown, Monitor, Prime (whatever `buttonsForMode` switches on — enumerate from the source, not this list) asserting the button set (labels + action kinds) for each, plus the unknown-mode fallback. This retires the 66.7% gap (uncovered 27,32,41-42,57-59 are mode branches).

- [ ] **Step 6: Coverage check** — `bun run test:coverage`: every `src/helpers/**` file ≥75% lines (settingsApi should land ≥90). Paste the helpers rows into the report.

- [ ] **Step 7: Full gate + commit**

```bash
bun run typecheck && bun run lint && bun run test && bun run build
git add src/helpers
git commit -m "test(web-react): backfill helpers coverage (settingsApi, loader, save/dash hooks, buttonsForMode)"
```

---

### Task 3: Settings-components coverage backfill (2a tabs + shell pieces)

**Files:**
- Create: `web-react/src/components/settings/tabs/GeneralTab.test.tsx`, `.../PwmTab.test.tsx`, `.../UnitsTab.test.tsx`, `web-react/src/components/settings/SettingsError.test.tsx`

**Interfaces:**
- Consumes: `renderRoute`, `rs.mock` of `../../../helpers/settings/useSaveSettings` (the 2b-1 tab tests are the exact pattern — copy their mock/spy arrangement from e.g. `SafetyTab.test.tsx`).
- Produces: every file under `src/components/settings/**` ≥75% lines.

- [ ] **Step 1: GeneralTab tests** — render with fixture settings (grill name + theme fields per the component source), assert loaded values; edit grill name; Save → assert the spy got the exact delta and `[]` flags (GeneralTab is the no-flag write). Include the render-phase resync case: re-render with changed settings object → fields show new values.

- [ ] **Step 2: PwmTab tests** — render scalar fields from fixture (`pwm.pwm_control/update_time/frequency/min_duty_cycle/max_duty_cycle` — enumerate from the component source), edit one number + one toggle, Save → exact delta + `["settings_update"]`.

- [ ] **Step 3: UnitsTab tests** — current unit renders; selecting the OTHER unit opens the confirm modal (assert its title mentions stopping the grill); confirm → `rs.mock` of `../../command`'s `createCommand` asserts `setUnits` called with the target unit and revalidation follows on ok; **failure path**: `setUnits` resolves not-ok → error surfaces and the displayed unit does NOT change (this is the 2b-1 ok-check — cover it).

- [ ] **Step 4: SettingsError test** — render it (it's the route errorElement; give it a thrown error via the router if it uses `useRouteError` — `createMemoryRouter` with a loader that throws) and assert the message + retry affordance render. Also add whatever tiny case `SettingsShell.tsx:21` needs (its uncovered branch — check the source; it's the 85.7%→100 line).

- [ ] **Step 5: Coverage check** — `bun run test:coverage`: all `src/components/settings/**` ≥75. WorkModeTab/SafetyTab top-ups belong to Task 4 only if still short after this task's suite additions — check the table and note.

- [ ] **Step 6: Full gate + commit**

```bash
bun run typecheck && bun run lint && bun run test && bun run build
git add src/components/settings
git commit -m "test(web-react): backfill settings-component coverage (2a tabs, error/shell)"
```

---

### Task 4: Dashboard/app components backfill + sub-75 top-ups

**Files:**
- Create: `web-react/src/components/AppPrefs.test.tsx`, `web-react/src/components/DashboardRoute.test.tsx`, `web-react/src/components/ConnectionStatus.test.tsx`, `web-react/src/components/App.test.tsx`
- Modify (extend): `web-react/src/components/dashboard/Dashboard.test.tsx`, `.../ControlButtons.test.tsx`, `web-react/src/components/settings/tabs/WorkModeTab.test.tsx`, `.../SafetyTab.test.tsx` (+ `StartupTab.test.tsx` if Task 3's check showed it short)

**Interfaces:**
- Consumes: fake-socket pattern from Task 2's useDashData test (`rs.mock("../helpers/useDashData")` is simpler here — mock the HOOK, not the socket, for component tests).
- Produces: every remaining `src/**` file ≥75% lines — the full-table precondition for Task 5's thresholds.

- [ ] **Step 1: ConnectionStatus** — render both states it supports (connecting; unreachable-with-URL) and assert the copy incl. the target URL.

- [ ] **Step 2: DashboardRoute** — `rs.mock` the path it imports `useDashData` from; return (a) no-data state → ConnectionStatus renders; (b) fixture DashData → Dashboard renders (assert mode badge). Two tests.

- [ ] **Step 3: AppPrefs** — render the provider with a probe child consuming the context; toggle accent → `document.documentElement` gets `data-accent`; toggle animate → the animate flag propagates. Assert persisted side effects it performs (read the source for localStorage usage and assert it).

- [ ] **Step 4: App routing smoke** — `rs.mock` `../helpers/useDashData` (fixture data) AND `../helpers/settings/settingsApi` (fixture settings + mode). App uses `createBrowserRouter` — if it exports the routes array, build a `createMemoryRouter` from it in the test; if not, add a tiny named export of the routes array from `App.tsx` (structure-preserving refactor, no behavior change) and test through that. Assert: `/` renders the dashboard; `/settings/general` renders the shell + GeneralTab.

- [ ] **Step 5: Top-ups to clear every remaining sub-75 row** — consult the Task 3 coverage table and close what's left, guided by the uncovered-line lists: ControlButtons 86-99 (drive the confirm-required button path through ConfirmAction accept AND cancel), Dashboard 56-57,158-170 (cook-timer branch and/or hopper/pellet display variants — read the lines), WorkModeTab (edit at least one field in each of the three sections + assert full delta), SafetyTab (edit remaining numeric fields + both toggles, assert full delta). Every added test asserts rendered DOM or exact save payloads — no bare renders.

- [ ] **Step 6: Coverage check — THE table is clean**: `bun run test:coverage` shows every included file ≥75% lines. Paste the full table.

- [ ] **Step 7: Full gate + commit**

```bash
bun run typecheck && bun run lint && bun run test && bun run build
git add src/components
git commit -m "test(web-react): backfill dashboard/app coverage; all files >=75% lines"
```

---

### Task 5: Enforce coverage thresholds permanently

**Files:**
- Modify: `web-react/rstest.config.ts`

**Interfaces:**
- Consumes: Task 4's all-green table.
- Produces: `bun run test:coverage` FAILS if any included file drops below 75% lines. Every later task's gate inherits this.

- [ ] **Step 1: Add thresholds** to the coverage block:

```ts
thresholds: {
  "src/**/*.{ts,tsx}": { lines: 75, perFile: true },
},
```

- [ ] **Step 2: Prove enforcement fires** — temporarily set `lines: 101`, run `bun run test:coverage`, expect a NONZERO exit complaining about thresholds; restore `75`, rerun → exit 0. Paste both outputs (this is the negative proof; a threshold that can't fail is decoration).

- [ ] **Step 3: Full gate + commit**

```bash
bun run typecheck && bun run lint && bun run test && bun run test:coverage && bun run build
git add rstest.config.ts
git commit -m "test(web-react): enforce per-file 75% line-coverage threshold"
```

---

### Task 6: Backend — GET /api/controller_metadata

**Files:**
- Modify: `blueprints/api/routes.py`
- Test: `tests/web/test_page_api.py` (extend)

**Interfaces:**
- Produces: `GET /api/controller_metadata` → 201, body = the parsed `controller/controllers.json` (top-level key `metadata` with 9 controller entries). Task 11's `getControllerMetadata` consumes it.

- [ ] **Step 1: Write the failing test** in `tests/web/test_page_api.py` (match the file's existing client/fixture style — read two neighboring tests first):

```python
def test_api_controller_metadata(client):
    r = client.get("/api/controller_metadata")
    assert r.status_code == 201
    body = r.get_json()
    assert "metadata" in body
    assert "pid" in body["metadata"]
    cfg = body["metadata"]["pid"]["config"]
    assert cfg and cfg[0]["option_name"]
```

- [ ] **Step 2: Run it — FAIL** — `uv run pytest tests/web/test_page_api.py -k controller_metadata -q`. Expected: the API returns its unknown-action response, not 201.

- [ ] **Step 3: Implement** in `blueprints/api/routes.py` — import `read_generic_json` from the same module `blueprints/settings/routes.py` imports it from (grep that file's import line); add beside the other GET handlers:

```python
def _api_get_controller_metadata(settings, server_status):
    return jsonify(read_generic_json("./controller/controllers.json")), 201
```

and register `"controller_metadata": _api_get_controller_metadata,` in `_API_GET_ACTIONS`.

- [ ] **Step 4: Run — PASS**, then the file's whole suite: `uv run pytest tests/web/test_page_api.py -q` (all green; [chromium]-marked tests may skip in agent envs — note if so).

- [ ] **Step 5: Format + commit**

```bash
uvx ruff format blueprints/api/routes.py tests/web/test_page_api.py
git add blueprints/api/routes.py tests/web/test_page_api.py
git commit -m "feat(api): read-only GET /api/controller_metadata for the React controller form"
```

---

### Task 7: colorFormat helpers + ColorField primitive

**Files:**
- Create: `web-react/src/helpers/settings/colorFormat.ts`, `web-react/src/helpers/settings/colorFormat.test.ts`, `web-react/src/components/settings/fields/ColorField.tsx`, `.../ColorField.test.tsx`

**Interfaces:**
- Produces: `rgbStringToHex(rgb: string): string`, `hexToRgbString(hex: string): string`; `<ColorField label value onChange />` where `value`/`onChange` speak the STORED format (`"rgb(r, g, b, 1)"`). Tasks 8 uses ColorField.

- [ ] **Step 1: Failing pure tests** (`colorFormat.test.ts`, node) — round-trip all 12 stored COLOR_LIST strings, literally:

```ts
const COLOR_LIST_VALUES = [
  "rgb(0, 64, 255, 1)", "rgb(0, 128, 255, 1)", "rgb(0, 200, 64, 1)", "rgb(0, 232, 126, 1)",
  "rgb(132, 0, 0, 1)", "rgb(200, 0, 0, 1)", "rgb(126, 0, 126, 1)", "rgb(126, 64, 125, 1)",
  "rgb(255, 210, 0, 1)", "rgb(255, 255, 0, 1)", "rgb(255, 126, 0, 1)", "rgb(255, 126, 64, 1)",
];
it("round-trips every stored COLOR_LIST value", () => {
  for (const v of COLOR_LIST_VALUES) expect(hexToRgbString(rgbStringToHex(v))).toBe(v);
});
it("malformed rgb falls back to #000000", () => expect(rgbStringToHex("nope")).toBe("#000000"));
it("malformed hex falls back to rgb(0, 0, 0, 1)", () => expect(hexToRgbString("zz")).toBe("rgb(0, 0, 0, 1)"));
it("uppercase hex accepted", () => expect(hexToRgbString("#FF8A2B")).toBe("rgb(255, 138, 43, 1)"));
```

- [ ] **Step 2: Implement** `colorFormat.ts`:

```ts
// history_page.probe_config colors are stored as the nonstandard
// "rgb(r, g, b, 1)" string form (see common/defaults.py COLOR_LIST).
// These helpers round-trip that format exactly; alpha is always 1.
export function rgbStringToHex(rgb: string): string {
  const m = rgb.match(/rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/);
  if (!m) return "#000000";
  const to2 = (s: string) => Math.min(255, Number(s)).toString(16).padStart(2, "0");
  return `#${to2(m[1])}${to2(m[2])}${to2(m[3])}`;
}

export function hexToRgbString(hex: string): string {
  const m = hex.match(/^#?([0-9a-fA-F]{6})$/);
  if (!m) return "rgb(0, 0, 0, 1)";
  const v = Number.parseInt(m[1], 16);
  return `rgb(${(v >> 16) & 255}, ${(v >> 8) & 255}, ${v & 255}, 1)`;
}
```

Run → PASS. Coverage for this file must be 100% (`bun run test:coverage`).

- [ ] **Step 3: ColorField + RTL test.** Component (mirror the label/markup classes of `Toggle.tsx`/`NumberField.tsx` — read one first):

```tsx
import { hexToRgbString, rgbStringToHex } from "../../../helpers/settings/colorFormat";

export function ColorField({
  label, value, onChange,
}: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <label className="pf-field">
      <span className="pf-field-label">{label}</span>
      <input type="color" value={rgbStringToHex(value)} onChange={(e) => onChange(hexToRgbString(e.target.value))} />
    </label>
  );
}
```

Test: renders the label; input's value is the hex of the stored string; `fireEvent.change(input, {target: {value: "#ff8a2b"}})` → onChange called with `"rgb(255, 138, 43, 1)"`.

- [ ] **Step 4: Full gate (+coverage) + commit**

```bash
bun run typecheck && bun run lint && bun run test && bun run test:coverage && bun run build
git add src/helpers/settings/colorFormat.ts src/helpers/settings/colorFormat.test.ts src/components/settings/fields/ColorField.tsx src/components/settings/fields/ColorField.test.tsx
git commit -m "feat(web-react): ColorField primitive + colorFormat helpers (stored rgb-string round-trip)"
```

---

### Task 8: HistoryTab — Chart Colors section

**Files:**
- Modify: `web-react/src/components/settings/tabs/HistoryTab.tsx`, `.../HistoryTab.test.tsx`

**Interfaces:**
- Consumes: `ColorField` (Task 7), existing `Toggle`/`Section`, `useSaveSettings` spy pattern.
- Produces: HistoryTab's save delta additionally rebuilds `history_page.probe_config` (whole subtree), still flags `[]`.

- [ ] **Step 1: Failing RTL tests** — extend `HistoryTab.test.tsx` with a settings fixture whose `history_page.probe_config` is:

```ts
const PROBE_CONFIG = {
  Grill: { name: "Grill", type: "Primary", enabled: true,
    line_color: "rgb(0, 64, 255, 1)", bg_color: "rgb(0, 64, 255, 1)",
    line_color_setpoint: "rgb(0, 64, 255, 1)", bg_color_setpoint: "rgb(0, 64, 255, 1)",
    line_color_target: "rgb(0, 128, 255, 1)", bg_color_target: "rgb(0, 128, 255, 1)",
    dash_setpoint: true, fill: false },
  Probe1: { name: "Probe-1", type: "Food", enabled: true,
    line_color: "rgb(0, 200, 64, 1)", bg_color: "rgb(0, 200, 64, 1)",
    line_color_target: "rgb(0, 232, 126, 1)", bg_color_target: "rgb(0, 232, 126, 1)",
    dash_setpoint: true, fill: false },
};
```

Cases: (a) renders one card per label showing `name` and type chip; (b) Grill shows setpoint ColorFields, Probe-1 does NOT (presence-driven — assert by accessible label count); (c) change Grill's Line color via the color input → Save → spy delta contains `history_page.probe_config.Grill.line_color === "rgb(<new>, 1)"` AND the untouched Probe1 subtree, flags exactly `[]`; (d) toggling Fill lands in the delta; (e) empty `probe_config: {}` renders the "No probes configured" hint, no crash.

- [ ] **Step 2: Implement the section** — new `Section title="Chart Colors"` in HistoryTab: iterate `Object.entries(v.probeConfig)`; per entry render header (name, type chip, enabled Toggle), ColorFields for exactly the color keys PRESENT on that entry (order: line, bg, then setpoint pair if present, then target pair), and Dash-setpoint/Fill Toggles. State: fold `probe_config` into the tab's existing render-phase-synced local state (`readHistory` gains `probeConfig: structuredClone(settings.history_page?.probe_config ?? {})`); edits update the clone; Save rebuilds `history_page.probe_config` into the existing delta (keep the current scalar fields exactly as they are) with flags `[]` unchanged.

- [ ] **Step 3: Run tests — PASS**; `bun run test:coverage` — HistoryTab ≥75 still (it will rise).

- [ ] **Step 4: Full gate + commit**

```bash
bun run typecheck && bun run lint && bun run test && bun run test:coverage && bun run build
git add src/components/settings/tabs/HistoryTab.tsx src/components/settings/tabs/HistoryTab.test.tsx
git commit -m "feat(web-react): HistoryTab chart-colors section (per-probe colors/flags, bare write)"
```

---

### Task 9: RangeProfileTable widget

**Files:**
- Create: `web-react/src/components/settings/RangeProfileTable.tsx`, `.../RangeProfileTable.test.tsx`

**Interfaces:**
- Produces (Task 10 depends on these exact names):

```ts
export interface RangeProfileColumn {
  key: string; label: string; suffix?: string; min?: number; max?: number;
}
export function RangeProfileTable(props: {
  boundaries: number[];
  profiles: Record<string, number>[];
  columns: RangeProfileColumn[];
  rangeHeader: string;
  unit: string; // display-only, e.g. "°F"
  onChange: (boundaries: number[], profiles: Record<string, number>[]) => void;
}): JSX.Element;
```

- [ ] **Step 1: Failing RTL tests** with `boundaries=[60,80,90]`, 4 profiles `{startuptime,...}`-shaped, columns for all three keys. Cases:
  - Range labels derive: `< 60°`, `60 – 79°`, `80 – 89°`, `≥ 90°` (integer math: upper label = next boundary − 1).
  - Editing a cell number → `onChange` fires with boundaries unchanged and ONLY that profile row's key changed.
  - Editing a boundary input (row 0's range cell) → `onChange` with `boundaries[0]` changed, profiles untouched.
  - "+ Add" → `onChange` with boundaries `[...prev, last+10]` and profiles `[...prev, {...lastRow}]` (invariant holds: N+1).
  - Row remove ✕ on row i → boundaries lose index `min(i, N-1)`, profiles lose row i; invariant holds.
  - With 2 rows / 1 boundary, remove buttons are disabled.
  - Column `min/max` clamp: entering 999 in a column with `max: 100` emits 100.

- [ ] **Step 2: Implement.** Controlled table (no internal array state); `<table>` with a header row (`rangeHeader` + column labels), one `<tr>` per profile: range cell (text for the bounds + a NumberField-style `<input type="number">` for the editable boundary where the row has one — rows 0..N-1 edit boundary i; last row's range cell is text only), one numeric input per column (clamped via the column's min/max on change), and a remove button (disabled at the 2-row floor). Below: an add button. Every mutation calls `onChange` with fresh arrays — never mutate props. Reuse the `.pf-` styling conventions from `settings.css`; add the table styles there if none fit (a `.pf-rpt` block).

- [ ] **Step 3: Run tests — PASS**; coverage for the new component ≥75 (aim higher — the widget is the phase's riskiest logic).

- [ ] **Step 4: Full gate + commit**

```bash
bun run typecheck && bun run lint && bun run test && bun run test:coverage && bun run build
git add src/components/settings/RangeProfileTable.tsx src/components/settings/RangeProfileTable.test.tsx src/components/settings/settings.css
git commit -m "feat(web-react): generic RangeProfileTable (boundaries+1 invariant by construction)"
```

---

### Task 10: SmartStart + PWM table integration

**Files:**
- Modify: `web-react/src/components/settings/tabs/StartupTab.tsx` + `.test.tsx`, `web-react/src/components/settings/tabs/PwmTab.tsx` + `.test.tsx`

**Interfaces:**
- Consumes: `RangeProfileTable` + `RangeProfileColumn` (Task 9 signatures verbatim).
- Produces: StartupTab save delta additionally replaces `startup.smartstart.temp_range_list` + `startup.smartstart.profiles` wholesale; PwmTab likewise `pwm.temp_range_list` + `pwm.profiles`. Flags: each tab keeps its existing flag behavior for scalars, but per Flask parity the TABLE-only saves are bare — since both tabs save everything in one delta, the flag sets stay as-shipped (`["settings_update"]` for both tabs) — **plan ruling: one Save per tab, existing flags kept; the arrays ride in the same delta.** (Flask's bare-write table endpoints are separate forms; our single-Save tab model already sends `settings_update` for these tabs' scalar fields, which is a strict superset — the control loop re-reads settings, harmless for table-only edits. The reviewer should verify this reasoning, not silently narrow it.)

- [ ] **Step 1: StartupTab failing tests** — fixture gets `startup.smartstart.temp_range_list: [60, 80, 90]` and the 4 default profiles (`{startuptime: 360, augerontime: 15, p_mode: 0}` etc. from `common/defaults.py:207-215`). Cases: table renders 4 rows with derived labels; edit row 2's `startuptime` to 300 → Save → delta contains the FULL `startup.smartstart.profiles` array with row 2 changed and `temp_range_list` unchanged, flags `["settings_update"]`; add-range then Save → both arrays grew by one.

- [ ] **Step 2: Implement in StartupTab** — inside the existing Smart Start section, under the enabled/exit_temp fields: `<RangeProfileTable boundaries={v.smartstartTemps} profiles={v.smartstartProfiles} columns={SMARTSTART_COLUMNS} rangeHeader="Range" unit={units} onChange={...}/>` with

```ts
const SMARTSTART_COLUMNS: RangeProfileColumn[] = [
  { key: "startuptime", label: "Startup time", suffix: "s", min: 30, max: 1200 },
  { key: "augerontime", label: "Auger on", suffix: "s", min: 1, max: 60 },
  { key: "p_mode", label: "P-Mode", min: 0, max: 9 },
];
```

state via the tab's existing render-phase-synced `v` (arrays cloned in `readStartup`), Save extends the existing delta with both paths set wholesale.

- [ ] **Step 3: PwmTab failing tests + implement** — fixture `pwm.temp_range_list: [3, 7, 10, 15]`, 5 profiles `{duty_cycle: 20|35|50|75|100}`. Column:

```ts
const DUTY_COLUMNS: RangeProfileColumn[] = [
  { key: "duty_cycle", label: "Duty cycle", suffix: "%", min: v.min_duty_cycle, max: v.max_duty_cycle },
];
```

(min/max come from the tab's CURRENT local values so edits clamp against what's on screen). `rangeHeader="ΔT range"`. Tests: 5 derived labels (`0 – 2°` style per the derivation — first label with no lower boundary is `< 3°`; assert what the widget actually derives for the shared algorithm), duty edit → Save → full arrays in delta + `["settings_update"]`, clamp at max_duty_cycle.

- [ ] **Step 4: Full gate (+coverage ≥75 on both tabs) + commit**

```bash
bun run typecheck && bun run lint && bun run test && bun run test:coverage && bun run build
git add src/components/settings/tabs/StartupTab.tsx src/components/settings/tabs/StartupTab.test.tsx src/components/settings/tabs/PwmTab.tsx src/components/settings/tabs/PwmTab.test.tsx
git commit -m "feat(web-react): SmartStart + PWM duty-cycle range-profile tables"
```

---

### Task 11: Controller tab

**Files:**
- Modify: `web-react/src/helpers/settings/settingsApi.ts` (+ test), `web-react/src/helpers/settings/settingsRoutes.ts` (+ test), `web-react/src/components/settings/SettingsShell.tsx`, `web-react/src/components/App.tsx`
- Create: `web-react/src/components/settings/tabs/ControllerTab.tsx`, `.../ControllerTab.test.tsx`

**Interfaces:**
- Consumes: Task 6's endpoint.
- Produces: `getControllerMetadata(baseUrl: string): Promise<ControllerMetadata | null>` (fail-open null) with

```ts
export interface ControllerOption {
  option_name: string; option_friendly_name: string; option_description: string;
  option_type: "float" | "int" | "bool" | string;
  option_default: number | boolean | null;
  option_min: number | null; option_max: number | null;
}
export interface ControllerMetadata {
  metadata: Record<string, { friendly_name: string; description: string; config: ControllerOption[] }>;
}
```

loader context becomes `{settings, mode, controllerMeta: ControllerMetadata | null}`; nav gains `{to: "controller", label: "Controller"}` after Work Mode; route `controller` → `<ControllerTab/>`.

- [ ] **Step 1: settingsApi + loader** — add `getControllerMetadata` (GET `/api/controller_metadata`, parse JSON, ANY throw/non-ok → `null`); extend `settingsLoader`'s `Promise.all` with it; widen the loader's return + `SettingsShell`'s outlet context type. Extend the Task-2 tests: metadata resolves through the loader; fetch rejection → `controllerMeta: null` while settings/mode still resolve.

- [ ] **Step 2: Failing ControllerTab tests** — metadata fixture: `pid` with the REAL 4 options (PB float default 60.0, Td 45.0, Ti 180.0, center 0.5 — friendly names "Proportional Band(PB)", "Derivative Time (Td)", "Integral Time (Ti)", "Center Ratio"), `fuzzy` with `config: []`, plus one `bool`-typed synthetic option on pid to cover Toggle rendering (or use a real bool from another controller — check `controllers.json` and prefer real). Settings fixture: `controller: {selected: "pid", config: {pid: {PB: 55}}}`. Cases:
  - Renders the Select with the selected controller's friendly name; PB shows 55 (config value), Td/Ti/center show their defaults (fallback path).
  - Switch Select to `fuzzy` → fields disappear, "no configuration options" hint renders; nothing saved yet.
  - Back to pid, edit PB to 62.5, Save → spy delta EXACTLY `{controller: {selected: "pid", config: {pid: {PB: 62.5, Td: 45, Ti: 180, center: 0.5}}}}` (config rebuilt whole, floats coerced) with flags `["controller_update"]`.
  - `controllerMeta: null` in context → error state ("Controller metadata unavailable"), no Select, no crash.

- [ ] **Step 3: Implement ControllerTab** — house tab skeleton (`useOutletContext`, `useSaveSettings`, render-phase `prev` sync keyed on BOTH `settings` and selection): local state `{selected, values}`; switching selection re-derives `values` from `settings.controller.config[selected]` + defaults (render-phase, not effect); fields per option: `float`/`int` → NumberField (min/max when non-null; on save `int` → `Math.round(Number(v))`, `float` → `Number(v)`), `bool` → Toggle; unknown type → skip. Save builds the delta above. Description text under the Select from the metadata.

- [ ] **Step 4: Wire nav + route** — `SettingsShell.tsx` SETTINGS_TABS: insert `{ to: "controller", label: "Controller" }` directly after Work Mode; `App.tsx` adds `{ path: "controller", element: <ControllerTab /> }` beside the other settings children.

- [ ] **Step 5: Run tests — PASS**; coverage: ControllerTab ≥75, settingsApi stays ≥75.

- [ ] **Step 6: Full gate + commit**

```bash
bun run typecheck && bun run lint && bun run test && bun run test:coverage && bun run build
git add src/helpers/settings src/components/settings src/components/App.tsx
git commit -m "feat(web-react): metadata-driven Controller tab (9th tab, controller_update flag)"
```

---

### Task 12: E2e round-trips + final verification

**Files:**
- Modify: `web-react/tests/e2e/settings.spec.ts`

**Interfaces:**
- Consumes: everything; the live backend (restart per Global Constraints — REQUIRED here, the running worker predates Task 6's route).

- [ ] **Step 1: Add three e2e tests** (follow the file's existing helpers/read-back style):
  - **Chart color round-trip**: open `/settings/history`, change the first color input's value, Save, `page.reload()`, assert the input kept the new value.
  - **SmartStart round-trip**: open `/settings/startup`, edit the first profile row's startup-time input to a distinct value (e.g. 361), Save, reload, assert 361 persisted; restore the original value afterward via the API or a second save (leave the backend as found).
  - **Controller round-trip**: open `/settings/controller`, assert the Select shows the backend's current controller; edit PB to a distinct value, Save, reload, assert it persisted; `GET /api/settings` cross-check `controller.config.<selected>.PB` equals it; restore original.

- [ ] **Step 2: Restart gunicorn** (Global Constraints recipe — must load `/api/controller_metadata`), verify 201, then `bun run test:e2e` → ALL specs green (the pre-existing 6 + the 3 new; cold-start-flake rerun rule applies).

- [ ] **Step 3: Full final gate** — `bun run typecheck && bun run lint && bun run test && bun run test:coverage && bun run build` all green; paste final test count + the coverage summary line.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/settings.spec.ts
git commit -m "test(web-react): e2e round-trips for chart colors, smartstart table, controller tab"
```

---

## Verification summary (maps to spec)

- Coverage: Task 1 honest baseline → Tasks 2-4 backfill (exit = per-file table) → Task 5 permanent per-file 75% threshold with negative proof. New helpers 100% (Task 7 colorFormat).
- Features: Task 6 endpoint (+pytest) → 7 ColorField → 8 chart colors → 9 table widget → 10 integrations → 11 controller tab → 12 e2e ×3.
- Every task: full gate + coverage run; flags Flask-verified; structure/layering tests inherently green (files placed per tree rules).
