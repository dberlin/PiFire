import { useId } from "react";

export function Toggle({
  label,
  checked,
  onChange,
  disabled,
  hint,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
  /** Why the control is in the state it is — announced with it, not beside it. */
  hint?: string;
}) {
  const hintId = useId();
  return (
    <>
      {/* The hint sits outside the <label> on purpose: a <label> wrapping a
          control folds all of its text content into that control's
          accessible name, so a hint left inside would double as part of the
          name instead of staying a separate description. */}
      <label className="pf-field">
        <span className="pf-field-label">{label}</span>
        <button
          type="button"
          className={`pf-switch ${checked ? "on" : ""}`}
          aria-pressed={checked}
          aria-describedby={hint ? hintId : undefined}
          disabled={disabled}
          onClick={() => !disabled && onChange(!checked)}
        >
          <span className="pf-switch-knob" />
        </button>
      </label>
      {hint && (
        <span className="pf-settings-hint" id={hintId}>
          {hint}
        </span>
      )}
    </>
  );
}
