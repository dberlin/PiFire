import { QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { createBrowserRouter, RouterProvider } from "react-router";

import { queryClient } from "../helpers/query/queryClient";
import { useWebUiBuild } from "../helpers/useWebUiBuild";
import { AppPrefsProvider } from "./AppPrefs";
import { routes } from "./appRoutes";

const router = createBrowserRouter(routes);

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
