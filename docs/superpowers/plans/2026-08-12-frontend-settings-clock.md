# Frontend Settings and Clock Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give settings retrieval, settings-tab identity, and wall-clock updates one typed owner each while preserving routing, drafts, cache invalidation, and dashboard behavior.

**Architecture:** TanStack Query keys are parameterized by normalized API base and `useSettings(baseUrl)` becomes the sole settings query. A typed tab manifest owns navigation identity while route component bindings remain exhaustively mapped in `appRoutes.tsx`. Dashboard uses the existing `useNow` external store and deletes its private interval.

**Tech Stack:** React 19, TypeScript, React Router, TanStack Query, Bun, Rstest, TypeScript LSP.

## Global Constraints

- Preserve current settings query stale time, route loader error behavior, save invalidation, first-time wizard redirect, and A→B→A API-base fencing.
- Preserve tab order, labels, index redirect, probes-only loader, and hidden-but-addressable PWM route.
- Preserve drafts across tab switches and unsaved indicators. Raw draft keys must not survive.
- Preserve whole-second dashboard clock/cook timer behavior.
- Use TypeScript LSP references and rename support for exported key/hook/type changes.

---

### Task 1: Characterize Settings Cache Ownership and Base Changes

**Files:**
- Modify: `web-react/tests/unit/helpers/settings/useSettings.test.tsx`
- Modify: `web-react/tests/unit/components/dashboard/Dashboard.test.tsx`
- Modify: `web-react/tests/unit/components/DashboardRoute.test.tsx`
- Modify: settings loader tests under `web-react/tests/unit/helpers/settings/`

**Interfaces:**
- Produces failing tests for `useSettings(baseUrl?: string)` and base-aware query keys.

- [ ] **Step 1: Use LSP references for `useSettings`, `queryKeys.settings`, `settingsRoot`, `mode`, and `controllerMetadata`**

Record all invalidators and route loaders before changing key shapes.

- [ ] **Step 2: Add query-key and transport tests**

Assert that `/a` and `/b` produce distinct settings cache entries, switching A→B shows no stale A-derived MPC configuration, switching back to A may reuse A's own valid cache, and invalidating A does not invalidate B.

- [ ] **Step 3: Add Dashboard authority tests**

Assert Dashboard issues no second settings request when the shared query is primed; ambient/controller values update from the shared result; failed reads retain fail-closed learning defaults. In `DashboardRoute.test.tsx`, assert the route reuses the base-aware primed settings query and preserves its advisory fail-quiet gate.

- [ ] **Step 4: Run focused tests and confirm end-state tests fail**

From `web-react/`, run:

```bash
bun run test -- \
  tests/unit/helpers/settings/useSettings.test.tsx \
  tests/unit/helpers/settings/settingsRoutes.test.ts \
  tests/unit/components/dashboard/Dashboard.test.tsx \
  tests/unit/components/DashboardRoute.test.tsx
```

Expected: new base-isolation/one-request assertions fail.

- [ ] **Step 5: Commit tests**

Describe: `test(web): define shared settings query ownership`.

---

### Task 2: Make Query Keys Base-Aware

**Files:**
- Modify: `web-react/src/helpers/query/keys.ts`
- Modify: `web-react/src/helpers/settings/useSettings.ts`
- Modify: `web-react/src/helpers/settings/settingsRoutes.ts`
- Modify: `web-react/src/helpers/settings/useSaveSettings.ts`
- Modify every LSP-reported settings key caller

**Interfaces:**
- Produces:

```ts
const normalizeApiBase = (baseUrl: string) => baseUrl.replace(/\/$/, "");
queryKeys.settingsRoot(baseUrl: string)
queryKeys.settings(baseUrl: string)
queryKeys.mode(baseUrl: string)
queryKeys.controllerMetadata(baseUrl: string)
useSettings(baseUrl?: string)
```

The top-level `queryKeys.allSettings = ["settings"]` may exist only for deliberate all-origin cache reset; ordinary save invalidation uses `settingsRoot(currentBase)`.

- [ ] **Step 1: Implement key factories and optional-base hook**

