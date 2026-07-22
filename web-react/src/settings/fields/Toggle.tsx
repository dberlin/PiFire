export function Toggle({ label, checked, onChange, disabled }: { label: string; checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <label className="pf-field">
      <span className="pf-field-label">{label}</span>
      <button type="button" className={`pf-switch ${checked ? "on" : ""}`} aria-pressed={checked} disabled={disabled} onClick={() => !disabled && onChange(!checked)}>
        <span className="pf-switch-knob" />
      </button>
    </label>
  );
}
