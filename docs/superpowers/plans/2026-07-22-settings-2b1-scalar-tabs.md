# Settings 2b-1 (Scalar Tabs + RTL Harness) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the five scalar settings tabs (Safety, Pellet levels, Work Mode basics, Startup/Shutdown, History basics) over the existing `/api/settings_update` (no backend change), and adopt React Testing Library — convert the two component-proxy tests to rendered-component tests, add RTL tests for every component, keep pure functions pure.

**Architecture:** Each tab reads loaded settings via `useOutletContext`, edits local state (render-phase sync), and `save(delta, flags)` through `useSaveSettings` → `POST /api/settings_update`; React applies the same coercions the Flask `_settings_*` handlers did. RTL runs under jsdom scoped to `*.test.tsx`; pure tests stay node-env `*.test.ts`.

**Tech Stack:** React 19 (+ React Compiler), react-router (data router), Vite 8/rolldown, Vitest 4 + @testing-library/react + jsdom, ESLint, Playwright, Flask/pytest. Package manager **bun**.

## Global Constraints

- Package manager **bun**, never npm: `bun add`, `bun run …`. Commit `bun.lock`.
- TypeScript strict, `noUnusedLocals`/`noUnusedParameters`. Typecheck `bunx tsc -b`.
- **`bun run lint` clean** (react-hooks + React Compiler rules); NO new `eslint-disable` for `set-state-in-effect`. Sync loader-data with the **render-phase `prevSettings` pattern**, never a `useEffect`.
- **No backend changes.** All settings writes go through the existing `POST /api/settings_update` with per-section flags (whitelist already permits `settings_update`/`controller_update`/`distance_update`/`probe_profile_update`).
- **RTL convention:** component tests are `*.test.tsx` with a `// @vitest-environment jsdom` docblock as their FIRST line; pure tests remain `*.test.ts` (node env). `@testing-library/jest-dom` matchers are enabled via a setup file.
- Console output in tests must be **pristine** — no React/router warnings (incl. HydrateFallback).
- Python/e2e: prototype backend on `:5000`; **restart gunicorn before e2e** (no `--reload`): `kill <gunicorn master pid>` then `uv run gunicorn -k gthread --threads 25 -b 0.0.0.0:5000 -w 1 app:app`.
- Frontend work under `web-react/`; run bun commands there.
- **Every subagent (implementer AND reviewer) records its outcome to the Hindsight `claude_code` bank when done** (standing project rule).

---

## File Structure

- `web-react/src/test-setup.ts` — jest-dom matchers (create).
- `web-react/src/test-utils.tsx` — `renderTab` / router-context render helpers (create).
- `web-react/vite.config.ts` — `test.setupFiles` (modify).
- `web-react/package.json` — RTL dev-deps (modify).
- `web-react/src/settings/settingsApi.ts` — add `getMode` (modify).
- `web-react/src/settings/settingsRoutes.ts` — loader returns `{ settings, mode }` (modify).
- `web-react/src/settings/SettingsShell.tsx` — `{settings, mode}` outlet context + 5 nav entries (modify).
- `web-react/src/App.tsx` — 5 routes + `HydrateFallback` (modify).
- `web-react/src/settings/tabs/{SafetyTab,PelletsTab,WorkModeTab,StartupShutdownTab,HistoryTab}.tsx` (create) + `.test.tsx` each.
- `web-react/src/settings/tabs/UnitsTab.tsx` — `CommandResult.ok` check (modify).
- Converted: `web-react/src/dashboard/Dashboard.test.tsx`, `ControlButtons.test.tsx` (create; delete the two old `.test.ts`).
- New component RTL tests: `web-react/src/dashboard/{GrillGauge,ProbeCard,SystemStatus,HopperGauge,SetpointEntry,ConfirmAction,Banners}.test.tsx`; `web-react/src/settings/fields/*.test.tsx`; `web-react/src/settings/SettingsShell.test.tsx`; each tab's `.test.tsx`.
- `web-react/tests/e2e/settings.spec.ts` — add a scalar-tab round-trip (modify).

---

## Task 1: RTL harness

**Files:** create `web-react/src/test-setup.ts`, `web-react/src/test-utils.tsx`; modify `web-react/vite.config.ts`, `web-react/package.json`.