Use the configured `PUBLIC_PIFIRE_URL` only as the default argument. The query function and key must use the same normalized value.

- [ ] **Step 2: Migrate loader and save invalidation**

`settingsLoader` passes its exact configured base to all three factories. `useSaveSettings` invalidates only the base used by its write.

- [ ] **Step 3: Migrate all LSP references**

No literal `['settings', ...]` key may remain outside `keys.ts`. Keep history/metrics keys unchanged.

- [ ] **Step 4: Run Task 1 tests and TypeScript diagnostics**

Expected: key/base tests pass; no type errors.

- [ ] **Step 5: Commit**

Describe: `refactor(web): key settings queries by API base`.

---

### Task 3: Remove Dashboard's Second Settings Transport

**Files:**
- Modify: `web-react/src/components/dashboard/Dashboard.tsx:26-136,419-428`
- Modify: `web-react/src/components/DashboardRoute.tsx`
- Modify: `web-react/tests/unit/components/dashboard/Dashboard.test.tsx`

**Interfaces:**
- Dashboard consumes `useSettings(apiBase)` directly.
- Derived configuration is a pure value:

```ts
const mpcConfig = {
  selectedController: settings?.controller?.selected ?? "",
  ambientC: readConfiguredAmbientC(settings) ?? DEFAULT_MPC_AMBIENT_C,
};
```

Use the actual existing settings accessors/shape identified by LSP; do not duplicate parser logic.

- [ ] **Step 1: Delete mirrored request state**

Remove `useQueryClient`, request identity memo, `loadedMpcConfig`, cancellation effect, and direct `fetchQuery` from Dashboard.

- [ ] **Step 2: Derive learning inputs from the shared query**

During a base transition, use defaults until the new base's query resolves. Do not retain the previous base's values in component state.

- [ ] **Step 3: Run Dashboard and query tests**

Expected: one settings authority, race/failure behavior preserved.

- [ ] **Step 4: Commit**

Describe: `refactor(web): share dashboard settings state`.

---

### Task 4: Define the Typed Settings Tab Manifest

**Files:**
- Create: `web-react/src/helpers/settings/settingsTabs.ts`
- Create/modify: `web-react/tests/unit/helpers/settings/settingsTabs.test.ts`

**Interfaces:**
- Produces:

```ts
export const SETTINGS_TABS = [
  { id: "general", label: "General", editable: true, hideWithoutDcFan: false },
  // exact current order ...
  { id: "probes", label: "Probes", editable: true, hideWithoutDcFan: false },
] as const;
export type SettingsTabId = (typeof SETTINGS_TABS)[number]["id"];
export type EditableSettingsTabId = Extract<...>;
```

Use a type derivation that makes `editable: true` IDs available without a handwritten second union.

- [ ] **Step 1: Add manifest tests**

Assert exact order/labels, one PWM visibility flag, all IDs unique, and probes remains last.

- [ ] **Step 2: Implement metadata only**

Do not import React components or route loaders into the manifest. Keep component bindings in `appRoutes.tsx`.

- [ ] **Step 3: Run tests and LSP diagnostics**

Expected: PASS with readonly literal ID types.

- [ ] **Step 4: Commit**

Describe: `refactor(web): define typed settings tab identities`.

---

### Task 5: Type Draft Keys and Navigation

**Files:**
- Modify: `web-react/src/helpers/settings/settingsDrafts.ts`
- Modify: `web-react/src/components/settings/SettingsShell.tsx`
- Modify ten editable tab components
- Modify: `web-react/tests/unit/components/settings/settingsDrafts.test.tsx`
- Modify: `web-react/tests/unit/components/settings/SettingsShell.test.tsx`

**Interfaces:**
- `useSettingsDraft<T>(key: EditableSettingsTabId, read: ...)`.
- `SettingsDrafts = Partial<Record<EditableSettingsTabId, SettingsDraft<unknown>>>`.

- [ ] **Step 1: Change draft APIs to the editable ID union**

Keep the generic return type from the reader. Limit the internal `unknown` cast to the store boundary; callers receive `T` as today.

- [ ] **Step 2: Render navigation from the manifest**

