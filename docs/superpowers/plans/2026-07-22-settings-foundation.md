# Settings Foundation (Phase 2a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the React settings foundation — a data-router app shell, settings loaded via a route **loader**, one small JSON write endpoint, a styled form-primitive kit, and three working tabs (Grill Name/Theme, PWM, Units) that exercise all three settings write paths — plus ESLint (react-hooks + React Compiler rules).

**Architecture:** Hybrid data model, each transport used for what it's good at. The **dashboard** stays on the live SocketIO stream (unchanged from phase 1 — continuous ~1 Hz push). **Settings** are load-on-navigation, so they use React Router's **data router** (`createBrowserRouter`) with a route **loader** (`GET /api/settings`) and read via `useLoaderData`/`useOutletContext`. Writes go through a new `POST /api/settings_update` (deep_update + `save_settings_and_flag_update` with caller-named control flags), with `useRevalidator` re-running the loader after a save; Units uses the existing `/api/set/units` command. App-level UI prefs (accent/animate) live in a small context. `react-router` in **library mode** (static-served by Flask). The only backend change is the one endpoint.

**Tech Stack:** React 19 (+ React Compiler already wired), react-router (library mode, `createBrowserRouter`), Vite 8/rolldown, Vitest 4, ESLint flat config, Playwright, Flask/pytest. Package manager **bun**.

## Global Constraints

