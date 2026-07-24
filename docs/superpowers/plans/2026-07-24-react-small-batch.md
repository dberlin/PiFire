# React Small Batch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the three remaining small React-migration gaps: auto-open the wizard on a fresh install, add a read-only Platform summary tab, and make display config options fall back to their manifest defaults.

**Architecture:** Three independent, self-contained changes. No shared new abstractions. Task 2 is the sole owner of `App.tsx`.

**Tech Stack:** React 19 + TypeScript (TS7/tsgo), rsbuild, `@rstest/core`, `@testing-library/react` 16, Biome. Package manager: **bun**.

## Design decisions (user-approved 2026-07-24)

- **D1 — PlatformTab is READ-ONLY.** The backlog called it "the 12th settings tab", but legacy Flask has **no** platform editor (it only *reads* `settings['platform']['dc_fan']` as a show/hide conditional). The wizard owns every one of these keys — `current`, `system_type`, `dc_fan`, `triggerlevel`, `standalone`, `real_hw` are all grillplatform manifest `settings_dependencies`, written on finish — and `dc_fan` is *server-derived* at install for `x86_numato`/`ft232h_relay`. An editable tab would create dual ownership and silently lose edits on the next wizard run. So: a read-only summary plus a "Configure in Setup Wizard" link.
- **D2 — No display-config repair migration.** The shape bug fixed in `c37b1036` was never released, so no real install is affected. Forward fix only; nothing touches existing settings.

## Global Constraints

