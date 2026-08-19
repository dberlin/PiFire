import type { SaveFieldError } from "@pifire/core/settings/controllerTypes";

/** The backend's message for one settings path, or null if it did not reject it. */
export function errorFor(errors: SaveFieldError[], path: string): string | null {
  return errors.find((e) => e.path === path)?.message ?? null;
}

/** The errors no field on the current tab renders. They still have to be shown
 *  somewhere: a cross-section rule can reject a path this tab does not own, and
 *  a failed save with nothing on screen is worse than an unplaced message. */
export function unmatchedErrors(errors: SaveFieldError[], paths: string[]): SaveFieldError[] {
  const claimed = new Set(paths);
  return errors.filter((e) => !claimed.has(e.path));
}
