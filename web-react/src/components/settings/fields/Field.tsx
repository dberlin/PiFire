import { type ReactNode, useId } from "react";

export interface FieldProps {
  label: string;
  /** The setting's description. Rendered beneath the control. */
  hint?: string;
  /** The backend's reason for refusing this field on the last save. */
  error?: string | null;
  /** Dotted settings path, e.g. "startup.duration". Present on settings
   *  fields, absent on wizard fields that write no settings path. Task 2
   *  gives this meaning; here it is accepted and ignored. */
  path?: string;
  children: (aria: { describedBy: string | undefined; invalid: true | undefined }) => ReactNode;
}

export function Field({ label, hint, error = null, children }: FieldProps) {
  const hintId = useId();
  const errorId = useId();
  // aria-describedby takes a space-separated id list; only reference ids for
  // parts that actually render, or the attribute points at nothing.
  const describedBy =
    [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(" ") || undefined;
  return (
    <>
      {/* The hint sits outside the <label> on purpose: a <label> wrapping a
          control folds all of its text content into that control's
          accessible name, so a hint left inside would double as part of the
          name instead of staying a separate description. */}
      {/* biome-ignore lint/a11y/noLabelWithoutControl: the control here comes
          from the render-prop `children`, so static analysis cannot see it
          inside the <label>. The FieldProps contract requires children to
          render exactly one real form control, so the label always wraps
          one at runtime even though the linter cannot verify that. */}
      <label className="pf-field">
        <span className="pf-field-label">{label}</span>
        {children({ describedBy, invalid: error ? true : undefined })}
      </label>
      {hint && (
        <span className="pf-field-hint" id={hintId}>
          {hint}
        </span>
      )}
      {error && (
        <span className="pf-field-error" id={errorId} role="alert">
          {error}
        </span>
      )}
    </>
  );
}