- **bun, not npm** for all web-react install/run.
- **Testing API is `@rstest/core`** (`rs.fn`/`rs.mock`) — NOT vitest/`vi`. `.test.tsx` runs in jsdom.
- **`bun run lint` must be run and exit 0** in every task (Biome enforces format; `bunx biome check --write <file>` if needed). Two pre-existing `react-refresh` **warnings** (`App.tsx`, `WizardShell.tsx`) are acceptable; **errors** are not.
- **`bun run typecheck`** (TS7, `noUnusedLocals`) must stay clean.
- **Coverage ≥75% lines per changed file.**
- **House style — no `setState` in `useEffect`.** Settings tabs re-sync from the loader via render-phase adjustment (see `SafetyTab.tsx`), because the React Compiler lint rejects setState-in-effect. Do NOT add a suppression; restructure instead. (Task 1's effect performs a *fetch + navigate* side effect, not state derivation — that is allowed.)
- **jj boundary protocol:** the controller runs `jj new` before each dispatch; the implementer finalizes with a single `jj desc -m`.

## Parallelization

Dependency graph — **all three tasks are fully independent**:

```
T1 (DashboardRoute.tsx + test)                    ─┐
T2 (PlatformTab.tsx + test, App.tsx, SettingsShell.tsx + test) ─┼─ all concurrent
T3 (ConfigOptionField.tsx + test)                 ─┘
```

**Dispatch all three concurrently**, each in its own isolated jj workspace:

```bash
jj workspace add --name sb1 -r <plan-commit> ../PiFire-sb1   # T1
jj workspace add --name sb2 -r <plan-commit> ../PiFire-sb2   # T2
jj workspace add --name sb3 -r <plan-commit> ../PiFire-sb3   # T3
# then in each: cd ../PiFire-sbN/web-react && bun install   (node_modules is
# gitignored, ~200MB per workspace — pre-warm these before dispatching)
```

Isolated workspaces are **mandatory, not optional**: two agents sharing one
working copy cross-pollute each other's commits regardless of which files they
touch.

**File-ownership rules that make this safe:**
- `App.tsx` is touched by **T2 only** (the route registration). T1 does **not**
  edit it — the stale comment at `App.tsx:33-46` (which says the first_time_setup
  gate is "left as follow-up work") is updated by the **controller** during
  integration, once T1 has landed.
- No other file is touched by more than one task.

**Reviews parallelize too** (they are read-only) — dispatch all three task
reviewers concurrently once their implementations land.

**Integration (controller):**
1. `jj workspace forget sb1 sb2 sb3`
2. Linearize with **change ids** (stable across rebase):
   `jj rebase -s <T2-change> -d <T1-change>`, then `jj rebase -s <T3-change> -d <T2-change>`
3. Update the now-stale `App.tsx:33-46` comment (see Integration Step below).
4. **Verify the merged state** — no individual task tests the combination:
   `bun run typecheck && bun run lint && bun run test && bun run build`
5. `rm -rf ../PiFire-sb{1,2,3}`

---

### Task 1: `first_time_setup` auto-redirect to `/wizard`

**Files:**
- Modify: `web-react/src/components/DashboardRoute.tsx`
- Test: `web-react/src/components/DashboardRoute.test.tsx`

**Do NOT modify `App.tsx`** — it is Task 2's file; the controller updates its comment.

**Interfaces:**
- Consumes: `getSettings(baseUrl): Promise<Settings>` from `../helpers/settings/settingsApi`; `useNavigate` from `react-router`; existing `useDashData`, `useAppPrefs`, `ConnectionStatus`, `Dashboard`.
- Produces: no signature change — `DashboardRoute()` still takes no props.

**Why an effect rather than a route loader:** `/` deliberately has **no** route loader. React Router's data routers defer rendering until a loader resolves — even a synchronous one resolves on a microtask — so adding one would turn the dashboard's first paint into an async gap and break existing synchronous assertions. A brief dashboard flash before redirect is the accepted tradeoff, documented in the code.

- [ ] **Step 1: Read the existing test file to match its harness**

Run: `sed -n '1,40p' web-react/src/components/DashboardRoute.test.tsx`
Note how it mocks `useDashData` and renders the component; mirror that. You will additionally mock `../helpers/settings/settingsApi` and `react-router`'s `useNavigate`.

- [ ] **Step 2: Write the failing tests**

Add to `web-react/src/components/DashboardRoute.test.tsx`. Extend the file's existing mocks — an `rs.mock` factory replaces the whole module, so keep every stub the file already provides and ADD to it:

```tsx
const navigateMock = rs.fn();
const getSettingsMock = rs.fn();

rs.mock("react-router", () => ({
  useNavigate: () => navigateMock,
}));
rs.mock("../helpers/settings/settingsApi", () => ({
  getSettings: (...args: unknown[]) => getSettingsMock(...args),
}));
```

and these three tests:

```tsx
  it("navigates to /wizard when first_time_setup is true", async () => {
    getSettingsMock.mockResolvedValue({ globals: { first_time_setup: true } });
    renderDashboardRoute();
    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith("/wizard"));
  });

  it("does not navigate when first_time_setup is false", async () => {
    getSettingsMock.mockResolvedValue({ globals: { first_time_setup: false } });
    renderDashboardRoute();
    await waitFor(() => expect(getSettingsMock).toHaveBeenCalled());
    expect(navigateMock).not.toHaveBeenCalled();
  });

  it("still renders the dashboard when the check fails (advisory only)", async () => {
    getSettingsMock.mockRejectedValue(new Error("offline"));
    renderDashboardRoute();
    await waitFor(() => expect(getSettingsMock).toHaveBeenCalled());
    expect(navigateMock).not.toHaveBeenCalled();
  });
```

`renderDashboardRoute()` is whatever render helper the existing file uses (or a direct `render(<DashboardRoute />)` matching its current tests) — reuse it, do not invent a new one.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd /home/dannyb/sources/PiFire-sb1/web-react && bun run test src/components/DashboardRoute.test.tsx`
Expected: FAIL — `getSettings` is never called and `navigate` is never invoked.

- [ ] **Step 4: Implement the gate**

In `web-react/src/components/DashboardRoute.tsx`, add the imports and the effect. The full file becomes:

```tsx
import { useEffect } from "react";
import { useNavigate } from "react-router";
import { getSettings } from "../helpers/settings/settingsApi";
import { useDashData } from "../helpers/useDashData";
import { useAppPrefs } from "./AppPrefs";
import { ConnectionStatus } from "./ConnectionStatus";
import { Dashboard } from "./dashboard/Dashboard";

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

export function DashboardRoute() {
  const { dash, phase, controlAlive, targetUrl, command } = useDashData();
  const { accent, setAccent, animate, setAnimate } = useAppPrefs();
  const navigate = useNavigate();

  // Non-blocking first_time_setup gate. "/" deliberately has NO route loader
  // (see App.tsx): React Router defers rendering until a loader resolves --
  // even a synchronous one resolves on a microtask -- so a loader here would
  // turn the dashboard's first paint into an async gap. Instead we check once
  // after mount and redirect a fresh install to the wizard. A brief dashboard
  // flash before the redirect is the accepted tradeoff; a failed check is
  // advisory and must never block the dashboard.
  useEffect(() => {
    let cancelled = false;
    getSettings(BASE_URL)
      .then((s) => {
        if (!cancelled && s.globals?.first_time_setup) navigate("/wizard");
      })
      .catch(() => {
        /* advisory only -- never block the dashboard on this check */
      });
    return () => {
      cancelled = true;
    };
  }, [navigate]);

  if (phase !== "live" && phase !== "demo") {
    return (
      <div className="pf-fit">
        <ConnectionStatus phase={phase} targetUrl={targetUrl} />
      </div>
    );
  }
  return (
    <Dashboard
      dash={dash}
      command={command}
      phase={phase}
      controlAlive={controlAlive}
      accent={accent}
      setAccent={setAccent}
      animate={animate}
      setAnimate={setAnimate}
    />
  );
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd /home/dannyb/sources/PiFire-sb1/web-react && bun run test src/components/DashboardRoute.test.tsx`
Expected: PASS (new tests plus every pre-existing test in the file).

- [ ] **Step 6: Gate**

Run: `cd /home/dannyb/sources/PiFire-sb1/web-react && bun run typecheck && bun run lint && bun run test`
Expected: all PASS. If the React Compiler lint objects to the effect, do NOT add a suppression — report it instead (the effect performs a fetch/navigate side effect, not state derivation, so it should be accepted).

- [ ] **Step 7: Commit**

```bash
git add web-react/src/components/DashboardRoute.tsx web-react/src/components/DashboardRoute.test.tsx
git commit -m "feat(web-react): redirect a fresh install to the setup wizard"
```

---

### Task 2: read-only PlatformTab

**Files:**
- Create: `web-react/src/components/settings/tabs/PlatformTab.tsx`
- Create: `web-react/src/components/settings/tabs/PlatformTab.test.tsx`
- Modify: `web-react/src/components/App.tsx` (register the route — **this task solely owns this file**)
- Modify: `web-react/src/components/settings/SettingsShell.tsx` (add the nav entry)
- Test: `web-react/src/components/settings/SettingsShell.test.tsx`

**Interfaces:**
- Consumes: `useOutletContext<{ settings: Settings; mode: string }>()` (house pattern — see `SafetyTab.tsx`); `Section({ title, children })` from `../fields/Section`; `Link` from `react-router`; `Settings` type from `../../../helpers/settings/settingsApi`.
- Produces: `PlatformTab()` — no props.

**Read-only by design (D1).** No `useSaveSettings`, no field components, no mutation. Values come straight from the loader's `settings.platform`.

- [ ] **Step 1: Write the failing tests**

Create `web-react/src/components/settings/tabs/PlatformTab.test.tsx`:

```tsx
import { afterEach, describe, expect, it } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import type { Settings } from "../../../helpers/settings/settingsApi";
import { PlatformTab } from "./PlatformTab";

afterEach(cleanup);

function renderTab(settings: Settings) {
  const router = createMemoryRouter(
    [
      {
        path: "/",
        element: <PlatformTab />,
        // PlatformTab reads the settings via useOutletContext in the app; in
        // isolation we provide the same shape through a parent route context.
      },
    ],
    { initialEntries: ["/"] },
  );
  return render(<RouterProvider router={router} />);
}

describe("PlatformTab", () => {
  it("renders the platform summary values read-only", () => {
    renderTab({
      platform: {
        current: "pcb_4.x.x",
        system_type: "raspberry_pi_all",
        dc_fan: true,
        triggerlevel: "HIGH",
        standalone: true,
        real_hw: true,
        outputs: { auger: 14, fan: 15, igniter: 18, power: 4, dc_fan: 26, pwm: 13 },
      },
    } as Settings);

    expect(screen.getByText("pcb_4.x.x")).toBeInTheDocument();
    expect(screen.getByText("raspberry_pi_all")).toBeInTheDocument();
    expect(screen.getByText("DC Fan (PWM)")).toBeInTheDocument();
    expect(screen.getByText("HIGH")).toBeInTheDocument();
    // read-only: no inputs, selects or save button anywhere
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /save/i })).not.toBeInTheDocument();
  });

  it("links to the setup wizard", () => {
    renderTab({ platform: {} } as Settings);
    expect(screen.getByRole("link", { name: /configure in setup wizard/i })).toHaveAttribute(
      "href",
      "/wizard",
    );
  });

  it("renders placeholders when platform settings are absent", () => {
    renderTab({} as Settings);
    expect(screen.getByText("Grill Platform")).toBeInTheDocument();
    // AC Fan is the falsy-dc_fan rendering; must not throw on a missing section
    expect(screen.getByText("AC Fan")).toBeInTheDocument();
  });
});
```

**IMPORTANT:** the component reads `useOutletContext`, so the test must supply that context. Read how the OTHER tab tests in `web-react/src/components/settings/tabs/*.test.tsx` provide it (there is an established `renderRoute`/outlet-context helper in that directory) and **mirror the existing helper exactly** rather than the sketch above — adapt `renderTab` to the file's real convention.

- [ ] **Step 2: Run to verify it fails**

Run: `cd /home/dannyb/sources/PiFire-sb2/web-react && bun run test src/components/settings/tabs/PlatformTab.test.tsx`
Expected: FAIL — module `./PlatformTab` not found.

- [ ] **Step 3: Implement the tab**

Create `web-react/src/components/settings/tabs/PlatformTab.tsx`:

```tsx
import { Link, useOutletContext } from "react-router";
import type { Settings } from "../../../helpers/settings/settingsApi";
import { Section } from "../fields/Section";

const DASH = "—";

function yesNo(v: unknown): string {
  return v ? "Yes" : "No";
}
function orDash(v: unknown): string {
  return v === undefined || v === null || v === "" ? DASH : String(v);
}

// Read-only by design: every value below is owned by the Setup Wizard, which
// writes them on finish (and DERIVES dc_fan for x86_numato / ft232h_relay).
// Editing them here would be silently overwritten by the next wizard run, so
// this tab summarises and links out instead.
export function PlatformTab() {
  const { settings } = useOutletContext<{ settings: Settings; mode: string }>();
  const platform = settings.platform ?? {};
  const outputs = platform.outputs ?? {};

  const summary: { label: string; value: string }[] = [
    { label: "Board / Profile", value: orDash(platform.current) },
    { label: "System Type", value: orDash(platform.system_type) },
    { label: "Fan Type", value: platform.dc_fan ? "DC Fan (PWM)" : "AC Fan" },
    { label: "Relay Trigger Level", value: orDash(platform.triggerlevel) },
    { label: "Standalone", value: yesNo(platform.standalone) },
    { label: "Real Hardware", value: yesNo(platform.real_hw) },
  ];
  const pins: { label: string; value: string }[] = [
    { label: "Auger", value: orDash(outputs.auger) },
    { label: "Fan", value: orDash(outputs.fan) },
    { label: "Igniter", value: orDash(outputs.igniter) },
    { label: "Power", value: orDash(outputs.power) },
    { label: "DC Fan", value: orDash(outputs.dc_fan) },
    { label: "PWM", value: orDash(outputs.pwm) },
  ];

  return (
    <div className="pf-settings-tab" data-tab="platform">
      <Section title="Grill Platform">
        <p className="pf-section-note">
          Platform hardware is configured by the Setup Wizard. These values are shown here for
          reference only.
        </p>
        <Link className="pf-btn" to="/wizard">
          Configure in Setup Wizard
        </Link>
        <dl className="pf-kv">
          {summary.map((row) => (
            <div className="pf-kv-row" key={row.label}>
              <dt>{row.label}</dt>
              <dd>{row.value}</dd>
            </div>
          ))}
        </dl>
      </Section>
      <Section title="Output Pins">
        <dl className="pf-kv">
          {pins.map((row) => (
            <div className="pf-kv-row" key={row.label}>
              <dt>{row.label}</dt>
              <dd>{row.value}</dd>
            </div>
          ))}
        </dl>
      </Section>
    </div>
  );
}
```

- [ ] **Step 4: Register the route and nav entry**

In `web-react/src/components/App.tsx`, add the import alongside the other tab imports:

```tsx
import { PlatformTab } from "./settings/tabs/PlatformTab";
```

and add the route as the LAST child of `/settings` (after `{ path: "units", element: <UnitsTab /> }`):

```tsx
      { path: "platform", element: <PlatformTab /> },
```

In `web-react/src/components/settings/SettingsShell.tsx`, append to the tab list (after `{ path: "units", label: "Units" }`):

```tsx
  { path: "platform", label: "Platform" },
```

- [ ] **Step 5: Add the nav test**

In `web-react/src/components/settings/SettingsShell.test.tsx`, mirror whatever assertion the file already makes about the tab list and extend it to include the new "Platform" entry. Read the file first; if it asserts an exact tab count or an exact label array, update that expectation.

- [ ] **Step 6: Run to verify all pass**

Run: `cd /home/dannyb/sources/PiFire-sb2/web-react && bun run test src/components/settings/tabs/PlatformTab.test.tsx src/components/settings/SettingsShell.test.tsx`
Expected: PASS.

- [ ] **Step 7: Gate**

Run: `cd /home/dannyb/sources/PiFire-sb2/web-react && bun run typecheck && bun run lint && bun run test`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add web-react/src/components/settings/tabs/PlatformTab.tsx web-react/src/components/settings/tabs/PlatformTab.test.tsx web-react/src/components/App.tsx web-react/src/components/settings/SettingsShell.tsx web-react/src/components/settings/SettingsShell.test.tsx
git commit -m "feat(web-react): add a read-only Platform settings tab"
```

---

### Task 3: `ConfigOptionField` falls back to the manifest default

**Files:**
- Modify: `web-react/src/components/wizard/ConfigOptionField.tsx`
- Test: `web-react/src/components/wizard/ConfigOptionField.test.tsx`

**Interfaces:**
- Consumes: `ConfigOption` from `../../helpers/wizard/wizardTypes` (has an optional `default?: unknown`).
- Produces: no signature change — `ConfigOptionField({ option, value, onChange })`.

**The bug:** the component renders `String(value)` with no fallback. When a display module has never been configured, `value` is `undefined`, so a `list` field renders `value="undefined"` (matching no option, so the select shows blank) and a `string` field renders empty — even though the manifest supplies `option.default`. Fall back to `option.default` when `value` is `undefined`.

- [ ] **Step 1: Write the failing tests**

Add to `web-react/src/components/wizard/ConfigOptionField.test.tsx` (match the file's existing imports and render helper):

```tsx
  it("falls back to the manifest default for a list option with no stored value", () => {
    render(
      <ConfigOptionField
        option={{
          option_name: "rotation",
          option_friendly_name: "Screen Rotation",
          option_type: "list",
          list_values: [0, 90, 180, 270],
          list_labels: ["0°", "90°", "180°", "270°"],
          default: 90,
        }}
        value={undefined}
        onChange={rs.fn()}
      />,
    );
    expect(screen.getByRole("combobox", { name: "Screen Rotation" })).toHaveValue("90");
  });

  it("falls back to the manifest default for a string option with no stored value", () => {
    render(
      <ConfigOptionField
        option={{
          option_name: "display_data_filename",
          option_friendly_name: "Layout File",
          option_type: "string",
          default: "./display/default.json",
        }}
        value={undefined}
        onChange={rs.fn()}
      />,
    );
    expect(screen.getByRole("textbox", { name: "Layout File" })).toHaveValue(
      "./display/default.json",
    );
  });

  it("prefers a stored value over the manifest default", () => {
    render(
      <ConfigOptionField
        option={{
          option_name: "rotation",
          option_friendly_name: "Screen Rotation",
          option_type: "list",
          list_values: [0, 90, 180, 270],
          list_labels: ["0°", "90°", "180°", "270°"],
          default: 90,
        }}
        value={270}
        onChange={rs.fn()}
      />,
    );
    expect(screen.getByRole("combobox", { name: "Screen Rotation" })).toHaveValue("270");
  });
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd /home/dannyb/sources/PiFire-sb3/web-react && bun run test src/components/wizard/ConfigOptionField.test.tsx`
Expected: FAIL — the list select has value `"undefined"` and the text input is empty.

- [ ] **Step 3: Implement the fallback**

Rewrite `web-react/src/components/wizard/ConfigOptionField.tsx` as:

```tsx
import type { ConfigOption } from "../../helpers/wizard/wizardTypes";

export interface ConfigOptionFieldProps {
  option: ConfigOption;
  value: unknown;
  onChange: (next: string) => void;
}

export function ConfigOptionField({ option, value, onChange }: ConfigOptionFieldProps) {
  if (option.hidden) return null;

  // A module that has never been configured has no stored value for its
  // options; fall back to the manifest's `default` so the field shows what the
  // driver will actually use rather than a blank/"undefined" selection.
  const effective = value === undefined ? option.default : value;

  if (option.option_type === "list") {
    const listValues = option.list_values ?? [];
    const listLabels = option.list_labels ?? [];
    return (
      <label className="pf-field">
        <span className="pf-field-label">{option.option_friendly_name}</span>
        <select
          className="pf-input"
          value={String(effective)}
          onChange={(e) => {
            const chosen = listValues.find((item) => String(item) === e.target.value);
            onChange(String(chosen ?? e.target.value));
          }}
        >
          {listValues.map((item, i) => (
            <option key={String(item)} value={String(item)}>
              {listLabels[i] ?? String(item)}
            </option>
          ))}
        </select>
      </label>
    );
  }

  return (
    <label className="pf-field">
      <span className="pf-field-label">{option.option_friendly_name}</span>
      <input
        className="pf-input"
        type="text"
        value={String(effective ?? "")}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd /home/dannyb/sources/PiFire-sb3/web-react && bun run test src/components/wizard/ConfigOptionField.test.tsx`
Expected: PASS (new tests plus all pre-existing ones — the stored-value path is unchanged).

- [ ] **Step 5: Gate**

Run: `cd /home/dannyb/sources/PiFire-sb3/web-react && bun run typecheck && bun run lint && bun run test`
Expected: all PASS. The full suite matters here — `ConfigOptionField` is rendered by `ModuleCard` in the display step, so `DisplayStep`/`WizardShell` tests exercise it.

- [ ] **Step 6: Commit**

```bash
git add web-react/src/components/wizard/ConfigOptionField.tsx web-react/src/components/wizard/ConfigOptionField.test.tsx
git commit -m "fix(web-react): fall back to the manifest default for unset config options"
```

---

### Integration Step (controller, after all three land)

- [ ] **Update the stale `App.tsx` comment.** Lines 33-46 currently say the
      first_time_setup gate is "intentionally NOT wired here" and "left as
      follow-up work". Task 1 wired it. Replace that trailing explanation with:

```tsx
// first_time_setup gate: Flask forces the wizard when GRILL_ID hasn't been
// set up yet. Wiring that as a redirect *from the index loader* was
// considered but rejected: "/" (DashboardRoute) has no loader at all, and
// React Router's data routers always defer rendering until a route's loader
// resolves -- even a synchronous one resolves on a microtask -- so adding one
// would turn the dashboard's first paint into an async gap, breaking the
// existing synchronous assertions in App.test.tsx / DashboardRoute.test.tsx
// and adding an extra network round trip to every dashboard load. The gate is
// instead a non-blocking post-mount check inside DashboardRoute.
```

- [ ] **Verify the merged state** (nothing else tests the combination):
      `cd web-react && bun run typecheck && bun run lint && bun run test && bun run build`
- [ ] **e2e** (main checkout; HUP-reload gunicorn first so it serves current code):
      `cd web-react && bunx playwright test --reporter=line`
- [ ] **Update the backlog** — strike the three items in
      `.superpowers/sdd/react-migration-backlog.md` and note D1/D2.

---

## Self-Review

**1. Spec coverage:** first_time_setup redirect → T1 ✅. PlatformTab (read-only per D1) → T2 ✅. ConfigOptionField defaults → T3 ✅. D2 (no repair migration) → explicitly out of scope ✅.

**2. Placeholder scan:** every code step carries complete code. Two steps say "mirror the file's existing helper" (T2 Step 1's outlet-context helper, T2 Step 5's nav assertion) — both name the exact file to read and are accompanied by concrete code to adapt, because those helpers' exact shapes are local conventions the implementer must match rather than duplicate.

**3. Type consistency:** `ConfigOption.default` is `unknown` (already declared optional in `wizardTypes.ts`), so `effective` stays `unknown` and both render paths already `String(...)` it. `Settings["platform"]` fields are all optional in `settingsTypes.gen.ts`, which is why `PlatformTab` guards with `?? {}` and `orDash`. `getSettings(baseUrl)` matches the existing `settingsApi` signature used by `settingsLoader`.
