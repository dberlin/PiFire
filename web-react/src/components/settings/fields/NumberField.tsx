import { clampToBounds } from "../../../helpers/settings/bounds";
import { Field } from "./Field";

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
  integer,
  error = null,
  path,
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
  integer?: boolean;
  /** The backend's reason for refusing this field on the last save. */
  error?: string | null;
  path?: string;
}) {
  return (
    <Field label={label} hint={hint} error={error} path={path}>
      {({ id, describedBy, invalid }) => (
        <span className="pf-field-control">
          <input
            id={id}
            className="pf-input"
            type="number"
            value={value}
            min={min}
            max={max}
            step={step ?? (integer ? 1 : undefined)}
            disabled={disabled}
            aria-describedby={describedBy}
            aria-invalid={invalid}
            onChange={(e) => onChange(Number(e.target.value))}
            // Bounds enforcement lives here, not in onChange. There is no <form>
            // anywhere in the settings tree, so the browser never runs constraint
            // validation on these inputs and `min`/`max` alone only drive the
            // spinner arrows and `:invalid` styling — nothing stops a typed 500 in
            // a max={9} field. Blur is the moment the value is finished; clamping
            // on change would make a bounded field untypeable (with min={20},
            // typing "25" clamps the intermediate "2" to 20, yielding "205"). The
            // same reasoning applies to rounding an integer-backed field: the
            // strict backend refuses a float against an int field on save, so a
            // typed fraction is rounded here rather than as each digit arrives.
            onBlur={(e) => {
              const typed = Number(e.target.value);
              const rounded = integer ? Math.round(typed) : typed;
              const clamped = clampToBounds(rounded, min, max);
              if (clamped !== typed) onChange(clamped);
            }}
          />
          {suffix && <span className="pf-field-suffix">{suffix}</span>}
        </span>
      )}
    </Field>
  );
}
