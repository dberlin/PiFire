import { getSettings, type Settings } from "./settingsApi";

const BASE_URL = import.meta.env.VITE_PIFIRE_URL || "";

// React Router route loader — runs on navigation into /settings. Throws on
// failure so the route's errorElement renders.
export async function settingsLoader(): Promise<{ settings: Settings }> {
  return { settings: await getSettings(BASE_URL) };
}
