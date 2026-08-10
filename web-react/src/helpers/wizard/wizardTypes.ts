import type { ProbeMap, WizardDraftRequest } from "../contracts/wizard.gen";

// Mutable client working state remains local; only serialized request/response
// contracts are generated from Pydantic.
export interface WizardWorking {
  selections: NonNullable<WizardDraftRequest["selections"]>;
  settings_dep_values: NonNullable<WizardDraftRequest["settings_dep_values"]>;
  display_config: NonNullable<WizardDraftRequest["display_config"]>;
  probe_map: ProbeMap;
  probes_units: string;
}
