import type { WizardState, WizardWorking } from "../../../helpers/wizard/wizardTypes";
import { DevicesCard } from "../probes/DevicesCard";
import { PortsCard } from "../probes/PortsCard";

export interface ProbesStepProps {
  state: WizardState;
  working: WizardWorking;
  onChange: (next: WizardWorking) => void;
  baseUrl: string;
}

export function ProbesStep({ state, working, onChange, baseUrl }: ProbesStepProps) {
  const setProbeMap = (probe_map: WizardWorking["probe_map"]) =>
    onChange({ ...working, probe_map });
  return (
    <div className="pf-wizard-step" data-step="probes">
      <h2 className="pf-wizard-step-title">Probes</h2>
      <label className="pf-field">
        <span className="pf-field-label">Temp Units</span>
        <select
          className="pf-input"
          value={working.probes_units}
          onChange={(e) => onChange({ ...working, probes_units: e.target.value })}
        >
          <option value="F">Fahrenheit</option>
          <option value="C">Celsius</option>
        </select>
      </label>
      <DevicesCard
        probeMap={working.probe_map}
        modules={state.modules_metadata.probes}
        baseUrl={baseUrl}
        onChange={setProbeMap}
      />
      <PortsCard
        probeMap={working.probe_map}
        profiles={state.probe_profiles}
        onChange={setProbeMap}
      />
    </div>
  );
}
