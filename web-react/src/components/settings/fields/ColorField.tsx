import { hexToRgbString, rgbStringToHex } from "../../../helpers/settings/colorFormat";
import { Field } from "./Field";

export function ColorField({
  label,
  value,
  onChange,
  hint,
  error = null,
  path,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  hint?: string;
  error?: string | null;
  path?: string;
}) {
  return (
    <Field label={label} hint={hint} error={error} path={path}>
      {({ id, describedBy, invalid }) => (
        <input
          id={id}
          type="color"
          className="pf-input"
          value={rgbStringToHex(value)}
          aria-describedby={describedBy}
          aria-invalid={invalid}
          onChange={(e) => onChange(hexToRgbString(e.target.value))}
        />
      )}
    </Field>
  );
}