- Package manager is **bun**, never bare npm: `bun add`, `bun run …`. Commit `bun.lock`.
- TypeScript strict, `noUnusedLocals` + `noUnusedParameters`. Typecheck `bunx tsc -b`.
- **`bun run lint` (ESLint, react-hooks + `react-hooks/react-compiler` rules) must be clean** — a verification gate for every task from Task 1 on.
- **Data transports:** dashboard = live SocketIO (unchanged); settings = `GET /api/settings` via a route **loader**; settings writes = `POST /api/settings_update` (flags-bearing) with `useRevalidator` after save; Units = the existing `/api/set/units` command. The only backend change is the one new endpoint; touch no other Python behavior.
- `react-router` in **library mode** via `createBrowserRouter` + `RouterProvider` (the data router — used for loaders). Do NOT add `@react-router/dev`/framework mode. Keep the existing `vite.config.ts` compiler wiring (`@vitejs/plugin-react` + `@rolldown/plugin-babel` + `reactCompilerPreset()`) — confirm, don't replace.
- New endpoint envelope matches the existing settings family: `{ "result": "success", "message": …, "data": … }` (NOT the command grammar's `"OK"`). The settings client keys off `result === "success"`.
- Backend handler is a module-level `_`-prefixed function in `blueprints/api/routes.py` registered in `_API_POST_ACTIONS` (repo convention — no `services.py`).
- Python tests run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/... -q`.
- Dashboard route (`/`) and demo mode must remain unaffected.
- Frontend work is under `web-react/`; run bun commands from there.

---

## File Structure

- `web-react/eslint.config.js` — new flat ESLint config (create).
- `web-react/package.json` — react-router dep, eslint dev-deps, `lint` script (modify).
- `web-react/src/AppPrefs.tsx` — accent/animate context provider (create).
- `web-react/src/DashboardRoute.tsx` — dashboard route element (socket hook + prefs → `Dashboard`) (create).
- `web-react/src/App.tsx` — `createBrowserRouter` + `RouterProvider` under `AppPrefsProvider` (modify).
- `web-react/src/settings/settingsApi.ts` — `buildSettingsUrl`, `getSettings`, `applySettings` (pure, tested) (create).
- `web-react/src/settings/settingsApi.test.ts` — URL + body-shape tests (create).
- `web-react/src/settings/settingsRoutes.ts` — `settingsLoader` (route loader) (create).
- `web-react/src/settings/useSaveSettings.ts` — save helper (`applySettings` + `useRevalidator`) (create).
- `web-react/src/settings/delta.ts` + `delta.test.ts` — pure `setPath` delta helper (create).
- `web-react/src/settings/SettingsShell.tsx` — nav rail + `<Outlet context={{settings}}/>`, responsive (create).
- `web-react/src/settings/SettingsError.tsx` — route `errorElement` for load failure (create).
- `web-react/src/settings/fields/{Toggle,Select,NumberField,TextField,Section}.tsx` (create).
- `web-react/src/settings/tabs/{GeneralTab,PwmTab,UnitsTab}.tsx` (create).
- `web-react/src/settings/settings.css` — responsive settings layout + field styles (create).
- `web-react/src/main.tsx` — import `settings.css` (modify).
- `web-react/src/dashboard/Dashboard.tsx` — header menu button → `navigate("/settings")` (modify).
- `web-react/src/command.ts` — add `setUnits(units)` to the command client (modify).
- `web-react/tests/e2e/settings.spec.ts` — settings round-trip e2e (create).
- Backend: `blueprints/api/routes.py` — `_api_post_settings_update` + registration (modify).
- Backend test: `tests/web/test_api_settings_update.py` (create).

---

## Task 1: Tooling — ESLint (react-hooks + compiler) + react-router dep + lint-clean baseline

**Files:**
- Create: `web-react/eslint.config.js`
- Modify: `web-react/package.json` (deps + `lint` script)
- Modify: whatever existing `web-react/src/**` files ESLint flags, to reach clean

**Interfaces:**
- Consumes: nothing.
- Produces: a passing `bun run lint`; `react-router` available for later tasks.

> Note: `eslint-plugin-react-hooks` was already added to `package.json`/`bun.lock` by the user. This task adds the rest and the config; do not remove the existing entry.

- [ ] **Step 1: Install remaining deps (bun)**

Run (in `web-react/`):

```bash
bun add react-router
bun add -d eslint @eslint/js typescript-eslint eslint-plugin-react-refresh globals
```

(`eslint-plugin-react-hooks` is already present — leave it.)

- [ ] **Step 2: Write `eslint.config.js`**

```js
import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "tests/e2e", "*.config.js", "*.config.ts"] },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: { ecmaVersion: 2022, globals: globals.browser },
    plugins: { "react-hooks": reactHooks, "react-refresh": reactRefresh },
    rules: {
      ...reactHooks.configs["recommended-latest"].rules,
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
    },
  },
);
```

- [ ] **Step 3: Add the `lint` script**

In `web-react/package.json` `scripts`, add: `"lint": "eslint ."`.

- [ ] **Step 4: Verify the React Compiler rule is active**

Run: `bunx eslint --print-config src/App.tsx | grep -i "react-compiler"`
Expected: a line showing `react-hooks/react-compiler` enabled. If absent, `bun add -d eslint-plugin-react-hooks@latest` and re-check. Do not proceed until present.

- [ ] **Step 5: Run lint and fix all violations to clean**

Run: `bun run lint`. Fix every reported problem in `web-react/src/**`. Likely candidates in the existing phase-1 code:
- `src/App.tsx`: `document.documentElement.setAttribute("data-accent", accent)` runs during render (render-phase side effect) — this moves into the new `AppPrefs` provider's `useEffect` in Task 4, but if Task 1 runs first, wrap it in `useEffect(() => {...}, [accent])` now.
- `useEffect` exhaustive-deps in `src/useDashData.ts`, `src/dashboard/hooks.ts`, `src/dashboard/Dashboard.tsx`, `src/dashboard/ControlButtons.tsx` — add missing deps, or keep a deliberate mount-only `[]` with a one-line comment.
Re-run until zero problems. Do NOT blanket-disable rules; fix the code (a single targeted `// eslint-disable-next-line` with a reason is acceptable only for a deliberate exception).

- [ ] **Step 6: Confirm nothing regressed**

Run (in `web-react/`): `bunx tsc -b && bun run test && bun run build` → all exit 0.

- [ ] **Step 7: Commit**

```bash
cd web-react && git add eslint.config.js package.json bun.lock src
git commit -m "chore(web-react): eslint (react-hooks + react-compiler) + react-router; lint-clean baseline"
```

---

## Task 2: Backend — `POST /api/settings_update` (delta + control flags)

**Files:**
- Modify: `blueprints/api/routes.py`
- Test: `tests/web/test_api_settings_update.py`

**Interfaces:**
- Consumes: `deep_update`, `read_control`, `write_settings` (already imported), `read_settings`; `save_settings_and_flag_update` from `common.app`.
- Produces: `POST /api/settings_update` accepting `{"settings": <delta>, "flags": [<flag>...]}` → `{"result": "success"|"error", "message": str, "data": settings}`.

- [ ] **Step 1: Write the failing test**

Create `tests/web/test_api_settings_update.py` (match the client fixture used by a neighboring `tests/web/test_*api*.py` — read one first):

```python
import json
from common.datastore_accessors import read_control, read_settings, write_control
from common.common import WriteKind


def test_settings_update_persists_delta_and_sets_flag(client):
    body = {"settings": {"pwm": {"update_time": 7}}, "flags": ["settings_update"]}
    resp = client.post("/api/settings_update", data=json.dumps(body), content_type="application/json")
    assert resp.status_code == 200
    assert resp.get_json()["result"] == "success"
    assert read_settings()["pwm"]["update_time"] == 7
    assert read_control()["settings_update"] is True


def test_settings_update_empty_flags_sets_none(client):
    ctrl = read_control(); ctrl["settings_update"] = False
    write_control(ctrl, WriteKind.OVERWRITE, origin="test")
    body = {"settings": {"globals": {"grill_name": "Smokey"}}, "flags": []}
    resp = client.post("/api/settings_update", data=json.dumps(body), content_type="application/json")
    assert resp.status_code == 200
    assert read_settings()["globals"]["grill_name"] == "Smokey"
    assert read_control()["settings_update"] is False
```

- [ ] **Step 2: Run it to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_settings_update.py -q`
Expected: FAIL (404).

- [ ] **Step 3: Add the handler + registration**

In `blueprints/api/routes.py`, add `save_settings_and_flag_update` to the `from common.app import (...)` line. Add near `_api_post_settings`:

```python
def _api_post_settings_update(settings, request_json):
    """
    JSON settings write that ALSO sets control-update flags so the running
    control loop re-reads. Mirrors save_settings_and_flag_update.
    body: { "settings": <partial settings dict>, "flags": ["settings_update", ...] }
    """
    try:
        delta = request_json.get("settings", {})
        flags = request_json.get("flags", []) or []
        settings = deep_update(settings, delta)
        control = read_control()
        save_settings_and_flag_update(settings, control, *flags, origin="api")
        return jsonify({"result": "success", "message": "Settings updated.", "data": settings}), 200
    except Exception as e:
        return jsonify({"result": "error", "message": f"Settings update failed: {e}", "data": {}}), 200
```

Register in `_API_POST_ACTIONS`:

```python
_API_POST_ACTIONS = {
    "settings": _api_post_settings,
    "settings_update": _api_post_settings_update,
    "control": _api_post_control,
    "wled_push_profiles": _api_post_wled_push_profiles,
    "wled_test_profile": _api_post_wled_test_profile,
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_settings_update.py -q` → 2 passed.

- [ ] **Step 5: ruff format**

Run: `uvx ruff format blueprints/api/routes.py tests/web/test_api_settings_update.py`

- [ ] **Step 6: Commit**

```bash
git add blueprints/api/routes.py tests/web/test_api_settings_update.py
git commit -m "feat(api): POST /api/settings_update — settings delta + control flags"
```

---

## Task 3: Settings API + loader + save helper

**Files:**
- Create: `web-react/src/settings/settingsApi.ts`
- Create: `web-react/src/settings/settingsApi.test.ts`
- Create: `web-react/src/settings/settingsRoutes.ts`
- Create: `web-react/src/settings/useSaveSettings.ts`

**Interfaces:**
- Consumes: the backend endpoints (Task 2); `react-router` `useRevalidator`.
- Produces:
  - `type Settings = Record<string, any>`; `type SettingsFlag = "settings_update" | "controller_update" | "distance_update" | "probe_profile_update"`
  - `function buildSettingsUrl(baseUrl: string, path: string): string`
  - `async function getSettings(baseUrl: string): Promise<Settings>`
  - `async function applySettings(baseUrl, delta: object, flags: SettingsFlag[]): Promise<{ ok: boolean; message: string; data?: Settings }>`
  - `async function settingsLoader(): Promise<{ settings: Settings }>` (the route loader)
  - `function useSaveSettings(): { save(delta: object, flags: SettingsFlag[]): Promise<boolean>; saving: boolean; baseUrl: string }`

- [ ] **Step 1: Write the failing tests (pure API)**

Create `web-react/src/settings/settingsApi.test.ts`:

```ts
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { buildSettingsUrl, applySettings } from "./settingsApi";

describe("buildSettingsUrl", () => {
  it("joins base + /api + path", () => {
    expect(buildSettingsUrl("", "settings")).toBe("/api/settings");
    expect(buildSettingsUrl("http://pi:5000", "settings_update")).toBe("http://pi:5000/api/settings_update");
  });
});

describe("applySettings", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({ result: "success", message: "", data: {} }) }));
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("POSTs {settings, flags} to /api/settings_update and maps success", async () => {
    const r = await applySettings("", { globals: { grill_name: "X" } }, ["settings_update"]);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/settings_update");
    const init = fetchMock.mock.calls[0][1];
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ settings: { globals: { grill_name: "X" } }, flags: ["settings_update"] });
    expect(r.ok).toBe(true);
  });

  it("maps a non-success envelope to ok:false", async () => {
    fetchMock.mockResolvedValueOnce({ ok: true, json: async () => ({ result: "error", message: "bad" }) });
    expect(await applySettings("", {}, [])).toMatchObject({ ok: false, message: "bad" });
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `bun run test src/settings/settingsApi.test.ts` → FAIL.

- [ ] **Step 3: Implement `settingsApi.ts`**

```ts
export type Settings = Record<string, any>;
export type SettingsFlag = "settings_update" | "controller_update" | "distance_update" | "probe_profile_update";

export function buildSettingsUrl(baseUrl: string, path: string): string {
  return `${baseUrl}/api/${path}`;
}

export async function getSettings(baseUrl: string): Promise<Settings> {
  const res = await fetch(buildSettingsUrl(baseUrl, "settings"));
  if (!res.ok) throw new Error(`GET /api/settings failed: HTTP ${res.status}`);
  const body = (await res.json()) as { settings?: Settings };
  return body.settings ?? (body as Settings); // GET /api/settings returns { settings: {...} }
}

export async function applySettings(
  baseUrl: string,
  delta: object,
  flags: SettingsFlag[],
): Promise<{ ok: boolean; message: string; data?: Settings }> {
  try {
    const res = await fetch(buildSettingsUrl(baseUrl, "settings_update"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ settings: delta, flags }),
    });
    if (!res.ok) return { ok: false, message: `HTTP ${res.status}` };
    const body = (await res.json()) as { result?: string; message?: string; data?: Settings };
    return { ok: body.result === "success", message: body.message ?? "", data: body.data };
  } catch (e) {
    return { ok: false, message: e instanceof Error ? e.message : "network error" };
  }
}
```

- [ ] **Step 4: Implement `settingsRoutes.ts` (the loader)**

```ts
import { getSettings, type Settings } from "./settingsApi";

const BASE_URL = import.meta.env.VITE_PIFIRE_URL || "";

// React Router route loader — runs on navigation into /settings. Throws on
// failure so the route's errorElement renders.
export async function settingsLoader(): Promise<{ settings: Settings }> {
  return { settings: await getSettings(BASE_URL) };
}
```

- [ ] **Step 5: Implement `useSaveSettings.ts`**

```ts
import { useCallback, useState } from "react";
import { useRevalidator } from "react-router";
import { applySettings, type SettingsFlag } from "./settingsApi";

const BASE_URL = import.meta.env.VITE_PIFIRE_URL || "";

export function useSaveSettings() {
  const revalidator = useRevalidator();
  const [saving, setSaving] = useState(false);
  const save = useCallback(async (delta: object, flags: SettingsFlag[]): Promise<boolean> => {
    setSaving(true);
    const r = await applySettings(BASE_URL, delta, flags);
    setSaving(false);
    if (r.ok) revalidator.revalidate(); // re-run the loader → fresh settings
    return r.ok;
  }, [revalidator]);
  return { save, saving, baseUrl: BASE_URL };
}
```

- [ ] **Step 6: Run tests + typecheck + lint**

Run: `bun run test src/settings/settingsApi.test.ts && bunx tsc -b && bun run lint` → tests pass, tsc 0, lint clean.

- [ ] **Step 7: Commit**

```bash
cd web-react && git add src/settings/settingsApi.ts src/settings/settingsApi.test.ts src/settings/settingsRoutes.ts src/settings/useSaveSettings.ts
git commit -m "feat(web-react): settings API + route loader + save helper"
```

---

## Task 4: Data router + app-prefs context + settings shell

**Files:**
- Create: `web-react/src/AppPrefs.tsx`
- Create: `web-react/src/DashboardRoute.tsx`
- Modify: `web-react/src/App.tsx`
- Create: `web-react/src/settings/SettingsShell.tsx`
- Create: `web-react/src/settings/SettingsError.tsx`
- Create: `web-react/src/settings/settings.css`
- Modify: `web-react/src/main.tsx` (import `settings.css`)
- Modify: `web-react/src/dashboard/Dashboard.tsx` (header → navigate to `/settings`)

**Interfaces:**
- Consumes: `react-router` (`createBrowserRouter`, `RouterProvider`, `Outlet`, `NavLink`, `Navigate`, `useLoaderData`, `useOutletContext`, `useNavigate`); `settingsLoader` (Task 3); `Dashboard`/`useDashData` (phase 1); the three tabs (Tasks 6–8).
- Produces: `AppPrefsProvider` + `useAppPrefs()`; routes `/` (dashboard) and `/settings` (loader + nested tabs); `SettingsShell` provides `{ settings }` via `Outlet` context (`useOutletContext<{ settings: Settings }>()`).

- [ ] **Step 1: `AppPrefs.tsx` — accent/animate context**

```tsx
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import type { AccentName } from "./types";

interface AppPrefs {
  accent: AccentName; setAccent: (a: AccentName) => void;
  animate: boolean; setAnimate: (v: boolean) => void;
}
const Ctx = createContext<AppPrefs | null>(null);

export function AppPrefsProvider({ children }: { children: ReactNode }) {
  const [accent, setAccent] = useState<AccentName>("ember");
  const [animate, setAnimate] = useState(true);
  useEffect(() => { document.documentElement.setAttribute("data-accent", accent); }, [accent]);
  return <Ctx.Provider value={{ accent, setAccent, animate, setAnimate }}>{children}</Ctx.Provider>;
}

export function useAppPrefs(): AppPrefs {
  const c = useContext(Ctx);
  if (!c) throw new Error("useAppPrefs must be used within AppPrefsProvider");
  return c;
}
```

- [ ] **Step 2: `DashboardRoute.tsx` — the `/` element (socket + prefs → Dashboard)**

```tsx
import { useDashData } from "./useDashData";
import { ConnectionStatus } from "./components/ConnectionStatus";
import { Dashboard } from "./dashboard/Dashboard";
import { useAppPrefs } from "./AppPrefs";

export function DashboardRoute() {
  const { dash, phase, controlAlive, targetUrl, command } = useDashData();
  const { accent, setAccent, animate, setAnimate } = useAppPrefs();
  if (phase !== "live" && phase !== "demo") {
    return <div className="pf-fit"><ConnectionStatus phase={phase} targetUrl={targetUrl} /></div>;
  }
  return (
    <Dashboard dash={dash} command={command} phase={phase} controlAlive={controlAlive}
      accent={accent} setAccent={setAccent} animate={animate} setAnimate={setAnimate} />
  );
}
```

- [ ] **Step 3: `App.tsx` — the data router**

```tsx
import { createBrowserRouter, RouterProvider, Navigate } from "react-router";
import { AppPrefsProvider } from "./AppPrefs";
import { DashboardRoute } from "./DashboardRoute";
import { SettingsShell } from "./settings/SettingsShell";
import { SettingsError } from "./settings/SettingsError";
import { settingsLoader } from "./settings/settingsRoutes";
import { GeneralTab } from "./settings/tabs/GeneralTab";
import { PwmTab } from "./settings/tabs/PwmTab";
import { UnitsTab } from "./settings/tabs/UnitsTab";

const router = createBrowserRouter([
  { path: "/", element: <DashboardRoute /> },
  {
    path: "/settings",
    element: <SettingsShell />,
    loader: settingsLoader,
    errorElement: <SettingsError />,
    children: [
      { index: true, element: <Navigate to="general" replace /> },
      { path: "general", element: <GeneralTab /> },
      { path: "pwm", element: <PwmTab /> },
      { path: "units", element: <UnitsTab /> },
    ],
  },
]);

export default function App() {
  return (
    <AppPrefsProvider>
      <RouterProvider router={router} />
    </AppPrefsProvider>
  );
}
```

- [ ] **Step 4: `SettingsShell.tsx` — nav rail + Outlet(context=settings)**

```tsx
import { NavLink, Outlet, useLoaderData, useNavigate } from "react-router";
import type { Settings } from "./settingsApi";

const SETTINGS_TABS = [
  { path: "general", label: "General" },
  { path: "pwm", label: "PWM Fan" },
  { path: "units", label: "Units" },
];

export function SettingsShell() {
  const { settings } = useLoaderData() as { settings: Settings };
  const navigate = useNavigate();
  return (
    <div className="pf-settings">
      <aside className="pf-settings-nav">
        <button className="pf-settings-back" onClick={() => navigate("/")}>← Dashboard</button>
        <div className="pf-settings-title">Settings</div>
        {SETTINGS_TABS.map((t) => (
          <NavLink key={t.path} to={t.path} className={({ isActive }) => `pf-settings-link ${isActive ? "active" : ""}`}>
            {t.label}
          </NavLink>
        ))}
      </aside>
      <main className="pf-settings-content">
        <Outlet context={{ settings }} />
      </main>
    </div>
  );
}
```

- [ ] **Step 5: `SettingsError.tsx` — the route errorElement**

```tsx
import { useNavigate } from "react-router";

export function SettingsError() {
  const navigate = useNavigate();
  return (
    <div className="pf-fit">
      <div className="pf-settings-error">
        Couldn't load settings.
        <button className="pf-modal-btn" onClick={() => navigate(0)}>Retry</button>
        <button className="pf-modal-btn" onClick={() => navigate("/")}>Dashboard</button>
      </div>
    </div>
  );
}
```

- [ ] **Step 6: `settings.css` (responsive) + import**

Create `web-react/src/settings/settings.css`:

```css
.pf-settings { display: flex; min-height: 100vh; background: var(--page); color: var(--text); }
.pf-settings-nav { flex: 0 0 220px; background: #1c1712; border-right: 1px solid rgba(255,255,255,0.08); padding: 16px; display: flex; flex-direction: column; gap: 6px; }
.pf-settings-back { background: none; border: none; color: var(--text-dim); font: 600 13px "Barlow"; text-align: left; cursor: pointer; padding: 6px 8px; }
.pf-settings-title { font: 700 18px "Barlow"; margin: 8px 8px 12px; }
.pf-settings-link { color: var(--text-dim); text-decoration: none; padding: 10px 12px; border-radius: 10px; font: 600 15px "Barlow"; }
.pf-settings-link.active { background: color-mix(in srgb, var(--accent) 16%, transparent); color: var(--text); }
.pf-settings-content { flex: 1; padding: 28px 32px; max-width: 720px; overflow-y: auto; }
.pf-settings-error { display: flex; flex-direction: column; align-items: center; gap: 12px; color: var(--text-dim); font: 600 16px "Barlow"; }
@media (max-width: 640px) {
  .pf-settings { flex-direction: column; }
  .pf-settings-nav { flex-basis: auto; flex-direction: row; flex-wrap: wrap; }
}
```

Add `import "./settings/settings.css";` to `web-react/src/main.tsx`.

- [ ] **Step 7: Dashboard header → settings nav**

In `web-react/src/dashboard/Dashboard.tsx`, import `useNavigate` from `react-router`, add `const navigate = useNavigate();`, and add a gear button next to the accent swatches / ANIM toggle: `<button className="pf-toggle" onClick={() => navigate("/settings")} aria-label="settings">⚙</button>`.

- [ ] **Step 8: Typecheck + lint + build**

Run: `bunx tsc -b && bun run lint && bun run build`
Expected: exit 0. The three tab imports must resolve — in sequential execution Tasks 6–8 create them; to keep this task self-contained, create the three tab files as minimal stubs now (`export function GeneralTab() { return null; }`, etc.) and flesh them out in their tasks. Note the stubs in your report.

- [ ] **Step 9: Commit**

```bash
cd web-react && git add src/App.tsx src/AppPrefs.tsx src/DashboardRoute.tsx src/main.tsx src/dashboard/Dashboard.tsx src/settings/SettingsShell.tsx src/settings/SettingsError.tsx src/settings/settings.css src/settings/tabs
git commit -m "feat(web-react): data router + app-prefs context + settings shell"
```

---

## Task 5: Form primitives + delta helper

**Files:**
- Create: `web-react/src/settings/delta.ts` + `delta.test.ts`
- Create: `web-react/src/settings/fields/{Toggle,Select,NumberField,TextField,Section}.tsx`
- Modify: `web-react/src/settings/settings.css` (field styles)

**Interfaces:**
- Produces: `function setPath(obj: object, path: string, value: unknown): object` (immutable nested set); field components `Toggle`/`Select`/`NumberField`/`TextField`/`Section`.

- [ ] **Step 1: Failing test for `setPath`**

Create `web-react/src/settings/delta.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { setPath } from "./delta";

describe("setPath", () => {
  it("builds a nested delta from a dot path", () => {
    expect(setPath({}, "globals.grill_name", "Smokey")).toEqual({ globals: { grill_name: "Smokey" } });
    expect(setPath({}, "pwm.update_time", 7)).toEqual({ pwm: { update_time: 7 } });
  });
  it("merges into an existing partial without mutating input", () => {
    const base = { pwm: { update_time: 7 } };
    const out = setPath(base, "pwm.frequency", 100);
    expect(out).toEqual({ pwm: { update_time: 7, frequency: 100 } });
    expect(base).toEqual({ pwm: { update_time: 7 } });
  });
});
```

- [ ] **Step 2: Run to verify failure** — `bun run test src/settings/delta.test.ts` → FAIL.

- [ ] **Step 3: Implement `delta.ts`**

```ts
export function setPath(obj: object, path: string, value: unknown): object {
  const keys = path.split(".");
  const root: Record<string, any> = { ...(obj as Record<string, any>) };
  let cur = root;
  for (let i = 0; i < keys.length - 1; i++) {
    const k = keys[i];
    cur[k] = { ...(cur[k] ?? {}) };
    cur = cur[k];
  }
  cur[keys[keys.length - 1]] = value;
  return root;
}
```

- [ ] **Step 4: Run to verify pass** — `bun run test src/settings/delta.test.ts` → PASS.

- [ ] **Step 5: Field components** (create under `web-react/src/settings/fields/`)

`NumberField.tsx`:

```tsx
export function NumberField({ label, value, onChange, min, max, step, suffix }: {
  label: string; value: number; onChange: (v: number) => void; min?: number; max?: number; step?: number; suffix?: string;
}) {
  return (
    <label className="pf-field">
      <span className="pf-field-label">{label}</span>
      <span className="pf-field-control">
        <input className="pf-input" type="number" value={value} min={min} max={max} step={step}
          onChange={(e) => onChange(Number(e.target.value))} />
        {suffix && <span className="pf-field-suffix">{suffix}</span>}
      </span>
    </label>
  );
}
```

`Toggle.tsx`:

```tsx
export function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="pf-field">
      <span className="pf-field-label">{label}</span>
      <button type="button" className={`pf-switch ${checked ? "on" : ""}`} aria-pressed={checked} onClick={() => onChange(!checked)}>
        <span className="pf-switch-knob" />
      </button>
    </label>
  );
}
```

`Select.tsx`:

```tsx
export function Select({ label, value, options, onChange }: {
  label: string; value: string; options: { value: string; label: string }[]; onChange: (v: string) => void;
}) {
  return (
    <label className="pf-field">
      <span className="pf-field-label">{label}</span>
      <select className="pf-input" value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </label>
  );
}
```

`TextField.tsx`:

```tsx
export function TextField({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <label className="pf-field">
      <span className="pf-field-label">{label}</span>
      <input className="pf-input" type="text" value={value} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}
```

`Section.tsx`:

```tsx
import type { ReactNode } from "react";
export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="pf-section">
      <h2 className="pf-section-title">{title}</h2>
      <div className="pf-section-body">{children}</div>
    </section>
  );
}
```

- [ ] **Step 6: Field styles → `settings.css`**

```css
.pf-section { margin-bottom: 28px; }
.pf-section-title { font: 700 20px "Barlow"; margin: 0 0 14px; }
.pf-section-body { display: flex; flex-direction: column; gap: 14px; }
.pf-field { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.pf-field-label { font: 600 15px "Barlow"; color: var(--text-dim); }
.pf-field-control { display: flex; align-items: center; gap: 8px; }
.pf-input { background: var(--inset); color: var(--text); border: 1px solid rgba(255,255,255,0.12); border-radius: 10px; padding: 8px 12px; font: 600 15px "Barlow"; min-width: 120px; }
.pf-field-suffix { color: var(--text-dim); font: 600 14px "Barlow"; }
.pf-switch { width: 46px; height: 26px; border-radius: 999px; border: none; background: var(--inset); position: relative; cursor: pointer; }
.pf-switch.on { background: var(--accent); }
.pf-switch-knob { position: absolute; top: 3px; left: 3px; width: 20px; height: 20px; border-radius: 50%; background: #f4ede2; transition: transform 120ms ease; }
.pf-switch.on .pf-switch-knob { transform: translateX(20px); }
.pf-settings-actions { display: flex; align-items: center; gap: 12px; margin-top: 8px; }
.pf-settings-saved { color: #8fe09a; font: 600 14px "Barlow"; }
.pf-settings-hint { color: var(--text-dim); font: 500 13px "Barlow"; line-height: 1.6; }
```

- [ ] **Step 7: Typecheck + lint + test** — `bunx tsc -b && bun run lint && bun run test` → exit 0.

- [ ] **Step 8: Commit**

```bash
cd web-react && git add src/settings/delta.ts src/settings/delta.test.ts src/settings/fields src/settings/settings.css
git commit -m "feat(web-react): settings form primitives + delta helper"
```

---

## Task 6: GeneralTab (Grill Name + Theme) — plain write

**Files:** Create/replace `web-react/src/settings/tabs/GeneralTab.tsx`

**Interfaces:**
- Consumes: `useOutletContext<{ settings: Settings }>` (Task 4), `useSaveSettings` (Task 3), `setPath` (Task 5), `Section`/`TextField`/`Select`.
- Produces: a General tab saving `globals.grill_name` + `globals.page_theme` via `save(delta, [])` (no flags).

- [ ] **Step 1: Implement GeneralTab**

```tsx
import { useState } from "react";
import { useOutletContext } from "react-router";
import type { Settings } from "../settingsApi";
import { useSaveSettings } from "../useSaveSettings";
import { setPath } from "../delta";
import { Section } from "../fields/Section";
import { TextField } from "../fields/TextField";
import { Select } from "../fields/Select";

const THEMES = [
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];

export function GeneralTab() {
  const { settings } = useOutletContext<{ settings: Settings }>();
  const { save, saving } = useSaveSettings();
  const [name, setName] = useState<string>(settings.globals?.grill_name ?? "");
  const [theme, setTheme] = useState<string>(settings.globals?.page_theme ?? "light");
  const [saved, setSaved] = useState(false);

  // Re-sync from the loader on revalidation via render-phase adjustment — the
  // repo house style (Task 1's Dashboard cook-timer). NOT a useEffect: the React
  // Compiler lint rule `react-hooks/set-state-in-effect` rejects setState-in-effect.
  const [prevSettings, setPrevSettings] = useState(settings);
  if (settings !== prevSettings) {
    setPrevSettings(settings);
    setName(settings.globals?.grill_name ?? "");
    setTheme(settings.globals?.page_theme ?? "light");
  }

  const onSave = async () => {
    let delta = setPath({}, "globals.grill_name", name);
    delta = setPath(delta, "globals.page_theme", theme);
    setSaved(await save(delta, [])); // display-only: no control flag
  };

  return (
    <Section title="General">
      <TextField label="Grill Name" value={name} onChange={setName} />
      <Select label="Theme" value={theme} options={THEMES} onChange={setTheme} />
      <div className="pf-settings-actions">
        <button className="pf-modal-btn accent" disabled={saving} onClick={onSave}>{saving ? "Saving…" : "Save"}</button>
        {saved && <span className="pf-settings-saved">Saved ✓</span>}
      </div>
    </Section>
  );
}
```

> Verify `globals.page_theme`'s real value domain against `common/defaults.py`; adjust `THEMES` option values if they differ from `light`/`dark`.

- [ ] **Step 2: Typecheck + lint + build** — `bunx tsc -b && bun run lint && bun run build` → exit 0.

- [ ] **Step 3: Manual smoke** (backend running + `bun run dev`): `/settings/general` → change grill name → Save → "Saved ✓"; the value persists on reload (loader revalidated); dashboard header shows the new `grillName` on its next socket frame.

- [ ] **Step 4: Commit**

```bash
cd web-react && git add src/settings/tabs/GeneralTab.tsx
git commit -m "feat(web-react): settings General tab (grill name + theme)"
```

---

## Task 7: PwmTab — flags-bearing write (`settings_update`)

**Files:** Create/replace `web-react/src/settings/tabs/PwmTab.tsx`

**Interfaces:** Consumes `useOutletContext`, `useSaveSettings`, `setPath`, form primitives. Produces a PWM tab saving scalar `pwm.*` fields with `save(delta, ["settings_update"])`.

- [ ] **Step 1: Implement PwmTab**

```tsx
import { useState } from "react";
import { useOutletContext } from "react-router";
import type { Settings } from "../settingsApi";
import { useSaveSettings } from "../useSaveSettings";
import { setPath } from "../delta";
import { Section } from "../fields/Section";
import { Toggle } from "../fields/Toggle";
import { NumberField } from "../fields/NumberField";

type Pwm = { pwm_control: boolean; update_time: number; min_duty_cycle: number; max_duty_cycle: number; frequency: number };

function readPwm(settings: Settings): Pwm {
  const p = settings.pwm ?? {};
  return {
    pwm_control: !!p.pwm_control, update_time: p.update_time ?? 10,
    min_duty_cycle: p.min_duty_cycle ?? 20, max_duty_cycle: p.max_duty_cycle ?? 100, frequency: p.frequency ?? 100,
  };
}

export function PwmTab() {
  const { settings } = useOutletContext<{ settings: Settings }>();
  const { save, saving } = useSaveSettings();
  const [pwm, setPwm] = useState<Pwm>(() => readPwm(settings));
  const [saved, setSaved] = useState(false);

  // Render-phase re-sync on revalidation (house style; NOT useEffect — the React
  // Compiler lint rule rejects setState-in-effect).
  const [prevSettings, setPrevSettings] = useState(settings);
  if (settings !== prevSettings) {
    setPrevSettings(settings);
    setPwm(readPwm(settings));
  }

  const set = <K extends keyof Pwm>(k: K, v: Pwm[K]) => setPwm((s) => ({ ...s, [k]: v }));

  const onSave = async () => {
    let d: object = {};
    for (const [k, v] of Object.entries(pwm)) d = setPath(d, `pwm.${k}`, v);
    setSaved(await save(d, ["settings_update"])); // control loop must re-read pwm
  };

  return (
    <Section title="PWM Fan">
      <Toggle label="PWM Control" checked={pwm.pwm_control} onChange={(v) => set("pwm_control", v)} />
      <NumberField label="Update Time" value={pwm.update_time} onChange={(v) => set("update_time", v)} min={1} suffix="s" />
      <NumberField label="Min Duty Cycle" value={pwm.min_duty_cycle} onChange={(v) => set("min_duty_cycle", v)} min={0} max={100} suffix="%" />
      <NumberField label="Max Duty Cycle" value={pwm.max_duty_cycle} onChange={(v) => set("max_duty_cycle", v)} min={0} max={100} suffix="%" />
      <NumberField label="Frequency" value={pwm.frequency} onChange={(v) => set("frequency", v)} min={1} suffix="Hz" />
      <div className="pf-settings-actions">
        <button className="pf-modal-btn accent" disabled={saving} onClick={onSave}>{saving ? "Saving…" : "Save"}</button>
        {saved && <span className="pf-settings-saved">Saved ✓</span>}
      </div>
    </Section>
  );
}
```

- [ ] **Step 2: Typecheck + lint + build** — exit 0.

- [ ] **Step 3: Manual smoke** — `/settings/pwm`: change Update Time → Save → "Saved ✓"; reload persists; `curl -s localhost:5000/api/settings | python3 -c "import sys,json;print(json.load(sys.stdin)['settings']['pwm']['update_time'])"` shows the new value.

- [ ] **Step 4: Commit**

```bash
cd web-react && git add src/settings/tabs/PwmTab.tsx
git commit -m "feat(web-react): settings PWM tab (settings_update flag path)"
```

---

## Task 8: UnitsTab — command path + confirm gate

**Files:** Create/replace `web-react/src/settings/tabs/UnitsTab.tsx`; modify `web-react/src/command.ts`

**Interfaces:** Consumes `useOutletContext`, `useRevalidator`, `createCommand`/`CommandClient` (phase 1), `ConfirmAction` (phase 1). Produces `CommandClient.setUnits(units: "F"|"C")` (`/api/set/units/{F|C}`); a Units tab that confirms (grill will STOP) then calls it and revalidates.

- [ ] **Step 1: Add `setUnits` to `command.ts`**

In `interface CommandClient` add `setUnits(units: "F" | "C"): Promise<CommandResult>;`; in `createCommand`'s returned object add `setUnits: (units) => post(baseUrl, ["set", "units", units]),`. Add a case to `src/command.test.ts`:

```ts
it("setUnits → /api/set/units/{F|C}", async () => {
  await createCommand("").setUnits("C");
  expect(url()).toBe("/api/set/units/C");
});
```

- [ ] **Step 2: Run the command test** — `bun run test src/command.test.ts` → PASS (9 cases).

- [ ] **Step 3: Implement UnitsTab**

```tsx
import { useState } from "react";
import { useOutletContext, useRevalidator } from "react-router";
import type { Settings } from "../settingsApi";
import { createCommand } from "../../command";
import { ConfirmAction } from "../../dashboard/ConfirmAction";
import { Section } from "../fields/Section";
import { Select } from "../fields/Select";

const BASE_URL = import.meta.env.VITE_PIFIRE_URL || "";
const UNIT_OPTIONS = [
  { value: "F", label: "Fahrenheit (°F)" },
  { value: "C", label: "Celsius (°C)" },
];

export function UnitsTab() {
  const { settings } = useOutletContext<{ settings: Settings }>();
  const revalidator = useRevalidator();
  const [units, setUnits] = useState<"F" | "C">(settings.globals?.units === "C" ? "C" : "F");
  const [pending, setPending] = useState<"F" | "C" | null>(null);

  // Render-phase re-sync on revalidation (house style; NOT useEffect).
  const [prevSettings, setPrevSettings] = useState(settings);
  if (settings !== prevSettings) {
    setPrevSettings(settings);
    setUnits(settings.globals?.units === "C" ? "C" : "F");
  }

  const onChange = (v: string) => {
    const next = v === "C" ? "C" : "F";
    if (next !== units) setPending(next); // changing units stops the grill
  };

  const confirmChange = async () => {
    const next = pending!;
    setPending(null);
    await createCommand(BASE_URL).setUnits(next);
    setUnits(next);
    revalidator.revalidate();
  };

  return (
    <>
      <Section title="Units">
        <Select label="Temperature Units" value={units} options={UNIT_OPTIONS} onChange={onChange} />
        <p className="pf-settings-hint">Changing units converts all stored temperatures and <b>stops the grill</b>.</p>
      </Section>
      <ConfirmAction open={pending !== null} title={`Switch to °${pending ?? ""}? This will stop the grill.`}
        onCancel={() => setPending(null)} onConfirm={confirmChange} />
    </>
  );
}
```

- [ ] **Step 4: Typecheck + lint + test + build** — `bunx tsc -b && bun run lint && bun run test && bun run build` → exit 0.

- [ ] **Step 5: Commit**

```bash
cd web-react && git add src/command.ts src/command.test.ts src/settings/tabs/UnitsTab.tsx
git commit -m "feat(web-react): settings Units tab (command path + confirm gate)"
```

---

## Task 9: Settings round-trip e2e

**Files:** Create `web-react/tests/e2e/settings.spec.ts`

**Interfaces:** Consumes the running prototype backend + `bun run dev` (phase-1 `playwright.config.ts`). Produces an e2e proving the three write paths round-trip.

- [ ] **Step 1: Write the e2e**

```ts
import { test, expect } from "@playwright/test";

// Requires the prototype backend running (control.py + gunicorn on :5000).
test("grill name saves and round-trips to the dashboard header", async ({ page }) => {
  await page.goto("/settings/general");
  const name = "E2E Grill " + Date.now().toString().slice(-4);
  await page.getByLabel("Grill Name").fill(name);
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Saved ✓")).toBeVisible({ timeout: 10000 });
  await page.reload();
  await expect(page.getByLabel("Grill Name")).toHaveValue(name);
  await page.goto("/");
  await expect(page.getByText(name)).toBeVisible({ timeout: 15000 });
});

test("PWM update-time saves via the settings_update path", async ({ page }) => {
  await page.goto("/settings/pwm");
  await page.getByLabel("Update Time").fill("9");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Saved ✓")).toBeVisible({ timeout: 10000 });
  await page.reload();
  await expect(page.getByLabel("Update Time")).toHaveValue("9");
});

test("units change is gated by a confirm and applies", async ({ page }) => {
  await page.goto("/settings/units");
  await page.getByLabel("Temperature Units").selectOption("C");
  await expect(page.getByText(/stop the grill/i)).toBeVisible();
  await page.getByRole("button", { name: "Confirm" }).click();
  await page.reload();
  await expect(page.getByLabel("Temperature Units")).toHaveValue("C");
  // reset to F for rerun idempotency
  await page.getByLabel("Temperature Units").selectOption("F");
  await page.getByRole("button", { name: "Confirm" }).click();
});
```

- [ ] **Step 2: Run the e2e (backend up)**

Run (in `web-react/`): `bun run test:e2e tests/e2e/settings.spec.ts` → 3 passed. If the grill-name→dashboard assertion flakes on socket timing, raise only that timeout (do not weaken the assertion). A genuine failure to round-trip is a real finding — report it with the network evidence.

- [ ] **Step 3: Full gate** — `bunx tsc -b && bun run lint && bun run test && bun run build` → all exit 0.

- [ ] **Step 4: Commit**

```bash
cd web-react && git add tests/e2e/settings.spec.ts
git commit -m "test(web-react): settings round-trip e2e (name/pwm/units)"
```

---

## Self-Review notes (already reconciled)

- **Hybrid transports:** dashboard stays on the socket (Task 4 `DashboardRoute` renders the unchanged phase-1 `Dashboard`); settings load via a route **loader** (T3 `settingsLoader`, T4 wiring) and read via `useOutletContext` (T6–T8); saves go through `applySettings` + `useRevalidator` (T3 `useSaveSettings`). This is the "routes for load-on-nav, socket for stream" split.
- **Spec coverage:** data-router shell + responsive settings + loader/errorElement (T4), settings API + loader + save (T3), the one backend endpoint (T2), form primitives (T5), three tabs proving all three write paths — plain (T6), flags (T7), command+confirm (T8) — ESLint react-hooks+compiler (T1), backend pytest (T2) + e2e (T9). PWM profiles table + other tabs are Non-Goals (2b).
- **Type consistency:** `Settings`/`SettingsFlag`/`applySettings`/`getSettings` (T3) used in T4/T6–T8; `settingsLoader` return `{ settings }` (T3) matches `SettingsShell` `useLoaderData` + `Outlet context` and the tabs' `useOutletContext<{ settings: Settings }>` (T4/T6–T8); `useSaveSettings` (T3) consumed by T6/T7; `CommandClient.setUnits` (T8) matches phase-1 `command.ts` patterns; `ConfirmAction` reused with its phase-1 prop shape; `useAppPrefs` (T4) consumed by `DashboardRoute`.
- **Backend envelope:** `_api_post_settings_update` returns `{result:"success"}`; `applySettings` checks `result === "success"` — consistent.
- **Render side-effect:** the accent `setAttribute` lives in `AppPrefs`'s `useEffect` (T4), satisfying the React Compiler lint rule (T1).
- **Deferred (not this slice):** production Flask must serve `index.html` for `/settings/*` deep links (SPA catch-all) — belongs to the later app-into-Flask integration, not phase 2a (dev/e2e run against the vite dev server which handles it).
```