**Interfaces:**
- Produces: a working jsdom+RTL setup; `renderWithRouterOutlet(ui, ctx)` and `renderTab(TabComponent, { settings, mode, save })` helpers for rendering components that use react-router outlet context.

- [ ] **Step 1: Install deps**

Run (in `web-react/`): `bun add -d @testing-library/react @testing-library/jest-dom @testing-library/user-event jsdom`

- [ ] **Step 2: `src/test-setup.ts`**

```ts
import "@testing-library/jest-dom/vitest";
```

- [ ] **Step 3: wire setupFiles in `vite.config.ts`**

In the `test` block add `setupFiles: ["./src/test-setup.ts"]` (keep the existing `exclude`). Do NOT set a global `environment` — component tests opt into jsdom per-file.

- [ ] **Step 4: `src/test-utils.tsx` — render helpers**

```tsx
import type { ReactElement, ReactNode } from "react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { render } from "@testing-library/react";

// Render a component that reads `useOutletContext()` — wrap it in a memory
// router whose parent route provides `context` and renders <Outlet/>.
export function renderWithOutletContext(ui: ReactElement, context: unknown) {
  const router = createMemoryRouter(
    [{ path: "/", element: ui, loader: () => null, handle: {} }],
    { initialEntries: ["/"] },
  );
  // The simplest reliable path: render the element inside a route that itself
  // provides Outlet context. Implement via a tiny wrapper route.
  return renderRoute(ui, context);
}

import { Outlet, useOutletContext } from "react-router";
function ContextProvider({ context, children }: { context: unknown; children?: ReactNode }) {
  return <Outlet context={context} />;
}
export function renderRoute(ui: ReactElement, context: unknown) {
  const router = createMemoryRouter(
    [{ path: "/", element: <ContextProvider context={context} />, children: [{ index: true, element: ui }] }],
    { initialEntries: ["/"] },
  );
  return render(<RouterProvider router={router} />);
}
export { useOutletContext };
```

(If the double-helper is awkward at implementation time, collapse to the single `renderRoute` — the requirement is: a helper that renders a settings tab so `useOutletContext<{settings, mode}>` resolves. Keep whichever compiles cleanly and is lint-clean.)

- [ ] **Step 5: Smoke test proves the harness works**

Create `web-react/src/test-utils.smoke.test.tsx` (delete after, or keep as a trivial guard):

```tsx
// @vitest-environment jsdom
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

describe("rtl harness", () => {
  it("renders and jest-dom matchers work", () => {
    render(<button>Hi</button>);
    expect(screen.getByRole("button", { name: "Hi" })).toBeInTheDocument();
  });
});
```

- [ ] **Step 6: Run + verify**

Run: `bun run test && bunx tsc -b && bun run lint`
Expected: existing pure tests still pass (node env) + the smoke test passes (jsdom), tsc 0, lint 0.

- [ ] **Step 7: Commit**

```bash
cd web-react && git add src/test-setup.ts src/test-utils.tsx src/test-utils.smoke.test.tsx vite.config.ts package.json bun.lock
git commit -m "test(web-react): RTL + jsdom harness (scoped to *.test.tsx)"
```

---

## Task 2: Cross-cutting — loader mode, SettingsShell nav, HydrateFallback, UnitsTab ok-check

**Files:** modify `settingsApi.ts`, `settingsRoutes.ts`, `SettingsShell.tsx`, `App.tsx`, `settings/tabs/UnitsTab.tsx`; create `SettingsShell.test.tsx`.

**Interfaces:**
- Consumes: `getSettings` (2a), RTL harness (Task 1).
- Produces: `getMode(baseUrl): Promise<string>`; `settingsLoader(): Promise<{ settings: Settings; mode: string }>`; tabs read `useOutletContext<{ settings: Settings; mode: string }>()`; a `HydrateFallback` route element; `UnitsTab` gates on `CommandResult.ok`.

- [ ] **Step 1: `getMode` in `settingsApi.ts`**

