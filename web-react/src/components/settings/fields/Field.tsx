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
  children: (aria: { describedBy: string | undefined; invalid: true | undefined }) => ReactNode;
}

export function Field({ label, hint, error = null, path, children }: FieldProps) {
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
  const hintId = useId();
  const errorId = useId();
  // aria-describedby takes a space-separated id list; only reference ids for
  // parts that actually render, or the attribute points at nothing.
  const describedBy =
    [hint ? hintId : null, resolvedError ? errorId : null].filter(Boolean).join(" ") || undefined;
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
        {children({ describedBy, invalid: resolvedError ? true : undefined })}
      </label>
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
    </>
  );
}
