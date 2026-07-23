import { getWizardState } from "./wizardApi";
import type { WizardState } from "./wizardTypes";

export const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

// React Router route loader — runs on navigation into /wizard. Throws on
// failure so the route's errorElement renders.
export async function wizardLoader(): Promise<WizardState> {
  return getWizardState(BASE_URL);
}
