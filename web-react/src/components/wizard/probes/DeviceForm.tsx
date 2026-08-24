import type { Config, ProbeModuleData, WireValue } from "@pifire/core/contracts/wizard";
import { moduleImageUrl } from "../../../helpers/wizard/wizardAssets";
import { DeviceConfigField } from "./DeviceConfigField";

const SOFTWARE_DETECTION_WARNING =
  "WARNING: This amplifier does not have enabled, board-supported thermocouple fault detection. A disconnected or electrically shorted/collapsed probe may read as the cold-junction (ambient) temperature instead of reporting a fault. Software thermocouple fault detection is STRONGLY RECOMMENDED.";

export interface DeviceFormProps {
  mode: "add" | "edit";
  moduleData: ProbeModuleData;
  values: Config;
  nameValue: string;
  availableProbes: string[];
  baseUrl: string;
  onNameChange: (name: string) => void;
  onFieldChange: (label: string, value: WireValue) => void;
  onSubmit: () => void;
  onCancel: () => void;
  error: string | null;
}

export function DeviceForm(props: DeviceFormProps) {
  const { moduleData, values, nameValue, availableProbes, baseUrl } = props;
  const thermocoupleModule = moduleData.device_specific.type === "thermocouple";
  const hardwareFaultDetectionEnabled =
    values.hardware_fault_detection === true || values.hardware_fault_detection === "True";
  const showSoftwareDetectionWarning =
    thermocoupleModule && !hardwareFaultDetectionEnabled;
  return (
    <div className="pf-device-form" role="dialog" aria-label={`${props.mode} device`}>
      {moduleImageUrl(baseUrl, moduleData.image) && (
        <img
          className="pf-module-image"
          src={moduleImageUrl(baseUrl, moduleData.image)}
          alt={moduleData.friendly_name}
        />
      )}
      <h3 className="pf-module-name">{moduleData.friendly_name}</h3>
      {moduleData.description && <p className="pf-module-description">{moduleData.description}</p>}
      {showSoftwareDetectionWarning ? (
        <p className="pf-module-notes">{SOFTWARE_DETECTION_WARNING}</p>
      ) : !thermocoupleModule && moduleData.notes ? (
        <p className="pf-module-notes">{moduleData.notes}</p>
      ) : null}
      {props.error && <p role="alert">{props.error}</p>}
      {moduleData.device_specific.config.map((field) => (
        <DeviceConfigField
          key={field.label}
          field={field}
          value={values[field.label]}
          allValues={values}
          availableProbes={availableProbes}
          baseUrl={baseUrl}
          onChange={props.onFieldChange}
        />
      ))}
      <label className="pf-field">
        <span className="pf-field-label">Unique Device Name</span>
        <input
          className="pf-input"
          type="text"
          required
          value={nameValue}
          onChange={(e) => props.onNameChange(e.target.value)}
        />
      </label>
      <div className="pf-form-actions">
        <button type="button" className="pf-btn" onClick={props.onCancel}>
          Cancel
        </button>
        <button type="button" className="pf-btn pf-btn-primary" onClick={props.onSubmit}>
          {props.mode === "add" ? "Add" : "Save"}
        </button>
      </div>
    </div>
  );
}
