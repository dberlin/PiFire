import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { createMemoryRouter, Outlet, RouterProvider } from "react-router";
import { AppPrefsProvider } from "../../src/components/AppPrefs";
import { useSettingsDraftStore } from "../../src/helpers/settings/settingsDrafts";

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
    <AppPrefsProvider>
      <RouterProvider router={router} />
    </AppPrefsProvider>,
  );
}
