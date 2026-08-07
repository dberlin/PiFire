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
