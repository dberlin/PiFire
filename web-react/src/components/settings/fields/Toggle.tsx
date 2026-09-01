import { Field } from "./Field";

export function Toggle({
  label,
  checked,
  onChange,
  disabled,
  hint,
  error = null,
  path,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
  /** Why the control is in the state it is — announced with it, not beside it. */
  hint?: string;
  /** The backend's reason for refusing this field on the last save. */
  error?: string | null;
  path?: string;
}) {
  return (
    <Field label={label} hint={hint} error={error} path={path}>
      {({ id, describedBy }) => (
        <button
          id={id}
          type="button"
          className={`pf-switch ${checked ? "on" : ""}`}
          aria-pressed={checked}
          // No aria-invalid: role="button" does not support it, so assistive
          // tech drops it and the error never reaches a screen reader. The
          // error text is announced through aria-describedby instead.
          aria-describedby={describedBy}
          disabled={disabled}
          onClick={() => !disabled && onChange(!checked)}
        >
          <span className="pf-switch-knob" />
        </button>
      )}
    </Field>
  );
}