```ts
export async function getMode(baseUrl: string): Promise<string> {
  try {
    const res = await fetch(buildSettingsUrl(baseUrl, "get/mode"));
    if (!res.ok) return "";
    const body = (await res.json()) as { data?: { mode?: string } };
    return body.data?.mode ?? "";
  } catch {
    return ""; // mode-gating fails open to "unknown"; History tab treats non-"Stop" as gated
  }
}
```
(`GET /api/get/mode` → `{data:{mode:"Stop"|"Hold"|…}}`.)

- [ ] **Step 2: loader returns `{ settings, mode }`**

`settingsRoutes.ts`:
```ts
import { getMode, getSettings, type Settings } from "./settingsApi";
const BASE_URL = import.meta.env.VITE_PIFIRE_URL || "";
export async function settingsLoader(): Promise<{ settings: Settings; mode: string }> {
  const [settings, mode] = await Promise.all([getSettings(BASE_URL), getMode(BASE_URL)]);
  return { settings, mode };
}
```

- [ ] **Step 3: SettingsShell provides `{settings, mode}` + adds nav entries**

In `SettingsShell.tsx`: `const { settings, mode } = useLoaderData() as { settings: Settings; mode: string };` and `<Outlet context={{ settings, mode }} />`. Extend `SETTINGS_TABS`:
```ts
const SETTINGS_TABS = [
  { path: "general", label: "General" },
  { path: "work-mode", label: "Work Mode" },
  { path: "pwm", label: "PWM Fan" },
  { path: "startup", label: "Startup / Shutdown" },
  { path: "safety", label: "Safety" },
  { path: "pellets", label: "Pellet Levels" },
  { path: "history", label: "History" },
  { path: "units", label: "Units" },
];
```

- [ ] **Step 4: routes + HydrateFallback in `App.tsx`**

Add child routes `work-mode`/`startup`/`safety`/`pellets`/`history` (elements = the new tabs, imported). Add a `HydrateFallbackElement` to the router options OR a `HydrateFallback` element on the root — the minimal fix is passing `hydrateFallbackElement` when creating the router, or adding `HydrateFallback` to the settings route. Use:
```tsx
const router = createBrowserRouter([...], /* nothing needed if… */);
```
Concretely: add a tiny `export function HydrateFallback() { return <div className="pf-fit" />; }` and set it on the `/settings` route as `HydrateFallback: HydrateFallback` (route-level) so the warning is gone. Verify the warning no longer prints during the Task-1 smoke/e2e.

- [ ] **Step 5: UnitsTab ok-check**

In `UnitsTab.confirmChange`, await the command result and only update local state / revalidate on success; on failure set an error message shown in the tab:
```tsx
const r = await createCommand(BASE_URL).setUnits(next);
if (r.ok) { setUnits(next); revalidator.revalidate(); }
else { setError(r.message || "Failed to change units"); }
```
(Add a `const [error, setError] = useState<string | null>(null)` and render it.)

- [ ] **Step 6: SettingsShell RTL test** (`SettingsShell.test.tsx`, jsdom docblock): render the shell (via a memory router providing loader data `{settings:{...}, mode:"Stop"}`), assert all 8 nav links render and the back-to-dashboard control exists.

- [ ] **Step 7: gate** — `bunx tsc -b && bun run lint && bun run test && bun run build` (note: the 5 new tab routes reference tab components created in later tasks — create them as `return null` stubs now so imports resolve; Tasks 6–10 flesh them out; note stubs in report).

- [ ] **Step 8: Commit**

```bash
cd web-react && git add src/settings/settingsApi.ts src/settings/settingsRoutes.ts src/settings/SettingsShell.tsx src/settings/SettingsShell.test.tsx src/App.tsx src/settings/tabs/UnitsTab.tsx src/settings/tabs
git commit -m "feat(web-react): loader mode + nav + HydrateFallback + UnitsTab ok-check"
```

---

## Task 3: Convert dashboard view tests to RTL (deriveView → components)

**Files:** create `web-react/src/dashboard/{Dashboard,GrillGauge,ProbeCard,SystemStatus,HopperGauge}.test.tsx`; delete `web-react/src/dashboard/deriveView.test.ts`. Keep `deriveView.ts` (production).

**Interfaces:** consumes RTL harness; `FIXTURE_DASH`.

- [ ] **Step 1: Widget RTL tests replacing the deriveView cases**

