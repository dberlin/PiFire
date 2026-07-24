import type { ProbeProfile } from "../../../helpers/wizard/probeTypes";

const TYPE_OPTIONS = [
  { value: "Food", label: "Food Probe" },
  { value: "Primary", label: "Primary Probe" },
  { value: "Aux", label: "Auxillary Probe" },
];

export interface PortFormProps {
  mode: "add" | "edit";
  devicePortOptions: { value: string; label: string }[];
  profiles: ProbeProfile[];
  values: { name: string; device_port: string; type: string; profile_id: string; enabled: string };
  onFieldChange: (field: string, value: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
  error: string | null;
}

export function PortForm({
  mode,
  devicePortOptions,
  profiles,
  values,
  onFieldChange,
  onSubmit,
  onCancel,
  error,
}: PortFormProps) {
  const showProfile = values.device_port.includes("ADC"); // §6
  const showEnabled = !values.type.includes("Aux"); // §6
  return (
    <div className="pf-port-form" role="dialog" aria-label={`${mode} probe`}>
      {error && <p role="alert">{error}</p>}
      <label className="pf-field">
        <span className="pf-field-label">Probe Name</span>
        <input
          className="pf-input"
          type="text"
          value={values.name}
          onChange={(e) => onFieldChange("name", e.target.value)}
        />
      </label>
      <label className="pf-field">
        <span className="pf-field-label">Device &amp; Port</span>
        <select
          className="pf-input"
          value={values.device_port}
          onChange={(e) => onFieldChange("device_port", e.target.value)}
        >
          <option value="">— select —</option>
          {devicePortOptions.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </label>
      <label className="pf-field">
        <span className="pf-field-label">Probe Type</span>
        <select
          className="pf-input"
          value={values.type}
          onChange={(e) => onFieldChange("type", e.target.value)}
        >
          {TYPE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </label>
      {showProfile && (
        <label className="pf-field">
          <span className="pf-field-label">Probe Profile</span>
          <select
            className="pf-input"
            value={values.profile_id}
            onChange={(e) => onFieldChange("profile_id", e.target.value)}
          >
            <option value="">— select —</option>
            {profiles.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
      )}
      {showEnabled && (
        <label className="pf-field">
          <span className="pf-field-label">Enabled</span>
          <select
            className="pf-input"
            value={values.enabled}
            onChange={(e) => onFieldChange("enabled", e.target.value)}
          >
            <option value="true">Yes</option>
            <option value="false">No</option>
          </select>
        </label>
      )}
      <div className="pf-form-actions">
        <button type="button" className="pf-btn" onClick={onCancel}>
          Cancel
        </button>
        <button type="button" className="pf-btn pf-btn-primary" onClick={onSubmit}>
          {mode === "add" ? "Add" : "Save"}
        </button>
      </div>
    </div>
  );
}
