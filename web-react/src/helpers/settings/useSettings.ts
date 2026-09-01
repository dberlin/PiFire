import type { SettingsSchema } from "@pifire/core/settings/settingsTypes";
import { useQuery } from "@tanstack/react-query";

import { normalizeApiBase, queryKeys } from "../query/keys";
import { getSettings } from "./settingsApi";

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
export function useSettings(baseUrl = BASE_URL) {
  const normalizedBaseUrl = normalizeApiBase(baseUrl);
  return useQuery<SettingsSchema>({
    queryKey: queryKeys.settings(normalizedBaseUrl),
    queryFn: () => getSettings(normalizedBaseUrl),
  });
}
