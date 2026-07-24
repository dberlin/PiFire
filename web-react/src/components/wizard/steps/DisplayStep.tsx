import { useModuleSwitch } from "../../../helpers/wizard/useModuleSwitch";
import {
  displayConfigFor,
  selectModule,
  setDepValue,
  setDisplayConfig,
  setSectionDepValues,
} from "../../../helpers/wizard/wizardState";
import type { ModuleValues, WizardState, WizardWorking } from "../../../helpers/wizard/wizardTypes";
import { ModuleCard } from "../ModuleCard";

export interface DisplayStepProps {
  state: WizardState;
  working: WizardWorking;
  onChange: (next: WizardWorking) => void;
  baseUrl: string;
}

export function DisplayStep({ state, working, onChange, baseUrl }: DisplayStepProps) {
  const selectedDisplay = working.selections.display ?? "";
  const { loading, error, switchModule } = useModuleSwitch({
    baseUrl,
    section: "display",
    errorMessage: "Couldn't load the display configuration. Please try again.",
    // Apply ONLY the dep-values. `display_config` stays client-held so an
    // unsaved config edit survives switching modules (the returned `config` is
    // deliberately ignored).
    apply: (values: ModuleValues, newModule: string) => {
      let next = selectModule(working, "display", newModule);
      next = setSectionDepValues(next, "display", values.settings);
      onChange(next);
    },
  });

  return (
    <div className="pf-wizard-step" data-step="display">
      <h2 className="pf-wizard-step-title">Display</h2>
      {error && <p className="pf-wizard-finish-error">{error}</p>}
      <ModuleCard
        section="display"
        configSource="settings-by-module"
        modules={state.modules_metadata.display}
        selectedModule={working.selections.display}
        depValues={working.settings_dep_values.display ?? {}}
        configValues={displayConfigFor(working, selectedDisplay)}
        baseUrl={baseUrl}
        disabled={loading}
        onSelectModule={(m) => switchModule(m)}
        onDepChange={(k, v) => onChange(setDepValue(working, "display", k, v))}
        onConfigChange={(name, v) => onChange(setDisplayConfig(working, selectedDisplay, name, v))}
      />
    </div>
  );
}
