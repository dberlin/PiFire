import type {
  Config,
  ModuleSettingValue,
  ProbeMap,
  WireValue,
  WizardSection,
  WizardState,
} from "@pifire/core/contracts/wizard";

import type { WizardWorking } from "./wizardTypes";

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
  value: ModuleSettingValue,
): WizardWorking {
  return {
    ...w,
    settings_dep_values: {
      ...w.settings_dep_values,
      [section]: { ...w.settings_dep_values[section], [key]: value },
    },
  };
}

export function displayConfigFor(w: WizardWorking, module: string): Config {
  return w.display_config[module] ?? {};
}

export function setDisplayConfig(
  w: WizardWorking,
  module: string,
  optionName: string,
  value: WireValue,
): WizardWorking {
  return {
    ...w,
    display_config: {
      ...w.display_config,
      [module]: { ...displayConfigFor(w, module), [optionName]: value },
    },
  };
}

export const EMPTY_PROBE_MAP: ProbeMap = { probe_devices: [], probe_info: [] };

// Order-insensitive structural equality. probe_maps are plain JSON (arrays of
// objects with fixed string/number keys); a manifest-sourced map and a
// reducer-built map can differ only in object key order, which this ignores.
function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null) {
    return false;
  }
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  const ak = Object.keys(a);
  const bk = Object.keys(b);
  if (ak.length !== bk.length) return false;
  return ak.every((k) => deepEqual(Reflect.get(a, k), Reflect.get(b, k)));
}

export function setSectionDepValues(
  w: WizardWorking,
  section: WizardSection,
  values: Record<string, ModuleSettingValue>,
): WizardWorking {
  return {
    ...w,
    settings_dep_values: { ...w.settings_dep_values, [section]: { ...values } },
  };
}

export function replaceProbeMap(w: WizardWorking, probe_map: ProbeMap): WizardWorking {
  return { ...w, probe_map };
}

// D2 guard: reseed the probe_map from the newly-selected board's default only
// on a fresh install AND only when the current map has NOT diverged from the
// previous board's default -- so manual probe edits are never clobbered.
// Callers resolve prev/new board maps as `board_probe_maps[module] ?? EMPTY_PROBE_MAP`.
export function reseedProbeMapForBoard(
  currentMap: ProbeMap,
  prevBoardMap: ProbeMap,
  newBoardMap: ProbeMap,
  firstTimeSetup: boolean,
): ProbeMap {
  if (firstTimeSetup && deepEqual(currentMap, prevBoardMap)) {
    return structuredClone(newBoardMap);
  }
  return currentMap;
}
