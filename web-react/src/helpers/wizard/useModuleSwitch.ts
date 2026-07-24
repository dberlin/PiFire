import { useState } from "react";
import { fetchModuleValues } from "./wizardApi";
import type { ModuleValues, WizardSection } from "./wizardTypes";

export interface UseModuleSwitchParams {
  baseUrl: string;
  section: WizardSection;
  errorMessage: string;
  apply: (values: ModuleValues, newModule: string) => void;
}

export interface ModuleSwitch {
  loading: boolean;
  error: string | null;
  switchModule: (newModule: string) => void;
}

// Shared async module-switch mechanics for the wizard's module-card steps
// (grillplatform / display / distance): fetch the target module's values from
// the server, expose loading + error, and hand the values to a per-step `apply`.
// `apply` is defined in the component body and closes over that render's
// `working`, so it can read the PRE-switch selection directly -- callers must
// not thread a prevModule through this hook.
export function useModuleSwitch({
  baseUrl,
  section,
  errorMessage,
  apply,
}: UseModuleSwitchParams): ModuleSwitch {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(newModule: string) {
    setLoading(true);
    setError(null);
    try {
      const values = await fetchModuleValues(baseUrl, section, newModule);
      apply(values, newModule);
    } catch {
      // Advisory failure: leave the prior selection/deps intact so the user can
      // retry -- never half-apply a switch.
      setError(errorMessage);
    } finally {
      setLoading(false);
    }
  }

  return { loading, error, switchModule: (newModule: string) => void run(newModule) };
}
