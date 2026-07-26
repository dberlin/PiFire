import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { createMemoryRouter, Outlet, RouterProvider } from "react-router";
import { useSettingsDraftStore } from "./helpers/settings/settingsDrafts";

// Settings tabs (and other routed components) read their data via
// `useOutletContext()`. To exercise them in isolation we build a tiny memory
// router: a parent route renders <Outlet context={context}/>, and `ui` is
// mounted as its index child, so `useOutletContext()` resolves exactly as it
// would inside the real SettingsShell layout route.
export function renderRoute(ui: ReactElement, context: unknown) {
  // Stands in for SettingsShell: it owns the draft store a settings tab writes
  // its in-progress edits into (helpers/settings/settingsDrafts.ts), so a tab
  // rendered in isolation behaves exactly as it does inside the real shell.
  // Cross-TAB persistence is not observable from here -- only one tab is ever
  // mounted -- and is covered by settingsDrafts.test.tsx, which drives the real
  // shell. Declared inside so this module still exports only `renderRoute`.
  function RouteHost() {
    const store = useSettingsDraftStore((context as { settings?: unknown })?.settings);
    return <Outlet context={{ ...(context as object), ...store }} />;
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
  return render(<RouterProvider router={router} />);
}
