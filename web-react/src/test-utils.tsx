import type { ReactElement } from "react";
import { createMemoryRouter, Outlet, RouterProvider } from "react-router";
import { render } from "@testing-library/react";

// Settings tabs (and other routed components) read their data via
// `useOutletContext()`. To exercise them in isolation we build a tiny memory
// router: a parent route renders <Outlet context={context}/>, and `ui` is
// mounted as its index child, so `useOutletContext()` resolves exactly as it
// would inside the real SettingsShell layout route.
export function renderRoute(ui: ReactElement, context: unknown) {
  const router = createMemoryRouter(
    [
      {
        path: "/",
        element: <Outlet context={context} />,
        children: [{ index: true, element: ui }],
      },
    ],
    { initialEntries: ["/"] },
  );
  return render(<RouterProvider router={router} />);
}
