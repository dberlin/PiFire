# TanStack Query for the web-react REST Plane — Implementation Plan

> **For the implementing engineer:** execute this plan with `skill://executing-plans` or `skill://subagent-driven-development`. Use `skill://test-driven-development` for each behavioral task and `skill://verification-before-completion` before publishing. This repo is Jujutsu — use `jj`, never raw `git` (`skill://jujutsu`).

**Goal:** Put the app's REST reads behind one TanStack Query cache so the settings blob is fetched once instead of five times, and so hand-rolled loading/error/cancellation/refetch machinery is deleted rather than maintained. The socket.io live plane is untouched.

**Architecture:** The app has two data planes. The **push plane** (`src/helpers/useLiveState.ts`) owns one socket.io connection on `AppShell` and distributes dash + pellet data through Outlet context — react-query has nothing to offer a server-push stream and must not be introduced there. The **pull plane** is every `fetch` behind a `src/helpers/**/‌*Api.ts` module, currently re-implemented per component as `useState(data/loading/error)` + `useEffect` + a `cancelled` flag. This plan moves the pull plane onto a single `QueryClient`, keeps react-router's three loaders as the owners of route-blocking data (priming the same cache via `fetchQuery`, not `ensureQueryData` — see Testing notes below), and leaves the API modules' own error conventions alone by converting only at the query boundary.

**Tech stack:** React 19.2, TypeScript 5.9 (strict), react-router 8.3, `@tanstack/react-query` 5.101.4, Rsbuild 2.1 with React Compiler, Rstest 0.11, Biome 2.5.5 + ESLint 10, Bun.

## Testing notes

Two timer traps bit this plan's tests during execution. Both are about fake
timers interacting with react-query, and both produce a test that PASSES
against a deliberately broken implementation — a false negative, not a false
positive — which is the worse failure mode for a test to have.

**Trap 1 — the synchronous `advanceTimersByTime` never lets react-query
re-arm.** `await act(() => rs.advanceTimersByTime(ms))` advances the fake
clock and flushes React's synchronous work, but it does not await anything,
so it never yields the microtask queue react-query needs to settle an
in-flight fetch and re-arm `refetchInterval` for the next tick. A query whose
interval is genuinely broken (wired to a real cadence instead of `false`, or
vice versa) can pass this assertion anyway, because the observer never got
the chance to prove it either way. Use the async form instead:
`await act(async () => { await rs.advanceTimersByTimeAsync(ms); });`.

**Trap 2 — one `advanceTimersByTimeAsync` call is still not always enough.**
react-query settles a refetch in two hops, confirmed at the source:
`node_modules/@tanstack/query-core/build/modern/notifyManager.cjs:29` sets
`defaultScheduler = systemSetTimeoutZero`, which
`node_modules/@tanstack/query-core/build/modern/timeoutManager.cjs:89-91`
defines as literally `setTimeout(callback, 0)` — a real, fake-timer-visible
MACROTASK between "the query data settles" and "the observer re-renders".
`advanceTimersByTimeAsync(ms)` stopping exactly on a poll boundary flushes the
fetch's own microtasks but leaves that `setTimeout(0)` hop still pending, so
anything downstream of the new data (a `useEffect` reading it, a `reload()`
it calls) has not run yet. The clock needs one more nudge to drain it. The
shipped pattern is the `tick()` / `settle()` helper pair in
`tests/unit/helpers/useWebUiBuild.test.tsx`: `tick(ms)` advances the clock by
the interval and `settle()` advances it by one more virtual millisecond
purely to let the pending `setTimeout(0)` fire, kept as a separate call so a
call site's intent ("advance the poll clock" vs. "let the pending render
flush") stays readable instead of hiding in an off-by-one.

**Trap 3 (not a timer trap, but adjacent) — `ensureQueryData` is not
`fetchQuery`.** `ensureQueryData` returns whatever is cached once data
exists, full stop — it never consults `isInvalidated` or `staleTime`. A
loader that primes its cache with `ensureQueryData` after an `invalidateQueries`
call will keep serving the pre-invalidation value. `fetchQuery` calls
`query.isStaleByTime()`, which does check both, so an invalidated or expired
entry is actually refetched. See Task 3's `settingsLoader` for the corrected
call and the comment explaining why.

**A related, separate defect:** `@testing-library/react`'s `waitFor` does not
work under `rs.useFakeTimers()` — it polls on a real timer that fake timers
freeze, so it times out at 5000ms rather than observing an update. Any test
combining fake timers with a poll assertion needs the explicit
`tick()`/`settle()` pattern above, not `waitFor`.

## Global constraints

- **Bun, never npm.** `bun add`, `bun run test`, `bun run typecheck`, `bun run lint`.
- **Do not touch the push plane.** `src/helpers/useLiveState.ts`, `src/helpers/shellContext.ts`, and `src/components/shell/AppShell.tsx` are out of scope. Dash and pellet data arrive by socket and need no cache.
- **Do not remove the three route loaders.** `settingsLoader`, `probeModulesLoader`, `wizardLoader` stay as loaders; they gain a cache, they do not lose route-blocking behavior or their `errorElement` paths.
- **API modules keep their existing error conventions.** Some throw (`getSettings`, `helpers/files/apiEnvelope.ts`), some resolve a `{ok, status, message, data}` envelope (`AdminResult`, `MetricsResult`, `UpdateResult`). Write paths and their tests branch on those envelopes directly. Convert at the query boundary only, via `unwrap()`.
- **No `setState` inside `useEffect`.** The React Compiler is on (`rsbuild.config.ts:39`) and `react-hooks/set-state-in-effect` rejects it. Use the render-phase adjustment idiom already documented at `src/helpers/settings/settingsDrafts.ts:56-66`. Setting state during render is legal only for a component's **own** state.
- **Coverage gate:** `rstest.config.ts` enforces `lines: 75, perFile: true` across `src/**/*.{ts,tsx}`. Every new file needs its own tests.
- **Each task is one Jujutsu revision**, started by Step 1 and left as the working copy. Do not publish until the final verification task is green.
- Every task ends with `bun run typecheck && bun run lint && bun run test` green before moving on.

## Scope

**In scope** — the structurally distinct conversions, all verified against the source:

| What | Where |
|---|---|
| 5 uncoordinated `getSettings` reads | `settingsRoutes.ts:18`, `accent.ts:41`, `DashboardRoute.tsx:30`, `HistoryPage.tsx:101`, `TunerPage.tsx:77` |
| Chart read + `requestId` nonce + auto-refresh poll | `HistoryPage.tsx:144-148` |
| `requestId` nonces | `CookFilePage.tsx`, `CookFileChart.tsx`, `RecipePage.tsx` |
| Plain mount reads | `MetricsPage.tsx:24-43`, `AdminPage.tsx` |
| Plain read poll | `useWebUiBuild.ts:41-68` |

**Deliberately deferred to a follow-up plan** (Task 14 records this):

- **Progress/stream state machines** — `UpdatePage.tsx:91-106`, `wizard/InstallProgress.tsx:49`, `TunerPage.tsx:96-120`, `logs/StreamingLogPanel.tsx:97`, `logs/LogViewer.tsx:68`. These poll toward a *terminal condition* and fire side effects on specific transitions (`setDone`, `clearInterval` from inside the callback, reload-the-state-the-run-changed). `useQuery` models a cache entry, not a run; converting them trades clear code for a `refetchInterval` callback plus the same state machine. Low value, real risk.
- **The remaining ~20 plain mount-fetch components** (`EventsPage`, `PelletsPage` sub-reads, `RecipeList`, `CookFileList`, the `wizard/probes/*` pickers, `admin/*Card`, `pellets/VocabTable`, `cookfiles/MediaPanel`, `cookfiles/CommentList`, `recipes/*Editor`). Mechanical once Task 9 establishes the recipe; not worth 20 near-identical tasks in this document.

---

## Task 1: Install TanStack Query and stand up the client

**Files:**

- Modify: `web-react/package.json`
- Create: `web-react/src/helpers/query/queryClient.ts`
- Create: `web-react/src/helpers/query/keys.ts`
- Modify: `web-react/src/components/App.tsx:153-163`
- Modify: `web-react/tests/unit/test-utils.tsx`
- Create: `web-react/tests/unit/helpers/query/queryClient.test.ts`

**Interfaces:**

- Produces: `createQueryClient(): QueryClient` and the module singleton `queryClient` from `src/helpers/query/queryClient.ts`; `queryKeys` from `src/helpers/query/keys.ts`; `testQueryClient()` and `renderWithQuery(ui)` from `tests/unit/test-utils.tsx`. Every later task consumes these.

### Steps

- [ ] **Step 1: Start the revision**

```bash
jj new -m "feat(web-react): add TanStack Query client and key registry"
```

- [ ] **Step 2: Add the dependency**

```bash
cd web-react && bun add @tanstack/react-query@5.101.4
```

- [ ] **Step 3: Write the failing client test**

Create `web-react/tests/unit/helpers/query/queryClient.test.ts`:

