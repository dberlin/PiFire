import { useState } from "react";
import { fetchModuleValues } from "../../../helpers/wizard/wizardApi";
import {
  EMPTY_PROBE_MAP,
  replaceProbeMap,
  reseedProbeMapForBoard,
  selectModule,
  setDepValue,
  setSectionDepValues,
} from "../../../helpers/wizard/wizardState";
import type { WizardState, WizardWorking } from "../../../helpers/wizard/wizardTypes";
import { ModuleCard } from "../ModuleCard";

export interface GrillPlatformStepProps {
  state: WizardState;
  working: WizardWorking;
  onChange: (next: WizardWorking) => void;
  baseUrl: string;
}

export function GrillPlatformStep({ state, working, onChange, baseUrl }: GrillPlatformStepProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSelect(newModule: string) {
    const prevModule = working.selections.grillplatform;
    setLoading(true);
    setError(null);
    try {
      const res = await fetchModuleValues(baseUrl, "grillplatform", newModule);
      let next = selectModule(working, "grillplatform", newModule);
      next = setSectionDepValues(next, "grillplatform", res.settings);
      const prevBoardMap = state.board_probe_maps[prevModule ?? ""] ?? EMPTY_PROBE_MAP;
      const newBoardMap = state.board_probe_maps[newModule] ?? EMPTY_PROBE_MAP;
      const reseeded = reseedProbeMapForBoard(
        working.probe_map,
        prevBoardMap,
        newBoardMap,
        state.first_time_setup,
      );
      next = replaceProbeMap(next, reseeded);
      onChange(next);
    } catch {
      // Advisory failure: leave the prior selection/deps/probe_map intact so
      // the user can retry -- never half-apply a switch.
      setError("Couldn't load the platform configuration. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="pf-wizard-step" data-step="grillplatform">
      <h2 className="pf-wizard-step-title">Grill Platform</h2>
      {error && <p className="pf-wizard-finish-error">{error}</p>}
      <ModuleCard
        section="grillplatform"
        configSource="none"
        modules={state.modules_metadata.grillplatform}
        selectedModule={working.selections.grillplatform}
        depValues={working.settings_dep_values.grillplatform ?? {}}
        configValues={{}}
        baseUrl={baseUrl}
        disabled={loading}
        onSelectModule={(m) => void handleSelect(m)}
        onDepChange={(k, v) => onChange(setDepValue(working, "grillplatform", k, v))}
        onConfigChange={() => {}}
      />
    </div>
  );
}
