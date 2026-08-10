import type { ProbeProfile } from "../../../helpers/contracts/wizard.gen";

const TYPE_OPTIONS = [
  { value: "Food", label: "Food Probe" },
  { value: "Primary", label: "Primary Probe" },
  { value: "Aux", label: "Auxillary Probe" },
];

// Copied verbatim from wizard/wizard_manifest.json probe_config_options.<key>.description.
// Legacy renders these with |safe (raw HTML); mirrored here via dangerouslySetInnerHTML
// since this is static trusted manifest copy, not user input.
const FIELD_DESCRIPTIONS: Record<string, string> = {
  name: "This name will be displayed throughout the PiFire UI for this probe.  It should be unique to this probe and not match other probe names.",
  device_port: "The device name and port where the temperature readings will be read from.",
  type: "Probe types are as follows: <i><br><strong>Food</strong> - Used as a food probe to track the temperature of food items.  This probe will be displayed in the UI and tracked in history. <br><strong>Primary</strong> - Used as the primary probe to control the grill/smoker in hold mode, etc.  There must be one Primary probe, and only one.  This probe is displayed in the UI and tracked in the history.  <br><strong>Aux</strong> - Auxillary temperature input, not displayed in the UI, but tracked in the history. Used by virtual probes or for reference in tuning.</i>",
  profile_id:
    "The probe profile that will be applied to this probe (if applicable for the device).",
  enabled: "Probe is enabled and visible in the UI.",
};

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
        <span
          className="pf-field-hint"
          dangerouslySetInnerHTML={{ __html: FIELD_DESCRIPTIONS.name }}
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
        <span
          className="pf-field-hint"
          dangerouslySetInnerHTML={{ __html: FIELD_DESCRIPTIONS.device_port }}
        />
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
        <span
          className="pf-field-hint"
          dangerouslySetInnerHTML={{ __html: FIELD_DESCRIPTIONS.type }}
        />
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
          <span
            className="pf-field-hint"
            dangerouslySetInnerHTML={{ __html: FIELD_DESCRIPTIONS.profile_id }}
          />
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
          <span
            className="pf-field-hint"
            dangerouslySetInnerHTML={{ __html: FIELD_DESCRIPTIONS.enabled }}
          />
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
