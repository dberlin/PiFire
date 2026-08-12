/**
 * Every query key in the app, in one place.
 *
 * Invalidation is the reason. useSaveSettings has to name the settings entry
 * from a module that knows nothing about the pages reading it, and a key
 * spelled inline in two files is a cache that has silently split in two.
 *
 * Each API origin owns its own settings subtree. React-query matches keys by
 * prefix, so invalidating settingsRoot(baseUrl) reaches the settings blob,
 * mode and controller metadata for that origin without touching another one.
 * That preserves revalidation semantics while fencing A → B → A base changes.
 */
export const normalizeApiBase = (baseUrl: string): string => baseUrl.replace(/\/$/, "");

const settingsRoot = (baseUrl: string) => ["settings", normalizeApiBase(baseUrl)] as const;

export const queryKeys = {
  /** Deliberate all-origin prefix; ordinary writes invalidate settingsRoot(baseUrl). */
  allSettings: ["settings"] as const,
  settingsRoot,
  settings: (baseUrl: string) => [...settingsRoot(baseUrl), "all"] as const,
  mode: (baseUrl: string) => [...settingsRoot(baseUrl), "mode"] as const,
  controllerMetadata: (baseUrl: string) =>
    [...settingsRoot(baseUrl), "controller-metadata"] as const,
  metrics: ["metrics"] as const,
  webUiBuild: ["webui-build"] as const,
  adminState: ["admin", "state"] as const,
  /** Prefix of every history window, so one invalidate reaches all of them at
   * once -- used by clear_history, which wipes every window server-side, not
   * just whichever one happens to be on screen. */
  historyRoot: ["history"] as const,
  historyChart: (minutes: number | undefined) => ["history", "chart", minutes ?? null] as const,
  /** Prefix of both cook-file entries, so one invalidate reaches detail+chart. */
  cookfileRoot: (filename: string) => ["cookfile", filename] as const,
  cookfileDetail: (filename: string) => ["cookfile", filename, "detail"] as const,
  cookfileChart: (filename: string) => ["cookfile", filename, "chart"] as const,
  recipe: (filename: string) => ["recipe", filename] as const,
} as const;
