import {
  type ControllerMetadata,
  getControllerMetadata,
  getMode,
  getSettings,
  type Settings,
} from "./settingsApi";

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

// React Router route loader — runs on navigation into /settings. Throws on
// failure so the route's errorElement renders.
export async function settingsLoader(): Promise<{
  settings: Settings;
  mode: string;
  controllerMeta: ControllerMetadata | null;
}> {
  const [settings, mode, controllerMeta] = await Promise.all([
    getSettings(BASE_URL),
    getMode(BASE_URL),
    getControllerMetadata(BASE_URL),
  ]);
  return { settings, mode, controllerMeta };
}
