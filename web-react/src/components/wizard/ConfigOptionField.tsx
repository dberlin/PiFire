import type { ConfigOption } from "../../helpers/wizard/wizardTypes";

export interface ConfigOptionFieldProps {
  option: ConfigOption;
  value: unknown;
  onChange: (next: string) => void;
}

export function ConfigOptionField({ option, value, onChange }: ConfigOptionFieldProps) {
  if (option.hidden) return null;

  if (option.option_type === "list") {
    const listValues = option.list_values ?? [];
    const listLabels = option.list_labels ?? [];
    return (
      <label className="pf-field">
        <span className="pf-field-label">{option.option_friendly_name}</span>
        <select
          className="pf-input"
          value={String(value)}
          onChange={(e) => {
            const chosen = listValues.find((item) => String(item) === e.target.value);
            onChange(String(chosen ?? e.target.value));
          }}
        >
          {listValues.map((item, i) => (
            <option key={String(item)} value={String(item)}>
              {listLabels[i] ?? String(item)}
            </option>
          ))}
        </select>
      </label>
    );
  }

  return (
    <label className="pf-field">
      <span className="pf-field-label">{option.option_friendly_name}</span>
      <input
        className="pf-input"
        type="text"
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}
