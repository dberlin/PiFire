import type { I2cBusValue, ProbeMap, WizardSection } from "../contracts/wizard.gen";

// Mutable client working state remains local; only serialized request/response
// contracts are generated from Pydantic.
export interface WizardWorking {
  selections: Record<WizardSection, string | null>;
  settings_dep_values: Record<WizardSection, Record<string, string | I2cBusValue | null>>;
  display_config: Record<string, Record<string, unknown>>;
  probe_map: ProbeMap;
  probes_units: string;
}
