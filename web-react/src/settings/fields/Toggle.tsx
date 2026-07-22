export function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="pf-field">
      <span className="pf-field-label">{label}</span>
      <button type="button" className={`pf-switch ${checked ? "on" : ""}`} aria-pressed={checked} onClick={() => onChange(!checked)}>
        <span className="pf-switch-knob" />
      </button>
    </label>
  );
}
