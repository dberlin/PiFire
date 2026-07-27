import type { AccentName } from "../types";
import { setPath } from "./delta";
import { applySettings, getSettings, type Settings } from "./settingsApi";

// One accent covers the whole appliance. This is the key display/qtapp.py's
// _accent_fn reads once a second, so a change here reaches the attached screen
// as well as the browser. It is held per display module because that is where
// the display's own configuration lives.
export function accentPath(s: Settings): string | null {
  const module = s.modules?.display;
  return module ? `display.config.${module}.accent_theme` : null;
}

// The store spells these "Ember" / "Ice" / "Crimson" (display/qml/Theme.qml);
// the app spells them lowercase.
export function readAccent(s: Settings): AccentName {
  const module = s.modules?.display;
  const stored = module ? s.display?.config?.[module]?.accent_theme : undefined;
  const name = typeof stored === "string" ? stored.toLowerCase() : "ember";
  return name === "ice" || name === "crimson" ? name : "ember";
}

export function storedAccentName(a: AccentName): string {
  return a.charAt(0).toUpperCase() + a.slice(1);
}

/**
 * Persist an accent chosen somewhere that does not already hold settings.
 *
 * The dashboard's swatch row is the caller: it has no settings of its own, and
 * the path depends on which display module is selected, so the module has to be
 * read before the write. Same GET-then-POST shape the dashboard's notify write
 * already uses.
 *
 * Advisory: the caller has already applied the accent locally, so a failed
 * write costs the persistence, not the appearance.
 */
export async function saveAccent(baseUrl: string, accent: AccentName): Promise<boolean> {
  try {
    const settings = await getSettings(baseUrl);
    const path = accentPath(settings);
    if (!path) return false;
    const result = await applySettings(baseUrl, setPath({}, path, storedAccentName(accent)), []);
    return result.ok;
  } catch {
    return false;
  }
}