Render each widget with crafted props and assert DOM. The old `deriveView.test.ts` cases map to rendered assertions — e.g. `SystemStatus.test.tsx`:
```tsx
// @vitest-environment jsdom
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { SystemStatus } from "./SystemStatus";
import { deriveView } from "./deriveView";
import { FIXTURE_DASH } from "../fixture";

describe("SystemStatus", () => {
  it("shows RUNNING/FEEDING/HOT when outputs are on", () => {
    const v = deriveView({ ...FIXTURE_DASH, outputs: { fan: true, auger: true, igniter: true } });
    render(<SystemStatus fan={v.fan} auger={v.auger} igniter={v.igniter} animate={false} />);
    expect(screen.getByText("RUNNING")).toBeInTheDocument();
    expect(screen.getByText("FEEDING")).toBeInTheDocument();
    expect(screen.getByText("HOT")).toBeInTheDocument();
  });
  it("shows IDLE/IDLE/IDLE when outputs are off", () => {
    const v = deriveView({ ...FIXTURE_DASH, outputs: { fan: false, auger: false, igniter: false } });
    render(<SystemStatus fan={v.fan} auger={v.auger} igniter={v.igniter} animate={false} />);
    expect(screen.getAllByText("IDLE")).toHaveLength(3);
  });
});
```
Do the same for: `HopperGauge` (thresholds → `%` text + `LEVEL OK`/`RUNNING LOW`/`REFILL PELLETS`), `ProbeCard` (temp/target/AMBIENT/bar), `GrillGauge` (mode label, SET text, temp). Cover the same behaviors the deleted `deriveView.test.ts` asserted, but through the rendered DOM.

- [ ] **Step 2: `Dashboard.test.tsx`** — render `<Dashboard>` with `FIXTURE_DASH`-derived props (accent/animate/setters as `vi.fn()`, a `command` stub) and assert the header (grill name, LIVE/DEMO label), mode badge, cook-time element, and that the probe column renders when `foodProbes` present. (`Dashboard` needs no router — it takes props; but it uses `useClock`/`useFitScale` which are fine under jsdom.)

- [ ] **Step 3: delete the old pure test** — `git rm web-react/src/dashboard/deriveView.test.ts`.

