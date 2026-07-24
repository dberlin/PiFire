import type { WizardSection, WizardState, WizardWorking } from "./wizardTypes";

export function initialWorking(state: WizardState): WizardWorking {
  return {
    selections: { ...state.selections },
    settings_dep_values: structuredClone(state.settings_dep_values),
    display_config: structuredClone(state.display_config),
    probe_map: structuredClone(state.probe_map),
    probes_units: state.probes_units,
  };
}

export function selectModule(
  w: WizardWorking,
  section: WizardSection,
  module: string,
): WizardWorking {
  return { ...w, selections: { ...w.selections, [section]: module } };
}

export function setDepValue(
  w: WizardWorking,
  section: WizardSection,
  key: string,
  value: string | null,
): WizardWorking {
  return {
    ...w,
    settings_dep_values: {
      ...w.settings_dep_values,
      [section]: { ...w.settings_dep_values[section], [key]: value },
    },
  };
}

export function displayConfigFor(w: WizardWorking, module: string): Record<string, unknown> {
  return w.display_config[module] ?? {};
}

export function setDisplayConfig(
  w: WizardWorking,
  module: string,
  optionName: string,
  value: unknown,
): WizardWorking {
  return {
    ...w,
    display_config: {
      ...w.display_config,
      [module]: { ...displayConfigFor(w, module), [optionName]: value },
    },
  };
}
