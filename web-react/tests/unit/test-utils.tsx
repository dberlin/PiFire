import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render } from "@testing-library/react";
import type { ReactElement } from "react";
import { createMemoryRouter, Outlet, RouterProvider } from "react-router";
import { AppPrefsProvider } from "../../src/components/AppPrefs";
import { useSettingsDraftStore } from "../../src/helpers/settings/settingsDrafts";

/**
 * Flush the macrotask hop react-query's `notifyManager` schedules before it
 * delivers an observer re-render.
 *
 * `await act(() => client.invalidateQueries(...))` resolves as soon as the
 * refetch promise settles -- BEFORE notifyManager hands the result to
 * subscribed observers. notifyManager.cjs's `defaultScheduler` is
 * `systemSetTimeoutZero` (timeoutManager.cjs), i.e. a real `setTimeout(fn, 0)`
 * macrotask, not a microtask `invalidateQueries` already awaits. Sampling the
 * DOM immediately after `invalidateQueries` therefore reads the component one
 * render too early. Await this once more after any `invalidateQueries` (or
 * other query-client mutation) whose effect you assert on synchronously.
 */
export async function flushObservers() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

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

// Settings tabs (and other routed components) read their data via
// `useOutletContext()`. To exercise them in isolation we build a tiny memory
// router: a parent route renders <Outlet context={context}/>, and `ui` is
// mounted as its index child, so `useOutletContext()` resolves exactly as it
// would inside the real SettingsShell layout route.
export function renderRoute(ui: ReactElement, context: unknown, overrides?: object) {
  // Stands in for SettingsShell: it owns the draft store a settings tab writes
  // its in-progress edits into (helpers/settings/settingsDrafts.ts), so a tab
  // rendered in isolation behaves exactly as it does inside the real shell.
  // Cross-TAB persistence is not observable from here -- only one tab is ever
  // mounted -- and is covered by settingsDrafts.test.tsx, which drives the real
  // shell. Declared inside so this module still exports only `renderRoute`.
  function RouteHost() {
    const store = useSettingsDraftStore((context as { settings?: unknown })?.settings);
    // `overrides` lands last so a test can seed the draft ANOTHER tab would
    // have written. A seeded `drafts` is fixed for the render: the rendered
    // tab's own writes go to the real store but stay masked, so use this for
    // read-only assertions about a neighbouring tab's pending edit.
    return <Outlet context={{ ...(context as object), ...store, ...overrides }} />;
  }
  const router = createMemoryRouter(
    [
      {
        path: "/",
        element: <RouteHost />,
        children: [{ index: true, element: ui }],
      },
    ],
    { initialEntries: ["/"] },
  );
  // App.tsx wraps every route in this, and a tab that reads a preference (the
  // accent on General) needs it present to render at all.
  return render(
    <QueryClientProvider client={testQueryClient()}>
      <AppPrefsProvider>
        <RouterProvider router={router} />
      </AppPrefsProvider>
    </QueryClientProvider>,
  );
}
