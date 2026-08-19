import type { ControllerCatalog } from "@pifire/core/settings/controllerTypes";
import type { SettingsSchema } from "@pifire/core/settings/settingsTypes";
import { normalizeApiBase, queryKeys } from "../query/keys";
import { queryClient } from "../query/queryClient";
import { getControllerMetadata, getMode, getSettings } from "./settingsApi";

const BASE_URL = normalizeApiBase(import.meta.env.PUBLIC_PIFIRE_URL || "");

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
  settings: SettingsSchema;
  mode: string;
  controllerMeta: ControllerCatalog | null;
}> {
  const [settings, mode, controllerMeta] = await Promise.all([
    queryClient.fetchQuery({
      queryKey: queryKeys.settings(BASE_URL),
      queryFn: () => getSettings(BASE_URL),
    }),
    queryClient.fetchQuery({
      queryKey: queryKeys.mode(BASE_URL),
      queryFn: () => getMode(BASE_URL),
    }),
    queryClient.fetchQuery({
      queryKey: queryKeys.controllerMetadata(BASE_URL),
      queryFn: () => getControllerMetadata(BASE_URL),
    }),
  ]);
  return { settings, mode, controllerMeta };
}
