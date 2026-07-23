import {
  displayConfigFor,
  selectModule,
  setDepValue,
  setDisplayConfig,
} from "../../../helpers/wizard/wizardState";
import type { WizardState, WizardWorking } from "../../../helpers/wizard/wizardTypes";
import { ModuleCard } from "../ModuleCard";

export interface DisplayStepProps {
  state: WizardState;
  working: WizardWorking;
  onChange: (next: WizardWorking) => void;
  baseUrl: string;
}

export function DisplayStep({ state, working, onChange, baseUrl }: DisplayStepProps) {
  const selectedDisplay = working.selections.display ?? "";
  return (
    <div className="pf-wizard-step" data-step="display">
      <h2 className="pf-wizard-step-title">Display</h2>
      <ModuleCard
        section="display"
        configSource="settings-by-module"
        modules={state.modules_metadata.display}
        selectedModule={working.selections.display}
        depValues={working.settings_dep_values.display ?? {}}
        configValues={displayConfigFor(working, selectedDisplay)}
        baseUrl={baseUrl}
        onSelectModule={(m) => onChange(selectModule(working, "display", m))}
        onDepChange={(k, v) => onChange(setDepValue(working, "display", k, v))}
        onConfigChange={(name, v) => onChange(setDisplayConfig(working, selectedDisplay, name, v))}
      />
    </div>
  );
}
