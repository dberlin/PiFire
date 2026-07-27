import { clampToBounds } from "../../../helpers/settings/bounds";

export function NumberField({
  label,
  value,
  onChange,
  min,
  max,
  step,
  suffix,
  hint,
  disabled,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
  suffix?: string;
  hint?: string;
  disabled?: boolean;
}) {
  return (
    <label className="pf-field">
      <span className="pf-field-label">{label}</span>
      <span className="pf-field-control">
        <input
          className="pf-input"
          type="number"
          value={value}
          min={min}
          max={max}
          step={step}
          disabled={disabled}
          onChange={(e) => onChange(Number(e.target.value))}
          // Bounds enforcement lives here, not in onChange. There is no <form>
          // anywhere in the settings tree, so the browser never runs constraint
          // validation on these inputs and `min`/`max` alone only drive the
          // spinner arrows and `:invalid` styling — nothing stops a typed 500 in
          // a max={9} field. Blur is the moment the value is finished; clamping
          // on change would make a bounded field untypeable (with min={20},
          // typing "25" clamps the intermediate "2" to 20, yielding "205").
          onBlur={(e) => {
            const typed = Number(e.target.value);
            const clamped = clampToBounds(typed, min, max);
            if (clamped !== typed) onChange(clamped);
          }}
        />
        {suffix && <span className="pf-field-suffix">{suffix}</span>}
      </span>
      {hint && <span className="pf-field-hint">{hint}</span>}
    </label>
  );
}
