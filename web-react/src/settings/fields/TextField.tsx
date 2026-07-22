export function TextField({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <label className="pf-field">
      <span className="pf-field-label">{label}</span>
      <input className="pf-input" type="text" value={value} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}
