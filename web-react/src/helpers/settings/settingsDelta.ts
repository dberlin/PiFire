// A settings write that states more than "merge this".
//
// The backend applies a settings body as a deep merge, which can say "assign
// this" but has no way to say "remove this": an omitted key is asking for
// nothing, not for a deletion. Mirrors common/settings_schema.py's
// settings_delta() -- and, before that, common/control_delta.py, which solved
// the same problem for the control blob.
export const SETTINGS_DELTA_KEY = "__settings_delta__";
export const SETTINGS_DELTA_VERSION = 1;

export interface SettingsDelta {
  [SETTINGS_DELTA_KEY]: typeof SETTINGS_DELTA_VERSION;
  set?: object;
  delete?: string[][];
}

/**
 * Wrap a partial and the key paths to remove. With nothing to remove the
 * partial is returned unchanged, so the common save stays the plain body every
 * other tab already sends.
 */
/**
 * Wrap a partial and the key paths to remove. With nothing to remove the
 * partial is returned unchanged, so the common save stays the plain body every
 * other tab already sends.
 *
 * The rule for callers: dropping a key from `setValues` is not a deletion --
 * the backend's merge treats an absent key as silence, so the persisted value
 * survives untouched. Any surface that removes a key from its own object
 * before saving must name that key's full path here, in `deletePaths`.
 */
export function settingsDelta(setValues: object, deletePaths: string[][] = []): object {
  if (deletePaths.length === 0) return setValues;
  return {
    [SETTINGS_DELTA_KEY]: SETTINGS_DELTA_VERSION,
    set: setValues,
    delete: deletePaths.map((path) => [...path]),
  };
}
