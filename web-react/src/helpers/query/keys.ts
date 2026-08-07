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