Filter PWM through metadata plus `hasDcFan(settings)`. Unsaved markers index drafts only for editable tabs; noneditable tabs have no draft lookup.

- [ ] **Step 3: Migrate every raw draft key with LSP**

Expected callers: General, Work Mode, Controller, PWM, Startup, Safety, Pellets, History, Notifications, and Probes. No arbitrary string call remains.

- [ ] **Step 4: Run settings shell/draft/tab tests**

Expected: drafts survive navigation and PWM behavior is unchanged.

- [ ] **Step 5: Commit**

Describe: `refactor(web): type settings drafts by tab`.

---

### Task 6: Make Settings Routes Exhaustive

**Files:**
- Modify: `web-react/src/components/appRoutes.tsx:101-130`
- Modify route tests

**Interfaces:**
- Define `const settingsRoutes: Record<SettingsTabId, RouteObject>` or an equivalently exhaustive `satisfies` mapping.
- The probes entry alone includes `loader: probeModulesLoader`.

- [ ] **Step 1: Add failing exhaustiveness/runtime tests**

Assert every manifest ID has one child route, no extra route exists, index redirects to `general`, and probes/PWM special behavior remains.

- [ ] **Step 2: Build child routes from ordered manifest IDs plus typed bindings**

Do not put component constructors in `settingsTabs.ts`. Use `SETTINGS_TABS.map(({id}) => settingsRoutes[id])` so navigation and routes share order/identity.

- [ ] **Step 3: Run route and settings tests**

Expected: PASS and no missing binding compile error.

- [ ] **Step 4: Commit**

Describe: `refactor(web): derive settings routes from the tab manifest`.

---

### Task 7: Delete the Dashboard-Only Clock

**Files:**
- Modify: `web-react/src/helpers/dashboard/hooks.ts:1-14`
- Modify: `web-react/src/components/dashboard/Dashboard.tsx`
- Modify: `web-react/tests/unit/helpers/clock.test.ts`
- Modify dashboard tests and `web-react/tests/e2e/layoutBaseline.ts` comment

**Interfaces:**
- Dashboard calls `useNow(true)` from `helpers/clock.ts`.
- It derives `const now = new Date(nowSeconds * 1000)` only for localized HH:MM formatting.

- [ ] **Step 1: Add a Dashboard/shared-clock test**

Using fake timers, assert Dashboard subscribes to the module-level store and renders clock/cook elapsed changes on the same whole-second tick as `TimerBar`.

- [ ] **Step 2: Replace `useClock()` and delete it**

Retain `useFitScale` in `dashboard/hooks.ts`. Remove now-unused React imports.

- [ ] **Step 3: Use LSP references to verify `useClock` has zero references**

Delete stale comments naming two clocks.

- [ ] **Step 4: Run clock and Dashboard tests**

Expected: PASS with no extra interval after unmount.

- [ ] **Step 5: Commit**

Describe: `refactor(web): use one shared wall clock`.

---

### Task 8: Frontend Aggregate Gate

**Files:**
- No production changes unless verification finds a defect.

- [ ] **Step 1: Run all settings, dashboard, and clock unit tests**

From `web-react/`, run:

```bash
bun run test -- \
  tests/unit/helpers/settings \
  tests/unit/components/settings \
  tests/unit/components/dashboard/Dashboard.test.tsx \
  tests/unit/components/DashboardRoute.test.tsx \
  tests/unit/helpers/clock.test.ts
```

- [ ] **Step 2: Run TypeScript LSP workspace diagnostics**

Expected: no introduced errors.

- [ ] **Step 3: Run frontend lint and production build**

From `web-react/`, run the existing `bun run lint` and `bun run build` scripts. Record pre-existing lint failures separately; no new failure is accepted.

- [ ] **Step 4: Inspect query behavior**

Run the Dashboard/settings UI path against the development API or existing browser fixture. Verify one settings request per base, working tab navigation, unsaved marker, probes loader, hidden PWM pill, and ticking dashboard clock.

- [ ] **Step 5: Commit any test-only verification adjustments as a separate change**

No production cleanup should be bundled here.
