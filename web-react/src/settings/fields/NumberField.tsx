export function NumberField({ label, value, onChange, min, max, step, suffix }: {
  label: string; value: number; onChange: (v: number) => void; min?: number; max?: number; step?: number; suffix?: string;
}) {
  return (
    <label className="pf-field">
      <span className="pf-field-label">{label}</span>
      <span className="pf-field-control">
        <input className="pf-input" type="number" value={value} min={min} max={max} step={step}
          onChange={(e) => onChange(Number(e.target.value))} />
        {suffix && <span className="pf-field-suffix">{suffix}</span>}
      </span>
    </label>
  );
}