- [ ] **Step 4: gate** — `bunx tsc -b && bun run lint && bun run test` (the converted DOM tests replace the removed pure cases; total test count changes — that's expected).

- [ ] **Step 5: Commit**

```bash
cd web-react && git add src/dashboard/*.test.tsx && git rm src/dashboard/deriveView.test.ts
git commit -m "test(web-react): convert deriveView cases to RTL widget tests"
```

---

## Task 4: Convert control tests to RTL + modals/banners RTL

**Files:** create `web-react/src/dashboard/{ControlButtons,SetpointEntry,ConfirmAction,Banners}.test.tsx`; delete `web-react/src/dashboard/controlButtons.test.ts`. Keep `controlButtons.ts`.

- [ ] **Step 1: `ControlButtons.test.tsx`** — render `<ControlButtons dash={...} command={stub} disabled={false} />` per mode; assert the rendered button labels for Stopped/Monitor/Cooking; click **Smoke** → assert `command.setMode` called with `"smoke"`; click **Hold** → assert the setpoint modal appears (`getByText("Set Hold Temperature")`); click **Stop** → assert the confirm modal appears. Use `@testing-library/user-event`.

- [ ] **Step 2: `SetpointEntry.test.tsx`** — render with `open`, assert initial clamped value; click `+`/`−` steps clamp; submit calls `onSubmit` with the value; scrim click calls `onCancel`.

- [ ] **Step 3: `ConfirmAction.test.tsx`** — open renders title; Confirm→`onConfirm`, Cancel/scrim→`onCancel`; `open=false` renders nothing.

- [ ] **Step 4: `Banners.test.tsx`** — renders one banner per error/warning; `criticalError` styles the error banner critical; empty → nothing.

- [ ] **Step 5: delete pure test** — `git rm web-react/src/dashboard/controlButtons.test.ts`.

- [ ] **Step 6: gate + commit**

```bash
cd web-react && git add src/dashboard/*.test.tsx && git rm src/dashboard/controlButtons.test.ts
git commit -m "test(web-react): convert control-button cases to RTL + modal/banner tests"
```

---

## Task 5: Field-primitive RTL tests

**Files:** create `web-react/src/settings/fields/{Toggle,Select,NumberField,TextField,Section}.test.tsx`.

- [ ] **Step 1: One test file per primitive** (jsdom docblock). Assert render + interaction:
  - `NumberField`: shows `value` and `suffix`; typing calls `onChange` with the parsed **number**.
  - `Toggle`: `aria-pressed` reflects `checked`; click calls `onChange(!checked)`.
  - `Select`: renders options; selecting calls `onChange(value)`.
  - `TextField`: shows value; typing calls `onChange(string)`.
  - `Section`: renders `title` and children.

Representative (`NumberField.test.tsx`):
```tsx
// @vitest-environment jsdom
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { NumberField } from "./NumberField";

describe("NumberField", () => {
  it("renders value + suffix and emits parsed numbers", () => {
    const onChange = vi.fn();
    render(<NumberField label="Max Temp" value={550} onChange={onChange} suffix="°" />);
    const input = screen.getByRole("spinbutton");
    expect(input).toHaveValue(550);
    expect(screen.getByText("°")).toBeInTheDocument();
    fireEvent.change(input, { target: { value: "600" } });
    expect(onChange).toHaveBeenCalledWith(600);
  });
});
```

- [ ] **Step 2: gate + commit**

```bash
cd web-react && git add src/settings/fields/*.test.tsx
git commit -m "test(web-react): RTL tests for settings field primitives"
```

---

## Task 6: SafetyTab (bare write, no flag) — the tab template

**Files:** create `web-react/src/settings/tabs/SafetyTab.tsx` (replace stub) + `SafetyTab.test.tsx`. Route/nav already added in Task 2.

**Interfaces:** consumes `useOutletContext<{settings, mode}>`, `useSaveSettings`, `setPath`, primitives.

- [ ] **Step 1: Implement SafetyTab**

```tsx
import { useState } from "react";
import { useOutletContext } from "react-router";
import type { Settings } from "../settingsApi";
import { useSaveSettings } from "../useSaveSettings";
import { setPath } from "../delta";
import { Section } from "../fields/Section";
import { Toggle } from "../fields/Toggle";
import { NumberField } from "../fields/NumberField";

type Safety = {
  minstartuptemp: number; maxstartuptemp: number; maxtemp: number;
  reigniteretries: number; startup_check: boolean; allow_manual_changes: boolean; manual_override_time: number;
};
function readSafety(s: Settings): Safety {
  const x = s.safety ?? {};
  return {
    minstartuptemp: x.minstartuptemp ?? 75, maxstartuptemp: x.maxstartuptemp ?? 100, maxtemp: x.maxtemp ?? 550,
    reigniteretries: x.reigniteretries ?? 1, startup_check: !!x.startup_check,
    allow_manual_changes: !!x.allow_manual_changes, manual_override_time: x.manual_override_time ?? 30,
  };
}

export function SafetyTab() {
  const { settings } = useOutletContext<{ settings: Settings; mode: string }>();
  const { save, saving } = useSaveSettings();
  const [v, setV] = useState<Safety>(() => readSafety(settings));
  const [prev, setPrev] = useState(settings);
  const [saved, setSaved] = useState(false);
  if (settings !== prev) { setPrev(settings); setV(readSafety(settings)); }
  const set = <K extends keyof Safety>(k: K, val: Safety[K]) => setV((s) => ({ ...s, [k]: val }));

  const onSave = async () => {
    let d: object = {};
    for (const [k, val] of Object.entries(v)) d = setPath(d, `safety.${k}`, val);
    setSaved(await save(d, [])); // _settings_safety does a bare write — no control flag
  };

  return (
    <Section title="Safety">
      <NumberField label="Min Startup Temp" value={v.minstartuptemp} onChange={(n) => set("minstartuptemp", n)} suffix="°" />
      <NumberField label="Max Startup Temp" value={v.maxstartuptemp} onChange={(n) => set("maxstartuptemp", n)} suffix="°" />
      <NumberField label="Max Grill Temp" value={v.maxtemp} onChange={(n) => set("maxtemp", n)} suffix="°" />
      <NumberField label="Reignite Retries" value={v.reigniteretries} onChange={(n) => set("reigniteretries", n)} min={0} />
      <NumberField label="Manual Override Time" value={v.manual_override_time} onChange={(n) => set("manual_override_time", n)} min={0} suffix="s" />
      <Toggle label="Startup Check" checked={v.startup_check} onChange={(b) => set("startup_check", b)} />
      <Toggle label="Allow Manual Output Changes" checked={v.allow_manual_changes} onChange={(b) => set("allow_manual_changes", b)} />
      <div className="pf-settings-actions">
        <button className="pf-modal-btn accent" disabled={saving} onClick={onSave}>{saving ? "Saving…" : "Save"}</button>
        {saved && <span className="pf-settings-saved">Saved ✓</span>}
      </div>
    </Section>
  );
}
```

- [ ] **Step 2: `SafetyTab.test.tsx`** (jsdom) — render via `renderRoute(<SafetyTab/>, { settings: { safety: {maxtemp: 550, startup_check: true, ...} }, mode: "Stop" })` with a `useSaveSettings` save spy (mock the module), assert fields show loaded values, change Max Grill Temp to 600 + click Save, assert the save spy got `({ safety: { maxtemp: 600, … } }, [])`.

- [ ] **Step 3: gate + commit** — `bunx tsc -b && bun run lint && bun run test && bun run build`; commit `SafetyTab.tsx` + test.

---

## Task 7: PelletsTab (`settings_update` + conditional `distance_update`)

**Files:** create `PelletsTab.tsx` + `PelletsTab.test.tsx`.

- [ ] **Step 1: Implement** — fields: `pelletlevel.{warning_enabled(bool),warning_time,warning_level,empty,full}`, `globals.augerrate(float, step 0.1)`, `globals.prime_ignition(bool)`. Build the delta with `setPath` across both subtrees. **Flags:** start `["settings_update"]`; if `empty` OR `full` differs from the loaded value, also include `"distance_update"`. Same render-phase sync + Save/Saved pattern as SafetyTab.

- [ ] **Step 2: Test** — (a) change `warning_time` → save flags `["settings_update"]`; (b) change `empty` → save flags include `"distance_update"`. Assert the exact `(delta, flags)`.

- [ ] **Step 3: gate + commit.**

---

## Task 8: WorkModeTab (cycle_data + smoke_plus + keep_warm, `settings_update`)

**Files:** create `WorkModeTab.tsx` + `WorkModeTab.test.tsx`.

- [ ] **Step 1: Implement** — three `Section`s. `cycle_data`: HoldCycleTime, SmokeOnCycleTime, SmokeOffCycleTime, PMode (int), u_min/u_max (float, step 0.1), LidOpenDetectEnabled(bool), LidOpenThreshold, LidOpenPauseTime, FanPidEnabled(bool). `smoke_plus`: enabled(bool), min_temp, max_temp, on_time, off_time, duty_cycle(20–100), fan_ramp(bool). `keep_warm`: temp, s_plus(bool). Save delta across `cycle_data.*`/`smoke_plus.*`/`keep_warm.*` with flags `["settings_update"]`. (Controller config is 2b-2 — NOT here.)

- [ ] **Step 2: Test** — edit a cycle field + a smoke_plus toggle, save, assert delta touches both subtrees with `["settings_update"]`.

- [ ] **Step 3: gate + commit.**

---

## Task 9: StartupShutdownTab (clamps + select, `settings_update`)

**Files:** create `StartupShutdownTab.tsx` + `StartupShutdownTab.test.tsx`.

- [ ] **Step 1: Implement** — fields: `shutdown.{shutdown_duration, auto_power_off(bool)}`, `startup.{duration, startup_exit_temp, prime_on_startup, pwm_duty_cycle}`, `startup.smartstart.{enabled(bool), exit_temp}`, `startup.start_to_mode.{after_startup_mode(Select: Smoke/Hold/etc.), primary_setpoint, start_to_hold_prompt(bool)}`. **Coercions on save:** clamp `prime_on_startup` to `[0,200]` (else 0); clamp `pwm_duty_cycle` to `[settings.pwm.min_duty_cycle, settings.pwm.max_duty_cycle]`. Flags `["settings_update"]`. Options for `after_startup_mode` Select: `["Smoke","Hold"]` (match the current UI's options; verify against the wizard/settings template if more exist — default to Smoke/Hold).

- [ ] **Step 2: Test** — set `prime_on_startup` to 999 → saved delta clamps to 200; `after_startup_mode` select changes; flags `["settings_update"]`.

- [ ] **Step 3: gate + commit.**

---

## Task 10: HistoryTab (`autorefresh` string + `ext_data` mode gate, bare write)

**Files:** create `HistoryTab.tsx` + `HistoryTab.test.tsx`.

- [ ] **Step 1: Implement** — fields: `history_page.{minutes, datapoints, clearhistoryonstart(bool)}`, `history_page.autorefresh` (a Toggle whose on/off maps to the **string** `"on"`/`"off"`), and `globals.ext_data` (Toggle). **`ext_data` gate:** read `mode` from `useOutletContext`; the ext_data Toggle is **disabled unless `mode === "Stop"`**, with a hint ("Stop the grill to change extended-data logging"). Save delta: `history_page.{minutes,datapoints,clearhistoryonstart}` + `history_page.autorefresh` (string) + `globals.ext_data`; flags `[]` (bare write). (Chart colors are 2b-2.)

Give the Toggle a boolean view but persist the string: keep local `autorefresh: boolean`, and in the delta write `setPath(d, "history_page.autorefresh", autorefresh ? "on" : "off")`.

- [ ] **Step 2: Test** — (a) with `mode:"Stop"` the ext_data toggle is enabled and saving includes `globals.ext_data`; (b) with `mode:"Hold"` the ext_data toggle is `disabled`; (c) autorefresh toggle persists `"on"`/`"off"` string in the delta; flags `[]`.

- [ ] **Step 3: gate + commit.**

---

## Task 11: Settings scalar-tab e2e + full gate

**Files:** modify `web-react/tests/e2e/settings.spec.ts`.

- [ ] **Step 1: Add a scalar round-trip test** — navigate `/settings/safety`, change **Max Grill Temp** to a unique value, Save → "Saved ✓", reload → value persists (loader re-fetch). (Safety is a bare write, so no control interaction needed.) Also click through to `/settings/history` and assert the ext_data toggle's disabled state matches the live mode.

- [ ] **Step 2: Restart gunicorn, run e2e**

Restart the web server so it has current code (no `--reload`):
```bash
# find the gunicorn master pid, kill it, relaunch:
ps -eo pid,args | grep "gunicorn.*app:app" | grep -v grep
kill <master-pid>
cd /home/dannyb/sources/PiFire && (uv run gunicorn -k gthread --threads 25 -b 0.0.0.0:5000 -w 1 app:app &)
```
Then (in `web-react/`): `bun run test:e2e tests/e2e/settings.spec.ts` → all pass. A genuine round-trip failure is a real finding (report BLOCKED with network evidence); a stale-worker 404 means gunicorn wasn't restarted.

- [ ] **Step 3: Full gate** — `bunx tsc -b && bun run lint && bun run test && bun run build` all green; test console pristine (no HydrateFallback warning).

- [ ] **Step 4: Commit.**

---

## Self-Review notes (already reconciled)

- **Spec coverage:** RTL harness (T1), loader mode + nav + HydrateFallback + UnitsTab ok-check (T2), the two RTL conversions (T3 deriveView→widgets, T4 controlButtons→ControlButtons + modals), component RTL coverage (T3/T4/T5 + each tab's test), the five scalar tabs with correct flags/coercions (T6–T10), e2e (T11). Colors/tables/controller form are Non-Goals (2b-2).
- **Type consistency:** `Settings`/`SettingsFlag`/`useSaveSettings`/`setPath` (2a) reused; loader shape `{settings, mode}` (T2) consumed by every tab's `useOutletContext<{settings, mode}>` (T6–T10) and SettingsShell (T2); `getMode` (T2) used by the loader. Tabs registered as routes in T2 (stubs) then implemented T6–T10.
- **House style:** every tab syncs loader data via the render-phase `prevSettings` pattern, no `useEffect`/`eslint-disable` — matches phase-2a.
- **Flags fidelity (verified vs Flask handlers):** Safety `[]`, History `[]`, Work Mode/Startup/Pellets `["settings_update"]`, Pellets `+ distance_update` on empty/full change.
