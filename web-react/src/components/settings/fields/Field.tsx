import { type ReactNode, useEffect, useId } from "react";
import { useSettingsFieldErrors } from "../../../helpers/settings/fieldErrorContext";

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
  /** Extra class(es) appended to the field's wrapping container, e.g. the
   *  column layout a multi-row field like StringListField needs. Every
   *  other field renders with "pf-field" alone. */
  className?: string;
  children: (aria: {
    /** Pass this to the control's `id` -- it is what the sibling <label>'s
     *  `htmlFor` points at. Without it the label names nothing. */
    id: string;
    describedBy: string | undefined;
    invalid: true | undefined;
  }) => ReactNode;
}

export function Field({ label, hint, error = null, path, className, children }: FieldProps) {
  const ctx = useSettingsFieldErrors();
  // Claim on MOUNT, not when an error exists: the claimed set has to mean
  // "paths with a slot on screen", which is a fact about rendering, not about
  // this save's outcome. Deriving it from the errors would let a hidden
  // field's error vanish.
  useEffect(() => {
    if (!path || !ctx) return;
    return ctx.claim(path);
  }, [path, ctx]);
  const resolvedError =
    error ?? (path && ctx ? (ctx.errors.find((e) => e.path === path)?.message ?? null) : null);
  const controlId = useId();
  const hintId = useId();
  const errorId = useId();
  // aria-describedby takes a space-separated id list; only reference ids for
  // parts that actually render, or the attribute points at nothing.
  const describedBy =
    [hint ? hintId : null, resolvedError ? errorId : null].filter(Boolean).join(" ") || undefined;
  return (
    <div className={className ? `pf-field ${className}` : "pf-field"}>
      {/* The label is a SIBLING of the control, associated via htmlFor, not
          a wrapper around it. A wrapping <label> computes its control's
          accessible name from ALL of the label's descendant text -- for a
          field whose render prop returns more than the one control (a
          reveal button, a row of buttons), that folds their text in too
          ("MQTT Password" becomes "MQTT Password Show"). htmlFor names
          exactly the one element it points at and leaves everything else
          alone. */}
      <label className="pf-field-label" htmlFor={controlId}>
        {label}
      </label>
      {children({ id: controlId, describedBy, invalid: resolvedError ? true : undefined })}
      {hint && (
        <span className="pf-field-hint" id={hintId}>
          {hint}
        </span>
      )}
      {resolvedError && (
        <span className="pf-field-error" id={errorId} role="alert">
          {resolvedError}
        </span>
      )}
    </div>
  );
}
