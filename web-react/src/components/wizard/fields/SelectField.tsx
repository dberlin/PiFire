export interface SelectFieldOption {
  value: string;
  label: string;
}

export interface SelectFieldProps {
  label: string;
  value: string;
  options: SelectFieldOption[];
  hidden?: boolean;
  onChange: (value: string) => void;
}

export function SelectField({ label, value, options, hidden, onChange }: SelectFieldProps) {
  if (hidden) return null;
  return (
    <label className="pf-field">
      <span className="pf-field-label">{label}</span>
      <select className="pf-input" value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
    </label>
  );
}
