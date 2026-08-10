import type { ProbeModuleCatalog } from "../contracts/wizard.gen";
import { getProbeModules } from "./probeMapApi";

export const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

// React Router route loader for /settings/probes. Throws on failure so the
// parent's errorElement (SettingsError) renders. Runs alongside -- not
// inside -- settingsLoader, so useSaveSettings' revalidate() refreshes both.
export async function probeModulesLoader(): Promise<ProbeModuleCatalog> {
  return getProbeModules(BASE_URL);
}
