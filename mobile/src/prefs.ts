import AsyncStorage from "@react-native-async-storage/async-storage";

import { THEME, type AccentName } from "./theme";

export interface Prefs {
  host: string | null;
  accent: AccentName;
  alerts: boolean;
}

const PREFS_KEY = "pifire.prefs";

// The allowed accent set, read off THEME itself rather than duplicated --
// mergePrefs's whole job is to reject anything not in this list.
const ACCENTS = Object.keys(THEME) as AccentName[];

export const defaultPrefs: Prefs = {
  host: null,
  accent: "ember",
  alerts: true,
};

function isAccentName(value: unknown): value is AccentName {
  return typeof value === "string" && (ACCENTS as string[]).includes(value);
}

// Validates each field against its allowed set rather than trusting stored
// JSON verbatim. Concretely: an app updated past a renamed or removed accent
// must not render `THEME[undefined]` -- it falls back to defaultPrefs.accent
// instead of propagating whatever a previous, incompatible version wrote.
export function mergePrefs(stored: unknown): Prefs {
  const s =
    typeof stored === "object" && stored !== null ? (stored as Record<string, unknown>) : {};
  return {
    host: typeof s.host === "string" ? s.host : defaultPrefs.host,
    accent: isAccentName(s.accent) ? s.accent : defaultPrefs.accent,
    alerts: typeof s.alerts === "boolean" ? s.alerts : defaultPrefs.alerts,
  };
}

export async function loadPrefs(): Promise<Prefs> {
  const raw = await AsyncStorage.getItem(PREFS_KEY);
  if (!raw) {
    return { ...defaultPrefs };
  }
  try {
    return mergePrefs(JSON.parse(raw));
  } catch {
    return { ...defaultPrefs };
  }
}

export async function savePrefs(p: Prefs): Promise<void> {
  await AsyncStorage.setItem(PREFS_KEY, JSON.stringify(p));
}
