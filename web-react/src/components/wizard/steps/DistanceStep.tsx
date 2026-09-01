import type { ModuleValues, WizardState } from "@pifire/core/contracts/wizard";

import { useModuleSwitch } from "../../../helpers/wizard/useModuleSwitch";
import {
  selectModule,
  setDepValue,
  setSectionDepValues,
} from "../../../helpers/wizard/wizardState";
import type { WizardWorking } from "../../../helpers/wizard/wizardTypes";
import { ModuleCard } from "../ModuleCard";

export interface DistanceStepProps {
  state: WizardState;
  working: WizardWorking;
  onChange: (next: WizardWorking) => void;
  baseUrl: string;
}

export function DistanceStep({ state, working, onChange, baseUrl }: DistanceStepProps) {
  const { loading, error, switchModule } = useModuleSwitch({
    baseUrl,
    section: "distance",
    errorMessage: "Couldn't load the sensor configuration. Please try again.",
    apply: (values: ModuleValues, newModule: string) => {
      let next = selectModule(working, "distance", newModule);
      next = setSectionDepValues(next, "distance", values.settings);
      onChange(next);
    },
  });

  return (
    <div className="pf-wizard-step" data-step="distance">
      <h2 className="pf-wizard-step-title">Distance / Hopper</h2>
      {error && <p className="pf-wizard-finish-error">{error}</p>}
      <ModuleCard
        section="distance"
        configSource="none"
        modules={state.modules_metadata.distance}
        selectedModule={working.selections.distance}
        depValues={working.settings_dep_values.distance ?? {}}
        configValues={{}}
        baseUrl={baseUrl}
        disabled={loading}
        onSelectModule={(m) => switchModule(m)}
        onDepChange={(k, v) => onChange(setDepValue(working, "distance", k, v))}
        onConfigChange={() => {}}
      />
    </div>
  );
}
