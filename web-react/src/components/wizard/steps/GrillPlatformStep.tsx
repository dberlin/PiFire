import { useModuleSwitch } from "../../../helpers/wizard/useModuleSwitch";
import {
  EMPTY_PROBE_MAP,
  replaceProbeMap,
  reseedProbeMapForBoard,
  selectModule,
  setDepValue,
  setSectionDepValues,
} from "../../../helpers/wizard/wizardState";
import type { ModuleValues, WizardState } from "../../../helpers/contracts/wizard.gen";
import type { WizardWorking } from "../../../helpers/wizard/wizardTypes";
import { ModuleCard } from "../ModuleCard";

export interface GrillPlatformStepProps {
  state: WizardState;
  working: WizardWorking;
  onChange: (next: WizardWorking) => void;
  baseUrl: string;
}

export function GrillPlatformStep({ state, working, onChange, baseUrl }: GrillPlatformStepProps) {
  const { loading, error, switchModule } = useModuleSwitch({
    baseUrl,
    section: "grillplatform",
    errorMessage: "Couldn't load the platform configuration. Please try again.",
    apply: (values: ModuleValues, newModule: string) => {
      // `working` is this render's (pre-switch) value, so this reads the
      // PREVIOUS selection -- same semantics as capturing prevModule before the
      // fetch.
      const prevModule = working.selections.grillplatform;
      let next = selectModule(working, "grillplatform", newModule);
      next = setSectionDepValues(next, "grillplatform", values.settings);
      const prevBoardMap = state.board_probe_maps[prevModule ?? ""] ?? EMPTY_PROBE_MAP;
      const newBoardMap = state.board_probe_maps[newModule] ?? EMPTY_PROBE_MAP;
      next = replaceProbeMap(
        next,
        reseedProbeMapForBoard(
          working.probe_map,
          prevBoardMap,
          newBoardMap,
          state.first_time_setup,
        ),
      );
      onChange(next);
    },
  });

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
        onSelectModule={(m) => switchModule(m)}
        onDepChange={(k, v) => onChange(setDepValue(working, "grillplatform", k, v))}
        onConfigChange={() => {}}
      />
    </div>
  );
}