```ts
import { describe, expect, it } from "@rstest/core";
import { createQueryClient } from "../../../../src/helpers/query/queryClient";

describe("createQueryClient", () => {
  it("does not retry: a failed read is rendered in place, not silently re-attempted", () => {
    const defaults = createQueryClient().getDefaultOptions().queries;
    expect(defaults?.retry).toBe(false);
  });

  it("does not refetch on window focus: the live plane is socket-push, not polled", () => {
    const defaults = createQueryClient().getDefaultOptions().queries;
    expect(defaults?.refetchOnWindowFocus).toBe(false);
  });

  it("holds a read fresh long enough for sibling pages to share it", () => {
    const defaults = createQueryClient().getDefaultOptions().queries;
    expect(defaults?.staleTime).toBe(30_000);
  });
});
```

- [ ] **Step 4: Run it and watch it fail**

```bash
cd web-react && bun run test tests/unit/helpers/query/queryClient.test.ts
```

Expected: FAIL — cannot resolve `src/helpers/query/queryClient`.

- [ ] **Step 5: Create the client**

Create `web-react/src/helpers/query/queryClient.ts`:

```ts
import { QueryClient } from "@tanstack/react-query";

/**
 * Defaults for an appliance UI on a LAN, not for a public web app.
 *
 * retry: false -- every page this cache serves renders its own failure in
 *   place with a retry affordance the user can see. A silent retry only
 *   delays that, and it makes a test's mock call count non-deterministic.
 *
 * refetchOnWindowFocus: false -- the plane that changes on its own is the
 *   socket (helpers/useLiveState.ts). Everything behind a query key here is
 *   configuration, which changes only through a write this app makes and
 *   then invalidates explicitly.
 *
 * staleTime: 30s -- the point of the exercise. Five call sites read the whole
 *   settings blob; within one navigation they should share one response.
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false, staleTime: 30_000 },
    },
  });
}

/**
 * The app's single client.
 *
 * Exported as a value rather than built inside a component because
 * settingsLoader runs OUTSIDE React -- App.tsx wires it as a route loader, and
 * react-router calls it before any provider exists -- and it has to prime the
 * same cache the components later read.
 */
export const queryClient = createQueryClient();
```

- [ ] **Step 6: Create the key registry**

Create `web-react/src/helpers/query/keys.ts`:

```ts
/**
 * Every query key in the app, in one place.
 *
 * Invalidation is the reason. useSaveSettings has to name the settings entry
 * from a module that knows nothing about the pages reading it, and a key
 * spelled inline in two files is a cache that has silently split in two.
 *
 * The settings trio shares the `settings` prefix on purpose: react-query
 * matches keys by prefix, so invalidating `settingsRoot` reaches all three.
 * That preserves today's behaviour exactly -- revalidator.revalidate() re-runs
 * settingsLoader, which refetches settings, mode AND controller metadata.
 */
export const queryKeys = {
  settingsRoot: ["settings"] as const,
  settings: ["settings", "all"] as const,
  mode: ["settings", "mode"] as const,
  controllerMetadata: ["settings", "controller-metadata"] as const,
  metrics: ["metrics"] as const,
  webUiBuild: ["webui-build"] as const,
  adminState: ["admin", "state"] as const,
  historyChart: (minutes: number | undefined) => ["history", "chart", minutes ?? null] as const,
  /** Prefix of both cook-file entries, so one invalidate reaches detail+chart. */
  cookfileRoot: (filename: string) => ["cookfile", filename] as const,
  cookfileDetail: (filename: string) => ["cookfile", filename, "detail"] as const,
  cookfileChart: (filename: string) => ["cookfile", filename, "chart"] as const,
  recipe: (filename: string) => ["recipe", filename] as const,
} as const;
```

- [ ] **Step 7: Run the client test and watch it pass**

```bash
cd web-react && bun run test tests/unit/helpers/query/queryClient.test.ts
```

Expected: PASS, 3 tests.

- [ ] **Step 8: Wrap the app**

In `web-react/src/components/App.tsx`, add the imports and replace the `App` component body. `QueryClientProvider` goes OUTSIDE `AppPrefsProvider` because Task 4 gives `AppPrefsProvider` a query of its own:

```tsx
import { QueryClientProvider } from "@tanstack/react-query";
import { queryClient } from "../helpers/query/queryClient";
```

```tsx
export default function App() {
  // Above the router, not inside a route: an update can land while the user is
  // anywhere, including the wizard, which is the one route mounted outside
  // AppShell.
  useWebUiBuild();
  return (
    <QueryClientProvider client={queryClient}>
      <AppPrefsProvider>
        <RouterProvider router={router} />
      </AppPrefsProvider>
    </QueryClientProvider>
  );
}
```

- [ ] **Step 9: Extend the test harness**

In `web-react/tests/unit/test-utils.tsx`, add the imports:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
```

Add these exports:

```tsx
/**
 * A fresh client per render.
 *
 * Sharing one across tests leaks a resolved settings entry into the next test,
 * which then never calls its own mock and asserts against the previous test's
 * fixture. gcTime: 0 so nothing survives the unmount either, and staleTime: 0
 * so a test that expects a refetch gets one.
 */
export function testQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  });
}

/** For a component that uses queries but needs no router. */
export function renderWithQuery(ui: ReactElement) {
  return render(<QueryClientProvider client={testQueryClient()}>{ui}</QueryClientProvider>);
}
```

And wrap `renderRoute`'s existing return so routed components get a client too:

```tsx
  return render(
    <QueryClientProvider client={testQueryClient()}>
      <AppPrefsProvider>
        <RouterProvider router={router} />
      </AppPrefsProvider>
    </QueryClientProvider>,
  );
```

- [ ] **Step 10: Verify the whole suite still passes**

```bash
cd web-react && bun run typecheck && bun run lint && bun run test
```

Expected: PASS. Nothing consumes a query yet, so this task must be behavior-neutral. If `App.test.tsx` or `AppPrefs.test.tsx` fail here, they render `AppPrefsProvider` directly — wrap those renders in `renderWithQuery`.

---

## Task 2: Add the envelope-to-rejection adapter

**Files:**

- Create: `web-react/src/helpers/query/unwrap.ts`
- Create: `web-react/tests/unit/helpers/query/unwrap.test.ts`

**Interfaces:**

- Produces: `ResultEnvelope<T>`, `ApiError`, and `unwrap<T>(p: Promise<ResultEnvelope<T>>): Promise<T>`. Tasks 9 and 10 consume `unwrap`; nothing else does.

### Steps

- [ ] **Step 1: Start the revision**

```bash
jj new -m "feat(web-react): bridge result envelopes into query rejections"
```

- [ ] **Step 2: Write the failing test**

Create `web-react/tests/unit/helpers/query/unwrap.test.ts`:

```ts
import { describe, expect, it } from "@rstest/core";
import { ApiError, unwrap } from "../../../../src/helpers/query/unwrap";

describe("unwrap", () => {
  it("returns the payload when the envelope reports success", async () => {
    await expect(unwrap(Promise.resolve({ ok: true, status: 200, message: "", data: { a: 1 } }))).resolves.toEqual({ a: 1 });
  });

  it("rejects with the server's message when the envelope reports failure", async () => {
    const failing = unwrap(Promise.resolve({ ok: false, status: 503, message: "not_stopped", data: null }));
    await expect(failing).rejects.toThrow("not_stopped");
  });

  it("carries the status so a caller can branch on it", async () => {
    const err = await unwrap(
      Promise.resolve({ ok: false, status: 404, message: "not_found", data: null }),
    ).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(404);
  });

  it("rejects on ok-with-null-data, which is a broken read contract", async () => {
    await expect(
      unwrap(Promise.resolve({ ok: true, status: 200, message: "", data: null })),
    ).rejects.toBeInstanceOf(ApiError);
  });
});
```

- [ ] **Step 3: Run it and watch it fail**

```bash
cd web-react && bun run test tests/unit/helpers/query/unwrap.test.ts
```

Expected: FAIL — cannot resolve `src/helpers/query/unwrap`.

- [ ] **Step 4: Write the adapter**

Create `web-react/src/helpers/query/unwrap.ts`:

```ts
/**
 * The envelope the resolve-don't-throw API modules already share:
 * helpers/admin/adminTypes.ts AdminResult, helpers/metrics/metricsTypes.ts
 * MetricsResult, helpers/update/updateTypes.ts UpdateResult.
 */
export interface ResultEnvelope<T> {
  ok: boolean;
  /** HTTP status, or 0 when the request never reached a server. */
  status: number;
  message: string;
  data: T | null;
}

export class ApiError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/**
 * Bridge a resolve-don't-throw READ into a react-query fetcher.
 *
 * useQuery decides success or failure by whether the promise REJECTS. An
 * envelope carrying ok:false resolves, so without this a failed read would
 * land in `data` and render as a success holding null.
 *
 * The API modules keep their envelopes: write paths branch on `.ok` and
 * `.message` directly (helpers/admin/adminTypes.ts documents `message` as a
 * machine token the Python tests assert on), and only the query boundary
 * converts.
 *
 * `data === null` under `ok: true` is a broken server contract for a read,
 * which is all this is used for. Rejecting on it is what keeps
 * `useQuery().data` non-nullable for every caller.
 */
