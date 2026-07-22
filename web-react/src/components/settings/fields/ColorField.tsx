import { hexToRgbString, rgbStringToHex } from "../../../helpers/settings/colorFormat";

export function ColorField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <label className="pf-field">
      <span className="pf-field-label">{label}</span>
      <input
        type="color"
        value={rgbStringToHex(value)}
        onChange={(e) => onChange(hexToRgbString(e.target.value))}
      />
    </label>
  );
}
