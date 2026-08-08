import type { ConfigOption } from "../../helpers/wizard/wizardTypes";
import { Field } from "../settings/fields/Field";

export interface ConfigOptionFieldProps {
  option: ConfigOption;
  value: unknown;
  onChange: (next: string) => void;
}

export function ConfigOptionField({ option, value, onChange }: ConfigOptionFieldProps) {
  if (option.hidden) return null;

  // A module that has never been configured has no stored value for its
  // options; fall back to the manifest's `default` so the field shows what the
  // driver will actually use rather than a blank/"undefined" selection.
  const effective = value === undefined ? option.default : value;

  if (option.option_type === "list") {
    const listValues = option.list_values ?? [];
    const listLabels = option.list_labels ?? [];
    return (
      <Field label={option.option_friendly_name} hint={option.option_description}>
        {({ id, describedBy, invalid }) => (
          <select
            id={id}
            className="pf-input"
            value={String(effective)}
            aria-describedby={describedBy}
            aria-invalid={invalid}
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
        )}
      </Field>
    );
  }

  return (
    <Field label={option.option_friendly_name} hint={option.option_description}>
      {({ id, describedBy, invalid }) => (
        <input
          id={id}
          className="pf-input"
          type="text"
          value={String(effective ?? "")}
          aria-describedby={describedBy}
          aria-invalid={invalid}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
    </Field>
  );
}