export async function unwrap<T>(p: Promise<ResultEnvelope<T>>): Promise<T> {
  const r = await p;
  if (!r.ok || r.data === null) throw new ApiError(r.message, r.status);
  return r.data;
}
```

- [ ] **Step 5: Run the test and watch it pass**

```bash
cd web-react && bun run test tests/unit/helpers/query/unwrap.test.ts
```

Expected: PASS, 4 tests.

- [ ] **Step 6: Verify**

```bash
cd web-react && bun run typecheck && bun run lint && bun run test
```

---

## Task 3: Give the settings loader and the save path a shared cache

**Files:**

- Create: `web-react/src/helpers/settings/useSettings.ts`
- Modify: `web-react/src/helpers/settings/settingsRoutes.ts:13-24`
- Modify: `web-react/src/helpers/settings/useSaveSettings.ts:24-46`
- Create: `web-react/tests/unit/helpers/settings/useSettings.test.tsx`
- Modify: `web-react/tests/unit/helpers/settings/settingsRoutes.test.ts` (if present; otherwise skip)
- Modify: `web-react/tests/unit/helpers/settings/useSaveSettings.test.tsx`

**Interfaces:**

- Consumes: `queryKeys`, `queryClient` (Task 1).
- Produces: `useSettings(): UseQueryResult<Settings>` from `src/helpers/settings/useSettings.ts`. Tasks 4, 5 and 6 consume it.

### Steps

- [ ] **Step 1: Start the revision**

```bash
jj new -m "feat(web-react): share one settings cache entry across loader and pages"
```

- [ ] **Step 2: Write the failing hook test**

Create `web-react/tests/unit/helpers/settings/useSettings.test.tsx`:

```tsx
import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import { screen, waitFor } from "@testing-library/react";
import * as actualSettingsApi from "../../../../src/helpers/settings/settingsApi" with {
  rstest: "importActual",
};
import { renderWithQuery } from "../../test-utils";

const getSettingsMock = rs.fn();
rs.mock("../../../../src/helpers/settings/settingsApi", () => ({
  ...actualSettingsApi,
  getSettings: (...a: unknown[]) => getSettingsMock(...a),
}));

const { useSettings } = await import("../../../../src/helpers/settings/useSettings");

function Probe() {
  const { data } = useSettings();
  return <div>{data?.globals?.grill_name ?? "pending"}</div>;
}

beforeEach(() => getSettingsMock.mockReset());

describe("useSettings", () => {
  it("exposes the settings blob once the read lands", async () => {
    getSettingsMock.mockResolvedValue({ globals: { grill_name: "Smokey" } });
    renderWithQuery(<Probe />);
    await waitFor(() => expect(screen.getByText("Smokey")).toBeVisible());
  });

  it("serves two mounted readers from ONE request", async () => {
    getSettingsMock.mockResolvedValue({ globals: { grill_name: "Smokey" } });
    renderWithQuery(
      <>
        <Probe />
        <Probe />
      </>,
    );
    await waitFor(() => expect(screen.getAllByText("Smokey")).toHaveLength(2));
    expect(getSettingsMock).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 3: Run it and watch it fail**

```bash
cd web-react && bun run test tests/unit/helpers/settings/useSettings.test.tsx
```

Expected: FAIL — cannot resolve `src/helpers/settings/useSettings`.

- [ ] **Step 4: Write the hook**

Create `web-react/src/helpers/settings/useSettings.ts`:

```ts
import { useQuery } from "@tanstack/react-query";
import { queryKeys } from "../query/keys";
import { getSettings, type Settings } from "./settingsApi";

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

/**
 * The whole settings blob, shared.
 *
 * getSettings() already THROWS on failure (settingsApi.ts:16), so it is a
 * react-query fetcher exactly as it stands -- no unwrap() needed here.
 *
 * Callers that only want an advisory read (the dashboard's first_time_setup
 * gate, the tuner's probe list) should treat `data === undefined` as "no
 * answer yet or no answer at all" and do nothing, which is the fail-quiet
 * behaviour those call sites already had.
 */
export function useSettings() {
  return useQuery<Settings>({
    queryKey: queryKeys.settings,
    queryFn: () => getSettings(BASE_URL),
  });
}
```

- [ ] **Step 5: Run the test and watch it pass**

```bash
cd web-react && bun run test tests/unit/helpers/settings/useSettings.test.tsx
```

Expected: PASS, 2 tests — including the one-request assertion.

- [ ] **Step 6: Prime the cache from the loader**

Replace the body of `settingsLoader` in `web-react/src/helpers/settings/settingsRoutes.ts`:

```ts
import { queryKeys } from "../query/keys";
import { queryClient } from "../query/queryClient";
import {
  type ControllerMetadata,
  getControllerMetadata,
  getMode,
  getSettings,
  type Settings,
} from "./settingsApi";

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

// React Router route loader -- runs on navigation into /settings. Throws on
// failure so the route's errorElement renders.
//
// fetchQuery rather than a bare fetch: this is the same settings entry
// AppPrefsProvider, the dashboard's first_time_setup gate and the tuner read,
// so priming it here means those pages cost no request of their own, and a
// save that invalidates the key is seen by all of them at once.
//
// fetchQuery, NOT ensureQueryData: ensureQueryData returns whatever is
// cached, full stop -- it never consults isInvalidated or staleTime once
// data exists, so it would keep serving the pre-save blob forever after the
// first successful load, and would never call the fetcher again to surface
// a later failure either. fetchQuery calls query.isStaleByTime(), which
// DOES check isInvalidated (and the 30s staleTime): a fresh, un-invalidated
// entry is still served from cache with no request, but an invalidated or
// expired one is refetched, and a refetch failure rethrows exactly like a
// cold-cache failure does -- which is what keeps SettingsError reachable on
// every navigation, not only the first.
export async function settingsLoader(): Promise<{
  settings: Settings;
  mode: string;
  controllerMeta: ControllerMetadata | null;
}> {
  const [settings, mode, controllerMeta] = await Promise.all([
    queryClient.fetchQuery({
      queryKey: queryKeys.settings,
      queryFn: () => getSettings(BASE_URL),
    }),
    queryClient.fetchQuery({ queryKey: queryKeys.mode, queryFn: () => getMode(BASE_URL) }),
    queryClient.fetchQuery({
      queryKey: queryKeys.controllerMetadata,
      queryFn: () => getControllerMetadata(BASE_URL),
    }),
  ]);
  return { settings, mode, controllerMeta };
}
```

- [ ] **Step 7: Write the failing invalidation test**

Add to `web-react/tests/unit/helpers/settings/useSaveSettings.test.tsx`:

```tsx
it("marks the shared settings entry stale before revalidating", async () => {
  // Seed the cache as the loader would, then save. If the save did not
  // invalidate, the next fetchQuery would see a fresh, un-invalidated entry
  // (staleTime is 30s) and serve it straight from cache -- the tab would
  // revalidate right back onto the pre-save values, the bug this assertion
  // exists to prevent.
  queryClient.setQueryData(queryKeys.settings, { globals: { grill_name: "before" } });
  mockApplySettings.mockResolvedValue({ ok: true, message: "", errors: [] });

  // `renderWithLoader` and `Probe` are this file's existing harness -- Probe
  // calls useSaveSettings() and exposes a button that invokes save().
  renderWithLoader();
  await userEvent.click(screen.getByRole("button", { name: /save/i }));

  expect(queryClient.getQueryState(queryKeys.settings)?.isInvalidated).toBe(true);
});
```

- [ ] **Step 8: Run it and watch it fail**

```bash
cd web-react && bun run test tests/unit/helpers/settings/useSaveSettings.test.tsx
```

Expected: FAIL — `isInvalidated` is `false`.

- [ ] **Step 9: Invalidate on a successful save**

In `web-react/src/helpers/settings/useSaveSettings.ts`, add the imports and replace the success branch:

```ts
import { queryKeys } from "../query/keys";
import { queryClient } from "../query/queryClient";
```

```ts
      if (r.ok) {
        // Mark the shared entry invalidated BEFORE re-running the loader. The
        // loader primes itself through fetchQuery, which serves a cache entry
        // unchanged as long as it is neither stale-by-time NOR invalidated --
        // so without this, revalidate() would put the PRE-save values back on
        // screen (staleTime is 30s, easily long enough to still be "fresh").
        //
        // settingsRoot is the prefix of all three loader keys (settings, mode,
        // controller metadata), which preserves exactly what revalidate() did
        // before this cache existed: refetch all three.
        await queryClient.invalidateQueries({ queryKey: queryKeys.settingsRoot });
        revalidator.revalidate(); // re-run the loader → fresh settings
      }
```

- [ ] **Step 10: Run the tests and watch them pass**

```bash
cd web-react && bun run test tests/unit/helpers/settings
```

Expected: PASS, including the new invalidation test.

- [ ] **Step 11: Verify**

```bash
cd web-react && bun run typecheck && bun run lint && bun run test
```

Expected: PASS. `SettingsShell.test.tsx` and `settingsDrafts.test.tsx` exercise the loader; if either now sees a cached entry from a previous test, add `queryClient.clear()` to that file's `beforeEach`.

---

## Task 4: Move accent seeding into the prefs provider

**Files:**

- Modify: `web-react/src/components/AppPrefs.tsx:12-19`
- Modify: `web-react/tests/unit/components/AppPrefs.test.tsx`

**Interfaces:**

- Consumes: `useSettings` (Task 3), `readAccent` (`src/helpers/settings/accent.ts:17`).
- Produces: no new exports. `AppPrefsProvider` now seeds `accent` itself, which is what lets Task 5 delete the dashboard's copy of that read.

**Why this moves:** `DashboardRoute` currently reads settings and calls `setAccent` — the *provider's* state — from inside a `.then()`. Translated naively to `useQuery`, that becomes `setAccent` inside a `useEffect`, which `react-hooks/set-state-in-effect` rejects under the active React Compiler. The render-phase idiom that replaces it (`settingsDrafts.ts:59-66`) is legal only for a component's own state, so the seeding has to live where the state lives. This also fixes a latent gap: the accent now applies on every route, not only after the dashboard has mounted.

### Steps

- [ ] **Step 1: Start the revision**

```bash
jj new -m "refactor(web-react): seed accent where the accent state lives"
```

- [ ] **Step 2: Write the failing test**

`AppPrefs.test.tsx` currently mocks nothing and has a local `Probe` that reads `useAppPrefs()`. Add a `settingsApi` module mock in the style `MetricsPage.test.tsx` uses (`importActual` + a lazy `rs.fn()` wrapper), extend `Probe` to render the accent and expose a setter button as shown below, and add:

```tsx
it("adopts the stored accent when settings arrive", async () => {
  getSettingsMock.mockResolvedValue({
    modules: { display: "ili9341" },
    display: { config: { ili9341: { accent_theme: "Crimson" } } },
  });
  renderWithQuery(
    <AppPrefsProvider>
      <Probe />
    </AppPrefsProvider>,
  );
  await waitFor(() => expect(screen.getByTestId("accent")).toHaveTextContent("crimson"));
});

it("does not overwrite a user's choice when settings refetch", async () => {
  // A save invalidates the settings key, so a refetch WILL happen while the
  // user is sitting on their own selection. Seeding is a first-answer-only
  // event for exactly this reason.
  getSettingsMock.mockResolvedValue({
    modules: { display: "ili9341" },
    display: { config: { ili9341: { accent_theme: "Crimson" } } },
  });
  const client = testQueryClient();
  render(
    <QueryClientProvider client={client}>
      <AppPrefsProvider>
        <Probe />
      </AppPrefsProvider>
    </QueryClientProvider>,
  );
  await waitFor(() => expect(screen.getByTestId("accent")).toHaveTextContent("crimson"));

  await userEvent.click(screen.getByRole("button", { name: "pick ice" }));
  expect(screen.getByTestId("accent")).toHaveTextContent("ice");

  await act(() => client.invalidateQueries({ queryKey: queryKeys.settings }));
  expect(screen.getByTestId("accent")).toHaveTextContent("ice");
});
```

`Probe` is a local helper in that file:

```tsx
function Probe() {
  const { accent, setAccent } = useAppPrefs();
  return (
    <>
      <span data-testid="accent">{accent}</span>
      <button type="button" onClick={() => setAccent("ice")}>
        pick ice
      </button>
    </>
  );
}
```

- [ ] **Step 3: Run it and watch it fail**

```bash
cd web-react && bun run test tests/unit/components/AppPrefs.test.tsx
```

Expected: FAIL — accent stays `ember`; the provider reads no settings yet.

- [ ] **Step 4: Seed inside the provider**

Replace `AppPrefsProvider` in `web-react/src/components/AppPrefs.tsx`:

```tsx
export function AppPrefsProvider({ children }: { children: ReactNode }) {
  const [accent, setAccent] = useState<AccentName>("ember");
  const [animate, setAnimate] = useState(true);

  // Adopt the stored accent the FIRST time settings arrive, and never again:
  // after that the user's own click owns it, and any settings save invalidates
  // this key, so a later refetch must not reach back in and undo a swatch they
  // just picked.
  //
  // Render-phase adjustment, NOT a useEffect: the React Compiler is active and
  // `react-hooks/set-state-in-effect` rejects setState-in-effect. Same idiom
  // helpers/settings/settingsDrafts.ts:59 uses. It is legal here precisely
  // because `accent` is THIS component's own state -- which is why the seeding
  // moved out of DashboardRoute, where writing the provider's state during
  // render would instead be "update a component while rendering a different
  // component".
  const { data: settings } = useSettings();
  const [seeded, setSeeded] = useState(false);
  if (settings && !seeded) {
    setSeeded(true);
    setAccent(readAccent(settings));
  }

  useEffect(() => {
    document.documentElement.setAttribute("data-accent", accent);
  }, [accent]);
  return <Ctx.Provider value={{ accent, setAccent, animate, setAnimate }}>{children}</Ctx.Provider>;
}
```

Add the imports:

```tsx
import { readAccent } from "../helpers/settings/accent";
import { useSettings } from "../helpers/settings/useSettings";
```

- [ ] **Step 5: Run the tests and watch them pass**

```bash
cd web-react && bun run test tests/unit/components/AppPrefs.test.tsx
```

Expected: PASS.

- [ ] **Step 6: Verify**

```bash
cd web-react && bun run typecheck && bun run lint && bun run test
```

---

## Task 5: Convert the dashboard's first-time-setup gate

**Files:**

- Modify: `web-react/src/components/DashboardRoute.tsx:1-43`
- Modify: `web-react/tests/unit/components/DashboardRoute.test.tsx`

**Interfaces:**

- Consumes: `useSettings` (Task 3). Task 4 has already taken the accent half of this effect.

### Steps

- [ ] **Step 1: Start the revision**

```bash
jj new -m "refactor(web-react): read the first-time-setup gate from the settings cache"
```

- [ ] **Step 2: Write the failing test**

This file's harness is `renderDashboardRoute()`, with `getSettingsMock`, the `OK` settings fixture, `wizardShowing()` and `stubCommand()`. It renders `AppPrefsProvider` itself, so after Task 4 that render needs a `QueryClientProvider` — add it inside `renderDashboardRoute`. Then add to `web-react/tests/unit/components/DashboardRoute.test.tsx`:

```tsx
it("issues no settings request of its own when the cache is already warm", async () => {
  // AppPrefsProvider and /settings' loader read the same entry. The gate must
  // ride that entry, not add a fourth GET to every dashboard paint.
  getSettingsMock.mockResolvedValue(OK);
  renderDashboardRoute();
  await waitFor(() => expect(getSettingsMock).toHaveBeenCalled());
  expect(getSettingsMock).toHaveBeenCalledTimes(1);
});
```

- [ ] **Step 3: Run it and watch it fail**

```bash
cd web-react && bun run test tests/unit/components/DashboardRoute.test.tsx
```

Expected: FAIL — 2 calls, one from `AppPrefsProvider` and one from the page's own effect.

- [ ] **Step 4: Replace the effect**

In `web-react/src/components/DashboardRoute.tsx`, delete the `getSettings` import, the `readAccent` import, the `BASE_URL`-based effect at lines 29-43, and `useEffect`'s fetch body. Replace with:

```tsx
  // Non-blocking first_time_setup gate. "/" deliberately has NO route loader
  // (see App.tsx): React Router defers rendering until a loader resolves --
  // even a synchronous one resolves on a microtask -- so a loader here would
  // turn the dashboard's first paint into an async gap. A brief dashboard
  // flash before the redirect is the accepted tradeoff.
  //
  // The read behind this is now the app's shared settings entry
  // (helpers/settings/useSettings.ts), which AppPrefsProvider has usually
  // already primed, so the gate costs no request of its own. A failed read
  // leaves `data` undefined and the gate simply does not fire -- the same
  // advisory, fail-quiet behaviour it always had.
  const { data: settings } = useSettings();
  const firstTime = settings?.globals?.first_time_setup === true;
  useEffect(() => {
    if (firstTime) navigate("/wizard");
  }, [firstTime, navigate]);
```

`navigate` in an effect is fine — it is not `setState`.

- [ ] **Step 5: Run the tests and watch them pass**

```bash
cd web-react && bun run test tests/unit/components/DashboardRoute.test.tsx
```

Expected: PASS, including the existing redirect-on-fresh-install test.

- [ ] **Step 6: Verify**

```bash
cd web-react && bun run typecheck && bun run lint && bun run test
```

---

## Task 6: Convert the tuner's probe-list read

**Files:**

- Modify: `web-react/src/components/tuner/TunerPage.tsx:73-88`
- Modify: `web-react/tests/unit/components/tuner/TunerPage.test.tsx`

**Interfaces:**

- Consumes: `useSettings` (Task 3).

**Note:** this converts the mount-time probe-list read ONLY. The session poll at `TunerPage.tsx:96-120` is deferred (see Scope) and must be left exactly as it is.

### Steps

- [ ] **Step 1: Start the revision**

```bash
jj new -m "refactor(web-react): read the tuner probe list from the settings cache"
```

- [ ] **Step 2: Write the failing test**

This file's harness is `getSettingsMock` with the `SETTINGS` fixture, plus `installFakeClock()`, `startTuning()` and `recordAllSegments()`. Wrap its render in `renderWithQuery` and add:

```tsx
it("takes the probe list from the shared settings entry", async () => {
  getSettingsMock.mockResolvedValue(SETTINGS);
  renderWithQuery(<TunerPage />);
  await waitFor(() =>
    expect(screen.getByRole("combobox", { name: /probe/i })).toHaveValue("Grill"),
  );
  expect(getSettingsMock).toHaveBeenCalledTimes(1);
});
```

Match the option label to whatever `SETTINGS` actually declares as its first probe.

- [ ] **Step 3: Run it and watch it fail**

```bash
cd web-react && bun run test tests/unit/components/tuner/TunerPage.test.tsx
```

Expected: FAIL.

- [ ] **Step 4: Replace the mount effect**

The existing effect derives three pieces of state from the settings read. Two of them (`selected`, `reference`) are user-editable, so they stay as state seeded once; `probes` becomes pure derivation.

Replace lines 73-88 of `web-react/src/components/tuner/TunerPage.tsx` with:

```tsx
  //  The probe list the operator picks from. No session, no control write --
  //  just a read, and now the app's shared settings entry rather than a fourth
  //  independent GET of the same blob.
  const { data: settings } = useSettings();
  const probes = useMemo(() => (settings ? probeLabels(settings) : []), [settings]);

  //  Reference defaults to the first probe that is NOT the tune target -- auto
  //  tuning reads a DIFFERENT, trusted probe.
  //
  //  Render-phase seeding rather than a useEffect (see AppPrefs.tsx for the
  //  same idiom and the reason): these are this component's own state, and the
  //  seed must happen once, on the first list to arrive, so a later refetch
  //  cannot yank the operator's selection out from under them mid-session.
  const [seeded, setSeeded] = useState(false);
  if (probes.length > 0 && !seeded) {
    setSeeded(true);
    setSelected((current) => current || probes[0] || "");
    setReference((current) => current || probes.find((l) => l !== probes[0]) || probes[0] || "");
  }
```

Delete the now-unused `probes` state declaration and the `getSettings` import; add `useMemo` to the React import and `useSettings` to the imports.

- [ ] **Step 5: Run the tests and watch them pass**

```bash
cd web-react && bun run test tests/unit/components/tuner/TunerPage.test.tsx
```

Expected: PASS, including the existing session tests — the poll was not touched.

- [ ] **Step 6: Verify**

```bash
cd web-react && bun run typecheck && bun run lint && bun run test
```

---

## Task 7: Make the accent write invalidate the cache

**Files:**

- Modify: `web-react/src/helpers/settings/accent.ts:39-57`
- Modify: `web-react/tests/unit/helpers/settings/accent.test.ts`

**Interfaces:**

- Consumes: `queryKeys`, `queryClient` (Task 1).

**Why:** `saveAccent` keeps its own `getSettings` — it is a rare user-initiated write path, not a render path, and leaving the read inline keeps the helper independently testable. What it must gain is invalidation, so that the /settings General tab sees an accent changed from the dashboard swatch.

### Steps

- [ ] **Step 1: Start the revision**

```bash
jj new -m "fix(web-react): invalidate settings after an accent write"
```

- [ ] **Step 2: Write the failing test**

In `web-react/tests/unit/helpers/settings/accent.test.ts`:

```ts
it("invalidates the shared settings entry so other readers see the new accent", async () => {
  queryClient.setQueryData(queryKeys.settings, { modules: { display: "ili9341" } });
  // ...existing fetch stubbing for a successful applySettings...
  expect(await saveAccent("", "crimson")).toBe(true);
  expect(queryClient.getQueryState(queryKeys.settings)?.isInvalidated).toBe(true);
});

it("leaves the cache alone when the write is refused", async () => {
  queryClient.setQueryData(queryKeys.settings, { modules: { display: "ili9341" } });
  // ...existing fetch stubbing for a rejected applySettings...
  expect(await saveAccent("", "crimson")).toBe(false);
  expect(queryClient.getQueryState(queryKeys.settings)?.isInvalidated).toBe(false);
});
```

Add `beforeEach(() => queryClient.clear())` to the file — the singleton is shared across tests in a run.

- [ ] **Step 3: Run it and watch it fail**

```bash
cd web-react && bun run test tests/unit/helpers/settings/accent.test.ts
```

Expected: FAIL — `isInvalidated` is `false` in the first test.

- [ ] **Step 4: Invalidate after a successful write**

In `web-react/src/helpers/settings/accent.ts`, replace the tail of `saveAccent`:

```ts
    const result = await applySettings(
      baseUrl,
      setPath({}, path as SettingsPath, storedAccentName(accent)),
      [],
    );
    // The dashboard swatch has already applied the accent locally, but every
    // other reader of the settings entry (AppPrefsProvider, /settings' loader,
    // the General tab) is still holding the old one. Only on success: a
    // refused write changed nothing to tell them about.
    if (result.ok) await queryClient.invalidateQueries({ queryKey: queryKeys.settingsRoot });
    return result.ok;
```

Add the imports:

```ts
import { queryKeys } from "../query/keys";
import { queryClient } from "../query/queryClient";
```

- [ ] **Step 5: Run the tests and watch them pass**

```bash
cd web-react && bun run test tests/unit/helpers/settings/accent.test.ts
```

- [ ] **Step 6: Verify**

```bash
cd web-react && bun run typecheck && bun run lint && bun run test
```

---

## Task 8: Convert the history page — read, nonce and auto-refresh

**Files:**

- Modify: `web-react/src/components/history/HistoryPage.tsx`
- Modify: `web-react/tests/unit/components/history/HistoryPage.test.tsx`

**Interfaces:**

- Consumes: `useSettings` (Task 3), `queryKeys.historyChart` (Task 1), `fetchHistoryChart` (`helpers/history/historyApi.ts:74`, already throws).

**Why all three at once:** the page's `requestId` counter is simultaneously its cache key, its in-flight guard, its race resolver and its poll trigger (`HistoryPage.tsx:131-148` documents the coupling). Converting any one of them alone would leave the counter half-alive.

### Steps

- [ ] **Step 1: Start the revision**

```bash
jj new -m "refactor(web-react): put the history chart behind a query key"
```

- [ ] **Step 2: Write the failing tests**

```tsx
it("refetches when the window changes and keeps the previous chart visible", async () => {
  fetchHistoryChartMock.mockResolvedValue(CHART_FIXTURE);
  renderWithQuery(<HistoryPage />);
  await waitFor(() => expect(screen.getByTestId("history-chart")).toBeVisible());

  await userEvent.clear(screen.getByLabelText("Minutes"));
  await userEvent.type(screen.getByLabelText("Minutes"), "120");

  await waitFor(() => expect(fetchHistoryChartMock).toHaveBeenLastCalledWith("", 120));
});

// `waitFor` does not work under `rs.useFakeTimers()` -- it polls on a real
// timer that fake timers freeze, so it times out at 5000ms instead of
// observing the update. Settle explicitly instead. See "Testing notes" above
// for why the settle/tick helpers advance the clock TWICE: react-query's
// notifyManager flushes an observer's result through a real `setTimeout(0)`,
// which fake timers make a visible macrotask distinct from the query settling.
async function settle() {
  await act(async () => {
    await rs.advanceTimersByTimeAsync(0);
    await Promise.resolve();
  });
}

async function tick(ms: number) {
  await act(async () => {
    await rs.advanceTimersByTimeAsync(ms);
    await Promise.resolve();
  });
}

it("polls on the auto-refresh cadence when the setting is on", async () => {
  rs.useFakeTimers();
  getSettingsMock.mockResolvedValue({ history_page: { autorefresh: "on" } });
  fetchHistoryChartMock.mockResolvedValue(CHART_FIXTURE);
  renderWithQuery(<HistoryPage />);
  await settle();
  expect(fetchHistoryChartMock).toHaveBeenCalledTimes(1);

  await tick(REFRESH_MS);
  expect(fetchHistoryChartMock).toHaveBeenCalledTimes(2);
  rs.useRealTimers();
});

it("does not poll when the setting is off", async () => {
  rs.useFakeTimers();
  getSettingsMock.mockResolvedValue({ history_page: { autorefresh: "off" } });
  fetchHistoryChartMock.mockResolvedValue(CHART_FIXTURE);
  renderWithQuery(<HistoryPage />);
  await settle();
  expect(fetchHistoryChartMock).toHaveBeenCalledTimes(1);

  await tick(REFRESH_MS * 3);
  expect(fetchHistoryChartMock).toHaveBeenCalledTimes(1);
  rs.useRealTimers();
});
```

- [ ] **Step 3: Run them and watch them fail**

```bash
cd web-react && bun run test tests/unit/components/history/HistoryPage.test.tsx
```

- [ ] **Step 4: Replace the read, the nonce and the poll**

Delete `requestId`, `setRequestId`, `outcome`, `setOutcome`, `data`, `setData`, the fetch effect, the settings effect and the poll effect. Replace with:

```tsx
  //  The auto-refresh preference. This route has no loader (see App.tsx) and
  //  GET /api/history/chart answers with chart data only, so a settings read
  //  is the only way to see the flag -- now the app's shared entry rather than
  //  a GET of its own. A failed read leaves polling off, the same fail-quiet
  //  direction this always failed in.
  const { data: settings } = useSettings();
  const autoRefresh = settings?.history_page?.autorefresh === "on";

  //  The chart. `minutes` is part of the key, so changing the window IS the
  //  refetch -- which is what retires the requestId counter that used to be
  //  the cache key, the in-flight guard, the race resolver and the poll
  //  trigger all at once.
  //
  //  placeholderData keeps the previous window's chart on screen while the new
  //  one loads, rather than dropping to the loading branch and back.
  //  refetchInterval is the auto-refresh poll: react-query already holds a
  //  request per key, so the in-flight guard the old effect hand-rolled (the
  //  `loading` dependency) has nothing left to do.
  const {
    data,
    isPending,
    isError,
  } = useQuery({
    queryKey: queryKeys.historyChart(minutes ?? undefined),
    queryFn: () => fetchHistoryChart(BASE_URL, minutes ?? undefined),
    placeholderData: (previous) => previous,
    refetchInterval: autoRefresh ? REFRESH_MS : false,
  });

  const loading = isPending;
  const failed = isError;
```

Leave `shownMinutes`, `chart`, `chartKey` and `resetNonce` exactly as they are — `resetNonce` drives the deliberate chart remount described at `HistoryPage.tsx:120-129` and is not a fetch trigger.

Add the imports:

```tsx
import { useQuery } from "@tanstack/react-query";
import { queryKeys } from "../../helpers/query/keys";
import { useSettings } from "../../helpers/settings/useSettings";
```

- [ ] **Step 5: Run the tests and watch them pass**

```bash
cd web-react && bun run test tests/unit/components/history/HistoryPage.test.tsx
```

Expected: PASS, including the pre-existing zoom-preservation and reset-zoom tests. If a drag-zoom test now fails, `placeholderData` changed the identity of `data` between renders — confirm `chartKey` is still `${shownMinutes}-${resetNonce}` and unaffected by a poll tick, which is the invariant `HistoryPage.tsx:131-135` describes.

- [ ] **Step 6: Verify**

```bash
cd web-react && bun run typecheck && bun run lint && bun run test
```

---

## Task 9: Convert the metrics page — the plain-read recipe

**Files:**

- Modify: `web-react/src/components/metrics/MetricsPage.tsx:23-45`
- Modify: `web-react/tests/unit/components/metrics/MetricsPage.test.tsx`

**Interfaces:**

- Consumes: `unwrap` (Task 2), `queryKeys.metrics` (Task 1), `fetchMetrics` (returns `MetricsResult`, does not throw).

**This task is the reference conversion** for every remaining envelope-returning mount read. Tasks 10 and 12, and the deferred follow-up plan, all follow this shape.

### Steps

- [ ] **Step 1: Start the revision**

```bash
jj new -m "refactor(web-react): put the metrics read behind a query key"
```

- [ ] **Step 2: Write the failing test**

This file calls `render(<MetricsPage />)` directly in every test — replace each with `renderWithQuery(<MetricsPage />)`, keeping the existing `fetchMetricsMock`, `ok()` and `payload()` helpers as they are. Then add:

```tsx
it("renders the server's message when the envelope reports failure", async () => {
  fetchMetricsMock.mockResolvedValue({ ok: false, status: 500, message: "no metrics db", data: null });
  renderWithQuery(<MetricsPage />);
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("no metrics db"));
});
```

This is the assertion that catches the whole class of bug `unwrap` exists for: without it, `ok: false` resolves, `useQuery` reports success, and the page renders the empty state instead of the error.

- [ ] **Step 3: Run it and watch it fail**

```bash
cd web-react && bun run test tests/unit/components/metrics/MetricsPage.test.tsx
```

- [ ] **Step 4: Replace the fetch block**

Replace `MetricsPage.tsx:24-43` (the three `useState`s and the effect) with:

```tsx
  const { data: payload, isPending, error } = useQuery({
    queryKey: queryKeys.metrics,
    //  fetchMetrics RESOLVES its failures (metricsTypes.ts documents why), so
    //  it needs unwrap() to reject before useQuery can tell the two apart.
    queryFn: () => unwrap(fetchMetrics(BASE_URL)),
  });

  if (isPending) {
    return (
      <div className="pf-metrics">
        <p>Loading metrics…</p>
      </div>
    );
  }

  if (!payload) {
    return (
      <div className="pf-metrics">
        <p className="pf-settings-error-text" role="alert">
          {error?.message ?? "The server did not answer."}
        </p>
      </div>
    );
  }
```

Add the imports:

```tsx
import { useQuery } from "@tanstack/react-query";
import { queryKeys } from "../../helpers/query/keys";
import { unwrap } from "../../helpers/query/unwrap";
```

Remove the now-unused `useEffect` and `useState` imports.

- [ ] **Step 5: Run the tests and watch them pass**

```bash
cd web-react && bun run test tests/unit/components/metrics/MetricsPage.test.tsx
```

Expected: PASS, including the existing export-href and empty-state tests.

- [ ] **Step 6: Verify**

```bash
cd web-react && bun run typecheck && bun run lint && bun run test
```

---

## Task 10: Convert the cook-file page and chart

**Files:**

- Modify: `web-react/src/components/cookfiles/CookFilePage.tsx`
- Modify: `web-react/src/components/cookfiles/CookFileChart.tsx`
- Modify: `web-react/tests/unit/components/cookfiles/CookFilePage.test.tsx`
- Modify: `web-react/tests/unit/components/cookfiles/CookFileChart.test.tsx`

**Interfaces:**

- Consumes: `queryKeys.cookfileDetail(filename)`, `queryKeys.cookfileChart(filename)`, `queryKeys.cookfileRoot(filename)` (Task 1); `fetchCookFileDetail` and `fetchCookFileChart` (`helpers/files/cookfileApi.ts`). Both throw `FileRequestError` (`helpers/files/apiEnvelope.ts:20`), so they are already valid fetchers — **no `unwrap` here**.

**Note:** `CookFilePage`'s `Outcome { id, problem }` interface exists only to pair a request id with its failure. It goes away with the counter. Keep `toDetail`; it is a pure mapper. The existing test harness in `CookFilePage.test.tsx` is `mount()`, with `fetchCookFileDetailMock`, `recoverCookFileMock`, `CookFileRequestError` and the `DETAIL` fixture — use those, and wrap `mount()`'s render in a `QueryClientProvider` from `testQueryClient()`.

### Steps

- [ ] **Step 1: Start the revision**

```bash
jj new -m "refactor(web-react): put cook-file reads behind a query key"
```

- [ ] **Step 2: Write the failing test**

```tsx
it("surfaces the 422 errortype the repair prompt branches on", async () => {
  fetchCookFileDetailMock.mockRejectedValue(
    new CookFileRequestError({ status: 422, message: "old version", errortype: "version" }),
  );
  mount("cook.pifire");
  await waitFor(() => expect(screen.getByText(/old version/)).toBeVisible());
});

it("re-reads the file after a repair without a hand-rolled counter", async () => {
  fetchCookFileDetailMock
    .mockRejectedValueOnce(
      new CookFileRequestError({ status: 422, message: "old version", errortype: "version" }),
    )
    .mockResolvedValue(DETAIL);
  recoverCookFileMock.mockResolvedValue({ ok: true });
  mount("cook.pifire");
  await waitFor(() => expect(screen.getByText(/old version/)).toBeVisible());

  await userEvent.click(screen.getByRole("button", { name: /repair/i }));
  await waitFor(() => expect(screen.queryByText(/old version/)).not.toBeInTheDocument());
});
```

- [ ] **Step 3: Run them and watch them fail**

```bash
cd web-react && bun run test tests/unit/components/cookfiles
```

- [ ] **Step 4: Replace the counters**

In both files, delete `requestId`/`setRequestId` and the `Outcome` interface in `CookFilePage.tsx`, and replace the fetch effect with:

```tsx
  const { data, isPending, error } = useQuery({
    queryKey: queryKeys.cookfileDetail(filename),
    //  helpers/files/apiEnvelope.ts throws FileRequestError already, so this
    //  needs no unwrap(): `error` below IS the FileRequestError, and its
    //  `.detail.errortype` is what the repair prompt branches on.
    queryFn: () => fetchCookFileDetail(filename, BASE_URL),
  });

  const problem = error instanceof FileRequestError ? error.detail : null;
```

In `CookFileChart.tsx`, the same shape with `queryKeys.cookfileChart(filename)` and `fetchCookFileChart`.

Replace every former `setRequestId((n) => n + 1)` — the refetch-after-repair triggers — with an invalidate on the shared prefix, so a repair refreshes the detail AND the chart in one call:

```tsx
  const queryClient = useQueryClient();
  const reload = useCallback(
    () => queryClient.invalidateQueries({ queryKey: queryKeys.cookfileRoot(filename) }),
    [queryClient, filename],
  );
```

- [ ] **Step 5: Run the tests and watch them pass**

```bash
cd web-react && bun run test tests/unit/components/cookfiles
```

- [ ] **Step 6: Verify**

```bash
cd web-react && bun run typecheck && bun run lint && bun run test
```

---

## Task 11: Convert the recipe page

**Files:**

- Modify: `web-react/src/components/recipes/RecipePage.tsx`
- Modify: `web-react/tests/unit/components/recipes/RecipePage.test.tsx`

**Interfaces:**

- Consumes: `queryKeys.recipe(filename)` (Task 1); `fetchRecipeDetail` (`helpers/files/recipeApi.ts`). Same throwing `apiEnvelope` client as Task 10 — no `unwrap`. The existing harness in `RecipePage.test.tsx` is `mount()`, with `fetchRecipeDetailMock`, `updateIngredientMock`, `useShellStateMock` and the `DETAIL` fixture.

**Note:** `RecipePage` also reads `live` off `useShellState()` for run status (`RecipePage.tsx:52`). Leave that alone; it is push-plane data. Only the `requestId`-driven recipe read converts. The page's editors (`IngredientsEditor`, `StepsEditor`, `InstructionsEditor`) write and then bump the counter — those become `invalidateQueries` on the same key.

### Steps

- [ ] **Step 1: Start the revision**

```bash
jj new -m "refactor(web-react): put the recipe read behind a query key"
```

- [ ] **Step 2: Write the failing test**

```tsx
it("re-reads the recipe after an editor saves", async () => {
  fetchRecipeDetailMock.mockResolvedValueOnce(DETAIL).mockResolvedValue({
    ...DETAIL,
    recipe: { ...DETAIL.recipe, ingredients: [{ name: "brisket", quantity: "1" }] },
  });
  updateIngredientMock.mockResolvedValue({ ok: true });
  mount("brisket.json");
  await waitFor(() => expect(fetchRecipeDetailMock).toHaveBeenCalledTimes(1));

  await userEvent.click(screen.getByRole("button", { name: /save ingredients/i }));
  await waitFor(() => expect(screen.getByText("brisket")).toBeVisible());
});
```

- [ ] **Step 3: Run it and watch it fail**

```bash
cd web-react && bun run test tests/unit/components/recipes/RecipePage.test.tsx
```

- [ ] **Step 4: Replace the counter**

Delete `requestId`/`setRequestId` and the `Outcome`/`Problem` interfaces; replace the fetch effect with the same shape Task 10 used, keyed on `queryKeys.recipe(filename)` with `fetchRecipeDetail` as the fetcher. Keep `toProblem` — it is a pure mapper and now takes `error` instead of an `Outcome`. Replace the editors' `setRequestId((n) => n + 1)` callbacks with an `invalidateQueries` on that key.

- [ ] **Step 5: Run the tests and watch them pass**

```bash
cd web-react && bun run test tests/unit/components/recipes/RecipePage.test.tsx
```

- [ ] **Step 6: Verify**

```bash
cd web-react && bun run typecheck && bun run lint && bun run test
```

---

## Task 12: Convert the admin page's state read

**Files:**

- Modify: `web-react/src/components/admin/AdminPage.tsx`
- Modify: `web-react/tests/unit/components/admin/AdminPage.test.tsx`

**Interfaces:**

- Consumes: `unwrap` (Task 2), `queryKeys.adminState` (Task 1), `fetchAdminState` (`helpers/admin/adminApi.ts:102`, returns `AdminResult<AdminState>`).

**Constraint from the source:** `adminApi.ts:97-101` warns that `GET /api/admin/state` calls `gather_system_info()`, which probes the platform and writes readings back into control — *"Fetch it on mount and after a change, never on a timer."* Therefore this query must set **`refetchInterval: false`** explicitly and must not inherit any polling. Refetch happens only through `invalidateQueries` after one of the page's own actions.

### Steps

- [ ] **Step 1: Start the revision**

```bash
jj new -m "refactor(web-react): put the admin state read behind a query key"
```

- [ ] **Step 2: Write the failing tests**

This file's existing harness is `renderPage()`, the `fetchAdminStateMock`, the `STATE` fixture, and the `ok(...)` / `refuse(...)` envelope builders. Wrap `renderPage()`'s render in a `QueryClientProvider` from `testQueryClient()`, then add:

```tsx
it("renders the server's refusal message when the envelope reports failure", async () => {
  fetchAdminStateMock.mockResolvedValue(refuse(409, "not_stopped"));
  renderPage();
  await waitFor(() => expect(screen.getByRole("alert")).toBeVisible());
});

it("never re-reads on a timer: the read probes hardware and writes to control", async () => {
  rs.useFakeTimers();
  fetchAdminStateMock.mockResolvedValue(ok(STATE));
  renderPage();
  // `waitFor` does not work under `rs.useFakeTimers()` (it polls on a real
  // timer that fake timers freeze, and times out at 5000ms), and the
  // SYNCHRONOUS `advanceTimersByTime` never yields the microtask react-query
  // needs to settle a fetch and re-arm `refetchInterval` -- against a
  // deliberately broken build with a real interval instead of `false`, that
  // combination produces a false pass, not a failure. Use the async form and
  // let it settle explicitly. See "Testing notes" above and the `tick()` /
  // `settle()` pair in tests/unit/components/history/HistoryPage.test.tsx and
  // tests/unit/helpers/useWebUiBuild.test.tsx.
  await act(async () => {
    await rs.advanceTimersByTimeAsync(0);
  });
  expect(fetchAdminStateMock).toHaveBeenCalledTimes(1);

  await act(async () => {
    await rs.advanceTimersByTimeAsync(300_000);
  });
  expect(fetchAdminStateMock).toHaveBeenCalledTimes(1);
  rs.useRealTimers();
});
```

- [ ] **Step 3: Run them and watch them fail**

```bash
cd web-react && bun run test tests/unit/components/admin/AdminPage.test.tsx
```

- [ ] **Step 4: Replace the fetch block**

```tsx
  const { data: state, isPending, error } = useQuery({
    queryKey: queryKeys.adminState,
    queryFn: () => unwrap(fetchAdminState(BASE_URL)),
    //  Explicit, not inherited. adminApi.ts:99 -- state_payload() calls
    //  gather_system_info(), which probes the platform and writes the readings
    //  back into control. This is a read with side effects: on mount and after
    //  a change, never on a timer.
    refetchInterval: false,
  });
```

Replace the page's post-action reload calls with `queryClient.invalidateQueries({ queryKey: queryKeys.adminState })`.

- [ ] **Step 5: Run the tests and watch them pass**

```bash
cd web-react && bun run test tests/unit/components/admin/AdminPage.test.tsx
```

- [ ] **Step 6: Verify**

```bash
cd web-react && bun run typecheck && bun run lint && bun run test
```

---

## Task 13: Convert the build-id poll

**Files:**

- Modify: `web-react/src/helpers/useWebUiBuild.ts:36-69`
- Modify: `web-react/src/components/App.tsx`
- Modify: `web-react/tests/unit/helpers/useWebUiBuild.test.tsx`

**Interfaces:**

- Consumes: `queryKeys.webUiBuild` (Task 1). `fetchBuildId` already resolves `string | null` and never throws — it needs no `unwrap`; `null` is a legitimate "no answer" value the hook must keep treating as "not a change".

**App.tsx change:** `useWebUiBuild()` is currently called in `App`'s own body, which is ABOVE the `QueryClientProvider` in `App`'s returned JSX. Once it uses a query it must move into a child of the provider.

### Steps

- [ ] **Step 1: Start the revision**

```bash
jj new -m "refactor(web-react): poll the served build id through the query cache"
```

- [ ] **Step 2: Write the failing tests**

This file stubs global `fetch` (`fetchMock`) rather than the module, and drives the hook through a local `Probe` component. Keep both; wrap `Probe`'s render in `renderWithQuery` so the hook has a client. The existing assertions (first-read capture, changed-id reload, null-is-not-a-change) must keep passing unchanged — they are the behavioral contract. Add:

```tsx
// react-query settles a refetch in two hops: the fetch promise resolves (a
// microtask), then notifyManager re-renders observers via a REAL setTimeout(0)
// (see node_modules/@tanstack/query-core's notifyManager.js), so React can
// batch the update. advanceTimersByTimeAsync stopping exactly on the poll
// boundary flushes the first hop but leaves that second one still pending --
// nothing downstream of `data` (this hook's useEffect, and so `reload`) has
// run yet. `waitFor` can't be used to paper over this either: it polls on a
// timer that fake timers freeze (see "Testing notes" above and
// HistoryPage.test.tsx's auto-refresh describe block).
async function tick(ms: number) {
  await act(async () => {
    await rs.advanceTimersByTimeAsync(ms);
  });
}

// Ticking one more virtual millisecond past a settle is what lets
// notifyManager's setTimeout(0) hop fire.
async function settle() {
  await act(async () => {
    await rs.advanceTimersByTimeAsync(1);
  });
}

it("re-reads on the poll cadence without a hand-rolled interval", async () => {
  rs.useFakeTimers();
  const reload = rs.fn();
  fetchMock
    .mockResolvedValueOnce({ ok: true, json: async () => ({ build: "abc" }) })
    .mockResolvedValue({ ok: true, json: async () => ({ build: "def" }) });
  renderWithQuery(<Probe reload={reload} />);
  await tick(0);
  await settle();
  expect(fetchMock).toHaveBeenCalledTimes(1);

  await tick(60_000);
  await settle();
  expect(reload).toHaveBeenCalledTimes(1);
  rs.useRealTimers();
});
```

Note: this assertion is black-box-equivalent to the old hand-rolled
`setInterval` at 60s -- it passes against BOTH the pre-conversion and the
post-conversion code, so do not expect it to go red in the next step. Its
job is regression coverage for the query-cache path, not a TDD failing test.

- [ ] **Step 3: Run them and watch them pass**

```bash
cd web-react && bun run test tests/unit/helpers/useWebUiBuild.test.tsx
```

- [ ] **Step 4: Convert the hook**

Replace the `useEffect` in `web-react/src/helpers/useWebUiBuild.ts` with:

```ts
export function useWebUiBuild(baseUrl = BASE_URL, reload = () => window.location.reload()) {
  // The build this tab is running. Captured from the first successful read
  // rather than baked into the bundle, so it needs no build-time plumbing.
  const runningRef = useRef<string | null>(null);

  const { data: serving } = useQuery({
    queryKey: queryKeys.webUiBuild,
    queryFn: () => fetchBuildId(baseUrl),
    refetchInterval: POLL_MS,
    // A tab left open on a phone is suspended, not polling; coming back to it
    // is the moment a stale bundle is most likely and most worth catching.
    // This overrides the client default, which is off for the rest of the app.
    refetchOnWindowFocus: true,
    // fetchBuildId swallows its own failures and answers null, so there is
    // nothing here that can go stale in a way a retry would fix.
    staleTime: 0,
  });

  useEffect(() => {
    // A null -- backend down, mid-restart, no bundle built -- is never a
    // change, so a grill that briefly loses its backend does not reload itself
    // in a loop. Only a CHANGED id reloads.
    if (serving == null) return;
    if (runningRef.current === null) {
      runningRef.current = serving;
      return;
    }
    if (serving !== runningRef.current) reload();
  }, [serving, reload]);
}
```

This effect writes a ref and calls `reload`, neither of which is `setState` — it does not trip `react-hooks/set-state-in-effect`.

- [ ] **Step 5: Move the call inside the provider**

In `web-react/src/components/App.tsx`, extract the hook call into a child so it sits under `QueryClientProvider`:

```tsx
// useWebUiBuild reads through the query cache, so it has to be called from a
// component INSIDE QueryClientProvider -- App's own body is above it. Kept
// above the router all the same: an update can land while the user is
// anywhere, including the wizard, which is the one route mounted outside
// AppShell.
function BuildWatcher({ children }: { children: ReactNode }) {
  useWebUiBuild();
  return children;
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BuildWatcher>
        <AppPrefsProvider>
          <RouterProvider router={router} />
        </AppPrefsProvider>
      </BuildWatcher>
    </QueryClientProvider>
  );
}
```

- [ ] **Step 6: Run the tests and watch them pass**

```bash
cd web-react && bun run test tests/unit/helpers/useWebUiBuild.test.tsx tests/unit/components/App.test.tsx
```

- [ ] **Step 7: Verify**

```bash
cd web-react && bun run typecheck && bun run lint && bun run test
```

---

## Task 14: Full verification and record the deferred work

**Files:**

- Create: `docs/superpowers/backlogs/react-query-remaining-reads.md`
- Modify: `web-react/README.md` (if it documents data flow; otherwise skip)

**Interfaces:**

- Consumes: everything above. Produces no code.

### Steps

- [ ] **Step 1: Start the revision**

```bash
jj new -m "docs(web-react): record the reads still on hand-rolled fetch"
```

- [ ] **Step 2: Run the full gate**

```bash
cd web-react && bun run typecheck && bun run lint && bun run test
```

Expected: all green. Record the test count and compare against the pre-plan baseline — no test should have been deleted without a replacement named in this plan.

- [ ] **Step 3: Confirm the coverage gate still holds**

```bash
cd web-react && bun run test:coverage
```

Expected: PASS. `rstest.config.ts` enforces 75% lines per file across `src/**`; the three new files (`query/queryClient.ts`, `query/keys.ts`, `query/unwrap.ts`) each have their own tests from Tasks 1 and 2.

- [ ] **Step 4: Confirm the settings blob is fetched once**

```bash
cd web-react && bun run dev
```

Load `/`, open DevTools → Network, filter `api/settings`. Expected: **one** request on first paint, where before this plan the dashboard produced two (provider + gate) and navigating to `/settings`, `/history` or `/tuner` each produced another. Navigate `/` → `/history` → `/tuner` within 30s and confirm no further `api/settings` request. Then save any settings tab and confirm exactly one refetch follows.

- [ ] **Step 5: Run the e2e suite**

```bash
cd web-react && bun run test:e2e
```

Expected: PASS. The fidelity screenshot projects are the check that no loading state changed shape — `MetricsPage` and `HistoryPage` both had their loading branches rewritten.

- [ ] **Step 6: Write the backlog note**

Create `docs/superpowers/backlogs/react-query-remaining-reads.md`:

```markdown
# REST reads still on hand-rolled fetch

The 2026-08-07 plan put the settings blob, the history chart, cook files,
recipes, metrics, admin state and the build-id poll behind TanStack Query.
These were left alone, deliberately.

## Progress and stream state machines — probably leave as they are

`UpdatePage.tsx:91-106`, `wizard/InstallProgress.tsx:49`,
`TunerPage.tsx:96-120`, `logs/StreamingLogPanel.tsx:97`, `logs/LogViewer.tsx:68`.

Each polls toward a TERMINAL condition and fires side effects on specific
transitions -- UpdatePage sets `done`, clears its own interval from inside the
callback, and reloads the state the run changed. `useQuery` models a cache
entry, not a run. Converting these buys a `refetchInterval` callback and keeps
the state machine anyway.

## Plain mount reads — mechanical, follow MetricsPage

`EventsPage`, `RecipeList`, `CookFileList`, `PelletsPage`'s sub-reads,
`pellets/VocabTable`, `cookfiles/MediaPanel`, `cookfiles/CommentList`,
`admin/SystemCard`, `admin/LogsCard`, `admin/BackupsCard`,
`recipes/IngredientsEditor`, `recipes/StepsEditor`,
`recipes/InstructionsEditor`, `recipes/RecipeAssetManager`,
`recipes/RecipeRunStatus`, `settings/tabs/ProbesTab`,
`settings/tabs/UnitsTab`, `tuner/ProfileForm`, `wizard/probes/PortsCard`,
`wizard/probes/DevicesCard`, `wizard/probes/ThermoworksPicker`,
`wizard/probes/BluetoothPicker`, `wizard/fields/UsbSerialPicker`,
`wizard/fields/I2cBusField`.

Recipe: `src/components/metrics/MetricsPage.tsx` for an envelope-returning API
(needs `unwrap`), `src/components/cookfiles/CookFilePage.tsx` for a throwing
one (does not). Add the key to `src/helpers/query/keys.ts`.

## Not a candidate

The socket.io push plane -- `helpers/useLiveState.ts`,
`helpers/shellContext.ts`, `components/shell/AppShell.tsx`. Dash and pellet
data arrive by server push at a 1s cadence; there is no cache key, no
staleness and no refetch for react-query to manage.
```

- [ ] **Step 7: Final check**

```bash
cd web-react && bun run typecheck && bun run lint && bun run test
jj log -r 'trunk()..@' --no-graph -T 'description.first_line() ++ "\n"'
```

Expected: 14 revisions, one per task, each with a conventional-commit subject.
